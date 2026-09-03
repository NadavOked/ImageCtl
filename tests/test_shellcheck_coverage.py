"""כל קובץ מעטפת בריפו נסרק על ידי shellcheck.

הרשימה ב-`.github/workflows/shellcheck.yml` נכתבת ביד, ולכן כל קובץ
שנוסף אחריה נופל מהכיסוי **בשקט** — הוא לא מופיע כאדום, הוא פשוט
לא נבדק. ‏#247: שמונה קבצים ב-`tools/` היו במצב הזה.

זה בדיוק הדפוס שהפרויקט אוסר: היעדר סימן כישלון נספר כהצלחה.

הטסט אינו מריץ `shellcheck` — הוא רץ גם על ווינדוס, שם אין בינארי.
הוא בודק **כיסוי**: שכל `*.sh` בריפו נתפס על ידי אחד הנתיבים
שמופיעים בקובץ ה-workflow.
"""
import glob
import io
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "shellcheck.yml"

# קבצים שמוחרגים במכוון. כל חריג חייב סיבה — "פשוט לא ברשימה" אינו
# חריג, הוא חור. מפתח שמסתיים ב-`/**` מחריג תת-עץ שלם.
EXEMPT: dict = {
    "vendor/**": (
        "‏`vendor/diskless-pxe` היא חבילה חיצונית שנכנסה verbatim (#231) "
        "ואינה מחוברת לשום מסלול אתחול. ‏36 קבצי המעטפת שבה **כן** נסרקו: "
        "‏`shellcheck` על מעבדת ה-VM (10.98.10.8, 2026-09-02) החזיר 13 "
        "ממצאים, מהם **0 ברמת `error`** — ‏6×SC1007 (הניב `CDPATH= cd`), "
        "‏3×SC2086, ‏SC2046, ‏SC2044, ‏SC2013, ‏SC1091. הסריקה הזאת ידנית "
        "וחד-פעמית, ולכן זה חור מוצהר ולא כיסוי: הוספת התת-עץ ל-"
        "‏`.github/workflows/shellcheck.yml` פתוחה כ-Issue נפרד, וכל מי "
        "שמחבר את החבילה למסלול אתחול חייב לסגור אותה קודם."
    ),
}


def _is_exempt(path: str) -> bool:
    for pattern in EXEMPT:
        if pattern.endswith("/**"):
            if path.startswith(pattern[:-2]):
                return True
        elif path == pattern:
            return True
    return False

CONT = re.compile(r"\\\s*\n\s*")          # המשך שורה של מעטפת
QUOTES = "\"'"


def _flat():
    """תוכן ה-workflow אחרי פירוק המשכי שורה."""
    return CONT.sub(" ", io.open(WF, encoding="utf-8").read())


def _paths():
    """כל נתיב מעטפת שמוזכר ב-workflow, מחוץ להערות.

    נאסף מכל שורה ולא רק משורות `shellcheck`, כי רשימת קבצים יכולה
    לשבת בלולאת `for`. הגרסה הראשונה קראה רק שורות shellcheck ולכן
    דיווחה שקבצים אינם מכוסים בזמן שהם כן — הבודק עצמו היה הבאג.
    """
    flat = _flat()
    assert re.search(r"\bshellcheck\b", flat), "אין קריאה ל-shellcheck"
    out = set()
    for line in flat.split("\n"):
        if line.lstrip().startswith("#"):
            continue
        for tok in re.split(r"\s+", line):
            tok = tok.strip().strip(QUOTES)
            if not tok or tok.startswith("-") or "/" not in tok:
                continue
            if tok.endswith(".sh") or tok.endswith("*"):
                out.add(tok)
    for extra in ("agent/imagectl-agent", "agent/init"):
        if extra in flat:
            out.add(extra)
    return out


def _covered():
    args = _paths()
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        out = set()
        for a in args:
            out |= {p.replace(os.sep, "/") for p in glob.glob(a)}
    finally:
        os.chdir(cwd)
    return args, out


def _all_shell():
    cwd = os.getcwd()
    os.chdir(ROOT)
    try:
        return {p.replace(os.sep, "/")
                for p in glob.glob("**/*.sh", recursive=True)
                if not p.startswith(".git")}
    finally:
        os.chdir(cwd)


def test_the_workflow_actually_names_files():
    """אפס ארגומנטים פירושו שהפרסור נשבר — לא ש'הכול מכוסה'."""
    args, covered = _covered()
    assert len(args) >= 4, f"רק {len(args)} נתיבים — הפרסור כנראה נשבר"
    assert len(covered) >= 20, f"רק {len(covered)} קבצים מכוסים — חשוד"


def test_there_are_shell_files_to_check():
    assert len(_all_shell()) >= 25, "פחות מדי קבצי sh — הסריקה לא רצה"


def test_every_shell_file_is_scanned():
    _, covered = _covered()
    missing = sorted(p for p in _all_shell() - covered if not _is_exempt(p))
    assert not missing, (
        "קבצי מעטפת שאינם נסרקים על ידי shellcheck: %s. "
        "הוסף אותם ל-.github/workflows/shellcheck.yml, או הצהר עליהם "
        "ב-EXEMPT כאן עם סיבה. קובץ שאינו נסרק אינו 'נקי' — הוא לא "
        "נבדק (#247)." % missing)


def test_the_shell_dialect_is_derived_and_not_guessed():
    """הסוג נגזר מה-shebang.

    הניסיון הראשון פיצל את הקבצים ל-bash ול-sh לפי ניחוש. כל השמונה
    היו bash, וה-CI נפל על bashisms שהוצהרו כ-POSIX. החלטה שאפשר
    לגזור אין סיבה לנחש — והטסט שומר שלא נחזור לנחש.
    """
    flat = _flat()
    assert "head -1" in flat and "shebang" not in flat.split("\n")[0], (
        "השלב אינו גוזר את סוג המעטפת מה-shebang")
    assert re.search(r'shellcheck -s "\$\w+"', flat), (
        "‏shellcheck נקרא עם סוג קבוע ולא עם הסוג שנגזר")


def test_exemptions_carry_a_reason():
    for path, reason in EXEMPT.items():
        assert isinstance(reason, str) and reason.strip(), (
            f"{path} מוחרג בלי סיבה — חריג בלי נימוק הוא חור שקט")


def test_no_exemption_is_stale():
    """חריג שאינו תואם לאף קובץ הוא חור פתוח לקבצים עתידיים.

    ‏`vendor/**` בפרט: אם התת-עץ הוסר או שונה שמו, החריג נשאר ומכסה
    מראש כל מה שיגיע לשם אחר כך — הרשאה שאיש לא הכריע עליה.
    """
    shell = _all_shell()
    for pattern in EXEMPT:
        assert any(_is_exempt(p) for p in shell if _match(pattern, p)), (
            f"{pattern} אינו תואם לאף קובץ מעטפת — חריג מת שיכסה קבצים "
            "שטרם נכתבו. מחק אותו.")


def _match(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return path == pattern
