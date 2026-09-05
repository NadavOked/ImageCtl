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
            # ‏vmlinuz ו-initrd.img כפי שהתחנה מושכת אותם (#333): קוד
            # וגודל, כי 404 של ‎/boot הוא גוף בן תשעה בייטים (#332).
            "assets": {"vmlinuz": (200, 9_000_000),
                       "initrd.img": (200, 31_000_000)},
            "menu": "linux /boot/vmlinuz ip=dhcp imagectl.server=x console=tty0"}
    hooks = {
        "ss": lambda: fake["ss"],
        "unit_active": lambda name: fake["active"],
        "http_get": lambda url: fake["http"],
        "http_size": lambda url: fake["assets"].get(url.rsplit("/", 1)[-1],
                                                    (404, 9)),
        "http_text": lambda url: (200, fake["menu"]),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "listeners": lambda: fake["listeners"],
        "apply_sshd": lambda text: pytest.fail("בדיקה נגעה ב-sshd אמיתי"),
        "settle": lambda: None,
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
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


# --- #333: שרשרת האתחול אינה נגמרת בשורש ה-TFTP -------------------------
#
# ‏shim, ‏GRUB ו-grub.cfg מביאים את התחנה עד התפריט. משם היא מושכת ב-HTTP
# את ‎/boot/vmlinuz ואת ‎/boot/initrd.img מ-/srv/imagectl/boot, ואת
# **אלה** ‎boot_files לא בדק. בהתקנה נקייה (#332) התיקייה נשארה ריקה,
# שניהם החזירו 404, והשורה הייתה ירוקה בשני המקרים.

def test_a_fresh_server_without_the_initrd_is_red(health_server):
    """זה בדיוק מה שנמדד ב-#332: המתקין סיים "מוכן", התיקייה ריקה.

    ‏shim, ‏GRUB והתפריט **כן** על ה-TFTP — ולכן השורה הייתה ירוקה
    למרות ששום תחנה לא יכולה לעלות. הכרעה: `bad`. שרת שאינו יכול
    להגיש אתחול אינו "אזהרה" — אי אפשר לפרוס ממנו.
    """
    health_server["fake"]["assets"] = {}          # שניהם 404, כמו בהתקנה נקייה
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert "initrd.img" in rows["boot_files"]["detail"]
    assert "vmlinuz" in rows["boot_files"]["detail"]


def test_a_truncated_asset_is_red_although_it_answers_200(health_server):
    """גודל בלבד אינו ראיה, וגם 200 בלבד אינו.

    גוף ה-404 של ‎/boot הוא תשעה בייטים — "יש תשובה ויש גודל" הוא בדיוק
    המצב שנראה תקין ב-#332. קובץ שנקטע באמצע העתקה נראה אותו הדבר.
    """
    health_server["fake"]["assets"] = {"vmlinuz": (200, 9_000_000),
                                       "initrd.img": (200, 9)}
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert "initrd.img" in rows["boot_files"]["detail"]


def test_a_boot_asset_that_could_not_be_probed_is_red_not_ok(health_server):
    """‏"לא הצלחנו לבדוק" אינו "בדקנו, הכל תקין" — עיקרון 5.

    אם בדיקה שלא רצה הייתה מחזירה `ok`, היינו חוזרים בדיוק לבאג: מסך
    ירוק על שרת שאינו יכול להגיש אתחול.

    והשורה גם אומרת **מה** לא רצה. "הקובץ החזיר 200 בלי גודל" על שרת
    שלא ענה בכלל שולח לחפש קובץ פגום במקום שרת שקט.
    """
    health_server["fake"]["assets"] = {"vmlinuz": (None, None),
                                       "initrd.img": (None, None)}
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "bad"
    assert rows["boot_files"]["state"] != "ok"
    assert "לא רצה" in rows["boot_files"]["detail"]
    assert "השרת לא ענה" in rows["boot_files"]["detail"]
    assert "200" not in rows["boot_files"]["detail"]


def test_the_green_row_says_the_assets_were_actually_pulled(health_server):
    """הצד החיובי. שורה ירוקה שאינה מזכירה את ה-initrd היא שורה שלא
    בדקה אותו — וזה מה שהיה כאן."""
    rows = by_id(health_server["admin"].get("/api/console/health").json())
    assert rows["boot_files"]["state"] == "ok"
    assert "initrd.img" in rows["boot_files"]["detail"]


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


# --- #329: סחיפת shim ---------------------------------------------------
#
# המתקין מעתיק את ה-shim פעם אחת ולא מעדכן. מיקרוסופט דוחפת עדכוני
# SBAT שמבטלים shim ישן, ואז כל הצי מפסיק לעלות ב-PXE בבת אחת.
# שלושת המצבים נפרדים: תואם · ישן · לא ניתן לקרוא. השלישי אינו
# "תקין" — זו בדיקה שלא רצה.

def _shim(server, tmp_path, src_bytes, tftp_bytes):
    from server import health
    src = tmp_path / "shimx64.efi.signed"
    if src_bytes is not None:
        src.write_bytes(src_bytes)
    tftp = server["tftp"]
    tftp.joinpath("bootx64.efi").write_bytes(tftp_bytes)
    # אותם fakes כמו ב-fixture: אף בדיקה אינה נוגעת במכונה שמריצה
    # אותה. ‏hostguard תופס בדיוק את זה (#113).
    hooks = {**health.default_hooks(),
             "ss": lambda: SS_DNSMASQ, "unit_active": lambda n: "active",
             "http_get": lambda u: 200, "http_text": lambda u: (200, ""),
             "http_size": lambda u: (200, 31_000_000),
             "interfaces": lambda: [{"name": "eth0", "state": "up",
                                     "mac": "aa", "addresses": []}],
             "listeners": lambda: Listeners(True),
             "apply_sshd": lambda t: pytest.fail("נגעה ב-sshd אמיתי"),
             "settle": lambda: None,
             "tftp_root": lambda: tftp, "shim_src": lambda: src}
    ctx = server["admin"].app.state.ctx
    rows = health.collect(ctx, hooks, "http://x")
    return by_id(rows)["shim_fresh"]


def test_a_shim_that_matches_the_system_copy_is_ok(health_server, tmp_path):
    r = _shim(health_server, tmp_path, b"same", b"same")
    assert r["state"] == "ok"


def test_a_stale_shim_in_tftp_is_reported_bad(health_server, tmp_path):
    """זה התרחיש: apt שדרג את המערכת, והעותק ב-TFTP נשאר מאחור."""
    r = _shim(health_server, tmp_path, b"new-after-sbat-revocation", b"old")
    assert r["state"] == "bad"
    assert "הריצו את המתקין" in r["detail"]


def test_a_shim_that_cannot_be_read_is_unknown_and_not_ok(health_server, tmp_path):
    """‏"לא הצלחנו לבדוק" אינו "בדקנו, תואם" — עיקרון 5.

    אם חוסר-קריאה היה מחזיר `ok`, שרת בלי shim-signed מותקן היה
    מדווח שהשרשרת עדכנית. זה בדיוק הכשל שהבדיקה נועדה למנוע.
    """
    r = _shim(health_server, tmp_path, None, b"whatever")
    assert r["state"] == "unknown"
    assert r["state"] != "ok"
