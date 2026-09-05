"""שני זרמים, שני כללים — ‏issue #60.

‏`udp-sender` אחד יש בשרת, ולכן **שידור מולטיקאסט אחד** בכל המערכת: סבב
כיתה או גל חדר שיכפולים תופסים את החריץ, והשני מקבל 409. זה הכלל הישן
והוא נשאר.

מה שלא נכון היה להיכנס תחת אותו כלל הוא **משיכת יוניקאסט** — תחנה בודדת
שמושכת אימג' ב-HTTP. היא אינה נוגעת ב-udp-sender ואינה מתחרה על כתובת
המולטיקאסט; היא צורכת רוחב פס, וזה שיקול תפעולי ולא התנגשות. כמה כאלה
רצות יחד, וגם בזמן שסבב משדר.

הערה על ההנחה שבישיו: "שחזור יוניקאסט פותח session עם expected_clients==1"
אינו מדויק. סבב של מחשב אחד הוא **עדיין מולטיקאסט** (אותו udp-sender עם
‏`--min-receivers 1`), ולכן הוא נשאר חסום — יש כאן בדיקה שמקבעת את זה.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body, setup_classroom

#: הכתובת שאיתה נוצר השרת ב-conftest — וילן ההפצה.
VLAN = "http://10.44.12.10:8080"
#: כתובת מקומית אחרת של אותו שרת: תחנה שמגיעה מרשת אחרת (#42).
OFF_VLAN = "http://10.10.10.8:8080"

CREDS = {"username": "labtech", "password": "deploy-pass-1"}


# --- עזרים -------------------------------------------------------------------


def second_class(server) -> dict:
    """כיתה שנייה — התחנות שמושכות ביוניקאסט אינן בכיתה שמקבלת סבב."""
    assert server["admin"].post(
        "/api/console/groups",
        json={"id": "grp_LAB2", "label": "כיתה LAB2", "role": "classroom"},
    ).status_code == 200
    result = server["admin"].post(
        "/api/console/machines/import",
        json={"group_id": "grp_LAB2",
              "text": "b4:2e:99:07:2a:01 07\nb4:2e:99:07:2a:02 08\n"},
    ).json()
    assert result["saved"] == 2 and not result["rejected"]
    return {"group": "grp_LAB2", "mac1": "b4:2e:99:07:2a:01",
            "mac2": "b4:2e:99:07:2a:02"}


def open_round(server, group: str, image: str = "img_7f3a91", expected: int = 2):
    return server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": group, "image_id": image, "expected_clients": expected},
    )


def pull(server, mac: str, image: str = "img_7f3a91", base: str = VLAN,
         creds: dict | None = None):
    """פתיחת משיכת יוניקאסט מהתחנה, כמו שאשף השחזור יפתח אותה."""
    body = {"mac": mac, "image_id": image, **(CREDS if creds is None else creds)}
    return server["anon"].post(base + "/api/v1/agent/pulls", json=body)


def report(server, session_id: str, mac: str, state: str):
    return server["anon"].post("/api/v1/agent/progress", json={
        "session_id": session_id, "mac": mac, "state": state,
        "targets": [{"dev": "sda", "bytes_written": 10, "bytes_total": 10,
                     "state": state}],
    })


def overview(server) -> dict:
    response = server["admin"].get("/api/console/overview")
    assert response.status_code == 200
    return response.json()


# --- החריץ היחיד נשאר יחיד ---------------------------------------------------


def test_a_second_multicast_round_is_still_refused(server):
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert open_round(server, lab1["group"]).status_code == 200

    second = open_round(server, lab2["group"])

    assert second.status_code == 409
    assert "כבר יש סבב פעיל" in second.json()["detail"]


def test_a_one_machine_round_is_a_broadcast_too_and_is_refused(server):
    """סבב של מחשב אחד אינו "יוניקאסט": הוא רץ על אותו udp-sender."""
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert open_round(server, lab1["group"]).status_code == 200

    single = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": lab2["group"], "image_id": "img_7f3a91",
              "macs": [lab2["mac1"]], "expected_clients": 1},
    )

    assert single.status_code == 409
    assert "כבר יש סבב פעיל" in single.json()["detail"]


def test_a_room_wave_still_takes_the_slot_from_a_class_round(server):
    """גל חדר השיכפולים הוא מולטיקאסט — הכלל הישן נשאר גם עליו."""
    lab1 = setup_classroom(server)
    assert server["admin"].post(
        "/api/console/machines",
        json={"mac": "b4:2e:99:07:3a:01", "name": "01", "group_id": "grp_CLONERS"},
    ).status_code == 200
    assert server["deploy"].post(
        "/api/console/room", json={"image_id": "img_7f3a91", "target_drives": 4},
    ).status_code == 200

    assert open_round(server, lab1["group"]).status_code == 409


# --- משיכת יוניקאסט אינה תופסת את החריץ --------------------------------------


def test_a_unicast_pull_opens_while_a_round_is_open(server):
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert open_round(server, lab1["group"]).status_code == 200

    response = pull(server, lab2["mac1"])

    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "unicast"


def test_two_unicast_pulls_run_side_by_side(server):
    """‏8.5: שתי תחנות מחוץ לוילן בו-זמנית, אחת לינוקס ואחת Windows."""
    lab2 = second_class(server)

    first = pull(server, lab2["mac1"], image="img_7f3a91")
    second = pull(server, lab2["mac2"], image="img_2c8e04")

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["id"] != second.json()["id"]
    pulls = overview(server)["pulls"]
    assert {p["id"] for p in pulls} == {first.json()["id"], second.json()["id"]}
    assert {p["image_id"] for p in pulls} == {"img_7f3a91", "img_2c8e04"}


def test_a_round_opens_while_pulls_are_running(server):
    """הכיוון השני: משיכה פעילה אינה חוסמת שידור."""
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert pull(server, lab2["mac1"]).status_code == 200
    assert pull(server, lab2["mac2"]).status_code == 200

    assert open_round(server, lab1["group"]).status_code == 200


def test_the_same_machine_does_not_pull_twice(server):
    """שתי משיכות על אותו דיסק היו דורסות זו את זו — זה כן מוצהר."""
    lab2 = second_class(server)
    assert pull(server, lab2["mac1"], image="img_7f3a91").status_code == 200

    again = pull(server, lab2["mac1"], image="img_2c8e04")

    assert again.status_code == 409
    assert "כבר מושכת" in again.json()["error"]
    assert len(overview(server)["pulls"]) == 1


def test_the_same_request_twice_is_the_same_pull(server):
    """ה-retry של הסוכן (#104): אותה תחנה, אותו אימג', ואיש עוד לא דיווח.

    ‏`curl --retry 3 --max-time 10` שולח שוב בקשה שהשרת כבר קיבל וענה
    עליה באיחור. השנייה אינה משיכה חדשה ואינה תחרות על הדיסק — היא
    אותה בקשה, ולכן מקבלת את **אותו** מזהה. אחרת נפתחת משיכה שנייה
    שאיש לא ידווח אליה, והתחנה נחסמת על עבודה שלא באמת רצה.
    """
    lab2 = second_class(server)
    first = pull(server, lab2["mac1"])
    assert first.status_code == 200, first.text

    again = pull(server, lab2["mac1"])

    assert again.status_code == 200, again.text
    assert again.json()["id"] == first.json()["id"]
    assert len(overview(server)["pulls"]) == 1


def test_a_pull_that_reported_is_not_handed_out_again(server):
    """מרגע שדווח משהו זו עבודה קיימת ולא בקשה שאבדה — וחוזרים לחסימה.

    כך גם משיכה שנכשלה: היא נשארת נראית עד שמפעיל סוגר אותה, ובקשה
    חדשה של אותה תחנה אינה משתלטת עליה בשקט (עיקרון 5).
    """
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report(server, pull_id, lab2["mac1"], "writing").json() == {"ok": True}

    again = pull(server, lab2["mac1"])

    assert again.status_code == 409
    assert "כבר מושכת" in again.json()["error"]


# --- מה שהקונסולה רואה -------------------------------------------------------


def test_the_overview_shows_the_round_and_the_pulls_together(server):
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert open_round(server, lab1["group"]).status_code == 200
    pull_id = pull(server, lab2["mac1"]).json()["id"]

    view = overview(server)

    assert view["session"]["group_id"] == lab1["group"]
    assert [p["id"] for p in view["pulls"]] == [pull_id]
    only = view["pulls"][0]
    assert only["kind"] == "unicast"
    assert only["state"] == "running"
    assert [m["mac"] for m in only["members"]] == [lab2["mac1"]]


def test_a_pull_reports_progress_like_any_member(server):
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]

    assert report(server, pull_id, lab2["mac1"], "writing").json() == {"ok": True}

    member = overview(server)["pulls"][0]["members"][0]
    assert member["state"] == "writing"
    assert member["bytes_total"] == 10


def test_a_finished_pull_leaves_the_overview(server):
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report(server, pull_id, lab2["mac1"], "done").json() == {"ok": True}

    assert overview(server)["pulls"] == []


def test_a_failed_pull_stays_on_the_screen(server):
    """‏"נכשל" אינו "הסתיים" — מפעיל חייב לראות אותו (עיקרון 5)."""
    lab2 = second_class(server)
    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert report(server, pull_id, lab2["mac1"], "failed").json() == {"ok": True}

    pulls = overview(server)["pulls"]

    assert [p["id"] for p in pulls] == [pull_id]
    assert pulls[0]["members"][0]["state"] == "failed"
    # ומפעיל יכול לפנות אותו מהקונסולה, באותו endpoint של כל סבב.
    assert server["deploy"].post(
        f"/api/console/sessions/{pull_id}/close").status_code == 200
    assert overview(server)["pulls"] == []


# --- בידוד: משיכה אינה "הסבב" ------------------------------------------------


def test_a_pull_is_not_the_round_that_hello_offers(server):
    """מכונה אחרת בכיתה לא תקבל את המשיכה של השכנה כאילו היא סבב."""
    lab2 = second_class(server)
    assert pull(server, lab2["mac1"]).status_code == 200

    answer = server["anon"].post(
        "/api/v1/agent/hello", json=hello_body(lab2["mac2"])).json()

    assert answer["session"] is None


def test_a_pull_is_not_the_round_the_station_screen_shows(server):
    lab2 = second_class(server)
    assert pull(server, lab2["mac1"]).status_code == 200

    assert server["anon"].get(
        "/api/v1/agent/sessions/active").json()["session"] is None


def test_closing_a_pull_does_not_stop_the_broadcast(server_with_sender):
    """‏`on_closed` מגיע ל-SenderEngine שמתעלם ממזהה הסבב: סגירת משיכה
    הייתה הורגת את השידור של הכיתה."""
    from test_sender import wait_for

    server, recorder = server_with_sender
    lab1 = setup_classroom(server)
    lab2 = second_class(server)
    assert open_round(server, lab1["group"], expected=1).status_code == 200
    server["anon"].post("/api/v1/agent/hello", json=hello_body(lab1["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(lab1["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: len(recorder.commands) >= 1)

    pull_id = pull(server, lab2["mac1"]).json()["id"]
    assert server["deploy"].post(
        f"/api/console/sessions/{pull_id}/close").status_code == 200

    assert recorder.processes[0].terminated is False
    assert server["admin"].get(
        "/api/console/overview").json()["sender"]["state"] == "sending"


# --- מי מורשה לפתוח משיכה (‏#42 לא משתנה) ------------------------------------


def test_a_pull_from_another_network_demands_a_login(server):
    lab2 = second_class(server)

    response = pull(server, lab2["mac1"], base=OFF_VLAN, creds={})

    assert response.status_code == 401
    assert response.json()["code"] == "bad_login"


def test_a_pull_from_another_network_with_a_login_is_allowed(server):
    lab2 = second_class(server)

    assert pull(server, lab2["mac1"], base=OFF_VLAN).status_code == 200


def test_a_wrong_password_does_not_open_a_pull(server):
    lab2 = second_class(server)

    response = pull(server, lab2["mac1"],
                    creds={"username": "labtech", "password": "nope"})

    assert response.status_code == 401
    assert overview(server)["pulls"] == []


def test_an_unregistered_machine_gets_no_pull(server):
    second_class(server)

    response = pull(server, "b4:2e:99:07:9f:ff")

    assert response.status_code == 403
    assert response.json()["code"] == "unknown_mac"


def test_a_pull_of_an_unknown_image_is_refused(server):
    lab2 = second_class(server)

    response = pull(server, lab2["mac1"], image="img_nope")

    assert response.status_code == 404
    assert response.json()["code"] == "no_image"
