"""משיכת יוניקאסט — תחנה בודדת שמושכת אימג' ב-HTTP (‏issue #60).

זה המסלול של אשף השחזור (זרימה 13.2): אדם ניגש למחשב אחד, בוחר אימג',
מקליד ERASE, והמכונה מושכת את קבצי המחיצות מ-`/api/v1/images/...`. אין
כאן `udp-sender` ואין כתובת מולטיקאסט — ולכן אין כאן גם את החריץ היחיד
שהמולטיקאסט תופס: כמה משיכות רצות יחד, וגם בזמן שסבב כיתה משדר.

למה בכל זאת session ולא "כלום": משיכה היא **עבודה אמיתית על השרת** —
רוחב פס וקריאה מהדיסק. עד היום היא לא הייתה רשומה בשום מקום, ומפעיל
שהסתכל בקונסולה ראה שרת פנוי בזמן ששתי תחנות מושכות ממנו. כ-session
היא מקבלת בחינם את מה שכל עבודה אחרת מקבלת: דיווחי התקדמות (ממשק 4),
שורת יומן, ותצוגה במבט-העל.

המשיכה נפתחת כבר במצב `running` — אין למי לחכות, התחנה מושכת בעצמה.
היא נסגרת כשהיא מסתיימת בהצלחה, או ביד של מפעיל מהקונסולה. משיכה
שנכשלה **נשארת על המסך**: "נכשל" ו"הסתיים" הם שני מצבים שונים.
"""

from __future__ import annotations

import json
import sqlite3

from .db import journal, now_iso
from .sessions import PULL_PREFIX, TAKEN, UNICAST, SessionError, SessionStore


def _roster_macs(row: sqlite3.Row) -> list[str]:
    raw = row["roster_json"]
    return json.loads(raw) if raw else []


def active_for(store: SessionStore, mac: str) -> sqlite3.Row | None:
    """המשיכה הפעילה של המכונה הזו, אם יש.

    שתי משיכות של אותה מכונה היו כותבות לאותו דיסק בו-זמנית — וזו
    התנגשות אמיתית, בשונה משתי תחנות שונות. נחסמת במפורש, עם הסבר.

    הזיהוי הוא לפי ה-roster ולא לפי `session_members`: שורת החבר נכתבת
    **אחרי** ה-INSERT של הסבב, ולכן בדיקה שנשענת עליה מפספסת בדיוק
    משיכה שנפתחה ברגע זה (#104). ה-roster נכתב באותו INSERT עצמו.
    שורה ישנה בלי roster (התקנה מלפני העמודה) נבדקת עדיין לפי החבר —
    "אין ראיה ב-roster" אינו "אין משיכה".
    """
    for row in store.active_pulls():
        macs = _roster_macs(row)
        if mac in macs or (not macs and store.is_member(row["id"], mac)):
            return row
    return None


def _is_the_same_request(store: SessionStore, row: sqlite3.Row,
                         image_id: str) -> bool:
    """האם המשיכה הפעילה היא אותה בקשה בדיוק, שאיש עוד לא דיווח עליה.

    הסוכן שולח עם `curl --retry 3 --max-time 10`, ופקיעת `--max-time`
    היא שגיאה חולפת לעניין `--retry`: שרת שקיבל בקשה, פתח משיכה וענה
    ב-11 שניות מקבל אותה **שוב**. אותה תחנה + אותו אימג' + אפס דיווחים
    = זו אותה משיכה, ולכן מחזירים לה את אותו `id` במקום לפתוח שנייה או
    לחסום אותה מפני עצמה.

    משיכה שכבר דיווחה משהו — התקדמות, סיום או כישלון — אינה "אותה
    בקשה" אלא עבודה קיימת, והחסימה עליה נשארת. גם כישלון: משיכה
    שנכשלה נשארת נראית עד שמפעיל סוגר אותה (עיקרון 5).

    ‏`all` על רשימה ריקה הוא בכוונה: אין חבר = התהליכון שפתח עדיין לא
    הספיק לכתוב אותו, ואין דיווח שאפשר לאבד.
    """
    if row["image_id"] != image_id:
        return False
    return all(
        not member["done"] and member["state"] == "waiting"
        and not member["bytes_written"]
        for member in store.members(row["id"])
    )


def _resume_or_refuse(conn: sqlite3.Connection, store: SessionStore,
                      row: sqlite3.Row, mac: str, image_id: str) -> str:
    if not _is_the_same_request(store, row, image_id):
        raise SessionError(TAKEN[UNICAST])
    journal(conn, "pull_retry", f"{mac} — {row['id']}")
    return row["id"]


def open_pull(conn: sqlite3.Connection, store: SessionStore, mac: str,
              group_id: str, image_id: str, opened_by: str) -> str:
    """פותחת משיכה ורושמת את המכונה כחברה בה מיד.

    ההרשמה כאן ולא ב-hello: אין hello שיצרף אותה — המשיכה כבר רצה, וגם
    הדיווח הראשון שלה חייב למצוא חבר קיים (`reports.ingest`).

    שני מסלולים מגיעים למשיכה קיימת: הבדיקה המקדימה, ו-`store.open`
    שנדחה על האינדקס הייחודי מפני שתהליכון אחר כתב בין השניים (#104).
    שניהם נגמרים באותו מקום — אותו `id` אם זו אותה בקשה, ואחרת סירוב.
    """
    existing = active_for(store, mac)
    if existing is not None:
        return _resume_or_refuse(conn, store, existing, mac, image_id)
    try:
        session_id = store.open(
            group_id, image_id, PULL_PREFIX, expected_clients=1,
            opened_by=opened_by, roster=[mac], kind=UNICAST,
        )
    except SessionError:
        existing = active_for(store, mac)
        if existing is None:
            raise           # לא המרוץ — שגיאת ולידציה אמיתית
        return _resume_or_refuse(conn, store, existing, mac, image_id)
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at)"
        " VALUES (?, ?, 'waiting', ?)",
        (session_id, mac, now_iso()),
    )
    conn.commit()
    return session_id


def sweep(store: SessionStore) -> None:
    """סוגרת משיכות שהסתיימו בהצלחה — ורק אותן.

    ראיה חיובית: המכונה דיווחה `done` (ממשק 4). משיכה שנכשלה נשארת
    פעילה ונראית, עד שמפעיל סוגר אותה — עיקרון 5: "לא הצלחנו" אינו
    "הצלחנו", ובוודאי לא נעלם מהמסך לבד.
    """
    for row in store.active_pulls():
        members = store.members(row["id"])
        if members and all(m["done"] for m in members):
            store.close(row["id"], "", event="pull_done")


def journal_refusal(conn: sqlite3.Connection, mac: str, reason: str) -> None:
    """סירוב לפתוח משיכה נרשם. תחנה שאינה מצליחה למשוך היא תקלה שמישהו
    יחפש בקונסולה, ולא אירוע שקורה בשקט."""
    journal(conn, "pull_refused", f"{mac} — {reason}")
