"""מסך מחשב השיכפול (clonergui.sh) — הצד של הסוכן, בהזרקת מעטפת.

מחשב השיכפול קיבל מסך (הכרעת הבעלים): הוא מציג התקדמות פר-מגירה ותפריט
SMART גרפי, ומחליף את התפריט הטקסטואלי של #666 כשהמסך חי. שתי הבדיקות
המרכזיות כאן הן בקרות שליליות מובנות:

* ``cloner_gui_state`` בונה את קובץ המצב **מהאמת המקומית** — קובצי
  ``$RUN_DIR/targets/<dev>``, מסודרים לפי port בצד ה-C — ולא מהשרת.
* ``gui_smart_choice`` **חוסם עד שמגיעה החלטה תקינה**: החלטה לא-חוקית
  נדחית והמתנה נמשכת; רק ``replace``/``rescue``/``skip`` משחררים. תקלת
  מסך אינה אישור כתיבה (עיקרון 5) — אם ה-GUI נעלם, היא נכשלת ולא
  מוותרת ל-``rescue``.

הצד הגרפי (C) נבנה ונבדק על המעבדה: ``requires_native`` מדלג כאן כשאין
מהדר או ספריות cairo/pango, וה-``--png`` מריץ את בדיקת הניתוב המובנית
של ``render_png`` (ה-``want[]`` שכולל עכשיו ``SCREEN_CLONER``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
GUI = REPO / "native-gui"


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


def sh(script: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


PRELUDE = (
    f'. {posix(AGENT)}/lib/common.sh; '
    f'. {posix(AGENT)}/lib/jsonq.sh; '
    f'. {posix(AGENT)}/lib/sysinfo.sh; '
    f'. {posix(AGENT)}/lib/progress.sh; '
    f'. {posix(AGENT)}/lib/clonergui.sh; '
)


def env(run: Path, gui: Path) -> str:
    return (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
            f'IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff; ')


def target(run: Path, dev: str, state: str, total: int, base: int, cur: str) -> None:
    """יעד פר-מגירה, בדיוק במבנה של target_init/progress.sh."""
    d = run / "targets" / dev
    d.mkdir(parents=True, exist_ok=True)
    (d / "state").write_text(state + "\n", newline="\n")
    (d / "total").write_text(f"{total}\n", newline="\n")
    (d / "base").write_text(f"{base}\n", newline="\n")
    (d / "bytes.raw").write_text(cur, newline="\n")
    (d / "counter").write_text(posix(d / "bytes.raw") + "\n", newline="\n")


# disk_port מוזרק: מקורו נבדק ב-test_agent/test_smart; כאן נבדק המבנה.
PORTS = 'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; sdc) echo 3;; *) ;; esac; }; '


# --- cloner_gui_state: קובץ המצב מהאמת המקומית --------------------------------


def state_lines(tmp_path: Path, smart_request: str | None = None) -> list[str]:
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    target(run, "sda", "writing", 100, 40, "5")     # base 40 + cur 5 = 45
    target(run, "sdb", "waiting", 100, 0, "")
    if smart_request is not None:
        (gui / "smart-request").write_text(smart_request + "\n", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text().splitlines()


@requires_native("jq", why="json_get קורא ל-jq לשם האימג' מהמניפסט")
def test_state_carries_the_image_name(tmp_path):
    assert "cloner=Office 2024" in state_lines(tmp_path)


def test_a_drawer_record_reports_the_local_bytes_and_port(tmp_path):
    """התקדמות פר-מגירה נקראת מ-target_bytes (base+counter), לא מהשרת.

    זו הבקרה השלילית: אילו הרשומה נבנתה מהמונה המשותף של המולטיקאסט
    ולא פר-מגירה, שדה הבייטים היה שווה לכל המגירות (#25). כאן sda הוא
    45 (40+5) ו-sdb הוא 0 — שני מספרים שונים, מהקבצים המקומיים."""
    lines = state_lines(tmp_path)
    # ‏#410: שדות 7–8 הם קצב ו-ETA; ‏-1 = לא נמדד (דגימה ראשונה / לא כותבת).
    assert "drawer=1|sda|writing|45|100||-1|-1" in lines
    assert "drawer=2|sdb|waiting|0|100||-1|-1" in lines


def test_a_second_sample_carries_the_rate_and_eta_from_the_local_counter(tmp_path):
    """‏#410: הקצב הוא ממוצע מאז הדגימה הראשונה עם בייטים על המגירה
    (‏`$GUI_DIR/pace.<dev>` = "epoch bytes"). העוגן מוזז 10 שניות אחורה
    כי שתי הריצות קורות באותה שנייה — ואפס שניות הוא בצדק "לא נמדד"."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    target(run, "sda", "writing", 1000, 0, "50")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    assert "drawer=1|sda|writing|50|1000||-1|-1" in (gui / "state").read_text().splitlines()
    t0, b0 = (gui / "pace.sda").read_text().split()
    assert b0 == "50"
    (gui / "pace.sda").write_text(f"{int(t0) - 10} 50\n", newline="\n")
    target(run, "sda", "writing", 1000, 0, "250")           # +200 בייטים ב-10 שניות
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    line = [l for l in (gui / "state").read_text().splitlines() if l.startswith("drawer=")][0]
    port, dev, state, bytes_, total, err, rate, eta = line[len("drawer="):].split("|")
    assert bytes_ == "250"
    assert 15 <= int(rate) <= 20, rate                      # 200 / (10s + הזמן בין הריצות)
    assert eta == str((1000 - 250) // int(rate))
    assert any(l.startswith("updated=") for l in (gui / "state").read_text().splitlines())


def test_a_drawer_that_stopped_writing_drops_its_anchor_and_reports_unmeasured(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (run / "manifest.json").write_text('{"name":"Office 2024"}', newline="\n")
    (gui / "pace.sda").write_text("1 1\n", newline="\n")
    target(run, "sda", "done", 1000, 1000, "")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    assert "drawer=1|sda|done|1000|1000||-1|-1" in (gui / "state").read_text().splitlines()
    assert not (gui / "pace.sda").exists()


def test_a_pending_smart_request_becomes_a_prompt_record(tmp_path):
    """כשהסוכן כתב smart-request, המצב נושא smart_prompt — וזה מה
    שמצייר את הפאנל הדומיננטי בצד ה-C. ה-request הוא nonce|port|verdict|
    reason, וה-nonce **זורם ל-GUI** (הוא נצייר עם ה-prompt וחוזר בקליק,
    כדי שהגשר יכבול אותו לבקשה המדויקת)."""
    lines = state_lines(tmp_path, smart_request="12-2|2|fail|pending")
    assert "smart_prompt=12-2|2|fail|pending" in lines


def test_no_smart_request_means_no_prompt(tmp_path):
    """הצד החיובי: בלי בקשה אין פאנל, אחרת כל דיסק היה נראה פגום."""
    assert not any(l.startswith("smart_prompt=") for l in state_lines(tmp_path))


def test_an_error_pipe_cannot_corrupt_the_record(tmp_path):
    """‏`|` או שורה חדשה בתוך הודעת השגיאה היו שוברים את פורמט הרשומה —
    הם מקופלים לרווחים לפני שהערך נכתב."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    target(run, "sda", "failed", 100, 0, "")
    (run / "targets" / "sda" / "error").write_text("bad | disk\nline2", newline="\n")
    out = sh(env(run, gui) + PRELUDE + PORTS + "cloner_gui_state")
    assert out.returncode == 0, out.stderr
    drawer = [l for l in (gui / "state").read_text().splitlines() if l.startswith("drawer=")][0]
    assert drawer.count("|") == 7, drawer          # בדיוק שמונה שדות (#410: +קצב, +ETA)
    assert "bad   disk line2" in drawer


def test_the_kiosk_load_chain_carries_the_cached_smart_verdict(tmp_path):
    """‏#834: הקיוסק הוא תהליך נפרד עם שרשרת טעינה משלו (‏`gui_libs`), ועל
    הברזל היא לא כללה את ‏`smart.sh` — ‏`cloner_gui_state` הדפיס
    ‏`smart_hello_field: not found` פעם בשנייה, ושדה ה-SMART של כל ‏cdisk
    הפך ל-`unchecked` גם כשהמטמון אמר ‏`ok`. "לא הצלחנו לבדוק" הוצג כ"לא
    נבדק" (עיקרון 5).

    כאן רצה **שרשרת הקיוסק עצמה** (‏`gui_libs`, לא PRELUDE של הטסט) על
    משכפל ממתין — בלי יעדים, עם דיסק אחד ב-sysfs מזויף ומטמון SMART
    ‏`ok`. הראיה החיובית: ‏`cdisk=...|passed` — הערך שנקרא מהמטמון, לא
    ברירת המחדל. בקרה שלילית: הסרת ‏`. smart.sh` מ-`gui_libs` מחזירה את
    ‏`not found` ל-stderr ואת ‏`unchecked` לרשומה — שתי הבדיקות נופלות."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    sysroot = tmp_path / "sysroot"
    (sysroot / "sys" / "block" / "sda" / "device").mkdir(parents=True)
    (sysroot / "sys" / "block" / "sda" / "size").write_text("1000\n", newline="\n")
    (sysroot / "sys" / "block" / "sda" / "device" / "model").write_text("FakeDisk\n", newline="\n")
    (run / "smart").mkdir()
    (run / "smart" / "sda.v").write_text("ok ok 0 0 0 0\n", newline="\n")
    out = sh(env(run, gui) + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(AGENT / "lib")!r}; '
             f'. {posix(AGENT)}/lib/guibridge.sh; gui_libs; cloner_gui_state')
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    lines = (gui / "state").read_text().splitlines()
    # ‏#874: השדה השישי (הסיבה מזיכרון השרת) ריק על דיסק ירוק.
    assert any(l.startswith("cdisk=") and l.endswith("|passed|") for l in lines), lines


# --- ‏#867/#874: המלאי הממתין — ממוין לפי מספר הדיסק, ונושא את זיכרון השרת ----

#: ‏sgdisk מזויף למסך הממתין: רושם בלבד. עד #874 המסך קרא ממנו את הסימון
#: של #845 (‏`sgdisk -p`); מאז הזיכרון בשרת, ו-`sgdisk.calls` נשאר ריק.
IDLE_SGDISK = """#!/bin/sh
printf '%s\\n' "$*" >> "{box}/sgdisk.calls"
exit 0
"""


def idle_state(tmp_path: Path, ports: dict[str, str], failed: dict[str, str] | None = None,
               runs: int = 1) -> list[str]:
    """משכפל ממתין (בלי יעדים) עם הדיסקים ב-`ports` (dev → מספר דיסק, ריק =
    אין חריץ) ב-sysfs מזויף, ‏SMART ‏`ok` במטמון לכולם, וזיכרון השרת (#874)
    כפי ש-jq היה מדפיס אותו מתשובת ה-hello: ‏`failed` = dev → cause, לפי
    סידורי (‏S-<dev>). רץ בשרשרת הקיוסק עצמה (‏`gui_libs`), ‏`runs` פעמים."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    box = tmp_path / "box"; box.mkdir()
    sysroot = tmp_path / "sysroot"
    (run / "smart").mkdir()
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    for dev in ports:
        (sysroot / "sys" / "block" / dev / "device").mkdir(parents=True)
        (sysroot / "sys" / "block" / dev / "size").write_text("1000\n", newline="\n")
        (sysroot / "sys" / "block" / dev / "device" / "model").write_text(f"Model {dev}\n", newline="\n")
        (run / "smart" / f"{dev}.v").write_text("ok ok 0 0 0 0\n", newline="\n")
    memory = " ".join(f"echo 'S-{dev}||{cause}';" for dev, cause in (failed or {}).items())
    cases = " ".join(f"{d}) echo {p};;" for d, p in ports.items() if p)
    port_fn = f'disk_port() {{ case "$1" in {cases} *) ;; esac; }}; '
    script = (
        f"mkdir -p {posix(box)}/bin\n"
        f"cat > {posix(box)}/bin/sgdisk <<'STUB'\n{IDLE_SGDISK.format(box=posix(box))}STUB\n"
        f"chmod 0755 {posix(box)}/bin/sgdisk\n"
        f'export PATH="$(cd {posix(box)}/bin && pwd):$PATH"\n'   # ‏C: בתוך PATH נשבר ב-MSYS
        + env(run, gui)
        + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(AGENT / "lib")!r}; '
        f'. {posix(AGENT)}/lib/guibridge.sh; gui_libs; ' + port_fn
        + 'disk_serial() { printf "S-%s" "$1"; }; '
        + 'json_get_join() { [ -s "$1" ] || return 1; echo ok; ' + memory + ' }; '
        + " && ".join(["cloner_gui_state"] * runs)
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr
    assert not (box / "sgdisk.calls").exists(), "המסך הממתין נגע ב-sgdisk (#845 הוסר)"
    return [l for l in (gui / "state").read_text().splitlines() if l.startswith("cdisk=")]


def test_idle_disks_are_sorted_by_disk_number_not_device_name(tmp_path):
    """‏#867: במחשב 2 המיפוי הוא sda→3, sdb→1, sdc→2, והמסך הציג "דיסק 3,
    1, 2". נדב מדבר ב"דיסק 1/2/3 לפי SATA" — הרשומות ממוינות לפי השדה
    הראשון, לא לפי סדר הגילוי של `list_disks`. בקרה שלילית: על main הסדר
    נשאר 3,1,2."""
    lines = idle_state(tmp_path, {"sda": "3", "sdb": "1", "sdc": "2"})
    assert [l.split("|")[0] for l in lines] == ["cdisk=1", "cdisk=2", "cdisk=3"], lines
    assert [l.split("|")[1] for l in lines] == ["sdb", "sdc", "sda"], lines


def test_an_idle_disk_without_a_slot_sorts_last_and_keeps_the_others_ordered(tmp_path):
    """דיסק ש-`disk_port` לא גזר לו חריץ (USB, בקר זר) אינו שובר את
    המיון: הוא מגיע **אחרי** הממוספרים, והם עדיין 1,2."""
    lines = idle_state(tmp_path, {"sda": "2", "sdb": "", "sdc": "1"})
    assert [l.split("|")[1] for l in lines] == ["sdc", "sda", "sdb"], lines
    assert lines[-1].startswith("cdisk=0|sdb|"), lines


def test_a_disk_the_server_remembers_reports_failed_last_with_its_cause(tmp_path):
    """‏#867/#874: דיסק 1 במחשב 2 נכשל בלילה (כבל, לפי שורות ה-ATA), והוא
    עובר SMART (‏`ok` במטמון). המסך הציג אותו ירוק. שדה ה-smart של הרשומה
    חייב להיות `failed_last` והשדה השישי הסיבה שהשרת סיווג; הדיסק הנקי
    לצדו נשאר `passed` בלי סיבה. ‏sgdisk אינו נקרא. בקרה שלילית: על main
    שניהם `passed` (הזיוף לא מדפיס טבלה), ו-`sgdisk -p` נקרא."""
    lines = idle_state(tmp_path, {"sda": "1", "sdb": "2"}, failed={"sda": "cable"})
    assert lines[0] == "cdisk=1|sda|Model sda|512000|failed_last|cable", lines
    assert lines[1] == "cdisk=2|sdb|Model sdb|512000|passed|", lines


def test_the_memory_is_read_from_the_last_hello_every_second(tmp_path):
    """‏cloner_gui_state רץ פעם בשנייה וקורא את זיכרון השרת מתשובת ה-hello
    האחרונה — "נקה" בקונסולה מגיע למסך בלי ריבוט. שתי ריצות: האדום נשאר
    אדום כל עוד הרשומה בתשובה."""
    lines = idle_state(tmp_path, {"sda": "1", "sdb": "2"}, failed={"sdb": "disk"}, runs=2)
    assert lines[1].endswith("|failed_last|disk"), lines


# --- gui_smart_choice: חוסם עד החלטה תקינה, ולעולם לא מוותר לכתיבה ------------


def smart_choice_env(tmp_path: Path) -> tuple[str, Path]:
    gui = tmp_path / "gui"; gui.mkdir()
    run = tmp_path / "run"; run.mkdir()
    return env(run, gui) + PRELUDE + PORTS + 'D_ROLE=cloner; ', gui


def test_gui_smart_choice_returns_a_valid_decision(tmp_path):
    """המסלול המלא: הסוכן כותב smart-request עם nonce, ממתין, והמסך
    (מדומה כאן ע"י קריאת ה-nonce וכתיבת nonce|decision, בדיוק כמו הגשר)
    משחרר עם החלטה תקינה."""
    setup, gui = smart_choice_env(tmp_path)
    script = f"""{setup}
sleep 20 & _gui_pid=$!
( gui_smart_choice sda fail pending > {posix(gui)}/out; echo "rc=$?" > {posix(gui)}/rc ) &
CH=$!
# ממתינים שהבקשה תיכתב, קוראים את ה-nonce, ומשחררים בהחלטה כבולה אליו
for _ in 1 2 3 4 5; do [ -f {posix(gui)}/smart-request ] && break; sleep 0.3; done
N=$(IFS='|' read -r n _ < {posix(gui)}/smart-request; echo "$n")
printf '%s|replace\\n' "$N" > {posix(gui)}/smart-decision
wait $CH
kill $_gui_pid 2>/dev/null
cat {posix(gui)}/out {posix(gui)}/rc
[ -f {posix(gui)}/smart-request ] && echo REQUEST-LEFT
[ -f {posix(gui)}/smart-decision ] && echo DECISION-LEFT
true
"""
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "replace" in out.stdout
    assert "rc=0" in out.stdout
    # אחרי הכרעה, שני הקבצים נמחקים — קליק ישן לא יאשר את הדיסק הבא.
    assert "REQUEST-LEFT" not in out.stdout
    assert "DECISION-LEFT" not in out.stdout


def test_gui_smart_choice_blocks_until_a_valid_decision(tmp_path):
    """‏**הבקרה השלילית המרכזית (HIGH + #666).** ‏gui_smart_choice
    משתחרר **רק** על החלטה שכבולה ל-nonce של הבקשה **וגם** חוקית. כאן
    נבדקים שני מסלולי הדחייה, שכל אחד מהם לבדו הוא באג בטיחות:

    1. **nonce לא-תואם** — החלטה של בקשה אחרת (קליק מושהה שהוזרק אחרי
       שהדיסק התחלף). אילו התקבלה, ההחלטה של דיסק אחד הייתה מאושרת
       לדיסק אחר — במקרה הגרוע `rescue` על הדיסק החשוד הלא-נכון.
    2. **ערך לא-חוקי** — "garbage" עם ה-nonce הנכון.

    רק החלטה תקינה עם ה-nonce הנכון משחררת."""
    setup, gui = smart_choice_env(tmp_path)
    script = f"""{setup}
sleep 20 & _gui_pid=$!
( gui_smart_choice sda fail pending > {posix(gui)}/out; echo done > {posix(gui)}/rc ) &
CH=$!
for _ in 1 2 3 4 5; do [ -f {posix(gui)}/smart-request ] && break; sleep 0.3; done
N=$(IFS='|' read -r n _ < {posix(gui)}/smart-request; echo "$n")
# (1) nonce לא-תואם עם ערך חוקי — חייב להידחות
printf 'BOGUS-9|rescue\\n' > {posix(gui)}/smart-decision
sleep 2
[ -f {posix(gui)}/rc ] && echo RETURNED-ON-BAD-NONCE
[ -f {posix(gui)}/smart-decision ] && echo BAD-NONCE-KEPT
# (2) nonce תואם אך ערך לא-חוקי — חייב להידחות
printf '%s|garbage\\n' "$N" > {posix(gui)}/smart-decision
sleep 2
[ -f {posix(gui)}/rc ] && echo RETURNED-ON-BAD-VALUE
# (3) nonce תואם + ערך חוקי — משחרר
printf '%s|skip\\n' "$N" > {posix(gui)}/smart-decision
wait $CH
kill $_gui_pid 2>/dev/null
cat {posix(gui)}/out
"""
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "RETURNED-ON-BAD-NONCE" not in out.stdout, "שוחרר על nonce לא-תואם — באג הבטיחות"
    assert "BAD-NONCE-KEPT" not in out.stdout, "החלטה עם nonce לא-תואם לא נמחקה"
    assert "RETURNED-ON-BAD-VALUE" not in out.stdout, "שוחרר על ערך לא-חוקי"
    assert "skip" in out.stdout


def test_a_dead_gui_fails_instead_of_defaulting_to_write(tmp_path):
    """המסך נעלם בלי החלטה: הפונקציה נכשלת (rc≠0) ואינה מדפיסה כלום,
    כדי שהקורא ייפול לתפריט הטקסטואלי — לא ל-`rescue`. עיקרון 5."""
    setup, gui = smart_choice_env(tmp_path)
    # ‏pid שכבר מת: לולאת ההמתנה לא מתחילה בכלל.
    script = f"""{setup}
sh -c 'exit 0' & _gui_pid=$!
wait $_gui_pid 2>/dev/null
gui_smart_choice sda fail pending > {posix(gui)}/out; echo "rc=$?"
"""
    out = sh(script)
    assert "rc=1" in out.stdout, out.stdout
    assert (gui / "out").read_text() == "", "אסור פלט — היעדר פלט הוא הנפילה לטקסט"


def test_a_decision_with_no_trailing_newline_still_cleans_up_the_handshake(tmp_path):
    """‏#680: החלטה תקינה שכתובה בלי newline מסיים גורמת ל-`read` להחזיר
    nonzero (EOF לפני שורה שלמה) — אבל handshake נקי הוא חוזה לכל מסלול
    יציאה, גם הכישלון הזה. אילו הקבצים נשארו, קליק ישן על הדיסק הבא היה
    עלול להתבלבל (ראו את ה-nonce למעלה)."""
    setup, gui = smart_choice_env(tmp_path)
    script = f"""{setup}
sleep 20 & _gui_pid=$!
( gui_smart_choice sda fail pending > {posix(gui)}/out; echo "rc=$?" > {posix(gui)}/rc ) &
CH=$!
for _ in 1 2 3 4 5; do [ -f {posix(gui)}/smart-request ] && break; sleep 0.3; done
N=$(IFS='|' read -r n _ < {posix(gui)}/smart-request; echo "$n")
printf '%s|replace' "$N" > {posix(gui)}/smart-decision
wait $CH
kill $_gui_pid 2>/dev/null
cat {posix(gui)}/out {posix(gui)}/rc
[ -f {posix(gui)}/smart-request ] && echo REQUEST-LEFT
[ -f {posix(gui)}/smart-decision ] && echo DECISION-LEFT
true
"""
    out = sh(script)
    assert out.returncode == 0, out.stderr
    assert "rc=1" in out.stdout, out.stdout
    assert "REQUEST-LEFT" not in out.stdout, "smart-request נשאר אחרי כשל read"
    assert "DECISION-LEFT" not in out.stdout, "smart-decision נשאר אחרי כשל read"


def test_gui_smart_choice_refuses_off_the_cloner(tmp_path):
    """רק מחשב שיכפול. תחנת כיתה מקבלת את התפריט הטקסטואלי של #666."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    setup = env(run, gui) + PRELUDE + PORTS + 'D_ROLE=classroom; _gui_pid=1; '
    out = sh(setup + 'gui_smart_choice sda fail pending; echo "rc=$?"')
    assert "rc=1" in out.stdout


# --- gui_dispatch: הכרעת SMART כבולה לבקשה הפעילה, בלי כניסה -----------------


def bridge_setup(tmp_path: Path, screen: str) -> tuple[str, Path]:
    """גשר ה-GUI טעון, עם gui_role שמסמן אם נקרא (אסור על אסימון SMART)."""
    gui = tmp_path / "gui"; gui.mkdir()
    run = tmp_path / "run"; run.mkdir()
    setup = (f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
             f'GUI_SCREEN={screen} MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; '
             f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; '
             f'. {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/buildmenuitems.sh; '
             f'. {posix(AGENT)}/lib/buildmenu.sh; '
             f'. {posix(AGENT)}/lib/buildcapture.sh; . {posix(AGENT)}/lib/roomdraw.sh; '
             f'. {posix(AGENT)}/lib/roomflow.sh; '
             f'. {posix(AGENT)}/lib/guistate.sh; . {posix(AGENT)}/lib/sysinfo.sh; '
             f'. {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/clonergui.sh; '
             f'. {posix(AGENT)}/lib/guibridge.sh; '
             'gui_role() { echo GUI_ROLE_CALLED >&2; return 1; }; ')
    return setup, gui


def test_the_bridge_binds_the_decision_to_the_active_request(tmp_path):
    """‏cloner headless — אין לו כניסה. ה-GUI שולח `smart-<action>|<nonce>`
    (ה-nonce שצויר עם ה-prompt); הגשר מקבל רק כשה-nonce תואם לבקשה
    הפעילה, וכותב את ההחלטה כבולה אליו."""
    setup, gui = bridge_setup(tmp_path, "cloner")
    (gui / "smart-request").write_text("7-2|2|fail|pending\n", newline="\n")
    out = sh(setup + 'token="smart-replace|7-2"; gui_dispatch; echo "rc=$?"')
    assert "rc=0" in out.stdout, out.stderr
    assert (gui / "smart-decision").read_text().strip() == "7-2|replace"
    assert "GUI_ROLE_CALLED" not in out.stderr, "בדיקת ה-role נקראה על אסימון SMART"


def test_the_bridge_rejects_a_stale_click_after_a_same_port_reprompt(tmp_path):
    """‏**הבקרה השלילית של באג ה-HIGH — התרחיש המדויק של אסטרה.** דיסק
    ‏port 2 התבקש (bakash A, nonce 1-2), המפעיל לחץ כפול; הבקשה הבאה
    היא **שוב אותו port 2** (הדיסק נתבקש שוב, nonce 2-2). הקליק המושהה
    השני נושא את nonce-A הישן (1-2). הוא תואם ב-port אבל **לא ב-nonce**
    → נדחה.

    זה בדיוק מה שבדיקת port בלבד **לא** תפסה: same-port re-prompt.
    בלי echo של ה-nonce, הקליק הישן היה מאושר לבקשה B — במקרה הגרוע
    ‏rescue על הדיסק הלא-נכון (עיקרון 5/7)."""
    setup, gui = bridge_setup(tmp_path, "cloner")
    (gui / "smart-request").write_text("2-2|2|fail|pending\n", newline="\n")   # bakash B
    out = sh(setup + 'token="smart-rescue|1-2"; gui_dispatch; echo "rc=$?"')   # nonce ישן של A
    assert "rc=1" in out.stdout
    assert not (gui / "smart-decision").exists(), "קליק מושהה עם nonce ישן אושר על same-port"


def test_the_bridge_refuses_a_smart_click_with_no_active_request(tmp_path):
    """קליק SMART כשאין בקשה פעילה (מושהה/כפול, או אחרי שהבקשה נצרכה)
    **נדחה** — לא נכתבת החלטה. על הקוד הישן הגשר כתב smart-decision על
    כל אסימון smart-*, ואז gui_smart_choice של הדיסק הבא היה קורא החלטה
    שהמפעיל לא קיבל עליו."""
    setup, gui = bridge_setup(tmp_path, "cloner")
    assert not (gui / "smart-request").exists()
    out = sh(setup + 'token="smart-replace|1-2"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout
    assert not (gui / "smart-decision").exists(), "החלטה נכתבה בלי בקשה פעילה"


def test_the_bridge_rejects_a_smart_click_with_no_nonce(tmp_path):
    """אסימון smart-* בלי nonce (פורמט ישן) נדחה — ה-nonce חובה כדי
    לכבול את הקליק לבקשה שצוירה."""
    setup, gui = bridge_setup(tmp_path, "cloner")
    (gui / "smart-request").write_text("7-2|2|fail|pending\n", newline="\n")
    out = sh(setup + 'token=smart-replace; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout
    assert not (gui / "smart-decision").exists()


def test_the_bridge_rejects_a_smart_token_off_the_cloner_screen(tmp_path):
    """אותו אסימון על מסך אחר (למשל כיתה) נדחה — לא נכתבת החלטה, גם אם
    יש smart-request פעיל."""
    setup, gui = bridge_setup(tmp_path, "class")
    (gui / "smart-request").write_text("7-2|2|fail|pending\n", newline="\n")
    out = sh(setup + 'token="smart-skip|7-2"; gui_dispatch; echo "rc=$?"')
    assert "rc=1" in out.stdout
    assert not (gui / "smart-decision").exists()


# --- הצד הגרפי (C): נבנה ונבדק היכן שיש מהדר וספריות ------------------------


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_native_gui_builds_and_routes_the_cloner_screen(tmp_path):
    """בונה את הבינארי ומריץ `--png` עם קובץ מצב של cloner. בדיקת הניתוב
    המובנית של `render_png` (ה-`want[]`) מאמתת ש-`--screen cloner` הגיע
    ל-`SCREEN_CLONER` ושהרשומות `drawer=`/`smart_prompt=` נפרסו — היא
    יוצאת ב-rc≠0 אם לא. זו הבקרה השלילית של הצד הגרפי: קוד ישן בלי
    ה-screen/הפרסר נכשל כאן."""
    binary = tmp_path / "gui-bin"
    build = subprocess.run(
        [BASH, "-c",
         'set -e; cd "$1"; '
         'cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE '
         '$(pkg-config --cflags pangocairo cairo libdrm) '
         '-o "$2" $(make -s -f Makefile print-gui-src) '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    assert binary.exists()

    # ‏--png מרנדר את **כל** הכרטיסים מהמצב הזה, וה-want[] מאמת ניתוב לכל
    # אחד. כרטיס message דורש msg_title, אחרת הוא נופל ל-menu וה-self-check
    # נכשל — לכן שורת message כאן, לצד רשומות ה-cloner שהפרסר צריך לפרוס.
    state = tmp_path / "state.txt"
    state.write_text(
        "cloner=Office 2024\n"
        "drawer=1|sda|writing|45|100|\n"
        "drawer=2|sdb|failed|0|100|pending sectors\n"
        "smart_prompt=2-2|2|fail|pending\n"
        "message=המחשב אינו רשום|רשמו אותו בקונסולה ורעננו.\n",
        newline="\n",
    )
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"),
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, run.stderr           # הניתוב נכשל => rc≠0
    assert (tmp_path / "card-cloner-light.png").exists()
    assert (tmp_path / "card-cloner-dark.png").exists()


def _build_gui(tmp_path: Path) -> Path:
    binary = tmp_path / "gui-bin"
    build = subprocess.run(
        [BASH, "-c",
         'set -e; cd "$1"; '
         'cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE '
         '$(pkg-config --cflags pangocairo cairo libdrm) '
         '-o "$2" $(make -s -f Makefile print-gui-src) '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary)],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL,
    )
    assert build.returncode == 0, build.stderr
    assert binary.exists()
    return binary


def _png(binary: Path, prefix: Path, state: Path, size: str = "1280x1024") -> None:
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(prefix), "--size", size,
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31",
         "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)


def _png_rgb(path: Path):
    """מפענח PNG (8-bit, RGB/RGBA, filter method 0) בלי תלות חיצונית, ומחזיר
    (width, height, channels, bytes) אחרי ביטול הסינון פר-שורה."""
    import struct
    import zlib

    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, width, height, bitd, ctype, idat = 8, 0, 0, 0, 0, b""
    while pos < len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        chunk = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            width, height, bitd, ctype = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            idat += chunk
        elif typ == b"IEND":
            break
        pos += 12 + ln
    assert bitd == 8 and ctype in (2, 6), f"unexpected PNG {bitd=} {ctype=}"
    ch = 4 if ctype == 6 else 3
    raw = zlib.decompress(idat)
    stride = width * ch
    out, prev, p = bytearray(), bytearray(stride), 0
    for _y in range(height):
        ft = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        for x in range(stride):
            a = line[x - ch] if x >= ch else 0
            b = prev[x]
            c = prev[x - ch] if x >= ch else 0
            if ft == 1:
                line[x] = (line[x] + a) & 0xFF
            elif ft == 2:
                line[x] = (line[x] + b) & 0xFF
            elif ft == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 0xFF
            elif ft == 4:
                pp = a + b - c
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        out += line; prev = line
    return width, height, ch, bytes(out)


def _count_color(px, ch, rgb, tol=26) -> int:
    r0, g0, b0 = rgb
    n = 0
    for i in range(0, len(px), ch):
        if abs(px[i] - r0) <= tol and abs(px[i + 1] - g0) <= tol and abs(px[i + 2] - b0) <= tol:
            n += 1
    return n


# ‏#1090: לגואי אין פלטה משלו — כל צבע הוא טוקן של הקונסולה (design-tokens.css),
# ו-theme.c מעתיק אותו. הטסטים קוראים את הטוקן מהקובץ, לא ערך קשיח: כשהקונסולה
# תשנה צבע, הטסט ייפול אם הגואי לא זז איתה.
TOKENS_CSS = REPO / "server" / "static" / "design-tokens.css"


def _token(name: str, theme: str = "light") -> tuple[int, int, int]:
    """‏`--clr-*` מהבלוק של `:root` (בהיר) או `:root[data-theme="dark"]` (כהה)."""
    import re
    css = TOKENS_CSS.read_text(encoding="utf-8")
    start = css.index(':root[data-theme="dark"]{' if theme == "dark" else ":root{")
    block = css[start:css.index("\n}", start)]
    m = re.search(re.escape(name) + r":#([0-9a-fA-F]{6})\b", block)
    assert m, f"{name} not in design-tokens.css ({theme})"
    v = int(m.group(1), 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


def _screen_enum() -> dict[str, int]:
    """‏`typedef enum { SCREEN_... } Screen;` מ-ui.h — שם→מספר, כדי שהודעת
    "routed to screen 8, not 7" של `--png` תיקרא בשמות ולא במספרים קשיחים."""
    import re
    src = (REPO / "native-gui" / "src" / "ui.h").read_text(encoding="utf-8")
    body = re.search(r"typedef enum \{(.*?)\} Screen;", src, re.S).group(1)
    names = [n for n in re.findall(r"\b(SCREEN_[A-Z_]+)\b", body)]
    return {n: i for i, n in enumerate(names)}


def _routing_error(stderr: str) -> str:
    """מתרגם `routed to screen N, not M` לשמות מה-enum, לדוח קריא."""
    import re
    by_num = {v: k for k, v in _screen_enum().items()}
    m = re.search(r"routed to screen (\d+), not (\d+)", stderr)
    if not m:
        return stderr
    got, want = int(m.group(1)), int(m.group(2))
    return f"{stderr.strip()} ({by_num.get(got, got)} instead of {by_num.get(want, want)})"


def _row_frac(px, ch, w, y, rgb, tol=2) -> float:
    """איזה חלק מהפיקסלים בשורה y הוא בצבע rgb."""
    row = px[y * w * ch:(y + 1) * w * ch]
    return _count_color(row, ch, rgb, tol) / w


def _ink_bounds(px, ch, w, y0, y1, bg, tol=2):
    """גבולות (left, right, top, bottom) של כל מה שאינו צבע הרקע בין השורות y0..y1."""
    r0, g0, b0 = bg
    left, right, top, bottom = w, -1, -1, -1
    for y in range(y0, y1):
        base = y * w * ch
        for x in range(w):
            i = base + x * ch
            if abs(px[i] - r0) > tol or abs(px[i + 1] - g0) > tol or abs(px[i + 2] - b0) > tol:
                if x < left: left = x
                if x > right: right = x
                if top < 0: top = y
                bottom = y
    return left, right, top, bottom


def _color_bounds(px, ch, w, x0, x1, y0, y1, rgb, tol=6):
    """גבולות הפיקסלים בצבע token בתוך מלבן — כמו _ink_bounds, בלי צבעי הרקע."""
    r0, g0, b0 = rgb
    left, right, top, bottom = w, -1, -1, -1
    for y in range(y0, y1):
        base = y * w * ch
        for x in range(x0, x1):
            i = base + x * ch
            if (abs(px[i] - r0) <= tol and abs(px[i + 1] - g0) <= tol
                    and abs(px[i + 2] - b0) <= tol):
                left, right = min(left, x), max(right, x)
                if top < 0:
                    top = y
                bottom = y
    return left, right, top, bottom


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_standby_shows_connected_disks_and_empty_cloner_port(tmp_path):
    """‏#1170: standby מציג את פורטים 1–3 גם כשפורט 2 חסר מהמלאי.

    שתי תמונות נבדלות רק בשורות cdisk: שמות הדגם והגדלים של פורטים 1 ו-3
    מוסיפים דיו כהה לעומת שלוש שורות ריקות. בקרה שלילית: screens.c שלפני
    התיקון מתעלם מ-cdisk בשני המצבים, ולכן מספר פיקסלי הטקסט זהה.
    """
    binary = _build_gui(tmp_path)
    with_disks = tmp_path / "1170-with-disks.txt"
    without_disks = tmp_path / "1170-without-disks.txt"
    common = "disk_probe=drives\nmessage=x|y\n"
    with_disks.write_text(
        "cdisk=1|sda|Samsung SSD 870 EVO|500107862016|passed|\n"
        "cdisk=3|sdc|Crucial MX500|1000204886016|warn|\n" + common,
        encoding="utf-8", newline="\n",
    )
    without_disks.write_text(common, encoding="utf-8", newline="\n")
    _png(binary, tmp_path / "1170-with", with_disks)
    _png(binary, tmp_path / "1170-without", without_disks)

    _, _, ch1, px1 = _png_rgb(tmp_path / "1170-with-standby-light.png")
    _, _, ch0, px0 = _png_rgb(tmp_path / "1170-without-standby-light.png")
    ink = _token("--clr-text")
    assert _count_color(px1, ch1, ink, tol=6) > _count_color(px0, ch0, ink, tol=6)

    source = (GUI / "src" / "screens.c").read_text(encoding="utf-8")
    for wording in ("ריק", "אין פורטי SATA בקושחה", "לא חוברו דיסקים",
                    "הדיסקים לא נבדקו"):
        assert wording in source


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_login_show_password_toggle_is_at_the_right_of_the_field(tmp_path):
    """‏#1167: "הצג" יושב בקצה הימני של שדה הסיסמה.

    שדה הסיסמה ריק ברינדור, ולכן **כל הדיו** בתוך תיבת השדה (בין תווית
    "סיסמה" לקו התחתון, בתוך רוחב השדה) הוא הכפתור. מודדים דיו מול צבע
    הרקע של השדה, לא צבע-טוקן: טקסט 12px מוחלק כמעט אינו פוגע בטוקן
    המדויק (הגרסה הקודמת של הטסט עברה גם כשהכפתור היה משמאל). בקרה
    שלילית: `Rect er={x,…}` (הנוסחה הישנה) → הדיו בחצי השמאלי.
    """
    binary = _build_gui(tmp_path)
    state = tmp_path / "1170-login.txt"
    state.write_text("message=x|y\n", encoding="utf-8", newline="\n")
    for size in ("1920x1080", "1280x1024"):
        prefix = tmp_path / f"1170-login-{size}"
        _png(binary, prefix, state, size)
        w, h, ch, px = _png_rgb(tmp_path / f"1170-login-{size}-login-light.png")
        pane = min(600, max(420, w * .32))
        fw = min(400, pane - 128)
        field_left = int(w - pane + (pane - fw) / 2)
        field_right = int(field_left + fw)
        # רקע הפאנל: פיקסל בתוך השדה, גבוה מעל הטופס (אין שם דיו).
        bg_i = ((h // 5) * w + (field_left + field_right) // 2) * ch
        bg = (px[bg_i], px[bg_i + 1], px[bg_i + 2])
        # קווי השדות: שורות שבהן הדיו (בכל צבע) חוצה >90% מרוחב השדה.
        # שני קווים — שם משתמש וסיסמה — כל אחד עשוי כמה שורות רצופות.
        wide = [y for y in range(h // 4, 3 * h // 4)
                if (lambda b: b[1] - b[0] > fw * .9)(_ink_bounds_x(px, ch, w, field_left, field_right, y, y + 1, bg))]
        lines: list[list[int]] = []
        for y in wide:
            if lines and y - lines[-1][1] <= 2:
                lines[-1][1] = y
            else:
                lines.append([y, y])
        assert len(lines) >= 2, f"expected two field underlines at {size}, got {lines}"
        pw_line = lines[1][0]
        y0, y1 = pw_line - 34, pw_line - 2
        left, right, top, bottom = _ink_bounds_x(px, ch, w, field_left, field_right, y0, y1, bg)
        assert right >= 0, f"no ink in the password box at {size} — the toggle is not drawn?"
        centre = (left + right) / 2
        assert centre > (field_left + field_right) / 2,             f"show-password toggle ink at columns {left}..{right} (rows {top}..{bottom}), not in the right half at {size}"
        assert right - left < fw * .5, f"ink spans {right-left}px — more than a toggle; measuring the wrong band?"


def _ink_bounds_x(px, ch, w, x0, x1, y0, y1, bg, tol=12):
    """כמו _ink_bounds, מוגבל לעמודות x0..x1."""
    r0, g0, b0 = bg
    left, right, top, bottom = x1, -1, -1, -1
    for y in range(y0, y1):
        base = y * w * ch
        for x in range(x0, x1):
            i = base + x * ch
            if abs(px[i] - r0) > tol or abs(px[i + 1] - g0) > tol or abs(px[i + 2] - b0) > tol:
                if x < left: left = x
                if x > right: right = x
                if top < 0: top = y
                bottom = y
    return left, right, top, bottom


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_room_grid_paints_healthy_drawers_green(tmp_path):
    """‏#695: מסך "הפצה למחשבי שיכפול" מצייר מלבן לכל מחשב עם הדיסקים בפנים.
    ‏disk בריא (`smart=passed`) נצבע ב-`led_ok` ירוק, ו-`smart=fail` ב-**כתום**
    (‏#872: F1 = כתום; אדום שמור ל-`failed_last`). ‏#1090: שלושת הצבעים הם
    טוקני הקונסולה `--clr-success`/`--clr-warning`/`--clr-danger` (בהיר).
    הטסט מזין `machine`/`machine_drawers`/`room_drawer` דרך `--state`, מרנדר
    את כרטיס החדר, וסופר פיקסלים ירוקים ואדומים. **בקרה שלילית:** בלי הפרסר
    של `room_drawer` ב-`state.c` אין מגירות, כל התאים אפורים (`led_idle`),
    והירוק/אדום נעלמים — הטסט נופל התנהגותית."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "room.txt"
    state.write_text(
        "machine=CLONE-01|3C:52:82:A1:00:21|1|1|2|writing|-1|0|\n"
        "machine_drawers=0|3\n"
        "room_drawer=0|1|SER-A|Model A|256060514304|passed|1|1\n"
        "room_drawer=0|2|SER-B|Model B|500107862016|passed|1|0\n"
        "room_drawer=0|3|SER-C|Model C|128035676160|fail|1|0\n"
        "message=x|y\n",
        newline="\n",
    )
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)
    w, h, ch, px = _png_rgb(tmp_path / "card-room-light.png")
    green = _count_color(px, ch, _token("--clr-success"))
    orange = _count_color(px, ch, _token("--clr-warning"))
    red = _count_color(px, ch, _token("--clr-danger"))
    assert green > 400, f"healthy drawers not painted green ({green} px)"
    assert orange > 200, f"F1 drawer not painted orange ({orange} px)"
    assert red < 200, f"a SMART-failed drawer must not be red -- red is failed_last ({red} px)"


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_cloner_idle_disks_show_smart_health_color(tmp_path):
    """‏#709: מסך מחשב השיכפול הממתין מצייר ריבוע סטטוס לכל דיסק במלאי —
    ירוק ל-`passed`, אדום ל-`fail` (אותה מוסכמה כמו גריד החדר). מזין
    `cdisk=...|smart` דרך `--state`, מרנדר את כרטיס ה-cloner, וסופר
    פיקסלים ב-`led_ok`/`ROOM_BAD`. **בקרה שלילית:** בלי השדה החמישי
    ב-`state.c` הדיסק ה-`fail` נופל ל-`unchecked` ונצבע ירוק, והאדום
    נעלם — הטסט נכשל התנהגותית."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "cloner.txt"
    state.write_text(
        "cloner=Office 2024\n"
        "cdisk=1|sda|Healthy Model|500107862016|passed\n"
        "cdisk=2|sdb|Bad Model|500107862016|fail\n"
        "message=x|y\n",
        newline="\n",
    )
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)
    w, h, ch, px = _png_rgb(tmp_path / "card-cloner-light.png")
    green = _count_color(px, ch, _token("--clr-success"))
    orange = _count_color(px, ch, _token("--clr-warning"))
    red = _count_color(px, ch, _token("--clr-danger"))
    assert green > 50, f"healthy idle disk not shown green ({green} px)"
    assert orange > 50, f"F1 idle disk not shown orange ({orange} px)"
    assert red < 50, f"a SMART-failed idle disk must not be red ({red} px)"


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_cloner_idle_disk_that_failed_last_clone_is_painted_red(tmp_path):
    """‏#867: דיסק שנושא את הסימון של #845 מגיע ב-`cdisk=` כ-`failed_last`,
    והמסך מצייר אותו אדום עם "נכשל בשיכפול הקודם" — גם כש-SMART שלו נקי.
    כאן הדיסק היחיד הוא `failed_last`, ולכן על המסך חייב להיות אדום.
    **בקרה שלילית:** ‏`state.c` על main מקפל ערך לא-מוכר ל-`unchecked`
    והריבוע נצבע ירוק — אפס פיקסלים אדומים והטסט נופל."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "cloner.txt"
    state.write_text(
        "cloner=Office 2024\n"
        "cdisk=1|sda|Failed Model|500107862016|failed_last\n"
        "message=x|y\n",
        newline="\n",
    )
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:1F", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)
    w, h, ch, px = _png_rgb(tmp_path / "card-cloner-light.png")
    red = _count_color(px, ch, _token("--clr-danger"))
    assert red > 50, f"disk that failed the last clone not shown red ({red} px)"


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_restore_card_routes_to_a_graphical_screen(tmp_path):
    """‏#706: כרטיס 'משיכת אימג' לכונן המחשב הזה' חייב לפתוח מסך גרפי
    (‏SCREEN_RESTORE) ולא ליפול לזרימת הטקסט שקורסת. מזין דיסק פנימי
    (‏removable=0) ושני אימג'ים, מרנדר את כרטיס restore, ודורש שבדיקת
    הניתוב המובנית של --png תעבור (rc==0 => restore נותב ל-SCREEN_RESTORE).
    **בקרה שלילית:** החזרת HIT_RESTORE ל-`emit1("restore"); return 1`
    (בלי MODE_RESTORE/הניתוב) => הניתוב נוחת מחוץ ל-SCREEN_RESTORE
    וה-self-check יוצא rc≠0."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "restore.txt"
    state.write_text(
        "disk=/dev/sda|Samsung SSD 870 EVO|256060514304|1|0\n"
        "image=img_1|Office 2024 — סטנדרט|Office\n"
        "image=img_2|SolidWorks 2025|הנדסאים\n"
        "message=x|y\n",
        newline="\n",
    )
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)   # restore routed to SCREEN_RESTORE, else rc≠0
    assert (tmp_path / "card-restore-light.png").exists()
    assert (tmp_path / "card-restore-dark.png").exists()


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_png_renders_the_cloner_card_from_a_state_without_cloner_data(tmp_path):
    """‏#1090: משכפל ממתין מנותב ל-`SCREEN_STANDBY` (מסך חדש), ולכן קובץ מצב
    של **מחשב בנייה** — בלי `cloner=`/`drawer=`/`smart_prompt=` — הפיל את
    כל ה-`--png`: כרטיס `cloner` נחת על standby ובדיקת הניתוב יצאה rc≠0
    ("routed to screen 8, not 7"). ‏`png_setup` מבטיח ש"קובץ מצב של המשתמש
    מרנדר כל כרטיס" — ולכן הכרטיס מקבל נתוני דוגמה כשאין לו משלו, כמו
    שכרטיס `standby` מנקה אותם. **בקרה שלילית:** בלי הזריעה ב-`png_setup`
    ה-rc הוא 1 והכרטיס `cloner` אינו נכתב."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "build.txt"
    state.write_text("disk=/dev/sda|Samsung SSD 870 EVO|256060514304|1|0\nmessage=x|y\n",
                     newline="\n")
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, _routing_error(run.stderr)
    assert (tmp_path / "card-cloner-light.png").exists()
    assert (tmp_path / "card-standby-light.png").exists()
    # שני המסכים קיימים ב-enum ושונים זה מזה: standby אינו alias של cloner.
    screens = _screen_enum()
    assert "SCREEN_STANDBY" in screens and screens["SCREEN_STANDBY"] != screens["SCREEN_CLONER"]


# --- ‏#765: מסך-מלא רספונסיבי + פלטת Clarity -------------------------------


def _render_all(binary: Path, tmp_path: Path, size: str):
    """מרנדר את כל הכרטיסים בגודל נתון (בלי --state: משתמש ב-sample_state,
    שמאכלס גם msg_title כך שבדיקת הניתוב של message עוברת)."""
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", size,
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31"],
        capture_output=True, text=True, timeout=120, env=env2,
        stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, run.stderr
    return run


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
@pytest.mark.parametrize("size", ["1920x1080", "1024x768"])
def test_native_gui_fills_the_screen(tmp_path, size):
    """‏#1090: המסך מלא — אין יותר פאנל ממורכז של #828. לפי הערות המוקאפ
    (‏`.hdr height 48`, ‏`.sb height 36`, ‏`.body padding 28/32/24`):

    * ה-header — 48 שורות בראש ב-`--clr-header` (כהה גם בבהיר, כמו vSphere),
      ושורה 48 כבר אינה header.
    * שורת הסטטוס — 36 השורות התחתונות ב-`--clr-nav`, קו שיער מעליהן.
    * התוכן נוגע בשוליים: 32 מימין ומשמאל, 28 מתחת ל-header, 24 מעל
      שורת הסטטוס — בשתי הרזולוציות.

    זה שומר רינדור בלינוקס, לא טענה לנאמנות פיקסל. הפאנל הישן (700–762px
    ממורכז, topline של 40) נופל על גובה ה-header ועל השוליים."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, size)
    w, h, ch, px = _png_rgb(tmp_path / "card-menu-light.png")
    header, nav, bg = _token("--clr-header"), _token("--clr-nav"), _token("--clr-bg")
    border = _token("--clr-border")
    hdr_h, sb_h, pad_x, pad_top, pad_bottom = 48, 36, 32, 28, 24

    # ה-header: השורה הראשונה והאחרונה (0, 47) נקיות מטקסט; שורת האמצע
    # נושאת את הטקסט (מכונה · שרת · גרסה, פיל, brand, שעה) — נמדד במעבדה
    # 0.71 ב-1024 ו-0.845 ב-1920 (השעה משנה רוחב), ולכן שם רק "header ברובו".
    # שורה 48 היא כבר הגוף.
    for y in (0, hdr_h - 1):
        assert _row_frac(px, ch, w, y, header) > .85, f"row {y} is not the header"
    assert _row_frac(px, ch, w, hdr_h // 2, header) > .6, "the header's text row is not the header"
    assert _row_frac(px, ch, w, hdr_h, header) < .05, f"the header is taller than {hdr_h}"

    # שורת הסטטוס: 36 שורות תחתונות — השורה העליונה שלהן היא קו השיער
    # (‏draw_status מצייר אותו ב-bar.y), השאר ב---clr-nav, ומעליהן רקע הגוף.
    for y in (h - 1, h - sb_h // 2, h - sb_h + 1):
        assert _row_frac(px, ch, w, y, nav) > .85, f"row {y} is not the status bar"
    assert _row_frac(px, ch, w, h - sb_h, border) > .85, "no hairline atop the status bar"
    assert _row_frac(px, ch, w, h - sb_h - 1, bg) > .85, f"the status bar is taller than {sb_h}"

    # הגוף: מה שאינו רקע בין ה-header לשורת הסטטוס נוגע בשוליים של המוקאפ.
    left, right, top, bottom = _ink_bounds(px, ch, w, hdr_h, h - sb_h, bg)
    assert abs(left - pad_x) <= 2, f"left margin {left}, want {pad_x}"
    assert abs((w - 1 - right) - pad_x) <= 2, f"right margin {w - 1 - right}, want {pad_x}"
    assert 0 <= top - (hdr_h + pad_top) <= 12, f"content starts at {top}, want ~{hdr_h + pad_top}"
    assert abs((h - sb_h - 1 - bottom) - pad_bottom) <= 2, \
        f"bottom margin {h - sb_h - 1 - bottom}, want {pad_bottom}"


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_native_gui_uses_the_console_action_blue(tmp_path):
    """‏#1090: כפתור "כניסה" צבוע ב-`--clr-action` של הקונסולה — ‏#0079B8
    בבהיר ו-#4c8fbd בכהה, כפי שהם ב-design-tokens.css (ולא הכחול הישן
    ‏#2d668a של מוקאפ #828). הערך נקרא מהקובץ, לא מוקשח כאן."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    for theme in ("light", "dark"):
        w, h, ch, px = _png_rgb(tmp_path / f"card-login-{theme}.png")
        blue = _count_color(px, ch, _token("--clr-action", theme), tol=6)
        assert blue > 200, f"console action blue absent on the {theme} login ({blue}px)"


# --- #872: שלושה צבעים — אדום/כתום/ירוק — במסך הגרפי ובקונסולה ------------------
#
# ‏native-gui מתקמפל על המעבדה בלבד (אין cc בווינדוס), ולכן זה שומר על
# **המקור**: הכלל יושב במקום אחד (`smart_level` + `smart_color` ב-
# ‏screens_rounds.c, מוצהרים ב-screens_rounds.h), והקבצים המפוצלים — הגריד
# ב-screens_room.c וכרטיסי המשכפל ב-screens_cloner.c — קוראים לו ואינם
# מקפלים fail לאדום בשום מקום אחר. ‏#1090: שלושת הצבעים הם טוקני ה-Theme
# (‏danger/warn/led_ok = ‏--clr-danger/--clr-warning/--clr-success של הקונסולה),
# ואין יותר ROOM_BAD/ROOM_WARN מקומיים. הקונסולה נבדקת באותה צורה — המיפוי
# ב-CSS/JS הוא טקסט. בקרה שלילית: לפני המיזוג ‏idle_smart_color/room_disk_healthy
# מקפלים warn/fail/failed_last לאדום, ואין failed_last בקונסולה.

STATIC = REPO / "server" / "static"
GUI_SRC = REPO / "native-gui" / "src"


def _c_function(src: str, name: str) -> str:
    """גוף ההגדרה של הפונקציה (רשימת פרמטרים ואז `{`) — לא אתר קריאה."""
    import re
    m = re.search(r"[ *]" + re.escape(name) + r"\([^;{)]*\)\s*\{", src)
    assert m, f"{name}() is not defined here"
    return src[m.start():src.index("\n}\n", m.start())]


def test_the_gui_maps_the_three_colours_in_one_place():
    src = (GUI_SRC / "screens_rounds.c").read_text(encoding="utf-8")
    level = _c_function(src, "smart_level")
    assert '"failed_last")) return 2' in level
    assert '"fail") || !strcmp(smart, "warn")) return 1' in level
    assert "return 0;" in level                             # ok/unchecked/כל השאר
    body = _c_function(src, "smart_color")
    assert "lvl == 2 ? t->danger : lvl == 1 ? t->warn : t->led_ok" in body
    # אין מיפוי שני: הגריד (screens_room.c — תא המגירה בהגדרה והבר בסבב חי)
    # וכרטיס הדיסק הממתין (screens_cloner.c) קוראים לאותה פונקציה, ואף קובץ
    # אינו מחזיק עותק של הכלל או פלטה מקומית.
    room = (GUI_SRC / "screens_room.c").read_text(encoding="utf-8")
    cloner = (GUI_SRC / "screens_cloner.c").read_text(encoding="utf-8")
    for text in (src, room, cloner):
        assert "room_disk_healthy" not in text and "idle_smart_color" not in text
        assert "ROOM_BAD" not in text and "ROOM_WARN" not in text
    for text in (room, cloner):
        assert text.count("smart_color(t,") >= 1
        assert "Rgb smart_color(" not in text and "int smart_level(" not in text
    assert "smart_level(m->room_drawers[i].smart)" in room   # נקודת המכונה: לא-ירוק = אזהרה


def test_the_gui_says_unchecked_in_words_next_to_a_green_dot():
    """‏unchecked נאמר במילים — "לא נבדק" ב-`smart_he`, שתא "בריאות" בכרטיס
    הדיסק מציג — ונצבע ירוק: ‏`smart_level` אינו מכיר אותו ולכן הוא רמה 0
    כמו passed. "לא הצלחנו לבדוק" מוצג, לא מוסתר (עיקרון 5)."""
    cloner = (GUI_SRC / "screens_cloner.c").read_text(encoding="utf-8")
    rounds = (GUI_SRC / "screens_rounds.c").read_text(encoding="utf-8")
    he = _c_function(rounds, "smart_he")
    assert '!strcmp(smart, "unchecked")) return "לא נבדק"' in he
    assert "smart_he(idle->smart)" in cloner                 # תא הבריאות מציג את המילה
    assert "unchecked" not in _c_function(rounds, "smart_level")   # נופל ל-return 0 = ירוק


def test_the_gui_panel_offers_replace_or_continue_only_on_a_red_disk():
    """אדום = החלף / המשך (שני כפתורים, "המשך" שולח skip); כתום נשאר עם
    שלושה ("כתוב בכל זאת" = rescue). הסוכן ממילא אינו מקבל rescue על אדום
    (test_smart) — אבל המסך לא מציע מה שלא יתקבל. ‏#1090: הכפתורים יושבים
    בשורה מתחת לשלושת הכרטיסים (`screen_cloner`), לא בפאנל."""
    src = (GUI_SRC / "screens_cloner.c").read_text(encoding="utf-8")
    fn = _c_function(src, "screen_cloner")
    assert 'int red = !strcmp(a->st.smart_verdict, "failed_last");' in fn
    guarded = fn[fn.index("if (!red) {"):]
    guarded = guarded[:guarded.index("\n        }")]          # סוגר הבלוק, לא ה-`}` של (Rect){…}
    assert '"כתוב בכל זאת"' in guarded and "HIT_SMART_RESCUE" in guarded   # rescue רק כשלא אדום
    assert fn.count("HIT_SMART_RESCUE") == 1
    assert 'red ? "המשך" : "דלג"' in fn and "HIT_SMART_SKIP" in fn
    assert '"החלף דיסק"' in fn and "HIT_SMART_REPLACE" in fn


def test_the_console_paints_failed_last_red_and_fail_orange():
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    scss = (STATIC / "station" / "station.css").read_text(encoding="utf-8")
    assert ".disk-smart.failed_last{background:var(--danger-line); color:var(--danger)}" in css
    assert ".disk-smart.fail{" not in css                   # כתום = ברירת המחדל של .disk-smart
    assert ".room-drawers .drawer .smart.failed_last{" in scss
    assert ".room-drawers .drawer .smart.fail{" not in scss
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    rjs = (STATIC / "station" / "room.js").read_text(encoding="utf-8")
    assert 'failed_last: "נכשל בשיכפול הקודם"' in js
    assert 'failed_last: "נכשל בשיכפול הקודם"' in rjs
    assert 'SMART_HE[d.smart]\n        ? ` <span class="smart ${esc(d.smart)}">' in rjs


def test_the_station_assets_all_carry_the_same_version():
    """אותו כלל כמו ב-index.html הראשי (test_server_netcfg): חצי bump גרוע
    מאין bump — ‏room.js חדש מול station.css ישן הוא מחלקה בלי צבע."""
    import re
    page = (STATIC / "station" / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([0-9.]+)", page))
    assert len(versions) == 1, versions
    assert "room.js?v=" in page and "station.css?v=" in page
