"""‏#980 — שתי פעולות של אובייקט "מחשבי שיכפול" בקונסולה שלא היה להן מקור.

1. **כיבוי כל המשכפלים** (‏`POST /api/console/room/poweroff`). **דרך הסוכן,
   לא `poweroff -f` מהשרת** (הכרעת נדב; ‏#587): מכונה שכובתה בלי
   ‏`arm_wol` אינה מתעוררת ב-WoL. השרת רק **רושם בקשה** לכל משכפל; הבקשה
   יוצאת בתשובת ה-hello הבא של המכונה (‏`power_action`, סעיף 3) — אותו
   hello באותו קצב, בלי polling חדש — והסוכן מכבה באותו מסלול כמו כיבוי
   ההמתנה (‏`idle.sh`: ‏`arm_wol` נקרא בחזרה, ורק אז `poweroff -f`).
   בקשה נמסרת **פעם אחת** ופגה אחרי `POWEROFF_TTL_SECONDS`: מכונה שהייתה
   כבויה בזמן הלחיצה ועולה מחר אינה נכבית ברגע שהיא עולה.
2. **היסטוריית סבבי החדר** (‏`GET /api/console/room/history`) — מטבלת
   ‏`room_rounds`, שכבר שומרת כל סבב.

**לא בראוטר של `room.py`**: הוא נכנס בשלמותו לקיוסק (`kiosk.py`), וכיבוי
החדר הוא פעולת ניהול שאין לה מקום על וילן ההפצה.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth
from .db import _write_lock, journal, now_iso, writing
from .room import CLONERS_GROUP, round_label, visible_round

#: הפעולה היחידה שהשרת מבקש מסוכן דרך ה-hello. ‏reboot לא נפתח — אין לו
#: צרכן, ו"כמו poweroff" אינו נימוק.
POWEROFF = "poweroff"
#: כמה זמן בקשת כיבוי ממתינה ל-hello של המכונה. ה-hello של משכפל ממתין
#: מגיע לכל המאוחר כל `POLL_MAX` (15 ש', ‏`agent/lib/poll.sh`); ‏120 ש' הם
#: שמונה פעימות — ומעבר לזה מכונה שלא ענתה אינה "תכבה אחר כך".
POWEROFF_TTL_SECONDS = 120
HISTORY_DEFAULT = 10
HISTORY_MAX = 50


def _cloners_label(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT label FROM groups WHERE id = ?", (CLONERS_GROUP,)).fetchone()
    return row["label"] if row else None


def request_poweroff(conn: sqlite3.Connection, user: str) -> int:
    """רושם בקשת כיבוי לכל משכפל רשום. מחזיר כמה בקשות נרשמו — **לא**
    כמה נכבו: הראיה לכיבוי היא שה-hello נפסק (`awake` ב-`/room`)."""
    macs = [r["mac"] for r in conn.execute(
        "SELECT mac FROM machines WHERE group_id = ?", (CLONERS_GROUP,))]
    at = now_iso()
    with _write_lock, writing(conn):
        for mac in macs:
            conn.execute(
                "INSERT INTO power_requests (mac, action, requested_at, requested_by)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (mac) DO UPDATE SET"
                " action = excluded.action, requested_at = excluded.requested_at,"
                " requested_by = excluded.requested_by",
                (mac, POWEROFF, at, user))
    return len(macs)


def take_power_action(conn: sqlite3.Connection, mac: str,
                      now: datetime | None = None) -> str | None:
    """מה שה-hello של `mac` נושא ב-`power_action`: ‏`"poweroff"` או None.

    הבקשה נמחקת בקריאה — נמסרת פעם אחת. סוכן שלא הצליח לדרוך WoL נשאר
    דולק ואומר זאת, ואינו מקבל את הבקשה שוב בכל פעימה. בקשה שפגה נמחקת
    ואינה נמסרת (ראו `POWEROFF_TTL_SECONDS`)."""
    row = conn.execute(
        "SELECT action, requested_at FROM power_requests WHERE mac = ?",
        (mac,)).fetchone()
    if row is None:
        return None
    with _write_lock, writing(conn):
        conn.execute("DELETE FROM power_requests WHERE mac = ?", (mac,))
    try:
        at = datetime.fromisoformat(row["requested_at"])
    except (TypeError, ValueError):
        return None                       # חותמת שאינה נקראת — לא מכבים
    age = (now or datetime.now(timezone.utc)) - at
    if age > timedelta(seconds=POWEROFF_TTL_SECONDS) or age < timedelta(0):
        journal(conn, "power_expired", f"{mac} {row['action']}")
        return None
    journal(conn, "power_delivered", f"{mac} {row['action']}")
    return row["action"]


def round_history(ctx, limit: int) -> list[dict]:
    """סבבי חדר שהסתיימו (‏`closed`/`failed`), מהחדש לישן. הסבב שמוצג
    עכשיו ב-`GET /room` (פעיל, או כושל שעוד לא נסגר) אינו כאן."""
    shown = visible_round(ctx.conn)
    rows = ctx.conn.execute(
        "SELECT * FROM room_rounds WHERE state IN ('closed', 'failed') AND id != ?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (shown["id"] if shown is not None else "", limit)).fetchall()
    return [{
        "id": r["id"],
        "state": r["state"],
        "image_id": r["image_id"],
        "image_name": round_label(ctx, r),
        "source_kind": r["source_kind"],
        "target_drives": r["target_drives"],
        "written_drives": r["written_drives"],
        "opened_by": r["opened_by"],
        "created_at": r["created_at"],
        "closed_at": r["closed_at"],
        "failed_reason": r["failed_reason"],
    } for r in rows]


def create_room_console_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/console/room")
    current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("/history")
    def history(limit: int = HISTORY_DEFAULT, user=Depends(current_user)):
        # קריאה בלבד — פתוחה לכל מחובר, כמו `GET /room`.
        if limit < 1:
            raise HTTPException(400, "limit חייב להיות לפחות 1")
        return round_history(ctx, min(limit, HISTORY_MAX))

    @router.post("/poweroff")
    async def poweroff(request: Request, user=Depends(admin_only)):
        # גוף ריק או לא-JSON נופל לאישור ריק — 400, כמו `/room/close`.
        try:
            body = await request.json()
        except Exception:                              # noqa: BLE001
            body = {}
        typed = body.get("confirm_name", "") if isinstance(body, dict) else ""
        label = _cloners_label(ctx.conn)
        if label is None or typed != label:
            raise HTTPException(400, "השם שהוקלד אינו זהה לשם קבוצת מחשבי השיכפול")
        shown = visible_round(ctx.conn)
        if shown is not None and shown["state"] in ("active", "closing"):
            # כיבוי באמצע גל מפיל כתיבה חיה — הסבב נעצר קודם, בהקלדה משלו.
            raise HTTPException(409, "יש סבב חדר פעיל — עצרו אותו לפני כיבוי החדר")
        count = request_poweroff(ctx.conn, user[0])
        journal(ctx.conn, "room_poweroff", f"requested={count}", user[0])
        return {"requested": count, "ttl_s": POWEROFF_TTL_SECONDS}

    return router
