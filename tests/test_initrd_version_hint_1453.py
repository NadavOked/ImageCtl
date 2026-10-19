"""#1453 -- the cloner header said "ה-initrd ישן מהשרת" for *any* difference.

Metal, cloner 2, 16/10: the lab server was v0.74.2 and only the initrd was
swapped for v0.74.2-22-g7e5f4427 -- a *newer* agent -- and the orange tag still
called it old. The header now says what it compared: older, newer, or "do not
match" when the two cannot be ordered (the same position with another hash, or
a version that is not X.Y.Z[-N-gSHA]). Equal versions draw no tag at all.

The strings come from the real header, through the same ``text_make`` driver
as ``test_lc_progress_1437_1440`` (cc + pango: the Testrunner).
"""

from __future__ import annotations

import pytest

from test_lc_progress_1437_1440 import driver, needs_cc, screens, state  # noqa: F401  (driver is a fixture)

OLD, NEWER, DIFFER = "ה-initrd ישן מהשרת", "ה-initrd חדש מהשרת", "ה-initrd לא תואם לשרת"


def _tags(driver, tmp_path, agent: str, server: str) -> list[str]:
    (drawn,) = screens(driver, tmp_path, state(
        30, 10**9, head=f"agent_version={agent}\nmachine_name=CLONER-02\nserver_version={server}\n"))
    return [s for s in drawn if s.startswith("ה-initrd")]


@needs_cc
@pytest.mark.parametrize("agent,server,want", [
    ("v0.74.2-22-g7e5f4427", "v0.74.2", NEWER),          # the lab swap that was reported
    ("v0.74.2", "v0.74.2-22-g7e5f4427", OLD),
    ("v0.73.2", "v0.74.2", OLD),
    ("v0.75.0", "v0.74.9", NEWER),
    ("v0.74.2-22-g7e5f4427", "v0.74.2-9-gabcdef0", NEWER),
    ("v0.74.2-22-g7e5f4427", "v0.74.2-22-g1111111", DIFFER),   # same place, another build
    ("v0.74.2-dirty", "v0.74.2", DIFFER),                       # not orderable: no claim of old or new
    ("v0.74.2", "v0.74.2", None),                                # equal: no tag
    ("unknown", "v0.74.2", None),                                # unknown claims nothing (as before)
])
def test_the_tag_says_what_was_compared(driver, tmp_path, agent, server, want):  # noqa: F811
    tags = _tags(driver, tmp_path, agent, server)
    assert tags == ([want] if want else []), (agent, server, tags)
