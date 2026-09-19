"""כפתור "עדכן" בהגדרות השרת (‏#748) — בדיקת-עדכון ואפליקציה מול הריפו
הציבורי. אף פקודת git/systemd-run אמיתית לא רצה כאן: כל חמשת ה-hooks
(‏describe/ls_remote_tags/fetch_tags/checkout/run_upgrade) מוזרקים דרך
``update_hooks``, ומחליפים לגמרי את server.update.default_hooks().
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import update as update_mod

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


# --- יחידה: פרסור סמנטי, בלי שרת ---------------------------------------------


def test_semver_key_accepts_only_vxyz():
    assert update_mod.semver_key("v0.24.0") == (0, 24, 0)
    assert update_mod.semver_key("v0.24.0-3-gabc123") is None
    assert update_mod.semver_key("0.24.0") is None


def test_parse_remote_tags_dedupes_annotated_peel_and_sorts():
    out = (
        "abc\trefs/tags/v0.9.0\n"
        "def\trefs/tags/v0.10.0\n"
        "def\trefs/tags/v0.10.0^{}\n"
        "xyz\trefs/heads/main\n"
        "qrs\trefs/tags/not-a-version\n"
    )
    assert update_mod.parse_remote_tags(out) == ["v0.9.0", "v0.10.0"]


def test_base_version_strips_describe_suffix():
    assert update_mod._base_version("v0.24.0-3-gabc123") == "v0.24.0"
    assert update_mod._base_version("v0.24.0") == "v0.24.0"
    assert update_mod._base_version(None) is None


# --- check_update: הראיה תמיד מוזרקת -----------------------------------------


def test_check_update_reports_available_when_remote_is_newer():
    hooks = {
        "describe": lambda repo_dir: "v0.24.0",
        "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.25.0\n", ""),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result == {"current": "v0.24.0", "latest": "v0.25.0",
                      "available": True, "reason": None}


def test_check_update_not_available_when_up_to_date():
    hooks = {
        "describe": lambda repo_dir: "v0.24.0",
        "ls_remote_tags": lambda url: (True, "x\trefs/tags/v0.24.0\n", ""),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result["available"] is False


def test_check_update_network_failure_is_not_no_versions():
    """‏עיקרון 5: הרצה שנכשלה אינה 'אין גרסאות' — שני מסלולים נבדלים."""
    hooks = {
        "describe": lambda repo_dir: "v0.24.0",
        "ls_remote_tags": lambda url: (False, "", "Could not resolve host"),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result["available"] is False
    assert result["latest"] is None
    assert "נכשלה" in result["reason"]


def test_check_update_no_tags_at_all_is_a_named_case():
    hooks = {
        "describe": lambda repo_dir: None,
        "ls_remote_tags": lambda url: (True, "", ""),
    }
    result = update_mod.check_update(hooks, "/repo", "https://example/pub.git")
    assert result["reason"] == "אין גרסאות (tags) בריפו הציבורי"


# --- שרת אמיתי (TestClient), הכול מוזרק --------------------------------------


@pytest.fixture()
def update_server(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    state = {"current": "v0.24.0", "remote": "x\trefs/tags/v0.24.0\n",
             "fetch_ok": True, "checkout_ok": True, "run_ok": True}
    calls = {"fetch": 0, "checkout": 0, "run": 0}

    hooks = {
        "describe": lambda repo_dir: state["current"],
        "ls_remote_tags": lambda url: (True, state["remote"], ""),
        "fetch_tags": lambda repo_dir: (calls.__setitem__("fetch", calls["fetch"] + 1)
                                        or (state["fetch_ok"], "")),
        "checkout": lambda repo_dir, tag: (calls.__setitem__("checkout", calls["checkout"] + 1)
                                           or (state["checkout_ok"], "")),
        "run_upgrade": lambda repo_dir, tag: (calls.__setitem__("run", calls["run"] + 1)
                                              or (state["run_ok"], "")),
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, update_hooks=hooks, repo_dir="/repo")
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "app": app, "state": state, "calls": calls}


def _enable(client):
    r = client.post("/api/console/settings", json={"update_enabled": True})
    assert r.status_code == 200


def test_me_reports_the_server_version_from_the_same_source_as_update(update_server):
    """‏#915: שורת הסטטוס בקונסולה מציגה את תג ה-git של העץ — מאותו hook
    ‏`describe` כמו `/update` — ולא `v0.9` קשיח ב-HTML. לכל משתמש מחובר,
    (הפצה — #1073 — לא נכנסת לקונסולה בכלל)."""
    from server import users
    admin, app, state = update_server["admin"], update_server["app"], update_server["state"]
    assert admin.get("/api/console/me").json()["version"] == "v0.24.0"
    state["current"] = "v0.25.0-2-gabc123"          # העץ זז — נקרא מחדש, לא מונח
    assert admin.get("/api/console/me").json()["version"] == "v0.25.0-2-gabc123"
    users.create(app.state.ctx.conn, "dep", "deploy-pass-123", "deploy", by="test", check_policy=False)
    deploy = TestClient(app)
    # ‏#1073 (18/09): למשתמש הפצה אין קונסולה — הכניסה מסורבת, ואין שורת סטטוס.
    # (ב-`create_app` המאוחד של הטסטים הכניסה של הקיוסק קודמת — ולכן הבדיקה
    # היא על הנתיב: `console_only` חוסם גם עם עוגייה. בייצור — `create_console_app`.)
    deploy.post("/api/console/login", json={"username": "dep", "password": "deploy-pass-123"})
    assert deploy.get("/api/console/me").status_code == 403
    state["current"] = None                          # אין תג על העץ = null, לא מספר מומצא
    assert admin.get("/api/console/me").json()["version"] is None


def test_update_disabled_by_default(update_server):
    """סעיף 3 בהאצלה: המתג כבוי כברירת מחדל, בלי שנגענו בו."""
    settings = update_server["admin"].get("/api/console/settings").json()
    assert settings.get("update_enabled") in (None, "false")


def test_switch_off_blocks_check_and_apply_with_404(update_server):
    admin = update_server["admin"]
    assert admin.post("/api/console/update/check").status_code == 404
    assert admin.post("/api/console/update/apply",
                      json={"tag": "v0.25.0", "confirm_name": "x"}).status_code == 404


def test_info_shows_current_version_even_when_switch_is_off(update_server):
    info = update_server["admin"].get("/api/console/update").json()
    assert info["current"] == "v0.24.0"
    assert info["enabled"] is False
    assert info["server_name"] == "10.44.12.10"


def test_check_returns_available_once_enabled(update_server):
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["remote"] = "x\trefs/tags/v0.25.0\n"
    result = admin.post("/api/console/update/check").json()
    # ‏#1000: התשובה היא המסמך שנשמר — עם חותמת `at` של השרת.
    assert result.pop("at")
    assert result == {"current": "v0.24.0", "latest": "v0.25.0",
                      "available": True, "reason": None}


def test_apply_rejects_bad_tag_format(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply",
                   json={"tag": "not-a-tag", "confirm_name": "10.44.12.10"})
    assert r.status_code == 400
    assert update_server["calls"]["fetch"] == 0


def test_apply_wrong_server_name_is_403_and_does_not_run(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply",
                   json={"tag": "v0.25.0", "confirm_name": "wrong-name"})
    assert r.status_code == 403
    assert update_server["calls"] == {"fetch": 0, "checkout": 0, "run": 0}


def test_apply_blocked_409_when_a_round_is_open(update_server):
    admin = update_server["admin"]
    _enable(admin)
    conn = update_server["app"].state.ctx.conn
    image_id = admin.get("/api/console/images").json()[0]["id"]
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES ('s1', 'g', ?, 'ROOM', 2, 300, 'running', 'nadav', ?, 0.0, 'multicast')",
        (image_id, "2026-09-16T00:00:00+00:00"))
    conn.commit()
    r = admin.post("/api/console/update/apply",
                   json={"tag": "v0.25.0", "confirm_name": "10.44.12.10"})
    assert r.status_code == 409
    assert update_server["calls"] == {"fetch": 0, "checkout": 0, "run": 0}


def test_apply_runs_fetch_checkout_then_script_and_reports_status(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/apply",
                   json={"tag": "v0.25.0", "confirm_name": "10.44.12.10"})
    assert r.status_code == 200
    assert r.json()["started"] is True
    assert update_server["calls"] == {"fetch": 1, "checkout": 1, "run": 1}

    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "applying"
    assert status["tag"] == "v0.25.0"
    # השרת עדיין לא "עלה מחדש" בתוך הבדיקה — ``current`` נשאר הישן,
    # ולכן טרם אומת. זה בדיוק ההבדל בין 'הופעל' ל'אומת' (סעיף 5, #748).
    assert status["verified"] is False


def test_apply_verified_true_once_current_version_matches_the_target(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0", "confirm_name": "10.44.12.10"})
    # מדמה את מה שה-restart של תוכנית השדרוג עושה בפועל: השרת עולה
    # מחדש על העץ שכבר עבר checkout, ו-``describe`` קורא את התג החדש.
    update_server["state"]["current"] = "v0.25.0"
    status = admin.get("/api/console/update/status").json()
    assert status["verified"] is True
    assert status["state"] == "done"


def test_fetch_failure_is_reported_and_stops_before_checkout(update_server):
    admin = update_server["admin"]
    _enable(admin)
    update_server["state"]["fetch_ok"] = False
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0", "confirm_name": "10.44.12.10"})
    assert update_server["calls"] == {"fetch": 1, "checkout": 0, "run": 0}
    status = admin.get("/api/console/update/status").json()
    assert status["state"] == "failed"


def test_revert_without_a_previous_version_is_404(update_server):
    admin = update_server["admin"]
    _enable(admin)
    r = admin.post("/api/console/update/revert",
                   json={"confirm_name": "10.44.12.10"})
    assert r.status_code == 404


def test_revert_uses_the_version_saved_before_the_last_apply(update_server):
    admin = update_server["admin"]
    _enable(admin)
    admin.post("/api/console/update/apply",
              json={"tag": "v0.25.0", "confirm_name": "10.44.12.10"})
    update_server["state"]["current"] = "v0.25.0"   # אחרי ה-restart המדומה
    r = admin.post("/api/console/update/revert",
                   json={"confirm_name": "10.44.12.10"})
    assert r.status_code == 200
    assert r.json()["tag"] == "v0.24.0"


def test_deploy_user_cannot_reach_update_endpoints(update_server, images_root, clock, tmp_path):
    from server import users
    from server.app import create_app
    app = update_server["app"]
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    deploy = TestClient(app)
    deploy.post("/api/console/login",
               json={"username": "labtech", "password": "deploy-pass-1"})
    assert deploy.get("/api/console/update").status_code == 403
    assert deploy.post("/api/console/update/check").status_code == 403
