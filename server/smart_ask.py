"""שאלת ה-SMART של מחשב שיכפול — ותשובה מהקונסולה (#989 סעיף 2).

מכונה שדיסק שלה כתום/אדום שואלת את האדם ליד המסך: החלף / כתוב בכל זאת / דלג.
עד כאן השאלה נענתה **רק** ליד המכונה. הכרעת נדב (v1): אפשר גם מהקונסולה —
**התשובה הראשונה גוברת**, והצד השני רואה שכבר נענה.

המנגנון, בלי ערוץ חדש מהשרת אל המכונה (כמו `power_action`, #980):

* **השאלה** נוסעת ב-hello של הדופק (`ask`: nonce, port, verdict, reason) —
  רק כשהמסך הגרפי של המכונה חי ויכול לקבל החלטה. שאלה בתפריט טקסט (אין
  גואי) אינה מוצעת כאן, כי תשובה מרחוק לא הייתה מגיעה לשום קורא.
* **תשובת קונסולה** (`POST /api/console/room/answer`) נרשמת בשורה, והסוכן
  מקבל אותה בתשובת ה-hello הבא ב-`ask_answer`.
* **תשובה ליד המכונה** נשלחת בהרשאה: ה-hello הבא נושא `ask_answered`, והשרת
  מכריע — מי שהגיע ראשון, באותו `UPDATE` מותנה. התשובה לסוכן היא **התשובה
  המנצחת**, וזו שהוא מבצע. שרת שלא ענה — ההחלטה המקומית נשארת (שיכפול לא
  נעצר כי השרת למטה).

אדום (`failed_last`) אין לו "כתוב בכל זאת": השרת מסרב ל-`rescue` עליו (400),
כמו התפריט ליד המכונה. תשובה שלא נקראה אינה "המשך" שקט (עיקרון 5) — כל
מצב סירוב אומר למפעיל למה.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from .db import _write_lock, journal, now_iso, writing

ANSWERS = ("replace", "rescue", "skip")
VERDICTS = ("warn", "fail", "failed_last")
#: כמה זמן שאלה שנענתה נשארת בתצוגה ("נענתה ע"י …") לפני שנעלמת.
ANSWERED_SHOWN_SECONDS = 300
REASON_MAX = 200
NONCE_CHARS = "0123456789-"


class AskError(Exception):
    """סירוב עם קוד HTTP והסבר למפעיל."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def well_formed_ask(raw) -> dict | None:
    """השאלה כפי שהסוכן שולח; כל צורה אחרת נזנחת (= "אין שאלה")."""
    if not isinstance(raw, dict):
        return None
    nonce, port = raw.get("nonce"), raw.get("port")
    verdict, reason = raw.get("verdict"), raw.get("reason")
    if (not isinstance(nonce, str) or not nonce or len(nonce) > 24
            or any(c not in NONCE_CHARS for c in nonce)):
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 64:
        return None
    if verdict not in VERDICTS:
        return None
    reason = reason.strip()[:REASON_MAX] if isinstance(reason, str) else ""
    return {"nonce": nonce, "port": port, "verdict": verdict, "reason": reason}


def well_formed_claim(raw) -> tuple[str, str] | None:
    """`ask_answered` של הסוכן → (nonce, answer), או None."""
    if not isinstance(raw, dict):
        return None
    nonce, answer = raw.get("nonce"), raw.get("answer")
    if not isinstance(nonce, str) or not nonce or answer not in ANSWERS:
        return None
    return nonce, answer


def _label(by: str | None) -> str:
    if not by:
        return "גורם לא ידוע"
    if by == "machine":
        return "המכונה (ליד המסך)"
    return f"הקונסולה ({by.split(':', 1)[1]})" if by.startswith("console:") else by


def _claim(conn: sqlite3.Connection, mac: str, nonce: str, answer: str, by: str) -> bool:
    """הכרעה אטומית: רק מי שמצא את השורה פתוחה, עם ה-nonce הזה, זוכה."""
    with _write_lock, writing(conn):
        cur = conn.execute(
            "UPDATE smart_asks SET answer = ?, answered_by = ?, answered_at = ?"
            " WHERE mac = ? AND nonce = ? AND answer IS NULL",
            (answer, by, now_iso(), mac, nonce))
        return cur.rowcount == 1


def _row(conn: sqlite3.Connection, mac: str):
    return conn.execute("SELECT * FROM smart_asks WHERE mac = ?", (mac,)).fetchone()


def note_hello(conn: sqlite3.Connection, mac: str, ask: dict | None,
               claim: tuple[str, str] | None) -> dict | None:
    """מה שה-hello אומר על השאלה, ומה שהוא מקבל בחזרה.

    מחזיר `{"nonce", "answer"}` — התשובה שהסוכן צריך לבצע — או None.
    """
    row = _row(conn, mac)
    if claim is not None and row is not None and row["nonce"] == claim[0]:
        # ההחלטה המקומית — מנצחת רק אם איש לא ענה לפניה. ראשונה, לפני כל
        # ניקוי למטה: ה-hello הזה אינו נושא `ask`, וזה לא "המכונה הפסיקה לשאול".
        if _claim(conn, mac, claim[0], claim[1], "machine"):
            journal(conn, "smart_answered", f"{mac} {row['port']} {claim[1]} machine")
        row = _row(conn, mac)
    if ask is not None:
        # nonce נשען על מונה בקובץ ב-tmpfs — אחרי אתחול הוא חוזר ל-1. שורה
        # שנסגרה (hello בלי שאלה אחריה) אינה מתאימה לשאלה חדשה עם אותו nonce.
        if row is None or row["nonce"] != ask["nonce"] or row["closed"]:
            with _write_lock, writing(conn):
                conn.execute(
                    "INSERT INTO smart_asks (mac, nonce, port, verdict, reason, asked_at)"
                    " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (mac) DO UPDATE SET"
                    " nonce = excluded.nonce, port = excluded.port, verdict = excluded.verdict,"
                    " reason = excluded.reason, asked_at = excluded.asked_at,"
                    " answer = NULL, answered_by = NULL, answered_at = NULL, closed = 0",
                    (mac, ask["nonce"], ask["port"], ask["verdict"], ask["reason"], now_iso()))
            row = _row(conn, mac)
    elif row is not None and row["answer"] is None:
        # המכונה כבר לא שואלת (אתחול, המסך נפל, השאלה נסגרה בלי תשובה
        # שהגיעה אלינו): שאלה פתוחה שאין מי שיענה עליה אינה מוצגת.
        with _write_lock, writing(conn):
            conn.execute("DELETE FROM smart_asks WHERE mac = ? AND nonce = ? AND answer IS NULL",
                         (mac, row["nonce"]))
        row = None
    elif row is not None and not row["closed"]:
        with _write_lock, writing(conn):          # נענתה, והמכונה כבר ממשיכה הלאה
            conn.execute("UPDATE smart_asks SET closed = 1 WHERE mac = ?", (mac,))
        row = _row(conn, mac)
    if row is not None and row["answer"] is not None and (
            (ask is not None and ask["nonce"] == row["nonce"])
            or (claim is not None and claim[0] == row["nonce"])):
        return {"nonce": row["nonce"], "answer": row["answer"]}
    return None


def view(conn: sqlite3.Connection, mac: str, now: datetime | None = None) -> dict | None:
    """השאלה כפי שהקונסולה מציגה: פתוחה, או שנענתה לאחרונה (עם מי ענה)."""
    row = _row(conn, mac)
    if row is None:
        return None
    if row["answer"] is not None:
        try:
            age = (now or datetime.now(timezone.utc)) - datetime.fromisoformat(row["answered_at"])
        except (TypeError, ValueError):
            return None
        if age > timedelta(seconds=ANSWERED_SHOWN_SECONDS) or age < timedelta(seconds=-5):
            return None
    return {"nonce": row["nonce"], "port": row["port"], "verdict": row["verdict"],
            "reason": row["reason"], "asked_at": row["asked_at"], "answer": row["answer"],
            "answered_by": _label(row["answered_by"]) if row["answer"] is not None else None,
            "answered_at": row["answered_at"]}


def answer_from_console(conn: sqlite3.Connection, mac: str, nonce, answer, user: str) -> dict:
    """`POST /api/console/room/answer` — התשובה הראשונה גוברת."""
    if answer not in ANSWERS:
        raise AskError(400, "תשובה לא מוכרת — החלף / כתוב בכל זאת / דלג")
    if not isinstance(nonce, str) or not nonce:
        raise AskError(400, "חסר מזהה השאלה")
    row = _row(conn, mac)
    if row is None:
        raise AskError(404, "למכונה הזו אין שאלה פתוחה")
    if row["nonce"] != nonce:
        raise AskError(409, "השאלה התחלפה — רעננו וענו על הנוכחית")
    if row["answer"] is not None:
        raise AskError(409, f'כבר נענה ע"י {_label(row["answered_by"])}')
    if row["verdict"] == "failed_last" and answer == "rescue":
        raise AskError(400, "דיסק אדום — אין \"כתוב בכל זאת\": החלף או המשך בלעדיו")
    if not _claim(conn, mac, nonce, answer, f"console:{user}"):
        fresh = _row(conn, mac)       # נענתה בין הקריאה לעדכון
        raise AskError(409, f'כבר נענה ע"י {_label(fresh["answered_by"] if fresh else None)}')
    journal(conn, "smart_answered", f"{mac} {row['port']} {answer} console:{user}", user)
    return {"ok": True, "answer": answer}
