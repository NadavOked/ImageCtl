"""The approved native mockup (docs/design/apple-mockups/native.html, 10/10).

Nadav approved the "מוצע" column of the five screens on 10/10; this file
pins what was built from it, screen by screen. The audit behind it, with
the findings and file:line, is docs/design/native-ui-audit.md.

Two kinds of test, as everywhere in native-gui:

* source contracts -- run everywhere, also on the Windows workstation that
  has no C compiler. They fail on the pre-mockup source.
* ``--png`` renders -- ``requires_native``: built and run on the Linux
  Testrunner (cc + pango/cairo), a skip here and a failure there when the
  tools are missing (``IMAGECTL_REQUIRE_NATIVE=1``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import (_build_gui, _count_color, _pkgconfig, _png, _png_rgb,
                             _render_all, _token)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def code(text: str) -> str:
    """<text> without its C comments -- a comment may quote the old code."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def body(text: str, name: str) -> str:
    """The braces of the definition of <name> (not a call site)."""
    m = re.search(r"\n(?:static )?[\w][\w \*]*\b" + re.escape(name) + r"\([^;{)]*\)\s*\{", text)
    assert m, f"{name}() is not defined here"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


# --- 1. the build machine's menu ------------------------------------------------


def test_menu_cards_are_as_tall_as_their_content_two_to_a_row():
    """The cards were `ch = bottom - gy`: four 640-800px boxes, 70% empty."""
    menu = body(_src("screens.c"), "screen_menu")
    assert "ch=bottom-gy" not in menu
    assert "cols=2" in menu and "row_h=dmax(row_h,cards[j].h)" in menu
    card = body(_src("screens.c"), "menu_card_make")
    assert "k.h=18+dmax(48,k.title.h+4+k.desc.h+8+k.flow.h)+18;" in card
    assert "if(k.h<132)k.h=132;" in card


def test_menu_draws_four_distinct_icons_from_the_mockup_svgs():
    """Capture and restore shared ICON_DISK, room and direct ICON_NETWORK."""
    s = _src("screens.c")
    menu = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};", s, re.S).group(1)
    icons = re.findall(r"HIT_(CAPTURE|ROOM|DIRECT|RESTORE),(MENU_ICON_\w+)", menu)
    assert dict(icons) == {"CAPTURE": "MENU_ICON_CAPTURE", "ROOM": "MENU_ICON_SERVER",
                           "DIRECT": "MENU_ICON_DIRECT", "RESTORE": "MENU_ICON_RESTORE"}
    assert "ICON_NETWORK" not in s and "ICON_NETWORK" not in _src("widgets.h")
    icon = body(_src("widgets.c"), "draw_menu_icon")
    # one path per SVG, by its distinctive coordinates
    assert "cairo_move_to(cr, 12, 11); cairo_line_to(cr, 12, 3);" in icon          # capture arrow
    assert "draw_rrect_path(cr, (Rect){ 8, 2, 8, 5 }, 1);" in icon                # the server
    assert "draw_rrect_path(cr, (Rect){ 15, 7, 7, 10 }, 1.5);" in icon            # direct: 2nd disk
    assert "cairo_arc_negative(cr, 12, 12, 9, M_PI, atan2(-6.7, -6.0));" in icon  # restore arc
    assert "cairo_set_line_width(cr, 1.6);" in icon


def test_menu_pills_come_from_the_state_not_from_a_string():
    """'השרת מחובר' and 'מחשבי שיכפול ערים' were literals (screens.c:227)."""
    s = _src("screens.c")
    assert '"השרת מחובר"' not in s and '"מחשבי שיכפול ערים"' not in s
    pill = body(s, "menu_server_pill")
    assert "s->hello_rc==200" in pill and "PILL_BAD" in pill and "טרם נבדק" in pill
    cloners = body(s, "menu_cloner_pill")
    assert "if(!s->nmachines)return;" in cloners          # absent is not "0 awake"
    assert "s->machines[i].awake" in cloners and "fresh_drawers" in cloners


def test_escape_on_the_menu_asks_before_it_signs_out():
    """Esc set signed_in=0 at once (main.c:691) -- a stray key dropped the session."""
    main = _src("main.c")
    handle = body(main, "handle")
    esc = handle[handle.index("if (e->key == KEYSYM_ESC) {"):]
    esc = esc[:esc.index("\n        }")]
    menu_line = next(x for x in esc.splitlines() if "a->screen == SCREEN_MENU" in x)
    assert "a->logout_confirming = 1" in menu_line and "signed_in = 0" not in menu_line
    modal = body(main, "logout_modal")
    assert "KEYSYM_ENTER) sign_out(a)" in modal and "KEYSYM_ESC) a->logout_confirming = 0" in modal
    assert handle.index("logout_modal(a, e)") < handle.index("switch (e->type)")
    assert "if(a->logout_confirming)draw_logout_confirm(a,cr,W,H);" in body(_src("screens.c"), "screen_menu")


def test_menu_keyboard_has_shift_tab_and_arrows():
    """Tab went forward only (main.c:698); KEYSYM_BACKTAB arrived and was dropped."""
    handle = body(_src("main.c"), "handle")
    menu = handle[handle.index("if (a->screen == SCREEN_MENU) {\n            int ids[5]"):]
    assert "KEYSYM_BACKTAB" in menu and "menu_arrow(a, e->key, n)" in menu
    assert "case KEY_LEFT:" in _src("input.c") and "KEYSYM_LEFT, KEYSYM_RIGHT" in _src("input.h")
    arrow = body(_src("main.c"), "menu_arrow")
    assert "KEYSYM_LEFT && f % 2 == 0" in arrow        # RTL: left is the next card


def test_menu_status_bar_counts_the_cards_it_shows():
    """'1–4' was fixed -- a deploy user has three cards, classes make five."""
    keys = body(_src("screens.c"), "status_keys")
    assert '"1–4  בחירה' not in keys
    assert "(a->admin?4:3)+(a->st.menu_class?1:0)" in keys


@GUI_BUILD
def test_menu_leaves_the_bottom_of_the_body_empty(tmp_path):
    """1280x800, sample data (admin, five cards in three rows): the band just
    above the "התנתק" row is page background. The old full-height cards
    filled it with their surface colour."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-menu-light.png")
    bg = _token("--clr-bg")
    band = [px[(y * w + x) * ch:(y * w + x) * ch + 3] for y in range(650, 680) for x in range(200, w - 200, 7)]
    off = sum(1 for p in band if any(abs(p[k] - bg[k]) > 3 for k in range(3)))
    assert off == 0, f"{off} pixels of the band above the logout row are not background"


def _icon_boxes(path: Path) -> list[bytes]:
    """The 48x48 action-soft icon boxes of the menu cards, right to left and
    top to bottom, as raw pixel blocks."""
    w, h, ch, px = _png_rgb(path)
    soft = _token("--clr-action-soft")
    hit = bytearray(w * h)
    for i in range(w * h):
        p = px[i * ch:i * ch + 3]
        hit[i] = all(abs(p[k] - soft[k]) <= 2 for k in range(3))
    seen = bytearray(w * h); boxes = []
    for start in range(w * h):
        if not hit[start] or seen[start]:
            continue
        stack = [start]; seen[start] = 1; x0 = y0 = 10 ** 9; x1 = y1 = -1
        while stack:
            i = stack.pop(); x, y = i % w, i // w
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for j in (i - 1, i + 1, i - w, i + w):
                if 0 <= j < w * h and hit[j] and not seen[j] and abs(j % w - x) <= 1:
                    seen[j] = 1; stack.append(j)
        if 40 <= x1 - x0 <= 56 and 40 <= y1 - y0 <= 56:      # an icon box, not a pill
            boxes.append((y0, -x0, x0, y0, x1, y1))
    boxes.sort()
    out = []
    for _, _, x0, y0, x1, y1 in boxes:
        out.append(b"".join(bytes(px[(y * w + x0) * ch:(y * w + x1 + 1) * ch]) for y in range(y0, y1 + 1)))
    return out


@GUI_BUILD
def test_the_four_menu_icons_are_four_different_drawings(tmp_path):
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    boxes = _icon_boxes(tmp_path / "card-menu-light.png")
    assert len(boxes) >= 4, f"found {len(boxes)} icon boxes"
    assert len(set(boxes[:4])) == 4, "two of capture/room/direct/restore draw the same icon"


@GUI_BUILD
def test_the_server_pill_turns_red_when_the_last_hello_failed(tmp_path):
    """Same state twice, hello_rc 200 vs 503: the 503 menu has a red pill the
    200 one does not (the restore card's 'הדיסק נמחק.' is red in both)."""
    binary = _build_gui(tmp_path)
    red = _token("--clr-danger")
    counts = {}
    for rc in ("200", "503"):
        state = tmp_path / f"{rc}.txt"
        state.write_text(f"server_name=TA\nserver_addr=10.44.0.1\nhello_age=4\nhello_rc={rc}\n"
                         "message=x|y\n", newline="\n")
        _png(binary, tmp_path / rc, state, "1280x800")
        _, _, ch, px = _png_rgb(tmp_path / f"{rc}-menu-light.png")
        counts[rc] = _count_color(px, ch, red)
    assert counts["503"] - counts["200"] > 60, counts


@GUI_BUILD
def test_the_logout_card_draws_the_question(tmp_path):
    """--png card "logout": the menu under a scrim with a two-button dialog."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    a = (tmp_path / "card-menu-light.png").read_bytes()
    b = (tmp_path / "card-logout-light.png").read_bytes()
    assert a != b


# --- 2. the capture progress screen ---------------------------------------------


def test_capture_progress_says_what_is_known_not_dashes():
    """With no total the screen drew "--" in 88px over "הושלם" while the
    capture was running (screens_capture.c:200-204, audit finding 7)."""
    prog = code(body(_src("screens_capture.c"), "screen_progress"))
    assert '"--"' not in prog
    # "הושלם" is the caption of a known percentage only
    assert 'if (s->pct >= 0) { snprintf(big, sizeof big, "%d%%", s->pct > 100 ? 100 : s->pct); cap_s = "הושלם"; }' in prog
    assert prog.count('"הושלם"') == 1
    # the unknown total: the bytes read, big, and the unknown said in words
    assert "fmt_bytes(rd, sizeof rd, (double)s->bytes);" in prog and '"נקראו"' in prog
    assert "הסך לא ידוע — מוצג מה שכבר נקרא" in prog
    assert '"טרם נקראו בייטים"' in prog


def status_line(screen: str) -> str:
    """What status_keys returns for <screen>: its own case, else default."""
    keys = code(body(_src("screens.c"), "status_keys"))
    at = keys.find(f"case {screen}:")
    if at < 0:
        at = keys.index("default:")
    return keys[at:].split("return", 1)[1].split(";")[0]


def test_capture_progress_status_bar_offers_no_dead_escape():
    said = status_line("SCREEN_PROGRESS")
    assert "Esc" not in said, said
    assert "אין פעולה במסך הזה" in said


# --- 3. the cloner, the room and the live round ---------------------------------


def test_cloner_draws_a_card_per_disk_it_found_not_three():
    """`for (int port = 1; port <= 3; port++)` and "שלושה דיסקים" -- cloner 1
    has two SATA ports (audit finding 5, S19)."""
    src = code(_src("screens_cloner.c"))
    assert "port <= 3" not in src and "שלושה דיסקים" not in src
    ports = body(src, "found_ports")
    assert "drawer_for_port(s, port) || idle_for_port(s, port)" in ports
    assert "s->smart_pending && s->smart_port == port" in ports
    cloner = body(src, "screen_cloner")
    assert "int ports[MAX_DRAWERS], n = found_ports(&a->st, ports);" in cloner


def test_cloner_smart_answers_are_ordered_by_risk():
    """'דלג' was BTN_DANGER (red) and 'כתוב בכל זאת' BTN_PRIMARY -- the
    inviting colour on the risky choice. Now: replace, skip (plain), and
    write-anyway as a warning outline, laid from the inline end."""
    cloner = code(body(_src("screens_cloner.c"), "screen_cloner"))
    assert "BTN_DANGER" not in cloner and "BTN_PRIMARY" not in cloner
    draws = re.findall(r'key_button\(a, cr, x, by, ([^,]+(?:, [^,]+)?), "?[^,]*"?, (BTN_\w+), (HIT_SMART_\w+), 1\)', cloner)
    assert [d[2] for d in draws] == ["HIT_SMART_REPLACE", "HIT_SMART_SKIP", "HIT_SMART_RESCUE"], draws
    assert [d[1] for d in draws] == ["BTN_PLAIN", "BTN_PLAIN", "BTN_WARN"], draws
    assert "BTN_WARN" in _src("widgets.h") and "kind == BTN_WARN" in body(_src("widgets.c"), "draw_btn")


def test_cloner_and_standby_status_bars_list_only_working_keys():
    said = status_line("SCREEN_CLONER")
    assert said.strip() == '"אין פעולה במסך הזה"'          # no SMART question, no keys
    keys = code(body(_src("screens.c"), "status_keys"))
    cloner = keys[keys.index("case SCREEN_CLONER:"):keys.index("case SCREEN_RESTORE:")]
    assert "if(!a->st.smart_pending)" in cloner
    assert "דיסקים: 1 · 2 · 3" not in keys


def test_live_round_draws_a_bar_per_reported_disk():
    """The live grid drew max(drawer_count, nroom_drawers) bars -- a slot with
    no disk got a bar with the machine's percentage on it."""
    live = code(body(_src("screens_room.c"), "room_live_grid_draw"))
    assert "m->drawer_count" not in live
    assert "if (d) found[nfound++] = d;" in live
    room = code(body(_src("screens_room.c"), "screen_room"))
    assert "found += s->machines[i].room_drawers[j].present;" in room
    assert "if (slots < s->machines[i].drawer_count) slots = s->machines[i].drawer_count;" not in room


def _white_runs_mode(path: Path, top: int, bottom: int) -> int:
    """The most common number of long (>=120px) surface-white runs per row in
    [top, bottom): the number of cards side by side."""
    w, h, ch, px = _png_rgb(path)
    white = _token("--clr-surface")
    counts = {}
    for y in range(top, bottom):
        runs = run = 0
        for x in range(w):
            i = (y * w + x) * ch
            if all(abs(px[i + k] - white[k]) <= 3 for k in range(3)):
                run += 1
            else:
                runs += run >= 120; run = 0
        runs += run >= 120
        if runs:
            counts[runs] = counts.get(runs, 0) + 1
    return max(counts, key=counts.get) if counts else 0


@GUI_BUILD
def test_a_two_disk_cloner_shows_two_cards(tmp_path):
    binary = _build_gui(tmp_path)
    state = tmp_path / "two.txt"
    state.write_text("cloner=Office 2024\n"
                     "drawer=1|sda|waiting|0|0|\n"
                     "drawer=2|sdb|waiting|0|0|\n"
                     "message=x|y\n", newline="\n")
    _png(binary, tmp_path / "two", state, "1000x760")
    assert _white_runs_mode(tmp_path / "two-cloner-light.png", 60, 760 - 40) == 2


@GUI_BUILD
def test_the_smart_bar_has_no_red_and_puts_write_anyway_at_the_end(tmp_path):
    """Sample data: disk 2 has an open SMART question (verdict fail). In the
    bar above the status line: no danger red (skip is no longer red), and
    the warning-outlined "write anyway" is in the left half."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-cloner-light.png")
    warn, red = _token("--clr-warning"), _token("--clr-danger")
    y0, y1 = h - 36 - 74 + 5, h - 36 - 4
    warn_x, reds = [], 0
    for y in range(y0, y1):
        for x in range(w):
            i = (y * w + x) * ch
            p = px[i:i + 3]
            if all(abs(p[k] - warn[k]) <= 12 for k in range(3)):
                warn_x.append(x)
            if all(abs(p[k] - red[k]) <= 12 for k in range(3)):
                reds += 1
    assert reds == 0, f"{reds} red pixels in the SMART bar"
    assert len(warn_x) > 100, len(warn_x)
    assert max(warn_x) < w / 2, "the warning button is not at the inline end"


def _theme_hex(field: str) -> tuple[int, int, int]:
    light = re.search(r"const Theme THEME_LIGHT\s*=\s*\{(.*?)\};", _src("theme.c"), re.S).group(1)
    v = int(re.search(r"\." + field + r"\s*=\s*HEX\(0x([0-9A-Fa-f]{6})\)", light).group(1), 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


@GUI_BUILD
def test_the_live_round_draws_two_bars_for_two_reported_disks(tmp_path):
    """One machine, three configured slots, two disks reported. Down the
    middle column the track colour appears once for the round's big bar and
    once per disk bar: 1 + 2. The old grid drew a third bar for the empty slot."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "live.txt"
    state.write_text("round=img|2|0|7|24|5|17|-1|-1|-1\n"
                     "machine=CLONE-01|3C:52:82:A1:00:21|1|1|2|writing|42|1|\n"
                     "machine_drawers=0|3\n"
                     "room_drawer=0|1|S1|Model A|256060514304|passed|1|1\n"
                     "room_drawer=0|2|S2|Model B|500107862016|passed|1|0\n"
                     "message=x|y\n", newline="\n")
    _png(binary, tmp_path / "live", state, "1000x760")
    w, h, ch, px = _png_rgb(tmp_path / "live-room-live-light.png")
    track = _theme_hex("track")
    x, runs, inside = w // 2, 0, False
    for y in range(48, h - 36):
        i = (y * w + x) * ch
        on = all(abs(px[i + k] - track[k]) <= 3 for k in range(3))
        runs += on and not inside
        inside = on
    assert runs == 3, f"{runs} track runs down the middle (want the big bar + 2 disks)"


# --- 4. the installer -------------------------------------------------------------


def test_installer_is_one_centred_column_of_one_width():
    """1556 / 760 / 720 / 650 wide, step by step (audit finding 9)."""
    frame = _src("screens_install.c")
    assert "#define INSTALL_COL_W 760.0" in frame
    assert "col=fmin(INSTALL_COL_W,cw-64),cx=(cw-col)/2" in frame
    assert "{cx,105,col,H-185}" in frame
    setup, forms = _src("screens_install_setup.c"), _src("screens_install_forms.c")
    assert "fmin(720,l.body.w)" not in setup and "fmin(650,l.body.w)" not in forms


def test_installer_progress_bar_fills_from_the_right():
    """fill.x = bar.x filled left to right on an RTL page (audit finding 8)."""
    final = _src("screens_install_final.c")
    assert "fill.x=bar.x+bar.w-fill.w;" in final


def test_installer_failure_is_said_in_hebrew_with_the_code_below():
    final = _src("screens_install_final.c")
    he = body(final, "failure_he")
    assert '{"packages","התקנת חבילות ImageCtl נכשלה."}' in he
    assert "(unsigned char)e[0]>=0x80" in he                # a Hebrew message stays as it is
    failed = body(final, "failed")
    assert "install_text_r(cr,t,s->error[0]?s->error" not in failed   # the raw code was the headline
    assert "if(raw)install_text_ltr(cr,s->error,11,400," in failed


def test_installer_footer_lists_the_working_keys_on_every_step():
    frame = _src("screens_install.c")
    keys = body(frame, "install_keys")
    for view in ("INSTALL_DISK", "INSTALL_ROLE", "INSTALL_NETWORK", "INSTALL_HOSTNAME",
                 "INSTALL_SUMMARY", "INSTALL_PROGRESS", "INSTALL_FAILED"):
        assert f"case {view}:" in keys, view
    assert "רווח  אני מבין" in keys                  # install.c: Space ticks it on the disk step
    assert "install_keys_draw(a,cr,l);}" in frame     # after every step's own drawing


def test_installer_future_steps_are_outlines_not_faint_discs():
    frame = _src("screens_install.c")
    assert "else{cairo_arc(cr,l.rail.x+l.rail.w-45,y,14,0,2*M_PI);draw_set(cr,dim);cairo_set_line_width(cr,2);cairo_stroke(cr);}" in frame


def _installer_pngs(binary: Path, tmp_path: Path, size: str = "1280x800") -> None:
    import os
    import subprocess
    env = dict(os.environ)
    fonts = REPO / "native-gui" / "fonts.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(tmp_path / "inst"), "--size", size], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr


@GUI_BUILD
def test_installer_progress_fill_is_at_the_right_of_the_bar(tmp_path):
    """Demo: 52%. 1280x800: the column is x 135..895, the bar y 105..113.
    The left 40% of the bar is track; the right 40% is the action blue."""
    binary = _build_gui(tmp_path)
    _installer_pngs(binary, tmp_path)
    w, h, ch, px = _png_rgb(tmp_path / "inst-install-5c-progress-light.png")
    blue = _token("--clr-action")
    def blues(x0, x1):
        return sum(1 for x in range(x0, x1)
                   if all(abs(px[(109 * w + x) * ch + k] - blue[k]) <= 6 for k in range(3)))
    assert blues(140, 135 + 304) == 0, "the fill starts at the left"
    assert blues(135 + 456, 890) > 200, "no fill at the right"


@GUI_BUILD
def test_installer_footer_draws_the_key_row(tmp_path):
    """1280x800, disk step: the key row ends at x 768 (clear of "הבא" at
    880); the old "שלב 1 מתוך 7" was at x 30..170 and left 400..768 blank."""
    binary = _build_gui(tmp_path)
    _installer_pngs(binary, tmp_path)
    w, h, ch, px = _png_rgb(tmp_path / "inst-install-0-disk-light.png")
    colors = {bytes(px[(y * w + x) * ch:(y * w + x) * ch + 3])
              for y in range(h - 60, h - 4) for x in range(400, 768)}
    assert len(colors) > 4, "no key row in the footer"


# --- 5. across every screen ---------------------------------------------------------


def test_a_click_draws_the_button_pressed_for_a_moment():
    """draw_btn knew hover only (widgets.c:36-57): no feedback on the frame
    the click lands (audit finding 2)."""
    btn = code(body(_src("widgets.c"), "draw_btn"))
    assert "int pressed = kind != BTN_OFF && id && a->pressed_id == id;" in btn
    assert "if (pressed) {" in btn and "+ (pressed ? 1 : 0)" in btn
    main = code(_src("main.c"))
    loop = main[main.index("while (!stop_now && !done) {"):]
    assert "if (a.pressed_id && now_s() >= a.pressed_until) { a.pressed_id = 0; scene_dirty = 1; }" in loop
    assert "a.pressed_id = app_hit(&a, e.x, e.y);" in loop
    assert "int pressed_id;" in _src("ui.h") and "double pressed_until;" in _src("ui.h")


def test_shift_tab_walks_the_fields_backwards():
    handle = code(body(_src("main.c"), "handle"))
    assert "(e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) && n" in handle
    assert "ids[k <= 0 ? n - 1 : k - 1]" in handle


def test_one_destructive_pattern_red_and_only_after_the_confirmation():
    """The live round's "עצור סבב" was a filled red button, clickable before
    anything was typed; Esc a second time sent the stop with whatever was in
    the field. Tools' "הרץ" was red and live with "BUILD-0" typed."""
    widgets = code(body(_src("widgets.c"), "draw_btn"))
    assert "if (kind != BTN_OFF) hit_add(a, b, id);" in widgets          # disabled = not clickable
    footer = code(body(_src("screens_room.c"), "live_footer"))
    assert "ready = confirming && typed_matches(a->room_confirm, s->round_image)" in footer
    assert "!confirming ? BTN_GHOST_DANGER : ready ? BTN_DANGER : BTN_OFF" in footer
    room = code(body(_src("screens_room.c"), "screen_room"))
    assert "BTN_DANGER" not in room
    close = code(body(_src("main.c"), "close_round"))
    assert "if (want && !typed_matches(typed, want))" in close
    handle = code(body(_src("main.c"), "handle"))
    assert "a->st.has_round && a->room_confirming) { a->room_confirming = 0;" in handle   # Esc cancels
    assert handle.count('"room-close", a->st.round_image)') == 3                          # click, Esc, Enter
    tools = code(body(_src("screens_tools.c"), "tools_confirm_view"))
    assert "kinds[nb] = !ready ? BTN_OFF : level == 2 ? BTN_DANGER : BTN_PRIMARY;" in tools
    assert "typed_matches(a->tool_confirm, a->tools_machine)" in tools
    match = code(body(_src("screens_rounds.c"), "typed_matches"))
    assert "want[0] &&" in match                                        # "" never matches ""


def test_every_status_bar_lists_only_keys_that_work():
    assert "Esc" not in status_line("SCREEN_MESSAGE")                    # default: a message has no way out
    assert "Enter" not in status_line("SCREEN_TOOLS")                    # handle() ignores Enter there
    keys = code(body(_src("screens.c"), "status_keys"))
    restore = keys[keys.index("case SCREEN_RESTORE:"):].split(";")[0]
    assert "Tab" not in restore                                          # one field: Tab goes nowhere
    room = keys[keys.index("case SCREEN_ROOM:"):keys.index("case SCREEN_CLONER:")]
    setup = room[:room.index("if(a->room_confirming)")]
    assert "N  " not in setup                                            # N starts a wave; setup has none
    assert "Esc  עצור / חזרה" not in keys
    # and the class screen's Esc, which its status bar promised, now works
    handle = code(body(_src("main.c"), "handle"))
    assert 'a->screen == SCREEN_CLASS && !a->st.has_session) { go_menu(a); emit1("back");' in handle


def test_the_screen_scales_with_its_height():
    """Every size was fixed px (audit finding 4): a 1080p screen was a
    1024x768 layout with more empty space and 10px labels."""
    screens = code(_src("screens.c"))
    assert "double ui_scale(int H){double s=floor(H/800.0*4)/4;return s<1?1:s>1.4?1.4:s;}" in screens
    draw = body(screens, "app_draw")
    assert "cairo_scale(cr,s,s);" in draw and "app_draw_logical(a,cr,W/s,H/s);" in draw
    assert "x/=s;y/=s;" in body(screens, "app_hit")
    assert "a->ptr_x / s, a->ptr_y / s" in body(_src("widgets.c"), "hovered")
    assert "double s=ui_scale(h);a->scale=s;cairo_scale(cr,s,s);screen_install(a,cr,w/s,h/s);" in _src("screens_install.c")


def test_no_text_is_drawn_below_11px():
    text = code(_src("text.c"))
    assert "#define TEXT_MIN_PX 11.0" in text
    assert "if (px < TEXT_MIN_PX) px = TEXT_MIN_PX;" in body(text, "font_desc")
    small = re.compile(r"(?:text_make(?:_markup)?\(cr, ?FONT_(?:SANS|MONO), ?|rtl_block_make\(cr, ?|"
                       r"install_text_(?:r|ltr)\(cr,(?:t,)?[^,]*,)(?:[0-9]|10)(?:\.\d+)?\s*,")
    for f in sorted(SRC.glob("*.c")):
        hits = small.findall(code(f.read_text(encoding="utf-8")))
        assert not hits, (f.name, hits)


@GUI_BUILD
def test_the_live_rounds_stop_is_red_words_until_confirmed(tmp_path):
    """Sample data, 1280x800: the live round's footer (y 700..742) has red
    text, not a filled red button."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-room-live-light.png")
    red = _token("--clr-danger")
    n = sum(1 for y in range(700, 742) for x in range(w)
            if all(abs(px[(y * w + x) * ch + k] - red[k]) <= 12 for k in range(3)))
    assert 0 < n < 900, f"{n} red pixels in the footer (a filled button is ~3000)"
