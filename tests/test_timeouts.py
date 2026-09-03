"""כל המתנה בסוכן — התקרה שלה, והדיווח כשהיא נחצית.

הרקע: קוד שקיבל מחיצת swap במגירה לא נכשל אלא *נתקע* — המגירה פתחה
fifo וחיכתה לזרם שלעולם לא הגיע. במעבדה זה נראה כמו קפיאה בלי סיבה,
ובלוג לא הייתה שורה אחת שמסבירה על מה חיכינו. קפיאה אילמת היא הפרה של
עיקרון 4 בדיוק כמו זריקת בלוק בשקט.

לכן כל בדיקה כאן מודדת **זמן אמיתי**: היא מריצה תת-תהליך עם timeout,
ונכשלת אם המסלול נתלה במקום לדווח. בדיקה שנתקעת במקום להיכשל היא בדיוק
הבעיה שהקובץ הזה נכתב נגדה — ב-CI היא הייתה תולה את הריצה עד התקרה של
GitHub במקום ליפול תוך דקה.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, SH_FILES, posix

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
WAITS = AGENT / "lib" / "waits.sh"

pytestmark = requires_native(("bash", BASH))

# הבדיקות שמריצות צינור אמיתי דורשות fifo, ולכן היה מתבקש לדלג עליהן
# בווינדוס כמו ב-test_fanout.py. לא: שם הסיבה היא gcc, כאן הכל sh, ו-mkfifo
# של Git Bash עובד. הבדיקה החשובה כאן היא בדיוק זו — היא לא תדולג בעמדה
# שבה נדב מריץ אותה.

#: התקרה של הבדיקה עצמה. כל מסלול כאן אמור להסתיים בשניות בודדות;
#: חצי דקה היא "משהו נתקע", לא "המכונה עמוסה".
TEST_TIMEOUT = 45


def run_sh(script: str, timeout: int = TEST_TIMEOUT) -> str:
    """מריץ סקריפט bash עם תקרת זמן אמיתית ומחזיר את פלטו. פקיעה = כישלון.

    הפלט הולך לקובץ ולא ל-PIPE, ו-stdout/stderr של התהליך הם DEVNULL.
    זה לא קישוט: ‏capture_output ממתין לסגירת הצינור, ו*כל* תהליך רקע
    שהסקריפט השאיר אחריו מחזיק עותק שלו — כולל היתומים שנוצרים בדיוק
    כשתקרה נאכפת. בלי זה הבדיקה הייתה מודדת את היתומים ולא את המסלול,
    ומכריזה על "תקיעה" גם כשהסוכן דיווח ויצא כשורה תוך שניות.

    ‏stdin=DEVNULL: בריצה רב-קבצית של pytest בווינדוס ה-handle של stdin
    נשבר תחת capture, וכל subprocess שיורש אותו נופל ב-WinError 50 (#14).
    """
    out_file = Path(tempfile.mkdtemp(prefix="imagectl-wait-")) / "out"
    wrapped = (
        'export PATH="/usr/bin:$PATH"\n'
        f"{{\n{script}\n}} > {posix(out_file)!r} 2>&1\n"
    )
    try:
        subprocess.run(
            [BASH, "-c", wrapped],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"המסלול נתקע יותר מ-{timeout} שניות במקום לדווח ולהיכשל")
    return out_file.read_text(encoding="utf-8", errors="replace")


def waits_prelude(box: Path, **env) -> str:
    """טוען common.sh + waits.sh עם תקרות שהבדיקה קובעת."""
    box.mkdir(parents=True, exist_ok=True)
    exports = " ".join(f"{k}={v!r}" for k, v in env.items())
    return (
        f"export RUN_DIR={posix(box)!r} {exports}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
    )


def log_of(run_dir: Path) -> str:
    """‏common.sh קובע את LOG_FILE מתוך RUN_DIR — שם הלוג נמצא."""
    path = run_dir / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- wait_pid: תהליך שלא נגמר ------------------------------------------------


def test_a_process_that_finishes_in_time_is_simply_waited_for(tmp_path):
    box = tmp_path / "box"
    out = run_sh(waits_prelude(box) + 'sleep 1 & wait_pid $! 10 "quick-child"; echo "rc=$?"')
    assert out.strip().endswith("rc=0")
    assert "פג הזמן" not in log_of(box)


def test_a_process_that_never_ends_is_killed_reported_and_left_behind(tmp_path):
    """הדרישה במלואה: תקרה, שורת לוג שאומרת על מה חיכינו וכמה,
    והמשך — לא יציאה שקטה ולא קריסה."""
    box = tmp_path / "box"
    out = run_sh(
        waits_prelude(box)
        + 'sleep 120 & p=$!; s=$(date +%s); '
        'wait_pid "$p" 2 "stuck-child"; rc=$?; e=$(date +%s); '
        'kill -0 "$p" 2>/dev/null && alive=yes || alive=no; '
        'echo "rc=$rc elapsed=$((e - s)) alive=$alive"; '
        'echo "and-the-caller-carried-on"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 8, "התקרה לא נאכפה בזמן"
    assert fields["alive"] == "no", "התהליך התקוע נשאר חי"
    assert "and-the-caller-carried-on" in out

    line = log_of(box)
    assert "פג הזמן" in line
    assert "stuck-child" in line, "הלוג לא אומר על מה חיכינו"
    assert "2 שניות" in line, "הלוג לא אומר כמה חיכינו"


# --- wait_progress: המדד הוא חוסר התקדמות, לא משך -----------------------------


def test_a_slow_but_moving_transfer_is_never_cut_off(tmp_path):
    """הדרישה שאסור לשבור: שחזור מחיצה גדולה על כונן איטי לוקח דקות.
    התקרה היא נגד מונה שנעצר, לא נגד משך כולל — כאן היא נמוכה בהרבה
    מזמן הריצה, והריצה בכל זאת מסתיימת בהצלחה."""
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + f'( i=0; while [ $i -lt 30 ]; do i=$((i + 1)); '
        f'echo $((i * 4096)) >> {posix(counter)!r}; sleep 0.2; done ) & '
        's=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 20 2 "slow-drive"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "0", "העברה איטית-אך-מתקדמת נקטלה"
    # רצה הרבה מעבר לתקרת השקט (2 שניות) ובכל זאת שרדה.
    assert int(fields["elapsed"]) >= 4
    assert "פג הזמן" not in log_of(box)


def test_a_transfer_that_stops_moving_is_reported_with_where_it_stopped(tmp_path):
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + f'( echo 65536 >> {posix(counter)!r}; sleep 120 ) & '
        's=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 30 2 "stalled-stream"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 12

    line = log_of(box)
    assert "פג הזמן" in line and "stalled-stream" in line
    # איפה זה נעצר — זה ההבדל בין "נתקע" ל"נתקע אחרי 64KB".
    assert "65536" in line


def test_a_stream_that_never_starts_uses_the_start_ceiling(tmp_path):
    """שתי תקרות שונות ובכוונה: "השידור עוד לא התחיל" סבלני בהרבה
    מ"השידור נפסק באמצע". כאן אין בייט ראשון, ולכן התקרה הקצרה שנבחרה
    לתחילה היא זו שפוקעת — ולא זו שאחריה."""
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + 'sleep 120 & s=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 2 60 "silent-sender"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 10, "התקרה של ההתחלה לא נאכפה"
    assert "0 בייטים" in log_of(box)


# --- המסלולים האמיתיים -------------------------------------------------------

STUBS = {
    # מחקה pv -n -b: מעתיק, ומדווח מונה בייטים ל-stderr (הקורא מפנה אותו
    # ל-bytes.raw). שני ערכים שונים — אחרת wait_progress רואה מונה עומד.
    "pv": '#!/bin/sh\necho 0 >&2\ncat\necho 65536 >&2\n',
    "zstd": '#!/bin/sh\nexec cat\n',
    "partclone.dd": '#!/bin/sh\ncat > /dev/null\n',
}


def make_stubs(stub_dir: Path, extra: dict | None = None) -> str:
    """זיופים נכתבים מתוך bash דווקא — קובץ שנוצר מווינדוס אינו נחשב
    בר-הרצה ב-Git Bash. ה-chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה,
    וזיוף כזה עובר בווינדוס ונופל ב-CI בלבד."""
    # שורות אמיתיות ולא "; " — סוגר של here-document חייב להיות לבדו
    # בשורה, ואיחוד בנקודה-פסיק בולע אותו בשקט (הבנאי "עבר" ולא יצר כלום).
    lines = [f"mkdir -p {posix(stub_dir)!r}"]
    for name, body in {**STUBS, **(extra or {})}.items():
        lines.append(f"cat > {posix(stub_dir)}/{name} <<'STUB'\n{body}STUB")
        lines.append(f"chmod 0755 {posix(stub_dir)}/{name}")
    lines.append(f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"')
    return "\n".join(lines) + "\n"


#: fanout מזויף שמאכיל את המגירה הראשונה בלבד ולא פותח את השנייה כלל —
#: בדיוק מה שקרה כש-fanout מת לפני הבייט הראשון (#49). הקורא של ה-fifo
#: השני נחסם ב-open() ולעולם לא ישתחרר מעצמו.
FEED_ONLY_THE_FIRST = (
    '#!/bin/sh\n'
    'shift\n'                       # החוצץ
    'first="$1"\n'
    'cat > "$first"\n'
    'echo "$first ok"\n'
)


def test_a_drawer_that_is_never_fed_fails_by_name_and_its_neighbour_finishes(tmp_path):
    """הרגרסיה של #49, בזמן אמיתי.

    מגירה אחת מקבלת את הזרם ומסיימת; השנייה תקועה על fifo שאיש לא כתב
    אליו. הקוד הישן היה מחכה לה לנצח, וכל החדר היה נראה קפוא. עכשיו היא
    נכשלת בשמה, השכנה שלה מסיימת (תרחיש QA: כשל במגירה אחת לא עוצר את
    השאר), והפונקציה חוזרת בהצלחה כי מישהו כן שרד.
    """
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagectl" * 4096)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for dev in ("sda", "sdb"):
        target = run / "targets" / dev
        target.mkdir(parents=True)
        (target / "state").write_text("writing\n")
        (target / "base").write_text("0\n")
        (target / "bytes.raw").write_text("")

    out = run_sh(
        make_stubs(box / "stubs", {"fanout": FEED_ONLY_THE_FIRST})
        + f"export RUN_DIR={posix(run)!r} "
        f"DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=3 WAIT_HELPER_S=3 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/drawers.sh; "
        f'stream_source() {{ cat {posix(payload)!r}; }}; '
        "restore_partition_drawers unicast http://s img 3 dd part.zst "
        f"{sha} '' sda sdb > {posix(box)}/pipe.out 2>&1; echo \"rc=$?\""
    )

    assert out.strip().endswith("rc=0"), out
    states = {d: (run / "targets" / d / "state").read_text().strip()
              for d in ("sda", "sdb")}
    assert states["sda"] != "failed", "המגירה שקיבלה את הזרם נפסלה"
    assert states["sdb"] == "failed", "המגירה הרעבה לא סומנה ככישלון"

    error = (run / "targets" / "sdb" / "error").read_text(encoding="utf-8")
    assert "פג הזמן" in error
    log = log_of(run)
    assert "פג הזמן" in log and "sdb" in log


def test_a_station_whose_stream_never_arrives_fails_instead_of_freezing(tmp_path):
    """תחנה בודדת: המקור פתוח ושותק. הצינור כולו היה מחכה לו לנצח."""
    box = tmp_path / "box"
    run = box / "run"
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    (target / "state").write_text("writing\n")
    (target / "base").write_text("0\n")
    (target / "bytes.raw").write_text("")

    out = run_sh(
        make_stubs(box / "stubs")
        + f"export RUN_DIR={posix(run)!r} "
        f"DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_STREAM_START_S=3 WAIT_STREAM_STALL_S=3 "
        "WAIT_HELPER_S=3; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        "stream_source() { sleep 120; }; "
        # הבדיקה הזו על התקרה של הזרם. בדיקת ההתקן שלפניה (#51) מוחלפת
        # כי אין התקן בלוקים בקופסה, ונבדקת בנפרד ב-test_restore_evidence.py.
        "node_is_block() { true; }; "
        "s=$(date +%s); "
        # פלט הצינור לקובץ נפרד, כדי ששורות ה-log של הסוכן לא יתערבבו
        # בשורת ה-rc שהבדיקה מפרסרת.
        f"restore_partition unicast http://s img sda 2 dd p2.zst deadbeef '' "
        f"> {posix(box)}/pipe.out 2>&1; "
        'rc=$?; e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1", out
    assert int(fields["elapsed"]) <= 25

    log = log_of(run)
    assert "פג הזמן" in log
    assert "מחיצה 2" in log, "הלוג לא אומר איזו מחיצה נתקעה"


# --- שמירה על הכלל: אין המתנה בלי תקרה ---------------------------------------


def test_no_bare_wait_is_left_anywhere_in_the_agent():
    """‏`wait <pid>` חשוף הוא המתנה בלי תקרה. כל אחת כזו עוברת דרך
    wait_pid/wait_progress, ולכן ההופעות היחידות של ה-builtin הן בתוך
    waits.sh עצמו."""
    offenders = []
    for path in SH_FILES:
        if path.name == "waits.sh":
            continue
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(r"\s*wait\b", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], "המתנות בלי תקרה: " + "; ".join(offenders)


@pytest.mark.parametrize("name", ["restore.sh", "drawers.sh", "capture.sh"])
def test_every_streaming_library_waits_through_the_helpers(name):
    """שלושת הקבצים שמזרימים בייטים — שחזור תחנה, חדר שיכפולים, קליטה —
    עוברים כולם דרך אותם שני עוזרים. אין קובץ שממציא המתנה משלו."""
    source = (AGENT / "lib" / name).read_text(encoding="utf-8")
    assert "wait_progress " in source, f"{name} מזרים בלי שעון על ההתקדמות"
    assert "wait_pid " in source, f"{name} ממתין לתהליך עזר בלי תקרה"


def test_every_ceiling_is_defined_in_one_place():
    """הערכים לא מפוזרים כמספרי קסם: כל WAIT_* שנקרא באיזשהו קובץ
    מוגדר — עם נימוק — בראש waits.sh."""
    defined = set(re.findall(r"^(WAIT_[A-Z_]+)=", WAITS.read_text(encoding="utf-8"),
                             flags=re.M))
    assert defined, "waits.sh לא מגדיר תקרות בכלל"
    used = set()
    for path in SH_FILES:
        if path.name == "waits.sh":
            continue
        used |= set(re.findall(r"\$\{?(WAIT_[A-Z_]+)", path.read_text(encoding="utf-8")))
    assert used - defined == set(), f"תקרות שאין להן הגדרה: {used - defined}"


@pytest.mark.parametrize(
    ("path", "needles"),
    [
        # ‏curl: בלי תקרת משך (מחיצה גדולה לוקחת דקות) אבל עם תקרת
        # חוסר-התקדמות — חיבור שנפל יוצא במקום להיתלות.
        ("lib/common.sh", ["--speed-limit", "--speed-time"]),
        ("lib/capture.sh", ["--speed-limit", "--speed-time"]),
        # ‏udp-receiver ממתין לשדר לנצח כברירת מחדל. אלה התקרות שלו עצמו.
        ("lib/restore.sh", ["--start-timeout", "--receive-timeout"]),
    ],
    ids=lambda v: str(v),
)
def test_the_long_transfers_carry_their_own_ceiling(path, needles):
    source = (AGENT / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in source, f"{path} מזרים בלי {needle}"


def test_fanout_bounds_the_fifo_open_itself():
    """‏open() על fifo לכתיבה נחסם עד שיש קורא. ‏fanout פותח ב-O_NONBLOCK
    ומוותר אחרי OPEN_RETRY_MS — ומדווח על כך כפקיעה, לא כ"לא ניתן לפתוח":
    ‏fifo שאין לו קורא ונתיב שאינו קיים הם שתי תקלות שונות לטכנאי."""
    source = (AGENT / "fanout.c").read_text(encoding="utf-8")
    assert "O_WRONLY | O_NONBLOCK" in source
    assert "OPEN_NO_READER" in source
    assert "timed out waiting for the writer pipeline" in source
    for ceiling in ("OPEN_RETRY_MS", "ROOM_GRACE_MS", "DRAIN_STALL_MS"):
        assert re.search(rf"#define {ceiling}\s+\d+", source), f"{ceiling} אינו קבוע"
