"""הכותב המקבילי עם בידוד — הסיכון שנספח ב' מדרג כגבוה.

הבדיקות מקמפלות את fanout.c ומריצות אותו באמת מול צינורות. זה המקום
היחיד בפרויקט שבו הנכונות תלויה בתזמון, ולכן "נראה בסדר" לא מספיק:
צרכן איטי חייב להיכשל לבדו, והמהירים לצדו חייבים לקבל כל בייט.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "fanout.c"

# הסיכון שנספח ב' מדרג כגבוה — וחבילה שלמה שלו יכלה לדלג בשקט (#52).
# לא pytestmark מודול: שומר התקציב של #353 רץ בלי gcc/fifo, בווינדוס.
needs_fanout = requires_native("gcc", posix=True, why="fanout צריך gcc ו-fifo")


@pytest.fixture(scope="module")
def fanout(tmp_path_factory):
    binary = tmp_path_factory.mktemp("build") / "fanout"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary), str(SOURCE)],
        check=True, stdin=subprocess.DEVNULL,
    )
    return binary


BUFFER = str(4 * 1024 * 1024)
READER_CHUNK = 64 * 1024

# #353: תקרות לקוראים איטיים. ROOM_GRACE_MS הוסר ב-#660; מה שנשאר הוא
# join/communicate צמודים לזמן השינה (8MiB×50ms/64KiB ≈ 6.4s מול join=30,
# ו-4MiB×250ms ≈ 16s מול join=60). נחקר שלוש פעמים כבאג ב-fanout.
# התקרה ≥ 10× זמן השינה הצפוי — לא skip שקט (#52).
SLOW_ALL_BYTES = 8 * 1024 * 1024
SLOW_ALL_DELAY_S = 0.05
SLOW_ALL_TIMEOUT_S = 120

LAGGARD_BYTES = 4 * 1024 * 1024
LAGGARD_PEER_DELAY_S = 0.05
LAGGARD_DELAY_S = 0.25
LAGGARD_TIMEOUT_S = 180


def xxh64_reference(data: bytes) -> str:
    """Small seed-zero implementation of the public XXH64 specification."""
    mask = (1 << 64) - 1
    p1, p2 = 11400714785074694791, 14029467366897019727
    p3, p4, p5 = 1609587929392839161, 9650029242287828579, 2870177450012600261

    def rol(value, bits):
        return ((value << bits) | (value >> (64 - bits))) & mask

    def rnd(acc, value):
        return rol((acc + value * p2) & mask, 31) * p1 & mask

    def merge(acc, value):
        return ((acc ^ rnd(0, value)) * p1 + p4) & mask

    offset = 0
    if len(data) >= 32:
        v1, v2, v3, v4 = (p1 + p2) & mask, p2, 0, (-p1) & mask
        while offset <= len(data) - 32:
            words = [int.from_bytes(data[offset + i:offset + i + 8], "little") for i in range(0, 32, 8)]
            v1, v2, v3, v4 = [rnd(v, word) for v, word in zip((v1, v2, v3, v4), words)]
            offset += 32
        value = (rol(v1, 1) + rol(v2, 7) + rol(v3, 12) + rol(v4, 18)) & mask
        for lane in (v1, v2, v3, v4):
            value = merge(value, lane)
    else:
        value = p5
    value = (value + len(data)) & mask
    while offset <= len(data) - 8:
        value ^= rnd(0, int.from_bytes(data[offset:offset + 8], "little"))
        value = (rol(value, 27) * p1 + p4) & mask
        offset += 8
    if offset <= len(data) - 4:
        value ^= (int.from_bytes(data[offset:offset + 4], "little") * p1) & mask
        value = (rol(value, 23) * p2 + p3) & mask
        offset += 4
    while offset < len(data):
        value ^= (data[offset] * p5) & mask   # wraps at 64 bits, like C
        value = rol(value, 11) * p1 & mask
        offset += 1
    value ^= value >> 33
    value = value * p2 & mask
    value ^= value >> 29
    value = value * p3 & mask
    value ^= value >> 32
    return f"{value:016x}"


def reader(path: Path, sink: list, delay: float = 0.0, stop_after: int | None = None):
    """קורא מ-fifo. `delay` מדמה כונן איטי, `stop_after` כונן שמת."""
    def run():
        total = 0
        with open(path, "rb") as handle:
            while True:
                if stop_after is not None and total >= stop_after:
                    return                        # נסגר באמצע: כונן שנפל
                chunk = handle.read(READER_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                sink.append(chunk)
                if delay:
                    time.sleep(delay)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def join_readers(threads, timeout):
    """join חייב להסתיים. timeout שחוזר בשקט מביא hash חסר שנחקר כבאג (#353)."""
    for thread in threads:
        thread.join(timeout=timeout)
        if thread.is_alive():
            pytest.fail(
                f"קורא לא סיים ב-{timeout}s — תקרת הטסט תחת עומס, לא מדיניות (#353)"
            )


def hung_reader(path: Path, release: threading.Event):
    """כונן תקוע: פותח את ה-fifo (כדי ש-open של fanout יצליח והמגירה
    תיחשב חיה), ואז לעולם אינו קורא ממנו — בדיוק ה-I/O timeout של #674,
    ולא EIO מיידי. משתחרר רק כש-`release` נדלק, ב-finally של הטסט."""
    def run():
        with open(path, "rb"):
            release.wait()                     # מחזיק את ה-fifo פתוח, בלי לקרוא
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def pausing_reader(path: Path, sink: list, pause_s: float, pause_after: int = 0):
    """קורא מ-fifo; אחרי pause_after בייטים עוצר pause_s שניות ואז ממשיך.
    זו עצירת SSD/התאוששות קרנל של #421, לא כונן תקוע לנצח (#674)."""
    def run():
        total = 0
        paused = False
        with open(path, "rb") as handle:
            while True:
                if not paused and total >= pause_after:
                    time.sleep(pause_s)
                    paused = True
                chunk = handle.read(READER_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                sink.append(chunk)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def run_fanout(fanout, fifos, data, buffer=BUFFER, env=None, timeout=60, flags=()):
    """`env` מתמזג ל-os.environ (למשל FANOUT_STALL_EVICT_MS לטסטי הבידוד).
    בפקיעת המתנה הורגים את התהליך לפני ש-raise — אחרת fanout תקוע (הקוד
    הישן ב-#674) נשאר חי אחרי הטסט ומדליף תהליך על ה-fifo."""
    full_env = {**os.environ, **env} if env else None
    process = subprocess.Popen(
        [str(fanout), *[str(a) for a in flags], buffer, *[str(f) for f in fifos]],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=full_env,
    )
    try:
        out, err = process.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise
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


@pytest.mark.parametrize("data, expected", [
    (b"", "ef46db3751d8e999"),
    (b"a", "d24ec4f1a98c6e5b"),
    (b"abc", "44bc2cf5ad770999"),
])
@needs_fanout
def test_xxh64_mode_matches_official_vectors(fanout, data, expected):
    result = subprocess.run([str(fanout), "--xxh64"], input=data, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected}\n".encode()


@needs_fanout
def test_xxh64_mode_matches_python_reference_for_large_input(fanout):
    data = os.urandom(5 * 1024 * 1024 + 17)
    result = subprocess.run([str(fanout), "--xxh64"], input=data, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{xxh64_reference(data)}\n".encode()


@pytest.mark.parametrize("size", [0, 1, 55, 56, 63, 64, 65, 1000, 3 * 1024 * 1024 + 19])
@needs_fanout
def test_sha256_file_matches_hashlib_at_block_boundaries(fanout, tmp_path, size):
    data = os.urandom(size)
    sha_file = tmp_path / "stream.sha256"
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    code, _, _ = run_fanout(fanout, [output], data, flags=("-S", sha_file))
    assert code == (1 if size == 0 else 0)
    assert sha_file.read_text() == f"{hashlib.sha256(data).hexdigest()}\n"


@pytest.mark.parametrize("data, expected", [
    (b"abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
    (b"", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
])
@needs_fanout
def test_sha256_mode_matches_fips_vectors(fanout, data, expected):
    result = subprocess.run([str(fanout), "--sha256"], input=data, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"{expected}\n".encode()


@needs_fanout
def test_xxh64_and_sha256_are_written_together(fanout, tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 19)
    xxh_file, sha_file = tmp_path / "stream.xxh64", tmp_path / "stream.sha256"
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    code, report, _ = run_fanout(
        fanout, [output], data, flags=("-H", xxh_file, "-S", sha_file),
    )
    assert code == 0 and report == {"target": "ok"}, report
    assert xxh_file.read_text() == f"{xxh64_reference(data)}\n"
    assert sha_file.read_text() == f"{hashlib.sha256(data).hexdigest()}\n"


@needs_fanout
def test_read_error_never_creates_sha256_evidence(fanout, tmp_path):
    sha_file = tmp_path / "must-not-exist"
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    input_fd = os.open(tmp_path, os.O_RDONLY)
    try:
        result = subprocess.run(
            [str(fanout), "-S", str(sha_file), BUFFER, str(output)],
            stdin=input_fd, capture_output=True, timeout=30,
        )
    finally:
        os.close(input_fd)
    assert result.returncode == 2
    assert not sha_file.exists()


@needs_fanout
def test_sha256_read_error_prints_no_digest(fanout, tmp_path):
    input_fd = os.open(tmp_path, os.O_RDONLY)
    try:
        result = subprocess.run(
            [str(fanout), "--sha256"], stdin=input_fd,
            capture_output=True, timeout=30,
        )
    finally:
        os.close(input_fd)
    assert result.returncode == 2
    assert result.stdout == b""


@needs_fanout
def test_hash_and_stream_progress_cover_every_input_byte(fanout, tmp_path):
    data = os.urandom(3 * 1024 * 1024 + 19)
    hash_file, progress = tmp_path / "stream.xxh64", tmp_path / "stream.bytes"
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    code, report, _ = run_fanout(
        fanout, [output], data,
        flags=("-H", hash_file, "-P", progress),
    )
    assert code == 0 and report == {"target": "ok"}, report
    assert hash_file.read_text() == f"{xxh64_reference(data)}\n"
    lines = progress.read_text().splitlines()
    assert lines and all(line.isdecimal() for line in lines)
    assert int(lines[-1]) == len(data)


@needs_fanout
def test_read_error_never_creates_hash_evidence(fanout, tmp_path):
    hash_file = tmp_path / "must-not-exist"
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    input_fd = os.open(tmp_path, os.O_RDONLY)
    try:
        result = subprocess.run(
            [str(fanout), "-H", str(hash_file), BUFFER, str(output)],
            stdin=input_fd, capture_output=True, timeout=30,
        )
    finally:
        os.close(input_fd)
    assert result.returncode == 2
    assert not hash_file.exists()


@needs_fanout
def test_xxh64_read_error_prints_no_digest(fanout, tmp_path):
    input_fd = os.open(tmp_path, os.O_RDONLY)
    try:
        result = subprocess.run(
            [str(fanout), "--xxh64"], stdin=input_fd,
            capture_output=True, timeout=30,
        )
    finally:
        os.close(input_fd)
    assert result.returncode == 2
    assert result.stdout == b""


@needs_fanout
@pytest.mark.parametrize("size", [3, 31, 33, 100, 3 * 1024 * 1024 + 19])
def test_xxh64_low_word_matches_zstd_frame_checksum(fanout, size):
    """Independent of xxh64_reference: a zstd frame ends with the low 32 bits of
    the official XXH64 of its content. The Python reference once skipped the
    64-bit wrap in the tail steps and disagreed with fanout on random bytes."""
    zstd = shutil.which("zstd")
    if zstd is None:
        pytest.skip("zstd is required for the independent XXH64 check")
    data = os.urandom(size)
    frame = subprocess.run([zstd, "-q", "-c", "--check"], input=data, capture_output=True,
                           check=True, timeout=60).stdout
    ours = subprocess.run([str(fanout), "--xxh64"], input=data, capture_output=True,
                          check=True, timeout=60).stdout.decode().strip()
    assert ours[8:] == frame[-4:][::-1].hex()
    assert ours == xxh64_reference(data)


@needs_fanout
def test_original_positional_interface_remains_unchanged(fanout, tmp_path):
    data = b"old interface" * 10000
    output = tmp_path / "target" / "feed"
    output.parent.mkdir()
    output.write_bytes(b"")
    code, report, _ = run_fanout(fanout, [output], data)
    assert code == 0 and report == {"target": "ok"}
    assert output.read_bytes() == data


# --- המקרה הרגיל -------------------------------------------------------------


@needs_fanout
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


@needs_fanout
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


@needs_fanout
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


@needs_fanout
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


@needs_fanout
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


@needs_fanout
def test_a_slow_drive_slows_the_stream_and_keeps_every_byte(fanout, tmp_path):
    """הכלל מ-#660 (וכמו #657 בצד השולח): מגירה איטית אינה נזרקת — היא
    מאטה את כל הזרם, וכל מגירה חיה מקבלת כל בייט.

    זו גם הבקרה השלילית של #660: הקוד הישן היה מפיל את האיטית ב-buffer
    overrun תוך גרייס אחד (ROOM_GRACE_MS), ואז report["sdc"] היה "failed"
    וה-hash של האיטית היה חסר — בדיוק הכשל בריצת הברזל 11/09. האיטי כאן
    קורא 64KiB כל 250ms (~256KB/s), הרבה מתחת לשני המהירים שלצדו.
    """
    data = os.urandom(8 * 1024 * 1024)
    fast_a, fast_b, slow = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], fast_a),
        reader(fifos[1], fast_b),
        reader(fifos[2], slow, delay=0.25),          # ~256KB/שנייה
    ]

    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    for t in threads:
        t.join(timeout=60)

    assert code == 0, report                          # אף אחד לא נפסל
    assert report == {"sda": "ok", "sdb": "ok", "sdc": "ok"}, report

    digest = hashlib.sha256(data).hexdigest()
    for sink in (fast_a, fast_b, slow):
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


def test_room_grace_ms_is_gone_so_slow_is_not_dead():
    """#421: ROOM_GRACE_MS=500 הפך כונן איטי למת — חצי שנייה בתוך
    התנהגות נורמלית של SSD, ופי 12 מוקדם מהתאוששות הקרנל שנמדדה.
    #660 הסיר את הסף: מגירה איטית מעכבת את הזרם, לא נזרקת.
    פינוי נשאר רק לצרכן שפסק לקחת בייטים (#674, STALL_EVICT_MS).
    שלושת המצבים lagging/detached/failed הוחלפו במדיניות הזו — lagging
    מסיים באותו גל, וניתוק חומרה הוא write error, לא overrun של 500ms.
    רץ בלי gcc — זה שומר על הסמל, לא על fifo.
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "#define ROOM_GRACE_MS" not in source
    assert "buffer overrun (drive too slow)" not in source
    assert "held_ms" not in source
    assert "A slow drawer is never dropped" in source
    assert "#define STALL_EVICT_MS" in source


def test_slow_reader_timeouts_dwarf_sleep_so_vm_load_is_not_a_failed_drive():
    """#353: join=30 מול ~6.4s שינה נכשל תחת load 6.78 ונחקר שלוש פעמים
    כבאג ב-fanout. ‏ROOM_GRACE_MS הוסר ב-#660; מה שנשאר הוא תקרת הטסט.
    skip שקט אינו פתרון (#52). רץ בלי gcc — זה שומר על התקציב, לא על fifo.
    """
    every = SLOW_ALL_BYTES / READER_CHUNK * SLOW_ALL_DELAY_S
    lag = LAGGARD_BYTES / READER_CHUNK * LAGGARD_DELAY_S
    assert SLOW_ALL_TIMEOUT_S >= 10 * every, (
        f"every-drive: תקרה {SLOW_ALL_TIMEOUT_S}s מול שינה {every:.1f}s (#353)"
    )
    assert LAGGARD_TIMEOUT_S >= 10 * lag, (
        f"laggard: תקרה {LAGGARD_TIMEOUT_S}s מול שינה {lag:.1f}s (#353)"
    )


@needs_fanout
def test_when_every_drive_is_slower_than_the_stream_nobody_is_failed(fanout, tmp_path):
    """‏"איטי מדי" הוא יחסי: כשכל החוצצים מלאים, הזרם עצמו מהיר מכל
    הכוננים (רשת מהירה, או VM שבו הרשת היא זיכרון) — חוסמים ומאיטים את
    המקור, לא פוסלים את כל החדר (#23).

    ‏#353: join=30 היה צמוד ל-~6.4s שינה ונכשל תחת load 6.78. התקרה כאן
    פי ~18 מזמן השינה, וקורא שלא סיים נכשל בשם ולא ב-hash חסר.
    """
    data = os.urandom(SLOW_ALL_BYTES)
    sinks = [[], [], []]
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [reader(f, s, delay=SLOW_ALL_DELAY_S) for f, s in zip(fifos, sinks)]

    # החוצץ הקטן המותר (READ_CHUNK): הזרם ממלא אותו מיד אצל כולם —
    # בלי המדיניות היחסית כל השלושה היו נפסלים תוך גרייס אחד.
    code, report, _ = run_fanout(
        fanout, fifos, data, buffer=str(1024 * 1024),
        timeout=SLOW_ALL_TIMEOUT_S,
    )
    join_readers(threads, SLOW_ALL_TIMEOUT_S)

    assert code == 0, report
    assert all(report[n] == "ok" for n in ("sda", "sdb", "sdc"))
    digest = hashlib.sha256(data).hexdigest()
    for sink in sinks:
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


@needs_fanout
def test_a_laggard_among_slow_peers_is_not_dropped_either(fanout, tmp_path):
    """הצד השני של #660: גם כשכל השלושה איטיים מהזרם ואחד מהם איטי פי
    חמישה משכניו, האיטי אינו נפסל. הקוד הישן הפיל אותו לבד (#45); היום
    כל השלושה מקבלים כל בייט, והזרם מתקדם בקצב האיטי ביותר. זו בקרה
    שלילית נוספת: על הקוד הישן report["sdc"] היה "failed".

    ‏#353: 4MiB×250ms/64KiB ≈ 16s שינה מול join=60 — אותו משטח רעידה.
    """
    data = os.urandom(LAGGARD_BYTES)
    peer_a, peer_b, laggard = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], peer_a, delay=LAGGARD_PEER_DELAY_S),
        reader(fifos[1], peer_b, delay=LAGGARD_PEER_DELAY_S),
        reader(fifos[2], laggard, delay=LAGGARD_DELAY_S),        # פי חמישה איטי משכניו
    ]

    code, report, _ = run_fanout(
        fanout, fifos, data, buffer=str(1024 * 1024),
        timeout=LAGGARD_TIMEOUT_S,
    )
    join_readers(threads, LAGGARD_TIMEOUT_S)

    assert code == 0, report
    assert report == {"sda": "ok", "sdb": "ok", "sdc": "ok"}, report

    digest = hashlib.sha256(data).hexdigest()
    for sink in (peer_a, peer_b, laggard):
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


@needs_fanout
def test_a_drawer_that_pauses_for_seconds_then_resumes_finishes(fanout, tmp_path):
    """#421 DoD: מגירה שנעצרת כמה שניות ומתאוששת חייבת לסיים בהצלחה.
    הקוד הישן (ROOM_GRACE_MS=500) הפיל אותה כ-buffer overrun ברגע שהחוצץ
    מלא והיא לבדה מעכבת, בזמן שאחרות מוכנות. 2s >> 500ms, ומתחת ל-
    STALL_EVICT_MS (150s) — זו השהיה, לא תקיעה.
    """
    pause_s = 2.0
    data = os.urandom(2 * 1024 * 1024)
    fast_a, fast_b, paused = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], fast_a),
        reader(fifos[1], fast_b),
        pausing_reader(fifos[2], paused, pause_s=pause_s, pause_after=0),
    ]
    start = time.monotonic()
    code, report, _ = run_fanout(
        fanout, fifos, data, buffer=str(1024 * 1024), timeout=60,
    )
    elapsed = time.monotonic() - start
    join_readers(threads, 60)

    assert code == 0, report
    assert report == {"sda": "ok", "sdb": "ok", "sdc": "ok"}, report
    assert elapsed >= pause_s, (
        f"הריצה ({elapsed:.2f}s) לא חצתה את ההשהיה — לא הוכח דבר"
    )
    digest = hashlib.sha256(data).hexdigest()
    for sink in (fast_a, fast_b, paused):
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


@needs_fanout
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


@needs_fanout
def test_when_every_target_fails_the_run_ends_instead_of_spinning(fanout, tmp_path):
    data = os.urandom(4 * 1024 * 1024)
    fifos = [make_fifo(tmp_path, "sda")]
    thread = reader(fifos[0], [], stop_after=64 * 1024)
    code, report, _ = run_fanout(fanout, fifos, data, buffer=str(1024 * 1024))
    thread.join(timeout=30)
    assert code == 1 and report["sda"].startswith("failed")


# --- כונן תקוע: הלקח של #674 -------------------------------------------------


@needs_fanout
def test_a_hung_drive_is_evicted_and_the_healthy_siblings_finish(fanout, tmp_path):
    """‏#674, כשל הברזל של 12/09: כונן שנתקע — ‏I/O timeout של ~180s, לא
    EIO מיידי — מפסיק לרוקן את ה-fifo שלו אבל אינו מחזיר שגיאה. הקוד
    הישן היה נחסם ב-make_room לנצח בהמתנה לחדר בטבעת שלו, האחים הבריאים
    לא היו מתנקזים, וכל השכפולים על המחשב היו נופלים. עכשיו הכונן התקוע
    מפונה אחרי STALL_EVICT_MS בלי התקדמות-כתיבה — נכשל **בקול** (עיקרון
    4/5, לא דילוג שקט) — והבריאים משלימים כל בייט.

    בקרה שלילית: על הקוד הישן (בלי FANOUT_STALL_EVICT_MS) ה-communicate
    פוקע ב-15s כי make_room מסתובב לנצח, ו-run_fanout מרים TimeoutExpired.
    """
    data = os.urandom(4 * 1024 * 1024)
    good = []
    fifos = [make_fifo(tmp_path, "sda"), make_fifo(tmp_path, "sdb")]
    release = threading.Event()
    good_thread = reader(fifos[0], good)
    hung_thread = hung_reader(fifos[1], release)
    try:
        code, report, err = run_fanout(
            fanout, fifos, data, buffer=str(1024 * 1024),
            env={"FANOUT_STALL_EVICT_MS": "500"}, timeout=15,
        )
    finally:
        release.set()                     # משחרר את הקורא התקוע — בלי דליפת thread
        good_thread.join(timeout=30)
        hung_thread.join(timeout=30)

    assert code == 1, report
    assert report["sda"] == "ok", report
    assert report["sdb"] == "failed stalled with no write progress", report
    assert "sdb" in err and "evicting" in err, err
    assert hashlib.sha256(b"".join(good)).hexdigest() == hashlib.sha256(data).hexdigest()
    assert counter_of(fifos[0]) == len(data)
    assert 0 < counter_of(fifos[1]) < len(data)


@needs_fanout
def test_a_slow_but_draining_drive_is_not_evicted_even_past_the_threshold(fanout, tmp_path):
    """הצד השני של #674, והשמירה על ‏#660: מגירה איטית שממשיכה לקבל
    בייטים אינה נתקעת. כל כתיבה מוצלחת מאפסת את last_progress_ms, ולכן
    גם כשסף הפינוי נמוך (500ms) והריצה כולה ארוכה ממנו בהרבה — האיטית
    אינה מפונה. הזמן הכולל אינו הקריטריון; היעדר התקדמות הוא.

    בקרה שלילית: זהו הצד שמגן מפני תיקון-יתר. גם על הקוד הישן וגם על
    החדש הוא עובר — כשל כאן היה אומר שהתיקון זורק כונן איטי-בריא, כלומר
    רגרסיה של #660. האיטי קורא 64KiB כל 100ms, פי חמישה מתחת לסף.
    """
    data = os.urandom(int(1.5 * 1024 * 1024))
    fast_a, fast_b, slow = [], [], []
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb", "sdc")]
    threads = [
        reader(fifos[0], fast_a),
        reader(fifos[1], fast_b),
        reader(fifos[2], slow, delay=0.1),        # 64KiB כל 100ms — הרבה מתחת ל-500ms
    ]
    start = time.monotonic()
    code, report, _ = run_fanout(
        fanout, fifos, data, buffer=str(1024 * 1024),
        env={"FANOUT_STALL_EVICT_MS": "500"}, timeout=60,
    )
    elapsed = time.monotonic() - start
    for t in threads:
        t.join(timeout=60)

    assert code == 0, report
    assert report == {"sda": "ok", "sdb": "ok", "sdc": "ok"}, report
    assert elapsed > 0.5, f"הריצה ({elapsed:.2f}s) לא חצתה את הסף — לא הוכח דבר"
    digest = hashlib.sha256(data).hexdigest()
    for sink in (fast_a, fast_b, slow):
        assert hashlib.sha256(b"".join(sink)).hexdigest() == digest


@needs_fanout
def test_an_overflowing_stall_threshold_is_rejected_not_taken_as_disabled(fanout, tmp_path):
    """‏errno/ERANGE: ‏`999999999999999999999` גולש ב-strtoll ל-LLONG_MAX,
    ובלי בדיקת errno היה עובר את `configured > 0` ומנטרל בפועל את הפינוי
    (סף של מיליוני שנים) — בדיוק ההיפוך של #674. הוא חייב להידחות, לחזור
    לברירת המחדל, ולומר זאת ב-stderr. ריצה בריאה רגילה, בלי תלות בתזמון.

    בקרה שלילית: על הקוד בלי `errno = 0` + `errno != ERANGE`, LLONG_MAX
    מתקבל בשקט וההודעה חסרה — כלומר ה-assert על stderr נכשל.
    """
    data = os.urandom(512 * 1024)
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, sink)
    code, report, err = run_fanout(
        fanout, [fifo], data,
        env={"FANOUT_STALL_EVICT_MS": "999999999999999999999"},
    )
    thread.join(timeout=30)

    assert code == 0 and report == {"sda": "ok"}, report
    assert b"".join(sink) == data
    assert "ignoring invalid FANOUT_STALL_EVICT_MS" in err, err


# --- #427: EOF אינו ok בלי אורך ---------------------------------------------


@needs_fanout
def test_an_empty_stream_is_failed_not_ok(fanout, tmp_path):
    """קלט ריק, יצרן שמת, ואימג' שלם היו אותו EOF → ok. ריק נכשל בשמו."""
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, sink)
    code, report, _ = run_fanout(fanout, [fifo], b"")
    thread.join(timeout=30)
    assert code == 1, report
    assert report["sda"] == "failed empty stream", report
    assert b"".join(sink) == b""


@needs_fanout
def test_a_short_stream_fails_every_drawer_not_ok(fanout, tmp_path):
    """יצרן שנהרג באמצע: FANOUT_EXPECTED_BYTES לא הושג → failed, לא ok."""
    data = os.urandom(256 * 1024)
    sinks = [[], []]
    fifos = [make_fifo(tmp_path, n) for n in ("sda", "sdb")]
    threads = [reader(f, s) for f, s in zip(fifos, sinks)]
    code, report, _ = run_fanout(
        fanout, fifos, data, buffer=str(1024 * 1024),
        env={"FANOUT_EXPECTED_BYTES": str(len(data) * 4)},
    )
    for t in threads:
        t.join(timeout=30)
    assert code == 1, report
    for name in ("sda", "sdb"):
        assert report[name].startswith("failed short stream"), report
        assert str(len(data)) in report[name], report
        assert str(len(data) * 4) in report[name], report
    assert [counter_of(f) for f in fifos] == [len(data), len(data)]


@needs_fanout
def test_a_stream_that_matches_the_expected_length_is_still_ok(fanout, tmp_path):
    """הצד השני של #427: אורך תואם אינו תיקון-יתר שדוחה זרם שלם."""
    data = os.urandom(256 * 1024)
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, sink)
    code, report, _ = run_fanout(
        fanout, [fifo], data,
        env={"FANOUT_EXPECTED_BYTES": str(len(data))},
    )
    thread.join(timeout=30)
    assert code == 0 and report == {"sda": "ok"}, report
    assert b"".join(sink) == data
    assert counter_of(fifo) == len(data)


@needs_fanout
def test_an_overflowing_expected_length_is_rejected_not_taken_as_disabled(fanout, tmp_path):
    """כמו #674 על הסטול: גלישה ל-LLONG_MAX היתה הופכת כל זרם ל-short
    (או מבטלת את הבדיקה). נדחית בקול, והזרם הבריא עובר."""
    data = os.urandom(64 * 1024)
    sink = []
    fifo = make_fifo(tmp_path, "sda")
    thread = reader(fifo, sink)
    code, report, err = run_fanout(
        fanout, [fifo], data,
        env={"FANOUT_EXPECTED_BYTES": "999999999999999999999"},
    )
    thread.join(timeout=30)
    assert code == 0 and report == {"sda": "ok"}, report
    assert b"".join(sink) == data
    assert "ignoring invalid FANOUT_EXPECTED_BYTES" in err, err


@needs_fanout
def test_a_regular_file_target_is_fsynced(fanout, tmp_path):
    """יעד שאינו FIFO: fsync + close נבדקים, והבייטים על הדיסק תואמים."""
    data = os.urandom(64 * 1024)
    folder = tmp_path / "sda"
    folder.mkdir()
    dest = folder / "feed"
    dest.write_bytes(b"")
    code, report, _ = run_fanout(
        fanout, [dest], data, buffer=str(1024 * 1024),
    )
    assert code == 0 and report == {"sda": "ok"}, report
    assert dest.read_bytes() == data


# --- שפיות בשורת הפקודה ------------------------------------------------------


@needs_fanout
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
@needs_fanout
def test_bad_usage_exits_with_a_message(fanout, args):
    result = subprocess.run([str(fanout), *args], capture_output=True, timeout=30, stdin=subprocess.DEVNULL)
    assert result.returncode == 2
    assert result.stderr


@needs_fanout
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


def test_every_test_that_compiles_fanout_skips_by_name_without_gcc():
    """‏#1283: חמישה טסטים ביקשו את ה-fixture `fanout` בלי `@needs_fanout`,
    ובווינדוס (אין gcc) הם נפלו ב-ERROR של setup — לא דילוג מוצהר
    שנספר בשמו (וכישלון תחת ‏IMAGECTL_REQUIRE_NATIVE=1, #52), אלא רעש
    שמסתיר כישלון אמיתי. השומר הזה רץ בכל מקום, גם בלי gcc."""
    import inspect

    unmarked = [
        name for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
        and "fanout" in inspect.signature(fn).parameters
        and needs_fanout.mark not in getattr(fn, "pytestmark", [])
    ]
    assert unmarked == [], f"בלי @needs_fanout: {unmarked}"
