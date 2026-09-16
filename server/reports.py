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

from . import disk_failures
from .db import journal, now_iso, update_one
from .tasks import OPEN_STATES, claim_task

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

#: כל מצב שהסוכן מדווח בו על עצמו (`build_progress` ב-`progress.sh`,
#: השדה `state` העליון). מקורם: ‏`waiting` (ברירת המחדל), ‏`writing`,
#: ‏`verifying`, ‏`naming`, ‏`done`, ‏`failed`, ‏`partial` במסלול הסבב/
#: המגירות, ו-`capturing`/`failed` במסלול הקליטה. ‏#772: מצב שאינו כאן
#: נדחה במקום להיכתב בשקט — דיווח בלי `state` (שהופך ל-`""`) או עם ערך
#: שרירותי היה מציב מצב לא-טרמינלי לנצח ותוקע את הגל (עיקרון 5).
VALID_STATES = frozenset(
    {"waiting", "writing", "verifying", "naming", "staging", "capturing"} | set(TERMINAL)
)

#: ‏#720: מצבי ה-staging של הדרייברים בדיווח הסיום (`drivers.state`).
#: ‏`skipped` = לא יעד Windows; ‏`no_match` = יש מלאי ואין חבילה; ‏`failed`
#: = ניסינו ולא הצלחנו (הסיבה ב-`error`) — והשחזור עצמו `done` בכל מקרה.
DRIVER_STATES = frozenset({"staged", "no_match", "skipped", "failed"})


def well_formed_drivers(value: object) -> dict | None:
    """השדה `drivers` של הדיווח בצורתו התקנית, או ``None`` — שדה חסר או
    פגום אינו נכתב (ואינו דורס ערך קודם), כמו כל שדה לא ידוע."""
    if not isinstance(value, dict) or value.get("state") not in DRIVER_STATES:
        return None
    packages = value.get("packages")
    if not isinstance(packages, list) or not all(isinstance(p, str) for p in packages):
        return None
    out: dict = {"state": value["state"], "packages": packages[:64]}
    for key in ("error", "reason"):
        if isinstance(value.get(key), str):
            out[key] = value[key][:300]
    return out

#: ‏`lost` — השרת קבע שחבר לא דיווח כלל מעל הספָּף. ‏**לא** `done` (אין
#: ראיה שכתב) ו**לא** `failed` (הוא מעולם לא דיווח כישלון): פשוט לא ידוע
#: מה קרה לו (#450). זהו מצב ש**השרת** קובע ב-`room.tick`, ולכן הוא
#: **אינו** ב-VALID_STATES — סוכן אינו יכול לטעון אותו על עצמו (#772).
LOST = "lost"

#: החברים שהגל אינו ממתין להם עוד: הטרמינליים שהסוכן מדווח, ועוד `lost`
#: שהשרת קובע. ‏`tick` בודק מול זה, לא מול `TERMINAL` — מכונה שנעלמה
#: לעולם אינה מדווחת מצב סופי, ובלי `lost` הגל היה תקוע לנצח (#450).
FINAL = frozenset({*TERMINAL, LOST})


def ingest(conn: sqlite3.Connection, payload: dict,
           token: str | None = None) -> dict:
    """‏`token` — כותרת `X-Imagectl-Task-Token` של הבקשה, אם נשלחה.
    נבדק **רק** במסלול המשימה (#855); לסבב אין אסימון."""
    session_id = payload.get("session_id")
    task_id = payload.get("task_id")
    raw_mac = payload.get("mac")
    state = payload.get("state", "")
    targets = payload.get("targets")
    if not raw_mac or not isinstance(targets, list) or not (session_id or task_id):
        return {"ok": False, "error": "missing session_id/task_id, mac or targets",
                "code": "bad_report"}

    # ‏#772: המצב מאומת מול הקבוצה המוכרת לפני כל כתיבה. ערך ריק או לא
    # מוכר אינו "התקדמות שלא זיהינו" אלא קלט פגום — הוא נדחה בשמו ואינו
    # נכתב, אחרת מצב החבר נהיה `""`, לעולם אינו טרמינלי, ו-tick נתקע.
    if state not in VALID_STATES:
        return {"ok": False, "error": f"unknown state: {state!r}",
                "code": "bad_state"}

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
        return _ingest_task(conn, task_id, mac, state, targets, sent_as, token)

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

    # ‏#771: מצב טרמינלי הוא בלתי-משתנה. חבר שכבר `done`/`failed`/`partial`
    # סיים את חלקו — יש לו ראיה חיובית להשלמה — ודיווח נוסף idempotent:
    # לא דורסים state ולא targets_json. בלי השומר הזה `done` כפול עם
    # `targets=[]` היה מוחק את targets_json → `_tally` סופר 0 מגירות
    # שהושלמו → גל שיכפול נוסף (re-image!); ו-`writing` מאוחר היה מרגרס
    # את `done` ל-לא-טרמינלי → תוקע את הגל. מחזירים ok בלי לסגת (עיקרון 5).
    if row["state"] in TERMINAL:
        return {"ok": True}

    targets = stamp_movement(row["targets_json"], targets)
    bytes_written = sum(_int(t.get("bytes_written")) for t in targets)
    bytes_total = sum(_int(t.get("bytes_total")) for t in targets)
    errors = "; ".join(
        f"{t.get('dev', '?')}: {t['error']}" for t in targets if t.get("error")
    )
    # ‏#720: תוצאת ה-staging — נכתבת כשהגיעה, ונשארת (COALESCE) כשלא.
    drivers = well_formed_drivers(payload.get("drivers"))

    previous = row["state"]
    conn.execute(
        "UPDATE session_members SET state = ?, bytes_written = ?, bytes_total = ?,"
        " error = ?, done = ?, targets_json = ?, updated_at = ?,"
        " drivers_json = COALESCE(?, drivers_json)"
        " WHERE session_id = ? AND mac = ?",
        (
            state, bytes_written, bytes_total, errors or None,
            1 if state == "done" else row["done"],
            json.dumps(targets), now_iso(),
            json.dumps(drivers, ensure_ascii=False) if drivers else None,
            session_id, mac,
        ),
    )
    conn.commit()

    if state != previous and state in TERMINAL:
        journal(conn, f"client_{state}", f"{mac} in {session_id}" + (f" — {errors}" if errors else ""))
        # ‏#720: "הושלם, דרייברים לא הונחו" הוא מצב שהמפעיל צריך לראות ביומן,
        # לא רק בכרטיס — פעם אחת, עם המעבר הטרמינלי.
        if drivers and drivers["state"] == "failed":
            journal(conn, "drivers_failed",
                    f"{mac} in {session_id} — {drivers.get('error', '?')}")
        elif drivers and drivers["state"] == "staged":
            journal(conn, "drivers_staged",
                    f"{mac} in {session_id} — {', '.join(drivers['packages'])}")
    # ‏#874: יעד שנכשל בכתיבה נכנס לזיכרון הכשלים (סידורי + חריץ + שורות
    # ה-ATA). פעם אחת ליעד — הדיווח חוזר כל 2 שניות, ו-`record` מתעלם מכפול.
    for t in targets:
        if t.get("state") == "failed":
            disk_failures.record(conn, session_id, mac, t)
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
                 state: str, targets: list, sent_as: str = "",
                 token: str | None = None) -> dict:
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
    # ‏#855: משימה **פתוחה** משתנה רק בידי מי שמחזיק את האסימון שלה —
    # אותו אסימון שההעלאה דורשת (`capture.task_for`). עד כאן `task_id`
    # (65,536 ניחושים) ו-MAC (מתפרסם ב-ARP) הספיקו לדיווח `failed`
    # שהרג קליטה חיה. הסדר מכוון: משימה שכבר נסגרה עונה `not_open`
    # כמו היום, גם בלי אסימון — זה מה ש-`report_final` של הסוכן מקבל
    # אחרי שהמניפסט סגר את המשימה (#127), ואסור שייהפך ל-403.
    if row["state"] in OPEN_STATES and claim_task(conn, task_id, token) is None:
        journal(conn, "task_token_refused", f"{task_id} progress from {mac}{sent_as}")
        return {"ok": False, "error": "this is not your task", "code": "bad_token"}

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
        " error = COALESCE(?, error), targets_json = ?, updated_at = ?"
        " WHERE id = ? AND state IN ('pending', 'running')",
        (new_state, written, total, errors or None, json.dumps(targets),
         now_iso(), task_id),
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
