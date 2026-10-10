"""#1244: the build machine's menu -- capture first, then room, direct, restore.

Nadav's order for "מה לעשות עם המחשב הזה?": 1 capture a new image, 2 clone
from the server, 3 direct clone, 4 restore onto this disk; the class card
(hidden in v1) last. The same list is drawn in three places, and each is
checked here by behaviour, not by reading the source:

* the text menu (`agent/lib/buildmenuitems.sh:build_menu_options`) -- the
  file the numbered menu is printed from, for both roles;
* the native GUI (`native-gui/src/screens.c` MENU + `main.c` visible_card_ids)
  -- rendered with ``--png``: the capture card is the only one with the amber
  "admin" pill, so the pill's pixels say which end of the row it is at. The
  cards are laid out right to left (RTL), card 1 = key 1 at the right edge.

The web station page (`server/static/station/index.html`) already lists
capture first and has no direct/restore cards; it is not part of the change.
`tests/test_build_menu.py` drives the text menu end to end (labels, numbers
and the capture taken on "1").

Negative control: on origin/main the text menu prints capture last and the
pill is in the left half of the row; both tests fail there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import (BASH, _build_gui, _pkgconfig, _png, _png_rgb, _token,
                             posix, sh)

REPO = Path(__file__).resolve().parent.parent

pytestmark = requires_native(("bash", BASH))


def text_menu(tmp_path: Path, role: str, classes: bool) -> list[str]:
    out = sh(
        f"RUN_DIR={posix(tmp_path)!r}; BUILD_ROLE={role}; "
        ". agent/lib/buildmenuitems.sh; "
        f"class_deploy_on() {{ {'true' if classes else 'false'}; }}; "
        'build_menu_options && cat "$RUN_DIR/build_menu.txt"')
    assert out.returncode == 0, out.stderr
    return out.stdout.split()


def test_the_text_menu_offers_capture_first_for_admin(tmp_path):
    assert text_menu(tmp_path, "admin", False) == ["capture", "room", "direct", "self"]


def test_the_class_option_stays_last(tmp_path):
    assert text_menu(tmp_path, "admin", True) == ["capture", "room", "direct", "self", "class"]


def test_deploy_never_sees_the_capture_and_keeps_the_same_order(tmp_path):
    """The role gate is unchanged: `deploy` gets 1 room, 2 direct, 3 restore."""
    assert text_menu(tmp_path, "deploy", False) == ["room", "direct", "self"]
    assert text_menu(tmp_path, "deploy", True) == ["room", "direct", "self", "class"]


def _pill_pixels(path: Path, color: tuple[int, int, int]) -> tuple[int, int]:
    """Pixels of `color` (tolerance 6) in the right and the left half."""
    w, _h, ch, px = _png_rgb(path)
    right = left = 0
    for i in range(0, len(px), ch):
        if all(abs(px[i + k] - color[k]) <= 6 for k in range(3)):
            if (i // ch) % w >= w // 2:
                right += 1
            else:
                left += 1
    return right, left


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)
def test_the_native_menu_draws_the_capture_card_first(tmp_path):
    """--png renders the menu as admin (png_setup). The amber pill
    (--clr-warning-soft) marks the capture card; it must sit in the right
    half -- card 1, key 1 -- and nowhere in the left half."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    # --png renders every card; "message" routes only with a message= record.
    state.write_text("menu_class=0\nmessage=x|y\n", newline="\n")
    _png(binary, tmp_path / "card", state)
    right, left = _pill_pixels(tmp_path / "card-menu-light.png",
                               _token("--clr-warning-soft"))
    assert right > 100, (right, left)
    assert left == 0, (right, left)
