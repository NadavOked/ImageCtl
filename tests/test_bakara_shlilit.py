"""‏#539 — הכלי שמריץ את הבקרה השלילית נבדק על מה שהוא **דוחה**.

כלי שמאמת בדיקות הוא בעצמו נקודת כשל של עיקרון 5: אם הוא קורא כשל
של הבדיקה כהצלחה, כל דוח שיוצא ממנו משכנע ושקרי. שלושת המצבים כאן
הם בדיוק שלוש הצורות שבהן הנוהל נשבר ידנית ב-#530.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "bakara_shlilit", ROOT / "tools" / "bakara-shlilit.py")
bs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bs)


# --- מה נחשב בקרה, ומה רק נראה כמוה --------------------------------------


def test_a_behavioural_failure_is_a_control():
    """‏`AssertionError` עם ערכים בפועל — זה מה שמחפשים."""
    out = "E  AssertionError: assert 200 == 403\n1 failed, 37 passed"
    assert bs.invalid_reason(1, 1, out) is None


def test_an_import_error_is_not_a_control():
    """זו הצורה השנייה שנשברה: הטסט לא רץ, והדוח נראה כמו כשל."""
    out = "ImportError: cannot import name 'claim_task'\n1 error"
    assert "ImportError" in bs.invalid_reason(1, 0, out)


def test_a_collection_error_is_not_a_control():
    out = "ERROR tests/test_x.py\n1 error in 0.3s"
    assert bs.invalid_reason(1, 0, out) is not None


def test_zero_failures_under_a_mutation_voids_the_test():
    """הצורה השלישית, והקשה לזיהוי: הטסט עבר מהסיבה הלא נכונה.

    בלי הכלל הזה `0 failed` נקרא ככיסוי, וזה בדיוק ההפך.
    """
    assert "פסול" in bs.invalid_reason(0, 0, "38 passed in 8.01s")


@pytest.mark.parametrize("rc", [2, 4, 5])
def test_a_run_that_never_happened_is_not_a_control(rc):
    """‏`0 failed` שאין מאחוריו ריצה.

    ‏4 אינו תיאורטי: נתיב טסט בשם שגוי הריץ אפס טסטים והשאיר פלט
    שנראה תקין לחלוטין — ‏"1 warning in 0.01s" ותו לא.
    """
    assert bs.invalid_reason(rc, 0, "1 warning in 0.01s") is not None


# --- המוטציה עצמה: ראיה חיובית בשני הכיוונים ------------------------------


def test_a_spec_that_does_not_match_the_code_is_refused(tmp_path):
    """הצורה הראשונה שנשברה: המוטציה לא נכתבה, והטסטים "עברו"."""
    f = tmp_path / "x.py"
    f.write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        bs.apply_mutation(f, "b = 2", "b = 3")
    assert "0 פעמים" in str(e.value)
    assert f.read_text(encoding="utf-8") == "a = 1\n", "הקובץ נגע למרות הכשל"


def test_an_ambiguous_spec_is_refused(tmp_path):
    """שתי התאמות אינן מוטציה אחת — ואי אפשר לייחס להן כשל."""
    f = tmp_path / "x.py"
    f.write_text("a = 1\na = 1\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        bs.apply_mutation(f, "a = 1", "a = 2")
    assert "2 פעמים" in str(e.value)


def test_a_matching_spec_is_applied_and_the_original_returned(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("keep\nold\nkeep\n", encoding="utf-8")
    original = bs.apply_mutation(f, "old", "new")
    assert f.read_text(encoding="utf-8") == "keep\nnew\nkeep\n"
    assert original == "keep\nold\nkeep\n", "המקור לשחזור אינו נכון"


# --- ספירת התוצאות -------------------------------------------------------


def test_the_counts_are_read_from_the_summary_line():
    rc, failed, passed, out = 1, 0, 0, ""
    failed, passed = _counts("3 failed, 35 passed, 2 skipped in 9.12s")
    assert (failed, passed) == (3, 35)


def test_a_clean_run_reads_as_zero_failures():
    assert _counts("38 passed in 7.97s") == (0, 38)


def _counts(summary: str) -> tuple[int, int]:
    """אותה קריאה שב-`run_tests`, מבודדת כדי שאפשר יהיה לבדוק אותה."""
    failed = passed = 0
    for token in summary.split(", "):
        parts = token.strip().rstrip(".").split()
        if len(parts) >= 2 and parts[0].isdigit():
            if parts[1] == "failed":
                failed = int(parts[0])
            elif parts[1] == "passed":
                passed = int(parts[0])
    return failed, passed


# --- מצב 3: השורה הממוטטת לא הורצה בכלל (R15) ----------------------------


def test_the_touched_lines_are_the_ones_that_changed():
    original = "a\nb\nc\nd\n"
    mutated = "a\nX\nY\nd\n"
    assert bs.mutated_lines(original, mutated) == {2, 3}


def test_an_insertion_is_reported_as_the_inserted_lines():
    assert bs.mutated_lines("a\nb\n", "a\nNEW\nb\n") == {2}


def test_a_deletion_reports_no_lines_in_the_mutated_file():
    """שורה שנמחקה אינה קיימת בקובץ הממוטט, ולכן אין מה למדוד עליה.

    זו קבוצה ריקה ולא כישלון — אבל היא **גם** אומרת שבדיקת ההרצה
    לא תוכל להכריע כאן, וזה מה שיירשם.
    """
    assert bs.mutated_lines("a\nb\nc\n", "a\nc\n") == set()


def test_identical_text_touches_nothing():
    assert bs.mutated_lines("a\nb\n", "a\nb\n") == set()


def test_the_measurement_not_happening_is_not_the_line_not_running():
    """‏`None` ו-`set()` הם שתי תשובות, ואסור לקפל אותן.

    ‏"לא ידענו אם הורצה" ו"לא הורצה" מובילים לשתי פעולות שונות של
    המתקן — עיקרון 5 בצורתו הישירה.
    """
    # ‏בורר שאינו קיים → ‏pytest יוצא 4, ולכן שום טסט לא רץ כאן.
    # **חשוב שלא להצביע על הקובץ הזה עצמו**: זו הייתה רקורסיה
    # אינסופית — הטסט מריץ pytest על הקובץ שמכיל אותו.
    missing = ROOT / "tools" / "no-such-file-xyz.py"
    assert bs.executed_lines(
        ["tests/test_bakara_shlilit.py::no_such_test"], missing) is None
