"""‏#1088: כרטיס וילן השרתים = לקוח DHCP; הקונסולה נקשרת לפי **שם כרטיס**
ומאזינה מחדש בהחלפת כתובת; רשת ההפצה מוגדרת **מהקונסולה** ולא במתקין.

ארבע קבוצות, כל אחת עם הראיה שלה:

1. ‏`ifaddr` — שם כרטיס → כתובת (שלושה מצבים), הדוגם, וקובץ החכירות.
2. ‏`ports.Listeners.rebind` — על סוקט אמיתי (loopback), כמו #996.
3. ‏`main` — ‏`--console-host eth0` מגיע למאזין הקונסולה כ**כתובת**; בלי
   `--server-url` השרת עולה במצב "לא הוגדרה" או מהרשומה בקונסולה.
4. ‏`deploy_net` + הקונסולה — הדלקת ה-DHCP הראשונה משלימה את ההתקנה, ורק
   כשהכרטיס נושא את הכתובת; ‏health, המתקין, היחידה, ו-pairing לפי שם.
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

import server.app
import server.main
from server import deploy_net, dhcp, health, ifaddr, netcfg, netcfg_host, ports
from server.ssh_switch import Listeners as SshListeners

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "install" / "setup-boot-server.sh"
UNIT = REPO / "install" / "imagectl-server.service"


# --- 1. ifaddr ------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("eth0", True), ("ens18", True), ("eth1.700", True),
    ("10.44.12.10", False), ("127.0.0.1", False), ("::1", False),
    ("0.0.0.0", False), ("localhost", False), ("", False), (None, False),
    ("eth 0", False), ("eth0\ninterface=eth1", False),
])
def test_is_interface_name(value, expected):
    assert ifaddr.is_interface_name(value) is expected


def _ip_json(name: str, *addrs: str) -> str:
    return json.dumps([{"ifname": name, "addr_info": [
        {"family": "inet", "local": a} for a in addrs]}])


def test_read_ipv4_three_states():
    """כתובת · אין כתובת (None) · הבדיקה נכשלה (חריגה) — לא מקופלים."""
    assert ifaddr.read_ipv4("eth0", run=lambda a: _ip_json("eth0", "10.1.2.3")) == "10.1.2.3"
    assert ifaddr.read_ipv4("eth0", run=lambda a: _ip_json("eth0")) is None

    def broken(args):
        raise ifaddr.AddressLookupError("Device \"eth0\" does not exist")
    with pytest.raises(ifaddr.AddressLookupError):
        ifaddr.read_ipv4("eth0", run=broken)
    with pytest.raises(ifaddr.AddressLookupError):
        ifaddr.read_ipv4("eth0", run=lambda a: "not json")


def test_read_ipv4_asks_ip_for_that_device_only():
    seen = []
    ifaddr.read_ipv4("ens18", run=lambda a: seen.append(a) or _ip_json("ens18", "10.9.9.9"))
    assert seen == [["-4", "addr", "show", "dev", "ens18"]]


def test_watcher_reports_changes_including_first_lease_and_retries_failed_rebind():
    state = {"addr": None}
    changes = []
    ok = {"value": True}

    def on_change(old, new):
        changes.append((old, new))
        return ok["value"]

    w = ifaddr.AddressWatcher("eth0", current=None, on_change=on_change,
                              lookup=lambda name: state["addr"])
    assert w.check_once() is False and changes == []          # None → None: אין שינוי
    state["addr"] = "10.1.2.3"
    assert w.check_once() is True and changes == [(None, "10.1.2.3")]
    assert w.check_once() is False                            # יציב — לא מדווח שוב
    # rebind שנכשל: הכתובת הידועה נשארת הישנה, והדגימה הבאה מנסה שוב.
    ok["value"] = False
    state["addr"] = "10.1.2.9"
    assert w.check_once() is False and w.current == "10.1.2.3"
    ok["value"] = True
    assert w.check_once() is True and changes[-1] == ("10.1.2.3", "10.1.2.9")


def test_watcher_lookup_failure_is_not_an_address_change():
    """‏`ip` שנפל פעם אחת אינו "אין כתובת" — המאזין נשאר על הכתובת."""
    errors, changes = [], []

    def failing(name):
        raise ifaddr.AddressLookupError("boom")
    w = ifaddr.AddressWatcher("eth0", current="10.1.2.3",
                              on_change=lambda o, n: changes.append((o, n)),
                              lookup=failing, on_error=errors.append)
    assert w.check_once() is False
    assert changes == [] and errors == ["boom"] and w.current == "10.1.2.3"


LEASES = """
lease {
  interface "eth0";
  fixed-address 10.1.2.3;
  option dhcp-server-identifier 10.1.0.1;
  option host-name "imagectl";
  renew 4 2026/09/18 06:00:00;
  expire 4 2026/09/18 12:00:00;
}
lease {
  interface "eth0";
  fixed-address 10.1.2.9;
  option dhcp-server-identifier 10.1.0.1;
  expire 4 2026/09/18 18:00:00;
}
"""


def test_dhclient_leases_take_the_last_lease():
    lease = ifaddr.parse_dhclient_leases(LEASES)
    assert lease.address == "10.1.2.9" and lease.server == "10.1.0.1"
    assert lease.expires.year == 2026 and lease.expires.hour == 18
    assert ifaddr.parse_dhclient_leases("") is None
    never = ifaddr.parse_dhclient_leases("lease { fixed-address 1.2.3.4; expire never; }")
    assert never.expires is None and never.expires_text() == "ללא תוקף"


def test_dhclient_lease_reads_the_interface_file(tmp_path):
    (tmp_path / "dhclient.eth0.leases").write_text(LEASES, encoding="utf-8")
    assert ifaddr.dhclient_lease("eth0", tmp_path).address == "10.1.2.9"
    assert ifaddr.dhclient_lease("eth1", tmp_path) is None


def test_servers_nic_status_three_states():
    lease = ifaddr.parse_dhclient_leases(LEASES)
    assert ifaddr.servers_nic_status("eth0", None, None, lookup_failed="ip נפל")[0] == "unknown"
    assert ifaddr.servers_nic_status("eth0", None, None)[0] == "warn"
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.9", lease, hostname="imagectl")
    assert state == "ok" and "DHCP" in detail and "פג ב-" in detail and "imagectl" in detail
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.9", None)
    assert state == "ok" and "סטטי" in detail
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.3", lease)
    assert state == "warn" and "10.1.2.9" in detail


# --- 2. Listeners.rebind — סוקט אמיתי --------------------------------------------


async def _app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _can_bind(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def _can_connect(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
        except OSError:
            return False
        return True


def _factories(host: str, port: int):
    return [lambda: uvicorn.Server(uvicorn.Config(
        _app, host=host, port=port, log_level="warning", lifespan="off"))]


async def _wait(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


@pytest.fixture()
def second_loopback():
    if not _can_bind("127.0.0.2", 0):
        pytest.skip("127.0.0.2 אינו ניתן לקשירה במכונה הזו")
    return "127.0.0.2"


def test_rebind_moves_the_listener_to_the_new_address(second_loopback):
    """‏#1088: אותו פורט, כתובת אחרת — הישנה משוחררת (bind מצליח), החדשה
    עונה (connect מצליח). ראיה בסוקט, לא בהגדרה."""
    port = _free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_console", _factories("127.0.0.1", port), port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve())
        await _wait(lambda: mgr.is_open("http_console"))
        assert _can_connect("127.0.0.1", port)

        await mgr.rebind("http_console", hosts=[second_loopback],
                         factories=_factories(second_loopback, port))
        await _wait(lambda: mgr.is_open("http_console"))
        assert _can_bind("127.0.0.1", port), "הכתובת הישנה עדיין תפוסה"
        assert _can_connect(second_loopback, port)
        assert mgr.spec("http_console")["hosts"] == [second_loopback]
        assert [b[0] for b in mgr.bound("http_console")] == [second_loopback]
        mgr.stop()
        await task

    asyncio.run(scenario())


def test_rebind_does_not_open_a_listener_the_operator_closed(second_loopback):
    port = _free_port()

    async def scenario():
        mgr = ports.Listeners(want_open=lambda p: False)
        mgr.add("http_console", _factories("127.0.0.1", port), port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve())
        await asyncio.sleep(0.1)
        assert not mgr.is_open("http_console")
        await mgr.rebind("http_console", hosts=[second_loopback],
                         factories=_factories(second_loopback, port))
        await asyncio.sleep(0.1)
        assert not mgr.is_open("http_console")
        assert _can_bind(second_loopback, port)
        mgr.stop()
        await task

    asyncio.run(scenario())


# --- 3. main ---------------------------------------------------------------------


def test_console_host_by_name_resolves_and_watches(tmp_path, monkeypatch):
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, run=None: "10.1.2.3")
    configs: dict[str, list[dict]] = {}
    captured: dict = {}
    started = []
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: (captured.update(kwargs=k), "rt")[1])
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_kiosk_app", lambda rt: "kiosk")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr(ifaddr.AddressWatcher, "start", lambda self: started.append(self) or self)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "eth0"])
    server.main.main()

    console_hosts = sorted(c["host"] for c in configs["console"])
    assert console_hosts == ["10.1.2.3", "127.0.0.1"]          # הכתובת, לא "eth0"
    assert all("ssl_certfile" in c for c in configs["console"])  # TLS דלוק על כרטיס
    assert [c["host"] for c in configs["agent"]] == ["10.44.12.10"]
    assert [w.name for w in started] == ["eth0"] and started[0].current == "10.1.2.3"
    # ‏SAN: הכתובת הנוכחית + שם המארח, לא שם הכרטיס.
    tls = captured["kwargs"]["console_tls"]
    assert "10.1.2.3" in tls.sans and "eth0" not in tls.sans
    assert socket.gethostname() in tls.sans
    # ‏health מקבל את שורת כתובת השרתים.
    assert callable(captured["kwargs"]["health_hooks"]["servers_nic"])


def test_console_host_by_name_without_a_lease_yet_is_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, run=None: None)
    configs = {}
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: "rt")
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_kiosk_app", lambda rt: "kiosk")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    started = []
    monkeypatch.setattr(ifaddr.AddressWatcher, "start", lambda self: started.append(self) or self)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "ens18"])
    server.main.main()
    assert [c["host"] for c in configs["console"]] == ["127.0.0.1"]
    assert started and started[0].current is None                 # None→כתובת ייחשב שינוי


def test_console_host_by_name_when_ip_fails_is_a_parser_error(tmp_path, monkeypatch, capsys):
    def broken(name, run=None):
        raise ifaddr.AddressLookupError("Device \"eth9\" does not exist")
    monkeypatch.setattr(ifaddr, "read_ipv4", broken)
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: pytest.fail("הגיע ל-runtime"))
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "eth9"])
    with pytest.raises(SystemExit) as exc:
        server.main.main()
    assert exc.value.code == 2
    assert "eth9" in capsys.readouterr().err


def _run_main_no_url(tmp_path, monkeypatch, interface_for=None):
    configs: dict = {}
    captured: dict = {}
    monkeypatch.setattr(server.app, "create_runtime",
                        lambda *a, **k: (captured.update(args=a, kwargs=k), "rt")[1])
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_kiosk_app", lambda rt: "kiosk")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", interface_for or (lambda url: None))
    monkeypatch.setattr(server.main, "_address_for_interface", lambda name: "10.44.9.10")
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "", "--console-tls", "off",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    server.main.main()
    return configs, captured


def test_without_server_url_and_without_a_console_record_the_server_is_unconfigured(
        tmp_path, monkeypatch, capsys):
    """התקנה נקייה (#1088): היחידה מעבירה ${IMAGECTL_URL} ריק. השרת עולה —
    סוכן וקיוסק על loopback בלבד, ומצב ההפצה נאמר: source=none."""
    configs, captured = _run_main_no_url(tmp_path, monkeypatch)
    assert captured["args"][2] == "http://127.0.0.1:8080"
    assert [c["host"] for c in configs["agent"]] == ["127.0.0.1"]
    assert [c["host"] for c in configs["kiosk"]] == ["127.0.0.1"]
    assert captured["kwargs"]["interface"] is None
    deploy = captured["kwargs"]["deploy"]
    assert deploy.state.source == "none" and not deploy.state.configured
    assert "not configured" in capsys.readouterr().out


def test_without_server_url_the_console_record_wins(tmp_path, monkeypatch):
    """מה שהקונסולה רשמה בהדלקת ה-DHCP הראשונה הוא הכתובת בעלייה הבאה."""
    from server.db import connect
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagectl.db")
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    conn.close()
    configs, captured = _run_main_no_url(tmp_path, monkeypatch,
                                         interface_for=lambda url: "eth1")
    assert captured["args"][2] == "http://10.44.9.10:8080"
    assert captured["kwargs"]["interface"] == "eth1"
    assert [c["host"] for c in configs["agent"]] == ["10.44.9.10"]
    assert captured["kwargs"]["deploy"].state.source == "console"


def test_a_console_record_whose_address_left_the_nic_degrades_instead_of_dying(
        tmp_path, monkeypatch, capsys):
    """הכרטיס כבר אינו נושא את הכתובת — נפילה הייתה משאירה את המנהל בלי
    קונסולה; השרת עולה במצב none ואומר למה."""
    from server.db import connect
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagectl.db")
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    conn.close()

    def no_nic(url):
        raise server.main.InterfaceDetectionError("אף כרטיס אינו נושא את 10.44.9.10")
    configs, captured = _run_main_no_url(tmp_path, monkeypatch, interface_for=no_nic)
    assert captured["args"][2] == "http://127.0.0.1:8080"
    assert captured["kwargs"]["deploy"].state.source == "none"
    assert "back to 'not configured'" in capsys.readouterr().out


def test_server_url_flag_still_wins_and_is_still_ip_only(tmp_path, monkeypatch):
    """‏#498 לא נשבר: עם הדגל — אותה בדיקה, אותה דחייה."""
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: pytest.fail("הגיע ל-runtime"))
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://imagectl.college:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    with pytest.raises(SystemExit):
        server.main.main()


# --- 4. deploy_net + הקונסולה -------------------------------------------------------


def test_resolve_precedence(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    assert deploy_net.resolve(conn, "http://1.2.3.4:8080", "eth0").source == "cli"
    assert deploy_net.resolve(conn, None, None).source == "none"
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    st = deploy_net.resolve(conn, None, None)
    assert (st.source, st.interface, st.url) == ("console", "eth1", "http://10.44.9.10:8080")
    assert deploy_net.resolve(conn, "http://1.2.3.4:8080", None).source == "cli"   # הדגל גובר
    assert st.public()["hint"] is None
    assert "בחר כרטיס" in deploy_net.DeployState("none", None, None).public()["hint"]


def test_write_grub_cfg_uses_the_installers_generator(tmp_path):
    from boot.grub_menu import GrubConfig, render_bootstrap
    assert deploy_net.write_grub_cfg("http://10.44.9.10:8080", tmp_path) is None
    text = (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii")
    assert text == render_bootstrap(GrubConfig(server_base="http://10.44.9.10:8080"))
    assert "set imagectl_fallback=10.44.9.10:8080" in text


def test_regenerate_firewall_skips_when_the_installer_wrote_none(tmp_path):
    assert deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0",
                                          conf=tmp_path / "nftables.conf") is None
    assert not (tmp_path / "nftables.conf").exists()


def test_complete_records_then_runs_every_step_and_names_failures(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    calls = []
    hooks = {
        "write_grub_cfg": lambda url, root: calls.append(("grub", url, root)) or None,
        "enable_dnsmasq": lambda: calls.append(("dnsmasq",)) or None,
        "firewall": lambda repo, deploy_if, servers_if, primary_ip: calls.append(
            ("fw", deploy_if, servers_if, primary_ip)) or "nft נפל",
        "restart_server": lambda: calls.append(("restart",)) or None,
    }
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=REPO, servers_interface="eth0",
        primary_ip=None, hooks=hooks)
    result = deploy_net.complete(ctx, conn, interface="eth1", server_ip="10.44.9.10")
    assert deploy_net.stored(conn) == ("eth1", "http://10.44.9.10:8080")
    assert [c[0] for c in calls] == ["grub", "dnsmasq", "fw", "restart"]
    assert calls[2] == ("fw", "eth1", "eth0", None)
    assert result["ok"] is False and result["errors"] == ["חומת אש: nft נפל"]
    assert result["restarting"] is True and result["url"] == "http://10.44.9.10:8080"


def test_render_adds_the_deploy_interface_line_when_set_from_the_console():
    """‏R20-F1 במסירה: imagectl.conf של המתקין בלי interface= — השורה
    נכתבת בקובץ של הקונסולה, גם בלי אף DHCP דלוק, ולא פעמיים."""
    text = dhcp.render([], deploy_interface="eth1")
    assert "bind-interfaces" in text and "interface=eth1" in text
    good = dhcp.InterfaceConfig("eth1", enabled=True, range_start="10.44.9.50",
                                range_end="10.44.9.200", server_ip="10.44.9.10")
    text = dhcp.render([good], deploy_interface="eth1")
    assert text.count("interface=eth1") == 1 and text.count("bind-interfaces") == 1
    assert "interface=" not in dhcp.render([])                 # בלי הפצה — כמו קודם
    with pytest.raises(ValueError):
        dhcp.render([], deploy_interface="eth1\ninterface=eth0")


GOOD = dict(
    enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
    netmask="255.255.255.0", gateway="10.44.9.1", dns=["10.44.0.5"],
    lease="12h", server_ip="10.44.9.10",
)


@pytest.fixture()
def console(tmp_path: Path, images_root: Path, clock):
    """שרת עם שני כרטיסים; eth1 נושא את 10.44.9.10, eth0 בלי הכתובת הזו."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    fake = {
        "interfaces": [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00", "addresses": ["10.1.2.3/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01", "addresses": ["10.44.9.10/24"]},
        ],
        "applied": [], "deploy_calls": [],
    }
    hooks = {
        "interfaces": lambda: fake["interfaces"],
        "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: fake["applied"].append(text) or None,
        "apply_proxy": lambda text, active: None,
        "read_active_conf": lambda: "", "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    deploy_hooks = {
        "write_grub_cfg": lambda url, root: fake["deploy_calls"].append(("grub", url)) or None,
        "enable_dnsmasq": lambda: fake["deploy_calls"].append(("dnsmasq",)) or None,
        "firewall": lambda repo, **kw: fake["deploy_calls"].append(("fw", kw)) or None,
        "restart_server": lambda: fake["deploy_calls"].append(("restart",)) or None,
    }

    def build(source: str):
        state = (deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080") if source == "cli"
                 else deploy_net.DeployState("none", None, None))
        ctx = deploy_net.DeployContext(state=state, agent_port=8080, tftp_root=tmp_path / "tftp",
                                       repo_dir=REPO, servers_interface="eth0", hooks=deploy_hooks)
        app = create_app(tmp_path / f"data-{source}", images_root, "http://127.0.0.1:8080",
                         now_fn=clock, dhcp_hooks=hooks, deploy=ctx)
        users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
        client = TestClient(app)
        client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
        return client, app.state.ctx, ctx
    return build, fake


def test_first_dhcp_enable_completes_the_install_and_restarts(console):
    build, fake = console
    client, ctx, deploy_ctx = build("none")
    assert client.get("/api/console/net/deploy").json()["configured"] is False

    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deploy"] == {"ok": True, "url": "http://10.44.9.10:8080", "interface": "eth1",
                              "errors": [], "restarting": True}
    assert deploy_net.stored(ctx.conn) == ("eth1", "http://10.44.9.10:8080")
    assert [c[0] for c in fake["deploy_calls"]] == ["grub", "dnsmasq", "fw", "restart"]
    assert fake["deploy_calls"][2][1] == {"deploy_if": "eth1", "servers_if": "eth0", "primary_ip": None}
    # קובץ ה-dnsmasq שנכתב נושא את כרטיס ההפצה (R20-F1 במסירה).
    assert "interface=eth1" in fake["applied"][-1]
    assert len(fake["applied"]) == 1                       # כתיבה אחת, לא שתיים
    assert client.get("/api/console/net/deploy").json()["source"] == "console"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "deploy_net_set" in events


def test_first_dhcp_enable_refuses_a_nic_that_does_not_carry_the_address(console):
    """הסדר של נדב: כתובת → DHCP. בלי הכתובת על הכרטיס — 409, ושום דבר
    לא נרשם (לא deploy ולא dhcp:eth0), כי אחרת השרת לא היה עולה."""
    build, fake = console
    client, ctx, _ = build("none")
    r = client.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409
    assert "10.44.9.10" in r.json()["detail"] and "10.1.2.3" in r.json()["detail"]
    assert deploy_net.stored(ctx.conn) == (None, None)
    from server.db import get_setting
    assert get_setting(ctx.conn, dhcp.SETTING_PREFIX + "eth0") is None
    assert fake["deploy_calls"] == [] and fake["applied"] == []


def test_dhcp_enable_with_a_cli_address_does_not_touch_the_install(console):
    build, fake = console
    client, ctx, _ = build("cli")
    assert client.get("/api/console/net/deploy").json() == {
        "configured": True, "source": "cli", "interface": "eth1",
        "url": "http://10.44.9.10:8080", "hint": None}
    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 200 and "deploy" not in r.json()
    assert fake["deploy_calls"] == [] and deploy_net.stored(ctx.conn) == (None, None)
    # עם הדגל — imagectl.conf של המתקין נושא את interface=; כאן לא מכפילים.
    assert "Deploy interface, set from the console" not in fake["applied"][-1]


# --- health -----------------------------------------------------------------------


def _health_hooks(deploy_state, servers_nic=None, tftp_root=None):
    hooks = health.default_hooks()
    hooks.update({
        "ss": lambda: "State Recv-Q Send-Q Local Address:Port Peer\n",
        "ss_tcp": lambda: "",
        "unit_active": lambda name: "inactive",
        "http_get": lambda url: None, "http_size": lambda url: (None, None),
        "http_text": lambda url: (None, ""),
        "interfaces": lambda: [{"name": "eth0", "state": "up"}],
        "tftp_root": lambda: tftp_root or Path("/nonexistent-tftp"),
        "udp_sender_pids": lambda: [], "nft_ruleset": lambda: None,
        "shim_src": lambda: "/nonexistent-shim",
        # שתי דלתות ה-SSH (#83) — מוזרקות, כדי לא לקרוא /proc של המכונה.
        "listeners": lambda: SshListeners(True),
        "apply_sshd": lambda text: pytest.fail("בדיקה נגעה ב-sshd אמיתי"),
        "settle": lambda: None,
        "deploy": (lambda: deploy_state) if deploy_state is not None else None,
        "servers_nic": (lambda: servers_nic) if servers_nic is not None else None,
    })
    return hooks


@pytest.fixture()
def health_ctx(tmp_path, images_root, clock):
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock)
    return app.state.ctx


def test_health_says_deploy_not_configured_instead_of_red_pxe_rows(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(deploy_net.DeployState("none", None, None)),
        "http://127.0.0.1:8080")}
    assert rows["deploy_net"]["state"] == "warn" and "בחר כרטיס" in rows["deploy_net"]["detail"]
    assert rows["tftp_port"]["state"] == "off"
    assert rows["dnsmasq"]["state"] == "off"
    assert rows["server"]["state"] == "off" and "127.0.0.1" in rows["server"]["detail"]
    assert "grub/grub.cfg" not in rows["boot_files"]["detail"]


def test_health_keeps_the_red_rows_when_deploy_is_configured(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080")),
        "http://10.44.9.10:8080")}
    assert "deploy_net" not in rows
    assert rows["tftp_port"]["state"] == "bad"
    assert rows["dnsmasq"]["state"] == "bad"
    assert rows["server"]["state"] == "bad"
    assert "grub/grub.cfg" in rows["boot_files"]["detail"]


def test_health_servers_nic_row_only_when_injected(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(None, ("ok", "כתובת השרתים (eth0): 10.1.2.3 (DHCP, פג ב-18/09 12:00)")),
        "http://10.44.9.10:8080")}
    assert rows["servers_nic"]["state"] == "ok" and "DHCP" in rows["servers_nic"]["detail"]
    rows = {c["id"] for c in health.collect(health_ctx, _health_hooks(None), "http://10.44.9.10:8080")}
    assert "servers_nic" not in rows and "deploy_net" not in rows


# --- netcfg: hostname ב-DHCP, וקליטת הכרטיס מהקובץ הראשי -------------------------------


def test_dhcp_stanza_sends_the_hostname():
    cfg = netcfg.NetConfig("eth0", mode=netcfg.MODE_DHCP)
    text = netcfg.render(cfg, hostname="imagectl")
    assert "iface eth0 inet dhcp\n    hostname imagectl\n" in text
    assert "hostname" not in netcfg.render(cfg)
    assert "hostname" not in netcfg.render(
        netcfg.NetConfig("eth1", mode=netcfg.MODE_STATIC, address="10.44.9.10"), hostname="imagectl")


def test_console_preview_writes_the_hostname_into_the_dhcp_file(tmp_path, images_root, clock):
    from server import users
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     netcfg_hooks={"hostname": lambda: "imagectl-haifa",
                                   "interfaces": lambda: [{"name": "eth0", "state": "up", "addresses": []}]})
    users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
    r = client.post("/api/console/net/config/eth0/preview", json={"mode": "dhcp"})
    assert r.status_code == 200, r.text
    assert "    hostname imagectl-haifa" in r.json()["after"]


MAIN_IFACES = """# The primary network interface
auto lo eth0
iface lo inet loopback

allow-hotplug eth0
iface eth0 inet dhcp
    hostname debian

iface eth1 inet static
    address 10.44.9.10
    netmask 255.255.255.0
"""


def test_comment_out_stanzas_removes_only_that_interface():
    text, changed = netcfg_host.comment_out_stanzas(MAIN_IFACES, "eth0")
    assert changed
    live = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    assert "auto lo" in live and "iface lo inet loopback" in live
    assert "iface eth1 inet static" in live and "    address 10.44.9.10" in live
    assert not any(l.split()[:2] == ["iface", "eth0"] for l in live)
    assert "allow-hotplug eth0" not in live
    assert "# imagectl: iface eth0 inet dhcp" in text
    assert netcfg_host.comment_out_stanzas(MAIN_IFACES, "eth9") == (MAIN_IFACES, False)


def test_adopt_interface_backs_up_comments_out_sources_and_writes(tmp_path):
    main = tmp_path / "interfaces"
    root = tmp_path / "interfaces.d"
    main.write_text(MAIN_IFACES, encoding="utf-8")
    text = netcfg.render(netcfg.NetConfig("eth0", mode="dhcp"), hostname="imagectl")
    notes, error = netcfg_host.adopt_interface("eth0", text, main=main, root=root, backup_suffix="t")
    assert error is None
    assert (tmp_path / "interfaces.pre-imagectl.t").read_text(encoding="utf-8") == MAIN_IFACES
    new_main = main.read_text(encoding="utf-8")
    assert f"source {str(root).rstrip('/')}/*" in new_main
    assert "# imagectl: iface eth0 inet dhcp" in new_main
    assert (root / "imagectl-eth0").read_text(encoding="utf-8") == text
    # ריצה שנייה — אין מה להעיר, אין גיבוי נוסף, הקובץ נכתב שוב.
    notes2, error2 = netcfg_host.adopt_interface("eth0", text, main=main, root=root, backup_suffix="u")
    assert error2 is None and not (tmp_path / "interfaces.pre-imagectl.u").exists()


# --- המתקין והיחידה -------------------------------------------------------------


def _installer_block(tag: str) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(rf"<<{tag}\n(.*?)\n{tag}\b", text, flags=re.DOTALL)
    assert match, f"לא נמצא heredoc {tag} בסקריפט ההתקנה"
    return match.group(1)


def test_installer_netcfg_block_writes_dhcp_with_hostname_and_records_it(tmp_path, monkeypatch):
    """הבלוק המוטמע במתקין (NETEOF) מורץ כמו שהוא — סטייה בין המתקין לקוד
    היא בדיוק החור (כמו test_installer_admin)."""
    from server.db import connect, get_setting
    code = _installer_block("NETEOF").replace("$APP_DIR", REPO.as_posix()) \
        .replace("$DATA_DIR", tmp_path.as_posix())
    main, root = tmp_path / "interfaces", tmp_path / "interfaces.d"
    main.write_text(MAIN_IFACES, encoding="utf-8")
    real = netcfg_host.adopt_interface
    monkeypatch.setattr(netcfg_host, "adopt_interface",
                        lambda name, text, **kw: real(name, text, main=main, root=root))
    monkeypatch.setattr(socket, "gethostname", lambda: "imagectl-haifa")
    for k, v in {"SERVERS_IF": "eth0", "SERVERS_MODE": "dhcp", "SERVERS_ADDR": "",
                 "SERVERS_MASK": "", "SERVERS_GW": "", "SERVERS_DNS": ""}.items():
        monkeypatch.setenv(k, v)
    exec(compile(code, "install/setup-boot-server.sh", "exec"), {})
    written = (root / "imagectl-eth0").read_text(encoding="utf-8")
    assert "iface eth0 inet dhcp" in written and "hostname imagectl-haifa" in written
    assert "# imagectl: iface eth0 inet dhcp" in main.read_text(encoding="utf-8")
    conn = connect(tmp_path / "imagectl.db")
    assert json.loads(get_setting(conn, netcfg.SETTING_PREFIX + "eth0"))["mode"] == "dhcp"


def test_installer_netcfg_block_refuses_a_bad_static_address(tmp_path, monkeypatch):
    code = _installer_block("NETEOF").replace("$APP_DIR", REPO.as_posix()) \
        .replace("$DATA_DIR", tmp_path.as_posix())
    monkeypatch.setattr(netcfg_host, "adopt_interface",
                        lambda *a, **k: pytest.fail("כתב קובץ להגדרה פסולה"))
    for k, v in {"SERVERS_IF": "eth0", "SERVERS_MODE": "static", "SERVERS_ADDR": "10.44.9",
                 "SERVERS_MASK": "", "SERVERS_GW": "", "SERVERS_DNS": ""}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as exc:
        exec(compile(code, "install/setup-boot-server.sh", "exec"), {})
    assert "אינה תקינה" in str(exc.value)


def test_installer_text_deploy_is_optional_and_servers_nic_is_dhcp_by_default():
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"--interface\|--deploy-if\)", text)
    assert re.search(r"--servers-mode\)", text) and 'SERVERS_MODE="dhcp"' in text
    assert "לא עכשיו" in text
    assert 'CONSOLE_HOST="${CONSOLE_HOST:-$SERVERS_IF}"' in text
    # בלי כרטיס הפצה: אין interface=, dnsmasq כבוי, grub.cfg לא נכתב, חומת אש בלי --deploy-if.
    assert '[[ -n "$IFACE" ]] && NFT_ARGS+=(--deploy-if "$IFACE")' in text
    assert "systemctl disable --now dnsmasq" in text
    assert "grub/grub.cfg לא נכתב" in text
    assert 'VERIFY_URL="${SERVER_URL:-http://127.0.0.1:${PORT:-8080}}"' in text
    # ‏--interface ליחידה רק כשיש כרטיס; --console-host תמיד.
    assert re.search(r'if \[\[ -n "\$IFACE" \]\]; then\n\s+STORAGE_ARGS\+=" --interface \$IFACE"', text)
    assert 'STORAGE_ARGS+=" --console-host $CONSOLE_HOST"' in text
    assert "getent ahostsv4" in text                                # ראשי בשם → כתובת לחומת האש


def test_unit_no_longer_requires_imagectl_url_and_can_write_the_tftp_root():
    text = UNIT.read_text(encoding="utf-8")
    assert "ExecStartPre" not in text
    assert "${IMAGECTL_URL}" in text
    rw = next(l for l in text.splitlines() if l.startswith("ReadWritePaths="))
    assert "-/srv/tftp" in rw


def test_server_main_accepts_an_empty_server_url_argument():
    """היחידה מעבירה ${IMAGECTL_URL} ריק כארגומנט ריק אחד — הפרסר מקבל."""
    args = server.main.build_parser().parse_args(["--server-url", ""])
    assert args.server_url == "" and args.tftp_root == "/srv/tftp"
    assert server.main.build_parser().parse_args([]).server_url == ""


# --- pairing לפי FQDN --------------------------------------------------------------


def test_interserver_url_accepts_a_hostname_and_the_client_resolves_per_connection(monkeypatch):
    from server import interserver_auth, storage_client
    host, port, _ = interserver_auth.parse_interserver_url("https://imagectl.college.local:8443/")
    assert (host, port) == ("imagectl.college.local", 8443)
    seen = []

    def fake_connect(addr, timeout=None):
        seen.append(addr)
        raise OSError("לא מתחברים בבדיקה")
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    with pytest.raises(Exception):
        storage_client.fetch_server_spki(host, port, timeout=0.1)
    assert seen == [("imagectl.college.local", 8443)]          # השם, לא IP שנפתר מראש


def test_the_parent_is_authenticated_by_certificate_not_by_address():
    """ה-SPKI-pin אינו תלוי בכתובת: ל-`TlsPeer` אין שדה כתובת בכלל, ולכן
    חילוף כתובת של הראשי אינו נוגע בטרסט. הראיה ההתנהגותית — #740."""
    from dataclasses import fields
    from server import interserver_api
    assert {f.name for f in fields(interserver_api.TlsPeer)} == {"tls_version", "cert_der", "exporter"}
    from server.storage_nodes import normalize_config
    assert normalize_config("secondary", "https://imagectl.college.local:8443")[1] == \
        "https://imagectl.college.local:8443"


# --- ‏#1013: קובץ ה-dnsmasq הראשי נגזר מה-DB בעליית השרת --------------------------


def _startup(tmp_path, images_root, clock, *, source: str, on_disk: str | None,
             sync: bool = True, tftp_root: Path | None = None):
    """מרים runtime כמו main (sync_dnsmasq=True) עם dnsmasq מזויף, ומחזיר
    מה נכתב. ‏`on_disk` = מה ש-read_active_conf "רואה" (None = אין קובץ)."""
    from server.app import create_app
    applied: list[str] = []
    hooks = {
        "interfaces": lambda: [], "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: applied.append(text) or None,
        "apply_proxy": lambda text, active: pytest.fail("העלייה אינה נוגעת ב-proxy"),
        "read_active_conf": lambda: on_disk, "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    state = {"cli": deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080"),
             "console": deploy_net.DeployState("console", "eth1", "http://10.44.9.10:8080"),
             "none": deploy_net.DeployState("none", None, None)}[source]
    ctx = deploy_net.DeployContext(state=state, agent_port=8080,
                                   tftp_root=tftp_root or Path("/srv/tftp"), repo_dir=REPO)
    app = create_app(tmp_path / f"data-{source}-{sync}", images_root, "http://127.0.0.1:8080",
                     now_fn=clock, dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=sync)
    return applied, app.state.ctx


def test_startup_writes_tftp_into_the_console_file_on_a_fresh_install(tmp_path, images_root, clock):
    """התקנה טרייה עם כרטיס הפצה (cli): המתקין כבר אינו כותב enable-tftp,
    ו-dnsmasq שלו עלה בלי TFTP. השרת, בעלייה, כותב את הקובץ שלו — עם
    TFTP ובלי DHCP — ומפעיל את dnsmasq (hook). זה מה שמעלה 69."""
    applied, ctx = _startup(tmp_path, images_root, clock, source="cli", on_disk=None)
    assert len(applied) == 1
    text = applied[0]
    assert dhcp.installer_serves_tftp(text) and "tftp-root=/srv/tftp" in text
    assert "dhcp-range" not in text and "interface=" not in text        # cli: interface= במתקין
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal").fetchall()]
    assert "dhcp_synced_at_startup" in events


def test_startup_does_not_touch_dnsmasq_when_the_file_already_matches(tmp_path, images_root, clock):
    """אתחול שגרתי של השרת אינו מפיל את dnsmasq: אותו תוכן = אפס כתיבות."""
    applied, _ = _startup(tmp_path, images_root, clock, source="cli", on_disk=None)
    same = applied[0]
    applied2, ctx = _startup(tmp_path / "again", images_root, clock, source="cli", on_disk=same)
    assert applied2 == []
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal").fetchall()]
    assert "dhcp_synced_at_startup" not in events


def test_startup_leaves_dnsmasq_alone_until_the_deploy_network_is_configured(tmp_path, images_root, clock):
    """‏#1088: בלי רשת הפצה dnsmasq נשאר כבוי עד ההדלקה הראשונה — restart
    מהעלייה היה מעלה אותו על כל הכרטיסים (בלי interface=)."""
    applied, _ = _startup(tmp_path, images_root, clock, source="none", on_disk=None)
    assert applied == []


def test_startup_sync_is_opt_in_like_known_macs(tmp_path, images_root, clock):
    """ברירת המחדל של create_runtime אינה נוגעת ב-dnsmasq — רק main מדליק."""
    applied, _ = _startup(tmp_path, images_root, clock, source="cli", on_disk=None, sync=False)
    assert applied == []


def test_startup_honours_the_tftp_switch_and_the_console_deploy_interface(tmp_path, images_root, clock):
    """מה שנשמר ב-DB הוא מה שנכתב: מתג כבוי → בלי enable-tftp; כרטיס הפצה
    מהקונסולה → interface= בקובץ הזה; tftp_root מההתקנה."""
    from server import ports as ports_mod
    from server.app import create_app
    applied: list[str] = []
    hooks = {"interfaces": lambda: [], "probe": lambda name: dhcp.ProbeResult(True, ()),
             "apply": lambda text: applied.append(text) or None,
             "apply_proxy": lambda text, active: None,
             "read_active_conf": lambda: None, "service_active": lambda unit: True}
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("console", "eth1", "http://10.44.9.10:8080"),
        agent_port=8080, tftp_root=Path("/data/tftp"), repo_dir=REPO)
    # ריצה ראשונה: המתג נשמר כבוי (כאילו המפעיל כיבה לפני האתחול)
    data = tmp_path / "data"
    app = create_app(data, images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx)
    ports_mod.set_enabled(app.state.ctx.conn, "tftp", False)
    app.state.ctx.conn.close()
    app = create_app(data, images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=True)
    assert len(applied) == 1
    text = applied[0]
    assert not dhcp.installer_serves_tftp(text) and "TFTP off" in text
    assert "interface=eth1" in text
    app.state.ctx.conn.close()
    # ומתג דלוק עם שורש לא-ברירת-מחדל
    applied.clear()
    app = create_app(tmp_path / "data2", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=True)
    assert "tftp-root=/data/tftp" in applied[0]
