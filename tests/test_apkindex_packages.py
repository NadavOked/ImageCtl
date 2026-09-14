"""בדיקת זמינות חבילות ה-rootfs מול APKINDEX — נכשלת כשחבילה מוצהרת חסרה (#306).

`check-runtime-deps.sh` חיפש שמות ב-`packages.txt` והדפיס WARN, ואז יצא 0
בכל מקרה — גם כש-`cog` אינו קיים באף מאגר Alpine. הכלי שאמור לתפוס את זה
דיווח ירוק. עיקרון 5: חבילה מוצהרת שאינה ב-APKINDEX היא כישלון, כמו
`REQUIRED_MODULES` ב-`build_initramfs.sh` (#78). אין אינדקס = לא נבדק.

הטסט מריץ את הסקריפט כתהליך POSIX אמיתי מול אינדקס מסונתז, בלי רשת
ובלי `apk`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "vendor" / "diskless-pxe" / "tools" / "check-runtime-deps.sh"


def _posix_shells() -> list[list[str]]:
    """לפחות מעטפת אחת, אחרת הטסט נכשל ולא מדלג (#52)."""
    shells: list[list[str]] = []
    if os.name == "nt":
        git = Path(r"C:\Program Files\Git\usr\bin\sh.exe")
        if git.exists():
            shells.append([str(git)])
    for name in ("dash", "sh", "ash"):
        found = shutil.which(name)
        if found and [found] not in shells:
            shells.append([found])
    busybox = shutil.which("busybox")
    if busybox:
        shells.append([busybox, "ash"])
    return shells


SHELLS = _posix_shells()


def test_a_posix_shell_is_available():
    """בלי מעטפת POSIX אין מה לבדוק — וזה כישלון, לא דילוג."""
    assert SHELLS, (
        "לא נמצאה אף מעטפת POSIX (dash/sh/ash/busybox). בלעדיה הטסטים "
        "למטה היו עוברים בלי להריץ את השומר — כישלון שנראה כמו הצלחה"
    )


def _write_lf(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def posix(path: Path) -> str:
    """נתיב ש-Git Bash מקבל בלי המרת `C:` לנקודה-פסיק."""
    s = path.resolve().as_posix()
    if len(s) >= 2 and s[1] == ":":
        return f"/{s[0].lower()}{s[2:]}"
    return s


def _apkindex(*names: str) -> str:
    parts = [f"C:fake\nP:{name}\nV:0.0.1-r0\n" for name in names]
    return "\n".join(parts) + "\n"


def _packages(*names: str) -> str:
    return "".join(f"{n}\n" for n in names)


def _run(tmp_path: Path, packages: str, indexes: list[str] | None) -> subprocess.CompletedProcess:
    pkg = tmp_path / "packages.txt"
    _write_lf(pkg, packages)
    argv = SHELLS[0] + [posix(SCRIPT), posix(pkg)]
    if indexes is not None:
        for i, body in enumerate(indexes):
            idx = tmp_path / f"APKINDEX.{i}"
            _write_lf(idx, body)
            argv.append(posix(idx))
    env = dict(os.environ)
    env["MSYS2_ARG_CONV_EXCL"] = "*"
    git_bin = Path(SHELLS[0][0]).parent
    env["PATH"] = str(git_bin) + os.pathsep + env.get("PATH", "")
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        timeout=60,
        env=env,
    )


@pytest.fixture(autouse=True)
def _need_shell():
    if not SHELLS:
        pytest.fail(
            "לא נמצאה מעטפת POSIX — הטסט לא היה בודק כלום"
        )


def test_a_declared_package_absent_from_apkindex_fails(tmp_path: Path):
    """‏`cog` מוצהר ואינו באינדקס — זה #306, והשומר חייב ליפול."""
    proc = _run(
        tmp_path,
        _packages("alpine-base", "cage", "cog"),
        [_apkindex("alpine-base", "cage")],
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"חבילה חסרה מהאינדקס יצאה 0 — זה #306 בדיוק.\n{output}"
    )
    assert "cog" in output, f"השומר נכשל בלי לנקוב ב-cog:\n{output}"
    assert "missing from APKINDEX" in output, output


def test_declared_packages_present_in_apkindex_pass(tmp_path: Path):
    """ולא תיקון-יתר: מה שיש באינדקס עובר."""
    proc = _run(
        tmp_path,
        _packages("alpine-base", "cage"),
        [_apkindex("alpine-base", "cage", "mesa-dri-gallium")],
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"חבילות קיימות נכשלו:\n{output}"
    assert "missing from APKINDEX" not in output


def test_a_package_in_the_second_index_still_counts(tmp_path: Path):
    """‏main + community: חבילה שנמצאת באינדקס השני אינה חסרה."""
    proc = _run(
        tmp_path,
        _packages("alpine-base", "cage"),
        [_apkindex("alpine-base"), _apkindex("cage")],
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"חבילה מ-community נספרה כחסרה:\n{output}"


def test_the_report_names_every_absent_package(tmp_path: Path):
    """שניים חסרים = הודעה אחת, לא נפילה על הראשון בלבד."""
    proc = _run(
        tmp_path,
        _packages("alpine-base", "ghost-a", "ghost-b"),
        [_apkindex("alpine-base")],
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "ghost-a" in output, output
    assert "ghost-b" in output, output


def test_no_apkindex_is_a_failure_not_a_pass(tmp_path: Path):
    """בלי אינדקס לא נבדק. יציאה 0 כאן היא בדיוק הבאג המקורי."""
    proc = _run(tmp_path, _packages("alpine-base"), indexes=None)
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"בלי APKINDEX יצא 0:\n{output}"
    assert "APKINDEX required" in output, output


def test_an_index_with_no_p_records_is_a_failure_not_a_pass(tmp_path: Path):
    """קובץ בלי רשומות P: הוא לא-אינדקס, לא 'הכול חסר'."""
    proc = _run(tmp_path, _packages("alpine-base"), ["not an apkindex\n"])
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0, f"אינדקס ריק יצא 0:\n{output}"
    assert "no P: records" in output, output
