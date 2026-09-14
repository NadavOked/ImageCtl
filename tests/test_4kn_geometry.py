"""‏#502: קליטה על 4Kn רושמת גיאומטריה לוגית, לא 512 קשיח.

הבאג המקורי: `capture.sh` הכפיל סקטורים ב-512 בארבעה מקומות וכתב
`"sector_size":512`. על NVMe 4Kn ‏(`logical_block_size=4096`) מחיצה של
238GB נרשמה כ-30GB, ובשחזור נבראה כך.

‏#670 מודד `logical_block_size` ומשתמש בו בגיאומטריית המחיצות, ב-swap,
ב-min_target ובשדה המניפסט. `_disk_bytes` נשאר `× 512` במכוון: sysfs
`/size` הוא תמיד יחידות 512, גם על 4Kn (מוסכמת קרנל, לא ניחוש).

ה-DoD המקורי ביקש סירוב בקליטה. #670 בחר מדידה במקום סירוב; השחזור
עדיין מסרב ל-`sector_size != 512` (`apply_gpt`), ולכן הנזק המקורי —
מחיצות שמינית מהאמת — אינו יכול לקרות על אימג' שנקלט אחרי #670.

אין תיקון ייצור כאן. הטסט מאשר שההתנהגות הנכונה כבר על main.
"""

from __future__ import annotations

import json

from native import requires_native
from test_agent import BASH, posix, sh
from test_capture_refusals import CURL_SINK, capture_run
from test_restore_evidence import build_box, log_of, rc_of

pytestmark = requires_native(("bash", BASH))

SECTOR = 4096
WRONG = 512
LINUX_START, LINUX_SECS = 2048, 204800
SWAP_START, SWAP_SECS = 206848, 4096
KERNEL_SECTORS = 2_097_152

#: sgdisk עם מחיצת linux ומחיצת swap — שני אתרי `size_bytes` של #502.
LINUX_AND_SWAP = (
    "#!/bin/sh\n"
    'if [ "$1" = "-i" ]; then\n'
    '  case "$2" in\n'
    "    1)\n"
    "      echo 'Partition GUID code: 0FC63DAF-8483-4772-8E79-3D69D8477DE4'\n"
    "      echo 'Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002'\n"
    "      echo 'First sector: 2048'\n"
    "      echo 'Partition size: 204800 sectors'\n"
    "      ;;\n"
    "    2)\n"
    "      echo 'Partition GUID code: 0657FD6D-A4AB-43C4-84E5-0933C84B4F4F'\n"
    "      echo 'Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000003'\n"
    "      echo 'First sector: 206848'\n"
    "      echo 'Partition size: 4096 sectors'\n"
    "      ;;\n"
    "  esac\n"
    "  exit 0\n"
    "fi\n"
    "echo 'Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001'\n"
    "echo 'Number  Start (sector)    End (sector)  Size       Code  Name'\n"
    "echo '   1            2048          206847   100.0 MiB   8300  Linux'\n"
    "echo '   2          206848          210943    16.0 MiB   8200  swap'\n"
)


def test_4kn_capture_writes_logical_geometry_not_512(tmp_path):
    """ארבעת האתרים מ-#502: sector_size, מחיצה, swap, min_target.

    הערך השגוי (סקטורים × 512) נשלל במפורש — אחרת הטסט היה עובר גם
    על הקיבוע הישן אם המספרים היו מתלכדים במקרה.
    """
    source = bytes(SECTOR) + b"EFI PART" + bytes(SECTOR - 8)
    logical = tmp_path / "box/sys/block/sda/queue/logical_block_size"
    sizef = tmp_path / "box/sys/block/sda/size"
    box, run, out = capture_run(
        tmp_path,
        image=source,
        stubs={
            "sgdisk": LINUX_AND_SWAP,
            "curl": CURL_SINK,
            # ברירת המחדל `cat >/dev/null` קוראת stdin, וב-while-read
            # על parts.txt היא בולעת את מחיצת ה-swap. partclone האמיתי
            # קורא מ-`-s` ולא מהצינור של הלולאה.
            "partclone.dd": "#!/bin/sh\nexit 0\n",
        },
        shell_pre=(
            f"printf '4096\\n' > {posix(logical)!r}; "
            f"printf '{KERNEL_SECTORS}\\n' > {posix(sizef)!r}; "
        ),
    )
    assert out.strip().endswith("rc=0"), (
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    )
    written = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    by_role = {p["role"]: p for p in written["partitions"]}
    linux, swap = by_role["linux"], by_role["swap"]
    end = (SWAP_START + SWAP_SECS) * SECTOR
    need = ((end + 2_097_151) // 1_048_576) * 1_048_576

    assert written["sector_size"] == SECTOR
    assert written["sector_size"] != WRONG
    assert linux["size_bytes"] == LINUX_SECS * SECTOR
    assert linux["size_bytes"] != LINUX_SECS * WRONG
    assert linux["start_sector"] == LINUX_START
    assert swap["size_bytes"] == SWAP_SECS * SECTOR
    assert swap["size_bytes"] != SWAP_SECS * WRONG
    assert swap["start_sector"] == SWAP_START
    assert written["min_target_bytes"] == need
    assert written["min_target_bytes"] != (
        ((SWAP_START + SWAP_SECS) * WRONG + 2_097_151) // 1_048_576
    ) * 1_048_576
    # sysfs /size הוא יחידות 512 גם על 4Kn — לא logical_block_size.
    assert written["source_disk_bytes"] == KERNEL_SECTORS * WRONG
    assert written["source_disk_bytes"] != KERNEL_SECTORS * SECTOR


def test_apply_gpt_refuses_4kn_instead_of_carving_by_512(tmp_path):
    """הנזק המקורי היה בשחזור. apply_gpt מסרב ל-4096 לפני sgdisk -n.

    בלי הסירוב, `_end=$((start + size_bytes / 512 - 1))` היה בורא מחיצה
    פי 8 מהפריסה הלוגית של אימג' 4Kn שנקלט אחרי #670.
    """
    box, run, prelude = build_box(tmp_path)
    out = sh(
        prelude
        + "json_get() { "
        "case \"$2\" in .sector_size) echo 4096 ;; .scheme) echo gpt ;;"
        " *) echo null ;; esac; }; "
        'apply_gpt sda m.json; echo "rc=$?"'
    )
    assert rc_of(out) == "rc=1", out
    log = log_of(run)
    assert "unsupported sector size: 4096" in log, log
    calls = box / "sgdisk.calls"
    recorded = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "--zap-all" not in recorded
    assert " -n " not in f" {recorded} "
