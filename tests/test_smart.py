"""בדיקות שער ה-SMART בצד השחזור (#652) -- כמו Clonezilla.

הסוכן בודק בריאות דיסק יעד לפני כתיבה. הכלל שאסור לשבור (עיקרון 5):
**‏`unchecked` לעולם לא חוסם** -- דיסק בלי SMART / USB / RAID / probe
שנכשל לפתוח נכתב בלי שאלה, כי "לא הצלחנו לבדוק" איננו "בדקנו ונכשל".

‏smartctl אמיתי אינו זמין (ואין דיסקים פגומים במעבדה), ולכן `smartctl`
מזויף: סקריפט שפולט פלט קנוני וקוד יציאה נבחר -- בדיוק הדפוס של
"לזייף שלב אחד ולהריץ את הפונקציה האמיתית".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL,
    )


# הרכיבים ש-smart.sh נשען עליהם, וחסימת ה-HTTP כך שהבדיקה אינה נוגעת ברשת.
PRELUDE = (
    f'. {posix(AGENT)}/lib/common.sh; '
    f'. {posix(AGENT)}/lib/sysinfo.sh; '
    f'. {posix(AGENT)}/lib/ui.sh; '
    f'. {posix(AGENT)}/lib/progress.sh; '
    f'. {posix(AGENT)}/lib/smart.sh; '
    f'. {posix(AGENT)}/lib/clonergui.sh; '   # gui_smart_choice — כמו בסוכן האמיתי
    'http_post_json() { return 0; }; '   # אין רשת בבדיקה
)


def env(run: Path, dev: Path, sysroot: Path | None = None, smartctl: str = "") -> str:
    out = (
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} '
        f'IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; '
    )
    if sysroot is not None:
        out += f'export SYSROOT={posix(sysroot)!r}; '
    if smartctl:
        out += f'export SMARTCTL={smartctl!r}; '
    return out


def make_smartctl(tmp_path: Path, body: str, rc: int = 0) -> str:
    """‏smartctl מזויף: מדפיס `body`, יוצא ב-`rc`.

    ‏rc הוא מפת-סיביות אמיתית: bit 3 (8) = הדיסק נכשל, וזה קוד היציאה
    ש-smartctl אמיתי מחזיר על `-H` שראה FAILED. הפונקציה שנבדקת חייבת
    לקרוא את הפלט, לא להסיק מ-rc."""
    p = tmp_path / "smartctl"
    p.write_text("#!/bin/sh\ncat <<'EOF'\n" + body + "\nEOF\nexit " + str(rc) + "\n",
                 newline="\n")
    # chmod דרך bash: ‏os.chmod של פייתון על ווינדוס אינו קובע את סיבית
    # ה-exec ש-Git Bash קורא, ו-`command -v` לא היה מוצא את הקובץ.
    subprocess.run([BASH, "-c", f'chmod +x {posix(p)!r}'], check=True,
                   stdin=subprocess.DEVNULL)
    return posix(p)


HEALTH_OK = "SMART overall-health self-assessment test result: PASSED"
HEALTH_FAIL = "SMART overall-health self-assessment test result: FAILED"


def attrs(realloc=0, pending=0, uncorr=0, crc=0) -> str:
    return (
        "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      "
        "UPDATED  WHEN_FAILED RAW_VALUE\n"
        f"  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  "
        f"Always       -       {realloc}\n"
        f"197 Current_Pending_Sector  0x0032   100   100   000    Old_age   "
        f"Always       -       {pending}\n"
        f"198 Offline_Uncorrectable   0x0030   100   100   000    Old_age   "
        f"Offline      -       {uncorr}\n"
        f"199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   "
        f"Always       -       {crc}\n"
    )


def full(health: str, **kw) -> str:
    return health + "\n\n" + attrs(**kw)


# --- smart_classify: פונקציה טהורה, שורה-שורה -------------------------------

CLASSIFY = [
    # health, realloc, pending, uncorr, crc -> "verdict reason"
    ("PASSED", 0, 0, 0, 0, "ok ok"),
    ("FAILED", 0, 0, 0, 0, "fail health_failed"),
    ("PASSED", 0, 0, 1, 0, "fail uncorrectable"),   # PASSED לא מספיק כשיש uncorr
    ("PASSED", 0, 4, 0, 0, "fail pending"),
    ("PASSED", 2, 0, 0, 0, "warn reallocated"),
    ("PASSED", 0, 0, 0, 50, "warn crc"),            # סף 50
    ("PASSED", 0, 0, 0, 49, "ok ok"),               # מתחת לסף -- מתעלמים
    ("unknown", 0, 0, 0, 0, "unchecked no_health"), # לא PASSED ולא FAILED
    ("unknown", 0, 0, 1, 0, "fail uncorrectable"),  # מאפיין רע גובר על היעדר בריאות
]


@pytest.mark.parametrize("health,re,pe,un,cr,expected", CLASSIFY)
def test_classify(health, re, pe, un, cr, expected):
    out = sh(PRELUDE + f'smart_classify {health} {re} {pe} {un} {cr}')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


# --- smart_probe: מפלט smartctl אמיתי אל verdict ----------------------------

def probe(tmp_path, body, rc=0, dev="sda"):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev_dir = tmp_path / "dev"; dev_dir.mkdir(exist_ok=True)
    (dev_dir / dev).write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, body, rc)
    out = sh(env(run, dev_dir, smartctl=sc) + PRELUDE + f'smart_probe {dev}')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_probe_healthy_disk_is_ok(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK)) == "ok"


def test_probe_failed_health_is_fail_even_with_nonzero_exit(tmp_path):
    # rc=8 הוא bit 3 -- הדיסק נכשל. זו תשובה, לא כשל פתיחה.
    assert probe(tmp_path, full(HEALTH_FAIL), rc=8) == "fail"


def test_probe_pending_sectors_are_fail(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK, pending=4)) == "fail"


def test_probe_reallocated_is_warn(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK, realloc=8)) == "warn"


def test_probe_high_crc_is_warn(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK, crc=120)) == "warn"


def test_probe_open_failure_is_unchecked_not_fail(tmp_path):
    # rc=2 = bit 1 = פתיחת ההתקן נכשלה (גם אחרי -d sat). unchecked, נכתב.
    assert probe(tmp_path, "Smartctl open device failed", rc=2) == "unchecked"


def test_probe_device_without_smart_is_unchecked(tmp_path):
    assert probe(tmp_path, "SMART support is: Unavailable") == "unchecked"


def test_probe_without_smartctl_is_unchecked(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent/smartctl") + PRELUDE
             + 'smart_probe sda')
    assert out.stdout.strip() == "unchecked"


# --- הכלל הקריטי: unchecked לעולם לא חוסם -----------------------------------

def test_unchecked_disk_is_written_without_asking(tmp_path):
    """דיסק שלא ניתן לבדוק חוזר ברשימת הכתיבה, ואף מקש לא נצרך.

    זו בקרה שלילית מובנית: ה-stdin ריק (`</dev/null`). אילו הפונקציה
    שאלה על `unchecked`, היא הייתה נחסמת/מקבלת EOF -- והדיסק לא היה
    ברשימת הכתיבה. הופעתו שם היא ההוכחה החיובית שלא נשאלה שאלה."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent") + PRELUDE
             + 'smart_preflight sess1 sda')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "sda"


# --- התפריט על fail/warn: החלטות בתור ---------------------------------------

def preflight(tmp_path, body, keys, disks="sda"):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev = tmp_path / "dev"; dev.mkdir(exist_ok=True)
    for d in disks.split():
        (dev / d).write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, body)
    # המקשים מוזרקים ב-`printf` בתוך הסקריפט (‏\n נקי): stdin של פייתון
    # בווינדוס הופך \n ל-\r\n, וה-`read` היה מקבל "2\r" (מלכודת ה-CR של
    # CONTRIBUTING). על ה-TTY האמיתי (לינוקס) אין CR.
    keys_printf = keys.replace("\n", "\\n")
    out = sh(env(run, dev, smartctl=sc) + PRELUDE
             + f"printf '{keys_printf}' | smart_preflight sess1 {disks}")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), run


def test_skip_removes_the_disk_from_the_write_list(tmp_path):
    written, run = preflight(tmp_path, full(HEALTH_FAIL), "3\n")
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"


def test_rescue_keeps_the_disk(tmp_path):
    written, _ = preflight(tmp_path, full(HEALTH_OK, realloc=2), "2\n")
    assert written == "sda"


def test_replace_removes_the_disk_and_flags_awaiting(tmp_path):
    written, run = preflight(tmp_path, full(HEALTH_FAIL), "1\n")
    assert written == ""
    assert (run / "smart/awaiting_replace").read_text().strip() == "sda"
    assert (run / "targets/sda/state").read_text().strip() == "replacing"


def test_bad_key_reprompts_then_takes_the_choice(tmp_path):
    # קלט לא-חוקי אינו בחירה שקטה: התפריט מצויר שוב עד מקש תקין.
    written, _ = preflight(tmp_path, full(HEALTH_FAIL), "9\nx\n2\n")
    assert written == "sda"


def test_a_failing_disk_does_not_stop_a_healthy_sibling(tmp_path):
    # מגירה אחת מדולגת, השנייה נכתבת -- כשל דיסק אחד לא מפיל את הסבב.
    # שתי המגירות מקבלות אותו פלט smartctl (fake יחיד), ולכן הראשונה
    # מדולגת (מקש 3) והשנייה ניצלת (מקש 2).
    written, _ = preflight(tmp_path, full(HEALTH_FAIL), "3\n2\n", disks="sda sdb")
    assert written == "sdb"


# --- תווית הדיסק: לפי חריץ, לא לפי שם ההתקן ----------------------------------

def test_disk_label_uses_the_slot_number(tmp_path):
    # ‏disk_port מוזרק (מקורו נבדק ב-test_agent) -- כאן נבדקת התווית עצמו.
    out = sh(PRELUDE + 'disk_port() { echo 3; }; smart_disk_label sda')
    assert out.stdout.strip() == "Disk 3"


def test_disk_label_falls_back_to_device_name_without_a_slot(tmp_path):
    out = sh(PRELUDE + 'disk_port() { return 0; }; smart_disk_label nvme0n1')
    assert out.stdout.strip() == "Disk nvme0n1"


# --- hello: הבריאות זמינה לקונסולה לפני start (מטמון בלבד) -------------------

def test_hello_field_reads_the_cache_and_never_probes(tmp_path):
    # אחרי probe אחד, שדה ה-hello מחזיר את ה-verdict מהמטמון; בלי probe
    # הוא `unchecked`. זה מה שמאפשר לקונסולה להראות "דיסק N: אזהרה"
    # *לפני* שסבב נפתח, בלי לשלם smartctl בכל hello.
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_OK, realloc=4))
    base = env(run, dev, smartctl=sc) + PRELUDE
    assert sh(base + 'smart_hello_field sdb').stdout.strip() == "unchecked"
    sh(base + 'smart_probe sda >/dev/null')
    assert sh(base + 'smart_hello_field sda').stdout.strip() == "warn"


# --- disk_event: מבנה החוזה --------------------------------------------------

def test_event_json_is_valid_and_carries_the_contract(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_OK, realloc=3))
    out = sh(env(run, dev, smartctl=sc) + PRELUDE
             + 'smart_probe sda >/dev/null; '
             + 'smart_event_json sess1 sda warn reallocated rescue rescue')
    assert out.returncode == 0, out.stderr
    ev = json.loads(out.stdout)
    assert ev["session_id"] == "sess1"
    assert ev["disk"] == "sda"
    assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
    assert ev["smart"]["verdict"] == "warn"
    assert ev["smart"]["reason"] == "reallocated"
    assert ev["smart"]["realloc"] == 3
    assert ev["write_state"] == "rescue"
    assert ev["decision"] == "rescue"
