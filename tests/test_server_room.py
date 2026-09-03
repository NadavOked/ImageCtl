"""סבב חדר השיכפולים (Issue #9, אפיון סעיף 29) — מקצה לקצה מול ה-API.

הגלים נבדקים כמו שהם קורים בחדר: מכונות מדווחות מגירות ב-hello,
גל יוצא כשהמוכנות מכסה את היתרה, מגירה נספרת לפי serial פעם אחת,
ומכונה שלא הוחלפו לה המגירות לא מצטרפת לגל הבא.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import Clock, hello_body, write_image, MANIFEST_256

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

CLONER1 = "aa:bb:cc:00:00:21"
CLONER2 = "aa:bb:cc:00:00:22"


@pytest.fixture()
def room_server(tmp_path: Path):
    """שרת עם WoL מזויף ומנוע שידור מזויף, ושני מחשבי שיכפול רשומים."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_sender import Recorder                       # noqa: PLC0415

    from server import users
    from server.app import create_app

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    woken: list[bytes] = []
    recorder = Recorder()
    app = create_app(
        tmp_path / "data", images, "http://10.99.12.10:8080",
        now_fn=Clock(), sender_runner=recorder,
        wol_send=woken.append,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test")
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test")

    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login",
               json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name in ((CLONER1, "shich-1"), (CLONER2, "shich-2")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": "grp_CLONERS",
        }).status_code == 200
    yield {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
           "anon": TestClient(app), "woken": woken}
    ctx.sender.stop()


def cloner_hello(client, mac: str, serials: list[str], *, ports: bool = True,
                 joining: bool | None = None) -> dict:
    """hello של מחשב שיכפול: מגירה לכל serial.

    ‏`ports=False` מדמה סוכן ישן (או VM עם SCSI) שאינו מדווח חריץ —
    השדה פשוט חסר, וזה חייב להמשיך לעבוד (עיקרון 1).

    ‏`joining=None` הוא סוכן שאינו שולח את השדה בכלל — ברירת המחדל
    ההיסטורית, שחייבת להישאר "מצטרף".
    """
    body = hello_body(mac)
    if joining is not None:
        body["joining"] = joining
    body["disks"] = [
        {"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304,
         "model": "Drawer SSD", "serial": serial, "removable": False,
         "scheme": "gpt", "has_data": False,
         **({"port": i + 1} if ports else {})}
        for i, serial in enumerate(serials)
    ]
    response = client.post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    return response.json()


def report(client, session_id: str, mac: str, per_drive: dict[str, str],
           top: str | None = None) -> None:
    """דיווח סופי של מכונה: מצב לכל מגירה (dev → done/failed).

    ברירת המחדל של `top` היא מה ש**סוכן ישן** שולח — `done` על כל מכונה
    שמגירה אחת שלה שרדה (#67). היא נשארת כאן בכוונה: זה חוזה שהשרת
    חייב להמשיך לכבד. סוכן מעודכן שולח `partial` במפורש.
    """
    states = list(per_drive.values())
    if top is None:
        top = "failed" if all(s == "failed" for s in states) else "done"
    response = client.post("/api/v1/agent/progress", json={
        "session_id": session_id, "mac": mac, "state": top,
        "targets": [
            {"dev": dev, "bytes_written": 100 if state == "done" else 4,
             "bytes_total": 100, "state": state,
             **({"error": "I/O error"} if state == "failed" else {})}
            for dev, state in per_drive.items()
        ],
    })
    assert response.status_code == 200


def room(client) -> dict:
    response = client.get("/api/console/room")
    assert response.status_code == 200
    return response.json()


def test_round_wakes_room_and_autostarts_when_drives_cover_target(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    # משתמש הפצה פותח — זו בדיוק ההרשאה שלו (אפיון סעיף 11).
    opened = deploy.post("/api/console/room",
                         json={"image_id": "img_7f3a91", "target_drives": 4})
    assert opened.status_code == 200
    assert len(room_server["woken"]) == 2          # WoL לכל החדר עם הפתיחה

    # שתי מכונות עולות עם שתי מגירות כל אחת — 4 מוכנות מול יעד 4.
    answer = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert answer["session"]["state"] == "open"
    cloner_hello(anon, CLONER2, ["S3", "S4"])

    view = room(deploy)["round"]
    assert view["ready_drives"] in (0, 4)          # לפני/אחרי tick של ה-GET
    assert room(deploy)["round"]["wave_state"] == "running"


def test_waves_accumulate_by_serial_until_target(room_server):
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91", "target_drives": 6},
                       ).status_code == 200
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])

    # 4 מוכנות מול יתרה 6 — הגל לא יוצא לבד; "התחל עכשיו" כן.
    assert room(deploy)["round"]["wave_state"] == "open"
    assert deploy.post("/api/console/room/start").status_code == 200

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done"})

    # הגל הסתיים: 4 נכתבו, גל שני נפתח מעצמו וממתין למגירות מוחלפות.
    view = room(deploy)["round"]
    assert view["written_drives"] == 4
    assert view["wave_number"] == 2
    assert view["wave_state"] == "open"

    # אותן מגירות — המכונה לא מצטרפת שוב (אחרת נספור אותן פעמיים).
    assert cloner_hello(anon, CLONER1, ["S1", "S2"])["session"] is None

    # מגירות מוחלפות: 2 טריות מכסות את היתרה — הגל יוצא לבד.
    answer = cloner_hello(anon, CLONER1, ["S5", "S6"])
    wave2 = answer["session"]["id"]
    assert wave2 != wave1
    assert room(deploy)["round"]["wave_state"] == "running"

    report(anon, wave2, CLONER1, {"sda": "done", "sdb": "done"})
    assert room(deploy)["round"] is None           # היעד הושג — הסבב נסגר

    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "room_open" in events and "room_wave" in events and "room_done" in events


def test_failed_drawer_is_not_counted_and_retries_next_wave(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 2})
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert room(deploy)["round"]["wave_state"] == "running"

    # מגירה אחת נכשלה — נספרת רק המוצלחת, בגלוי (עיקרון 4).
    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "failed"})
    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["wave_state"] == "open"

    # אותה מכונה, אותן מגירות: S2 הכושלת עדיין טרייה — מצטרפים וכותבים שוב.
    wave2 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert room(deploy)["round"]["wave_state"] == "running"
    report(anon, wave2, CLONER1, {"sdb": "done"})
    assert room(deploy)["round"] is None


def test_a_machine_that_lost_one_drawer_is_partial_and_not_done(room_server):
    """‏#67: המצב השלישי, לכל אורך הדרך — מהדיווח ועד המסך.

    ‏`partial` הוא **סופי**: הגל מסתיים ונפתח הבא, אחרת מכונה שאיבדה
    מגירה הייתה תולה את החדר כולו. הוא **אינו** הצלחה מלאה: הדגל `done`
    של חבר הסבב לא נדלק, המסך מקבל את המילה, והמגירה שנכשלה לא נספרת.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 6})
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S4", "S5"])
    assert deploy.post("/api/console/room/start").status_code == 200

    # המכונה הראשונה איבדה מגירה אחת מתוך שלוש; השנייה עוד כותבת.
    report(anon, wave1, CLONER1,
           {"sda": "done", "sdb": "failed", "sdc": "done"}, top="partial")

    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert machine["state"] == "partial", "המסך לא קיבל את המצב השלישי"
    assert "sdb" in (machine["error"] or "")

    member = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave1, CLONER1),
    ).fetchone()
    assert member["state"] == "partial"
    assert member["done"] == 0, "הושלם חלקית נספר כהצלחה מלאה"

    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "client_partial" in events

    # וכשהשנייה מסיימת, הגל נסגר: `partial` הוא סיום, לא המתנה. בלי זה
    # מכונה שאיבדה מגירה הייתה תולה את החדר כולו.
    report(anon, wave1, CLONER2, {"sda": "done", "sdb": "done"})
    view = room(deploy)["round"]
    assert view["written_drives"] == 4              # ‏S2 הכושלת לא נספרה
    assert view["wave_number"] == 2

    # והמגירה שנכשלה נשארת טרייה: הגל הבא כותב אותה שוב.
    assert cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"] is not None


def test_an_old_agent_that_still_says_done_keeps_working(room_server):
    """תאימות לאחור (#67): סוכן שלא עודכן שולח `done` גם כשמגירה נכשלה.

    ‏`schema` לא עלה, ולכן הוא חייב להמשיך לעבוד בדיוק כמו קודם —
    הספירה היא ממילא יעד-יעד, והמגירה הכושלת עדיין אינה נספרת.
    """
    deploy, anon, ctx = room_server["deploy"], room_server["anon"], room_server["ctx"]

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 4})
    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    assert deploy.post("/api/console/room/start").status_code == 200

    report(anon, wave1, CLONER1, {"sda": "done", "sdb": "failed"})   # top=done

    view = room(deploy)["round"]
    assert view["written_drives"] == 1 and view["wave_number"] == 2
    member = ctx.conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (wave1, CLONER1),
    ).fetchone()
    assert (member["state"], member["done"]) == ("done", 1)


def test_the_drawer_slot_travels_from_hello_to_the_console(room_server):
    """‏#27: הקונסולה צריכה לדעת איזה חריץ לשלוף — לא באיזה סדר התגלה.

    ‏`port` מדווח ב-hello (ממשק 2), נשמר כמו שהוא, וחוזר במסך החדר
    יחד עם שם ההתקן ומצב הכתיבה של אותה מגירה.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 3})
    wave = cloner_hello(anon, CLONER1, ["S1", "S2", "S3"])["session"]["id"]

    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert [(d["port"], d["dev"]) for d in machine["drawer_list"]] == [
        (1, "sda"), (2, "sdb"), (3, "sdc")]
    assert all(d["fresh"] for d in machine["drawer_list"])
    assert machine["drawers"] == 3 and machine["fresh_drawers"] == 3

    # תוך כדי הגל: המצב יושב על המגירה עצמה — "מגירה 3 (sdc) נכשלה",
    # ולא "אחת משלוש נכשלה, לך תמצא איזו".
    assert anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing",
        "targets": [
            {"dev": "sda", "bytes_written": 100, "bytes_total": 100, "state": "done"},
            {"dev": "sdb", "bytes_written": 50, "bytes_total": 100, "state": "writing"},
            {"dev": "sdc", "bytes_written": 4, "bytes_total": 100, "state": "failed",
             "error": "I/O error at sector 8419328"},
        ],
    }).status_code == 200
    by_port = {d["port"]: d for d in
               next(m for m in room(deploy)["machines"]
                    if m["mac"] == CLONER1)["drawer_list"]}
    assert by_port[3]["state"] == "failed" and "I/O error" in by_port[3]["error"]
    assert by_port[1]["state"] == "done" and by_port[2]["state"] == "writing"

    # ואחרי שהגל נסגר: המגירה שנכשלה נשארת הטרייה היחידה — לפי חריץ.
    report(anon, wave, CLONER1, {"sda": "done", "sdb": "done", "sdc": "failed"})
    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    fresh = [d["port"] for d in machine["drawer_list"] if d["fresh"]]
    assert fresh == [3] and machine["fresh_drawers"] == 1


def test_an_agent_without_the_port_field_still_works(room_server):
    """סוכן ישן: אין `port`, אין מספר חריץ — והכול ממשיך כרגיל."""
    deploy, anon = room_server["deploy"], room_server["anon"]

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 2})
    assert cloner_hello(anon, CLONER1, ["S1", "S2"],
                        ports=False)["session"]["state"] in ("open", "running")

    machine = next(m for m in room(deploy)["machines"] if m["mac"] == CLONER1)
    assert [d["port"] for d in machine["drawer_list"]] == [None, None]
    assert [d["dev"] for d in machine["drawer_list"]] == ["sda", "sdb"]
    assert machine["drawers"] == 2 and machine["fresh_drawers"] == 2


def test_room_round_respects_the_single_session_invariant(room_server):
    admin, deploy = room_server["admin"], room_server["deploy"]

    # סבב כיתה פעיל תופס את החריץ היחיד — סבב חדר נדחה, בגלוי.
    assert admin.post("/api/console/groups", json={
        "id": "grp_LAB9", "label": "כיתה 9", "role": "classroom",
    }).status_code == 200
    assert admin.post("/api/console/machines", json={
        "mac": "00:00:5e:00:00:09", "name": "05", "group_id": "grp_LAB9",
    }).status_code == 200
    assert admin.post("/api/console/sessions", json={
        "group_id": "grp_LAB9", "image_id": "img_7f3a91",
        "prefix": "LAB9", "expected_clients": 1,
    }).status_code == 200

    denied = deploy.post("/api/console/room",
                         json={"image_id": "img_7f3a91", "target_drives": 2})
    assert denied.status_code == 409


def test_close_round_mid_wave_and_bad_requests(room_server):
    deploy, anon = room_server["deploy"], room_server["anon"]

    assert deploy.post("/api/console/room/close").status_code == 409
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91", "target_drives": 0},
                       ).status_code == 400
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_nope", "target_drives": 2},
                       ).status_code == 400

    deploy.post("/api/console/room",
                json={"image_id": "img_7f3a91", "target_drives": 8})
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert deploy.post("/api/console/room/close").json()["ok"] is True
    assert room(deploy)["round"] is None
    # החריץ התפנה — אפשר לפתוח סבב חדש.
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91", "target_drives": 2},
                       ).status_code == 200


def test_wake_endpoint_sends_wol_to_the_whole_room(room_server):
    deploy, woken = room_server["deploy"], room_server["woken"]
    result = deploy.post("/api/console/room/wake")
    assert result.status_code == 200
    assert result.json()["woken"] == 2 and len(woken) == 2


def test_anonymous_cannot_touch_the_room(room_server):
    anon = room_server["anon"]
    assert anon.get("/api/console/room").status_code in (401, 403)
    assert anon.post("/api/console/room",
                     json={"image_id": "img_7f3a91", "target_drives": 2},
                     ).status_code in (401, 403)


def test_a_held_machine_beats_without_joining_the_wave(room_server):
    """מכונה שנעצרה על מסך שגיאה פועמת בלי להצטרף (#64).

    הדופק חייב לעשות בדיוק שני דברים: להחזיק אותה **נראית** בקונסולה,
    ולא לגרום לגל לצאת לדרך בהסתמך על מגירות שאיש לא יכתוב. לפני
    התיקון המכונה פשוט השתתקה, והקונסולה ציירה אותה כ"כבויה" בזמן
    שהיא ישבה דלוקה עם שגיאה על המסך.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91",
                             "target_drives": 4}).status_code == 200

    # ארבע מגירות טריות — די והותר ליעד — אבל כולן בדופק בלבד.
    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=False)
    cloner_hello(anon, CLONER2, ["S3", "S4"], joining=False)

    view = room(deploy)
    assert view["round"]["wave_state"] == "open"     # לא יצא לדרך
    assert view["round"]["ready_drives"] == 0        # ולא נספרו כמוכנות
    assert [m["joined"] for m in view["machines"]] == [False, False]

    # ובכל זאת נראות: זה כל תפקידו של הדופק.
    assert all(m["awake"] for m in view["machines"])

    # אותן מכונות ב-hello רגיל — עכשיו הגל כן יוצא.
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert room(deploy)["round"]["wave_state"] == "running"


def test_an_agent_that_omits_joining_still_joins(room_server):
    """סוכן ישן אינו שולח `joining` — והיעדרו נשאר "מצטרף"."""
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91",
                             "target_drives": 2}).status_code == 200

    cloner_hello(anon, CLONER1, ["S1", "S2"], joining=None)
    assert room(deploy)["round"]["wave_state"] == "running"


def test_a_wave_closes_when_the_reports_come_in_another_mac_form(room_server):
    """‏#108: הגל נסגר גם כשהדיווח מגיע ברישיות ובמקפים.

    זו הבקרה השלילית של הבאג בצורתו החמורה ביותר: שורה שלא עודכנה
    נשארת `waiting`, ‏`room.tick` לא מוצא שכל החברים במצב סופי, והגל
    הבא לא נפתח — סבב שלם נעצר על מכונות שכתבו בהצלחה.
    """
    deploy, anon = room_server["deploy"], room_server["anon"]
    assert deploy.post("/api/console/room",
                       json={"image_id": "img_7f3a91",
                             "target_drives": 6}).status_code == 200

    wave1 = cloner_hello(anon, CLONER1, ["S1", "S2"])["session"]["id"]
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    assert deploy.post("/api/console/room/start").status_code == 200

    # אותן מכונות בדיוק — רק צורת ה-MAC בדיווח שונה מזו שב-hello.
    report(anon, wave1, CLONER1.upper().replace(":", "-"),
           {"sda": "done", "sdb": "done"})
    report(anon, wave1, CLONER2.replace(":", ""), {"sda": "done", "sdb": "done"})

    view = room(deploy)["round"]
    assert view["written_drives"] == 4        # ארבע מגירות נספרו
    assert view["wave_number"] == 2           # והגל הבא נפתח
    assert view["wave_state"] == "open"

    events = [r["event"] for r in room_server["ctx"].conn.execute(
        "SELECT event FROM journal")]
    assert "report_from_nonmember" not in events


# --- מי רשאי להפעיל את החדר, ולא רק מי שמחובר (#152) -------------------------


def test_a_role_that_is_not_on_the_list_cannot_drive_the_room(room_server):
    """הבקרה השלילית של #152 — אותה בדיקה שנעשתה לתחנה ב-#94.

    לפני התיקון ארבע נקודות הקצה של החדר היו ``Depends(current_user)``
    בלבד: כל חשבון מחובר יכול היה להעיר את החדר ולשדר על המגירות —
    הפעולה שדורסת כל כונן מחובר.

    התפקיד ``auditor`` אינו קיים היום, ולכן זו סכימה של מחר: השאלה
    אינה מי מורשה עכשיו אלא האם הקוד **שואל**.
    """
    from test_station import add_user_with_role                # noqa: PLC0415

    add_user_with_role(room_server, "auditor", "audit-pass-12", "auditor")
    client = TestClient(room_server["app"])
    assert client.post("/api/console/login", json={
        "username": "auditor", "password": "audit-pass-12"}).status_code == 200

    # קריאה מותרת — היא אינה הרסנית
    assert client.get("/api/console/room").status_code == 200

    # וארבע הפעולות שכן משנות מצב
    assert client.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2}).status_code == 403
    assert client.post("/api/console/room/start").status_code == 403
    assert client.post("/api/console/room/wake").status_code == 403
    assert client.post("/api/console/room/close").status_code == 403

    # ולא נשלח WoL לאף מכונה בדרך
    assert room_server["woken"] == []
