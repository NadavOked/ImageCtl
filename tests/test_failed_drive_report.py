"""‏#553 — כונן שנכשל מדווח בשורה שאפשר לפעול לפיה.

**נמדד על ברזל, 08/09/2026:** סבב על 5 מגירות נפל, ‏**המפעיל לא ראה
שום דבר.** המסך הראה `0 of 5 written` ותו לא, ואיזה כונן נפל היה
קיים בשרת ולא הוצג בשום מקום.

## ⚠️ והמלכודת שהופכת את זה למסוכן

**שם ההתקן בלינוקס אינו סדר יציאות ה-SATA.** נמדד על HP2 באותו יום:

```
sda → port 1     sdb → port 3     sdc → port 2
```

שורה שאומרת "‏sdb נכשל" ומתורגמת ל"מגירה 2" **שולחת את הטכנאי
למגירה הלא נכונה** — והוא מוציא כונן תקין ומשאיר את הפגום בפנים.
**זה גרוע מלא לדווח בכלל.**

`port` הוא ה-`ataN` שהסוכן מדווח — החריץ הפיזי. הקרנל ממספר מ-1,
ולכן `ata1` הוא החריץ הראשון בבקר: **המגירה העליונה** (#27).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import room


def _disk(dev: str, port: int | None, serial: str | None,
          model: str | None = "SAMSUNG MZ7LN256") -> dict:
    return {"dev": dev, "port": port, "serial": serial, "model": model,
            "size_bytes": 256060514304, "removable": False,
            "scheme": "gpt", "has_data": True}


class _Member(dict):
    """שורת `session_members` מזויפת — רק `targets_json` נקרא כאן."""


def _drawers(disks: list[dict], targets: list[dict], written=frozenset()):
    """קורא ל-`drawer_list` האמיתי עם DB מזויף מינימלי.

    ‏**הפונקציה האמיתית ולא עותק שלה:** טסט שמשכפל את הלוגיקה עובר
    גם כשהמימוש נסחף.
    """
    class _Conn:
        def execute(self, *_a, **_k):
            class _R:
                def fetchone(self_inner):
                    return {"disks_json": json.dumps(disks)}
            return _R()

    member = _Member(targets_json=json.dumps(targets))
    return room.drawer_list(_Conn(), "aa:bb:cc:dd:ee:ff", set(written), member)


# --- המזהים שהשורה נשענת עליהם -------------------------------------------


def test_the_drawer_carries_serial_and_model():
    """בלי אלה, שורת הכשל אומרת "משהו נפל" ושולחת אדם לחפש."""
    got = _drawers([_disk("sda", 1, "naa.111")], [])[0]
    assert got["serial"] == "naa.111"
    assert got["model"] == "SAMSUNG MZ7LN256"
    assert got["port"] == 1


def test_the_physical_port_is_not_the_device_order():
    """**המלכודת עצמה**, בדיוק כפי שנמדדה על HP2.

    ‏`sdb` היא מגירה **3**. טסט שמניח `sda→1, sdb→2, sdc→3` היה
    עובר על נתונים מומצאים ונכשל על המכונה.
    """
    disks = [_disk("sda", 1, "naa.1"), _disk("sdb", 3, "naa.2"),
             _disk("sdc", 2, "naa.3")]
    by_dev = {d["dev"]: d["port"] for d in _drawers(disks, [])}
    assert by_dev == {"sda": 1, "sdb": 3, "sdc": 2}


def test_a_disk_without_a_port_reports_none_and_not_a_guess():
    """‏NVMe, ‏VM ובקר לא-ATA אינם מדווחים חריץ.

    **‏`None` ולא `0` ולא ניחוש** — מספר מגירה שגוי גרוע מהיעדר מספר,
    וזה בדיוק מה שההערה ב-`port_from_path` אומרת.
    """
    got = _drawers([_disk("nvme0n1", None, "nvme-1")], [])[0]
    assert got["port"] is None


@pytest.mark.parametrize("bad", [True, False, "1", 1.0])
def test_a_port_that_is_not_an_int_is_refused(bad):
    """‏`True` הוא `int` בפייתון. בלי הסינון, "מגירה True" על המסך."""
    assert _drawers([_disk("sda", bad, "naa.1")], [])[0]["port"] is None


# --- מה שהשורה מציגה ------------------------------------------------------


def test_a_failed_drawer_carries_its_state_and_error():
    """המצב והשגיאה מגיעים מ-`targets_json` של החבר."""
    got = _drawers(
        [_disk("sda", 1, "naa.1")],
        [{"dev": "sda", "state": "failed",
          "error": "fanout: buffer overrun (drive too slow)"}])[0]
    assert got["state"] == "failed"
    assert "buffer overrun" in got["error"]


def test_a_drawer_that_finished_is_not_marked_failed():
    """רדיוס הפגיעה — מגירה שהצליחה אינה נכנסת לרשימת הכשלים."""
    got = _drawers([_disk("sdb", 3, "naa.2")],
                   [{"dev": "sdb", "state": "done", "error": None}])[0]
    assert got["state"] == "done"
    assert got["error"] is None


def test_a_disk_the_round_never_touched_has_no_state():
    """כונן שלא היה יעד אינו "לא נכשל" — הוא לא נבדק.

    ‏`None` ולא `"ok"`: השניים נראים זהים על המסך ואינם זהים.
    """
    got = _drawers([_disk("sdc", 2, "naa.3")], [])[0]
    assert got["state"] is None
    assert got["error"] is None


# --- המסך עצמו: הבלוק שהטכנאי קורא -------------------------------------------
#
# ⚠️ **עד כאן `room_draw_failures` לא נבדק ברינדור בכלל.** הצד
# הפייתוני נבדק, והשורה שהאדם קורא בפועל אומתה **ידנית** על שרת
# המעבדה. ‏`jq` אינו קיים בתחנת הפיתוח, ולכן הטסטים האלה מדולגים
# כאן ומוצהרים — ולא "עוברים" על פלט ריק.

AGENT = Path(__file__).resolve().parent.parent / "agent"
BASH = shutil.which("bash")
JQ = shutil.which("jq")
requires_bash = pytest.mark.skipif(BASH is None, reason="bash לא זמין")
requires_jq = pytest.mark.skipif(
    JQ is None, reason="jq אינו מותקן — הרינדור לא היה רץ בכלל")


def render_failures(tmp_path: Path, drawers: list) -> str:
    """מריץ את `room_draw_failures` **האמיתי** מהקובץ."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "room.json").write_text(
        json.dumps({"machines": [{"name": "HP1", "drawer_list": drawers}]}),
        encoding="utf-8")
    script = textwrap.dedent(f"""
        RUN_DIR={run_dir.as_posix()!r}
        log() {{ :; }}
        . {AGENT.as_posix()}/lib/roomflow.sh
        room_draw_failures
    """)
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _failed(**over) -> dict:
    return {"dev": "sdb", "port": 3, "state": "failed", "serial": "naa.777",
            "model": "SAMSUNG MZ7LN256", "error": "fanout: buffer overrun",
            **over}


@requires_bash
@requires_jq
def test_the_failed_line_says_which_connector_on_the_board(tmp_path):
    """‏#567: החריץ **וגם** הכיתוב. ‏`ata3` הוא המחבר שכתוב עליו
    `SATA 2`, וטכנאי שקורא רק "3" הולך למחבר הרביעי."""
    out = render_failures(tmp_path, [_failed()])
    assert "drive 3 (SATA 2)" in out, out


@requires_bash
@requires_jq
def test_the_failed_line_carries_what_identifies_the_drive_in_hand(tmp_path):
    """‏`port` אומר לאן ללכת; הסריאל והדגם אומרים איזה כונן זה
    כשמחזיקים אותו (#553)."""
    out = render_failures(tmp_path, [_failed()])
    assert "naa.777" in out
    assert "SAMSUNG MZ7LN256" in out
    assert "fanout: buffer overrun" in out


@requires_bash
@requires_jq
def test_a_failed_drive_with_no_port_is_not_given_an_invented_one(tmp_path):
    """⚠️ מספר מגירה שגוי גרוע ממספר חסר: הוא שולח אדם להוציא כונן
    תקין. ‏NVMe ובקר לא-ATA אינם מדווחים `ataN`."""
    out = render_failures(tmp_path, [_failed(dev="nvme0n1", port=None)])
    assert "nvme0n1" in out
    assert "SATA" not in out


@requires_bash
@requires_jq
def test_a_drive_with_no_serial_says_so_instead_of_looking_identified(tmp_path):
    out = render_failures(tmp_path, [_failed(serial=None, model=None)])
    assert "no serial" in out
    assert "unknown model" in out


@requires_bash
@requires_jq
def test_a_round_with_no_failures_prints_nothing(tmp_path):
    """כותרת `FAILED DRIVES:` ריקה היא בהלה בלי סיבה."""
    out = render_failures(tmp_path, [_failed(state="done")])
    assert out.strip() == ""


@requires_bash
@requires_jq
def test_every_failed_line_fits_the_eighty_column_screen(tmp_path):
    out = render_failures(tmp_path, [_failed(model="A" * 30, serial="B" * 20)])
    for line in out.splitlines():
        assert len(line) <= 80, line
