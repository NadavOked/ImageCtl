"""השכבה השלישית של השומר: היתר מפורש, צר ומתועד (#454).

**למה היא קיימת.** ההערה מעל ``ALWAYS`` אמרה *"אין פקודה בפרויקט
שמצדיקה יצירת מערכת קבצים, ולכן אין שם שער שאפשר לפתוח"*. ב-06/09/2026
זה חדל להיות נכון: הנאס מייצא שני LUN ריקים ב-iSCSI, ויצירת מערכת
קבצים עליהם היא הפעולה התקינה. ‏#367 כבר טיפל באותה צורה — שם ההסבר
אמר "שמור למתאם" והקוד חסם גם אותו — **והפתרון היה להעביר שכבה, לא
למחוק את הכלל.**

**מה שהקובץ הזה שומר עליו.** שהשער צר: הוא נפתח רק עם היתר תקף, רק
על ההתקנים שההיתר נוקב בהם, ורק עד התפוגה. **וארבעת מסלולי הכישלון
חוסמים**, כי "לא הצלחנו לאמת את ההיתר" אינו "יש היתר" (עיקרון 5).

בקרה שלילית::

    git checkout origin/main -- tools/agents/guard-bash.py
    python -m pytest tests/test_guard_grant.py -q
"""

from __future__ import annotations

import datetime
import importlib.util
import json
from pathlib import Path

import pytest

# ‏guard-bash.py אינו מודול חוקי לייבוא (מקף בשם), ולכן נטען לפי נתיב.
_SPEC = importlib.util.spec_from_file_location(
    "guard_bash_under_test",
    Path(__file__).resolve().parents[1] / "tools" / "agents" / "guard-bash.py",
)
guard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(guard)

MKFS = "mkfs.ext4 -F -L imagectl-iscsi-hdd /dev/sdd"
UTC = datetime.timezone.utc


def _write_grant(root: Path, *, devices, expires_days=1, pattern=r"\bmkfs(\.|\b)",
                 reason="LUN ריקים של הנאס", granted_by="נדב", drop=None):
    when = datetime.datetime.now(UTC) + datetime.timedelta(days=expires_days)
    item = {"pattern": pattern, "devices": devices, "expires": when.isoformat(),
            "reason": reason, "granted_by": granted_by}
    if drop:
        item.pop(drop)
    path = root / "tools" / "agents"
    path.mkdir(parents=True, exist_ok=True)
    (path / "guard-grant.json").write_text(json.dumps([item]), encoding="utf-8")


def _check(monkeypatch, root: Path, command=MKFS):
    monkeypatch.chdir(root)
    return guard.check(command, coordinator=True)


def test_without_a_grant_it_is_still_blocked(tmp_path, monkeypatch):
    """ברירת המחדל לא השתנתה: בלי היתר, בדיוק כמו קודם."""
    why, _ = _check(monkeypatch, tmp_path)
    assert why, "הפעולה עברה בלי שום היתר"


def test_a_valid_grant_opens_exactly_that_device(tmp_path, monkeypatch):
    _write_grant(tmp_path, devices=["/dev/sdd"])
    why, _ = _check(monkeypatch, tmp_path)
    assert why is None, f"היתר תקף נחסם: {why}"


def test_a_grant_for_one_device_does_not_cover_another(tmp_path, monkeypatch):
    """הלב של השער. היתר על ה-LUN אינו היתר על דיסק המערכת."""
    _write_grant(tmp_path, devices=["/dev/sdd"])
    why, _ = _check(monkeypatch, tmp_path,
                    command="mkfs.ext4 -F /dev/sda")
    assert why, "היתר על /dev/sdd התיר את /dev/sda"


def test_a_command_touching_one_granted_and_one_not_is_blocked(tmp_path, monkeypatch):
    """כל ההתקנים בפקודה חייבים להיות מכוסים, לא רק אחד מהם."""
    _write_grant(tmp_path, devices=["/dev/sdd"])
    why, _ = _check(monkeypatch, tmp_path,
                    command="mkfs.ext4 -F /dev/sdd && mkfs.ext4 -F /dev/sda")
    assert why, "פקודה שנגעה גם בהתקן שלא הותר עברה"


def test_an_expired_grant_is_not_a_grant(tmp_path, monkeypatch):
    _write_grant(tmp_path, devices=["/dev/sdd"], expires_days=-1)
    why, _ = _check(monkeypatch, tmp_path)
    assert why, "היתר שפג עדיין פתח את השער"


def test_broken_json_blocks(tmp_path, monkeypatch):
    """נכשל סגור: קובץ שאי אפשר לקרוא אינו היתר."""
    path = tmp_path / "tools" / "agents"
    path.mkdir(parents=True)
    (path / "guard-grant.json").write_text("{ not json", encoding="utf-8")
    why, _ = _check(monkeypatch, tmp_path)
    assert why, "JSON שבור נספר כהיתר"


@pytest.mark.parametrize("missing", ["reason", "granted_by", "expires", "devices"])
def test_a_grant_missing_any_field_blocks(tmp_path, monkeypatch, missing):
    """הסיבה ומי אישר אינם קישוט — היתר בלי הסבר אינו קריא, ולכן אינו היתר."""
    _write_grant(tmp_path, devices=["/dev/sdd"], drop=missing)
    why, _ = _check(monkeypatch, tmp_path)
    assert why, f"היתר בלי {missing} עבר"


def test_rm_rf_root_has_no_gate_even_with_a_grant(tmp_path, monkeypatch):
    """‏ALWAYS נשאר ללא שער. לא כל כלל ראוי לשכבה השלישית."""
    _write_grant(tmp_path, devices=["/dev/sdd"], pattern=r".*")
    why, _ = _check(monkeypatch, tmp_path, command="rm -rf /")
    assert why, "היתר גורף פתח את rm -rf /"


def test_a_grant_cannot_smuggle_a_prefix_device(tmp_path, monkeypatch):
    """‏/dev/sd אינו מכסה את /dev/sda. התאמה מדויקת, לא תת-מחרוזת."""
    _write_grant(tmp_path, devices=["/dev/sd"])
    why, _ = _check(monkeypatch, tmp_path, command="mkfs.ext4 -F /dev/sda")
    assert why, "היתר על תחילית כיסתה התקן שלם"


def test_a_grant_does_not_open_the_gate_for_an_agent(tmp_path, monkeypatch):
    """הלב השני של השער: ההיתר מצמצם *מה*, המתאם קובע *מי*.

    קובץ ההיתר יושב בריפו, ולכן סוכן בעץ עבודה קורא בדיוק את אותו
    קובץ. אילו ההיתר לבדו היה פותח, היתר שנכתב למתאם היה פותח גם לכל
    סוכן מקביל — נסיגה מהגבול ש-#367 קבע.
    """
    _write_grant(tmp_path, devices=["/dev/sdd"])
    monkeypatch.chdir(tmp_path)
    why, coordinator_only = guard.check(MKFS, coordinator=False)
    assert why, "היתר פתח את השער לסוכן בעץ עבודה"
    assert coordinator_only, "נחסם, אבל ההסבר לא אמר לסוכן שזה שמור למתאם"
