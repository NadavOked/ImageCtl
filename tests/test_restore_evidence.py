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
    *.disk_guid*)    cat "$B/disk_guid" ;;
    *)               echo null ;;
esac
"""

#: ‏sgdisk מזויף שמתעד כל קריאה, ונכשל על תבנית ארגומנטים שהבדיקה בחרה
#: (למשל `-e *` — הזזת עותק הגיבוי, לב בדיקה 2.5 של דף ההרצה).
#:
#: ‏`-U` יוצא 0 כברירת מחדל — וזה **אינו** ראיה שה-GUID נכתב (#572).
#: ‏`-p` מדווח מה שעל הדיסק: מה ש-`-U` ביקשה, או את השקר ב-`disk_guid.lie`
#: כשרוצים את המצב שבו קוד היציאה 0 והערך לא נתפס.
#:
#: ‏#1212: אותו דבר ל-GUID הייחודי של כל מחיצה. ‏`-u N:G` נרשם ב-`uguid.N`,
#: ‏`-d N` ו-`--zap-all` מוחקים אותו, ו-`-i N` מדווח מה שעל הדיסק: השקר
#: ב-`uguid.lie.N` אם יש, אחרת מה ש-`-u` ביקש, ואחרת GUID ש-sgdisk "המציא"
#: — בדיוק מה שקורה במחיצה שנבראה בלי `-u`.
SGDISK_STUB = """#!/bin/sh
B="{box}"
printf '%s\\n' "$*" >> "$B/sgdisk.calls"
if [ -f "$B/sgdisk.fail" ]; then
    while read -r pat; do
        # shellcheck disable=SC2254 -- התבנית מהקובץ היא glob בכוונה
        case "$*" in $pat) exit 2 ;; esac
    done < "$B/sgdisk.fail"
fi
prev=""
for a in "$@"; do
    case "$prev" in
        -d) rm -f "$B/uguid.$a" ;;
        -u) printf '%s\\n' "${{a#*:}}" > "$B/uguid.${{a%%:*}}" ;;
    esac
    [ "$a" = "--zap-all" ] && rm -f "$B"/uguid.[0-9]*
    prev="$a"
done
case "$1" in
    -i)
        if [ -f "$B/uguid.lie.$2" ]; then
            echo "Partition unique GUID: $(cat "$B/uguid.lie.$2")"
        elif [ -f "$B/uguid.$2" ]; then
            echo "Partition unique GUID: $(cat "$B/uguid.$2")"
        else
            echo "Partition unique GUID: 0BADC0DE-0000-4000-8000-000000000000"
        fi
        ;;
    -U)
        printf '%s\\n' "$2" > "$B/disk_guid.requested"
        ;;
    -p)
        if [ -f "$B/disk_guid.lie" ]; then
            echo "Disk identifier (GUID): $(cat "$B/disk_guid.lie")"
        elif [ -f "$B/disk_guid.requested" ]; then
            echo "Disk identifier (GUID): $(cat "$B/disk_guid.requested")"
        fi
        ;;
esac
exit 0
"""

BLOCKDEV_STUB = """#!/bin/sh
B="{box}"
case "$1" in
    --getsize64) cat "$B/disksize"; exit 0 ;;
    --getss)
        printf '%s\\n' "$*" >> "$B/blockdev.calls"
        if [ -f "$B/getss" ]; then cat "$B/getss"; exit 0; fi
        echo "blockdev: cannot open $2" >&2
        exit 1
        ;;
    --rereadpt)
        printf '%s\\n' "$*" >> "$B/blockdev.calls"
        if [ -f "$B/rereadpt_fails" ]; then exit 1; fi
        exit 0
        ;;
esac
exit 0
"""


def build_box(tmp_path, *, plan=PLAN, count=None, plan_cut=None, nodes=None,
              sgdisk_fail=None, rereadpt_fails=False, settle=1, disk_guid=None,
              guid_lie=None, target_ss="512"):
    """קופסה עם הזיופים, ומחרוזת prelude שטוענת את הסוכן מולה.

    ‏`nodes` = אילו אינדקסים "חזרו" מהדיסק כהתקני בלוקים; ברירת המחדל
    היא כולם. ‏`count` = מה שהמניפסט מצהיר עליו (`.partitions | length`),
    ‏`plan_cut` = כמה שורות jq הספיק לפלוט לפני שמת.
    ‏`disk_guid` = מה ש-jq מחזיר ל-`.disk_guid`; ברירת המחדל `null`,
    ואז `apply_gpt` מדלגת על `sgdisk -U` לגמרי.
    ‏`guid_lie` = מה ש-`sgdisk -p` מדווח אחרי `-U` שיצא 0. כשהוא שונה
    מ-`disk_guid`, קוד היציאה אינו ראיה שה-GUID נכתב (#572).
    ‏`target_ss` = מה ש-`blockdev --getss` מדווח על היעד (#958); ‏`None` =
    ‏blockdev שנכשל, כלומר גודל סקטור שלא ניתן לקרוא.
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
    if target_ss is not None:
        (box / "getss").write_text(f"{target_ss}\n", newline="\n")
    (box / "disk_guid").write_text(
        ("null" if disk_guid is None else disk_guid) + "\n", newline="\n")
    if plan_cut is not None:
        (box / "plan_cut").write_text(f"{plan_cut}\n")
    if sgdisk_fail:
        (box / "sgdisk.fail").write_text("\n".join(sgdisk_fail) + "\n", newline="\n")
    if rereadpt_fails:
        (box / "rereadpt_fails").write_text("y\n", newline="\n")
    if guid_lie is not None:
        (box / "disk_guid.lie").write_text(guid_lie + "\n", newline="\n")

    # ‏posix(box) ולא box: ‏DEVROOT למטה עובר דרך posix(), וכאן הנתיב
    # נכתב כמו שהוא. בווינדוס זה \\ מול /, ולכן node_is_block החזיר שקר **תמיד** —
    # וחמישה טסטים נפלו על סיבה שאינה מה שהם בודקים.
    live = [f"{posix(box)}/dev/sda{i}" for i in
            (nodes if nodes is not None else [int(line[0]) for line in plan])]
    (box / "nodes").write_text("\n".join(live) + "\n" if live else "", newline="\n")

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
        f'. {posix(AGENT)}/lib/restore.sh; . {posix(AGENT)}/lib/expand.sh; . {posix(AGENT)}/lib/grow.sh; . {posix(AGENT)}/lib/failmark.sh; '
        # ‏#1212: ‏verify_table קורא בחזרה את GUID המחיצות דרך manifest.sh.
        f'. {posix(AGENT)}/lib/manifest.sh; '
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


def test_apply_gpt_restores_partition_attribute_bits(tmp_path):
    """#1130 negative control: -n/-t/-u recreated partitions with attrs=0."""
    attrs = "8000000000000001"
    plan = [line + f"|{attrs}" for line in PLAN]  # PLAN ends with the empty uuid field.
    box, _run, prelude = build_box(tmp_path, plan=plan)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    calls = (box / "sgdisk.calls").read_text().splitlines()
    creates = [line for line in calls if line.startswith("-n ")]
    assert len(creates) == len(plan)
    for index, call in enumerate(creates, 1):
        assert f"-A {index}:=:0x{attrs}" in call, call


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
    # ‏#845: אחרי הסיבה מצטרפת תוצאת הסימון על הדיסק, ולכן startswith.
    assert target_error(run).startswith("wrote 2 of 3 partitions"), target_error(run)
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


#: GUID דיסק אמיתי מהמעבדה (מ.17, 06/09) — הצורה, לא הזהות, היא מה שנבדק.
DISK_GUID = "047B3400-0000-0000-0000-0000003AEE00"
#: GUID אחר לגמרי — מה שהדיסק מדווח כש-`-U` יצא 0 והערך לא נתפס (#572).
LIE_GUID = "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF"


def _sgdisk_called_with_U(box) -> str:
    path = box / "sgdisk.calls"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def test_a_failing_disk_guid_no_longer_looks_like_a_written_table(tmp_path):
    """#537: ‏`sgdisk -U` היה היחיד ב-`apply_gpt` שלא הסתיים ב-`|| return 1`.
    כשהוא נכשל נרשמה WARNING והפונקציה החזירה 0 — טבלה בלי GUID הדיסק
    נראתה כמו טבלה שנכתבה."""
    box, run, prelude = build_box(
        tmp_path, disk_guid=DISK_GUID, sgdisk_fail=["-U *"])
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert f"-U {DISK_GUID}" in _sgdisk_called_with_U(box), (
        "‏-U לא רץ — הטסט לא בודק את מסלול ה-GUID")
    assert rc_of(out) == "rc=1", out
    assert "could not set the disk GUID" in log_of(run)


def test_a_disk_guid_that_could_not_be_set_does_not_reach_done(tmp_path):
    """#537 מקצה לקצה. Windows קושר את ה-BCD ל-GUID של הדיסק; שחזור
    עם GUID אחר הוא ‏`winload.efi 0xc000000e` — בדיוק #26. עד כאן
    ‏`apply_gpt` החזירה 0, הזרם נכתב, והמגירה הגיעה ל-`done`.

    הכישלון שנבדק הוא ההתנהגות — rc של השחזור ו-`done` — לא ייבוא
    ולא קובץ חסר. ‏`-U` חייב להופיע בקריאות, אחרת זה דילוג ולא כשל."""
    box, run, prelude = build_box(
        tmp_path, disk_guid=DISK_GUID, sgdisk_fail=["-U *"])
    out = sh(restore_run(prelude))
    assert f"-U {DISK_GUID}" in _sgdisk_called_with_U(box), (
        "‏-U לא רץ — הטסט לא בודק את מסלול ה-GUID")
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert (run / "targets" / "sda" / "state").read_text().strip() == "failed"
    assert wrote(run) == [], "הזרם רץ אחרי GUID שלא נקבע"
    assert "could not set the disk GUID" in log_of(run)


def test_a_disk_guid_that_sgdisk_accepted_still_reaches_done(tmp_path):
    """הצד השני: GUID במניפסט ש-`sgdisk -U` קיבלה, **ושנקרא בחזרה
    מהדיסק**, אינו חוסם שחזור. בלי זה 'הכול נחסם' היה עובר כהצלחה."""
    box, run, prelude = build_box(tmp_path, disk_guid=DISK_GUID)
    out = sh(restore_run(prelude))
    assert f"-U {DISK_GUID}" in _sgdisk_called_with_U(box), (
        "‏-U לא רץ — אין ראיה שהמסלול בכלל נלקח")
    assert rc_of(out) == "rc=0", out
    assert state_of(run) == "done"
    assert (run / "targets" / "sda" / "state").read_text().strip() == "done"
    assert wrote(run) == ["1", "2", "3"]
    assert f"disk GUID {DISK_GUID} came back from the disk" in log_of(run), (
        "ה-GUID נכתב — אבל לא נקרא בחזרה מהדיסק")


def test_a_zero_from_sgdisk_U_is_not_proof_the_guid_landed(tmp_path):
    """#572: ‏`sgdisk -U` יוצא 0, וה-GUID על הדיסק אינו מה שהוצהר.
    קוד היציאה אינו ראיה — אותה משפחה כמו `ethtool -s` ב-R17.
    ‏`-U` חייב להופיע בקריאות, אחרת זה דילוג ולא כשל (השומר של #537)."""
    box, run, prelude = build_box(
        tmp_path, disk_guid=DISK_GUID, guid_lie=LIE_GUID)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')
    assert f"-U {DISK_GUID}" in _sgdisk_called_with_U(box), (
        "‏-U לא רץ — הטסט לא בודק את מסלול ה-GUID")
    assert rc_of(out) == "rc=1", out
    log = log_of(run)
    assert f"disk GUID on the disk is {LIE_GUID}, not {DISK_GUID}" in log
    assert "could not set the disk GUID" not in log, (
        "זה אינו כשל של קוד היציאה — -U יצא 0")


def test_a_guid_that_did_not_land_does_not_reach_done(tmp_path):
    """#572 מקצה לקצה. ‏`-U` יצא 0, ה-GUID על הדיסק אחר, והשחזור
    עדיין הגיע ל-`done` — ‏winload.efi 0xc000000e על כל כיתה (#26).

    הכישלון שנבדק הוא ההתנהגות — rc של השחזור ו-`done` — לא ייבוא.
    ‏`-U` חייב להופיע בקריאות, אחרת זה דילוג ולא כשל."""
    box, run, prelude = build_box(
        tmp_path, disk_guid=DISK_GUID, guid_lie=LIE_GUID)
    out = sh(restore_run(prelude))
    assert f"-U {DISK_GUID}" in _sgdisk_called_with_U(box), (
        "‏-U לא רץ — הטסט לא בודק את מסלול ה-GUID")
    assert rc_of(out) == "rc=1", out
    assert state_of(run) == "failed"
    assert (run / "targets" / "sda" / "state").read_text().strip() == "failed"
    assert wrote(run) == [], "הזרם רץ אחרי GUID שלא נכתב"
    assert f"disk GUID on the disk is {LIE_GUID}, not {DISK_GUID}" in log_of(run)


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


# --- ‏#1171: "קטן מדי" אומר בכמה ---------------------------------------------


def test_a_disk_too_small_says_by_how_much(tmp_path):
    """‏256GB של יצרן אחד אינו 256GB של אחר: הפסילה נשארת (עיקרון 4), אבל
    ההודעה אומרת את ההפרש בפועל — 1.7MB — ולא רק שני מספרים של 12 ספרות."""
    box, run, prelude = build_box(tmp_path)
    (box / "needs").write_text("256062259200\n")
    (box / "disksize").write_text("256060514304\n")
    out = sh(prelude + 'disk_fits sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=1", out
    err = target_error(run)
    assert err.startswith("disk too small by 2 MiB"), err
    assert "1744896 bytes short" in err and "needs 256062259200" in err, err
    assert state_of(run / "targets" / "sda") == "failed"


def test_a_disk_that_fits_passes_disk_fits(tmp_path):
    box, run, prelude = build_box(tmp_path)
    (box / "needs").write_text("256060514304\n")
    (box / "disksize").write_text("256060514304\n")
    out = sh(prelude + 'disk_fits sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    assert target_error(run) == ""
