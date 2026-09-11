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

from .db import journal, now_iso, update_one

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

    # ‏#557: סבב שנסגר אינו מקבל דיווחים. עד כאן הוא קיבל — והחזיר
    # `200 OK` — ולכן `progress_loop` בסוכן, שלולאתו אינסופית ובולעת
    # את התשובה ב-`|| true`, המשיך לדווח עליו **לנצח**. נמדד 08/09:
    # שתי מכונות דיווחו על `ses_277091d3` במשך 50 דקות אחרי שנסגר,
    # אלפי בקשות — **ולכן לא הצטרפו לגל הבא.**
    #
    # זה בדיוק מה ש-#535 תיקן למשימות; מסלול הסבב לא קיבל את אותו
    # טיפול. אותה תשובה בשם, ואותו רישום ביומן.
    session = conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if session is None or session["state"] == "closed":
        journal(conn, "report_on_closed_session", f"{mac} for {session_id}{sent_as}")
        return {"ok": False, "error": "this session is no longer open",
                "code": "not_open"}

    row = conn.execute(
        "SELECT state, done, targets_json FROM session_members"
        " WHERE session_id = ? AND mac = ?",
        (session_id, mac),
    ).fetchone()
    if row is None:
        # מדווח שאינו חבר — לא מפוצצים, אבל גם לא סופרים אותו בשקט.
        # ‏`sent_as` נכנס כדי שהיומן יאבחן ולא יסתיר: הצורה הקנונית היא
        # מה שמחפשים בטבלה, והצורה שנשלחה היא מה שמסביר לקוח חריג.
        journal(conn, "report_from_nonmember", f"{mac} for {session_id}{sent_as}")
        return {"ok": False, "error": "not a member of this session", "code": "not_member"}

    targets = stamp_movement(row["targets_json"], targets)
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


def stamp_movement(previous_json: str | None, targets: list) -> list:
    """מוסיף לכל יעד `moved_at` — הפעם האחרונה ש**הבייטים שלו גדלו**.

    ⚠️ **‏`updated_at` אינו התקדמות, וזה נמדד ולא הונח.** בסבב 08/09
    המגירה שמתה הוסיפה לרענן את החותמת שלה כל 5 שניות במשך 32 דקות
    בזמן שהמונה שלה קפא — כלומר סימון "תקוע" שנשען על זמן הדיווח
    **לעולם לא היה נדלק על המגירה היחידה שבאמת הייתה תקועה.**

    לכן החותמת כאן זזה על ראיה חיובית אחת בלבד: מספר שגדל. יעד חדש,
    יעד שהמספר שלו **ירד** (דיווח שחזר אחורה — אין לו פרשנות אחרת
    חוץ מ"התחלנו מחדש"), ויעד שאין לו מספר קודם — כולם מקבלים `now`,
    כי אף אחד מהם אינו ראיה לקיפאון.
    """
    was = {}
    try:
        for t in json.loads(previous_json or "[]"):
            if isinstance(t, dict) and t.get("dev"):
                was[t["dev"]] = t
    except (TypeError, ValueError):
        # ‏`targets_json` פגום אינו "אין תנועה" — הוא "לא ידענו".
        # עיקרון 5: החותמת נדחפת קדימה ולא נשארת ישנה, אחרת שדה שבור
        # במסד היה מדליק אזעקת קיפאון על כל המגירות.
        was = {}
    stamped = []
    for t in targets:
        if not isinstance(t, dict):
            continue
        before = was.get(t.get("dev"))
        if (before is not None
                and _int(t.get("bytes_written")) == _int(before.get("bytes_written"))
                and before.get("moved_at")):
            t = {**t, "moved_at": before["moved_at"]}
        else:
            t = {**t, "moved_at": now_iso()}
        stamped.append(t)
    return stamped


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
    # ‏#535: תביעת מצב ב-`WHERE`. עד כאן דיווח `failed` הפך משימה
    # ל-`failed` **בלי קשר למצבה** — גם `done`, גם `cancelled` —
    # ובלי אימות מי הדווח. מכאן, משימה שאינה פתוחה אינה משתנה,
    # והדיווח מקבל תשובה שאומרת את זה בשמה.
    if not update_one(
        conn,
        # COALESCE: דיווח failed של הסוכן בלי שגיאות-יעד לא דורס סיבה
        # שהשרת כבר רשם (דחיית מניפסט, למשל) — error נשאר עם ההסבר.
        "UPDATE tasks SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = COALESCE(?, error), updated_at = ?"
        " WHERE id = ? AND state IN ('pending', 'running')",
        (new_state, written, total, errors or None, now_iso(), task_id),
    ):
        journal(conn, "report_on_closed_task", f"{mac} for {task_id}{sent_as}")
        return {"ok": False, "error": "the task is no longer open",
                "code": "not_open"}
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
