"""קליטת אימג' — זרימה 13.1, מהקונסולה אל מחשב הבנייה ובחזרה.

הקונסולה יוצרת משימה למכונת בנייה מסוימת. המכונה מקבלת אותה בתשובת
ה-hello (שדה `task` בסעיף 3), קוראת את הדיסק, ומעלה את הקבצים לכאן.

למה כך ולא מסך על מחשב הבנייה: שמות האימג'ים בעברית, וקונסולת לינוקס
לא מרנדרת RTL. ההקלדה נשארת בדפדפן, שם היא עובדת.

האימות בקבלה הוא העיקר: אימג' נכנס לספרייה רק אחרי שכל קובץ נבדק מול
ה-sha256 שבמניפסט. אימג' פגום שמתגלה מול כיתה הוא מה שאסור.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, registry
from .api import ServerContext
from .images import validate_display_name
from .db import journal, now_iso, update_one
from .images import MACHINE_MAC, inside, required_bytes, valid_image_id
from .tasks import active_task, staging_dir

log = logging.getLogger("imagectl.capture")

SAFE_FILE = re.compile(r"^p\d+\.[a-z]+\.pcl\.zst$")
CHUNK = 1024 * 1024

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
        row = ctx.conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None or row["state"] not in ("pending", "running"):
            raise HTTPException(404, "no such open task")
        return row

    async def stream_to(request: Request, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("wb") as handle:
            async for chunk in request.stream():
                digest.update(chunk)
                handle.write(chunk)
        return digest.hexdigest()

    @router.put("/{task_id}/files/{filename}")
    async def upload_partition(task_id: str, filename: str, request: Request):
        row = task_for(task_id, request)
        # רשימה לבנה על שם הקובץ: הוא מגיע ממכונה ברשת הלימודית.
        if not SAFE_FILE.match(filename):
            raise HTTPException(400, "unexpected partition file name")
        folder = staging_dir(ctx.library.root, task_id)
        folder.mkdir(parents=True, exist_ok=True)
        got = await stream_to(request, folder / filename)
        ctx.conn.execute(
            "UPDATE tasks SET state = 'running', updated_at = ? WHERE id = ?",
            (now_iso(), task_id),
        )
        ctx.conn.commit()
        log.info("capture %s: received %s", task_id, filename)
        return {"ok": True, "sha256": got}

    @router.put("/{task_id}/manifest")
    async def finish_capture(task_id: str, request: Request):
        row = task_for(task_id, request)
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
        if manifest.get("family") not in (256, 500):
            return "family must be 256 or 500"
        # בלי דרישת גודל אין החלטה "האם האימג' נכנס לכונן", והאימג' היה
        # נכנס לספרייה כדי להידלג שם בשקט בכל סריקה. נתפס כאן, בשמו.
        if required_bytes(manifest) is None:
            return "cannot determine how much room the image needs"
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
            if part.get("file") is None and part.get("role") == "swap":
                continue                      # swap: recorded, never uploaded
            name = part.get("file") or ""
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
                "bytes_written": r["bytes_written"], "bytes_total": r["bytes_total"],
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

        task_id = "tsk_" + secrets.token_hex(2)
        image_id = "img_" + secrets.token_hex(3)
        now = now_iso()
        ctx.conn.execute(
            "INSERT INTO tasks (id, mac, type, disk, image_id, name, description,"
            " folder, created_by, created_at, updated_at)"
            " VALUES (?, ?, 'capture', ?, ?, ?, ?, ?, ?, ?, ?)",
            (task_id, mac, disk, image_id, name,
             (body.get("description") or "").strip(),
             folder, user[0], now, now),
        )
        ctx.conn.commit()
        journal(ctx.conn, "capture_start", f'{task_id} {mac} disk={disk} "{name}"', user[0])
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
