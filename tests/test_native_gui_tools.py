"""‏#649 תחום 1 — מסך "כלים" ב-GUI הנייטיבי, נבדק על המקור (אין מהדר בווינדוס).

מה שנאכף כאן בלי לקמפל: הכפתור "כלים" חי **רק** בתפריט מחשב הבנייה
(‏`screen_menu` ב-`screens.c`) ולא במסכי הקלונר/הכיתה; שלושת המסכים
(רשימה / אישור / פלט) קיימים עם המחרוזות שהמפעיל רואה; ‏rc 2 מוצג
כ"לא הצלחנו לבדוק" ו-rc 3 כ"האישור לא תואם"; המצב הריק אומר "אין כלים
ארוזים"; והמסך צבוע **רק** בטוקנים הקיימים — אין `HEX(` בקובץ (כדי
ש-`test_native_gui_theme` יישאר מקור האמת היחיד לצבעים). ‏`--png`
מקבל שלושה כרטיסים חדשים, ו-`want[]` גדל איתם (‏#715 לימד).

הקומפילציה ו-`make png` — אצל המתאם במעבדה.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "native-gui" / "src"
TOOLS_C = (SRC / "screens_tools.c").read_text(encoding="utf-8")
SCREENS_C = (SRC / "screens.c").read_text(encoding="utf-8")
MAIN_C = (SRC / "main.c").read_text(encoding="utf-8")
UI_H = (SRC / "ui.h").read_text(encoding="utf-8")
MAKEFILE = (SRC.parent / "Makefile").read_text(encoding="utf-8")
BRIDGE = (SRC.parents[1] / "agent" / "lib" / "guibridge.sh").read_text(encoding="utf-8")


def function_body(text: str, name: str) -> str:
    m = re.search(r"\n(?:static )?\w[\w \*]*\b" + re.escape(name) + r"\([^)]*\)\s*\{", text)
    assert m, f"{name} לא נמצאה"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


# --- הכפתור: בתפריט הבנייה בלבד -------------------------------------------------


def test_the_tools_button_lives_in_the_build_menu_only():
    assert "HIT_TOOLS" in function_body(SCREENS_C, "screen_menu")
    for other in ("screens_cloner.c", "screens_class.c", "screens_rounds.c", "screens_room.c",
                  "screens_capture.c", "screens_restore.c"):
        assert "HIT_TOOLS" not in (SRC / other).read_text(encoding="utf-8"), other
    # ואינו אחת מבחירות התפריט: לא בטבלת MENU, כפתור משני בראש הכרטיס.
    menu_table = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};", SCREENS_C, flags=re.S).group(1)
    assert "HIT_TOOLS" not in menu_table
    assert "BTN_PLAIN, HIT_TOOLS" in SCREENS_C


def test_the_button_is_registered_before_the_body_clip():
    body = function_body(SCREENS_C, "screen_menu")
    assert body.index("HIT_TOOLS") < body.index("body_clip_begin(a, cr, body)")


def test_the_toolbox_routes_to_its_own_screen():
    assert "case MODE_TOOLS:   a->screen = SCREEN_TOOLS" in SCREENS_C
    assert "case SCREEN_TOOLS:    screen_tools(a, cr, W, H, head_h)" in SCREENS_C
    assert "SCREEN_TOOLS" in UI_H and "MODE_TOOLS" in UI_H
    assert "src/screens_tools.c" in MAKEFILE


# --- שלושת המסכים והמחרוזות שהמפעיל רואה ------------------------------------------


def test_the_list_groups_by_domain_with_hebrew_headings():
    for domain, heading in (("disk", "דיסקים"), ("net", "רשת"), ("windows", "Windows"),
                            ("boot", "אתחול"), ("hw", "חומרה")):
        assert f'"{domain}"' in TOOLS_C and f'"{heading}"' in TOOLS_C, (domain, heading)


def test_the_risk_tags_carry_the_three_words():
    for word in ("קריאה בלבד", "משנה דיסק", "הרסני"):
        assert word in TOOLS_C, word
    # ומילה לא מוכרת נקראת כהרסנית, כמו ב-tools.sh -- לא כקריאה בלבד.
    body = function_body(TOOLS_C, "tools_risk_level")
    assert "return 2;" in body.split("rw")[-1]


def test_rc_2_and_rc_3_have_their_own_words_and_2_is_not_green():
    body = function_body(TOOLS_C, "rc_word")
    line2 = next(l for l in body.splitlines() if "case 2:" in l)
    assert "לא הצלחנו לבדוק" in line2 and "warning" in line2 and "success" not in line2
    line3 = next(l for l in body.splitlines() if "case 3:" in l)
    assert "האישור לא תואם" in line3 and "danger" in line3


def test_the_empty_state_and_the_loading_state_are_two_texts():
    assert "לא נבחרו כלים בשרת" in TOOLS_C   # #1050: the cause is the selection, not the initrd
    assert "טוען את רשימת הכלים" in TOOLS_C


def test_the_confirm_view_asks_for_the_machine_name_on_rw_and_destroy():
    body = function_body(TOOLS_C, "tools_confirm_view")
    assert "הקלידו את שם המחשב" in body and "HIT_TOOL_CONFIRM" in body
    assert "HIT_TOOL_DISK_BASE" in body and "HIT_TOOL_ARG" in body


def test_the_output_view_spins_while_running_and_offers_run_again():
    body = function_body(TOOLS_C, "tools_output_view")
    assert "spinner_draw" in body and "רץ…" in body
    assert "הרץ שוב" in body and "HIT_TOOL_AGAIN" in body
    assert "למטה" in body and "למעלה" in body


def test_run_again_never_replays_a_typed_name():
    """‏rw/destroy חוזר למסך האישור עם שדה שם ריק; רק ro בלי ארגומנט רץ שוב."""
    body = function_body(MAIN_C, "handle")
    again = body[body.index("case HIT_TOOL_AGAIN:"):body.index("case HIT_TOOL_DOWN:")]
    assert "a->tool_confirm[0] = 0" in again and "TOOLS_CONFIRM" in again


def test_the_local_confirm_check_mirrors_the_bridge():
    body = function_body(MAIN_C, "tool_run")
    assert "strcmp(confirm, a->tools_machine)" in body
    assert 'tool-run|%s|%s|%s' in body


# --- טוקנים בלבד, וטבלאות --png ---------------------------------------------------


def test_the_tools_screen_defines_no_colour_of_its_own():
    assert "HEX(" not in TOOLS_C
    assert not re.search(r"cairo_set_source_rgb\s*\(", TOOLS_C)
    assert not re.search(r"\(Rgb\)\s*\{", TOOLS_C)


def test_png_cards_include_the_three_tools_views():
    cards = re.search(r"PNG_CARDS\[\]\s*=\s*\{(.*?)\};", MAIN_C, flags=re.S).group(1)
    for card in ('"tools"', '"tools-confirm"', '"tools-output"'):
        assert card in cards, card
    want = re.search(r"static const Screen want\[\]\s*=\s*\{(.*?)\};", MAIN_C, flags=re.S).group(1)
    assert want.count("SCREEN_TOOLS") == 3


# --- הגשר: שתי שורות, אחרי gui_role -------------------------------------------------


def test_the_bridge_dispatches_both_tokens_behind_the_session_gate():
    start = BRIDGE.index("gui_dispatch() {")
    body = BRIDGE[start:BRIDGE.index("\n}\n", start)]
    gate = body.index("gui_role ||")
    assert body.index("tool-list) tools_gui_list") > gate
    assert body.index('"tool-run|"*) tools_gui_run "$token"') > gate
    assert '. "$LIB_DIR/tools.sh"' in BRIDGE
