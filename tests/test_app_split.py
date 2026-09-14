"""‏#731 (tracer 1 של #703): פיצול המאזינים — סוכן מול קונסולה.

מה שנבדק כאן, וזה **התנהגות** ולא מבנה:
- **מלאי-נתיבים**: כל נתיב עונה באפליקציה הנכונה, ומחזיר 404 באחרת.
- **זהות משותפת**: שתי האפליקציות חולקות `ctx`/`conn` **אחד** — לא שני
  ‏DB (‏CLAUDE.md gotcha: שני חיבורי sqlite = "cannot commit").
- **אתחול חד-פעמי**: ה-sweep רץ **פעם אחת** לכל runtime, לא לכל אפליקציה.
- **כיבוי מתואם / כשל bind**: ‏`serve_all` מוריד את כל השרתים כשאחד יצא,
  ומגלגל החוצה חריגת bind במקום להיראות כאילו עלה (עיקרון 5).

הבקרה השלילית (בגוף ה-PR): מוטציה שבה כל אפליקציה בונה runtime משלה —
זהות הופכת שקרית וה-sweep רץ פעמיים, ובדיוק שני הטסטים האלה נופלים.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
    from starlette.routing import Mount
except ImportError:  # pragma: no cover
    TestClient = None

from server import app as app_module
from server.app import (create_agent_app, create_console_app, create_kiosk_app,
                        create_runtime)
from server.main import serve_all


def _runtime(tmp_path: Path, images_root: Path):
    """runtime עם שולח מזויף — אף round לא נפתח כאן, אבל השולח נבנה
    בכל מקרה, וריאל אחד היה מגיע ל-udp-sender (‏conftest חוסם ומכשיל)."""
    from test_sender import Recorder                       # noqa: PLC0415
    return create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        sender_runner=Recorder(block=True),
    )


def _mounts(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, Mount)}


def _absent(client: TestClient, method: str, path: str) -> bool:
    """הנתיב אינו קיים באפליקציה הזו — 404 ברמת ה-app (ולא 4xx אחר
    שמעיד שהנתיב **כן** קיים אבל דחה את הבקשה)."""
    return client.request(method, path, json={}).status_code == 404


def _present(client: TestClient, method: str, path: str) -> bool:
    return client.request(method, path, json={}).status_code != 404


@pytest.fixture()
def split(tmp_path: Path, images_root: Path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    rt = _runtime(tmp_path, images_root)
    agent_app = create_agent_app(rt)
    console_app = create_console_app(rt)
    yield rt, agent_app, console_app
    rt.ctx.sender.stop()


# --- מלאי נתיבים -----------------------------------------------------------

def test_agent_routes_only_on_agent_app(split):
    rt, agent_app, console_app = split
    agent, console = TestClient(agent_app), TestClient(console_app)

    # ‎/api/v1 של הסוכן + capture + מאזין ‎/boot — כאן, לא בקונסולה.
    assert _present(agent, "POST", "/api/v1/agent/hello")
    assert _absent(console, "POST", "/api/v1/agent/hello")
    # capture-manifest מחזיר 404 גם כשהנתיב קיים (המשימה לא נמצאה), ולכן
    # מבדילים דרך המתודה: GET על נתיב PUT → 405 אם הנתיב רשום, 404 אם לא.
    assert agent.get("/api/v1/capture/t1/manifest").status_code == 405
    assert console.get("/api/v1/capture/t1/manifest").status_code == 404
    assert "/boot" in _mounts(agent_app)
    assert "/boot" not in _mounts(console_app)


def test_console_routes_only_on_console_app(split):
    rt, agent_app, console_app = split
    agent, console = TestClient(agent_app), TestClient(console_app)

    # קונסולה + סטטי ‎/console — כאן, לא בסוכן.
    assert _present(console, "POST", "/api/console/login")
    assert _absent(agent, "POST", "/api/console/login")
    assert TestClient(console_app).get("/console/").status_code == 200
    assert _absent(agent, "GET", "/console/")
    assert "/console" in _mounts(console_app)
    assert "/console" not in _mounts(agent_app)


def test_kiosk_station_router_moved_to_kiosk_app(split):
    # ‏#738 (tracer 2 של #703): הקיוסק (`create_station_router`, ‏prefix
    # `/api/v1/agent/{groups,state,sessions}`) חולץ מהקונסולה לאפליקציית
    # הקיוסק. עכשיו נתיבי התחנה חיים על הקיוסק **בלבד** — לא על הסוכן
    # ולא על הקונסולה. זה ה-socket-boundary של אסטרה: וילן הכיתות פוגש
    # את הקיוסק, ואפליקציית הניהול אינה מאזינה שם.
    rt, agent_app, console_app = split
    kiosk_app = create_kiosk_app(rt)
    agent, console = TestClient(agent_app), TestClient(console_app)
    kiosk = TestClient(kiosk_app)
    assert _present(kiosk, "POST", "/api/v1/agent/sessions")
    assert _present(kiosk, "GET", "/api/v1/agent/state?mac=aa:bb:cc:dd:ee:ff")
    assert _absent(agent, "POST", "/api/v1/agent/sessions")
    assert _absent(console, "POST", "/api/v1/agent/sessions")
    assert _absent(console, "GET", "/api/v1/agent/state?mac=aa:bb:cc:dd:ee:ff")


# --- זהות משותפת -----------------------------------------------------------

def test_both_apps_share_one_runtime(split):
    rt, agent_app, console_app = split
    # אותו ctx בדיוק — לא שתי מופעים.
    assert agent_app.state.ctx is console_app.state.ctx
    assert agent_app.state.ctx is rt.ctx
    # ובעיקר אותו חיבור DB (זה מה שמונע את "cannot commit").
    assert agent_app.state.ctx.conn is console_app.state.ctx.conn
    assert rt.conn is agent_app.state.ctx.conn
    assert agent_app.state.runtime is console_app.state.runtime


# --- אתחול חד-פעמי ---------------------------------------------------------

def test_startup_sweep_runs_once_per_runtime(tmp_path, images_root, monkeypatch):
    if TestClient is None:
        pytest.skip("fastapi is required")
    calls: list[Path] = []
    real = app_module.sweep_work_areas

    def counting_sweep(conn, root):
        calls.append(root)
        return real(conn, root)

    monkeypatch.setattr(app_module, "sweep_work_areas", counting_sweep)

    rt = _runtime(tmp_path, images_root)
    try:
        create_agent_app(rt)
        create_console_app(rt)
        # runtime אחד → sweep אחד, גם אחרי בניית שתי האפליקציות.
        assert len(calls) == 1
    finally:
        rt.ctx.sender.stop()


# --- כיבוי מתואם וכשל bind (serve_all) -------------------------------------

class _FakeServer:
    """‏uvicorn.Server מזויף: אותו חוזה מינימלי ש-`serve_all` נשען עליו —
    ‏`serve()` קורוטינה ו-`should_exit` שמסמן לו לצאת."""

    def __init__(self, mode: str):
        self.mode = mode
        self.should_exit = False
        self.served = False

    async def serve(self):
        import asyncio
        self.served = True
        if self.mode == "quick":
            return                       # יצא מיד (Ctrl-C על שרת אחד)
        if self.mode == "raise":
            raise OSError("address already in use")   # כשל bind
        while not self.should_exit:      # "wait": חי עד שמבקשים ממנו לצאת
            await asyncio.sleep(0.005)


def test_serve_all_coordinated_shutdown():
    import asyncio
    quick, waiter = _FakeServer("quick"), _FakeServer("wait")
    asyncio.run(serve_all([quick, waiter]))
    # השרת שיצא מיד הוריד גם את זה שהיה ממתין.
    assert waiter.served and waiter.should_exit
    assert quick.should_exit


def test_serve_all_bind_failure_propagates_and_stops_others():
    import asyncio
    failing, waiter = _FakeServer("raise"), _FakeServer("wait")
    with pytest.raises(OSError):
        asyncio.run(serve_all([failing, waiter]))
    # כשל bind מגלגל חריגה **וגם** מוריד את השרת השני — אין חצי-שרת.
    assert waiter.should_exit
