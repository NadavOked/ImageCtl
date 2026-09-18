"""‏#738 (tracer 2 של #703): הקיוסק כאפליקציה נפרדת עם allowlist קשיח.

מה שנבדק כאן הוא **התנהגות**, לא מבנה:

- **allowlist קשיח** — מלאי הנתיבים של `kiosk_app` שווה **בדיוק** לרשימה
  המוצהרת, ובנוסף נבדק התנהגותית שנתיבי ניהול (users/machines/net/dhcp/
  storage/monitor/journal/settings/library-management) מחזירים 404 על
  הקיוסק. שני צדי הטענה: מה שצריך — יש, מה שאסור — אין.
- **כל workflow של הקיוסק עובד** על `kiosk_app`: כניסה, מסך התחנה
  (state/groups/sessions/active), פתיחת סבב כיתה, קליטה, וחדר השיכפולים.
- **בקרה שלילית** (`test_negative_control_*`): כשמחברים ל-kiosk_app ראוטר
  אסור (storage/users), טסט ה-allowlist נכשל **התנהגותית** — הנתיב האסור
  הופך נגיש, ומלאי הנתיבים מפסיק להיות שווה לרשימה.

הגבול הוא ה-socket (אסטרה, #703): אפליקציית הניהול לא תאזין על כתובת
שכיתה רואה. הטסט הזה הוא ההוכחה שה-socket של הקיוסק נושא רק את הקיוסק.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from server import users
from server.app import create_console_app, create_kiosk_app, create_runtime
from server.console_storage import create_storage_router
from server.console_api import create_console_router

from conftest import setup_classroom


#: מלאי הנתיבים המדויק של `kiosk_app` — (method, path). כל שינוי מכוון
#: כאן מחייב שינוי מקביל ב-`server/kiosk.py`; כל שינוי לא-מכוון (ראוטר
#: ניהול שנדבק) נופל כאן. ‏`/` הוא ה-redirect למסך התחנה.
EXPECTED_KIOSK_ROUTES = frozenset({
    ("GET", "/"),
    # תת-קבוצת /api/console המסוננת (allowlist הקיוסק)
    ("POST", "/api/console/login"),
    ("GET", "/api/console/groups"),
    ("GET", "/api/console/images"),
    ("GET", "/api/console/folders"),
    ("POST", "/api/console/folders"),
    ("POST", "/api/console/sessions"),
    ("POST", "/api/console/sessions/{session_id}/start"),
    ("POST", "/api/console/sessions/{session_id}/close"),
    ("POST", "/api/console/tasks/capture"),
    # חדר השיכפולים (ראוטר שלם — כולו קיוסק)
    ("GET", "/api/console/room"),
    ("POST", "/api/console/room"),
    ("POST", "/api/console/room/start"),
    ("POST", "/api/console/room/wake"),
    ("POST", "/api/console/room/close"),
    # מסך התחנה (ראוטר שלם — כולו קיוסק)
    ("GET", "/api/v1/agent/groups"),
    ("GET", "/api/v1/agent/groups/{group_id}/machines"),
    ("GET", "/api/v1/agent/sessions/active"),
    ("GET", "/api/v1/agent/state"),
    ("POST", "/api/v1/agent/sessions"),
})

#: נתיבי ניהול שאסור שיהיו נגישים על הקיוסק — (method, path). נבדקים
#: **התנהגותית** (404), לא רק מבנית: אלה הנתיבים שאסטרה מסמנת כמסוכנים
#: אם ייחשפו לווילן הכיתות.
FORBIDDEN_ON_KIOSK = (
    ("GET", "/api/console/users"),
    ("GET", "/api/console/machines"),
    ("GET", "/api/console/overview"),
    ("GET", "/api/console/me"),
    ("POST", "/api/console/logout"),
    ("GET", "/api/console/journal"),
    ("GET", "/api/console/settings"),
    ("GET", "/api/console/storage-nodes"),
    ("GET", "/api/console/storage-locations"),
    ("POST", "/api/console/storage-locations"),
    ("POST", "/api/console/images/upload"),
    ("POST", "/api/console/folders/order"),
)


def _api_routes(app: FastAPI) -> set[tuple[str, str]]:
    """מלאי נתיבי ה-API של האפליקציה — (method, path), בלי HEAD/OPTIONS
    האוטומטיים ובלי ה-Mount הסטטי (`/console`, ‏`/boot`) ובלי openapi.

    ‏FastAPI עוטף `include_router` כ-`_IncludedRouter` מקונן (לא משטח את
    ה-routes ל-`app.routes`), ולכן יורדים רקורסיבית לכל אובייקט שיש לו
    רשימת `routes` ואוספים רק את עלי ה-APIRoute."""
    routes: set[tuple[str, str]] = set()

    def walk(container) -> None:
        for route in getattr(container, "routes", ()):
            if isinstance(route, APIRoute):
                for method in route.methods or ():
                    if method not in ("HEAD", "OPTIONS"):
                        routes.add((method, route.path))
            # ‏FastAPI עוטף `include_router` כ-`_IncludedRouter` ששומר את
            # ה-routes תחת `original_router`; תת-ראוטרים רגילים תחת `routes`.
            elif getattr(route, "original_router", None) is not None:
                walk(route.original_router)
            elif hasattr(route, "routes"):
                walk(route)

    walk(app)
    return routes


@pytest.fixture()
def kiosk_env(tmp_path: Path, images_root: Path):
    """runtime אחד → console_app (להקמה כ-admin) + kiosk_app (הנבדק).

    ‏WoL מזויף (`wol_send`) כדי ש-`/room/wake` יעבוד בלי חומרה. סבב לא
    נפתח בהקמה, אבל השולח נבנה בכל מקרה — ריאל אחד היה מגיע ל-udp-sender
    (conftest חוסם ומכשיל)."""
    from test_sender import Recorder                       # noqa: PLC0415

    woken: list[bytes] = []
    rt = create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        sender_runner=Recorder(block=True), wol_send=woken.append,
    )
    console_app = create_console_app(rt)
    kiosk_app = create_kiosk_app(rt)
    users.create(rt.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(rt.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)

    admin_console = TestClient(console_app)
    assert admin_console.post(
        "/api/console/login", json={"username": "noc", "password": "admin-pass-123"}
    ).status_code == 200
    # ההקמה (קבוצה + מכונות) היא ניהול — נעשית דרך הקונסולה, כי היא
    # במכוון אינה נגישה על הקיוסק. שני ה-app חולקים את אותו conn.
    setup_classroom({"admin": admin_console})
    # קבוצת השיכפולים (`grp_CLONERS`) נזרעת בברירת המחדל של ה-DB —
    # אין צורך ליצור אותה. רושמים בה מכונת שיכפול אחת כדי שסבב חדר
    # יוכל להיפתח (בלי מכונות רשומות הפתיחה נדחית).
    assert admin_console.post(
        "/api/console/machines/import",
        json={"group_id": "grp_CLONERS", "text": "aa:bb:cc:00:00:20 c1\n"},
    ).json()["saved"] == 1

    yield {"rt": rt, "console_app": console_app, "kiosk_app": kiosk_app,
           "kiosk": TestClient(kiosk_app), "anon": TestClient(kiosk_app),
           "woken": woken}
    rt.ctx.sender.stop()


def _login_kiosk(env, username="noc", password="admin-pass-123") -> TestClient:
    client = TestClient(env["kiosk_app"])
    assert client.post(
        "/api/console/login", json={"username": username, "password": password}
    ).status_code == 200
    return client


# --- allowlist קשיח ---------------------------------------------------------

def test_kiosk_route_inventory_is_exactly_the_allowlist(kiosk_env):
    # מה שצריך — יש; מה שלא הוצהר — אין. שוויון, לא הכלה.
    assert _api_routes(kiosk_env["kiosk_app"]) == EXPECTED_KIOSK_ROUTES


def test_forbidden_admin_apis_are_not_reachable_on_kiosk(kiosk_env):
    # התנהגותית: כל נתיב ניהול מחזיר 404 ברמת ה-app — הנתיב פשוט לא
    # קיים על ה-socket הזה, גם למשתמש מחובר.
    admin = _login_kiosk(kiosk_env)
    for method, path in FORBIDDEN_ON_KIOSK:
        assert admin.request(method, path, json={}).status_code == 404, \
            f"{method} {path} נגיש על הקיוסק — הוא אמור להיות ניהול בלבד"


def test_image_manifests_are_not_exposed_on_the_kiosk_socket(kiosk_env):
    """המסך מקבל פרטי הרחבה מ-/api/console/images אחרי כניסה; ה-manifest
    המלא אינו נוסף ל-allowlist ולכן גם משתמש מחובר מקבל 404."""
    admin = _login_kiosk(kiosk_env)
    images = admin.get("/api/console/images").json()
    assert images and images[0]["expand_partitions"]
    assert admin.get(f"/api/v1/images/{images[0]['id']}/manifest").status_code == 404


def test_agent_and_boot_are_not_on_kiosk(kiosk_env):
    # הסוכן והאתחול חיים על אפליקציית הסוכן, לא על הקיוסק.
    kiosk = kiosk_env["anon"]
    assert kiosk.post("/api/v1/agent/hello", json={}).status_code == 404
    assert "/boot" not in {r.path for r in kiosk_env["kiosk_app"].routes
                           if hasattr(r, "path") and not isinstance(r, APIRoute)}


# --- workflows של הקיוסק -----------------------------------------------------

def test_login_workflow(kiosk_env):
    ok = kiosk_env["kiosk"].post(
        "/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert ok.status_code == 200 and ok.json()["role"] == "admin"
    bad = kiosk_env["kiosk"].post(
        "/api/console/login", json={"username": "noc", "password": "x"})
    assert bad.status_code == 401


def test_station_state_and_lists_are_anonymous(kiosk_env):
    anon = kiosk_env["anon"]
    # מסך התחנה — בלי כניסה (הדף רץ על המכונה עצמה).
    assert anon.get("/api/v1/agent/sessions/active").json() == {"session": None}
    state = anon.get("/api/v1/agent/state?mac=b4:2e:99:07:1a:c4")
    assert state.status_code == 200 and state.json()["known"] is True
    groups = anon.get("/api/v1/agent/groups")
    assert groups.status_code == 200
    assert any(g["id"] == "grp_LAB1" for g in groups.json())


def test_open_class_round_workflow(kiosk_env):
    # פתיחת סבב מהתחנה — שם+סיסמה בגוף (ממשק התחנה), לא cookie.
    anon = kiosk_env["anon"]
    opened = anon.post("/api/v1/agent/sessions", json={
        "username": "labtech", "password": "deploy-pass-1",
        "mac": "b4:2e:99:07:1a:c4", "group_id": "grp_LAB1",
        "image_id": "img_7f3a91",
    })
    assert opened.status_code == 200, opened.text
    # הסבב חי בשרת המשותף — נראה גם דרך תצוגת הסבב הפעיל של הקיוסק.
    active = anon.get("/api/v1/agent/sessions/active").json()["session"]
    assert active is not None and active["group_id"] == "grp_LAB1"


def test_capture_workflow(kiosk_env):
    admin = _login_kiosk(kiosk_env)
    # רשימות שהמסך צורך לפני הקליטה.
    assert admin.get("/api/console/images").status_code == 200
    assert admin.get("/api/console/folders").status_code == 200
    assert admin.post("/api/console/folders", json={"name": "כיתות"}).status_code == 200
    created = admin.post("/api/console/tasks/capture", json={
        "mac": "b4:2e:99:07:1a:c4", "disk": "sda", "name": "win-image",
        "folder": "כיתות",
    })
    assert created.status_code == 200, created.text
    assert created.json()["id"].startswith("tsk_")


def test_room_workflow(kiosk_env):
    admin = _login_kiosk(kiosk_env)
    assert admin.get("/api/console/room").status_code == 200
    opened = admin.post("/api/console/room",
                        json={"image_id": "img_7f3a91", "target_drives": 2})
    assert opened.status_code == 200, opened.text
    woke = admin.post("/api/console/room/wake")
    assert woke.status_code == 200


# --- בקרה שלילית -------------------------------------------------------------
#
# ההוכחה שהטסט תופס: כשראוטר ניהול נדבק ל-kiosk_app, שני הטסטים של ה-
# allowlist נכשלים התנהגותית. אנחנו בונים את המוטציה כאן ומוודאים שהיא
# נופלת — כך שאם מחר מישהו יחבר ראוטר אסור בטעות, לא נסמוך על "נראה בסדר".

def _mutated_kiosk_app(rt, extra_router) -> FastAPI:
    app = create_kiosk_app(rt)
    app.include_router(extra_router)
    return app


def test_negative_control_storage_router_breaks_allowlist(kiosk_env):
    rt = kiosk_env["rt"]
    mutated = _mutated_kiosk_app(rt, create_storage_router(rt.ctx))
    # מלאי הנתיבים מפסיק להיות שווה לרשימה — ‏storage-nodes נדבקו.
    assert _api_routes(mutated) != EXPECTED_KIOSK_ROUTES
    assert ("GET", "/api/console/storage-nodes") in _api_routes(mutated)
    # והתנהגותית: נתיב אסור שהיה 404 הפך נגיש (לא 404).
    admin = TestClient(mutated)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    assert admin.get("/api/console/storage-nodes").status_code != 404


def test_negative_control_full_console_router_breaks_allowlist(kiosk_env):
    rt = kiosk_env["rt"]
    mutated = _mutated_kiosk_app(rt, create_console_router(rt.ctx))
    routes = _api_routes(mutated)
    assert routes != EXPECTED_KIOSK_ROUTES
    # users ו-machines — בדיוק מה שאסור על וילן הכיתות — נגישים כעת.
    admin = TestClient(mutated)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    assert admin.get("/api/console/users").status_code != 404
    assert admin.get("/api/console/machines").status_code != 404
