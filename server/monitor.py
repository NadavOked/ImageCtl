"""Admin-only browser-to-machine RFB proxy and monitor boot gate."""

from __future__ import annotations

import asyncio
import json
import struct
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

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

#: ‏RFB security type 2 — מסגור VNC Authentication (challenge של 16
#: בייטים, תשובה של 16 בייטים). התשובה **אינה** DES של ה-challenge אלא
#: 16 בייטי הסוד עצמם; ראו הערת התכנון ב-agent/monitor.c.
RFB_SECURITY_SECRET = 2
RFB_VERSION = b"RFB 003.008\n"


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


def _target(ctx: ServerContext, mac: str,
            now: datetime | None = None) -> tuple[str, str, str | None]:
    """‏(ip, role, monitor_secret) של מכונה שמותר לפתוח אליה מוניטור.
    הסוד (#839) חוזר כמו שהוא — ‏None כשהמכונה מעולם לא דיווחה אחד."""
    canonical = registry.normalize_mac(mac)
    if canonical is None:
        raise HTTPException(404, "מכונה לא מוכרת")

    row = ctx.conn.execute(
        "SELECT d.ip, d.last_seen, d.monitor_secret, g.role "
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
    return row["ip"], row["role"], row["monitor_secret"]


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
    """‏#839: לחיצת-יד RFB 3.8 מול imagectl-monitor, עד SecurityResult.

    הפרוקסי — ולא הדפדפן — הוא הצד שמזדהה: הוא בוחר את סוג 2 בלבד, עונה
    ל-challenge ב-16 בייטי הסוד, וקורא את התוצאה. ‏None (סוג 1) **לעולם
    אינו נבחר** גם אם הוצע: מוניטור שמציע אותו הוא סוכן שלא שודרג או
    מישהו שמתחזה למכונה, ושניהם אינם מסלול. אחרי ההצלחה המכונה ממתינה
    ל-ClientInit — והוא מגיע מהדפדפן דרך הממסר הרגיל.
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
    await _read_exact(reader, 16)                     # ה-challenge; לא בשימוש
    writer.write(bytes.fromhex(secret))
    await writer.drain()
    result = struct.unpack(">I", await _read_exact(reader, 4))[0]
    if result != 0:
        raise MachineAuthError(f"המוניטור דחה את סוד השרת: {await _reason(reader)}")


def create_monitor_router(
    ctx: ServerContext,
    *,
    open_connection: OpenConnection = asyncio.open_connection,
    now_fn: Callable[[], datetime] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _, admin_only = auth.dependencies(ctx.conn)
    active: set[str] = set()
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
        rows = ctx.conn.execute(
            "SELECT m.mac, m.suffix, g.role, d.ip, d.last_seen "
            "FROM machines m "
            "JOIN groups g ON g.id = m.group_id "
            "LEFT JOIN net_devices d ON d.mac = m.mac "
            "WHERE g.role IN ('build', 'cloner') "
            "ORDER BY g.role, m.suffix"
        ).fetchall()
        now = now_fn() if now_fn else None
        return [
            {
                "mac": r["mac"],
                "name": r["suffix"],
                "role": r["role"],
                "ip": r["ip"],
                "online": bool(r["ip"]) and seen_within(r["last_seen"], now),
            }
            for r in rows
        ]

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

    @router.websocket("/monitor/{mac}")
    async def monitor(
        websocket: WebSocket,
        mac: str,
    ):
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
            ip, _role, secret = _target(ctx, canonical,
                                        now_fn() if now_fn else None)
        except HTTPException as exc:
            await websocket.close(code=4000 + exc.status_code,
                                  reason=str(exc.detail))
            return
        if secret is None:
            # ‏#839: בלי סוד אין עם מה להזדהות — ולכן גם אין TCP. מוניטור
            # שהיה נפתח כאן "כמו פעם" הוא בדיוק המסלול הלא-מאומת שנסגר.
            await websocket.close(
                code=WS_MACHINE_AUTH,
                reason="המכונה לא דיווחה סוד מוניטור — סוכן ישן?")
            return
        if not await reserve(canonical):
            await websocket.close(code=4409, reason="כבר פתוח מוניטור למכונה הזאת")
            return

        writer: asyncio.StreamWriter | None = None
        upstream: asyncio.Task | None = None
        downstream: asyncio.Task | None = None
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
                await websocket.close(code=WS_MACHINE_AUTH, reason=str(exc))
                return
            except asyncio.TimeoutError:
                await websocket.close(
                    code=WS_MACHINE_AUTH,
                    reason="המוניטור לא השלים את לחיצת-היד תוך 5 שניות")
                return

            offered = websocket.headers.get("sec-websocket-protocol", "")
            protocol = "binary" if "binary" in {
                item.strip() for item in offered.split(",")
            } else None
            await websocket.accept(subprotocol=protocol)

            pending = bytearray()

            async def from_browser() -> bytes | None:
                """מסגרת בינארית אחת; ‏None = הדפדפן התנתק."""
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return None
                payload = message.get("bytes")
                if payload is None:
                    # RFB is binary. Reject accidental text frames.
                    await websocket.close(code=1003)
                    return None
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
                return
            await websocket.send_bytes(b"\x01\x01")
            choice = await browser_exact(1)
            if choice is None:
                return
            if choice != b"\x01":
                await websocket.close(code=1002, reason="סוג אבטחה לא צפוי")
                return
            await websocket.send_bytes(b"\x00\x00\x00\x00")

            async def browser_to_machine() -> None:
                if pending:
                    writer.write(bytes(pending))
                    await writer.drain()
                    pending.clear()
                while True:
                    payload = await from_browser()
                    if payload is None:
                        return
                    writer.write(payload)
                    await writer.drain()

            async def machine_to_browser() -> None:
                while True:
                    payload = await reader.read(65536)
                    if not payload:
                        return
                    await websocket.send_bytes(payload)

            upstream = asyncio.create_task(browser_to_machine())
            downstream = asyncio.create_task(machine_to_browser())
            done, pending = await asyncio.wait(
                {upstream, downstream},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
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
            for task in (upstream, downstream):
                if task and not task.done():
                    task.cancel()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await release(canonical)

    return router
