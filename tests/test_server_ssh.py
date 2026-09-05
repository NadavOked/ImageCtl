"""מתגי ה-SSH והחיווי שלהם (‏#83).

מה שנבדק כאן הוא לא "האם ההגדרה נשמרה" אלא **האם המסך אומר את האמת**.
מתג שנכשל ומצייר "כבוי" הוא המצב המסוכן מכולם: מפעיל שמאמין שסגר, ולא
סגר. לכן רוב הקובץ הזה הוא תרחישי כשל — החלה שלא תפסה, טבלת סוקטים
שלא נקראה, תפריט שלא ענה — ובכולם התשובה הנכונה היא "לא אומת", לא
"בסדר".

‏**אף בדיקה כאן אינה נוגעת ב-sshd אמיתי.** הכתיבה ל-drop-in וה-reload
מוזרקים, בדיוק כמו `dhcp_hooks`: הבדיקה רואה את הטקסט שהיה נכתב, ולא
מריצה systemctl.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from server import ssh_switch

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

#: טבלת סוקטים אמיתית, כפי שהקרנל כותב אותה. ‏0100007F = 127.0.0.1,
#: ‏00000000 = 0.0.0.0, ‏0016 = 22, ‏0A = TCP_LISTEN.
PROC_TCP_HEADER = ("  sl  local_address rem_address   st tx_queue rx_queue tr "
                   "tm->when retrnsmt   uid  timeout inode")
PROC_WILDCARD = PROC_TCP_HEADER + """
   0: 00000000:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1
   1: 0A2C000A:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 2
"""
PROC_ONE_NIC = PROC_TCP_HEADER + """
   0: 0A2C000A:0016 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 1
"""
PROC_ONLY_ESTABLISHED = PROC_TCP_HEADER + """
   0: 0A2C000A:0016 0A2C0001:CAFE 01 00000000:00000000 00:00000000 00000000 0 0 1
"""


# --- הקריאה עצמה: שלושה מצבים, לא שניים -------------------------------------


def test_an_unreadable_socket_table_is_not_an_empty_one():
    """‏"" מקובץ שלא נקרא אינו "אף אחד לא מאזין". זה בדיוק ההבדל בין
    ‏http=000 לבין 200, והוא הבאג שהמשימה הזאת קיימת כדי למנוע."""
    assert ssh_switch.parse_proc_net_tcp("") == (False, [])
    assert ssh_switch.parse_proc_net_tcp("garbage\nmore garbage") == (False, [])
    checked, found = ssh_switch.parse_proc_net_tcp(PROC_TCP_HEADER)
    assert (checked, found) == (True, [])


def test_a_missing_proc_is_unknown_and_says_why(tmp_path: Path):
    result = ssh_switch.read_listeners(tmp_path / "nowhere")
    assert result.checked is False
    assert result.addresses == ()
    assert result.reason                      # לא נשאר בשקט


def test_the_socket_table_is_read_as_the_kernel_writes_it():
    checked, found = ssh_switch.parse_proc_net_tcp(PROC_WILDCARD)
    assert checked is True
    assert found == ["0.0.0.0"]               # ‏8080 אינו 22
    assert ssh_switch.parse_proc_net_tcp(PROC_ONE_NIC)[1] == ["10.0.44.10"]
    # חיבור פתוח אינו מאזין. ‏0A בלבד.
    assert ssh_switch.parse_proc_net_tcp(PROC_ONLY_ESTABLISHED)[1] == []


def test_reading_ipv6_too(tmp_path: Path):
    (tmp_path / "tcp").write_text(PROC_TCP_HEADER + "\n")
    (tmp_path / "tcp6").write_text(
        PROC_TCP_HEADER + "\n   0: 00000000000000000000000000000000:0016 "
        "00000000000000000000000000000000:0000 0A 00000000:00000000 "
        "00:00000000 00000000 0 0 1\n")
    result = ssh_switch.read_listeners(tmp_path)
    assert result.checked is True
    assert result.wildcard is True


NICS = [
    {"name": "eth0", "state": "up", "mac": "aa", "addresses": ["10.0.44.10/24"]},
    {"name": "eth1", "state": "up", "mac": "bb", "addresses": ["10.60.0.10/24"]},
]


def test_a_wildcard_listener_means_every_interface_including_the_classrooms():
    listeners = ssh_switch.Listeners(True, ("0.0.0.0",), True)
    assert ssh_switch.exposure(listeners, NICS) == {"eth0": True, "eth1": True}


def test_an_unchecked_table_claims_nothing():
    """אין ראיה — אין טענה. מפה ריקה, ולא "הכל סגור"."""
    assert ssh_switch.exposure(ssh_switch.Listeners(False), NICS) == {}


def test_a_conf_without_addresses_does_not_fall_back_to_all_interfaces():
    """‏sshd בלי ‏ListenAddress מאזין על הכל. "סגור" חייב להיאמר במפורש."""
    text = ssh_switch.render_sshd_conf([])
    assert "ListenAddress 127.0.0.1" in text
    assert text.count("ListenAddress") == 1


# --- ההגדרה: ברירת המחדל סגורה, וכשל סוגר -----------------------------------


class _Corrupt:
    """חיבור DB שכל קריאה ממנו נכשלת."""

    def execute(self, *args):
        raise RuntimeError("disk I/O error")


def test_a_setting_that_cannot_be_read_is_off(monkeypatch):
    monkeypatch.setattr(ssh_switch, "get_setting",
                        lambda conn, key: (_ for _ in ()).throw(RuntimeError()))
    assert ssh_switch.stations_enabled(object()) is False
    monkeypatch.setattr(ssh_switch, "get_setting", lambda conn, key: "not json")
    assert ssh_switch.stations_enabled(object()) is False
    monkeypatch.setattr(ssh_switch, "get_setting", lambda conn, key: '{"enabled": "yes"}')
    assert ssh_switch.stations_enabled(object()) is False
    assert ssh_switch.enabled_interfaces(_Corrupt()) == []


def test_the_debug_flag_is_not_an_operator_extra_any_more():
    extra = ("console=ttyS0,115200", "imagectl.debug=1")
    assert ssh_switch.station_cmdline(extra, False) == ("console=ttyS0,115200",)
    assert ssh_switch.station_cmdline(extra, True) == (
        "console=ttyS0,115200", "imagectl.debug=1")
    # ולא מוכפל כשהמפעיל כבר העביר אותו.
    assert ssh_switch.station_cmdline(extra, True).count("imagectl.debug=1") == 1


# --- השרת המלא, עם כל מגע במכונה מוזרק ---------------------------------------


@pytest.fixture()
def ssh_server(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server import users
    from server.app import create_app

    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")

    fake = {
        "listeners": ssh_switch.Listeners(True, ("0.0.0.0",), True),
        "interfaces": [dict(n) for n in NICS],
        # מה שהשרת "מגיש" לתפריט האתחול, כפי שהחיווי קורא אותו בחזרה.
        "menu": "linux /boot/vmlinuz ip=dhcp imagectl.server=x console=tty0",
        # ‏MAC לא רשום מקבל תפריט דיסק-מקומי, בלי שורת קרנל בכלל —
        # כמו על שרת אמיתי. אין מה לחפש בו.
        "menu_unregistered": 'set default=local\nmenuentry "local" {}',
        "menu_status": 200,
        "applied": [],
        "apply_error": None,
        # האם ה-sshd המדומה באמת מחיל את מה שנכתב לו. False = ההחלה
        # "הצליחה" ושום דבר לא השתנה — התרחיש שהמשימה נגדו.
        "apply_takes_effect": True,
    }

    def apply_sshd(text: str):
        fake["applied"].append(text)
        if fake["apply_takes_effect"]:
            addresses = tuple(sorted(
                line.split()[1] for line in text.splitlines()
                if line.startswith("ListenAddress")))
            fake["listeners"] = ssh_switch.Listeners(True, addresses, False)
        return fake["apply_error"]

    hooks = {
        # הבדיקות של מסך הבריאות עצמו — מספיק שיענו משהו.
        "ss": lambda: "",
        "unit_active": lambda name: "active",
        "http_get": lambda url: 200,
        "tftp_root": lambda: tftp,
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        # מתגי ה-SSH:
        "listeners": lambda: fake["listeners"],
        "apply_sshd": apply_sshd,
        "settle": lambda: None,
        "http_text": lambda url: (
            fake["menu_status"],
            fake["menu_unregistered"] if "mac=00:00:00:00:00:00" in url
            else fake["menu"]),
    }
    app = create_app(tmp_path / "data", images_root, "http://10.0.44.10:8080",
                     now_fn=clock, health_hooks=hooks,
                     extra_cmdline=("console=ttyS0,115200",))
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "app": app,
            "anon": TestClient(app)}


def by_id(rows):
    return {r["id"]: r for r in rows}


def journal_events(server):
    """הגולמי, מה-DB: ‏API היומן מתרגם לעברית, וכאן בודקים מה נרשם."""
    rows = server["app"].state.ctx.conn.execute(
        "SELECT event, detail, user FROM journal").fetchall()
    return [(r["event"], r["detail"], r["user"]) for r in rows]


# --- ברירת המחדל -------------------------------------------------------------


def test_both_doors_are_closed_by_default(ssh_server):
    state = ssh_server["admin"].get("/api/console/ssh").json()
    assert state["stations"]["enabled"] is False
    assert [n["enabled"] for n in state["interfaces"]] == [False, False]


def test_the_boot_menu_carries_the_debug_flag_only_while_the_switch_is_on(ssh_server):
    """הבדיקה שסוגרת את הדרישה הראשונה: המתג, ולא משתנה הסביבה, הוא
    מה שמכניס את `imagectl.debug` לשורת הקרנל."""
    setup_classroom(ssh_server)
    anon = ssh_server["anon"]
    assert "imagectl.debug" not in anon.get("/boot/menu?mac=b4:2e:99:07:1a:c4").text

    ssh_server["admin"].put("/api/console/ssh/stations",
                            json={"enabled": True, "confirm": "imagectl.debug"})
    text = anon.get("/boot/menu?mac=b4:2e:99:07:1a:c4").text
    assert "imagectl.debug=1" in text
    assert "console=ttyS0,115200" in text          # תוספות המפעיל נשארו

    ssh_server["admin"].put("/api/console/ssh/stations", json={"enabled": False})
    assert "imagectl.debug" not in anon.get("/boot/menu?mac=b4:2e:99:07:1a:c4").text


# --- עיקרון 7: הקלדת שם בכיוון המסוכן ----------------------------------------


def test_opening_the_station_door_requires_typing_the_word(ssh_server):
    setup_classroom(ssh_server)
    response = ssh_server["admin"].put("/api/console/ssh/stations",
                                       json={"enabled": True})
    assert response.status_code == 409
    assert "imagectl.debug" in response.json()["detail"]
    # ולא נפתח חצי: התפריט נשאר נקי.
    assert "imagectl.debug" not in ssh_server["anon"].get(
        "/boot/menu?mac=b4:2e:99:07:1a:c4").text


def test_closing_needs_no_typing_because_closing_is_the_safe_direction(ssh_server):
    ssh_server["admin"].put("/api/console/ssh/stations",
                            json={"enabled": True, "confirm": "imagectl.debug"})
    assert ssh_server["admin"].put(
        "/api/console/ssh/stations", json={"enabled": False}).status_code == 200


def test_opening_an_interface_requires_typing_its_name(ssh_server):
    response = ssh_server["admin"].put("/api/console/ssh/interfaces/eth1",
                                       json={"enabled": True})
    assert response.status_code == 409
    assert "eth1" in response.json()["detail"]
    assert ssh_server["fake"]["applied"] == []      # שום דבר לא הוחל


def test_an_interface_without_an_address_cannot_be_opened(ssh_server):
    ssh_server["fake"]["interfaces"][1]["addresses"] = []
    response = ssh_server["admin"].put("/api/console/ssh/interfaces/eth1",
                                       json={"enabled": True, "confirm": "eth1"})
    assert response.status_code == 409


def test_an_unknown_interface_is_404(ssh_server):
    assert ssh_server["admin"].put("/api/console/ssh/interfaces/eth9",
                                   json={"enabled": False}).status_code == 404


def test_closing_the_last_open_door_requires_typing_its_name(ssh_server):
    admin = ssh_server["admin"]
    admin.put("/api/console/ssh/interfaces/eth0",
              json={"enabled": True, "confirm": "eth0"})
    admin.put("/api/console/ssh/interfaces/eth1",
              json={"enabled": True, "confirm": "eth1"})
    # שתיים פתוחות — סגירת אחת מהן היא לחיצה אחת.
    assert admin.put("/api/console/ssh/interfaces/eth1",
                     json={"enabled": False}).status_code == 200
    # והאחרונה כבר לא: אחריה אין SSH לשרת בכלל.
    response = admin.put("/api/console/ssh/interfaces/eth0", json={"enabled": False})
    assert response.status_code == 409
    assert "eth0" in response.json()["detail"]
    assert admin.put("/api/console/ssh/interfaces/eth0",
                     json={"enabled": False, "confirm": "eth0"}).status_code == 200


# --- הלב: הצלחה נקבעת לפי קריאה חוזרת ----------------------------------------


def test_opening_one_interface_writes_only_its_address_and_reads_it_back(ssh_server):
    result = ssh_server["admin"].put(
        "/api/console/ssh/interfaces/eth0",
        json={"enabled": True, "confirm": "eth0"}).json()
    assert result["ok"] is True and result["verified"] is True
    conf = ssh_server["fake"]["applied"][-1]
    assert "ListenAddress 10.0.44.10" in conf
    assert "10.60.0.10" not in conf                 # וילן הכיתות לא נפתח
    nics = {n["name"]: n for n in result["state"]["interfaces"]}
    assert nics["eth0"]["listening"] is True
    assert nics["eth1"]["listening"] is False


def test_closing_everything_leaves_loopback_only_and_is_verified(ssh_server):
    admin = ssh_server["admin"]
    admin.put("/api/console/ssh/interfaces/eth0",
              json={"enabled": True, "confirm": "eth0"})
    result = admin.put("/api/console/ssh/interfaces/eth0",
                       json={"enabled": False, "confirm": "eth0"}).json()
    assert result["verified"] is True
    assert "ListenAddress 127.0.0.1" in ssh_server["fake"]["applied"][-1]
    rows = by_id(admin.get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "ok"


def test_a_switch_that_did_not_take_effect_reports_failure_not_success(ssh_server):
    """**הבדיקה המרכזית.** ה-sshd המדומה מקבל את הקובץ, מחזיר הצלחה,
    וממשיך להאזין על 0.0.0.0 — בדיוק כמו ‏sshd_config ראשי עם
    ‏ListenAddress משלו, או יחידה בלי ‏ReadWritePaths. אסור שהמסך יראה
    "סגור"."""
    ssh_server["fake"]["apply_takes_effect"] = False
    admin = ssh_server["admin"]
    result = admin.put("/api/console/ssh/interfaces/eth0",
                       json={"enabled": True, "confirm": "eth0"}).json()
    assert result["apply_error"] is None            # הפקודה "הצליחה"
    assert result["verified"] is False              # והדלת לא זזה
    assert result["ok"] is False
    assert ("ssh_unverified" in [e for e, _d, _u in journal_events(ssh_server)])
    rows = by_id(admin.get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "bad"


def test_an_apply_that_failed_is_reported_as_failed(ssh_server):
    ssh_server["fake"]["apply_error"] = "אין הרשאה לכתוב ל-sshd_config.d"
    ssh_server["fake"]["apply_takes_effect"] = False
    result = ssh_server["admin"].put(
        "/api/console/ssh/interfaces/eth0",
        json={"enabled": True, "confirm": "eth0"}).json()
    assert result["ok"] is False
    assert "הרשאה" in result["apply_error"]


def test_a_station_switch_the_menu_did_not_follow_is_not_a_success(ssh_server):
    """אותו כלל בדלת השנייה: הדגל לא הופיע בתפריט = לא אומת."""
    setup_classroom(ssh_server)
    ssh_server["fake"]["menu"] = "linux /boot/vmlinuz ip=dhcp imagectl.server=x"
    result = ssh_server["admin"].put(
        "/api/console/ssh/stations",
        json={"enabled": True, "confirm": "imagectl.debug"}).json()
    assert result["ok"] is False and result["verified"] is False


def test_a_station_switch_is_verified_against_the_served_menu(ssh_server):
    setup_classroom(ssh_server)
    ssh_server["fake"]["menu"] = ("linux /boot/vmlinuz imagectl.server=x "
                                 "imagectl.debug=1")
    result = ssh_server["admin"].put(
        "/api/console/ssh/stations",
        json={"enabled": True, "confirm": "imagectl.debug"}).json()
    assert result["ok"] is True and result["verified"] is True


# --- מסך הבריאות: מה שלא נבדק אינו ירוק --------------------------------------


def test_a_wide_open_sshd_is_red_and_says_so(ssh_server):
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "bad"
    assert "0.0.0.0" in rows["ssh_server"]["detail"]


def test_an_unreadable_socket_table_is_red_not_green(ssh_server):
    ssh_server["fake"]["listeners"] = ssh_switch.Listeners(
        False, reason="tcp: Permission denied")
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "bad"
    assert "Permission denied" in rows["ssh_server"]["detail"]


def test_a_menu_that_did_not_answer_is_red_not_green(ssh_server):
    ssh_server["fake"]["menu_status"] = None
    ssh_server["fake"]["menu"] = ""
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_stations"]["state"] == "bad"


def test_the_health_screen_names_the_interface_that_is_open(ssh_server):
    admin = ssh_server["admin"]
    admin.put("/api/console/ssh/interfaces/eth1",
              json={"enabled": True, "confirm": "eth1"})
    rows = by_id(admin.get("/api/console/health").json())
    assert rows["ssh_server"]["state"] == "warn"
    assert "eth1" in rows["ssh_server"]["detail"]
    assert "10.60.0.10" in rows["ssh_server"]["detail"]


def test_a_local_only_menu_is_not_evidence_that_the_door_is_shut(ssh_server):
    """המלכודת שנתפסה מול השרת החי: ‏MAC לא רשום — וגם מכונה שנחסמה
    אחרי כישלונות חוזרים (#75) — מקבל תפריט "עלה מהדיסק המקומי", בלי
    שורת קרנל בכלל. חיפוש הדגל שם היה מחזיר "סגור" לנצח, גם כשהדלת
    פתוחה לרווחה. ‏אין שורת קרנל = אין ראיה."""
    ssh_server["fake"]["menu"] = "set default=local\nmenuentry \"local\" {}"
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_stations"]["state"] == "bad"
    assert "דיסק-מקומי" in rows["ssh_stations"]["detail"]


def test_the_menu_is_asked_for_a_machine_the_server_knows(ssh_server):
    """הראיה נמשכת עבור מכונה רשומה, ושמה נאמר במסך — כדי שאפשר יהיה
    לבדוק ידנית בדיוק את מה שהשרת בדק."""
    setup_classroom(ssh_server)
    state = ssh_server["admin"].get("/api/console/ssh").json()
    assert state["stations"]["evidence"] == "closed"
    assert "b4:2e:99:07:1a:c4" in state["stations"]["detail"]


def test_with_no_machine_registered_the_station_door_is_unknown(ssh_server):
    """שרת טרי: אין מכונה רשומה לבקש עבורה תפריט, ולכן אין ראיה —
    ואדום, לא ירוק. "לא ידוע" אינו "סגור"."""
    state = ssh_server["admin"].get("/api/console/ssh").json()
    assert state["stations"]["evidence"] == "unknown"
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_stations"]["state"] == "bad"


def test_an_open_station_door_is_a_warning_not_a_quiet_line(ssh_server):
    setup_classroom(ssh_server)
    ssh_server["fake"]["menu"] = ("linux /boot/vmlinuz imagectl.server=x "
                                 "imagectl.debug=1")
    rows = by_id(ssh_server["admin"].get("/api/console/health").json())
    assert rows["ssh_stations"]["state"] == "warn"


# --- הרשאות ויומן ------------------------------------------------------------


def test_the_ssh_switch_is_management_so_deploy_gets_403(ssh_server):
    deploy = ssh_server["deploy"]
    assert deploy.get("/api/console/ssh").status_code == 403
    assert deploy.put("/api/console/ssh/stations",
                      json={"enabled": True, "confirm": "imagectl.debug"}
                      ).status_code == 403
    assert deploy.put("/api/console/ssh/interfaces/eth0",
                      json={"enabled": True, "confirm": "eth0"}).status_code == 403
    assert ssh_server["fake"]["applied"] == []


#: הנתיבים שמתג ה-SSH מגיש. רשומים כאן במפורש כדי שהליכה על עץ
#: הניתובים שהחמיצה אותם תיפול על השם החסר, ולא תדווח "נבדקו 0
#: נתיבים, הכל תקין" — זה בדיוק מה שקרה ב-#113.
SSH_PATHS = {"/api/console/ssh",
             "/api/console/ssh/stations",
             "/api/console/ssh/interfaces/{name}"}


def walk_routes(routes, prefix=""):
    """‏(נתיב מלא, ‏endpoint) לכל ניתוב באפליקציה — בכל גרסת FastAPI.

    עד ‏0.11x ‏`include_router` **שיטח** את הניתובים: ‏`app.routes` הכיל
    ‏`APIRoute` עם הנתיב המלא, וסינון לפי `r.path` הספיק. מ-0.13x ואילך
    הוא מוסיף במקומם עטיפה עצלה (‏`_IncludedRouter`) שמחזיקה את הראוטר
    המקורי ואת הקידומת — **ובלי `path` בכלל**. הסינון הישן החזיר `[]`
    בכל שרת, והבדיקה הפסיקה לבדוק מה שהיא נכתבה לבדוק (‏#113).

    ‏`getattr` ולא `isinstance`: העטיפה היא טיפוס פרטי של FastAPI, ואין
    טעם לקשור את הבדיקה לשם שלו. ‏`Mount` ‏(`routes` + `path`) נסרק גם
    הוא, וכך גם ראוטר מקונן בתוך ראוטר — כמו מתגי ה-SSH בתוך `health`.
    """
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:                  # FastAPI ≥ 0.13x
            context = getattr(route, "include_context", None)
            yield from walk_routes(inner.routes,
                                   prefix + getattr(context, "prefix", ""))
            continue
        path = getattr(route, "path", None)
        endpoint = getattr(route, "endpoint", None)
        if path is not None and endpoint is not None:
            yield prefix + path, endpoint
        nested = getattr(route, "routes", None)
        if nested:                             # Mount / Host
            yield from walk_routes(nested, prefix + (path or ""))


def test_the_switch_endpoints_never_block_the_event_loop(ssh_server):
    """נתפס מול שרת חי, ולא בבדיקה: הראיה של דלת התחנות נמשכת ב-HTTP
    **מהשרת עצמו**. ‏endpoint שהוא `async def` חוסם את הלולאה שאמורה
    לענות לאותה בקשה, ואז כל שינוי מסתיים ב"לא אומת" בפקיעת זמן — על
    כל שרת אמיתי, בזמן שהבדיקות עם hook מוזרק עוברות בירוק. ‏FastAPI
    מריץ `def` רגיל במאגר תהליכונים, והלולאה נשארת פנויה."""
    import inspect

    found = {path: endpoint for path, endpoint in
             walk_routes(ssh_server["app"].routes)
             if path.startswith("/api/console/ssh")}
    # קודם שההליכה עצמה עובדת, ורק אחר כך מה שהיא מצאה.
    assert set(found) == SSH_PATHS, (
        f"נתיבי ה-SSH שנמצאו אינם מה שהשרת מגיש: {sorted(found)}")
    for path, endpoint in found.items():
        assert not inspect.iscoroutinefunction(endpoint), path


def test_an_anonymous_caller_cannot_touch_the_doors(ssh_server):
    assert ssh_server["anon"].get("/api/console/ssh").status_code == 401


def test_every_change_of_state_is_journalled_with_the_user(ssh_server):
    admin = ssh_server["admin"]
    admin.put("/api/console/ssh/stations",
              json={"enabled": True, "confirm": "imagectl.debug"})
    admin.put("/api/console/ssh/interfaces/eth0",
              json={"enabled": True, "confirm": "eth0"})
    events = journal_events(ssh_server)
    assert ("ssh_stations", "on", "noc") in events
    assert ("ssh_server", "eth0 on", "noc") in events
