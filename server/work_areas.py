"""סחיפת אזורי עבודה יתומים בשורש ספריית האימג'ים (‏#88).

קליטה מזרימה את קבצי המחיצות ל-`.capture-<task>` (‏tasks.staging_dir),
וייבוא מחלץ ארכיון ל-`.import-<token>` (‏archive.STAGING_PREFIX). שניהם
אמורים להתפנות בסוף: הקליטה ב-rename לספרייה, בביטול, ובאימות שנכשל;
הייבוא ב-`finally`. מה שנשאר על הדיסק הוא בדיוק המקרה שאין לו ניקוי —
השרת מת באמצע, ואיש לא הריץ אותו. נמדד במעבדה (‏2026-08-29): חמישה
`.capture-*` בני 1.4GB יחד, מול ארבעה אימג'ים אמיתיים.

‏#71 כבר הפך אותם לבלתי נראים (‏`scan` מדלגת על נתיב שרכיב שלו מתחיל
בנקודה), ולכן מה שנשאר אינו סכנת הצגה אלא מקום דיסק. הדיסק הזה הוא
אותו דיסק שמשרת שידור לכיתה, ושרת שנגמר לו המקום באמצע סבב נכשל
כמשהו שנראה אחרת לגמרי.

**למה סחיפה אוטומטית ולא דיווח בלבד (עיקרון 7).**
עיקרון 7 מעמיד הקלדת שם לפני פעולה הרסנית — מחיקת אימג', עצירת סבב.
ההקלדה שם שומרת על **שיקול דעת אנושי**: רק אדם יודע אם האימג' הזה עוד
נחוץ לקורס. ל-`.capture-tsk_0930` אין שיקול דעת כזה להוסיף: הטכנאי
אינו יכול לדעת אם המשימה חיה, וגם אינו אמור — היחיד שיודע הוא טבלת
המשימות, והשרת קורא אותה טוב ממנו. הקלדת שם כזה היא טקס בלי בטיחות.

ושתי עובדות נוספות:

1. אזור עבודה אינו נתונים של אדם אלא שטח עבודה של השרת עצמו, והשרת
   כבר מוחק אותו בלי לשאול איש בשלושה מסלולים (ביטול קליטה, אימות
   שנכשל, ‏`finally` של ייבוא). כאן לא נולדת יכולת הרסנית חדשה — אותו
   ניקוי בדיוק מגיע למסלול היחיד שבו התהליך מת לפני ה-`finally` שלו.
2. דיווח שאיש לא קרא אינו תיקון. הכשל ב-#88 הוא שקט מטבעו; אם התיקון
   מותנה בכך שמפעיל יפתח מסך וילחץ לפני הסבב הבא, הדיסק נשאר מלא.

מה ש**כן** נשאר לאדם הוא כל מה שאי אפשר להוכיח (ראו `plan`): שם לא
מוחקים כלום, ורושמים ביומן מה נשאר ולמה. סוחפים את מה שהוכח, מדווחים
על מה שלא — כל מנגנון במקום שבו הוא באמת עוזר.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from .db import journal
from .images import ImageLibrary
from .tasks import OPEN_STATES

log = logging.getLogger("imagectl.work_areas")

#: אזור עבודה של קליטה. חייב להתאים ל-`tasks.staging_dir`; יש בדיקה.
CAPTURE_AREA = re.compile(r"^\.capture-([A-Za-z0-9_-]+)$")

#: מצבי משימה סופיים. לא רק "המשימה נגמרה" אלא **אף אחד לא יכתוב לשם
#: יותר**: ‏`capture.task_for` מחזיר 404 לכל העלאה למשימה שאינה
#: ‏pending/running, ולכן אזור העבודה של משימה במצב כזה סגור לכתיבה
#: בקוד, לא בתקווה.
DEAD_STATES = ("done", "failed", "cancelled")


def _dir_bytes(path: Path) -> int:
    """כמה בייטים תופסת התיקייה. קובץ שלא נמדד — לא נספר, לא מנחשים."""
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
        except OSError as exc:                       # קובץ שנעלם תוך כדי
            log.warning("could not size %s: %s", item, exc)
    return total


def human_bytes(size: int) -> str:
    """‏1478000000 → "1.4GB". ליומן, שנקרא בעיניים ולא בסקריפט."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f}B" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    raise AssertionError                             # pragma: no cover


def plan(conn, images_root: str | Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """‏(מה נמחק, [(מה נשאר, למה)]) — ההחלטה, בלי לגעת בדיסק.

    שלושה כללים, ואף אחד מהם אינו "לא ראינו סימן שהוא חי" (עיקרון 5):

    * **לא מתחיל בנקודה — לא נוגעים.** אלה האימג'ים עצמם, והדיסק הוא
      מקור האמת (עיקרון 3). סחיפה שטועה כאן בלתי הפיכה.
    * **‏`.capture-<task>` שיש לו שורה במצב סופי — נמחק.** זו הראיה
      החיובית: השורה קיימת, ומצבה אומר שהמשימה נגמרה ושהעלאה אליה כבר
      נדחית. שורות משימה אינן נמחקות לעולם מהטבלה, ולכן המצב הוא רשומה
      ולא היעדר רשומה.
    * **כל השאר נשאר.** משימה ב-pending/running עשויה עוד לחזור אחרי
      אתחול; שורה שאינה קיימת אינה ראיה שהמשימה מתה אלא ראיה שה-DB
      הזה לא מכיר אותה (‏--data-dir אחר, בסיס משוחזר מגיבוי), וזה בדיוק
      ה-`if found:` על `None` שעיקרון 5 אוסר.

    ‏**‏`.import-*` אינו נסחף, וזה לא פיקוח.** לאזור ייבוא אין רשומה
    בשום מקום — לא מזהה, לא שורה, כלום. הטענה היחידה שאפשר להשמיע
    לזכות מחיקתו היא "התהליך שהחזיק אותו כבר לא קיים, כי אנחנו עולים
    עכשיו" — וזו הסקה מעובדה גלובלית, לא ראיה על התיקייה הזאת. היא
    נשברת ברגע שתהליך שני (שרת פיתוח, סימולציה) רץ מול אותו שורש
    ספרייה, ואז נמחק ייבוא שרץ באמת. לכן הוא מדווח ולא נמחק. לקליטה
    יש ראיה כזאת בדיוק — שורה בטבלה — וההבדל הוא בראיה, לא בקידומת.
    """
    root = Path(images_root)
    doomed: list[Path] = []
    kept: list[tuple[Path, str]] = []
    if not root.is_dir():
        return doomed, kept
    for path in sorted(root.iterdir()):
        if not path.name.startswith(ImageLibrary.WORK_AREA_PREFIX):
            continue                    # אימג' אמיתי, או קובץ של המפעיל
        if path.is_symlink() or not path.is_dir():
            kept.append((path, "אינו תיקייה רגילה"))
            continue
        match = CAPTURE_AREA.match(path.name)
        if match is None:
            kept.append((path, "אזור עבודה שאין לו רשומת משימה"))
            continue
        row = conn.execute(
            "SELECT state FROM tasks WHERE id = ?", (match.group(1),)
        ).fetchone()
        if row is None:
            kept.append((path, "אין שורה למשימה הזאת בבסיס הנתונים"))
        elif row[0] in OPEN_STATES:
            kept.append((path, f"המשימה עדיין {row[0]}"))
        elif row[0] in DEAD_STATES:
            doomed.append(path)
        else:
            kept.append((path, f"מצב משימה לא מוכר: {row[0]}"))
    return doomed, kept


def sweep(conn, images_root: str | Path) -> dict:
    """מוחק את מה ש-`plan` הוכיח שהוא יתום, ורושם ביומן. חוזר לצורך בדיקה."""
    doomed, kept = plan(conn, images_root)
    swept: list[str] = []
    freed = 0
    for path in doomed:
        size = _dir_bytes(path)
        try:
            shutil.rmtree(path)
        except OSError as exc:
            log.warning("could not sweep %s: %s", path, exc)
        # ‏`rmtree` יכול גם להצליח חלקית. "נמחק" נקבע בקריאה חוזרת
        # מהדיסק ולא בהיעדר חריגה — אחרת נספור מקום שלא התפנה (עיקרון 5).
        if path.exists():
            kept.append((path, "המחיקה לא הושלמה"))
            continue
        swept.append(path.name.split("-", 1)[1])
        freed += size

    if swept:
        journal(conn, "work_area_swept",
                f"swept={len(swept)} freed={human_bytes(freed)} "
                f"ids={','.join(swept)}")
        log.info("swept %d orphan work areas, freed %d bytes", len(swept), freed)
    if kept:
        journal(conn, "work_area_kept",
                "; ".join(f"{path.name}: {why}" for path, why in kept))
    return {"swept": swept, "freed_bytes": freed,
            "kept": [(p.name, why) for p, why in kept]}
