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


def test_image_fit_is_rendered_from_one_helper_and_the_room_applies_the_floor():
    """‏#953: "נכנס לדיסק מ-X GB" מגיע מ-`imageSizeCells` (פונקציית רינדור
    אחת, שתעבור כמו שהיא למסך של #954) גם בטבלה וגם במגירת האימג';
    מסך החדר משווה כל אימג' ל-`disk_floor` מהשרת ב**כל** רענון; וה-`?v=`
    הוקפץ בשני הדפים — JS ישן מול API חדש הוא gotcha ידוע."""
    js = _console_js()
    table = js[js.index("function images()"):js.index("function filterImagesFolder")]
    assert "imageSizeCells(r)" in table
    assert "דיסק יעד:" not in table            # הנוסח הישן, בלי הרצפה
    detail = js[js.index("function openImageDetail"):js.index("function renameImage")]
    assert "נכנס לדיסק מ-" in detail and "librarySize(img.used_bytes)" in detail

    room = (STATIC / "station" / "room.js").read_text(encoding="utf-8")
    setup = room[room.index("async function renderSetup"):room.index("async function openRound")]
    assert "applyImageFit(data.disk_floor || null)" in setup
    assert "גודל הדיסקים לא ידוע" in room

    for page in (_index(), (STATIC / "station" / "index.html").read_text(encoding="utf-8")):
        versions = {tuple(int(x) for x in v.split("."))
                    for v in re.findall(r"\?v=([0-9.]+)", page)}
        assert len(versions) == 1, versions
    assert "?v=6.8" in _index()
    assert "?v=3.31" in (STATIC / "station" / "index.html").read_text(encoding="utf-8")
