"""קליטת דיווחי ההתקדמות — ממשק 4.

הדיווח מגיע כל 2 שניות מכל מכונה כותבת. נשמר מצב אחרון לכל חבר סבב,
כדי שהקונסולה תציג התקדמות חיה; מעברים חשובים (done, failed) נרשמים
ביומן פעם אחת, לא כל 2 שניות.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from boot.grub_menu import normalize_mac as lenient_mac

from .db import journal, now_iso

log = logging.getLogger("imagectl.reports")

#: כמה מהמחרוזת הגולמית נכנס ליומן ולפלט. ‏`lenient_mac` מוריד כל תו
#: שאינו הקסה, ולכן גוף עוין יכול לעטוף 12 ספרות בכמה שירצה — היומן
#: מראה את מה שנשלח, לא מאחסן אותו לפי אורכו.
_RAW_SHOWN = 32

#: המצבים שבהם המכונה סיימה את חלקה — יש מה לרשום ביומן, ואין מה לחכות
#: לו יותר (ממשק 4). ‏`partial` הוא השלישי שבהם (#67): חלק מהמגירות
#: נכתבו וחלק נכשלו. הוא מסיים כמו `done` — אחרת גל שלם היה נתקע על
#: מכונה שכבר אמרה את דברה — אבל **אינו** מדליק את `done`, שהוא הראיה
#: החיובית להצלחה מלאה ומכאן הלאה נספר ככזו (עיקרון 5).
TERMINAL = ("done", "failed", "partial")


def ingest(conn: sqlite3.Connection, payload: dict) -> dict:
    session_id = payload.get("session_id")
    task_id = payload.get("task_id")
    raw_mac = payload.get("mac")
    state = payload.get("state", "")
    targets = payload.get("targets")
    if not raw_mac or not isinstance(targets, list) or not (session_id or task_id):
        return {"ok": False, "error": "missing session_id/task_id, mac or targets",
                "code": "bad_report"}

    # ‏MAC מנורמל לפני כל נגיעה ב-DB — אותה פונקציה שבה משתמשים hello
    # ו-pulls, כי הן אלה שיצרו את השורה שאנחנו מחפשים. הצד השני של
    # ההשוואה קנוני לפי הסכימה (`db.py`: "קנוני: lowercase עם נקודתיים"),
    # והשוואה גולמית מולו החזירה "אינה חברה" על מכונה שכן חברה (#108).
    mac = lenient_mac(raw_mac)
    if mac is None:
        # עיקרון 5: "לא הצלחנו לקרוא את המזהה" ו"המכונה אינה חברה" הם
        # שני מצבים שונים. קלט שאינו MAC אינו טענה על חברוּת, ולכן הוא
        # מקבל קוד משלו ואינו נרשם כדיווח-מחוץ-לסבב.
        log.warning("progress report with a malformed mac: %r",
                    str(raw_mac)[:_RAW_SHOWN])
        return {"ok": False, "error": "missing or malformed mac", "code": "bad_mac"}
    sent_as = _sent_as(raw_mac, mac)

    if task_id:
        return _ingest_task(conn, task_id, mac, state, targets, sent_as)

    row = conn.execute(
        "SELECT state, done FROM session_members WHERE session_id = ? AND mac = ?",
        (session_id, mac),
    ).fetchone()
    if row is None:
        # מדווח שאינו חבר — לא מפוצצים, אבל גם לא סופרים אותו בשקט.
        # ‏`sent_as` נכנס כדי שהיומן יאבחן ולא יסתיר: הצורה הקנונית היא
        # מה שמחפשים בטבלה, והצורה שנשלחה היא מה שמסביר לקוח חריג.
        journal(conn, "report_from_nonmember", f"{mac} for {session_id}{sent_as}")
        return {"ok": False, "error": "not a member of this session", "code": "not_member"}

    bytes_written = sum(_int(t.get("bytes_written")) for t in targets)
    bytes_total = sum(_int(t.get("bytes_total")) for t in targets)
    errors = "; ".join(
        f"{t.get('dev', '?')}: {t['error']}" for t in targets if t.get("error")
    )

    previous = row["state"]
    conn.execute(
        "UPDATE session_members SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = ?, done = ?, targets_json = ?, updated_at = ?"
        " WHERE session_id = ? AND mac = ?",
        (
            state, bytes_written, bytes_total, errors or None,
            1 if state == "done" else row["done"],
            json.dumps(targets), now_iso(), session_id, mac,
        ),
    )
    conn.commit()

    if state != previous and state in TERMINAL:
        journal(conn, f"client_{state}", f"{mac} in {session_id}" + (f" — {errors}" if errors else ""))
    return {"ok": True}


def _ingest_task(conn: sqlite3.Connection, task_id: str, mac: str,
                 state: str, targets: list, sent_as: str = "") -> dict:
    """דיווח קליטה: יעד אחד — דיסק המקור — ומצב על המשימה עצמה.

    ‏`mac` כאן כבר קנוני (‏`ingest` מנרמל): ‏`tasks.mac` נכתב דרך
    ‏`registry.normalize_mac` בפתיחת המשימה, והשוואה גולמית מולו נשברת
    בדיוק כמו ב-`session_members` (#108).
    """
    row = conn.execute(
        "SELECT state FROM tasks WHERE id = ? AND mac = ?", (task_id, mac)
    ).fetchone()
    if row is None:
        journal(conn, "report_from_nonmember", f"{mac} for {task_id}{sent_as}")
        return {"ok": False, "error": "not this machine's task", "code": "not_member"}

    written = sum(_int(t.get("bytes_written")) for t in targets)
    total = sum(_int(t.get("bytes_total")) for t in targets)
    errors = "; ".join(f"{t.get('dev', '?')}: {t['error']}"
                       for t in targets if t.get("error"))
    # מצב 'done' נקבע בשרת כשהמניפסט מתקבל ומאומת, לא לפי הצהרת הסוכן.
    new_state = "failed" if state == "failed" else (
        "running" if row["state"] == "pending" else row["state"]
    )
    conn.execute(
        # COALESCE: דיווח failed של הסוכן בלי שגיאות-יעד לא דורס סיבה
        # שהשרת כבר רשם (דחיית מניפסט, למשל) — error נשאר עם ההסבר.
        "UPDATE tasks SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = COALESCE(?, error), updated_at = ? WHERE id = ?",
        (new_state, written, total, errors or None, now_iso(), task_id),
    )
    conn.commit()
    if state == "failed" and row["state"] != "failed":
        journal(conn, "capture_failed", f"{task_id} {errors or 'agent reported failure'}")
    return {"ok": True}


def _sent_as(raw: object, mac: str) -> str:
    """הצורה שנשלחה, רק כשהיא נבדלת מהקנונית — אחרת מחרוזת ריקה.

    ‏`_MAC` ב-`journal_he` מתרגם רק את הצורה הקנונית לשם המחשב, ולכן
    הצורה הגולמית נשארת קריאה כפי שהיא לצד השם.
    """
    text = raw if isinstance(raw, str) else str(raw)
    return "" if text == mac else f" (sent as {text[:_RAW_SHOWN]})"


def _int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0
