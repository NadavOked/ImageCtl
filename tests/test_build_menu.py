"""תפריט הטקסט של מחשב הבנייה — ‏issue #135.

מחשב הבנייה הציג מסך סטטי בלי תפריט: "הזמינו קליטה מהקונסולה". נדב עומד
מול המחשב ורוצה להזמין אותה **משם** — ולכן: כניסה, ואז שתיים או שלוש
אפשרויות לפי התפקיד שהשרת החזיר.

מה שנבדק כאן הוא התנהגות מול שרת HTTP אמיתי בתהליכון, בדיוק כמו
‏`test_final_report.py`: הפונקציה `build_console_screen` נחתכת מתוך
‏`agent/imagectl-agent` ורצה, ה-curl שבתוכה מדבר עם השרת המזויף, וכל
בקשה שהגיעה נשמרת. ארבע הבדיקות הן בקרה שלילית — **על הקוד שלפני
התיקון כולן נופלות על התנהגות**, כי המסך הישן אינו מבקש סיסמה, אינו
מציג תפריט ואינו שולח POST. הספריות החדשות נטענות **רק אם הן קיימות**,
כדי שהכישלון יהיה על מה שהמסך עשה ולא על קובץ חסר.

1. מנהל רואה שלוש אפשרויות.
2. משתמש deploy רואה שתיים — בלי קליטה, שהיא `admin_only` בשרת.
3. הקליטה מגיעה ל-`POST /api/console/tasks/capture` עם ה-`folder` הנכון —
   גם לתיקייה קיימת וגם לתיקייה חדשה שנוצרה בדרך.
4. ‏`unverified` (השרת לא ענה) אינו מתנהג כמו `rejected` (השרת בדק ואמר
   לא): אין "שם משתמש או סיסמה שגויים", אין שלושה ניסיונות, והיומן אומר
   שהסיסמה **לא נבדקה**. זו ההבחנה של עיקרון 5 במקום שהיא נולדה בו.

הפלט של כל הרצה הולך לקובץ ולא ל-PIPE, וה-stdin מגיע מקובץ תשובות
(‏`capture_output` ממתין לסגירת הצינור, לא ליציאת התהליך).
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
from test_final_report import cut_function

REPO = Path(__file__).resolve().parent.parent

#: ‏curl הוא המנגנון שנבדק (קוד התשובה וה-cookie הם הראיה החיובית),
#: ו-jq קורא את רשימות התיקיות והאימג'ים. בלעדיהם אין מה לבדוק, ובמקום
#: שבו הם אמורים להיות זה כישלון ולא דילוג (#52).
native_tools = requires_native(("bash", BASH), "curl", "jq")

MAC = "00:00:5e:07:1a:c4"
ANSWER = {"schema": 1, "known": True, "role": "build", "task": None,
          "session": None, "allowed_images": [], "ui": {"require_login": True}}

FOLDERS = [
    {"name": "Lab", "description": "", "images": 2},
    {"name": "Classrooms", "description": "", "images": 5},
]

#: שלוש התוויות של התפריט, כפי שהן על המסך.
CAPTURE_LABEL = "Upload an image to the server"
ROOM_LABEL = "Deploy to the cloning machines"
CLASS_LABEL = "Deploy to a classroom"

RUN_TIMEOUT_S = 90


# --- שרת קונסולה מזויף, שאפשר להגיד לו מה להחזיר -----------------------------


class Console(HTTPServer):
    """מחזיר את מה שהוגדר לו, וזוכר כל בקשה שהגיעה."""

    agent_login_status = 200
    role = "admin"
    folders: list[dict] = []
    requests: list[dict] = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def _send(self, status: int, payload, cookie: str = "") -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _record(self, body=None) -> None:
        self.server.requests.append({
            "method": self.command, "path": self.path, "body": body,
            "cookie": self.headers.get("Cookie", ""),
        })

    def do_GET(self) -> None:                        # noqa: N802 — BaseHTTP
        self._record()
        if self.path == "/api/console/folders":
            self._send(200, self.server.folders)
        elif self.path == "/api/console/images":
            self._send(200, [{"id": "img_1", "name": "Win11", "family": 256}])
        elif self.path == "/api/console/room":
            self._send(200, {"round": None, "machines": []})
        else:
            self._send(404, {"detail": "no such path"})

    def do_POST(self) -> None:                       # noqa: N802 — BaseHTTP
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw) if raw else None
        except ValueError:
            body = {"unparsable": raw.decode("utf-8", "replace")}
        self._record(body)

        if self.path == "/api/v1/agent/login":
            status = self.server.agent_login_status
            if status != 200:
                self._send(status, {"error": "no"})
                return
            self._send(200, {"ok": True, "role": self.server.role})
        elif self.path == "/api/console/login":
            token = f"u|{self.server.role}|9999999999|deadbeef"
            self._send(200, {"username": "u", "role": self.server.role,
                             "idle_seconds": 300},
                       cookie=f"imagectl_session={token}; Path=/; HttpOnly")
        elif self.path == "/api/console/folders":
            self._send(200, {"ok": True})
        elif self.path == "/api/console/tasks/capture":
            self._send(200, {"id": "tsk_01", "image_id": "img_01"})
        else:
            self._send(404, {"detail": "no such path"})

    def log_message(self, *_args) -> None:
        """בלי רעש ל-stderr של הריצה."""


@pytest.fixture
def console():
    httpd = Console(("127.0.0.1", 0), Handler)
    httpd.requests = []
    httpd.folders = [dict(f) for f in FOLDERS]
    httpd.role = "admin"
    httpd.agent_login_status = 200
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


# --- הרצת המסך האמיתי --------------------------------------------------------
#
# ‏`build_console_screen` נחתכת מ-`imagectl-agent` — היא קיימת **בשתי**
# הגרסאות, לפני התיקון ואחריו, ולכן הבקרה השלילית נופלת על מה שהמסך עשה.
# הספריות של #135 נטענות רק אם הן קיימות, מאותה סיבה בדיוק.

NEW_LIBS = ("buildmenu.sh", "buildcapture.sh", "roomflow.sh")


def sourced_libs() -> str:
    names = ["common.sh", "jsonq.sh", "ui.sh", "classround.sh", "hold.sh",
             *NEW_LIBS]
    return "".join(f". {posix(AGENT)}/lib/{n}; "
                   for n in names if (AGENT / "lib" / n).exists())


#: ‏`sleep` הוא המתנה לאדם, לא תזמון שנבדק — בלעדיו הבדיקה מחכה דקות
#: על מסכי הודעה. ‏`pick_internal_disk` קורא את /sys של המכונה המריצה.
STUBS = (
    'sleep() { :; }; '
    'pick_internal_disk() { echo sda; }; '
    'class_round_flow() { echo "CLASS-ROUND-OPENED"; return 0; }; '
)


def run_screen(tmp_path: Path, server: str, answers, *, stubs: str = "") -> dict:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(ANSWER), encoding="utf-8")

    body = cut_function("build_console_screen")
    assert body is not None, "‏build_console_screen אינה מוגדרת ב-imagectl-agent"
    funcs = tmp_path / "screen.sh"
    funcs.write_text(body, encoding="utf-8")

    stdin_file = tmp_path / "answers.txt"
    stdin_file.write_text("".join(f"{a}\n" for a in answers), encoding="utf-8")
    out_file = tmp_path / "out.txt"

    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGECTL_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=4; "
        + sourced_libs()
        + STUBS
        + stubs
        + f". {posix(funcs)}; "
        + f"build_console_screen < {shlex.quote(posix(stdin_file))}; "
        + 'echo "RETURNED rc=$?"'
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
                f"המסך לא סיים תוך {RUN_TIMEOUT_S}s. הפלט עד כה:\n"
                + out_file.read_text(encoding="utf-8")
            ) from None
    log = run / "agent.log"
    return {
        "out": out_file.read_text(encoding="utf-8"),
        "log": log.read_text(encoding="utf-8") if log.exists() else "",
    }


def posted(console: Console, path: str) -> list[dict]:
    return [r["body"] for r in console.requests
            if r["method"] == "POST" and r["path"] == path]


# --- 1+2: התפריט נגזר מהתפקיד ------------------------------------------------


@native_tools
def test_an_admin_is_offered_all_three_actions(tmp_path, console):
    """מנהל: קליטה, חדר שיכפול, כיתה. שלוש, ממוספרות 1-3."""
    console.role = "admin"

    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "0"])

    assert "Username:" in result["out"], "המסך לא ביקש כניסה בכלל"
    assert f"1) {CAPTURE_LABEL}" in result["out"]
    assert f"2) {ROOM_LABEL}" in result["out"]
    assert f"3) {CLASS_LABEL}" in result["out"]
    assert "Choose [1-3]" in result["out"]


@native_tools
def test_a_deploy_user_is_offered_two_actions_without_the_capture(
        tmp_path, console):
    """משתמש deploy: שתיים בלבד.

    הקליטה היא `admin_only` בשרת (`capture.py`), והתפריט לא מציע מה
    שיחזור 403 — הסתרה אינה הרשאה, אבל תפריט שמציע מה שהוא לא יכול
    לעשות משקר למפעיל.
    """
    console.role = "deploy"

    result = run_screen(tmp_path, url_of(console), ["deployer", "pw", "0"])

    assert CAPTURE_LABEL not in result["out"], "משתמש deploy קיבל קליטה"
    assert f"1) {ROOM_LABEL}" in result["out"]
    assert f"2) {CLASS_LABEL}" in result["out"]
    assert "Choose [1-2]" in result["out"]


@native_tools
def test_the_role_comes_from_the_answer_the_login_already_stored(
        tmp_path, console):
    """התפקיד נקרא מ-`login_resp.json` — אין קריאה נוספת לשרת בשבילו."""
    console.role = "deploy"

    run_screen(tmp_path, url_of(console), ["deployer", "pw", "0"])

    logins = [r for r in console.requests if r["path"] == "/api/v1/agent/login"]
    assert len(logins) == 1, "הכניסה נשלחה יותר מפעם אחת"
    assert not [r for r in console.requests if r["path"].endswith("/me")]


# --- 3: זרימת הקליטה מגיעה ל-POST עם התיקייה הנכונה --------------------------


@native_tools
def test_a_capture_into_an_existing_folder_carries_that_folder(
        tmp_path, console):
    """תיקייה קיימת: בחירה מהרשימה, ואותו שם הולך ב-`folder`."""
    answers = ["admin", "pw", "1", "2", "Win11 lab", "y"]

    result = run_screen(tmp_path, url_of(console), answers)

    assert "Lab" in result["out"] and "Classrooms" in result["out"]
    captures = posted(console, "/api/console/tasks/capture")
    assert len(captures) == 1, f"לא נשלחה בקשת קליטה אחת: {console.requests}"
    assert captures[0] == {"mac": MAC, "name": "Win11 lab", "disk": "sda",
                           "folder": "Classrooms"}
    # לא נוצרה תיקייה — נבחרה קיימת.
    assert posted(console, "/api/console/folders") == []


@native_tools
def test_a_capture_into_a_new_folder_creates_it_first(tmp_path, console):
    """תיקייה חדשה: נוצרת בשרת, ואז נושאת את הקליטה.

    הסדר הוא של האפיון (סעיף 26): רשימת התיקיות, בחירה מתוכן או חדשה,
    ואז שם האימג' והדיסק.
    """
    answers = ["admin", "pw", "1", "3", "New Course", "Win11 lab", "y"]

    result = run_screen(tmp_path, url_of(console), answers)

    assert posted(console, "/api/console/folders") == [{"name": "New Course"}]
    captures = posted(console, "/api/console/tasks/capture")
    assert len(captures) == 1, result["out"]
    assert captures[0]["folder"] == "New Course"
    assert captures[0]["name"] == "Win11 lab"


@native_tools
def test_a_hebrew_folder_name_never_reaches_the_server(tmp_path, console):
    """שם שהקונסולה הזאת אינה יכולה להציג נדחה במקלדת, לא בכיתה.

    ‏(הכרעת נדב, 30/08: שמות תיקיות ואימג'ים באנגלית או מספרים.)
    """
    answers = ["admin", "pw", "1", "3", "מעבדה", "מעבדה", "מעבדה"]

    result = run_screen(tmp_path, url_of(console), answers)

    assert posted(console, "/api/console/folders") == []
    assert "cannot display Hebrew" in result["out"]


@native_tools
def test_the_capture_body_is_the_one_the_console_sends(tmp_path, console):
    """‏`{mac,name,disk,folder}` — בדיוק השדות של `POST /tasks/capture`."""
    run_screen(tmp_path, url_of(console),
               ["admin", "pw", "1", "1", "Base", "y"])

    body = posted(console, "/api/console/tasks/capture")[0]
    assert sorted(body) == ["disk", "folder", "mac", "name"]
    assert body["folder"] == "Lab"


@native_tools
def test_the_capture_request_carries_the_console_session_cookie(
        tmp_path, console):
    """הקליטה היא `admin_only` — היא חייבת לנסוע עם ה-cookie של הקונסולה."""
    run_screen(tmp_path, url_of(console),
               ["admin", "pw", "1", "1", "Base", "y"])

    sent = [r for r in console.requests
            if r["path"] == "/api/console/tasks/capture"][0]
    assert "imagectl_session=" in sent["cookie"]


# --- 4: unverified אינו rejected ---------------------------------------------


@native_tools
def test_an_unanswered_login_is_not_a_wrong_password(tmp_path, console):
    """**עיקרון 5 במקום שהוא נולד בו.**

    השרת לא ענה — הסיסמה לא נבדקה. אסור שזה ייראה כמו "סיסמה שגויה":
    טכנאי שנשלח לחפש הקלדה שגויה כשהתקלה היא כבל או שרת מחפש במקום
    הלא נכון.
    """
    console.agent_login_status = 503

    result = run_screen(tmp_path, url_of(console), ["admin", "pw"])

    assert "was not checked" in result["out"], result["out"]
    assert "Wrong username or password" not in result["out"]
    assert "TEST-REBOOT: the server never checked the password" in result["out"]
    assert "no verdict from the server" in result["log"]
    # ניסיון אחד ולא שלושה: הקלדה חוזרת אינה מתקנת כבל.
    logins = [r for r in console.requests if r["path"] == "/api/v1/agent/login"]
    assert len(logins) == 1


@native_tools
def test_a_rejected_login_says_so_and_is_retried(tmp_path, console):
    """הצד השני של אותה הבחנה: השרת **כן** בדק ואמר לא."""
    console.agent_login_status = 401

    result = run_screen(tmp_path, url_of(console), ["admin", "no", "admin",
                                                    "no", "admin", "no"])

    assert "Wrong username or password" in result["out"]
    assert "was not checked" not in result["out"]
    assert "TEST-REBOOT: login failed" in result["out"]
    logins = [r for r in console.requests if r["path"] == "/api/v1/agent/login"]
    assert len(logins) == 3, "שלושת הניסיונות של recovery_login לא רצו"


@native_tools
def test_a_role_the_menu_does_not_know_gets_nothing(tmp_path, console):
    """רשימת-היתר, כמו `station.py`: תפקיד שלישי אינו נולד עם זכויות."""
    console.role = "auditor"

    result = run_screen(tmp_path, url_of(console), ["watcher", "pw", "0"])

    assert CAPTURE_LABEL not in result["out"]
    assert ROOM_LABEL not in result["out"]
    assert CLASS_LABEL not in result["out"]
    assert "may not capture or deploy" in result["out"]


# --- הזרימות האחרות עדיין מגיעות למקום הנכון ---------------------------------


@native_tools
def test_the_class_option_uses_the_existing_class_round_flow(
        tmp_path, console):
    """הפצה לכיתה אינה נכתבת מחדש — `classround.sh` כבר עושה את זה."""
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "3"])

    assert "CLASS-ROUND-OPENED" in result["out"]
    assert "not part of the class" in result["out"], \
        "המסך השאיר את ההבטחה של מסך התחנה ('will join automatically')"


@native_tools
def test_the_room_option_reads_the_room_before_it_offers_anything(
        tmp_path, console):
    """הפצה למחשבי שיכפול: קודם קוראים את מצב החדר מהשרת."""
    run_screen(tmp_path, url_of(console), ["admin", "pw", "2", "0"])

    assert any(r["method"] == "GET" and r["path"] == "/api/console/room"
               for r in console.requests), console.requests


@native_tools
def test_standby_hands_the_machine_back_to_the_poll_loop(tmp_path, console):
    """‏0 אינו יציאה למקום שאין: מחשב בנייה אין לו מערכת מקומית."""
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "0"])

    assert "Standing by" in result["out"]
    assert "RETURNED rc=0" in result["out"], "המסך לא חזר ללולאה"
    assert "TEST-REBOOT" not in result["out"]


# --- הכלל הסטטי: אין עברית על המסך של הסוכן ---------------------------------


@pytest.mark.parametrize("name", NEW_LIBS)
def test_the_new_agent_screens_print_no_hebrew(name):
    """לקונסולת Linux אין פונט עברי ואין RTL — ‏0 שורות עברית, כמו ui.sh."""
    path = AGENT / "lib" / name
    assert path.exists(), f"‏{name} חסר"
    hebrew = [line for line in path.read_text(encoding="utf-8").splitlines()
              if any("֐" <= ch <= "ת" for ch in line)]
    assert hebrew == []
