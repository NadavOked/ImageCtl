"""הדיווח האחרון של עבודה נשלח ונקרא, לא נשלח ונשכח — ‏issue #101.

‏`progress.sh` הצהיר על הכלל הנכון בהערה שלו, ויישם אותו במקום אחד
מתוך שלושה: ‏`pull_close` שלח את דיווח הסיום סינכרונית ובדק את קוד
היציאה, בעוד **מסלול סבב הכיתה ומסלול המגירות** הוציאו אותו דרך לולאת
ה"שלח ושכח" והמתינו ב-`sleep 6`. שש שניות אינן ראיה: עם
‏`--max-time 10 --retry 3` הן לא מחזיקות בהכרח אפילו ניסיון אחד שהושלם.

המחיר של הניחוש הזה הוא לא שורה חסרה במסך. מכונה שסיימה שחזור ושה-`done`
שלה לא הגיע נשארת `session_members.done = 0`, הסבב נשאר `running`,
וה-hello הבא שלה נענה באותו שחזור — **לולאת שחזור**, בדיוק תרחיש ה-QA
הקריטי שב-`CLAUDE.md`.

מה שנבדק כאן:

* **ראיה חיובית** — ‏`report_final` מחזיר 0 רק כשהשרת ענה 200 (`curl -sfS`
  הופך 400 לקוד יציאה), ועוצר את לולאת הדיווח לפני הדיווח האחרון כדי
  שיהיה כותב אחד ל-`progress.json`.
* **תקרת ניסיונות ולא תקרת שניות** — כל ניסיון הוא curl שלם עם ה-retry
  שלו, ולכן ספירת שניות כאן הייתה ניחוש שני.
* **הבקרה השלילית** — שני המסלולים **אינם ממשיכים** לאתחל/לכבות בלי
  תשובה. על הקוד שלפני התיקון הבדיקות האלה נופלות: הוא ישן שש שניות
  והמשיך.
* **למה זה חשוב** — הצד השרתי, שכבר עובד: מי שדיווח `done` לא מקבל את
  הסבב שוב, ומי שלא דיווח — כן.

הפלט של כל הרצה הולך לקובץ ולא ל-PIPE, ולולאת הדיווח מזויפת בכל מסלול
שנעצר: תהליך רקע ששורד את הריצה מחזיק את הצינור, ואז ההמתנה עליו נראית
כמו באג בקוד שנבדק (גוטצ'ה מוכרת ב-`CLAUDE.md`).
"""

from __future__ import annotations

import json
import shlex
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix

REPO = Path(__file__).resolve().parent.parent

#: ‏curl הוא המנגנון שנבדק — קוד היציאה שלו *הוא* הראיה החיובית — ו-jq
#: קורא את תשובת השרת שממנה נגזרים מזהה הסבב והאימג'. בלעדיהם אין מה
#: לבדוק, ובמקום שבו הם אמורים להיות זה כישלון ולא דילוג (#52).
native_tools = requires_native(("bash", BASH), "curl", "jq")

MAC = "00:00:5e:07:1a:c4"
SESSION = "ses_a91f"
IMAGE = "img_7f3a91"

MANIFEST = {"id": IMAGE, "name": "Windows 11 Lab", "family": 256,
            "total_compressed_bytes": 40960}
ANSWER = {"schema": 1, "known": True, "role": "classroom",
          "session": {"id": SESSION, "state": "running", "image_id": IMAGE,
                      "prefix": "LAB1"},
          "group": {"suffix": "05"}}

#: תקרה על הריצה כולה — רשת ביטחון ולא כוונון תזמון. הדרך היחידה להגיע
#: אליה היא תהליך שלא נסגר, וזה באג שצריך להיראות בשמו ולא כתלייה.
RUN_TIMEOUT_S = 90


# --- שרת אמיתי בתהליכון, שאפשר להגיד לו לסרב --------------------------------


class Reports(HTTPServer):
    """מקבל דיווחים ומחזיר את מה שהוגדר לו.

    ‏`refuse_first` הוא ההשהיה של האיסיו בצורתה המדידה: השרת לא עונה
    כמו שצריך על N הדיווחים הראשונים ואז חוזר לעצמו. ‏`refuse_first`
    שלילי = לא עונה כמו שצריך אף פעם.
    """

    refuse_first = 0
    received: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self) -> None:                       # noqa: N802 — שם של BaseHTTP
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            self.server.received.append(json.loads(raw))
        except ValueError:
            self.server.received.append({"unparsable": raw.decode("utf-8", "replace")})
        seen = len(self.server.received)
        refuse = self.server.refuse_first < 0 or seen <= self.server.refuse_first
        body = b'{"ok":false,"code":"nope"}' if refuse else b'{"ok":true}'
        self.send_response(503 if refuse else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """בלי רעש ל-stderr של הריצה."""


@pytest.fixture
def reports():
    httpd = Reports(("127.0.0.1", 0), Handler)
    httpd.received = []
    httpd.refuse_first = 0
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def url_of(httpd: HTTPServer) -> str:
    host, port = httpd.server_address[:2]
    return f"http://{host}:{port}"


# --- הרצת הפונקציות של הסוכן הראשי ------------------------------------------
#
# ‏`imagectl-agent` הוא סקריפט ולא ספרייה: ה-source שלו מריץ את הלולאה
# הראשית. הפונקציות נחתכות ממנו לפי הגבול שהקובץ עצמו מגדיר — הגדרה
# בעמודה 0 ו-`}` בעמודה 0 — ונטענות לבד. החיתוך נכשל בקול אם השם השתנה:
# בדיקה שלא מצאה את מה שהיא בודקת אינה בדיקה שעברה.


def cut_function(name: str) -> str | None:
    source = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    marker = f"\n{name}() {{\n"
    start = source.find(marker)
    if start == -1:
        return None
    end = source.find("\n}\n", start + len(marker))
    assert end != -1, f"‏{name} אינה נסגרת ב-}} בעמודה 0"
    return source[start + 1:end + 3]


def agent_functions(*names: str) -> str:
    """החיתוך שהבדיקות הסטטיות עומדות עליו: שם שאינו שם הוא כישלון."""
    chunks = []
    for name in names:
        body = cut_function(name)
        assert body is not None, f"‏{name} אינה מוגדרת בעמודה 0 ב-imagectl-agent"
        chunks.append(body)
    return "\n".join(chunks)


def loadable_functions() -> str:
    """מה שנטען להרצה. ‏`hold_unheard` נלקחת רק אם היא קיימת — לא כדי
    לסלוח על היעדרה אלא ההפך: כך בקרה שלילית מול הקוד שלפני התיקון
    מריצה את **המסלול האמיתי** שלו ונופלת על ההתנהגות (המכונה אתחלה
    בלי אישור), ולא על קובץ שלא נחתך.

    מ-#109 ואילך `hold_unheard` ו-`hold_beat` יושבות ב-`agent/lib/hold.sh`,
    ולכן החיתוך מ-`imagectl-agent` מחזיר `None` — ‏`sourced_libs` מביאה
    אותן משם. שתי הדרכים חיות זו לצד זו בכוונה: זה מה שמאפשר להריץ את
    אותו טסט בדיוק מול הקוד שלפני התיקון ומול זה שאחריו."""
    wanted = ("hold_unheard", "do_task", "do_restore_drawers", "do_restore")
    return "\n".join(body for body in (cut_function(n) for n in wanted)
                     if body is not None)


def sourced_libs() -> str:
    """ספריות הסוכן שהמסלולים צריכים. ‏`hold.sh` נטענת **רק אם היא
    קיימת**: בקרה שלילית ש-`git stash` הוריד בה את קוד הסוכן חייבת
    ליפול על ההתנהגות ולא על קובץ חסר."""
    names = ["common.sh", "jsonq.sh", "progress.sh", "ui.sh", "hold.sh"]
    return "".join(f". {posix(AGENT)}/lib/{n}; "
                   for n in names if (AGENT / "lib" / n).exists())


def journal(run: Path) -> str:
    path = run / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


#: הלולאה מזויפת בכל בדיקה של המסלולים: מה שנבדק שם הוא הדיווח האחרון,
#: וכל POST אמיתי שמגיע לשרת המזויף הוא שלו ושל אף אחד אחר. הזיוף גם
#: מונע לולאת רקע אינסופית ששורדת את הריצה — `hold_unheard` מרימה אחת.
#: ‏`$1$4` ולא `$1 $4`: מסלול הסבב מזוהה ב-session ומסלול הקליטה
#: ב-task, ותמיד בדיוק אחד מהם מלא (ממשק 4) — כך העקבה נושאת את המזהה
#: של שני המסלולים בלי רווח תלוי-מסלול בסוף השורה.
LOOP_STUB = 'progress_loop() { echo "LOOP $1$4" >> "$RUN_DIR/trace"; }; '

STUBS = (
    'pick_internal_disk() { echo sda; }; '
    'list_drawers() { echo "sdb sdc"; }; '
    'http_get() { cat "$RUN_DIR/manifest.src.json"; }; '
    'name_this_machine() { echo "NAMED" >> "$RUN_DIR/trace"; '
    'echo done > "$RUN_DIR/state"; }; '
    'hold_beat() { echo "BEAT" >> "$RUN_DIR/trace"; }; '
    '_end() { [ "${RESTORE_RC:-0}" = "0" ] && echo done || echo failed; }; '
    'run_restore() { echo "RESTORE" >> "$RUN_DIR/trace"; '
    'target_set "$2" "$(_end)"; _end > "$RUN_DIR/state"; '
    'return "${RESTORE_RC:-0}"; }; '
    'run_restore_drawers() { echo "DRAWERS" >> "$RUN_DIR/trace"; '
    'for _t in sdb sdc; do target_set "$_t" "$(_end)"; done; '
    '_end > "$RUN_DIR/state"; return "${RESTORE_RC:-0}"; }; '
)


def run_path(tmp_path: Path, server: str, call: str, *, tries: int = 2,
             restore_rc: int = 0, answer: dict | None = None,
             stubs: str = "", env: str = "") -> dict:
    """מריץ מסלול אחד של הסוכן ומחזיר מה שיצא ממנו.

    ‏`call` הוא הקריאה עצמה — ‏`do_restore`, ‏`do_restore_drawers ...`
    או `do_task`. ‏`answer` מחליף את תשובת השרת (מסלול הקליטה קורא
    ‏`.task.*` ולא `.session.*`), ‏`stubs` ו-`env` מוסיפים למסלול הזה
    בלבד. הפלט הולך לקובץ: תהליך רקע ששרד מחזיק צינור פתוח, וההמתנה
    עליו נראית כמו באג בקוד שנבדק ולא בכלי הבדיקה.
    """
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(answer or ANSWER), encoding="utf-8")
    (run / "manifest.src.json").write_text(json.dumps(MANIFEST), encoding="utf-8")
    funcs = tmp_path / "agentfuncs.sh"
    funcs.write_text(loadable_functions(), encoding="utf-8")
    out_file = tmp_path / "out.txt"

    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGECTL_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=4 PROGRESS_INTERVAL_S=0.2 "
        f"FINAL_REPORT_TRIES={tries} FINAL_REPORT_GAP_S=0 D_ROLE=classroom "
        f"RESTORE_RC={restore_rc} {env}; "
        + sourced_libs()
        + STUBS
        + stubs
        + LOOP_STUB
        + f". {posix(funcs)}; "
        # ‏`wait` ולא `sleep`: ‏`hold_unheard` מרימה את הדווח ברקע, ופייתון
        # קורא את העקבה רק אחרי שכל תהליכי הרקע של המעטפת באמת יצאו.
        # במסלול המוצלח הוא לא נחוץ ולא נקרא — שם המעטפת יוצאת ב-exit 0.
        + f'{call}; echo "RETURNED rc=$?"; wait'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        try:
            subprocess.run(
                [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
                stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            raise AssertionError(
                f"המסלול לא סיים תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")
            ) from None
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": journal(run),
        "trace": (run / "trace").read_text(encoding="utf-8").splitlines()
        if (run / "trace").exists() else [],
    }


HOLD = "FAILED: the work finished but the server never confirmed it"


# --- הבקרה השלילית: המסלולים אינם ממשיכים בלי תשובה --------------------------


@native_tools
def test_a_class_round_does_not_reboot_when_the_final_report_was_refused(
        tmp_path, reports):
    """**לב האיסיו, מסלול סבב הכיתה.**

    השרת אינו מאשר את דיווח הסיום. הקוד שלפני התיקון ישן שש שניות,
    הרג את הדווח ואתחל — והמכונה הזאת, שהשרת עדיין סופר אותה כמי שלא
    סיימה, הייתה מקבלת את אותו הסבב באתחול הבא ומשחזרת שוב.
    """
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "TEST-HOLD" in result["out"], "המכונה המשיכה בלי אישור מהשרת"
    assert HOLD in result["out"]
    assert "RETURNED rc=1" in result["out"]
    # הדיווח באמת נשלח, ולא פעם אחת בלבד — ראיה שהתקרה היא ניסיונות.
    assert len(reports.received) == 2
    assert reports.received[-1]["state"] == "done"
    assert "did not acknowledge the final report" in result["log"]


@native_tools
def test_the_cloning_room_does_not_power_off_when_the_final_report_was_refused(
        tmp_path, reports):
    """אותו כשל במסלול המגירות, ושם הוא חמור באותה מידה: המגירות
    מוחלפות כשהמכונה כבויה, ולכן כיבוי בלי אישור פירושו שהסבב לא
    יתפנה — ‏`_spent` דורש `done` מכל חבר — והסבב הבא ייחסם."""
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports),
                      f"do_restore_drawers {IMAGE} {SESSION}")

    assert "TEST-HOLD" in result["out"], "המכונה נכבתה בלי אישור מהשרת"
    assert HOLD in result["out"]
    assert "RETURNED rc=1" in result["out"]
    assert reports.received[-1]["session_id"] == SESSION
    assert reports.received[-1]["state"] == "done"


@native_tools
def test_the_held_machine_stays_visible_instead_of_going_quiet(tmp_path, reports):
    """עצירה אינה שתיקה (#64): המסך נקוב בשם, הדופק ממשיך, והדווח
    מורם בחזרה כדי שה-`done` ינחת ברגע שהשרת יחזור לענות."""
    reports.refuse_first = -1

    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "Leave the computer on and contact IT." in result["out"]
    # ‏`hold_unheard` מרימה את הדווח מחדש — הזיוף רושם את הקריאה השנייה.
    assert result["trace"].count(f"LOOP {SESSION}") == 2


# --- הצד החיובי: עם אישור, וכשהוא מאחר -------------------------------------


@native_tools
def test_a_class_round_reboots_once_the_server_took_the_report(tmp_path, reports):
    """אישור הוא אישור: ‏200 → המכונה מסיימת את דרכה כרגיל."""
    result = run_path(tmp_path, url_of(reports), "do_restore")

    assert "TEST-HOLD" not in result["out"]
    assert "RETURNED" not in result["out"], "המסלול לא הגיע לאתחול"
    assert len(reports.received) == 1
    report = reports.received[0]
    assert report["session_id"] == SESSION and report["state"] == "done"
    assert report["targets"][0]["dev"] == "sda"
    assert report["targets"][0]["state"] == "done"


@native_tools
def test_the_final_report_is_retried_until_the_server_comes_back(tmp_path, reports):
    """הגדרת "גמור" של האיסיו: שרת שאינו עונה כשהשחזור נגמר, וחוזר
    אחר כך — הדיווח האחרון מגיע, והמכונה ממשיכה כרגיל."""
    reports.refuse_first = 2

    result = run_path(tmp_path, url_of(reports), "do_restore", tries=4)

    assert "TEST-HOLD" not in result["out"]
    assert "RETURNED" not in result["out"]
    assert len(reports.received) == 3
    assert "got through on try 3" in result["log"]


@native_tools
def test_a_restore_that_failed_still_holds_on_its_own_message(tmp_path, reports):
    """שחזור שנכשל לא נגע בשינוי הזה: המסך שלו הוא "restore did not
    complete", והדווח נשאר לרוץ כדי שהקונסולה תראה `failed`."""
    result = run_path(tmp_path, url_of(reports), "do_restore", restore_rc=1)

    assert "FAILED: restore did not complete" in result["out"]
    assert HOLD not in result["out"]


# --- ‏report_final עצמו: עוצר את הלולאה, וקורא את התשובה ---------------------


def report_final_probe(tmp_path: Path, sender: str, *, tries: int = 2) -> dict:
    """מריץ את `report_final` מול תהליך רקע אמיתי שאפשר להרוג."""
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    out_file = tmp_path / "probe.txt"
    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} IMAGECTL_TEST=1 "
        f"FINAL_REPORT_TRIES={tries} FINAL_REPORT_GAP_S=0; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/progress.sh; "
        + sender
        + 'sleep 45 > /dev/null 2>&1 & _pid=$!; '
        f'if report_final "$_pid" {SESSION} {MAC} "http://x"; '
        'then echo "SENT"; else echo "NOT-SENT"; fi; '
        'if kill -0 "$_pid" 2>/dev/null; then echo "LOOP-ALIVE"; kill "$_pid"; '
        'else echo "LOOP-STOPPED"; fi'
    )
    with out_file.open("w", encoding="utf-8") as sink:
        subprocess.run(
            [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
            stdout=sink, stderr=subprocess.STDOUT, text=True, cwd=str(REPO),
            stdin=subprocess.DEVNULL, timeout=RUN_TIMEOUT_S,
        )
    return {"out": out_file.read_text(encoding="utf-8"), "log": journal(run)}


@native_tools
def test_the_reporter_is_stopped_before_the_closing_report_is_sent(tmp_path):
    """כותב אחד ל-`progress.json`. זה מה ש-`pull_close` עשה מהיום
    הראשון, וזה מה שהמסלולים האחרים לא עשו."""
    result = report_final_probe(
        tmp_path, 'http_post_json() { echo "POST" >> "$RUN_DIR/trace"; }; ')

    assert "SENT" in result["out"]
    assert "LOOP-STOPPED" in result["out"]


@native_tools
def test_a_report_the_server_refused_is_not_a_report_that_arrived(tmp_path):
    """עיקרון 5 בצורתו הצרופה: קוד היציאה הוא הראיה. ‏`curl -sfS` נותן
    ל-400 של השרת קוד יציאה, ולכן "לא הייתה שגיאה" אינו "התקבל"."""
    result = report_final_probe(tmp_path, "http_post_json() { return 22; }; ")

    assert "NOT-SENT" in result["out"]
    assert "LOOP-STOPPED" in result["out"]
    assert "WARNING" in result["log"]
    assert "did not acknowledge the final report after 2 tries" in result["log"]


@native_tools
def test_the_ceiling_is_tries_and_the_journal_says_which_one(tmp_path):
    """כל ניסיון הוא curl שלם עם ה-retry שלו — ולכן התקרה נספרת
    בניסיונות. היומן אומר איזה מהם עבר, כדי ששרת איטי ייראה כשרת
    איטי ולא כשרת תקין."""
    counter = (
        'http_post_json() { _n=$(cat "$RUN_DIR/n" 2>/dev/null || echo 0); '
        '_n=$((_n + 1)); echo "$_n" > "$RUN_DIR/n"; [ "$_n" -ge 3 ]; }; '
    )
    result = report_final_probe(tmp_path, counter, tries=5)

    assert "SENT" in result["out"]
    assert "the final report was not accepted (try 1 of 5)" in result["log"]
    assert "the final report was not accepted (try 2 of 5)" in result["log"]
    assert "got through on try 3" in result["log"]


# --- הבקרה הסטטית: השעון אינו הראיה, וזה מנגנון אחד ולא שלושה ---------------


@native_tools
def test_no_restore_path_sleeps_instead_of_reading_the_answer():
    """‏`sleep 6` לא חוזר, בשום צורה ובשום מסלול — הוא היה ההימור."""
    for name in ("do_restore", "do_restore_drawers"):
        body = agent_functions(name)
        assert "sleep 6" not in body, f"‏{name} עדיין מהמרת על שעון"
        assert "report_final" in body, f"‏{name} אינה קוראת את התשובה"


@native_tools
def test_the_answer_is_read_before_the_machine_leaves():
    """הסדר הוא כל העניין: קודם אישור, ורק אחריו אתחול או כיבוי."""
    station = agent_functions("do_restore")
    assert station.index("report_final") < station.index("reboot -f")
    room = agent_functions("do_restore_drawers")
    assert room.index("report_final") < room.index("poweroff -f")


@native_tools
def test_all_three_closing_reports_go_through_one_mechanism():
    """הכלל הנכון היה מיושם במקום אחד מתוך שלושה, וכך הוא נשחק.
    ‏`pull_close` עובר עכשיו דרך אותה פונקציה בדיוק."""
    progress = (AGENT / "lib" / "progress.sh").read_text(encoding="utf-8")
    close = progress[progress.index("pull_close() {"):]
    assert "report_final" in close
    agent = agent_functions("do_restore", "do_restore_drawers")
    assert agent.count("report_final") == 2


@native_tools
def test_the_periodic_loop_keeps_its_forgiveness():
    """מה שאסור לתקן: ‏`|| true` בלולאה התקופתית נכון ומתועד — דיווח
    שייחזור בעוד שתי שניות אינו חייב תשובה. הבעיה מעולם לא הייתה
    הלולאה אלא שהדיווח *האחרון* עבר דרכה."""
    progress = (AGENT / "lib" / "progress.sh").read_text(encoding="utf-8")
    loop = progress[progress.index("progress_loop() {"):]
    loop = loop[:loop.index("\n}\n")]
    assert "|| true" in loop


# --- למה זה חשוב: הצד השרתי, שכבר עובד --------------------------------------


def test_a_machine_that_reported_done_is_not_sent_the_round_again(server):
    """תרחיש ה-QA הקריטי מ-`CLAUDE.md`, ושתי הזרועות שלו יחד.

    השרת בסדר: מי שדיווח `done` מקבל תשובה בלי סבב. מה שמסוכן הוא
    הזרוע השנייה — מי ש-`done` שלו לא הגיע מקבל את אותו הסבב שוב,
    ומשחזר שוב. זו הסיבה שהסוכן אינו מאתחל בלי אישור.
    """
    from conftest import hello_body, setup_classroom            # noqa: PLC0415

    ids = setup_classroom(server)
    opened = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": IMAGE,
        "prefix": "LAB1", "expected_clients": 1,
    })
    assert opened.status_code == 200
    session_id = opened.json()["id"]

    first = server["anon"].post("/api/v1/agent/hello",
                                json=hello_body(ids["mac1"])).json()
    assert first["session"]["state"] in ("open", "running")

    def hello_again() -> dict:
        return server["anon"].post("/api/v1/agent/hello",
                                   json=hello_body(ids["mac1"])).json()

    # הזרוע המסוכנת: המכונה סיימה, ה-`done` לא הגיע — ‏hello מחזיר לה
    # את אותו הסבב, והיא תשחזר שוב. זה מה שהשינוי בסוכן מונע.
    assert hello_again()["session"] is not None

    report = {"session_id": session_id, "mac": ids["mac1"], "state": "done",
              "targets": [{"dev": "sda", "bytes_written": 40960,
                           "bytes_total": 40960, "state": "done"}]}
    assert server["anon"].post("/api/v1/agent/progress",
                               json=report).status_code == 200

    assert hello_again()["session"] is None
