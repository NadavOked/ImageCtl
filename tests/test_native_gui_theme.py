"""#828: tokens follow the owner's HTML desktop cascade, including final overrides.

Media layout and light-theme appearance require Linux PNG comparison. The
mockup only supplies dark colours; light is derived with readable contrast.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "docs/design/native-gui-mockup-2026-09-13.html").read_text(encoding="utf-8")
C = (ROOT / "native-gui/src/theme.c").read_text(encoding="utf-8")
H = (ROOT / "native-gui/src/theme.h").read_text(encoding="utf-8")


def desktop_rules(css):
    """Balanced blocks keep media declarations out of the desktop cascade."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    pos = 0
    while pos < len(css):
        start = css.find("{", pos)
        if start < 0:
            return
        selector = css[pos:start].strip()
        end, depth = start + 1, 1
        while end < len(css) and depth:
            depth += (css[end] == "{") - (css[end] == "}")
            end += 1
        assert depth == 0, "unbalanced mockup CSS"
        if not selector.startswith("@"):
            yield selector, css[start + 1:end - 1]
        pos = end


CSS = {}
for block in re.findall(r"<style\b[^>]*>(.*?)</style>", HTML, re.S):
    for selectors, body in desktop_rules(block):
        declarations = dict(part.split(":", 1) for part in body.split(";") if ":" in part)
        declarations = {k.strip(): v.strip() for k, v in declarations.items()}
        for selector in selectors.split(","):
            CSS.setdefault(selector.strip(), {}).update(declarations)


def css_value(selector, prop):
    value = CSS[selector][prop]
    return re.sub(r"var\((--[\w-]+)\)", lambda m: CSS[":root"][m[1]], value)


dark = re.search(r"const Theme THEME_DARK\s*=\s*\{(.*?)\};", C, re.S)[1]
COLOR_BINDINGS = re.findall(
    r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\),\s*/\* css: (.*?) \| (.*?) \| (\d+) \*/", dark)
NUMBER_BINDINGS = re.findall(
    r"const double (\w+) = ([\d.]+); /\* css: (.*?) \| (.*?) \| (\d+) \*/", C)
# Sizes the mockup sets inline on one screen's own line (style="font-size:23px")
# rather than in the stylesheet: cited as "html: <function> | prop | index".
HTML_BINDINGS = re.findall(
    r"const double (\w+) = ([\d.]+); /\* html: (\w+) \| (.*?) \| (\d+) \*/", C)


def check_theme_dark_matches_mockup(name, actual, selector, prop, index):
    colors = re.findall(r"#([\da-fA-F]{6}|[\da-fA-F]{3})\b", css_value(selector, prop))
    expected = colors[int(index)].lower()
    if len(expected) == 3:
        expected = "".join(c * 2 for c in expected)
    assert actual.lower() == expected, f"{name}: #{actual} != {selector} {prop} #{expected}"


def check_theme_dimension_matches_mockup(name, actual, selector, prop, index):
    value = css_value(selector, prop)
    if prop in ("margin", "padding", "inset"):
        component = value.split()[int(index)]
    else:
        component = re.findall(r"(?:\d*\.)?\d+(?:px|%)?", value)[int(index)]
    expected = float(component.removesuffix("px").removesuffix("%"))
    if component.endswith("%"):
        expected /= 100
    assert float(actual) == expected, f"{name}: {actual} != {selector} {prop} {component}"


def check_theme_inline_dimension_matches_mockup(name, actual, function, prop, index):
    line = re.search(r"function %s\(\)\{.*" % function, HTML)
    assert line, function
    values = re.findall(r"%s:(\d+)px" % prop, line[0])
    assert float(actual) == float(values[int(index)]), (name, actual, values)


def check_every_exported_dimension_has_a_mockup_binding():
    exported = set(re.findall(r"extern const double (\w+);", H))
    bound = {v[0] for v in NUMBER_BINDINGS} | {v[0] for v in HTML_BINDINGS}
    assert exported == bound | {"N_DIM_ALPHA"}
    assert len(exported) >= 40


def check_every_theme_color_has_a_mockup_binding():
    fields = set(re.findall(r"\.(\w+)\s*=\s*HEX", dark)) - {"shadow_strong"}
    assert fields == {v[0] for v in COLOR_BINDINGS}
    assert len(fields) >= 25


def check_mockup_final_override_is_applied():
    assert CSS[".native-topline"]["height"] == "40px"
    assert CSS[".native-panel"]["padding"] == "20px"
    assert CSS[".native-title"]["font-size"] == "26px"


def check_light_theme_preserves_readable_surface_contrast():
    light = re.search(r"const Theme THEME_LIGHT\s*=\s*\{(.*?)\};", C, re.S)[1]
    colors = dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\)", light))
    def luminance(name):
        rgb = [int(colors[name][i:i+2], 16)/255 for i in (0, 2, 4)]
        rgb = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in rgb]
        return sum(v*w for v, w in zip(rgb, (.2126, .7152, .0722)))
    for fg, bg in [("ink", "surface"), ("muted", "surface"), ("on_ink", "indigo"),
                   ("alert_ink", "alert_bg"), ("on_ink", "success_btn")]:
        pair = sorted((luminance(fg), luminance(bg)))
        assert (pair[1]+.05)/(pair[0]+.05) >= 4.5, (fg, bg)


def check_retained_extension_tokens():
    """Non-mockup states use explicit, pinned extensions rather than new CSS."""
    assert re.search(r"const double N_DIM_ALPHA = 0\.55;", C)
    expected = {"HEAD_TITLE": "E7EDF1", "HEAD_SUB": "90A0AA",
                "ROOM_BAD": "E5484D", "ROOM_WARN": "B36B00",
                "STRIPE_A": "64A6D2", "STRIPE_B": "A2CFE5", "STRIPE_IDLE": "7E8C95"}
    actual = dict(re.findall(r"const Rgb (\w+) = HEX\(0x([\dA-Fa-f]+)\);", C))
    assert actual == expected


def check_shadow_matches_mockup():
    rgba = re.search(r"rgba\(0,0,0,([\d.]+)\)", CSS[".native-panel"]["box-shadow"])
    assert rgba
    values = re.findall(r"\.shadow_strong = HEX\(0x000000\), .shadow_strong_a = ([\d.]+)", C)
    assert len(values) == 2
    assert all(float(v) == float(rgba[1]) for v in values)


# The mockup has no light rules. These are the reviewed derivation choices;
# changing one is a deliberate design change, not an unnoticed token drift.
EXPECTED_LIGHT = {'porcelain': 'EEF1F5', 'surface': 'FFFFFF', 'ink': '243540', 'muted': '536975', 'hair': 'BFCBD3', 'indigo': '2D668A', 'indigo_soft': 'DFEDF5', 'led_write': '397EA9', 'led_ok': '318700', 'led_idle': '737373', 'danger': 'A52F39', 'hover': 'EAF2F7', 'field': 'F5F8FA', 'field_line': 'A7BAC7', 'track': 'DCE5EB', 'sunken': 'F4F7F9', 'btn_hover': 'E5EFF5', 'btn_hover_line': '4B86AC', 'ink_hover': '245572', 'on_ink': 'FFFFFF', 'danger_line': 'B75A62', 'mark_line': '4E7186', 'login_a': 'EEF3F7', 'login_b': 'DCE6EC', 'login_glow': 'FFFFFF', 'choice': 'F5F8FA', 'choice_line': 'B8CAD5', 'selected_line': '5E97BA', 'button': 'EDF3F7', 'button_line': 'A8BCC9', 'danger_bg': 'F9E9EA', 'warning_bg': 'FFF6DD', 'warning_line': 'B49A57', 'warning_ink': '826117', 'success_bg': 'E8F4EB', 'success_line': '4D8A63', 'success_ink': '2E7342', 'metric': 'F0F5F8', 'metric_line': 'C0CDD5', 'status_bg': 'E6EEF3'}

EXPECTED_LIGHT.update({'disk_bg': 'F1F6F9', 'disk_line': 'B7C8D3', 'disk_selected': 'E1EEF5', 'disk_selected_line': '5C95B8', 'clone_line': 'B6C8D2', 'round_line': 'B7C8D2', 'image_bg': 'F1F6F9', 'image_line': 'B5C9D5', 'image_selected': 'DFEDF5', 'image_selected_line': '5F9BC0'})
# #828 completion: .native-alert, .native-btn.success, .room-node borders,
# --yellow, .native-brand span.
EXPECTED_LIGHT.update({'alert_bg': 'FBF3DC', 'alert_line': 'B49A57', 'alert_ink': '6E5312', 'success_btn': '2D6F4D', 'success_btn_line': '55A879', 'node_active_line': '4D89AD', 'node_warn_line': 'B49A57', 'warn': 'C48A0A', 'brand_accent': '2F6F97'})

def check_derived_light_palette_is_pinned():
    light = re.search(r"const Theme THEME_LIGHT\s*=\s*\{(.*?)\};", C, re.S)[1]
    actual = dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\)", light))
    actual.pop("shadow_strong")
    assert actual == EXPECTED_LIGHT


class TestNativeTheme(unittest.TestCase):
    """Collected by pytest; also runnable with the standard library alone."""


def bind(check, args=()):
    def test(self):
        check(*args)
    return test


for check, bindings in [(check_theme_dark_matches_mockup, COLOR_BINDINGS),
                        (check_theme_dimension_matches_mockup, NUMBER_BINDINGS),
                        (check_theme_inline_dimension_matches_mockup, HTML_BINDINGS)]:
    for binding in bindings:
        setattr(TestNativeTheme, check.__name__.replace("check_", "test_") + "_" + binding[0],
                bind(check, binding))
for check in [check_every_exported_dimension_has_a_mockup_binding,
              check_every_theme_color_has_a_mockup_binding,
              check_mockup_final_override_is_applied,
              check_light_theme_preserves_readable_surface_contrast,
              check_retained_extension_tokens, check_shadow_matches_mockup,
              check_derived_light_palette_is_pinned]:
    setattr(TestNativeTheme, check.__name__.replace("check_", "test_"), bind(check))

if __name__ == "__main__":
    unittest.main()
