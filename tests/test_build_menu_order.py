"""#1244: the build machine's menu -- capture first, then room, direct, restore.

Nadav's order for "מה לעשות עם המחשב הזה?": 1 capture a new image, 2 clone
from the server, 3 direct clone, 4 restore onto this disk; the class card
(hidden in v1) last. Checked by behaviour, not by reading the source:

* the native GUI (`native-gui/src/screens.c` MENU + `main.c` visible_card_ids)
  -- rendered with ``--png``: the capture card is the only one with the amber
  "admin" pill, so the pill's pixels say which end of the row it is at. The
  cards are laid out right to left (RTL), card 1 = key 1 at the right edge.

The text menu that printed the same list (`build_menu_options`) was removed
on 11/10 with the rest of the text screens (GUI only); its three tests went
with it. The web station page (`server/static/station/index.html`) already
lists capture first and has no direct/restore cards.

Negative control: on origin/main before #1244 the pill is in the left half of
the row, and the test fails there.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import BASH, _build_gui, _pkgconfig, _png, _png_rgb, _token

REPO = Path(__file__).resolve().parent.parent

pytestmark = requires_native(("bash", BASH))


def _pill_pixels(path: Path, color: tuple[int, int, int], tol: int = 2) -> tuple[int, int]:
    """Pixels of `color` (within `tol`) in the right and the left half."""
    w, _h, ch, px = _png_rgb(path)
    right = left = 0
    for i in range(0, len(px), ch):
        if all(abs(px[i + k] - color[k]) <= tol for k in range(3)):
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
    half -- card 1, key 1 -- and nowhere in the left half. The pill is a flat
    fill, so it is matched exactly (tolerance 2): since the 10/10 mockup the
    restore card -- bottom-left -- says "הדיסק נמחק." in red, and one
    anti-aliased edge pixel of that text (~9% red over white) falls within 6
    of the amber's value. That pixel is not a pill."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    # --png renders every card; "message" routes only with a message= record.
    state.write_text("menu_class=0\nmessage=x|y\n", newline="\n")
    _png(binary, tmp_path / "card", state)
    right, left = _pill_pixels(tmp_path / "card-menu-light.png",
                               _token("--clr-warning-soft"))
    assert right > 100, (right, left)
    assert left == 0, (right, left)
