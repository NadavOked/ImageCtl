"""מסך "פורטים" בקונסולה (#822) — הרשימה מגיעה מהשרת, לא defs קבוע ב-JS.

אותו דפוס hooks מוזרק כמו test_server_health.py (health_server), כך
שאף בדיקה כאן לא נוגעת ב-ss/systemctl/sshd אמיתיים.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.ssh_switch import Listeners

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_DNSMASQ = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))
UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))
"""


@pytest.fixture()
def ports_server(tmp_path: Path, images_root: Path, clock):
    """כמו health_server, אבל בלי דרישת קבצי אתחול — מסך הפורטים אינו
    בודק boot_files/shim, ואין להם בכלל hooks כאן."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import monitor, users
    from server.app import create_app
    from server.db import set_setting

    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)

    fake = {"ss": SS_DNSMASQ, "http": 200,
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            "listeners": Listeners(True),
            "menu": "linux /boot/vmlinuz ip=dhcp imagectl.server=x console=tty0",
            "udp_sender_pids": [],
            # ‏#996: טבלת ה-TCP "לא נקראה" כאן — כמו בתחנה בלי ss. בלי
            # ההזרקה הבדיקה הייתה מריצה ss אמיתי (ובמעבדה — רואה מאזינים
            # אמיתיים). המתג עצמו נבדק ב-test_port_toggles.py.
            "ss_tcp": "",
            "nft_ruleset": (
                "table inet imagectl {\n"
                "\tchain input {\n"
                "\t\tiifname \"lo\" accept\n"
                "\t}\n"
                "}\n")}
    hooks = {
        "ss": lambda: fake["ss"],
        "ss_tcp": lambda: fake["ss_tcp"],
        "nft_ruleset": lambda: fake["nft_ruleset"],
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
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    anon = TestClient(app)
    return {"admin": admin, "deploy": deploy, "anon": anon, "fake": fake,
           "ctx": app.state.ctx, "monitor": monitor, "set_setting": set_setting}


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_ports_endpoint_is_console_only(ports_server):
    """‏#1073: הדף הוא קונסולה — deploy מקבל 403 `deploy_no_console` (לא
    admin_only: הנתיב עצמו נשאר current_user, והסירוב הוא של הקונסולה)."""
    resp = ports_server["deploy"].get("/api/console/ports")
    assert resp.status_code == 403 and "מחשב הבנייה" in resp.json()["detail"]
    resp = ports_server["admin"].get("/api/console/ports")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_ports_endpoint_requires_login(ports_server):
    assert ports_server["anon"].get("/api/console/ports").status_code == 401


def test_the_list_includes_5900_8082_and_ssh_not_a_fixed_five(ports_server):
    """זה בדיוק מה שהעניין #822 מדווח: 5900/8082/SSH חסרים ברשימה
    הקבועה הישנה."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["port"] == "5900"
    assert rows["kiosk"]["port"] == "8082"
    assert rows["ssh_stations"]["port"] == "22"
    # וגם הקיימים מקודם
    assert rows["tftp"]["port"] == "69"
    assert rows["http_boot"]["port"] == "8080"
    assert rows["multicast"]["port"] == "9000–9001"  # פורט-הפצה-מכוון


def test_monitor_switch_off_shows_off_not_green(ports_server):
    """המתג כבוי כברירת מחדל — השורה חייבת לומר את זה, לא "לא אומת"
    ולא ירוק."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["state"] == "off"
    assert "כבוי" in rows["monitor"]["detail"]


def test_monitor_switch_on_shows_a_warning_not_green(ports_server):
    """הדלקה היא סיכון מוצהר (RFB על כל תחנה) — warn ולא ok, באותו
    היגיון כמו ssh_stations פתוח."""
    ports_server["set_setting"](ports_server["ctx"].conn,
                                ports_server["monitor"].STATION_KEY,
                                ports_server["monitor"].flag_json(True))
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["monitor"]["state"] == "warn"
    assert "דלוק" in rows["monitor"]["detail"]


def test_unverified_ports_say_so_and_are_not_painted_green(ports_server):
    """8081/8082: טבלת הסוקטים של TCP לא נקראה — "לא נקרא" הוא `unknown`
    (אדום, כמו דלת SSH שאי אפשר לראות), ולעולם לא ok (עיקרון 5). ‏#996
    החליף את "אין hook" הישן בקריאה אמיתית מ-`ss -ltnp`; ‏4011 נקרא
    מטבלת ה-UDP, שכאן קיימת ואין בה מאזין — ובלי proxy דלוק זה off."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    for pid in ("http_console", "kiosk"):
        assert rows[pid]["state"] == "unknown"
        assert rows[pid]["listening"] is None
    assert rows["pxe_proxy"]["state"] == "off"
    assert rows["pxe_proxy"]["listening"] is False
    for pid in ("http_console", "pxe_proxy", "kiosk"):
        assert rows[pid]["state"] != "ok"


def test_ssh_stations_reuses_real_evidence_not_the_setting(ports_server):
    """דלת התחנות נמדדת מהתפריט שהשרת מגיש בפועל (כמו ב-/health),
    ולא מההגדרה השמורה."""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["ssh_stations"]["state"] == "ok"       # התפריט נקי

    ports_server["fake"]["menu"] = ("linux /boot/vmlinuz imagectl.server=x "
                                    "imagectl.debug=1 console=tty0")
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["ssh_stations"]["state"] == "warn"
    assert "imagectl.debug" in rows["ssh_stations"]["detail"]


def test_a_stranger_on_tftp_is_red(ports_server):
    ports_server["fake"]["ss"] = (
        "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
        'UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     '
        'users:(("evil",pid=1,fd=1))\n')
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["tftp"]["state"] == "warn"
    assert "evil" in rows["tftp"]["detail"]


def test_every_row_carries_a_firewall_note(ports_server):
    """"הסבר קצר לכל שורה" — בקשת נדב, לא רק state/label."""
    rows = ports_server["admin"].get("/api/console/ports").json()
    assert rows
    for row in rows:
        assert row["note"], f"{row['id']} is missing a note"


def test_ports_list_includes_firewall_row(ports_server):
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    fw = rows["firewall"]
    assert fw["name"] == "חומת אש"
    assert fw["toggle"] == "none"
    assert fw["state"] == "ok"
    assert "פעילה" in fw["detail"]
    assert fw["enabled"] is None


def test_firewall_row_three_states(ports_server):
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "ok"

    ports_server["fake"]["nft_ruleset"] = ""
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "off"
    assert rows["firewall"]["detail"] == "לא נטענה"

    ports_server["fake"]["nft_ruleset"] = None
    rows = by_id(ports_server["admin"].get("/api/console/ports").json())
    assert rows["firewall"]["state"] == "unknown"
    assert rows["firewall"]["detail"] == "לא הצלחנו לבדוק"
