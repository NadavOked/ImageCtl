"""‏`server/agentctl` — המאמת והיומן, ובעיקר: מה נחשב ראיה לבדיקה כפולה.

הקוד הזה הועבר מחבילת בקרת סוכנים חיצונית. בסקירה אדוורסרית נטען עליו
ש-`double_check_satisfied` **סומכת על `ValidationResult` שהקורא בנה**,
ורואה שני מאמתים כעצמאיים רק מפני ש-`validator_id` שלהם שונה. כלומר
אפשר להגיש שתי תוצאות עם ‏`checks={}` ו-`passed=True`, ולקבל
"הבדיקה הכפולה עברה" **בלי ששום בדיקה רצה מעולם**.

הטענה נבדקה בהרצה, וזה מה שנמצא:

* ‏`validate()` עצמה **בסדר** — ‏`passed = bool(results) and all(...)`,
  ולכן `checks` ריק אינו יכול להיות `passed`.
* השער החיצוני (‏`safe_gate.evaluate_gate` בחבילת המקור) אכן חסם את
  התרחיש שהוצג — אבל ב-`critical-risk action requires human approval`,
  כלומר **מסיבה אחרת לגמרי**. אותו זיוף בסיכון `high` היה עובר.
* ‏`double_check_satisfied` **עצמה לא בדקה כלום** מלבד את הדגל
  ‏`passed` ואת ה-id. זה מה שתוקן כאן.

התיקון הוא עיקרון 5 בצורתו הישירה: הצלחה נקבעת לפי **ראיה חיובית** —
‏`checks` לא ריק, וכולן עברו — ולא לפי דגל שמישהו הציב. השער החיצוני
אינו חלק מהראיה: פונקציה שנכונה רק כי מישהו אחר במעלה הזרם חסם, אינה
נכונה.

‏`safe_gate.py` **לא הועבר** לכאן — יש בו פרצה פתוחה (‏#226).
"""

from __future__ import annotations

import json

import pytest

from server.agentctl import observability
from server.agentctl.models import (
    Classification,
    Difficulty,
    RiskLevel,
    Task,
    TaskType,
    Urgency,
    ValidationResult,
)
from server.agentctl.validator import (
    double_check_satisfied,
    requires_double_check,
    validate,
)


def _task() -> Task:
    return Task(task_id="t-1", title="פריסת אימג'", body="סבב לכיתה 3")


def _classification(risk: RiskLevel) -> Classification:
    return Classification(
        task_type=TaskType.OPS,
        difficulty=Difficulty.MEDIUM,
        urgency=Urgency.NORMAL,
        risk_level=risk,
    )


def _real_result(validator_id: str, *, ok: bool = True) -> ValidationResult:
    """תוצאה כפי ש-`validate` באמת מייצרת אותה — עם ראיה בפנים."""
    return validate(
        _task(),
        {"sha256": lambda _t: (ok, "" if ok else "hash mismatch")},
        validator_id=validator_id,
    )


# --- validate: כשל סגור --------------------------------------------------


def test_validate_fails_when_there_is_nothing_to_check():
    """‏`checks` ריק אינו "הכל תקין" — הוא "לא נבדק דבר"."""
    result = validate(_task(), {})
    assert result.passed is False
    assert result.checks == {}


def test_validate_fails_closed_when_a_check_raises():
    """בדיקה שזרקה היא בדיקה שלא הצליחה לבדוק — ולכן נכשלת."""

    def explodes(_task: Task) -> tuple[bool, str]:
        raise RuntimeError("hivex לא נמצא")

    result = validate(_task(), {"hostname": explodes, "sha256": lambda _t: (True, "")})
    assert result.passed is False
    assert result.checks == {"hostname": False, "sha256": True}
    assert any("RuntimeError" in note and "hivex" in note for note in result.notes)


def test_validate_passes_only_when_every_check_passes():
    passing = validate(_task(), {"a": lambda _t: (True, ""), "b": lambda _t: (True, "")})
    assert passing.passed is True

    failing = validate(_task(), {"a": lambda _t: (True, ""), "b": lambda _t: (False, "לא")})
    assert failing.passed is False
    assert failing.checks == {"a": True, "b": False}


def test_requires_double_check_only_above_medium():
    assert requires_double_check(_classification(RiskLevel.HIGH)) is True
    assert requires_double_check(_classification(RiskLevel.CRITICAL)) is True
    assert requires_double_check(_classification(RiskLevel.LOW)) is False
    assert requires_double_check(_classification(RiskLevel.MEDIUM)) is False


# --- double_check_satisfied: הפגם המרכזי ---------------------------------


def test_two_empty_results_are_not_a_double_check():
    """**זה הטסט של הממצא.**

    שתי תוצאות שנבנו ביד, ‏`passed=True` ו-`checks={}`, עם שני
    ‏`validator_id` שונים. לפני התיקון זה החזיר `True` — "בדיקה כפולה
    עברה" בלי ששום בדיקה רצה. הפונקציה חייבת לדחות את זה **בעצמה**,
    בלי שום שער חיצוני בתמונה.
    """
    forged_primary = ValidationResult(True, {}, (), "primary")
    forged_secondary = ValidationResult(True, {}, (), "secondary")

    assert (
        double_check_satisfied(
            _classification(RiskLevel.CRITICAL), forged_primary, forged_secondary
        )
        is False
    )
    # ובסיכון high, שבו השער החיצוני לא היה דורש אישור אנושי:
    assert (
        double_check_satisfied(
            _classification(RiskLevel.HIGH), forged_primary, forged_secondary
        )
        is False
    )


def test_an_empty_primary_is_rejected_even_when_no_double_check_is_needed():
    """גם בסיכון נמוך — "אין דרישה לאימות כפול" אינו "לא צריך לבדוק"."""
    forged = ValidationResult(True, {}, (), "primary")
    assert double_check_satisfied(_classification(RiskLevel.LOW), forged, None) is False


def test_a_result_that_contradicts_its_own_checks_is_rejected():
    """‏`passed=True` מעל בדיקה שנכשלה הוא דגל שסותר את הראיה שמתחתיו."""
    liar = ValidationResult(True, {"sha256": False}, (), "primary")
    assert double_check_satisfied(_classification(RiskLevel.LOW), liar, None) is False

    assert (
        double_check_satisfied(
            _classification(RiskLevel.HIGH), _real_result("primary"), liar
        )
        is False
    )


def test_a_blank_validator_id_is_not_an_identity():
    """‏id ריק אינו "מאמת אחר" — הוא היעדר זהות."""
    primary = _real_result("primary")
    anonymous = validate(_task(), {"sha256": lambda _t: (True, "")}, validator_id="")
    assert anonymous.passed is True
    assert (
        double_check_satisfied(_classification(RiskLevel.HIGH), primary, anonymous)
        is False
    )


def test_the_same_validator_twice_is_not_two_validators():
    primary = _real_result("primary")
    again = _real_result("primary")
    assert (
        double_check_satisfied(_classification(RiskLevel.HIGH), primary, again) is False
    )


def test_high_risk_without_a_secondary_is_rejected():
    assert (
        double_check_satisfied(
            _classification(RiskLevel.HIGH), _real_result("primary"), None
        )
        is False
    )


def test_a_failed_secondary_is_rejected():
    assert (
        double_check_satisfied(
            _classification(RiskLevel.HIGH),
            _real_result("primary"),
            _real_result("secondary", ok=False),
        )
        is False
    )


def test_two_real_independent_validations_are_accepted():
    """הצד החיובי — בלעדיו התיקון היה יכול פשוט לדחות הכול."""
    assert (
        double_check_satisfied(
            _classification(RiskLevel.HIGH),
            _real_result("primary"),
            _real_result("secondary"),
        )
        is True
    )
    assert (
        double_check_satisfied(
            _classification(RiskLevel.LOW), _real_result("primary"), None
        )
        is True
    )


# --- observability -------------------------------------------------------


def test_events_round_trip_through_the_log(tmp_path):
    log = tmp_path / "nested" / "events.jsonl"
    observability.emit_event(log, "task.started", task_id="t-1", data={"שם": "כיתה 3"})
    observability.emit_event(log, "task.started", task_id="t-2", data={})
    observability.emit_event(log, "task.done", task_id="t-1", data={"ok": True})

    assert log.exists(), "emit_event אמור ליצור את תיקיית האב"
    summary = observability.summarize(log)
    assert summary["exists"] is True
    assert summary["events"] == 3
    assert summary["tasks"] == 2
    assert summary["by_type"] == {"task.started": 2, "task.done": 1}

    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert first["data"] == {"שם": "כיתה 3"}
    assert first["timestamp_utc"].endswith("+00:00")


def test_a_missing_log_is_not_an_empty_log(tmp_path):
    """היעדר יומן ויומן ריק הם שני מצבים — ‏`exists` מפריד ביניהם."""
    missing = observability.summarize(tmp_path / "never-written.jsonl")
    assert missing == {"exists": False, "events": 0, "by_type": {}, "tasks": 0}

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert observability.summarize(empty)["exists"] is True
    assert observability.summarize(empty)["events"] == 0


def test_a_corrupt_line_fails_loudly(tmp_path):
    """שורה פגומה אינה מדולגת בשקט — אחרת הסיכום משקר על מה שראה."""
    log = tmp_path / "events.jsonl"
    observability.emit_event(log, "task.started", task_id="t-1", data={})
    with log.open("a", encoding="utf-8") as fh:
        fh.write("{לא JSON\n")

    with pytest.raises(json.JSONDecodeError):
        observability.summarize(log)
