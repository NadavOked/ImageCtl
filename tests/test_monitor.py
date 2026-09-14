"""המוניטור מרחוק (#690): שער ההגדרות (admin בלבד, הדלקה בהקלדה),
ושער ה-WebSocket שסוגר אנונימי (4401) ו-deploy (4403) **לפני** שהוא
פותח חיבור TCP למכונה — ואז מעביר בייטים גולמיים של RFB ל-admin.

מה שנבדק כאן הוא לא "האם ההגדרה נשמרה" אלא **מי מגיע לשירות ה-RFB**.
שער שנכשל ומחבר את מי שאסור לו הוא בדיוק המצב המסוכן: לכן הבדיקה
המרכזית מוודאת שה-connector **לא נקרא** כשההרשאה חסרה. הבקרה השלילית
(מחיקת בדיקת התפקיד) הופכת אותה מירוקה-תמיד לתופסת-באג.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("fastapi")

from server import auth, monitor
from server.db import net_seen, set_setting

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

MAC = "aa:bb:cc:dd:ee:ff"
IP = "10.44.12.50"


class FakeReader:
    """קורא RFB מזויף: מגיש את הבאנר פעם אחת, ואז חוסם עד ביטול —
    כדי שהצד השני (browser_to_machine) יספיק להעביר את בייטי הלקוח
    לפני שהזרם נסגר. ‏read שמחזיר מיד "" היה מסיים את הפרוקסי במרוץ."""

    def __init__(self):
        self._sent = False

    async def read(self, _n: int) -> bytes:
        if not self._sent:
            self._sent = True
            return b"RFB 003.008\n"
        await asyncio.Event().wait()   # נחסם עד שהמשימה מבוטלת ב-finally
        return b""


class FakeWriter:
    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def monitor_client(server, role: str, connector):
    ctx = server["ctx"]
    app = FastAPI()
    app.include_router(
        monitor.create_monitor_router(
            ctx,
            open_connection=connector,
        )
    )
    client = TestClient(app)
    client.cookies.set(
        auth.COOKIE_NAME,
        auth.issue(ctx.conn, "noc" if role == "admin" else "labtech", role),
    )
    return client


def prepare_build_machine(server):
    ctx = server["ctx"]
    response = server["admin"].post(
        "/api/console/machines",
        json={"mac": MAC, "name": "Monitor", "group_id": "grp_BUILD"},
    )
    assert response.status_code == 200
    net_seen(ctx.conn, MAC, IP)
    set_setting(ctx.conn, monitor.STATION_KEY, monitor.flag_json(True))


def test_monitor_settings_are_admin_only(server):
    assert server["anon"].get(
        "/api/console/monitor/settings"
    ).status_code == 401
    assert server["deploy"].get(
        "/api/console/monitor/settings"
    ).status_code == 403

    result = server["admin"].get("/api/console/monitor/settings")
    assert result.status_code == 200
    assert result.json()["port"] == monitor.MONITOR_PORT


def test_enabling_monitor_requires_explicit_confirmation(server):
    denied = server["admin"].put(
        "/api/console/monitor/settings",
        json={"enabled": True},
    )
    assert denied.status_code == 409

    enabled = server["admin"].put(
        "/api/console/monitor/settings",
        json={"enabled": True, "confirm": "imagectl.monitor"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


@pytest.mark.parametrize(
    ("role", "expected_code"),
    [("deploy", 4403)],
)
def test_websocket_behaviorally_rejects_non_admin_before_tcp(
    server,
    role,
    expected_code,
):
    """Negative control: removing the role guard makes this test connect."""
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, role, connector)

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}"):
            pass

    assert caught.value.code == expected_code
    assert called is False


def test_websocket_rejects_anonymous_before_tcp(server):
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    app = FastAPI()
    app.include_router(
        monitor.create_monitor_router(
            server["ctx"],
            open_connection=connector,
        )
    )

    with pytest.raises(WebSocketDisconnect) as caught:
        with TestClient(app).websocket_connect(
            f"/api/console/monitor/{MAC}"
        ):
            pass

    assert caught.value.code == 4401
    assert called is False


def test_admin_proxy_carries_raw_rfb_bytes(server):
    prepare_build_machine(server)
    reader = FakeReader()
    writer = FakeWriter()
    opened = []

    async def connector(host, port):
        opened.append((host, port))
        return reader, writer

    client = monitor_client(server, "admin", connector)

    with client.websocket_connect(
        f"/api/console/monitor/{MAC}",
        subprotocols=["binary"],
    ) as websocket:
        assert websocket.accepted_subprotocol == "binary"
        assert websocket.receive_bytes() == b"RFB 003.008\n"
        websocket.send_bytes(b"client-rfb-data")

    assert opened == [(IP, monitor.MONITOR_PORT)]
    assert writer.writes == [b"client-rfb-data"]
    assert writer.closed is True


def test_monitor_client_does_not_require_a_subprotocol():
    """‏#690: הלקוח פותח WebSocket **בלי** לבקש subprotocol.

    ‏`new WebSocket(url, "binary")` מחייב את השרת להחזיר "binary" בתשובת
    ה-101; ‏uvicorn עם מימוש ה-WS ‎wsproto **אינו** מהדהד את ה-subprotocol
    (‎websockets כן), ואז Chrome סוגר את החיבור מיד (1006 → "החיבור נסגר",
    מסך ריק). נמדד על הברזל: מול אותו שרת, ‎ws=websockets החזיר "binary"
    ו-ws=wsproto החזיר None. ‏RFB עובד על מסגרות בינאריות גולמיות בלי שום
    subprotocol, אז לא דורשים אותו — וזה חסין לשני המימושים.

    בקרה שלילית: החזרת ``, "binary"`` ל-new WebSocket מפילה את הבדיקה.
    """
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent
            / "server" / "static" / "monitor.js").read_text(encoding="utf-8")
    assert re.search(r"new WebSocket\s*\(", text), "אין קריאת new WebSocket ב-monitor.js"
    # ארגומנט שני (אחרי הפסיק) ל-new WebSocket הוא ה-subprotocol — אסור.
    assert not re.search(r"new WebSocket\s*\(\s*[\w.]+\([^)]*\)\s*,", text), (
        "monitor.js מבקש subprotocol מ-new WebSocket — wsproto לא מהדהד "
        "אותו ו-Chrome סוגר את החיבור (1006)")
