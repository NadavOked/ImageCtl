"""אימות לקונסולה — cookie חתום ב-HMAC, בלי תלות חיצונית.

הטוקן: username|role|expiry חתומים בסוד שנולד בהתקנה. אין sessions
בזיכרון — שרת שקם מחדש לא מנתק אף אחד, והחתימה היא ההוכחה.

החתימה מוכיחה זהות בלבד. ההרשאה נקראת מטבלת המשתמשים בכל בקשה, כי
תפקיד שנחתם לפני שעתיים אינו התפקיד של עכשיו (#91).

בלי from __future__ import annotations — האנוטציה Request בתוך
dependencies() חייבת להיות אובייקט אמיתי בשביל FastAPI (אותו באג
שנתפס ב-boot/http.py).
"""

import hashlib
import hmac
import sqlite3
import time

from .db import get_setting

COOKIE_NAME = "imagectl_session"
TTL_SECONDS = 12 * 3600

SECRET_KEY = "console_secret"
#: ‏`db._initialize` זורע 32 בייטים. פחות מ-16 אינו סוד, הוא שריד.
MIN_SECRET_BYTES = 16

_HOW_TO_FIX = (
    "הסוד נולד פעם אחת באתחול בסיס הנתונים "
    "(‏settings.console_secret). אם הוא נעלם — שחזרו את קובץ הנתונים "
    "מגיבוי. יצירת סוד חדש מנתקת את כל המחוברים, ולכן היא החלטה "
    "של מפעיל ולא של השרת."
)


class SecretUnusable(RuntimeError):
    """הסוד שחותם את ה-cookies חסר או פגום.

    לא נתפס בשום מקום, בכוונה: ‏"לא הצלחנו לקרוא את הסוד" איננו
    ‏"אין משתמש מחובר", ובוודאי לא "יש לנו סוד". עיקרון 5.
    """


def dependencies(conn: sqlite3.Connection):
    """(current_user, admin_only) — ה-dependencies המשותפים לכל הראוטרים."""
    from fastapi import Depends, HTTPException, Request

    def current_user(request: Request) -> tuple[str, str]:
        found = check(conn, request.cookies.get(COOKIE_NAME))
        if found is None:
            raise HTTPException(401, "לא מחובר")
        return found

    def admin_only(user: tuple[str, str] = Depends(current_user)) -> tuple[str, str]:
        if user[1] != "admin":
            raise HTTPException(403, "פעולה למנהל בלבד")
        return user

    return current_user, admin_only


def _secret(conn: sqlite3.Connection) -> bytes:
    """הסוד שחותם את ה-cookies, או חריגה. אין ברירת מחדל.

    כאן ישב ``or "00"``: סוד חסר הפך למפתח HMAC של בייט אחד **ידוע**,
    וכל מי שיודע את זה חתם לעצמו ``admin|admin|<תפוגה>`` ונכנס כמנהל
    (#90). זה עיקרון 5 במקום הרגיש ביותר במערכת — הנפילה של המנגנון
    שאמור לקרוא את הסוד הפכה ל"קראנו סוד". מכאן והלאה: מה שלא ניתן
    לקרוא ולאמת מפוצץ את הבקשה, ולא מייצר הרשאה.
    """
    raw = (get_setting(conn, SECRET_KEY) or "").strip()
    if not raw:
        raise SecretUnusable(f"‏{SECRET_KEY} חסר בבסיס הנתונים. {_HOW_TO_FIX}")
    try:
        secret = bytes.fromhex(raw)
    except ValueError:
        raise SecretUnusable(
            f"‏{SECRET_KEY} אינו hex תקין. {_HOW_TO_FIX}"
        ) from None
    if len(secret) < MIN_SECRET_BYTES:
        raise SecretUnusable(
            f"‏{SECRET_KEY} קצר מ-{MIN_SECRET_BYTES} בייטים. {_HOW_TO_FIX}"
        )
    return secret


def assert_secret(conn: sqlite3.Connection) -> None:
    """בדיקת עלייה: קוראים את הסוד פעם אחת, ליד ההתקנה ולא מול כיתה.

    ‏`_secret` כבר מגן על כל בקשה, אבל שרת שעולה עם סוד שבור צריך
    להיכשל בזמן שמישהו מסתכל על המסך — לא בבוקר אחרי, כשמסך התחנה
    מחזיר 500 והתקלה נראית כמו רשת.
    """
    _secret(conn)


def issue(conn: sqlite3.Connection, username: str, role: str) -> str:
    expiry = int(time.time()) + TTL_SECONDS
    payload = f"{username}|{role}|{expiry}"
    signature = hmac.new(_secret(conn), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{signature}"


def check(conn: sqlite3.Connection, token: str | None) -> tuple[str, str] | None:
    """מחזיר (username, role) לטוקן תקף, אחרת None.

    החתימה מוכיחה **מי** — שהטוקן יצא מהשרת הזה ולא שונה בדרך. היא לא
    מוכיחה **מה מותר לו**: התפקיד שבמטען הוא צילום מרגע הכניסה, ומנהל
    שהורד ל-deploy או שנמחק נשאר מנהל עד סוף ה-TTL (#91). לכן ההרשאה
    נקראת מהטבלה בכל בקשה, והטוקן משמש רק כזהות.

    שאילתה לכל בקשה מאומתת היא המחיר, והוא נבדק: ‏SELECT יחיד לפי
    מפתח ראשי, קריאה בלבד, ולכן אינו נוגע בנעילת הכתיבה של WAL.
    ‏`conn` הוא ``db.Database`` — חיבור לכל תהליכון, וזה בדיוק
    התהליכון שבו ‏uvicorn מריץ את ``current_user`` (#54).

    אין כאן מטמון: מטמון היה מחזיר בדיוק את החלון שהתיקון סוגר.
    """
    if not token or token.count("|") != 3:
        return None
    payload, _, signature = token.rpartition("|")
    expected = hmac.new(_secret(conn), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    username, _signed_role, expiry = payload.split("|")
    if int(expiry) < time.time():
        return None
    row = conn.execute(
        "SELECT role, disabled_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None or not row["role"]:
        return None                # נמחק, או שורה בלי תפקיד — אין הרשאה
    if row["disabled_at"]:
        # ‏#186: חסימה חלה **כאן** ולא רק בכניסה. חסימה שנבדקת בכניסה
        # בלבד הייתה פותחת מחדש את החלון שסגר #91 — עד 12 שעות שבהן
        # משתמש חסום ממשיך לעבוד עם הטוקן שכבר בידו.
        return None
    return username, row["role"]
