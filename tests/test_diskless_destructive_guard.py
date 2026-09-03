"""‏השומר של `vendor/diskless-pxe` נגד פקודות הרסניות — נכשל כשצריך (#231).

הגרסה שהגיעה בחבילה **לא יכלה להיכשל**. תחת `set -eu` היא הריצה

    ! grep -R -nE '(dd|wipefs|mkfs|sgdisk|parted)' "$ROOT/rootfs/overlay" ...

וב-POSIX ‏`set -e` **אינו** חל על pipeline שמתחיל ב-`!`. לכן דווקא
במקרה שהשומר קיים בשבילו — grep שמצא התאמה — הסקריפט הדפיס את
השורה ההרסנית, המשיך הלאה, והכריז `PASS` עם קוד יציאה 0.

הטסט הזה מריץ את השומר **כתהליך אמיתי תחת מעטפת POSIX אמיתית**, ולא
קורא את הקוד שלו: מה שקובע הוא קוד היציאה שה-CI רואה, ובאג כזה חי
בדיוק בפער שבין "מה שכתוב" ל"מה שהמעטפת עושה עם זה". השומר מקבל עץ
מסונתז ב-tmp ולא את החבילה עצמה, כדי שהבקרה השלילית לא תדרוש לכתוב
פקודה הרסנית לתוך הריפו.

שלושת המצבים נבדקים בנפרד, כי הם שלושה דברים שונים ואסור לקפל אותם:
‏0 = נמצאה פקודה הרסנית (כישלון) · 1 = נסרק ונקי (מעבר) · כל השאר =
הסריקה עצמה נשברה (כישלון). "לא הצלחנו לבדוק" אינו "בדקנו, הכל תקין".
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "vendor" / "diskless-pxe"
GUARD = PACKAGE / "tests" / "no-destructive-defaults.sh"
CI_RUN = PACKAGE / "ci" / "run.sh"

DESTRUCTIVE = "#!/bin/sh\ndd if=/dev/zero of=/dev/sda\n"
HARMLESS = "#!/bin/sh\necho hello\n"

#: אותה פקודה, בהזחת **טאב** בתוך `if`. המחלקה המקורית בתבנית הייתה
#: ‏`[;&| ]` — רווח מילולי — ולכן הצורה הזאת חמקה מהשומר. נמצא בסקירת
#: ‏#299. חור בצורת תו לבן בשומר גרוע משומר שאינו קיים, כי הוא נקרא
#: ככיסוי.
TAB_INDENTED = "#!/bin/sh\nif [ -n \"$x\" ]; then\n\tdd if=/dev/zero of=/dev/sda\nfi\n"


def _posix_shells() -> list[list[str]]:
    """‏המעטפות שהשומר ייבדק תחתן — לפחות אחת, אחרת הטסט נכשל.

    ‏`skipif` כאן היה מחזיר בדיוק את הכשל שהטסט בא לתפוס: ריצה ירוקה
    שלא בדקה כלום. ב-CI מותקנות `dash` ו-`busybox` (‏`tests.yml`),
    ובתחנת הפיתוח יש `sh` של git-bash.
    """
    shells: list[list[str]] = []
    for name in ("dash", "sh", "ash"):
        found = shutil.which(name)
        if found:
            shells.append([found])
    busybox = shutil.which("busybox")
    if busybox:
        shells.append([busybox, "ash"])
    return shells


SHELLS = _posix_shells()
SHELL_IDS = [" ".join(Path(part).name for part in argv) for argv in SHELLS]


def test_a_posix_shell_is_available():
    """בלי מעטפת POSIX אין מה לבדוק — וזה כישלון, לא דילוג."""
    assert SHELLS, (
        "לא נמצאה אף מעטפת POSIX (dash/sh/ash/busybox). בלעדיה הטסטים "
        "למטה היו עוברים בלי להריץ את השומר — כישלון שנראה כמו הצלחה"
    )


def _write_lf(path: Path, text: str) -> None:
    """כתיבה עם LF בלבד.

    ‏`Path.write_text` בווינדוס מתרגם `\\n` ל-`\\r\\n`, ו-`dash` קורא
    את ה-CR כחלק מהפקודה: ‏`set +e\\r` הופך ל-`set: Illegal option -`.
    הטסט היה נכשל על עותק שהוא עצמו שיבש, לא על השומר.
    """
    path.write_text(text, encoding="utf-8", newline="\n")


def _package_tree(root: Path, overlay_files: dict[str, str]) -> Path:
    """עץ חבילה מסונתז: השומר גוזר את שורש הסריקה מ-`$0`, לא מ-cwd."""
    (root / "tests").mkdir(parents=True)
    (root / "rootfs" / "overlay").mkdir(parents=True)
    (root / "ipxe").mkdir(parents=True)
    _write_lf(root / "ipxe" / "boot.ipxe", "#!ipxe\necho placeholder\n")
    for name, body in overlay_files.items():
        _write_lf(root / "rootfs" / "overlay" / name, body)
    guard = root / "tests" / GUARD.name
    _write_lf(guard, GUARD.read_text(encoding="utf-8"))
    return guard


def _run(argv: list[str], cwd: Path | None = None, env: dict[str, str] | None = None):
    return subprocess.run(
        argv, cwd=None if cwd is None else str(cwd), env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=120,
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_the_guard_fails_on_a_destructive_default(shell, tmp_path):
    """הבקרה השלילית: ‏`dd if=/dev/zero of=/dev/sda` חייב להפיל את השומר."""
    guard = _package_tree(tmp_path, {"evil.sh": DESTRUCTIVE})
    proc = _run(shell + [str(guard)])
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"השומר יצא 0 על פקודה הרסנית — זה #231 בדיוק.\n{output}"
    )
    assert "PASS" not in proc.stdout, f"הודפס PASS על קלט הרסני:\n{output}"
    assert "dd if=/dev/zero of=/dev/sda" in output, (
        f"השומר נכשל בלי להראות מה הוא מצא:\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_the_guard_fails_on_a_tab_indented_destructive_default(shell, tmp_path):
    """הזחת טאב אינה מסתירה כלום — התבנית קולטת כל תו לבן, לא רק רווח."""
    guard = _package_tree(tmp_path, {"evil.sh": TAB_INDENTED})
    proc = _run(shell + [str(guard)])
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"‏dd בהזחת טאב חמק מהשומר:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_the_guard_passes_on_a_clean_tree(shell, tmp_path):
    """ולא תיקון-יתר: עץ נקי עובר, עם מספר הקבצים שנסרקו כראיה חיובית."""
    guard = _package_tree(tmp_path, {"ok.sh": HARMLESS})
    proc = _run(shell + [str(guard)])
    assert proc.returncode == 0, f"עץ נקי נכשל:\n{proc.stdout}{proc.stderr}"
    assert "PASS" in proc.stdout
    assert "2 files scanned" in proc.stdout, (
        f"PASS בלי לומר כמה נסרק אינו ראיה חיובית:\n{proc.stdout}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_missing_scan_root_is_a_failure_not_a_pass(shell, tmp_path):
    """שורש סריקה חסר = לא נבדק. ‏grep מחזיר 2, וזה חייב להפיל."""
    guard = _package_tree(tmp_path, {"ok.sh": HARMLESS})
    shutil.rmtree(tmp_path / "ipxe")
    proc = _run(shell + [str(guard)])
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"שורש חסר הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_an_empty_tree_is_a_failure_not_a_pass(shell, tmp_path):
    """‏0 קבצים נסרקו = 0 הגנה. ‏grep מחזיר 1 בדיוק כמו על עץ נקי."""
    guard = _package_tree(tmp_path, {})
    (tmp_path / "ipxe" / "boot.ipxe").unlink()
    proc = _run(shell + [str(guard)])
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"עץ ריק הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_the_shipped_package_is_clean(shell):
    """ועל החבילה האמיתית — השומר עובר. אחרת התיקון שינה את המשמעות."""
    proc = _run(shell + [str(GUARD)])
    assert proc.returncode == 0, f"החבילה עצמה נכשלת:\n{proc.stdout}{proc.stderr}"
    assert "files scanned, 0 hits" in proc.stdout


#: הכלים ש-`ci/run.sh` ושרשרת הטסטים שלו חייבים כדי לרוץ בכלל.
NEEDED = ("sh", "find", "grep", "xargs", "wc", "tr", "sed", "cat", "mktemp")

#: כל קישור שלא נוצר בחוות ה-symlink, לדיווח אם בסוף חסר כלי חיוני.
_farm_failures: list[str] = []


def _path_without_shellcheck(farm: Path) -> str:
    """‏PATH שאין בו `shellcheck` — אבל יש בו כל השאר.

    הגרסה הראשונה פשוט הסירה מ-PATH כל תיקייה שיש בה shellcheck.
    על מעבדת ה-VM הוא יושב ב-`/usr/bin`, ולכן ההסרה לקחה איתה גם את
    `find`, ‏`grep` ו-`sh` — ‏`ci/run.sh` נפל על "command not found"
    והטסט "עבר את הטענה על קוד היציאה" מהסיבה הלא נכונה. כלומר הבודק
    עצמו היה בדיוק הבאג שהוא נכתב בשבילו.

    במקום זה: חוות symlink-ים לכל מה שיש בתיקיות שהוסרו, פרט
    ל-`shellcheck` עצמו, בראש ה-PATH.
    """
    entries = [e for e in os.environ.get("PATH", "").split(os.pathsep) if e]
    keep = [e for e in entries if not shutil.which("shellcheck", path=e)]
    stripped = [e for e in entries if e not in keep]
    if not stripped:
        return os.pathsep.join(keep)
    farm.mkdir(parents=True, exist_ok=True)
    for directory in stripped:
        try:
            names = os.listdir(directory)
        except OSError as exc:
            _farm_failures.append(f"listdir {directory}: {exc}")
            continue
        for name in names:
            if name == "shellcheck" or name.startswith("shellcheck."):
                continue
            link = farm / name
            if link.exists():
                continue
            try:
                os.symlink(Path(directory) / name, link)
            except OSError as exc:
                # ‏`pass` ריק כאן היה בדיוק הדפוס שה-PR הזה מסיר: כל
                # קישור שנכשל נרשם, וההודעה של `NEEDED` למטה מציגה אותו
                # אם משהו חיוני חסר בסוף.
                _farm_failures.append(f"symlink {name}: {exc}")
    return os.pathsep.join([str(farm)] + keep)


@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_ci_run_fails_when_shellcheck_is_missing(shell, tmp_path):
    """החצי השני של #231: ‏`ci/run.sh` דילג על shellcheck ויצא 0.

    זה בדיוק הכלי שהיה תופס את `SC2251` על השומר. ‏CI ירוק שלא הריץ
    ניתוח סטטי הוא CI שמדווח על בדיקה שלא קרתה.
    """
    env = dict(os.environ)
    env["PATH"] = _path_without_shellcheck(tmp_path / "bin")
    assert shutil.which("shellcheck", path=env["PATH"]) is None, (
        "לא הצלחתי להרכיב PATH בלי shellcheck — הטסט לא היה בודק כלום"
    )
    missing = [t for t in NEEDED if shutil.which(t, path=env["PATH"]) is None]
    assert not missing, (
        f"‏PATH המצומצם איבד גם {missing} — ‏ci/run.sh היה נופל על "
        "'command not found' והטסט היה 'עובר' מהסיבה הלא נכונה. "
        f"כשלי חוות הקישורים: {_farm_failures[:10]}"
    )
    proc = _run(shell + [str(CI_RUN)], cwd=PACKAGE, env=env)
    output = proc.stdout + proc.stderr
    assert "shellcheck is not installed" in output, (
        f"‏ci/run.sh לא הגיע לענף ה-shellcheck, או שהוא עדיין שותק:\n{output}"
    )
    assert proc.returncode != 0, f"‏ci/run.sh יצא 0 בלי ניתוח סטטי:\n{output}"
    assert "not installed; skipped" not in output, "ההודעה השקטה חזרה"
