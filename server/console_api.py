"""ה-API של הקונסולה — מה שהדפדפן מדבר איתו.

RBAC לפי סעיף 11: משתמש deploy יכול לראות אימג'ים ולנהל סבב הפצה,
ותו לא. ניהול — קבוצות, טבלאות MAC, משתמשים, יומן, הגדרות — admin בלבד.
כל פעולה כותבת נרשמת ביומן עם שם המשתמש.
"""

from __future__ import annotations

import re
import sqlite3
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from . import auth, registry, users
from .api import ServerContext
from .db import get_setting, journal, now_iso, set_setting, update_one
from .journal_he import JournalTranslator
from .sessions import SessionError

WRITE_SETTINGS = {"recovery_require_login", "session_wait_seconds",
                  "console_idle_seconds"}


def create_console_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

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
        return {
            "username": user[0],
            "role": user[1],
            "idle_seconds": int(get_setting(ctx.conn, "console_idle_seconds") or 300),
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
            ctx.conn.execute(
                "INSERT INTO groups (id, label, role, sort) VALUES (?, ?, ?, ?)",
                (gid, label, role, (last or 0) + 1),
            )
        except sqlite3.IntegrityError:
            # ‏rollback לפני ה-raise, ולא אחרי (#184). ה-INSERT שנכשל
            # כבר פתח טרנזאקציית כתיבה; חריגה שיוצאת בלי לסגור אותה
            # משאירה **נעילה יתומה**, והכתיבה הבאה נופלת ב-
            # ‏`database is locked` אחרי `busy_timeout` שלם — בלי שום
            # קשר נראה לעין בין השתיים. זה ה-gotcha של #54.
            #
            # ותופסים `IntegrityError` ולא `Exception`: כשל אחר —
            # דיסק מלא, סכימה שהשתנתה — היה מתחפש כאן ל"כבר קיימת",
            # וזו בדיוק ההודעה שתשלח את המפעיל לחפש במקום הלא נכון.
            ctx.conn.rollback()
            raise HTTPException(409, "קבוצה בשם הזה כבר קיימת")
        ctx.conn.commit()
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
        for index, gid in enumerate(ids):
            ctx.conn.execute("UPDATE groups SET sort = ? WHERE id = ?", (index, gid))
        ctx.conn.commit()
        journal(ctx.conn, "group_reorder", ", ".join(ids), user[0])
        return {"ok": True}

    @router.put("/groups/{gid}")
    async def rename_group(gid: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        label = (body.get("label") or "").strip()
        if not label:
            raise HTTPException(400, "שם ריק")
        if not update_one(ctx.conn,
                          "UPDATE groups SET label = ? WHERE id = ?", (label, gid)):
            raise HTTPException(404, "קבוצה לא קיימת")
        ctx.conn.commit()
        journal(ctx.conn, "group_edit", f"{gid} label={label}", user[0])
        return {"ok": True}

    @router.delete("/groups/{gid}")
    def del_group(gid: str, user=Depends(admin_only)):
        from .db import FIXED_GROUPS
        if gid in {g[0] for g in FIXED_GROUPS}:
            # חדר השיכפולים ומחשב הבנייה הם יחידים במערכת — אין להם
            # ניהול קבוצות, ולכן גם אי אפשר למחוק אותם.
            raise HTTPException(400, "קבוצה קבועה — אפשר להסיר ממנה מכונות, לא למחוק אותה")
        ctx.conn.execute("DELETE FROM groups WHERE id = ?", (gid,))
        ctx.conn.commit()
        journal(ctx.conn, "group_delete", gid, user[0])
        return {"ok": True}

    @router.get("/machines")
    def machines(group: str | None = None, user=Depends(current_user)):
        query = (
            "SELECT mac, suffix, group_id, note, added_at FROM machines"
            + (" WHERE group_id = ?" if group else "")
            + " ORDER BY group_id, suffix"
        )
        rows = ctx.conn.execute(query, (group,) if group else ()).fetchall()
        return [dict(r) for r in rows]

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
        return {"saved": saved, "rejected": [vars(l) for l in rejected]}

    @router.post("/machines")
    async def add_machine(request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            mac = registry.add_machine(
                ctx.conn, body.get("mac", ""), body.get("name", ""),
                body.get("group_id", ""), user[0],
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"mac": mac}

    @router.put("/machines/{mac}")
    async def edit_machine(mac: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        try:
            registry.update_machine(
                ctx.conn, mac, body.get("name"), body.get("group_id"), user[0]
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    def registry_group_exists(gid: str) -> bool:
        return ctx.conn.execute("SELECT 1 FROM groups WHERE id = ?", (gid,)).fetchone() is not None

    @router.delete("/machines/{mac}")
    def del_machine(mac: str, user=Depends(admin_only)):
        canonical = registry.normalize_mac(mac)
        ctx.conn.execute("DELETE FROM machines WHERE mac = ?", (canonical,))
        ctx.conn.commit()
        journal(ctx.conn, "machine_delete", canonical or mac, user[0])
        return {"ok": True}

    @router.get("/machines.csv")
    def machines_csv(user=Depends(admin_only)):
        return PlainTextResponse(registry.export_csv(ctx.conn), media_type="text/csv")

    # --- סבבים (גם deploy) ---------------------------------------------------

    @router.post("/sessions")
    async def open_session(request: Request, user=Depends(current_user)):
        """פתיחת סבב כיתה — מהקונסולה או ממסך מחשב הבנייה (אותו cookie).

        `macs` (רשות) — בחירת מחשבים: רק הם מוערים ומצטרפים. קידומת
        ומספר מחשבים הם רשות — נגזרים מהקבוצה ומהבחירה, כמו בתחנה.
        """
        body = await request.json()
        image_id = body.get("image_id", "")
        group_id = body.get("group_id", "")
        if ctx.library.get(image_id) is None:
            raise HTTPException(400, "אימג' לא קיים בספרייה")
        if not registry_group_exists(group_id):
            raise HTTPException(400, "קבוצה לא קיימת")

        roster = None
        if body.get("macs") is not None:
            if not isinstance(body["macs"], list):
                raise HTTPException(400, "macs חייב להיות רשימה")
            roster = sorted({registry.normalize_mac(m) for m in body["macs"]})
            if not roster or None in roster:
                raise HTTPException(400, "בחירת המחשבים ריקה או מכילה MAC פגום")

        machines = ctx.conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (group_id,)
        ).fetchone()["n"]
        expected = int(body.get("expected_clients") or 0) \
            or (len(roster) if roster else machines)
        prefix = body.get("prefix") or group_id.removeprefix("grp_").upper()
        try:
            session_id = ctx.store.open(
                group_id, image_id, prefix, expected,
                opened_by=user[0], roster=roster,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(409, str(exc))
        return {"id": session_id}

    @router.post("/sessions/{session_id}/start")
    def start_session(session_id: str, user=Depends(current_user)):
        try:
            ctx.store.start_now(session_id, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @router.post("/sessions/{session_id}/close")
    def close_session(session_id: str, user=Depends(current_user)):
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
        except Exception:
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
        if users.admin_count(ctx.conn) <= 1:
            row = ctx.conn.execute(
                "SELECT role FROM users WHERE username = ?", (username,)
            ).fetchone()
            if row and row["role"] == "admin":
                raise HTTPException(400, "זה המנהל האחרון — מחיקתו תנעל את הקונסולה")
        users.delete(ctx.conn, username, by=user[0])
        return {"ok": True}

    @router.get("/journal")
    def read_journal(limit: int = 200, user=Depends(admin_only)):
        rows = ctx.conn.execute(
            "SELECT ts, user, event, detail FROM journal ORDER BY id DESC LIMIT ?",
            (min(limit, 1000),),
        ).fetchall()
        # התרגום בקריאה: השורות נשמרות גולמיות, השמות נפתרים לפי ההווה.
        translator = JournalTranslator(ctx.conn, ctx.library)
        result = []
        for r in rows:
            label, text = translator.translate(r["event"], r["detail"])
            result.append({
                "ts": r["ts"], "user": r["user"], "event": r["event"],
                "label": label, "text": text,
            })
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
            set_setting(ctx.conn, key, str(value))
            journal(ctx.conn, "setting_change", f"{key}={value}", user[0])
        return {"ok": True}

    return router
