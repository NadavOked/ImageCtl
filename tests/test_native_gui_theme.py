"""‏#765: הפלטה של הגואי הנייטיב חייבת לשקף אחד-לאחד את console.css.

חוזה `native-gui/src/theme.c` הוא "אף צבע אינו מומצא כאן; אם token משתנה
ב-CSS הוא משתנה כאן, באותו שם". שלב א' של עיצוב ה-Clarity שינה את
console.css אך לא את theme.c — הפער הזה הוא בדיוק מה ש-#765 סוגר. הבדיקות
כאן קוראות את שני הקבצים ומאמתות שכל token תואם, וכך הן היו תופסות את
theme.c המיושן. אין להן תלות נייטיב (‏cc/pango) — הן רצות על כל סביבה.

**בקרה שלילית:** ‏`git checkout <base> -- native-gui/src/theme.c
native-gui/src/theme.h` מחזיר את הערכים הישנים, וכל אחת מהבדיקות נופלת
עם ה-hex בפועל מול הצפוי.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
# הטוקנים חיים ב-design-tokens.css (מאז מעטפת ה-vCenter), עם console.css
# כגיבוי לגרסאות שבהן ה-:root עדיין שם. קוראים את שניהם כך שהבדיקה
# תמצא את הבלוק בכל מקום (עיקרון 5 — לא להיכשל collection על מיקום).
CONSOLE_CSS = (REPO / "server" / "static" / "console.css").read_text(encoding="utf-8")
_TOKENS_CSS = REPO / "server" / "static" / "design-tokens.css"
if _TOKENS_CSS.exists():
    CONSOLE_CSS = _TOKENS_CSS.read_text(encoding="utf-8") + "\n" + CONSOLE_CSS
THEME_C = (REPO / "native-gui" / "src" / "theme.c").read_text(encoding="utf-8")
THEME_H = (REPO / "native-gui" / "src" / "theme.h").read_text(encoding="utf-8")

# ‏token של console.css -> שם השדה ב-struct Theme (מקף -> קו-תחתון).
TOKENS = [
    "porcelain", "surface", "ink", "muted", "hair", "indigo", "indigo-soft",
    "led-write", "led-ok", "led-idle", "danger", "hover", "field", "field-line",
    "track", "sunken", "btn-hover", "btn-hover-line", "ink-hover", "on-ink",
    "danger-line", "mark-line", "login-a", "login-b", "login-glow",
]


def _to_hex(v: str) -> str:
    """צבע CSS -> 'rrggbb'. תומך ב-#RGB, ‏#RRGGBB ו-rgb()/rgba().

    ‏design-tokens.css מבטא חלק מהטוקנים כ-rgb(... / א%) (למשל login-glow);
    theme.c מחזיק את שלישיית ה-RGB בלבד (אין שדה אלפא ל-glow), ולכן משווים
    את שלישיית ה-RGB. ערך שאינו צבע מוכר נכשל בקול (עיקרון 5), לא מוחזר ריק.
    """
    v = v.strip()
    if v.startswith("#"):
        h = v.lstrip("#").lower()
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        assert re.fullmatch(r"[0-9a-f]{6}", h), f"not a hex colour: {v!r}"
        return h
    m = re.fullmatch(r"rgba?\(\s*(\d{1,3})[ ,]+(\d{1,3})[ ,]+(\d{1,3})\s*(?:[/,][^)]*)?\)", v)
    assert m, f"unsupported colour: {v!r}"
    return "".join(f"{int(m.group(i)):02x}" for i in (1, 2, 3))


def _css_tokens(header: str) -> dict[str, str]:
    """קורא בלוק CSS שטוח (`header{ ... }`) למילון token->value גולמי."""
    m = re.search(re.escape(header) + r"\s*\{([^}]*)\}", CONSOLE_CSS)
    assert m, f"CSS block {header!r} not found"
    return {n.strip(): val.strip()
            for n, val in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", m.group(1))}


def _resolve(name: str, variables: dict[str, str]) -> str:
    """מרחיב שרשרת `var(--x)` לערך הסופי לפי מפת המשתנים של המצב.

    זה מה שהדפדפן עושה בפועל: אליאס תאימות כמו `--hover:var(--clr-hover)`
    מוגדר פעם אחת ב-:root, וב-dark רק `--clr-hover` נדרס — כך שהערך האפקטיבי
    של `--hover` בכהה הוא ערך ה-dark של `--clr-hover`. טוקן שה-target שלו אינו
    נדרס בכהה נשאר עם ערך האור. בלי ההרחבה הזאת ההשוואה משווה `var(...)`
    כמחרוזת, וזה בדיוק מה שהסתיר את היעדר ערכי ה-dark של האליאסים (#765).
    """
    value = variables[name]
    seen = set()
    while True:
        m = re.fullmatch(r"var\(\s*--([\w-]+)\s*(?:,[^)]*)?\)", value.strip())
        if not m:
            return value.strip()
        target = m.group(1)
        assert target not in seen, f"var() cycle via --{target}"
        seen.add(target)
        assert target in variables, f"--{target} (from --{name}) not defined"
        value = variables[target]


def _theme_c_fields(struct: str) -> dict[str, str]:
    m = re.search(r"const Theme " + struct + r"\s*=\s*\{(.*?)\};", THEME_C, re.S)
    assert m, f"{struct} not found in theme.c"
    return {f: h.lower()
            for f, h in re.findall(r"\.(\w+)\s*=\s*HEX\(0x([0-9A-Fa-f]{6})\)", m.group(1))}


# מפת המשתנים לכל מצב: dark יורש מ-light ודורס רק את מה שהבלוק הכהה מגדיר,
# בדיוק כמו ה-cascade של CSS.
LIGHT_VARS = _css_tokens(":root")
DARK_VARS = {**LIGHT_VARS, **_css_tokens(':root[data-theme="dark"]')}

# הערכים האפקטיביים (resolved) שהקונסולה מרנדרת בפועל בכל מצב.
CSS_LIGHT = {t: _to_hex(_resolve(t, LIGHT_VARS)) for t in TOKENS}
CSS_DARK = {t: _to_hex(_resolve(t, DARK_VARS)) for t in TOKENS}
C_LIGHT = _theme_c_fields("THEME_LIGHT")
C_DARK = _theme_c_fields("THEME_DARK")


@pytest.mark.parametrize("token", TOKENS)
def test_theme_light_mirrors_console_css(token):
    field = token.replace("-", "_")
    want = CSS_LIGHT[token]
    got = C_LIGHT[field]
    assert got == want, f"THEME_LIGHT.{field}: theme.c #{got} != console.css #{want}"


@pytest.mark.parametrize("token", TOKENS)
def test_theme_dark_mirrors_console_css(token):
    field = token.replace("-", "_")
    want = CSS_DARK[token]
    got = C_DARK[field]
    assert got == want, f"THEME_DARK.{field}: theme.c #{got} != console.css #{want}"


def _css_px(header_tokens: dict[str, str], name: str) -> float:
    return float(header_tokens[name].strip().rstrip("px"))


def _radius(name: str) -> float:
    m = re.search(r"#define\s+" + name + r"\s+([\d.]+)", THEME_H)
    assert m, f"{name} not defined in theme.h"
    return float(m.group(1))


def test_theme_radii_match_console_css():
    """פינות חדשות (Clarity): --r/--r-sm ב-design-tokens.css מול RADIUS_R/RADIUS_SM."""
    assert _radius("RADIUS_R") == _css_px(LIGHT_VARS, "r")
    assert _radius("RADIUS_SM") == _css_px(LIGHT_VARS, "r-sm")
