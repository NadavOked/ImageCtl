"""‏#821: שתי הרצות של `lab-run-tests.sh` על אותו Issue רצו במקביל על אותו
`/root/ic-<N>-tmp`, וה-`trap cleanup EXIT` של הראשונה רוקן את `TMPDIR` מתחת
לשנייה — 317 `FileNotFoundError` בריצה אחת (14/09, `/root/ic-456`).

הבדיקות כאן מריצות את `tools/git/lab-run-tests-remote.sh` — החצי שמוזרם
למעבדה דרך `ssh <host> bash -s -- <N> < lab-run-tests-remote.sh` — מקומית,
נגד עץ חד-פעמי (`LAB_RUN_ROOT`) ו-`python3` מזויף ב-`PATH` שמתנהג כמו pytest:
כותב ל-`TMPDIR`, ממתין לאות, ומדפיס שורת סיכום. אותם בייטים רצים כאן ועל
המעבדה (לקח #604, כמו `lab-tmp-sweep.sh`), ולכן מה שמוכח כאן הוא הלוגיקה
עצמה: המנעול, מי מנקה, ושקוד היציאה ושורת הסיכום של pytest עוברים דרך
הסקריפט בלי להיבלע.

מה שלא נבדק כאן: ‏SSH למעבדה עצמה — הצינור ב-`lab-run-tests.sh` — ו-`flock`
על מערכת הקבצים של המעבדה. ‏`flock` הוא util-linux ואינו קיים ב-Git Bash של
ווינדוס, ולכן בדיקות המנעול מדלגות שם **בגלוי** ורצות על לינוקס.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/git/lab-run-tests-remote.sh"
BASH = shutil.which("bash") or shutil.which("bash.exe")
FLOCK = shutil.which("flock")

# EX_TEMPFAIL של sysexits(3): מחוץ לטווח קודי היציאה של pytest (0-5), כדי
# ש"ריצה אחרת חיה" לא ייראה כמו "טסט נכשל".
LOCK_BUSY = 75

FAKE_PYTEST = """#!/usr/bin/env bash
# fake python3: behaves like `python3 -m pytest tests -q` for the test.
set -eu
mkdir -p "$TMPDIR/pytest-of-root/pytest-1"
echo "started" > "$TMPDIR/pytest-of-root/pytest-1/marker.txt"
echo "$$" > "$FAKE_PYTEST_STARTED"
for _ in $(seq 1 200); do
    [ -e "$FAKE_PYTEST_GO" ] && break
    sleep 0.1
done
[ -e "$TMPDIR/pytest-of-root/pytest-1/marker.txt" ] || { echo "E marker vanished under me" >&2; exit 1; }
echo "..."
echo "3 passed in 0.12s"
exit "${FAKE_PYTEST_RC:-0}"
"""


def _wait_for(path: Path, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.05)
    return False


@unittest.skipUnless(BASH, "bash is required to run lab-run-tests-remote.sh")
class LabRunTestsRemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root = base / "root"
        (self.root / "ic-7").mkdir(parents=True)
        self.tmpdir = self.root / "ic-7-tmp"
        self.bin = base / "bin"
        self.bin.mkdir()
        fake = self.bin / "python3"
        fake.write_text(FAKE_PYTEST, encoding="utf-8", newline="\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.signals = base / "signals"
        self.signals.mkdir()

    def env(self, tag: str, rc: int = 0, path: str | None = None) -> dict:
        return dict(os.environ,
                    PATH=path if path is not None else str(self.bin) + os.pathsep + os.environ["PATH"],
                    LAB_RUN_ROOT=str(self.root),
                    FAKE_PYTEST_STARTED=str(self.signals / f"{tag}.started"),
                    FAKE_PYTEST_GO=str(self.signals / f"{tag}.go"),
                    FAKE_PYTEST_RC=str(rc))

    def start(self, tag: str, rc: int = 0, issue: str = "7") -> subprocess.Popen:
        return subprocess.Popen([BASH, str(SCRIPT), issue], env=self.env(tag, rc),
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, encoding="utf-8", errors="replace")

    def run_once(self, tag: str, rc: int = 0, issue: str = "7", **kw) -> subprocess.CompletedProcess:
        (self.signals / f"{tag}.go").write_text("go")
        return subprocess.run([BASH, str(SCRIPT), issue], env=self.env(tag, rc, **kw),
                              stdin=subprocess.DEVNULL, capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)

    # --- שורת הסיכום וקוד היציאה עוברים דרך הסקריפט -------------------------

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_summary_line_and_exit_code_pass_through_and_tmpdir_is_emptied(self):
        proc = self.run_once("a")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("3 passed in", proc.stdout, "שורת הסיכום של pytest נבלעה")
        self.assertTrue(self.tmpdir.is_dir(), "התיקייה עצמה חייבת להישאר לריצה הבאה (#388)")
        leftovers = [p.name for p in self.tmpdir.iterdir() if p.name != ".lock"]
        self.assertEqual(leftovers, [], "TMPDIR לא רוקן ביציאה")

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_a_red_pytest_still_cleans_up_and_keeps_its_exit_code(self):
        proc = self.run_once("b", rc=1)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertIn("3 passed in", proc.stdout)
        self.assertFalse((self.tmpdir / "pytest-of-root").exists(), "נתיב הכישלון לא ניקה (#388)")

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_missing_checkout_is_an_error(self):
        proc = self.run_once("c", issue="8")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("checkout does not exist", proc.stderr)

    # --- עיקרון 5: "לא הצלחנו לנעול" אינו "אין ריצה אחרת" ----------------------

    def test_missing_flock_refuses_to_run_rather_than_running_unlocked(self):
        proc = self.run_once("d", path="/nonexistent-bin")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("flock", proc.stderr)
        self.assertFalse((self.signals / "d.started").exists(), "pytest רץ בלי מנעול")

    # --- המנעול עצמו (flock, לינוקס בלבד) ---------------------------------------

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_second_run_on_the_same_issue_exits_at_once_naming_the_live_pid(self):
        first = self.start("e")
        self.addCleanup(first.kill)
        self.assertTrue(_wait_for(self.signals / "e.started"), "הריצה הראשונה לא התחילה")
        marker = self.tmpdir / "pytest-of-root" / "pytest-1" / "marker.txt"
        self.assertTrue(marker.exists())

        t0 = time.monotonic()
        second = self.run_once("f")
        self.assertLess(time.monotonic() - t0, 5, "הריצה השנייה המתינה במקום לצאת מיד")
        self.assertEqual(second.returncode, LOCK_BUSY, second.stderr)
        holder = (self.tmpdir / ".lock").read_text().strip()
        self.assertRegex(holder, r"^\d+$", "קובץ המנעול חייב לשאת את ה-pid של המחזיק")
        self.assertIn(f"pid {holder}", second.stderr)
        self.assertFalse((self.signals / "f.started").exists(), "pytest השני רץ למרות המנעול")
        self.assertTrue(marker.exists(), "יציאת הריצה השנייה רוקנה את TMPDIR של הראשונה")

        (self.signals / "e.go").write_text("go")
        out, err = first.communicate(timeout=30)
        self.assertEqual(first.returncode, 0, err)
        self.assertIn("3 passed in", out)
        self.assertFalse(marker.exists(), "המחזיק לא ניקה ביציאה")

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_the_lock_is_released_when_the_holder_exits(self):
        self.assertEqual(self.run_once("g").returncode, 0)
        proc = self.run_once("h")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    @unittest.skipUnless(FLOCK, "flock (util-linux) is not available on this host")
    def test_different_issues_do_not_block_each_other(self):
        (self.root / "ic-9").mkdir()
        first = self.start("i")
        self.addCleanup(first.kill)
        self.assertTrue(_wait_for(self.signals / "i.started"))
        proc = self.run_once("j", issue="9")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        (self.signals / "i.go").write_text("go")
        first.communicate(timeout=30)
        self.assertEqual(first.returncode, 0)


class LabRunTestsWrapperTests(unittest.TestCase):
    """‏`lab-run-tests.sh` מזרים את הקובץ הנפרד — לא עותק שני של אותו בלוק."""

    def test_wrapper_streams_the_remote_script_file(self):
        text = (ROOT / "tools/git/lab-run-tests.sh").read_text(encoding="utf-8")
        self.assertIn('< "$remote"', text)
        self.assertNotIn("<<'REMOTE'", text, "הבלוק הישן חזר — עותק שני שנסחף (#604)")
        self.assertIn("lab-run-tests-remote.sh", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
