"""Admin-only browser-to-machine RFB proxy and monitor boot gate."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import struct
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, WebSocket
from starlette.websockets import WebSocketDisconnect

from . import auth, registry
from .api import ServerContext
from .db import get_setting, journal, set_setting

MONITOR_PORT = 5900
ONLINE_SECONDS = 90
STATION_KEY = "monitor:stations"
CMDLINE_PARAM = "imagectl.monitor=1"
CMDLINE_PREFIX = "imagectl.monitor"

#: מה שמקלידים כדי להדליק את המוניטור בכל התחנות. ‏ASCII, וזהה למה
#: שנכנס לשורת הקרנל — מי שמקליד יודע בדיוק מה הוא מדליק, כמו מתג ה-SSH.
MONITOR_CONFIRM = "imagectl.monitor"

#: קוד סגירת ה-WebSocket כשאין cookie תקף (טרם התחברות) וכשמחובר אך
#: אינו admin. הסגירה קורית **לפני** פתיחת ה-TCP למכונה, ולכן היעדר
#: הרשאה לעולם אינו נוגע בשירות ה-RFB — הבקרה השלילית מוכיחה בדיוק זאת.
WS_UNAUTHENTICATED = 4401
WS_FORBIDDEN = 4403

#: ‏#839: הפרוקסי לא הצליח להזדהות מול המוניטור **במכונה** — המכונה לא
#: דיווחה סוד (סוכן ישן), המוניטור לא הציע את סוג-האבטחה שלנו (מוניטור
#: ישן, או מי שמתחזה לו), או שדחה את הסוד. מובחן מ-4403 (המשתמש בדפדפן
#: אינו admin) ומ-4502 (אין מי שיענה על 5900).
WS_MACHINE_AUTH = 4512

#: ‏#1129: הגשר סוגר את שני הצדדים אחרי IDLE_SECONDS בלי בייט אחד לאף כיוון
#: — ‏WS/TCP חצי-פתוח היה משאיר task ו-`active` תפוס לנצח.
WS_IDLE = 4408
IDLE_SECONDS = 600

#: ‏#1129: ‏Origin זר (דף באתר אחר שפתח WebSocket עם ה-cookie של המנהל —
#: ‏SameSite=lax אינו מגן על WS בכל הדפדפנים) נסגר **לפני** accept, כמו
#: ‏ConsoleSourceGuard: תוקף לא מקבל 101 ולא הסבר.
ORIGIN_REFUSED = "Origin אינו תואם לשרת"

#: ‏RFC 6455 §5.5.1: מסגרת close נושאת עד 125 בייט — 2 לקוד ו-123 לסיבה.
CLOSE_REASON_MAX_BYTES = 123

#: ‏#1129: הודעות לקוח→שרת של RFB 3.8 שהדפדפן רשאי לשלוח, ואורכן הקבוע.
#: ‏SetEncodings (2) ו-ClientCutText (6) משתני-אורך ונפתרים ב-`client_message_length`.
RFB_CLIENT_CUT_TEXT = 6
RFB_CLIENT_FIXED_LENGTHS = {0: 20, 3: 10, 4: 8, 5: 6}
POWER_PREFIX = "imagectl-power:"
POWER_ACTIONS = ("reboot", "poweroff")
POWER_OVER_WS_REFUSED = "פקודת כוח אינה עוברת ב-WebSocket — רק POST …/power"


def origin_allowed(headers) -> bool:
    """‏#1129: ‏`Origin` חייב להתאים ל-`Host` של הבקשה, או להיעדר (לקוח שאינו
    דפדפן — ‏wsmon.py, בדיקות). דפדפן תמיד שולח Origin ב-WebSocket, ולכן
    היעדרו אינו מסלול עוקף לדף זר; ‏`null` (sandbox/file://) נדחה."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = headers.get("host", "")
    return bool(host) and urlsplit(origin).netloc.lower() == host.lower()


def client_message_length(buf: bytes) -> int | None:
    """אורך ההודעה הראשונה ב-buf, או None כשחסרים בייטים כדי לקבוע.
    סוג לא מוכר → ValueError."""
    kind = buf[0]
    if kind in RFB_CLIENT_FIXED_LENGTHS:
        return RFB_CLIENT_FIXED_LENGTHS[kind]
    if kind == 2:                                 # SetEncodings: pad, u16 count
        if len(buf) < 4:
            return None
        return 4 + 4 * struct.unpack(">H", buf[2:4])[0]
    if kind == RFB_CLIENT_CUT_TEXT:               # pad(3), u32 length
        if len(buf) < 8:
            return None
        return 8 + struct.unpack(">I", buf[4:8])[0]
    raise ValueError(kind)


def power_frame(action: str) -> bytes:
    """‏ClientCutText (סוג 6) עם אסימון הכוח ש-monitor.c מזהה (#781)."""
    body = (POWER_PREFIX + action).encode("ascii")
    return b"\x06\x00\x00\x00" + struct.pack(">I", len(body)) + body


def machine_name(conn, canonical: str) -> str | None:
    """השם הקנוני של המכונה (`suffix`) — מה שמקלידים לאישור כוח."""
    row = conn.execute("SELECT suffix FROM machines WHERE mac = ?",
                       (canonical,)).fetchone()
    return None if row is None else row["suffix"]


def close_reason(text: str) -> str:
    """סיבת סגירה שנכנסת במסגרת close. עברית היא 2 בייט לתו, ו-`websockets`
    זורק ProtocolError על סיבה ארוכה מ-123 בייט — מה שלפני #904 לא קרה
    מעולם, כי הסיבה נזרקה לפח יחד עם ה-handshake."""
    data = text.encode("utf-8")
    if len(data) <= CLOSE_REASON_MAX_BYTES:
        return text
    return data[:CLOSE_REASON_MAX_BYTES].decode("utf-8", "ignore")


async def accept_browser(websocket: WebSocket) -> None:
    """מקבל את ה-WebSocket **לפני** כל בדיקה, כדי שדחייה תגיע לדפדפן כקוד
    וסיבה (‏#904 סעיף 4). לפי מפרט ASGI, ‏`websocket.close` לפני accept הוא
    דחיית ה-handshake — uvicorn מגיש אותה כ-HTTP 403 בשני המימושים
    (‏wsproto/websockets), והדפדפן רואה 1006 בלי קוד ובלי סיבה: 4401,
    ‏4403, ‏4404, ‏4409, ‏4502 ו-4512 כולם נראו "החיבור נסגר". ה-accept אינו
    שולח בייט אחד של RFB ואינו נוגע במכונה — השער (cookie/admin) עדיין
    סוגר לפני TCP, וזו הבקרה השלילית של #859. **החריג:** ‏`ConsoleSourceGuard`
    (‏#859, כתובת מקור) ממשיך לסגור לפני accept — peer מרשת אסורה לא
    מקבל 101 כלל, בדיוק כמו שהוא לא מקבל 200 ב-HTTP."""
    offered = websocket.headers.get("sec-websocket-protocol", "")
    protocol = "binary" if "binary" in {
        item.strip() for item in offered.split(",")
    } else None
    await websocket.accept(subprotocol=protocol)

#: ‏RFB security type 2 — מסגור VNC Authentication (challenge של 16
#: בייטים, תשובה של 16 בייטים). התשובה היא HMAC-SHA256(סוד, challenge)
#: קטום ל-16 בייטים (#1077); הסוד עצמו אינו עובר על 5900.
RFB_SECURITY_SECRET = 2
RFB_VERSION = b"RFB 003.008\n"
MONITOR_AUTH_HMAC = "hmac"
HMAC_RESPONSE_LEN = 16


def flag_json(enabled: bool) -> str:
    """ייצוג ההגדרה בדיוק כמו ssh_switch.flag_json — מקור יחיד לצורה."""
    return json.dumps({"enabled": bool(enabled)})

OpenConnection = Callable[
    [str, int],
    Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]],
]


def stations_enabled(conn) -> bool:
    try:
        raw = get_setting(conn, STATION_KEY)
        return bool(raw and json.loads(raw).get("enabled") is True)
    except (ValueError, AttributeError):
        return False


def station_cmdline(extra: tuple[str, ...], enabled: bool) -> tuple[str, ...]:
    """Make the monitor gate single-source and independent of SSH."""
    kept = tuple(item for item in extra
                 if not item.startswith(CMDLINE_PREFIX))
    return kept + ((CMDLINE_PARAM,) if enabled else ())


def seen_within(last_seen: str | None,
                now: datetime | None = None) -> bool:
    """‏"מחובר" לרשימת המוניטור: אותו חלון (‏ONLINE_SECONDS) שבו
    ‏``_target`` מסכים לפתוח חיבור. חותמת חסרה, פגומה או נאיבית = לא
    מחובר — לא ידוע אינו "כן" (עיקרון 5)."""
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except (TypeError, ValueError):
        return False
    if seen.tzinfo is None:
        return False
    current = now or datetime.now(timezone.utc)
    return current - seen <= timedelta(seconds=ONLINE_SECONDS)


def machine_rows(conn, now: datetime | None = None) -> list[dict]:
    """רשימת המכונות של דף המוניטור (#822): רק build/cloner, ‏``online``
    נקבע **בשרת** באותו חלון-נוכחות של ``_target``. משמש גם את הראשי
    שצופה במשני (#655 v1) — אותה רשימה, אותו חישוב."""
    rows = conn.execute(
        "SELECT m.mac, m.suffix, g.role, d.ip, d.last_seen, d.prompt "
        "FROM machines m "
        "JOIN groups g ON g.id = m.group_id "
        "LEFT JOIN net_devices d ON d.mac = m.mac "
        "WHERE g.role IN ('build', 'cloner') "
        "ORDER BY g.role, m.suffix"
    ).fetchall()
    return [
        {
            "mac": r["mac"],
            "name": r["suffix"],
            "role": r["role"],
            "ip": r["ip"],
            "online": bool(r["ip"]) and seen_within(r["last_seen"], now),
            "prompt": r["prompt"],   # #906: השאלה שממתינה לאדם, או None
        }
        for r in rows
    ]


def hmac_response(secret: str, challenge: bytes) -> bytes:
    """HMAC-SHA256(secret, challenge) truncated to the 16-byte RFB response."""
    return hmac.new(bytes.fromhex(secret), challenge,
                    hashlib.sha256).digest()[:HMAC_RESPONSE_LEN]


def _target(ctx: ServerContext, mac: str,
            now: datetime | None = None
            ) -> tuple[str, str, str | None, str | None]:
    """‏(ip, role, monitor_secret, monitor_auth) של מכונה שמותר לפתוח אליה
    מוניטור. הסוד (#839) חוזר כמו שהוא — ‏None כשהמכונה מעולם לא דיווחה
    אחד. ‏monitor_auth הוא ``hmac`` אחרי #1077, או None לסוכן ישן."""
    canonical = registry.normalize_mac(mac)
    if canonical is None:
        raise HTTPException(404, "מכונה לא מוכרת")

    row = ctx.conn.execute(
        "SELECT d.ip, d.last_seen, d.monitor_secret, d.monitor_auth, g.role "
        "FROM machines m "
        "JOIN groups g ON g.id = m.group_id "
        "LEFT JOIN net_devices d ON d.mac = m.mac "
        "WHERE m.mac = ?",
        (canonical,),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "מכונה לא מוכרת")
    if row["role"] not in {"build", "cloner"}:
        raise HTTPException(409, "מוניטור עדיין אינו זמין למחשב כיתה")
    if not row["ip"] or not row["last_seen"]:
        raise HTTPException(409, "המכונה אינה מחוברת")

    try:
        seen = datetime.fromisoformat(row["last_seen"])
    except (TypeError, ValueError):
        raise HTTPException(409, "זמן הנוכחות של המכונה אינו תקין") from None
    if seen.tzinfo is None:
        raise HTTPException(409, "זמן הנוכחות של המכונה אינו תקין")

    current = now or datetime.now(timezone.utc)
    if current - seen > timedelta(seconds=ONLINE_SECONDS):
        raise HTTPException(409, "המכונה אינה מחוברת")
    return row["ip"], row["role"], row["monitor_secret"], row["monitor_auth"]


class MachineAuthError(Exception):
    """המוניטור במכונה לא קיבל את הפרוקסי — הסיבה בטקסט, לדפדפן."""


async def _read_exact(reader, n: int) -> bytes:
    """‏n בייטים בדיוק; ‏EOF מוקדם הוא כשל בשם, לא bytes קצר (עיקרון 5)."""
    chunks = bytearray()
    while len(chunks) < n:
        piece = await reader.read(n - len(chunks))
        if not piece:
            raise MachineAuthError(
                f"המוניטור סגר את החיבור אחרי {len(chunks)}/{n} בייטים")
        chunks += piece
    return bytes(chunks)


async def _reason(reader) -> str:
    """מחרוזת הסיבה של RFB 3.8 (u32 אורך + טקסט). האורך נחתך: מכונה
    עוינת לא תגרום לפרוקסי להקצות 4GB, וסיבת סגירת WebSocket מוגבלת
    ממילא ל-123 בייטים."""
    length = struct.unpack(">I", await _read_exact(reader, 4))[0]
    if length > 200:
        return "(סיבה ארוכה מדי)"
    return (await _read_exact(reader, length)).decode("utf-8", "replace")[:40]


async def authenticate_machine(reader, writer, secret: str) -> None:
    """‏#839/#1077: לחיצת-יד RFB 3.8 מול imagectl-monitor, עד SecurityResult.

    הפרוקסי — ולא הדפדפן — הוא הצד שמזדהה: הוא בוחר את סוג 2 בלבד, עונה
    ל-challenge ב-HMAC-SHA256(סוד, challenge) הקטום ל-16 בייטים, וקורא
    את התוצאה. הסוד עצמו אינו נשלח. ‏None (סוג 1) **לעולם אינו נבחר** גם
    אם הוצע: מוניטור שמציע אותו הוא סוכן שלא שודרג או מישהו שמתחזה
    למכונה, ושניהם אינם מסלול. אחרי ההצלחה המכונה ממתינה ל-ClientInit
    — והוא מגיע מהדפדפן דרך הממסר הרגיל.
    """
    banner = await _read_exact(reader, 12)
    if not banner.startswith(b"RFB 003."):
        raise MachineAuthError("על 5900 עונה משהו שאינו RFB")
    writer.write(RFB_VERSION)
    await writer.drain()
    count = (await _read_exact(reader, 1))[0]
    if count == 0:
        raise MachineAuthError(f"המוניטור דחה את החיבור: {await _reason(reader)}")
    offered = await _read_exact(reader, count)
    if RFB_SECURITY_SECRET not in offered:
        raise MachineAuthError(
            "המוניטור במכונה אינו דורש סוד — סוכן ישן? (סוגי אבטחה "
            f"{sorted(offered)})")
    writer.write(bytes([RFB_SECURITY_SECRET]))
    await writer.drain()
    challenge = await _read_exact(reader, 16)
    writer.write(hmac_response(secret, challenge))
    await writer.drain()
    result = struct.unpack(">I", await _read_exact(reader, 4))[0]
    if result != 0:
        raise MachineAuthError(f"המוניטור דחה את סוד השרת: {await _reason(reader)}")


async def bridge_browser(websocket: WebSocket, reader, writer, *,
                         idle_seconds: float = IDLE_SECONDS) -> str:
    """מגשר WebSocket שכבר התקבל (‏`accept_browser`) לזרם RFB שכבר עבר
    אימות מול המכונה.

    הדפדפן רואה בדיוק את מה שראה תמיד (‏#839): גרסה, סוג-אבטחה None יחיד,
    ‏SecurityResult OK — ואז הבייטים עוברים לשני הכיוונים. משמש
    את המוניטור המקומי **וגם** את המוניטור דרך המשני (#655 v1), ששם
    ``reader``/``writer`` הם המנהרה מהמשני ולא TCP למכונה. חוזר עם סיבת
    הסיום — ``browser``/``machine``/``idle``/``refused`` — ליומן;
    ביטול/ניתוק מטופלים אצל הקורא.

    ‏#1129: הכיוון דפדפן→מכונה עובר **בגבולות הודעה** (RFB 3.8, סוגים
    0/2/3/4/5), ו-ClientCutText (6) נדחה בשם — עד כאן פקודת הכוח
    ‏`imagectl-power:` נסעה בו, והאישור בהקלדת שם היה UI בלבד; עכשיו היא
    עוברת רק ב-`POST …/power` שמאמת את השם **בשרת**. סוג לא מוכר נסגר
    1002. הכיוון מכונה→דפדפן נשאר גולמי — הפרסור שלו בדפדפן, עם תקרות.
    ‏idle_seconds בלי בייט לאף כיוון → שני הצדדים נסגרים (4408)."""
    pending = bytearray()
    loop = asyncio.get_running_loop()
    last_activity = loop.time()
    outcome = "browser"

    async def from_browser() -> bytes | None:
        """מסגרת בינארית אחת; ‏None = הדפדפן התנתק."""
        nonlocal last_activity
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            return None
        payload = message.get("bytes")
        if payload is None:
            # RFB is binary. Reject accidental text frames.
            await websocket.close(code=1003)
            return None
        last_activity = loop.time()
        return payload

    async def browser_exact(n: int) -> bytes | None:
        while len(pending) < n:
            payload = await from_browser()
            if payload is None:
                return None
            pending.extend(payload)
        taken = bytes(pending[:n])
        del pending[:n]
        return taken

    # ‏#839: לחיצת-היד מול הדפדפן היא של הפרוקסי, לא של המכונה —
    # המכונה כבר עברה אימות למעלה. הדפדפן רואה בדיוק את מה שראה
    # לפני: גרסה, סוג-אבטחה None יחיד, ‏SecurityResult OK. מכאן
    # והלאה הבייטים עוברים כמו שהם (ClientInit → ServerInit …).
    await websocket.send_bytes(RFB_VERSION)
    if await browser_exact(12) is None:
        return outcome
    await websocket.send_bytes(b"\x01\x01")
    choice = await browser_exact(1)
    if choice is None:
        return outcome
    if choice != b"\x01":
        await websocket.close(code=1002, reason="סוג אבטחה לא צפוי")
        return "refused"
    await websocket.send_bytes(b"\x00\x00\x00\x00")
    # ‏ClientInit (בייט shared יחיד) הוא ההודעה היחידה בלי סוג — עובר כמו
    # שהוא; מכאן כל הודעה מהדפדפן נושאת סוג.
    client_init = await browser_exact(1)
    if client_init is None:
        return outcome
    writer.write(client_init)
    await writer.drain()

    async def browser_to_machine() -> None:
        nonlocal outcome
        buf = bytearray(pending)
        pending.clear()
        while True:
            while buf:
                try:
                    length = client_message_length(buf)
                except ValueError as exc:
                    await websocket.close(code=1002,
                                          reason=f"הודעת RFB לא מוכרת: {exc}")
                    outcome = "refused"
                    return
                if length is None or len(buf) < length:
                    break
                if buf[0] == RFB_CLIENT_CUT_TEXT:
                    await websocket.close(code=WS_FORBIDDEN,
                                          reason=POWER_OVER_WS_REFUSED)
                    outcome = "refused"
                    return
                writer.write(bytes(buf[:length]))
                del buf[:length]
            await writer.drain()
            payload = await from_browser()
            if payload is None:
                return
            buf.extend(payload)

    async def machine_to_browser() -> None:
        nonlocal last_activity, outcome
        while True:
            payload = await reader.read(65536)
            if not payload:
                outcome = "machine"
                return
            last_activity = loop.time()
            await websocket.send_bytes(payload)

    async def idle_watch() -> None:
        nonlocal outcome
        while True:
            remaining = idle_seconds - (loop.time() - last_activity)
            if remaining <= 0:
                outcome = "idle"
                return
            await asyncio.sleep(remaining)

    upstream = asyncio.create_task(browser_to_machine())
    downstream = asyncio.create_task(machine_to_browser())
    watchdog = asyncio.create_task(idle_watch())
    tasks = (upstream, downstream, watchdog)
    try:
        done, not_done = await asyncio.wait(
            tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in not_done:
            task.cancel()
        await asyncio.gather(*done, *not_done, return_exceptions=True)
        if watchdog in done:
            await websocket.close(
                code=WS_IDLE,
                reason=f"המוניטור נסגר — {int(idle_seconds // 60)} דק' בלי תעבורה")
    finally:
        # ביטול של ה-endpoint עצמו (‏#725) לא משאיר משימת ממסר יתומה.
        for task in tasks:
            if not task.done():
                task.cancel()
    return outcome


def create_monitor_router(
    ctx: ServerContext,
    *,
    open_connection: OpenConnection = asyncio.open_connection,
    now_fn: Callable[[], datetime] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    active: set[str] = set()
    #: ‏#1129: הכותב אל המכונה של כל גשר פתוח — הנתיב היחיד של פקודת כוח.
    bridges: dict[str, asyncio.StreamWriter] = {}
    lock = asyncio.Lock()

    @router.get("/monitor/settings")
    def read_settings(user=Depends(admin_only)):
        """‏admin בלבד. גם הקריאה ניהולית — משתמש deploy מקבל 403, כמו
        מתג ה-SSH: רשימת מי שאפשר להציץ אליו היא מידע ניהולי."""
        del user
        return {"port": MONITOR_PORT, "enabled": stations_enabled(ctx.conn)}

    @router.get("/monitor/machines")
    def list_machines(user=Depends(admin_only)):
        """‏#822: דף המוניטור בקונסולה. רק build/cloner — אותו JOIN
        ואותו חלון-נוכחות של ``_target``, כדי ש"מחובר" בדף יהיה מה
        שהשרת יסכים לחבר אליו, ולא חישוב בדפדפן."""
        del user
        return machine_rows(ctx.conn, now_fn() if now_fn else None)

    @router.put("/monitor/settings")
    def set_settings(body: dict, user=Depends(admin_only)):
        """הדלקה דורשת הקלדה מפורשת (עיקרון 7): מוניטור מעמיד שירות
        ‏RFB בכל תחנת בנייה/שיכפול, ולחיצה מקרית אינה נראית עד שמישהו
        מתחבר. כיבוי הוא הכיוון אל ברירת המחדל ואינו דורש אישור."""
        want = bool(body.get("enabled", False))
        if want and body.get("confirm") != MONITOR_CONFIRM:
            raise HTTPException(
                409, f"הדלקת מוניטור בכל התחנות — יש להקליד בדיוק: "
                     f"{MONITOR_CONFIRM}")
        set_setting(ctx.conn, STATION_KEY, flag_json(want))
        journal(ctx.conn, "monitor_stations",
                "on" if want else "off", user[0])
        return {"enabled": stations_enabled(ctx.conn)}

    async def reserve(mac: str) -> bool:
        async with lock:
            if mac in active:
                return False
            active.add(mac)
            return True

    async def release(mac: str) -> None:
        async with lock:
            active.discard(mac)
            bridges.pop(mac, None)

    @router.post("/monitor/{mac}/power")
    async def power(mac: str, body: dict, user=Depends(admin_only)):
        """‏#1129: הפעלה-מחדש/כיבוי של המכונה המנוטרת — האישור **בשרת**.
        עד כאן הדפדפן שלח `imagectl-power:` ב-ClientCutText על ה-WS אחרי
        בדיקת שם ב-JS בלבד, והשם הגיע מ-`?name=` בכתובת (R48 ממצא 10):
        כל מי שיש לו WS של admin כיבה מכונה בלי המודאל. עכשיו: ‏`confirm`
        מושווה ל-`suffix` **מהטבלה**, הגשר מסרב ל-ClientCutText מהדפדפן,
        והפקודה נכתבת אל המכונה מכאן. דורש מוניטור פתוח (409): הערוץ
        למכונה הוא ה-RFB המאומת של הגשר, ואין ערוץ אחר."""
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        action = body.get("action")
        if action not in POWER_ACTIONS:
            raise HTTPException(400, "פעולה לא מוכרת — reboot או poweroff")
        name = machine_name(ctx.conn, canonical)
        if name is None:
            raise HTTPException(404, "מכונה לא מוכרת")
        if body.get("confirm") != name:
            raise HTTPException(403, "השם שהוקלד אינו זהה לשם המחשב")
        writer = bridges.get(canonical)
        if writer is None:
            raise HTTPException(
                409, "אין מוניטור פתוח למכונה — הפקודה נוסעת דרך חיבור המוניטור")
        writer.write(power_frame(action))
        await writer.drain()
        journal(ctx.conn, "monitor_power", f"{canonical} {action}", user[0])
        return {"ok": True, "action": action}

    @router.websocket("/monitor/{mac}")
    async def monitor(
        websocket: WebSocket,
        mac: str,
    ):
        # ‏#1129: ‏Origin זר נסגר לפני accept — דף באתר אחר שמנצל את ה-cookie
        # אינו מקבל 101, בדיוק כמו ConsoleSourceGuard. הלקוח הלגיטימי
        # (monitor.js) שולח Origin של השרת עצמו.
        if not origin_allowed(websocket.headers):
            await websocket.close(code=WS_FORBIDDEN, reason=ORIGIN_REFUSED)
            return
        # ‏#904: accept לפני השער, אחרת כל סגירה למטה מגיעה לדפדפן כ-403
        # אילם (ראו `accept_browser`). ה-accept אינו פותח דבר מול המכונה.
        await accept_browser(websocket)
        # ‏השער נבדק כאן ידנית, ולא דרך `Depends(admin_only)`. ‏Depends
        # על websocket מפיל את החיבור בקוד גנרי, וה-RFB proxy הוא נתיב
        # שאסור שאי-הרשאה תיגע בו בכלל: הסגירה חייבת לקרות **לפני**
        # ‏`open_connection`, עם קוד מובחן (4401/4403) שהלקוח קורא. הבקרה
        # השלילית מסירה את בדיקת ה-admin ומראה ש-deploy מגיע ל-connector
        # במקום להיסגר 4403 — כלומר השורה הזאת היא מה שסוגר את הדלת.
        found = auth.check(ctx.conn, websocket.cookies.get(auth.COOKIE_NAME))
        if found is None:
            await websocket.close(code=WS_UNAUTHENTICATED, reason="נדרשת התחברות")
            return
        if found[1] != "admin":
            await websocket.close(code=WS_FORBIDDEN, reason="פעולה למנהל בלבד")
            return

        canonical = registry.normalize_mac(mac)
        if canonical is None:
            await websocket.close(code=4404, reason="מכונה לא מוכרת")
            return

        # ‏אחרי השער: אי-זמינות המכונה נסגרת גם היא כ-WebSocket, ולא
        # כ-HTTPException — חריגה בנתיב websocket אחרי השער היא כשל
        # לא מטופל, ולכן קוד הסטטוס מתורגם לקוד סגירה (4000+status).
        try:
            ip, _role, secret, machine_auth = _target(ctx, canonical,
                                              now_fn() if now_fn else None)
        except HTTPException as exc:
            await websocket.close(code=4000 + exc.status_code,
                                  reason=close_reason(str(exc.detail)))
            return
        if secret is None:
            # ‏#839: בלי סוד אין עם מה להזדהות — ולכן גם אין TCP. מוניטור
            # שהיה נפתח כאן "כמו פעם" הוא בדיוק המסלול הלא-מאומת שנסגר.
            await websocket.close(
                code=WS_MACHINE_AUTH,
                reason="המכונה לא דיווחה סוד מוניטור — סוכן ישן?")
            return
        if machine_auth != MONITOR_AUTH_HMAC:
            # ‏#1077: סוכן ישן ששולח סוד גולמי. לא נופלים לגולמי בשקט —
            # "לא הצלחנו לאמת" אינו "אימתנו" (עיקרון 5). אתחול מספיק.
            await websocket.close(
                code=WS_MACHINE_AUTH,
                reason="המכונה אינה תומכת באתגר-תגובה (HMAC) — סוכן ישן? אתחל מחדש")
            return
        if not await reserve(canonical):
            await websocket.close(code=4409, reason="כבר פתוח מוניטור למכונה הזאת")
            return

        writer: asyncio.StreamWriter | None = None
        try:
            try:
                reader, writer = await asyncio.wait_for(
                    open_connection(ip, MONITOR_PORT), timeout=3.0
                )
            except (OSError, asyncio.TimeoutError):
                # ‏`finally` משחרר את ההזמנה; כאן רק סוגרים את החיבור.
                await websocket.close(
                    code=4502, reason="שירות המוניטור במכונה אינו זמין")
                return

            try:
                await asyncio.wait_for(
                    authenticate_machine(reader, writer, secret), timeout=5.0)
            except MachineAuthError as exc:
                await websocket.close(code=WS_MACHINE_AUTH,
                                      reason=close_reason(str(exc)))
                return
            except asyncio.TimeoutError:
                await websocket.close(
                    code=WS_MACHINE_AUTH,
                    reason="המוניטור לא השלים את לחיצת-היד תוך 5 שניות")
                return

            bridges[canonical] = writer
            journal(ctx.conn, "monitor_connected", canonical, found[0])
            # ניתוק/ביטול תוך כדי הגשר (ה-runner מבטל את ה-endpoint כשהדפדפן
            # הלך — ראו למטה) = "הדפדפן סגר"; חריגה אחרת = "כשל".
            outcome = "error"
            try:
                outcome = await bridge_browser(websocket, reader, writer,
                                               idle_seconds=IDLE_SECONDS)
            except (WebSocketDisconnect, asyncio.CancelledError):
                outcome = "browser"
                raise
            finally:
                journal(ctx.conn, "monitor_closed", f"{canonical} {outcome}",
                        found[0])
        except (WebSocketDisconnect, asyncio.CancelledError):
            # ‏WebSocketDisconnect: הלקוח סגר בצורה מסודרת. ‏CancelledError:
            # ה-ASGI runner מבטל את משימת ה-endpoint בזמן פירוק החיבור —
            # ‏Starlette TestClient מבטל מיד אחרי websocket.disconnect, ו-uvicorn
            # נוהג כך בכיבוי. ‏`gather(return_exceptions=True)` בולע רק חריגות
            # של המשימות-הבנות; ביטול של המשימה שמריצה את ה-gather עצמה חוצה
            # אותה החוצה (#725). מגיעים לכאן רק אחרי שאחת ממשימות הממסר כבר
            # הסתיימה — סגירה רגילה, לא כשל — ואסור שהביטול יצא מה-endpoint:
            # יציאה כזו מסמנת את ה-future של ה-runner כמבוטל ו-result() זורק.
            pass
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await release(canonical)

    return router
