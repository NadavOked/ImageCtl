"""‏#989 ס׳ 2 — תשובת מפעיל לשאלת ה-SMART מהקונסולה (`server/smart_ask.py`).

השאלה נוסעת ב-hello של הדופק (`ask`), תשובת הקונסולה חוזרת ב-`ask_answer`
של ה-hello הבא, ותשובה ליד המכונה נשלחת בהרשאה (`ask_answered`) — **הראשונה
גוברת**, והצד השני רואה שכבר נענה (409 "כבר נענה ע"י …").

עיקרון 5: "לא נענתה" אינה "המשך". אדום אין לו "כתוב בכל זאת"; שאלה שהמכונה
הפסיקה לשאול אינה פתוחה; nonce שחוזר אחרי אתחול אינו שאלה שכבר נענתה.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import hello_body

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

CLONER = "aa:bb:cc:00:00:31"
OTHER = "aa:bb:cc:00:00:32"
BUILD = "b4:2e:99:07:1a:c5"


@pytest.fixture()
def server(tmp_path: Path, images_root):
    from server import users
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     wol_send=lambda _packet: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    for mac, name, group in ((CLONER, "01", "grp_CLONERS"), (OTHER, "02", "grp_CLONERS"),
                             (BUILD, "INS", "grp_BUILD")):
        assert admin.post("/api/console/machines", json={
            "mac": mac, "name": name, "group_id": group}).status_code == 200
    yield {"ctx": ctx, "admin": admin, "deploy": deploy, "anon": TestClient(app)}
    ctx.sender.stop()


def ask(nonce="3-2", port=2, verdict="warn", reason="crc 4"):
    return {"nonce": nonce, "port": port, "verdict": verdict, "reason": reason}


def hello(server, mac=CLONER, **extra) -> dict:
    response = server["anon"].post("/api/v1/agent/hello", json={**hello_body(mac), "joining": False, **extra})
    assert response.status_code == 200
    return response.json()


def room_ask(server, mac=CLONER):
    machines = server["admin"].get("/api/console/room").json()["machines"]
    return next(m for m in machines if m["mac"] == mac)["ask"]


def answer(server, who="admin", mac=CLONER, nonce="3-2", value="skip"):
    return server[who].post("/api/console/room/answer", json={"mac": mac, "nonce": nonce, "answer": value})


def smart_journal(server) -> list[str]:
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'smart_answered' ORDER BY id")]


# --- השאלה נראית בקונסולה -----------------------------------------------------


def test_the_question_rides_the_hello_and_shows_in_the_room(server):
    assert room_ask(server) is None
    assert "ask_answer" not in hello(server, ask=ask())
    shown = room_ask(server)
    assert (shown["nonce"], shown["port"], shown["verdict"], shown["reason"]) == ("3-2", 2, "warn", "crc 4")
    assert shown["answer"] is None and shown["answered_by"] is None


@pytest.mark.parametrize("bad", [
    {"nonce": "", "port": 2, "verdict": "warn", "reason": ""},
    {"nonce": "a;b", "port": 2, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": 0, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": True, "verdict": "warn", "reason": ""},
    {"nonce": "3-2", "port": 2, "verdict": "ok", "reason": ""},
    "x", 7, [],
])
def test_a_malformed_question_is_dropped_not_stored(server, bad):
    hello(server, ask=bad)
    assert room_ask(server) is None


def test_a_hello_without_the_question_closes_an_open_one(server):
    hello(server, ask=ask())
    assert room_ask(server) is not None
    hello(server)      # המכונה כבר לא שואלת (המסך נפל / אתחול)
    assert room_ask(server) is None
    assert answer(server).status_code == 404


# --- תשובה מהקונסולה ----------------------------------------------------------


def test_a_console_answer_comes_back_in_the_next_hello_and_says_who(server):
    hello(server, ask=ask())
    response = answer(server, value="rescue")
    assert response.status_code == 200 and response.json() == {"ok": True, "answer": "rescue"}
    assert hello(server, ask=ask())["ask_answer"] == {"nonce": "3-2", "answer": "rescue"}
    shown = room_ask(server)
    assert shown["answer"] == "rescue" and shown["answered_by"] == "הקונסולה (noc)"
    assert smart_journal(server) == [f"{CLONER} 2 rescue console:noc"]


def test_the_answer_is_not_delivered_to_a_hello_that_does_not_ask(server):
    hello(server, ask=ask())
    answer(server, value="skip")
    assert "ask_answer" not in hello(server)           # לא שואלת עוד — אין מה לבצע
    assert "ask_answer" not in hello(server, ask=ask(nonce="4-2"))   # שאלה אחרת


def test_a_deploy_user_has_no_console_and_cannot_answer(server):
    # משתמש הפצה עובד ממחשב הבנייה (#1073): הקונסולה סגורה לו — והשאלה נשארת פתוחה.
    hello(server, ask=ask())
    assert answer(server, who="deploy", value="replace").status_code == 403
    assert room_ask(server)["answer"] is None


# --- הכרעה: הראשונה גוברת -----------------------------------------------------


def test_machine_first_then_the_console_gets_409_naming_the_machine(server):
    hello(server, ask=ask())
    reply = hello(server, ask_answered={"nonce": "3-2", "answer": "skip"})
    assert reply["ask_answer"] == {"nonce": "3-2", "answer": "skip"}
    late = answer(server, value="rescue")
    assert late.status_code == 409 and late.json()["detail"] == 'כבר נענה ע"י המכונה (ליד המסך)'
    assert room_ask(server)["answer"] == "skip"
    assert smart_journal(server) == [f"{CLONER} 2 skip machine"]


def test_console_first_then_the_machine_is_told_the_winning_answer(server):
    hello(server, ask=ask())
    assert answer(server, value="replace").status_code == 200
    # המכונה לחצה "דלג" באותה שנייה — השרת מכריע, והיא מבצעת את מה שהוא מחזיר.
    reply = hello(server, ask_answered={"nonce": "3-2", "answer": "skip"})
    assert reply["ask_answer"] == {"nonce": "3-2", "answer": "replace"}
    assert room_ask(server)["answered_by"] == "הקונסולה (noc)"
    assert smart_journal(server) == [f"{CLONER} 2 replace console:noc"]


def test_two_console_answers_second_gets_409(server):
    hello(server, ask=ask())
    assert answer(server, value="skip").status_code == 200
    second = answer(server, value="replace")
    assert second.status_code == 409 and second.json()["detail"] == 'כבר נענה ע"י הקונסולה (noc)'


# --- סירובים ------------------------------------------------------------------


def test_refusals_say_why_and_change_nothing(server):
    assert answer(server).status_code == 404                              # אין שאלה
    hello(server, ask=ask())
    stale = answer(server, nonce="2-2")
    assert stale.status_code == 409 and "התחלפה" in stale.json()["detail"]
    assert answer(server, value="maybe").status_code == 400
    assert answer(server, mac=BUILD).status_code == 404                   # לא משכפל
    assert answer(server, mac="de:ad:be:ef:00:01").status_code == 404     # לא רשום
    assert server["admin"].post("/api/console/room/answer", content=b"not json").status_code == 404
    assert room_ask(server)["answer"] is None, "no refused call may answer the question"
    assert smart_journal(server) == []
    assert server["anon"].post("/api/console/room/answer", json={"mac": CLONER}).status_code == 401


def test_red_disk_has_no_write_anyway(server):
    hello(server, ask=ask(verdict="failed_last", reason="cable"))
    refused = answer(server, value="rescue")
    assert refused.status_code == 400 and "אדום" in refused.json()["detail"]
    assert room_ask(server)["answer"] is None
    assert answer(server, value="skip").status_code == 200


# --- אחרי האתחול: nonce חוזר ---------------------------------------------------


def test_a_nonce_that_comes_back_after_a_reboot_is_a_new_question(server):
    hello(server, ask=ask())
    answer(server, value="replace")
    hello(server, ask=ask())                  # נמסרה
    hello(server)                             # ממשיכה הלאה / מתכבה והתעוררה: hello בלי שאלה
    # המכונה עלתה מחדש, המונה ב-tmpfs חזר ל-1, ושוב אותו nonce ואותו port:
    fresh = hello(server, ask=ask())
    assert "ask_answer" not in fresh, "the old answer must not answer the new question"
    assert room_ask(server)["answer"] is None
    assert answer(server, value="skip").status_code == 200


def test_questions_are_per_machine(server):
    hello(server, ask=ask())
    assert room_ask(server, OTHER) is None
    assert answer(server, mac=OTHER).status_code == 404
