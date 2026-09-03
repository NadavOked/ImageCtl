"""בדיקות על מכשיר המדידה עצמו.

כל האימות של הפרויקט נשען על משפט אחד — "N עברו, אפס דילוגים" — ועל
הנחה שקטה: שכשהריצה נגמרה, היא נגמרה. שתיהן נמצאו לא נכונות. ‏#52:
שלוש חבילות שלמות יכלו לדלג ו-`pytest` יצא 0. ‏#79: טסט השאיר
`udp-sender` אמיתי רץ שעתיים וחצי על פורט השידור של השרת.

הקובץ הזה בודק את שני התיקונים בראיה חיובית: שהדגל באמת הופך דילוג
לכישלון, ושהסורק באמת מוצא תהליך חי ובאמת מוודא שהוא מת אחרי ההריגה.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

import pytest

import hygiene
import native
from native import requires_native

from conftest import hello_body, setup_classroom


# --- ‏#52: כלי חסר במקום שהוא אמור להיות -------------------------------------


def test_a_missing_tool_is_a_skip_here_and_a_failure_where_it_belongs(monkeypatch):
    """אותו כלי חסר, שתי תשובות — לפי הדגל, לא לפי ניחוש."""
    monkeypatch.delenv(native.ENV_FLAG, raising=False)
    lenient = requires_native("no-such-tool-ever")
    assert lenient.name == "skipif" and lenient.args[0] is True

    monkeypatch.setenv(native.ENV_FLAG, "1")
    strict = requires_native("no-such-tool-ever")
    assert strict.name == native.MISSING_MARK
    assert "no-such-tool-ever" in strict.args[0]


def test_a_tool_that_is_here_never_marks_anything(monkeypatch):
    """בלי דרישה חסרה אין סימון בכלל — לא דילוג ולא כישלון."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    mark = requires_native(("bash", "/usr/bin/bash"))
    assert mark.name == "skipif" and mark.args[0] is False


def test_a_missing_header_counts_as_a_missing_requirement(monkeypatch, tmp_path):
    """‏hivewrite צריך גם `/usr/include/hivex.h`, לא רק תוכניות."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    absent = requires_native(paths=(str(tmp_path / "nope.h"),))
    assert absent.name == native.MISSING_MARK
    present = requires_native(paths=(str(tmp_path),))
    assert present.args[0] is False


def test_the_flag_reads_the_environment_each_time(monkeypatch):
    """הדגל אינו נקבע ביבוא — אחרת CI שמדליק אותו לא היה משנה כלום."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    assert native.native_required()
    monkeypatch.setenv(native.ENV_FLAG, "0")
    assert not native.native_required()


class _Report:
    """דוח מדולג מינימלי, בצורה שבה pytest מוסר אותו."""

    def __init__(self, nodeid: str, reason: str):
        self.nodeid, self.skipped = nodeid, True
        self.longrepr = ("tests/x.py", 1, f"Skipped: {reason}")


def test_a_skipped_run_is_not_a_green_run_when_native_is_required(monkeypatch):
    """רשת הביטחון: כל דילוג, מאיזו סיבה שלא תהיה — כולל `importorskip`."""
    audit = native.SkipAudit()
    audit.record(_Report("tests/test_fanout.py::test_a", "gcc חסר"))
    audit.record(_Report("tests/test_fanout.py::test_a", "gcc חסר"))   # לא נכפל

    monkeypatch.delenv(native.ENV_FLAG, raising=False)
    assert audit.verdict() == []

    monkeypatch.setenv(native.ENV_FLAG, "1")
    verdict = audit.verdict()
    assert verdict and "1 טסטים דולגו" in verdict[0]
    assert any("test_fanout.py::test_a" in line for line in verdict)
    assert any("gcc חסר" in line for line in verdict)


# --- ‏#79: מה שהריצה משאירה אחריה --------------------------------------------


def test_the_server_fixture_never_hands_out_the_real_sender(server):
    """הראיה החיובית: מנוע השידור מחזיק שולח מוזרק, לא את זה של המערכת.

    לפני התיקון `server` בנה אפליקציה בלי `sender_runner`, ולכן כל סבב
    שהבשיל תחתיו הפעיל `udp-sender` אמיתי — וזה הטסט שנכשל על הקוד ההוא.
    """
    from server.sender import run_process

    assert server["ctx"].sender.runner is server["recorder"]
    assert server["ctx"].sender.runner is not run_process


def test_a_ripened_round_records_the_command_instead_of_broadcasting(server):
    """המסלול המדויק מ-#79: סבב של 1/1 מבשיל תוך כדי hello.

    ‏`test_the_session_starts_once_when_two_threads_ripen_it` עשה בדיוק
    את זה, והשאיר מאחוריו שולח חי על ה-portbase של ההפצה.
    """
    ids = setup_classroom(server)
    assert server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).status_code == 200

    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))

    recorder = server["recorder"]
    assert recorder.spawned.wait(timeout=5), "השידור לא יצא לדרך בכלל"
    assert recorder.commands[0][0] == "udp-sender"


def test_the_guard_blocks_a_real_spawn_and_records_it(tmp_path, monkeypatch):
    """גם כשטסט שוכח להזריק שולח — תהליך אמיתי לא יוצא מכאן.

    הרישום הוא מה שהופך את זה לראיה: החסימה לבדה הייתה "אין תהליך",
    ואי אפשר להבדיל בינה לבין "אין באג".
    """
    from server import sender

    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "sender.log")
    with pytest.raises(OSError):
        sender.run_process(["udp-sender", "--portbase", "21000",
                            "--file", str(tmp_path)])
    # ‏`_no_real_sender` מפיל טסט שהשאיר רישום — הרישום הזה מכוון.
    assert hygiene.blocked_spawns.pop()[0] == "udp-sender"


@requires_native("cat", paths=("/proc",), why="הסריקה קוראת ב-/proc")
def test_the_scan_finds_a_live_sender_and_proves_it_died(tmp_path):
    """סורקים, הורגים, וקוראים שוב — "שלחנו SIGTERM" אינו "התהליך מת".

    התהליך כאן הוא עותק של `cat` בשם `udp-sender`, חסום על fifo שאיש
    אינו כותב אליו: אותה צורה שהסורק מחפש (שם התוכנית **וגם** הנתיב),
    בלי לשדר בית אחד לרשת ובלי להשאיר אחריו תת-תהליך משלו. סקריפט עם
    ‏shebang לא היה עובד — הקרנל מחליף את argv[0] בשם המפרש.
    """
    fake = tmp_path / "udp-sender"
    shutil.copy(shutil.which("cat"), fake)
    pipe = tmp_path / "stream"
    os.mkfifo(pipe)
    marker = str(tmp_path)

    child = subprocess.Popen([str(fake), str(pipe)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not hygiene.live_stream_processes(marker):
            time.sleep(0.05)
        found = hygiene.live_stream_processes(marker)
        assert [pid for pid, _ in found] == [child.pid], f"הסורק לא מצא: {found}"

        assert hygiene.kill_and_confirm(marker) == []
        assert hygiene.live_stream_processes(marker) == []
    finally:
        if child.poll() is None:                      # pragma: no cover
            child.kill()
        child.wait(timeout=10)


@requires_native("cat", paths=("/proc",), why="הסריקה קוראת ב-/proc")
def test_the_scan_ignores_processes_that_are_not_ours(tmp_path):
    """סינון לפי נתיב הריצה **וגם** לפי שם התוכנית.

    בלי שם התוכנית, המעטפת שבה נכתב `--basetemp=...` — ו-pytest עצמו —
    היו נספרים כתהליכים יתומים ונהרגים. ובלי הנתיב, ריצה מקבילה של
    מישהו אחר על אותו שרת הייתה נהרגת יחד איתנו.
    """
    assert hygiene.live_stream_processes(str(tmp_path / "someone-else")) == []

    pipe = tmp_path / "stream"
    os.mkfifo(pipe)
    child = subprocess.Popen([shutil.which("cat"), str(pipe)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        assert hygiene.live_stream_processes(str(tmp_path)) == []
    finally:
        child.kill()
        child.wait(timeout=10)


def test_the_session_verdict_says_not_checked_instead_of_clean(monkeypatch):
    """בלי `/proc` לא בדקנו — וזה לא אותו דבר כמו "נקי" (עיקרון 5)."""
    monkeypatch.setattr(hygiene, "scan_supported", lambda: False)
    assert hygiene.session_verdict("/tmp/whatever", native_required=False) == []
    strict = hygiene.session_verdict("/tmp/whatever", native_required=True)
    assert strict and "לא נבדקה" in strict[0]


def test_the_block_is_installed_for_the_whole_run():
    """החסימה מותקנת פעם אחת ואינה מוחזרת — אחרת תהליכון השידור שממשיך
    לרוץ אחרי סוף הטסט מוצא את `subprocess` האמיתי בדיוק בחלון הזה."""
    from server import sender

    assert sender.subprocess is not subprocess
    assert getattr(sender.subprocess, "STDOUT", None) == subprocess.STDOUT
