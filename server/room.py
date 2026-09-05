"""סבב חדר השיכפולים — הפצה מצטברת על פני גלים (אפיון סעיף 29).

הסבב חי בשרת ומצהיר יעד סך-כוננים. כל גל הוא session רגיל על קבוצת
השיכפול — אותו מנוע שידור, אותם דיווחי התקדמות. מה שמייחד את החדר:

- המוכנות נמדדת בכוננים (מגירות), לא במחשבים. גל יוצא כשהמגירות
  הטריות מכסות את היתרה, או בלחיצת "התחל עכשיו". בלי טיימר —
  בחדר עומד אדם ליד המכונות.
- מגירה מזוהה ב-serial של הכונן: כונן שכבר נכתב בסבב לא נספר שוב,
  ומכונה שהמגירות שלה לא הוחלפו לא מצטרפת לגל הבא.
- כשגל מסתיים והיעד לא הושג, הגל הבא נפתח אוטומטית — "כיבוי,
  החלפת מגירות, הדלקה — סבב שני מתחיל אוטומטית".

הערה: אין כאן `from .api import ServerContext` — זה היה סוגר מעגל
(api → hello → room). ctx מגיע כפרמטר ומשמש כפי שהוא.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, reports
from .db import journal, now_iso, update_one
from .images import restore_refusal
from .sessions import SessionError, SessionStore, SessionSuperseded

log = logging.getLogger("imagectl.room")

CLONERS_GROUP = "grp_CLONERS"
#: מי רשאי להפעיל את חדר השיכפולים. רשימת-היתר מפורשת, ולא "מחובר".
#:
#: אותה הכרעה כמו ``station.ROUND_OPENER_ROLES`` (#94), ובאותו נימוק:
#: היום זה כל התפקידים שקיימים ולכן זה לא משנה התנהגות, אבל הקוד שלף
#: את ``admin_only`` ומעולם לא בדק דבר — ותפקיד שלישי (צופה, מבקר,
#: חשבון ניטור) היה מקבל ביום היוולדו את ``/wake`` ו-``/start``, שהם
#: הפעולה שדורסת כל מגירה מחוברת בחדר. "לא ידענו מה התפקיד" הוא סירוב.
#:
#: ``GET`` נשאר פתוח לכל מחובר — קריאת מצב אינה הרסנית.
ROOM_OPERATOR_ROLES = ("admin", "deploy")
#: מכונה שדיברה עם השרת בטווח הזה נחשבת ערה — הסוכן דוגם כל ~2 שניות.
AWAKE_SECONDS = 30


# --- שאילתות טהורות ----------------------------------------------------------


def active_round(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM room_rounds WHERE state = 'active' LIMIT 1"
    ).fetchone()


def _written(round_row: sqlite3.Row) -> set[str]:
    return set(json.loads(round_row["written_serials"]))


def _disks(conn: sqlite3.Connection, mac: str) -> list[dict]:
    row = conn.execute(
        "SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or not row["disks_json"]:
        return []
    disks = json.loads(row["disks_json"])
    return disks if isinstance(disks, list) else []


def fresh_serials(conn: sqlite3.Connection, mac: str, written: set[str]) -> list[str]:
    """המגירות של המכונה שעוד לא נכתבו בסבב הנוכחי."""
    return [
        d["serial"] for d in _disks(conn, mac)
        if isinstance(d, dict) and d.get("serial") and d["serial"] not in written
    ]


def has_fresh_drawers(conn: sqlite3.Connection, mac: str) -> bool:
    """שער ההצטרפות לגל: מכונה שהמגירות שלה כבר נכתבו נשארת בהמתנה.

    בלי סבב חדר פעיל אין מה לסנן — מצטרפים כרגיל.
    """
    round_row = active_round(conn)
    if round_row is None:
        return True
    return bool(fresh_serials(conn, mac, _written(round_row)))


def drawer_list(conn: sqlite3.Connection, mac: str, written: set[str],
                member: sqlite3.Row | None = None) -> list[dict]:
    """המגירות של המכונה כפי שהטכנאי רואה אותן: חריץ, התקן ומצב (#27).

    ‏`port` הוא ה-ataN שהסוכן דיווח בממשק 2 — החריץ הפיזי, לא סדר הגילוי
    של הקרנל. סוכן ישן, VM או בקר לא-ATA לא מדווחים אותו, והוא יוצא `null`:
    הקונסולה נופלת חזרה לתצוגה לפי שם ההתקן, בלי להיכשל (עיקרון 1).
    """
    targets = {}
    if member is not None:
        targets = {t.get("dev"): t
                   for t in json.loads(member["targets_json"] or "[]")
                   if isinstance(t, dict)}
    drawers = []
    for disk in _disks(conn, mac):
        if not isinstance(disk, dict):
            continue
        port = disk.get("port")
        target = targets.get(disk.get("dev")) or {}
        drawers.append({
            "dev": disk.get("dev"),
            "port": port if isinstance(port, int) and not isinstance(port, bool)
            else None,
            "fresh": bool(disk.get("serial")) and disk["serial"] not in written,
            "state": target.get("state"),
            "error": target.get("error"),
        })
    return drawers


def ready_drives(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row) -> int:
    """כמה כוננים טריים מחוברים למכונות שכבר הצטרפו לגל הפתוח."""
    written = _written(round_row)
    return sum(
        len(fresh_serials(conn, member["mac"], written))
        for member in store.members(round_row["wave_session_id"])
    )


# --- מחזור החיים -------------------------------------------------------------


def open_round(ctx, image_id: str, target_drives: int, user: str) -> dict:
    manifest = ctx.library.get(image_id)
    if manifest is None:
        raise ValueError("אימג' לא קיים בספרייה")
    # ‏#381: אימג' הקשור למכונה אחת אינו נשפך על מגירות. אין כאן רשימת
    # יעדים בכלל — הכוננים יותקנו במכונות שאיש עוד אינו יודע מי הן —
    # ולכן `restore_refusal` מסרב אותו, וזה הכיוון הנכון (עיקרון 5).
    refusal = restore_refusal(manifest, None)
    if refusal is not None:
        raise ValueError(refusal)
    if target_drives < 1:
        raise ValueError("יעד הכוננים חייב להיות חיובי")
    machines = ctx.conn.execute(
        "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
    ).fetchone()["n"]
    if machines == 0:
        raise ValueError("אין מחשבי שיכפול רשומים — רשמו אותם בקונסולה קודם")
    if active_round(ctx.conn) is not None:
        raise SessionError("כבר יש סבב חדר פעיל")

    # פתיחת הגל תופסת את חריץ הסבב היחיד במערכת ומעירה את החדר (WoL).
    wave_id = ctx.store.open(
        CLONERS_GROUP, image_id, prefix="ROOM",
        expected_clients=machines, opened_by=user,
    )
    round_id = "room_" + secrets.token_hex(4)
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state,"
        " wave_session_id, opened_by, created_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
        (round_id, image_id, target_drives, wave_id, user, now_iso()),
    )
    ctx.conn.commit()
    journal(ctx.conn, "room_open", f"{round_id} {image_id} target={target_drives}", user)
    return {"id": round_id, "wave_session_id": wave_id}


def close_round(ctx, user: str) -> None:
    round_row = active_round(ctx.conn)
    if round_row is None:
        raise SessionError("אין סבב חדר פעיל")
    wave = ctx.conn.execute(
        "SELECT id, state FROM sessions WHERE id = ?",
        (round_row["wave_session_id"],),
    ).fetchone()
    if wave is not None and wave["state"] in ("open", "running"):
        ctx.store.close(wave["id"], user)
    ctx.conn.execute(
        "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
        (now_iso(), round_row["id"]),
    )
    ctx.conn.commit()
    journal(ctx.conn, "room_close",
            f'{round_row["id"]} written={round_row["written_drives"]}'
            f'/{round_row["target_drives"]}', user)


def tick(conn: sqlite3.Connection, store: SessionStore) -> None:
    """מקדם את מכונת המצבים של הסבב. נקרא מכל hello של מחשב שיכפול
    ומכל משיכת מצב של המסך — אין לו תהליכון משלו."""
    round_row = active_round(conn)
    if round_row is None:
        return
    wave = None
    if round_row["wave_session_id"]:
        wave = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (round_row["wave_session_id"],)
        ).fetchone()

    if wave is None or wave["state"] == "closed":
        # הסבב פעיל והגל שלו כבר אינו. בלי הענף הזה זה מצב **יציב**:
        # כל דופק עתידי נוסג כאן, החדר נראה פתוח ואף גל אינו נפתח (#217).
        _resume(conn, store, round_row, wave)
    elif wave["state"] == "open":
        remaining = round_row["target_drives"] - round_row["written_drives"]
        ready = ready_drives(conn, store, round_row)
        if 0 < remaining <= ready:
            store.start_auto(wave["id"])
    elif wave["state"] == "running":
        members = store.members(wave["id"])
        # ‏`partial` הוא סיום לכל דבר (#67): מכונה שכתבה שתי מגירות מתוך
        # שלוש אמרה את דברה, והגל אינו ממתין לה עוד. הספירה שאחריו היא
        # ממילא לפי מגירה — המגירה שנכשלה פשוט לא נספרת ונשארת טרייה.
        if members and all(m["state"] in reports.TERMINAL for m in members):
            _finish_wave(conn, store, round_row, members)


#: תקרה לאורך פרט ביומן. הודעת חריגה יכולה לגרור traceback שלם, ושורת
#: יומן שאי אפשר לקרוא על המסך אינה מוסיפה למפעיל דבר.
_DETAIL_MAX = 200


def _one_line(value: object) -> str:
    """ערך כפי שמותר לכתוב אותו לשורת יומן או לוג: **שורה אחת**.

    **לא** מפני שהוא מזייף רשומה בטבלת היומן — הוא אינו יכול:
    ‏`journal()` מעביר את `detail` כפרמטר קשור לעמודת TEXT, ושורה חדשה
    בתוכו נשארת שדה אחד. בקרה שלילית שהייתה נשענת על "רשומה שנייה
    בטבלה" **הייתה נכשלת להיכשל**, וזה בדיוק סוג השומר שנראה עובד.

    שתי הסיבות האמיתיות:

    1. ‏`log.exception` הולך ליומן ה-systemd, שהוא **טקסטואלי**. שורה
       חדשה בהודעת חריגה מייצרת שם שורה שנראית עצמאית — כלומר רשומה
       מזויפת ביומן שהמפעיל קורא. זה אותו יומן שכבר נכווינו בו ב-#179.
    2. תקרת אורך: הודעת חריגה יכולה לגרור traceback שלם, ושדה שאי-אפשר
       לקרוא על המסך אינו מוסיף למפעיל דבר.

    ‏`normalize_mac` מחזיר hex קנוני או `None`, ולכן מסלול ה-MAC אינו
    ניתן לניצול ממילא. **הווקטור החי היחיד הוא טקסט החריגה** — והשמירה
    יושבת בנקודת הכתיבה, לא בהנחה על מי שקורא.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return " ".join(text.split())[:_DETAIL_MAX]


def pulse(conn: sqlite3.Connection, store: SessionStore, mac: str) -> bool:
    """הדופק של הסבב מתוך מסלול ה-hello — לעולם אינו מפיל אותו.

    אותה מוסכמה כמו `agent_loops.note`: הפונקציה לעולם אינה זורקת
    ולעולם אינה נוגעת בתשובה שנשלחת למכונה, ועיקרון 1 נשאר בדיוק כפי
    שהוא. ‏hello שמחזיר 500 אומר למחשב השיכפול שהשרת מת, וזה שקר —
    השרת חי, רק קידום הסבב נכשל (#177).

    **אבל לא בשקט (עיקרון 5).** גל שלא נפתח הוא בדיוק מה שהמפעיל חייב
    לראות: הסבב נראה תקוע והחדר ממתין לאדם שיבחין. לכן הכישלון נרשם
    ביומן — שם הוא מגיע למסך — וגם ב-`log.exception` עם ה-traceback.
    ‏`except: pass` היה הופך את הבאג לשקוף, וזה גרוע מ-500.

    מוחזר האם הדופק עבר, למי שכן רוצה לדעת.
    """
    who = _one_line(mac)
    try:
        tick(conn, store)
        return True
    except Exception as exc:      # noqa: BLE001 — קידום הסבב לא מפיל hello
        log.exception("room tick from %s failed", who)
        try:
            journal(conn, "room_tick_failed", f"{who} — {_one_line(exc)}")
        except Exception:         # noqa: BLE001 — גם היומן לא מפיל hello
            log.exception("room tick failure from %s was not journaled", who)
        return False


def _tally(conn: sqlite3.Connection, round_row: sqlite3.Row,
           members: list[sqlite3.Row]) -> tuple[int, set[str]]:
    """כמה מגירות נכתבו בסבב עד כה, ואילו — לפי serial.

    **הספירה חוזרת על עצמה בבטחה:** מגירה שכבר בקבוצה אינה נספרת שוב,
    ולכן ספירה חוזרת של אותו גל (במסלול השחזור של `_resume`) מחזירה
    בדיוק את אותו מספר. זה מה שמאפשר לשחזר בלי לדעת אם הספירה הקודמת
    הספיקה להיכתב.
    """
    written = _written(round_row)
    new_drives = 0
    for member in members:
        serial_of = {
            d.get("dev"): d.get("serial")
            for d in _disks(conn, member["mac"]) if isinstance(d, dict)
        }
        for target in json.loads(member["targets_json"] or "[]"):
            serial = serial_of.get(target.get("dev"))
            if target.get("state") == "done" and serial and serial not in written:
                written.add(serial)
                new_drives += 1
    return round_row["written_drives"] + new_drives, written


def _resume(conn: sqlite3.Connection, store: SessionStore,
            round_row: sqlite3.Row, wave: sqlite3.Row | None) -> None:
    """הסבב פעיל, והגל שהוא מצביע עליו סגור (או שאין כזה) — ‏#217.

    שלוש דרכים נמדדות להגיע לכאן: סגירת הגל מ-endpoint הסבבים הכללי של
    הקונסולה (בלי שום תקלה — ‏`close_session` אינו יודע שזה גל של חדר),
    וכשל בכתיבת `room_rounds` אחרי ש-`store.close` או `store.open` כבר
    בוצע להם commit — שני מסלולי הנסיגה של `_finish_wave`.

    **התביעה כאן היא איפוס המצביע**, בדיוק כמו שהסגירה המותנית היא
    התביעה ב-#177: ‏`UPDATE` מותנה שרק תהליכון אחד מנצח בו, ומי שהפסיד
    רואה `NULL` וכבר אינו במצב הזה. איפוס לפני הפתיחה ולא אחריה, כדי
    שכשל **בפתיחה** ישאיר מצב שהדופק הבא יודע לתקן — ולא סבב שמצביע על
    גל שאינו קיים.
    """
    total, written = round_row["written_drives"], _written(round_row)
    if wave is not None:
        total, written = _tally(conn, round_row, store.members(wave["id"]))
        if not update_one(
            conn,
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " wave_session_id = NULL WHERE id = ? AND state = 'active'"
            " AND wave_session_id = ?",
            (total, json.dumps(sorted(written)), round_row["id"], wave["id"]),
        ):
            return
        conn.commit()

    target = round_row["target_drives"]
    if total >= target:
        conn.execute(
            "UPDATE room_rounds SET state = 'closed', closed_at = ? WHERE id = ?",
            (now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done", f'{round_row["id"]} written={total}/{target}')
        return
    if wave is not None:
        # לא בשקט (עיקרון 5): גל שנעלם מתחת לסבב הוא בדיוק מה שהמפעיל
        # צריך לראות, גם כשהחדר מתאושש לבדו. שורה אחת, כי התביעה למעלה
        # מצליחה פעם אחת.
        journal(conn, "room_wave_lost",
                f'{round_row["id"]} {wave["id"]} written={total}/{target}')
    _attach_wave(conn, store, round_row, total)


def _attach_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, total: int) -> None:
    """מצמיד לסבב גל — קיים או חדש.

    **קודם מאמצים.** גל שנפתח ואיש לא הספיק להצביע עליו כבר מחזיק את
    חריץ המולטיקאסט היחיד, ופתיחת גל נוסף הייתה נכשלת ב-`TAKEN` בכל
    דופק מכאן והלאה. הזיהוי הוא קבוצה + קידומת + אימג' — חתימה שסבב
    כיתה אינו נושא.
    """
    existing = store.active_for_group(CLONERS_GROUP)
    opened_here = False
    if (existing is not None and existing["prefix"] == "ROOM"
            and existing["image_id"] == round_row["image_id"]):
        wave_id = existing["id"]
    else:
        machines = conn.execute(
            "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
        ).fetchone()["n"]
        try:
            wave_id = store.open(
                CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
                expected_clients=max(1, machines), opened_by="",
            )
        except SessionError:
            # החריץ תפוס בידי סבב אחר. **לא נבלע**: המצב כבר ביומן
            # (`room_wave_lost`) וגם על המסך (`wave_state: closed`),
            # והדופק הבא ינסה שוב. חריגה כאן הייתה מייצרת
            # `room_tick_failed` בכל hello — רעש שמכסה על השורה שכן
            # אומרת משהו.
            return
        opened_here = True
    if not update_one(
        conn,
        "UPDATE room_rounds SET wave_session_id = ?, wave_number = wave_number + 1"
        " WHERE id = ? AND state = 'active' AND wave_session_id IS NULL",
        (wave_id, round_row["id"]),
    ):
        # תהליכון אחר הקדים אותנו. גל שאיש אינו מצביע עליו יחזיק את החריץ
        # לנצח, ולכן סוגרים את מה ש**אנחנו** פתחנו — אבל רק אחרי שנבדק
        # שהסבב אינו מצביע עליו: המנצח יכול היה לאמץ בדיוק את הגל הזה
        # (זה הענף הראשון כאן), וסגירתו הייתה מחזירה את החדר למצב שממנו
        # באנו. שאילתה, ולא הנחה — הראיה החיובית היא שהמצביע אינו שלנו.
        if opened_here and conn.execute(
            "SELECT 1 FROM room_rounds WHERE id = ? AND wave_session_id = ?",
            (round_row["id"], wave_id),
        ).fetchone() is None:
            store.close(wave_id, "")
        return
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


def _finish_wave(conn: sqlite3.Connection, store: SessionStore,
                 round_row: sqlite3.Row, members: list[sqlite3.Row]) -> None:
    """הגל הסתיים: סופרים לפי serial אילו מגירות נכתבו, וממשיכים."""
    total, written = _tally(conn, round_row, members)

    if total >= round_row["target_drives"]:
        # הסגירה היא התביעה: שני תהליכונים שהגיעו לכאן עם אותו גל —
        # רק זה שסגר אותו בפועל כותב את השורה התחתונה של הסבב (#177).
        if not store.close(round_row["wave_session_id"], ""):
            return
        conn.execute(
            "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
            " state = 'closed', closed_at = ? WHERE id = ?",
            (total, json.dumps(sorted(written)), now_iso(), round_row["id"]),
        )
        conn.commit()
        journal(conn, "room_done",
                f'{round_row["id"]} written={total}/{round_row["target_drives"]}')
        return

    # היעד לא הושג — הגל הבא נפתח מעצמו וממתין למגירות מוחלפות.
    machines = conn.execute(
        "SELECT COUNT(*) AS n FROM machines WHERE group_id = ?", (CLONERS_GROUP,)
    ).fetchone()["n"]
    # סגירת הגל הגמור ופתיחת הבא הן טרנזאקציה אחת: בין השתיים החריץ
    # היה פנוי, ושני דופקים בו-זמנית הפכו את השני ל-`TAKEN` — ה-hello
    # שהריץ אותו החזיר 500 (#177), ופותח שלישי (סבב כיתה מהקונסולה)
    # שהיה נכנס לאותו חלון היה משאיר את החדר בלי גל בכלל.
    try:
        wave_id = store.open(
            CLONERS_GROUP, round_row["image_id"], prefix="ROOM",
            expected_clients=max(1, machines), opened_by="",
            replaces=round_row["wave_session_id"],
        )
    except SessionSuperseded:
        # תהליכון אחר סגר את הגל הזה ופתח את הבא. זה אינו כישלון אלא
        # בדיוק מה שהאטומיות נועדה לייצר: פותח אחד, לא שניים.
        return
    conn.execute(
        "UPDATE room_rounds SET written_drives = ?, written_serials = ?,"
        " wave_session_id = ?, wave_number = wave_number + 1 WHERE id = ?",
        (total, json.dumps(sorted(written)), wave_id, round_row["id"]),
    )
    conn.commit()
    journal(conn, "room_wave",
            f'{round_row["id"]} wave={round_row["wave_number"] + 1}'
            f' written={total}/{round_row["target_drives"]}')


# --- התצוגה ------------------------------------------------------------------


def _is_awake(last_seen: str | None) -> bool:
    if not last_seen:
        return False
    try:
        seen = datetime.fromisoformat(last_seen)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - seen <= timedelta(seconds=AWAKE_SECONDS)


def status_view(ctx) -> dict:
    """מה שמסך החדר מציג: המכונות, המגירות, והסבב אם יש."""
    round_row = active_round(ctx.conn)
    written = _written(round_row) if round_row else set()
    member_of = {}
    if round_row is not None:
        member_of = {
            m["mac"]: m for m in ctx.store.members(round_row["wave_session_id"])
        }

    machines = []
    for row in ctx.conn.execute(
        "SELECT m.mac, m.suffix, d.last_seen FROM machines m"
        " LEFT JOIN net_devices d ON d.mac = m.mac"
        " WHERE m.group_id = ? ORDER BY m.suffix", (CLONERS_GROUP,)
    ):
        member = member_of.get(row["mac"])
        drawers = drawer_list(ctx.conn, row["mac"], written, member)
        machines.append({
            "mac": row["mac"],
            "name": row["suffix"],
            "awake": _is_awake(row["last_seen"]),
            "drawers": len(drawers),
            "fresh_drawers": sum(1 for d in drawers if d["fresh"]),
            "drawer_list": drawers,
            "joined": member is not None,
            "state": member["state"] if member else None,
            "bytes_written": member["bytes_written"] if member else 0,
            "bytes_total": member["bytes_total"] if member else 0,
            "error": member["error"] if member else None,
        })

    view = {"round": None, "machines": machines}
    if round_row is not None:
        wave = ctx.conn.execute(
            "SELECT state FROM sessions WHERE id = ?",
            (round_row["wave_session_id"],),
        ).fetchone()
        manifest = ctx.library.get(round_row["image_id"])
        view["round"] = {
            "id": round_row["id"],
            "image_id": round_row["image_id"],
            "image_name": manifest["name"] if manifest else round_row["image_id"],
            "target_drives": round_row["target_drives"],
            "written_drives": round_row["written_drives"],
            "remaining_drives": round_row["target_drives"] - round_row["written_drives"],
            "wave_number": round_row["wave_number"],
            "wave_state": wave["state"] if wave else "closed",
            "ready_drives": ready_drives(ctx.conn, ctx.store, round_row),
            "opened_by": round_row["opened_by"],
        }
    return view


# --- ה-API -------------------------------------------------------------------


def create_room_router(ctx, wake=None) -> APIRouter:
    """`wake` מוזרק מ-app.py כדי ששליחת ה-WoL תהיה אותה פונקציה בכל
    המערכת (וניתנת לזיוף בבדיקות)."""
    router = APIRouter(prefix="/api/console/room")
    current_user, _admin_only = auth.dependencies(ctx.conn)

    def room_operator(user=Depends(current_user)) -> tuple[str, str]:
        """מחובר **וגם** בתפקיד שמותר לו להפעיל את החדר."""
        if user[1] not in ROOM_OPERATOR_ROLES:
            journal(ctx.conn, "room_role_denied", f"{user[0]} ({user[1]})")
            raise HTTPException(403, "פעולה למפעיל סבבים בלבד")
        return user

    @router.get("")
    def status(user=Depends(current_user)):
        tick(ctx.conn, ctx.store)
        return status_view(ctx)

    @router.post("")
    async def open_(request: Request, user=Depends(room_operator)):
        body = await request.json()
        try:
            return open_round(
                ctx, body.get("image_id", ""),
                int(body.get("target_drives", 0)), user[0],
            )
        except SessionError as exc:       # לפני ValueError — הוא יורש ממנו
            raise HTTPException(409, str(exc))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc) or "בקשה לא תקינה")

    @router.post("/start")
    def start_now(user=Depends(room_operator)):
        round_row = active_round(ctx.conn)
        if round_row is None:
            raise HTTPException(409, "אין סבב חדר פעיל")
        try:
            ctx.store.start_now(round_row["wave_session_id"], user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    @router.post("/wake")
    def wake_room(user=Depends(room_operator)):
        if wake is None:
            raise HTTPException(503, "WoL אינו מחובר בשרת הזה")
        woken = wake()
        journal(ctx.conn, "wol_sent", f"{CLONERS_GROUP} count={woken}", user[0])
        # הסיבה עולה למסך ולא רק ליומן: "0 מחשבים" בלי הסבר שולח את
        # הטכנאי לחפש WoL ב-BIOS של 12 מכונות, כשהסיבה היא כבל אחד
        # בשרת (#74). ‏getattr — שולח מוזרק בטסטים עשוי להחזיר int רגיל.
        return {"woken": int(woken),
                "failed": len(getattr(woken, "failed", ())),
                "reasons": list(getattr(woken, "reasons", ()))}

    @router.post("/close")
    def close_(user=Depends(room_operator)):
        try:
            close_round(ctx, user[0])
        except SessionError as exc:
            raise HTTPException(409, str(exc))
        return {"ok": True}

    return router
