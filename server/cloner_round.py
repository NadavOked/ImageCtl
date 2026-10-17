"""סבב חדר של מכונה אחת, שנפתח ממסך מחשב השיכפול עצמו (16/10).

נדב, 16/10: למסך הבחירה של מחשב השיכפול נוספת אפשרות שלישית — "שחזור
אימג' מהשרת": בוחרים אימג' ומגירות **של המחשב הזה**, מאשרים בכפתור
אדום, והמחשב ממשיך מהשרת בדיוק כמו בסבב חדר רגיל (‏hello → גל →
‏`do_restore_drawers` → כיבוי להחלפה). **בלי שם משתמש וסיסמה**: הנוכחות
הפיזית ליד המכונה היא ההרשאה (הכרעת נדב).

הנתיב הרסני ולכן צר, ו**נכשל סגור** (סקירת אבטחה, 16/10):

- **זהות, בלי פטור.** ‏`identity_gate` של כל נתיב סוכן (‏#855/#997: ‏MAC
  מוצהר שתואם לחכירת ה-DHCP של כתובת המקור) — וכאן גם **שומר שאינו מותקן
  או מתג שכבוי הם סירוב**, לא היתר: בלי התאמה חיובית בין MAC לכתובת אין
  פתיחה.
- **רק מחשב שיכפול רשום** (קבוצת מחשבי השיכפול, תפקיד `cloner`).
- **המסך פתוח עכשיו.** הסוכן מבקש אסימון (‏`POST …/cloner-round/nonce`)
  כשהוא מציג את מסך השחזור; הפתיחה דורשת אותו, הוא חד-פעמי (נצרך בכל
  ניסיון שעבר את הזהות) ופג אחרי `NONCE_TTL_S`. ⚠️ **האסימון אינו גורם
  הרשאה**: כל מי שעובר את שער הזהות מקבל אותו. הוא מעיד רק שהמסך הגיע
  לשלב "שחזור" (וחוסם שידור חוזר של בקשה ישנה). ההרשאה כאן היא זהות
  ‏MAC↔חכירה + נוכחות ליד המכונה (הכרעת נדב 16/10), ואין גורם נוסף —
  אין למכונה אישור משלה (ראו interfaces.md).
- **קצב:** ניסיון אחד למכונה בכל `RATE_S` שניות (‏429).
- **הנתונים של השרת, לא של הבקשה.** הגוף נושא פורטים בלבד — לא MAC-ים, לא
  דיסקים. ‏`target_slots` נבנה מה-MAC המאומת; החריצים נבדקים מול המיפוי
  הרשום (‏`drawer_slots`) והדיסקים מה-hello האחרון שנשמר בשרת
  (‏`_validate_target_slots`), והאימג'ים המותרים מחושבים מאותם דיסקים
  שמורים (‏`allowed_for_disks`) — כל שדה אחר בגוף מתעלמים ממנו.
- אחר כך כל בדיקות `open_round`: אימג' קשור, גודל, ‏409 כשיש סבב פעיל.
- **אין כאן סגירה, עצירה, "התחל עכשיו" או כיבוי**: הנתיב פותח, ותו לא.

כל ניסיון נרשם ביומן: ‏`room_open_cloner` כשנפתח ("נפתח ממחשב שיכפול
<שם>", היוזם = המכונה), ‏`cloner_round_refused` עם הסיבה כשלא.
"""

from __future__ import annotations

import json
import secrets
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac

from . import identity, registry
from .api import ServerContext, _error, identity_gate
from .db import journal
from .room import open_round
from .sessions import SessionError

#: הנתיב, על פורט הסוכן (‏`/api/v1/agent/*`, ‏#824).
PATH = "/agent/cloner-round"
#: כמה זמן אסימון המסך תקף — כמו תקרת המסך בסוכן (‏LC_QUIET_S, ‏600) ועוד מרווח.
NONCE_TTL_S = 900
#: ניסיון פתיחה אחד למכונה בכל כמה שניות.
RATE_S = 10

#: השעון — מוחלף בבדיקות.
_now = time.monotonic


def _disks_of(ctx: ServerContext, mac: str) -> list[dict]:
    """הדיסקים מה-hello האחרון **שנשמר בשרת** — לא מהבקשה."""
    row = ctx.conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    if row is None or not row["disks_json"]:
        return []
    disks = json.loads(row["disks_json"])
    return [d for d in disks if isinstance(d, dict)] if isinstance(disks, list) else []


def initiator(machine) -> str:
    """היוזם כפי שהוא נרשם ביומן וב-``opened_by`` של הסבב."""
    return f"מחשב שיכפול {machine['suffix'] or machine['mac']}"


def create_cloner_round_router(ctx: ServerContext) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    lock = threading.Lock()
    nonces: dict[str, tuple[str, float]] = {}     # mac -> (nonce, expires at)
    last_try: dict[str, float] = {}               # mac -> the last attempt past the role check

    def refuse(mac: str, status: int, message: str, code: str, why: str = "") -> JSONResponse:
        journal(ctx.conn, "cloner_round_refused", f"{mac} — {why or message} ({code})")
        return _error(status, message, code)

    async def caller(request: Request, where: str):
        """(body, mac, machine) או (None, None, תשובת-סירוב). הזהות נכשלת סגור."""
        try:
            body = await request.json()
        except ValueError:
            return None, None, refuse("?", 400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return None, None, refuse("?", 400, "body is not an object", "bad_json")
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return None, None, refuse("?", 400, "missing or malformed mac", "bad_mac")
        if ctx.leases is None or not identity.enabled(ctx.conn):
            return None, None, refuse(
                mac, 403, "זהות המחשב אינה נבדקת בשרת הזה — פתיחת סבב מהמכונה סגורה",
                identity.UNVERIFIABLE, "identity check is off")
        refused = identity_gate(ctx, mac, request, where)
        if refused is not None:
            journal(ctx.conn, "cloner_round_refused", f"{mac} — זהות ({where})")
            return None, None, refused
        machine = registry.lookup(ctx.conn, mac)
        if machine is None:
            return None, None, refuse(mac, 403, "המחשב אינו רשום במערכת", "unknown_mac")
        if machine["role"] != "cloner":   # #1554: כל קבוצת משכפלים, לא רק grp_CLONERS
            return None, None, refuse(mac, 403, "רק מחשב שיכפול פותח סבב מהמסך שלו", "not_a_cloner",
                                      f"אינו מחשב שיכפול ({machine['role']})")
        return body, mac, machine

    @router.post(PATH + "/nonce")
    async def screen_nonce(request: Request) -> JSONResponse:
        """אסימון חד-פעמי למסך השחזור שעל המכונה — מבקשים אותו כשהמסך עולה
        ואחרי כל ניסיון. ‏``{"ok": true, "nonce", "ttl_s"}``."""
        _body, mac, machine = await caller(request, "cloner-round-nonce")
        if mac is None:
            return machine
        nonce = secrets.token_hex(16)
        with lock:
            nonces[mac] = (nonce, _now() + NONCE_TTL_S)
        return JSONResponse({"ok": True, "nonce": nonce, "ttl_s": NONCE_TTL_S})

    @router.post(PATH)
    async def cloner_round(request: Request) -> JSONResponse:
        """פתיחת סבב חדר על המגירות של המכונה הפונה בלבד.

        גוף: ‏``{"mac", "nonce", "image_id", "ports": [int, ...]}``. תשובה: ‏200
        ‏``{"ok": true, "id", "wave_session_id"}``; כל סירוב הוא
        ‏``{"ok": false, "error": <עברית>, "code"}`` (ממשק, ‏interfaces.md).
        """
        body, mac, machine = await caller(request, "cloner-round")
        if mac is None:
            return machine
        now = _now()
        with lock:
            if now - last_try.get(mac, -RATE_S) < RATE_S:
                limited = True
            else:
                limited = False
                last_try[mac] = now
            held = nonces.pop(mac, None) if not limited else None   # single-use: any attempt spends it
        if limited:
            return refuse(mac, 429, "ניסיון פתיחה אחד בכל כמה שניות — נסו שוב בעוד רגע", "rate_limited")
        sent = body.get("nonce")
        if (held is None or not isinstance(sent, str) or held[1] < now
                or not secrets.compare_digest(sent, held[0])):
            return refuse(mac, 403, "מסך השחזור במחשב אינו פתוח מול השרת — חזרו ובחרו שוב", "bad_nonce")

        ports = body.get("ports")
        if (not isinstance(ports, list) or not ports or any(
                isinstance(p, bool) or not isinstance(p, int) for p in ports)):
            return refuse(mac, 400, "יש לבחור לפחות מגירת יעד אחת", "bad_ports")
        image_id = body.get("image_id")
        if not isinstance(image_id, str) or not image_id:
            return refuse(mac, 400, "לא נבחר אימג'", "bad_image")
        if image_id not in ctx.library.allowed_for_disks(_disks_of(ctx, machine["mac"])):
            return refuse(mac, 403, "האימג' הזה אינו מותר למחשב הזה", "image_not_allowed",
                          f"האימג' {image_id} אינו מותר למחשב הזה")

        who = initiator(machine)
        try:
            opened = open_round(ctx, image_id, len(ports), who,
                                [{"mac": machine["mac"], "ports": ports}])
        except SessionError as exc:          # לפני ValueError — הוא יורש ממנו
            return refuse(mac, 409, str(exc), "round_conflict")
        except (ValueError, TypeError) as exc:
            return refuse(mac, 400, str(exc) or "בקשה לא תקינה", "bad_round")
        journal(ctx.conn, "room_open_cloner",
                f"{opened['id']} — נפתח ממחשב שיכפול {machine['suffix'] or mac}"
                f" ({mac}), {image_id}, מגירות {','.join(str(p) for p in sorted(ports))}",
                who)
        return JSONResponse({"ok": True, **opened})

    return router
