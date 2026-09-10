"""‏#523 — ‏`gate_verdict` היא הפונקציה שמסרבת, ואיש לא בדק אותה.

`tools/git/gate-checks.sh` מכיל שתי פונקציות, והן עונות על שתי
שאלות שונות:

* ``gate_wait_for_checks`` — **האם הבדיקות הסתיימו?** נבדקת ב-
  ``test_publish_to_public.py``.
* ``gate_verdict`` — **האם הן עברו?** ‏`success` בלבד, ולא
  ``skipped`` ולא ``failure``. **לא נבדקה מעולם.**

```
tools/git/gate-checks.sh:53          gate_verdict()      ← מוגדרת
tools/git/publish-to-public.sh:272   gate_verdict "$(gate_wait_for_checks)"
tests/                              ← אף קריאה אליה
```

*(‏``tests/test_pr_gate.py`` כן מזכיר ``gate_verdict`` — אבל זה
**קובץ אחר**, ``tools/agents/gate_verdict.py``. שם דומה, מודול שונה.)*

**וזה ההסבר ל-#497:** שער הפרסום אמר "ירוק" בלי שבדק CI. ‏``skipped``
נראה בדיוק כמו ``success`` ל-``gate_wait_for_checks`` — שתיהן
``completed`` — והפונקציה היחידה שמבחינה ביניהן לא הורצה בטסט אחד.

הפיגום כאן מריץ את ``gate_verdict`` **האמיתי**: הוא טוען את
``gate-checks.sh`` ומזין לו את הפלט שהוא היה מקבל מ-``gh``. הוא
אינו קורא את הסקריפט כטקסט ואינו משכפל את הלולאה — טסט שמשכפל
את המימוש עובר גם כשהמימוש נסחף, וזו המחלה ש-#523 מתאר.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import pytest

from test_agent import BASH
from native import requires_native

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "git" / "gate-checks.sh"

pytestmark = requires_native(("bash", BASH))

#: שתי הבדיקות ש-`REQUIRED_CHECKS` דורש, במצב שעובר.
BOTH_PASS = "pytest (3.12)\tcompleted\tsuccess\npytest (3.13)\tcompleted\tsuccess\n"


def _verdict(tmp_path: Path, response: str) -> subprocess.CompletedProcess:
    """מריץ את `gate_verdict` האמיתי על תשובה נתונה.

    ‏``stdin=DEVNULL``: בריצה רב-קבצית של pytest בווינדוס ה-handle של
    stdin נשבר תחת capture, וכל subprocess שיורש אותו נופל
    ב-``WinError 50`` (#14).
    """
    harness = tmp_path / "harness.sh"
    harness.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'die() { echo "error: $*" >&2; exit 1; }\n'
        'REQUIRED_CHECKS=("pytest (3.12)" "pytest (3.13)")\n'
        "public_repo=owner/name; pr_number=1\n"
        f". {shlex.quote(str(GATE))}\n"
        f"gate_verdict {shlex.quote(response)}\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [BASH, str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=dict(os.environ), stdin=subprocess.DEVNULL, timeout=60,
    )


def test_two_passing_checks_are_a_green_verdict(tmp_path):
    """רדיוס הפגיעה. בלי זה כל התיקון יכול להיות "לסרב תמיד"."""
    r = _verdict(tmp_path, BOTH_PASS)
    assert r.returncode == 0, r.stderr
    assert "verdict: SUCCESS" in r.stdout, r.stdout


@pytest.mark.parametrize("conclusion", ["skipped", "failure", "cancelled", "timed_out"])
def test_a_required_check_that_did_not_succeed_is_refused(tmp_path, conclusion):
    """‏``skipped`` הוא המקרה של #497, והשאר הם אותה משפחה.

    כולם ``completed`` — כלומר ``gate_wait_for_checks`` מרוצה מהם
    ומחזיר. **רק ``gate_verdict`` מבחין.**
    """
    response = (f"pytest (3.12)\tcompleted\t{conclusion}\n"
                "pytest (3.13)\tcompleted\tsuccess\n")
    r = _verdict(tmp_path, response)
    assert r.returncode != 0, (
        f"‏{conclusion} התקבל כירוק — זה בדיוק #497\n{r.stdout}")
    assert "did not pass" in r.stderr or "did not conclude success" in r.stderr, r.stderr


def test_a_required_check_that_never_ran_is_refused(tmp_path):
    """‏PR שלא נגע בפייתון יכול להיות ירוק בלי ש-`tests` רץ בו בכלל.

    ירוק כזה אינו ראיה — וזו ההערה שיושבת בקוד עצמו.
    """
    r = _verdict(tmp_path, "shellcheck\tcompleted\tsuccess\n")
    assert r.returncode != 0, r.stdout
    assert "did not run at all" in r.stderr, r.stderr


def test_an_empty_response_is_refused(tmp_path):
    """אין בדיקות = אין ראיה. לא "הכול עבר"."""
    r = _verdict(tmp_path, "")
    assert r.returncode != 0, r.stdout
    assert "did not run at all" in r.stderr, r.stderr


def test_the_publish_tool_actually_calls_the_verdict(tmp_path):
    """**המוטציה שאף טסט אחר לא תופס.**

    מחיקת ``gate_verdict "$(gate_wait_for_checks)"``
    מ-``publish-to-public.sh`` משאירה את הפונקציה בקובץ, ולכן כל
    טסטי היחידה למעלה ממשיכים לעבור. הבדיקה כאן היא שהקריאה
    **קיימת בנתיב הפרסום**, ולא רק שהפונקציה תקינה.

    זו בכוונה בדיקה סטטית, וזה החריג היחיד בקובץ: הנתיב שמריץ אותה
    דורש ריפו ציבורי, ‏PR פתוח ו-`gh` מאומת. **טסט שרץ בקצה הזה
    היה בודק את GitHub, לא אותנו.** מה שכן נבדק התנהגותית הוא
    ``gate_verdict`` עצמה, בכל חמשת הטסטים שמעליו.
    """
    publish = (ROOT / "tools" / "git" / "publish-to-public.sh").read_text(
        encoding="utf-8")
    assert "gate_verdict" in publish, (
        "‏publish-to-public.sh אינו קורא ל-gate_verdict — "
        "השער חוזר להיות זה של #497")
    # ולא רק שהשם מופיע: הוא חייב לקבל את פלט ההמתנה, אחרת הוא נשאל
    # על תשובה שלא נקראה.
    assert "gate_verdict \"$(gate_wait_for_checks" in publish, publish[-400:]
