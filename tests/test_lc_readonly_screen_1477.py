"""#1477 -- the local clone screens say what #1466 made true: a smaller target
is shrunk on a RAM view, and the source is read only. Before this the
shrinking phase said "מכווץ זמנית את המקור… המחיצות יוחזרו לגודלן", the
confirm dialog "יכווצו במקומן… ויוחזרו", and the screen had no way to know
which path ran.

Two halves:

* **the agent's record** (bash): ``lc_run=`` carries ``shrink_path`` as a
  ninth, last field -- what ``lc_shrink_source`` wrote, ``snapshot`` or
  ``refused``; anything else (no file, garbage) is empty, never guessed.
* **what the screen says** (cc + pango, the Testrunner -- skipped on the
  Windows station): the text-capturing driver of #1437/#1440 renders the
  progress screen in ``shrinking`` and the result on both paths.

**Negative control:** on lcgui.sh before #1477 the record has eight fields
and all five agent cases fail (``8 == 9``); on the screens before it the shrinking
phase draws "מכווץ זמנית את המקור…" / "המחיצות יוחזרו לגודלן בסוף ההעתקה."
and the snapshot result has no "עותק בזיכרון" line -- the screen tests
fail (run on the Testrunner; not run here, see the PR).
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_cloner_gui import BASH
from test_local_clone_agent import run
from test_lc_progress_1437_1440 import DISK, DISK2, driver, needs_cc, screens  # noqa: F401 -- driver is a fixture

needs_bash = requires_native(("bash", BASH))


def _lc_run(tmp_path, shrink_path: str | None) -> list[str]:
    write = "" if shrink_path is None else f'printf "%s\\n" {shrink_path!r} > "$LC/run/shrink_path"; '
    out = run(tmp_path, 'mkdir -p "$LC/run"; date +%s > "$LC/run/started"; echo sdc > "$LC/run/src"; '
              + write + 'lc_gui_run')
    assert out.returncode == 0, out.stderr
    (line,) = [l for l in out.stdout.splitlines() if l.startswith("lc_run=")]
    return line[len("lc_run="):].split("|")


@needs_bash
@pytest.mark.parametrize("path", ["snapshot", "refused"])
def test_the_record_carries_the_path_the_agent_wrote(tmp_path, path):
    f = _lc_run(tmp_path, path)
    assert len(f) == 9 and f[8] == path, f


@needs_bash
@pytest.mark.parametrize("path", [None, "in_place", "snap shot"])
def test_no_path_or_an_unknown_one_is_empty_never_guessed(tmp_path, path):
    f = _lc_run(tmp_path, path)
    assert len(f) == 9 and f[8] == "", f


# --- what the screen says ------------------------------------------------------------


def _state(phase: str, shrink_path: str, report: str = "") -> str:
    return ("lc_screen=local\nlc_inv=done\n" f"lc_phase={phase}\n" + DISK + DISK2 +
            f"lc_run=sdc|206000000000|206000000000|900|0|{report}|2||{shrink_path}\n"
            "lc_part=1|EFI|vfat|3030000000|3030000000|done|0\n"
            "lc_part=2|Windows|ntfs|203100000000|496000000000|done|0\n"
            f"lc_tgt=sdb|{'done' if phase == 'done' else 'writing'}|206000000000|2|2||||206000000000\n")


@needs_cc
def test_the_shrinking_phase_says_the_source_is_read_only(driver, tmp_path):
    (drawn,) = screens(driver, tmp_path, _state("shrinking", ""))
    assert "מכין עותק מכווץ בזיכרון…" in drawn and "המקור נקרא בלבד ולא נכתב." in drawn, drawn
    assert not any("יוחזר" in s or "מכווץ זמנית" in s for s in drawn), drawn


@needs_cc
def test_the_result_on_the_snapshot_path_names_the_ram_view(driver, tmp_path):
    snap, plain = screens(driver, tmp_path, _state("done", "snapshot", "sent"), _state("done", "", "sent"))
    assert any(s.endswith("(המקור) נקרא בלבד ולא שונה — הכיווץ נעשה על עותק בזיכרון.") for s in snap), snap
    assert any(s.endswith("(המקור) נקרא בלבד ולא שונה.") for s in plain), plain
    assert not any("הוחזר" in s or "כווץ זמנית" in s for s in snap + plain), snap + plain
