"""#1090: the native GUI is bound to the approved 18/09 console tokens."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "docs/design/native-gui-mockup-2026-09-18.html").read_text(encoding="utf-8")
C = (ROOT / "native-gui/src/theme.c").read_text(encoding="utf-8")
H = (ROOT / "native-gui/src/theme.h").read_text(encoding="utf-8")


def desktop_rules(css):
    """Yield balanced non-at-rules; nested keyframes are not theme rules."""
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


def css_value(selector, prop, theme="dark"):
    """Resolve var() against dark variables with the required :root fallback."""
    if selector == ':root[data-theme="dark"]' and theme == "light":
        selector = ":root"
    rules = CSS.get(selector, {})
    if prop not in rules and selector == ':root[data-theme="dark"]':
        rules = CSS[":root"]
    value = rules[prop]
    variables = dict(CSS[":root"])
    if theme == "dark":
        variables.update(CSS[':root[data-theme="dark"]'])
    for _ in range(4):
        changed = False
        def replace(match):
            nonlocal changed
            changed = True
            return variables[match[1]]
        value = re.sub(r"var\((--[\w-]+)\)", replace, value)
        if not changed:
            break
    return value


dark_body = re.search(r"const Theme THEME_DARK\s*=\s*\{(.*?)\};", C, re.S)[1]
light_body = re.search(r"const Theme THEME_LIGHT\s*=\s*\{(.*?)\};", C, re.S)[1]
COLOR_BINDINGS = re.findall(
    r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\),\s*/\* css: (.*?) \| (.*?) \| (\d+) \*/",
    dark_body,
)
NUMBER_BINDINGS = re.findall(
    r"const double (\w+) = ([\d.]+); /\* css: (.*?) \| (.*?) \| (\d+) \*/", C
)


def normalized_hex(value):
    colors = re.findall(r"#([\da-fA-F]{6}|[\da-fA-F]{3})\b", value)
    assert colors, value
    result = colors[0].lower()
    return result if len(result) == 6 else "".join(c * 2 for c in result)


def check_theme_colors_match_mockup(name, dark_actual, selector, prop, index):
    del index  # every bound token resolves to one color
    light = dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\)", light_body))
    assert dark_actual.lower() == normalized_hex(css_value(selector, prop, "dark")), name
    assert light[name].lower() == normalized_hex(css_value(selector, prop, "light")), name


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


def check_every_exported_dimension_has_a_mockup_binding():
    exported = set(re.findall(r"extern const double (\w+);", H))
    bound = {v[0] for v in NUMBER_BINDINGS}
    assert exported == bound
    assert len(exported) >= 40


def check_every_theme_color_has_a_mockup_binding():
    dark_fields = set(re.findall(r"\.(\w+)\s*=\s*HEX", dark_body))
    light_fields = set(re.findall(r"\.(\w+)\s*=\s*HEX", light_body))
    bound = {v[0] for v in COLOR_BINDINGS}
    assert dark_fields == light_fields == bound
    assert len(bound) >= 25


def check_theme_radius_uses_each_roots_r_token():
    dark = float(re.search(r"\.radius\s*=\s*([\d.]+)", dark_body)[1])
    light = float(re.search(r"\.radius\s*=\s*([\d.]+)", light_body)[1])
    assert dark == float(css_value(':root[data-theme="dark"]', "--r", "dark").removesuffix("px"))
    assert light == float(css_value(":root", "--r", "light").removesuffix("px"))


def contrast(colors, fg, bg):
    def luminance(name):
        rgb = [int(colors[name][i:i + 2], 16) / 255 for i in (0, 2, 4)]
        rgb = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
        return sum(v * w for v, w in zip(rgb, (.2126, .7152, .0722)))
    pair = sorted((luminance(fg), luminance(bg)))
    return (pair[1] + .05) / (pair[0] + .05)


def check_light_theme_preserves_readable_surface_contrast():
    colors = dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\)", light_body))
    for fg, bg in [("ink", "surface"), ("muted", "surface"), ("on_ink", "indigo"),
                   ("alert_ink", "alert_bg"), ("on_ink", "success_btn")]:
        assert contrast(colors, fg, bg) >= 4.5, (fg, bg)


def check_documented_dark_action_contrast_exception_is_still_exact():
    colors = dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]+)\)", dark_body))
    ratio = contrast(colors, "on_ink", "indigo")
    assert 3.50 <= ratio <= 3.54  # accepted in login-mockup-2026-09-18.html (#829)


class TestNativeTheme(unittest.TestCase):
    """Collected by pytest; also runnable with the standard library alone."""


def bind(check, args=()):
    def test(self):
        check(*args)
    return test


for check, bindings in [(check_theme_colors_match_mockup, COLOR_BINDINGS),
                        (check_theme_dimension_matches_mockup, NUMBER_BINDINGS)]:
    for binding in bindings:
        setattr(TestNativeTheme, check.__name__.replace("check_", "test_") + "_" + binding[0],
                bind(check, binding))
for check in [check_every_exported_dimension_has_a_mockup_binding,
              check_every_theme_color_has_a_mockup_binding,
              check_theme_radius_uses_each_roots_r_token,
              check_light_theme_preserves_readable_surface_contrast,
              check_documented_dark_action_contrast_exception_is_still_exact]:
    setattr(TestNativeTheme, check.__name__.replace("check_", "test_"), bind(check))

if __name__ == "__main__":
    unittest.main()
