"""#1085 — כניסה: admin/admin + כפייה, נוהל סיסמאות, MFA, הגבלת ניסיונות."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server import totp, users
from server.app import create_console_app, create_runtime


def _fresh(tmp_path, images_root, clock, monkeypatch):
    """אפליקציית הקונסולה (:8081) — MFA והחלפה כפויה. הקיוסק נבדק בנפרד.

    ‏`create_app` המשולב שם את הקיוסק ראשון (#1073), ולכן login שם מדלג
    על MFA. בייצור הם על שני פורטים.
    """
    from server import sender as sender_module
    from test_sender import Recorder
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    rt = create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
    )
    return create_console_app(rt)


def _console_client(server):
    return TestClient(create_console_app(server["app"].state.runtime))


def test_fresh_admin_admin_is_forced_to_change_password(
        tmp_path, images_root, clock, monkeypatch):
    app = _fresh(tmp_path, images_root, clock, monkeypatch)
    c = TestClient(app)
    r = c.post("/api/console/login", json={"username": "admin", "password": "admin"})
    assert r.status_code == 200
    assert r.json() == {"must_change_password": True}
    assert c.get("/api/console/me").status_code == 403
    assert c.get("/api/console/me").json()["detail"] == "password_change_required"
    assert c.get("/api/console/users").status_code == 403
    r = c.post("/api/console/me/password", json={
        "current_password": "admin", "new_password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    me = c.get("/api/console/me")
    assert me.status_code == 200
    assert me.json()["username"] == "admin"
    assert me.json()["is_builtin"] is True
    assert me.json()["mfa_enabled"] is False
    assert me.json()["must_change_password"] is False


@pytest.mark.parametrize("password, snippet", [
    ("12345678", "אותיות"),
    ("abcdefgh", "מספרים"),
    ("abcd1234", "תו מיוחד"),
    ("short", "8 תווים"),
])
def test_password_policy_four_rules(server, password, snippet):
    r = server["admin"].post("/api/console/users", json={
        "username": "pwtest", "password": password, "role": "deploy",
    })
    assert r.status_code == 400
    assert snippet in r.json()["detail"]


def test_aa12345_bang_is_accepted(server):
    assert server["admin"].post("/api/console/users", json={
        "username": "nadavok", "password": "Aa12345!", "role": "deploy",
    }).status_code == 200


def test_duplicate_username_is_409_case_insensitive(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "DupUser", "password": "Aa12345!", "role": "deploy",
    }).status_code == 200
    r = admin.post("/api/console/users", json={
        "username": "dupuser", "password": "Aa12345!", "role": "deploy",
    })
    assert r.status_code == 409


def test_new_admin_must_enroll_mfa(server):
    admin = server["admin"]
    assert admin.post("/api/console/users", json={
        "username": "nadav", "password": "Aa12345!", "role": "admin",
    }).status_code == 200
    c = _console_client(server)
    r = c.post("/api/console/login", json={"username": "nadav", "password": "Aa12345!"})
    assert r.status_code == 200
    assert r.json() == {"mfa_enrollment_required": True}
    assert c.get("/api/console/users").status_code == 403
    assert c.get("/api/console/users").json()["detail"] == "mfa_enrollment_required"
    setup = c.post("/api/console/me/mfa/setup")
    assert setup.status_code == 200
    secret = setup.json()["secret"]
    assert setup.json()["otpauth_url"].startswith("otpauth://totp/ImageCtl:")
    code = totp.totp_code(secret)
    assert c.post("/api/console/me/mfa/verify", json={"code": code}).status_code == 200
    en = c.post("/api/console/me/mfa/enable", json={"code": totp.totp_code(secret)})
    assert en.status_code == 200, en.text
    assert en.json()["mfa_enabled"] is True
    assert len(en.json()["backup_codes"]) == 8
    assert all(len(x) == 10 for x in en.json()["backup_codes"])
    assert c.get("/api/console/me").status_code == 200
    assert c.get("/api/console/me").json()["mfa_enabled"] is True


def test_two_step_login_and_wrong_code(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "mfa2", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "mfa2", "Aa12345!")
    c2 = _console_client(server)
    r = c2.post("/api/console/login", json={"username": "mfa2", "password": "Aa12345!"})
    assert r.json()["mfa_required"] is True
    challenge = r.json()["challenge"]
    assert c2.get("/api/console/me").status_code == 401
    bad = c2.post("/api/console/login/mfa", json={"challenge": challenge, "code": "000000"})
    assert bad.status_code == 401
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'mfa2'"
    ).fetchone()["mfa_secret"]
    ok = c2.post("/api/console/login/mfa", json={
        "challenge": challenge, "code": totp.totp_code(secret),
    })
    assert ok.status_code == 200, ok.text
    assert ok.json()["username"] == "mfa2"


def test_same_totp_code_twice_is_rejected(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "replay", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "replay", "Aa12345!")
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'replay'"
    ).fetchone()["mfa_secret"]
    code = totp.totp_code(secret)
    c2 = _console_client(server)
    ch1 = c2.post("/api/console/login", json={
        "username": "replay", "password": "Aa12345!",
    }).json()["challenge"]
    assert c2.post("/api/console/login/mfa", json={
        "challenge": ch1, "code": code,
    }).status_code == 200
    c3 = _console_client(server)
    ch2 = c3.post("/api/console/login", json={
        "username": "replay", "password": "Aa12345!",
    }).json()["challenge"]
    again = c3.post("/api/console/login/mfa", json={"challenge": ch2, "code": code})
    assert again.status_code == 401


def test_backup_code_is_single_use(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "bak", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    body = complete_console_login(c, server["ctx"].conn, "bak", "Aa12345!")
    codes = body["backup_codes"]
    c2 = _console_client(server)
    ch = c2.post("/api/console/login", json={
        "username": "bak", "password": "Aa12345!",
    }).json()["challenge"]
    assert c2.post("/api/console/login/mfa", json={
        "challenge": ch, "code": codes[0],
    }).status_code == 200
    c3 = _console_client(server)
    ch = c3.post("/api/console/login", json={
        "username": "bak", "password": "Aa12345!",
    }).json()["challenge"]
    assert c3.post("/api/console/login/mfa", json={
        "challenge": ch, "code": codes[0],
    }).status_code == 401


def test_remember_browser_skips_mfa_until_it_expires(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "rem", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "rem", "Aa12345!")
    secret = server["ctx"].conn.execute(
        "SELECT mfa_secret FROM users WHERE username = 'rem'"
    ).fetchone()["mfa_secret"]
    c2 = _console_client(server)
    ch = c2.post("/api/console/login", json={
        "username": "rem", "password": "Aa12345!",
    }).json()["challenge"]
    r = c2.post("/api/console/login/mfa", json={
        "challenge": ch, "code": totp.totp_code(secret), "remember_browser": True,
    })
    assert r.status_code == 200
    assert "imagectl_trusted" in r.cookies
    skip = c2.post("/api/console/login", json={"username": "rem", "password": "Aa12345!"})
    assert skip.json().get("username") == "rem"
    assert "mfa_required" not in skip.json()
    server["ctx"].conn.execute("UPDATE trusted_browsers SET expires_at = 1")
    server["ctx"].conn.commit()
    again = c2.post("/api/console/login", json={"username": "rem", "password": "Aa12345!"})
    assert again.json().get("mfa_required") is True


def test_builtin_cannot_setup_mfa(tmp_path, images_root, clock, monkeypatch):
    app = _fresh(tmp_path, images_root, clock, monkeypatch)
    c = TestClient(app)
    c.post("/api/console/login", json={"username": "admin", "password": "admin"})
    c.post("/api/console/me/password", json={
        "current_password": "admin", "new_password": "Aa12345!",
    })
    r = c.post("/api/console/me/mfa/setup")
    assert r.status_code == 403
    assert r.json()["detail"] == "builtin_no_mfa"


def test_admin_disable_mfa_forces_reenrollment(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "off", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "off", "Aa12345!")
    assert admin.post("/api/console/users/off/mfa/disable").status_code == 200
    c2 = _console_client(server)
    r = c2.post("/api/console/login", json={"username": "off", "password": "Aa12345!"})
    assert r.json().get("mfa_enrollment_required") is True


def test_five_failures_delay_and_ten_lock(server, monkeypatch):
    from server import login_guard
    t = {"now": 1_000_000.0}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    anon = server["anon"]
    body = {"username": "labtech", "password": "wrong"}
    for _ in range(5):
        assert anon.post("/api/console/login", json=body).status_code == 401
    delayed = anon.post("/api/console/login", json=body)
    assert delayed.status_code == 429
    row = server["ctx"].conn.execute("SELECT key, failures FROM login_attempts").fetchone()
    assert row["failures"] == 5
    server["ctx"].conn.execute(
        "UPDATE login_attempts SET failures = 9, last_at = ?", (t["now"] - 100,),
    )
    server["ctx"].conn.commit()
    t["now"] += 1
    assert anon.post("/api/console/login", json=body).status_code == 401
    locked = anon.post("/api/console/login", json=body)
    assert locked.status_code == 403
    assert "15 דקות" in locked.json()["detail"]
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "login_lockout" in events


def test_revoke_disconnects_a_live_session(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "cut", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "cut", "Aa12345!")
    assert c.get("/api/console/me").status_code == 200
    assert admin.post("/api/console/sessions/revoke",
                      json={"username": "cut"}).status_code == 200
    assert c.get("/api/console/me").status_code == 401


def test_kiosk_login_skips_mfa(server):
    from server.app import create_kiosk_app
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "kioskadm", "password": "Aa12345!", "role": "admin",
    })
    c = _console_client(server)
    complete_console_login(c, server["ctx"].conn, "kioskadm", "Aa12345!")
    kiosk = TestClient(create_kiosk_app(server["app"].state.runtime))
    r = kiosk.post("/api/console/login", json={
        "username": "kioskadm", "password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    assert r.json().get("username") == "kioskadm"
    assert "mfa_required" not in r.json()


def test_agent_login_without_mfa_and_must_change(server):
    from conftest import setup_classroom
    ids = setup_classroom(server)
    mac = ids["mac1"]
    # deploy — בלי MFA
    r = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "deploy-pass-1", "mac": mac,
    })
    # identity gate may refuse from TestClient without lease; skip if so
    if r.status_code == 403 and r.json().get("code", "").startswith("identity"):
        pytest.skip("שומר הזהות דולק בפיקסטורה הזו")
    assert r.status_code == 200, r.text
    users.change_own_password(server["ctx"].conn, "labtech",
                              "deploy-pass-1", "Aa12345!")
    # force must_change
    server["ctx"].conn.execute(
        "UPDATE users SET must_change_password = 1 WHERE username = 'labtech'")
    server["ctx"].conn.commit()
    r = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "Aa12345!", "mac": mac,
    })
    if r.status_code == 403 and r.json().get("code", "").startswith("identity"):
        pytest.skip("שומר הזהות דולק בפיקסטורה הזו")
    assert r.status_code == 403
    assert "החלף סיסמה בקונסולה קודם" in r.json()["error"]


def test_ensure_admin_creates_forced_builtin_and_does_not_touch_existing(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "a.db")
    assert users.ensure_admin(conn) == "admin"
    info = users.flags(conn, "admin")
    assert info["is_builtin"] is True
    assert info["must_change_password"] is True
    assert users.verify(conn, "admin", "admin") == "admin"
    assert users.ensure_admin(conn) is None
    users.change_own_password(conn, "admin", "admin", "Aa12345!")
    assert users.ensure_admin(conn) is None
    assert users.verify(conn, "admin", "Aa12345!") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is False

    conn2 = connect(tmp_path / "b.db")
    users.create(conn2, "nadav", "Aa12345!", "admin", by="t")
    assert users.ensure_admin(conn2) is None
    assert users.lookup(conn2, "admin") is None
    assert users.ensure_admin(conn2, password="Custom1!") is None
    assert users.lookup(conn2, "admin") is None


def test_ensure_admin_with_password_skips_force(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "c.db")
    assert users.ensure_admin(conn, password="Custom1!") is None
    info = users.flags(conn, "admin")
    assert info["is_builtin"] is True
    assert info["must_change_password"] is False
    assert users.verify(conn, "admin", "Custom1!") == "admin"


def test_reset_password_forces_change(server):
    admin = server["admin"]
    admin.post("/api/console/users", json={
        "username": "rst", "password": "Aa12345!", "role": "admin",
    })
    r = admin.post("/api/console/users/rst/reset-password")
    assert r.status_code == 200
    new = r.json()["new_password"]
    c = _console_client(server)
    body = c.post("/api/console/login", json={"username": "rst", "password": new}).json()
    assert body.get("must_change_password") is True


def test_deploy_with_mfa_still_refused_on_console_before_mfa(server):
    """#1073 לפני #1085: deploy עם MFA מופעל מקבל deploy_no_console, לא mfa_required."""
    from server import auth
    conn = server["ctx"].conn
    conn.execute(
        "UPDATE users SET mfa_enabled = 1, mfa_secret = 'MFRGGZDFMY' "
        "WHERE username = 'labtech'"
    )
    conn.commit()
    c = _console_client(server)
    r = c.post("/api/console/login", json={
        "username": "labtech", "password": "deploy-pass-1",
    })
    assert r.status_code == 403, r.text
    assert r.json() == {"error": "deploy_no_console",
                        "message_he": auth.DEPLOY_NO_CONSOLE_HE}
    assert "mfa_required" not in r.json()
    assert auth.COOKIE_NAME not in r.cookies


def test_password_change_session_is_not_blocked_by_console_only(server):
    """#1085 + #1073: session מוגבלת להחלפת סיסמה עוברת את console_only."""
    conn = server["ctx"].conn
    users.create(conn, "pwadmin", "Aa12345!", "admin", by="test",
                 is_builtin=True, must_change_password=True)
    c = _console_client(server)
    r = c.post("/api/console/login", json={
        "username": "pwadmin", "password": "Aa12345!",
    })
    assert r.status_code == 200, r.text
    assert r.json() == {"must_change_password": True}
    assert c.get("/api/console/me").status_code == 403
    assert c.get("/api/console/me").json()["detail"] == "password_change_required"
    changed = c.post("/api/console/me/password", json={
        "current_password": "Aa12345!", "new_password": "Bb12345!",
    })
    assert changed.status_code == 200, changed.text
    assert c.get("/api/console/me").status_code == 200
    assert c.get("/api/console/me").json()["username"] == "pwadmin"
