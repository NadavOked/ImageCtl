"""‏`tools/lab/class-client/user-data` — שני שערי שקר בטור (#509, #510).

אין תשתית קיימת לקובץ הזה (ה-grep על `class-client` / `user-data`
ב-`tests/` מצא רק את `testrunner/user-data`). הטסטים כאן הם המינימום:
שולפים את `imagectl-class-verify` ואת יחידת ה-systemd מתוך ה-seed,
ומריצים את הלוגיקה תחת מעטפת POSIX אמיתית.

#509 — `l4()` מיין כל פלט לא-מוכר ל-`silent` (=החבילה הופלה). כלי
שנשבר הוכיח שחומת האש עובדת. ‏`tftp_get` באותו קובץ כבר מחזיר
`probe-error`; `l4` חייב את אותה הבחנה.

#510 — `ExecStart` הריץ `verify | tee … > /dev/ttyS0` בלי pipefail.
קוד היציאה היה של `tee` (תמיד 0), ושלושה FAIL נראו `active (exited)`.
ב-POSIX sh אין `pipefail` — התיקון הוא בלי צינור.

הטסט מריץ את הפקודה ואת הפונקציה האמיתיות, לא קורא אותן. באג של
סמנטיקת מעטפת חי בפער שבין "מה שכתוב" ל"מה שהמעטפת עושה עם זה".
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SEED = REPO / "tools" / "lab" / "class-client" / "user-data"

#: ההזחה של גוש `content: |` ב-cloud-init (כמו test_lab_ic_diff.py).
INDENT = "      "


def _posix_shells() -> list[list[str]]:
    """לפחות אחת, אחרת הטסט נכשל — skipif כאן היה ירוק בלי לבדוק."""
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


def _write_lf(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def posix(path: Path) -> str:
    """נתיב ש-dash של Git Bash מקבל בלי המרת `C:` לנקודה-פסיק."""
    s = path.resolve().as_posix()
    if len(s) >= 2 and s[1] == ":":
        return f"/{s[0].lower()}{s[2:]}"
    return s


def embedded(path: str) -> str:
    """שולף גוף `content: |` של קובץ ב-write_files, בלי הזחת cloud-init."""
    lines = SEED.read_text(encoding="utf-8").splitlines()
    needle = f"- path: {path}"
    start = next(i for i, ln in enumerate(lines) if ln.strip() == needle)
    content = next(i for i in range(start, len(lines))
                   if lines[i].strip() == "content: |")
    out = []
    for line in lines[content + 1:]:
        if line.strip() and not line.startswith(INDENT):
            break
        out.append(line[len(INDENT):] if line.startswith(INDENT) else "")
    while out and not out[-1]:
        out.pop()
    return "\n".join(out) + "\n"


def verify_script() -> str:
    return embedded("/usr/local/sbin/imagectl-class-verify")


def unit_text() -> str:
    return embedded("/etc/systemd/system/imagectl-class-verify.service")


def shell_function(script: str, name: str) -> str:
    match = re.search(
        rf"^{re.escape(name)}\(\) \{{$.*?^}}$", script, flags=re.M | re.S
    )
    assert match, f"לא נמצאה {name}() בסקריפט שחולץ מה-seed"
    return match.group(0)


def execstart_command() -> str:
    match = re.search(r"^ExecStart=/bin/sh -c '(.*)'\s*$", unit_text(), re.M)
    assert match, "ExecStart אינו /bin/sh -c '…' — אי אפשר להריץ את היחידה"
    return match.group(1)


def _run(argv: list[str], env: dict[str, str] | None = None):
    return subprocess.run(
        argv, capture_output=True, text=True, encoding="utf-8",
        errors="replace", stdin=subprocess.DEVNULL, timeout=30, env=env,
    )


def run_l4(shell: list[str], tmp: Path, probe_output: str, want: str):
    """מריץ את `l4()` האמיתית מול גשש מזויף שפולט `probe_output`.

    השם `imagectl-l4-probe` אינו מזהה POSIX (מקפים) — אי אפשר להגדיר
    אותו כפונקציה. ה-stub יושב על PATH בשמו האמיתי.
    """
    probe_file = tmp / "probe.out"
    if probe_output == "":
        probe_file.write_bytes(b"")
    else:
        text = probe_output if probe_output.endswith("\n") else probe_output + "\n"
        _write_lf(probe_file, text)
    bindir = tmp / "bin"
    bindir.mkdir()
    stub = bindir / "imagectl-l4-probe"
    _write_lf(stub, "#!/bin/sh\ncat \"$PROBE_OUT\"\n")
    stub.chmod(0o755)
    script = tmp / "run-l4.sh"
    func = shell_function(verify_script(), "l4")
    _write_lf(script, (
        "#!/bin/sh\n"
        "set -u\n"
        "fails=0\n"
        f"{func}\n"
        f"l4 {want} 'L6 tcp/22' tcp 10.10.10.8 22\n"
        "printf 'fails=%s\\n' \"$fails\"\n"
        "exit \"$fails\"\n"
    ))
    env = os.environ.copy()
    env["PROBE_OUT"] = posix(probe_file)
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    return _run(shell + [str(script)], env=env)


def got_class(out: str) -> str:
    match = re.search(r"got=(\S+)", out)
    assert match, f"אין שדה got= בפלט:\n{out}"
    return match.group(1)


def run_execstart(shell: list[str], tmp: Path, exit_code: int, body: str):
    """מריץ את פקודת ה-ExecStart האמיתית, עם מאמת ו-ttyS0 ב-tmp."""
    verify = tmp / "imagectl-class-verify"
    log = tmp / "verify.log"
    tty = tmp / "ttyS0"
    _write_lf(verify, f"#!/bin/sh\nprintf '%s\\n' '{body}'\nexit {exit_code}\n")
    verify.chmod(0o755)
    cmd = (execstart_command()
           .replace("/usr/local/sbin/imagectl-class-verify", posix(verify))
           .replace("/var/log/imagectl-class-verify.log", posix(log))
           .replace("/dev/ttyS0", posix(tty)))
    proc = _run(shell + ["-c", cmd])
    return proc, log, tty


def test_the_extractor_found_the_real_bodies():
    """שולף ריק היה משווה כלום לכלום — ירוק תמיד (test_lab_ic_diff)."""
    verify = verify_script()
    unit = unit_text()
    assert "l4()" in verify and "tftp_get()" in verify, verify[:200]
    assert "ExecStart=" in unit, unit
    assert len(verify.splitlines()) >= 40
    assert "imagectl-l4-probe" in shell_function(verify, "l4")


# --- #509 -------------------------------------------------------------------


UNKNOWN_PROBE = (
    "Traceback (most recent call last):",
    "python3: not found",
    "",
    "oops",
)


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize("blob", UNKNOWN_PROBE, ids=["traceback", "no-python", "empty", "oops"])
def test_unknown_l4_probe_output_is_probe_error_not_a_drop(shell, blob, tmp_path):
    """#509 — פלט לא מוכר הוא כשל בדיקה, לא PASS על L6/L7/L8.

    לפני התיקון `case *)` מיין הכל ל-silent, ו-`l4 silent` דיווח PASS.
    """
    proc = run_l4(shell, tmp_path, blob, "silent")
    out = proc.stdout + proc.stderr
    assert proc.returncode != 0, (
        f"probe שבור יצא 0 — l4 ספר אותו כהפלה מוצלחת (#509):\n{out}"
    )
    assert got_class(out) == "probe-error", (
        f"פלט לא מוכר לא סומן probe-error:\n{out}"
    )
    assert "PASS" not in proc.stdout, f"PASS על כלי שנשבר:\n{out}"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_real_l4_timeout_is_still_silent(shell, tmp_path):
    """רדיוס: `no-answer` הוא שקט אמיתי, לא probe-error. בלי זה התיקון דוחה L6 תקין."""
    proc = run_l4(shell, tmp_path, "no-answer timeout 6s", "silent")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"timeout אמיתי נכשל:\n{out}"
    assert got_class(out) == "silent", out
    assert proc.stdout.startswith("PASS"), out


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
@pytest.mark.parametrize("blob,label", [
    ("answer 12 bytes", "answer"),
    ("refused RST", "refused"),
])
def test_l4_answer_and_refused_are_still_reachable(shell, blob, label, tmp_path):
    proc = run_l4(shell, tmp_path, blob, "reachable")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 0, f"{label} נכשל:\n{out}"
    assert got_class(out) == "reachable", out


# --- #510 -------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_failing_verifier_fails_the_oneshot_unit(shell, tmp_path):
    """#510 — מאמת שיוצא 3 חייב להשאיר את היחידה failed, לא active(exited).

    לפני התיקון הצינור `| tee … > /dev/ttyS0` החזיר 0 של tee.
    """
    proc, log, tty = run_execstart(shell, tmp_path, 3, "3 FAIL")
    out = proc.stdout + proc.stderr
    assert proc.returncode == 3, (
        f"מאמת שיצא 3 — היחידה ראתה {proc.returncode} ולא 3 (#510):\n{out}"
    )
    assert log.read_text(encoding="utf-8") == "3 FAIL\n", log.read_text(encoding="utf-8")
    assert tty.read_text(encoding="utf-8") == "3 FAIL\n", (
        f"‏/dev/ttyS0 לא קיבל את הפלט — זו הדרך היחידה לקרוא מהמארח:\n"
        f"{tty.read_text(encoding='utf-8')!r}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_a_passing_verifier_still_reaches_ttys0(shell, tmp_path):
    """רדיוס: ALL PASS עדיין 0, ועדיין נשפך ל-ttyS0. בלי זה התיקון דוחה הכול."""
    proc, log, tty = run_execstart(shell, tmp_path, 0, "ALL PASS")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert log.read_text(encoding="utf-8") == "ALL PASS\n"
    assert tty.read_text(encoding="utf-8") == "ALL PASS\n"


def test_execstart_is_posix_sh_without_a_pipe_or_bashism():
    """‏`set -o pipefail` היה 'מתקן' תחת bash ונשבר ב-dash של היחידה."""
    unit = unit_text()
    cmd = execstart_command()
    assert "pipefail" not in unit, "pipefail אינו קיים ב-/bin/sh (dash)"
    assert "PIPESTATUS" not in unit, "PIPESTATUS הוא bashism"
    assert "|" not in cmd, (
        f"ExecStart עדיין צינור — קוד היציאה הוא של האחרון בלבד:\n{cmd}"
    )
    assert "/dev/ttyS0" in cmd, "‏> /dev/ttyS0 חייב להישאר"
    assert cmd.startswith("/usr/local/sbin/imagectl-class-verify")
    assert "exit $rc" in cmd
