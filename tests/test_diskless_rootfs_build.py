"""‏`vendor/diskless-pxe/rootfs/build.sh` אינו מכריז הצלחה על עץ ריק (#385).

הצורה המקורית האכילה את `apk add` מתוך הצבת פקודה:

    apk --root "$ROOT" --initdb add $(grep -v '^#' packages.txt | tr '\\n' ' ')

שני מנגנוני בליעה, וכל אחד לבדו מספיק: קוד היציאה של pipeline הוא של
`tr` (תמיד 0), ו-POSIX זורק את הסטטוס של `$( )` — `set -e` לא יורה.
`packages.txt` חסר, או שכולו הערות, והסקריפט מדפיס `Rootfs staged at`
על עץ בלי `curl`/`partclone`/`zstd`.

הטסט מריץ את הסקריפט **כתהליך אמיתי תחת מעטפת POSIX אמיתית**, ולא
קורא את הקוד שלו. ‏`apk` מוחלף בכפיל שיוצא 0 תמיד — כולל בלי חבילות —
כי זה בדיוק מה ש-Alpine עושה, וזה מה שהפך את הבליעה להצלחה נראית.
הסקריפט המלא דורש Alpine-as-root; זה לא רץ כאן, ומוצהר.

שלושת מצבי `grep` נבדקים בנפרד כי הם שלושה דברים שונים:
‏0 + חבילות = הצלחה עם מספר · ‏1 / אפס חבילות אחרי הסינון = כישלון ·
‏2 (קובץ חסר) = כישלון. "לא הצלחנו לקרוא" אינו "נבנה בהצלחה".
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "vendor" / "diskless-pxe" / "rootfs" / "build.sh"
PACKAGES = BUILD.parent / "packages.txt"
START = "#!/bin/sh\n# overlay entry the script chmod +x-s\n"

APK_STUB = """#!/bin/sh
# Alpine `apk add` with no packages exits 0. The stub does the same, so a
# swallowed empty list still looks like success — the bug, not a stand-in.
log=${APK_LOG:-}
if [ -n "$log" ]; then
    for a in "$@"; do
        printf '%s\\n' "$a" >> "$log"
    done
fi
exit 0
"""


def _posix_shells() -> list[list[str]]:
    """‏המעטפות שהסקריפט ייבדק תחתן — לפחות אחת, אחרת הטסט נכשל.

    ‏`skipif` כאן היה מחזיר בדיוק את הכשל שהטסט בא לתפוס: ריצה ירוקה
    שלא הריצה כלום. ב-CI מותקנות `dash` ו-`busybox`, ובתחנת הפיתוח
    יש `sh` של git-bash.
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
        "למטה היו עוברים בלי להריץ את הסקריפט — כישלון שנראה כמו הצלחה"
    )


def _write_lf(path: Path, text: str, mode: int = 0o644) -> None:
    """כתיבה עם LF בלבד — ‏`Path.write_text` בווינדוס מתרגם ל-CRLF,
    ו-`dash` קורא את ה-CR כחלק מהפקודה."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(mode)


def posix(p: Path | str) -> str:
    text = str(p).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def path_entry(p: Path | str) -> str:
    """נתיב שמתאים לשבת בתוך `PATH`. ‏`C:/...` נחתך במפריד הנקודתיים."""
    return posix(p)


def _staging(root: Path, packages: str | None) -> Path:
    """עץ rootfs מסונתז: הסקריפט האמיתי, overlay מינימלי, packages לפי המקרה."""
    here = root / "rootfs"
    overlay = here / "overlay" / "etc" / "local.d" / "imagectl.start"
    _write_lf(here / "build.sh", BUILD.read_text(encoding="utf-8"), 0o755)
    _write_lf(overlay, START, 0o755)
    if packages is not None:
        _write_lf(here / "packages.txt", packages)
    return here / "build.sh"


def _apk_box(root: Path) -> tuple[Path, Path]:
    box = root / "bin"
    box.mkdir(parents=True)
    _write_lf(box / "apk", APK_STUB, 0o755)
    log = root / "apk.log"
    log.write_text("", encoding="utf-8", newline="\n")
    return box, log


def _run(shell: list[str], build: Path, *, cwd: Path, box: Path, log: Path,
         out: Path, work: Path):
    script = (
        f"export PATH={path_entry(box)!r}:/usr/bin:$PATH; "
        f"export APK_LOG={posix(log)!r}; "
        f"export OUT={posix(out)!r}; "
        f"export ROOT={posix(work)!r}; "
        f"{posix(build)!r}"
    )
    return subprocess.run(
        shell + ["-c", script],
        cwd=str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=60,
    )


def _output(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def _apk_args(log: Path) -> list[str]:
    text = log.read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line]


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_missing_packages_txt_is_a_failure_not_a_success(shell, tmp_path):
    """הבקרה השלילית: קובץ חסר חייב לצאת בקוד שאינו אפס, בלי Rootfs staged."""
    build = _staging(tmp_path, None)
    box, log = _apk_box(tmp_path)
    proc = _run(shell, build, cwd=build.parent, box=box, log=log,
                out=tmp_path / "out", work=tmp_path / "work")
    output = _output(proc)
    assert proc.returncode != 0, (
        f"build.sh יצא 0 כש-packages.txt חסר — זה #385 בדיוק.\n{output}"
    )
    assert "Rootfs staged" not in proc.stdout, (
        f"הודפס Rootfs staged על רשימה שלא נקראה:\n{output}"
    )
    assert "could not read packages.txt" in output, (
        f"הסקריפט נכשל בלי לומר ש-packages.txt לא נקרא:\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_comments_only_packages_txt_is_a_failure_not_a_success(shell, tmp_path):
    """קובץ שכולו הערות: grep יוצא 1, וזה כישלון — לא apk add ריק."""
    build = _staging(tmp_path, "# only a comment\n# another\n")
    box, log = _apk_box(tmp_path)
    proc = _run(shell, build, cwd=build.parent, box=box, log=log,
                out=tmp_path / "out", work=tmp_path / "work")
    output = _output(proc)
    assert proc.returncode != 0, (
        f"build.sh יצא 0 על packages.txt שכולו הערות:\n{output}"
    )
    assert "Rootfs staged" not in proc.stdout, (
        f"הודפס Rootfs staged על אפס חבילות:\n{output}"
    )
    assert "has no packages" in output, (
        f"הסקריפט נכשל בלי לומר שהרשימה ריקה מחבילות:\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_whitespace_only_packages_txt_is_a_failure_not_a_success(shell, tmp_path):
    """שורות ריקות אחרי הסרת הערות: grep יוצא 0, והספירה חייבת להפיל."""
    build = _staging(tmp_path, "# comment\n\n\n")
    box, log = _apk_box(tmp_path)
    proc = _run(shell, build, cwd=build.parent, box=box, log=log,
                out=tmp_path / "out", work=tmp_path / "work")
    output = _output(proc)
    assert proc.returncode != 0, (
        f"build.sh יצא 0 על packages.txt בלי שמות חבילות:\n{output}"
    )
    assert "Rootfs staged" not in proc.stdout
    assert "has no packages" in output, (
        f"הסקריפט נכשל בלי לומר שהרשימה ריקה מחבילות:\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_success_prints_the_package_count(shell, tmp_path):
    """הצלחה נושאת ראיה חיובית: מספר החבילות שהוזנו ל-apk, לא רק staged."""
    build = _staging(tmp_path, "# hdr\ncurl\nzstd\n")
    box, log = _apk_box(tmp_path)
    work = tmp_path / "work"
    proc = _run(shell, build, cwd=build.parent, box=box, log=log,
                out=tmp_path / "out", work=work)
    output = _output(proc)
    assert proc.returncode == 0, f"שתי חבילות תקינות נכשלו:\n{output}"
    assert "added 2 packages" in proc.stdout, (
        f"הצלחה בלי מספר החבילות — אין ראיה חיובית:\n{output}"
    )
    assert "Rootfs staged at" in proc.stdout
    args = _apk_args(log)
    assert "curl" in args and "zstd" in args, (
        f"apk לא קיבל את החבילות שנקראו:\n{args}\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_packages_txt_is_resolved_from_the_script_not_cwd(shell, tmp_path):
    """הרצה מתיקייה אחרת מוצאת את packages.txt ליד $0, לא ב-cwd."""
    build = _staging(tmp_path, "curl\npartclone\n")
    box, log = _apk_box(tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    proc = _run(shell, build, cwd=cwd, box=box, log=log,
                out=tmp_path / "out", work=tmp_path / "work")
    output = _output(proc)
    assert proc.returncode == 0, (
        f"הרצה מ-cwd אחר נכשלה — packages.txt כנראה עדיין יחסי:\n{output}"
    )
    args = _apk_args(log)
    assert "curl" in args and "partclone" in args, (
        f"apk רץ בלי החבילות של הסקריפט — נקרא packages.txt מה-cwd:\n"
        f"{args}\n{output}"
    )
    assert "added 2 packages" in proc.stdout


def test_the_shipped_packages_txt_lists_packages():
    """הרשימה עצמה אינה ריקה. PASS על קובץ הערות בלבד היה אותו באג."""
    lines = [
        line.strip()
        for line in PACKAGES.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines, f"{PACKAGES} ריק מחבילות — build.sh היה נכשל בצדק"
    assert "curl" in lines and "partclone" in lines and "zstd" in lines
