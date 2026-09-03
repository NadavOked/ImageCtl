"""hivewrite — כתיבת ערך בודד ל-hive בלי לדרוס את שכניו (#33).

הבדיקות מקמפלות את agent/hivewrite.c ומריצות אותו מול hive אמיתי
(‏tests/fixtures/system-mini.hiv, נוצר עם make_fixture.py על Windows).
הנקודה הקריטית אינה "הערך נכתב" אלא "שאר ערכי המפתח שרדו" — ‏setval
של hivexsh היה מוחק את כל הגדרות ה-TCP/IP של המכונה המשוחזרת.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "agent" / "hivewrite.c"
FIXTURE = REPO / "tests" / "fixtures" / "system-mini.hiv"

PARAMS = r"ControlSet001\Services\Tcpip\Parameters"
COMPUTERNAME = r"ControlSet001\Control\ComputerName\ComputerName"

# ‏#33 — שם המחשב ב-Windows. חבילה שדילגה כאן נראתה בדיוק כמו חבילה
# שעברה, וזה מה שהחביא את הבאג חודשים (#52).
pytestmark = requires_native(
    "gcc", "hivexget", paths=("/usr/include/hivex.h",), posix=True,
    why="hivewrite צריך gcc, ‏libhivex-dev ו-hivexget",
)


@pytest.fixture(scope="module")
def hivewrite(tmp_path_factory):
    binary = tmp_path_factory.mktemp("build") / "hivewrite"
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
         "-o", str(binary), str(SOURCE), "-lhivex"],
        check=True,
    )
    return binary


@pytest.fixture
def hive(tmp_path):
    dst = tmp_path / "SYSTEM"
    shutil.copyfile(FIXTURE, dst)
    return dst


def run(binary, *args):
    return subprocess.run(
        [str(binary), *map(str, args)], capture_output=True, text=True
    )


def hivexget(hive, path, name):
    result = subprocess.run(
        ["hivexget", str(hive), path, name], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def value_listing(hive, path):
    """כל ערכי המפתח, שורה לערך — הבסיס להשוואת 'מי שרד'."""
    result = subprocess.run(
        ["hivexget", str(hive), path], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    return sorted(result.stdout.splitlines())


def test_writes_both_tcpip_names(hivewrite, hive):
    result = run(hivewrite, hive, PARAMS,
                 "Hostname", "LAB1-05", "NV Hostname", "LAB1-05")
    assert result.returncode == 0, result.stderr
    assert hivexget(hive, PARAMS, "Hostname") == "LAB1-05"
    assert hivexget(hive, PARAMS, "NV Hostname") == "LAB1-05"


def test_every_sibling_value_survives(hivewrite, hive):
    """הסיבה שהעוזר קיים: המפתח מלא הגדרות TCP/IP שאסור לאבד."""
    before = value_listing(hive, PARAMS)
    result = run(hivewrite, hive, PARAMS,
                 "Hostname", "LAB1-05", "NV Hostname", "LAB1-05")
    assert result.returncode == 0, result.stderr
    after = value_listing(hive, PARAMS)

    changed = {line for line in before if "TEST-WIN11" in line}
    assert len(changed) == 2, "the fixture lost its expected shape"
    assert sorted(set(before) - changed
                  | {line.replace("TEST-WIN11", "LAB1-05") for line in changed}
                  ) == after


def test_single_value_key(hivewrite, hive):
    result = run(hivewrite, hive, COMPUTERNAME, "ComputerName", "LAB1-05")
    assert result.returncode == 0, result.stderr
    assert hivexget(hive, COMPUTERNAME, "ComputerName") == "LAB1-05"


def test_missing_key_fails_and_leaves_the_hive_alone(hivewrite, hive):
    original = FIXTURE.read_bytes()
    result = run(hivewrite, hive,
                 r"ControlSet001\Control\ComputerName\ActiveComputerName",
                 "ComputerName", "LAB1-05")
    assert result.returncode != 0
    assert "not found" in result.stderr
    assert hive.read_bytes() == original


def test_non_ascii_value_is_refused_before_commit(hivewrite, hive):
    original = FIXTURE.read_bytes()
    result = run(hivewrite, hive, PARAMS, "Hostname", "שם-בעברית")
    assert result.returncode != 0
    assert hive.read_bytes() == original


def test_dangling_pair_is_a_usage_error(hivewrite, hive):
    result = run(hivewrite, hive, PARAMS, "Hostname")
    assert result.returncode != 0
    assert "usage" in result.stderr


def test_get_reads_string_and_dword(hivewrite, hive):
    """‏-g הוא מסלול האימות של הסוכן — hivexget הוא wrapper ל-hivexsh
    שאינו רץ ב-initramfs, ולכן הקריאה-חזרה חייבת לעבור באותו בינארי."""
    result = run(hivewrite, "-g", hive, PARAMS, "Hostname")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "TEST-WIN11"
    result = run(hivewrite, "-g", hive, "Select", "Current")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


def test_get_reads_back_what_set_wrote(hivewrite, hive):
    assert run(hivewrite, hive, PARAMS,
               "Hostname", "LAB1-05", "NV Hostname", "LAB1-05").returncode == 0
    result = run(hivewrite, "-g", hive, PARAMS, "NV Hostname")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "LAB1-05"


def test_get_missing_value_fails(hivewrite, hive):
    result = run(hivewrite, "-g", hive, PARAMS, "NoSuchValue")
    assert result.returncode != 0
    assert "not found" in result.stderr