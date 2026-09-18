"""‏#996: מתג הדלקה/כיבוי לכל פורט — ‏`PUT /api/console/ports/{id}` ומה
ש-`/ports` מחזיר לצידו (`enabled`/`bind`/`toggle`/`off_means`).

אותו דפוס hooks מוזרק כמו test_console_ports.py: ‏`ss`/`ss_tcp` מזויפים,
ומנהל המאזינים מזויף — רושם מה נתבקש ומחזיר "מאזין" רק למה שפתוח. ככה
"‏`ss` לא מראה מאזין אחרי כיבוי" נמדד באותו מקום שהמתג נגע בו. הסוקט
האמיתי נבדק ב-test_port_listeners.py.

עיקרון 5 — שלושה מצבים, לא שניים: "כבוי" (המתג) ≠ "לא מאזין" (נמדד ב-ss)
‏≠ "לא נקרא" (ss חסר). כל אחד מהם צבע משלו, ואף אחד אינו ירוק.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.ssh_switch import Listeners as SshListeners  # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_HEAD = "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
SS_UDP = SS_HEAD + (
    'UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))\n'
    'UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))\n')

PORTS = {"http_boot": 8080, "http_console": 8081, "kiosk": 8082, "interserver": 8443}
HOSTS = {"http_boot": ["0.0.0.0"], "http_console": ["10.44.0.1", "127.0.0.1"],
         "kiosk": ["0.0.0.0"], "interserver": ["10.30.0.8"]}


class FakeListeners:
    """המנהל המזויף: `set_open`/`request` רושמים, `is_open`/`bound` עונים."""

    def __init__(self, ids=("http_boot", "http_console", "kiosk")):
        self.open = set(ids)
        self.calls: list[tuple[str, bool]] = []
        self.fail_open: set[str] = set()
        self.ids_ = list(ids)

    def ids(self):
        return list(self.ids_)

    def spec(self, port_id):
        return {"port": PORTS[port_id], "hosts": HOSTS[port_id]}

    def is_open(self, port_id):
        return port_id in self.open

    def bound(self, port_id):
        if port_id not in self.open:
            return []
        return [(h, PORTS[port_id]) for h in HOSTS[port_id]]

    def request(self, port_id, want, timeout=None):
        from server.ports import ListenerError
        self.calls.append((port_id, want))
        if want and port_id in self.fail_open:
            raise ListenerError(f"{port_id}: address already in use")
        (self.open.add if want else self.open.discard)(port_id)

    def ss_tcp(self) -> str:
        lines = [SS_HEAD]
        for pid in sorted(self.open):
            for host in HOSTS[pid]:
                lines.append(f'LISTEN 0      2048         {host}:{PORTS[pid]}   '
                             f'0.0.0.0:*     users:(("python3",pid=900,fd=7))\n')
        return "".join(lines)


def _build(tmp_path: Path, images_root: Path, clock, listeners, *, base_port=80):
    from server import users
    from server.app import create_app

    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)
    fake = {"ss": SS_UDP, "http": 200,
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa",
                            "addresses": ["10.44.0.1/24"]}],
            "listeners": SshListeners(True),
            "menu": "linux /boot/vmlinuz ip=dhcp imagectl.server=x console=tty0",
            "udp_sender_pids": [], "ss_tcp": None,
            "nft_ruleset": None}
    hooks = {
        "ss": lambda: fake["ss"],
        "ss_tcp": lambda: (fake["ss_tcp"] if fake["ss_tcp"] is not None
                           else (listeners.ss_tcp() if listeners else SS_HEAD)),
        "unit_active": lambda name: "active",
        "http_get": lambda url: fake["http"],
        "http_size": lambda url: (200, 31_000_000),
        "http_text": lambda url: (200, fake["menu"]),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "listeners": lambda: fake["listeners"],
        "apply_sshd": lambda text: pytest.fail("בדיקה נגעה ב-sshd אמיתי"),
        "settle": lambda: None,
        "udp_sender_pids": lambda: fake["udp_sender_pids"],
        # ‏#996: מנהל המאזינים מוזרק כמו כל השאר — None = אין מנהל בתהליך
        # הזה (כמו ב-create_app של הבדיקות), והמתג חייב לומר זאת.
        "port_listeners": listeners,
        "nft_ruleset": lambda: fake["nft_ruleset"],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    base = f"http://testserver:{base_port}"
    admin, deploy = TestClient(app, base_url=base), TestClient(app, base_url=base)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "conn": conn,
            "listeners": listeners, "app": app}


@pytest.fixture()
def toggles(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    return _build(tmp_path, images_root, clock, FakeListeners())


def by_id(rows):
    return {r["id"]: r for r in rows}


def rows_of(t):
    resp = t["admin"].get("/api/console/ports")
    assert resp.status_code == 200
    return by_id(resp.json())


def server_name(t) -> str:
    return t["admin"].get("/api/console/me").json()["server_name"]


# --- מה ש-/ports מחזיר עכשיו --------------------------------------------------


def test_every_row_carries_the_switch_fields(toggles):
    rows = rows_of(toggles)
    for row in rows.values():
        for key in ("enabled", "bind", "toggle", "off_means", "toggle_url",
                    "confirm_word", "listening"):
            assert key in row, f"{row['id']} is missing {key}"
        assert row["toggle"] in ("api", "confirm", "none")
        assert row["off_means"]


def test_the_missing_rows_are_there_now(toggles):
    """‏DHCP 67, בין-שרתים 8443 ו-SSH לשרת × כרטיס — חסרו ב-/ports."""
    rows = rows_of(toggles)
    assert rows["dhcp"]["port"] == "67" and rows["dhcp"]["proto"] == "udp"
    assert rows["interserver"]["port"] == "8443"
    assert rows["ssh_server:eth0"]["port"] == "22"
    assert rows["ssh_server:eth0"]["toggle_url"] == "/api/console/ssh/interfaces/eth0"
    assert rows["ssh_server:eth0"]["confirm_word"] == "eth0"


def test_bind_comes_from_the_socket_table_not_from_configuration(toggles):
    rows = rows_of(toggles)
    assert rows["http_console"]["bind"] == ["10.44.0.1:8081", "127.0.0.1:8081"]
    assert rows["http_console"]["listening"] is True
    assert rows["http_console"]["state"] == "ok"
    assert rows["tftp"]["bind"] == ["0.0.0.0:69"]
    assert rows["dhcp"]["bind"] == ["0.0.0.0:67"]


def test_our_ports_point_at_the_ports_api_and_say_who_needs_the_name(toggles):
    rows = rows_of(toggles)
    name = server_name(toggles)
    for pid in ("http_boot", "http_console"):
        assert rows[pid]["toggle"] == "confirm"
        assert rows[pid]["confirm_word"] == name
        assert rows[pid]["toggle_url"] == f"/api/console/ports/{pid}"
    assert rows["kiosk"]["toggle"] == "api"
    assert rows["kiosk"]["toggle_url"] == "/api/console/ports/kiosk"
    # למי שיש כבר מתג במקום אחר — מצביעים לשם, לא משכפלים.
    assert rows["dhcp"]["toggle_url"] == "/api/console/net/interfaces/{name}"
    assert rows["dhcp"]["toggle"] == "confirm"
    assert rows["pxe_proxy"]["toggle_url"] == "/api/console/net/interfaces/{name}"
    assert rows["monitor"]["toggle_url"] == "/api/console/monitor/settings"
    assert rows["monitor"]["confirm_word"] == "imagectl.monitor"
    assert rows["ssh_stations"]["toggle_url"] == "/api/console/ssh/stations"
    assert rows["ssh_stations"]["confirm_word"] == "imagectl.debug"
    # ולמי שאין מתג — אומרים למה.
    assert rows["tftp"]["toggle"] == "none" and rows["multicast"]["toggle"] == "none"
    assert rows["tftp"]["enabled"] is None


# --- שלושה מצבים, שלושה צבעים -------------------------------------------------


def test_enabled_but_nobody_listens_is_red_not_off(toggles):
    toggles["fake"]["ss_tcp"] = SS_HEAD           # הטבלה נקראה, ריקה
    rows = rows_of(toggles)
    assert rows["kiosk"]["enabled"] is True
    assert rows["kiosk"]["listening"] is False
    assert rows["kiosk"]["state"] == "bad"


def test_disabled_but_still_listening_is_red(toggles):
    from server import ports
    ports.set_enabled(toggles["conn"], "kiosk", False)
    rows = rows_of(toggles)                        # ה-fake עדיין "מאזין"
    assert rows["kiosk"]["enabled"] is False and rows["kiosk"]["listening"] is True
    assert rows["kiosk"]["state"] == "bad"


def test_socket_table_unreadable_is_unknown_never_green(toggles):
    toggles["fake"]["ss_tcp"] = ""                 # ss לא רץ
    rows = rows_of(toggles)
    for pid in ("http_boot", "http_console", "kiosk"):
        assert rows[pid]["listening"] is None
        assert rows[pid]["state"] == "unknown"
        assert "לא נקרא" in rows[pid]["detail"]


# --- המתג ---------------------------------------------------------------------


def test_kiosk_off_closes_the_listener_and_reads_back(toggles):
    """הגדרת ה"גמור" של #996: כיבוי 8082 → ‏ss (מוזרק) לא מראה מאזין +
    ‏`/ports.enabled=false`; הדלקה → חוזר."""
    resp = toggles["admin"].put("/api/console/ports/kiosk", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["applied"] is True
    assert body["port"]["enabled"] is False and body["port"]["listening"] is False
    assert toggles["listeners"].calls == [("kiosk", False)]
    rows = rows_of(toggles)
    assert rows["kiosk"]["enabled"] is False
    assert rows["kiosk"]["bind"] == [] and rows["kiosk"]["listening"] is False
    assert rows["kiosk"]["state"] == "off"
    assert "מפעיל" in rows["kiosk"]["detail"]

    resp = toggles["admin"].put("/api/console/ports/kiosk", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    rows = rows_of(toggles)
    assert rows["kiosk"]["enabled"] is True and rows["kiosk"]["listening"] is True
    assert rows["kiosk"]["state"] == "ok"


def test_the_state_is_saved_so_it_survives_a_restart(toggles):
    from server import ports
    assert ports.enabled(toggles["conn"], "kiosk") is True
    toggles["admin"].put("/api/console/ports/kiosk", json={"enabled": False})
    assert ports.enabled(toggles["conn"], "kiosk") is False
    journal = [r["event"] for r in toggles["conn"].execute(
        "SELECT event FROM journal ORDER BY id DESC LIMIT 3").fetchall()]
    assert "port_toggle" in journal


def test_http_boot_off_needs_the_server_name(toggles):
    """‏8080 כבוי = אין PXE ואין סוכנים — הרסני, מאחורי הקלדת שם (עיקרון 7)."""
    put = lambda body: toggles["admin"].put("/api/console/ports/http_boot", json=body)  # noqa: E731
    assert put({"enabled": False}).status_code == 400
    assert put({"enabled": False, "confirm": "wrong"}).status_code == 400
    assert toggles["listeners"].calls == []
    resp = put({"enabled": False, "confirm": server_name(toggles)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["port"]["off_means"]
    assert toggles["listeners"].calls == [("http_boot", False)]
    # הדלקה חזרה היא הכיוון אל ברירת המחדל — בלי הקלדה.
    assert put({"enabled": True}).status_code == 200


def test_a_disabled_boot_port_is_off_by_operator_in_health_not_a_warning(toggles):
    toggles["admin"].put("/api/console/ports/http_boot",
                         json={"enabled": False, "confirm": server_name(toggles)})
    toggles["fake"]["http"] = None                 # ואכן לא עונה
    checks = {c["id"]: c for c in toggles["admin"].get("/api/console/health").json()}
    assert checks["server"]["state"] == "off"
    assert "מפעיל" in checks["server"]["detail"]
    rows = rows_of(toggles)
    assert rows["http_boot"]["state"] == "off"


def test_the_console_cannot_be_closed_through_itself(tmp_path, images_root, clock):
    """"הדלת האחרונה" — בקשה שמגיעה דרך 8081 לסגור את 8081 מסורבת בשמו."""
    t = _build(tmp_path, images_root, clock, FakeListeners(), base_port=8081)
    resp = t["admin"].put("/api/console/ports/http_console",
                          json={"enabled": False, "confirm": server_name(t)})
    assert resp.status_code == 409, resp.text
    assert "8081" in resp.json()["detail"]
    assert t["listeners"].calls == []
    assert t["listeners"].is_open("http_console")


def test_kiosk_needs_no_name(toggles):
    assert toggles["admin"].put("/api/console/ports/kiosk",
                                json={"enabled": False}).status_code == 200


def test_deploy_user_gets_403_and_the_listener_is_untouched(toggles):
    resp = toggles["deploy"].put("/api/console/ports/kiosk", json={"enabled": False})
    assert resp.status_code == 403
    assert toggles["listeners"].calls == []


def test_unknown_port_is_404(toggles):
    assert toggles["admin"].put("/api/console/ports/nope",
                                json={"enabled": False}).status_code == 404


def test_rows_toggled_elsewhere_refuse_here_and_point_there(toggles):
    for pid, url in (("dhcp", "/api/console/net/interfaces/{name}"),
                     ("monitor", "/api/console/monitor/settings"),
                     ("ssh_stations", "/api/console/ssh/stations")):
        resp = toggles["admin"].put(f"/api/console/ports/{pid}", json={"enabled": False})
        assert resp.status_code == 409, pid
        assert url in resp.json()["detail"]
    resp = toggles["admin"].put("/api/console/ports/tftp", json={"enabled": False})
    assert resp.status_code == 409
    assert toggles["listeners"].calls == []


def test_open_that_fails_to_bind_is_reported_not_faked(toggles):
    """‏DB אומר "דלוק", הפורט תפוס על ידי אחר: ‏ok=false עם הסיבה, המצב
    השמור נשאר "דלוק" (זו הבקשה), ו-/ports מראה את הפער באדום."""
    toggles["admin"].put("/api/console/ports/kiosk", json={"enabled": False})
    toggles["listeners"].fail_open.add("kiosk")
    resp = toggles["admin"].put("/api/console/ports/kiosk", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False and body["applied"] is False
    assert "already in use" in body["detail"]
    rows = rows_of(toggles)
    assert rows["kiosk"]["enabled"] is True and rows["kiosk"]["listening"] is False
    assert rows["kiosk"]["state"] == "bad"


def test_without_a_listener_manager_the_switch_says_it_did_not_apply(
        tmp_path, images_root, clock):
    """‏create_app בבדיקות אינו מריץ מאזינים — המתג נשמר, ואומר במפורש
    שלא הוחל עכשיו. לא "ok" על מה שלא קרה (עיקרון 5)."""
    t = _build(tmp_path, images_root, clock, None)
    resp = t["admin"].put("/api/console/ports/kiosk", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False and body["applied"] is False
    assert body["port"]["enabled"] is False
    from server import ports
    assert ports.enabled(t["conn"], "kiosk") is False


def test_interserver_without_a_listener_has_no_switch(tmp_path, images_root, clock):
    """‏8443 עולה רק על משני עם --interserver-host; בלעדיו השורה קיימת,
    אומרת שאינה מוגדרת, ואין לה מתג."""
    t = _build(tmp_path, images_root, clock, FakeListeners())
    rows = rows_of(t)
    assert rows["interserver"]["toggle"] == "none"
    assert rows["interserver"]["state"] == "off"
    t2 = _build(tmp_path / "b", images_root, clock,
                FakeListeners(("http_boot", "http_console", "kiosk", "interserver")))
    rows = rows_of(t2)
    assert rows["interserver"]["toggle"] == "api"
    assert rows["interserver"]["bind"] == ["10.30.0.8:8443"]
    assert rows["interserver"]["state"] == "ok"
