"""אף סקריפט בריפו אינו בונה שומר על `! cmd` כפקודה עצמאית (#231).

הבאג שהוליד את הטסט הזה: ב-POSIX ‏`set -e` **אינו** חל על pipeline
שמתחיל ב-`!`. סקריפט שכתוב כך —

    set -eu
    ! grep -R -nE '(dd|wipefs|mkfs)' overlay/
    echo "guard: PASS"

— מדפיס `PASS` ויוצא 0 **דווקא כשה-grep מצא** את מה שהוא חיפש. זה
המבנה שהפיל את השומר של חבילת ה-diskless-PXE, וזה בדיוק הדפוס
שחוזר בפרויקט מאז #33: בדיקה שכשהיא נכשלת היא נראית כמו הצלחה.

הריפו נבדק ידנית ב-2026-09-02 והיה **נקי**. הטסט הזה אינו מתקן דבר —
הוא מונע את החזרה. שער בלי בקרה שלילית הוא הבטחה, ולכן
`test_the_scanner_can_actually_fail` מזין לסורק קובץ מסונתז שמכיל את
הדפוס ודורש שיימצא: סורק שלא יודע להיכשל שווה בדיוק כמו השומר שהוא בא
להחליף.

**מה שאינו נחשב ממצא:** ‏`if ! grep -q ...; then` ו-`while ! ping ...`
תקינים לחלוטין — שם הסטטוס נצרך על ידי `if`/`while`, ו-`set -e` ממילא
אינו אמור לחול. הסורק מחפש `!` בתחילת **פקודה עצמאית** בלבד: תחילת
שורה, או מיד אחרי `;`.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: הספריות שבהן רץ קוד מעטפת. ‏`vendor/` נכלל: קוד חיצוני אינו פטור.
SCANNED_DIRS = ("agent", "install", "tools", "tests", "vendor", "boot")

#: ‏`!` שפותח פקודה עצמאית — בתחילת שורה, אחרי `;`, ואחרי `&&`/`||`.
#:
#: ‏`&&` ו-`||` נכללים כי גם שם הסטטוס נבלע: ‏POSIX קובע ש-`set -e`
#: אינו חל על אף איבר ב-AND-OR list פרט לאחרון, והאחרון כאן הוא
#: ‏`! cmd` — שגם עליו הוא אינו חל. כלומר `setup && ! grep -q x`
#: ואחריו `echo PASS` הוא בדיוק #231 בתחפושת.
#:
#: לא נתפס: ‏`[ ! -f x ]`, ‏`!=`, ‏`#!/bin/sh`, ו-`\!` של find.
BARE_NEGATION = re.compile(r"(?:^|;|&&|\|\|)[ \t]*![ \t]+[^ \t;&|=]", re.MULTILINE)

#: מילות פתיחה שהופכות את שאר השורה ל**תנאי**. שם `!` תקין לחלוטין:
#: הסטטוס נצרך על ידי `if`/`while`, ו-`set -e` ממילא אינו אמור לחול.
CONDITIONAL = re.compile(r"^(if|elif|while|until)\b")

#: מוצא מפורש שנסקר והוכרע. ריק היום, ובכוונה.
ALLOWED: frozenset[str] = frozenset()


def _is_shell_script(path: Path) -> bool:
    if path.suffix == ".sh":
        return True
    if path.suffix:
        return False
    try:
        first = path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
    except OSError:
        return False
    return first.startswith("#!") and ("sh" in first or "bash" in first)


def _shell_scripts() -> list[Path]:
    found: list[Path] = []
    for name in SCANNED_DIRS:
        base = REPO / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and _is_shell_script(path):
                found.append(path)
    return found


def _hits(text: str) -> list[tuple[int, str]]:
    out = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # שורה שפותחת בתנאי היא תנאי לכל אורכה — גם ה-`!` שאחרי `&&`
        # בתוכה. בלי החרגה כזאת `if setup && ! grep -q x; then` היה
        # נחסם, וזה כתיב תקין.
        if CONDITIONAL.match(stripped):
            continue
        if BARE_NEGATION.search(line):
            out.append((number, line.strip()))
    return out


def test_the_scan_actually_covers_shell_scripts():
    """סורק שלא מצא קובץ אחד עובר תמיד. מספר הקבצים הוא הראיה החיובית."""
    scripts = _shell_scripts()
    assert len(scripts) >= 20, (
        f"נסרקו {len(scripts)} סקריפטים בלבד תחת {SCANNED_DIRS} — "
        "שער שסורק אפס קבצים ירוק תמיד"
    )


def test_no_bare_negated_command_guards():
    """הממצא עצמו: אף סקריפט אינו משתמש ב-`! cmd` כפקודה עצמאית."""
    findings = []
    for path in _shell_scripts():
        rel = path.relative_to(REPO).as_posix()
        if rel in ALLOWED:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in _hits(text):
            findings.append(f"{rel}:{number}: {line}")
    assert not findings, (
        "‏`! cmd` כפקודה עצמאית תחת `set -e` — הסטטוס נבלע והשורה הבאה "
        "רצה כאילו הכל תקין (#231):\n" + "\n".join(findings)
    )


def test_the_scanner_can_actually_fail(tmp_path):
    """בקרה שלילית לסורק עצמו — על הקוד המדויק שהפיל את #231."""
    bad = tmp_path / "guard.sh"
    bad.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "! grep -R -nE '(dd|wipefs|mkfs)' overlay/\n"
        "echo 'guard: PASS'\n",
        encoding="utf-8",
    )
    hits = _hits(bad.read_text(encoding="utf-8"))
    assert hits, "הסורק לא זיהה את הדפוס שהוא נכתב בשבילו"
    assert hits[0][0] == 3


def test_the_scanner_catches_negation_after_and_or(tmp_path):
    """‏`&&` ו-`||` הם אותו חור בדיוק — ‏`set -e` אינו חל שם.

    הגרסה הראשונה של הסורק עגנה רק על תחילת שורה ועל `;`, ולכן
    ‏`setup && ! grep -q x` הייתה חומקת. נמצא בסקירת #299.
    """
    for line in ("setup && ! grep -q foo bar", "setup || ! test -f bar"):
        script = tmp_path / "g.sh"
        script.write_text(f"#!/bin/sh\nset -eu\n{line}\necho PASS\n", encoding="utf-8")
        assert _hits(script.read_text(encoding="utf-8")), f"{line!r} חמק מהסורק"


def test_the_scanner_does_not_flag_a_condition(tmp_path):
    """ולא תיקון-יתר: ‏`!` בתוך תנאי הוא כתיב תקין ואסור לחסום אותו."""
    good = tmp_path / "ok.sh"
    good.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "if ! grep -q foo bar; then echo missing; fi\n"
        "while ! ping -c1 host; do sleep 1; done\n"
        '[ ! -f /etc/passwd ] && echo none\n'
        'if [ "$a" != "$b" ]; then echo differ; fi\n'
        "if setup && ! grep -q foo bar; then echo missing; fi\n"
        "until ping -c1 h || ! test -f stop; do sleep 1; done\n"
        "find . \\! -name '*.tmp' -print\n",
        encoding="utf-8",
    )
    assert _hits(good.read_text(encoding="utf-8")) == []
