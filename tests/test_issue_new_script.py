"""‏tools/issue-new.sh — הכלי שפותח Issue מלא, או לא פותח בכלל.

הכלי הזה קיים כי הרביעייה שכתובה ב-CONTRIBUTING.md וב-ISSUES.md
(יצירה → לוח → תלות → קריאה חוזרת) הועתקה ידנית ולא הורצה שלמה.
המדידה: ב-2026-08-31 נמצאו **70 Issues סגורים בלי milestone** ושניים
בלי label (CONTRIBUTING.md:112-116).

מה שנבדק כאן הוא **התנהגות**, לא נוסח: הסקריפט מורץ באמת, מול `gh`
מזויף על ה-PATH שרושם כל קריאה לקובץ. ככה אפשר לשאול את השאלה שאי
אפשר לענות עליה בקריאת טקסט — **האם `gh` נקרא בכלל** כשחסר שדה חובה.
זו הנקודה: ‏Issue חלקי בריפו ציבורי הוא משהו שצריך לחזור אליו, וזה
בדיוק מה שלא קרה 70 פעם.

אף Issue אמיתי אינו נוצר כאן. ה-`gh` שרץ בבדיקות האלה הוא זיוף.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "issue-new.sh"

#: מגבלת ~300 השורות לקובץ (CLAUDE.md, עיקרון 8).
MAX_LINES = 300

SH = shutil.which("sh") or shutil.which("bash")

# ‏skipif הוא בדיוק הדפוס של #52 — חבילה שלמה שדילגה ו-pytest שיצא 0.
# לכן הדילוג מותר **רק בווינדוס בלי Git Bash**. על לינוקס (וב-CI) אין
# ל-`sh` דרך להיעדר, ואם הוא נעדר הבדיקה תיפול ולא תדלג.
requires_sh = pytest.mark.skipif(
    SH is None and os.name == "nt",
    reason="אין sh בווינדוס הזה (Git Bash לא מותקן) — רץ ב-CI ועל לינוקס",
)


def source() -> str:
    return SCRIPT.read_text(encoding="utf-8")


# --- הקובץ עצמו -------------------------------------------------------------


def test_the_script_exists_and_is_posix_sh():
    assert SCRIPT.is_file(), "tools/issue-new.sh חסר"
    first = source().splitlines()[0]
    # הסוכן בפרויקט הוא busybox ash, וה-CI סורק את הקובץ כ-`-s sh`.
    # shebang של bash כאן היה אומר ששני אלה חלוקים על מה הקובץ.
    assert first == "#!/bin/sh", f"shebang לא צפוי: {first}"


def test_the_script_does_not_grow_past_the_line_limit():
    lines = source().count("\n") + 1
    assert lines <= MAX_LINES, f"{lines} שורות"


def test_the_script_is_on_the_shellcheck_workflow_list():
    """רשימת הקבצים ב-shellcheck.yml מתוחזקת ביד.

    קובץ מעטפת שאינו ברשימה אינו נסרק **בכלל**, וזה נראה בדיוק כמו
    קובץ שנסרק ועבר — עיקרון 5. הבדיקה הזו היא מה שמחזיק את הרשימה
    מסונכרנת עם הקובץ שנוסף כאן.
    """
    workflow = (ROOT / ".github" / "workflows" / "shellcheck.yml").read_text(
        encoding="utf-8"
    )
    assert "tools/issue-new.sh" in workflow, (
        "tools/issue-new.sh אינו ברשימת הקבצים של shellcheck.yml — הוא לא ייסרק"
    )


def test_the_script_never_swallows_a_failure_into_a_pass():
    """עיקרון 5 כטקסט. הכלי הזה קיים כדי לתפוס metadata חסר; ‏`|| true`
    או `2>/dev/null` בתוכו היו הופכים אותו למי שמדווח "נוצר" על כלום."""
    for number, line in enumerate(source().splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue                       # הערה שמסבירה את האיסור
        assert "|| true" not in line, f"issue-new.sh:{number}"
        assert "2>/dev/null" not in line, f"issue-new.sh:{number}"
        assert "set +e" not in line, f"issue-new.sh:{number}"


def test_the_repository_is_not_hardcoded_in_the_source():
    """הכלי אמור לעבוד גם על ריפו אחר. ‏owner/name קשיח בקוד פותח
    Issue בריפו הלא נכון בשקט."""
    assert "NadavOked/ImageCtl" not in source(), (
        "שם הריפו קשיח בסקריפט — הוא נגזר מ-git remote"
    )
    assert "git remote get-url origin" in source()


# --- הרצה אמיתית מול gh מזויף ------------------------------------------------


def make_gh_shim(tmp_path: Path) -> tuple[Path, Path]:
    """‏gh מזויף על ה-PATH. מחזיר (תיקיית bin, קובץ היומן).

    כל קריאה נרשמת ליומן — כולל המקרה שבו אין אף קריאה, שהוא בדיוק
    מה שנבדק בסירוב.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "gh-calls.log"
    (bindir / "gh").write_text(
        "#!/bin/sh\n"
        f'echo "CALL: $*" >> "{log.as_posix()}"\n'
        'case "$1 $2" in\n'
        '  "issue create") echo "https://github.com/o/r/issues/999"; exit 0 ;;\n'
        '  "issue view") printf \'%s\\t%s\\t%s\\n\' '
        '"${FAKE_LABELS:-2}" "${FAKE_MILESTONE:-1}" "${FAKE_PROJECT:-1}"; exit 0 ;;\n'
        "esac\n"
        # ה-id של Issue חוסם הוא מזהה פנימי, ומכוון **אינו** שווה למספרו:
        # שליחת המספר במקומו היא הבאג שהבדיקה מחפשת.
        'case "$*" in\n'
        '  *"--method POST"*) exit 0 ;;\n'
        '  *"dependencies/blocked_by"*) echo "${FAKE_DEPS:-1}"; exit 0 ;;\n'
        '  *"--jq .id"*) echo 3300771234; exit 0 ;;\n'
        "esac\n"
        "exit 0\n",
        encoding="utf-8",
        newline="\n",
    )
    # ‏0o700 ולא 0o755: הזיוף צריך להיות בר-הרצה למי שמריץ את הבדיקה
    # בלבד. ‏CodeQL סימן את המסכה הרחבה כ-`py/overly-permissive-file`
    # ‏(high) על ה-PR הזה — קובץ בר-הרצה שכל משתמש במכונה יכול לקרוא.
    os.chmod(bindir / "gh", 0o700)
    log.write_text("", encoding="utf-8")
    return bindir, log


def run_tool(tmp_path: Path, *args: str, cwd: Path | None = None, **env_extra: str):
    bindir, log = make_gh_shim(tmp_path)
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env.update(env_extra)
    # ‏stdin=DEVNULL: בריצה רב-קבצית של pytest בווינדוס ה-handle של stdin
    # נשבר תחת capture, וכל subprocess שיורש אותו נופל ב-WinError 50 (#14).
    proc = subprocess.run(
        [SH, str(SCRIPT), *args],
        cwd=str(cwd) if cwd else str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    return proc, log.read_text(encoding="utf-8")


@requires_sh
@pytest.mark.parametrize(
    "missing, args",
    [
        ("--milestone", ["--title", "t", "--label", "ci"]),
        ("--label", ["--title", "t", "--milestone", "m"]),
        ("--title", ["--label", "ci", "--milestone", "m"]),
    ],
)
def test_a_missing_required_field_refuses_before_gh_is_called(tmp_path, missing, args):
    """הסירוב, והראיה שהוא **לפני** היצירה.

    ‏exit לבדו אינו מספיק: סקריפט שיוצר Issue ואז מתלונן היה גם הוא
    יוצא בקוד שאינו אפס, ומשאיר בדיוק את הזבל שהכלי אמור למנוע.
    היומן הריק הוא הראיה החיובית שאיש לא נגע בריפו.
    """
    proc, calls = run_tool(tmp_path, *args)
    assert proc.returncode != 0, f"חסר {missing} והכלי יצא באפס"
    assert missing in proc.stderr, f"ההודעה אינה מציינת את {missing}: {proc.stderr}"
    assert calls == "", f"‏gh נקרא למרות ש{missing} חסר: {calls}"


@requires_sh
def test_the_full_sequence_runs_in_order_and_reads_the_fields_back(tmp_path):
    """הרביעייה של ISSUES.md, ובראשה הקריאה החוזרת.

    ‏`gh issue create` שהחזיר URL אינו ראיה שהשדות נקבעו — ‏label שאינו
    קיים בריפו ו-milestone שהוקלד לא נכון הם שני מצבים שבהם היציאה
    מוצלחת והשדה ריק. בלי השלב הזה הכלי הוא רק העתקה של הבעיה.
    """
    proc, calls = run_tool(
        tmp_path, "--title", "t", "--label", "ci", "--label", "task",
        "--milestone", "סדר לריפו", "--repo", "o/r",
    )
    assert proc.returncode == 0, proc.stderr
    lines = [ln for ln in calls.splitlines() if ln.startswith("CALL: ")]
    assert len(lines) == 3, f"נמצאו {len(lines)} קריאות במקום שלוש:\n{calls}"
    assert lines[0].startswith("CALL: issue create ")
    assert "--label ci,task" in lines[0], "התוויות החוזרות לא צורפו"
    assert "--milestone סדר לריפו" in lines[0]
    assert lines[1].startswith("CALL: project item-add 2 --owner o ")
    # הקריאה החוזרת — שלושת השדות בדיוק, כפי ש-ISSUES.md מורה.
    assert lines[2].startswith("CALL: issue view 999 ")
    for field in ("labels", "milestone", "projectItems"):
        assert field in lines[2], f"הקריאה החוזרת אינה קוראת את {field}"
    assert "https://github.com/o/r/issues/999" in proc.stdout


@requires_sh
@pytest.mark.parametrize(
    "var, field",
    [("FAKE_LABELS", "labels"),
     ("FAKE_MILESTONE", "milestone"),
     ("FAKE_PROJECT", "projectItems")],
)
def test_an_empty_field_in_the_readback_fails_loudly_and_keeps_the_issue(
    tmp_path, var, field
):
    """שדה שחזר ריק = כישלון שנוקב בשם השדה ומדפיס את ה-URL.

    ה-Issue **אינו נמחק**: חצי-מוגמר שמדווח עדיף על נעלם בשקט.
    """
    proc, _ = run_tool(
        tmp_path, "--title", "t", "--label", "ci", "--milestone", "m",
        "--repo", "o/r", **{var: "0"},
    )
    assert proc.returncode != 0, f"{field} חזר ריק והכלי יצא באפס"
    assert field in proc.stderr, f"ההודעה אינה נוקבת בשם {field}: {proc.stderr}"
    assert "https://github.com/o/r/issues/999" in proc.stderr, (
        "ה-URL לא הודפס — אין למי לחזור כדי להשלים"
    )
    assert "issue delete" not in source(), "הכלי מוחק Issue חצי-מוגמר"


@requires_sh
def test_a_dependency_is_registered_as_a_link_and_read_back(tmp_path):
    """‏`blocked by` נרשם כקשר, לא כטקסט (CONTRIBUTING.md).

    ה-API מצפה ל-id הפנימי של ה-Issue החוסם ולא למספר שלו — שתי קריאות
    נפרדות, ולכן שתיהן נבדקות. גם כאן הבקשה שיצאה באפס אינה הקשר עצמו:
    הכלי קורא את התלויות בחזרה.
    """
    proc, calls = run_tool(
        tmp_path, "--title", "t", "--label", "ci", "--milestone", "m",
        "--blocked-by", "#77", "--repo", "o/r",
    )
    assert proc.returncode == 0, proc.stderr
    # ה-id נשלף לפני ה-POST, ולא נשלח מספר ה-Issue במקומו.
    assert "CALL: api repos/o/r/issues/77 --jq .id" in calls, calls
    post = [ln for ln in calls.splitlines() if "--method POST" in ln]
    assert post, f"לא נשלחה בקשת POST לרישום התלות:\n{calls}"
    assert "dependencies/blocked_by" in post[0]
    assert "issue_id=77" not in post[0], "נשלח מספר ה-Issue במקום ה-id"
    # והקריאה החוזרת על התלות עצמה.
    assert any("dependencies/blocked_by --jq length" in ln
               for ln in calls.splitlines()), (
        f"התלות נרשמה ולא נקראה בחזרה:\n{calls}"
    )


@requires_sh
@pytest.mark.parametrize("bad", ["xyz", "#", "12x"])
def test_a_dependency_that_is_not_an_issue_number_is_refused_before_gh(tmp_path, bad):
    proc, calls = run_tool(
        tmp_path, "--title", "t", "--label", "ci", "--milestone", "m",
        "--blocked-by", bad, "--repo", "o/r",
    )
    assert proc.returncode != 0
    assert calls == "", f"‏gh נקרא עם --blocked-by לא תקין: {calls}"


@requires_sh
def test_the_repo_comes_from_the_git_remote_and_works_on_another_repo(tmp_path):
    """לא מוקשח: ריפו אחר לגמרי (otogit) מייצר --repo אחר.

    הבדיקה מקימה ריפו זמני עם origin משלו ומריצה שם את הכלי. אילו
    השם היה קשיח, ה-Issue היה נפתח בריפו של ImageCtl — בשקט.
    """
    other = tmp_path / "otogit"
    other.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "remote", "add", "origin", "https://github.com/someone/otogit.git"],
    ):
        subprocess.run(cmd, cwd=str(other), check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)

    proc, calls = run_tool(
        tmp_path, "--title", "t", "--label", "ci", "--milestone", "m", cwd=other,
    )
    assert proc.returncode == 0, proc.stderr
    create = [ln for ln in calls.splitlines() if ln.startswith("CALL: issue create")][0]
    assert "--repo someone/otogit" in create, create
    assert "ImageCtl" not in create, "שם הריפו של הפרויקט דלף לריפו אחר"
    # הבעלים ללוח נגזר מאותו מקור, ולא נשאר NadavOked.
    add = [ln for ln in calls.splitlines() if "project item-add" in ln][0]
    assert "--owner someone" in add, add
