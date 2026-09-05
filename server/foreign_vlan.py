"""מי פונה לשרת מרשת שאינה וילן ההפצה (‏#137).

ההכרעה של נדב: מכונה שקוראת מרשת אחרת נצבעת **אדום** בבריאות, בלי
ירוק-או-לא, עד שהיא מפסיקה לפנות. גם המקרה הלגיטימי — הוא עומד ליד
מחשב ומושך אימג' ביד — מוצג אדום, כי הוא אירוע שראוי לתשומת לב;
והמקרה שהמשימה נכתבה בשבילו הוא מחשב בנייה או מחשב שיכפול שחובר
לשקע הלא נכון, וייראה תקין ולא יעבוד.

**זו התראה ולא שער.** הפונקציות כאן אינן נוגעות בתשובה שנשלחת למכונה
ואינן זורקות לעולם: מכונה לא מוכרת ממשיכה לקבל ‏`known:false` ואתחול
מהדיסק בדיוק כמו קודם (עיקרון 1).

**וזו אינה לולאה.** ‏`agent_loops` ממשיך להוציא את המקרה הזה מפורשות,
ובצדק: מחוץ לווילן ההפצה השרת אינו מגיש את שרשרת האתחול ואין לו ראיה
שהמכונה עברה בתפריט שלו (‏#42). שתי השורות נפרדות במסך ובקוד — הנימוק
שם נכון, והמסקנה שונה.

**תצוגה חיה, לא ארכיון.** שורה יורדת אחרי חלון שתיקה, והמונה יורד
איתה. הארכיון הוא היומן.
"""

from __future__ import annotations

import ipaddress
import logging
import sqlite3
from datetime import datetime, timedelta

from .agent_loops import SILENCE_SECONDS
from .db import journal, now_iso

log = logging.getLogger("imagectl.foreign_vlan")

__all__ = ["SILENCE_SECONDS", "current", "note", "where"]


def _cutoff(now: str) -> str:
    """החותמת שמתחתיה שתיקה מורידה את השורה.

    ההשוואה על מחרוזות ISO ולא על מספרים, מפני ש-`now_iso` כותב תמיד
    UTC ברוחב קבוע — ולכן סדר לקסיקוגרפי הוא סדר כרונולוגי.
    """
    return (datetime.fromisoformat(now)
            - timedelta(seconds=SILENCE_SECONDS)).isoformat(timespec="seconds")


def where(scope: dict | None) -> str | None:
    """הכתובת המקומית שעליה הבקשה התקבלה — כלומר מאיזו רשת היא הגיעה.

    אותו מקור בדיוק שממנו נגזר ‎`off_deploy_vlan`: ‏`scope["server"]`,
    ה-sockname של החיבור. מכוון: *לא* כותרת Host, שהיא קלט של הלקוח
    ולכן תאפשר לתחנה להכריז על עצמה כ"בתוך הווילן".

    ‏None פירושו שאי אפשר לומר מאיזו רשת — ואז אין מה להציג.
    """
    try:
        host, _port = scope.get("server")
        return str(ipaddress.ip_address(host))
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return None


def note(conn: sqlite3.Connection, mac: str, scope: dict | None,
         *, off_vlan: bool, now: str | None = None) -> int | None:
    """רושם פנייה מרשת זרה ומחזיר את מספרה בחלון הנוכחי.

    ‏None = אין מה לרשום, או שלא ניתן לאשר שנרשם. ניטור בלבד.
    """
    try:
        if not off_vlan:
            return None
        address = where(scope)
        if address is None:
            # ‏`off_deploy_vlan` מחזיר True רק כששתי הכתובות נקראו, ולכן
            # לא אמורים להגיע לכאן. אם בכל זאת — אין מה להציג, ובפרט
            # אסור להמציא "רשת לא ידועה" ולהראות אותה כעובדה (עיקרון 5).
            log.error("off-vlan hello from %s but its network is unreadable", mac)
            return None
        return _count(conn, mac, address, now or now_iso())
    except Exception:  # noqa: BLE001 — ניטור לא מפיל hello
        log.exception("off-vlan contact from %s was not recorded", mac)
        return None


def _count(conn: sqlite3.Connection, mac: str, address: str,
           ts: str) -> int | None:
    """ה-UPSERT, כולל איפוס החלון — בכתיבה אחת, בלי מרוץ קריאה-כתיבה.

    שתיקה ארוכה מ-`SILENCE_SECONDS` מתחילה ספירה חדשה באותה שורה.
    הכתובת נדרסת תמיד: מה שמוצג הוא מהיכן היא פונה **עכשיו**.
    """
    cutoff = _cutoff(ts)
    conn.execute(
        "INSERT INTO off_vlan_contacts (mac, address, hits, first_at, last_at)"
        " VALUES (?, ?, 1, ?, ?)"
        " ON CONFLICT (mac) DO UPDATE SET"
        "   address  = excluded.address,"
        "   hits     = CASE WHEN last_at >= ? THEN hits + 1 ELSE 1 END,"
        "   first_at = CASE WHEN last_at >= ? THEN first_at ELSE excluded.first_at END,"
        "   last_at  = excluded.last_at",
        (mac, address, ts, ts, cutoff, cutoff),
    )
    conn.commit()
    # ראיה חיובית: הערך נקרא בחזרה. שורה שאינה שם, או שהחותמת בה אינה
    # זו שנכתבה, פירושה שהרישום לא קרה — ולא שהוא יצא אחד (עיקרון 5).
    row = conn.execute(
        "SELECT hits, last_at FROM off_vlan_contacts WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or row["last_at"] != ts:
        log.error("off-vlan contact from %s was not recorded", mac)
        journal(conn, "off_vlan_unverified", mac)
        return None
    if row["hits"] == 1:
        # פעם אחת לחלון, ולא לכל hello. היומן הוא הארכיון: הוא מה
        # שמאפשר לשאול "המחשב הזה עשה את זה גם שלשום" הרבה אחרי
        # שהשורה ירדה מהמסך.
        journal(conn, "off_vlan_contact", f"{mac} from {address}")
    return int(row["hits"])


def current(conn: sqlite3.Connection, now: str | None = None) -> list[dict]:
    """מי פונה מרשת זרה **עכשיו** — שורה אחת למחשב, האחרון בראש.

    ירידה מהרשימה אינה "נפתר". אין אירוע "נרפא", יש רק היעדר אירוע:
    מחשב כבוי שותק בדיוק כמו מחשב שהועבר לשקע הנכון, ושניהם ירדו
    מכאן בלי שנדע במה מדובר. מה שנשאר בינתיים הוא היומן.
    """
    moment = now or now_iso()
    at = datetime.fromisoformat(moment)
    return [
        {
            "mac": row["mac"],
            # "כיתה ושם" כשהמכונה רשומה; אחרת MAC, וגם הוא מוצג.
            "name": f'{row["label"]}-{row["suffix"]}' if row["suffix"] else None,
            "address": row["address"],
            "hits": int(row["hits"]),
            "first_at": row["first_at"],
            "last_at": row["last_at"],
            "silent_seconds": max(
                0, int((at - datetime.fromisoformat(row["last_at"])).total_seconds())
            ),
        }
        for row in conn.execute(
            "SELECT v.mac, v.address, v.hits, v.first_at, v.last_at,"
            "       m.suffix, g.label"
            " FROM off_vlan_contacts v"
            " LEFT JOIN machines m ON m.mac = v.mac"
            " LEFT JOIN groups g ON g.id = m.group_id"
            " WHERE v.last_at >= ? ORDER BY v.last_at DESC, v.mac",
            (_cutoff(moment),),
        )
    ]
