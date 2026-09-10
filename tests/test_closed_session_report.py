"""‏#557 — סבב שנסגר אינו מקבל דיווחים, והסוכן מפסיק לדווח עליו.

**נמדד על ברזל, 08/09/2026.** הסבב `ses_277091d3` נסגר ב-07:36:21.
ב-08:25 — **‏50 דקות אחר כך** — שתי מכונות עדיין שלחו
`POST /api/v1/agent/progress` עליו, כל שנייה, אלפי פעמים. השרת ענה
`200 OK` בכל אחת.

⚠️ **והמחיר לא היה רעש:** הן לא הצטרפו לגל הבא. הסבב החדש נשאר
פתוח וריק, ‏`udp-sender` לא רץ, **וההפצה נעצרה** עד אתחול ידני.

זהו בדיוק הפגם ש-#535 תיקן **למשימות**. מסלול הסבב לא קיבל את אותו
טיפול — ולכן הטסטים כאן בודקים את שני הצדדים: שהשרת מסרב, ושהסוכן
**מקשיב לסירוב**.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom
from server import reports


def _payload(session_id: str, mac: str, state: str = "writing") -> dict:
    return {"session_id": session_id, "mac": mac, "state": state,
            "targets": [{"dev": "sda", "bytes_written": 1, "bytes_total": 2,
                         "state": state}]}


def _session(server, sid: str, state: str = "open") -> str:
    """סבב אמיתי עם חבר אמיתי, ומחזיר את ה-MAC.

    ‏**דרך ה-fixture ולא ב-INSERT ידני:** ‏`sessions` נשען על מפתחות
    זרים ל-`groups` ול-`images`, וטסט שמזריק מזהים מומצאים נופל על
    האילוץ ולא על הקוד שנבדק.
    """
    info = setup_classroom(server)
    conn = server["app"].state.ctx.conn
    # ⚠️ **אין טבלת `images`** — הדיסק הוא מקור האמת לאימג'ים (עיקרון 3),
    # וה-DB מחזיק רק מה שאין לו ייצוג טבעי כקבצים. המזהה נלקח מהספרייה.
    image_id = server["admin"].get("/api/console/images").json()[0]["id"]
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at, kind)"
        " VALUES (?, ?, ?, 'ROOM', 2, 300, ?, 'nadav', ?, 0.0, 'multicast')",
        (sid, info["group"], image_id, state, "2026-09-08T00:00:00+00:00"))
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, done,"
        " bytes_written, bytes_total, updated_at)"
        " VALUES (?, ?, 'writing', 0, 0, 0, ?)",
        (sid, info["mac1"], "2026-09-08T00:00:00+00:00"))
    conn.commit()
    return info["mac1"]


# --- צד השרת -----------------------------------------------------------------


def test_a_report_to_an_open_session_is_accepted(server):
    """רדיוס הפגיעה. בלי זה התיקון היה חוסם גם את המסלול התקין."""
    mac = _session(server, "ses_open")
    conn = server["app"].state.ctx.conn
    assert reports.ingest(conn, _payload("ses_open", mac))["ok"] is True


def test_a_report_to_a_closed_session_is_refused(server):
    """**הפגם עצמו.** עד #557 זה החזיר `{"ok": True}`."""
    mac = _session(server, "ses_shut", state="closed")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, _payload("ses_shut", mac))
    assert answer["ok"] is False, answer
    assert answer["code"] == "not_open", answer


def test_a_report_to_a_session_that_never_existed_is_refused(server):
    """מזהה שהומצא אינו "לא חבר" — הסבב עצמו איננו."""
    mac = _session(server, "ses_real")
    conn = server["app"].state.ctx.conn
    answer = reports.ingest(conn, _payload("ses_ghost", mac))
    assert answer["code"] == "not_open", answer


def test_the_refusal_is_journalled(server):
    """בלי רישום, אלפי הבקשות של 08/09 היו נשארות בלתי-נראות."""
    mac = _session(server, "ses_shut2", state="closed")
    conn = server["app"].state.ctx.conn
    reports.ingest(conn, _payload("ses_shut2", mac))
    events = [e["event"] for e in
              server["admin"].get("/api/console/journal").json()]
    assert "report_on_closed_session" in events, events


def test_a_closed_session_is_not_modified_by_the_refused_report(server):
    """הסירוב אינו רק תשובה — הוא גם אי-כתיבה.

    דיווח `done` על סבב סגור **אסור** שיסמן חבר כגמור: זה היה
    מזייף השלמה שלא קרתה.
    """
    mac = _session(server, "ses_shut3", state="closed")
    conn = server["app"].state.ctx.conn
    reports.ingest(conn, _payload("ses_shut3", mac, state="done"))
    row = conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ?",
        ("ses_shut3",)).fetchone()
    assert row["state"] == "writing", dict(row)
    assert row["done"] == 0, dict(row)


@pytest.mark.parametrize("exists", [True, False])
def test_not_open_body_survives_curl_fail_http_contract(server, exists):
    """curl -f suppresses 4xx bodies: ingest-only tests missed this regression."""
    mac = _session(server, "ses_http_closed", state="closed")
    sid = "ses_http_closed" if exists else "ses_http_missing"
    response = server["anon"].post(
        "/api/v1/agent/progress", json=_payload(sid, mac))
    assert response.status_code == 200, response.text
    assert response.json()["code"] == "not_open"
    assert response.json()["ok"] is False


def test_invalid_progress_still_returns_http_400(server):
    response = server["anon"].post("/api/v1/agent/progress", json={})
    assert response.status_code == 400
    assert response.json()["ok"] is False
