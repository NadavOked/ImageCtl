"""סיווג כשל כתיבה — כבל/חריץ או הדיסק — משורות ה-ATA של הקרנל (#874).

הסוכן **אינו מסווג**: הוא אוסף מ-`dmesg` את שורות `ata<N>` / `I/O error`
של הפורט של היעד מאז תחילת הכתיבה ושולח אותן כמות שהן (`ata_log`).
הסיווג נעשה כאן, במקום אחד, ונבדק שורה-שורה על שני הדפוסים שנמדדו.

נמדד 16/09 על דיסק 1 במחשב 2 (`/root/dmesg-sda-20260916-001507.txt`):
``SError: { UnrecovData HostInt 10B8B Handshk }``, ``ATA bus error``,
``limiting SATA link speed to 1.5 Gbps`` — שגיאות קידוד **על הקו**, הדיסק
ענה ``DRDY``. זה כבל/פורט. ובראיה השנייה (07:03) דיסק **אחר** על אותו
פורט נכשל באותה חתימה — שני דיסקים, פורט אחד.

עיקרון 5: מה שאינו מתאים לאף דפוס הוא ``unclassified`` — לא ניחוש.
"""

from __future__ import annotations

import re

#: סימני קו — ביטויים (תת-מחרוזת): שגיאת אפיק, הורדת מהירות הקישור, כשל
#: ממשק. מספיק אחד מהם — הקו אשם.
CABLE_PHRASES = ("ATA bus error", "limiting SATA link speed",
                 "interface fatal error")

#: סימני קו — דגלים בסוגריים המסולסלים של הקרנל (`SError: { ... }`,
#: `error: { ... }`): שגיאות קידוד 8b/10b, לחיצת-יד, CRC בהעברה.
CABLE_FLAGS = frozenset({"10B8B", "Handshk", "ICRC"})

#: סימני מדיה/דיסק — דגלים: סקטור לא-ניתן-לתיקון, פקודה שהדיסק דחה.
#: נספרים **רק בלי** סימן קו — קו רעוע מייצר גם ABRT, ואז הכבל הוא ההסבר
#: הפשוט יותר.
DISK_FLAGS = frozenset({"UNC", "ABRT"})

#: סימני דיסק — ביטויים: שגיאת מדיה. **לא** `Read log ... failed` — הקרנל
#: מדפיס אותו אחרי **כל** איפוס קו (נמדד על ברזל, #890: הכבל הידוע על פורט
#: 1 במחשב 2 סווג "דיסק" בגללו), ולבדו הוא ``unclassified``.
DISK_PHRASES = ("media error",)

#: הדגלים שבתוך `{ ... }` — מה שהקרנל מדפיס כקבוצה, מופרד ברווחים.
_BRACED = re.compile(r"\{([^}]*)\}")

CAUSES = ("cable", "disk", "unclassified")

#: תקרות הקלט: הסוכן שולח עד 40 שורות; מעבר לזה — קלט לא מהסוכן שלנו.
MAX_LINES = 40
MAX_LINE_CHARS = 300


def clean_log(value: object) -> list[str]:
    """‏`ata_log` כפי שהגיע → רשימת מחרוזות בגבולות. מה שאינו רשימה או
    שורה שאינה מחרוזת נזרק; אורך נחתך. ריק הוא קלט תקין ("לא נאספו שורות")."""
    if not isinstance(value, list):
        return []
    out = [line[:MAX_LINE_CHARS] for line in value if isinstance(line, str)]
    return out[:MAX_LINES]


def _flags(text: str) -> set[str]:
    found: set[str] = set()
    for group in _BRACED.findall(text):
        found.update(group.split())
    return found


def classify(lines: list[str]) -> str:
    """‏cable / disk / unclassified — לפי הסימנים, בסדר הזה."""
    text = "\n".join(lines)
    flags = _flags(text)
    if any(p in text for p in CABLE_PHRASES) or flags & CABLE_FLAGS:
        return "cable"
    if any(p in text for p in DISK_PHRASES) or flags & DISK_FLAGS:
        return "disk"
    return "unclassified"
