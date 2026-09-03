"""DHCP לכל כרטיס רשת — ההגדרה המסוכנת ביותר במערכת (אפיון סעיף 24).

הבדיקות כאן הן על שכבות הבטיחות לפני שהן על התכונה: כבוי כברירת מחדל,
אישור בשם הממשק, זיהוי DHCP קיים, ואישור נוסף ל-trunk. הקובץ שנכתב
ל-dnsmasq נבדק כטקסט, וההחלה עצמה (כתיבה + restart) מוחלפת ב-hook.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import dhcp
from server.dhcp import InterfaceConfig, render, render_proxy, validate

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


GOOD = dict(
    enabled=True, range_start="10.99.9.50", range_end="10.99.9.200",
    netmask="255.255.255.0", gateway="10.99.9.1", dns=["10.99.0.5"],
    lease="12h", server_ip="10.99.9.10",
)


# --- הלוגיקה הטהורה ----------------------------------------------------------


def test_a_disabled_interface_needs_no_fields():
    validate(InterfaceConfig("eth0"))


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"range_start": "10.99.9.300"}, "תחילת הטווח"),
        ({"range_end": "10.99.9.20"}, "גדולה מסופו"),
        ({"range_end": "10.99.10.200"}, "מחוץ לרשת"),
        ({"server_ip": "10.99.9.100"}, "בתוך הטווח"),
        ({"server_ip": "10.99.8.10"}, "אינה ברשת"),
        ({"gateway": "10.99.8.1"}, "השער"),
        ({"lease": "soon"}, "חכירה"),
    ],
)
def test_bad_configs_are_refused_in_hebrew(change, fragment):
    with pytest.raises(ValueError) as err:
        validate(InterfaceConfig("eth0", **{**GOOD, **change}))
    assert fragment in str(err.value)


MIXED = [
    InterfaceConfig("eth0", **GOOD),
    InterfaceConfig("eth1.101", proxy=True, server_ip="10.99.101.10"),
    InterfaceConfig("eth2"),                           # כבוי — לא מופיע
]


def test_render_puts_each_interface_under_its_own_tag():
    text = render(MIXED)
    assert "interface=eth0" in text
    assert "interface=eth2" not in text
    assert "dhcp-range=set:if-eth0,10.99.9.50,10.99.9.200,255.255.255.0,12h" in text
    assert "option:router,10.99.9.1" in text
    assert "dhcp-boot=tag:if-eth0,tag:efi-x86_64,bootx64.efi,,10.99.9.10" in text
    assert "bind-interfaces" in text


def test_the_main_instance_never_touches_a_proxy_interface():
    """‏#36: ממשק ב-proxy מקפיא את dnsmasq 2.91 על בקשת PXE:4011. הוא רץ
    באינסטנס נפרד, ולכן אסור שיופיע בקובץ הראשי בשום צורה — לא כטווח,
    לא כ-pxe-service, ואפילו לא כ-interface להגשת TFTP: שני התהליכים
    היו נלחמים על אותם פורטים. except-interface מוציא אותו מפורשות."""
    text = render(MIXED)
    live = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert not any("proxy" in ln for ln in live)
    assert not any("pxe-service" in ln for ln in live)
    assert not any(ln == "interface=eth1.101" for ln in live)
    assert "except-interface=eth1.101" in live


def test_a_proxy_only_setup_still_leaves_the_main_instance_serving_tftp():
    """כשאין DHCP מלא בכלל, האינסטנס הראשי נשאר TFTP על כל הכרטיסים
    חוץ מזה של ה-proxy — אין לו טווח, אז הוא לא מחלק כלום."""
    text = render([InterfaceConfig("eth1.101", proxy=True, server_ip="10.99.101.10")])
    assert "bind-interfaces" in text and "except-interface=eth1.101" in text
    assert "dhcp-range" not in text


def test_the_proxy_file_is_a_standalone_instance():
    """קובץ ה-proxy אינו ב-/etc/dnsmasq.d — הוא נטען ביחידה משלו, ולכן
    חייב להביא איתו הכל: בלי DNS, ‏TFTP משלו, ו-leasefile נפרד כדי ששני
    התהליכים לא יכתבו לאותו קובץ חכירות."""
    text = render_proxy(MIXED)
    assert "port=0" in text and "bind-interfaces" in text
    assert "interface=eth1.101" in text
    assert "interface=eth0" not in text                # ה-DHCP המלא נשאר בראשי
    assert "enable-tftp" in text and "tftp-root=/srv/tftp" in text
    assert "dhcp-leasefile=" in text
    assert "dhcp-range=set:if-eth1.101,10.99.101.10,proxy" in text
    assert "option:router" not in text


def test_the_proxy_file_is_empty_when_no_interface_is_in_proxy_mode():
    text = render_proxy([InterfaceConfig("eth0", **GOOD), InterfaceConfig("eth2")])
    assert "interface=" not in text and "dhcp-range" not in text


def test_proxy_answers_legacy_bios_as_well_as_uefi():
    """‏#40: בענף ה-proxy הייתה תשובת PXE ל-UEFI בלבד. מחשבי השיכפול הם
    Legacy BIOS ‏(#38) — בלי שורת x86PC הם לא מקבלים תשובה כלל."""
    text = render_proxy(MIXED)
    assert 'pxe-service=tag:if-eth1.101,tag:bios,x86PC,"ImageCtl",grub/i386-pc/core.0' in text
    assert ('pxe-service=tag:if-eth1.101,tag:efi-x86_64,x86-64_EFI,"ImageCtl",bootx64.efi'
            in text)
    assert "dhcp-match=set:bios,option:client-arch,0" in text


def test_proxy_lets_dnsmasq_answer_with_its_own_address():
    """‏#37: ב-pxe-service אין שדה כתובת שרת — ואסור שיהיה. בלעדיו dnsmasq
    מגיש מה-TFTP שלו ושם ב-siaddr את כתובתו על הממשק שענה, וזו הכתובת
    שהתחנה באמת יכולה להגיע אליה. משם GRUB לוקח את net_default_server."""
    text = render_proxy([InterfaceConfig("eth1.101", proxy=True, server_ip="10.99.101.10")])
    lines = [ln for ln in text.splitlines() if ln.startswith("pxe-service=")]
    assert len(lines) == 2
    for line in lines:
        assert line.endswith(("bootx64.efi", "grub/i386-pc/core.0")), (
            f"נוספה כתובת שרת מפורשת ל-pxe-service, וזה מקבע וילן אחד: {line}"
        )


def test_render_splits_the_boot_loader_by_client_arch():
    """‏#38: מחשבי השיכפול הם Legacy BIOS — אופציה 93 בוחרת את הטוען.
    ‏BIOS ‏(arch 0) מקבל GRUB i386-pc, ‏UEFI ‏(7/9) את ה-shim החתום,
    באותה רשת. שניהם ממשיכים לאותו grub.cfg."""
    text = render([InterfaceConfig("eth0", **GOOD)])
    assert "dhcp-match=set:bios,option:client-arch,0" in text
    assert "dhcp-match=set:efi-x86_64,option:client-arch,7" in text
    assert "dhcp-boot=tag:if-eth0,tag:bios,grub/i386-pc/core.0,,10.99.9.10" in text
    assert "dhcp-boot=tag:if-eth0,tag:efi-x86_64,bootx64.efi,,10.99.9.10" in text
    # אין שורת boot חסרת-תג שתתפוס קושחות לא מזוהות — ברירת המחדל
    # למי שאינו מוכר היא כלום, לא טוען שגוי.
    assert "dhcp-boot=tag:if-eth0,bootx64.efi" not in text


def test_render_with_nothing_enabled_is_an_empty_comment():
    text = render([InterfaceConfig("eth0"), InterfaceConfig("eth1")])
    assert "interface=" not in text and "dhcp-range" not in text


def test_list_interfaces_reads_sysfs_and_skips_loopback(tmp_path):
    for name, state in (("lo", "unknown"), ("eth0", "up"), ("eth1", "down")):
        d = tmp_path / name
        d.mkdir()
        (d / "operstate").write_text(state)
        (d / "address").write_text("00:00:5e:07:1a:c4\n")
    found = dhcp.list_interfaces(tmp_path)
    assert [i["name"] for i in found] == ["eth0", "eth1"]
    assert found[0]["state"] == "up" and found[0]["mac"] == "00:00:5e:07:1a:c4"


def test_apply_reports_an_unwritable_path_instead_of_raising(tmp_path):
    # קובץ בשם התיקייה → הכתיבה נכשלת → הודעה, לא חריגה.
    blocker = tmp_path / "blocked"
    blocker.write_text("")
    assert dhcp.apply("x", blocker / "conf") is not None
    # כתיבה שנכשלת חוזרת לפני כל systemctl — בדיקות לא נוגעות במכונה.
    assert dhcp.apply_proxy("x", True, blocker / "conf") is not None


def test_a_probe_result_is_never_falsey():
    """‏#53: כל עוד "לא בדקנו" ו"בדקנו ושקט" הם שניהם falsey, `if found:`
    מקפל אותם לאחד — ומדליק DHCP על רשת המכללה כשהבדיקה רק נכשלה. תוצאה
    אמיתית תמיד מפילה `if` מקרי לצד החוסם."""
    assert dhcp.ProbeResult(False)
    assert dhcp.ProbeResult(True)
    assert dhcp.ProbeResult(False).checked is False
    assert dhcp.ProbeResult(False).servers == ()


def test_a_probe_that_cannot_open_the_socket_says_so_instead_of_reporting_silence():
    """בלי root, בלי SO_BINDTODEVICE (ווינדוס) או על ממשק שאינו קיים —
    הסוקט לא נפתח. התשובה היא "לא נבדק", לא "שקט"."""
    result = dhcp.probe_existing_dhcp("imagectl-no-such-nic0", timeout=0.1)
    assert result.checked is False and result.servers == ()


def test_the_proxy_conf_lives_outside_the_directory_dnsmasq_reads():
    """‏#36 כולו תלוי בזה: קובץ ב-/etc/dnsmasq.d נטען לאינסטנס הראשי,
    וההפרדה מתבטלת בשקט."""
    assert "/etc/dnsmasq.d" not in dhcp.PROXY_CONF
    assert dhcp.DEFAULT_CONF.startswith("/etc/dnsmasq.d")


# --- דרך הקונסולה ------------------------------------------------------------


@pytest.fixture()
def dhcp_server(tmp_path: Path, images_root: Path, clock):
    """שרת עם שני כרטיסים מזויפים, גלאי DHCP מזויף, והחלה שרק רושמת."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    fake = {
        "interfaces": [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00", "addresses": ["10.99.9.10/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01", "addresses": ["10.99.1.10/24"]},
        ],
        "existing": [],                 # מה ה-probe "רואה"
        "probe_checked": True,          # האם הבדיקה בכלל הצליחה לרוץ (#53)
        "applied": [],                  # מה נכתב לאינסטנס הראשי
        "proxy_applied": [],            # מה נכתב לאינסטנס ה-proxy: (טקסט, פעיל)
        "apply_error": None,
        "proxy_error": None,
        # ‏#36: הגרסה שה-API "רואה". אף בדיקה לא מריצה dnsmasq אמיתי,
        # ובברירת המחדל זו הגרסה של המעבדה — זו שהקפיאה שוחזרה בה.
        "dnsmasq_version": "Dnsmasq version 2.91  Copyright (c) 2000-2024\n",
    }
    hooks = {
        "interfaces": lambda: fake["interfaces"],
        "dnsmasq_version": lambda: fake["dnsmasq_version"],
        # ה-probe המזויף חייב להיות מסוגל להחזיר גם "לא הצלחתי לבדוק" —
        # זה המצב שלא היה כאן, ולכן #53 לא נתפס.
        "probe": lambda name: dhcp.ProbeResult(
            fake["probe_checked"], tuple(fake["existing"])),
        "apply": lambda text: (fake["applied"].append(text), fake["apply_error"])[1],
        "apply_proxy": lambda text, active: (
            fake["proxy_applied"].append((text, active)), fake["proxy_error"])[1],
    }
    app = create_app(tmp_path / "data", images_root, "http://10.99.12.10:8080",
                     now_fn=clock, dhcp_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy = TestClient(app)
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "ctx": app.state.ctx}


def test_a_nic_gets_a_description_and_can_be_forgotten(dhcp_server):
    """שורת כרטיס כמו כל שורה: תיאור חופשי (למשל וילן), והסרה שמאפסת."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]

    assert admin.put("/api/console/net/interfaces/eth0/description",
                     json={"description": "700"}).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth0"]["description"] == "700"
    assert rows["eth1"]["description"] == ""

    # מדליקים DHCP, ואז מסירים — הכרטיס חוזר לכבוי ו-dnsmasq מתעדכן.
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    applied_before = len(fake["applied"])
    assert admin.delete("/api/console/net/interfaces/eth0").json()["ok"] is True
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth0"]["enabled"] is False and rows["eth0"]["description"] == ""
    assert len(fake["applied"]) == applied_before + 1
    assert "interface=eth0" not in fake["applied"][-1]

    # שתי הפעולות — admin בלבד.
    deploy = dhcp_server["deploy"]
    assert deploy.put("/api/console/net/interfaces/eth0/description",
                      json={"description": "x"}).status_code == 403
    assert deploy.delete("/api/console/net/interfaces/eth0").status_code == 403


def test_a_vlan_subinterface_can_be_added_by_hand(dhcp_server):
    """כרטיס שעוד לא קיים במכונה נוסף ידנית, מופיע כבוי ומסומן חסר."""
    admin = dhcp_server["admin"]
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1.700", "description": "וילן 700"},
                      ).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    row = rows["eth1.700"]
    assert row["enabled"] is False and row["present"] is False
    assert row["description"] == "וילן 700"

    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1.700"}).status_code == 409
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "לא באנגלית"}).status_code == 400

    # כרטיס חי שעוד לא הוגדר — "הוספה" קולטת אותו: תצורה כבויה + תיאור.
    assert admin.post("/api/console/net/interfaces",
                      json={"name": "eth1", "description": "רשת המכללה"},
                      ).status_code == 200
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth1"]["present"] is True
    assert rows["eth1"]["description"] == "רשת המכללה"
    assert dhcp_server["deploy"].post("/api/console/net/interfaces",
                                      json={"name": "eth9"}).status_code == 403


def test_every_interface_starts_off(dhcp_server):
    rows = dhcp_server["admin"].get("/api/console/net/interfaces").json()
    assert [r["name"] for r in rows] == ["eth0", "eth1"]
    assert all(r["enabled"] is False and r["proxy"] is False for r in rows)
    assert rows[0]["addresses"] == ["10.99.9.10/24"] and rows[0]["state"] == "up"


def test_turning_on_needs_the_interface_name_typed(dhcp_server):
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json=GOOD)
    assert r.status_code == 409 and "eth0" in r.json()["detail"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 409
    assert dhcp_server["fake"]["applied"] == []     # שום דבר לא נכתב

    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    assert "interface=eth0" in dhcp_server["fake"]["applied"][-1]
    rows = admin.get("/api/console/net/interfaces").json()
    assert rows[0]["enabled"] is True and rows[0]["range_start"] == "10.99.9.50"


def test_an_existing_dhcp_server_blocks_the_switch(dhcp_server):
    """הסיכון מנספח ב': DHCP שני על רשת שכבר יש בה אחד."""
    dhcp_server["fake"]["existing"] = ["10.99.9.1"]
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409 and "10.99.9.1" in r.json()["detail"]
    assert dhcp_server["fake"]["applied"] == []
    # מי שיודע מה הוא עושה יכול לעקוף — במפורש.
    r = admin.put("/api/console/net/interfaces/eth0",
                  json={**GOOD, "confirm": "eth0", "ignore_existing": True})
    assert r.status_code == 200


def test_a_check_that_could_not_run_blocks_the_switch_too(dhcp_server):
    """‏#53 — הבאג עצמו: `None` (לא הצלחנו לבדוק) נספר כ"נקי".

    ‏None חוזר בדיוק כשאין הרשאות או כשפורט 68 תפוס — המצב הרגיל של
    כרטיס trunk שמקבל כתובת מרשת המכללה. כלומר: על הכרטיס המסוכן ביותר,
    שכבת הבטיחות שאמורה לעצור פשוט לא רצה, ודיווחה "נקי".
    """
    dhcp_server["fake"]["probe_checked"] = False
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409, "בדיקה שלא רצה פתחה את ההדלקה"
    assert dhcp_server["fake"]["applied"] == []      # שום דבר לא הגיע ל-dnsmasq

    # ההודעה חייבת להבדיל: מפעיל שיקרא "נמצא שרת DHCP" יחפש שרת שאינו קיים.
    detail = r.json()["detail"]
    assert "לא ניתן לבדוק" in detail
    assert "נמצא שרת DHCP פעיל" not in detail
    assert "ignore_existing" in detail               # מה עושים מכאן


def test_an_unrunnable_check_is_still_overridable_on_purpose(dhcp_server):
    """המעקף המפורש נשאר מעקף — מי שיודע שהכרטיס מבודד מדליק בכל זאת,
    ולא נתקע בלי דרך קדימה."""
    dhcp_server["fake"]["probe_checked"] = False
    r = dhcp_server["admin"].put(
        "/api/console/net/interfaces/eth0",
        json={**GOOD, "confirm": "eth0", "ignore_existing": True})
    assert r.status_code == 200, r.text
    assert "interface=eth0" in dhcp_server["fake"]["applied"][-1]


def test_the_probe_endpoint_tells_the_three_states_apart(dhcp_server):
    """אותה הבחנה גם בדיווח לקונסולה: "לא נבדק" אינו "נקי"."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.get("/api/console/net/interfaces/eth0/probe").json() == {
        "interface": "eth0", "checked": True, "servers": []}

    fake["existing"] = ["10.99.9.1"]
    assert admin.get("/api/console/net/interfaces/eth0/probe").json()["servers"] \
        == ["10.99.9.1"]

    fake["existing"], fake["probe_checked"] = [], False
    body = admin.get("/api/console/net/interfaces/eth0/probe").json()
    assert body["checked"] is False and body["servers"] == []


def test_the_trunk_needs_a_second_confirmation(dhcp_server):
    admin = dhcp_server["admin"]
    # סימון trunk לבד (כבוי) לא דורש אישור — הוא רק מסמן.
    r = admin.put("/api/console/net/interfaces/eth1", json={"trunk": True})
    assert r.status_code == 200
    assert admin.get("/api/console/net/interfaces").json()[1]["trunk"] is True

    body = {**GOOD, "range_start": "10.99.1.50", "range_end": "10.99.1.200",
            "gateway": "10.99.1.1", "server_ip": "10.99.1.10", "confirm": "eth1"}
    r = admin.put("/api/console/net/interfaces/eth1", json=body)
    assert r.status_code == 409 and "confirm_trunk" in r.json()["detail"]
    r = admin.put("/api/console/net/interfaces/eth1", json={**body, "confirm_trunk": True})
    assert r.status_code == 200


def test_proxy_mode_does_not_hand_out_addresses(dhcp_server):
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    r = admin.put("/api/console/net/interfaces/eth1",
                  json={"proxy": True, "server_ip": "10.99.1.10",
                        "confirm": "eth1", "confirm_proxy_broken": True})
    assert r.status_code == 200, r.text
    text, active = fake["proxy_applied"][-1]
    assert active is True
    assert "dhcp-range=set:if-eth1,10.99.1.10,proxy" in text
    assert "option:router" not in text
    preview = admin.get("/api/console/net/dnsmasq").json()
    assert preview["text"] == fake["applied"][-1] and preview["proxy_text"] == text
    assert preview["proxy_path"] != preview["path"]


def test_proxy_runs_in_its_own_instance_so_a_freeze_spares_the_main_dhcp(dhcp_server):
    """‏#36: ה-DHCP של וילן ההפצה וה-proxy הם שני תהליכים. הקובץ הראשי
    לא מכיל את ממשק ה-proxy, וכיבוי ה-proxy עוצר את היחידה שלו."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": True, "server_ip": "10.99.1.10",
                           "confirm": "eth1",
                           "confirm_proxy_broken": True}).status_code == 200

    main_text = fake["applied"][-1]
    assert "dhcp-range=set:if-eth0," in main_text      # ההפצה ממשיכה לחלק
    assert "except-interface=eth1" in main_text
    assert "proxy" not in "".join(
        ln for ln in main_text.splitlines() if not ln.startswith("#"))
    assert "interface=eth1" in fake["proxy_applied"][-1][0]

    # כיבוי ה-proxy: היחידה נעצרת, ההפצה לא זזה.
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": False}).status_code == 200
    assert fake["proxy_applied"][-1][1] is False
    assert "dhcp-range=set:if-eth0," in fake["applied"][-1]


def test_a_proxy_failure_is_reported_next_to_the_main_one(dhcp_server):
    dhcp_server["fake"]["proxy_error"] = "imagectl-proxy לא הגיב ל-restart"
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth1",
                                 json={"proxy": True, "server_ip": "10.99.1.10",
                                       "confirm": "eth1",
                                       "confirm_proxy_broken": True})
    assert r.json()["ok"] is False and "imagectl-proxy" in r.json()["apply_error"]


def test_bad_values_never_reach_dnsmasq(dhcp_server):
    admin = dhcp_server["admin"]
    r = admin.put("/api/console/net/interfaces/eth0",
                  json={**GOOD, "range_end": "10.99.9.20", "confirm": "eth0"})
    assert r.status_code == 400 and "גדולה" in r.json()["detail"]
    assert dhcp_server["fake"]["applied"] == []


def test_turning_off_needs_no_confirmation_and_rewrites_the_file(dhcp_server):
    admin = dhcp_server["admin"]
    admin.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    r = admin.put("/api/console/net/interfaces/eth0", json={"enabled": False})
    assert r.status_code == 200
    assert "interface=eth0" not in dhcp_server["fake"]["applied"][-1]


def test_apply_failure_is_reported_not_hidden(dhcp_server):
    dhcp_server["fake"]["apply_error"] = "dnsmasq לא עלה: bad option"
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth0",
                                 json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 200
    assert r.json()["ok"] is False and "dnsmasq" in r.json()["apply_error"]
    events = [row["event"] for row in dhcp_server["ctx"].conn.execute(
        "SELECT event FROM journal").fetchall()]
    assert "dhcp_apply_failed" in events


def test_dhcp_is_admin_only(dhcp_server):
    deploy = dhcp_server["deploy"]
    assert deploy.put("/api/console/net/interfaces/eth0",
                      json={**GOOD, "confirm": "eth0"}).status_code == 403
    assert deploy.get("/api/console/net/interfaces/eth0/probe").status_code == 403
    assert deploy.get("/api/console/net/dnsmasq").status_code == 403


def test_an_unknown_interface_is_refused(dhcp_server):
    r = dhcp_server["admin"].put("/api/console/net/interfaces/wlan9", json={"trunk": True})
    assert r.status_code == 404
