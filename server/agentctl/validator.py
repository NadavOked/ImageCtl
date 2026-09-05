"""אימות משימה, ואימות **כפול** למשימות סיכון גבוה.

‏`validate` מריצה אוסף בדיקות ומחזירה `ValidationResult` — עם
‏`checks` שהוא הראיה, ו-`passed` שהוא הסיכום שלו. היא נכשלת סגור:
בדיקה שזרקה חריגה נספרת כבדיקה שנכשלה, ו-`checks` ריק אינו יכול
להיות `passed`.

‏`double_check_satisfied` **אינה סומכת על `passed` לבדו.** תוצאה
מגיעה אליה כאובייקט שהקורא בנה, ודגל בוליאני שמישהו הציב אינו ראיה
שמשהו נבדק. לפני התיקון היה אפשר להגיש שתי תוצאות עם `checks={}`
ו-`passed=True`, לתת להן שני `validator_id` שונים, ולקבל "בדיקה
כפולה עברה" בלי ששום בדיקה רצה. בחבילת המקור זה נחסם בפועל רק על ידי
השער שקורא לה (‏`safe_gate`), ורק בסיכון `critical` ורק בגלל דרישת
האישור האנושי — כלומר מסיבה אחרת לגמרי, ובסיכון `high` לא נחסם כלל.

לכן `_is_substantiated` כאן: הראיה נדרשת **בפונקציה עצמה**. פונקציה
שנכונה רק כי מישהו במעלה הזרם חסם אינה נכונה (עיקרון 5).
"""

from collections.abc import Callable

from .models import Classification, RiskLevel, Task, ValidationResult

Check = Callable[[Task], tuple[bool, str]]


def validate(
    task: Task, checks: dict[str, Check], *, validator_id: str = "primary"
) -> ValidationResult:
    results: dict[str, bool] = {}
    notes: list[str] = []

    for name, check in checks.items():
        try:
            ok, note = check(task)
        except Exception as exc:  # נכשל סגור: לא הצלחנו לבדוק = נכשל
            ok, note = False, f"{type(exc).__name__}: {exc}"
        results[name] = bool(ok)
        if note:
            notes.append(f"{name}: {note}")

    passed = bool(results) and all(results.values())
    return ValidationResult(passed, results, tuple(notes), validator_id)


def requires_double_check(classification: Classification) -> bool:
    return classification.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)


def _is_substantiated(result: ValidationResult | None) -> bool:
    """האם התוצאה נשענת על ראיה, ולא רק מצהירה שכן.

    שלושת התנאים, וכל אחד מהם ראיה חיובית בפני עצמו:

    1. יש בכלל תוצאה;
    2. ‏`checks` אינו ריק **וכולן עברו** — זה מה שנבדק בפועל;
    3. ‏`passed` מסכים עם `checks` — דגל שסותר את מה שמתחתיו הוא
       תוצאה שנבנתה ביד, לא תוצאה שהתקבלה מריצה.
    """
    if result is None:
        return False
    if not result.checks or not all(result.checks.values()):
        return False
    return bool(result.passed)


def double_check_satisfied(
    classification: Classification,
    primary: ValidationResult,
    secondary: ValidationResult | None,
) -> bool:
    if not _is_substantiated(primary):
        return False
    if not requires_double_check(classification):
        return True
    if secondary is None or not _is_substantiated(secondary):
        return False

    # שני מאמתים הם שניים רק אם לשניהם יש זהות, והיא שונה. ‏id ריק
    # אינו "מאמת אחר" — הוא היעדר זהות, והוא היה עובר בהשוואת `!=`.
    primary_id = primary.validator_id.strip()
    secondary_id = secondary.validator_id.strip()
    return bool(primary_id and secondary_id and primary_id != secondary_id)
