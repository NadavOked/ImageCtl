"""מנוע השידור — הפעלת udp-sender כשסבב עובר ל"משדר".

הרצת התהליכים מוזרקת, ולכן כל הלוגיקה נבדקת בלי udpcast מותקן: מה
בדיוק בשורת הפקודה, באיזה סדר רצות המחיצות, ומה קורה כשמשהו נכשל.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from server import sender as sender_module
from server.images import ImageLibrary
from server.room import close_round
from server.sender import DEFAULT_START_TIMEOUT, SenderEngine
from server.sessions import SessionError

from conftest import MANIFEST_256, hello_body, setup_classroom, write_image

#: לוג של שידור שהתחיל באמת — ברירת המחדל ל-Recorder, כדי שטסט שמצפה
#: ל-`done` לא ייכשל אחרי #438 רק כי לא כתב את הראיה החיובית.
TRANSFER_LOG = "Starting transfer:\n"
#: הלוג שנמדד על דביאן 13 מול udpcast 20120424 כשאיש לא הצטרף: קוד 0,
#: בלי Starting transfer ובלי "No participants... exiting".
NOBODY_JOINED_LOG = (
    "Udp-sender 20120424\n"
    "Using full duplex mode\n"
    "Using mcast address 234.44.3.1\n"
    "UDP sender for /tmp/nc.bin at 10.44.3.1 on eth3\n"
    "Broadcasting control to 10.44.3.255\n"
)


class FakeProcess:
    """תהליך מזויף: נחסם עד ש-`release` נקרא, בדיוק כמו udp-sender שמחכה."""

    def __init__(self, cmd, code=0, block=False, delay=0.0,
                 ignore_term=False, ignore_kill=False):
        self.cmd = cmd
        self.code = code
        self.terminated = False
        self.killed = False
        self.delay = delay          # כמה זמן `wait` באמת לוקח (#341)
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        # PID שלא קיים ב-/proc: אחרי poll() שאמר מת, אישור ה-PID לא
        # יתנגש בתהליך אמיתי על מעבדת ה-VM.
        self.pid = 10_000_000
        self._gate = threading.Event()
        if not block:
            self._gate.set()

    def wait(self):
        if self.delay:
            threading.Event().wait(self.delay)
        self._gate.wait(timeout=5)
        return self.code

    def poll(self):
        return self.code if self._gate.is_set() else None

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.code = -15
            self._gate.set()

    def kill(self):
        self.killed = True
        if not self.ignore_kill:
            self.code = -9
            self._gate.set()

    def release(self):
        self._gate.set()


class Recorder:
    def __init__(self, code=0, block=False, delay=0.0, log_text=TRANSFER_LOG,
                 ignore_term=False, ignore_kill=False):
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.code = code
        self.block = block
        self.delay = delay
        self.log_text = log_text
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        self.spawned = threading.Event()

    def __call__(self, cmd):
        self.commands.append(cmd)
        if self.log_text is not None:
            sender_module.SENDER_LOG.write_text(self.log_text, errors="replace")
        process = FakeProcess(
            cmd, self.code, self.block, self.delay,
            self.ignore_term, self.ignore_kill,
        )
        self.processes.append(process)
        self.spawned.set()
        return process


@pytest.fixture()
def free_ports(monkeypatch):
    """‏`port_holders` קורא את `/proc/net/udp`, שאינו קיים בווינדוס — ושם
    כל טסט כאן מת בכשל **סביבתי** (#312) שקובר את ההתנהגות הנבדקת.

    מוחלף כאן **רק מקור האמת על הפורטים**: הלוגיקה של `_ports_are_free`
    ושל `_port_verdict` רצה כרגיל ומקבלת תשובה חיובית "נבדק ונמצא פנוי",
    ולא "לא הצלחנו לבדוק" שמקופל להצלחה (עיקרון 5).
    """
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])


@pytest.fixture()
def library(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    return ImageLibrary(tmp_path)


def wait_for(predicate, timeout=5.0):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        deadline.wait(0.02)
    return False


# --- שורת הפקודה ------------------------------------------------------------


def test_one_transmission_per_partition_in_manifest_order(library):
    """סעיף 7: שידור אחד לכל קובץ מחיצה, בסדר שבמניפסט."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 12})
    assert wait_for(lambda: engine.status()["state"] == "done")

    assert len(recorder.commands) == 2
    files = [cmd[cmd.index("--file") + 1] for cmd in recorder.commands]
    assert [f.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in files] == [
        "p1.esp.pcl.zst", "p3.win.pcl.zst",     # לפי סדר המניפסט
    ]


def test_the_command_carries_the_receiver_count_and_portbase(library):
    # ‏portbase גבוה ולא זה של ההפצה: אפילו טסט שמעביר אותו במפורש לא
    # נוגע בפורטים שמכונה אמיתית מאזינה להם (#156).
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, portbase=21000, interface="eth1")
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 12})
    assert wait_for(lambda: engine.status()["state"] == "done")

    cmd = recorder.commands[0]
    assert cmd[0] == "udp-sender"
    assert cmd[cmd.index("--min-receivers") + 1] == "12"
    assert cmd[cmd.index("--portbase") + 1] == "21000"
    assert cmd[cmd.index("--interface") + 1] == "eth1"
    assert "--nokbd" in cmd                      # אין מקלדת בחדר השרתים


def test_the_bitrate_cap_reaches_the_command_when_configured(library):
    """רשת מהירה מהדיסקים מפילה מקבלים ("Dropped by server", ‏#24) —
    הרסן עובר ל-udp-sender רק כשהוגדר, וברירת המחדל נקייה ממנו."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, max_bitrate="200m")
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--max-bitrate") + 1] == "200m"

    bare = SenderEngine(library, runner=Recorder())
    assert "--max-bitrate" not in bare.command_for("x", 3)


def test_the_drop_ceiling_is_stated_and_not_left_at_udpcast_default(library):
    """‏05/09/2026: מכונה אחת מתה, וכל החדר קפא. ‏udpcast הוא סנכרוני לפי
    פרוסות — הוא ממתין ל-ACK **מכל משתתף** לפני שהוא קורא את הפרוסה הבאה
    מהדיסק. מקבל שמת אינו שולח `CMD_DISCONNECT`; הוא פשוט שותק, ונשאר
    ב-`participantsDb`.

    בלי `--retries-until-drop` udpcast מגדיר **200**, ואחרי העשירית כל
    המתנה היא לפחות שנייה — כ-190 שניות של חדר קפוא. נמדד בפועל: המשדר
    היה חי, ‏`read_bytes` לא זז בכלל בשש שניות, ו-`tx_bytes` גדל ב-4,082
    בייט בחמש שניות. זה מפר תרחיש QA מפורש ב-`CLAUDE.md`:
    **"כשל בתחנה/מגירה אחת לא עוצר את השאר"** (#437).

    ⚠️ **מה הטסט הזה בודק, ומה לא.** הוא בודק ש**התקרה נאמרת במפורש**
    ואינה נשארת על ברירת המחדל של udpcast. הוא **אינו** בודק שמקבל מת
    באמת אינו מקפיא את החדר — לזה צריך שני `udp-receiver` אמיתיים ו-SIGKILL
    באמצע, ו-`tests/hygiene.py` אוסר על pytest להריץ udpcast אמיתי (#79:
    יתום החזיק את פורט הייצור יום וחמש שעות). השם הקודם של הטסט הבטיח את
    ההתנהגות ולא את הדגל — בדיוק הפער שהעיקרון הזה קיים כדי למנוע.

    **בקרה שלילית:** הסרת הדגל מ-`command_for` מפילה אותו."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert "--retries-until-drop" in cmd, (
        "בלי הדגל udpcast מוותר רק אחרי 200 בקשות — מקבל מת מקפיא את החדר"
    )
    dropped_after = int(cmd[cmd.index("--retries-until-drop") + 1])
    assert 1 <= dropped_after <= 30, (
        f"{dropped_after} בקשות ACK: גבוה מדי, החדר קפוא דקות; "
        "נמוך מדי, מקבל בריא עם הפרעה רגעית נזרק"
    )
    # ‏--async מוותר על ה-ACK לגמרי: דאטגרם אבוד הופך לדיסק פגום בשקט,
    # וזה בדיוק מה שעיקרון 4 ("אין זריקת בלוק בשקט") אוסר.
    assert "--async" not in cmd


def test_a_round_with_no_joiners_still_asks_for_one_receiver(library):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 0})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--min-receivers") + 1] == "1"


# --- כשלים ------------------------------------------------------------------


def test_the_command_caps_the_wait_for_the_first_receiver(library, free_ports):
    """‏#341: בלי `--start-timeout` שידור שאיש לא הצטרף אליו נתקע לנצח —
    ‏`--max-wait` מתחיל לספור רק מהמקבל הראשון."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")

    cmd = recorder.commands[0]
    assert "--start-timeout" in cmd
    assert cmd[cmd.index("--start-timeout") + 1] == str(DEFAULT_START_TIMEOUT)


def test_nobody_joining_fails_and_says_that_nobody_joined(library, free_ports):
    """‏#438: udp-sender יוצא 0 בלי Starting transfer כשאיש לא הצטרף.

    זה הצורה שנמדדה מול udpcast 20120424 — לא קוד 1, ולא הזנב.
    """
    recorder = Recorder(code=0, log_text=NOBODY_JOINED_LOG)
    engine = SenderEngine(library, runner=recorder, start_timeout=0.02)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")

    error = engine.status()["error"]
    assert "אף מחשב לא הצטרף" in error
    assert "0.02" in error                 # התקרה נאמרת, ולא רק שפקעה
    assert "אימג'" in error or "פגום" in error
    assert len(recorder.commands) == 1     # לא ממשיכים למחיצה הבאה


def test_an_unreadable_sender_log_is_not_a_successful_send(
    library, free_ports, monkeypatch, tmp_path,
):
    """קובץ לוג חסר אינו "איש לא הצטרף" ואינו הצלחה (#438, עיקרון 5)."""
    monkeypatch.setattr(sender_module, "SENDER_LOG", tmp_path / "missing.log")
    recorder = Recorder(code=0, log_text=None)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    error = engine.status()["error"]
    assert "לא הצלחנו לקרוא" in error
    assert "אף מחשב לא הצטרף" not in error


def test_a_started_transfer_that_then_fails_is_not_blamed_on_nobody_joining(
    library, free_ports,
):
    """מחיצה שכן התחילה להשתדר ונפלה — הלוג מכיל Starting transfer —
    אינה 'אף מחשב לא הצטרף'. זה אבחון שלא נבדק (עיקרון 5, ‏#438)."""
    recorder = Recorder(code=1, log_text="Starting transfer: /x\nbroken\n")
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")

    error = engine.status()["error"]
    assert "אף מחשב לא הצטרף" not in error
    assert "קוד 1" in error


def test_a_failed_partition_stops_the_round(library):
    recorder = Recorder(code=3)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    # לא ממשיכים למחיצה הבאה אחרי כשל.
    assert len(recorder.commands) == 1
    assert "קוד 3" in engine.status()["error"]
    # כישלון מהיר אינו פקיעת המתנה: אסור לתלות בו אבחון שלא נבדק.
    assert "אף מחשב לא הצטרף" not in engine.status()["error"]


def test_a_missing_image_fails_before_spawning_anything(library):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_ghost", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert recorder.commands == []


def test_a_missing_partition_file_fails_loudly(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    (tmp_path / MANIFEST_256["id"] / "p3.win.pcl.zst").unlink()
    recorder = Recorder()
    engine = SenderEngine(ImageLibrary(tmp_path), runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert "p3.win.pcl.zst" in engine.status()["error"]
    assert len(recorder.commands) == 1           # הראשונה כן שודרה


def test_stopping_terminates_the_running_transmission(library):
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine.status()["state"] == "sending")

    engine.stop("ses_1")
    assert wait_for(lambda: recorder.processes[0].terminated)
    assert wait_for(lambda: engine.status()["state"] == "stopped")
    # לא עוברים למחיצה הבאה אחרי עצירה.
    assert len(recorder.commands) == 1


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored(
    library, free_ports, monkeypatch,
):
    """udpcast חוסם SIGTERM ב-doTransfer — TERM לבד משאיר יתום (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    recorder = Recorder(block=True, ignore_term=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine._process is not None)

    left = engine.stop("ses_1")
    process = recorder.processes[0]
    assert process.terminated
    assert process.killed
    assert left is None
    assert process.poll() is not None


def test_stop_does_not_claim_success_when_the_pid_is_still_alive(
    library, free_ports, monkeypatch,
):
    """‏"שלחנו SIGKILL" אינו "התהליך מת" — בלי ראיה זו אינה הצלחה (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    monkeypatch.setattr(sender_module, "STOP_KILL_WAIT", 0.05, raising=False)
    recorder = Recorder(block=True, ignore_term=True, ignore_kill=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine._process is not None)

    left = engine.stop("ses_1")
    process = recorder.processes[0]
    assert left == process.pid
    assert process.killed
    process.release()


# --- החיבור לסבב האמיתי ------------------------------------------------------


def test_the_round_starts_the_sender_and_closing_stops_it(server_with_sender):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]

    # ה-hello הראשון מצרף; הבא כבר מוצא סבב בשל ומפעיל את השידור.
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: len(recorder.commands) >= 1)

    status = server["admin"].get("/api/console/overview").json()["sender"]
    assert status["session_id"] == session
    assert status["partitions"] == 2

    server["admin"].post(f"/api/console/sessions/{session}/close")
    assert wait_for(lambda: recorder.processes[0].terminated)


def test_the_journal_reports_the_broadcast_in_hebrew(server_with_sender):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert wait_for(lambda: any(
        r["event"] == "send_start"
        for r in server["admin"].get("/api/console/journal").json()))

    row = next(r for r in server["admin"].get("/api/console/journal").json()
               if r["event"] == "send_start")
    assert row["label"] == "השידור יצא לדרך"
    assert "Office 2024 Standard" in row["text"]
    assert "2 מחיצות" in row["text"]
    assert "img_" not in row["text"]


def _active_room_with_closed_wave(ctx, wave_id: str) -> None:
    """סבב חדר פעיל שגל השידור שלו כבר סגור — הנתיב שבו close_round
    לא היה קורא ל-store.close, ולכן sender.stop לא רץ (#439)."""
    ctx.conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (wave_id,))
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state,"
        " wave_session_id, opened_by, created_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
        ("room_438", "img_7f3a91", 2, wave_id, "noc", "2026-01-01T00:00:00"),
    )
    ctx.conn.commit()


def test_close_round_stops_the_sender_even_when_the_wave_is_already_closed(
    server_with_sender, free_ports,
):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    ctx = server["ctx"]
    assert wait_for(lambda: ctx.sender._process is not None)

    _active_room_with_closed_wave(ctx, session)
    close_round(ctx, "noc")
    assert wait_for(lambda: recorder.processes[0].terminated)
    row = ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = 'room_438'"
    ).fetchone()
    assert row["state"] == "closed"


def test_close_round_does_not_report_ok_when_the_sender_is_still_alive(
    server_with_sender, monkeypatch, free_ports,
):
    """‏{"ok": true} כשהמשדר חי הוא בדיוק עיקרון 5 בכיוון ההפוך (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    monkeypatch.setattr(sender_module, "STOP_KILL_WAIT", 0.05, raising=False)
    server, recorder = server_with_sender
    recorder.ignore_term = True
    recorder.ignore_kill = True
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    ctx = server["ctx"]
    assert wait_for(lambda: ctx.sender._process is not None)

    _active_room_with_closed_wave(ctx, session)
    with pytest.raises(SessionError, match="udp-sender עדיין רץ"):
        close_round(ctx, "noc")
    row = ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = 'room_438'"
    ).fetchone()
    assert row["state"] == "active"
    recorder.processes[0].release()
