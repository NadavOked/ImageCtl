"""מה שהריצה משאירה אחריה חי.

חבילת הטסטים הפעילה `udp-sender` **אמיתי** — על אותו portbase שהשרת
מקצה להפצה — והוא נמצא רץ שעתיים וחצי אחרי שהריצה הסתיימה, משדר קובץ
שכבר נמחק (#79). ‏`--max-wait 120` לא עזר: בלי מקבל אחד udpcast ממתין
בלי גבול, ותקרת המתנה שלא נבדקה היא הבטחה ולא ראיה. תהליך כזה על פורט
ההפצה מפיל שידור אמיתי, והאבחון נראה כמו תקלת רשת.

שלוש שכבות, וכולן על ראיה חיובית:

1. **חסימה** — בתוך הטסטים מנוע השידור לא *יכול* להפעיל תהליך אמיתי.
   ‏`block_real_processes` מחליף את `subprocess` בתוך `server.sender`
   בלבד. כל ניסיון נרשם ב-`blocked_spawns`, והטסט שגרם לו נכשל בשמו —
   כי טסט שמגיע לשולח האמיתי אינו בודק את מה שהוא חושב שהוא בודק.
2. **‏portbase שאינו יכול להתנגש** — ‏`assign_test_portbase` מקצה לריצה
   ‏portbase גבוה ואקראי. שתי השכבות האחרות מניחות שמישהו מנקה, ושתיהן
   נשברות באותה נקודה: ריצה **שנקטעה** לא מריצה ניקוי, ויתום שלה אינו
   שייך לאף `basetemp` חי — ולכן אף ריצה עתידית לא תיגע בו לעולם.
   ‏#156: יתום כזה שרד יום וחמש שעות והחזיק את פורטי ההפצה. יתום שאינו
   יכול להתנגש הוא יתום לא מזיק, וזו ההגנה היחידה שאינה תלויה באיש.
3. **סריקה בסוף הריצה** — קוראים ב-`/proc` מי חי ומזכיר את תיקיית
   ה-tmp של הריצה הזאת (ורק אותה — לא הורגים ריצה של מישהו אחר),
   הורגים, וקוראים שוב כדי לראות שבאמת מתו. "שלחנו SIGTERM" אינו
   "התהליך מת".
"""

from __future__ import annotations

import os
import random
import signal
import subprocess
import time
from pathlib import Path

#: הכלים שמזרימים בייטים ברשת. אלה שיכולים לשרוד את הריצה ולהפריע
#: לשידור אמיתי; שאר תת-התהליכים בטסטים (gcc, ‏sh) מסיימים מעצמם.
STREAM_TOOLS = ("udp-sender", "udp-receiver")

#: כל ניסיון להפעיל תהליך אמיתי ממנוע השידור, לפי הסדר.
blocked_spawns: list[list[str]] = []


class _NoRealProcess:
    """מחליף את המודול `subprocess` — בתוך `server.sender` בלבד.

    לא ‏`monkeypatch`: תהליכון השידור ממשיך לרוץ גם אחרי שהטסט נגמר,
    וכל החזרה של המקור בסוף טסט פותחת בדיוק את החלון שבו הוא מספיק
    להפעיל שולח אמיתי. החסימה מותקנת פעם אחת לריצה ואינה מוחזרת.
    """

    STDOUT = subprocess.STDOUT

    @staticmethod
    def Popen(cmd, *args, **kwargs):          # noqa: N802 — חתימה של subprocess
        blocked_spawns.append([str(part) for part in cmd])
        raise OSError(
            "הטסטים חוסמים הפעלת udp-sender אמיתי (#79) — הזרק `sender_runner`"
        )


def block_real_processes() -> bool:
    """מתקין את החסימה. ‏False כשאין בכלל שרת לייבא (עמדה בלי fastapi)."""
    try:
        from server import sender
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return False
    sender.subprocess = _NoRealProcess
    return True


#: הטווח שממנו נבחר ה-portbase של הריצה: גבוה, רחוק מפורטי ההפצה,
#: ו**מתחת** לטווח הפורטים הארעיים של לינוקס (32768-60999) — סוקט יוצא
#: מקרי שיושב שם רגע היה נראה לבדיקת הפורטים כמו יתום. הצעד 2 שומר
#: שהזוג (`portbase`, ‏`portbase+1`) של ריצה אחת לא ייפול על ה-portbase
#: של ריצה מקבילה.
TEST_PORTBASE_RANGE = (20000, 30000, 2)

#: ה-portbase שהוקצה לריצה הזאת; ‏None כשלא הוקצה בכלל.
test_portbase: int | None = None


def assign_test_portbase() -> int | None:
    """נועל את **כל** מנועי השידור בריצה על portbase גבוה ואקראי (#156).

    לא מספיק שכל טסט יעביר portbase משלו: ‏`create_app` בונה מנוע משלו
    בלי לשאול, וזה המנוע שהשאיר יתום על פורטי ההפצה. לכן דורסים את
    ברירת המחדל של המודול — פעם אחת לריצה ובלי החזרה, מאותה סיבה
    שהחסימה אינה מוחזרת.

    מחזיר את ה-portbase שהוקצה, או `None` כשאין בכלל שרת לייבא.
    """
    global test_portbase
    try:
        from server import sender
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return None
    test_portbase = random.SystemRandom().randrange(*TEST_PORTBASE_RANGE)
    sender.DEFAULT_PORTBASE = test_portbase
    return test_portbase


def scan_supported() -> bool:
    """אפשר בכלל לבדוק? בלי `/proc` אין ראיה — ואומרים זאת, לא מניחים."""
    return Path("/proc").is_dir()


def live_stream_processes(marker: str) -> list[tuple[int, str]]:
    """תהליכי שידור חיים ששורת הפקודה שלהם מזכירה את `marker`.

    הסינון הוא לפי שם התוכנית **וגם** לפי הנתיב, כדי ששורת פקודה
    שסתם מכילה את הנתיב (מעטפת, ‏pytest עצמו) לא תיספר כתהליך יתום.
    """
    found: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:                        # התהליך מת בינתיים
            continue
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if not argv or os.path.basename(argv[0]) not in STREAM_TOOLS:
            continue
        line = " ".join(argv)
        if marker in line:
            found.append((int(entry.name), line))
    return sorted(found)


def kill_and_confirm(marker: str, timeout: float = 5.0) -> list[tuple[int, str]]:
    """הורג את מי ששייך ל-`marker`, וקורא שוב. מחזיר את מי ששרד."""
    for pid, _ in live_stream_processes(marker):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:                        # כבר מת
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not live_stream_processes(marker):
            return []
        time.sleep(0.1)
    for pid, _ in live_stream_processes(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.3)
    return live_stream_processes(marker)


def session_verdict(basetemp: str | None, native_required: bool) -> list[str]:
    """שורות לדוח בסוף הריצה — ריק כשהריצה באמת לא השאירה כלום."""
    if basetemp is None:
        return []
    if not scan_supported():
        # לא בדקנו אינו "נקי". בווינדוס אין `/proc` ואין udpcast, ולכן
        # זו לא תקלה; במקום שבו דורשים כלים מקומיים — היא כן.
        return ["אי אפשר לסרוק תהליכים יתומים בלי /proc — הריצה לא נבדקה"] \
            if native_required else []
    found = live_stream_processes(basetemp)
    if not found:
        return []
    lines = [f"{len(found)} תהליכי שידור שרדו את הריצה (#79) — תחת {basetemp}:"]
    lines += [f"    pid {pid}: {line}" for pid, line in found]
    survivors = kill_and_confirm(basetemp)
    lines.append("נהרגו, ואימות חוזר ב-/proc מראה שמתו." if not survivors
                 else f"ו-{len(survivors)} מהם שרדו גם את SIGKILL: {survivors}")
    return lines
