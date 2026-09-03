"""לוגו המוסד — מחליף את סמל ברירת המחדל בקונסולה.

הקובץ יושב בתיקיית הנתונים ולא ב-static, כדי שעדכון גרסה של הקוד לא
ידרוס אותו ושגיבוי של תיקיית הנתונים יכלול אותו.

ההגשה פתוחה בלי כניסה: מסך הכניסה עצמו מציג את הלוגו, ולוגו של מכללה
אינו סוד.

‏SVG הוא היוצא מן הכלל שדורש עבודה: הוא מסמך, לא רק תמונה. ראו
``_svg_refusal`` ואת כותרות ההגשה.
"""

from __future__ import annotations

import re
# ‏defusedxml אינה נדרשת כאן, וההסבר המלא ב-`_svg_refusal`: השומר על
# תוכן פעיל רץ **לפני** הפענוח ו-`_ACTIVE` כולל `<!ENTITY`. נמדד:
# פצצת ישויות נדחית, DTD חיצוני אינו נטען, ו-XXE נופל ב-ParseError.
import xml.etree.ElementTree as ET  # nosemgrep
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from . import auth
from .api import ServerContext
from .db import journal

TYPES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}
MAX_BYTES = 2 * 1024 * 1024
LOGO_STEM = "logo"
SVG_MEDIA = "image/svg+xml"

#: הקונסולה טוענת את הלוגו ב-``<img>``, ושם סקריפט בתוך SVG אכן לא רץ.
#: זו הייתה ההנמקה שבקוד — והיא תיארה מסלול אחד מתוך שניים. הקובץ מוגש
#: גם בכתובת משלו, על אותו origin שמחזיק את ה-cookie של הקונסולה, ודפדפן
#: שמנווט אליה ישירות מקבל **מסמך** SVG. שם סקריפט רץ (#97).
#:
#: לכן הכותרות האלה, ולא רק הבדיקה בהעלאה: הן מגינות גם על לוגו שכבר
#: יושב על הדיסק מלפני התיקון, ואינן תלויות בכך שהבדיקה תפסה הכל.
SERVE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
}

#: תוכן פעיל ב-SVG. ``<!ENTITY`` נבדק לפני הפענוח ולא אחריו — הרחבת
#: ישויות היא זו שמפוצצת את המפענח.
_ACTIVE = re.compile(
    r"<\s*script|<\s*foreignObject|<\s*!ENTITY|javascript\s*:|\son[a-zA-Z]+\s*=",
    re.IGNORECASE,
)
_SVG_ROOTS = ("{http://www.w3.org/2000/svg}svg", "svg")


def _svg_refusal(body: bytes) -> str | None:
    """סיבת דחייה ל-SVG, או ``None`` אם הוא נקי — לפי **ראיה חיובית**.

    לא "לא מצאנו ``<script>``, כנראה בסדר" אלא "פענחנו את הקובץ,
    השורש הוא svg, ולא נמצא בו תוכן פעיל". קובץ שלא הצלחנו לפענח נדחה:
    בדיקה שנופלת ומחזירה "עבר" היא הפרצה עצמה, לא השומר עליה.
    """
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return "לא הצלחנו לקרוא את ה-SVG כטקסט"
    found = _ACTIVE.search(text)
    if found:
        return f"‏SVG עם תוכן פעיל ({found.group(0).strip()}) — נדחה"
    # ‏nosemgrep: use-defused-xml -- ו-CodeQL py/xml-bomb נסגרה מאותה סיבה.
    # הבדיקה על תוכן פעיל רצה **לפני** השורה הזאת, ו-`_ACTIVE` כולל
    # ‏`<!ENTITY` — פצצת ישויות נדחית לפני שהפענוח מתחיל. נמדד:
    #   ENTITY bomb  -> נדחה   ·   DTD חיצוני -> לא נטען   ·   XXE -> ParseError
    # ‏`defusedxml` הייתה תלות שלישית בשביל בדיקה שכבר קיימת, ו-
    # ‏`requirements.txt` מצמיד בכוונה שתי תלויות בלבד.
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return f"‏SVG שאינו XML תקין ({exc.msg if hasattr(exc, 'msg') else exc})"
    if root.tag not in _SVG_ROOTS:
        return "הקובץ אינו SVG — שורש המסמך אינו svg"
    return None


def find_logo(data_dir: Path) -> Path | None:
    for suffix in TYPES.values():
        candidate = data_dir / "branding" / (LOGO_STEM + suffix)
        if candidate.is_file():
            return candidate
    return None


def create_branding_router(ctx: ServerContext, data_dir: Path) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    branding = data_dir / "branding"

    @router.get("/branding/logo")
    def get_logo():
        path = find_logo(data_dir)
        if path is None:
            return Response(status_code=204)      # אין לוגו — הקונסולה תציג ברירת מחדל
        media = next(t for t, s in TYPES.items() if s == path.suffix)
        return FileResponse(path, media_type=media, headers=SERVE_HEADERS)

    @router.post("/branding/logo")
    async def set_logo(request: Request, user=Depends(admin_only)):
        media = (request.headers.get("content-type") or "").split(";")[0].strip()
        if media not in TYPES:
            raise HTTPException(400, "סוג קובץ לא נתמך — PNG, JPG, WEBP או SVG")
        body = b""
        async for chunk in request.stream():
            body += chunk
            if len(body) > MAX_BYTES:
                raise HTTPException(400, "הקובץ גדול מ-2MB")
        if not body:
            raise HTTPException(400, "לא התקבל קובץ")
        if media == SVG_MEDIA:
            refusal = _svg_refusal(body)
            if refusal is not None:
                journal(ctx.conn, "logo_refused", refusal, user[0])
                raise HTTPException(400, refusal)

        branding.mkdir(parents=True, exist_ok=True)
        for suffix in TYPES.values():             # לוגו אחד בכל רגע
            (branding / (LOGO_STEM + suffix)).unlink(missing_ok=True)
        (branding / (LOGO_STEM + TYPES[media])).write_bytes(body)
        journal(ctx.conn, "logo_set", f"{media} {len(body)} bytes", user[0])
        return {"ok": True}

    @router.delete("/branding/logo")
    def clear_logo(user=Depends(admin_only)):
        for suffix in TYPES.values():
            (branding / (LOGO_STEM + suffix)).unlink(missing_ok=True)
        journal(ctx.conn, "logo_clear", "", user[0])
        return {"ok": True}

    return router
