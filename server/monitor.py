"""Admin-only browser-to-machine RFB proxy and monitor boot gate."""

from __future__ import annotations

import asyncio
import json
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


def _target(ctx: ServerContext, mac: str,
            now: datetime | None = None) -> tuple[str, str]:
    canonical = registry.normalize_mac(mac)
    if canonical is None:
        raise HTTPException(404, "מכונה לא מוכרת")

    row = ctx.conn.execute(
        "SELECT d.ip, d.last_seen, g.role "
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
    return row["ip"], row["role"]


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
            ip, _role = _target(ctx, canonical, now_fn() if now_fn else None)
        except HTTPException as exc:
            await websocket.close(code=4000 + exc.status_code,
                                  reason=str(exc.detail))
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

            offered = websocket.headers.get("sec-websocket-protocol", "")
            protocol = "binary" if "binary" in {
                item.strip() for item in offered.split(",")
            } else None
            await websocket.accept(subprotocol=protocol)

            async def browser_to_machine() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    payload = message.get("bytes")
                    if payload is None:
                        # RFB is binary. Reject accidental text frames.
                        await websocket.close(code=1003)
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
