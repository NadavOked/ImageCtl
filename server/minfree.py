"""מינימום מקום פנוי במחיצת המערכת אחרי שחזור (‏#1354).

הכרעת נדב 13/10: אימג' מכווץ שמשוחזר לדיסק קטן יכול "להיכנס" ולהשאיר
ל-Windows כמה GB בודדים — זה לא תקין. יעד שאחרי השחזור יישארו בו פחות
מ-`min_free_gb` פנויים במחיצת המערכת **אינו נכנס**, בדיוק כמו יעד קטן מדי.

הערך הוא **הגדרה בקונסולה** (‏`settings.min_free_gb`, ברירת מחדל 20, ‏0–500),
ולא קבוע בקוד: נדב רוצה לשנות אותו בלי עדכון גרסה. הסוכן מקבל אותו בכל
תשובת hello (‏`min_free_gb`), והאכיפה היא שלו (‏`agent/lib/diskfit.sh`,
‏`min_free_check`) — מה שכאן הוא התאום שהקונסולה מציגה ממנו "נכנס לדיסק
מ-X GB", וחדר השיכפולים מסרב ממנו לפני שנפתח גל.

**מחיצת המערכת** = מחיצת ה-`windows` הראשונה על הפלטה, ואם אין — ה-`linux`
הראשונה (הבחירה של hostname.sh, ממשקים סעיף 1). היא גדלה על היעד רק כשהיא
המחיצה שהשחזור מותח (‏`expandable_candidate`) ורק מעבר לסף של 1GiB של
`expand_last`. ‏`used_bytes` חסר = **לא נבדק**, לא "נכנס" (עיקרון 5).
"""

from __future__ import annotations

import logging
import sqlite3

from .db import get_setting
from .imagefit import expandable_candidate
from .images import _whole, required_bytes

log = logging.getLogger("imagectl.minfree")

SETTING = "min_free_gb"
#: התאום של `MIN_FREE_GB_DEFAULT` ב-`agent/lib/diskfit.sh` — טסט משווה.
MIN_FREE_GB_DEFAULT = 20
MIN_FREE_GB_MAX = 500
GIB = 1 << 30
#: ‏`expand_last` מותח רק כשהיעד גדול מהפריסה ביותר מ-2097152 סקטורים.
EXPAND_THRESHOLD = 1 << 30


def parse_gb(raw: object) -> int | None:
    """ערך הגדרה תקין — מספר שלם 0–500 — או `None`. ‏`bool` אינו מספר כאן,
    ומחרוזת עם אפס מוביל נדחית כמו בסוכן (‏ash קורא ‎020 כאוקטלי)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str) and raw.isascii() and raw.isdigit() \
            and (raw == "0" or not raw.startswith("0")) and len(raw) <= 3:
        value = int(raw)
    else:
        return None
    return value if 0 <= value <= MIN_FREE_GB_MAX else None


def min_free_gb(conn: sqlite3.Connection) -> int:
    """ההגדרה, או ברירת המחדל. חסרה = לא נקבעה (ברירת המחדל, בשקט); פגומה
    = ברירת המחדל **עם שורת יומן** — לעולם לא 0, שהוא "הכלל כבוי"."""
    raw = get_setting(conn, SETTING)
    if raw is None:
        return MIN_FREE_GB_DEFAULT
    value = parse_gb(raw)
    if value is None:
        log.warning("setting %s=%r is not 0-%d -- using %d", SETTING, raw,
                    MIN_FREE_GB_MAX, MIN_FREE_GB_DEFAULT)
        return MIN_FREE_GB_DEFAULT
    return value


def system_partition(manifest: dict) -> dict | None:
    parts = [p for p in manifest.get("partitions") or []
             if isinstance(p, dict) and _whole(p.get("start_sector")) is not None]
    for role in ("windows", "linux"):
        found = sorted((p for p in parts if p.get("role") == role),
                       key=lambda p: p["start_sector"])
        if found:
            return found[0]
    return None


def free_after(manifest: dict, disk_bytes: int) -> tuple[int | None, str] | None:
    """(בייטים פנויים במחיצת המערכת אחרי שחזור לדיסק בגודל `disk_bytes`,
    התפקיד שלה). ‏`(None, role)` = ‏`used_bytes` לא נמדד. ‏`None` = אין מחיצת
    מערכת, והכלל אינו חל. התאום של `system_free_after` בסוכן."""
    sys_part = system_partition(manifest)
    if sys_part is None:
        return None
    used, size = _whole(sys_part.get("used_bytes")), _whole(sys_part.get("size_bytes"))
    need = required_bytes(manifest)
    if used is None or size is None or need is None:
        return None, sys_part["role"]
    cand = expandable_candidate(manifest)
    grow = disk_bytes - need
    grows = cand is not None and cand.get("index") == sys_part.get("index") and grow > EXPAND_THRESHOLD
    return size - used + (grow if grows else 0), sys_part["role"]


def min_disk_bytes(manifest: dict, gb: int) -> int | None:
    """הדיסק הקטן ביותר שהאימג' נכנס אליו **כולל** `gb` פנויים במחיצת
    המערכת — מה שהקונסולה מציגה כ"נכנס לדיסק מ-X". ‏`None` = לא ניתן לקבוע
    (גיאומטריה חסרה, או `used_bytes` שלא נמדד כשהכלל דלוק)."""
    need = required_bytes(manifest)
    if need is None:
        return None
    sys_part = system_partition(manifest)
    if gb <= 0 or sys_part is None:
        return need
    at_need = free_after(manifest, need)
    if at_need is None or at_need[0] is None:
        return None
    short = gb * GIB - at_need[0]
    if short <= 0:
        return need
    cand = expandable_candidate(manifest)
    if cand is None or cand.get("index") != sys_part.get("index"):
        return None    # המחיצה לא תגדל באף דיסק — אין גודל שבו הוא נכנס
    return need + max(short, EXPAND_THRESHOLD + 1)


def where(role: str) -> str:
    return "במחיצת הלינוקס הראשית" if role == "linux" else "ב-C:"


def refusal(manifest: dict, disk_bytes: int, gb: int) -> str | None:
    """המחרוזת של הסוכן (‏`min_free_check`) לדיסק בגודל נתון, או `None`
    כשנשאר מספיק. אותה מחרוזת בדיוק, כדי שהמפעיל יראה סיבה אחת."""
    if gb <= 0:
        return None
    result = free_after(manifest, disk_bytes)
    if result is None:
        return None
    free, role = result
    if free is None:
        return (f"לא נבדק אם יישארו {gb}GB פנויים {where(role)} — "
                f"לא נמדד כמה תפוס בה (used_bytes חסר)")
    if free >= gb * GIB:
        return None
    return f"לא נכנס — יישארו רק {free / GIB:.1f} GB פנויים {where(role)}, המינימום {gb}GB"
