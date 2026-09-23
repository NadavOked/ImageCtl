"""ה-API של ספריית האימג'ים בקונסולה — תיקיות ועריכת מניפסטים.

העריכה נכתבת ל-manifest.json על הדיסק (מקור האמת). מחיקת אימג' דורשת
הקלדת שמו המדויק — סעיף 15 באפיון.
"""

from __future__ import annotations

import json

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from . import auth
from .api import ServerContext
from .images import validate_display_name
from .library_scrub import forget_scrub, last_scrubs, scrub_library
from .archive import ArchiveError, import_tar, tar_stream
from .db import get_setting, journal, set_setting
from .sessions import ROOM_PREFIX

FOLDERS_KEY = "image_folders"
#: חלון "סבבים לאחרונה" במגירת האימג' (#972, המוקאפ: "ב-30 הימים האחרונים").
ROUNDS_WINDOW = timedelta(days=30)


def _aware(stamp: object) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(stamp))
    except ValueError:
        return None
    return when if when.tzinfo is not None else None


def recent_rounds(conn, now: datetime | None = None) -> dict[str, int | None]:
    """כמה סבבים השתמשו בכל אימג' בחלון ``ROUNDS_WINDOW`` (#972).

    סבב = שורה ב-``sessions`` (כיתה, תחנה, משיכה) **או** סבב חדר ב-
    ``room_rounds``. כל גל של סבב חדר הוא גם שורה ב-``sessions`` (קבוצת
    המשכפלים, קידומת ``ROOM``) — אלה לא נספרים שוב, אחרת סבב של שלושה
    גלים היה נראה כשלושה סבבים. אימג' שאינו במילון = 0 (הטבלאות אינן
    נמחקות לעולם, ולכן היעדר הוא מדידה). ‏``None`` = לפחות שורה אחת
    שחותמת הזמן שלה אינה נקראת — אי אפשר לדעת אם היא בחלון, וספירה
    בלעדיה הייתה מספר קטן שנראה אמיתי (עיקרון 5)."""
    from .room import CLONERS_GROUP  # noqa: PLC0415 — נמנע ממעגל ייבוא, כמו במחיקה
    since = (now or datetime.now(timezone.utc)) - ROUNDS_WINDOW
    rows = list(conn.execute(
        "SELECT image_id, created_at FROM sessions WHERE NOT (group_id = ? AND prefix = ?)",
        (CLONERS_GROUP, ROOM_PREFIX)))
    rows += list(conn.execute("SELECT image_id, created_at FROM room_rounds"))
    counts: dict[str, int | None] = {}
    for row in rows:
        image_id, when = row["image_id"], _aware(row["created_at"])
        if when is None:
            counts[image_id] = None
        elif when >= since and counts.get(image_id, 0) is not None:
            counts[image_id] = counts.get(image_id, 0) + 1
    return counts


def _checked_name(value: str, what: str) -> str:
    """שם תצוגה תקין, או 400 בעברית. הכלל עצמו יושב ב-
    ‏`images.validate_display_name` — מקום אחד שכל נקודות הכניסה עוברות
    דרכו (#138), בנוסח `_checked_name` של console_dhcp (#102)."""
    try:
        return validate_display_name(value, what)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


def create_library_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/images")
    def images(user=Depends(current_user)):
        # ‏#972: שני שדות מה-DB לצד מה שמהדיסק — ‏`last_scrub` (‏null = לא
        # אומת מאז הכניסה) ו-`rounds_30d` (‏null = לא ניתן לספור).
        scrubs, rounds = last_scrubs(ctx.conn), recent_rounds(ctx.conn)
        listed = ctx.library.public_list()
        for image in listed:
            image["last_scrub"] = scrubs.get(image["id"])
            image["rounds_30d"] = rounds.get(image["id"], 0)
        return listed

    @router.get("/images/{image_id}")
    def image_manifest(image_id: str, user=Depends(current_user)):
        """‏#972: המניפסט הציבורי למגירת האימג' — בלי מפתחות `_` (‏`_dir`
        ונתיבי הדיסק). ‏`/api/v1/.../manifest` נשאר של הסוכן (#738)."""
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(404, "אימג' לא קיים")
        return {k: v for k, v in manifest.items() if not k.startswith("_")}

    @router.post("/images/scrub")
    def scrub_images(user=Depends(admin_only)):
        return scrub_library(ctx.library, conn=ctx.conn)

    @router.post("/images/{image_id}/scrub")
    def scrub_image(image_id: str, user=Depends(admin_only)):
        try:
            return scrub_library(ctx.library, image_id, conn=ctx.conn)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except KeyError:
            raise HTTPException(404, "image does not exist")

    @router.put("/images/{image_id}")
    async def edit_image(image_id: str, request: Request, user=Depends(admin_only)):
        changes = await request.json()
        try:
            found = ctx.library.write_meta(image_id, changes)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if not found:
            raise HTTPException(404, "אימג' לא קיים")
        journal(ctx.conn, "image_edit",
                f"{image_id} " + ", ".join(f"{k}={v}" for k, v in changes.items()),
                user[0])
        return {"ok": True}

    @router.post("/images/{image_id}/delete")
    async def delete_image(image_id: str, request: Request, user=Depends(admin_only)):
        # מחיקת אימג' — אישור בהקלדת שם האימג' (סעיף 15 באפיון).
        body = await request.json()
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(404, "אימג' לא קיים")
        if body.get("confirm_name", "") != manifest["name"]:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם האימג'")
        from . import room as room_module  # noqa: PLC0415 — נמנע ממעגל ייבוא
        active_session = ctx.store.active()
        round_row = room_module.active_round(ctx.conn)
        if (active_session is not None and active_session["image_id"] == image_id) \
                or (round_row is not None and round_row["image_id"] == image_id):
            raise HTTPException(409, "האימג' נמצא בשימוש בסשן פעיל")
        ctx.library.delete(image_id)
        forget_scrub(ctx.conn, image_id)
        journal(ctx.conn, "image_delete", f'{image_id} "{manifest["name"]}"', user[0])
        return {"ok": True}

    # --- הורדה למחשב והעלאה ממנו ---------------------------------------------

    @router.get("/images/{image_id}/download")
    def download_image(image_id: str, user=Depends(current_user)):
        """האימג' כולו כקובץ tar אחד, בזרימה — בלי קובץ ביניים בשרת."""
        manifest = ctx.library.get(image_id)
        if manifest is None:
            raise HTTPException(404, "אימג' לא קיים")
        journal(ctx.conn, "image_download", f'{image_id} "{manifest["name"]}"', user[0])
        return StreamingResponse(
            tar_stream(Path(manifest["_dir"]), image_id),
            media_type="application/x-tar",
            headers={"Content-Disposition": f'attachment; filename="{image_id}.tar"'},
        )

    @router.post("/images/upload")
    async def upload_image(request: Request, user=Depends(admin_only)):
        """קליטת אימג' מקובץ tar. הגוף הוא הקובץ עצמו, לא multipart.

        נכתב לדיסק תוך כדי קבלה — אימג' שוקל עשרות ג'יגה ואסור שייכנס
        לזיכרון.
        """
        ctx.library.root.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            delete=False, dir=ctx.library.root, suffix=".upload"
        )
        temp = Path(handle.name)
        try:
            with handle:
                async for chunk in request.stream():
                    handle.write(chunk)
            if temp.stat().st_size == 0:
                raise ArchiveError("לא התקבל קובץ")
            manifest = import_tar(temp, ctx.library.root, set(ctx.library.scan()))
        except ArchiveError as exc:
            raise HTTPException(400, str(exc))
        except Exception as exc:                       # tar פגום, קלט חתוך
            raise HTTPException(400, f"הארכיון לא נקרא: {exc}")
        finally:
            temp.unlink(missing_ok=True)
        # ‏#138, נקודת הכניסה שה-Issue מסמן כמסוכנת ביותר: השם והתיקייה
        # מגיעים **מקובץ שמישהו העלה**, לא מטופס — זה בדיוק המסלול של
        # ‏#110. הבדיקה כאן ולא ב-`import_tar`, כדי ש-sha256 יאמת קודם
        # (עיקרון 6) ואז השם; אימג' שנדחה על שמו מוסר מהספרייה.
        try:
            validate_display_name(manifest["name"], "שם האימג'")
            if manifest.get("folder"):
                validate_display_name(manifest["folder"], "שם התיקייה")
        except ValueError as exc:
            ctx.library.delete(manifest["id"])
            raise HTTPException(400, str(exc))
        journal(ctx.conn, "image_upload",
                f'{manifest["id"]} "{manifest["name"]}"', user[0])
        return {"id": manifest["id"], "name": manifest["name"]}

    def stored_folders() -> dict:
        return json.loads(get_setting(ctx.conn, FOLDERS_KEY) or "{}")

    def existing_folders() -> set[str]:
        """כל שם תיקייה שהמפעיל רואה ב-GET /folders — שמור בקונסולה או
        נגזר ממניפסט קיים. זו ההגדרה של "קיימת" ל-order ול-POST (#526)."""
        known = set(stored_folders())
        known.update(i["folder"] for i in ctx.library.public_list() if i["folder"])
        return known

    @router.get("/folders")
    def folders(user=Depends(current_user)):
        """תיקיות = מה שנוצר בקונסולה + מה שקיים במניפסטים בפועל.

        הסדר הוא סדר המילון השמור — זה מה שהגרירה בקונסולה קובעת.
        תיקייה שקיימת רק במניפסטים מצטרפת לסוף, לפי הא"ב.
        """
        known = stored_folders()
        counts: dict[str, int] = {}
        for image in ctx.library.public_list():
            counts[image["folder"]] = counts.get(image["folder"], 0) + 1
        names = list(known) + sorted(n for n in counts if n and n not in known)
        return [
            {"name": n, "description": known.get(n, ""), "images": counts.get(n, 0)}
            for n in names
        ]

    @router.post("/folders/order")
    async def reorder_folders(request: Request, user=Depends(admin_only)):
        """הסדר שנקבע בגרירה — המילון נכתב מחדש לפי הרשימה."""
        names = (await request.json()).get("names")
        if not isinstance(names, list) or not all(isinstance(n, str) for n in names):
            raise HTTPException(400, "צריך רשימת שמות")
        # סדר אינו יוצר — שם שאינו קיים נדחה, כמו groups/order (#526).
        unknown = [n for n in names if n not in existing_folders()]
        if unknown:
            raise HTTPException(400, f"תיקייה לא קיימת: {unknown[0]}")
        known = stored_folders()
        ordered = {n: known.get(n, "") for n in names}
        for name, description in known.items():        # מי שלא נשלח — נשאר בסוף
            ordered.setdefault(name, description)
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(ordered, ensure_ascii=False))
        journal(ctx.conn, "folder_reorder", ", ".join(names), user[0])
        return {"ok": True}

    @router.put("/folders/{name}")
    async def rename_folder(name: str, request: Request, user=Depends(admin_only)):
        """שינוי שם תיקייה — מעדכן גם את כל המניפסטים שמצביעים עליה."""
        body = await request.json()
        # הערך **הגולמי**: ‏strip לפני הבדיקה הופך "‏LAB1 " לתקין ושומר
        # אותו מקוצץ — וזה בדיוק הניקוי השקט שהאכיפה באה למנוע.
        new_name = body.get("name") or ""
        if new_name:
            new_name = _checked_name(new_name, "שם התיקייה")
        description = body.get("description")
        known = stored_folders()
        if new_name and new_name != name:
            if new_name in known:
                raise HTTPException(409, "כבר יש תיקייה בשם הזה")
            for image in ctx.library.public_list():
                if image["folder"] == name:
                    ctx.library.write_meta(image["id"], {"folder": new_name})
            known[new_name] = known.pop(name, "")
        elif name not in known:
            known[_checked_name(name, "שם התיקייה")] = ""
        target = new_name or name
        if description is not None:
            known[target] = description.strip()
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(known, ensure_ascii=False))
        journal(ctx.conn, "folder_edit", f"{name} -> {target}", user[0])
        return {"name": target}

    @router.post("/folders")
    async def add_folder(request: Request, user=Depends(admin_only)):
        body = await request.json()
        name = _checked_name(body.get("name") or "", "שם התיקייה")
        # יצירה אינה דריסה — שם קיים נדחה, כמו rename_folder (#526).
        if name in existing_folders():
            raise HTTPException(409, "כבר יש תיקייה בשם הזה")
        known = stored_folders()
        known[name] = (body.get("description") or "").strip()
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(known, ensure_ascii=False))
        journal(ctx.conn, "folder_create", name, user[0])
        return {"ok": True}

    @router.post("/folders/{name}/delete")
    def delete_folder(name: str, user=Depends(admin_only)):
        if any(i["folder"] == name for i in ctx.library.public_list()):
            raise HTTPException(409, "התיקייה אינה ריקה — העבירו קודם את האימג'ים")
        known = stored_folders()
        # 200 על תיקייה שלא הייתה הוא "מחקנו" בלי שנמחק דבר — היומן רק
        # אחרי מחיקה שקרתה באמת (#526, משפחת #522).
        if name not in known:
            raise HTTPException(404, "תיקייה לא קיימת")
        known.pop(name)
        set_setting(ctx.conn, FOLDERS_KEY, json.dumps(known, ensure_ascii=False))
        journal(ctx.conn, "folder_delete", name, user[0])
        return {"ok": True}

    return router
