"""‏#703 (tracer 5): הקונסולה (8081) מוגשת ב-HTTPS עם תעודה חתומה-עצמית.

מה שנבדק כאן, כהתנהגות:
- הפעלה ראשונה עם ‎--console-host‏ שאינו loopback **יוצרת** תעודה+מפתח
  ב-``<data_dir>/console-tls/`` — המפתח 0600, ה-SAN הוא כרטיס הניהול
  ושם המארח — והפעלה שנייה **טוענת את אותה תעודה** (אותה טביעת אצבע).
- ‏``--console-tls off`` מתקבל רק על loopback; על כרטיס ניהול — סירוב
  בשם שני הדגלים, לא נפילה שקטה ל-HTTP.
- ‏``main`` מוליך את התעודה **למאזיני הקונסולה בלבד** (כולל המאזין
  הכפול על 127.0.0.1, #904) — לא לסוכן (8080) ולא לקיוסק (8082).
- עוגיית ההתחברות נושאת ``Secure`` תחת TLS, ולא בלעדיו (שומר מפני
  תיקון-יתר שהיה שובר את הרצת הפיתוח על http://127.0.0.1).
- ‏``/me`` מדווח ``tls`` עם טביעת האצבע (לשורת הסטטוס), ומסך הפורטים
  קורא ל-8081 ‏HTTPS.
- **לחיצת-יד אמיתית**: ‏uvicorn עם התעודה שנוצרה, לקוח stdlib שסומך רק
  עליה — 200. זו הראיה ש-``ssl_certfile``/``ssl_keyfile`` של stdlib
  מקבלים את המפתח (EC P-256, PKCS8) שהמחולל המשותף מפיק.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import stat
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

import server.main
from server import console_tls
from test_console_bind import run_main
from tools.e2e import harness

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

FP_RE = re.compile(r"^([0-9A-F]{2}:){31}[0-9A-F]{2}$")


# ---------- התעודה על הדיסק ----------

def test_first_start_creates_cert_and_0600_key_with_the_management_sans(tmp_path: Path):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "imagectl-srv")

    assert tls.cert_path == tmp_path / "data" / "console-tls" / "console.crt"
    assert tls.cert_path.is_file() and tls.key_path.is_file()
    assert b"BEGIN CERTIFICATE" in tls.cert_path.read_bytes()
    assert b"PRIVATE KEY" in tls.key_path.read_bytes()
    if os.name == "posix":
        assert stat.S_IMODE(tls.key_path.stat().st_mode) == 0o600
    assert set(tls.sans) == {"10.0.0.1", "imagectl-srv"}
    assert FP_RE.match(tls.fingerprint_sha256), tls.fingerprint_sha256


def test_second_start_loads_the_same_cert_not_a_new_one(tmp_path: Path):
    """הדפדפן מאשר פעם אחת — תעודה שמתחלפת בכל הפעלה הייתה מבקשת אישור
    בכל בוקר, ומרגילה את המפעיל ללחוץ "המשך" בלי לקרוא."""
    first = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    cert_bytes = first.cert_path.read_bytes()
    second = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")

    assert second.fingerprint_sha256 == first.fingerprint_sha256
    assert second.cert_path.read_bytes() == cert_bytes


def test_half_an_identity_is_refused_not_regenerated(tmp_path: Path):
    """תעודה בלי מפתח: יצירה מחדש שקטה הייתה מחליפה תעודה שדפדפנים כבר
    אישרו; הכשל נאמר בשמו."""
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    tls.key_path.unlink()

    with pytest.raises(console_tls.ConsoleTLSError, match="תעודה בלי מפתח"):
        console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")


# ---------- הדגל ----------

def test_tls_off_is_refused_off_loopback_and_named():
    with pytest.raises(console_tls.ConsoleTLSError) as exc:
        console_tls.check_tls_flag("off", "10.0.0.1")
    assert "--console-tls" in str(exc.value) and "--console-host" in str(exc.value)
    console_tls.check_tls_flag("off", "127.0.0.1")        # loopback — מותר
    console_tls.check_tls_flag("on", "10.0.0.1")           # TLS — תמיד מותר


def test_main_refuses_tls_off_on_a_management_nic(tmp_path: Path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as exc:
        run_main(tmp_path, monkeypatch,
                 "--console-host", "10.0.0.1", "--console-tls", "off")
    assert exc.value.code == 2
    err = capsys.readouterr().err
    # ‏"unrecognized arguments: --console-tls" של argparse גם יוצא 2 ומזכיר
    # את הדגל — הראיה שהסירוב הוא **שלנו** היא ההסבר על loopback.
    assert "--console-tls" in err and "loopback" in err, err


# ---------- main → uvicorn ----------

def test_main_gives_the_cert_to_every_console_listener_and_nothing_else(
        tmp_path: Path, monkeypatch):
    """ברירת המחדל היא TLS דלוק. שני מאזיני הקונסולה (כרטיס הניהול +
    ‏127.0.0.1, ‏#904) מקבלים את אותה תעודה מ-``data_dir``; הסוכן והקיוסק
    אינם משתנים (הסוכן הוא http בהכרח — GRUB בלי TLS)."""
    configs = run_main(tmp_path, monkeypatch, "--console-host", "10.0.0.1")

    console = configs["console"]
    assert sorted(c["host"] for c in console) == ["10.0.0.1", "127.0.0.1"]
    expected_cert = tmp_path / "data" / "console-tls" / "console.crt"
    for cfg in console:
        assert Path(cfg["ssl_certfile"]) == expected_cert
        assert Path(cfg["ssl_keyfile"]) == expected_cert.with_name("console.key")
    assert expected_cert.is_file()
    for app in ("agent", "kiosk"):
        for cfg in configs[app]:
            assert "ssl_certfile" not in cfg and "ssl_keyfile" not in cfg


def test_main_tls_off_on_loopback_serves_plain_http(tmp_path: Path, monkeypatch):
    configs = run_main(tmp_path, monkeypatch, "--console-tls", "off")

    assert [c["host"] for c in configs["console"]] == ["127.0.0.1"]
    assert "ssl_certfile" not in configs["console"][0]
    assert not (tmp_path / "data" / "console-tls").exists()


def test_e2e_harness_runs_the_console_on_loopback_without_tls(
        tmp_path: Path, monkeypatch):
    """ה-e2e מדבר עם הקונסולה ב-http (לקוח urllib פשוט) — מותר לו כי
    הקונסולה שלו על 127.0.0.1. הדגל חייב גם להתפרס ב-``server.main``
    (#201: argv שנבלע נראה כמו "השרת לא עלה")."""
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen",
                        lambda cmd, **kw: captured.append(list(cmd)) or object())
    harness.start_server(tmp_path)

    args = server.main.build_parser().parse_args(captured[0][3:])
    assert args.console_tls == "off" and args.console_host == "127.0.0.1"
    console_tls.check_tls_flag(args.console_tls, args.console_host)   # מותר


# ---------- הקונסולה: עוגייה, /me, פורטים ----------

def _console(tmp_path: Path, images_root: Path, clock, tls):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_console_app, create_runtime
    from test_console_ports import SS_DNSMASQ
    from test_sender import Recorder

    hooks = {"ss": lambda: SS_DNSMASQ, "unit_active": lambda name: "active",
             "http_get": lambda url: 200, "http_size": lambda url: (200, 1),
             "http_text": lambda url: (200, ""), "interfaces": lambda: [],
             "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: []}
    # אפליקציית הקונסולה **האמיתית** (8081), לא המעטפת המשולבת: במעטפת
    # ה-``/login`` של הקיוסק (8082, http — allowlist של מסך התחנה) נרשם
    # ראשון ומצל על זה של הקונסולה, ועוגיית הקיוסק בצדק אינה Secure.
    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        now_fn=clock, health_hooks=hooks, console_tls=tls,
                        sender_runner=Recorder(block=True))
    app = create_console_app(rt)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    base = "https://testserver" if tls else "http://testserver"
    client = TestClient(app, base_url=base)
    login = client.post("/api/console/login",
                        json={"username": "noc", "password": "admin-pass-123"})
    assert login.status_code == 200, login.text
    return client, login


def test_login_cookie_is_secure_under_tls(tmp_path: Path, images_root: Path, clock):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    client, login = _console(tmp_path, images_root, clock, tls)

    cookie = login.headers["set-cookie"]
    assert "secure" in cookie.lower(), cookie
    assert "httponly" in cookie.lower()
    assert client.get("/api/console/me").status_code == 200     # העוגייה חוזרת


def test_login_cookie_is_not_secure_without_tls(tmp_path: Path, images_root: Path, clock):
    """‏--console-tls off על 127.0.0.1: עוגיית Secure על http הייתה נזרקת
    על ידי הדפדפן, והמפתח לא היה מצליח להיכנס לעולם."""
    _client, login = _console(tmp_path, images_root, clock, None)
    assert "secure" not in login.headers["set-cookie"].lower()


def test_me_reports_the_fingerprint_for_the_status_bar(tmp_path: Path, images_root: Path, clock):
    tls = console_tls.ensure_console_cert(tmp_path / "data", "10.0.0.1", "srv")
    client, _login = _console(tmp_path, images_root, clock, tls)

    me = client.get("/api/console/me").json()
    assert me["tls"] == {"mode": "self-signed",
                         "fingerprint_sha256": tls.fingerprint_sha256}


def test_me_reports_no_tls_when_off(tmp_path: Path, images_root: Path, clock):
    client, _login = _console(tmp_path, images_root, clock, None)
    assert client.get("/api/console/me").json()["tls"] is None


def test_ports_screen_calls_8081_https(tmp_path: Path, images_root: Path, clock):
    client, _login = _console(tmp_path, images_root, clock, None)
    rows = {r["id"]: r for r in client.get("/api/console/ports").json()}
    assert rows["http_console"]["name"] == "HTTPS"
    assert rows["http_console"]["port"] == "8081"
    assert "HTTPS" in rows["http_console"]["note"] or "https" in rows["http_console"]["note"]


# ---------- לחיצת-יד אמיתית ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_uvicorn_serves_https_with_the_generated_cert(tmp_path: Path):
    """‏stdlib ssl בצד השרת (uvicorn) ובצד הלקוח, שסומך **רק** על התעודה
    שנוצרה ומאמת את שם המארח מול ה-IP SAN — 200. לקוח בלי התעודה נכשל
    בלחיצת-היד (השומר מפני "עבר כי לא אימת")."""
    uvicorn = pytest.importorskip("uvicorn")
    tls = console_tls.ensure_console_cert(tmp_path / "data", "127.0.0.1", "srv")

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/plain")]})
        await send({"type": "http.response.body", "body": b"tls-ok"})

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning",
        **tls.uvicorn_kwargs()))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if server.started:
                break
            thread.join(0.05)
        assert server.started, "uvicorn לא עלה"

        trusted = ssl.create_default_context(cafile=str(tls.cert_path))
        with urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=5,
                                    context=trusted) as resp:
            assert resp.status == 200 and resp.read() == b"tls-ok"

        untrusted = ssl.create_default_context()
        with pytest.raises(Exception) as exc:
            urllib.request.urlopen(f"https://127.0.0.1:{port}/", timeout=5,
                                   context=untrusted)
        assert "CERTIFICATE_VERIFY_FAILED" in str(exc.value)

        # ‏HTTP רגיל על מאזין ה-TLS אינו מקבל דף — fail-closed (uvicorn
        # סוגר/מחזיר שגיאה; העיקר: לא 200 ולא הפניה).
        with pytest.raises(Exception):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
    finally:
        server.should_exit = True
        thread.join(5)
