"""tools/lab/record-deploy.sh — ה-SHA שנרשם הוא מה שנפרס, לא מה שהנחנו.

#511: הסקריפט לקח `git rev-parse HEAD` מהמכונה שמריצה אותו, וב-SSH
אסף רק `git describe` כ-version. אם הלפטופ בקומיט A והשרת ב-B,
GitHub Environments נרשם כפריסה של A. זה מה שקרה ב-06/09.

הבדיקות מריצות את הסקריפט מול `ssh` ו-`gh` מזויפים על ה-PATH. אף
שרת ואף ריפו אמיתיים אינם נגעִים.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "lab" / "record-deploy.sh"

BASH = shutil.which("bash")
requires_bash = pytest.mark.skipif(
    BASH is None and os.name == "nt",
    reason="אין bash בווינדוס הזה (Git Bash לא מותקן) — רץ ב-CI ועל לינוקס",
)

FAKE_INITRD = "a" * 64
OTHER_SHA = "b" * 40


def local_head() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT, check=True, stdin=subprocess.DEVNULL,
        capture_output=True, text=True,
    ).stdout.strip()


def make_bin(tmp_path: Path, *, remote_sha: str | None) -> tuple[Path, Path, Path]:
    """מחזיר (bindir, יומן ssh, יומן gh). remote_sha=None — בלי שדה sha."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    ssh_log = tmp_path / "ssh.log"
    gh_log = tmp_path / "gh.log"
    ssh_log.write_text("", encoding="utf-8")
    gh_log.write_text("", encoding="utf-8")

    sha_line = "" if remote_sha is None else f'echo "sha=$FAKE_REMOTE_SHA"\n'
    (bindir / "ssh").write_text(
        "#!/bin/sh\n"
        f'echo "SSH: $*" >> "{ssh_log.as_posix()}"\n'
        'echo "version=v0.99.0"\n'
        'echo "dirty=0"\n'
        'echo "active=active"\n'
        'echo "routes=12"\n'
        f'echo "served={FAKE_INITRD}"\n'
        f'echo "ondisk={FAKE_INITRD}"\n'
        + sha_line,
        encoding="utf-8",
        newline="\n",
    )
    # ‏0o700: אותו דפוס כמו test_issue_new_script — הזיוף בר-הרצה
    # למי שמריץ את הבדיקה, לא לכל המכונה.
    (bindir / "gh").write_text(
        "#!/bin/sh\n"
        f'echo "CALL: $*" >> "{gh_log.as_posix()}"\n'
        f'echo "---STDIN---" >> "{gh_log.as_posix()}"\n'
        f'cat >> "{gh_log.as_posix()}"\n'
        f'echo >> "{gh_log.as_posix()}"\n'
        "echo 42\n",
        encoding="utf-8",
        newline="\n",
    )
    py = Path(sys.executable).as_posix()
    (bindir / "python").write_text(
        f"#!/bin/sh\nexec '{py}' \"$@\"\n",
        encoding="utf-8",
        newline="\n",
    )
    for name in ("ssh", "gh", "python"):
        os.chmod(bindir / name, 0o700)
    return bindir, ssh_log, gh_log


def run_script(tmp_path: Path, *, remote_sha: str | None) -> subprocess.CompletedProcess:
    bindir, ssh_log, gh_log = make_bin(tmp_path, remote_sha=remote_sha)
    env = dict(os.environ)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env["IMAGECTL_REPO"] = "test/imagectl"
    env["IMAGECTL_LAB_KEY"] = str(tmp_path / "no-such-key")
    if remote_sha is not None:
        env["FAKE_REMOTE_SHA"] = remote_sha
    proc = subprocess.run(
        [BASH or "bash", str(SCRIPT), "lab", "nobody@invalid.invalid"],
        cwd=str(ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    proc.ssh_log = ssh_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    proc.gh_log = gh_log.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    return proc


@requires_bash
def test_a_remote_sha_that_differs_from_local_is_a_failed_deploy(tmp_path):
    """הבאג: שרת על B, לפטופ על A — נרשמה פריסה של A בלי להגיד כלום."""
    proc = run_script(tmp_path, remote_sha=OTHER_SHA)
    assert "SSH:" in proc.ssh_log, (
        f"ה-ssh המזויף לא רץ — הבדיקה לא בדקה כלום:\n{proc.stderr}"
    )
    assert proc.returncode != 0, (
        "SHA מרוחק שונה מהמקומי והסקריפט יצא 0 — נרשמה פריסה של מה שלא נפרס:\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}\ngh={proc.gh_log!r}"
    )
    err = proc.stderr
    assert OTHER_SHA in err, f"ה-SHA המרוחק אינו בהודעת הכשל:\n{err}"
    assert local_head() in err, f"ה-HEAD המקומי אינו בהודעת הכשל:\n{err}"
    assert "CALL:" not in proc.gh_log, (
        f"gh נקרא למרות אי-התאמה — הפריסה נרשמה:\n{proc.gh_log}"
    )


@requires_bash
def test_a_matching_remote_sha_is_what_gets_recorded(tmp_path):
    """כשהשרת באמת על אותו קומיט — נרשם ה-SHA המרוחק, לא ניחוש מקומי."""
    sha = local_head()
    proc = run_script(tmp_path, remote_sha=sha)
    assert "SSH:" in proc.ssh_log, proc.stderr
    assert proc.returncode == 0, proc.stderr
    assert "CALL:" in proc.gh_log, f"gh לא נקרא בהצלחה:\n{proc.gh_log}\n{proc.stderr}"
    assert f'"ref": "{sha}"' in proc.gh_log or f'"ref":"{sha}"' in proc.gh_log, (
        f"הפריסה לא נרשמה על ה-SHA המרוחק:\n{proc.gh_log}"
    )


@requires_bash
def test_a_missing_remote_sha_is_could_not_check_not_a_local_guess(tmp_path):
    """אין SHA מהשרת = לא הצלחנו לבדוק. לרשום את המקומי היה בדיוק #511."""
    proc = run_script(tmp_path, remote_sha=None)
    assert "SSH:" in proc.ssh_log, proc.stderr
    assert proc.returncode != 0, (
        "השרת לא החזיר SHA והסקריפט יצא 0:\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )
    assert "CALL:" not in proc.gh_log, (
        f"נרשמה פריסה בלי SHA מרוחק:\n{proc.gh_log}"
    )
