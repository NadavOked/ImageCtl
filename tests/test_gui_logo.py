"""‏#1168: לוגו המכללה שהועלה בקונסולה מגיע לגואי הקטן — במקום ה-brandmark הקבוע.

הגואי אינו מתקמפל בווינדוס; הבדיקה כאן היא על המקור (הסוכן, מפתח המצב,
הציור) ועל חוזה ה-state. הקומפילציה ו-`--png` — על ה-Testrunner."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui" / "src"
AGENT = REPO / "agent" / "lib"


def test_the_agent_fetches_the_logo_once_and_only_a_png_reaches_the_state():
    guistate = (AGENT / "guistate.sh").read_text(encoding="utf-8")
    guiparent = (AGENT / "guiparent.sh").read_text(encoding="utf-8")
    assert "gui_fetch_logo()" in guistate
    assert 'http_get "$SERVER/api/v1/agent/branding/logo"' in guistate
    assert '"89504e47"' in guistate, "ארבעת הבייטים של PNG — לא סיומת ולא Content-Type"
    assert 'log "branding: logo is not PNG -- brandmark' in guistate
    assert 'log "branding: no logo on the server -- brandmark"' in guistate
    # the state line exists only when the file does -- the GUI falls back by itself
    assert r"""[ -s "$GUI_DIR/logo.png" ] && { printf 'logo=%s\n' "$GUI_DIR/logo.png" >> "$GUI_DIR/state.next" || return 1; }""" in guistate
    assert guiparent.index("gui_fetch_logo") < guiparent.index("/usr/bin/imagectl-kiosk >>"), "נמשך לפני שהקיוסק עולה"


def test_the_gui_reads_the_key_and_falls_back_on_a_bad_file():
    ui_h = (GUI / "ui.h").read_text(encoding="utf-8")
    state_c = (GUI / "state.c").read_text(encoding="utf-8")
    screens = (GUI / "screens.c").read_text(encoding="utf-8")
    assert "char logo[256];" in ui_h
    assert 'else if (KEY("logo")) cp(s->logo, sizeof s->logo, val);' in state_c
    assert "cairo_image_surface_create_from_png(path)" in screens
    assert "cairo_surface_status(surf)!=CAIRO_STATUS_SUCCESS" in screens, "קובץ שלא נפענח = brandmark, לפי הסטטוס ולא לפי קיום הקובץ"
    assert "draw_brand_mark(cr,t,mark)" in screens, "ה-brandmark נשאר כברירת המחדל"
