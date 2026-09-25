"""‏#418 — אחרי הפצה, איזה כונן פיזי הצליח בכל מכונה.

**התלונה של נדב:** "במחשב שיכפול 2 מה הדיסק שהצליח?" התשובה שהייתה
זמינה היא `sdb` — שם התקן שאינו מופיע על שום מגירה. הוא הכריע לפי זה
לקחת את הכונן האמצעי, בזמן שבפועל `sda` הוא זה שהצליח.

## למה `fresh == false` הוא הראיה הנכונה

‏`server/room.py:_tally` מוסיף לקבוצת `written` **רק** סריאל שסיים
בהצלחה (`state == "done"`), ולא נספר שוב. ‏`drawer_list` מחשב מזה
`fresh = serial not in written` **לכל מכונה בנפרד**,
מהמלאי החי שלה (`disks_json`). כלומר `fresh == false` נכון גם למגירה
שסיימה בגל **קודם** של אותו סבב, אחרי שהמכונה כבר יצאה מרשימת החברים
החיה של הגל הנוכחי — בדיוק המצב שבו `state` (שמגיע רק מ-`targets_json`
של החבר **הנוכחי**) כבר לא קיים.

מגירה שנכשלה (`state == "failed"`) **אינה** נכנסת ל-`written` (ראו
‏`_tally`), ולכן `fresh` שלה נשאר `True` — ומסך זה לא מציג אותה, בדיוק
כמו שהיא לא נספרת בסבב הבא כ"כתובה".

## ‏#105: `fresh == false` הוא ראיה רק כשיש serial

כונן **בלי** serial אינו יכול להיות ב-`written` — אין לו זהות. שרת
ישן החזיר לו `fresh: false` (אותו ערך כמו "נכתב"), והמסך הזה הציג
כונן ריק תחת WRITTEN. עכשיו השרת מחזיר `fresh: null`, והמסך — גם מול
שרת ישן — אינו מציג כונן בלי serial ככתוב לעולם, אלא בשורה משלו.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parent.parent / "agent"
BASH = shutil.which("bash")
JQ = shutil.which("jq")
requires_bash = pytest.mark.skipif(BASH is None, reason="bash לא זמין")
requires_jq = pytest.mark.skipif(
    JQ is None, reason="jq אינו מותקן — הרינדור לא היה רץ בכלל")


def render_done(tmp_path: Path, machines: list, fn: str = "room_draw_done") -> str:
    """מריץ את `room_draw_done` **האמיתי** מהקובץ, לא עותק שלו."""
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    (run_dir / "room.json").write_text(
        json.dumps({"machines": machines}), encoding="utf-8")
    script = textwrap.dedent(f"""
        RUN_DIR={run_dir.as_posix()!r}
        log() {{ :; }}
        ui_clear() {{ :; }}
        ui_header() {{ :; }}
        . {AGENT.as_posix()}/lib/jsonq.sh
        . {AGENT.as_posix()}/lib/roomdraw.sh
        {fn}
    """)
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _drawer(**over) -> dict:
    return {"dev": "sda", "port": 1, "state": None, "fresh": False,
            "serial": "naa.111", "model": "SAMSUNG MZ7LN256", **over}


def _machine(name: str, *drawers: dict) -> dict:
    return {"name": name, "drawer_list": list(drawers)}


@requires_bash
@requires_jq
def test_a_drawer_finished_in_an_earlier_wave_still_shows_its_serial(tmp_path):
    """הליבה של #418: `state` נעלם עם רוטציית הגל, `fresh` לא.

    בגל השני של אותו סבב חבר הגל הקודם כבר אינו ברשימה החיה, ולכן
    ‏`state` הוא `None` — בדיוק כמו מגירה שמעולם לא נגעה בה. `fresh`
    לבדו מבחין ביניהן."""
    out = render_done(tmp_path, [_machine("HP1", _drawer(state=None))])
    assert "naa.111" in out


@requires_bash
@requires_jq
def test_two_written_drawers_on_the_same_machine_are_told_apart(tmp_path):
    """‏"מגירה 1 · נכתבה" ו-"מגירה 2 · נכתבה" נראות זהות בלי הסריאל —
    בדיוק המצב שבו נדב נשלח לכונן הלא נכון."""
    out = render_done(tmp_path, [_machine(
        "HP1", _drawer(dev="sda", port=1, serial="naa.111"),
        _drawer(dev="sdb", port=2, serial="naa.222"))])
    assert "naa.111" in out
    assert "naa.222" in out
    assert "drive 1 (SATA 0)" in out
    assert "drive 2 (SATA 1)" in out


@requires_bash
@requires_jq
def test_a_failed_drawer_is_not_reported_as_written(tmp_path):
    """‏`_tally` אינו סופר כשל לתוך `written` — `fresh` כשל נשאר `True`,
    ואסור שהוא יופיע כאן וגם ברשימת הכשלים."""
    out = render_done(tmp_path, [_machine(
        "HP1", _drawer(state="failed", fresh=True))])
    assert out.strip() == ""


@requires_bash
@requires_jq
def test_a_drawer_still_fresh_is_not_reported_as_written(tmp_path):
    """מגירה שממתינה עדיין (טרם נכתבה בסבב הזה) אינה "כתובה"."""
    out = render_done(tmp_path, [_machine("HP1", _drawer(state=None, fresh=True))])
    assert out.strip() == ""


@requires_bash
@requires_jq
def test_a_written_drive_with_no_port_is_not_given_an_invented_one(tmp_path):
    """⚠️ מספר מגירה שגוי גרוע ממספר חסר — אותו כלל כמו ברשימת הכשלים."""
    out = render_done(tmp_path, [_machine(
        "HP1", _drawer(dev="nvme0n1", port=None))])
    assert "nvme0n1" in out
    assert "SATA" not in out


@requires_bash
@requires_jq
def test_a_drive_with_no_serial_is_never_listed_as_written(tmp_path):
    """‏#105. הטסט שהיה כאן (`..._with_no_serial_says_so_instead_of_looking_
    identified`) **אישר את הבאג**: הוא ציפה ל-"no serial" תחת WRITTEN.
    אבל כונן בלי serial לא יכול להיות ב-`written_serials`, ולכן `fresh:
    false` שלו (משרת ישן) לא היה ראיה לכתיבה — הוא היה כונן ריק שמוצג
    ככתוב, ונשלף ככזה. גם מול שרת ישן: לא ב-WRITTEN."""
    for fresh in (False, None):
        out = render_done(tmp_path / str(fresh), [_machine(
            "HP1", _drawer(serial=None, model=None, fresh=fresh))])
        assert "WRITTEN:" not in out
        assert out.strip() == ""


def render_unidentified(tmp_path: Path, *drawers: dict) -> str:
    return render_done(tmp_path, [_machine("HP1", *drawers)],
                       fn="room_draw_unidentified")


@requires_bash
@requires_jq
def test_a_drive_with_no_serial_gets_its_own_line_by_slot(tmp_path):
    """"לא ניתן לזהות את הכונן בחריץ N" — בחריץ, לא בשם התקן."""
    out = render_unidentified(tmp_path, _drawer(
        dev="sdb", port=2, serial=None, model=None, fresh=None))
    assert "CANNOT IDENTIFY THE DRIVE" in out
    assert "drive 2 (SATA 1)" in out
    assert "unknown model" in out
    assert "not known if written" in out


@requires_bash
@requires_jq
def test_a_drive_with_no_serial_shows_this_waves_report_as_the_evidence(tmp_path):
    """יש ראיה אחרת — דיווח המגירה מהסוכן בגל הזה — והיא מוצגת כמות שהיא,
    בשורה של "לא מזוהה", לא תחת WRITTEN."""
    out = render_unidentified(tmp_path, _drawer(
        port=1, serial=None, fresh=None, state="done"))
    assert "written this wave (agent)" in out
    assert "WRITTEN:" not in out


@requires_bash
@requires_jq
def test_identified_drives_are_not_listed_as_unidentified(tmp_path):
    out = render_unidentified(tmp_path, _drawer(fresh=False),
                              _drawer(dev="sdb", port=2, serial="naa.2", fresh=True))
    assert out.strip() == ""


@requires_bash
@requires_jq
def test_the_machine_row_says_why_nothing_is_fresh(tmp_path):
    """‏"0/2 fresh" לבדו הוא המסך מה-Issue: מגירות מחוברות ואף מילה למה."""
    machine = {"name": "HP1", "awake": True, "fresh_drawers": 0, "drawers": 2,
               "state": None, "drawer_list": [
                   _drawer(serial=None, fresh=None),
                   _drawer(dev="sdb", port=2, serial="naa.2", fresh=False)]}
    out = render_done(tmp_path, [machine], fn="room_draw")
    row = next(line for line in out.splitlines() if "HP1" in line and "fresh" in line)
    assert "0/2" in row and "1 unidentified" in row


@requires_bash
@requires_jq
def test_a_round_with_nothing_written_yet_prints_nothing(tmp_path):
    """כותרת `WRITTEN:` ריקה היא רעש בלי מידע."""
    out = render_done(tmp_path, [_machine("HP1", _drawer(state=None, fresh=True))])
    assert "WRITTEN:" not in out
