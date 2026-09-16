"""ה-API של הקונסולה — מה שהדפדפן מדבר איתו.

RBAC לפי סעיף 11: משתמש deploy יכול לראות אימג'ים ולנהל סבב הפצה,
ותו לא. ניהול — קבוצות, טבלאות MAC, משתמשים, יומן, הגדרות — admin בלבד.
כל פעולה כותבת נרשמת ביומן עם שם המשתמש.
"""

from __future__ import annotations

import json
import re
import sqlite3
import shutil

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import PlainTextResponse

from . import auth, dhcp, disk_failures, inventory, registry, storage_nodes, users
from .api import ServerContext
from .db import (_write_lock, get_setting, journal, now_iso, set_setting,
                 update_one, writing)
from .images import restore_refusal
from .imagefit import validate_expand_choice
from .journal_he import EVENTS_HE, JournalTranslator
from .session_view import label as session_label
from .sessions import SessionError
from .station import ROUND_OPENER_ROLES

WRITE_SETTINGS = {"recovery_require_login", "session_wait_seconds",
                  "console_idle_seconds", "class_deploy_enabled",
                  # ‏#748: כפתור "עדכן" — כבוי כברירת מחדל (נדב, 16/09).
                  "update_enabled"}

#: #406: השדות שעריכת מכונה מכירה. שדה מחוץ לרשימה = טעות של הקורא,
#: והוא נדחה ב-400 במקום להיבלע ולהחזיר ``{"ok": True}`` שלא שינה כלום.
EDIT_MACHINE_FIELDS = {"name", "group_id", "drawer_count"}


def _drawer_count(raw):
    """#695: מאמת את מספר המגירות שהוגדר למחשב שכפול (1..8). ‏None = לא
    נשלח (משאירים את ברירת המחדל בסכמה)."""
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "מספר המגירות חייב להיות מספר")
    if not 1 <= n <= 8:
        raise HTTPException(400, "מספר המגירות חייב להיות בין 1 ל־8")
    return n

#: כל כתיבה כאן עוברת ב-``with _write_lock, writing(ctx.conn)`` — שני
#: המנגנונים של `db.py`, מאותה סיבה שבגללה `net_seen` קיבל אותם (#272,
#: ‏#356, ‏#313): כתיבה שנכשלה בלי ``rollback`` משאירה את החיבור בתוך
#: טרנזאקציה, ומשם **כל** כתיבה על התהליכון הזה נכשלת מיד עד אתחול
#: השרת — קונסולה שמחזירה שגיאה בכל שמירה, גם אחרי שהעומס חלף.
#: ‏`journal` נוטל את אותה נעילה בעצמו, והיא ``Lock`` ולא ``RLock``,
#: ולכן הרישום ביומן נשאר **מחוץ** לבלוק.


def create_console_router(
    ctx: ServerContext, known_macs_hooks: dict | None = None
) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    def _sync_known_macs(user_id) -> dict | None:
        """‏#141: מי שנכנס/יוצא מטבלת המכונות משנה מי מקבל dhcp-boot בכלל —
        הקובץ נכתב מחדש **מיד**, לא ממתין לעריכת DHCP הבאה בלשונית הרשת.

        מחזיר את שדה `network` של התשובה (#857): ‏`None` כשלא הופעל
        (אין hooks), ‏`{"applied": True, "error": None}` בהצלחה,
        ‏`{"applied": False, "error": "<סיבה>"}` בכשל. עד #857 הכשל
        נרשם ביומן בלבד והתשובה הייתה 200 נקי — ה-DB עודכן, הרשת לא,
        והמכונה החדשה לא קיבלה GRUB בשקט. ה-DB **אינו** מגולגל אחורה
        (המפעיל ביקש את המכונה) — התשובה פשוט מבחינה.

        ‏`known_macs_hooks` הוא `None` כברירת מחדל בכוונה (בדיוק כמו
        `dhcp_hooks` ב-console_dhcp.py, אבל כאן ה-None הוא גם ברירת
        המחדל הבטוחה לבדיקות): הוספה/מחיקה/ייבוא של מכונה קורים כמעט
        בכל בדיקה בחבילה, ובלי hook מוזרק זה היה אומר כתיבה אמיתית
        ל-/etc/imagectl/known-macs ו-`systemctl reload dnsmasq` על כל
        אחת מהן. הייצור מדליק אותו במפורש דרך `main.py` — לא ברירת מחדל
        שקטה, כדי שהיא לא תיעלם באותה שקט שבו הייתה נדלקת."""
        if known_macs_hooks is None:
            return None
        text = dhcp.render_known_macs(registry.all_macs(ctx.conn))
        error = known_macs_hooks["apply"](text)
        if error:
            journal(ctx.conn, "known_macs_apply_failed", error, user_id)
            return {"applied": False, "error": str(error)}
        return {"applied": True, "error": None}

    @router.post("/login")
    async def login(request: Request, response: Response):
        body = await request.json()
        role = users.verify(ctx.conn, body.get("username", ""), body.get("password", ""))
        if role is None:
            journal(ctx.conn, "login_failed", body.get("username", ""))
            raise HTTPException(401, "שם משתמש או סיסמה שגויים")
        username = body["username"].strip()
        response.set_cookie(
            auth.COOKIE_NAME, auth.issue(ctx.conn, username, role),
            httponly=True, samesite="lax", max_age=auth.TTL_SECONDS,
        )
        journal(ctx.conn, "login", "", username)
        return {
            "username": username,
            "role": role,
            "idle_seconds": int(get_setting(ctx.conn, "console_idle_seconds") or 300),
        }

    @router.post("/logout")
    def logout(response: Response, user=Depends(current_user)):
        response.delete_cookie(auth.COOKIE_NAME)
        return {"ok": True}

    @router.get("/me")
    def me(user=Depends(current_user)):
        # זמן הניתוק מוחזר לכל משתמש מחובר — גם deploy, שאין לו גישה
        # למסך ההגדרות אבל הניתוק חל גם עליו.
        #
        # ‏capabilities: דגלים נגזרי-שרת שהקונסולה חושפת מהם רכיבים
        # מותנים. ‏`interbranch_transfer` (#723) — רק ל-admin, רק על
        # standalone, ורק כשיש משני פעיל אחד לפחות. הדגל הוא הנראות;
        # האכיפה עצמה יושבת בשכבת ה-route (שלבים הבאים ב-#655).
        return {
            "username": user[0],
            "role": user[1],
            "idle_seconds": int(get_setting(ctx.conn, "console_idle_seconds") or 300),
            "capabilities": {
                "interbranch_transfer":
                    storage_nodes.can_interbranch_transfer(ctx.conn, user[1]),
                # ‏#740: הוספת משני זמינה על ראשי גם בלי משניים קיימים (זו
                # הפעולה שיוצרת את הראשון); פאנל ה-pairing המקומי — על משני.
                "enroll_secondary":
                    storage_nodes.can_enroll_secondary(ctx.conn, user[1]),
                "open_local_pairing":
                    storage_nodes.can_open_local_pairing(ctx.conn, user[1]),
            },
        }

    # --- מבט-על --------------------------------------------------------------

    @router.get("/overview")
    def overview(user=Depends(current_user)):
        from . import pulls as pulls_module    # noqa: PLC0415 — נמנע ממעגל ייבוא
        from . import room as room_module      # noqa: PLC0415 — נמנע ממעגל ייבוא
        room_module.tick(ctx.conn, ctx.store)
        pulls_module.sweep(ctx.store)
        session = ctx.store.active()
        session_view = None
        if session is not None:
            session_view = ctx.store.view(ctx.store.maybe_start(session), ctx.library)
        machines = ctx.conn.execute("SELECT COUNT(*) AS n FROM machines").fetchone()["n"]
        try:
            usage = shutil.disk_usage(ctx.library.root)
            storage = {"total_bytes": usage.total, "free_bytes": usage.free}
        except OSError:
            storage = None
        return {
            "session": session_view,
            # משיכות היוניקאסט שרצות עכשיו. הן אינן "הסבב" — אבל הן
            # עבודה אמיתית על השרת, ושרת שעובד לא ייראה פנוי (#60).
            "pulls": [ctx.store.view(row, ctx.library)
                      for row in ctx.store.active_pulls()],
            "room": room_module.status_view(ctx)["round"],
            "sender": ctx.sender.status() if ctx.sender else None,
            "machines": machines,
            "images": len(ctx.library.scan()),
            "storage": storage,
            "now": now_iso(),
        }

    # אימג'ים ותיקיות — ב-console_library.py.

    # --- קבוצות וטבלת MAC (admin) -------------------------------------------

    @router.get("/groups")
    def groups(user=Depends(current_user)):
        rows = ctx.conn.execute(
            "SELECT g.id, g.label, g.role, g.sort, COUNT(m.mac) AS machines FROM groups g"
            " LEFT JOIN machines m ON m.group_id = g.id"
            " GROUP BY g.id ORDER BY g.sort, g.id"
        ).fetchall()
        return [dict(r) for r in rows]

    def derive_group_id(label: str) -> str:
        """מזהה מהשם: שם באנגלית/ספרות משמש כפי שהוא, שם בעברית מקבל
        מזהה רץ. המזהה נכנס לכתובות URL וליומן ולכן נשאר ASCII — אבל
        המשתמש לא חייב להמציא אותו."""
        base = re.sub(r"[^A-Za-z0-9_-]", "", label.replace(" ", "_")).strip("_-")
        if not base:
            n = ctx.conn.execute(
                "SELECT COUNT(*) AS n FROM groups WHERE role = 'classroom'"
            ).fetchone()["n"]
            base = f"CLASS{n + 1}"
        gid, bump = f"grp_{base}", 1
        while ctx.conn.execute(
            "SELECT 1 FROM groups WHERE id = ?", (gid,)
        ).fetchone() is not None:
            bump += 1
            gid = f"grp_{base}-{bump}"
        return gid

    @router.post("/groups")
    async def add_group(request: Request, user=Depends(admin_only)):
        body = await request.json()
        gid, label, role = body.get("id", "").strip(), body.get("label", "").strip(), body.get("role", "")
        if not label or role not in ("build", "cloner", "classroom"):
            raise HTTPException(400, "צריך שם ותפקיד חוקי")
        # המזהה נכנס לכתובות URL ולשורות היומן, ולכן חייב להיות ASCII —
        # אבל הוא רשות: בלעדיו הוא נגזר מהשם (שיכול להיות בכל שפה).
        if gid in ("", "grp_"):
            gid = derive_group_id(label)
        if not re.fullmatch(r"grp_[A-Za-z0-9][A-Za-z0-9_-]*", gid):
            raise HTTPException(400, "מזהה חייב להיות אותיות או ספרות באנגלית")
        # קבוצה חדשה נכנסת לסוף הרשימה, לא לאמצע.
        last = ctx.conn.execute("SELECT MAX(sort) AS n FROM groups").fetchone()["n"]
        try:
            # ה-``rollback`` שעמד כאן ידנית (#184) יושב עכשיו ב-`writing`,
            # והוא חל על **כל** מסלול יציאה ולא רק על `IntegrityError`:
            # ה-INSERT שנכשל כבר פתח טרנזאקציית כתיבה, וחריגה שיוצאת
            # בלי לסגור אותה משאירה **נעילה יתומה** — והכתיבה הבאה
            # נופלת ב-`database is locked` בלי קשר נראה לעין בין
            # השתיים (זה ה-gotcha של #54).
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "INSERT INTO groups (id, label, role, sort) VALUES (?, ?, ?, ?)",
                    (gid, label, role, (last or 0) + 1),
                )
        except sqlite3.IntegrityError:
            # תופסים `IntegrityError` ולא `Exception`: כשל אחר — דיסק
            # מלא, סכימה שהשתנתה — היה מתחפש כאן ל"כבר קיימת", וזו
            # בדיוק ההודעה שתשלח את המפעיל לחפש במקום הלא נכון.
            raise HTTPException(409, "קבוצה בשם הזה כבר קיימת")
        journal(ctx.conn, "group_create", f"{gid} ({role})", user[0])
        return {"ok": True}

    @router.post("/groups/order")
    async def reorder_groups(request: Request, user=Depends(admin_only)):
        """הסדר שנקבע בגרירה. מקבל את המזהים לפי הסדר הרצוי."""
        ids = (await request.json()).get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(400, "צריך רשימת מזהים")
        known = {r["id"] for r in ctx.conn.execute("SELECT id FROM groups")}
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise HTTPException(400, f"קבוצה לא קיימת: {unknown[0]}")
        # שורה לכל קבוצה — רצף אחד. סדר שנכתב חלקית הוא רשימה שהמפעיל
        # רואה אחרת ממה שגרר, ואינו יודע שזה מה שקרה.
        with _write_lock, writing(ctx.conn):
            for index, gid in enumerate(ids):
                ctx.conn.execute("UPDATE groups SET sort = ? WHERE id = ?",
                                 (index, gid))
        journal(ctx.conn, "group_reorder", ", ".join(ids), user[0])
        return {"ok": True}

    @router.put("/groups/{gid}")
    async def rename_group(gid: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        label = (body.get("label") or "").strip()
        if not label:
            raise HTTPException(400, "שם ריק")
        # ‏`update_one` מכסה את השורה שלא נמצאה; ‏`writing` מכסה את
        # ה-UPDATE שנכשל בכלל, ואת ה-``commit`` שאחריו.
        with _write_lock, writing(ctx.conn):
            if not update_one(ctx.conn,
                              "UPDATE groups SET label = ? WHERE id = ?",
                              (label, gid)):
                raise HTTPException(404, "קבוצה לא קיימת")
        journal(ctx.conn, "group_edit", f"{gid} label={label}", user[0])
        return {"ok": True}

    @router.delete("/groups/{gid}")
    def del_group(gid: str, user=Depends(admin_only)):
        from .db import FIXED_GROUPS
        if gid in {g[0] for g in FIXED_GROUPS}:
            # חדר השיכפולים ומחשב הבנייה הם יחידים במערכת — אין להם
            # ניהול קבוצות, ולכן גם אי אפשר למחוק אותם.
            raise HTTPException(400, "קבוצה קבועה — אפשר להסיר ממנה מכונות, לא למחוק אותה")
        with _write_lock, writing(ctx.conn):
            if not update_one(ctx.conn, "DELETE FROM groups WHERE id = ?", (gid,)):
                raise HTTPException(404, "קבוצה לא קיימת")
        journal(ctx.conn, "group_delete", gid, user[0])
        return {"ok": True}

    @router.get("/machines")
    def machines(group: str | None = None, user=Depends(current_user)):
        # #417: המלאי האחרון שהמכונה דיווחה ב-hello, ליד מסך המכונה.
        # ‏null = מעולם לא דיווחה (או שהדיווח האחרון היה פגום); [] = דיווחה
        # בפועל אפס כוננים — שני ממצאים שונים, ואסור לקפל (עיקרון 5).
        query = (
            "SELECT m.mac, m.suffix, m.group_id, m.note, m.drawer_count,"
            " m.added_at, d.disks_json, d.last_seen AS disks_reported_at,"
            " d.prompt"   # #906: מה המכונה ממתינה עליו לאדם (NULL = לא ממתינה)
            " FROM machines m LEFT JOIN net_devices d ON d.mac = m.mac"
            + (" WHERE m.group_id = ?" if group else "")
            + " ORDER BY m.group_id, m.suffix"
        )
        rows = ctx.conn.execute(query, (group,) if group else ()).fetchall()
        # ‏#720: המלאי החומרתי האחרון (schema 2). null = מעולם לא דיווחה.
        inventories = inventory.latest_all(ctx.conn)
        result = []
        for r in rows:
            row = dict(r)
            disks_json = row.pop("disks_json")
            row["disks"] = json.loads(disks_json) if disks_json is not None else None
            seen = inventories.get(row["mac"])
            row["inventory"] = seen["inventory"] if seen else None
            row["inventory_seen_at"] = seen["seen_at"] if seen else None
            result.append(row)
        return result

    @router.post("/machines/import")
    async def import_machines(request: Request, user=Depends(admin_only)):
        body = await request.json()
        group_id = body.get("group_id", "")
        role = registry.group_role(ctx.conn, group_id)
        if role is None:
            raise HTTPException(400, "קבוצה לא קיימת")
        lines = registry.parse_paste(body.get("text", ""), role)
        if body.get("dry_run"):
            return {"preview": [vars(l) for l in lines]}
        saved, rejected = registry.import_lines(ctx.conn, group_id, lines, user[0])
        network = _sync_known_macs(user[0]) if saved else None
        return {"saved": saved, "rejected": [vars(l) for l in rejected],
                "network": network}

    @router.post("/machines")
    async def add_machine(request: Request, user=Depends(admin_only)):
        body = await request.json()
        drawer_count = _drawer_count(body.get("drawer_count"))   # #695
        try:
            mac = registry.add_machine(
                ctx.conn, body.get("mac", ""), body.get("name", ""),
                body.get("group_id", ""), user[0],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if drawer_count is not None:
            with _write_lock, writing(ctx.conn):
                ctx.conn.execute(
                    "UPDATE machines SET drawer_count = ? WHERE mac = ?",
                    (drawer_count, mac),
                )
            journal(ctx.conn, "machine_drawer_count",
                    f"{mac} count={drawer_count}", user[0])
        return {"mac": mac, "network": _sync_known_macs(user[0])}

    @router.put("/machines/{mac}")
    async def edit_machine(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "גוף הבקשה חייב להיות אובייקט")
        unknown = set(body) - EDIT_MACHINE_FIELDS
        if unknown:
            raise HTTPException(400, "שדות לא מוכרים: " + ", ".join(sorted(unknown)))
        drawer_count = _drawer_count(body.get("drawer_count"))   # #695
        try:
            registry.update_machine(
                ctx.conn, mac, body.get("name"), body.get("group_id"), user[0]
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if drawer_count is not None:
            with _write_lock, writing(ctx.conn):
                if not update_one(
                    ctx.conn,
                    "UPDATE machines SET drawer_count = ? WHERE mac = ?",
                    (drawer_count, registry.normalize_mac(mac)),
                ):
                    raise HTTPException(404, "מכונה לא קיימת")
            journal(ctx.conn, "machine_drawer_count",
                    f"{registry.normalize_mac(mac)} count={drawer_count}", user[0])
        return {"ok": True}

    @router.delete("/machines/{mac}")
    def del_machine(mac: str, user=Depends(admin_only)):
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        with _write_lock, writing(ctx.conn):
            if not update_one(ctx.conn, "DELETE FROM machines WHERE mac = ?", (canonical,)):
                raise HTTPException(404, "מכונה לא קיימת")
        journal(ctx.conn, "machine_delete", canonical, user[0])
        return {"ok": True, "network": _sync_known_macs(user[0])}

    @router.get("/machines.csv")
    def machines_csv(user=Depends(admin_only)):
        return PlainTextResponse(registry.export_csv(ctx.conn), media_type="text/csv")

    # --- #874: דיסקים אדומים — זיכרון כשלי הכתיבה בשרת ---------------------------

    @router.get("/disk-failures")
    def read_disk_failures(user=Depends(current_user)):
        return disk_failures.list_open(ctx.conn)

    @router.post("/disk-failures/{failure_id}/clear")
    def clear_disk_failure(failure_id: int, user=Depends(admin_only)):
        # "נקה" — הדיסק הוחלף / הכבל תוקן. רשומה שכבר נוקתה או שאינה
        # קיימת אינה "נוקתה" (עיקרון 5): 404, לא ok.
        if not disk_failures.clear(ctx.conn, failure_id, by=user[0]):
            raise HTTPException(404, "רשומה לא קיימת או שכבר נוקתה")
        return {"ok": True}

    # --- סבבים (גם deploy) ---------------------------------------------------

    def round_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שרשאי להפעיל סבב כיתה.

        אותה רשימת-היתר שבה נפתח הסבב מהתחנה — ``ROUND_OPENER_ROLES``
        (#94) — ולא עותק שלה: מדובר באותו אובייקט בדיוק, וכשיתווסף
        תפקיד שלישי אסור ששתי הרשימות ייפרדו. חדר השיכפולים עשה את
        אותה הכרעה ב-#152 (``room.ROOM_OPERATOR_ROLES``).

        ‏#581 סגר את ``start``/``close`` מאחורי הבדיקה הזו, אך **פתיחת**
        הסבב מהקונסולה נשארה ``current_user`` בלבד — #592 סגר גם אותה.
        וכך כל חשבון מחובר — תפקיד שיתווסף מחר בכלל — כבר אינו יכול
        לפתוח או לעצור שידור חי לכיתה שלמה.
        """
        if user[1] not in ROUND_OPENER_ROLES:
            journal(ctx.conn, "session_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.post("/sessions")
    async def open_session(request: Request, user=Depends(round_operator)):
        """פתיחת סבב כיתה — מהקונסולה או ממסך מחשב הבנייה (אותו cookie).

        `macs` (רשות) — בחירת מחשבים: רק הם מוערים ומצטרפים. קידומת
        ומספר מחשבים הם רשות — נגזרים מהקבוצה ומהבחירה, כמו בתחנה.
        """
        body = await request.json()
        image_id = body.get("image_id", "")
        group_id = body.get("group_id", "")
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(400, "אימג' לא קיים בספרייה")
        # #534: יעד סבב הוא כיתה בלבד — לא מחשב הבנייה ולא חדר השיכפולים.
        # אותה בדיקה של station.py, דרך registry.group_role המשותפת.
        if registry.group_role(ctx.conn, group_id) != "classroom":
            raise HTTPException(400, "יעד הסבב חייב להיות קבוצת כיתה")

        roster = None
        if body.get("macs") is not None:
            if not isinstance(body["macs"], list):
                raise HTTPException(400, "macs חייב להיות רשימה")
            roster = sorted({registry.normalize_mac(m) for m in body["macs"]})
            if not roster or None in roster:
                raise HTTPException(400, "בחירת המחשבים ריקה או מכילה MAC פגום")

        # ‏#381, אחרי שה-roster ידוע: אימג' שנקלט ממחשב כיתה מסוים
        # מסורב לכל יעד אחר, ובהודעה שנוקבת בשני הצדדים (עיקרון 5).
        refusal = restore_refusal(manifest, roster)
        if refusal is not None:
            journal(ctx.conn, "session_image_bound", f"{image_id} — {refusal}",
                    user[0])
            raise HTTPException(400, refusal)

        # ‏#59: בחירת ההרחבה — ברירת המחדל האוטומטית, כיבוי, או מחיצה
        # שנבחרה ביד. מאומתת מול המניפסט הזה לפני שהסבב נפתח.
        try:
            expand_choice = validate_expand_choice(
                manifest, body.get("expand_partition"))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        machines = ctx.conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (group_id,)
        ).fetchone()["n"]
        expected = int(body.get("expected_clients") or 0) \
            or (len(roster) if roster else machines)
        prefix = body.get("prefix") or group_id.removeprefix("grp_").upper()
        try:
            session_id = ctx.store.open(
                group_id, image_id, prefix, expected,
                opened_by=user[0], roster=roster, expand_partition=expand_choice,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return {"id": session_id}

    @router.post("/sessions/{session_id}/start")
    def start_session(session_id: str, user=Depends(round_operator)):
        # תפקיד בלבד, בלי הקלדת שם — אותה הכרעה כמו ``/room/start``:
        # עיקרון 7 נוקב ב"עצירת סבב", וההתחלה רק מקדימה את מה שהטיימר
        # עומד לעשות ממילא.
        try:
            ctx.store.start_now(session_id, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @router.post("/sessions/{session_id}/close")
    async def close_session(session_id: str, request: Request,
                            user=Depends(round_operator)):
        # גוף ריק או לא-JSON הוא בדיוק המקרה שההקלדה נועדה לתפוס, ולכן
        # הוא נופל לאישור ריק — 400 עם ההסבר, ולא 500 שנראה כתקלת שרת.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        row = ctx.conn.execute(
            "SELECT image_id FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(409, "סבב לא קיים")
        # פעולה הרסנית מאחורי הקלדת שם — אותו דפוס כמו מחיקת אימג'
        # ועצירת סבב החדר (#533). מה שמוקלד הוא מה שהמסך כבר מציג,
        # כולל הנפילה חזרה ל-`image_id` כשהמניפסט נמחק באמצע הסבב.
        if typed != session_label(row, ctx.library):
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם האימג' שהסבב משדר")
        try:
            ctx.store.close(session_id, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    # --- משתמשים, יומן, הגדרות (admin) --------------------------------------

    @router.get("/users")
    def list_users(user=Depends(admin_only)):
        return users.list_users(ctx.conn)

    @router.post("/users")
    async def add_user(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            users.create(
                ctx.conn, body.get("username", ""), body.get("password", ""),
                body.get("role", ""), by=user[0],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except sqlite3.IntegrityError:
            # רק התנגשות UNIQUE היא "שם תפוס". כל חריגה אחרת — דיסק מלא,
            # DB נעול — עולה כ-500 עם הסיבה האמיתית, ולא מתחפשת לשם תפוס
            # ששולח את המפעיל לחפש חשבון שלא נוצר (עיקרון 5).
            raise HTTPException(409, "משתמש בשם הזה כבר קיים")
        return {"ok": True}

    @router.put("/users/{username}")
    async def edit_user(username: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        role = body.get("role") or None
        if username == user[0] and role and role != user[1]:
            raise HTTPException(400, "אי אפשר לשנות את התפקיד של עצמך")
        disabled = body.get("disabled")
        if disabled is not None and username == user[0]:
            # נעילה מיידית מחוץ למסך, ובלי דרך לחזור — `auth.check` קורא
            # את החסימה בכל בקשה (#186), כולל בזו ששחררה אותה.
            raise HTTPException(400, "אי אפשר לחסום את המשתמש המחובר")
        try:
            users.update(ctx.conn, username, by=user[0],
                         password=body.get("password") or None, role=role)
            if disabled is not None:
                users.set_disabled(ctx.conn, username, bool(disabled), by=user[0])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @router.delete("/users/{username}")
    def del_user(username: str, user=Depends(admin_only)):
        if username == user[0]:
            raise HTTPException(400, "אי אפשר למחוק את המשתמש המחובר")
        # השומר על "יישאר מי שינהל" ירד ל-``users.delete`` ב-#521: שם הוא
        # תנאי **בתוך** ה-DELETE ולא קריאה שלפניו, ולכן שני מנהלים שנמחקים
        # בו-זמנית אינם רואים שניהם "יש שניים". כאן נשאר רק התרגום ל-HTTP.
        try:
            users.delete(ctx.conn, username, by=user[0])
        except ValueError as exc:
            raise HTTPException(404 if "לא קיים" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.get("/journal/events")
    def read_journal_events(user=Depends(admin_only)):
        # לתפריט הסינון — מציג את מה שהמפעיל רואה (השם בעברית), לא את
        # מפתח האירוע הגולמי.
        return sorted(
            ({"event": k, "label": v} for k, v in EVENTS_HE.items()),
            key=lambda e: e["label"],
        )

    @router.get("/journal")
    def read_journal(
        response: Response,
        limit: int = 200,
        event: str = "",
        user_filter: str = Query("", alias="user"),
        date_from: str = Query("", alias="from"),
        date_to: str = Query("", alias="to"),
        machine: str = "",
        q: str = "",
        user=Depends(admin_only),
    ):
        limit = max(1, min(limit, 1000))
        conditions = []
        params: list = []
        # אירוע, משתמש וטווח זמן הם עמודות אמיתיות — מסננים בשאילתה עצמה.
        if event:
            conditions.append("event = ?")
            params.append(event)
        if user_filter:
            conditions.append("user = ?")
            params.append(user_filter)
        if date_from:
            conditions.append("ts >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("ts <= ?")
            params.append(date_to)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        # מכונה/MAC וחיפוש חופשי חבויים בתוך detail הגולמי, לא בעמודה
        # (המלכודת ב-#115: מה שהמפעיל רואה, כמו שם כיתה, אינו מה
        # שכתוב בשורה — grp_a3f1 וכו'). לכן סורקים חלון גדול של שורות,
        # מתרגמים אותן כמו למסך, ומסננים על התוצאה המתורגמת — לא על
        # המזהה הגולמי שהמפעיל לא הקליד ולא ראה.
        scan_cap = 20000
        if machine or q:
            scanned = ctx.conn.execute(
                f"SELECT COUNT(*) AS n FROM journal {where}", params
            ).fetchone()["n"]
            # אם יש יותר שורות תואמות-לעמודות מהחלון שסרקנו, זו לא "אין
            # תוצאות" — זו חיפוש שלא כיסה את כל היומן. עיקרון 5: אסור
            # לקפל "לא בדקנו הכל" ל"בדקנו והכל תקין".
            if scanned > scan_cap:
                response.headers["X-Journal-Search-Truncated"] = "true"
        rows = ctx.conn.execute(
            f"SELECT ts, user, event, detail FROM journal {where} "
            "ORDER BY id DESC LIMIT ?",
            (*params, scan_cap),
        ).fetchall()
        translator = JournalTranslator(ctx.conn, ctx.library)
        machine_q = machine.strip().lower()
        text_q = q.strip().lower()
        result = []
        for r in rows:
            label, text = translator.translate(r["event"], r["detail"])
            if machine_q and machine_q not in text.lower() and machine_q not in r["detail"].lower():
                continue
            if text_q and not any(
                text_q in field.lower() for field in (label, text, r["user"])
            ):
                continue
            result.append({
                "ts": r["ts"], "user": r["user"], "event": r["event"],
                "label": label, "text": text,
            })
            if len(result) >= limit:
                break
        return result

    @router.get("/settings")
    def read_settings(user=Depends(admin_only)):
        return {key: get_setting(ctx.conn, key) for key in sorted(WRITE_SETTINGS)}

    @router.post("/settings")
    async def write_settings(request: Request, user=Depends(admin_only)):
        body = await request.json()
        for key, value in body.items():
            if key not in WRITE_SETTINGS:
                raise HTTPException(400, f"הגדרה לא מוכרת: {key}")
            if isinstance(value, bool):
                # ‏JSON true → "True", שנקרא ככבוי (עיקרון 5): מנרמלים
                # למה שהקוראים משווים אליו — "true"/"false".
                value = "true" if value else "false"
            set_setting(ctx.conn, key, str(value))
            journal(ctx.conn, "setting_change", f"{key}={value}", user[0])
        return {"ok": True}

    return router
