"""משימות ישירות — שאילתות טהורות, בלי תלות ב-FastAPI.

המודול הזה קיים כדי לשבור מעגל: `hello` צריך לדעת אם יש משימה למכונה,
ו-`capture` צריך את אותה שאילתה אבל גם את ההקשר של האפליקציה. שני
הצדדים מייבאים מכאן.
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path

#: אורך האסימון בבתים. ‏`token_hex(24)` = 48 תווים = 192 סיביות.
#:
#: להשוואה, `task_id` הוא `token_hex(2)` — **65,536 ערכים**, וזה מספיק
#: כדי לנחש אותו (#535). המזהה הוא שם, לא סוד; האסימון הוא הסוד.
TOKEN_BYTES = 24


def new_token() -> str:
    return secrets.token_hex(TOKEN_BYTES)

#: מצבים שבהם המשימה עדיין מחכה למכונה או רצה עליה.
OPEN_STATES = ("pending", "running")

#: הכותרת שבה הסוכן מחזיר את האסימון. **כותרת ולא שדה בגוף**: העלאת
#: מחיצה היא זרם בייטים גולמי בלי גוף JSON, ולכן זה המקום היחיד
#: ששלושת המסלולים חולקים.
TOKEN_HEADER = "X-Imagectl-Task-Token"


def staging_dir(library_root: Path, task_id: str) -> Path:
    """איפה נאספים קבצי הקליטה עד שהמניפסט מאומת."""
    return library_root / f".capture-{task_id}"


def active_task(conn: sqlite3.Connection, mac: str) -> dict | None:
    """המשימה הפתוחה של המכונה, במבנה של שדה `task` בממשק 3."""
    row = conn.execute(
        "SELECT * FROM tasks WHERE mac = ? AND state IN ('pending', 'running')"
        " ORDER BY created_at LIMIT 1",
        (mac,),
    ).fetchone()
    if row is None:
        return None
    return {
        "id": row["id"], "type": row["type"], "disk": row["disk"],
        "image_id": row["image_id"], "name": row["name"],
        "description": row["description"], "folder": row["folder"],
        # ‏#530: ה-token נמסר **רק** למכונה שה-hello שלה תואם את ה-MAC
        # של המשימה, וכל כתיבה על המשימה דורשת אותו בחזרה. בלעדיו
        # כל פונה שמכיר `task_id` העלה אימג' משלו (#530), ביטל ביטול
        # (#532), או הרג קליטה חיה (#535).
        "token": row["token"],
    }


def claim_task(conn: sqlite3.Connection, task_id: str, token: str | None) -> dict | None:
    """המשימה **הפתוחה** ששייכת ל-``token`` הזה, או ``None``.

    שלוש שאלות שונות, ואסור לקפל אותן: משימה שאינה קיימת, משימה
    שנסגרה, ופונה שאינו בעליה. הקורא מבחין ביניהן לפי מה שמוחזר
    כאן ולפי מה שהוא כבר יודע — הפונקציה הזו עונה רק על **הצירוף**.

    ההשוואה היא ב-``secrets.compare_digest``: השוואת מחרוזות רגילה
    יוצאת מוקדם על התו הראשון שאינו תואם, וזה מדליף את האסימון
    ניחוש-אחר-ניחוש למי שמודד זמן.
    """
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM tasks WHERE id = ? AND state IN ('pending', 'running')",
        (task_id,),
    ).fetchone()
    if row is None or not row["token"]:
        return None
    if not secrets.compare_digest(str(row["token"]), str(token)):
        return None
    return dict(row)
