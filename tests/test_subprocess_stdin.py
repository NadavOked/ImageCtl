"""שער: תהליך-בן בטסטים חייב stdin= מפורש (#541, #14).

הכלל מתועד ב-CLAUDE.md ולא נאכף. מדידת grep ספרה 68 אתרים כי סרקה
שורה בודדת; אחרי #560 נשארו חורים בקבצים חדשים. תיעוד אינו שער.

חריג יחיד: ``input=`` — פייתון אוסר אותו יחד עם ``stdin=``.
PIPE / קובץ / DEVNULL כבר נושאים ``stdin=`` ולכן עוברים.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
_FUNCS = frozenset({"run", "Popen", "call", "check_output", "check_call"})


def _imported(tree: ast.AST) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "subprocess":
                    modules.add(alias.asname or "subprocess")
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                if alias.name in _FUNCS:
                    names.add(alias.asname or alias.name)
    return modules, names


def _is_spawn(node: ast.Call, modules: set[str], names: set[str]) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id in modules and func.attr in _FUNCS
    return isinstance(func, ast.Name) and func.id in names


def missing_stdin(source: str) -> list[int]:
    """שורות שבהן subprocess נקרא בלי stdin= ובלי input=."""
    tree = ast.parse(source)
    modules, names = _imported(tree)
    found: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_spawn(node, modules, names):
            continue
        kws = {kw.arg for kw in node.keywords if kw.arg}
        if "stdin" not in kws and "input" not in kws:
            found.append(node.lineno)
    return found


def test_the_scanner_flags_a_call_without_stdin():
    """עיקרון 5 על הסורק עצמו: בלי זה 'אין הפרות' יכול להיות סורק שבור."""
    src = (
        "import subprocess\n"
        "subprocess.run(['true'])\n"
        "subprocess.run(['true'], stdin=subprocess.DEVNULL)\n"
        "subprocess.run(['true'], input=b'')\n"
    )
    assert missing_stdin(src) == [2], missing_stdin(src)


def test_no_test_file_omits_stdin():
    """אתר חדש בלי stdin= נופל כאן בשמו, בלי שמישהו יזכור לעדכן רשימה."""
    offenders: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        lines = missing_stdin(path.read_text(encoding="utf-8"))
        offenders.extend(f"{path.name}:{n}" for n in lines)
    assert not offenders, (
        "subprocess בטסט בלי stdin= מפורש — בווינדוס זה WinError 50 "
        f"בריצה רב-קבצית (#14, #541):\n  " + "\n  ".join(offenders)
    )
