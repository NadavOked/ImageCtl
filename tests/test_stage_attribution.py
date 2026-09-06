"""ייחוס שלב: מזייפים שלב אחד, מריצים את הפונקציה האמיתית, בודקים
את המחרוזת שהטכנאי רואה ואת מצב היעד — לא קוד יציאה.

למה הקובץ הזה קיים. ‏#440 ו-#444 (וגם צד השחזור של #443) שרדו לא כי
לא נכתבו טסטים, אלא כי הטסטים כיסו את הטופולוגיות שהמחבר דמיין:

- ``test_a_stream_cut_at_the_source`` מזייף מקור שנופל *אחרי* ש-fanout
  צרך את כל המטען (``GOOD_FANOUT``) — לא מגירה אחרונה שמתה באמצע.
- ``test_sigpipe_does_not_kill_the_whole_machine`` יש בו אח ששרד.
- טסט ההרחבה מחליף את ``grow_filesystem`` ב-``echo`` וטוען ``rc=0``.
- ``test_expansion_knows_every_filesystem_family`` הוא חיפוש מחרוזת
  בקוד, לא הרצה.

הדפוס (העתק אותו למסלול הבא, ~30 דקות):

1. זיוף של **שלב אחד** שנופל (fanout / ntfsresize / e2fsck / ...).
2. הרצת הפונקציה האמיתית (``restore_partition_drawers`` /
   ``run_restore`` / ``run_restore_drawers``).
3. ``assert`` על ``targets/<dev>/error`` ו-``targets/<dev>/state``.

בקרה שלילית — להחזיר **את כל** קבצי הסוכן, לא אחד::

    git checkout origin/main -- agent/lib/drawers.sh \\
        agent/lib/expand.sh agent/lib/restore.sh
    python -m pytest tests/test_stage_attribution.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_stream_honesty import GOOD_SHA, call, drawer_run, errors_of
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))


def state_of(run: Path, dev: str) -> str:
    return (run / "targets" / dev / "state").read_text(encoding="utf-8").strip()


def error_of(run: Path, dev: str) -> str:
    path = run / "targets" / dev / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- זיופי שלב ---------------------------------------------------------------

#: fanout של המגירה האחרונה שמתה: בולע 64KB (לא את כולו — כדי שהמקור
#: יקבל SIGPIPE), פותח את ה-fifo כדי שהצינור ייגמר ולא ייתקע על open,
#: מדווח ``failed write error`` ויוצא 1. זו הטופולוגיה ש-GOOD_FANOUT
#: (צורך הכול, יוצא 0) ו-DYING_FANOUT (יוצא 137 בלי שורת דוח) לא מכסות.
LAST_DRAWER_DIES = (
    "#!/bin/sh\n"
    "shift\n"
    "for out; do ( : > \"$out\" ) & done\n"
    "head -c 65536 > /dev/null\n"
    "wait\n"
    "for out; do echo \"$out failed write error\"; done\n"
    "exit 1\n"
)

#: כמו למעלה, בלי שורת דוח — הדרך לבדוק שחסר fanout.out אינו "רשת"
#: ואינו "הדיסק תקין".
LAST_DRAWER_DIES_SILENT = (
    "#!/bin/sh\n"
    "shift\n"
    "for out; do ( : > \"$out\" ) & done\n"
    "head -c 65536 > /dev/null\n"
    "wait\n"
    "exit 1\n"
)

#: partclone שכותב את שורת ה-EIO ללוג שכבר קיים ולא נקרא, ויוצא 1.
DYING_PARTCLONE = (
    "#!/bin/sh\n"
    "log=/dev/null\n"
    "while [ $# -gt 0 ]; do\n"
    "  case \"$1\" in -L) log=\"$2\"; shift 2 ;; *) shift ;; esac\n"
    "done\n"
    "cat > /dev/null\n"
    "echo \"ERROR: Input/output error\" > \"$log\"\n"
    "exit 1\n"
)


# --- #440: דיסק מת במגירה היחידה --------------------------------------------


def test_the_last_drawer_dying_is_a_disk_fault_not_a_cut_stream(tmp_path):
    """מגירה יחידה, fanout.rc=1, source.rc=141.

    לפני התיקון ההודעה היא ``הזרם נקטע (מקור rc=141)`` והטכנאי נשלח
    לרשת. אחריו — ההתקן והכתיבה, עם מה ש-fanout ו-partclone כתבו.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": LAST_DRAWER_DIES, "partclone.dd": DYING_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=1"), out
    assert state_of(run, "sda") == "failed"
    err = errors_of(run, disks=("sda",))["sda"]
    assert "הכתיבה לדיסק נכשלה" in err, err
    assert "write error" in err, err
    assert "Input/output error" in err, err
    assert "מקור rc=" not in err, err
    assert "הזרם נקטע" not in err, err
    assert "sha256" not in err, err


def test_a_missing_fanout_report_is_neither_ok_nor_a_network_cut(tmp_path):
    """עיקרון 5 על המסווג עצמו: אין fanout.out — אין דיווח, לא רשת."""
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": LAST_DRAWER_DIES_SILENT, "partclone.dd": DYING_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=1"), out
    err = errors_of(run, disks=("sda",))["sda"]
    assert "אין דיווח" in err, err
    assert "הכתיבה לדיסק נכשלה" in err, err
    assert "מקור rc=" not in err, err
    assert "הזרם נקטע" not in err, err
    assert state_of(run, "sda") == "failed"


# --- #444: הרחבה שנכשלה אינה done ------------------------------------------

FAILING = "#!/bin/sh\nexit 1\n"
OK = "#!/bin/sh\nexit 0\n"
E2FSCK_CORRECTED = "#!/bin/sh\nexit 1\n"
E2FSCK_UNFIXED = "#!/bin/sh\nexit 4\n"


def grow_drawers(tmp_path, *, fs="ntfs", disks=("sda",), stubs=None,
                 mark=True) -> tuple[Path, str]:
    """``run_restore_drawers`` האמיתי, עם הכתיבה מוחלפת — מה שנבדק
    הוא רק מה קורה ליעד אחרי ``finish_grow``."""
    run = tmp_path / "run"
    run.mkdir()
    extra = {"ntfsresize": OK, "e2fsck": OK, "resize2fs": OK}
    extra.update(stubs or {})
    marks = ""
    if mark:
        marks = "".join(
            f"echo '2|{fs}' > \"$RUN_DIR/targets/{d}/expanded\"; "
            for d in disks)
    out = run_sh(
        make_stubs(tmp_path / "stubs", extra)
        + f"export RUN_DIR={posix(run)!r} DEVROOT=/dev; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/expand.sh; . {posix(AGENT)}/lib/drawers.sh; "
        "disk_fits() { return 0; }; apply_gpt() { return 0; }; "
        "expand_last() { return 0; }; "
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        "restore_partition_drawers() { return 0; }; "
        + "".join(f"target_init {d} 100; " for d in disks)
        + marks
        + "run_restore_drawers multicast http://s img m.json "
        + " ".join(disks) + '; echo "rc=$?"'
    )
    return run, out


def grow_station(tmp_path, *, fs="ntfs", stubs=None, mark=True) -> tuple[Path, str]:
    """``run_restore`` האמיתי של תחנת הכיתה — אותו שלב, מסך אחר."""
    run = tmp_path / "run"
    run.mkdir()
    extra = {"ntfsresize": OK, "e2fsck": OK, "resize2fs": OK}
    extra.update(stubs or {})
    mark_cmd = f"echo '2|{fs}' > \"$RUN_DIR/targets/sda/expanded\"; " if mark else ""
    out = run_sh(
        make_stubs(tmp_path / "stubs", extra)
        + f"export RUN_DIR={posix(run)!r} DEVROOT=/dev; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/expand.sh; "
        "disk_fits() { return 0; }; apply_gpt() { return 0; }; "
        "expand_last() { return 0; }; "
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        "restore_partition() { return 0; }; "
        "target_init sda 100; "
        + mark_cmd
        + 'run_restore multicast sda http://s img m.json; echo "rc=$?"'
    )
    return run, out


def test_ntfsresize_failure_after_gpt_grow_is_not_done(tmp_path):
    """המחיצה כבר גדלה; ntfsresize יוצא 1. לפני התיקון: done, 100%."""
    run, out = grow_drawers(tmp_path, fs="ntfs", stubs={"ntfsresize": FAILING})
    assert state_of(run, "sda") != "done", (
        f"הרחבה שנכשלה עדיין done; rc-line={out.strip()!r} "
        f"error={error_of(run, 'sda')!r}")
    err = error_of(run, "sda")
    assert "ההרחבה של מערכת הקבצים" in err, err
    assert "ntfs" in err, err
    assert (run / "state").read_text(encoding="utf-8").strip() == "failed"


def test_resize2fs_failure_after_gpt_grow_is_not_done(tmp_path):
    run, _out = grow_drawers(
        tmp_path, fs="ext4", stubs={"e2fsck": OK, "resize2fs": FAILING})
    assert state_of(run, "sda") != "done"
    assert "ההרחבה של מערכת הקבצים" in error_of(run, "sda")
    assert "ext4" in error_of(run, "sda")


def test_e2fsck_exit_4_is_not_done_even_if_resize2fs_would_succeed(tmp_path):
    """המלכודת: e2fsck=4 ו-resize2fs=0. הקוד הישן זרק את 4 והחזיר 0."""
    run, _out = grow_drawers(
        tmp_path, fs="ext4",
        stubs={"e2fsck": E2FSCK_UNFIXED, "resize2fs": OK})
    assert state_of(run, "sda") != "done"
    assert "ההרחבה של מערכת הקבצים" in error_of(run, "sda")


def test_unknown_filesystem_after_gpt_grow_is_not_done(tmp_path):
    run, _out = grow_drawers(tmp_path, fs="xfs")
    assert state_of(run, "sda") != "done"
    err = error_of(run, "sda")
    assert "ההרחבה של מערכת הקבצים" in err, err
    assert "xfs" in err, err


def test_the_classroom_station_also_refuses_done_when_grow_fails(tmp_path):
    """אותו באג ב-restore.sh, מכונה עם מסך."""
    run, out = grow_station(tmp_path, fs="ntfs", stubs={"ntfsresize": FAILING})
    assert out.strip().endswith("rc=1"), out
    assert state_of(run, "sda") != "done"
    assert "ההרחבה של מערכת הקבצים" in error_of(run, "sda")
    assert (run / "state").read_text(encoding="utf-8").strip() == "failed"


def test_one_drawer_failing_grow_leaves_the_machine_partial(tmp_path):
    """למגירות כבר יש partial, והקונסולה מציגה אותו (#67). כאן הוא
    נולד מכשל הרחבה במגירה אחת, לא מכשל כתיבה."""
    run, out = grow_drawers(
        tmp_path, fs="ntfs", disks=("sda", "sdb"),
        stubs={"ntfsresize": (
            "#!/bin/sh\n"
            "case \"$*\" in *sdb*) exit 1 ;; *) exit 0 ;; esac\n"
        )})
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert state_of(run, "sdb") != "done"
    assert "ההרחבה של מערכת הקבצים" in error_of(run, "sdb")
    assert (run / "state").read_text(encoding="utf-8").strip() == "partial"


# --- רדיוס הפגיעה: אזהרת הרחבה אינה נכשלת את העבודה כולה -----------------


def test_e2fsck_that_corrected_errors_still_reaches_done(tmp_path):
    """e2fsck יוצא 1 כשהוא תיקן — הצלחה, לא כישלון. בלי זה התיקון
    הופך כל כרך עם inode שתוקן לעבודה שנכשלה."""
    run, out = grow_drawers(
        tmp_path, fs="ext4",
        stubs={"e2fsck": E2FSCK_CORRECTED, "resize2fs": OK})
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert error_of(run, "sda") == ""


def test_nothing_to_grow_is_still_done(tmp_path):
    """בלי סימון expanded אין מה להגדיל — זה לא כשל."""
    run, out = grow_drawers(tmp_path, fs="ntfs", mark=False)
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
