"""‏#87: כיווץ מחיצת ה-NTFS של דיסק המקור לפני הקליטה, והחזרתה אחריה.

דיסק בנייה של 512GB עם 100GB בשימוש ייצר עד כאן אימג' של 512GB, ובדיקה
2.7 חסמה אותו בצדק על כל כונן 256. הכיווץ (‏agent/lib/shrink.sh) רץ
**על המקור**, אחרי שער המהובר (‏#651) ולפני שהזרם מתחיל, ורק אחרי
שאדם ליד המכונה אמר כן. המניפסט רושם את הפריסה המצומצמת —
‏`size_bytes` קטן, ‏`shrunk_from_bytes` לתיעוד, וכל מחיצה שיושבת אחרי
ווינדוס (‏recovery) זזה אחורה בהפרש, עם `source_start_sector` — ולכן
‏`min_target_bytes` ו-`required_bytes` בשחזור קטנים בלי שינוי שם.

כל צעד הרסני נמדד כאן על **הסדר** ועל **הארגומנטים** שהכלים קיבלו,
ולא על "רץ בלי שגיאה": ‏ntfsfix -b -d לפני ntfsresize, ‏-s עם המספר
המדויק, ‏sgdisk עם ה-GUID/השם/הדגלים, ‏rereadpt, קריאה חוזרת של הגודל,
ואז partclone — ובסוף ההחזרה בסדר ההפוך. סטאבים של ntfsresize כמו
ב-`test_stage_attribution.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix, sh
from test_capture_refusals import (
    CURL_SHRINK_SERVER,
    PIPE_INPUT,
    capture_body,
    capture_run,
    first_index,
    refusal_reason,
)

pytestmark = requires_native(("bash", BASH))

WIN_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC_GUID = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
WIN_UGUID = "4C7B1E00-0000-4000-8000-000000000003"
REC_UGUID = "4C7B1E00-0000-4000-8000-000000000004"

#: הפריסה של Windows 11 על דיסק 512GB: ‏ESP, ‏C: ענקית, ‏recovery אחריה.
WIN_START = 1085440
WIN_SECTORS = 998_244_352            # ‏476 GiB, כפולה של 2048
REC_START = WIN_START + WIN_SECTORS
REC_SECTORS = 2_048_000              # ‏~1GB
MIN_BYTES = 100_000_000_000          # מה ש-ntfsresize --info "מודד"
SOURCE_DISK = bytes(512) + b"EFI PART" + bytes(504)

#: ‏sgdisk עם מצב: אחרי `-d N -n N:start:end` הוא זוכר את הגודל החדש
#: וקורא אותו בחזרה ב-`-i` — כמו דיסק אמיתי. כל קריאה נרשמת ל-sgdisk.calls
#: (הארגומנטים כשורה אחת), וזה מה שהטסטים מודדים.
SGDISK_WIN_REC = f'''#!/bin/sh
echo "$*" >> "$RUN_DIR/sgdisk.calls"
sizes="$RUN_DIR/sgdisk.sizes"
[ -f "$sizes" ] || printf '3={WIN_SECTORS}\\n4={REC_SECTORS}\\n' > "$sizes"
[ "$1" = "-a" ] && shift 2
if [ "$1" = "-d" ]; then
  idx="$2"; spec="$4"
  start=${{spec#*:}}; start=${{start%%:*}}; end=${{spec##*:}}
  n=$((end - start + 1))
  [ "${{SGDISK_READBACK_LIES:-0}}" = 1 ] || sed -i "s/^$idx=.*/$idx=$n/" "$sizes"
  : > "$RUN_DIR/sgdisk.written"
  exit 0
fi
if [ "$1" = "-i" ]; then
  n=$(sed -n "s/^$2=//p" "$sizes")
  # אחרי כתיבה: התחלה שזזה ב-2048 (SGDISK_START_LIES) -- מה ש-Align() של
  # sgdisk עושה לתחילה לא-מיושרת, בלי לצעוק.
  drift=0; [ "${{SGDISK_START_LIES:-0}}" = 1 ] && [ -f "$RUN_DIR/sgdisk.written" ] && drift=2048
  case "$2" in
    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI system partition)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000001"
       echo "First sector: 2048 (at 1024.0 KiB)"
       echo "Partition size: 204800 sectors (100.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'EFI system partition'" ;;
    3) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {WIN_UGUID}"
       echo "First sector: $(({WIN_START} + drift)) (at 530.0 MiB)"
       echo "Partition size: $n sectors (huge)"
       [ "${{SGDISK_NO_ATTRS:-0}}" = 1 ] || echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Basic data partition'" ;;
    4) echo "Partition GUID code: {REC_GUID} (Windows RE)"
       echo "Partition unique GUID: {REC_UGUID}"
       echo "First sector: {REC_START} (at the end)"
       echo "Partition size: $n sectors (1000.0 MiB)"
       echo "Attribute flags: 8000000000000001"
       echo "Partition name: 'Recovery'" ;;
  esac
  exit 0
fi
echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
echo "   1            2048          206847   100.0 MiB   EF00  EFI system partition"
echo "   3         {WIN_START}      {WIN_START + WIN_SECTORS - 1}   476.0 GiB   0700  Basic data partition"
echo "   4       {REC_START}       {REC_START + REC_SECTORS - 1}   1000.0 MiB  2700  Recovery"
'''

#: ‏ntfsresize: ‏--info מדפיס את המינימום; ‏-s / ‏-f -f יוצאים 0. כל קריאה
#: נרשמת ל-order.log ליד השאר. הכיווץ (‏-s) רושם גם מה הגיע ב-stdin: השאלה
#: "proceed?" של ntfsresize רואה EOF כ**המשך** (proceed_question: fgets
#: שנכשל אינו עוצר), ולכן התשובה חייבת להיות "y" מפורש ולא היעדר תשובה.
NTFSRESIZE_OK = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "Current volume size: $((({WIN_SECTORS} * 512) - 512)) bytes"
            echo "You might resize at {MIN_BYTES} bytes or 100000 MB (freeing 411000 MB)."
            exit 0 ;;
  "-s "*|*" -s "*) IFS= read -r ans; echo "ntfsresize stdin=${{ans:-EOF}}" >> "$RUN_DIR/order.log" ;;
esac
exit "${{NTFSRESIZE_RC:-0}}"
'''
NTFSFIX_OK = '#!/bin/sh\necho "ntfsfix $*" >> "$RUN_DIR/order.log"\nexit 0\n'
BLOCKDEV_OK = '#!/bin/sh\necho "blockdev $*" >> "$RUN_DIR/order.log"\nexit 0\n'
PARTCLONE_LOGGED = ('#!/bin/sh\necho "partclone $*" >> "$RUN_DIR/order.log"\n'
                    'head -c 4096 /dev/zero\nexit 0\n')

#: ‏3 הוא NTFS (ווינדוס), 4 הוא NTFS (‏recovery), ‏1 vfat. השער של #651
#: מאושר — הוא נבדק ב-test_capture_hibernation.py; כאן הוא לא הנושא.
FS_MAP = ('_fs_of() { case "$1" in *3|*4) echo ntfs ;; *) echo vfat ;; esac; }; '
          'capture_ntfs_hibernation_reason() { return 0; }; '
          # ‏#926: לדיסק יש סידורי — בלעדיו הרשומה בשרת מסורבת והכיווץ לא מתחיל.
          'disk_serial() { printf S926; }; disk_port() { printf 1; }; ')
SAY_YES = 'shrink_ask() { echo continue; }; '
SAY_PLAIN = 'shrink_ask() { echo plain; }; '
SAY_CANCEL = 'shrink_ask() { echo cancel; }; '


def stubs(**extra: str) -> dict[str, str]:
    base = {
        # ‏#926: curl שעונה גם ל-shrink-open/close — בלי רשומה בשרת אין כיווץ.
        "sgdisk": SGDISK_WIN_REC, "curl": CURL_SHRINK_SERVER,
        "partclone.ntfs": PARTCLONE_LOGGED, "partclone.dd": PIPE_INPUT, "partclone.fat": PIPE_INPUT,
        "ntfsresize": NTFSRESIZE_OK, "ntfsfix": NTFSFIX_OK, "blockdev": BLOCKDEV_OK,
    }
    base.update(extra)
    return base


def order(run: Path) -> list[str]:
    path = run / "order.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def sgdisk_calls(run: Path) -> list[str]:
    path = run / "sgdisk.calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def rewrites(run: Path) -> list[str]:
    """הקריאות שכותבות טבלה: מחיקה+יצירה באותו אינדקס (‏-d N -n N:...)."""
    return [c for c in sgdisk_calls(run) if " -d " in f" {c} "]


def manifest(run: Path) -> dict:
    return json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))


def part(m: dict, idx: int) -> dict:
    return next(p for p in m["partitions"] if p["index"] == idx)


def shrink_target(min_bytes: int, current: int) -> int:
    """הפונקציה עצמה, מהקובץ: המרווח והעיגול נבדקים על הקוד ולא על עותק."""
    out = sh(f". {posix(AGENT)}/lib/shrinkplan.sh; _shrink_target {min_bytes} {current}")
    return int(out.strip())


MIB = 1 << 20
GIB = 1 << 30

# --- החשבון --------------------------------------------------------------------


def test_the_target_is_the_minimum_plus_ten_percent_rounded_to_a_mebibyte():
    """‏100GB בשימוש → יעד של ~110GB; ההפרש מהנוכחי כפולה של MiB."""
    current = WIN_SECTORS * 512
    new = shrink_target(MIN_BYTES, current)
    assert MIN_BYTES + MIN_BYTES // 10 <= new < MIN_BYTES + MIN_BYTES // 10 + 2 * MIB
    assert (current - new) % MIB == 0, "הזנב במניפסט חייב להישאר מיושר"
    assert new < current


def test_a_small_volume_gets_the_two_gibibyte_floor_not_ten_percent():
    """‏10% של 5GB הם 500MB — פחות מדי לווינדוס שמתעורר; הרצפה היא 2GiB."""
    new = shrink_target(5 * 10**9, 200 * 10**9)
    assert new >= 5 * 10**9 + 2 * GIB


def test_nothing_to_shrink_returns_the_current_size_itself():
    """מינימום קרוב לנוכחי — ההפרש קטן ממגה, והפונקציה מחזירה את הנוכחי
    ולא מספר שגדול ממנו (ואז הקורא היה 'מכווץ' כלפי מעלה)."""
    target = -(-(MIN_BYTES + MIN_BYTES // 10) // MIB) * MIB
    current = target + 500_000
    assert shrink_target(MIN_BYTES, current) == current
    assert shrink_target(current, current) == current
    assert shrink_target(MIN_BYTES, target + MIB) == target


# --- המסלול המלא: כיווץ, זרם, החזרה ---------------------------------------------


def test_a_windows_disk_is_shrunk_before_the_stream_and_grown_back_after(tmp_path):
    """הסדר הוא הבטיחות: fix לפני resize, טבלה אחרי מערכת קבצים, קריאה
    חוזרת לפני שהזרם מתחיל, וההחזרה — טבלה ואז מערכת קבצים — אחרי הזרם."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")

    current = WIN_SECTORS * 512
    new = shrink_target(MIN_BYTES, current)
    new_sectors = new // 512
    steps = order(run)
    node = f"{posix(box / 'dev')}/sda3"

    def at(prefix: str, after: int = 0) -> int:
        for i, line in enumerate(steps):
            if i >= after and line.startswith(prefix):
                return i
        raise AssertionError(f"{prefix!r} לא נמצא אחרי {after} ב-{steps}")

    info = at("ntfsresize --info")
    resize = at(f"ntfsresize -s {new} --no-progress-bar {node}", info)
    reread = at("blockdev --rereadpt", resize)
    fix_after = at(f"ntfsfix -d {node}", reread)
    stream = at("partclone", fix_after)
    # ההחזרה: טבלה (rereadpt שני), ואז מתיחה (double force), ואז fix.
    reread_back = at("blockdev --rereadpt", stream)
    grow = at(f"ntfsresize -f -f --no-progress-bar {node}", reread_back)
    at(f"ntfsfix -d {node}", grow)

    shrink_end = WIN_START + new_sectors - 1
    shrunk = rewrites(run)
    assert len(shrunk) == 2, shrunk
    assert f"-n 3:{WIN_START}:{shrink_end} -t 3:{WIN_GUID} -u 3:{WIN_UGUID} -c 3:Basic data partition -A 3:=:0x0000000000000000" in shrunk[0], shrunk[0]
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1} -t 3:{WIN_GUID} -u 3:{WIN_UGUID} -c 3:Basic data partition" in shrunk[1], shrunk[1]
    # הסימון נמחק אחרי ההחזרה — פעם אחת, לא שוב ב-_capture_failed.
    assert not (run / "targets" / "sda" / "shrunk").exists()


def test_the_manifest_describes_the_compacted_layout(tmp_path):
    """‏size_bytes מכווץ + shrunk_from_bytes; ה-recovery זזה אחורה בדיוק
    בהפרש ונושאת source_start_sector; והרצפה נגזרת מהפריסה החדשה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    current = WIN_SECTORS * 512
    new = shrink_target(MIN_BYTES, current)
    delta_sectors = (current - new) // 512

    win = part(m, 3)
    assert win["size_bytes"] == new
    assert win["shrunk_from_bytes"] == current
    assert win["start_sector"] == WIN_START
    assert "source_start_sector" not in win, "המועמדת לא זזה — השדה חסר, לא 0"
    assert win["expandable"] is True

    rec = part(m, 4)
    assert rec["start_sector"] == REC_START - delta_sectors
    assert rec["source_start_sector"] == REC_START
    assert rec["size_bytes"] == REC_SECTORS * 512
    assert rec["attrs"] == "8000000000000001"
    assert "shrunk_from_bytes" not in rec
    assert rec["start_sector"] % 2048 == 0, "הזנב חייב להישאר מיושר ל-2048"

    esp = part(m, 1)
    assert "shrunk_from_bytes" not in esp and "source_start_sector" not in esp

    # הרצפה: סוף ה-recovery החדש + מגה, לא 512GB.
    end = (rec["start_sector"] * 512) + rec["size_bytes"]
    assert m["min_target_bytes"] == (end + 2 * MIB - 1) // MIB * MIB
    assert m["min_target_bytes"] < 130 * 10**9


def test_the_shrunk_manifest_is_what_restore_and_the_server_require(tmp_path):
    """הקצה השני: אותו מניפסט, דרך `required_bytes` של restore.sh ושל
    השרת — שניהם רואים ~110GB ולא 512, בלי שינוי בקוד שלהם."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    from server.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"]
    assert required_bytes(m) < 256_060_514_304, "האימג' עדיין לא נכנס ל-SSD של 256"


# --- ההכרעה של האדם ------------------------------------------------------------


def test_cancel_stops_the_capture_before_anything_is_written(tmp_path):
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_CANCEL)
    reason = refusal_reason(box, run, out)
    assert "הכיווץ לא אושר" in reason, reason
    assert "GB" in reason, "השאלה עצמה — המספרים — חייבת להגיע לקונסולה"
    steps = order(run)
    assert steps == [s for s in steps if s.startswith("ntfsresize --info")], steps
    assert not rewrites(run)
    assert not (run / "new-manifest.json").exists()


def test_no_answer_is_cancel_not_yes(tmp_path):
    """‏EOF על stdin (מסך שנעלם, run_sh עם DEVNULL) — שום דבר לא מכווץ."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP)
    reason = refusal_reason(box, run, out)
    assert "הכיווץ לא אושר" in reason, reason
    assert not [s for s in order(run) if s.startswith("ntfsresize -s")]


def test_capture_without_shrinking_is_still_offered(tmp_path):
    """‏[2]: ההתנהגות של עד היום — קריאה בלבד, האימג' בגודל המקור."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_PLAIN)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    assert part(m, 3)["size_bytes"] == WIN_SECTORS * 512
    assert "shrunk_from_bytes" not in part(m, 3)
    assert part(m, 4)["start_sector"] == REC_START
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsresize -f", "ntfsfix"))]
    assert not rewrites(run)


def test_the_text_menu_maps_keys_and_eof(tmp_path):
    """‏shrink_ask עצמו: 1/2/3 → continue/plain/cancel, EOF → cancel, קלט
    לא-חוקי מצייר שוב ולא בוחר בשקט. ‏#929: שאלה **אחת** על קובץ תוכנית —
    שורה למחיצה — והמסך מציג את כולן."""
    plan = tmp_path / "shrink.plan"
    plan.write_text(f"3|{WIN_GUID}|{WIN_UGUID}|{WIN_START}|{WIN_SECTORS}|512000000000|110000000000\n"
                    f"4|{WIN_GUID}|{DATA_UGUID}|{DATA_START}|{DATA_SECTORS}|53687091200|22548578304\n",
                    encoding="utf-8", newline="\n")

    def ask(keys: str) -> tuple[str, str]:
        out = sh(f"export IMAGECTL_TEST=1; . {posix(AGENT)}/lib/ui.sh; . {posix(AGENT)}/lib/shrinkplan.sh; "
                 f"printf '{keys}' | shrink_ask {posix(plan)!r} 2> {posix(tmp_path / 'screen')!r}").strip()
        return out, (tmp_path / "screen").read_text(encoding="utf-8", errors="replace")
    assert ask("1\\n")[0] == "continue"
    assert ask("2\\n")[0] == "plain"
    assert ask("3\\n")[0] == "cancel"
    assert ask("")[0] == "cancel"
    decision, screen = ask("x\\n9\\n1\\n")
    assert decision == "continue"
    assert "partition 3: 512.0 GB -> 110.0 GB" in screen and "partition 4: 53.7 GB -> 22.5 GB" in screen, screen
    assert screen.count("[1] Shrink and capture") == 3, "מסך אחד לשלושת הניסיונות, לא שאלה למחיצה"


# --- כשל בכל צעד = הקליטה נכשלת בגלוי, והמקור מוחזר -----------------------------


def test_a_failed_ntfsresize_fails_the_capture_and_says_the_state_is_unknown(tmp_path):
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"NTFSRESIZE_RC": "3"})
    reason = refusal_reason(box, run, out)
    assert "ntfsresize" in reason and "rc=3" in reason, reason
    assert "chkdsk" in reason, "אחרי resize שנפל המצב לא ידוע — וזה מה שההודעה חייבת לומר"
    assert not rewrites(run), "הטבלה לא נגעה אחרי resize שנפל"
    assert not (run / "new-manifest.json").exists()


def test_a_table_that_reads_back_wrong_fails_and_the_source_is_restored(tmp_path):
    """‏sgdisk יצא 0, הקרנל קרא, והגודל שחזר מהדיסק אינו מה שביקשנו (#51):
    כשל גלוי, ולפני היציאה המקור מקבל את הטבלה המקורית בחזרה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SGDISK_READBACK_LIES": "1"})
    reason = refusal_reason(box, run, out)
    assert "טבלת המחיצות לא אושרה" in reason and "שלב 3" in reason, reason
    calls = rewrites(run)
    assert len(calls) == 2, calls
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1}" in calls[1], "ההחזרה לא רצה"
    assert [s for s in order(run) if s.startswith("ntfsresize -f -f")], "מערכת הקבצים לא נמתחה בחזרה"
    assert not (run / "new-manifest.json").exists()


def test_a_failed_grow_back_after_a_failed_shrink_reaches_the_console(tmp_path):
    """שני כשלים בזה אחר זה: הטבלה לא אושרה (שלב 3), ובהחזרה הטבלה חזרה אבל
    מערכת הקבצים לא נמתחה. עד כאן הסיבה השנייה נכתבה ליומן (tmpfs) בלבד
    ונדרסה בשדה error — והמפעיל לא ידע אם דיסק הבנייה נשאר מכווץ. שתי
    הסיבות חייבות להגיע לקונסולה, בסדר הזה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=NTFSRESIZE_GROW_FAILS),
                                shell_pre=FS_MAP + SAY_YES, env={"SGDISK_READBACK_LIES": "1"})
    reason = refusal_reason(box, run, out)
    assert "טבלת המחיצות לא אושרה" in reason, reason
    assert "המקור לא הוחזר לגודלו" in reason, reason
    assert reason.index("טבלת המחיצות לא אושרה") < reason.index("המקור לא הוחזר לגודלו")
    assert "ראו ביומן" not in reason, "היומן נמחק באתחול -- הסיבה חייבת להיות כאן"


def test_the_entry_is_recreated_unaligned_and_its_start_is_read_back(tmp_path):
    """‏sgdisk -n מזיז תחילה שאינה כפולה של 2048 בלי לצעוק (CreatePartition →
    Align). כניסה שנוצרת מחדש חייבת להיווצר **בדיוק** במקומה (‏-a 1), והקריאה
    החוזרת משווה גם את התחילה, לא רק את הגודל: תחילה שזזה = NTFS שאינו
    בתחילת המחיצה = ווינדוס שלא עולה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    for call in rewrites(run):
        assert call.startswith("-a 1 -d 3 -n 3:"), call

    box, run, out = capture_run(tmp_path / "lies", stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SGDISK_START_LIES": "1"})
    reason = refusal_reason(box, run, out)
    assert "טבלת המחיצות לא אושרה" in reason and "שלב 3" in reason, reason
    assert not (run / "new-manifest.json").exists()


def test_an_entry_whose_flags_cannot_be_read_is_not_shrunk(tmp_path):
    """הדגלים והשם נקראים מ-sgdisk -i לפני הכיווץ כדי להיכתב מחדש; קריאה
    שלא הניבה דגלים אינה "אין דגלים" — היא סירוב, לפני שנכתב בייט."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SGDISK_NO_ATTRS": "1"})
    reason = refusal_reason(box, run, out)
    assert "sgdisk -i" in reason, reason
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsfix"))]
    assert not rewrites(run)


def test_the_source_is_never_treated_like_a_clone_target(tmp_path):
    """‏grow.sh מריץ `ntfsfix -b -d` על **יעד** שיכפול — ‏-b מוחק את רשימת
    הקלאסטרים הפגומים, וזה נכון לדיסק חדש שקיבל עותק. על דיסק הבנייה זה
    מוחק את הזיכרון של הסקטורים הפגומים שלו, ומעקף את הסירוב של ntfsresize
    עצמו לדיסק כזה (בלי -b משלו הוא יוצא 1). ולכן: ‏ntfsfix על המקור רק
    ‏-d ורק **אחרי** ה-resize; והכיווץ בלי -f — ווליום מלוכלך מסורב על ידי
    ntfsresize בקול ("Volume is scheduled for check"), ו-"y" מפורש עונה על
    השאלה שלו, כי EOF שם הוא המשך."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    steps = order(run)
    assert not [s for s in steps if s.startswith("ntfsfix -b")], steps
    node = f"{posix(box / 'dev')}/sda3"
    assert f"ntfsresize --info --no-progress-bar {node}" in steps, steps
    assert [s for s in steps if s.startswith("ntfsresize -s ")], steps
    assert "ntfsresize stdin=y" in steps, steps
    fixes = [s for s in steps if s.startswith("ntfsfix")]
    assert fixes == [f"ntfsfix -d {node}", f"ntfsfix -d {node}"], fixes


def test_an_unreadable_minimum_is_a_refusal_not_a_skip(tmp_path):
    """‏ntfsresize --info שלא הדפיס מספר: "לא הצלחנו לבדוק" ≠ "אין מה לכווץ"."""
    silent = '#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\necho "ERROR: NTFS is inconsistent"\nexit 1\n'
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=silent), shell_pre=FS_MAP + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק" in reason and "inconsistent" in reason, reason
    assert not (run / "new-manifest.json").exists()


#: כמו NTFSRESIZE_OK, אבל המתיחה בחזרה (‏-f -f) נכשלת.
NTFSRESIZE_GROW_FAILS = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "You might resize at {MIN_BYTES} bytes"; exit 0 ;;
  *"-f -f"*) exit 5 ;;
esac
exit 0
'''


def test_a_grow_back_failure_after_the_capture_is_a_warning_not_a_failure(tmp_path):
    """האימג' כבר עבר; ווינדוס עולה ממחיצה קטנה. ‏rc=0, מניפסט קיים,
    והאזהרה בשדה error כדי שהמפעיל ידע שהמקור נשאר מכווץ."""
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=NTFSRESIZE_GROW_FAILS), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    assert (run / "new-manifest.json").exists()
    warning = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert "המקור לא הוחזר לגודלו" in warning, warning


def test_the_grow_back_can_be_switched_off(tmp_path):
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"CAPTURE_SHRINK_RESTORE": "0"})
    assert out.strip().endswith("rc=0"), out
    assert not [s for s in order(run) if s.startswith("ntfsresize -f -f")]
    assert len(rewrites(run)) == 1


# --- מתי לא מכווצים ------------------------------------------------------------


def test_nothing_to_shrink_asks_nobody_and_writes_nothing(tmp_path):
    close = NTFSRESIZE_OK.replace(f"at {MIN_BYTES} bytes", f"at {WIN_SECTORS * 512 - MIB} bytes")
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=close),
                                shell_pre=FS_MAP + 'shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo cancel; }; ')
    assert out.strip().endswith("rc=0"), out
    assert "asked" not in order(run)
    assert part(manifest(run), 3)["size_bytes"] == WIN_SECTORS * 512


def test_direct_send_never_shrinks(tmp_path):
    """‏#715: הקריאה השנייה משווה sha256 לראשונה; מחיצה שהוחזרה ביניהן
    אינה אותם בייטים. בשידור ישיר הדיסק נשאר קריאה-בלבד, כמו שהמסך מבטיח."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"CAPTURE_SINK": "discard"})
    assert out.strip().endswith("rc=0"), out
    assert not [s for s in order(run) if s.startswith("ntfsresize")]
    assert part(manifest(run), 3)["size_bytes"] == WIN_SECTORS * 512


def test_a_hibernated_disk_is_refused_before_the_shrink_is_even_measured(tmp_path):
    """‏#651 קודם: ntfsresize מסרב למהובר ממילא, אבל השער שלנו רץ לפניו,
    ואף קריאה — גם לא --info — לא מגיעה לדיסק."""
    hibernated = ('_fs_of() { echo ntfs; }; '
                  'capture_ntfs_hibernation_reason() { echo "$CAPTURE_HIBERNATED_MESSAGE"; return 1; }; ')
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=hibernated + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "הדיסק מהובר" in reason
    assert order(run) == []


# --- ‏#929: כל מחיצות ה-NTFS מכווצות, כל אחת במקומה ---------------------------

DATA_START = WIN_START + WIN_SECTORS
DATA_SECTORS = 104_857_600            # ‏D: של 50 GiB אחרי C:
DATA_UGUID = "4C7B1E00-0000-4000-8000-000000000005"
MIN_D = 20_000_000_000                # מה ש-ntfsresize --info "מודד" ב-D:

#: דיסק בנייה עם D: **אחרי** C: (‏ESP, ‏MSR, ‏C: ענקית, ‏D:). עם מצב כמו
#: SGDISK_WIN_REC: הגדלים של 3 ו-4 נזכרים אחרי `-d N -n N:start:end` ונקראים
#: בחזרה ב-`-i`; ה**תחילות** קבועות — D: אינה זזה על המקור (הכרעת נדב 17/09).
SGDISK_C_THEN_D = f'''#!/bin/sh
echo "$*" >> "$RUN_DIR/sgdisk.calls"
sizes="$RUN_DIR/sgdisk.sizes"
[ -f "$sizes" ] || printf '3={WIN_SECTORS}\\n4={DATA_SECTORS}\\n' > "$sizes"
[ "$1" = "-a" ] && shift 2
if [ "$1" = "-d" ]; then
  idx="$2"; spec="$4"
  start=${{spec#*:}}; start=${{start%%:*}}; end=${{spec##*:}}
  n=$((end - start + 1))
  sed -i "s/^$idx=.*/$idx=$n/" "$sizes"
  exit 0
fi
if [ "$1" = "-i" ]; then
  n=$(sed -n "s/^$2=//p" "$sizes")
  case "$2" in
    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI system partition)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000001"
       echo "First sector: 2048 (at 1024.0 KiB)"
       echo "Partition size: 204800 sectors (100.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'EFI system partition'" ;;
    2) echo "Partition GUID code: E3C9E316-0B5C-4DB8-817D-F92DF00215AE (Microsoft reserved)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002"
       echo "First sector: 206848 (at 101.0 MiB)"
       echo "Partition size: 32768 sectors (16.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Microsoft reserved partition'" ;;
    3) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {WIN_UGUID}"
       echo "First sector: {WIN_START} (at 530.0 MiB)"
       echo "Partition size: $n sectors (huge)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Basic data partition'" ;;
    4) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {DATA_UGUID}"
       echo "First sector: {DATA_START} (at the end)"
       echo "Partition size: $n sectors (50.0 GiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Data'" ;;
  esac
  exit 0
fi
echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
echo "   1            2048          206847   100.0 MiB   EF00  EFI system partition"
echo "   2          206848          239615   16.0 MiB    0C01  Microsoft reserved partition"
echo "   3         {WIN_START}      {WIN_START + WIN_SECTORS - 1}   476.0 GiB   0700  Basic data partition"
echo "   4       {DATA_START}       {DATA_START + DATA_SECTORS - 1}   50.0 GiB    0700  Data"
'''

#: ‏ntfsresize שמודד לפי המחיצה: ‏sda3 → MIN_BYTES, ‏sda4 → MIN_D. ‏NTFSRESIZE_FAIL_ON
#: מפיל את `-s` על מחיצה אחת בלבד; ‏NTFSRESIZE_INFO_FAIL_ON — את `--info` שלה;
#: ‏NTFSRESIZE_GROW_FAIL_ON — את המתיחה בחזרה (`-f -f`) שלה.
NTFSRESIZE_TWO = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*)
    [ -n "${{NTFSRESIZE_INFO_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_INFO_FAIL_ON") echo "ERROR: NTFS is inconsistent. Run chkdsk /f"; exit 1 ;; esac
    case "$*" in *3) min={MIN_BYTES}; cur=$(({WIN_SECTORS} * 512 - 512)) ;; *) min={MIN_D}; cur=$(({DATA_SECTORS} * 512 - 512)) ;; esac
    echo "Cluster size       : 4096 bytes"
    echo "Current volume size: $cur bytes"
    echo "You might resize at $min bytes or $((min / 1000000)) MB (freeing some MB)."
    exit 0 ;;
  *"-f -f"*)
    [ -n "${{NTFSRESIZE_GROW_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_GROW_FAIL_ON") exit 5 ;; esac ;;
  "-s "*|*" -s "*)
    IFS= read -r ans; echo "ntfsresize stdin=${{ans:-EOF}}" >> "$RUN_DIR/order.log"
    [ -n "${{NTFSRESIZE_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_FAIL_ON") echo "ERROR: resize failed"; exit 3 ;; esac ;;
esac
exit 0
'''

#: ‏partclone שנופל על מחיצה 4 בלבד — כשל באמצע הזרם, אחרי ששתיהן כווצו.
PARTCLONE_FAILS_ON_4 = ('#!/bin/sh\necho "partclone $*" >> "$RUN_DIR/order.log"\n'
                        'case " $* " in *"/sda4 "*) echo "read error on sda4" >&2; exit 1 ;; esac\n'
                        'head -c 4096 /dev/zero\nexit 0\n')

FS_MAP_C_D = ('_fs_of() { case "$1" in *3|*4) echo ntfs ;; *) echo vfat ;; esac; }; '
              'capture_ntfs_hibernation_reason() { return 0; }; '
              'disk_serial() { printf S926; }; disk_port() { printf 1; }; ')
FS_MAP_C_EXT4 = FS_MAP_C_D.replace("*3|*4) echo ntfs", "*3) echo ntfs ;; *4) echo ext4")

SSD_256 = "256060514304"


def two_stubs(**extra: str) -> dict[str, str]:
    return stubs(sgdisk=SGDISK_C_THEN_D, ntfsresize=NTFSRESIZE_TWO, **extra)


def two_targets() -> tuple[int, int, int, int]:
    """(C: חדש, D: חדש, ההפרש של C: בסקטורים, ההפרש של D: בסקטורים)."""
    new_c = shrink_target(MIN_BYTES, WIN_SECTORS * 512)
    new_d = shrink_target(MIN_D, DATA_SECTORS * 512)
    return new_c, new_d, (WIN_SECTORS * 512 - new_c) // 512, (DATA_SECTORS * 512 - new_d) // 512


def positions(steps: list[str], *prefixes: str) -> list[int]:
    """המקום של כל קידומת ב-order.log, בסדר שנתבקש — נופל בשם על מה שחסר."""
    found = []
    after = 0
    for prefix in prefixes:
        for i, line in enumerate(steps):
            if i >= after and line.startswith(prefix):
                found.append(i)
                after = i + 1
                break
        else:
            raise AssertionError(f"{prefix!r} לא נמצא אחרי {after} ב-{steps}")
    return found


def rewritten_indexes(run: Path) -> list[str]:
    return [c.split(" -d ")[1].split(" ")[0] for c in rewrites(run)]


def test_c_and_d_are_both_shrunk_in_place_and_grown_back_in_reverse(tmp_path):
    """הכרעת נדב 17/09: **כל** מחיצת NTFS-data מכווצת **במקומה** — ‏D: נשארת
    בתחילתה על המקור, רק הגודל משתנה — ובסדר: מדידה של שתיהן לפני כתיבה
    ראשונה, רשומה אחת בשרת, ‏C: ואז D:, הזרם, ואז ההחזרה בסדר הפוך —
    ‏D: ואז C: — והרשומה נסגרת רק אחרי ששתיהן חזרו."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    new_c, new_d, _, _ = two_targets()
    dev = posix(box / "dev")
    steps = order(run)
    positions(steps,
              f"ntfsresize --info --no-progress-bar {dev}/sda3",
              f"ntfsresize --info --no-progress-bar {dev}/sda4",
              "shrink-open",
              f"ntfsresize -s {new_c} --no-progress-bar {dev}/sda3", "blockdev --rereadpt", f"ntfsfix -d {dev}/sda3",
              f"ntfsresize -s {new_d} --no-progress-bar {dev}/sda4", "blockdev --rereadpt", f"ntfsfix -d {dev}/sda4",
              "partclone",
              f"ntfsresize -f -f --no-progress-bar {dev}/sda4", f"ntfsfix -d {dev}/sda4",
              f"ntfsresize -f -f --no-progress-bar {dev}/sda3", f"ntfsfix -d {dev}/sda3",
              "shrink-close")
    assert steps.count("shrink-open") == 1 and steps.count("shrink-close") == 1, steps
    assert steps.index("shrink-open") > max(i for i, s in enumerate(steps) if s.startswith("ntfsresize --info")), \
        "שתי המדידות לפני הרשומה — לא נרשם כיווץ שלא נמדד"

    calls = rewrites(run)
    assert len(calls) == 4, calls
    assert f"-a 1 -d 3 -n 3:{WIN_START}:{WIN_START + new_c // 512 - 1} -t 3:{WIN_GUID} -u 3:{WIN_UGUID} -c 3:Basic data partition -A 3:=:0x0000000000000000" in calls[0], calls[0]
    assert f"-a 1 -d 4 -n 4:{DATA_START}:{DATA_START + new_d // 512 - 1} -t 4:{WIN_GUID} -u 4:{DATA_UGUID} -c 4:Data -A 4:=:0x0000000000000000" in calls[1], \
        "D: מכווצת **במקומה** — אותה תחילה על המקור"
    assert f"-d 4 -n 4:{DATA_START}:{DATA_START + DATA_SECTORS - 1}" in calls[2], "ההחזרה בסדר הפוך: D: ראשונה"
    assert f"-d 3 -n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1}" in calls[3], calls[3]
    assert not (run / "targets" / "sda" / "shrunk").exists()


def test_the_source_gpt_reads_back_exactly_what_it_was(tmp_path):
    """הטבלה על המקור אחרי הקליטה זהה לזו שלפניה — הגדלים (מה שהזיוף
    זוכר) והתחילות (מה שההחזרה כותבת) — לכל מחיצה שכווצה."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    assert (run / "sgdisk.sizes").read_text(encoding="utf-8") == f"3={WIN_SECTORS}\n4={DATA_SECTORS}\n"
    restores = rewrites(run)[2:]
    assert [c.split(" -n ")[1].split(" ")[0] for c in restores] == \
        [f"4:{DATA_START}:{DATA_START + DATA_SECTORS - 1}", f"3:{WIN_START}:{WIN_START + WIN_SECTORS - 1}"]


def test_the_manifest_compacts_both_and_marks_only_the_last_expandable(tmp_path):
    """היעד מקבל את הפריסה הצפופה: ‏C: בגודלה החדש במקומה; ‏D: זזה אחורה
    בהפרש של C: (‏source_start_sector זוכר את מקומה האמיתי) ובגודלה החדש;
    ‏expandable על D: בלבד; והרצפה היא סכום המינימומים ומחיצות המערכת."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    new_c, new_d, delta_c, _ = two_targets()

    win = part(m, 3)
    assert win["size_bytes"] == new_c and win["shrunk_from_bytes"] == WIN_SECTORS * 512
    assert win["start_sector"] == WIN_START and "source_start_sector" not in win
    assert win["expandable"] is False, "רק האחרונה נמתחת על היעד (expand_last)"

    data = part(m, 4)
    assert data["size_bytes"] == new_d and data["shrunk_from_bytes"] == DATA_SECTORS * 512
    assert data["start_sector"] == DATA_START - delta_c
    assert data["source_start_sector"] == DATA_START
    assert data["start_sector"] % 2048 == 0
    assert data["expandable"] is True

    for idx in (1, 2):
        assert "shrunk_from_bytes" not in part(m, idx) and "source_start_sector" not in part(m, idx)

    end = data["start_sector"] * 512 + data["size_bytes"]
    assert m["min_target_bytes"] == (end + 2 * MIB - 1) // MIB * MIB
    assert m["min_target_bytes"] < 140 * 10**9
    from server.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"]


def test_c_before_d_now_fits_a_256_drive(tmp_path):
    """הסירוב הישן של #929 ("‏C: אינה האחרונה — מחקו את D:") נעלם: שתיהן
    מכווצות, והאימג' נכנס ל-SSD של 256 — ‏target_floor_bytes נרשם."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"CAPTURE_TARGET_BYTES": SSD_256})
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    m = manifest(run)
    assert m["target_floor_bytes"] == int(SSD_256)
    assert m["min_target_bytes"] < int(SSD_256)


def test_one_question_lists_every_partition(tmp_path):
    """שאלה **אחת** למפעיל, עם הטבלה של כולן — לא שאלה למחיצה. ובביטול
    המספרים של שתיהן מגיעים לקונסולה (שדה error)."""
    asked = ('shrink_ask() { echo asked >> "$RUN_DIR/order.log"; cat "$1" > "$RUN_DIR/asked.plan"; echo cancel; }; ')
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + asked)
    reason = refusal_reason(box, run, out)
    assert order(run).count("asked") == 1
    plan = (run / "asked.plan").read_text(encoding="utf-8").splitlines()
    assert [ln.split("|")[0] for ln in plan] == ["3", "4"], plan
    new_c, new_d, _, _ = two_targets()
    assert "מחיצה 3" in reason and "מחיצה 4" in reason, reason
    assert f"{new_c / 1e9:.1f} GB" in reason and f"{new_d / 1e9:.1f} GB" in reason, reason
    assert not rewrites(run) and not [s for s in order(run) if s.startswith("ntfsresize -s")]


def test_a_failure_in_the_second_stream_grows_both_back_and_closes_after_both(tmp_path):
    """‏partclone נופל על D: אחרי ששתיהן כווצו: ‏C: ו-D: חוזרות לגודלן —
    ‏D: ואז C: — והרשומה בשרת (#926) נסגרת רק אחרי ש**שתיהן** חזרו."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(**{"partclone.ntfs": PARTCLONE_FAILS_ON_4}),
                                shell_pre=FS_MAP_C_D + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "partition 4" in reason, reason
    dev = posix(box / "dev")
    steps = order(run)
    positions(steps, f"ntfsresize -f -f --no-progress-bar {dev}/sda4", f"ntfsfix -d {dev}/sda4",
              f"ntfsresize -f -f --no-progress-bar {dev}/sda3", f"ntfsfix -d {dev}/sda3", "shrink-close")
    assert steps.count("shrink-close") == 1
    calls = rewrites(run)
    assert len(calls) == 4, calls
    assert f"-d 4 -n 4:{DATA_START}:{DATA_START + DATA_SECTORS - 1}" in calls[2]
    assert f"-d 3 -n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1}" in calls[3]
    assert (run / "sgdisk.sizes").read_text(encoding="utf-8") == f"3={WIN_SECTORS}\n4={DATA_SECTORS}\n"
    assert not (run / "new-manifest.json").exists()


def test_a_grow_back_that_fails_on_one_partition_keeps_the_record_open_and_names_it(tmp_path):
    """‏D: חזרה, ‏C: לא (ntfsresize -f -f על sda3 נפל): אין shrink-close,
    האזהרה נוקבת במחיצה 3 בלבד, והשרת מקבל את הסיבה (shrink-note)."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_GROW_FAIL_ON": "3"})
    assert out.strip().endswith("rc=0"), out
    warning = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert "המקור לא הוחזר לגודלו" in warning and "מחיצה 3" in warning, warning
    assert "מחיצה 4" not in warning, "‏D: חזרה — היא אינה חלק מהאזהרה"
    assert "shrink-close" not in order(run)
    assert [s for s in order(run) if s.startswith("shrink-note")]
    # ‏D: כן הוחזרה במלואה — הטבלה, המתיחה והניקוי — למרות הכשל ב-C:.
    dev = posix(box / "dev")
    positions(order(run), f"ntfsresize -f -f --no-progress-bar {dev}/sda4", f"ntfsfix -d {dev}/sda4")


def test_a_failed_shrink_of_the_second_partition_grows_the_first_back(tmp_path):
    """‏ntfsresize -s נופל על D: אחרי ש-C: כבר כווצה: הקליטה נכשלת בשם
    (מחיצה 4, chkdsk), ‏C: חוזרת לגודלה, וטבלת D: לא נגעה."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_FAIL_ON": "4"})
    reason = refusal_reason(box, run, out)
    assert "מחיצה 4" in reason and "rc=3" in reason and "chkdsk" in reason, reason
    assert rewritten_indexes(run) == ["3", "3"], rewrites(run)
    assert f"-n 3:{WIN_START}:{WIN_START + WIN_SECTORS - 1}" in rewrites(run)[1]
    assert (run / "sgdisk.sizes").read_text(encoding="utf-8") == f"3={WIN_SECTORS}\n4={DATA_SECTORS}\n"


def test_a_partition_that_cannot_be_measured_refuses_the_whole_capture(tmp_path):
    """‏--info נופל על D: (מלוכלכת): לא "מכווצים חלקית" — **כל** הקליטה
    מסורבת בשם, לפני שאלה, לפני רשומה, לפני בייט."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_INFO_FAIL_ON": "4"})
    reason = refusal_reason(box, run, out)
    assert "מחיצה 4" in reason and "לא הצלחנו לבדוק" in reason and "chkdsk" in reason, reason
    steps = order(run)
    assert steps == [s for s in steps if s.startswith("ntfsresize --info")], steps
    assert not rewrites(run) and not (run / "new-manifest.json").exists()


def test_a_non_ntfs_data_partition_is_named_when_the_image_still_does_not_fit(tmp_path):
    """‏C: (NTFS) כווצה, ‏D: היא ext4 — לא מכווצת כאן (מסלול e2fsck נפרד) —
    והאימג' עדיין גדול מהיעד: הסירוב אומר איזו מחיצה, למה, ומה לעשות."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_EXT4 + SAY_YES,
                                env={"CAPTURE_TARGET_BYTES": "128000000000"})
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason, reason
    assert "מחיצה 4" in reason and "ext4" in reason and "NTFS בלבד" in reason, reason
    assert f"{DATA_SECTORS * 512 / 1e9:.1f} GB" in reason, reason
    assert "אינה האחרונה" not in reason, "ההודעה הישנה של #929 — הכיווץ כבר אינו מוגבל לאחרונה"
    # ‏C: כן כווצה והוחזרה; ‏D: לא נמדדה אפילו.
    assert rewritten_indexes(run) == ["3", "3"]
    assert not [s for s in order(run) if s.startswith("ntfsresize --info") and s.endswith("sda4")]


def test_the_old_refusal_is_unchanged_when_the_last_partition_is_the_big_one(tmp_path):
    """‏ESP/C:/recovery, בלי כיווץ: אין מחיצת data שאינה NTFS, וההודעה היא זו
    של #87 — בלי תוספת."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_PLAIN,
                                env={"CAPTURE_TARGET_BYTES": SSD_256})
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason, reason
    assert "אינה האחרונה" not in reason and "NTFS בלבד" not in reason, reason
    assert reason.endswith("בייט לפני הקליטה"), reason


def test_on_the_target_only_the_last_partition_is_stretched(tmp_path):
    """הצד השני, על היעד: ‏apply_gpt/expand_last על המניפסט המצומצם —
    ‏D: (האחרונה) נמתחת עד סוף הכונן, ‏C: נשארת בגודלה המכווץ, במקומה."""
    from test_agent import run_expand
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    plan = ["|".join(str(p.get(k, "")) for k in ("index", "type_guid", "role", "fs", "start_sector", "size_bytes",
                                                   "file", "sha256")) + f"|{str(p['expandable']).lower()}|{p['unique_guid']}"
            for p in m["partitions"]]
    rc, calls, marker = run_expand(tmp_path / "target", plan, disk_sectors=976773168)
    assert rc == "rc=0", (rc, calls)
    assert marker == "4|ntfs", marker
    assert len(calls) == 1 and calls[0].startswith(f"-d 4 -n 4:{part(m, 4)['start_sector']}:0 "), calls
    assert not [c for c in calls if c.startswith("-d 3")], "‏C: אינה נמתחת ואינה זזה"


# --- הנעילות המבניות ------------------------------------------------------------


def test_the_shrink_runs_after_the_hibernation_gate_and_before_the_layout_math():
    lines = capture_body()
    gate = first_index(lines, "capture_ntfs_hibernation_reason")
    shrink = first_index(lines, "shrink_before_capture")
    layout = first_index(lines, "_min_target=$(awk")
    assert gate < shrink < layout, (gate, shrink, layout)
    assert shrink < first_index(lines, "partclone_for_fs")


def test_the_agent_loads_shrink_before_capture():
    src = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert src.index('"$LIB_DIR/shrink.sh"') < src.index('"$LIB_DIR/capture.sh"')


def test_every_shrink_step_checks_its_exit_code():
    """אין `|| true` ואין `2>/dev/null` על צעד הרסני (עיקרון 5)."""
    src = (AGENT / "lib" / "shrink.sh").read_text(encoding="utf-8")
    assert "|| true" not in src
    assert "ntfsfix -b" not in src, "‏-b מוחק את רשימת הקלאסטרים הפגומים -- לעולם לא על המקור"
    for tool in ("ntfsfix -d", "ntfsresize -s", "ntfsresize -f -f", "sgdisk -a 1 -d", "blockdev --rereadpt"):
        assert tool in src, tool
        for line in src.splitlines():
            if tool in line and not line.strip().startswith("#"):
                assert "2>/dev/null" not in line, line


def test_the_interfaces_document_carries_the_new_fields():
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    for field in ("shrunk_from_bytes", "source_start_sector"):
        assert f"`{field}`" in text, field
    assert "הכיווץ עצמו עדיין אינו ממומש" not in text
