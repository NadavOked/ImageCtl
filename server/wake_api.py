"""‏#984 — WoL למחשב בנייה ולמשכפל **בודד** מהקונסולה.

עד כאן WoL מהקונסולה היה מסלול אחד: ‏`POST /api/console/room/wake`
(‏`room.py`), שמעיר את כל ‏`grp_CLONERS`. כאן שני המסלולים שנדב הכריע
עליהם (17/09): מכונה אחת לפי MAC, וקבוצת הבנייה כולה.

**תחנות כיתה הן v2.** המסלול מסרב להן ב-403 במקום "לפתוח כי זה אותו
קוד" — ההכרעה היא על מי מתעורר מהקונסולה, לא על מה המודול יודע לשלוח.

התשובה זהה ל-`/room/wake`: ‏`{sent, failed, reasons}` — ‏`sent` ולא
‏`woken`, כי חבילת WoL היא UDP בלי ACK ואין ראיה שהמכונה קמה (#528).
השולח מוזרק מ-`app.py` (‏`wol_send`) כמו בחדר, כדי שהטסטים לא ישדרו.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException

from . import auth, wol
from .api import ServerContext
from .db import journal
from .registry import normalize_mac
from .room import ROOM_OPERATOR_ROLES

#: מי מתעורר מהקונסולה ב-v1 — מחשבי בנייה ושיכפול. ‏classroom = v2.
WAKEABLE_ROLES = ("build", "cloner")
CLASSROOM_V2 = "WoL לתחנות כיתה — v2"


def _result(sent: wol.WakeResult) -> dict:
    # ‏getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל (כמו ב-room.py).
    return {"sent": int(sent),
            "failed": len(getattr(sent, "failed", ())),
            "reasons": list(getattr(sent, "reasons", ()))}


def create_wake_router(
    ctx: ServerContext, send: Callable[[bytes], None] | None = None,
) -> APIRouter:
    """‏`send` מוזרק מ-app.py (‏`rt.wol_send`) — אותו שולח כמו בחדר.
    ‏None = ברירת המחדל של המודול (שידור לפי טבלת הניתוב)."""
    router = APIRouter(prefix="/api/console")
    current_user, _admin_only = auth.dependencies(ctx.conn)
    send_kw = {"send": send} if send else {}

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """אותה רשימת-היתר כמו `/room/wake` (‏`room.py`): תפקיד שלישי
        אינו מקבל את הכפתור ביום היוולדו."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.post("/machines/{mac}/wake")
    def wake_machine(mac: str, user=Depends(room_operator)):
        canonical = normalize_mac(mac)
        if canonical is None:
            raise HTTPException(400, "MAC לא תקין")
        row = ctx.conn.execute(
            "SELECT g.role FROM machines m JOIN groups g ON g.id = m.group_id"
            " WHERE m.mac = ?", (canonical,)).fetchone()
        if row is None:
            raise HTTPException(404, "מכונה לא רשומה")
        if row["role"] not in WAKEABLE_ROLES:
            raise HTTPException(403, CLASSROOM_V2)
        sent = wol.wake_machine(ctx.conn, canonical, **send_kw)
        if sent:
            journal(ctx.conn, "wol_sent", f"{canonical} count={int(sent)}", user[0])
        return _result(sent)

    @router.post("/groups/{gid}/wake")
    def wake_group(gid: str, user=Depends(room_operator)):
        row = ctx.conn.execute(
            "SELECT role FROM groups WHERE id = ?", (gid,)).fetchone()
        if row is None:
            raise HTTPException(404, "קבוצה לא קיימת")
        if row["role"] not in WAKEABLE_ROLES:
            raise HTTPException(403, CLASSROOM_V2)
        sent = wol.wake_group(ctx.conn, gid, **send_kw)
        if sent:
            journal(ctx.conn, "wol_sent", f"{gid} count={int(sent)}", user[0])
        return _result(sent)

    return router
