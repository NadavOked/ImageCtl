"""בדיקות היחידה של לב השרת — נרמול, ספרייה, ומחזור חיי סבב.

בלי HTTP: המודולים נבדקים ישירות, עם שעון מוזרק. ה-flows המלאים דרך
ה-API נמצאים ב-test_server_api.py.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import registry, users
from server.db import connect
from server.images import ImageLibrary
from server.sessions import SessionError, SessionStore

from conftest import MANIFEST_256, MANIFEST_500, MANIFEST_LINUX, Clock, write_image


# --- נרמול MAC וסיומות -------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["B4:2E:99:07:1A:C4", "b4-2e-99-07-1a-c4", "b42e9907 1ac4", "b42e99071ac4"],
)
def test_mac_variants_normalize(raw):
    assert registry.normalize_mac(raw) == "b4:2e:99:07:1a:c4"


@pytest.mark.parametrize("raw", ["", "b4:2e:99:07:1a", "hello world!", "gg:2e:99:07:1a:c4", None])
def test_bad_macs_are_rejected(raw):
    assert registry.normalize_mac(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("5", "05"), ("05", "05"), ("27", "27"), ("ins", "INS"), ("Ins", "INS")],
)
def test_suffix_normalization(raw, expected):
    """מי שיקליד 5 ומי שיקליד 05 מקבלים את אותה רשומה, לא שתיים."""
    assert registry.normalize_suffix(raw) == expected


@pytest.mark.parametrize("raw", ["", "005", "1a", "PROF"])
def test_bad_suffixes_are_rejected(raw):
    assert registry.normalize_suffix(raw) is None


def test_paste_flags_duplicates_inside_the_paste():
    lines = registry.parse_paste(
        "b4:2e:99:07:1a:c4 01\n# הערה\n\nb42e99071ac4 02\n"
    )
    assert lines[0].error is None
    assert "שורה 1" in lines[1].error


def test_conflicting_suffix_is_an_error_not_a_note(tmp_path):
    """סעיף 10: הסיומת קבועה לנצח. סיומת שנייה לאותו MAC נדחית."""
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
    saved, _ = registry.import_lines(
        conn, "g", registry.parse_paste("b4:2e:99:07:1a:c4 01"), "t"
    )
    assert saved == 1
    saved, rejected = registry.import_lines(
        conn, "g", registry.parse_paste("b4:2e:99:07:1a:c4 INS"), "t"
    )
    assert saved == 0 and "קבועה" in rejected[0].error
    # אותה סיומת שוב — עדכון שקט, לא שגיאה.
    saved, rejected = registry.import_lines(
        conn, "g", registry.parse_paste("b4:2e:99:07:1a:c4 01"), "t"
    )
    assert saved == 1 and not rejected


# --- ספריית האימג'ים ---------------------------------------------------------


def test_broken_manifest_is_skipped_not_fatal(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    bad = tmp_path / "broken"
    bad.mkdir()
    (bad / "manifest.json").write_text("{not json", encoding="utf-8")
    missing = tmp_path / "missing_fields"
    missing.mkdir()
    (missing / "manifest.json").write_text(
        json.dumps({"schema": 1, "id": "img_x"}), encoding="utf-8"
    )
    library = ImageLibrary(tmp_path)
    assert set(library.scan()) == {"img_7f3a91"}


def test_a_work_area_is_not_part_of_the_library(tmp_path):
    """‏#71: מה שיושב באזור עבודה של השרת — ייבוא או קליטה — עדיין לא
    אומת, ולכן אינו אימג': לא ברשימה, לא בבחירה, ולא כמועמד לסבב."""
    write_image(tmp_path, MANIFEST_256)
    write_image(tmp_path / ".import-9f2c14ab", MANIFEST_500)
    write_image(tmp_path / ".capture-tsk_1a", MANIFEST_LINUX)

    library = ImageLibrary(tmp_path)
    assert set(library.scan()) == {"img_7f3a91"}
    assert [image["id"] for image in library.public_list()] == ["img_7f3a91"]
    assert library.get("img_2c8e04") is None
    assert library.file_path("img_lnx001", "p1.esp.pcl.zst") is None
    assert library.allowed_for_disks(None) == ["img_7f3a91"]


def test_two_folders_claiming_one_id_serve_neither(tmp_path):
    """מזהה ששתי תיקיות מצהירות עליו הוא מזהה שאיננו יודעים מה הוא.
    ‏"הראשונה לפי הסדר" היא הכרעה שקטה לפי מיון נתיבים — והיא נפרסת על
    כיתה שלמה (עיקרון 5)."""
    write_image(tmp_path / "a", MANIFEST_256)
    write_image(tmp_path / "z", MANIFEST_256)
    write_image(tmp_path, MANIFEST_500)

    library = ImageLibrary(tmp_path)
    assert set(library.scan()) == {"img_2c8e04"}
    assert library.get("img_7f3a91") is None
    assert library.allowed_for_disks(None) == ["img_2c8e04"]


def test_file_serving_is_a_whitelist(tmp_path):
    """רק קובץ שהמניפסט מכריז עליו. שם אחר — גם אם הקובץ קיים — לא מוגש."""
    write_image(tmp_path, MANIFEST_256)
    (tmp_path / MANIFEST_256["id"] / "secret.txt").write_text("x")
    library = ImageLibrary(tmp_path)
    assert library.file_path("img_7f3a91", "p1.esp.pcl.zst") is not None
    assert library.file_path("img_7f3a91", "secret.txt") is None
    assert library.file_path("img_7f3a91", "../secret.txt") is None
    assert library.file_path("img_7f3a91", "manifest.json") is None


def test_size_filtering_matches_the_family_rule(tmp_path, images_root):
    """אימג' 256 מותר בכונן 256 ו-500; אימג' 500 רק ב-500 (סעיף 9)."""
    library = ImageLibrary(images_root)
    disk_256 = [{"size_bytes": 256060514304, "removable": False}]
    disk_500 = [{"size_bytes": 500107862016, "removable": False}]
    usb_only = [{"size_bytes": 999999999999, "removable": True}]
    assert library.allowed_for_disks(disk_256) == ["img_7f3a91"]
    assert library.allowed_for_disks(disk_500) == ["img_2c8e04", "img_7f3a91"]
    assert library.allowed_for_disks(usb_only) == []


# --- מחזור חיי סבב -----------------------------------------------------------


@pytest.fixture()
def store(tmp_path):
    conn = connect(tmp_path / "s.db")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g', 'g', 'classroom')")
    clock = Clock()
    return SessionStore(conn, now_fn=clock), clock


def test_only_one_active_session_ever(store):
    sessions, _ = store
    sessions.open("g", "img_1", "LAB1", 30, "noc")
    with pytest.raises(SessionError):
        sessions.open("g", "img_2", "LAB2", 30, "noc")


def test_every_joiner_resets_the_timer(store):
    """התנאי הכפול מסעיף 13.3: הטיימר נמדד מהמצטרף האחרון."""
    sessions, clock = store
    sid = sessions.open("g", "img_1", "LAB1", 30, "noc", wait_seconds=300)
    session = sessions.active()
    sessions.record_hello(session, "aa:aa:aa:aa:aa:01")
    clock.advance(200)
    assert sessions.starts_in_seconds(sessions.active()) == 100
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:02")
    assert sessions.starts_in_seconds(sessions.active()) == 300
    # הצטרפות חוזרת של אותה מכונה לא מאפסת.
    clock.advance(50)
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:02")
    assert sessions.starts_in_seconds(sessions.active()) == 250
    assert sessions.joined_count(sid) == 2


def test_timer_expiry_starts_the_round(store):
    sessions, clock = store
    sessions.open("g", "img_1", "LAB1", 30, "noc", wait_seconds=300)
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:01")
    clock.advance(301)
    assert sessions.maybe_start(sessions.active())["state"] == "running"


def test_empty_round_never_starts_by_itself(store):
    """סבב בלי אף מצטרף לא "מבשיל" — הוא נסגר מהקונסולה."""
    sessions, clock = store
    sessions.open("g", "img_1", "LAB1", 30, "noc", wait_seconds=300)
    clock.advance(10_000)
    assert sessions.maybe_start(sessions.active())["state"] == "open"


def test_reaching_the_declared_count_starts_the_round(store):
    sessions, _ = store
    sessions.open("g", "img_1", "LAB1", 2, "noc")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:01")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:02")
    assert sessions.maybe_start(sessions.active())["state"] == "running"


def _running_round(sessions, group="g", image="img_1", prefix="LAB1"):
    sid = sessions.open(group, image, prefix, 2, "noc")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:01")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:02")
    sessions.maybe_start(sessions.active())
    return sid


def _finish_member(sessions, sid, mac, state="done"):
    sessions.conn.execute(
        "UPDATE session_members SET state = ?, done = ?"
        " WHERE session_id = ? AND mac = ?",
        (state, 1 if state == "done" else 0, sid, mac))
    sessions.conn.commit()


def test_a_spent_round_yields_to_the_next_one(store):
    """‏#35: סבב שכל חבריו סיימו (או נכשלו) נשאר מוצג לסיכום — אבל פתיחה
    חדשה מפנה אותו במקום להיתקע על session_conflict לנצח."""
    sessions, _ = store
    sid = _running_round(sessions)
    _finish_member(sessions, sid, "aa:aa:aa:aa:aa:01")
    _finish_member(sessions, sid, "aa:aa:aa:aa:aa:02", state="failed")

    new_id = sessions.open("g", "img_2", "LAB2", 2, "noc")
    old = sessions.conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (sid,)).fetchone()
    assert old["state"] == "closed"
    assert sessions.active()["id"] == new_id
    events = [r["event"] for r in
              sessions.conn.execute("SELECT event FROM journal")]
    assert "session_autoclose" in events


def test_a_round_with_a_straggler_still_blocks(store):
    """מחשב אחד עוד כותב — הסבב באמת פעיל, והחסימה נשארת."""
    sessions, _ = store
    sid = _running_round(sessions)
    _finish_member(sessions, sid, "aa:aa:aa:aa:aa:01")
    with pytest.raises(SessionError):
        sessions.open("g", "img_2", "LAB2", 2, "noc")


def test_an_open_round_is_never_evicted(store):
    """סבב שעוד לא התחיל מחכה למצטרפים — פתיחה שנייה נכשלת כרגיל."""
    sessions, _ = store
    sessions.open("g", "img_1", "LAB1", 2, "noc")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:01")
    with pytest.raises(SessionError):
        sessions.open("g", "img_2", "LAB2", 2, "noc")


def test_a_spent_cloner_wave_is_left_to_the_room(store):
    """גל חדר שיכפולים גמור אינו מפונה מכאן — room.py מנהל את הגלים,
    ופינוי מבחוץ היה שומט את חשבון הכוננים של הסבב המצטבר."""
    sessions, _ = store
    sessions.conn.execute(
        "INSERT INTO groups (id, label, role) VALUES ('c', 'c', 'cloner')")
    sid = sessions.open("c", "img_1", "ROOM", 2, "noc")
    sessions.record_hello(sessions.active(), "aa:aa:aa:aa:aa:01")
    sessions.start_now(sid, "noc")
    _finish_member(sessions, sid, "aa:aa:aa:aa:aa:01")
    with pytest.raises(SessionError):
        sessions.open("g", "img_2", "LAB2", 2, "noc")


# --- משתמשים -----------------------------------------------------------------


def test_password_verification_round_trip(tmp_path):
    conn = connect(tmp_path / "u.db")
    users.create(conn, "noc", "correct-horse-1", "admin", by="t")
    assert users.verify(conn, "noc", "correct-horse-1") == "admin"
    assert users.verify(conn, "noc", "wrong") is None
    assert users.verify(conn, "ghost", "whatever") is None


def test_short_passwords_are_refused(tmp_path):
    conn = connect(tmp_path / "u.db")
    with pytest.raises(ValueError):
        users.create(conn, "x", "short", "deploy", by="t")
