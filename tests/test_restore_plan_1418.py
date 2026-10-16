"""‏#1418 / ‏#1419 / ‏#1420 — `validate_restore_plan` תחת busybox, כמו בסוכן.

השער של #1408 עבר את כל הטסטים ונכשל בכל שחזור אמיתי, משלוש סיבות
שאף אחת מהן לא הייתה נראית בלי להריץ את הפונקציה עצמה:

* ‏`set -- sgdisk ...` דרס את `$1`/`$2` — ‏sgdisk נשאל על `/dev/sgdisk` (#1418).
* ‏busybox awk לא מפענח `[^\\/|[:cntrl:]]` — כל תוכנית "malformed" (#1420).
* מחיצה בלי מערכת קבצים (MSR, ‏BIOS boot) נרשמת `fs=""` — ונדחתה (#1419).

מריצים את הפונקציה האמיתית תחת `busybox ash` עם ה-awk של busybox, עם
‏sgdisk מדומה שרושם את הארגומנטים שלו.
"""

from __future__ import annotations

from pathlib import Path

from test_json_escape import BUSYBOX, bb, source_line
from native import requires_native

pytestmark = requires_native(
    ("busybox", BUSYBOX), posix=True,
    why="הסוכן רץ תחת busybox ash, ו-#1420 הוא באג של busybox awk דווקא")

ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
MSR = "E3C9E316-0B5C-4DB8-817D-F92DF00215AE"
BASIC = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
SHA = "a" * 64

ROWS = [
    f"1|{ESP}|esp|vfat|2048|104857600|p1.img.zst|{SHA}|false|||8000000000000000",
    f"2|{MSR}|msr||206848|16777216|p2.img.zst|{SHA}|false||",
    f"3|{BASIC}|windows|ntfs|239616|10737418240|p3.img.zst|{SHA}|true||",
]


def _validate(tmp_path: Path, rows: list[str]) -> tuple[str, str]:
    run = tmp_path / "run"
    run.mkdir()
    plan = tmp_path / "plan.txt"
    plan.write_text("\n".join(rows) + "\n", encoding="utf-8")
    calls = tmp_path / "sgdisk.args"
    body = (
        f'RUN_DIR="{run.as_posix()}"; LOG_FILE="{(tmp_path / "log").as_posix()}"\n'
        f'DEVROOT="{(tmp_path / "dev").as_posix()}"\n'
        'log() { :; }\n'
        + source_line("restore.sh")
        + f'manifest_plan() {{ cat "{plan.as_posix()}"; }}\n'
        'blockdev() { echo 500118192; }\n'
        'json_get() { [ "$1" = manifest.json ] && echo 11111111-2222-3333-4444-555555555555; }\n'
        f'sgdisk() {{ echo "$*" > "{calls.as_posix()}"; }}\n'
        'if validate_restore_plan sda manifest.json; then echo OK; else echo "FAIL $PLAN_ERROR"; fi\n'
    )
    out = bb(tmp_path, body).strip()
    return out, calls.read_text(encoding="utf-8").strip() if calls.exists() else ""


def test_a_windows_plan_with_msr_passes_and_sgdisk_sees_the_real_disk(tmp_path):
    out, args = _validate(tmp_path, ROWS)
    assert out == "OK", out
    assert args.endswith(f" {(tmp_path / 'dev').as_posix()}/sda"), args                       # ‏#1418: לא /dev/sgdisk
    assert "-U 11111111-2222-3333-4444-555555555555" in args       # ‏#1418: המניפסט, לא "--pretend"
    assert "-n 2:206848:" in args and f"-t 2:{MSR}" in args        # ‏#1419: MSR נשאר בטבלה
    assert "-A 1:=:0x8000000000000000" in args
    # ‏#1434: ההדמיה מתחילה מטבלה ריקה (--clear); בלעדיה המחיצות הקיימות
    # נשארות בזיכרון, ו-n- נכשל על כל דיסק משומש
    assert args.startswith("-a 1 --pretend --clear "), args


def test_a_file_name_with_a_path_separator_is_still_rejected(tmp_path):
    for bad in ("../p3.img.zst", "sub\\p3.img.zst", ""):
        rows = ROWS[:2] + [ROWS[2].replace("p3.img.zst", bad)]
        out, args = _validate_in(tmp_path, bad, rows)
        assert out == "FAIL תוכנית השחזור פגומה — הדיסק לא שונה", (bad, out)
        assert args == "", "sgdisk רץ על תוכנית שנדחתה"


def test_an_unknown_filesystem_is_still_rejected(tmp_path):
    rows = ROWS[:2] + [ROWS[2].replace("|ntfs|", "|xfs|")]
    out, args = _validate(tmp_path, rows)
    assert out == "FAIL תוכנית השחזור פגומה — הדיסק לא שונה", out
    assert args == ""


def _validate_in(tmp_path: Path, tag: str, rows: list[str]) -> tuple[str, str]:
    sub = tmp_path / f"case{abs(hash(tag))}"
    sub.mkdir()
    return _validate(sub, rows)


def test_partitions_out_of_disk_order_pass_and_a_real_overlap_is_rejected(tmp_path):
    """‏#1428: ב-awk `n` לא אותחל — ‏s[""] מול e[0] — ולכן כל אימג' שהאינדקסים
    שלו אינם בסדר הדיסק (Debian מ-VBUILD: מחיצה 14 לפני 1) נדחה."""
    reordered = [ROWS[2], ROWS[0], ROWS[1]]
    out, args = _validate_in(tmp_path, "order", reordered)
    assert out == "OK", out
    unknown = [ROWS[0], ROWS[1].replace("|msr||", "|msr|unknown|"), ROWS[2]]
    out, _ = _validate_in(tmp_path, "unknown-fs", unknown)   # ‏_fs_of כש-blkid יצא ≠0
    assert out == "OK", out
    overlap = ROWS[:2] + [ROWS[2].replace("|239616|", "|206848|")]   # על MSR
    out, args = _validate_in(tmp_path, "overlap", overlap)
    assert out == "FAIL תוכנית השחזור פגומה — הדיסק לא שונה", out
    assert args == ""


def test_a_local_clone_plan_and_a_swap_partition_pass_without_a_stream_file(tmp_path):
    """‏#1433: בשיכפול מקומי הקובץ הוא `local:pN` ואין sha; ל-swap אין קובץ
    ואין sha. השער של #1408 דחה את שניהם — שיכפול מקומי נכשל תמיד."""
    local = [r.replace(f"p{i}.img.zst|{SHA}", f"local:p{i}|")
             for i, r in zip((1, 2, 3), ROWS)]
    out, _ = _validate_in(tmp_path, "local", local)
    assert out == "OK", out
    swap = ROWS[:2] + [ROWS[2].replace(f"|windows|ntfs|", "|swap|swap|").replace(f"p3.img.zst|{SHA}", "|")]
    out, _ = _validate_in(tmp_path, "swap", swap)
    assert out == "OK", out
    for bad in (ROWS[2].replace(f"p3.img.zst|{SHA}", "local:p3|abc"),       # local עם sha חלקי
                ROWS[2].replace(f"p3.img.zst|{SHA}", "|"),                  # ntfs בלי קובץ
                ROWS[2].replace(f"p3.img.zst|{SHA}", "local:../p3|")):
        out, args = _validate_in(tmp_path, bad, ROWS[:2] + [bad])
        assert out == "FAIL תוכנית השחזור פגומה — הדיסק לא שונה", (bad, out)
