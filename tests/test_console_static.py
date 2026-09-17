"""‏#915/#916: שאריות העיצוב (#829) ב-HTML של הקונסולה שהוצגו כנתונים חיים.

בדיקות תוכן על הקבצים הסטטיים — לא מרנדרות ולא מריצות שרת. כל מחרוזת
כאן היא כזו שנדב ראה על המסך במעבדה (v0.26.1) והייתה קשיחה ב-HTML:
‏`v0.9` בכניסה ובשורת הסטטוס, ‏`הודעות: 2`, ‏`task-count 3`, סבב דמו
‏`LAB1 74%`, שלוש שורות משימות דמו, וכרטיס "כללי naming" עם `LAB1-05`.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"


def _index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


def _console_js() -> str:
    return (STATIC / "console.js").read_text(encoding="utf-8")


def test_index_html_has_no_hardcoded_version():
    """הגרסה מגיעה מ-`/api/console/me` (תג git). ‏`v0.9` בכניסה ובשורת
    הסטטוס היו מהעיצוב; המחזיק היחיד הוא `#serverVersion` — ריק ב-HTML."""
    page = _index()
    assert "v0.9" not in page
    assert re.search(r"\bv\d+\.\d+(\.\d+)?\b", page) is None, "גרסה קשיחה ב-HTML"
    assert '<span id="serverVersion"></span>' in page


def test_index_html_task_dock_has_no_demo_numbers():
    """המעגן והפאנל מתמלאים מ-`renderActivity` (‏`/overview`). ב-HTML: מונה 0,
    בלי סבב דמו, בלי 'הודעות: N', ובלי שורות דמו עם onclick לפונקציות."""
    page = _index()
    for demo in ("הודעות: 2", "LAB1 74%", "פעילות אחרונה",
                 "openLogDetail('", "openImageDetail('Ubuntu", "openMachineDetail('LAB1"):
        assert demo not in page, demo
    assert '<span class="task-count">0</span>' in page
    assert re.search(r'class="task-count">[1-9]', page) is None
    assert '<tbody><tr><td colspan="6">אין העברות פעילות</td></tr></tbody>' in page


def test_the_login_screen_shows_no_version_tag():
    """הגרסה מוצגת רק אחרי כניסה (‏`/me` דורש אימות); מסך הכניסה בלי תג."""
    page = _index()
    login = page[page.index('id="login"'):page.index('id="app"')]
    assert 'class="tag' not in login


def test_console_js_fills_the_version_from_me():
    js = _console_js()
    assert 'getElementById("serverVersion")' in js
    assert "ME.version" in js


def test_the_notifications_drawer_lists_what_the_badge_counts():
    """התג בסרגל סופר (‏`updateAlertBadge`) מכונות שנכשלו ובדיקות בריאות
    ‏warn/bad; המגירה חייבת להציג את אותם פריטים — לא "טוען נתונים…" לנצח."""
    js = _console_js()
    fn = js[js.index("function topNotifications"):]
    fn = fn[:fn.index("\nfunction ", 1)]
    assert "טוען נתונים" not in fn
    assert "machineIsFailed" in fn
    assert 'c.state !== "warn" && c.state !== "bad"' in fn
    assert "אין התראות" in fn


def test_the_machines_page_has_no_demo_text_and_no_dead_tabs():
    """‏#916: בלי כרטיס "כללי naming" (‏`LAB1-05`), ושתי לשוניות חיות בלבד —
    'ייבוא/ייצוא' הייתה עותק שלישי של אותו `machineAdminPage`."""
    js = _console_js()
    assert "LAB1-05" not in js
    assert "כללי naming" not in js
    assert 'tabs: ["כל התחנות", "ניהול לפי סוג"], render: machines' in js
    assert "machines: [machines, machineAdminPage]," in js


def test_the_machines_filter_is_visible_and_clearable():
    """‏#916: הסינון מלחיצה על קבוצה בעץ נשאר בין מעברים; בלי חיווי הוא
    נראה כמו "אין מחשבים רשומים" מול עץ מאויש. הצומת "מחשבים" מאפס אותו,
    והדף מציג את הקבוצה המסוננת עם כפתור ביטול וריק שמבחין בין המצבים."""
    js, page = _console_js(), _index()
    assert "MACHINES_FILTER=null;selectPageById('machines')" in page
    fn = js[js.index("function machines()"):js.index("function clearMachinesFilter")]
    assert "clearMachinesFilter()" in fn
    assert "אין מחשבים בקבוצה" in fn
    assert "אין מחשבים רשומים" in fn        # המצב האמיתי של "אין בכלל" נשאר
    assert "function clearMachinesFilter() {\n  MACHINES_FILTER = null;" in js


def test_the_machines_page_lists_shrunk_source_disks_in_orange_with_clear():
    """‏#926: דיסק מקור שכווץ לקליטה ולא הוחזר לגודלו (הרשומה בשרת) מוצג
    בדף המחשבים ככרטיס משלו — כתום (`disk-smart` בלי `failed_last`: ווינדוס
    עולה ממנו), עם המחיצה והגודל המקורי במילים וכפתור "נקה" — לצד הדיסקים
    האדומים של #874, ומאותו `loadMachines`."""
    js = _console_js()
    assert 'api("/shrink-records")' in js
    fn = js[js.index("function shrinkRecordsCard"):]
    fn = fn[:fn.index("\nasync function clearShrinkRecord")]
    assert '<span class="disk-smart">' in fn and "failed_last" not in fn
    assert "כווצה לקליטה ולא הוחזרה לגודלה המקורי" in fn
    assert "clearShrinkRecord(" in fn and ">נקה<" in fn
    assert "/shrink-records/${id}/clear" in js
    machines = js[js.index("function machines()"):js.index("function clearMachinesFilter")]
    assert "${shrinkRecordsCard()}" in machines and "${diskFailuresCard()}" in machines


# --- #954 גל 1: הסקירה הכללית נבנתה מחדש, הקוד הישן נמחק ---------------------


def test_static_includes_are_at_least_6_9():
    """‏#954 גל 1 = ‏`?v=6.9`. הבדיקה מונוטונית (≥) ולא שוויון — bump מוצדק
    הבא לא אמור להפיל אותה; שוויון בין כל ה-includes נבדק ב-console_rewrite."""
    versions = {float(v) for v in re.findall(r'\?v=([\d.]+)"', _index())}
    assert versions, "אין ?v= ב-index.html"
    assert min(versions) >= 6.9, versions


def test_old_home_page_code_is_gone():
    """הדף הישן נמחק, לא הוסתר: בלי `object-strip`, בלי "Details", בלי כרטיס
    "אין נתונים להצגה" בתחתית, בלי "+ פעולה" לסקירה, ובלי הלשונית הישנה
    "משימות אחרונות". הסקירה מציירת כותרת ולשוניות בעצמה (`own: true`)."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy() {")]
    for gone in ("object-strip", ">Details<", "אין נתונים להצגה", "emptyDataCard", "footer-note"):
        assert gone not in fn, gone
    assert 'render: home, load: loadHome, own: true' in js
    assert "משימות אחרונות" not in js[js.index("const pages = {"):js.index("let current = ")]
    actions = js[js.index("function openAction()"):js.index("function openDrawer")]
    assert "home:" not in actions, "לסקירה אין תפריט '+ פעולה' גנרי (README §מה שנשבר)"
    assert "if (page.own) return page.render(index);" in js


def test_shared_ui_renderers_exist_for_the_next_pages():
    """שפת העיצוב המשותפת (README §"שפת העיצוב") יושבת ב-`UI` בראש
    console.js — כותרת אובייקט, KPI, כרטיס, datagrid, מצב, מפתח-ערך,
    ציר-זמן, הודעה, ריק-מצב, ו"בקרוב" למה שדורש API."""
    js = _console_js()
    block = js[js.index("const UI = {"):js.index("function fmtDate(")]
    for name in ("objHeader(", "kpi(", "card(", "datagrid(", "status(", "pill(", "barRow(",
                 "kv(", "note(", "empty(", "timeline(", "soon("):
        assert name in block, name
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .obj{", ".page .kpi{", ".page table.dg{", ".page .st{", ".page .kv{",
                ".page .ev{", ".page .note{", ".page .empty{"):
        assert sel in css, sel


def test_uptime_is_a_placeholder_not_a_number():
    """‏home.md "דורש API": זמן הפעילות מוצג כ"בקרוב", לא כמספר מומצא."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy() {")]
    assert 'UI.soon("זמן פעילות")' in fn
    assert "uptime" not in fn.lower()


def test_images_tree_node_uses_the_folder_icon():
    """הכרעת נדב (17/09 06:50): אייקון "אימג'ים" בעץ → תיקייה, גם לתיקיות
    מתחתיו. האימג' עצמו נשאר עם אייקון תמונה."""
    page, js = _index(), _console_js()
    start = page.index('data-page="images"')
    node = page[start:page.index("אימג'ים</span>", start)]
    assert 'M3 7a2 2 0 0 1 2-2h4l2 2h8' in node
    assert 'circle cx="16"' not in node
    fn = js[js.index("function populateSidebarImages"):js.index("function fillSidebarTree")]
    assert fn.count('uiIcon("folder")') == 2
    assert fn.count('uiIcon("image")') == 1
