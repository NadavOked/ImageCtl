"""#1437 / #1440 — the local clone's progress screen on metal (cloner 2,
15/10, v0.73.2): "כ-444 דק'" after 12 s, "0%" with 1 GB of 500 copied,
"מכונה לא ידוע · סוכן לא ידוע · שרת לא ידוע" with no server, and a 496 GB
partition with 203 GB in use drawn as "496 GB בשימוש".

Two halves:

* **the agent's records** (bash + jq): a ``used_bytes`` that was not
  measured reaches the screen as ``-1``, not as the partition's size, and the
  copy's total is ``null`` -- not a sum that has a size in it (#1440).
* **what the screen says** (cc + pango, the Testrunner): a driver renders the
  real screens with ``text_make`` wrapped (``ld --wrap``) and prints every
  string drawn, so the assertions are on the words, not on pixels.

The "copied" counter that "lagged" the finished partition (#1437 item 2) is
the same root as #1440: on v0.73.2 every NTFS ``used_bytes`` was null (#1368),
partition 1 was drawn at its size "3.03GB בשימוש" (no "מתוך" -- the
bytes == size branch), while the counter showed what partclone actually read.

**Negative control:** on the code before this change every test below fails
-- lcgui.sh prints the size, localclone.sh sums it, the big number is "0%",
the time left is the average since the start (313 min here, 33 is right),
an unmeasured partition reads "לא נמדד בשימוש מתוך", and the header says
"לא ידוע" three times.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix
from test_local_clone_agent import run

G = 1_000_000_000

needs_jq = requires_native(("bash", BASH), ("jq", shutil.which("jq")))
needs_cc = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    why="native-gui נבנה על המעבדה בלבד",
)

MANIFEST = ('{"partitions":['
            '{"index":1,"role":"esp","fs":"vfat","size_bytes":104857600,"used_bytes":%s},'
            '{"index":2,"role":"windows","fs":"ntfs","size_bytes":496000000000,"used_bytes":%s},'
            '{"index":3,"role":"swap","fs":"swap","size_bytes":2000000000,"used_bytes":null}]}')


# --- the agent's records ------------------------------------------------------------

@needs_jq
def test_an_unmeasured_partition_reaches_the_screen_as_not_measured(tmp_path):
    manifest = MANIFEST % ("31000000", "null")
    out = run(tmp_path, 'mkdir -p "$LC/run"; cat > "$LC/run/manifest.json" <<\'EOF\'\n' + manifest + '\nEOF\n'
              'date +%s > "$LC/run/started"; echo sdc > "$LC/run/src"; echo null > "$LC/run/total"; lc_gui_run')
    assert out.returncode == 0, out.stderr
    parts = [l for l in out.stdout.splitlines() if l.startswith("lc_part=")]
    assert parts[1].split("|")[3] == "-1", parts                 # not 496000000000 under "בשימוש"
    assert parts[0].split("|")[3] == "31000000", parts           # a measured one is its own
    assert parts[2].split("|")[3] == "0", parts                  # swap is recreated, never copied
    assert out.stdout.startswith("lc_run=sdc|null|"), out.stdout


@needs_jq
@pytest.mark.parametrize("used2,want", [("null", "null"), ("203100000000", str(31000000 + 203100000000))])
def test_the_total_is_not_known_when_one_partition_was_not_measured(tmp_path, used2, want):
    (tmp_path / "m.json").write_text(MANIFEST % ("31000000", used2), encoding="utf-8", newline="\n")
    out = run(tmp_path, f'lc_run_total {posix(tmp_path / "m.json")!r}')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == want, out.stdout              # never 496 GB + the ESP


# --- what the screen says ------------------------------------------------------------

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "text.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static int logging;
Text __real_text_make(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d);
Text __real_text_make_ltr(cairo_t *cr, Font f, double px, int w, const char *s, int wp);
Text __wrap_text_make(cairo_t *cr, Font f, double px, int w, const char *s, int wp, Dir d) {
    if (logging) printf("T %s\n", s);
    return __real_text_make(cr, f, px, w, s, wp, d);
}
Text __wrap_text_make_ltr(cairo_t *cr, Font f, double px, int w, const char *s, int wp) {
    if (logging) printf("T %s\n", s);
    return __real_text_make_ltr(cr, f, px, w, s, wp);
}
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    snprintf(a.mac, sizeof a.mac, "3C:52:82:A1:00:1F");
    for (int i = 1; i < argc; i++) {
        FILE *f = fopen(argv[i], "r"); if (!f) { perror(argv[i]); return 2; }
        state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a);
        cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
        cairo_t *cr = cairo_create(s);
        printf("@ %d\n", i); logging = 1; app_draw(&a, cr, 1280, 800); logging = 0; fflush(stdout);
        cairo_destroy(cr); cairo_surface_destroy(s);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("lc1437")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-Wl,--wrap=text_make -Wl,--wrap=text_make_ltr '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def screens(driver: Path, tmp_path: Path, *states: str) -> list[list[str]]:
    """The strings drawn after each state, in order (one reload per state)."""
    paths = []
    for i, text in enumerate(states):
        p = tmp_path / f"s{i}.txt"; p.write_text(text, encoding="utf-8", newline="\n"); paths.append(posix(p))
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(driver), *paths], capture_output=True, text=True, encoding="utf-8", timeout=120,
                         env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    draws: list[list[str]] = []
    for line in out.stdout.splitlines():
        if line.startswith("@ "): draws.append([])
        elif line.startswith("T ") and draws: draws[-1].append(line[2:])
    assert len(draws) == len(states), out.stdout
    return draws


DISK = "lc_disk=3|sdc|Samsung 860|S3|500107862016|ok|ok|Windows|2|-1|500107862016|1|\n"
DISK2 = "lc_disk=2|sdb|Samsung 870|S2|500107862016|ok|ok||0|0|-1|0|\n"


def state(elapsed: int, copied: int, total: str = "206000000000", used2: str = "203100000000", head: str = "") -> str:
    return ("lc_screen=local\nlc_inv=done\nlc_phase=copying\n" + head + DISK + DISK2 +
            f"lc_run=sdc|{total}|{copied}|{elapsed}|0||2|\n"
            "lc_part=1|EFI|vfat|3030000000|3030000000|done|0\n"
            f"lc_part=2|Windows|ntfs|{used2}|496000000000|copy|{max(copied - 3030000000, 0)}\n"
            f"lc_tgt=sdb|writing|{copied}|2|1||||{total}\n")


@needs_cc
def test_one_gb_of_five_hundred_is_not_zero_percent(driver, tmp_path):
    (drawn,) = screens(driver, tmp_path, state(30, 1 * G, total="500000000000"))
    assert "0.2%" in drawn and "0%" not in drawn, drawn


@needs_cc
def test_the_time_left_is_the_last_minute_of_the_copy_not_the_average_since_the_start(driver, tmp_path):
    """The copy started 600 s in (a shrink), then 100 MB/s. The average since
    the start says 10.6 MB/s and 313 minutes; the window says 100 and 33."""
    first, second, third = screens(driver, tmp_path, state(610, 1 * G), state(640, 4 * G), state(671, 7_100_000_000))
    assert "מחשב את הזמן שנותר…" in first and "עוד לא נמדדה" in first, first          # one sample: nothing yet
    assert "מחשב את הזמן שנותר…" in second and "100 MB/s" in second, second           # 30 s: the speed, not the time
    assert "כ-33 דק' נותרו" in third and "100 MB/s" in third, third                   # (206 - 7.1) GB at 100 MB/s


@needs_cc
def test_a_new_run_starts_the_window_over(driver, tmp_path):
    *_, again = screens(driver, tmp_path, state(610, 1 * G), state(671, 7_100_000_000), state(5, 1 * G))
    assert "מחשב את הזמן שנותר…" in again and not any("נותרו" in s for s in again), again


@needs_cc
def test_an_unmeasured_partition_is_said_and_the_total_and_time_do_not_pretend(driver, tmp_path):
    (drawn,) = screens(driver, tmp_path, state(700, 12 * G, total="null", used2="-1"))
    joined = "\n".join(drawn)
    assert "בשימוש: לא נמדד" in joined, drawn                              # partition 2, not "496 GB בשימוש"
    assert "הסך לא ידוע — מחיצה 2 לא נמדדה" in drawn, drawn                  # the copied box: no "/ 500"
    assert "הזמן שנותר לא ידוע — מחיצה 2 לא נמדדה" in drawn, drawn
    assert "—" in drawn and not any(s.endswith("%") and s[0].isdigit() for s in drawn), drawn   # no share of an unknown whole
    assert not any(" / " in s and s.endswith(" GB") for s in drawn), drawn


@needs_cc
def test_with_no_server_the_header_says_what_the_cloner_knows(driver, tmp_path):
    offline, online = screens(driver, tmp_path, state(30, G, head="agent_version=v0.74.2\n"),
                              state(30, G, head="agent_version=v0.74.2\nmachine_name=CLONER-02\nserver_version=v0.74.2\n"))
    assert "3C:52:82:A1:00:1F" in offline, offline                          # the machine's own MAC, not "לא ידוע"
    assert "סוכן v0.74.2 · שרת: לא מחובר (שיכפול מקומי)" in offline, offline
    assert "לא ידוע" not in offline, offline
    assert "CLONER-02" in online and "סוכן v0.74.2 · שרת v0.74.2" in online, online
