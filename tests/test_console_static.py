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
    """‏#916 → ‏#954 גל 3: בלי כרטיס "כללי naming" (‏`LAB1-05`), ובלי הלשוניות
    הישנות "כל התחנות / ניהול לפי סוג" — הדף הוא `machines(tab)` שמצייר את
    הכותרת והלשוניות בעצמו (`own: true`), ו-`machines.js` נמחק."""
    js = _console_js()
    assert "LAB1-05" not in js
    assert "כללי naming" not in js
    assert '"כל התחנות"' not in js and "ניהול לפי סוג" not in js
    assert "machineAdminPage" not in js and "loadMachinesTab" not in js
    assert 'tabs: ["כל המחשבים", "נראו ברשת", "דיסקים אדומים"], render: machines, load: loadMachines, own: true' in js
    assert "machines.js" not in _index()
    assert not (STATIC / "machines.js").exists()


def test_the_machines_filter_is_visible_and_clearable():
    """‏#916: הסינון מלחיצה על קבוצה בעץ נשאר בין מעברים; בלי חיווי הוא
    נראה כמו "אין מחשבים רשומים" מול עץ מאויש. הצומת "מחשבים" מאפס אותו
    (וגם את הכיתה הפתוחה כאובייקט, גל 3), והדף מציג את הקבוצה המסוננת עם
    ביטול וריק שמבחין בין המצבים."""
    js, page = _console_js(), _index()
    assert "MACHINES_FILTER=null;MACHINES_CLASS=null;selectPageById('machines')" in page
    fn = js[js.index("function machinesTableCard("):js.index("function machinesTabs()")]
    assert "clearMachinesFilter()" in fn
    assert "מוצגת קבוצה" in fn
    table = js[js.index("function machinesTableHtml()"):js.index("function mchSelBar(")]
    assert "אין מחשבים בקבוצה" in table
    assert "אין מחשבים רשומים" in table        # המצב האמיתי של "אין בכלל" נשאר
    assert "function clearMachinesFilter() {\n  MACHINES_FILTER = null;" in js


def test_the_machines_page_lists_shrunk_source_disks_in_orange_with_clear():
    """‏#926: דיסק מקור שכווץ לקליטה ולא הוחזר לגודלו (הרשומה בשרת) מוצג
    בלשונית "דיסקים אדומים" ככרטיס משלו — כתום (`disk-smart` בלי
    `failed_last`: ווינדוס עולה ממנו), עם המחיצה והגודל המקורי במילים וכפתור
    "נקה" — לצד הדיסקים האדומים של #874, ומאותו `loadMachines`."""
    js = _console_js()
    assert 'api("/shrink-records")' in js
    fn = js[js.index("function shrinkRecordsCard"):]
    fn = fn[:fn.index("\nasync function clearShrinkRecord")]
    assert '<span class="disk-smart">' in fn and "failed_last" not in fn
    assert "כווצה לקליטה ולא הוחזרה לגודלה המקורי" in fn
    assert '["נקה", `clearShrinkRecord(' in fn
    assert "/shrink-records/${id}/clear" in js
    machines = js[js.index("function machines(tab = 0)"):js.index("function clearMachinesFilter")]
    assert "shrinkRecordsCard()" in machines and "diskFailuresCard()" in machines


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


# ---------- #954 גל 2: ספריית האימג'ים ----------

def test_static_includes_are_at_7_1():
    """גל 2 = ‏7.0; גל 3 (מחשבים) מחליף שוב JS+CSS+HTML — `?v=` עולה ל-7.1
    (מטמון הדפדפן). שוויון על כל ה-includes — bump חלקי הוא הבאג."""
    page = _index()
    versions = {float(v) for v in re.findall(r'\?v=(\d+\.\d+)"', page)}
    assert versions == {7.1}, versions


def test_old_images_page_code_is_gone():
    """הדף הישן נמחק, לא הוסתר: כרטיסי "כללי בטיחות"/"פעולות מהירות" עם
    אפסים קבועים, עמודות גרסה/SHA-256/סטטוס שאין להן שדה ב-`/images`,
    כפתורי Up/Down/Edit באנגלית, ו-`library.js` המת שהיה מוער ב-index.html."""
    js = _console_js()
    for gone in ("כללי בטיחות", "פעולות מהירות", "מטא־נתונים", "checksum שגוי",
                 "function filterImagesFolder", "function reorderImage(", "function reorderFolder(",
                 'aria-label="Move folder up"', "<th>SHA-256</th>", "<th>גרסה</th>"):
        assert gone not in js, gone
    assert "library.js" not in _index()
    assert not (STATIC / "library.js").exists()


def test_images_page_is_the_datastore_browser_from_the_api_only():
    """עץ תיקיות + טבלה אחת + מגירה, מה-API הקיים: "נכנס לדיסק מ-" מ-`min_target_bytes`
    (‏#953, ‏null = לא ידוע), "בשימוש" מ-`/overview`, קליטה כשורה בטבלה, ומה שאין
    לו API ("טבלת המחיצות") מוצג כ"דורש API" — לא מומצא."""
    js = _console_js()
    for needed in ("function images(tab = 0)", "own: true", "function imagesTree()", "role=\"tree\"",
                   "min_target_bytes", "לא ידוע", "function captureRowHtml(", "function imageDrawerHtml(",
                   "דורש API", "function bulkDelete()", "confirm_name", "function imgDrop(", "/folders/order"):
        assert needed in js, needed
    assert "images: [images, emptyDataCard]" not in js


# ---------- #954 גל 3: מחשבים ----------

def test_machines_page_is_one_grouped_table_from_the_api_only():
    """טבלה אחת מקובצת (בנייה, שיכפול, ואז קבוצה לכל כיתה — תיקון נדב 17/09),
    מגירה, הכיתה כאובייקט; מה-API הקיים בלבד. "דיסק N · SATA N-1" ולעולם לא
    sd*; מה שאין לו API ("אתחול מרחוק", "עריכת MAC", היסטוריית סבבים לכיתה)
    מוצג כ"דורש API" — לא כפתור מנוטרל ולא נתון מומצא."""
    js = _console_js()
    for needed in ("function machines(tab = 0)", "function groupRowHtml(", "function machineRowHtml(", "function groupPage(",
                   "function machineDrawerHtml(", "function seenDevicesCard()", "function openAddMachine(", "dry_run: true",
                   'UI.soon("אתחול מרחוק")', 'UI.soon("עריכת MAC")', "דורש API", "SATA ${n - 1}", 'verify: { label: "הקלד את שם המחשב"'):
        assert needed in js, needed
    block = js[js.index("/* ---------- #954 גל 3"):js.index("function healthStatusClass(")]
    assert "disabled" not in block, "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "sda" not in block and "sdb" not in block, "שם sd* בדף המחשבים"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .dg tr.group td .grp{", ".page .disks{", ".page .disk.err{", ".page .dg-bar select{"):
        assert sel in css, sel
