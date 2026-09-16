"""#151: השומר לפי כתובת מקור על `/console` ו-`/api/console`.

שלוש שכבות בדיקה:
- יחידה על ``parse_networks`` — פרסור CIDR/כתובת בודדת, ודחיית קלט שגוי.
- יחידה על ``ConsoleSourceGuard`` — הזרקת ``scope`` ישירה (בלי TestClient),
  כמו ``tests/test_boot_http.py``, כדי לשלוט בדיוק בכתובת ה-peer, כולל
  ``scope`` בלי ``client`` בכלל.
- אינטגרציה על ``create_console_app`` דרך ``TestClient(app, client=...)`` —
  מוכיחה שהשומר אכן מותקן באפליקציה האמיתית, ושהוא **כבוי כברירת מחדל**
  (לא שובר את test_app_split.py ואת שאר הבדיקות שלא ביקשו אותו).
- ‏WebSocket (#859): אותו peer שנחסם ב-HTTP נחסם גם במוניטור — הסגירה
  לפני ``accept``, בקוד 4403; מכתובת מותרת הבקשה מגיעה עד שער ה-cookie
  של המוניטור (4401) — הראיה שהשומר העביר אותה ולא בלע אותה.

עיקרון 5 כאן: בקשה שאי אפשר לדעת מאיפה הגיעה — נחסמת, לא נפתחת.
"""

from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path

import pytest

from server.console_source_guard import ConsoleSourceGuard, parse_networks

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


# --- parse_networks ---------------------------------------------------------

def test_parse_networks_cidr_and_single_address():
    nets = parse_networks(["10.44.0.0/24", "10.10.10.1"])
    assert nets == (
        ipaddress.ip_network("10.44.0.0/24"),
        ipaddress.ip_network("10.10.10.1/32"),
    )


def test_parse_networks_tolerates_host_bits_set():
    # ‏strict=False: "10.44.0.5/24" היא טעות הקלדה נפוצה (רוצים את הרשת,
    # לא את המארח), וההכוונה ברורה — לא נכשל עליה.
    nets = parse_networks(["10.44.0.5/24"])
    assert nets == (ipaddress.ip_network("10.44.0.0/24"),)


def test_parse_networks_rejects_garbage():
    with pytest.raises(ValueError):
        parse_networks(["not-an-ip"])


# --- ConsoleSourceGuard: הזרקת scope ישירה ----------------------------------

async def _inner_ok(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _run(app, scope: dict) -> dict:
    messages: list[dict] = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages
                    if m["type"] == "http.response.body")
    return {"status": status, "body": body}


def _http_scope(client) -> dict:
    return {"type": "http", "path": "/console/", "client": client}


def test_allows_request_from_configured_network():
    guard = ConsoleSourceGuard(_inner_ok, networks=parse_networks(["10.10.10.0/24"]))
    result = _run(guard, _http_scope(("10.10.10.5", 51000)))
    assert result == {"status": 200, "body": b"ok"}


def test_denies_request_outside_configured_networks():
    guard = ConsoleSourceGuard(_inner_ok, networks=parse_networks(["10.10.10.0/24"]))
    result = _run(guard, _http_scope(("10.20.0.7", 51000)))
    assert result["status"] == 403
    assert b"151" in result["body"]


def test_fails_closed_when_client_is_missing():
    """‏scope בלי ``client`` בכלל — אי אפשר לדעת מאיפה הגיעה הבקשה.
    עיקרון 5: זה חוסם, לא "כנראה מקומי/מותר"."""
    guard = ConsoleSourceGuard(_inner_ok, networks=parse_networks(["10.10.10.0/24"]))
    scope = {"type": "http", "path": "/console/", "client": None}
    result = _run(guard, scope)
    assert result["status"] == 403


def test_fails_closed_on_unparseable_client_address():
    guard = ConsoleSourceGuard(_inner_ok, networks=parse_networks(["10.10.10.0/24"]))
    scope = _http_scope(("not-an-ip", 51000))
    result = _run(guard, scope)
    assert result["status"] == 403


def test_non_http_scope_passes_through_untouched():
    """אירוע lifespan (לא http) לא נבדק לפי כתובת — אחרת השרת לא עולה."""
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])

    guard = ConsoleSourceGuard(inner, networks=parse_networks(["10.10.10.0/24"]))
    asyncio.run(guard({"type": "lifespan"}, None, None))
    assert calls == ["lifespan"]


# --- ConsoleSourceGuard: websocket (#859) -----------------------------------

def _ws_scope(client) -> dict:
    return {"type": "websocket", "path": "/api/console/monitor/aa:bb:cc:dd:ee:ff",
            "client": client}


def _run_ws(scope: dict) -> tuple[list[str], list[dict]]:
    """מריץ scope של websocket דרך שומר על ``10.10.10.0/24`` ומחזיר (סוגי
    ה-scope שהגיעו ל-inner, ההודעות שנשלחו). ה-inner מדמה handler שמקבל
    את החיבור — כך שאם השומר העביר אותו, ``websocket.accept`` יופיע
    בהודעות."""
    calls: list[str] = []
    messages: list[dict] = []

    async def inner(scope, receive, send):
        calls.append(scope["type"])
        await send({"type": "websocket.accept"})

    async def receive():
        return {"type": "websocket.disconnect"}

    async def send(message):
        messages.append(message)

    guard = ConsoleSourceGuard(inner, networks=parse_networks(["10.10.10.0/24"]))
    asyncio.run(guard(scope, receive, send))
    return calls, messages


def test_websocket_outside_allowed_networks_is_closed_before_accept():
    calls, messages = _run_ws(_ws_scope(("10.20.0.7", 51000)))
    assert calls == []                                  # ה-handler לא נגע בחיבור
    assert [m["type"] for m in messages] == ["websocket.close"]
    assert messages[0]["code"] == 4403


def test_websocket_without_client_is_closed_before_accept():
    """גם ב-websocket: אין ראיה מאיפה הגיע — חוסמים (עיקרון 5)."""
    calls, messages = _run_ws(_ws_scope(None))
    assert calls == []
    assert [m["type"] for m in messages] == ["websocket.close"]


def test_websocket_inside_allowed_networks_reaches_the_handler():
    calls, messages = _run_ws(_ws_scope(("10.10.10.5", 51000)))
    assert calls == ["websocket"]
    assert [m["type"] for m in messages] == ["websocket.accept"]


# --- אינטגרציה: create_console_app -----------------------------------------

@pytest.fixture()
def runtime(tmp_path: Path, images_root: Path):
    from test_sender import Recorder                       # noqa: PLC0415
    from server.app import create_runtime
    rt = create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        sender_runner=Recorder(block=True),
        console_allowed_networks=parse_networks(["10.10.10.0/24", "10.44.0.0/24"]),
    )
    yield rt
    rt.ctx.sender.stop()


def test_console_app_denies_source_outside_allowed_networks(runtime):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.app import create_console_app
    app = create_console_app(runtime)
    client = TestClient(app, client=("10.99.0.1", 12345))
    resp = client.get("/console/")
    assert resp.status_code == 403


def test_console_app_allows_source_inside_allowed_networks(runtime):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.app import create_console_app
    app = create_console_app(runtime)
    client = TestClient(app, client=("10.10.10.1", 12345))
    resp = client.get("/console/")
    assert resp.status_code == 200


def test_console_app_monitor_websocket_denied_outside_allowed_networks(runtime):
    """‏#859: המוניטור (#690) הוא WebSocket על אפליקציית הקונסולה. מכתובת
    זרה הוא נסגר 4403 לפני accept — לפני שער ה-cookie של המוניטור, ולכן
    בלי קשר לזהות. מכתובת מותרת אותה בקשה מגיעה לשער ומקבלת 4401 (אין
    עוגייה) — הראיה שהשומר העביר אותה הלאה, לא בלע."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from starlette.websockets import WebSocketDisconnect
    from server.app import create_console_app
    app = create_console_app(runtime)
    path = "/api/console/monitor/aa:bb:cc:dd:ee:ff"

    with pytest.raises(WebSocketDisconnect) as denied:
        with TestClient(app, client=("10.99.0.1", 12345)).websocket_connect(path):
            pass
    assert denied.value.code == 4403

    with pytest.raises(WebSocketDisconnect) as allowed:
        with TestClient(app, client=("10.10.10.1", 12345)).websocket_connect(path):
            pass
    assert allowed.value.code == 4401


def test_console_app_guard_is_off_by_default(tmp_path: Path, images_root: Path):
    """בלי ``console_allowed_networks`` — התנהגות היום, בלי קשר לכתובת."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415
    from server.app import create_console_app, create_runtime
    rt = create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        sender_runner=Recorder(block=True),
    )
    try:
        app = create_console_app(rt)
        client = TestClient(app, client=("203.0.113.9", 12345))
        assert client.get("/console/").status_code == 200
    finally:
        rt.ctx.sender.stop()
