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
    """אין תג גרסה קשיח. הגרסה בתחתית מתמלאת מ-`/me` כשיש session; לפני כן
    רק hostname מהדפדפן (אין endpoint ציבורי לגרסה/fingerprint)."""
    page = _index()
    login = page[page.index('id="login"'):page.index('id="app"')]
    assert 'class="tag' not in login
    assert "גישה מאובטחת" not in login
    assert "זכור אותי" not in login
    assert "קונסולת ניהול ImageCtl" not in login
    assert 'id="login-body"' in login
    assert 'class="art"' in login


def test_login_screens_copy_is_in_console_js():
    """#1085 שלב ב': חמשת המסכים וההודעות לפי המוקאפ — ב-loginHtml, לא ב-HTML קשיח."""
    js = _console_js()
    for s in (
        'זכור את הדפדפן הזה ל-7 שעות',
        "בחר סיסמה חדשה",
        "הקוד מאפליקציית האימות",
        "השתמש בקוד גיבוי",
        "הגדרת אימות דו-שלבי",
        "המשך לקונסולה",
        "שמרתי את הקודים במקום בטוח",
        "שם משתמש או סיסמה שגויים",
        "החשבון נעול ל-",
        "קוד שגוי",
        "מקומי · ללא MFA",
    ):
        assert s in js, s
    assert "function loginHtml(" in js
    assert "function passwordCanSubmit(" in js
    assert "function setupContinueEnabled(" in js
    assert "function otpFromPaste(" in js


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
    fn = js[js.index("function home("):js.index("function deploy(")]
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


def test_uptime_and_deploy_ip_come_from_me_and_null_is_named():
    """‏#968 סגר את "דורש API" של home.md: זמן הפעילות וכתובת ההפצה מגיעים
    מ-`/me` (uptime_seconds, deploy_ip); ‏null מוצג בשם — לא "בקרוב", לא 0,
    ולא 127.0.0.1. ההיוריסטיקה `journalSeverity` נמחקה, לא נשארה לצד השדה."""
    js = _console_js()
    fn = js[js.index("function home("):js.index("function deploy(")]
    assert 'UI.soon("זמן פעילות")' not in fn
    assert "homeUptime()" in fn and "homeDeployIp()" in fn
    assert "ME.uptime_seconds" in js and "ME.deploy_ip" in js
    assert "זמן פעילות לא נבדק" in js and "רשת הפצה לא הוגדרה" in js
    assert "function journalSeverity" not in js
    assert "function journalCls" in js and "row.severity" in js


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

def test_static_includes_are_at_8_5():
    """גל 2 = ‏7.0; גל 3 (מחשבים) = ‏7.1; גל 3א = ‏7.3; השער (מוניטור רק בדף
    המוניטור) = ‏7.4; גל 5 (בריאות + פורטים) = ‏7.5; גל 6 (מוניטור, דרייברים,
    הגדרות, הרשאות, יומן + WoL למחשב) = ‏7.6; #703 (TLS) = ‏7.7; דף הפורטים
    לחוזה #996/#1015 וגל 7 (סניפים ושרת משני) נכנסו יחד בשער = ‏7.9; #1032
    (סבב ליטוש — hover, שורת סינון, כפתורים כפולים) = ‏8.1 (גל 8, הרשת,
    לקח את 8.0 במקביל); בשער be שניהם יחד = ‏8.2; #649 שלב 1 (ארגז כלים) = ‏8.3;
    #1049 שלב ב' (בריאות המכונה בכרטיס) = ‏8.4; #1048 (כבל ו-LLDP) ותרשים
    הרשת — טקסט התיבות ב-foreignObject במקום <text> שגלש על הקווים = ‏8.6;
    מודל האתרים (וילן השרתים במקום "ניהול", משני דרך FW · 8443 בקו מקווקו) = ‏8.7;
    #1073 (הפצה בלי וובי — `message_he` בכניסה, מטריצת ההרשאות) = ‏8.8;
    #1066 שלב ב' (דף "אחסון", storage.js) = ‏8.9;
    #1093 (ערכת נושא לפי משתמש, נשמרת בשרת) = ‏8.9;
    #1081 (v1 בלי כיתות — `capabilities.classrooms`) = ‏8.9;
    #1077 (מוניטור כבוי בברירת מחדל — אזהרת 5900) = ‏8.9;
    #1085 שלב ב' (מסכי כניסה לפי המוקאפ) = ‏9.0;
    #1033 (גריד הקלונרים — מלבן אנכי, דיסקים מוערמים לפי SATA, שתי שורות
    בתיבת דיסק) = ‏9.0;
    #1071 (pull אימג' מהמשני לראשי) = ‏9.0;
    #1080 (fingerprint SSH בכרטיס המכונה) — הגייט bk איחד את #1085ב/#1033/#1071/#1080 ל-‏9.1;
    #1088 (רשת ההפצה מהקונסולה — הערה בדף הרשת, ההדלקה הראשונה) = ‏9.2;
    #1013 (מתג TFTP 69 בדף הפורטים — אזהרה לפני הקלדת השם) = ‏9.3.
    שוויון על כל ה-includes — bump חלקי הוא הבאג."""

    page = _index()
    versions = {float(v) for v in re.findall(r'\?v=(\d+\.\d+)"', page)}
    assert versions == {9.3}, versions


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


# ---------- #954 גל 3א: מחשבי שיכפול ומחשבי בנייה כאובייקטים ----------

def test_cloners_and_builders_objects_are_built_on_group_page_from_the_api_only():
    """הקבוצות הקבועות כאובייקטים על `groupPage` של גל 3 (לא מערכת חדשה):
    גריד חריצים "דיסק N · SATA N-1" (לעולם לא sd*), צבע SMART ירוק רק על
    `ok`, אדום מזיכרון הכשלים עם "נקה", מגירות מ-`/room` בסבב ומ-`/machines`
    בלעדיו; מחשבי בנייה — קליטה בתהליך מ-`/tasks` עם ביטול מאחורי הקלדת שם.
    מה שאין לו API — כיבוי כולם, WoL למחשב יחיד, שלבי הקליטה, תיקייה,
    היסטוריה — "דורש API" בטקסט, לא כפתור מנוטרל ולא נתון מומצא."""
    js = _console_js()
    for needed in ("function clonersView(", "function buildersView(", "function machineSlots(", "function slotClass(",
                   "function slotHtml(", "function captureNowCard(", "function capturesTableCard(", "function cancelCaptureVerified(",
                   "function refreshGroupLive(", 'api("/room")', "drawer_list", 'UI.soon("כיבוי כולם")', "verify: { label: \"הקלד את שם האימג'\"",
                   'return "SMART לא נבדק"', "SATA ${s.n - 1}", "דורש API"):
        assert needed in js, needed
    block = js[js.index("/* ---------- #954 גל 3א"):js.index("/* ---------- לשונית \"נראו ברשת\"")]
    assert "disabled" not in block, "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "sda" not in block and "sdb" not in block, "שם sd* בקוד המשכפלים/הבנייה"
    assert 'd.smart === "ok"' in block and 'return ""' in block, "'לא נבדק' חייב להישאר אפור"
    assert "function groupPage(g, tab = 0)" in js and js.count("function groupPage(") == 1, "אובייקט אחד לכל הקבוצות"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .mgrid{", ".page .mgrid.big{", ".page .slots{", ".page .disk.run{", ".page .disk.empty{", ".page .legend i.sw-err{", ".page .cap-now{"):
        assert sel in css, sel


def test_deploy_page_is_the_cloners_room_from_the_api_only():
    """‏#954 גל 4: דף ההפצה הוא חדר המשכפלים (deploy.md) — על אותו גריד של
    גל 3א (`clonerCardHtml` במצב חדר, `machineSlots`, `slotHtml`), לא עותק.
    כתיבה רק ל-endpoints הקיימים (`POST /room`, `/room/start`, `/room/wake`,
    `/room/close` מאחורי הקלדת שם); סבב חדש inline (לא sheet); ההכרעה על
    SMART (כתום ממשיך, אדום מדלג) גלויה בדף; מה שאין לו API — קצב/איבוד,
    היסטוריה, דילוג מרחוק, תשובה מהקונסולה — "דורש API" בטקסט. הדף הישן
    (סבבים / הצטרפות חיה, בקרת סבב, המגירה) איננו."""
    js = _console_js()
    for needed in ("function deploy(tab = 0)", "function loadDeploy(", "function roomKpis(", "function roomGridCard(", "function roomNotes(",
                   "function roomNewCard(", "function stopRoom(", "function startWave(", "function imageFitReason(", "function deployClassView(",
                   'post("/room", body)', 'post("/room/start")', 'post("/room/close", { confirm_name: name })', "verify: { label: \"הקלד את שם האימג'\", mustEqual: name }",
                   "clonerCardHtml(m, admin, true)", "בלי תשובה הסוכן ממשיך לכתוב", "בלי תשובה הסוכן מדלג", "קצב ואיבוד — <b", "דורש API",
                   'deploy: { crumb: "סבב הפצה", title: "סבב הפצה", tabs: deployTabs(), render: deploy, load: loadDeploy, own: true }'):
        assert needed in js, needed
    block = js[js.index("/* ---------- #954 גל 4"):js.index("/* ---------- ספריית אימג'ים (#954 גל 2)")]
    assert "sda" not in block and "sdb" not in block, "שם sd* בקוד החדר"
    assert 'disabled>' not in block.replace('${reason ? " disabled" : ""}', ""), "כפתור מנוטרל במקום 'דורש API' (README §8) — רק אפשרות אימג' שלא נכנסת (#953) מושבתת"
    assert "דלג בשניהם" not in block
    assert "prompt()" not in block and "confirm(" not in block, "prompt/confirm חסומים — רק sheet()"
    for gone in ("הצטרפות חיה", "בקרת סבב", "async function openRoundDetail", "startSelectedRound", "openNewDeployment", "roundDrawerOpen", "deploy: [deploy, emptyDataCard]"):
        assert gone not in js, gone
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .rnew{", ".page .rnotes{", ".page .rnew .radios{"):
        assert sel in css, sel


def test_health_and_ports_pages_are_tables_from_the_api_with_a_switch_per_row():
    """‏#954 גל 5: בריאות = טבלת בדיקות (חמישה מצבים, "לא נבדק" אינו ירוק,
    לולאות אתחול מקובצות) + כרטיס גרסה ועדכון (הקלדת שם השרת) — בלי מתגים
    ובלי הלשוניות הזהות "סקירה/שירותים/בדיקות" (#829 M5). פורטים = טבלה אחת
    עם מתג בכל שורה (נדב 17/09 06:25): המתגים של היום (DHCP, proxy, מוניטור,
    SSH לתחנות, SSH לשרת × כרטיס) קוראים לאותם endpoints; לשאר — החוזה של
    #996 (enabled/bind/toggle/off_means, PUT /ports/{id}), ובלעדיו המתג
    מוסבר ולא מנוטרל. הכרטיסים עם "פתיחה/סגירה/שינוי" המנוטרלים — נמחקו.
    ‏#1015 (מקור אחד לכל שורה): portServerToggle משתמש ב-portToggleUrl(p)
    (toggle_url של השורה, ואם חסר — /ports/{id} כמו קודם) ומכבד confirm_when."""
    js = _console_js()
    for needed in ("function health()", "function healthRows(", "function healthUpdateCard(", "async function loadHealthUpdate(",
                   'unknown: ["unk", "לא נבדק"]', 'off: ["", "כבוי"]', '["agent_loop:", "לולאות אתחול", "agent_loops"]',
                   "function ports()", "function portRows(", "function portSwitchHtml(", "function portServerToggle(",
                   "function portConfirmDirection(", "function portToggleUrl(",
                   "await put(portToggleUrl(p), { enabled: enabling, ...extra })", "const word = p.confirm_word || ME.server_name", 'mustEqual: word',
                   "nicBody(n, { enabled: false, proxy: false })",
                   '"/ssh/stations"', "`/ssh/interfaces/${encodeId(nic.name)}`", 'mustEqual: "imagectl.monitor"',
                   'role="switch"', "דורש API (#996)", "havePortsDhcp", "havePortsSshServer",
                   'health: { crumb: "בריאות ושירותים", title: "בריאות ושירותים", tabs: [], render: health, load: loadHealth, own: true }',
                   'render: ports, load: loadPorts, own: true }'):
        assert needed in js, needed
    for gone in ('id="ssh-body"', "function loadSsh(", "function sshLight(", "SSH_LIGHT", 'title="בקרוב (נדרש endpoint)"',
                 'tabs: ["סקירה", "שירותים", "בדיקות"]', "health: [health,", "ports: [ports]"):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 5/#996: רשת › פורטים"):js.index("/* ---------- #954 גל 6: מוניטור — הרשימה")]   # גל 6: המוניטור אחרי הפורטים
    assert "disabled" not in block, "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .st.unk::before{", ".page .sw{", ".page .sw.unk{", ".page .upd{"):
        assert sel in css, sel


def test_small_pages_are_own_tables_from_the_api_and_wol_is_per_machine():
    """‏#954 גל 6: מוניטור (רשימה), דרייברים, הגדרות, הרשאות ויומן נבנו מחדש
    כעמודי `own` — טבלה אחת (datagrid) לכל אחד, מתגים `role="switch"`, הקלדת
    שם למחיקת משתמש/חבילה, "לא נקרא" כמצב משלו — והדפים הישנים (‏"Settings"
    באנגלית, `usersAdminPage` + `permissions` כפולים, "אירועים / Audit",
    כרטיס-לכל-מכונה במוניטור) נמחקו. ‏#984: WoL למחשב בודד — `UI.soon("WoL")`
    ו"אין WoL למחשב יחיד" הוחלפו ב-`wakeMachine` / `wakeGroup`; "נשלח" ≠
    "התעוררה". מתג זהות המכונה (#855) נשאר בהגדרות."""
    js, drivers, page = _console_js(), (STATIC / "drivers.js").read_text(encoding="utf-8"), _index()
    for needed in ("function monitorPage()", "function monitorRowHtml(", "function settings()", "function settingRowHtml(", "async function saveSettings()",
                   "function permissions()", "const ROLE_MATRIX = [", "function userDeleteSheet(", "function logs()", "function logBarHtml(", "function logMore()",
                   "async function wakeMachine(", "async function wakeGroup(", "function wolResultText(",
                   'key: "identity_check"', "WoL נשלח ל-", '"/api/console/journal" + logQuery()', 'X-Journal-Search-Truncated',
                   'settings: { crumb: "הגדרות", title: "הגדרות", tabs: [], render: settings, load: loadSettingsData, own: true }',
                   'permissions: { crumb: "הרשאות", title: "הרשאות", tabs: [], render: permissions, load: loadUsersData, own: true }',
                   'logs: { crumb: "יומן", title: "יומן", tabs: [], render: logs, load: loadJournalData, own: true }',
                   'monitor: { crumb: "מוניטור", title: "מוניטור", tabs: [], render: monitorPage, load: loadMonitor, own: true }',
                   'tabs: ["חבילות", "כיסוי לפי מכונה"], render: (i) => driversPage(i), load: () => loadDrivers(), own: true'):
        assert needed in js, needed
    for gone in ("function usersAdminPage(", "function journalPage(", "function settingsPage(", "function loadUsersAdmin(", "function openRolesDrawer(",
                 "function openLogFilter(", "JOURNAL_FILTER_FIELDS", "function loadUpdateInfo(", "function checkForUpdate(", 'title:"Settings"',
                 'tabs: ["אירועים", "Audit"]', 'tabs: ["משתמשים", "תפקידים"]', 'tabs: ["מכונות"]', "detail-grid\"><div class=\"detail-box\"><span class=\"k\">MAC",
                 "function wakeMachine() { soon(); }", 'UI.soon("WoL")', "אין WoL למחשב יחיד", 'UI.soon("Wake-on-LAN")', 'UI.soon("הער את כולם (WoL)")',
                 "permissions: [usersAdminPage, permissions]", "logs: [journalPage, logs]", "settings: [settingsPage]", "monitor: [monitorPage]"):
        assert gone not in js, gone
    for needed in ("function driversPage(tab = 0)", "function driversCoverageCard()", "function driverRuleHits(", "function openDriverDetail(", "tools/drivers/build-package.py"):
        assert needed in drivers, needed
    assert 'class="table"' not in drivers, "הטבלה הישנה"
    assert "<span>הגדרות</span>" in page and "<span>Settings</span>" not in page
    assert "<span>יומן</span>" in page and "יומן / Audit" not in page
    block = js[js.index("/* ---------- #954 גל 6: הרשאות"):js.index("/* ---------- #954 גל 8: רשת כאובייקט")]   # גל 8 החליף את "כרטיסי רשת / רשת הפצה"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .srow{", ".page .srow.changed{", ".page .logo-box{"):
        assert sel in css, sel


def test_the_class_build_and_cloner_tree_nodes_open_a_role_focused_machines_page():
    """נדב 17/09 (עם צילום): לשלושת צומתי האב בעץ — כיתות / מחשבי בנייה / מחשבי
    שיכפול — "אמור להיות חלון כמו של המחשבים, אבל רק של כיתות / שיכפול / בנייה,
    בלי לשונית נראו ברשת". קליק = הדף הממוקד (selectMachinesRole), דאבל =
    פותח/סוגר את הענף. הקבוצות שמתחתיהם (grp_BUILD וכו') נשארות אובייקטים."""
    page = _index()
    for role, box in (("classroom", "classesTree"), ("build", "buildTree"), ("cloner", "clonerTree")):
        assert (f"onclick=\"selectMachinesRole('{role}')\" "
                f"ondblclick=\"toggleInventoryGroup(this,'{box}')\"") in page, role
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    assert "function selectMachinesRole(role)" in js
    assert 'if (MACHINES_ROLE) return [ROLE_PAGE_HE[MACHINES_ROLE], red];' in js
def test_network_is_one_object_page_with_the_diagram_nics_and_deploy_tabs():
    """‏#954 גל 8: "רשת" בעץ הוא דף (קליק = הדף, דאבל = פותח/סוגר), ושלוש
    הלשוניות שנבנו לפי `network.md` / `network-nics.md` / `network-deploy.md`
    (תרשים SVG inline, טבלת כרטיסים בפועל/מוגדר/פער, רשת הפצה — מה מחולק בפועל
    מול השמור) + "פורטים" = הדף הקיים של גל 5, שלא נגעו בו מלבד רצועת הלשוניות.
    הדפים הישנים `nic`/`netdeploy`/`network` (כרטיס-לכל-NIC, `object-strip`,
    "פורטיים" קבועים, 10.44.12.0/24 קשיח) נמחקו. ‏#53: כל שינוי DHCP/כתובת
    עובר בטפסים הקיימים של net.js/netcfg.js. ‏#1032: תא הפעולות שומר מקום."""
    js, page, css = _console_js(), _index(), (STATIC / "console.css").read_text(encoding="utf-8")
    # העץ: הצומת "רשת" מנווט לדף; הילדים פותחים את האובייקט בלשונית; "פורטים" כמו שהיה
    assert ("""<div class="inventory-node" data-page="network" onclick="selectPageById('network')\""""
            """ ondblclick="toggleInventoryGroup(this,'netTA')\"""") in page
    assert 'id="nicNode"' in page and "openNetwork(1,null,this.parentElement)" in page
    assert 'data-net-tab="2" onclick="openNetwork(2,null,this)"' in page
    assert """<div class="inventory-node" data-page="ports" onclick="selectPageById('ports')\"""" in page
    for gone in ('data-page="nic"', 'data-page="netdeploy"', "NIC_HIGHLIGHT"):
        assert gone not in page, gone
    for needed in ("function networkPage(", "function loadNetwork(", "function netDiagramModel(", "function netDiagramSvg(", "function networkDiagramTab(",
                   "function networkNicsTab(", "function networkDeployTab(", "function netServicesOn(", "function netGapStatus(", "function netProbe(", "function openNetwork(",
                   'network: { crumb: "רשת", title: "רשת", tabs: NET_TABS, render: (i) => networkPage(i), load: loadNetwork, own: true }',
                   'const NET_TABS = ["תרשים", "חיבורים פיזיים", "רשת הפצה", "פורטים"];',
                   'width="${ND.W}" height="${H}" viewBox="0 0 ${ND.W} ${H}"',   # gotcha: SVG עם height מפורש
                   "מה מותר — דורש API (#705)", 'title: "כיתות — v2"', "`/net/interfaces/${encodeId(name)}/probe`",
                   "editNic(n)", "editAddress(netCfgRow(n.name)", "routeDeleteSheet(", 'cls: "stable",'):
        assert needed in js, needed
    for gone in ("function renderNicCard(", "function netBannerHtml(", "function nic()", "function netdeploy()", "function network()", "function saveNetwork(",
                 "function loadNetcfgData(", "function loadNetPages(", "function editDeployNic(", "function nicUnion(", "10.44.12.0/24", "פורטיים",
                 'netdeploy: [netdeploy]', 'nic: [nic]', "selectPageById('nic')", "selectPageById('netdeploy')"):
        assert gone not in js, gone
    block = js[js.index("/* ---------- #954 גל 8: רשת כאובייקט"):js.index("function healthCheckById(id) {")]
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    assert "disabled" not in block.replace("disabled_at", ""), "כפתור מנוטרל במקום 'דורש API' (README §8)"
    assert "function routeDeleteSheet(" in (STATIC / "netcfg.js").read_text(encoding="utf-8")
    # ‏#1032: תא הפעולות שומר מקום (visibility), לא display:none
    assert ".page .dg.stable td .acts{display:flex;visibility:hidden" in css
    for sel in (".page .netdiag-svg{", ".page .netdiag .ln.off{stroke-dasharray:4 4}", ".page .netdiag .led.err{", ".page .kv3{"):
        assert sel in css, sel


# ---------- #649 שלב 1: ארגז כלים ----------

def test_tools_page_is_an_admin_tree_node_with_two_selections_per_tool():
    """‏#649 שלב 1 (הכרעת נדב 17/09): צומת "ארגז כלים" תחת "תשתית" (admin בלבד,
    אחרי "דרייברים"), עמוד `own` שבו קבוצה = כרטיס וטבלה עם ☐ בנייה/שיכפול
    **ו-☐ תלמיד** לכל כלי (תלמיד = v2, הבחירה נשמרת כבר עכשיו); סיכון = pill
    (ro אפור / rw כתום / destroy אדום עם "הקלדת שם"); גודל = ארוז ירוק / מספר /
    "לא נמדד" (לא 0 — עיקרון 5); שמירה ב-`PUT /tools/selection` נדלקת רק כשיש
    שינוי; בלי .acts בשורות (#1032: שום דבר לא זז ב-hover)."""
    page, js = _index(), _console_js()
    infra = page[page.index('id="infraTA"'):page.index('id="adminTA"')]
    node = infra[infra.index('data-page="tools"') - 40:infra.index('data-page="tools"') + 200]
    assert 'data-admin' in node and "selectPageById('tools')" in node
    assert infra.index('data-page="drivers"') < infra.index('data-page="tools"'), "אחרי דרייברים"
    assert "<span>ארגז כלים</span>" in infra
    for needed in ("function toolsPage()", "async function loadTools()", "function toolRowHtml(", "function toolsGroupsHtml(", "function toolsBarHtml(",
                   "function toolsToggle(", "function toolsGroupMark(", "async function toolsSave()", "function toolsDirty()", "function toolsSummary(",
                   'await api("/tools/catalog")', 'await put("/tools/selection", body)', 'const body = { build: [...TOOLS_DRAFT.build], student: [...TOOLS_DRAFT.student] };',
                   'box("build", "בנייה/שיכפול"), box("student", "לתלמיד (v2)")', 'id="tools-save"', "סמן את המומלצים", "לא נמדד", 'UI.status("ok", "ארוז")',
                   "דורש הקלדת שם המחשב", 'destroy: ["err"', 'rw: ["warn"', 'ro: [""',
                   'tools: { crumb: "ארגז כלים", title: "ארגז כלים", tabs: [], render: toolsPage, load: loadTools, own: true }'):
        assert needed in js, needed
    block = js[js.index("/* ---------- #649 שלב 1: ארגז כלים"):js.index("const pages = {")]
    assert 'class="acts"' not in block.split("function toolRowHtml(")[1].split("function toolsGroupsHtml(")[0], "#1032: בלי .acts בשורה"
    assert "prompt(" not in block and "confirm(" not in block.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    assert "disabled" not in block.replace('${dirty ? "" : " disabled"}', "").replace("save.disabled = !dirty", ""), "כפתור מנוטרל רק לשמירה בלי שינוי"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page table.dg.tools{", ".page .dg-bar label.chk{"):
        assert sel in css, sel


def test_the_machine_drawer_shows_the_ssh_hostkey_fingerprint():
    """‏#1080: שורת SSH בכרטיס המכונה — 12 תווים, tooltip מלא, כתום רק
    כשהמפתח השתנה באותו אתחול; אתחול = אפור 'מפתח חדש מאתחול'."""
    js = _console_js()
    assert "function sshHostkeyHtml(" in js
    fn = js[js.index("function sshHostkeyHtml"):js.index("function machineDrawerHtml")]
    assert "SHA256:" in fn and "slice(0, 12)" in fn
    assert 'title="${esc(fp)}"' in fn
    assert 'UI.status("warn", "השתנה באתחול הזה")' in fn
    assert "מפתח חדש מאתחול" in fn
    assert "לא דווח" in fn
    drawer = js[js.index("function machineDrawerHtml"):js.index("function openMachineDetail")]
    assert '["SSH", sshHostkeyHtml(m)]' in drawer


def test_the_machine_drawer_has_a_health_group_with_three_states_per_field():
    """‏#1049 שלב ב': קבוצת "בריאות המכונה" במגירה — השערים מעליה, וכל שדה
    בשלושה מצבים: null = "לא נבדק" (אפור), {error} = "לא הצלחנו לבדוק" (כתום),
    ערך = מוצג. בעמוד הבית: כל verdict הוא UI.note ב"דורש טיפול"."""
    js = _console_js()
    assert 'class="sec">בריאות המכונה' in js
    assert "machineVerdictsHtml(m)" in js and "machineHealthHtml(m)" in js
    health = js[js.index("function machineHealthHtml"):js.index("function machineDrawerHtml")]
    for label in ("חשמל", "שעון", "מעבד", "זיכרון", "טמפ' מקס'", "רשת",
                  "מתג ופורט", "כבל", "NVMe",
                  "קריסה קודמת", "מפתח OEM", "PCI בלי דרייבר", "הצפנה"):
        assert f'["{label}"' in health, label
    assert "netprobeLldpHtml" in health and "netprobeCableHtml" in js
    assert "לא נקלט (35ש')" in js and "לא נבדק — יש קישור" in js
    assert "לא הצלחנו להאזין" in js
    cell = js[js.index("function probeCell"):js.index("function probeSkewText")]
    assert "לא נבדק" in cell and "לא הצלחנו לבדוק" in cell
    assert 'UI.status("warn", `לא הצלחנו לבדוק' in cell, "שגיאה בכתום, לא באפור ולא בירוק"
    assert "מעולם לא דיווחה בדיקת מכונה" in health
    assert "selectPageById('drivers')" in health, "PCI בלי דרייבר מקשר לדף הדרייברים"
    home = js[js.index("function homeAttention"):js.index("function journalCls")]
    assert "m.probe_verdicts" in home and "openMachineDetail(" in home


def test_storage_page_is_an_admin_tree_node_with_all_eight_mockup_screens():
    """‏#1066 שלב ב': צומת "אחסון" תחת "תשתית" (admin בלבד, בין "בריאות
    ושירותים" ל"רשת"), עמוד `own` ב-`storage.js` שכל שמונת מסכי המוקאפ
    (`docs/design/console-redesign/storage-mockup-2026-09-18.html`) הם מצבים
    שלו: רשימה · הוסף→סוג · NFS · SMB · iSCSI א' · iSCSI ב' (שלושה מצבי דיסק) ·
    מגירה · שורות הבריאות מקובצות. "שמור ועגן" נדלק רק אחרי `POST /test` מוצלח
    (עיקרון 5); פירמוט והסרה מאחורי הקלדת IQN / שם (עיקרון 7); ב-`unknown` אין
    כפתור פירמוט כלל. הרינדור עצמו נבדק ב-`storage_page.test.cjs`."""
    page, js = _index(), _console_js()
    storage = (STATIC / "storage.js").read_text(encoding="utf-8")
    infra = page[page.index('id="infraTA"'):page.index('id="adminTA"')]
    node = infra[infra.index('data-page="storage"') - 40:infra.index('data-page="storage"') + 420]
    assert 'data-admin' in node and "selectPageById('storage')" in node
    assert "<span>אחסון</span>" in node
    assert infra.index('data-page="health"') < infra.index('data-page="storage"') < infra.index('data-page="network"'), "בין בריאות לרשת"
    assert '<script src="storage.js?v=' in page
    assert page.index('src="console.js?v=') < page.index('src="storage.js?v='), "storage.js אחרי console.js (UI/api/sheet)"
    assert 'storage: { crumb: "אחסון", title: "אחסון", tabs: [], render: () => storagePage(), load: () => loadStorage(), own: true }' in js
    for icon in ('"storage": "<ellipse', '"plus": "<path'):
        assert icon in js, icon
    assert '["storage_", "אחסון — מיקומים", "storage_summary"]' in js, "שורות storage_<id> בבריאות מקובצות"
    assert 'act = UI.acts([["פתח באחסון", "selectPageById(\'storage\')"]]);' in js
    assert 'r.available === false ? ` ${UI.pill("err", "לא זמין")}`' in js, "אימג' על מיקום לא נגיש — pill בספרייה"
    for needed in ("function storagePage()", "async function loadStorage()", "function startStorageTimer()", "function storageListView()",
                   "function storageAddView()", "function storageLocalView()", "function storageNfsView()", "function storageSmbView()",
                   "function storageIscsiAView()", "function storageIscsiBView()", "function storageDrawerHtml(loc)",
                   "async function storageScan()", "async function storageTest()", "async function storageSave()",
                   "async function storageIscsiLogin()", "async function storageIscsiDisk()", "async function storageMount(id)",
                   "function storageFormatSheet(id)", "function storageDelete(id)", "function storageDisconnect(id)",
                   'post(SL + "/scan", body)', 'post(SL + "/test", { type: storageType(), params: storageParams() })',
                   "tested: true", "/iscsi-login`", "/disk`", "/mount`", "/format`", "/check`", "/disconnect`", "/connect`", 'method: "DELETE"',
                   'disabled title="נדלק רק אחרי בדיקה מוצלחת"', "ה-IQN שהוקלד אינו זהה ליעד", "השם שהוקלד אינו זהה לשם המיקום",
                   'unknown: אין כפתור פירמוט כלל', "}, 30000);", "legend-states", "show-acts storage"):
        assert needed in storage, needed
    assert "prompt(" not in storage and "confirm(" not in storage.replace("confirmSheet(", ""), "prompt/confirm חסומים — רק sheet()"
    css = (STATIC / "console.css").read_text(encoding="utf-8")
    for sel in (".page .dg.show-acts td .acts{visibility:visible}", ".page .types{display:grid;grid-template-columns:repeat(4,minmax(0,1fr))",
                ".page .picklist{", ".page .steps{", ".page .disk-state{display:grid;grid-template-columns:minmax(0,1fr) 250px",
                ".page .big-choice{", ".page .legend-states{", ".page .btn:disabled{"):
        assert sel in css, sel
    narrow = css[css.index("#1066 שלב ב'"):]
    assert "grid-template-columns:1fr" not in narrow, "1fr לבדו = minmax(auto,1fr) וגולש ב-375 (הלקח מהמוקאפ)"
    assert ".page table.dg.storage{min-width:760px}" in narrow
