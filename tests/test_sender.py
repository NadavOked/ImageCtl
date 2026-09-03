"""מנוע השידור — הפעלת udp-sender כשסבב עובר ל"משדר".

הרצת התהליכים מוזרקת, ולכן כל הלוגיקה נבדקת בלי udpcast מותקן: מה
בדיוק בשורת הפקודה, באיזה סדר רצות המחיצות, ומה קורה כשמשהו נכשל.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from server.images import ImageLibrary
from server.sender import SenderEngine

from conftest import MANIFEST_256, hello_body, setup_classroom, write_image


class FakeProcess:
    """תהליך מזויף: נחסם עד ש-`release` נקרא, בדיוק כמו udp-sender שמחכה."""

    def __init__(self, cmd, code=0, block=False):
        self.cmd = cmd
        self.code = code
        self.terminated = False
        self._gate = threading.Event()
        if not block:
            self._gate.set()

    def wait(self):
        self._gate.wait(timeout=5)
        return self.code

    def poll(self):
        return self.code if self._gate.is_set() else None

    def terminate(self):
        self.terminated = True
        self.code = -15
        self._gate.set()

    def release(self):
        self._gate.set()


class Recorder:
    def __init__(self, code=0, block=False):
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.code = code
        self.block = block
        self.spawned = threading.Event()

    def __call__(self, cmd):
        self.commands.append(cmd)
        process = FakeProcess(cmd, self.code, self.block)
        self.processes.append(process)
        self.spawned.set()
        return process


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


def test_a_round_with_no_joiners_still_asks_for_one_receiver(library):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 0})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--min-receivers") + 1] == "1"


# --- כשלים ------------------------------------------------------------------


def test_a_failed_partition_stops_the_round(library):
    recorder = Recorder(code=3)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    # לא ממשיכים למחיצה הבאה אחרי כשל.
    assert len(recorder.commands) == 1
    assert "קוד 3" in engine.status()["error"]


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
