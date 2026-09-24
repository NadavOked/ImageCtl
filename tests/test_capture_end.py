"""אחרי קליטה — לחזור לתפריט או לכבות, במקום כיבוי אוטומטי בלבד (#409).

נדב, 05/09, בקליטה הראשונה מחומרה: "צריך להוסיף שבסוף, כשהוא מסיים,
**לחזור לתפריט הראשי** — אולי אני רוצה להפיץ לכיתה? או לכבות." עד כאן
‏`do_task` ישן 20 שניות וכיבה, בלי לשאול.

ההכרעה (תגובת ההיקף ב-Issue): **רק מחשב הבנייה** משתנה — תלמיד מאתחל
ומחשב שיכפול מכבה, ושניהם נכונים ואינם נוגעים כאן. והברירה בלי תשובה
נקבעה ב-Issue עצמו: **כיבוי אחרי פסק זמן**, ההגנה הקיימת — מי שקלט ויצא
מהחדר מקבל מכונה כבויה והדיסק נשלף ממכונה כבויה.

מה נבדק, על הפונקציות האמיתיות מול שרת דיווח אמיתי בתהליכון:

* **‏`1` חוזר לתפריט** — ‏`do_task` חוזר ללולאה, ו-`_build_standby`
  מתאפס כדי ש-`build_console_screen` יצייר את `build_menu_flow` שוב
  (הקליטה הוזמנה מהתפריט, ו-`build_standby` הדליק אותו).
* **‏`2` מכבה מיד** — בלי ה-`sleep 20` שהיה.
* **שומרים — עוברים בשני המצבים:** אין מסך (EOF) → כיבוי; מסך שאיש
  אינו עונה בו (צינור פתוח ושקט) → כיבוי אחרי התקרה, לא המתנה לנצח.
* **השאלה עוברת במנגנון הקיים** — ‏`attended` (‏hello עם `prompt`, #906)
  ושחרור המסך הגרפי כמו לשאלת הכיווץ (`shrink_gui_release`).

הכיבוי מזויף כ-`exit 0` (כמו `poweroff -f`: אינו חוזר), ו-`sleep` מזויף
כרישום — כך "מכבה מיד" נמדד בעקבה ולא בשעון. ‏`IMAGECTL_TEST=0`: ה-`exit 0`
של מצב הטסט יושב לפני הכיבוי, ולא לפניו אין מה לבדוק.

הפלט הולך לקובץ ולא ל-PIPE (גוטצ'ה ב-`CLAUDE.md`); ה-stdin הוא צינור
משלנו ולא ירושה (‏#14).
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from test_agent import BASH, posix
from test_final_report import (  # noqa: F401 — ‏`reports` הוא fixture
    LOOP_STUB,
    REPO,
    RUN_TIMEOUT_S,
    MAC,
    STUBS,
    journal,
    loadable_functions,
    native_tools,
    reports,
    sourced_libs,
    url_of,
)
from test_hold_and_capture import CAPTURE_STUBS, TASK_ANSWER

END_STUBS = (
    'attended() { echo "ASKED $1" >> "$RUN_DIR/trace"; shift; "$@"; }; '
    'shrink_gui_release() { echo "GUI-RELEASE" >> "$RUN_DIR/trace"; }; '
    'finish_and_stop() { echo "POWEROFF" >> "$RUN_DIR/trace"; exit 0; }; '
    'sleep() { echo "SLEEP $1" >> "$RUN_DIR/trace"; }; '
)


def run_end(tmp_path: Path, server: str, *, answer: str | None,
            keep_open: bool = False, ask_s: int = 20) -> dict:
    """מריץ קליטה מוצלחת עד סופה. ‏`answer` נכתב ל-stdin ואז הוא נסגר;
    ‏`keep_open` משאיר אותו פתוח ושקט עד שהמעטפת יוצאת — מסך בלי אדם."""
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(TASK_ANSWER), encoding="utf-8")
    funcs = tmp_path / "agentfuncs.sh"
    funcs.write_text(loadable_functions(), encoding="utf-8")
    out_file = tmp_path / "out.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGECTL_TEST=0 HTTP_RETRIES=0 HTTP_TIMEOUT=4 PROGRESS_INTERVAL_S=0.2 "
        f"FINAL_REPORT_TRIES=2 FINAL_REPORT_GAP_S=0 D_ROLE=build "
        f"CAPTURE_END_ASK_S={ask_s}; "
        + sourced_libs() + STUBS + CAPTURE_STUBS + END_STUBS + LOOP_STUB
        + f". {posix(funcs)}; "
        # הקליטה הוזמנה מהתפריט: ‏build_standby הדליק את הדגל.
        + '_build_standby=1; do_task; '
        + 'echo "RETURNED rc=$? standby=$_build_standby"'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        proc = subprocess.Popen(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
            stdin=subprocess.PIPE, stdout=sink, stderr=subprocess.STDOUT,
            cwd=str(REPO),
        )
        assert proc.stdin is not None
        try:
            if answer is not None:
                proc.stdin.write(answer.encode())
                proc.stdin.flush()
            if not keep_open:
                proc.stdin.close()
            proc.wait(timeout=RUN_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
            raise AssertionError(
                f"הקליטה לא הסתיימה תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")) from None
        finally:
            if not proc.stdin.closed:
                proc.stdin.close()
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
    }


def waited(trace: list[str]) -> list[str]:
    """כל `sleep` שאינו אפס — ההמתנה שהמפעיל היה רואה."""
    return [t for t in trace if t.startswith("SLEEP ") and t != "SLEEP 0"]


# --- הבחירה ---------------------------------------------------------------


@native_tools
def test_one_returns_to_the_build_menu(tmp_path, reports):
    """**לב #409.** ‏`1` — המכונה נשארת דלוקה וחוזרת לתפריט, כדי להפיץ
    לכיתה מיד אחרי הקליטה. הקוד הישן ישן 20 שניות וכיבה בכל מקרה."""
    result = run_end(tmp_path, url_of(reports), answer="1\n")

    assert "POWEROFF" not in result["trace"], "המכונה כבתה למרות שהמפעיל בחר תפריט"
    assert "RETURNED rc=0 standby=0" in result["out"], (
        "‏do_task לא חזר ללולאה, או שהתפריט יישאר מוסתר (‏_build_standby)")
    assert "back to the menu" in result["log"]
    # הדיווח נשלח לפני השאלה — הקליטה סגורה בשרת גם כשלא מכבים.
    assert len(reports.received) == 1 and reports.received[0]["state"] == "done"


@native_tools
def test_two_powers_off_at_once(tmp_path, reports):
    """‏`2` — כיבוי **מיד**, בלי ההמתנה של 20 שניות שהייתה לפניו."""
    result = run_end(tmp_path, url_of(reports), answer="2\n")

    assert "POWEROFF" in result["trace"]
    assert waited(result["trace"]) == [], f"המתנה לפני הכיבוי: {waited(result['trace'])}"
    assert "RETURNED" not in result["out"]


@native_tools
def test_the_screen_says_there_is_a_choice(tmp_path, reports):
    """ההודעה אומרת שאפשר לבחור, ומה יקרה בלי תשובה — לא "You can power
    off", משפט שנשמע כמו הוראה."""
    result = run_end(tmp_path, url_of(reports), answer="2\n")

    assert "Capture complete. The image is in the library." in result["out"]
    assert "[1] Back to the menu" in result["out"]
    assert "[2] Power off" in result["out"]
    assert "Powering off in 20 seconds" in result["out"]


@native_tools
def test_the_question_goes_through_the_existing_mechanism(tmp_path, reports):
    """‏`attended` — ה-hello נושא את השאלה לקונסולה בזמן ההמתנה (#906) —
    והמסך הגרפי משוחרר לפני השאלה, כמו לשאלת הכיווץ: שאלה על מסך הטקסט
    מאחורי הקיוסק לא נראית ולא נענית."""
    result = run_end(tmp_path, url_of(reports), answer="2\n")

    asked = [t for t in result["trace"] if t.startswith("ASKED ")]
    assert asked == ["ASKED Capture complete: back to the menu, or power off"]
    trace = result["trace"]
    assert "GUI-RELEASE" in trace
    assert trace.index("GUI-RELEASE") < trace.index(asked[0])


@native_tools
def test_an_invalid_key_is_not_a_choice(tmp_path, reports):
    """קלט לא חוקי מצייר שוב ואינו בוחר בשקט; הבחירה הבאה היא שקובעת."""
    result = run_end(tmp_path, url_of(reports), answer="x\n\n1\n")

    assert "POWEROFF" not in result["trace"]
    assert "RETURNED rc=0 standby=0" in result["out"]


# --- בלי תשובה: כיבוי — שומרים שעוברים גם על הקוד שלפני #409 --------------------


@native_tools
def test_no_screen_powers_off(tmp_path, reports):
    """EOF — אין מסך, או שהוא נסגר — הוא "אין תשובה", והברירה היא כיבוי."""
    result = run_end(tmp_path, url_of(reports), answer=None)

    assert "POWEROFF" in result["trace"]
    assert "RETURNED" not in result["out"]


@native_tools
def test_nobody_at_the_keyboard_powers_off_after_the_ceiling(tmp_path, reports):
    """מסך פתוח שאיש אינו עונה בו: כיבוי אחרי התקרה, לא המתנה לנצח.

    הצינור נשאר פתוח ושקט עד שהמעטפת יוצאת; בלי תקרה ‏`read` היה חוסם
    והריצה הייתה נופלת על `RUN_TIMEOUT_S`."""
    result = run_end(tmp_path, url_of(reports), answer=None,
                     keep_open=True, ask_s=1)

    assert "POWEROFF" in result["trace"]
    assert "RETURNED" not in result["out"]
