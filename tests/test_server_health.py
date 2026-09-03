"""מסך בריאות המערכת — הבדיקות שהיו דורשות טרמינל, עכשיו בקונסולה.

הפקודות (ss, systemctl, HTTP) מוזרקות, כך שכל תרחיש — פורט תפוס על ידי
זר, dnsmasq שנפל, שרת שלא עונה — משוחזר כאן בלי מכונה אמיתית.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.health import port_owner
from server.ssh_switch import Listeners

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_DNSMASQ = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))
UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))
"""

SS_STRANGER = """State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process
UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("isc-dhcp-server",pid=99,fd=4))
"""


def test_port_owner_reads_ss_output():
    assert port_owner(SS_DNSMASQ, 67) == "dnsmasq"
    assert port_owner(SS_DNSMASQ, 69) == "dnsmasq"
    assert port_owner(SS_DNSMASQ, 68) is None
    assert port_owner(SS_STRANGER, 67) == "isc-dhcp-server"


@pytest.fixture()
def health_server(tmp_path: Path, images_root: Path, clock):
    """שרת עם בדיקות מזויפות שאפשר לסובב תרחיש-תרחיש."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")

    fake = {"ss": SS_DNSMASQ, "active": "active", "http": 200,
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa", "addresses": []}],
            # שתי דלתות ה-SSH (#83) — גם הן מוזרקות, כדי ששום בדיקה
            # לא תקרא את /proc של המכונה שהיא רצה עליה ולא תיגע ב-sshd.
            "listeners": Listeners(True),
            "menu": "linux /boot/vmlinuz ip=dhcp imagectl.server=x console=tty0"}
    hooks = {
        "ss": lambda: fake["ss"],
        "unit_active": lambda name: fake["active"],
        "http_get": lambda url: fake["http"],
        "http_text": lambda url: (200, fake["menu"]),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "listeners": lambda: fake["listeners"],
        "apply_sshd": lambda text: pytest.fail("בדיקה נגעה ב-sshd אמיתי"),
        "settle": lambda: None,
    }
    app = create_app(tmp_path / "data", images_root, "http://10.99.12.10:8080",
                     now_fn=clock, health_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "tftp": tftp}


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_a_healthy_server_is_all_green(health_server):
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "ok"
    assert rows["tftp_port"]["state"] == "ok"
    assert rows["dnsmasq"]["state"] == "ok"
    assert rows["boot_files"]["state"] == "ok"
    assert rows["server"]["state"] == "ok"
    assert rows["nics"]["state"] == "ok"
    # DHCP עוד לא הודלק מהקונסולה — וזה נאמר, לא מוסתר.
    assert "לא הודלק" in rows["nics"]["detail"]
    # שתי דלתות ה-SSH סגורות, וזה נאמר לפי ראיה ולא לפי ההגדרה.
    assert rows["ssh_stations"]["state"] == "ok"
    assert rows["ssh_server"]["state"] == "ok"


def test_a_stranger_on_port_67_is_red(health_server):
    health_server["fake"]["ss"] = SS_STRANGER
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "bad"
    assert "isc-dhcp-server" in rows["dhcp_port"]["detail"]
    assert rows["tftp_port"]["state"] == "bad"          # אף אחד לא מגיש TFTP


def test_a_dead_dnsmasq_and_unreachable_server_are_red(health_server):
    health_server["fake"]["active"] = "failed"
    health_server["fake"]["http"] = None
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dnsmasq"]["state"] == "bad"
    assert rows["server"]["state"] == "bad"


def test_missing_boot_files_are_named(health_server):
    (health_server["tftp"] / "grubx64.efi").unlink()
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert "grubx64.efi" in rows["boot_files"]["detail"]


def test_where_checks_cannot_run_the_light_is_grey_not_red(health_server):
    """על מכונת פיתוח בלי ss/systemctl המסך לא זועק אדום — הוא אומר
    שאי אפשר לבדוק כאן."""
    health_server["fake"]["ss"] = ""
    health_server["fake"]["active"] = ""
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["dhcp_port"]["state"] == "off"
    assert rows["tftp_port"]["state"] == "off"
    assert rows["dnsmasq"]["state"] == "off"


def test_but_an_ssh_check_that_could_not_run_is_red_not_grey(health_server):
    """ההפך המכוון של הבדיקה שמעליה. פורט 67 שלא נבדק משאיר PXE שלא
    עובד — ורואים את זה מיד. דלת SSH שלא נבדקה נראית *בדיוק* כמו דלת
    סגורה, ולכן אפור כאן היה שקר מרגיע (#83)."""
    health_server["fake"]["listeners"] = Listeners(False, reason="אין /proc")
    health_server["fake"]["menu"] = ""
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "bad"
    assert rows["ssh_stations"]["state"] == "bad"


def test_health_is_admin_only(health_server):
    assert health_server["deploy"].get("/api/console/health").status_code == 403
