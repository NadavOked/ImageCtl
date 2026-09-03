"""הגדרות הרשת של השרת עצמו — כתובת, שער, DNS ונתיבים (‏#55, ‏#57).

מה שנבדק כאן הוא לא "האם ההגדרה נשמרה" אלא **האם הכתובת באמת השתנתה**.
שלושה מצבים, ולא שניים: "כתבנו את הקובץ", "ההחלה רצה" ו"‏ip addr מראה
את הכתובת". רק השלישי הוא הצלחה, ולכן רוב הקובץ הזה הוא תרחישי כשל —
קובץ שנכתב ולא נטען, ‏ifup שהצליח ולא שינה כלום, מצב שלא נקרא בכלל.

‏**אף בדיקה כאן אינה נוגעת ברשת אמיתית.** הכתיבה, ה-ifup והקריאה של
`ip addr` מוזרקים, בדיוק כמו `dhcp_hooks`: הבדיקה רואה את הטקסט שהיה
נכתב ואת הפקודות שהיו רצות. שרת המעבדה, שהבדיקות רצות עליו דרך SSH,
לא היה שורד את הגרסה האחרת.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import dhcp, netcfg, netcfg_host
from server.netcfg import MODE_DHCP, MODE_MANUAL, MODE_STATIC, NetConfig, StaticRoute

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


STATIC = dict(mode=MODE_STATIC, address="10.99.9.10", netmask="255.255.255.0")
ROUTE = {"destination": "10.97.0.0", "netmask": "255.255.255.0",
         "gateway": "10.99.9.9"}


def cfg(name: str = "eth1", **over) -> NetConfig:
    data = {**STATIC, **over}
    data["routes"] = [StaticRoute.from_any(r) for r in data.get("routes", [])]
    return NetConfig(name=name, **data)


# --- אימות: הכל חוזר יחד -----------------------------------------------------


def test_a_plain_static_address_is_accepted():
    assert netcfg.problems(cfg(), [cfg()]) == []


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"address": "10.99.9.300"}, "כתובת הכרטיס אינה תקינה"),
        ({"address": ""}, "כתובת הכרטיס אינה תקינה"),
        ({"netmask": "255.0.255.0"}, "מסכת רשת אינה תקינה"),
        ({"address": "10.99.9.0"}, "כתובת הרשת או הברודקאסט"),
        ({"gateway": "10.99.8.1"}, "אינו ברשת של"),
        ({"gateway": "nope"}, "השער אינו כתובת תקינה"),
        ({"dns": ["8.8.8.8", "lol"]}, "DNS"),
        ({"mode": "sideways"}, "מצב לא מוכר"),
    ],
)
def test_bad_settings_are_refused_in_hebrew(change, fragment):
    found = netcfg.problems(cfg(**change), [cfg(**change)])
    assert found and any(fragment in p for p in found), found


def test_a_masked_netmask_is_not_just_four_numbers():
    """‏255.0.255.0 נראית כמו מסכה ואינה — ‏ifupdown היה מקבל אותה
    ומייצר רשת שאיש לא התכוון אליה."""
    assert netcfg._valid_mask("255.255.254.0") is True
    assert netcfg._valid_mask("255.0.255.0") is False


def test_every_problem_comes_back_at_once_and_not_one_at_a_time():
    """‏#55 מבקש את שתי הבדיקות **יחד**. מפעיל שמתקן בעיה אחת ומגלה את
    השנייה רק בניסיון הבא לומד שהמסך לא יודע מה הוא רוצה — ובמסך שמשנה
    כתובות זה הרגע שבו הוא מנחש ומנתק את עצמו."""
    other = NetConfig("eth0", mode=MODE_STATIC, address="10.99.9.20",
                      netmask="255.255.255.0")
    serving = dhcp.InterfaceConfig(
        "eth1", enabled=True, range_start="10.99.9.50", range_end="10.99.9.200",
        netmask="255.255.255.0", server_ip="10.99.9.10")
    broken = cfg(address="10.96.0.10", gateway="10.99.8.1",
                 routes=[{**ROUTE, "gateway": "192.168.5.1"}])
    found = netcfg.problems(broken, [other, broken], serving)
    joined = " · ".join(found)
    assert len(found) >= 4, found
    assert "השער 10.99.8.1 אינו ברשת" in joined          # השער של הכרטיס
    assert "אינו ברשת של אף כרטיס" in joined             # השער של הנתיב
    assert "יוצא מהרשת החדשה" in joined                  # הטווח של ה-DHCP
    assert "מכריז על עצמו בכתובת" in joined              # כתובת ה-DHCP


def test_an_address_that_belongs_to_another_card_is_refused():
    other = NetConfig("eth0", mode=MODE_STATIC, address="10.99.9.10",
                      netmask="255.255.255.0")
    found = netcfg.problems(cfg(), [other, cfg()])
    assert any("כבר מוגדרת על eth0" in p for p in found), found


def test_two_cards_in_the_same_subnet_are_refused():
    other = NetConfig("eth0", mode=MODE_STATIC, address="10.99.9.20",
                      netmask="255.255.255.0")
    found = netcfg.problems(cfg(), [other, cfg()])
    assert any("חופפת לרשת של eth0" in p for p in found), found


# --- הכרטיס שמחלק DHCP (אילוץ ה-PRD) ------------------------------------------


SERVING = dhcp.InterfaceConfig(
    "eth1", enabled=True, range_start="10.99.9.50", range_end="10.99.9.200",
    netmask="255.255.255.0", server_ip="10.99.9.10")


def test_a_card_that_hands_out_addresses_cannot_become_a_dhcp_client():
    found = netcfg.problems(cfg(mode=MODE_DHCP), [], SERVING)
    assert any("אינו יכול להיות לקוח DHCP" in p for p in found), found


def test_a_card_that_hands_out_addresses_cannot_be_left_unmanaged():
    found = netcfg.problems(cfg(mode=MODE_MANUAL), [], SERVING)
    assert any("חייב כתובת סטטית" in p for p in found), found


def test_moving_a_dhcp_card_off_its_range_is_refused():
    """הבדיקה שאף אחד מהצדדים לבדו לא יכול לעשות: ‏dhcp.validate בודק
    ש-server_ip בתוך הרשת של הטווח, ואינו יודע דבר על הכתובת שהכרטיס
    באמת יקבל."""
    moved = cfg(address="10.99.8.10")
    found = netcfg.problems(moved, [moved], SERVING)
    assert any("יוצא מהרשת החדשה" in p for p in found), found


def test_a_card_that_serves_nothing_is_not_constrained():
    assert netcfg.problems(cfg(mode=MODE_DHCP), [],
                           dhcp.InterfaceConfig("eth1")) == []


# --- נתיבים סטטיים (‏#57) -----------------------------------------------------


def test_a_route_target_must_be_a_network_and_not_a_host():
    found = netcfg.problems(cfg(routes=[{**ROUTE, "destination": "10.97.0.5"}]),
                            [cfg()])
    assert any("היעד אינו רשת" in p for p in found), found


def test_a_route_gateway_nobody_can_reach_is_refused():
    found = netcfg.problems(cfg(routes=[{**ROUTE, "gateway": "192.168.7.1"}]),
                            [cfg()])
    assert any("אינו ברשת של אף כרטיס" in p for p in found), found


def test_a_route_gateway_reachable_through_another_card_is_fine():
    """השער של eth1 יכול להיות לגיטימי דרך eth0 — הבדיקה היא מול כל
    הכרטיסים, לא מול זה שנערך."""
    other = NetConfig("eth0", mode=MODE_STATIC, address="10.98.10.8",
                      netmask="255.255.255.0")
    route = {**ROUTE, "gateway": "10.98.10.9"}
    assert netcfg.problems(cfg(routes=[route]), [other, cfg(routes=[route])]) == []


def test_the_same_destination_twice_is_refused():
    found = netcfg.problems(cfg(routes=[ROUTE, ROUTE]), [cfg()])
    assert any("מופיע פעמיים" in p for p in found), found


def test_a_gateway_without_a_static_address_is_refused():
    found = netcfg.problems(cfg(mode=MODE_DHCP, gateway="10.99.9.1"), [])
    assert any("דורשים כתובת סטטית" in p for p in found), found


# --- רינדור -------------------------------------------------------------------


def test_a_static_card_renders_an_ifupdown_stanza():
    text = netcfg.render(cfg(gateway="10.99.9.1", dns=["10.99.0.5"],
                             routes=[ROUTE]))
    assert "auto eth1" in text
    assert "iface eth1 inet static" in text
    assert "    address 10.99.9.10" in text
    assert "    netmask 255.255.255.0" in text
    assert "    gateway 10.99.9.1" in text
    assert "post-up ip route add 10.97.0.0/24 via 10.99.9.9 dev eth1" in text
    assert "pre-down ip route del 10.97.0.0/24 via 10.99.9.9 dev eth1" in text


def test_the_route_lines_never_swallow_their_own_failure():
    """עיקרון 5 בשורה אחת: ‏`ip route add` שנכשל חייב להפיל את ה-ifup.
    ‏`|| true` כאן היה הופך "הנתיב הוגדר" לנכון על הנייר ולשקר
    ב-`ip route` — וזה בדיוק הבאג שחוזר בכל שכבה בפרויקט."""
    text = netcfg.render(cfg(routes=[ROUTE]))
    assert "|| true" not in text
    assert "2>/dev/null" not in text


def test_dns_is_not_written_as_a_line_that_does_nothing():
    """‏`dns-nameservers` עובד רק דרך resolvconf, שאינו מותקן כאן.
    שורה שנראית כמו הגדרה ואינה עושה כלום היא "כתבנו את הקובץ"
    שמתחזה ל"ההגדרה נכנסה"."""
    text = netcfg.render(cfg(dns=["10.99.0.5"]))
    assert "dns-nameservers" not in text
    assert "# dns 10.99.0.5" in text
    assert "nameserver 10.99.0.5" in netcfg.render_resolv([cfg(dns=["10.99.0.5"])])


def test_an_unmanaged_card_gets_no_configuration_at_all():
    text = netcfg.render(NetConfig("eth9"))
    assert "auto eth9" not in text and "iface" not in text
    assert "not managed" in text


def test_a_dhcp_client_card_is_two_lines():
    text = netcfg.render(cfg(mode=MODE_DHCP))
    assert "iface eth1 inet dhcp" in text
    assert "address" not in text


def test_resolv_conf_is_one_file_built_from_every_card():
    text = netcfg.render_resolv([cfg("eth0", dns=["10.99.0.5"]),
                                 cfg("eth1", dns=["10.99.0.5", "8.8.8.8"])])
    assert text.count("nameserver") == 2                 # כפילות מוסרת
    assert text.index("10.99.0.5") < text.index("8.8.8.8")


# --- הקריאה החוזרת: מה באמת מוגדר --------------------------------------------


IP_ADDR = """1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever
2: eth0    inet 10.98.10.8/24 brd 10.98.10.255 scope global eth0
3: eth1    inet 10.99.9.10/24 brd 10.99.9.255 scope global eth1
"""
IP_ROUTE = """default via 10.98.10.1 dev eth0 onlink
10.97.0.0/24 via 10.99.9.9 dev eth1
10.99.9.0/24 dev eth1 proto kernel scope link src 10.99.9.10
"""


def test_the_machine_is_read_the_way_iproute_writes_it():
    assert netcfg_host.parse_addr(IP_ADDR)["eth1"] == ["10.99.9.10/24"]
    assert netcfg_host.parse_addr("") == {}
    routes = netcfg_host.parse_routes(IP_ROUTE)
    assert "default via 10.98.10.1" in routes
    assert "10.97.0.0/24 via 10.99.9.9" in routes
    assert netcfg_host.parse_resolv("nameserver 10.99.0.5\nsearch x\n") == ["10.99.0.5"]


def test_a_state_that_could_not_be_read_is_never_a_match():
    """זה ההבדל בין ‎http=000 ל-200, ובין "לא בדקנו" ל"בדקנו והכל תקין"."""
    unread = netcfg_host.LiveState(False, reason="ip addr: לא רץ")
    found = netcfg_host.mismatches(cfg(), unread)
    assert found and "לא נקרא" in found[0]


def test_an_address_that_is_not_on_the_card_is_a_mismatch():
    state = netcfg_host.LiveState(True, {"eth1": ["10.99.8.10/24"]}, [], [])
    assert any("אינו נושא את 10.99.9.10/24" in p
               for p in netcfg_host.mismatches(cfg(), state))


def test_a_route_that_never_made_it_into_the_table_is_a_mismatch():
    state = netcfg_host.LiveState(True, netcfg_host.parse_addr(IP_ADDR),
                                  ["10.99.9.0/24"], [])
    found = netcfg_host.mismatches(cfg(routes=[ROUTE]), state)
    assert any("אינו בטבלת הניתוב" in p for p in found), found


def test_a_dns_server_missing_from_resolv_conf_is_a_mismatch():
    state = netcfg_host.LiveState(True, netcfg_host.parse_addr(IP_ADDR),
                                  netcfg_host.parse_routes(IP_ROUTE), [])
    assert any("resolv.conf" in p
               for p in netcfg_host.mismatches(cfg(dns=["10.99.0.5"]), state))


def test_a_full_match_is_empty():
    state = netcfg_host.LiveState(True, netcfg_host.parse_addr(IP_ADDR),
                                  netcfg_host.parse_routes(IP_ROUTE),
                                  ["10.99.0.5"])
    assert netcfg_host.mismatches(
        cfg(dns=["10.99.0.5"], routes=[ROUTE]), state) == []


def test_a_main_interfaces_file_that_never_sources_the_directory(tmp_path: Path):
    """הצורה הנקייה ביותר של "כתבנו ולא קרה כלום": קובץ בתיקייה שאיש
    לא טוען. ‏None (הקובץ הראשי לא נקרא) אינו "כן"."""
    main = tmp_path / "interfaces"
    main.write_text("auto lo\niface lo inet loopback\n")
    assert netcfg_host.sourced(main, "/etc/network/interfaces.d") is False
    main.write_text("source /etc/network/interfaces.d/*\n")
    assert netcfg_host.sourced(main, "/etc/network/interfaces.d") is True
    assert netcfg_host.sourced(tmp_path / "nowhere") is None


# --- דרך הקונסולה -------------------------------------------------------------


def build_state(fake) -> netcfg_host.LiveState:
    """מצב "המכונה" שנגזר ממה שבאמת נכתב לקבצים.

    זו הנקודה: הבדיקה לא מספרת לשרת שהשינוי תפס — היא מפעילה סימולציה
    של המכונה שקוראת את הקבצים, ואפשר לכבות אותה (`applies`) כדי לבדוק
    בדיוק את המקרה של קובץ שנכתב ושום דבר לא זז.
    """
    if not fake["readable"]:
        return netcfg_host.LiveState(False, reason="ip addr: לא רץ")
    addresses = {n["name"]: list(n["addresses"]) for n in fake["interfaces"]}
    routes = ["10.98.10.0/24 dev eth0"]
    for name, text in fake["confs"].items():
        if not fake["applies"]:
            continue
        address = re.search(r"^    address (\S+)", text, re.M)
        mask = re.search(r"^    netmask (\S+)", text, re.M)
        if address and mask:
            prefix = netcfg._prefix_len(mask.group(1))
            addresses[name] = [f"{address.group(1)}/{prefix}"]
        gateway = re.search(r"^    gateway (\S+)", text, re.M)
        if gateway:
            routes.append(f"default via {gateway.group(1)}")
        for target, via in re.findall(r"post-up ip route add (\S+) via (\S+)", text):
            routes.append(f"{target} via {via}")
    servers = netcfg_host.parse_resolv(fake["resolv"]) if fake["applies"] else []
    return netcfg_host.LiveState(True, addresses, routes, servers)


@pytest.fixture()
def net_server(tmp_path: Path, images_root: Path, clock):
    """שרת עם שני כרטיסים, מכונה מדומה, ושעון בידיים.

    הקונסולה "מגיעה" דרך eth0 (‏10.98.10.8) — בדיוק כמו שרת המעבדה.
    """
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    fake = {
        "interfaces": [
            {"name": "eth0", "state": "up", "mac": "aa:00",
             "addresses": ["10.98.10.8/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:01",
             "addresses": ["10.99.9.10/24"]},
        ],
        "confs": {},                # מה נכתב ל-interfaces.d
        "resolv": "nameserver 10.99.0.5\n",
        # ‏(שם, האם סמן ההחזרה כבר היה על הדיסק) לכל כתיבה — הראיה
        # לסדר הכתיבות, שהוא ההגנה עצמה (‏#56).
        "writes": [],
        "applied": [],              # אילו כרטיסים עברו ifdown/ifup
        "apply_error": None,
        "write_error": None,
        "applies": True,            # האם ההחלה באמת משנה משהו
        "readable": True,           # האם המצב בפועל בכלל נקרא
        "timer": (True, "active"),  # האם זרוע ההחזרה של #56 פעילה
        "local": "10.98.10.8",      # דרך איזו כתובת הגיעה הבקשה
        "dnsmasq": [],              # מה נכתב ל-dnsmasq (לשונית ה-DHCP)
        "proxy": [],                # ומה לאינסטנס ה-proxy
    }

    state_dir = tmp_path / "netstate"

    def write_conf(name, text, root=None):
        if fake["write_error"]:
            return fake["write_error"]
        fake["writes"].append((name, (state_dir / "pending.json").exists()))
        if text is None:
            fake["confs"].pop(name, None)
        else:
            fake["confs"][name] = text
        return None

    def apply_interface(name):
        fake["applied"].append(name)
        return fake["apply_error"]

    hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "netcfg_read_conf": lambda name, root=None: fake["confs"].get(name),
        "netcfg_write_conf": write_conf,
        "netcfg_write_resolv": lambda text, path=None: fake.__setitem__(
            "resolv", text),
        "netcfg_apply": apply_interface,
        "netcfg_state": lambda: build_state(fake),
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: fake["timer"],
        "netcfg_now": clock,
        "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: fake["local"],
    }
    # שתי הלשוניות הן שני ראוטרים, ולכן **שתי** הזרקות: הבדיקה שמוודאת
    # שהצדדים מסכימים חוצה אל `/api/console/net/interfaces` — הראוטר של
    # ה-DHCP — ובלי `dhcp_hooks` הוא נופל על ברירת המחדל שקוראת את
    # ‏`/sys/class/net` של המכונה שמריצה. במעבדה יש שם `eth1` ולכן זה
    # עבר; ל-runner אין, והוא קיבל 404 (#113). אותה רשימת כרטיסים
    # מוזרקת לשני הצדדים, כי זה בדיוק מה שהבדיקה טוענת עליו.
    dhcp_hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: (fake["dnsmasq"].append(text), None)[1],
        "apply_proxy": lambda text, active: (
            fake["proxy"].append((text, active)), None)[1],
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    app = create_app(tmp_path / "data", images_root, "http://10.98.10.8:8080",
                     now_fn=clock, netcfg_hooks=hooks, dhcp_hooks=dhcp_hooks,
                     netcfg_state_dir=state_dir)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "app": app,
            "state_dir": state_dir, "ctx": app.state.ctx, "clock": clock,
            # אותם hooks בדיוק שזרוע ההחזרה של #56 מקבלת בבדיקות שלה,
            # כדי שגם היא לא תיגע במכונה.
            "hooks": hooks}


CONF = "/api/console/net/config"


def put(server, name="eth1", **over):
    body = {**STATIC, "confirm": name, **over}
    return server["admin"].put(f"{CONF}/{name}", json=body)


def confirm(server, interface="eth1"):
    """"אני עדיין רואה את הקונסולה" — משחרר את הסמן של #56 כדי שאפשר
    יהיה לעשות שינוי נוסף. שינוי ששלח את הסמן ולא אושר חוסם את הבא."""
    return server["admin"].post(f"{CONF}/confirm", json={"interface": interface})


def journal_events(server) -> list[tuple[str, str]]:
    rows = server["ctx"].conn.execute(
        "SELECT event, detail FROM journal ORDER BY rowid").fetchall()
    return [(r["event"], r["detail"]) for r in rows]


def test_a_static_address_goes_from_the_console_to_a_file(net_server):
    """הפרוסה הראשונה של #55, מקצה לקצה: כתובת לכרטיס שאינו זה שדרכו
    הגיעה הבקשה, שנכתבת לקובץ ששורד אתחול ומאומתת בקריאה חוזרת."""
    result = put(net_server, address="10.99.9.11").json()
    fake = net_server["fake"]
    assert result["ok"] is True and result["verified"] is True
    assert result["mismatches"] == []
    assert "iface eth1 inet static" in fake["confs"]["eth1"]
    assert "    address 10.99.9.11" in fake["confs"]["eth1"]
    assert fake["applied"] == ["eth1"]
    # הקובץ הוא ב-interfaces.d, ולכן הוא נטען שוב בכל אתחול.
    assert netcfg_host.conf_path("eth1").name == "imagectl-eth1"
    assert str(netcfg_host.conf_path("eth1")).startswith("/etc/network/interfaces.d")


def test_the_file_is_written_but_nothing_moved(net_server):
    """המצב שהמשימה קיימת בשבילו: הכתיבה הצליחה, ‏ifup יצא באפס,
    והכתובת לא זזה — קובץ `interfaces` שלא טוען את `interfaces.d`,
    כרטיס שמנוהל בפועל בידי משהו אחר. "נשמר" כאן הוא שקר."""
    net_server["fake"]["applies"] = False
    result = put(net_server, address="10.99.9.11").json()
    assert result["apply_error"] is None            # הפקודות רצו
    assert result["ok"] is False and result["verified"] is False
    assert any("אינו נושא את 10.99.9.11/24" in m for m in result["mismatches"])
    assert any(e == "net_config_unverified" for e, _ in journal_events(net_server))


def test_a_state_that_cannot_be_read_is_not_a_success(net_server):
    net_server["fake"]["readable"] = False
    result = put(net_server, address="10.99.9.11").json()
    assert result["ok"] is False and result["verified"] is False
    assert "לא נקרא" in result["mismatches"][0]


def test_nothing_is_written_before_the_name_is_typed(net_server):
    """עיקרון 7: שינוי כתובת הוא הרסני-בפועל."""
    response = net_server["admin"].put(f"{CONF}/eth1", json={**STATIC})
    assert response.status_code == 409
    assert "eth1" in response.json()["detail"]
    assert net_server["fake"]["confs"] == {}
    assert net_server["admin"].put(
        f"{CONF}/eth1", json={**STATIC, "confirm": "eth0"}).status_code == 409


def test_a_bad_setting_is_refused_before_anything_is_written(net_server):
    response = put(net_server, address="10.99.9.500")
    assert response.status_code == 400
    assert "כתובת הכרטיס אינה תקינה" in response.json()["detail"]
    assert net_server["fake"]["confs"] == {}
    assert net_server["fake"]["applied"] == []


def test_a_card_that_is_not_in_the_machine_is_refused(net_server):
    assert put(net_server, name="eth7").status_code == 404


def test_a_write_that_fails_says_so_instead_of_a_500(net_server):
    net_server["fake"]["write_error"] = "לא ניתן לכתוב: Permission denied"
    result = put(net_server, address="10.99.9.11")
    assert result.status_code == 200
    assert "Permission denied" in result.json()["apply_error"]
    assert result.json()["ok"] is False


def test_the_preview_shows_the_file_before_and_after(net_server):
    put(net_server, address="10.99.9.11")
    preview = net_server["admin"].post(
        f"{CONF}/eth1/preview",
        json={**STATIC, "address": "10.99.9.12"}).json()
    assert "    address 10.99.9.11" in preview["before"]
    assert "    address 10.99.9.12" in preview["after"]
    assert preview["problems"] == []
    assert preview["changed"] == ["address"]
    assert preview["path"].endswith("imagectl-eth1")


def test_the_preview_names_the_problems_without_writing(net_server):
    preview = net_server["admin"].post(
        f"{CONF}/eth1/preview",
        json={**STATIC, "address": "10.99.9.999"}).json()
    assert preview["problems"]
    assert net_server["fake"]["confs"] == {}


def test_deploy_is_refused_everywhere(net_server):
    """הגדרת רשת של השרת היא ניהול. משתמש הפצה לא רואה ולא נוגע."""
    deploy = net_server["deploy"]
    assert deploy.get(CONF).status_code == 403
    assert deploy.put(f"{CONF}/eth1", json={**STATIC, "confirm": "eth1"}
                      ).status_code == 403
    assert deploy.post(f"{CONF}/eth1/preview", json=STATIC).status_code == 403
    assert deploy.post(f"{CONF}/confirm", json={}).status_code == 403


def test_the_read_shows_the_evidence_next_to_the_setting(net_server):
    put(net_server, address="10.99.9.11")
    body = net_server["admin"].get(CONF).json()
    rows = {r["name"]: r for r in body["interfaces"]}
    assert rows["eth1"]["mode"] == MODE_STATIC
    assert rows["eth1"]["live_addresses"] == ["10.99.9.11/24"]
    assert rows["eth1"]["mismatches"] == []
    assert body["live"]["checked"] is True
    assert body["sourced"] is True


def test_the_dhcp_side_and_the_address_side_agree(net_server):
    """שתי הבדיקות יחד גם דרך ה-API: כרטיס שמחלק כתובות אינו יכול
    לעבור ל-DHCP client, וגם לא לזוז מהרשת של הטווח."""
    admin = net_server["admin"]
    assert put(net_server, address="10.99.9.10").status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1", json={
        "enabled": True, "range_start": "10.99.9.50", "range_end": "10.99.9.200",
        "netmask": "255.255.255.0", "server_ip": "10.99.9.10",
        "confirm": "eth1", "ignore_existing": True}).status_code == 200

    refused = put(net_server, mode=MODE_DHCP)
    assert refused.status_code == 400
    assert "לקוח DHCP" in refused.json()["detail"]

    moved = put(net_server, address="10.99.8.10")
    assert moved.status_code == 400
    assert "יוצא מהרשת החדשה" in moved.json()["detail"]


# --- ‏#57: שער, DNS ונתיבים ----------------------------------------------------


def test_a_static_route_reaches_the_file_and_the_routing_table(net_server):
    """מה שהתחיל את כל הבקשה (‏#50): נתיב שלא נעלם באתחול."""
    result = put(net_server, address="10.99.9.10", gateway="10.99.9.1",
                 dns=["10.99.0.5", "8.8.8.8"], routes=[ROUTE]).json()
    assert result["ok"] is True and result["mismatches"] == []
    conf = net_server["fake"]["confs"]["eth1"]
    assert "post-up ip route add 10.97.0.0/24 via 10.99.9.9 dev eth1" in conf
    assert "    gateway 10.99.9.1" in conf
    assert "nameserver 8.8.8.8" in net_server["fake"]["resolv"]
    body = net_server["admin"].get(CONF).json()
    assert "10.97.0.0/24 via 10.99.9.9" in body["live"]["routes"]
    assert body["live"]["nameservers"] == ["10.99.0.5", "8.8.8.8"]


def test_removing_a_route_removes_it_from_both(net_server):
    put(net_server, routes=[ROUTE])
    assert "post-up ip route add" in net_server["fake"]["confs"]["eth1"]
    assert confirm(net_server).status_code == 200
    result = put(net_server, routes=[]).json()
    assert result["ok"] is True
    assert "post-up ip route add" not in net_server["fake"]["confs"]["eth1"]
    body = net_server["admin"].get(CONF).json()
    assert "10.97.0.0/24 via 10.99.9.9" not in body["live"]["routes"]


def test_a_bad_route_is_refused_in_hebrew_before_anything_is_written(net_server):
    response = put(net_server, routes=[{**ROUTE, "destination": "10.97.0.5"}])
    assert response.status_code == 400
    assert "היעד אינו רשת" in response.json()["detail"]
    assert net_server["fake"]["confs"] == {}


# --- מה שהמכונה צריכה כדי שכל זה יעבוד ---------------------------------------

REPO = Path(__file__).resolve().parent.parent


def test_the_console_assets_all_carry_the_same_version():
    """מטמון הדפדפן: חצי bump גרוע מאין bump. ‏netcfg.js חדש מול
    console.js ישן הוא JS שקורא ל-`sheet()` בחתימה שכבר השתנתה."""
    page = (REPO / "server" / "static" / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([0-9.]+)", page))
    assert len(versions) == 1, versions
    assert "netcfg.js?v=" in page


def test_the_server_unit_may_write_where_the_code_writes():
    """‏`ReadWritePaths` שאינו כולל את היעד = כתיבה שנכשלת על שרת אמיתי
    ועוברת בכל בדיקה. הדרישה נבדקת מול הקבוע שבקוד, ולא מול מחרוזת
    שהועתקה לכאן."""
    unit = (REPO / "install" / "imagectl-server.service").read_text(encoding="utf-8")
    paths = next(line for line in unit.splitlines()
                 if line.startswith("ReadWritePaths="))
    assert netcfg_host.INTERFACES_DIR in paths
    # ‏resolv.conf הוא קובץ בשורש /etc, ואי אפשר לפתוח אותו לבדו.
    assert " /etc" in paths and netcfg_host.RESOLV_CONF.startswith("/etc/")
