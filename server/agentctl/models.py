"""הטיפוסים ש-`validator` ו-`observability` נוגעים בהם — ורק הם.

‏`models.py` המקורי בחבילה החיצונית מחזיק גם `Category`,
‏`RouteDecision`, ‏`GateDecision` ו-`ApprovalRecord`. אלה שייכים
לניתוב ולשער, ואין להם כאן קורא — לכן הם לא הועברו.

**מה כן הועבר ולמה:** ‏`Task` ו-`ValidationResult` הם הקלט והפלט של
‏`validate`. ‏`Classification` ו-`RiskLevel` הם מה ש-`requires_double_check`
שואל. ‏`TaskType`, ‏`Difficulty` ו-`Urgency` נגררים **לא** כי המאמת
משתמש בהם אלא כי הם **שדות של `Classification`**: מבנה מקוצץ היה נראה
כמו אותו טיפוס ומתנהג אחרת, ופורט של `classifier.py` בהמשך היה בונה
אובייקט שאינו מתאים. שלושה enums של ארבע שורות הם מחיר נמוך יותר
מטיפוס שהתפצל בשקט.

**‏`from __future__ import annotations` לא מופיע כאן במכוון** — הוא
שבר את FastAPI בפרויקט הזה פעמיים (ראה `CLAUDE.md`). כל התחביר כאן
(‏`list[str]`, ‏`X | None`) עובד ב-3.12 ו-3.13 בלי הייבוא הזה.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskType(str, Enum):
    CODE = "code"
    ANALYSIS = "analysis"
    WRITING = "writing"
    OPS = "ops"


class Difficulty(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Urgency(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    body: str
    success_criteria: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Classification:
    task_type: TaskType
    difficulty: Difficulty
    urgency: Urgency
    risk_level: RiskLevel
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationResult:
    """תוצאת אימות.

    ‏`checks` הוא **הראיה**: שם הבדיקה ← האם עברה. ‏`passed` הוא
    הסיכום שלו. שדה `passed` שנבנה ביד בלי `checks` הוא הצהרה בלי
    ראיה, ו-`validator.double_check_satisfied` דוחה אותה.
    """

    passed: bool
    checks: dict[str, bool]
    notes: tuple[str, ...] = ()
    validator_id: str = "primary"
