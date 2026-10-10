"""Small fixes from the native-GUI design audit (docs/design/native-ui-audit.md).

Each test pins one defect that was visible in the 108 renders of 2026-10-10
(1920x1080 and 1024x768, light and dark).  The native GUI cannot be built on
a Windows workstation, so these are source contracts: they fail on the
pre-fix source and pass on the fix.  The pixel-level proof is the re-render
listed in the audit's "what to render" section.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "native-gui" / "src"


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_installer_rail_active_number_is_not_white_on_white():
    """The active step's disc was header_text (white) and its number on_ink
    (white too), so every step 1..7 showed an empty white circle. Since the
    10/10 mockup the current disc is the action colour and the number on
    it on_ink; no disc is header_text any more."""
    s = _src("screens_install.c")
    line = next(x for x in s.splitlines() if "text_draw(cr,&n," in x)
    assert "filled?t->on_ink:dim" in line, line
    assert "Rgb disc=bad?t->danger:(done?t->success_ink:t->indigo);" in s
    assert "draw_set_a(cr,c,active||done||bad?1:.35)" not in s      # the white disc is gone


def test_installer_rail_future_steps_are_readable_in_the_light_theme():
    """Future steps used t->muted (#565656 in the light theme) on the #25333D
    rail: 1.8:1.  They must use a mix of the rail's own text colour."""
    s = _src("screens_install.c")
    assert "Rgb dim=" in s
    assert re.search(r"active\?t->header_text:dim\)", s)
    assert "active?t->header_text:t->muted" not in s


def test_installer_disk_row_wipe_label_does_not_sit_on_the_has_column():
    """"יימחק" was drawn at r.x+68 (58 wide), on top of the right-aligned
    "gpt:2 partitions ..." text at 1024x768.  It now sits on its own line in
    the same column."""
    s = _src("screens_install_setup.c")
    assert '"יימחק",11,600,r.x+68' not in s
    assert re.search(r'"יימחק",11,600,r\.x\+r\.w\*\.28,r\.y\+33', s)
    assert "r.y+(selected?9:14)" in s


def test_error_text_is_right_aligned_like_every_other_rtl_block():
    """error_make fed text_draw at the field's left edge with left-aligned
    text, so form errors appeared at the far left of an RTL card."""
    s = _src("widgets.c")
    line = next(x for x in s.splitlines() if x.startswith("Text error_make"))
    assert "rtl_block_make" in line, line


def test_empty_states_and_the_progress_note_use_the_title_edge():
    """Drawn with text_draw at the left of an RTL page: pick's "no disks",
    the class list's "no classes", the class rows' "nobody joined" and the
    progress note.  All were visibly on the wrong side."""
    for name, anchor in (("screens_capture.c", "none = sub_make("),
                         ("screens_capture.c", "Text note = sub_make("),
                         ("screens_class.c", "none = sub_make("),
                         ("screens_rounds.c", "rs->empty = sub_make(")):
        lines = _src(name).splitlines()
        i = next(k for k, x in enumerate(lines) if anchor in x)
        assert "PANGO_ALIGN_RIGHT" in lines[i + 1], (name, anchor, lines[i + 1])


def test_metric_value_that_wraps_is_shrunk_to_one_line():
    """metric_height() assumes a one-line value; at 1024x768 the unknown-total
    caption wrapped and overflowed its box onto the note below."""
    s = _src("widgets.c")
    body = s[s.index("void draw_metric"):]
    body = body[:body.index("\n}\n")]
    assert "text_line_height(cr,FONT_SANS,N_METRIC_VALUE) * 1.5" in body
    assert "N_METRIC_VALUE * 0.7" in body
