"""‏#926 — הפריסה המקורית של מחיצת המקור שכווצה בקליטה: זיכרון בשרת.

‏#87 שמר את הגודל המקורי ב-`targets/<disk>/shrunk` — ‏tmpfs. אובדן חשמל
או אתחול בין `ntfsresize -s` לבין ההחזרה השאיר את דיסק הבנייה מכווץ בלי
שאיש יודע: ווינדוס עולה, ‏C: קטן, והסוכן כבר לא זוכר מה היה. אותה
משפחה כמו #856 ו-#874, ואותו פתרון: **השרת זוכר**, לפי סידורי.

* **`POST /api/v1/agent/shrink-open`** — נשלח **לפני** הכתיבה הראשונה
  למקור, עם הפריסה המלאה של הכניסה (idx, start, size, GUIDs, דגלים, שם)
  והזהות (סידורי, פורט, דגם). בלי 2xx — הכיווץ לא מתחיל (עיקרון 5).
  סידורי ריק = אין למה להצמיד זיכרון = סירוב (`no_serial`), לא רשומה
  שלא תיענה לעולם. רשומה פתוחה על אותו סידורי = 409 (`already_open`):
  הדיסק כבר מכווץ לפי הזיכרון, ואין מכווצים פעמיים.
* **`POST /api/v1/agent/shrink-close`** — אחרי החזרה מוצלחת. סגירת רשומה
  שאינה פתוחה היא 404, לא "ok" (עיקרון 5).
* **‏hello (ממשק 3, `shrink_open`)** — הרשומות הפתוחות שהסידורי שלהן הוא
  אחד מהדיסקים שהמכונה דיווחה. הסידורי הוא הזהות; ‏`port` ו-`mac` הם
  המקום, לתצוגה.
* **הקונסולה** — `GET /api/console/shrink-records` (מחובר) ו-"נקה"
  (‏`POST …/{id}/clear`, admin).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server import db, shrink_records
from test_server_api import hello, open_session

pytest.importorskip("fastapi")

MAC = "b4:2e:99:07:1a:c4"
SERIAL = "S3TWNE0JB04745"


def layout(**over) -> dict:
    body = {
        "mac": MAC, "dev": "sda", "serial": SERIAL, "port": 1, "model": "Samsung SSD 870",
        "image_name": "win11-office", "task_id": "tsk_1",
        "idx": 3, "start_sector": 1085440, "size_sectors": 998244352,
        "type_guid": "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7",
        "unique_guid": "4C7B1E00-0000-4000-8000-000000000003",
        "attrs": "0000000000000000", "name": "Basic data partition",
        "ntfs_bytes": 511101108224,
    }
    body.update(over)
    return body


@pytest.fixture()
def conn(tmp_path: Path):
    return db.connect(tmp_path / "t.db")


# --- המודול: פתיחה, סגירה, ומה hello עונה ---------------------------------------


def test_open_keeps_the_whole_entry_and_the_identity(conn):
    row = shrink_records.open_record(conn, layout())
    assert row["id"] and row["serial"] == SERIAL and row["port"] == 1
    (rec,) = shrink_records.list_open(conn)
    for key in ("idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
                "attrs", "name", "ntfs_bytes", "image_name", "model", "dev", "mac"):
        assert rec[key] == layout()[key], key
    assert rec["opened_at"]


def test_a_second_open_on_the_same_serial_is_refused_not_duplicated(conn):
    shrink_records.open_record(conn, layout())
    with pytest.raises(shrink_records.AlreadyOpen):
        shrink_records.open_record(conn, layout(mac="aa:bb:cc:dd:ee:ff", dev="sdb"))
    assert len(shrink_records.list_open(conn)) == 1


def test_a_disk_without_a_serial_cannot_be_remembered(conn):
    """סידורי ריק: הרשומה לא תיענה לעולם ל-hello — עדיף סירוב גלוי לפני
    שנכתב בייט מאשר זיכרון שאין למה להצמיד."""
    for bad in (None, "", 7):
        with pytest.raises(shrink_records.BadRecord):
            shrink_records.open_record(conn, layout(serial=bad))
    assert shrink_records.list_open(conn) == []


@pytest.mark.parametrize("field", ["idx", "start_sector", "size_sectors", "type_guid"])
def test_a_layout_field_that_is_missing_or_wrongly_typed_is_refused(conn, field):
    """הפריסה היא מה שמחזיר את הדיסק — שדה חסר בה הוא רשומה שאי-אפשר
    להחזיר ממנה, ולכן הכיווץ לא מתחיל."""
    body = layout(); del body[field]
    with pytest.raises(shrink_records.BadRecord):
        shrink_records.open_record(conn, body)
    with pytest.raises(shrink_records.BadRecord):
        shrink_records.open_record(conn, layout(**{field: "x" if field != "type_guid" else 5}))
    assert shrink_records.list_open(conn) == []


def test_close_closes_once_and_only_an_open_record(conn):
    row = shrink_records.open_record(conn, layout())
    assert shrink_records.close_record(conn, SERIAL) is True
    assert shrink_records.close_record(conn, SERIAL) is False
    assert shrink_records.list_open(conn) == []
    assert shrink_records.open_for(conn, [{"serial": SERIAL}]) == []
    # אחרי סגירה אפשר לפתוח שוב — קליטה חדשה של אותו דיסק.
    again = shrink_records.open_record(conn, layout())
    assert again["id"] != row["id"]


def test_open_for_matches_by_serial_only(conn):
    """הסידורי הוא הזהות: הדיסק שנדד למכונה אחרת או לפורט אחר עדיין
    מכווץ, והרשומה מגיעה אליו. דיסק אחר באותו חריץ אינו תואם."""
    shrink_records.open_record(conn, layout())
    assert shrink_records.open_for(conn, [{"serial": "OTHER", "port": 1}]) == []
    assert shrink_records.open_for(conn, None) == []
    (rec,) = shrink_records.open_for(conn, [{"serial": "X"}, {"serial": SERIAL, "port": 2}])
    assert rec["serial"] == SERIAL and rec["port"] == 1 and rec["mac"] == MAC
    for key in ("id", "idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
                "attrs", "name", "ntfs_bytes", "image_name", "opened_at"):
        assert key in rec, key


# --- דרך ה-API: הסוכן, hello, והקונסולה -------------------------------------------


def test_the_agent_opens_before_the_shrink_and_closes_after_the_restore(server):
    ids = open_session(server, expected=2)
    agent = server["anon"]
    opened = agent.post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"]))
    assert opened.status_code == 200 and opened.json()["ok"] is True
    rid = opened.json()["id"]
    assert isinstance(rid, int)
    # פעם שנייה על אותו סידורי: 409 בשם, לא רשומה כפולה.
    twice = agent.post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"]))
    assert twice.status_code == 409 and twice.json()["code"] == "already_open"
    # ‏hello של המכונה שמדווחת את הסידורי הזה מקבל את הרשומה.
    from conftest import hello_body
    body = hello_body(ids["mac1"])
    body["disks"][0]["serial"] = SERIAL
    answer = agent.post("/api/v1/agent/hello", json=body).json()
    (rec,) = answer["shrink_open"]
    assert rec["id"] == rid and rec["size_sectors"] == 998244352 and rec["idx"] == 3
    # מכונה עם סידורי אחר — כלום.
    assert hello(server, ids["mac2"])["shrink_open"] == []
    # הקונסולה רואה את הרשומה (גם deploy — צפייה).
    rows = server["deploy"].get("/api/console/shrink-records").json()
    assert len(rows) == 1 and rows[0]["id"] == rid and rows[0]["serial"] == SERIAL
    assert rows[0]["mac"] == ids["mac1"] and rows[0]["port"] == 1
    # סגירה מהסוכן אחרי החזרה.
    closed = agent.post("/api/v1/agent/shrink-close", json={"mac": ids["mac1"], "serial": SERIAL, "id": rid})
    assert closed.status_code == 200 and closed.json() == {"ok": True}
    assert agent.post("/api/v1/agent/hello", json=body).json()["shrink_open"] == []
    assert server["deploy"].get("/api/console/shrink-records").json() == []
    # סגירה של מה שכבר סגור: 404, לא ok.
    again = agent.post("/api/v1/agent/shrink-close", json={"mac": ids["mac1"], "serial": SERIAL})
    assert again.status_code == 404 and again.json()["code"] == "not_open"
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert events.count("shrink_open") == 1 and events.count("shrink_close") == 1


def test_a_bad_open_is_an_orderly_error_and_writes_nothing(server):
    ids = open_session(server, expected=2)
    agent = server["anon"]
    assert agent.post("/api/v1/agent/shrink-open", json=layout(mac="nope")).json()["code"] == "bad_mac"
    r = agent.post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"], serial=""))
    assert r.status_code == 400 and r.json()["code"] == "no_serial"
    body = layout(mac=ids["mac1"]); del body["size_sectors"]
    r = agent.post("/api/v1/agent/shrink-open", json=body)
    assert r.status_code == 400 and r.json()["code"] == "bad_layout"
    assert agent.post("/api/v1/agent/shrink-open", content=b"{", headers={"Content-Type": "application/json"}).status_code == 400
    assert server["admin"].get("/api/console/shrink-records").json() == []


def test_clear_from_the_console_is_admin_only_and_once(server):
    ids = open_session(server, expected=2)
    rid = server["anon"].post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"])).json()["id"]
    assert server["deploy"].post(f"/api/console/shrink-records/{rid}/clear").status_code == 403
    assert server["admin"].post(f"/api/console/shrink-records/{rid}/clear").json() == {"ok": True}
    assert server["admin"].get("/api/console/shrink-records").json() == []
    assert server["admin"].post(f"/api/console/shrink-records/{rid}/clear").status_code == 404
    assert server["admin"].post("/api/console/shrink-records/999/clear").status_code == 404
    events = [r["event"] for r in server["admin"].get("/api/console/journal").json()]
    assert events.count("shrink_cleared") == 1


# --- סקירת Fable (PR #946) ------------------------------------------------------


@pytest.mark.parametrize("field", ["idx", "size_sectors"])
def test_a_zero_index_or_size_is_not_a_layout(conn, field):
    """‏GPT מתחיל מכניסה 1, ומחיצה בת 0 סקטורים אינה מחיצה: 0 היה עובר את
    `_int` ונרשם כרשומה שאי-אפשר להחזיר ממנה."""
    with pytest.raises(shrink_records.BadRecord) as exc:
        shrink_records.open_record(conn, layout(**{field: 0}))
    assert exc.value.code == "bad_layout"
    assert shrink_records.list_open(conn) == []
    assert shrink_records.open_record(conn, layout(start_sector=0))["id"], "תחילה 0 אינה נפסלת כאן — זו בדיקה של הטבלה, לא של הרשומה"


def test_a_note_explains_an_open_record_and_reaches_the_console(server):
    """הטבלה הוחזרה אך מערכת הקבצים לא נמתחה: הרשומה נשארת פתוחה, והסוכן
    שולח **למה** — הקונסולה מציגה את הסיבה לצד הרשומה. הערה על מה שאינו
    פתוח היא 404, כמו סגירה."""
    ids = open_session(server, expected=2)
    agent = server["anon"]
    rid = agent.post("/api/v1/agent/shrink-open", json=layout(mac=ids["mac1"])).json()["id"]
    why = "המקור לא הוחזר לגודלו: ntfsresize could not grow the filesystem back"
    r = agent.post("/api/v1/agent/shrink-note", json={"mac": ids["mac1"], "serial": SERIAL, "id": rid, "note": why})
    assert r.status_code == 200 and r.json() == {"ok": True}
    (row,) = server["deploy"].get("/api/console/shrink-records").json()
    assert row["note"] == why
    from conftest import hello_body
    body = hello_body(ids["mac1"]); body["disks"][0]["serial"] = SERIAL
    assert agent.post("/api/v1/agent/hello", json=body).json()["shrink_open"][0]["note"] == why
    bad = agent.post("/api/v1/agent/shrink-note", json={"mac": ids["mac1"], "serial": SERIAL, "note": ""})
    assert bad.status_code == 400 and bad.json()["code"] == "bad_note"
    assert agent.post("/api/v1/agent/shrink-close", json={"mac": ids["mac1"], "serial": SERIAL}).json() == {"ok": True}
    gone = agent.post("/api/v1/agent/shrink-note", json={"mac": ids["mac1"], "serial": SERIAL, "note": why})
    assert gone.status_code == 404 and gone.json()["code"] == "not_open"
