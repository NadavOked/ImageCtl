"""‏#51 — שחזור מוכרז מוצלח רק מול ראיה שנקראה בחזרה מהדיסק.

שני מסלולים הכריזו `done` בלי שהוכח שהבייטים הגיעו לכונן:

* ‏`apply_gpt` נגמרה ב-`sleep 1`, ולכן החזירה **תמיד 0**. קוד היציאה של
  ‏`sgdisk -e` נזרק, ו-`blockdev --rereadpt` נגמר ב-`|| true` — והוא
  הסימן היחיד שהקרנל קיבל את הטבלה ובנה את `/dev/sdaN`. בלי הצמתים
  האלה ‏`partclone -O /dev/sda3` יוצר **קובץ רגיל** ב-devtmpfs, כותב
  את המחיצה ל-RAM ויוצא 0 — וה-sha256 עובר, כי הוא נלקח על הבייטים
  שהתקבלו ולא על מה שיושב על הדיסק.
* ‏`manifest_plan` הוא `jq` בתוך צינור. ‏jq פולט שורות ואז נופל, וב-POSIX
  sh בלי `pipefail` קוד היציאה הזה בלתי נראה: תוכנית של שתי מחיצות
  מתוך שלוש נראית בדיוק כמו תוכנית שלמה, הלולאה נגמרת בשלום, והמכונה
  מכריזה `done`.

הזיופים כאן אינם "כדי לא לדרוש כלים": הם **המנגנון של השחזור**. ‏jq
מזויף כי הכשל שנבדק הוא jq שמת באמצע, ו-sgdisk/blockdev מזויפים כי
הכשל שנבדק הוא קוד היציאה שלהם. הבדיקה היחידה שאי אפשר לזייף בלי root
היא ‏`[ -b ]`, ולכן היא מבודדת ל-`node_is_block` — ראו את הריצה על
loop device אמיתי בדוח של האיסיו.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_agent import AGENT, BASH, posix, sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

#: תוכנית שחזור של שלוש מחיצות, בפורמט של manifest_plan (ממשק 1).
PLAN = [
    "1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|esp|vfat|2048|104857600"
    "|p1.esp.pcl.zst|aa|false|11111111-1111-1111-1111-111111111111|",
    "2|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|206848|53687091200"
    "|p2.windows.pcl.zst|bb|true|22222222-2222-2222-2222-222222222222|",
    "3|DE94BBA4-06D1-4D40-A16A-BFD50179D6AC|recovery|ntfs|105060352|838860800"
    "|p3.recovery.pcl.zst|cc|false|33333333-3333-3333-3333-333333333333|",
]

#: ‏jq מזויף. כל שאילתה שהסוכן באמת שולח מקבלת כאן תשובה מקובץ בקופסה,
#: כולל **הכשל** שבלב האיסיו: פלט חלקי ואז קוד יציאה 5, בדיוק כמו jq
#: שנפל על האלמנט הראשון שאינו ניתן לרינדור.
JQ_STUB = """#!/bin/sh
B="{box}"
case "$*" in
    *min_target_bytes*)       cat "$B/needs" ;;
    *"partitions | length"*)  cat "$B/count" ;;
    *".partitions[]"*)
        if [ -f "$B/plan_cut" ]; then
            head -n "$(cat "$B/plan_cut")" "$B/plan"
            echo "jq: error (at m.json:0): Cannot iterate over null" >&2
            exit 5
        fi
        cat "$B/plan"
        ;;
    *.sector_size*)  echo 512 ;;
    *.scheme*)       echo gpt ;;
    *.disk_guid*)    echo null ;;
    *)               echo null ;;
esac
"""

#: ‏sgdisk מזויף שמתעד כל קריאה, ונכשל על תבנית ארגומנטים שהבדיקה בחרה
#: (למשל `-e *` — הזזת עותק הגיבוי, לב בדיקה 2.5 של דף ההרצה).
SGDISK_STUB = """#!/bin/sh
B="{box}"
printf '%s\\n' "$*" >> "$B/sgdisk.calls"
if [ -f "$B/sgdisk.fail" ]; then
    while read -r pat; do
        # shellcheck disable=SC2254 -- התבנית מהקובץ היא glob בכוונה
        case "$*" in $pat) exit 2 ;; esac
    done < "$B/sgdisk.fail"
fi
exit 0
"""

BLOCKDEV_STUB = """#!/bin/sh
B="{box}"
case "$1" in
    --getsize64) cat "$B/disksize"; exit 0 ;;
    --rereadpt)
        printf '%s\\n' "$*" >> "$B/blockdev.calls"
        if [ -f "$B/rereadpt_fails" ]; then exit 1; fi
        exit 0
        ;;
esac
exit 0
"""


def build_box(tmp_path, *, plan=PLAN, count=None, plan_cut=None, nodes=None,
              sgdisk_fail=None, rereadpt_fails=False, settle=1):
    """קופסה עם הזיופים, ומחרוזת prelude שטוענת את הסוכן מולה.

    ‏`nodes` = אילו אינדקסים "חזרו" מהדיסק כהתקני בלוקים; ברירת המחדל
    היא כולם. ‏`count` = מה שהמניפסט מצהיר עליו (`.partitions | length`),
    ‏`plan_cut` = כמה שורות jq הספיק לפלוט לפני שמת.
    """
    box = tmp_path / "box"
    stubs = box / "stubs"
    stubs.mkdir(parents=True)
    run = box / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    (run / "targets" / "sda" / "base").write_text("0\n")
    (run / "targets" / "sda" / "bytes.raw").write_text("")

    (box / "plan").write_text("\n".join(plan) + "\n" if plan else "")
    (box / "count").write_text(f"{len(plan) if count is None else count}\n")
    (box / "needs").write_text("1048576\n")
    (box / "disksize").write_text("500000000000\n")
    if plan_cut is not None:
        (box / "plan_cut").write_text(f"{plan_cut}\n")
    if sgdisk_fail:
        (box / "sgdisk.fail").write_text("\n".join(sgdisk_fail) + "\n")
    if rereadpt_fails:
        (box / "rereadpt_fails").write_text("y\n")

    live = [f"{box}/dev/sda{i}" for i in
            (nodes if nodes is not None else [int(line[0]) for line in plan])]
    (box / "nodes").write_text("\n".join(live) + "\n" if live else "")

    # ‏chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה, וזיוף שלא ניתן להרצה
    # עובר בווינדוס (שם כל קובץ "בר-הרצה") ונופל ב-CI בלבד.
    written = ""
    for name, body in (("jq", JQ_STUB), ("sgdisk", SGDISK_STUB),
                       ("blockdev", BLOCKDEV_STUB)):
        written += (f"cat > {posix(stubs)}/{name} <<'STUB'\n"
                    + body.format(box=posix(box)) + "STUB\n"
                    + f"chmod 0755 {posix(stubs)}/{name}\n")

    prelude = (
        written
        + f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
        # ‏SYSROOT הוא שורש הקופסה: הסוכן מוסיף בעצמו `/sys/block/...`.
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(box)}/dev '
        f'SYSROOT={posix(box)} TABLE_SETTLE_S={settle} WAIT_POLL_S=1; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; '
        f'. {posix(AGENT)}/lib/jsonq.sh; . {posix(AGENT)}/lib/progress.sh; '
        f'. {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/expand.sh; '
        # הבדיקה היחידה שאי אפשר לזייף בלי root: התקן בלוקים אמיתי.
        # רשימת הצמתים ה"חיים" יושבת בקופסה, ולכן "הקרנל לא בנה את
        # /dev/sda3" הוא מצב שאפשר להעמיד בו את הקוד.
        f'node_is_block() {{ grep -qxF "$1" {posix(box)}/nodes 2>/dev/null; }}; '
    )
    return box, run, prelude


def rc_of(out: str) -> str:
    return out.strip().splitlines()[-1]


def log_of(run: Path) -> str:
    """‏common.sh קובע את LOG_FILE מתוך RUN_DIR — שם הלוג נמצא."""
    path = run / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def state_of(run: Path) -> str:
    return (run / "state").read_text().strip()


def target_error(run: Path) -> str:
    path = run / "targets" / "sda" / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- ‏A2: הטבלה נכתבה, או שהיא לא ------------------------------------------


def test_a_failing_sgdisk_e_no_longer_looks_like_a_written_table(tmp_path):
    """הכשל שנתן לאיסיו את שמו: ‏`apply_gpt` נגמרה ב-`sleep 1`, ולכן קוד
    היציאה שהחזירה היה של ה-sleep. ‏`sgdisk -e` הוא לב ההרחבה (בדיקה
    2.5): כשהוא נכשל, הטבלה על הכונן הגדול פשוט שגויה."""
    _box, run, prelude = build_box(tmp_path, sgdisk_fail=["-e *"])
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "could not move the GPT backup" in log_of(run)


def test_a_kernel_that_refused_the_table_stops_the_restore(tmp_path):
    """‏`blockdev --rereadpt` היה `|| true`, והוא הסימן היחיד שהקרנל קיבל
    את הטבלה ובנה את `/dev/sdaN`. בלעדיו partclone כותב לקובץ ב-RAM."""
    _box, run, prelude = build_box(tmp_path, rereadpt_fails=True)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "refused to re-read the partition table" in log_of(run)


def test_a_partition_that_never_came_back_is_named_and_counted(tmp_path):
    """הראיה החיובית: המחיצות **נספרות** אחרי הכתיבה. כאן הקרנל בנה שתיים
    מתוך שלוש — ‏sgdisk יצא 0 בכל הקריאות, ובכל זאת אין שחזור."""
    _box, run, prelude = build_box(tmp_path, nodes=[1, 2])
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    log = log_of(run)
    assert "2 of 3 partitions came back from the disk" in log
    assert "missing: 3" in log, "הלוג לא אומר איזו מחיצה חסרה"


def test_a_table_that_really_landed_passes_and_says_so(tmp_path):
    """הצד השני של אותה בדיקה — בלעדיו "הכל נחסם" היה עובר כהצלחה."""
    _box, run, prelude = build_box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    assert "all 3 partitions are live block devices" in log_of(run)


def test_the_expansion_reads_its_own_table_back_too(tmp_path):
    """ההרחבה בונה טבלה **שונה** מזו של apply_gpt, ולכן היא נקראת בחזרה
    שוב: מי שכתב אינו מעיד על עצמו. גם כאן ה-rereadpt היה `|| true`."""
    box, run, prelude = build_box(tmp_path, rereadpt_fails=True)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    out = sh(prelude + 'expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "refused to re-read the expanded table" in log_of(run)
    assert not (run / "targets" / "sda" / "expanded").exists(), \
        "סימון ההרחבה נכתב בלי שהטבלה אושרה"


def test_a_target_that_is_not_a_block_device_is_never_written_to(tmp_path):
    """הכשל בצורתו הישירה: ‏partclone על נתיב שאינו התקן בלוקים יוצר קובץ
    רגיל, כותב את המחיצה ל-RAM ויוצא 0. אין כאן מה לתקן אחר כך — היעד
    נכשל לפני שנפתח הצינור, ו-partclone לא רץ בכלל."""
    box, run, prelude = build_box(tmp_path, nodes=[1, 2])
    out = sh(prelude + "restore_partition unicast http://s img sda 3 ntfs "
             'p3.recovery.pcl.zst cc "" ; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "is not a block device" in log_of(run)
    assert not (box / "run" / "targets" / "sda" / "pipe.rc").exists()


# --- ‏A1: ‏done נגזר ממספר, לא מהיעדר כישלון ---------------------------------


def test_a_plan_that_jq_cut_in_half_is_refused(tmp_path):
    """‏jq פולט שורות ואז מת. בצינור של POSIX sh קוד היציאה הזה בלתי
    נראה, ולכן התוכנית מחומרנת לקובץ וה-rc נבדק. תוכנית חלקית אינה
    תוכנית: אין פלט, ויש כישלון."""
    _box, run, prelude = build_box(tmp_path, plan_cut=2)
    out = sh(prelude + 'manifest_plan m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "p1.esp.pcl.zst" not in out, "תוכנית חלקית דלפה לקורא"
    assert "plan: jq failed" in log_of(run)


def test_a_plan_shorter_than_the_manifest_declares_is_refused(tmp_path):
    """הצד השני: ‏jq יצא 0 ובכל זאת חסרה שורה (‏tmpfs שנגמר, פילטר
    שדילג). המספר במניפסט הוא הראיה שמולה נספרות השורות."""
    _box, run, prelude = build_box(tmp_path, plan=PLAN[:2], count=3)
    out = sh(prelude + 'manifest_plan m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    assert "plan: 2 of 3 partitions rendered" in log_of(run)


def test_a_whole_plan_is_read_exactly_as_before(tmp_path):
    """בקרה חיובית: מניפסט תקין מרונדר במלואו, שורה למחיצה, ובאותו סדר."""
    _box, _run, prelude = build_box(tmp_path)
    out = sh(prelude + 'manifest_plan m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    assert out.strip().splitlines()[:3] == PLAN


def restore_run(prelude, extra=""):
    """מריץ את run_restore המלא עם restore_partition מוחלף במונה: מה
    שנבדק כאן הוא ההכרעה על `done`, לא הצינור עצמו."""
    return (prelude
            + 'restore_partition() { echo "$5" >> "$RUN_DIR/wrote"; '
            + (extra or "return 0")
            + "; }; "
            + 'run_restore multicast sda http://s img m.json; echo "rc=$?"')


def wrote(run: Path) -> list[str]:
    path = run / "wrote"
    return path.read_text().split() if path.exists() else []


def test_a_partial_plan_can_no_longer_end_as_done(tmp_path):
    """הכשל של ‏A1 מקצה לקצה: תוכנית של שתי מחיצות מתוך שלוש. עד כאן
    הלולאה הייתה נגמרת בשלום והמכונה הייתה מכריזה `done` — עם מחיצה
    שלישית שאיש לא כתב. עכשיו היא נעצרת לפני הבייט הראשון."""
    _box, run, prelude = build_box(tmp_path, plan_cut=2)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert wrote(run) == [], "מחיצה נכתבה על סמך תוכנית חלקית"
    assert "plan: jq failed" in log_of(run)


def test_a_table_the_kernel_never_took_stops_it_before_the_first_byte(tmp_path):
    """‏A2 מקצה לקצה: ‏rereadpt נכשל, ולכן `/dev/sdaN` אינם קיימים. עד כאן
    ‏apply_gpt הייתה מחזירה 0, הצינור היה נפתח על קובץ ב-devtmpfs,
    ה-sha256 היה עובר והמכונה הייתה מגיעה ל-`done`."""
    _box, run, prelude = build_box(tmp_path, rereadpt_fails=True)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert target_error(run) == "could not write the partition table"
    assert wrote(run) == []


def test_done_is_decided_by_the_count_and_not_by_the_absence_of_a_failure(tmp_path):
    """מחיצה שלישית נכשלת: המונה הוא שמכריע, וההודעה אומרת כמה מתוך כמה
    — ולא "restore failed", שאינו אומר לטכנאי איפה זה נעצר."""
    _box, run, prelude = build_box(tmp_path)
    out = sh(restore_run(prelude, 'test "$5" != 3'))
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert target_error(run) == "wrote 2 of 3 partitions"
    assert wrote(run) == ["1", "2", "3"]


def test_a_restore_that_wrote_everything_still_reaches_done(tmp_path):
    """בקרה חיובית לכל השרשרת: טבלה שנקראה בחזרה, תוכנית שלמה, שלוש
    מחיצות שנכתבו — ורק אז `done`."""
    _box, run, prelude = build_box(tmp_path)
    out = sh(restore_run(prelude))
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "done"
    assert (run / "targets" / "sda" / "state").read_text().strip() == "done"
    assert wrote(run) == ["1", "2", "3"]


def test_the_settle_window_is_bounded_and_not_a_bare_sleep(tmp_path):
    """המתנה לצמתים היא בדיקה חוזרת עם תקרה, לא `sleep` שמקווה לטוב:
    היא נגמרת בכישלון מפורש בזמן סביר גם כשהם לעולם לא מופיעים."""
    _box, run, prelude = build_box(tmp_path, nodes=[], settle=2)
    out = sh(prelude + 's=$(date +%s); verify_table sda m.json; rc=$?; '
             'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"')
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1", out
    assert int(fields["elapsed"]) <= 10, "הבדיקה לא נגמרת בזמן סביר"
    assert "0 of 3 partitions came back" in log_of(run)
