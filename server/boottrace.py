"""שביל הפירורים (#400) — הצד שרושם, והצד שמסביר מה חסר.

הרשימה הסגורה של הצעדים יושבת ב-`boot/trace.py`, כי גם מחולל ה-GRUB
צריך אותה ו-`boot/` אינו מכיר את השרת. כאן יושבים האחסון והתרגום למסך.

מה שהמודול הזה נותן ואין בלעדיו: **שם לצעד שלא הגיע**. מכונה שהשאירה
‏`entry` ושתקה שבע דקות אינה "לא ידוע" — היא נעצרה לפני `http-ok`,
וזה משפט שאפשר לפעול לפיו. זה אפשרי רק מפני שהרשימה מנויה מראש.
"""

from __future__ import annotations

from datetime import datetime, timezone

from boot.trace import STEP_INDEX, STEPS, is_step

from .db import _write_lock, now_iso, writing

__all__ = ["STALL_SECONDS", "STEP_LABELS", "describe", "record", "trail"]

#: כמה שניות בלי צעד חדש הופכות "בדרך" ל"נתקע". כל מעבר בשרשרת הזאת
#: הוא שניות בודדות — משיכת מודול, `insmod`, טעינת קרנל — ולכן דקה היא
#: כבר תקיעה ודאית ולא איטיות. ה-HP של #400 שתק שבע דקות.
STALL_SECONDS = 60

#: מה שהמפעיל רואה. עברית, כי זו הקונסולה; הצד של GRUB נשאר ASCII.
STEP_LABELS: dict[str, str] = {
    "menu": "תפריט האתחול נמסר",
    "entry": "GRUB נכנס לערך ImageCtl",
    "http-ok": "מודול ה-HTTP נטען",
    "pre-linux": "לפני משיכת הקרנל",
    "pre-initrd": "הקרנל נטען",
    "pre-boot": "ה-initramfs נטען",
    "agent-net": "הרשת עלתה ב-initramfs",
    "agent-start": "הסוכן התחיל",
    "agent-hello": "לפני ה-hello הראשון",
}

#: מה שבא אחרי הצעד האחרון. זה כבר לא פירור אלא ה-hello עצמו, שנרשם
#: במקום אחר לגמרי (`net_devices`, ‏`agent_loops`) — ולכן הוא מוזכר
#: בשם ואינו נספר כשלב.
AFTER_LAST = "hello לשרת"


def record(conn, mac: str, step: str) -> bool:
    """רושם פירור אחד. מחזיר האם נרשם — ראיה חיובית, לא היעדר חריגה.

    צעד שאינו ברשימה נדחה כאן שוב, ולא רק בשכבת ה-HTTP: זו הכתיבה,
    וכתיבה שמקבלת כל מחרוזת הופכת את "לא הגיע לשלב 3" לניחוש.

    צעד שאינו מתקדם (אותו צעד, או אחד קודם) פירושו **אתחול חדש**, ואז
    ‏`first_at` מתאפס. ההכרעה בתוך ה-UPSERT ולא בקריאה-ואז-כתיבה: שתי
    בקשות פירור של אותה מכונה יכולות להגיע בשני תהליכונים.

    ‏`_write_lock` ו-`writing` כמו בכל כותב אחר בשרת (#272, ‏#313):
    תור הוגן בתוך התהליך, ו-rollback שלא משאיר חיבור מורעל.
    """
    if not is_step(step):
        return False
    ts = now_iso()
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO boot_steps (mac, step, idx, at, first_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (mac) DO UPDATE SET"
            "   step = excluded.step, idx = excluded.idx, at = excluded.at,"
            "   first_at = CASE WHEN excluded.idx <= boot_steps.idx"
            "              THEN excluded.at ELSE boot_steps.first_at END",
            (mac, step, STEP_INDEX[step], ts, ts),
        )
    return True


def _age_seconds(at: object, now: datetime | None) -> int | None:
    """כמה זמן עבר מאז החותמת. ‏None = לא הצלחנו לקרוא אותה.

    עיקרון 5: חותמת שלא נקראה, בלי אזור זמן, או מהעתיד אינה "אפס
    שניות" — היא "לא ידוע", והקורא מתייחס אליה כתקיעה.
    """
    if not isinstance(at, str):
        return None
    try:
        stamp = datetime.fromisoformat(at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    seconds = ((now or datetime.now(timezone.utc)) - stamp).total_seconds()
    return int(seconds) if seconds >= 0 else None


def describe(step: object, at: object, *, now: datetime | None = None) -> dict | None:
    """שורת התצוגה לצעד אחרון אחד. ‏None = המכונה לא השאירה שום פירור.

    ``next_label`` הוא כל הרעיון: **הצעד שלא הגיע**, בשמו.
    """
    if not is_step(step):
        return None
    index = STEP_INDEX[step]
    nxt = STEPS[index + 1] if index + 1 < len(STEPS) else None
    seconds = _age_seconds(at, now)
    return {
        "step": step,
        "label": STEP_LABELS[step],
        "index": index + 1,
        "total": len(STEPS),
        "at": at if isinstance(at, str) else None,
        "seconds": seconds,
        "next_step": nxt,
        "next_label": STEP_LABELS[nxt] if nxt else AFTER_LAST,
        # אין צעד הבא = השביל הושלם, ואין מה להיתקע לפניו. חותמת שלא
        # נקראה נספרת כתקיעה: "לא הצלחנו לבדוק" אינו "הכל בסדר".
        "stalled": nxt is not None and (seconds is None or seconds >= STALL_SECONDS),
    }


def trail(conn, *, now: datetime | None = None) -> dict[str, dict]:
    """הפירור האחרון של כל מכונה, לפי MAC. לשימוש מסכי הקונסולה."""
    rows = conn.execute("SELECT mac, step, at FROM boot_steps").fetchall()
    described = ((r["mac"], describe(r["step"], r["at"], now=now)) for r in rows)
    return {mac: info for mac, info in described if info is not None}
