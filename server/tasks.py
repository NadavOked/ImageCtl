"""משימות ישירות — שאילתות טהורות, בלי תלות ב-FastAPI.

המודול הזה קיים כדי לשבור מעגל: `hello` צריך לדעת אם יש משימה למכונה,
ו-`capture` צריך את אותה שאילתה אבל גם את ההקשר של האפליקציה. שני
הצדדים מייבאים מכאן.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: מצבים שבהם המשימה עדיין מחכה למכונה או רצה עליה.
OPEN_STATES = ("pending", "running")


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
    }
