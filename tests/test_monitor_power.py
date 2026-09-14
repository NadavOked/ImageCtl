"""כפתורי הפעלה-מחדש/כיבוי במסך המוניטור (#781).

הכפתור Ctrl+Alt+Del הוסר, ובמקומו שני כפתורים תפעוליים: **הפעלה מחדש**
ו-**כיבוי** של המחשב המנוטר. הפקודה נוסעת על אותו ערוץ RFB שעליו נסע
Ctrl+Alt+Del — הדפדפן שולח ClientCutText עם אסימון כוח, ו-monitor.c
(שרץ כ-root) ממפה אותו ל-`reboot -f`/`poweroff -f`.

מה שנבדק כאן: (א) שני הכפתורים קיימים ו-Ctrl+Alt+Del איננו, (ב) הם
מחווטים לאסימון הנכון, (ג) האישור עובר במודאל של הדף (לא prompt/confirm
החסומים), (ד) monitor.c רושם את hook ה-cut-text וממפה כל אסימון לפקודה
הנכונה.

הבנייה וההרצה בפועל של monitor.c (gcc, ריבוט על ברזל) הן אימות מעבדה —
כאן נבדק שהחיווט קיים, כמו ש-test_agent בודק שכל C מקומפל בבנאי.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"
MONITOR_HTML = STATIC / "monitor.html"
MONITOR_JS = STATIC / "monitor.js"
MONITOR_C = REPO / "agent" / "monitor.c"

#: האסימון שהדפדפן שולח ו-monitor.c מזהה. מקור יחיד לצורה — שני הצדדים
#: חייבים להסכים עליו בדיוק, ולכן הטסט נועל אותו.
POWER_PREFIX = "imagectl-power:"
REBOOT_TOKEN = POWER_PREFIX + "reboot"
POWEROFF_TOKEN = POWER_PREFIX + "poweroff"


def _html() -> str:
    return MONITOR_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return MONITOR_JS.read_text(encoding="utf-8")


def _c() -> str:
    return MONITOR_C.read_text(encoding="utf-8")


# --- (א) הכפתורים: שניים חדשים, וה-CAD איננו --------------------------------


def test_ctrl_alt_del_button_is_gone_from_html():
    """הכפתור המיותר הוסר — לא הטקסט ולא ה-id שלו נשארו."""
    html = _html()
    assert "Ctrl+Alt+Del" not in html
    assert 'id="cad"' not in html


def test_ctrl_alt_del_wiring_is_gone_from_js():
    """גם החיווט ב-JS הוסר: אין פונקציית ctrlAltDel ואין מאזין ל-cad."""
    js = _js()
    assert "ctrlAltDel" not in js
    assert '"cad"' not in js


def test_html_has_reboot_and_poweroff_buttons():
    html = _html()
    assert 'id="reboot-btn"' in html
    assert 'id="poweroff-btn"' in html
    assert "הפעלה מחדש" in html
    assert "כיבוי" in html


# --- (ב) החיווט: כל כפתור לאסימון הנכון --------------------------------------


def test_js_sends_the_reboot_and_poweroff_tokens():
    """‏JS בונה את האסימון מהתחילית + הפעולה; זה מה ש-monitor.c יזהה.
    נועלים את התחילית המדויקת ואת שתי הפעולות — שני הצדדים חייבים
    להסכים על שתיהן, אחרת monitor.c לא ימפה את מה שהדפדפן שולח."""
    js = _js()
    assert POWER_PREFIX in js
    assert '"reboot"' in js
    assert '"poweroff"' in js


def test_js_wires_each_button_to_a_power_action():
    """הכפתורים מחוברים למאזינים, וקיימת פונקציית שליחה משותפת."""
    js = _js()
    assert "sendPower" in js
    assert "reboot-btn" in js
    assert "poweroff-btn" in js


def test_js_sends_power_over_rfb_client_cut_text():
    """הפקודה נוסעת כ-ClientCutText (RFB msg type 6), על ערוץ ה-WS —
    אותו send() שכל שאר הפרוטוקול משתמש בו. לא fetch לנתיב חדש."""
    js = _js()
    assert "clientCutText" in js
    # ‏6 = ClientCutText. חייב להיבנות ולעבור ב-send() הקיים.
    assert "send(" in js


# --- (ג) אישור: מודאל של הדף, לא prompt/confirm החסומים ----------------------


def test_power_is_behind_a_confirm_modal_not_prompt_or_confirm():
    """‏prompt()/confirm() חסומים בדפדפן מוטמע (CLAUDE.md). האישור חייב
    לעבור במודאל של הדף, ופעולה הרסנית על מכונה חיה נעולה בהקלדת השם
    (עיקרון 7)."""
    js = _js()
    assert "confirm(" not in js
    assert "prompt(" not in js
    assert "powerModal" in js
    # מודאל האישור קיים ב-DOM של הדף.
    assert 'id="pmodal"' in _html()


# --- (ד) הסוכן: monitor.c ממפה אסימון → פקודה --------------------------------


def test_monitor_c_registers_cut_text_hook():
    """‏LibVNCServer קורא ל-setXCutText כשמגיע ClientCutText — זה השער
    שדרכו האסימון נכנס ל-monitor.c."""
    c = _c()
    assert "setXCutText" in c


def test_monitor_c_maps_tokens_to_reboot_and_poweroff():
    """כל אסימון ממופה לפקודה שלו. `-f` כמו בכל שאר הסוכן (common.sh)."""
    c = _c()
    assert REBOOT_TOKEN in c
    assert POWEROFF_TOKEN in c
    assert "reboot" in c
    assert "poweroff" in c
