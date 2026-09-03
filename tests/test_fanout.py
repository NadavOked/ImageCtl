"""הכותב המקבילי עם בידוד — הסיכון שנספח ב' מדרג כגבוה.

הבדיקות מקמפלות את fanout.c ומריצות אותו באמת מול צינורות. זה המקום
היחיד בפרויקט שבו הנכונות תלויה בתזמון, ולכן "נראה בסדר" לא מספיק:
צרכן איטי חייב להיכשל לבדו, והמהירים לצדו חייבים לקבל כל בייט.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "fanout.c"

# הסיכון שנספח ב' מדרג כגבוה — וחבילה שלמה שלו יכלה לדלג בשקט (#52).
pytestmark = requires_native("gcc", posix=True, why="fanout צריך gcc ו-fifo")


@pytest.fixture(scope="module")
def fanout(tmp_path_factory):
    binary = tmp_path_factory.mktemp("build") / "fanout"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(SOURCE)],
        check=True,
    )
    return binary


BUFFER = str(4 * 1024 * 1024)


def reader(path: Path, sink: list, delay: float = 0.0, stop_after: int | None = None):
    """קורא מ-fifo. `delay` מדמה כונן איטי, `stop_after` כונן שמת."""
    def run():
        total = 0
        with open(path, "rb") as handle:
            while True:
                if stop_after is not None and total >= stop_after:
                    return                        # נסגר באמצע: כונן שנפל
                chunk = handle.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                sink.append(chunk)
                if delay:
                    time.sleep(delay)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def run_fanout(fanout, fifos, data, buffer=BUFFER):
    process = subprocess.Popen(
        [str(fanout), buffer, *[str(f) for f in fifos]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    out, err = process.communicate(data, timeout=60)
    report = {}
    for line in out.decode().splitlines():
        path, _, rest = line.partition(" ")
        report[Path(path).parent.name] = rest
    return process.returncode, report, err.decode()


def make_fifo(tmp_path, name):
    folder = tmp_path / name
    folder.mkdir()
    fifo = folder / "feed"
    os.mkfifo(fifo)
    return fifo


# --- המקרה הרגיל -------------------------------------------------------------


def test_every_target_receives_the_whole_stream_byte_for_byte(fanout, tmp_path):
    data = os.urandom(3 * 1024 * 1024)
    sinks = [[], [], []]
    fifos = [make_fifo(tmp_path, f"sd{c}") for c in "abc"]
    threads = [reader(f, s) for f, s in zip(fifos, sinks)]

    code, report, _ = run_fanout(fanout, fifos, data)
    for t in threads:
        t.join(timeout=30)

    assert code == 0
    assert report == {"sda": "ok", "sdb": "ok", "sdc": "ok"}
    digest = hashlib.sha256(data).hexdigest()
    for sink in sinks:
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


def test_a_single_target_works(fanout, tmp_path):
    data = os.urandom(512 * 1024)
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, sink)
    code, report, _ = run_fanout(fanout, [fifo], data)
    thread.join(timeout=30)
    assert code == 0 and report == {"sda": "ok"}
    assert b"".join(sink) == data


# --- המונה של כל מגירה -------------------------------------------------------


def counter_of(fifo: Path) -> int:
    """המספר האחרון ב-`<fifo>.bytes` — בדיוק כמו שהסוכן קורא אותו."""
    text = Path(str(fifo) + ".bytes").read_text()
    return int(text.split()[-1])


def test_each_target_gets_a_counter_of_its_own(fanout, tmp_path):
    """‏#25: ה-pv שלפני fanout מודד את הזרם של המכונה — מספר אחד לכל
    המגירות, שאינו מדידה של אף אחת מהן. fanout הוא היחיד שיודע כמה
    נכנס לכל מגירה, ולכן הוא זה שסופר."""
    data = os.urandom(3 * 1024 * 1024)
    sinks = [[], []]
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb")]
    threads = [reader(f, s) for f, s in zip(fifos, sinks)]

    code, report, _ = run_fanout(fanout, fifos, data)
    for t in threads:
        t.join(timeout=30)

    assert code == 0 and report == {"sda": "ok", "sdb": "ok"}
    assert [counter_of(f) for f in fifos] == [len(data), len(data)]


def test_the_counter_of_a_drawer_that_died_stops_where_the_drawer_did(fanout, tmp_path):
    """וזה מה שמונה אחד למכונה לא יכול היה להראות לעולם: הכונן שמת
    קפא על מה שקיבל, והשכן שלו הגיע עד הסוף. שני מספרים שונים באותו
    רגע — עיקרון 4 בפס ההתקדמות, לא רק בשורת הסיכום."""
    data = os.urandom(4 * 1024 * 1024)
    good, dying = [], []
    fifos = [make_fifo(tmp_path, "sda"), make_fifo(tmp_path, "sdb")]
    threads = [
        reader(fifos[0], good),
        reader(fifos[1], dying, stop_after=256 * 1024),
    ]

    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    for t in threads:
        t.join(timeout=30)

    assert code == 1 and report["sdb"].startswith("failed")
    assert counter_of(fifos[0]) == len(data)
    assert 0 < counter_of(fifos[1]) < len(data)


def test_a_counter_that_cannot_be_opened_does_not_fail_the_drive(fanout, tmp_path):
    """פס התקדמות אינו סיבה לפסול כונן (עיקרון 1) — אבל השתיקה נאמרת
    ב-stderr, שהוא יומן הסוכן, ולא נעלמת."""
    data = os.urandom(256 * 1024)
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    (Path(str(fifo) + ".bytes")).mkdir()      # תיקייה במקום קובץ: open ייכשל
    thread = reader(fifo, sink)

    code, report, err = run_fanout(fanout, [fifo], data)
    thread.join(timeout=30)

    assert code == 0 and report == {"sda": "ok"}
    assert b"".join(sink) == data
    assert "counter" in err


# --- הלב: בידוד --------------------------------------------------------------


def test_a_slow_drive_fails_alone_and_the_fast_ones_finish_intact(fanout, tmp_path):
    """הדרישה מנספח ב': כונן איטי לא עוצר את המכונה.

    הכונן האיטי כאן איטי בסדרי גודל, כמו SSD זול שמיצה את מטמון ה-SLC.
    """
    data = os.urandom(8 * 1024 * 1024)
    fast_a, fast_b, slow = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], fast_a),
        reader(fifos[1], fast_b),
        reader(fifos[2], slow, delay=0.25),          # ~256KB/שנייה
    ]

    started = time.monotonic()
    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    elapsed = time.monotonic() - started
    for t in threads:
        t.join(timeout=30)

    assert code == 1                                  # משהו נכשל, לא הכל
    assert report["sda"] == "ok" and report["sdb"] == "ok"
    assert report["sdc"].startswith("failed")
    assert "slow" in report["sdc"] or "buffer" in report["sdc"]

    digest = hashlib.sha256(data).hexdigest()
    assert hashlib.sha256(b"".join(fast_a)).hexdigest() == digest
    assert hashlib.sha256(b"".join(fast_b)).hexdigest() == digest

    # והמדד שבגללו כל זה נכתב: המהירים לא חיכו לאיטי.
    assert elapsed < 8, f"the fast drives were held back ({elapsed:.1f}s)"


def test_when_every_drive_is_slower_than_the_stream_nobody_is_failed(fanout, tmp_path):
    """‏"איטי מדי" הוא יחסי: כשכל החוצצים מלאים, הזרם עצמו מהיר מכל
    הכוננים (רשת מהירה, או VM שבו הרשת היא זיכרון) — חוסמים ומאיטים את
    המקור, לא פוסלים את כל החדר (#23)."""
    data = os.urandom(8 * 1024 * 1024)
    sinks = [[], [], []]
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [reader(f, s, delay=0.05) for f, s in zip(fifos, sinks)]

    # החוצץ הקטן המותר (READ_CHUNK): הזרם ממלא אותו מיד אצל כולם —
    # בלי המדיניות היחסית כל השלושה היו נפסלים תוך גרייס אחד.
    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    for t in threads:
        t.join(timeout=30)

    assert code == 0, report
    assert all(report[n] == "ok" for n in ("sda", "sdb", "sdc"))
    digest = hashlib.sha256(data).hexdigest()
    for sink in sinks:
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


def test_a_drawer_slower_than_its_equally_slow_neighbours_still_fails_alone(fanout, tmp_path):
    """הצד השני של המדיניות היחסית — עיקרון 4 (#45).

    גם כאן *כל* השלושה איטיים מהזרם, ולכן הטסט הקודם לבדו היה מסתפק
    ב"אף אחד לא נכשל". אבל אחד מהם איטי פי חמישה משכניו, והוא זה
    שמעכב את הזרם בפועל — הוא חייב להיפסל בגלוי ולבד, ושני שכניו
    חייבים לסיים עם כל בייט. "יחסי" הוא לא "סלחני".
    """
    data = os.urandom(4 * 1024 * 1024)
    peer_a, peer_b, laggard = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], peer_a, delay=0.05),
        reader(fifos[1], peer_b, delay=0.05),
        reader(fifos[2], laggard, delay=0.25),        # פי חמישה איטי משכניו
    ]

    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    for t in threads:
        t.join(timeout=30)

    assert code == 1, report
    assert report["sda"] == "ok" and report["sdb"] == "ok", report
    assert report["sdc"].startswith("failed"), report
    assert "slow" in report["sdc"] or "buffer" in report["sdc"], report

    digest = hashlib.sha256(data).hexdigest()
    assert hashlib.sha256(b"".join(peer_a)).hexdigest() == digest
    assert hashlib.sha256(b"".join(peer_b)).hexdigest() == digest


def test_a_drive_that_dies_mid_write_does_not_take_the_others_with_it(fanout, tmp_path):
    data = os.urandom(4 * 1024 * 1024)
    good, dying = [], []
    fifos = [make_fifo(tmp_path, "sda"), make_fifo(tmp_path, "sdb")]
    threads = [
        reader(fifos[0], good),
        reader(fifos[1], dying, stop_after=256 * 1024),   # נסגר באמצע
    ]

    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    for t in threads:
        t.join(timeout=30)

    assert report["sda"] == "ok"
    assert report["sdb"].startswith("failed")
    assert code == 1
    assert hashlib.sha256(b"".join(good)).hexdigest() == hashlib.sha256(data).hexdigest()


def test_when_every_target_fails_the_run_ends_instead_of_spinning(fanout, tmp_path):
    data = os.urandom(4 * 1024 * 1024)
    fifos = [make_fifo(tmp_path, "sda")]
    thread = reader(fifos[0], [], stop_after=64 * 1024)
    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    thread.join(timeout=30)
    assert code == 1 and report["sda"].startswith("failed")


# --- שפיות בשורת הפקודה ------------------------------------------------------


def test_a_target_that_cannot_be_opened_is_reported_not_crashed(fanout, tmp_path):
    good = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, good)
    missing = tmp_path / "nowhere" / "feed"
    code, report, _ = run_fanout(fanout, [fifo, missing], b"hello world")
    thread.join(timeout=10)
    assert report["sda"] == "ok"
    assert report["nowhere"].startswith("failed")
    assert code == 1


@pytest.mark.parametrize("args", [[], ["1048576"], ["10", "/tmp/x"]])
def test_bad_usage_exits_with_a_message(fanout, args):
    result = subprocess.run([str(fanout), *args], capture_output=True, timeout=30)
    assert result.returncode == 2
    assert result.stderr


def test_sigpipe_does_not_kill_the_whole_machine(fanout, tmp_path):
    """כתיבה לצינור שהקורא שלו נעלם מרימה SIGPIPE, וברירת המחדל הורגת
    את התהליך — כלומר כונן אחד שמת היה מפיל את כל המגירות."""
    data = os.urandom(2 * 1024 * 1024)
    good = []
    fifos = [make_fifo(tmp_path, "sda"), make_fifo(tmp_path, "sdb")]
    thread = reader(fifos[0], good)
    reader(fifos[1], [], stop_after=8 * 1024)

    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    thread.join(timeout=30)

    # אילו SIGPIPE היה מתקבל, התהליך היה מת בלי לכתוב שורת דוח כלל.
    assert code in (0, 1), "fanout was killed instead of reporting"
    assert report.get("sda") == "ok"
    assert b"".join(good) == data
