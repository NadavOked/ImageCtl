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
import time
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

MAC = "b4:2e:99:07:1a:c4"
#: ‏#880: ‏`class_deploy_enabled` דלוק כאן במפורש — הבדיקות של התפריט
#: המלא הן בדיקות של v2. ברירת המחדל של השרת היא כבוי, ושדה חסר = כבוי.
ANSWER = {"schema": 1, "known": True, "role": "build", "task": None,
          "session": None, "allowed_images": [], "ui": {"require_login": True},
          "class_deploy_enabled": True}

FOLDERS = [
    {"name": "Lab", "description": "", "images": 2},
    {"name": "Classrooms", "description": "", "images": 5},
]

#: ארבע התוויות של התפריט, כפי שהן על המסך (‏#715 הוסיף את השלישית).
CAPTURE_LABEL = "Upload an image to the server"
ROOM_LABEL = "Deploy to the cloning machines"
DIRECT_LABEL = "Deploy THIS disk directly to the cloning machines"
CLASS_LABEL = "Deploy to a classroom"

#: חדר עם משכפל ער ושתי מגירות טריות — מה שהזרימה הישירה צריכה כדי להציע יעד.
ROOM_AWAKE = {"round": None, "machines": [
    {"mac": "aa:bb:cc:00:00:21", "name": "shich-1", "awake": True, "joined": False,
     "drawers": 2, "fresh_drawers": 2, "drawer_count": 3,
     "drawer_list": [{"dev": "sda", "port": 1, "fresh": True, "state": None},
                     {"dev": "sdb", "port": 2, "fresh": True, "state": None},
                     {"dev": "sdc", "port": None, "fresh": True, "state": None}]},
    {"mac": "aa:bb:cc:00:00:22", "name": "shich-2", "awake": False, "joined": False,
     "drawers": 1, "fresh_drawers": 1, "drawer_count": 3,
     "drawer_list": [{"dev": "sda", "port": 1, "fresh": True, "state": None}]},
]}

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
            self._send(200, self.server.room)
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

        if self.path == "/api/v1/agent/hello":
            # #908: the attended beat while the menu waits. Recorded like any request.
            self._send(200, ANSWER)
        elif self.path == "/api/v1/agent/login":
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
        elif self.path == "/api/console/room":
            self._send(200, {"id": "room_01", "wave_session_id": "ses_01",
                             "task_id": "tsk_02", "image_id": "live_00000001"})
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
    httpd.room = {"round": None, "machines": []}
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

NEW_LIBS = ("buildmenu.sh", "buildcapture.sh", "roomdraw.sh", "roomflow.sh",
            "directflow.sh")


def sourced_libs() -> str:
    # ‏attended.sh (#906/#908) רק אם קיים: הבקרה השלילית מחזירה קובץ ל-main,
    # והכישלון חייב להיות על ההתנהגות ולא על `.` של קובץ חסר.
    names = ["common.sh", "jsonq.sh", "ui.sh", "recovery.sh", "classround.sh",
             "hold.sh", "attended.sh", *NEW_LIBS]
    return "".join(f". {posix(AGENT)}/lib/{n}; "
                   for n in names if (AGENT / "lib" / n).exists())


#: ‏`sleep` בשניות שלמות הוא המתנה לאדם, לא תזמון שנבדק — בלעדיו הבדיקה
#: מחכה דקות על מסכי הודעה. ‏`sleep` שברי הוא **הפעימה** (‏ATTENDED_BEAT_S,
#: ‏#908) והוא אמיתי — אחרת הפעימה הייתה לולאה צפופה. ‏`pick_internal_disk`
#: קורא את /sys של המכונה המריצה. ‏`build_hello` מזויף כמו ב-test_smart.py:
#: ‏sysinfo.sh אינו נטען כאן, והבדיקה היא על *מה שנוסף* לגוף ועל הקצב.
STUBS = (
    'sleep() { case "$1" in *.*) command sleep "$1" ;; esac; }; '
    'build_hello() { printf %s "{\\"schema\\":2,\\"mac\\":\\"$MAC\\",\\"joining\\":$1}"; }; '
    'pick_internal_disk() { echo sda; }; '
    'class_round_flow() { echo "CLASS-ROUND-OPENED"; return 0; }; '
)


def run_screen(tmp_path: Path, server: str, answers, *, stubs: str = "",
               answer: dict = ANSWER) -> dict:
    run = tmp_path / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "resp.json").write_text(json.dumps(answer), encoding="utf-8")

    body = cut_function("build_console_screen")
    assert body is not None, "‏build_console_screen אינה מוגדרת ב-imagectl-agent"
    funcs = tmp_path / "screen.sh"
    funcs.write_text(body, encoding="utf-8")

    # מספר בין התשובות הוא השהיה בשניות — האדם שעדיין לא ענה (#908). בלי
    # השהיה ה-stdin הוא קובץ; איתה — צינור בתוך bash, שבו `command sleep`
    # עוקף את ה-stub.
    if any(not isinstance(a, str) for a in answers):
        feed = "{ " + "; ".join(
            f"command sleep {a}" if not isinstance(a, str)
            else f"printf '%s\\n' {shlex.quote(a)}" for a in answers) + "; } | "
        stdin = ""
    else:
        stdin_file = tmp_path / "answers.txt"
        stdin_file.write_text("".join(f"{a}\n" for a in answers), encoding="utf-8",
                              newline="\n")   # לא CRLF בווינדוס: "4\r" אינה בחירה
        feed, stdin = "", f" < {shlex.quote(posix(stdin_file))}"
    out_file = tmp_path / "out.txt"

    script = (
        f"export RUN_DIR={shlex.quote(posix(run))} MAC={MAC!r} "
        f"SERVER={shlex.quote(server)} "
        f'RESP={shlex.quote(posix(run / "resp.json"))} '
        f"IMAGECTL_TEST=1 HTTP_RETRIES=0 HTTP_TIMEOUT=4 ATTENDED_BEAT_S=0.3; "
        + sourced_libs()
        + STUBS
        + stubs
        + f". {posix(funcs)}; "
        + f"{feed}build_console_screen{stdin}; "
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
def test_an_admin_is_offered_all_four_actions(tmp_path, console):
    """מנהל: קליטה, חדר שיכפול, הפצה ישירה מהדיסק הזה (#715), כיתה.
    ארבע, ממוספרות 1-4 — הישירה בין החדר לכיתה, כמו בכרטיסי ה-GUI."""
    console.role = "admin"

    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "0"])

    assert "Username:" in result["out"], "המסך לא ביקש כניסה בכלל"
    assert f"1) {CAPTURE_LABEL}" in result["out"]
    assert f"2) {ROOM_LABEL}" in result["out"]
    assert f"3) {DIRECT_LABEL}" in result["out"]
    assert f"4) {CLASS_LABEL}" in result["out"]
    assert "Choose [1-4]" in result["out"]


@native_tools
def test_a_deploy_user_is_offered_two_actions_without_the_capture(
        tmp_path, console):
    """משתמש deploy: שלוש בלבד.

    הקליטה היא `admin_only` בשרת (`capture.py`), והתפריט לא מציע מה
    שיחזור 403 — הסתרה אינה הרשאה, אבל תפריט שמציע מה שהוא לא יכול
    לעשות משקר למפעיל.
    """
    console.role = "deploy"

    result = run_screen(tmp_path, url_of(console), ["deployer", "pw", "0"])

    assert CAPTURE_LABEL not in result["out"], "משתמש deploy קיבל קליטה"
    assert f"1) {ROOM_LABEL}" in result["out"]
    assert f"2) {DIRECT_LABEL}" in result["out"]
    assert f"3) {CLASS_LABEL}" in result["out"]
    assert "Choose [1-3]" in result["out"]


# --- #880: "הפצה לכיתות" רק כשה-hello אמר שהמתג דלוק -----------------------


@native_tools
def test_the_class_option_is_hidden_when_the_server_switched_it_off(
        tmp_path, console):
    """‏v1 מהדורת שיכפול: ‏`class_deploy_enabled: false` ב-hello → בלי
    "Deploy to a classroom", והמספור מתכווץ ל-[1-3] (קליטה, חדר, ישירה
    של #715 — שתמיד מוצגת כמו החדר). השרת מסרב ממילא
    (409) — תפריט שמציע מה שהשרת יסרב לו הוא תפריט שמשקר. **בקרה
    שלילית:** על main האפשרות מוצגת תמיד (`echo "class"` ללא תנאי)."""
    console.role = "admin"
    answer = {**ANSWER, "class_deploy_enabled": False}

    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "0"],
                        answer=answer)

    assert CLASS_LABEL not in result["out"], "הכיתה הוצעה כשהמתג כבוי"
    assert f"1) {CAPTURE_LABEL}" in result["out"]
    assert f"2) {ROOM_LABEL}" in result["out"]
    assert f"3) {DIRECT_LABEL}" in result["out"]
    assert "Choose [1-3]" in result["out"]


@native_tools
def test_a_hello_without_the_switch_field_hides_the_class_option(
        tmp_path, console):
    """שדה חסר = כבוי (שרת ישן, או תשובה שלא נקראה). הסוכן החדש תמיד
    מקבל את השדה ממחשב הבנייה; היעדרו אינו "דלוק" (עיקרון 1)."""
    console.role = "deploy"
    answer = {k: v for k, v in ANSWER.items() if k != "class_deploy_enabled"}

    result = run_screen(tmp_path, url_of(console), ["deployer", "pw", "0"],
                        answer=answer)

    assert CLASS_LABEL not in result["out"], "שדה חסר נקרא כדלוק"
    assert f"1) {ROOM_LABEL}" in result["out"]
    assert f"2) {DIRECT_LABEL}" in result["out"]
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
                           "folder": "Classrooms", "description": ""}
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
    """‏`{mac,name,disk,folder,description}` — שדות `POST /tasks/capture`.

    ‏`description` נוסף עם הגואי הנייטיב (#327): הקונסולה שולחת אותו
    (library.js), והשרת מקבל אותו (capture.py). זרימת הטקסט שולחת ריק.
    """
    run_screen(tmp_path, url_of(console),
               ["admin", "pw", "1", "1", "Base", "y"])

    body = posted(console, "/api/console/tasks/capture")[0]
    assert sorted(body) == ["description", "disk", "folder", "mac", "name"]
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
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "4"])

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
def test_the_direct_option_posts_this_disk_as_the_source_with_chosen_targets(
        tmp_path, console):
    """‏#715: בלי בורר אימג' — המקור הוא הדיסק של המכונה; היעדים הם המכונות
    שנבחרו, וכל מגירה טרייה **עם חריץ** בהן. המכונה הישנה (2) אינה מוצעת;
    המגירה בלי `port` אינה נשלחת."""
    console.room = ROOM_AWAKE
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "3", "1", "y"])

    assert "1) shich-1  drawers 1,2" in result["out"], result["out"]
    assert "shich-2" not in result["out"]
    assert "read only" in result["out"]
    bodies = posted(console, "/api/console/room")
    assert bodies == [{
        "source": {"kind": "build_disk", "mac": MAC, "disk": "sda"},
        "target_slots": [{"mac": "aa:bb:cc:00:00:21", "ports": [1, 2]}],
    }], bodies
    assert "Direct deployment ordered" in result["out"]
    assert "RETURNED rc=0" in result["out"]


@native_tools
def test_the_direct_option_with_no_awake_machine_posts_nothing(tmp_path, console):
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "3", "0"])
    assert "No awake cloning machine" in result["out"], result["out"]
    assert posted(console, "/api/console/room") == []


@native_tools
def test_standby_hands_the_machine_back_to_the_poll_loop(tmp_path, console):
    """‏0 אינו יציאה למקום שאין: מחשב בנייה אין לו מערכת מקומית."""
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", "0"])

    assert "Standing by" in result["out"]
    assert "RETURNED rc=0" in result["out"], "המסך לא חזר ללולאה"
    assert "TEST-REBOOT" not in result["out"]


# --- #908: hello ממשיך בזמן שהתפריט ממתין למפעיל ------------------------------


def hellos(console: Console) -> list[dict]:
    return posted(console, "/api/v1/agent/hello")


@native_tools
def test_hello_keeps_going_while_the_menu_waits_for_a_choice(tmp_path, console):
    """ממצא צדדי מ-#906: `build_menu` עומד על `read` ואף hello לא יוצא —
    מחשב בנייה שעומד בתפריט נראה בקונסולה "לא נראתה" אחרי ONLINE_SECONDS,
    והמוניטור אליו נחסם. כאן המפעיל עונה על הבחירה רק אחרי ~4 פעימות
    (‏ATTENDED_BEAT_S=0.3 לבדיקה בלבד), ולכן חייבים להיספר ≥2 hello בזמן
    ההמתנה — כל אחד דופק (`joining: false`), ‏`waiting_for: operator`
    ו-`prompt: menu`. בקרה שלילית: `buildmenu.sh` של main → 0.
    (‏#912: הכניסה שלפני התפריט שולחת `prompt: signin` — נספרים כאן רק
    ה-`menu`.)"""
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", 1.3, "0"])

    assert "Standing by" in result["out"], result["out"]
    beats = [h for h in hellos(console) if h["prompt"] == "menu"]
    assert len(beats) >= 2, (hellos(console), result["out"])
    for h in beats:
        assert h["waiting_for"] == "operator"
        assert h["joining"] is False
        assert h["mac"] == MAC


@native_tools
def test_the_beat_stops_when_the_menu_hands_back(tmp_path, console):
    """אחרי 0 (standby) הפעימה נעצרת: המכונה חוזרת ללולאה ששולחת hello
    בעצמה, ופעימה שנשארת מאחור הייתה כותבת `prompt: menu` על מכונה שכבר
    אינה בתפריט. הראיה: מספר ה-hello אינו גדל אחרי שהמסך חזר."""
    result = run_screen(tmp_path, url_of(console), ["admin", "pw", 0.7, "0"])

    assert "RETURNED rc=0" in result["out"], result["out"]
    before = len(hellos(console))
    assert before >= 1, result["out"]
    time.sleep(1.0)
    assert len(hellos(console)) == before


# --- #912: וגם מסך הכניסה שלפני התפריט ----------------------------------------


@native_tools
def test_hello_keeps_going_while_the_sign_in_waits(tmp_path, console):
    """ממצא צדדי מ-#908: התפריט נעטף, שער הכניסה לא — מחשב בנייה שעומד על
    "Username:" שעות נראה בקונסולה "לא נראתה". כאן המפעיל מקליד את שם
    המשתמש רק אחרי ~3 פעימות (‏ATTENDED_BEAT_S=0.3), ולכן חייבים להיספר ≥2
    hello עם `prompt: signin` **לפני** הראשון עם `prompt: menu` — פעימה
    אחת בכל רגע, לא שתיים במקביל. בקרה שלילית: `recovery.sh` של main → 0."""
    result = run_screen(tmp_path, url_of(console), [0.9, "admin", "pw", "0"])

    assert "Standing by" in result["out"], result["out"]
    prompts = [h["prompt"] for h in hellos(console)]
    assert prompts.count("signin") >= 2, prompts
    assert "menu" in prompts, prompts
    assert prompts.index("menu") > max(i for i, p in enumerate(prompts) if p == "signin"),         "פעימת signin אחרי שהתפריט כבר עלה — שתי פעימות במקביל"
    for h in hellos(console):
        assert h["waiting_for"] == "operator" and h["joining"] is False
        assert h["mac"] == MAC


@native_tools
def test_a_refused_sign_in_leaves_no_beat_behind(tmp_path, console):
    """הסיבה ש-#908 לא עטף את השער: כניסה שנדחתה מסתיימת ב-`die_local`.
    הפעימה חייבת להיעצר **לפני** — אחרת נשאר תהליך יתום שכותב `signin` על
    מכונה שכבר אתחלה. הראיה: אחרי TEST-REBOOT מספר ה-hello אינו גדל."""
    console.agent_login_status = 401

    result = run_screen(tmp_path, url_of(console),
                        [0.4, "admin", "no", "admin", "no", "admin", "no"])

    assert "TEST-REBOOT: login failed" in result["out"], result["out"]
    beats = hellos(console)
    assert beats and all(h["prompt"] == "signin" for h in beats), beats
    before = len(beats)
    time.sleep(1.0)
    assert len(hellos(console)) == before


# --- הכלל הסטטי: אין עברית על המסך של הסוכן ---------------------------------


@pytest.mark.parametrize("name", NEW_LIBS)
def test_the_new_agent_screens_print_no_hebrew(name):
    """לקונסולת Linux אין פונט עברי ואין RTL — ‏0 שורות עברית, כמו ui.sh."""
    path = AGENT / "lib" / name
    assert path.exists(), f"‏{name} חסר"
    hebrew = [line for line in path.read_text(encoding="utf-8").splitlines()
              if any("֐" <= ch <= "ת" for ch in line)]
    assert hebrew == []
