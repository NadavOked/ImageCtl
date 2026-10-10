"""apple-pending-4: ארבעת הממצאים שנדב אישר ("הכול מאושר") — בדיקות סטטיות על CSS ו-JS.

מקור: docs/design/apple-mockups/pending-4.html. ההתנהגות (תפריט ⋯, סימון עץ, כותרת)
נבדקת ב-tests/*.test.cjs; כאן רק מה שאינו רץ ב-vm — כללי ה-CSS וחוזי המבנה.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"


def _css() -> str:
    return (STATIC / "console.css").read_text(encoding="utf-8")


def _js() -> str:
    return (STATIC / "console.js").read_text(encoding="utf-8")


def _rule(css: str, selector: str) -> str:
    """גופי כל הכללים שהסלקטור מסיים אותם (כלל מאוחד בפסיקים נספר גם הוא)."""
    found = re.findall(r"(?:^|[{},\s])" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert found, f"אין כלל ל-{selector}"
    return ";".join(found)


# ---------- 1: טבלאות רחבות ----------

def test_wide_tables_have_a_sticky_inline_end_actions_column():
    """העמודה האחרונה דבוקה לקצה ה-inline-end (ב-RTL: השמאלי, שנחתך ראשון) ונשארת בעין."""
    body = _rule(_css(), ".page .dg.kebab-col th:last-child,.page .dg.kebab-col td:last-child")
    assert "position:sticky" in body and "inset-inline-end:0" in body
    # רקע מוצק, אחרת התוכן הגולל נראה מבעד לעמודה
    assert "background:var(--surface)" in _rule(_css(), ".page .dg.kebab-col td:last-child")


def test_secondary_columns_hide_in_a_narrow_card():
    css = _css()
    assert "container-type:inline-size" in _rule(css, ".page .card-b.flush")
    assert "@container (max-width:640px){.page .dg.kebab-col .c-opt{display:none}}" in css


def test_row_menu_destructive_item_is_red_and_separated():
    css = _css()
    assert "var(--danger)" in _rule(css, ".row-menu button.dg")
    assert "border-top" in _rule(css, ".row-menu hr")
    assert "position:fixed" in _rule(css, ".row-menu")


def test_row_menu_respects_reduced_motion():
    assert re.search(r"prefers-reduced-motion:reduce\)\{\.row-menu\{transform:none!important", _css())


def test_wide_tables_use_the_kebab_pattern():
    """הרשאות, חיבורים פיזיים ופורטים: מחלקת kebab-col וכותרת sr-only לעמודת הפעולות."""
    js = _js()
    for needle in ('cls: "kebab-col", opt: [4, 5]', 'cls: "stable kebab-col", opt: [3, 6]', 'cls: "dense kebab-col", opt: [6, 7]'):
        assert needle in js, needle
    assert js.count('<span class="sr">פעולות</span>') >= 3
    assert "UI.rowMenu(u.username, items)" in js
    # המחיקה נשארת מאחורי sheet עם הקלדת שם (עיקרון 7): הפריט מצביע על userDeleteSheet
    assert '["מחק משתמש…", `userDeleteSheet(\'${enc}\')`, "danger"]' in js


# ---------- 2: סימון העץ לפי הלשונית ----------

def test_tree_marks_active_node_with_a_bar_and_the_ancestor_softly():
    css = _css()
    # הפס בצד של הצומת הפעיל כבר קיים (border-inline-start) — לא נמחק
    assert "border-inline-start-color:var(--indigo)" in css
    assert "var(--indigo)" in _rule(css, ".inventory-node.anc")
    dot = _rule(css, ".inventory-node.anc::after")
    assert 'content:""' in dot and "border-radius:50%" in dot and "margin-inline-start:auto" in dot


def test_tab_changes_sync_the_tree_and_old_marks_are_cleared():
    js = _js()
    assert "syncTreeToTab();   // apple-pending-4 · 2" in js
    mark = js[js.index("function markTreeSelection("):js.index("function markTreeAncestor(")]
    assert ".inventory-node.anc" in mark and 'x.classList.remove("active", "anc")' in mark


# ---------- 3: אייקוני עץ ייחודיים ----------

TREE_ICON_KEYS = ["t-home", "t-deploy", "t-images", "t-machines", "t-health", "t-network", "t-monitor", "t-drivers",
                  "t-admin", "t-nic", "t-ports", "t-sliders", "t-key", "t-log", "t-infra"]


def _tree_icons() -> dict[str, str]:
    """תווית צומת → תוכן ה-SVG שלו, מעץ הניווט הראשי ב-index.html."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    out: dict[str, str] = {}
    for m in re.finditer(r'role="treeitem"[^>]*>(?:(?!role="treeitem").)*?<svg class="ui-icon"[^>]*>((?:(?!</svg>).)*)</svg>.*?<span>([^<]+)</span>', page, re.S):
        out.setdefault(m.group(2), m.group(1))
    return out


def test_fifteen_tree_icons_exist_inline_with_no_external_library():
    js = _js()
    for key in TREE_ICON_KEYS:
        assert f'"{key}": \'<' in js, key
    for banned in ("lucide", "fontawesome", "font-awesome", "feather", "material-icons"):
        assert banned not in (STATIC / "index.html").read_text(encoding="utf-8").lower()
    assert re.search(r"\.ui-icon\{[^}]*stroke:currentColor", _css().replace(" ", "")) or "stroke:currentColor" in _css().replace(" ", "")


def test_main_tree_has_no_duplicate_icons():
    """היום היו 5 כפילויות (מחשבים=מלאי=יומן, פורטים=חיבורים=רשת, מוניטור=ניהול=הרשאות, דרייברים=הגדרות,
    בריאות=תשתית). עכשיו כל צומת בעץ הראשי עם אייקון משלו."""
    icons = _tree_icons()
    wanted = ["סקירה כללית", "סבב הפצה", "מלאי", "אימג'ים", "מחשבים", "תשתית", "בריאות ושירותים", "אחסון", "רשת", "חיבורים פיזיים",
              "רשת הפצה", "פורטים", "מוניטור", "דרייברים", "ניהול", "הגדרות", "הרשאות", "יומן"]
    missing = [w for w in wanted if w not in icons]
    assert not missing, missing
    seen: dict[str, str] = {}
    for label in wanted:
        body = icons[label]
        assert body not in seen, f"אייקון כפול: {label} = {seen.get(body)}"
        seen[body] = label


def test_secondary_server_tree_uses_the_same_unique_icons():
    js = _js()
    tree = js[js.index("const BRANCH_TREE = ["):js.index("const BRANCH_VIEWS")]
    used = re.findall(r'"(t-[a-z]+)"', tree)
    assert {"t-home", "t-deploy", "t-images", "t-machines", "t-infra", "t-health", "t-network", "t-monitor", "t-drivers"} <= set(used)
    assert len(used) == len(set(used))
    # אין עוד אייקון של אחד מהם מחוץ לסט (למשל "health" ל"תשתית" או "user" למוניטור)
    assert not re.search(r'", "(health|user|settings|folder|list)", (true|false)\]', tree)
    assert 'icon: "health"' not in tree


def test_page_headers_use_the_icon_of_their_tree_node():
    js = _js()
    for name, key in [("בריאות ושירותים", "t-health"), ("הרשאות", "t-key"), ("יומן", "t-log"), ("פורטים", "t-ports"),
                      ("מוניטור", "t-monitor"), ("הגדרות", "t-sliders"), ("מחשבים", "t-machines")]:
        assert f'icon: "{key}", name: "{name}"' in js, name
    assert 'icon: "t-drivers", name: "דרייברים"' in (STATIC / "drivers.js").read_text(encoding="utf-8")


# ---------- 4: כותרת הסקירה ----------

def test_overview_header_is_one_row_with_one_ellipsis_sub_line():
    css = _css()
    sub = _rule(css, ".page .obj.compact .obj-sub")
    assert "white-space:nowrap" in sub and "text-overflow:ellipsis" in sub
    assert "flex-wrap:nowrap" in _rule(css, ".page .obj.compact .obj-row")


def test_attention_row_is_folded_with_a_counter_and_technical_details():
    css, js = _css(), _js()
    assert ".page .attn>summary .cnt" in css and ".page .attn[open]>summary::after" in css
    assert "list-style:none" in _rule(css, ".page .attn>summary")
    assert "<details class=\"tech\"><summary>פרטים טכניים</summary>" in js
    assert 'class="c12 attn ${worst}" id="home-attn"${HOME_ATTN_OPEN ? " open" : ""}' in js
    assert "compact: true });" in js[js.index("function home("):js.index("function home(") + 2500]


# ---------- #1255: פאנל "משימות אחרונות" ----------

def test_task_panel_columns_are_aligned_tabular_and_the_state_is_a_pill():
    css = _css()
    assert "tabular-nums" in _rule(css, ".task-table td.num")
    assert "text-align:start" in _rule(css, ".task-table th,.task-table td")
    assert "border-radius:10px" in _rule(css, ".task-table .pill")
    assert ".task-table .pill.err" in css and ".task-table .pill.info" in css and ".task-table .pill.ok" in css


def test_task_panel_has_no_hardcoded_dash_cells():
    """היום: `[r[0],r[1],"-","-",r[2],"-"]` — שלוש עמודות קבועות. הנתונים קיימים; ערך לא ידוע = "לא ידוע" (עיקרון 5)."""
    js = _js()
    fn = js[js.index("function renderActivity()"):js.index("function editImageDescription")]
    assert '"-","-"' not in fn and "'-'" not in fn
    assert 'const ACTIVITY_UNKNOWN = "לא ידוע"' in js


def test_new_server_fields_are_documented_in_interfaces_md():
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md").read_text(encoding="utf-8")
    assert "`created_by`, `created_by_role`" in doc and "`opened_by`, `opened_by_role`, `created_at`, `started_at`, `closed_at`" in doc


# ---------- כרטיס מחשב בנייה צר (נדב, 10/10) ----------

def test_builder_cards_get_a_sensible_width_and_the_value_column_can_shrink():
    css = _css()
    assert "minmax(min(100%,320px),1fr)" in _rule(css, ".page .mgrid.wide")
    assert "grid-template-columns:max-content minmax(0,1fr)" in _rule(css, ".page .kv")
    # שבירה-בכוח (anywhere) רק ל-mono (serial/MAC), לא לכל ערך
    assert "overflow-wrap:break-word" in _rule(css, ".page .kv .v")
    assert "overflow-wrap:anywhere" in _rule(css, ".page .kv .v .mono")


def test_long_state_text_is_clamped_to_three_lines_and_the_serial_is_one_ellipsis_line():
    css = _css()
    clamp = _rule(css, ".page .mcard .clamp")
    assert "-webkit-line-clamp:3" in clamp and "overflow:hidden" in clamp
    mono = _rule(css, ".page .mcard .kv .v .mono")
    assert "text-overflow:ellipsis" in mono and "white-space:nowrap" in mono and "max-width:100%" in mono
    js = _js()
    assert '<span class="mono" title="${esc(d0.serial)}">' in js, "serial מלא ב-title"
    assert 'class="mgrid wide">${sortMachinesBySuffix(kids).map((m) => builderCardHtml' in js
    assert '<span class="clamp" title="${esc(st.text)}">' in js


# ---------- דשבורדים: עקביות ----------

def test_dashboard_pages_share_the_compact_header():
    """סקירה, בריאות, אחסון, רשת (כל הלשוניות כולל פורטים), מוניטור וחדר ההפצה: אותה כותרת — שורה אחת + שורת משנה."""
    js = _js()
    for needle in ('actions, tabs, tab, compact: true });',                       # סקירה
                   'pill: pill + tlsPill, actions, compact: true });',            # בריאות
                   'tabs: NET_TABS, tab, tabClick: netTabClick, compact: true });',  # רשת
                   '`openNetwork(${i})`, compact: true });',                       # פורטים
                   'רענון</button>`, compact: true });',                           # מוניטור
                   'pill: tab === 0 ? pill : "", actions, tabs, tab, compact: true });'):  # חדר ההפצה
        assert needle in js, needle
    assert 'icon: "storage", name: "אחסון", sub, pill, actions, compact: true });' in (STATIC / "storage.js").read_text(encoding="utf-8")


def test_numbers_are_tabular_across_the_page_and_notes_share_the_technical_details_style():
    css = _css()
    assert "tabular-nums" in _rule(css, ".page")
    assert ".page .note .tech code" in css and "overflow-wrap:anywhere" in _rule(css, ".page .note .tech code")


def test_network_banners_hide_raw_system_text():
    js = _js()
    fn = js[js.index("function netUnreadNotes()"):js.index("function netWarnCount()")]
    assert fn.count("T(") >= 9 and "${esc(e." not in fn, "אין עוד שגיאה גולמית בגוף ההודעה"
    assert "UI.noteTech(" in (STATIC / "netcfg.js").read_text(encoding="utf-8")
