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
    assert "drawer=1|sda|writing|45|100|" in lines
    assert "drawer=2|sdb|waiting|0|100|" in lines


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
    assert drawer.count("|") == 5, drawer          # בדיוק שישה שדות
    assert "bad   disk line2" in drawer


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
             f'. {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/buildmenu.sh; '
             f'. {posix(AGENT)}/lib/buildcapture.sh; . {posix(AGENT)}/lib/roomflow.sh; '
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
    return subprocess.run(["pkg-config", "--exists", *pkgs]).returncode == 0


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
         '-o "$2" src/main.c src/screens.c src/screens_capture.c src/screens_rounds.c '
         'src/widgets.c src/state.c src/text.c src/draw.c src/theme.c src/backend.c src/input.c '
         '$(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(binary)],
        capture_output=True, text=True, timeout=300,
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
    )
    assert run.returncode == 0, run.stderr           # הניתוב נכשל => rc≠0
    assert (tmp_path / "card-cloner-light.png").exists()
    assert (tmp_path / "card-cloner-dark.png").exists()
