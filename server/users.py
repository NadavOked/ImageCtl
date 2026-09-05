"""משתמשים והרשאות — סעיף 11 באפיון.

שני תפקידים בלבד: admin (הכל) ו-deploy (לבחור אימג' ולהפיץ, ותו לא).
משתמש הפצה מקליד סיסמה על מסך שעומד בכיתה — גם אם היא דולפת, הכי גרוע
שיקרה זה התקנה מחדש של אימג' תקין.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3

from .db import _write_lock, journal, now_iso, writing

#: מספר הסבבים ל**סיסמאות חדשות**. סיסמה קיימת נבדקת במספר שנשמר איתה.
_ITERATIONS = 200_000
_SCHEME = "pbkdf2"

#: ‏`|` מפריד בין השדות בטוקן של הקונסולה (`auth.check`), ולכן שם
#: שמכיל אותו מייצר חשבון שאי אפשר להתחבר אליו לעולם.
_FORBIDDEN_IN_USERNAME = "|"

#: כל כתיבה כאן עוברת ב-``with _write_lock, writing(conn)`` — שני
#: המנגנונים של `db.py`, מאותה סיבה שבגללה `net_seen` קיבל אותם (#272,
#: ‏#356, ‏#313): כתיבה שנכשלה בלי ``rollback`` משאירה את החיבור בתוך
#: טרנזאקציה, ומשם **כל** כתיבה עליו נכשלת מיד עד אתחול השרת — כלומר
#: ניהול משתמשים שהפסיק לעבוד. ‏`journal` נוטל את אותה נעילה בעצמו,
#: והיא ``Lock`` ולא ``RLock``, ולכן הרישום ביומן נשאר **מחוץ** לבלוק.


def _derive(password: str, salt: str, iterations: int) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), iterations
    ).hex()


#: אנגלית בלבד, לפי הכרעת נדב (#111). רשימת-היתר ולא רשימת-איסור.
_USERNAME_RE = re.compile(r"[A-Za-z0-9._\- ]{1,32}")


def _hash(password: str, salt: str, iterations: int = _ITERATIONS) -> str:
    """הרשומה נושאת את הפרמטרים שלה — סכימה ומספר סבבים — כדי שאפשר
    יהיה לאמת אותה גם אחרי שברירת המחדל תשתנה."""
    return f"{_SCHEME}${iterations}${salt}${_derive(password, salt, iterations)}"


def _check_username(username: str) -> None:
    """שם משתמש הוא אנגלית בלבד — החלטה, לא עקיפה.

    רשימת-היתר ולא רשימת-איסור: ‏`[A-Za-z0-9._-]`, עד 32 תווים. שלושת
    האיסורים שקדמו לה כיסו כל אחד באג אחר, ורשימת איסורים תמיד מפספסת
    את הבא בתור.

    מה שכל אחד מהם היה:

    * ‏`|` — הטוקן הוא ``username|role|expiry|signature``, ו-`auth.check`
      פותח בספירת מפרידים. שם עם `|` **עובר את הכניסה** (‏200 ו-cookie)
      ונופל בבקשה הבאה: חשבון שנראה תקין ומתנהג כמו סיסמה שגויה.
    * תווי בקרה — שוברים את כותרת ה-cookie.
    * שם שאינו latin-1 — ‏starlette מקודד את הכותרת ב-latin-1, ולכן שם
      בעברית עבר את היצירה ואת בדיקת הסיסמה **ואז הפיל את `set_cookie`
      ב-500 — על הסיסמה הנכונה.** סיסמה שגויה החזירה 401 נקי, כלומר
      הכישלון היה ניתן לאבחון וההצלחה לא (#111).

    נדב הכריע שהשמות באנגלית בלבד, ולכן #111 נסגר כאן ולא בפורמט
    ה-cookie — שהחלפתו הייתה מנתקת פעם אחת את כל המחוברים.

    האכיפה היא **ביצירה בלבד**. ``verify`` אינו בודק שוב, כדי שחשבון
    קיים לא ייחסם רטרואקטיבית על ידי כלל שנוסף אחריו.
    """
    if not _USERNAME_RE.fullmatch(username):
        raise ValueError(
            "שם משתמש באותיות אנגליות, ספרות, רווח, נקודה, מקף או קו "
            "תחתון — עד 32 תווים"
        )


def create(conn: sqlite3.Connection, username: str, password: str, role: str, by: str) -> None:
    if role not in ("admin", "deploy"):
        raise ValueError("role must be admin or deploy")
    username = username.strip()
    if not username or len(password) < 8:
        raise ValueError("שם משתמש ריק או סיסמה קצרה משמונה תווים")
    _check_username(username)
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO users (username, pw_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (username, _hash(password, secrets.token_hex(16)), role, now_iso()),
        )
    journal(conn, "user_create", f"{username} ({role})", by)


def verify(conn: sqlite3.Connection, username: str, password: str) -> str | None:
    """מחזיר את התפקיד אם הסיסמה נכונה, אחרת None. השוואה קבועת-זמן.

    הפרמטרים לגיבוב באים **מהרשומה** ולא מהקבועים של הקובץ. כאן ישבו
    ‏`_scheme` ו-`_iters` — מפוענחים, ואז מושלכים לטובת `_ITERATIONS`
    הקבוע. כל עוד לא שינו את הקבוע זה עבד; ביום שהוא היה עולה (וזה מה
    שקבוע כזה נועד לו) **כל הסיסמאות הקיימות היו נכשלות בשקט**, בהודעה
    "שם משתמש או סיסמה שגויים" שנראית כמו טעות הקלדה ולא כמו מיגרציה
    שלא נעשתה. עם המנהל האחרון — קונסולה נעולה.

    ורשומה שלא ניתן לפענח נדחית, ולא נופלת חזרה לקבוע: "לא הבנו את
    הרשומה" איננו "הסיסמה שגויה במקרה", ובוודאי לא "ננחש 200,000".
    """
    # ‏#186: חסום אינו נכנס. הבדיקה כאן **בנוסף** ל-`auth.check` ולא
    # במקומה — זו הכניסה, וזו הבקשה שאחריה.
    row = conn.execute(
        "SELECT pw_hash, role, disabled_at FROM users WHERE username = ?", (username.strip(),)
    ).fetchone()
    if row is None or row["disabled_at"]:
        return None
    try:
        scheme, raw_iterations, salt, digest = row["pw_hash"].split("$")
        iterations = int(raw_iterations)
        bytes.fromhex(salt)                    # מלח שאינו hex — רשומה פגומה
    except ValueError:
        return None
    if scheme != _SCHEME or iterations < 1 or not digest:
        return None
    if hmac.compare_digest(_derive(password, salt, iterations), digest):
        return row["role"]
    return None


def update(
    conn: sqlite3.Connection, username: str, by: str,
    password: str | None = None, role: str | None = None,
) -> None:
    """שינוי תפקיד ו/או איפוס סיסמה. שדה שלא נשלח — לא נוגעים בו."""
    row = conn.execute(
        "SELECT role FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        raise ValueError("משתמש לא קיים")
    if role is not None and role not in ("admin", "deploy"):
        raise ValueError("תפקיד לא חוקי")
    if password is not None and len(password) < 8:
        raise ValueError("סיסמה קצרה משמונה תווים")
    # הורדת המנהל האחרון מתפקידו נועלת את כולם מחוץ לניהול.
    if role == "deploy" and row["role"] == "admin" and admin_count(conn) <= 1:
        raise ValueError("זה המנהל האחרון — אי אפשר להוריד אותו מתפקידו")

    # שני העדכונים הם טרנזאקציה אחת: תפקיד שהשתנה בלי הסיסמה שנשלחה
    # איתו הוא חשבון שאיש אינו יודע באיזה מצב הוא.
    changed = []
    with _write_lock, writing(conn):
        if role is not None and role != row["role"]:
            conn.execute("UPDATE users SET role = ? WHERE username = ?",
                         (role, username))
            changed.append(f"role={role}")
        if password is not None:
            conn.execute(
                "UPDATE users SET pw_hash = ? WHERE username = ?",
                (_hash(password, secrets.token_hex(16)), username),
            )
            changed.append("password")
    if changed:
        journal(conn, "user_edit", f"{username} " + ", ".join(changed), by)


def set_disabled(conn: sqlite3.Connection, username: str, disabled: bool,
                 by: str) -> None:
    """חסימה או שחרור. שני שומרים, ושניהם על אותו היגיון כמו במחיקה.

    **חסימה אינה מחיקה.** היא הפיכה, והיא משאירה את הרשומה — כך ששורות
    היומן שמזכירות את השם ממשיכות להצביע על מישהו. מחיקה הופכת אותן
    לשם שאין מאחוריו כלום.
    """
    row = conn.execute(
        "SELECT role, disabled_at FROM users WHERE username = ?", (username,)
    ).fetchone()
    if row is None:
        raise ValueError("משתמש לא קיים")
    if disabled and row["role"] == "admin" and active_admin_count(conn) <= 1:
        raise ValueError("זה המנהל הפעיל האחרון — חסימתו תנעל את הקונסולה")
    with _write_lock, writing(conn):
        conn.execute("UPDATE users SET disabled_at = ? WHERE username = ?",
                     (now_iso() if disabled else None, username))
    journal(conn, "user_disabled" if disabled else "user_enabled", username, by)


def active_admin_count(conn: sqlite3.Connection) -> int:
    """מנהלים שאינם חסומים. ‏`admin_count` סופר גם חסומים, ולכן הוא
    התשובה הלא נכונה לשאלה "האם יישאר מי שינהל"."""
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users"
        " WHERE role = 'admin' AND disabled_at IS NULL"
    ).fetchone()["n"]


def admin_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
    ).fetchone()["n"]


def delete(conn: sqlite3.Connection, username: str, by: str) -> None:
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM users WHERE username = ?", (username,))
    journal(conn, "user_delete", username, by)


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT username, role, created_at, disabled_at"
        " FROM users ORDER BY username"
    ).fetchall()
    return [{**dict(r), "disabled": bool(r["disabled_at"])} for r in rows]


def ensure_admin(conn: sqlite3.Connection) -> str | None:
    """בהתקנה טרייה נוצר admin עם סיסמה אקראית שמודפסת פעם אחת.

    מחזיר את הסיסמה אם נוצר משתמש, אחרת None.
    """
    row = conn.execute("SELECT 1 FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if row is not None:
        return None
    password = secrets.token_urlsafe(12)
    create(conn, "admin", password, "admin", by="")
    return password
