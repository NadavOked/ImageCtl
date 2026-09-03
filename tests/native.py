"""כלי מערכת חסר: מתי דילוג הוא אמת, ומתי הוא שקר.

‏`skipif` הוא הדרך של pytest לומר "אי אפשר לבדוק כאן". על עמדת הפיתוח
בווינדוס זו אמת: אין gcc, אין hivexget, ו-fanout צריך fifo של POSIX.
על שרת המעבדה וב-CI זו לא אמת אלא תקלה — שם הכלים אמורים להיות, וחבילה
שדילגה בשלמותה נספרה ירוקה בדיוק כמו חבילה שעברה. ככה שלוש חבילות —
כל סוכן ה-POSIX, ‏`fanout`, ו-‏`hivewrite` — יכלו לא לרוץ בלי שאיש
יבחין, ו-`pytest` יצא 0 (#52).

ההבחנה נשענת על דגל מפורש ולא על ניחוש: כש-`IMAGECTL_REQUIRE_NATIVE=1`
דלוק, כלי חסר הוא **כישלון**. הדגל כבוי כברירת מחדל, כדי שהעמדה
בווינדוס תמשיך לדלג כרגיל — שם הדילוג לגיטימי.

שתי שכבות, כי אף אחת מהן לבדה אינה מספיקה:

* ‏`requires_native` — לכל חבילה שיודעת איזה כלי היא צריכה. כשהדגל דלוק
  היא מסמנת את הטסטים בסימון `missing_native`, ו-`fail_on_missing_native`
  מפיל אותם בשמם, עם שם הכלי החסר.
* ‏`SkipAudit` — רשת ביטחון על **כל** דילוג בריצה, מאיזו סיבה שלא תהיה:
  ‏`importorskip`, ‏`pytest.skip()` באמצע טסט, וכל `skipif` שייכתב מחר
  ולא ייעבור דרך כאן. כשהדגל דלוק, ריצה עם ולו דילוג אחד אינה ירוקה.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

ENV_FLAG = "IMAGECTL_REQUIRE_NATIVE"
MISSING_MARK = "missing_native"


def native_required() -> bool:
    """נקרא בכל פעם מחדש, ולא נקבע ביבוא — הטסטים מזיזים את הדגל."""
    return os.environ.get(ENV_FLAG, "").strip().lower() not in ("", "0", "no", "false")


def missing_requirements(
    *tools: str | tuple[str, object], paths: tuple[str, ...] = ()
) -> list[str]:
    """מה מהדרישות אינו כאן. כל דרישה היא שם כלי ל-`which`, או זוג
    ‏(שם, ערך שנמצא) לכלי שהחבילה מאתרת בעצמה — ‏bash של Git למשל."""
    missing: list[str] = []
    for tool in tools:
        label, found = tool if isinstance(tool, tuple) else (tool, shutil.which(tool))
        if not found:
            missing.append(label)
    missing += [str(path) for path in paths if not Path(path).exists()]
    return missing


def requires_native(
    *tools: str | tuple[str, object],
    paths: tuple[str, ...] = (),
    posix: bool = False,
    why: str = "",
):
    """סימון pytest: דילוג בעמדה שאין בה את הכלי, כישלון כשהדגל דלוק."""
    missing = missing_requirements(*tools, paths=paths)
    if posix and os.name == "nt":
        missing.append("מערכת POSIX")
    if not missing:
        return pytest.mark.skipif(False, reason="הכלים כאן")
    if native_required():
        return getattr(pytest.mark, MISSING_MARK)(", ".join(missing))
    tail = f" ({why})" if why else ""
    return pytest.mark.skipif(True, reason=f"חסר כאן: {', '.join(missing)}{tail}")


def fail_on_missing_native(item) -> None:
    """נקרא מ-`pytest_runtest_setup`: הופך את הסימון לכישלון בשם הטסט."""
    for mark in item.iter_markers(MISSING_MARK):
        pytest.fail(
            f"{ENV_FLAG}=1 והכלי חסר: {mark.args[0]}. כאן הכלים אמורים להיות,"
            " ולכן זו תקלת סביבה ולא סיבה לדלג.",
            pytrace=False,
        )


def skip_reason(report) -> str:
    """הסיבה שדווחה לדילוג. ‏longrepr של דילוג הוא (קובץ, שורה, סיבה)."""
    longrepr = getattr(report, "longrepr", None)
    if isinstance(longrepr, tuple) and len(longrepr) == 3:
        return str(longrepr[2])
    return str(longrepr or "בלי סיבה")


class SkipAudit:
    """כל דילוג בריצה, לפי nodeid — כדי שאפשר יהיה לומר *מה* לא נבדק."""

    def __init__(self) -> None:
        self.skipped: dict[str, str] = {}

    def record(self, report) -> None:
        if report.skipped:
            self.skipped.setdefault(report.nodeid, skip_reason(report))

    def verdict(self) -> list[str]:
        """שורות לדוח — ריק כשאין מה לדווח."""
        if not self.skipped or not native_required():
            return []
        lines = [
            f"{ENV_FLAG}=1 אבל {len(self.skipped)} טסטים דולגו — כאן היעד אפס:"
        ]
        lines += [f"    {nodeid}  ←  {reason}"
                  for nodeid, reason in sorted(self.skipped.items())]
        return lines
