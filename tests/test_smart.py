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
    f'. {posix(AGENT)}/lib/jsonq.sh; '
    f'. {posix(AGENT)}/lib/ui.sh; '
    f'. {posix(AGENT)}/lib/progress.sh; '
    f'. {posix(AGENT)}/lib/failmark.sh; '   # disk_failure_cause — זיכרון השרת בשער (#872/#874)
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
    # ‏#872: שורת הבריאות (‏-H) לבדה מכריעה. המונים 5/197/198/199 נקראים
    # ונשמרים (מידע), ואינם צובעים: על 6 דיסקי הכיתה CRC הוא 137–55,242
    # (15/09) והם משכפלים מושלם. מונה מצטבר מספר על הכבלים, לא על הדיסק.
    ("PASSED", 0, 0, 0, 0, "ok ok"),
    ("FAILED", 0, 0, 0, 0, "fail health_failed"),
    ("PASSED", 0, 0, 1, 0, "ok ok"),                # uncorrectable אינו צובע
    ("PASSED", 0, 4, 0, 0, "ok ok"),                # pending אינו צובע
    ("PASSED", 2, 0, 0, 0, "ok ok"),                # reallocated אינו צובע
    ("PASSED", 0, 0, 0, 55242, "ok ok"),            # CRC — מחשב 2 דיסק 2, 15/09
    ("unknown", 0, 0, 0, 0, "unchecked no_health"), # לא PASSED ולא FAILED
    ("unknown", 0, 0, 1, 0, "unchecked no_health"), # מונה רע אינו תחליף לשורת בריאות
    ("FAILED", 0, 0, 0, 55242, "fail health_failed"),
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


def test_probe_pending_sectors_are_ok_and_recorded(tmp_path):
    # ‏#872: 197 אינו צובע — אבל הערך נשמר במטמון (מידע ל-hello ולאירוע).
    assert probe(tmp_path, full(HEALTH_OK, pending=4)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split() == ["ok", "ok", "0", "4", "0", "0"]


def test_probe_reallocated_is_ok_and_recorded(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK, realloc=8)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[2] == "8"


def test_probe_high_crc_is_ok_and_recorded(tmp_path):
    # 55,242 — מחשב 2 (.59) דיסק 2, המעבדה 15/09; שיכפל 20GB מושלם.
    assert probe(tmp_path, full(HEALTH_OK, crc=55242)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[5] == "55242"


def test_probe_crc_is_found_by_attribute_id_not_vendor_name(tmp_path):
    # #866: סמסונג מדפיסה 199 בשם CRC_Error_Count; לפי שם הוא נקרא 0 והדיסק
    # ירוק. השורה — מחשב 2 (.59) דיסק 2, המעבדה 15/09: 55,242 שגיאות CRC.
    samsung = attrs().replace(
        "199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   "
        "Always       -       0",
        "199 CRC_Error_Count         0x003e   045   045   000    Old_age   "
        "Always       -       55242")
    assert "UDMA" not in samsung
    # ‏#872: הערך עדיין חייב להיקרא נכון (55242 במטמון) — רק לא לצבוע.
    assert probe(tmp_path, full(HEALTH_OK).replace(attrs(), samsung)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[5] == "55242"


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


# --- #858: ניסוחי בריאות שאינם PASSED/FAILED, קוד היציאה, ו-NVMe ---------------

#: ‏SCSI/SAS (וגשרי USB מסוימים): smartctl מדפיס `SMART Health Status:` ולא
#: `overall-health`, ובכשל — תיאור ה-asc/ascq של החיישן (5d/00), לא המילה
#: FAILED. הגוף — מבנה הפלט של smartctl 7.4 ל-`-H -A` על דיסק SAS; אין SAS
#: במעבדה, ולכן הטקסט מטבלת התיאורים של smartmontools ולא מצילום.
SCSI_FAILURE = (
    "=== START OF READ SMART DATA SECTION ===\n"
    "SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]\n"
    "\n"
    "Current Drive Temperature:     34 C\n"
    "Drive Trip Temperature:        65 C\n"
    "\n"
    "Accumulated power on time, hours:minutes 42154:23\n"
    "Elements in grown defect list: 12\n"
    "\n"
    "Error counter log:\n"
    "           Errors Corrected by           Total   Correction     Gigabytes    Total\n"
    "               ECC          rereads/    errors   algorithm      processed    uncorrected\n"
    "           fast | delayed   rewrites  corrected  invocations   [10^9 bytes]  errors\n"
    "read:          0        0         0         0          0     123456.789           0\n"
    "write:         0        0         0         0          0      98765.432           0\n"
)
SCSI_OK = SCSI_FAILURE.replace(
    "FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]", "OK")


def nvme(critical: str, media_errors: int) -> str:
    """‏smartctl 7.x על NVMe: `Critical Warning != 0` הופך את שורת הבריאות
    ל-`FAILED!` (bit 3 = read-only → "media has been placed in read only
    mode"), והמונים הם שורות `שם:    ערך` עם רווחים ופסיקי-אלפים — לא
    הטבלה של ATA. אין NVMe במעבדה: המבנה לפי nvmeprint.cpp, לא מצילום."""
    failed = critical != "0x00"
    return (
        "=== START OF SMART DATA SECTION ===\n"
        "SMART overall-health self-assessment test result: "
        + ("FAILED!\n- media has been placed in read only mode\n" if failed else "PASSED\n")
        + "\n"
        "SMART/Health Information (NVMe Log 0x02)\n"
        f"Critical Warning:                   {critical}\n"
        "Temperature:                        38 Celsius\n"
        "Available Spare:                    100%\n"
        "Available Spare Threshold:          10%\n"
        "Percentage Used:                    3%\n"
        "Data Units Read:                    12,345,678 [6.32 TB]\n"
        "Data Units Written:                 9,876,543 [5.05 TB]\n"
        "Power Cycles:                       784\n"
        "Power On Hours:                     5,432\n"
        "Unsafe Shutdowns:                   61\n"
        f"Media and Data Integrity Errors:    {media_errors:,}\n"
        "Error Information Log Entries:      12\n"
    )


def test_scsi_health_failure_is_fail(tmp_path):
    """‏#858: `FAILURE` אינו `FAILED` — על main הדיסק היה `unchecked no_health`
    ונכתב בלי שאלה. rc=8 (bit 3) כמו ש-smartctl אמיתי מחזיר."""
    assert probe(tmp_path, SCSI_FAILURE, rc=8) == "fail"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[:2] == ["fail", "health_failed"]


def test_scsi_health_ok_is_ok(tmp_path):
    assert probe(tmp_path, SCSI_OK) == "ok"


def test_exit_bit_3_alone_is_fail_when_the_health_line_is_not_recognised(tmp_path):
    """‏smartctl אמר "הדיסק נכשל" (bit 3) בניסוח ששורת הבריאות שלנו אינה
    מכירה — הסיבית היא ראיה חיובית (עיקרון 5), לא `unchecked`."""
    body = "SMART Health Status: UNKNOWN SENSE [asc=5d, ascq=ff]\n\n" + attrs()
    assert probe(tmp_path, body, rc=8) == "fail"


def test_exit_bit_3_does_not_override_a_clean_open_failure(tmp_path):
    # rc=10 = bit 1 + bit 3: ההתקן לא נפתח — אין מה לקרוא, unchecked.
    assert probe(tmp_path, "Smartctl open device failed", rc=10) == "unchecked"


def test_nvme_read_only_media_is_fail_and_integrity_errors_are_recorded(tmp_path):
    """‏#858: Critical Warning 0x08 (read-only) → FAILED! + rc=8 → fail; ומונה
    `Media and Data Integrity Errors` (המקבילה של 198) נקרא לאחסון — על main
    ‏`_smart_int` חיפש אותו כעמודה בטבלה ורשם 0 תמיד. ‏#872: המונה אינו מכריע."""
    assert probe(tmp_path, nvme("0x08", 3), rc=8, dev="nvme0n1") == "fail"
    assert (tmp_path / "run/smart/nvme0n1.v").read_text().split() == \
        ["fail", "health_failed", "0", "0", "3", "0"]


def test_nvme_healthy_with_integrity_errors_is_ok_but_the_counter_is_kept(tmp_path):
    # פסיקי-אלפים (1,234) — כך smartctl מדפיס מונים ב-NVMe.
    assert probe(tmp_path, nvme("0x00", 1234), dev="nvme0n1") == "ok"
    assert (tmp_path / "run/smart/nvme0n1.v").read_text().split()[4] == "1234"


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

#: ‏sgdisk מזויף לשער: רושם בלבד. עד #874 השער קרא ממנו את הסימון של #845
#: (‏`sgdisk -p`); מאז הזיכרון בשרת, ו-`sgdisk.calls` חייב להישאר ריק.
GATE_SGDISK = """#!/bin/sh
printf '%s\\n' "$*" >> "{box}/sgdisk.calls"
exit 0
"""


def fake_memory(failed: dict[str, str]) -> str:
    """זיכרון השרת (#874) כפי ש-jq היה מדפיס אותו מתשובת ה-hello: "ok" ואז
    serial|port|cause לכל דיסק ב-`failed` (dev → cause). הסידורי מוזרק
    (‏S-<dev>) — אין sysfs אמיתי בבדיקה. ‏jq עצמו: test_failmark (requires_jq)."""
    lines = " ".join(f"echo 'S-{dev}||{cause}';" for dev, cause in failed.items())
    return ('disk_serial() {{ printf "S-%s" "$1"; }}; '
            'json_get_join() {{ [ -s "$1" ] || return 1; echo ok; {lines} }}; ').format(lines=lines)


def preflight(tmp_path, body, keys, disks="sda", failed=(), extra=""):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev = tmp_path / "dev"; dev.mkdir(exist_ok=True)
    box = tmp_path / "box"; (box / "bin").mkdir(parents=True, exist_ok=True)
    for d in disks.split():
        (dev / d).write_bytes(b"\x00")
    # ‏#874: `failed` הוא dev → cause (מחרוזת = cable); הרשומה מגיעה מהשרת.
    failed = {d: "cable" for d in failed} if not isinstance(failed, dict) else failed
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    extra = fake_memory(failed) + extra
    (box / "bin" / "sgdisk").write_text(GATE_SGDISK.format(box=posix(box)), newline="\n")
    sc = make_smartctl(tmp_path, body)
    # המקשים מוזרקים ב-`printf` בתוך הסקריפט (‏\n נקי): stdin של פייתון
    # בווינדוס הופך \n ל-\r\n, וה-`read` היה מקבל "2\r" (מלכודת ה-CR של
    # CONTRIBUTING). על ה-TTY האמיתי (לינוקס) אין CR.
    keys_printf = keys.replace("\n", "\\n")
    out = sh(f'chmod +x {posix(box)}/bin/sgdisk; export PATH="$(cd {posix(box)}/bin && pwd):$PATH"; '
             + env(run, dev, smartctl=sc) + PRELUDE + extra
             + f"printf '{keys_printf}' | smart_preflight sess1 {disks}")
    assert out.returncode == 0, out.stderr
    # ‏stderr חוזר גם הוא: התפריט מצויר עליו, ו-"Choose [" שם הוא הראיה
    # החיובית שנשאלה שאלה — היעדרו הוא הראיה שלא נשאלה.
    return out.stdout.strip(), run, out.stderr


def test_skip_removes_the_disk_from_the_write_list(tmp_path):
    written, run, _ = preflight(tmp_path, full(HEALTH_FAIL), "3\n")
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"


def test_rescue_keeps_the_disk(tmp_path):
    written, _, err = preflight(tmp_path, full(HEALTH_FAIL), "2\n")
    assert written == "sda"
    assert "Choose [" in err


def test_a_disk_with_bad_counters_but_passed_health_is_written_without_asking(tmp_path):
    """‏#872: 5/197/198/199 אינם עוצרים. הראיה החיובית: התפריט לא צויר
    ("Choose [" אינו ב-stderr) והדיסק ברשימת הכתיבה. בקרה שלילית: על main
    הדיסק הזה הוא `fail uncorrectable`, התפריט מצויר וה-EOF מדלג עליו."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK, realloc=8, pending=4,
                                                 uncorr=2, crc=55242), "")
    assert written == "sda"
    assert "Choose [" not in err, err


def test_replace_removes_the_disk_and_flags_awaiting(tmp_path):
    written, run, _ = preflight(tmp_path, full(HEALTH_FAIL), "1\n")
    assert written == ""
    assert (run / "smart/awaiting_replace").read_text().strip() == "sda"
    assert (run / "targets/sda/state").read_text().strip() == "replacing"


def test_bad_key_reprompts_then_takes_the_choice(tmp_path):
    # קלט לא-חוקי אינו בחירה שקטה: התפריט מצויר שוב עד מקש תקין.
    written, _, _ = preflight(tmp_path, full(HEALTH_FAIL), "9\nx\n2\n")
    assert written == "sda"


def test_a_failing_disk_does_not_stop_a_healthy_sibling(tmp_path):
    # מגירה אחת מדולגת, השנייה נכתבת -- כשל דיסק אחד לא מפיל את הסבב.
    # שתי המגירות מקבלות אותו פלט smartctl (fake יחיד), ולכן הראשונה
    # מדולגת (מקש 3) והשנייה ניצלת (מקש 2).
    written, _, _ = preflight(tmp_path, full(HEALTH_FAIL), "3\n2\n", disks="sda sdb")
    assert written == "sdb"


# --- #872: דיסק אדום (נכשל בשיכפול הקודם) — החלף או המשך (=דלג), לא רק צבע ---

def test_a_disk_that_failed_the_previous_clone_is_asked_replace_or_continue(tmp_path):
    """‏#872 (נדב 16/09): זיכרון השרת (#874: לפי סידורי) גובר על SMART נקי.
    הדיסק האדום נכנס לאותה שאלה, אבל בלי "כתוב בכל זאת" — שתי אפשרויות
    בלבד, ו"המשך" (מקש 2) = מדלג. השכן הבריא נכתב בלי שאלה, והסבב ממשיך.
    הסיבה שהשרת סיווג (cable) היא ה-reason. ‏sgdisk אינו נקרא (#845 הוסר).
    בקרה שלילית: על main הסוכן קורא `sgdisk -p` (הזיוף כאן מדפיס כלום →
    "לא נקרא"), sda נכתב בלי שאלה, ו-sgdisk.calls אינו ריק."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), "2\n",
                                  disks="sda sdb", failed=("sda",))
    assert written == "sdb"
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert "failed_last: cable" in (run / "targets/sda/error").read_text()
    assert not (tmp_path / "box" / "sgdisk.calls").exists()
    assert "[2] Continue without this disk" in err, err
    assert "Write anyway" not in err and "[3]" not in err, err
    assert err.count("Choose [1-2]") == 1, err   # השכן הבריא לא נשאל


def test_a_red_disk_has_no_key_3_and_reprompts(tmp_path):
    # מקש 3 אינו אפשרות על אדום: התפריט מצויר שוב; ואז EOF -> ברירת האדום = דלג.
    written, run, err = preflight(tmp_path, full(HEALTH_OK), "3\n", failed=("sda",))
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert err.count("Choose [1-2]") == 2, err


def test_replace_on_a_red_disk_powers_off_for_the_swap(tmp_path):
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), "1\n", failed={"sda": "disk"})
    assert written == ""
    assert (run / "smart/awaiting_replace").read_text().strip() == "sda"
    assert "awaiting disk swap (failed_last: disk)" in (run / "targets/sda/error").read_text()


def test_a_red_disk_by_slot_is_red_even_with_a_new_serial(tmp_path):
    """‏#874: הרשומה לפי החריץ (מכונה+פורט) — הדיסק הוחלף, הכבל לא. הסידורי
    כאן אחר (S-sda מול הרשומה על X), והחריץ 1 תואם → אדום, כמו לפי סידורי."""
    written, run, err = preflight(
        tmp_path, full(HEALTH_OK), "2\n",
        extra='disk_port() { printf 1; }; json_get_join() { echo ok; echo "X|1|cable"; }; ')
    assert written == ""
    assert "failed_last: cable" in (run / "targets/sda/error").read_text()
    assert "Choose [1-2]" in err, err


def test_the_memory_is_refreshed_from_the_last_hello_before_the_gate(tmp_path):
    """השער קורא את זיכרון השרת מתשובת ה-hello **האחרונה**: רשומה שנוקתה
    בקונסולה (איננה בתשובה) אינה צובעת עוד. הקובץ הישן מהאתחול נדרס."""
    run = tmp_path / "run"; run.mkdir()
    (run / "disk_failures").write_text("ok\nS-sda||cable\n", newline="\n")
    written, run, err = preflight(tmp_path, full(HEALTH_OK), "")   # התשובה: בלי כשלים
    assert written == "sda"
    assert "Choose [" not in err, err


def test_a_screen_that_offers_rescue_on_a_red_disk_is_not_obeyed(tmp_path):
    # מסך ישן (3 כפתורים) שהחזיר rescue על failed_last: לא מתקבל -- מדלגים.
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), "", failed=("sda",),
                                extra='gui_smart_choice() { echo rescue; }; ')
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"


# --- #872: בלי תשובה — "המשך" של אותו צבע: כתום כותב, אדום מדלג ---------------

def test_no_answer_on_an_orange_disk_writes_it(tmp_path):
    """‏EOF על fail (כתום) = המשך = כותב. בקרה שלילית: על main EOF = דלג
    בשני הצבעים, והדיסק אינו ברשימת הכתיבה."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL), "")
    assert "Choose [1-3]" in err, err
    assert written == "sda"


def test_no_answer_on_a_red_disk_skips_it(tmp_path):
    written, run, err = preflight(tmp_path, full(HEALTH_OK), "", failed=("sda",))
    assert "Choose [1-2]" in err, err
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"


# --- #875: log() בתוך הלכידה -- disk_event שלא נמסר אינו "דיסק" ------------------

def test_an_undelivered_disk_event_does_not_leak_into_the_write_list(tmp_path):
    """‏#875: ‏`smart_gate` לוכד את `smart_preflight` ב-`$( )`, ובתוכו
    ‏`smart_send_event` מדווח על disk_event שלא נמסר דרך `log` -- שכותב גם
    ל-stdout. עם curl כושל (שרת בעדכון, 5xx) הרשימה חייבת להיות **בדיוק**
    שמות הדיסקים, וההודעה בלוג. בקרה שלילית: על main הרשימה מכילה
    `imagectl: disk_event for sda was not delivered`."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    for d in ("sda", "sdb"):
        (dev / d).write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent") + PRELUDE
             + 'http_post_json() { return 22; }; '   # curl -f על 5xx
             + 'smart_gate sess1 sda sdb; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[:2] == ["sda sdb", "rc=0"], out.stdout
    assert "imagectl:" not in out.stdout, out.stdout
    assert "disk_event for sda was not delivered" in (run / "agent.log").read_text()


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
    sc = make_smartctl(tmp_path, full(HEALTH_FAIL, realloc=4))
    base = env(run, dev, smartctl=sc) + PRELUDE
    assert sh(base + 'smart_hello_field sdb').stdout.strip() == "unchecked"
    sh(base + 'smart_probe sda >/dev/null')
    assert sh(base + 'smart_hello_field sda').stdout.strip() == "fail"


# --- disk_event: מבנה החוזה --------------------------------------------------

def test_event_json_is_valid_and_carries_the_contract(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_OK, realloc=3))
    out = sh(env(run, dev, smartctl=sc) + PRELUDE
             + 'smart_probe sda >/dev/null; '
             + 'smart_event_json sess1 sda ok ok rescue rescue')
    assert out.returncode == 0, out.stderr
    ev = json.loads(out.stdout)
    assert ev["session_id"] == "sess1"
    assert ev["disk"] == "sda"
    assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
    assert ev["smart"]["verdict"] == "ok"
    assert ev["smart"]["reason"] == "ok"
    assert ev["smart"]["realloc"] == 3
    assert ev["write_state"] == "rescue"
    assert ev["decision"] == "rescue"
