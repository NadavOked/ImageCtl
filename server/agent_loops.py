"""מי מגיע לסוכן בלי שהייתה לו סיבה — התסמין של דיסק שלא עולה (‏#112).

מכונה בלי משימה ובלי סבב פתוח לקבוצה שלה מקבלת מהתפריט
`set default=local`. אם היא **בכל זאת** שלחה `hello`, אז השרשור לדיסק
המקומי נכשל: המחשב הגיע לסוכן, אין לו שם מענה, הוא נופל וחוזר — עד
שמישהו מתקן אותו. זה בדיוק "לתלמיד לא עולה הדיסק".

השומר של ‏#75 (`bootguard.py`) אינו רואה את זה, וגם לא אמור: הוא סופר
לפי הקשר **תחום בזמן** (`session:<id>` / `task:<id>`) כדי שמונה לא
יצטבר לנצח, ולמכונה כזו אין הקשר בכלל. שני המודולים הם שתי חצאים של
אותה תמונה — שם נספרות בקשות התפריט שהשרת **שלח** לסוכן, וכאן נספרות
הגעות לסוכן שהשרת **לא** שלח.

הראיה החיובית כאן היא ‏`hello` עצמו: הוא נשלח מתוך הסוכן הרץ, ולכן
הוא מוכיח היכן המכונה נמצאת. בקשת התפריט אינה מוכיחה דבר על היעד.

**תצוגה חיה, לא ארכיון.** שורה מתארת את הלולאה הנוכחית בלבד ויורדת
אחרי עשר דקות שתיקה, והמונה יורד איתה. הארכיון הוא היומן.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

from boot.grub_menu import LOCAL, decide

from .db import journal, now_iso
from .sessions import SessionStore

log = logging.getLogger("imagectl.agent_loops")

#: כמה שתיקה סוגרת את הלולאה הנוכחית. המחזור שנמדד על החומרה ב-#75
#: היה ‏14:05:51 → 14:07:54 → 14:08:4x, כלומר ~2 דקות; עשר דקות הן
#: חמישה מחזורים שלא הגיעו. נגזר, לא הומצא.
SILENCE_SECONDS = 600


def _cutoff(now: str) -> str:
    """החותמת שמתחתיה שתיקה נחשבת לסוף הלולאה.

    ההשוואה נעשית על מחרוזות ISO ולא על מספרים, מפני ש-`now_iso`
    כותב תמיד UTC ברוחב קבוע — ולכן סדר לקסיקוגרפי הוא סדר כרונולוגי.
    """
    return (datetime.fromisoformat(now)
            - timedelta(seconds=SILENCE_SECONDS)).isoformat(timespec="seconds")


def unexplained(conn: sqlite3.Connection, store: SessionStore, answer: dict,
                *, off_vlan: bool) -> bool | None:
    """האם ה-hello הזה הוא ראיה לכך שהאתחול לדיסק המקומי נכשל.

    ‏None פירושו **לא ידוע**, וזה אינו "לא": אם אי אפשר לקרוא את מצב
    הסבב, אי אפשר לדעת אם למכונה הייתה סיבה להגיע — ואז אסור להציג
    אותה כחשודה. עיקרון 5 חל כאן לשני הכיוונים.

    שלוש הרחקות מכוונות, וכולן קיימות כדי שהמסך יישאר קריא:

    * **מחוץ לווילן ההפצה** — שם השרת אינו מגיש את שרשרת האתחול, ואין
      לו ראיה שהמכונה בכלל עברה בתפריט שלו. סוכן שמדבר משם הוא בדרך
      כלל אשף השחזור שאדם הפעיל בכוונה (‏#42), לא לולאה.
    * **כשיש סבב פעיל לקבוצה** — תחנה שמדברת עם הסוכן בזמן סבב היא
      התנהגות תקינה לגמרי. מסך שמתמלא בכל סבב הוא מסך שאיש לא קורא,
      וזה גרוע מלא להציג כלום, כי הוא *נראה* כמו ניטור.
    * **מחשב בנייה** — ‏`build` מקבל `local` עם תפריט גלוי (#140), ואדם
      שעומד מולו בוחר את ImageCtl בכוונה (זרימה 13.1). הסוכן שולח אותו
      ל-`build_console` להמתין לפקודת קליטה, והוא פונה כל שתי שניות.

    ההרחקה השנייה משאירה נקודה עיוורת ידועה: מכונה שסיימה שחזור, או
    שאינה ברשימת הסבב, מקבלת גם היא דיסק מקומי — ובזמן סבב לא נספור
    אותה. זו ההחלטה שננעלה: פחות רגישות, בתמורה למסך שאפשר להאמין לו.

    **וגם השלישית משאירה נקודה עיוורת, במודע:** מחשב בנייה שבאמת נתקע
    בלולאה לא ייספר. אין לשרת שום סימן שמבדיל בינו לבין מחשב בנייה
    שאדם שלח לסוכן — שורת הפקודה של הקרנל נקייה מפרטי משימה (עיקרון 2),
    ושתי הטבלאות מחזירות בדיוק את אותו `local`. עדיף לא לספור מקרה
    נדיר מלצבוע באדום מכונה תקינה בכל פעם שנוגעים בה: מסך בריאות
    שצועק זאב מאמן את המפעיל להתעלם ממנו, וזה מבטל את #112 שלשמו
    הגלאי נבנה.
    """
    if off_vlan:
        return False
    if answer.get("role") == "build":
        return False
    if decide(answer).action != LOCAL:
        # השרת שלח אותה לסוכן: משימה, סבב, או מחשב שיכפול שברירת
        # המחדל שלו היא מסך ההמתנה (`cloner-wait`). ההגעה מוסברת.
        return False
    group = answer.get("group") or {}
    if not group.get("id"):
        # מכונה שאינה רשומה: אין לה קבוצה, ולכן אין סבב שיכול להסביר
        # אותה. מחשב זר שנופל לסוכן שוב ושוב הוא בדיוק מה שמפעיל רוצה
        # לדעת עליו — הוא יוצג לפי MAC.
        return True
    try:
        return store.active_for_group(group["id"]) is None
    except Exception:  # noqa: BLE001 — "לא הצלחנו לקרוא" אינו "אין סבב"
        return None


def note(conn: sqlite3.Connection, store: SessionStore, mac: str, answer: dict,
         *, off_vlan: bool = False, now: str | None = None) -> int | None:
    """סופר את ה-hello הזה בלולאה הנוכחית של המכונה ומחזיר את מספרו.

    ‏None = אין מה לספור, או שלא ניתן לאשר שנספר. ניטור בלבד: הפונקציה
    לעולם אינה זורקת ולעולם אינה נוגעת בתשובה שנשלחת למכונה — עיקרון 1
    נשאר בדיוק כפי שהוא.
    """
    try:
        verdict = unexplained(conn, store, answer, off_vlan=off_vlan)
        if verdict is None:
            log.error("cannot tell whether %s had a reason to reach the agent", mac)
            return None
        return _count(conn, mac, now or now_iso()) if verdict else None
    except Exception:  # noqa: BLE001 — ניטור לא מפיל hello
        log.exception("agent arrival from %s was not recorded", mac)
        return None


def _count(conn: sqlite3.Connection, mac: str, ts: str) -> int | None:
    """ה-UPSERT, כולל איפוס הלולאה — בכתיבה אחת, בלי מרוץ קריאה-כתיבה.

    שתיקה ארוכה מ-`SILENCE_SECONDS` מתחילה ספירה חדשה באותה שורה:
    מכונה שחוזרת אחרי שתיקה אינה ממשיכה מונה ישן.
    """
    cutoff = _cutoff(ts)
    conn.execute(
        "INSERT INTO agent_loops (mac, hits, first_at, last_at)"
        " VALUES (?, 1, ?, ?)"
        " ON CONFLICT (mac) DO UPDATE SET"
        "   hits     = CASE WHEN last_at >= ? THEN hits + 1 ELSE 1 END,"
        "   first_at = CASE WHEN last_at >= ? THEN first_at ELSE excluded.first_at END,"
        "   last_at  = excluded.last_at",
        (mac, ts, ts, cutoff, cutoff),
    )
    conn.commit()
    # ראיה חיובית: הערך נקרא בחזרה. שורה שאינה שם, או שהחותמת בה אינה
    # זו שנכתבה, פירושה שהספירה לא קרתה — ולא שהיא יצאה אחת (עיקרון 5).
    row = conn.execute(
        "SELECT hits, last_at FROM agent_loops WHERE mac = ?", (mac,)
    ).fetchone()
    if row is None or row["last_at"] != ts:
        log.error("agent arrival from %s was not recorded", mac)
        journal(conn, "agent_loop_unverified", mac)
        return None
    if row["hits"] == 1:
        # פעם אחת ללולאה, ולא לכל hello. היומן הוא הארכיון: הוא מה
        # שמאפשר לשאול "המחשב הזה עשה את זה שלוש פעמים השבוע" ולזהות
        # דיסק גוסס, הרבה אחרי שהשורה ירדה מהמסך.
        journal(conn, "agent_loop", mac)
    return int(row["hits"])


def current(conn: sqlite3.Connection, now: str | None = None) -> list[dict]:
    """מי בלולאה **עכשיו** — שורה אחת למחשב, האחרון שנראה בראש.

    ירידה מהרשימה אינה "נפתר". אין אירוע "נרפא", יש רק היעדר אירוע,
    ואסור לקפל אותו להצלחה: מכונה כבויה שותקת בדיוק כמו מכונה שתוקנה,
    ותרד מכאן בלי שנגעו בה — במתכוון. היא תחזור תוך שני מחזורים אם
    היא עדיין תקועה, ומה שנשאר בינתיים הוא היומן.
    """
    moment = now or now_iso()
    at = datetime.fromisoformat(moment)
    return [
        {
            "mac": row["mac"],
            # "כיתה ושם" כשהמכונה רשומה; אחרת MAC, וגם הוא מוצג.
            "name": f'{row["label"]}-{row["suffix"]}' if row["suffix"] else None,
            "hits": int(row["hits"]),
            "first_at": row["first_at"],
            "last_at": row["last_at"],
            "silent_seconds": max(
                0, int((at - datetime.fromisoformat(row["last_at"])).total_seconds())
            ),
        }
        for row in conn.execute(
            "SELECT a.mac, a.hits, a.first_at, a.last_at, m.suffix, g.label"
            " FROM agent_loops a"
            " LEFT JOIN machines m ON m.mac = a.mac"
            " LEFT JOIN groups g ON g.id = m.group_id"
            " WHERE a.last_at >= ? ORDER BY a.last_at DESC, a.mac",
            (_cutoff(moment),),
        )
    ]


__all__ = ["SILENCE_SECONDS", "current", "note", "unexplained"]
