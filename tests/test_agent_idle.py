"""‏#434: מכונה שממתינה לאיש נכבית אחרי חמש דקות — ולעולם לא כשמוצג כשל.

שלושה מצבים שנראו זהים מבחוץ (מחשב בנייה על מסך כניסה 35 דקות, מחשב
שיכפול `failed` שעות, תחנה שהפסיקה hello): רק הראשון הוא "ממתין". הכלל:
המתנה מעבר לסף → אזהרה של IDLE_WARN_S שניות → כיבוי דרך arm_wol; פעילות
(מקש בגואי, תשובת hello שהשתנתה, סבב פתוח, `stay_on`) מאפסת; מסך כשל
אינו מגיע ל-`poll_sleep` בכלל, ולכן אינו מגיע לשעון. ‏`poweroff` מזויף."""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native

from test_agent import AGENT, BASH, posix, sh

pytestmark = requires_native(("bash", BASH))

LIB = AGENT / "lib"


def run_idle(tmp_path: Path, setup: str, script: str) -> str:
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    calls = tmp_path / "calls"
    return sh(
        f'export RUN_DIR={posix(run)!r} IDLE_POWEROFF_S=2 IDLE_WARN_S=1; '
        f'. {posix(LIB)}/idle.sh; '
        f'log() {{ printf "%s\\n" "$*"; }}; sync() {{ :; }}; '
        f'poweroff() {{ printf "POWEROFF %s\\n" "$*" >> {posix(calls)!r}; }}; '
        'arm_wol() { return 0; }; '
        + setup + '; ' + script
    ), calls


def _since(tmp_path: Path, ago: int) -> str:
    return f'printf "%s\\n" "$(( $(date +%s) - {ago} ))" > "$RUN_DIR/idle.since"'


def test_idle_past_the_threshold_warns_then_powers_off(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10), "idle_check")
    assert "power-off in 1s unless a key is pressed" in out
    assert "no activity for 2s -- powering off" in out
    assert calls.read_text(encoding="utf-8") == "POWEROFF -f\n"


def test_a_key_press_during_the_countdown_cancels_it(tmp_path):
    # the kiosk touches idle.touch (the GUI's "touch" token); newer than the
    # clock = activity -- simulated by a touch after the warning began
    out, calls = run_idle(tmp_path, _since(tmp_path, 10),
                          '(sleep 0.3; touch "$RUN_DIR/idle.touch") & idle_check; wait')
    assert "cancelled by activity" in out
    assert not calls.exists()
    assert not (tmp_path / "run" / "idle.warn").exists()


@pytest.mark.parametrize("exempt", [
    'touch "$RUN_DIR/stay_on"',
    'export D_SESSION_STATE=open',
    'export D_TASK="{\\"id\\":\\"t1\\"}"',
    'export IDLE_POWEROFF_S=0',
    'touch "$RUN_DIR/idle.touch"; sleep 1',   # touched after the clock started
])
def test_exempt_states_never_power_off(tmp_path, exempt):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + "; " + exempt, "idle_check; echo rc=$?")
    assert "rc=0" in out and "powering off" not in out
    assert not calls.exists()


def test_a_changed_hello_answer_is_activity(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10), "POLL_MARK_CHANGED=1 idle_check; cat \"$RUN_DIR/idle.since\"")
    assert not calls.exists()
    since = int(out.strip().splitlines()[-1])
    assert since > 0


def test_without_wol_the_machine_stays_on_and_says_so(tmp_path):
    out, calls = run_idle(tmp_path, _since(tmp_path, 10) + "; arm_wol() { return 1; }", "idle_check; echo rc=$?")
    assert "wol not armed -- staying powered on" in out and "rc=1" in out
    assert not calls.exists()


def test_the_clock_beats_only_in_poll_sleep_and_the_failure_hold_never_reaches_it():
    poll = (LIB / "poll.sh").read_text(encoding="utf-8")
    assert "command -v idle_check >/dev/null 2>&1 && idle_check" in poll
    assert "POLL_MARK_CHANGED=1" in poll
    ui = (LIB / "ui.sh").read_text(encoding="utf-8") + (LIB / "hold.sh").read_text(encoding="utf-8")
    assert "poll_sleep" not in ui, "מסך כשל שמגיע ל-poll_sleep היה מגיע לשעון הכיבוי"
    agent = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/idle.sh"' in agent
    bridge = (LIB / "guibridge.sh").read_text(encoding="utf-8")
    assert 'touch "$RUN_DIR/idle.touch" || return 1' in bridge, "מקש בגואי חייב להגיע לשעון"
    gui = (AGENT.parent / "native-gui" / "src" / "main.c").read_text(encoding="utf-8")
    assert 'emit("touch"); emit_end();' in gui
    assert 'if (a->st.idle_poweroff_at <= 0 && now - last < 10) return;' in gui, "בזמן ספירה כל מקש נשלח, אחרת עד אחד ל-10 שניות"
