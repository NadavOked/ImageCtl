"""‏#582 — יומן המשימות אינו קובץ אחד שכל הסוכנים כותבים אליו.

**נמדד 08/09/2026:** חמישה סוכנים עבדו במקביל, כל אחד בעץ עבודה
משלו, וכולם כתבו ל-`logs/2026-09-08.md`. התוצאה: **שבע התנגשויות
‏`git merge`** באותו יום, בכל מיזוג בנפרד.

## ⚠️ ורשומה אחת אבדה בפועל

הרשומה של ‏Grok על #549 נכתבה מקומית ומעולם לא נכנסה לגיט. **היא
התגלתה רק מפני שקובץ לא-מנוהל חסם `git merge --ff-only`** — בלי
המקריות הזאת איש לא היה יודע שהיא חסרה.

**וההתנגשות היא הצורה הטובה — היא נראית.** הצורה הרעה היא רשומה
שנכתבת לעץ עבודה, לא נדחפת, והעץ נמחק.

## למה זה לא "סתם אי-נוחות"

‏`work-log-is-mandatory` הוא נוהל מחייב, ו-`AGENTS.md` דורש אותו
מכל סוכן. **נוהל שנשבר כשמפעילים אותו כמתוכנן אינו נוהל — הוא
מלכודת.**
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tools" / "agents" / "task-log.sh"
BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(BASH is None, reason="bash לא זמין")


def write_entry(repo: Path, agent: str, task: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, str(repo / "tools/agents/task-log.sh"),
         "--agent", agent, "--task", task, "--status", "done",
         "--changed", "בדיקה", "--unverified", "בדיקה בלבד"],
        cwd=str(repo), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """ריפו מזערי עם הכלי האמיתי — לא עותק של הלוגיקה."""
    work = tmp_path / "repo"
    (work / "tools" / "agents").mkdir(parents=True)
    shutil.copy(SCRIPT, work / "tools" / "agents" / "task-log.sh")
    subprocess.run(["git", "init", "-q", str(work)], check=True,
                   stdin=subprocess.DEVNULL, capture_output=True)
    return work


@requires_bash
def test_two_agents_never_write_to_the_same_file(repo):
    """⚠️ **הטענה כולה.** שני סוכנים, שני קבצים — ולכן `git` לא
    יכול להתנגש עליהם."""
    assert write_entry(repo, "grok", "#549").returncode == 0
    time.sleep(1.1)          # חותמת הזמן היא ברזולוציית שנייה
    assert write_entry(repo, "avticha", "#581").returncode == 0

    days = list((repo / "logs").iterdir())
    assert len(days) == 1, f"נוצרה יותר מתיקיית יום אחת: {days}"
    files = sorted(p.name for p in days[0].iterdir())
    assert len(files) == 2, f"שני סוכנים כתבו ל-{len(files)} קבצים: {files}"
    assert "grok" in files[0] and "avticha" in files[1], files


@requires_bash
def test_the_day_is_a_directory_and_not_a_single_file(repo):
    """‏`logs/YYYY-MM-DD.md` — הצורה שהתנגשה — אינה נוצרת יותר."""
    assert write_entry(repo, "grok", "#549").returncode == 0
    stray = list((repo / "logs").glob("*.md"))
    assert stray == [], f"נוצר קובץ יום משותף: {stray}"


@requires_bash
def test_the_file_name_carries_the_time_so_the_order_survives(repo):
    """הסדר הכרונולוגי היה נתון בקובץ המשותף. עכשיו הוא בשם הקובץ,
    ולכן `ls` לבדו מחזיר את הרשומות לפי סדר כתיבתן."""
    assert write_entry(repo, "grok", "#1").returncode == 0
    time.sleep(1.1)
    assert write_entry(repo, "codex", "#2").returncode == 0
    day = next((repo / "logs").iterdir())
    names = sorted(p.name for p in day.iterdir())
    assert all(re.match(r"^\d{6}-", n) for n in names), names
    assert names == sorted(names)


@requires_bash
def test_a_task_name_with_slashes_does_not_escape_the_directory(repo):
    """⚠️ שם המשימה מגיע מהסוכן. ‏`../` או `/` בתוכו היו כותבים
    מחוץ ל-`logs/` — ושם הרשומה נעלמת בשקט."""
    assert write_entry(repo, "grok", "../../etc/passwd").returncode == 0
    assert not (repo / "etc").exists()
    day = next((repo / "logs").iterdir())
    files = list(day.iterdir())
    assert len(files) == 1
    assert "/" not in files[0].name and ".." not in files[0].name


@requires_bash
def test_the_unverified_field_is_still_blocking(repo):
    """הבקרה ההפוכה: השינוי כאן לא ריכך את מה שהכלי כבר אכף.
    ‏`--unverified` נשאר שדה חובה (‏`work-log-is-mandatory`)."""
    proc = subprocess.run(
        [BASH, str(repo / "tools/agents/task-log.sh"),
         "--agent", "grok", "--task", "#1", "--status", "done",
         "--changed", "בדיקה"],
        cwd=str(repo), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
    )
    assert "unverified" in (proc.stdout + proc.stderr)
    assert not (repo / "logs").exists(), "נכתבה רשומה בלי --unverified"
