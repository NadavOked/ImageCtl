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


# --- #405: שם לא-כיתתי ארוך מדי נדחה ברישום, לא בפתיחת הסבב -------------------

#: הגבול נגזר בטסט בדיוק כמו בקוד — מ-HOSTNAME_MAX (קיים גם על ה-base)
#: ומהקידומת הארוכה ביותר ("ROOM") — ולא כמספר קסם, כדי שהבקרה השלילית
#: תיכשל **התנהגותית** ולא ב-ImportError על הסמל החדש.
from server.sessions import HOSTNAME_MAX          # noqa: E402
_NAME_MAX = HOSTNAME_MAX - len("ROOM") - 1        # = 10


@pytest.mark.parametrize("role", ["cloner", "build"])
def test_a_nonclassroom_name_too_long_for_a_hostname_is_rejected(role):
    """שם לא-כיתתי נכנס לשם המחשב אחרי קידומת ומקף. שם שאורכו חורג
    מ-HOSTNAME_MAX נדחה בנרמול עצמו, ולכן ברישום — לא בפתיחת הסבב."""
    ok = "x" * _NAME_MAX
    assert registry.normalize_name(role, ok) == ok
    assert registry.normalize_name(role, "x" * (_NAME_MAX + 1)) is None
    # הדוגמה מ-#405: HP-cloner-1 הוא 11 תווים.
    assert registry.normalize_name(role, "HP-cloner-1") is None


def test_a_too_long_cloner_name_is_rejected_at_import_with_both_numbers(tmp_path):
    """זה המסלול שבו נדב רשם בפועל — parse_paste/import_lines. הדחייה
    מגיעה ברישום, וההודעה נוקבת בשני המספרים: התקרה ותקרת שם המחשב."""
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('c', 'c', 'cloner')")
    lines = registry.parse_paste("aa:bb:cc:dd:ee:01 HP-cloner-1", "cloner")
    assert lines[0].error is not None
    assert str(_NAME_MAX) in lines[0].error and str(HOSTNAME_MAX) in lines[0].error
    saved, rejected = registry.import_lines(conn, "c", lines, "t")
    assert saved == 0 and len(rejected) == 1


def test_add_machine_rejects_a_name_too_long_for_a_hostname(tmp_path):
    """המסלול הידני (add_machine) דוחה באותה תקרה, ובאותם שני מספרים."""
    conn = connect(tmp_path / "t.db")
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('c', 'c', 'cloner')")
    with pytest.raises(ValueError) as exc:
        registry.add_machine(conn, "aa:bb:cc:dd:ee:01", "HP-cloner-1", "c", "t")
    assert str(_NAME_MAX) in str(exc.value) and str(HOSTNAME_MAX) in str(exc.value)


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


def test_public_list_exposes_source_disk_bytes_from_manifest(tmp_path):
    # Mutation caught: dropping source_disk_bytes from public_list() makes this key assertion fail.
    write_image(tmp_path, MANIFEST_256)

    image = ImageLibrary(tmp_path).public_list()[0]

    assert image["source_disk_bytes"] == MANIFEST_256["source_disk_bytes"]


def test_public_list_marks_missing_source_disk_bytes_unknown(tmp_path):
    # Mutation caught: defaulting a missing source_disk_bytes to 0 falsely displays it as a real 0 B disk.
    manifest = {key: value for key, value in MANIFEST_256.items() if key != "source_disk_bytes"}
    write_image(tmp_path, manifest)

    image = ImageLibrary(tmp_path).public_list()[0]

    assert image["source_disk_bytes"] is None


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


@pytest.mark.parametrize("bad_sha256", [
    "abc",
    "A" * 64,
    "a" * 63,
])
def test_an_image_without_a_canonical_sha256_is_not_served(tmp_path, bad_sha256):
    write_image(tmp_path, MANIFEST_256)
    manifest_path = tmp_path / MANIFEST_256["id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["partitions"][0]["sha256"] = bad_sha256
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    library = ImageLibrary(tmp_path)
    visible = library.get(MANIFEST_256["id"]) is not None
    served = library.file_path(
        MANIFEST_256["id"], manifest["partitions"][0]["file"]
    ) is not None

    assert (visible, served) == (False, False)


@pytest.mark.parametrize("field", ["start_sector", "size_bytes"])
def test_zero_partition_geometry_is_not_served(tmp_path, field):
    write_image(tmp_path, MANIFEST_256)
    manifest_path = tmp_path / MANIFEST_256["id"] / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["partitions"][0][field] = 0
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    library = ImageLibrary(tmp_path)

    assert library.get(MANIFEST_256["id"]) is None


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


def test_open_round_error_names_the_offending_machine(store):
    """‏#405: שם ארוך שכבר במרשם (נרשם לפני שהאכיפה עברה לרישום) עדיין
    חוסם את פתיחת הסבב — אבל ההודעה נוקבת ב**מכונה**, לא רק במספר, כדי
    שמי שפותח את הסבב על כל הקבוצה ידע מי חוסם אותו."""
    sessions, _ = store
    sessions.conn.execute(
        "INSERT INTO machines (mac, suffix, group_id, added_at)"
        " VALUES ('aa:aa:aa:aa:aa:aa', 'HP-cloner-1', 'g', '2026-01-01')")
    with pytest.raises(SessionError) as exc:
        sessions.open("g", "img_1", "ROOM", 2, "noc")
    assert "HP-cloner-1" in str(exc.value) and "11" in str(exc.value)


# --- משתמשים -----------------------------------------------------------------


def test_password_verification_round_trip(tmp_path):
    conn = connect(tmp_path / "u.db")
    users.create(conn, "noc", "correct-horse-1", "admin", by="t", is_builtin=True, check_policy=False)
    assert users.verify(conn, "noc", "correct-horse-1") == "admin"
    assert users.verify(conn, "noc", "wrong") is None
    assert users.verify(conn, "ghost", "whatever") is None


def test_short_passwords_are_refused(tmp_path):
    conn = connect(tmp_path / "u.db")
    with pytest.raises(ValueError, match="8 תווים"):
        users.create(conn, "xy", "short", "deploy", by="t")
