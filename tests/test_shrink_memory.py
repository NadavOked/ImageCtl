"""‏#926 — הצד של הסוכן: הרשומה בשרת לפני הכיווץ, וההצעה להחזיר באתחול.

‏#87 שמר את הגודל המקורי של מחיצת המקור ב-`targets/<disk>/shrunk` — ‏tmpfs.
אובדן חשמל בין `ntfsresize -s` לבין ההחזרה השאיר את דיסק הבנייה מכווץ
בלי שאיש יודע. ‏`agent/lib/shrinkmem.sh`:

* **לפני הכתיבה הראשונה** — `shrink-open` לשרת (‏`shrink_open_record`);
  בלי 200+ok+id **אין** `ntfsresize -s` (עיקרון 5). דיסק בלי סידורי, שרת
  שענה 500/000, או 409 "כבר פתוח" — סירובים נבדלים, בשם, לפני שנכתב בייט.
* **אחרי ההחזרה** — `shrink-close`; החזרה שנכשלה משאירה את הרשומה פתוחה.
* **באתחול** (‏`shrink_offer_pending`, מלולאת מחשב הבנייה): הרשומה מה-hello
  מושווית לכניסה **על הדיסק** (‏sgdisk -i), ורק אם התחילה וה-GUID הייחודי
  תואמים והגודל קטן — שאלה ליד המכונה: החזר / השאר. **בלי תשובה — השאר.**
  ההחזרה היא shrink_restore_source עם הנתונים מהרשומה, לא מ-tmpfs.

הכלים ההרסניים מזויפים כמו ב-`test_shrink_on_capture.py`, וכל צעד נמדד
על הסדר ועל הארגומנטים — לא על "רץ בלי שגיאה".
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix
from test_capture_refusals import CURL_SHRINK_SERVER, JQ_STUB, capture_run, refusal_reason
from test_shrink_on_capture import (
    BLOCKDEV_OK,
    FS_MAP,
    MIN_BYTES,
    NTFSFIX_OK,
    NTFSRESIZE_GROW_FAILS,
    NTFSRESIZE_OK,
    SAY_YES,
    SGDISK_WIN_REC,
    WIN_GUID,
    WIN_SECTORS,
    WIN_START,
    WIN_UGUID,
    manifest,
    order,
    rewrites,
    shrink_target,
    stubs,
)
from test_timeouts import log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")

SERIAL = "S926"


def sent(run: Path, name: str) -> list[dict]:
    path = run / f"{name}.sent"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def step(steps: list[str], prefix: str) -> int:
    for i, line in enumerate(steps):
        if line.startswith(prefix):
            return i
    raise AssertionError(f"{prefix!r} לא נמצא ב-{steps}")


# --- בקליטה: הרשומה לפני הכיווץ, הסגירה אחרי ההחזרה --------------------------------


def test_the_record_is_opened_before_the_first_write_and_closed_after_the_grow_back(tmp_path):
    """הסדר: ‏shrink-open לפני `ntfsresize -s`, ‏shrink-close אחרי ה-ntfsfix
    האחרון של ההחזרה. הגוף נושא את הכניסה המלאה והזהות; הסגירה — את
    הסידורי ואת ה-id שהשרת נתן; והסימון המקומי (shrink_id) נמחק."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    steps = order(run)
    opened = step(steps, "shrink-open")
    assert opened < step(steps, "ntfsresize -s "), steps
    assert step(steps, "ntfsresize --info") < opened, "ההרשמה אחרי המדידה — לא נרשם כיווץ שלא יקרה"
    grow = step(steps, "ntfsresize -f -f")
    closed = step(steps, "shrink-close")
    assert grow < closed, steps
    assert steps.index(f"ntfsfix -d {posix(box / 'dev')}/sda3", grow) < closed

    (body,) = sent(run, "shrink_open")
    assert body["serial"] == SERIAL and body["port"] == 1 and body["dev"] == "sda"
    assert body["idx"] == 3 and body["start_sector"] == WIN_START and body["size_sectors"] == WIN_SECTORS
    assert body["type_guid"] == WIN_GUID and body["unique_guid"] == WIN_UGUID
    assert body["attrs"] == "0000000000000000" and body["name"] == "Basic data partition"
    assert body["ntfs_bytes"] == WIN_SECTORS * 512
    (close,) = sent(run, "shrink_close")
    assert close["serial"] == SERIAL and close["id"] == 7
    assert not (run / "targets" / "sda" / "shrink_id").exists()


def test_the_serial_and_the_task_come_from_sysfs_and_the_hello_answer(tmp_path):
    """הזהות נקראת מ-sysfs (device/serial, device/model) והמשימה מתשובת
    ה-hello (task.name/id) — לא ממשתנים שהבדיקה שתלה."""
    box = tmp_path / "box"
    dev_dir = box / "sys" / "block" / "sda" / "device"
    dev_dir.mkdir(parents=True)
    (dev_dir / "serial").write_text("WD-REAL-1\n", encoding="utf-8")
    (dev_dir / "model").write_text("WDC WDS500\n", encoding="utf-8")
    resp = tmp_path / "response.json"
    resp.write_text(json.dumps(
        {"task": {"id": "tsk_106", "type": "capture", "disk": "sda", "name": "win11-office"}}), encoding="utf-8")
    no_serial_fake = FS_MAP.replace('disk_serial() { printf S926; }; disk_port() { printf 1; }; ', "")
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=no_serial_fake + SAY_YES,
                                env={"RESP": posix(resp)})
    assert out.strip().endswith("rc=0"), out
    (body,) = sent(run, "shrink_open")
    assert body["serial"] == "WD-REAL-1" and body["model"] == "WDC WDS500"
    assert body["image_name"] == "win11-office" and body["task_id"] == "tsk_106"
    assert body["port"] is None, "בקופסה אין ata<N> — פורט לא ידוע הוא null, לא 0"


@pytest.mark.parametrize("http", ["500", "000"])
def test_a_server_that_did_not_record_the_layout_means_no_shrink(tmp_path, http):
    """‏500 = השרת סירב, ‏000 = לא נשאל כלל (curl 7). בשניהם: אין
    `ntfsresize -s`, אין טבלה, והסיבה נוקבת בקוד — לא "נכשל"."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SHRINK_OPEN_HTTP": http})
    reason = refusal_reason(box, run, out)
    assert "השרת לא רשם" in reason and f"http {http}" in reason, reason
    assert "הדיסק לא שונה" in reason
    steps = order(run)
    assert not [s for s in steps if s.startswith(("ntfsresize -s", "ntfsfix", "ntfsresize -f"))], steps
    assert not rewrites(run)
    assert not (run / "new-manifest.json").exists()
    assert len(sent(run, "shrink_open")) == 1, "הניסיון נעשה — פעם אחת"


def test_a_record_that_is_already_open_on_the_server_refuses_in_words(tmp_path):
    """‏409: לפי זיכרון השרת הדיסק כבר מכווץ מקליטה קודמת. לא מכווצים
    פעמיים, והסיבה אומרת מה לעשות (החזרה באתחול / "נקה")."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SHRINK_OPEN_HTTP": "409"})
    reason = refusal_reason(box, run, out)
    assert "כבר מכווץ" in reason and "#3" in reason and "נקו את הרשומה" in reason, reason
    assert not [s for s in order(run) if s.startswith("ntfsresize -s")]
    assert not rewrites(run)


def test_a_disk_without_a_serial_is_not_shrunk_and_the_server_is_not_asked(tmp_path):
    no_serial = FS_MAP.replace("printf S926", ":")
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=no_serial + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "אין מספר סידורי" in reason and "קלטו בלי לכווץ" in reason, reason
    assert sent(run, "shrink_open") == []
    assert not [s for s in order(run) if s.startswith("ntfsresize -s")]
    assert not rewrites(run)


def test_a_pending_record_in_the_hello_answer_refuses_before_the_question(tmp_path):
    """ה-hello שהביא את המשימה אומר שהדיסק הזה כבר מכווץ (רשומה #5):
    הקליטה נעצרת לפני שאדם נשאל — ולפני שהשרת נשאל שוב."""
    pending = ('json_get_join() { echo ok; echo "5|S926|3|1085440|998244352|' + WIN_GUID + '|' + WIN_UGUID
               + '|Basic data partition|0000000000000000|511101108224|t"; }; '
               'shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo continue; }; ')
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + pending)
    reason = refusal_reason(box, run, out)
    assert "כבר כווצה" in reason and "#5" in reason and "מחיצה 3" in reason, reason
    assert "asked" not in order(run)
    assert sent(run, "shrink_open") == []
    assert not rewrites(run)


def test_a_failed_grow_back_leaves_the_record_open(tmp_path):
    """ההחזרה נכשלה (ntfsresize -f -f) — הדיסק עדיין מכווץ, והרשומה
    **נשארת** בשרת: אין shrink-close. האזהרה של #87 נשארת בשדה error."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=NTFSRESIZE_GROW_FAILS), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    assert sent(run, "shrink_close") == []
    assert "shrink-close" not in order(run)
    assert "המקור לא הוחזר לגודלו" in (run / "targets" / "sda" / "error").read_text(encoding="utf-8")


def test_a_close_the_server_refused_is_a_warning_not_a_failure(tmp_path):
    """הדיסק כבר בגודלו; רשומה שלא נסגרה נשארת כתומה בקונסולה עד "נקה"
    (או עד האתחול הבא). הקליטה עצמה הצליחה — rc=0, מניפסט קיים."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SHRINK_CLOSE_HTTP": "404"})
    assert out.strip().endswith("rc=0"), out
    assert (run / "new-manifest.json").exists()
    assert len(sent(run, "shrink_close")) == 1
    assert "did not close shrink record 7" in log_of(run)
    assert "http 404" in log_of(run)


# --- באתחול: ההצעה להחזיר, על מחשב הבנייה ------------------------------------------

SHRUNK_SECTORS = shrink_target(MIN_BYTES, WIN_SECTORS * 512) // 512


def record(**over) -> dict:
    rec = {"id": 5, "serial": SERIAL, "idx": 3, "start_sector": WIN_START, "size_sectors": WIN_SECTORS,
           "type_guid": WIN_GUID, "unique_guid": WIN_UGUID, "name": "Basic data partition",
           "attrs": "0000000000000000", "ntfs_bytes": WIN_SECTORS * 512,
           "opened_at": "2026-09-16T20:00:00+00:00", "mac": "b4:2e:99:07:1a:c4", "port": 1,
           "dev": "sda", "model": "Samsung SSD", "image_name": "win11", "task_id": "tsk_1"}
    rec.update(over)
    return rec


def record_line(rec: dict) -> str:
    keys = ("id", "serial", "idx", "start_sector", "size_sectors", "type_guid", "unique_guid",
            "name", "attrs", "ntfs_bytes", "opened_at")
    return "|".join(str(rec[k]) for k in keys)


def fake_jq(*records: dict) -> str:
    """‏json_get_join מזויף: מה ש-jq היה מדפיס על התשובה — "ok" ואז שורה
    לרשומה (הביטוי האמיתי נבדק ב-test_the_real_jq_expression…)."""
    body = " ".join(f"echo {line!r};" for line in ("ok", *(record_line(r) for r in records)))
    return 'json_get_join() { [ -s "$1" ] || return 1; ' + body + ' }; '


def offer_run(tmp_path, *, answer_records: list[dict] | None, keys: str = "",
              disk_sectors: int = SHRUNK_SECTORS, prelude: str = "", calls: int = 1,
              extra_stubs: dict | None = None):
    """מריץ `shrink_offer_pending` (מה שלולאת מחשב הבנייה מריצה לפני
    התפריט) מול דיסק מזויף שהכניסה 3 שלו היא `disk_sectors` סקטורים."""
    box = tmp_path / "box"
    run = box / "run"
    run.mkdir(parents=True)
    (box / "dev").mkdir()
    (box / "dev" / "sda").write_bytes(bytes(1024))
    sysd = box / "sys" / "block" / "sda"
    (sysd / "device").mkdir(parents=True)
    (sysd / "queue").mkdir()
    (sysd / "device" / "serial").write_text(SERIAL + "\n", encoding="utf-8")
    (sysd / "queue" / "logical_block_size").write_text("512\n", encoding="utf-8")
    (sysd / "removable").write_text("0\n", encoding="utf-8")
    (run / "sgdisk.sizes").write_text(f"3={disk_sectors}\n4=2048000\n", encoding="utf-8", newline="\n")
    if answer_records is not None:
        (run / "response.json").write_text(json.dumps({"schema": 1, "known": True, "role": "build",
                                                       "shrink_open": answer_records}),
                                           encoding="utf-8", newline="\n")
    stub = {"sgdisk": SGDISK_WIN_REC, "ntfsresize": NTFSRESIZE_OK, "ntfsfix": NTFSFIX_OK,
            "blockdev": BLOCKDEV_OK, "curl": CURL_SHRINK_SERVER}
    stub.update(extra_stubs or {})
    if JQ is None:
        stub["jq"] = JQ_STUB
    libs = " ".join(f". {posix(AGENT)}/lib/{n}.sh;" for n in (
        "common", "sysinfo", "waits", "jsonq", "restore", "ui", "attended", "shrink", "shrinkmem"))
    # קלט מקובץ ולא מצינור: פונקציה בצינור רצה בתת-מעטפת, ו-`_shrink_offered`
    # (פעם אחת לאתחול) לא היה שורד — בסוכן הקריאה ישירה, כמו כאן.
    (run / "keys.txt").write_text(keys, encoding="utf-8", newline="\n")
    keys_sh = f"< {posix(run / 'keys.txt')!r} "
    out = run_sh(
        make_stubs(box / "stubs", stub)
        + f"export IMAGECTL_TEST=1 RUN_DIR={posix(run)!r} DEVROOT={posix(box / 'dev')!r} "
        f"SYSROOT={posix(box)!r} SERVER=http://s MAC=b4:2e:99:07:1a:c4; "
        + libs + " attended_hello() { :; }; " + prelude
        + " ".join(f"{keys_sh}shrink_offer_pending; echo \"rc=$?\";" for _ in range(calls))
    )
    return box, run, out


def test_a_pending_record_asks_and_one_grows_the_partition_back(tmp_path):
    """הדיסק עומד על הגודל המכווץ, הרשומה תואמת (תחילה + GUID ייחודי):
    השאלה מוצגת, "1" מחזיר — הטבלה **מהרשומה** (הגודל המקורי, GUID, שם,
    דגלים), ואז ntfsresize -f -f, ‏ntfsfix -d, ואז shrink-close עם ה-id."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()))
    assert "rc=0" in out, out
    assert "was shrunk for a capture" in out and "[1] Grow it back" in out, out
    (call,) = rewrites(run)
    assert call.startswith("-a 1 -d 3 -n 3:"), call
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1} -t 3:{WIN_GUID} -u 3:{WIN_UGUID} -c 3:Basic data partition -A 3:=:0x0000000000000000" in call, call
    steps = order(run)
    node = f"{posix(box / 'dev')}/sda3"
    reread = step(steps, "blockdev --rereadpt")
    grow = step(steps, f"ntfsresize -f -f --no-progress-bar {node}")
    fix = step(steps, f"ntfsfix -d {node}")
    close = step(steps, "shrink-close")
    assert reread < grow < fix < close, steps
    assert not [s for s in steps if s.startswith("ntfsresize -s")], "החזרה, לא כיווץ"
    (body,) = sent(run, "shrink_close")
    assert body["serial"] == SERIAL and body["id"] == 5
    assert "back to its original size" in out
    assert "restored to" in log_of(run)


@pytest.mark.parametrize("keys", ["2\n", ""])
def test_leave_and_no_answer_write_nothing(tmp_path, keys):
    """‏[2] וגם EOF (מסך שנעלם): שום דבר לא נכתב, הרשומה נשארת פתוחה —
    עיקרון 1, שום כתיבה על דיסק בלי אדם שאמר כן."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys=keys, prelude=fake_jq(record()))
    assert "rc=0" in out, out
    assert "[1] Grow it back" in out, "השאלה כן הוצגה"
    assert rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == []
    assert "left at" in log_of(run) and "stays open" in log_of(run)


def test_a_partition_already_at_its_size_closes_the_record_without_asking(tmp_path):
    """הסגירה של הקליטה הקודמת לא הגיעה לשרת (או שהמפעיל הרחיב בעצמו):
    הדיסק כבר בגודלו — הרשומה נסגרת, בלי שאלה ובלי כתיבה."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()) + 'shrink_restore_ask() { echo asked >> "$RUN_DIR/order.log"; echo restore; }; ')
    assert "rc=0" in out, out
    assert "asked" not in order(run)
    assert rewrites(run) == []
    # ‏ntfsresize --info (קריאה בלבד) כן רץ: הוא מה שמוכיח שמערכת הקבצים ממלאת
    # את המחיצה (סקירת Fable) — כתיבה לא.
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsresize -f", "ntfsfix"))]
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5 and body["serial"] == SERIAL
    assert "already" in log_of(run) and "closing the record" in log_of(run)


@pytest.mark.parametrize("field, value", [("unique_guid", "4C7B1E00-0000-4000-8000-00000000BEEF"),
                                          ("start_sector", WIN_START + 2048)])
def test_an_entry_that_does_not_match_the_record_is_not_touched(tmp_path, field, value):
    """הרשומה אומרת מחיצה אחרת ממה שעל הדיסק (GUID ייחודי / תחילה):
    לא שואלים, לא כותבים, לא סוגרים — אזהרה ביומן שאומרת מה נקרא."""
    rec = record(**{field: value})
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n",
                              prelude=fake_jq(rec) + 'shrink_restore_ask() { echo asked >> "$RUN_DIR/order.log"; echo restore; }; ')
    assert "rc=0" in out, out
    assert "asked" not in order(run)
    assert rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == []
    assert "does not match the disk" in log_of(run) and "nothing changed" in log_of(run)


def test_a_failed_grow_back_at_boot_shows_the_error_and_keeps_the_record(tmp_path):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()),
                              extra_stubs={"ntfsresize": NTFSRESIZE_GROW_FAILS})
    # ההצעה אינה עוצרת את המכונה (rc=0 של shrink_offer_pending) — הכשל
    # נראה על המסך ונשאר בשרת, ומחשב הבנייה ממשיך לתפריט.
    assert "rc=0" in out, out
    assert "FAILED:" in out and "המקור לא הוחזר לגודלו" in out, out
    assert "The record stays in the console" in out
    assert sent(run, "shrink_close") == []


def test_the_question_is_asked_once_per_boot_and_nothing_pending_asks_nobody(tmp_path):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="2\n", prelude=fake_jq(record()), calls=3)
    assert out.count("[1] Grow it back") == 1, out
    assert out.count("rc=0") == 3

    box, run, out = offer_run(tmp_path / "none", answer_records=[], keys="1\n", prelude=fake_jq())
    assert "rc=0" in out and "[1] Grow it back" not in out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []

    # תשובה שלא נקראה (jq נפל) אינה "אין רשומות": לא שואלים, ולא מכריזים על נקי.
    box, run, out = offer_run(tmp_path / "unreadable", answer_records=[record()], keys="1\n",
                              prelude='json_get_join() { return 5; }; ')
    assert "rc=0" in out and "[1] Grow it back" not in out
    assert not (run / "shrink_open").exists() and not (run / "shrink_open.next").exists()


def test_a_disk_with_another_serial_is_not_the_one(tmp_path):
    """הרשומה על S-OTHER; הדיסק כאן S926 — לא מציעים ולא נוגעים."""
    rec = record(serial="S-OTHER")
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", prelude=fake_jq(rec))
    assert "rc=0" in out and "[1] Grow it back" not in out, out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []


# --- סקירת Fable (PR #946), ממצא 2: הטבלה הוחזרה אך מערכת הקבצים לא נמתחה ---------

#: ‏ntfsresize שמדווח ב---info ווליום **קטן** בתוך מחיצה בגודלה המלא —
#: המצב אחרי "טבלה נכתבה, ntfsresize -f -f נפל" (או אחרי אתחול באמצע):
#: ווינדוס אינו יכול להרחיב (אין שטח לא-מוקצה), והמחיצה "בגודלה".
NTFSRESIZE_SMALL_FS = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "Cluster size       : 4096 bytes"
            echo "Current volume size: {SHRUNK_SECTORS * 512 - 512} bytes"
            exit 0 ;;
  *"-f -f"*) exit "${{NTFSRESIZE_GROW_RC:-0}}" ;;
esac
exit 0
'''
#: ‏--info שלא הדפיס גודל ווליום כלל: "לא הצלחנו לבדוק" ≠ "מתאים".
NTFSRESIZE_INFO_BLIND = '#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\necho "ERROR: NTFS is inconsistent"\nexit 1\n'


def test_a_full_partition_with_a_small_filesystem_is_offered_a_grow_not_closed_silently(tmp_path):
    """הכניסה כבר בגודלה המקורי, אבל מערכת הקבצים בפנים עדיין ~110GB: סגירה
    שקטה הייתה משאירה דיסק שווינדוס לא יכול להרחיב ואף אחד לא רואה. במקום
    זה: שאלה ליד המכונה — [1] מותח את מערכת הקבצים (ntfsresize -f -f →
    ntfsfix -d) **בלי לגעת בטבלה**, ורק אז shrink-close."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out, out
    assert "[1] Grow the filesystem" in out and "smaller than its partition" in out, out
    assert rewrites(run) == [], "הטבלה כבר נכונה — לא נכתבת שוב"
    steps = order(run)
    node = f"{posix(box / 'dev')}/sda3"
    info = step(steps, f"ntfsresize --info --no-progress-bar {node}")
    grow = step(steps, f"ntfsresize -f -f --no-progress-bar {node}")
    fix = step(steps, f"ntfsfix -d {node}")
    close = step(steps, "shrink-close")
    assert info < grow < fix < close, steps
    (body,) = sent(run, "shrink_close")
    assert body["id"] == 5
    assert "filesystem grown" in log_of(run)


@pytest.mark.parametrize("keys", ["2\n", ""])
def test_leaving_a_small_filesystem_keeps_the_record_open(tmp_path, keys):
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys=keys, disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out and "[1] Grow the filesystem" in out, out
    assert not [s for s in order(run) if s.startswith(("ntfsresize -f", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == [] and rewrites(run) == []
    assert "stays open" in log_of(run)


def test_a_failed_filesystem_grow_keeps_the_record_open_with_the_reason(tmp_path):
    """‏[1] ו-ntfsresize -f -f נופל: אין close, המסך אומר FAILED, והסיבה
    נשלחת לשרת (shrink-note) כדי שהקונסולה תראה **למה** הרשומה פתוחה."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()) + "export NTFSRESIZE_GROW_RC=5; ",
                              extra_stubs={"ntfsresize": NTFSRESIZE_SMALL_FS})
    assert "rc=0" in out and "FAILED:" in out and "The record stays in the console" in out, out
    assert sent(run, "shrink_close") == []
    (note,) = sent(run, "shrink_note")
    assert note["id"] == 5 and note["serial"] == SERIAL
    assert "ntfsresize" in note["note"] and "מערכת הקבצים" in note["note"]
    assert not [s for s in order(run) if s.startswith("ntfsfix")], "בלי ntfsfix אחרי מתיחה שנפלה"


def test_a_filesystem_size_that_cannot_be_read_is_not_a_fit(tmp_path):
    """‏--info שלא הדפיס גודל: "לא הצלחנו לבדוק" אינו "מתאים" — לא סוגרים,
    לא שואלים על מתיחה (אין מספר להציג), אזהרה ביומן והרשומה נשארת."""
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", disk_sectors=WIN_SECTORS,
                              prelude=fake_jq(record()), extra_stubs={"ntfsresize": NTFSRESIZE_INFO_BLIND})
    assert "rc=0" in out and "[1]" not in out, out
    assert sent(run, "shrink_close") == [] and rewrites(run) == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize -f", "ntfsfix"))]
    assert "could not read the NTFS volume size" in log_of(run) and "stays open" in log_of(run)


def test_a_grow_failure_after_the_table_was_restored_sends_the_reason_in_the_capture(tmp_path):
    """הצד של הקליטה (shrink_restore_source): הטבלה הוחזרה, המתיחה נפלה —
    הרשומה נשארת פתוחה (אין close) **והסיבה מגיעה לשרת** (shrink-note), לא
    רק לשדה error של היעד."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=NTFSRESIZE_GROW_FAILS), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    assert sent(run, "shrink_close") == []
    (note,) = sent(run, "shrink_note")
    assert note["id"] == 7 and note["serial"] == SERIAL
    assert "המקור לא הוחזר לגודלו" in note["note"] and "ntfsresize could not grow" in note["note"]


def test_two_disks_with_the_same_serial_are_ambiguous_and_neither_is_touched(tmp_path):
    """שני דיסקים במכונה עם אותו סידורי ("0000000000000000" של גשר USB, ‏VM
    בלי סידורי אמיתי): הרשומה יכולה להיות של כל אחד מהם, והשער השני אינו
    מבחין — שחזור מאותו אימג' שומר תחילה ו-GUID ייחודי (#26), ודיסק שכבר
    "בגודלו" היה סוגר בשקט את הרשומה של אחיו המכווץ. לא שואלים, לא כותבים,
    לא סוגרים — אזהרה ביומן שנוקבת בסידורי."""
    twin = ('mkdir -p "$SYSROOT/sys/block/sdb/device"; echo S926 > "$SYSROOT/sys/block/sdb/device/serial"; '
            'cp "$DEVROOT/sda" "$DEVROOT/sdb"; ')
    box, run, out = offer_run(tmp_path, answer_records=[record()], keys="1\n", prelude=fake_jq(record()) + twin)
    assert "rc=0" in out and "[1] Grow it back" not in out, out
    assert rewrites(run) == [] and sent(run, "shrink_close") == []
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert "same serial" in log_of(run) and "S926" in log_of(run) and "nothing changed" in log_of(run)


@requires_jq
def test_the_real_jq_expression_reads_the_hello_answer(tmp_path):
    """הביטוי האמיתי של shrink_records_refresh מול jq אמיתי: "ok" ואז
    שורה לרשומה בסדר השדות ש-shrink_offer_disk קורא."""
    box, run, out = offer_run(tmp_path, answer_records=[record(), record(id=9, serial="Z", unique_guid=None, name=None)],
                              keys="2\n")
    assert "rc=0" in out, out
    lines = (run / "shrink_open").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "ok"
    assert lines[1] == record_line(record())
    assert lines[2] == "9|Z|3|1085440|998244352|" + WIN_GUID + "|||0000000000000000|" + str(WIN_SECTORS * 512) + "|2026-09-16T20:00:00+00:00"
    assert "[1] Grow it back" in out, "והרשומה של S926 אכן זוהתה מהשורה שנקראה"


# --- הנעילות המבניות ------------------------------------------------------------


def test_the_open_runs_after_the_operator_said_yes_and_before_the_resize():
    src = (AGENT / "lib" / "shrink.sh").read_text(encoding="utf-8")
    yes = src.index("continue) ;;")
    opened = src.index("shrink_open_record ")
    resize = src.index("| ntfsresize -s ")
    assert yes < opened < resize, (yes, opened, resize)
    assert 'SHRINK_ERROR="$SHRINKMEM_ERROR"; return 1' in src
    assert "shrink_close_record" in src[src.index("shrink_restore_source()"):]


def test_the_agent_loads_shrinkmem_after_shrink_and_offers_before_the_gui():
    src = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert src.index('"$LIB_DIR/shrink.sh"') < src.index('"$LIB_DIR/shrinkmem.sh"') < src.index('"$LIB_DIR/capture.sh"')
    body = src[src.index("build_console_screen()"):src.index("do_task()")]
    assert body.index("shrink_offer_pending") < body.index("gui_parent")


def test_no_destructive_step_in_shrinkmem_hides_its_exit_code():
    src = (AGENT / "lib" / "shrinkmem.sh").read_text(encoding="utf-8")
    assert "|| true" not in src
    for line in src.splitlines():
        if "sgdisk -i" in line and not line.strip().startswith("#"):
            assert "2>/dev/null" not in line, line
    assert "curl -sfS" not in src, "בלי -f: 409 ו-000 הן שתי תשובות שונות"


def test_the_interfaces_document_describes_the_memory():
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    for needle in ("`shrink_open`", "/api/v1/agent/shrink-open", "/api/v1/agent/shrink-close",
                   "/api/console/shrink-records", "shrink_records"):
        assert needle in text, needle
    assert "**הרשומה של הגודל המקורי יושבת ב-tmpfs בלבד**" not in text, "התיאור הישן של #87 — הרשומה כבר בשרת"
