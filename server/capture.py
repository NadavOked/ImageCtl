"""קליטת אימג' — זרימה 13.1, מהקונסולה אל מחשב הבנייה ובחזרה.

הקונסולה יוצרת משימה למכונת בנייה מסוימת. המכונה מקבלת אותה בתשובת
ה-hello (שדה `task` בסעיף 3), קוראת את הדיסק, ומעלה את הקבצים לכאן.

למה כך ולא מסך על מחשב הבנייה: שמות האימג'ים בעברית, וקונסולת לינוקס
לא מרנדרת RTL. ההקלדה נשארת בדפדפן, שם היא עובדת.

האימות בקבלה הוא העיקר: אימג' נכנס לספרייה רק אחרי שכל קובץ נבדק מול
ה-sha256 שבמניפסט. אימג' פגום שמתגלה מול כיתה הוא מה שאסור.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, registry, storage_locations
from .api import ServerContext, identity_gate
from .images import validate_display_name
from .db import journal, now_iso, update_one
from .progress_view import capture_progress
from .images import (
    MACHINE_MAC, carries_a_stream_file, has_disk_guid, has_partition_geometry,
    image_os, inside, required_bytes, valid_image_id,
)
# ⚠️ **לא `from . import tasks`.** יש כאן endpoint בשם `tasks`
# (שורה 280), והוא דורס את המודול — ‏`tasks.new_token` הופך
# ל-`AttributeError` על אובייקט פונקציה. ייבוא שמות מפורש.
from .tasks import (
    OPEN_STATES, TOKEN_HEADER, active_task, claim_task, new_token, staging_dir,
)


def _peer(request: Request) -> str:
    """מי פנה — ליומן בלבד, ולעולם לא כשער.

    ‏#95 כבר קבע ש-IP אינו זהות: הוא מתעדכן בקצב DHCP, והעמוד נפתח
    גם מחוץ למכונה. כאן הוא הראיה שתאפשר לאתר מי ניסה.
    """
    return request.client.host if request.client else "?"

log = logging.getLogger("imagectl.capture")

SAFE_FILE = re.compile(r"^p\d+\.[a-z]+\.pcl\.zst$")
CHUNK = 1024 * 1024


class _DiskFull(Exception):
    """אזל המקום באזור הביניים תוך כדי זרם. הסיבה נוקבת בשם הקובץ."""


def _free_bytes(folder: Path) -> int:
    return shutil.disk_usage(folder).free

#: ממי מותר לקלוט — **רשימת היתר מפורשת**, ולא "כל מי שאינו build" (#381).
#:
#: ‏`classroom` נוסף כאן כדי שמחשב כיתה יקלוט את הדיסק של עצמו (מסלול 8
#: ב-#380, הצד הכותב של #69). ‏`cloner` נשאר **בחוץ**: למכונת שיכפול אין
#: מערכת מקומית משלה, ומשימת קליטה עליה היא בקשה לקרוא דיסק ריק (#17).
#:
#: רשימת היתר ולא רשימת מניעה, מאותו טעם שבו `public-manifest.list` הוא
#: רשימת היתר (#369): תפקיד רביעי שייוולד מחר — צופה, מחשב ניהול — היה
#: מקבל ביום היוולדו את הזכות לקלוט, בשקט.
CAPTURE_ROLES = ("build", "classroom")


def _bind_machine(conn, manifest: dict, mac: str) -> None:
    """קושר את האימג' ל-MAC שנקלט ממנו, או משחרר אותו — ‏#381.

    **הקשירה נכתבת כאן ולא מגיעה מהסוכן.** ‏`machine_mac` שהגיע במניפסט
    שהמכונה העלתה נמחק בכל מקרה, בדיוק כמו `id` ו-`name`: השדה הזה הוא
    שער בטיחות, ומכונה ברשת הלימודית אינה מי שקובעת אותו.

    **הכיוון הבטוח הוא לקשור.** אימג' חופשי נולד רק מ**ראיה חיובית**
    שהמכונה היא מחשב בנייה — הרשומה קיימת והתפקיד `build`. מכונה שנמחקה
    מהמרשם בין יצירת המשימה לסיומה, או תפקיד שאיננו מכירים, מסתיימים
    באימג' קשור: "לא ידענו" אינו "מותר לכולם" (עיקרון 5).
    """
    machine = registry.lookup(conn, mac)
    if machine is not None and machine["role"] == "build":
        manifest.pop(MACHINE_MAC, None)
        return
    manifest[MACHINE_MAC] = mac


def _fail(conn, task_id: str, message: str) -> None:
    conn.execute(
        "UPDATE tasks SET state = 'failed', error = ?, updated_at = ? WHERE id = ?",
        (message, now_iso(), task_id),
    )
    conn.commit()
    journal(conn, "capture_failed", f"{task_id} {message}")


def create_agent_capture_router(ctx: ServerContext) -> APIRouter:
    """מה שמחשב הבנייה מדבר איתו. הרשאה: ה-MAC חייב להיות בעל המשימה."""
    router = APIRouter(prefix="/api/v1/capture")

    def task_for(task_id: str, request: Request):
        """המשימה, אם הפונה הוא בעליה.

        עד ‏#530 הפרמטר ``request`` התקבל כאן **ולא היה בשימוש**,
        בזמן שה-docstring של הראוטר הצהיר "ההרשאה: ה-MAC חייב להיות
        בעל המשימה". כל פונה שהכיר `task_id` — ‏65,536 ערכים
        אפשריים — העלה אימג' משלו, והשרת פרסם אותו.

        **שלוש שאלות שונות, שלוש תשובות:** משימה שאינה קיימת או
        שנסגרה → ‏404 · בקשה בלי אסימון → ‏401 · אסימון שאינו של
        המשימה הזו → ‏403. קיפולן לאחת היה מספר לפונה מה קיים.
        """
        token = request.headers.get(TOKEN_HEADER, "")
        row = ctx.conn.execute(
            "SELECT id, state FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None or row["state"] not in OPEN_STATES:
            raise HTTPException(404, "no such open task")
        if not token:
            raise HTTPException(401, f"missing {TOKEN_HEADER}")
        claimed = claim_task(ctx.conn, task_id, token)
        if claimed is None:
            journal(ctx.conn, "task_token_refused", f"{task_id} from {_peer(request)}")
            raise HTTPException(403, "this is not your task")
        return claimed

    async def stream_to(request: Request, path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("wb") as handle:
                async for chunk in request.stream():
                    digest.update(chunk)
                    handle.write(chunk)
        except OSError as exc:
            # ‏#411: דיסק שהתמלא תוך כדי זרם הוא כשל גלוי (עיקרון 4), לא 500
            # באמצע עם קובץ חלקי שנשאר. שאר ה-OSError ממשיכים כשהיו.
            if exc.errno != errno.ENOSPC:
                raise
            path.unlink(missing_ok=True)
            raise _DiskFull(
                f"אזל המקום בשרת בזמן כתיבת {path.name} — הדיסק התמלא") from exc
        return digest.hexdigest()

    @router.put("/{task_id}/files/{filename}")
    async def upload_partition(task_id: str, filename: str, request: Request):
        row = task_for(task_id, request)
        refused = identity_gate(ctx, row["mac"], request, "capture-files")
        if refused is not None:
            return refused
        # רשימה לבנה על שם הקובץ: הוא מגיע ממכונה ברשת הלימודית.
        if not SAFE_FILE.match(filename):
            raise HTTPException(400, "unexpected partition file name")
        folder = staging_dir(ctx.library.root, task_id)
        folder.mkdir(parents=True, exist_ok=True)
        # ‏#411: הזמנת מקום לפני הבייט הראשון. ההעלאה מזרימה ב-chunked (‏`curl
        # -T` על fifo, כדי לא להיחנק ב-OOM — ‏agent/lib/capture.sh), ולכן לרוב
        # אין Content-Length; כשיש, מקום פנוי שאינו מכיל אותו נדחה כאן ומספרי,
        # כמו הסירוב על "דיסק קטן מדי". בלי גודל מוצהר די ב-chunk אחד של מקום
        # כדי להתחיל — ‏ENOSPC תוך כדי זרם נתפס ב-`stream_to` ככשל גלוי.
        declared = request.headers.get("content-length")
        needed = int(declared) if declared and declared.isdigit() else CHUNK
        free = _free_bytes(folder)
        if free < needed:
            reason = (f"אין מקום בשרת לאחסן את הקליטה: {filename} דורש {needed}"
                      f" בייט, פנויים {free} בלבד")
            _fail(ctx.conn, task_id, reason)
            raise HTTPException(507, reason)
        try:
            got = await stream_to(request, folder / filename)
        except _DiskFull as exc:
            _fail(ctx.conn, task_id, str(exc))
            raise HTTPException(507, str(exc))
        # ‏#532: תביעת מצב ב-`WHERE`. ‏`task_for` בדק **לפני** ההזרמה,
        # וההזרמה של מחיצה נמשכת דקות — מנהל שביטל בינתיים קיבל
        # `ok: true`, והשורה הזו החזירה את המשימה ל-`running`.
        # ובלינוקס גם תיקיית ה-staging כבר הוסרה, וה-handle הפתוח
        # ממשיך לכתוב אל inode שנמחק.
        if not update_one(
            ctx.conn,
            "UPDATE tasks SET state = 'running', updated_at = ?"
            " WHERE id = ? AND state IN ('pending', 'running')",
            (now_iso(), task_id),
        ):
            (folder / filename).unlink(missing_ok=True)
            raise HTTPException(409, "the task is no longer open")
        ctx.conn.commit()
        log.info("capture %s: received %s", task_id, filename)
        return {"ok": True, "sha256": got}

    @router.put("/{task_id}/manifest")
    async def finish_capture(task_id: str, request: Request):
        row = task_for(task_id, request)
        refused = identity_gate(ctx, row["mac"], request, "capture-manifest")
        if refused is not None:
            return refused
        # המזהה נוצר כאן בשרת, ולכן הבדיקה הזאת אינה אמורה להיכשל לעולם —
        # וזו הסיבה שהיא כתובה: המזהה הופך לשם תיקייה, והכלל הזה נאכף
        # בשני המסלולים שמכניסים אימג' לספרייה, לא רק בזה שקלט מבחוץ
        # (‏#110). אם הוא נכשל, השורה בבסיס הנתונים אינה מה שחשבנו.
        if not valid_image_id(row["image_id"]):
            _fail(ctx.conn, task_id, f'malformed image id on task: {row["image_id"]!r}')
            raise HTTPException(500, "task carries a malformed image id")
        folder = staging_dir(ctx.library.root, task_id)
        raw = await request.body()
        try:
            manifest = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            # קטע מהגוף נכנס ליומן: "לא JSON" בלי ראיה השאיר אותנו
            # מגששים מול סוכן שאי אפשר להיכנס אליו (מעבדה, #12).
            snippet = raw[:160].decode("utf-8", "replace")
            _fail(ctx.conn, task_id, f"manifest is not valid JSON: {snippet}")
            raise HTTPException(400, "manifest is not valid JSON")

        problem = _validate(manifest, folder, row)
        if problem:
            _fail(ctx.conn, task_id, problem)
            shutil.rmtree(folder, ignore_errors=True)
            raise HTTPException(400, problem)

        # שמות התצוגה נקבעו בקונסולה ולא במחשב הבנייה.
        manifest["id"] = row["image_id"]
        manifest["name"] = row["name"]
        manifest["description"] = row["description"]
        manifest["folder"] = row["folder"]
        manifest["created"] = now_iso()
        manifest["created_by"] = row["created_by"]
        _bind_machine(ctx.conn, manifest, row["mac"])
        # ‏#82: הדרישה נגזרת מהפריסה, ולא מגודל דיסק המקור שהסוכן שלח.
        # הנרמול כאן ולא רק בקריאה כדי שהערך שמונח בספרייה יהיה הנכון —
        # הדיסק הוא מקור האמת, וסוכן ישן שממשיך לשלוח את גודל המקור
        # אינו מכניס ערך שגוי לתיקייה. ‏_validate כבר ווידא שיש מה לגזור.
        manifest["min_target_bytes"] = required_bytes(manifest)
        (folder / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        target = inside(ctx.library.root / row["image_id"], ctx.library.root)
        if target is None:
            _fail(ctx.conn, task_id, "image target falls outside the library root")
            raise HTTPException(500, "image target falls outside the library root")
        if target.exists():
            _fail(ctx.conn, task_id, "an image with this id already exists")
            raise HTTPException(409, "image already exists")
        folder.rename(target)

        ctx.conn.execute(
            "UPDATE tasks SET state = 'done', updated_at = ? WHERE id = ?",
            (now_iso(), task_id),
        )
        ctx.conn.commit()
        journal(ctx.conn, "capture_done",
                f'{row["image_id"]} "{row["name"]}"', row["created_by"])
        return {"ok": True, "image_id": row["image_id"]}

    def _validate(manifest: object, folder: Path, row) -> str | None:
        if not isinstance(manifest, dict) or manifest.get("schema") != 1:
            return "manifest schema must be 1"
        parts = manifest.get("partitions")
        if not isinstance(parts, list) or not parts:
            return "manifest has no partitions"
        for part in parts:
            if not has_partition_geometry(part):
                index = part.get("index") if isinstance(part, dict) else None
                return (f"partition {index!r} has malformed geometry:"
                        " start_sector and size_bytes must both be positive")
        if manifest.get("family") not in (256, 500):
            return "family must be 256 or 500"
        # בלי דרישת גודל אין החלטה "האם האימג' נכנס לכונן", והאימג' היה
        # נכנס לספרייה כדי להידלג שם בשקט בכל סריקה. נתפס כאן, בשמו.
        if required_bytes(manifest) is None:
            return "cannot determine how much room the image needs"
        # אימג' Windows קושר את ה-BCD ל-GUID של הדיסק — בלעדיו כל מחשב
        # משוחזר עולה ל-winload.efi 0xc000000e (#26, #571). ‏disk_guid ריק
        # אינו "אין צורך ב-GUID" אלא "הקליטה לא הצליחה לקרוא אותו": הסוכן
        # גוזר אותו מ-`sgdisk -p`, וכשל קריאה יוצא מחרוזת ריקה. נתפס כאן,
        # בקליטה, ולא מול כיתה (עיקרון 6) — אימג' פגום אינו נכנס לספרייה,
        # במקום שהשחזור ידלג עליו בשקט ויגיע ל-done. לינוקס פטור: ‏GRUB
        # מאתר לפי UUID של מערכת הקבצים, ולכן הוא נקלט בלי disk_guid כרגיל.
        if image_os(manifest) == "windows" and not has_disk_guid(manifest):
            return ("a windows image needs its source disk_guid (the BCD binds"
                    " to it) but the manifest has none -- capture did not read it")
        expandable = [p for p in parts if p.get("expandable")]
        if len(expandable) > 1:
            return "at most one expandable partition"
        if expandable:
            # אחרי המחיצה המורחבת מותרת כל מחיצה שאינה windows/linux (‏#58,
            # מרחיב את #46). הסוכן מעביר את כל הזנב הזה לסוף הכונן לפני
            # שנכתב בייט: ‏swap נבראת שם מחדש (אפיון סעיף 14) וכל השאר —
            # ‏recovery של Windows 11, למשל — נכתבת שם מקובץ הזרם שלה.
            # הכלל הישן ("רק swap") אמר שאף אימג' Windows לא יורחב לעולם.
            # מה שנשאר אסור הוא מחיצת מערכת אחרי המורחבת: אז המועמד אינו
            # האחרון על הדיסק, ולמתוח אותו היה דורס אותה.
            #
            # ההשוואה לפי start_sector ולא לפי סדר הרשימה — הרשימה בסדר
            # אינדקסים, ובאימג' ענן השורש הוא מחיצה 1 שרשומה ראשונה
            # ויושבת אחרונה. אותו כלל בדיוק שהסוכן מסמן לפיו.
            start = expandable[0].get("start_sector") or 0
            after = [p for p in parts if (p.get("start_sector") or 0) > start]
            if any(p.get("role") in ("windows", "linux") for p in after):
                return "no system partition may follow the expandable partition"
        for part in parts:
            # הפטור מקובץ נשען על `fs` ולא על `role` (#424): הסוכן מכריע
            # לפי `fs` בלבד, ורשומה עם `role: "swap"` ו-`fs: "ntfs"` עברה
            # כאן **בלי שום בדיקה** ואז קיבלה `mkswap` על מחיצת ווינדוס.
            if not carries_a_stream_file(part):
                if part.get("fs") == "swap":
                    continue                  # swap: recorded, never uploaded
                return (f"partition {part.get('index')} ({part.get('fs')}) has no"
                        " file, and only a swap partition may have none")
            name = part["file"]
            if not SAFE_FILE.match(name):
                return f"unexpected partition file name: {name}"
            path = folder / name
            if not path.is_file():
                return f"partition file was never uploaded: {name}"
            if _sha256(path) != part.get("sha256"):
                return f"sha256 mismatch on {name}"
        return None

    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(CHUNK):
                digest.update(chunk)
        return digest.hexdigest()

    return router


def create_console_capture_router(ctx: ServerContext) -> APIRouter:
    """מה שהקונסולה מדברת איתו — יצירת משימה, מעקב, ביטול."""
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/tasks")
    def tasks(user=Depends(current_user)):
        rows = ctx.conn.execute(
            "SELECT t.*, m.suffix, g.label AS group_label FROM tasks t"
            " LEFT JOIN machines m ON m.mac = t.mac"
            " LEFT JOIN groups g ON g.id = m.group_id"
            " ORDER BY t.created_at DESC LIMIT 20"
        ).fetchall()
        return [
            {
                "id": r["id"], "mac": r["mac"], "machine": r["suffix"],
                "group_label": r["group_label"], "type": r["type"],
                "disk": r["disk"], "image_id": r["image_id"], "name": r["name"],
                "state": r["state"], "error": r["error"],
                # ‏#435: ‏bytes_* דחוסים, ‏source_progress בציר הבלוקים —
                # ‏`capture_progress` מקפל אפס-מכנה ל-`None` (לא ל-`0%`).
                **capture_progress(r),
                "created_at": r["created_at"], "updated_at": r["updated_at"],
            }
            for r in rows
        ]

    @router.post("/tasks/capture")
    async def create_capture(request: Request, user=Depends(admin_only)):
        try:
            body = await request.json()
        except ValueError:
            # גוף פגום הוא שגיאת לקוח (400), לא קריסת שרת (500).
            raise HTTPException(400, "הגוף אינו JSON תקין")
        mac = registry.normalize_mac(body.get("mac", ""))
        machine = registry.lookup(ctx.conn, mac) if mac else None
        if machine is None:
            raise HTTPException(400, "מכונה לא רשומה")
        role = machine["role"]
        if role not in CAPTURE_ROLES:
            raise HTTPException(
                400,
                "קליטת אימג' אינה נעשית ממכונה בתפקיד "
                f"{role!r} — אין לה מערכת מקומית משלה שיש מה לקלוט ממנה")
        name = (body.get("name") or "").strip()
        disk = (body.get("disk") or "").strip()
        if not name or not disk:
            raise HTTPException(400, "צריך שם אימג' ודיסק מקור")
        # ‏#138: השם והתיקייה נבדקים כאן ולא במסך — מסך התחנה הוא קונסולת
        # טקסט של לינוקס, בלי גליפים עבריים ובלי RTL.
        # הערכים **הגולמיים** נבדקים, לא המנוקים: רווח בקצה נדחה בשמו.
        folder = body.get("folder") or ""
        try:
            name = validate_display_name(body.get("name") or "", "שם האימג'")
            if folder:
                folder = validate_display_name(folder, "שם התיקייה")
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if active_task(ctx.conn, mac) is not None:
            raise HTTPException(409, "כבר יש משימה פתוחה למכונה הזו")
        loc_id = body.get("location_id") or storage_locations.LOCAL_ID
        if not storage_locations.location_writable(ctx.conn, loc_id):
            raise HTTPException(409, "המיקום אינו זמין לקליטה")

        task_id = "tsk_" + secrets.token_hex(2)
        image_id = "img_" + secrets.token_hex(3)
        now = now_iso()
        ctx.conn.execute(
            "INSERT INTO tasks (id, mac, type, disk, image_id, name, description,"
            " folder, created_by, created_at, updated_at, token)"
            " VALUES (?, ?, 'capture', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, mac, disk, image_id, name,
             (body.get("description") or "").strip(),
             folder, user[0], now, now, new_token()),
        )
        ctx.conn.commit()
        journal(ctx.conn, "capture_start", f'{task_id} {mac} disk={disk} "{name}"', user[0])
        # ‏**האסימון אינו מוחזר כאן.** הוא נמסר רק ב-hello, ורק למכונה
        # שה-MAC שלה תואם — אחרת הוא היה יוצא לקונסולה, ליומן ולכל
        # מי שרואה את התשובה.
        return {"id": task_id, "image_id": image_id}

    @router.post("/tasks/{task_id}/cancel")
    def cancel(task_id: str, user=Depends(admin_only)):
        if not update_one(
            ctx.conn,
            "UPDATE tasks SET state = 'cancelled', updated_at = ? WHERE id = ?"
            " AND state IN ('pending', 'running')",
            (now_iso(), task_id),
        ):
            raise HTTPException(409, "המשימה כבר אינה פתוחה")
        ctx.conn.commit()
        shutil.rmtree(staging_dir(ctx.library.root, task_id), ignore_errors=True)
        journal(ctx.conn, "capture_cancel", task_id, user[0])
        return {"ok": True}

    return router
