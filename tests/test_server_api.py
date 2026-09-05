"""ה-flows המלאים דרך HTTP — בדיוק מה שהסוכן והדפדפן יעשו.

הלקוח כאן מדבר את ממשק 2 מילה במילה (גוף hello מלא), ומאמת את ממשק 3
בתשובה. תפריט ה-GRUB נבדק דרך אותו שרת — לוודא שה-resolver המוזרק
באמת מחובר, ושתפריט לא מצרף אף אחד לסבב.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom


def hello(server, mac, **kwargs):
    response = server["anon"].post("/api/v1/agent/hello", json=hello_body(mac, **kwargs))
    assert response.status_code == 200
    return response.json()


# --- hello -------------------------------------------------------------------


def test_unknown_mac_gets_nothing_and_raises_an_alert(server):
    answer = hello(server, "aa:aa:aa:aa:aa:aa")
    assert answer == {
        "schema": 1, "known": False, "role": "unknown", "group": None,
        "task": None, "session": None, "allowed_images": [],
        "ui": {"language": "he", "require_login": True},
    }
    events = [row["event"] for row in server["admin"].get("/api/console/journal").json()]
    assert "unknown_mac" in events


def test_known_machine_without_a_session_boots_locally(server):
    ids = setup_classroom(server)
    answer = hello(server, ids["mac1"])
    assert answer["known"] is True
    assert answer["role"] == "classroom"
    assert answer["group"] == {"id": "grp_LAB1", "label": "כיתה LAB1", "suffix": "05"}
    assert answer["task"] is None and answer["session"] is None


def test_allowed_images_respect_the_reported_disk(server):
    setup_classroom(server)
    small = hello(server, "b4:2e:99:07:1a:c4", disk_bytes=256060514304)
    big = hello(server, "b4:2e:99:07:1a:c4", disk_bytes=500107862016)
    assert small["allowed_images"] == ["img_7f3a91"]
    assert big["allowed_images"] == ["img_2c8e04", "img_7f3a91"]


def test_malformed_hello_is_an_orderly_error(server):
    response = server["anon"].post("/api/v1/agent/hello", json={"schema": 1})
    assert response.status_code == 400
    assert response.json()["code"] == "bad_mac"


# --- מחזור סבב שלם דרך ה-API -------------------------------------------------


def open_session(server, expected=2):
    ids = setup_classroom(server, expected)
    response = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": expected},
    )
    assert response.status_code == 200
    ids["session"] = response.json()["id"]
    return ids


def test_the_full_classroom_round(server):
    ids = open_session(server, expected=2)

    # מכונה ראשונה מצטרפת דרך hello — בלי endpoint נפרד.
    first = hello(server, ids["mac1"])
    assert first["session"]["state"] == "open"
    assert first["session"]["joined"] == 1
    assert first["ui"]["require_login"] is False   # סבב פתוח = בלי סיסמאות

    # השנייה משלימה את המספר המוצהר; ה-hello הבא כבר רואה running.
    hello(server, ids["mac2"])
    running = hello(server, ids["mac1"])
    assert running["session"]["state"] == "running"
    assert running["session"]["image_id"] == "img_7f3a91"
    assert running["session"]["starts_in_seconds"] == 0

    # דיווח סיום — והסבב לא מוצע לאותה מכונה שוב (אין לולאת שחזור).
    report = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 57982058496,
                     "bytes_total": 57982058496, "state": "done"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    after = hello(server, ids["mac1"])
    assert after["session"] is None
    # אבל השנייה עדיין בפנים.
    assert hello(server, ids["mac2"])["session"]["state"] == "running"


def test_late_machine_waits_for_the_next_round(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])                       # מצטרף ומתחיל (expected=1)
    late = hello(server, ids["mac2"])
    assert late["session"] is None                   # מאחר → דיסק מקומי


def test_failed_target_reaches_the_journal(server):
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    report = {
        "session_id": ids["session"], "mac": ids["mac1"], "state": "failed",
        "targets": [{"dev": "sda", "bytes_written": 4194304,
                     "bytes_total": 57982058496, "state": "failed",
                     "error": "I/O error at sector 8419328"}],
    }
    assert server["anon"].post("/api/v1/agent/progress", json=report).json()["ok"]
    journal = server["admin"].get("/api/console/journal").json()
    failures = [row for row in journal if row["event"] == "client_failed"]
    assert failures and "I/O error" in failures[0]["text"]
    # התרגום: תווית בעברית, וה-MAC נפתר לשם + קבוצה.
    assert failures[0]["label"] == "כתיבה נכשלה במחשב"
    assert "05" in failures[0]["text"] and "LAB1" in failures[0]["text"]

    members = server["admin"].get("/api/console/overview").json()["session"]["members"]
    assert members[0]["state"] == "failed"


def test_the_console_shows_names_not_identifiers(server):
    """מי שמסתכל על הקונסולה מחפש "LAB1-05", לא b4:2e:99:07:1a:c4."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]

    assert view["group_label"] == "כיתה LAB1"
    assert view["image_name"] == "Office 2024 Standard"
    assert view["single"] is False

    member = view["members"][0]
    assert member["name"] == "05"
    assert member["hostname"] == "LAB1-05"       # השם שייכתב למחשב בסיום
    assert member["mac"] == ids["mac1"]          # ה-MAC נשאר, לטכנאי


def test_a_one_machine_round_is_marked_as_a_single_station(server):
    ids = open_session(server, expected=1)
    hello(server, ids["mac1"])
    view = server["admin"].get("/api/console/overview").json()["session"]
    assert view["single"] is True


def test_an_unregistered_member_falls_back_to_its_mac(server):
    """מכונה שנמחקה מהטבלה באמצע סבב לא מפילה את התצוגה."""
    ids = open_session(server, expected=2)
    hello(server, ids["mac1"])
    server["admin"].delete(f"/api/console/machines/{ids['mac1']}")
    member = server["admin"].get("/api/console/overview").json()["session"]["members"][0]
    assert member["name"] is None and member["hostname"] is None
    assert member["mac"] == ids["mac1"]


def test_progress_from_a_nonmember_is_rejected(server):
    ids = open_session(server)
    report = {"session_id": ids["session"], "mac": "aa:aa:aa:aa:aa:aa",
              "state": "writing", "targets": []}
    response = server["anon"].post("/api/v1/agent/progress", json=report)
    assert response.status_code == 400
    assert response.json()["code"] == "not_member"


def test_manual_start_and_close(server):
    ids = open_session(server, expected=30)
    hello(server, ids["mac1"])
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/start"
    ).status_code == 200
    assert hello(server, ids["mac1"])["session"]["state"] == "running"
    assert server["deploy"].post(
        f"/api/console/sessions/{ids['session']}/close"
    ).status_code == 200
    assert hello(server, ids["mac1"])["session"] is None


# --- מניפסטים וקבצים ---------------------------------------------------------


def test_manifest_endpoint_hides_internals(server):
    manifest = server["anon"].get("/api/v1/images/img_7f3a91/manifest").json()
    assert manifest["id"] == "img_7f3a91"
    assert "_dir" not in manifest
    missing = server["anon"].get("/api/v1/images/img_none/manifest")
    assert missing.status_code == 404 and missing.json()["code"] == "no_image"


def test_partition_files_are_whitelisted(server):
    good = server["anon"].get("/api/v1/images/img_7f3a91/files/p1.esp.pcl.zst")
    assert good.status_code == 200
    assert good.content == b"compressed-partition-bytes"
    for bad_name in ("manifest.json", "..%2Fmanifest.json", "secret.txt"):
        assert server["anon"].get(
            f"/api/v1/images/img_7f3a91/files/{bad_name}"
        ).status_code == 404


# --- תפריט ה-GRUB מחובר לאותו מוח --------------------------------------------


def test_boot_menu_is_wired_to_the_resolver(server):
    ids = open_session(server)
    response = server["anon"].get(f"/boot/menu?mac={ids['mac1']}")
    assert response.status_code == 200
    assert "chain_local" in response.text
    response.text.encode("ascii")                    # הפלט חייב להישאר ASCII

    # תפריט שואל — הוא לא מצרף לסבב. ההצטרפות היא רק ב-hello.
    overview = server["admin"].get("/api/console/overview").json()
    assert overview["session"]["joined"] == 0


def test_extra_cmdline_reaches_the_boot_menu(tmp_path, images_root, clock):
    """תוספות מפעיל לשורת הקרנל (קונסולה טורית) עוברות כהגדרה — לא
    בעריכת קוד על השרת, שהולידה fork חי (#18).

    ‏`imagectl.debug` יוצא מן הכלל מאז #83: הוא פותח SSH ומעטפת טכנאי
    בכל תחנה, ולכן הוא מתג בקונסולה ולא תוספת מפעיל. גם כשהמפעיל מעביר
    אותו כאן — הוא אינו נכנס לשורת הקרנל כל עוד המתג כבוי, אחרת היו שני
    מקורות אמת לאותה דלת והישן היה גובר בשקט."""
    from fastapi.testclient import TestClient

    from server import users
    from server.app import create_app

    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock,
        extra_cmdline=("console=ttyS0,115200", "imagectl.debug=1"),
    )
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    admin = TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    setup_classroom({"admin": admin})

    text = TestClient(app).get("/boot/menu?mac=b4:2e:99:07:1a:c4").text
    assert "console=ttyS0,115200" in text
    assert "imagectl.debug" not in text


def test_boot_menu_never_errors_on_garbage(server):
    for query in ("", "?mac=", "?mac=zz:zz", "?mac=%00"):
        response = server["anon"].get(f"/boot/menu{query}")
        assert response.status_code == 200
        assert "chain_local" in response.text


# --- הרשאות ------------------------------------------------------------------


def test_anonymous_gets_401_everywhere(server):
    for path in ("/api/console/overview", "/api/console/images", "/api/console/journal"):
        assert server["anon"].get(path).status_code == 401


def test_deploy_user_is_fenced_in(server):
    """סעיף 11: לבחור אימג' ולהפיץ — כן. לנהל את המערכת — לא."""
    deploy = server["deploy"]
    assert deploy.get("/api/console/images").status_code == 200
    assert deploy.get("/api/console/overview").status_code == 200
    assert deploy.get("/api/console/users").status_code == 403
    assert deploy.get("/api/console/journal").status_code == 403
    assert deploy.get("/api/console/settings").status_code == 403
    assert deploy.get("/api/console/machines.csv").status_code == 403
    assert deploy.post(
        "/api/console/groups", json={"id": "x", "label": "x", "role": "classroom"}
    ).status_code == 403
    assert deploy.post(
        "/api/console/machines/import", json={"group_id": "x", "text": ""}
    ).status_code == 403


def test_wrong_password_fails_and_is_journaled(server):
    response = server["anon"].post(
        "/api/console/login", json={"username": "noc", "password": "nope"}
    )
    assert response.status_code == 401
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "login_failed" in events


def test_idle_timeout_reaches_every_signed_in_user(server):
    """זמן הניתוק מוחזר גם למשתמש deploy — ההגדרות חסומות בפניו, אבל
    הניתוק חל עליו באותה מידה."""
    assert server["deploy"].get("/api/console/me").json()["idle_seconds"] == 300
    assert server["admin"].post(
        "/api/console/settings", json={"console_idle_seconds": "600"}
    ).status_code == 200
    assert server["deploy"].get("/api/console/me").json()["idle_seconds"] == 600
    # וגם בתשובת הכניסה עצמה, כדי שהשעון יתחיל נכון מהרגע הראשון.
    fresh = server["anon"].post(
        "/api/console/login",
        json={"username": "labtech", "password": "deploy-pass-1"},
    ).json()
    assert fresh["idle_seconds"] == 600


def test_agent_login_checks_the_console_users(server):
    """סעיף 15: ההרשאה יושבת בשרת — מסך התחנה מאמת מול אותם משתמשים."""
    ok = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "deploy-pass-1",
        "mac": "b4:2e:99:07:1a:c4"})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "role": "deploy"}

    bad = server["anon"].post("/api/v1/agent/login", json={
        "username": "labtech", "password": "wrong", "mac": "b4:2e:99:07:1a:c4"})
    assert bad.status_code == 401
    assert bad.json()["code"] == "bad_login"

    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert "agent_login" in events and "agent_login_failed" in events


def test_recovery_login_toggle(server):
    """ברירת המחדל הבטוחה: recovery דורש כניסה. הדגמה יכולה לכבות."""
    setup_classroom(server)
    assert hello(server, "b4:2e:99:07:1a:c4")["ui"]["require_login"] is True
    assert server["admin"].post(
        "/api/console/settings", json={"recovery_require_login": "false"}
    ).status_code == 200
    assert hello(server, "b4:2e:99:07:1a:c4")["ui"]["require_login"] is False


# --- סינון וחיפוש ביומן (#115) -------------------------------------------


def test_journal_events_list_is_hebrew_labels(server):
    events = server["admin"].get("/api/console/journal/events").json()
    assert {"event": "login_failed", "label": "ניסיון כניסה כושל"} in events
    # ממוין לפי התווית — כדי שהתפריט יהיה קריא, לא לפי סדר יצירה במילון.
    labels = [e["label"] for e in events]
    assert labels == sorted(labels)


def test_journal_filter_by_event_type(server):
    server["anon"].post("/api/console/login", json={"username": "x", "password": "no"})
    server["admin"].post("/api/console/login", json={"username": "admin", "password": "wrong"})
    rows = server["admin"].get("/api/console/journal", params={"event": "login_failed"}).json()
    assert rows and all(r["event"] == "login_failed" for r in rows)


def test_journal_filter_by_date_range_excludes_out_of_range_rows(server):
    setup_classroom(server)
    hello(server, "b4:2e:99:07:1a:c4")  # אין סבב — לא כותב ליומן, רק ה-group_create/machine_add למעלה
    all_rows = server["admin"].get("/api/console/journal").json()
    assert all_rows, "צריך לפחות שורה אחת כדי שהבדיקה תהיה משמעותית"
    newest_ts = max(r["ts"] for r in all_rows)
    # "עד" לפני השורה החדשה ביותר — היא לא אמורה לחזור.
    before = server["admin"].get(
        "/api/console/journal", params={"to": "2000-01-01T00:00:00"}
    ).json()
    assert before == []
    after = server["admin"].get(
        "/api/console/journal", params={"from": newest_ts}
    ).json()
    assert any(r["ts"] == newest_ts for r in after)


def test_journal_machine_filter_matches_the_display_name_not_the_raw_id(server):
    """המלכודת ב-#115: מפעיל מחפש את מה שהוא רואה על המסך (שם הכיתה),
    לא את המזהה הגולמי (grp_...) שכתוב בפועל בשורת ה-DB."""
    admin = server["admin"]
    assert admin.post(
        "/api/console/groups",
        json={"id": "grp_9f2e", "label": "מבנה מדעים", "role": "classroom"},
    ).status_code == 200

    # ודאות שזה באמת מבחן על המלכודת: השם המוצג לא מופיע בתוך המזהה הגולמי.
    assert "מבנה מדעים" not in "grp_9f2e"

    by_label = admin.get(
        "/api/console/journal", params={"machine": "מבנה מדעים"}
    ).json()
    assert any(r["event"] == "group_create" and "מבנה מדעים" in r["text"] for r in by_label)

    by_raw_id = admin.get(
        "/api/console/journal", params={"machine": "grp_9f2e"}
    ).json()
    assert any(r["event"] == "group_create" for r in by_raw_id)


def test_journal_free_text_search_matches_translated_text(server):
    setup_classroom(server)
    rows = server["admin"].get("/api/console/journal", params={"q": "כיתה LAB1"}).json()
    assert any(r["event"] == "group_create" for r in rows)
    empty = server["admin"].get(
        "/api/console/journal", params={"q": "מחרוזת שלא קיימת באמת"}
    ).json()
    assert empty == []


def test_journal_filters_require_admin(server):
    assert server["deploy"].get(
        "/api/console/journal", params={"event": "login"}
    ).status_code == 403
    assert server["deploy"].get("/api/console/journal/events").status_code == 403
