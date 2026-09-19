"""‏#410 — מסך הטקסט של החדר: זמן שעבר, קצב, ETA וגיל המסך.

מריץ את `room_draw_pace` **האמיתי** מ-`agent/lib/roomdraw.sh` (לא עותק),
מול `room.json` שמדמה את מה ש-`GET /api/console/room` מחזיר. ⚠️ ‏null
מודפס "(not measured)" ולעולם לא `0` — קצב 0 נקרא "עצר" (עיקרון 5).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
import time
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
BASH = shutil.which("bash")
JQ = shutil.which("jq")
pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash לא זמין"),
    #: ⚠️ בלי `jq` הרינדור אינו רץ כלל, והטסט היה "עובר" על פלט ריק.
    pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הרינדור לא היה רץ בכלל"),
]


def render(tmp_path: Path, round_: dict | None, fetched_ago: int | None = 3) -> str:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "room.json").write_text(
        json.dumps({"round": round_, "machines": []}), encoding="utf-8")
    if fetched_ago is not None:
        (run_dir / "room_fetched_at").write_text(str(int(time.time()) - fetched_ago))
    script = textwrap.dedent(f"""
        RUN_DIR={run_dir.as_posix()!r}
        log() {{ :; }}
        . {AGENT.as_posix()}/lib/jsonq.sh
        . {AGENT.as_posix()}/lib/roomdraw.sh
        room_draw_pace
    """)
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def test_a_running_wave_shows_elapsed_rate_and_eta(tmp_path):
    out = render(tmp_path, {"elapsed_s": 3725, "rate_bps": 52_000_000, "eta_s": 610})
    assert "Elapsed:    1h 02m 05s" in out
    assert "Rate:       52 MB/s" in out
    assert "ETA:        10m 10s" in out


def test_an_unmeasured_wave_says_so_and_never_prints_zero(tmp_path):
    out = render(tmp_path, {"elapsed_s": 12, "rate_bps": None, "eta_s": None})
    assert "Elapsed:    0m 12s" in out
    assert "Rate:       (not measured)" in out
    assert "ETA:        (not measured)" in out
    assert "0 MB/s" not in out


def test_an_old_server_without_the_fields_is_not_measured(tmp_path):
    out = render(tmp_path, {"wave_number": 1})
    assert out.count("(not measured)") == 3


def test_the_screen_says_how_old_it_is(tmp_path):
    out = render(tmp_path, {"elapsed_s": 1}, fetched_ago=7)
    age = int(out.split("Updated:")[1].split("s ago")[0])
    assert 7 <= age <= 9


def test_a_screen_that_was_never_fetched_says_so(tmp_path):
    out = render(tmp_path, {"elapsed_s": 1}, fetched_ago=None)
    assert "Updated:    (never fetched)" in out


def test_every_line_fits_the_eighty_column_screen(tmp_path):
    out = render(tmp_path, {"elapsed_s": 359_999, "rate_bps": 999_000_000, "eta_s": 359_999})
    assert all(len(line) <= 80 for line in out.splitlines()), out
