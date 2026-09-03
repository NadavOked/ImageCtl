"""המספר שמוצג והמספר שנקרא הם אותו מספר — גם כשמניפסט אחד נכשל (#99).

ב-`image_menu` המונה `_i` מילא שלושה תפקידים: המספר שעל המסך, גבול
הוולידציה, ו**במשתמע** מספר השורה ב-`image_ids.txt`. שלושתם מתפצלים
ב-`continue` הראשון, כי `_i` גדל **לפני** המשיכה ואילו השורה נכתבת
רק **אחריה**.

שלושה אימג'ים ו-`B` שנכשל (שרת עמוס, קובץ שנמחק, ‏`http=000`):

    המסך:  1) Windows 11        הקובץ:  שורה 1 = A
           3) Ubuntu 24                 שורה 2 = C
           "Choose an image [1-3]"

* המפעיל מקליד **3** — מה שהוא רואה ליד Ubuntu — ומקבל מחרוזת ריקה.
  ‏`sed` מצליח, ‏`image_menu` מחזירה 0, והסבב נפתח עם ``"image_id":""``.
  השרת מחזיר 409, והמסך אומר "is another round already active?" —
  **אבחנה שגויה לגמרי**.
* המפעיל מקליד **2** — שורה שאינה על המסך — ומקבל את `C`. **הפצה
  לכיתה שלמה מתבצעת על אימג' שהוא לא בחר**, בלי שום סימן.

הבדיקה בוחרת לפי **מה שהמסך מציג**, לא לפי מספר קבוע: זה מה שהמפעיל
עושה, וזו הטענה היחידה ששווה לבדוק.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from test_agent import AGENT, BASH, posix, sh

pytestmark = pytest.mark.skipif(BASH is None, reason="bash is required")

#: ‏`B` הוא היחיד שהמשיכה שלו נכשלת.
STUBS = (
    'json_get_join() { echo "img_A img_B img_C"; }; '
    'http_get() { case "$1" in *img_B*) return 22 ;; esac; '
    '  case "$1" in *img_A*) echo A ;; *img_C*) echo C ;; esac; }; '
    'json_get() { _f=$(cat "$1"); case "$2" in '
    '  .name) case "$_f" in A) echo "Windows 11" ;; C) echo "Ubuntu 24" ;; esac ;; '
    '  .family) echo 256 ;; esac; }; '
)


def run_menu(tmp_path: Path, choice: str) -> tuple[str, str]:
    """מריץ `image_menu` עם בחירה נתונה. מחזיר (מה שהוחזר, המסך)."""
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    screen = tmp_path / "screen.txt"
    out = sh(
        f'export RUN_DIR={posix(run)!r} SERVER=http://s RESP={posix(run)}/r.json; '
        f'echo "{{}}" > {posix(run)}/r.json; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/classround.sh; '
        + STUBS
        + f'echo {choice} | image_menu 2> {posix(screen)}'
    )
    return out, screen.read_text(encoding="utf-8")


def number_of(screen: str, name: str) -> str:
    """המספר שמופיע על המסך ליד השם — מה שהמפעיל באמת מקליד."""
    m = re.search(rf"^\s*(\d+)\)\s*{re.escape(name)}", screen, re.M)
    assert m, f"‏{name} אינו על המסך:\n{screen}"
    return m.group(1)


def test_choosing_what_the_screen_shows_returns_that_image(tmp_path):
    """הבקרה השלילית של #99. לפני התיקון המסך אמר `3) Ubuntu 24`,
    והקלדת 3 החזירה מחרוזת ריקה."""
    _, screen = run_menu(tmp_path, "1")
    picked = number_of(screen, "Ubuntu 24")

    out, _ = run_menu(tmp_path, picked)
    assert "img_C" in out, f"בחירה ב-{picked} (Ubuntu) לא החזירה את img_C:\n{out}"


def test_the_prompt_never_offers_a_number_that_is_not_on_the_screen(tmp_path):
    """הענף החמור: מספר שאינו על המסך החזיר אימג' **אחר**, ולא שגיאה."""
    _, screen = run_menu(tmp_path, "1")
    shown = set(re.findall(r"^\s*(\d+)\)", screen, re.M))
    top = re.search(r"\[1-(\d+)\]", screen)
    assert top, f"אין שורת בחירה:\n{screen}"
    assert set(str(n) for n in range(1, int(top.group(1)) + 1)) == shown, (
        f"הטווח המוצע אינו זהה למה שעל המסך:\n{screen}")


def test_a_manifest_that_failed_is_named_and_not_silently_dropped(tmp_path):
    """אימג' שנשמט הוא מידע, לא היעדר מידע — עיקרון 5."""
    _, screen = run_menu(tmp_path, "1")
    assert "Windows 11" in screen and "Ubuntu 24" in screen
    assert re.search(r"skip|דולג|לא ניתן", screen, re.I), (
        f"אימג' שנשמט לא הוזכר על המסך:\n{screen}")


def test_the_menu_still_works_when_every_manifest_arrives(tmp_path):
    """המקרה שנבדק במעבדה — שרת שעונה על הכל — חייב להישאר כשהיה."""
    run = tmp_path / "run"
    run.mkdir(exist_ok=True)
    screen = tmp_path / "ok.txt"
    out = sh(
        f'export RUN_DIR={posix(run)!r} SERVER=http://s RESP={posix(run)}/r.json; '
        f'echo "{{}}" > {posix(run)}/r.json; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/classround.sh; '
        'json_get_join() { echo "img_A img_C"; }; '
        'http_get() { case "$1" in *img_A*) echo A ;; *img_C*) echo C ;; esac; }; '
        'json_get() { _f=$(cat "$1"); case "$2" in .name) echo "img-$_f" ;; '
        '  .family) echo 256 ;; esac; }; '
        f'echo 2 | image_menu 2> {posix(screen)}'
    )
    assert "img_C" in out, out
