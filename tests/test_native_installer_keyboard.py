"""‏#1206: כל מסך במתקין נגמר בחצים, רווח, Enter ו-Esc בלבד.

נמדד ב-23/09 על VM של ESXi: הקונסולה של ESXi בדפדפן מעבירה ל-VM את
החצים, Enter ו-Esc — **אבל לא Tab**, שהדפדפן בולע. ‏Tab שנשלח ישירות
(`govc vm.keystrokes`) כן הזיז פוקוס, כלומר המתקין טיפל בו; מה שחסר הוא
טיפול בחצים מחוץ למסך הדיסק. בלי עכבר (#1204) לא הייתה דרך לבחור תפקיד,
כרטיס רשת או DHCP/סטטי, ולא לעבור בין שדות הסיסמה.

החוזה שנבדק כאן:

* ‏↑/↓ (וגם Tab/Shift+Tab) עוברים על **כל** הפקדים במסך, בסדר הקריאה.
* נחיתה על אפשרות (תפקיד, כרטיס רשת, DHCP/סטטי) **בוחרת** אותה — כמו
  ברשימת הדיסקים: מה שמסומן הוא מה שנבחר.
* רווח מחוץ לשדה טקסט לוחץ על הכפתור שבפוקוס ("בדוק חיבור"), ובמסך הרשת
  מחליף DHCP↔סטטי בלי לעבור על שורות הכרטיסים האחרים.
* חץ שלא שינה דיסק אינו מבטל את "אני מבין" (VM עם דיסק אחד + ISO).

הדרייבר כולל את `install.c` ישירות (‏`#include`), כמו
‏`test_native_gui_input.py` עם `input.c`, ומזין אירועים ל-`install_handle`
על מצב ה-demo. הגשר הוא `echo >>LOG` — כל פעולה שהמתקין שלח נרשמת.
‏cc + כותרות cairo = לינוקס בלבד; בווינדוס זה דילוג, לא מעבר.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

ROLE, NETWORK, ADMIN, DISK = 2, 3, 5, 1


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo", _pkgconfig("cairo")),
    posix=True, why="install.c נבנה ומורץ על לינוקס בלבד",
)

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }

static const char *focus_name(const App *a) {
    static char buf[32];
    switch (a->focus) {
    case 0: return "none";
    case HIT_INSTALL_PRIMARY: return "primary";
    case HIT_INSTALL_SECONDARY: return "secondary";
    case HIT_INSTALL_PRIMARY_URL: return "primary_url";
    case HIT_INSTALL_CHECK_PRIMARY: return "check";
    case HIT_INSTALL_DHCP: return "dhcp";
    case HIT_INSTALL_STATIC: return "static";
    case HIT_INSTALL_ADDRESS: return "address";
    case HIT_INSTALL_NETMASK: return "netmask";
    case HIT_INSTALL_GATEWAY: return "gateway";
    case HIT_INSTALL_DNS: return "dns";
    case HIT_INSTALL_HOSTNAME: return "hostname";
    case HIT_INSTALL_ADMIN_USER: return "admin_user";
    case HIT_INSTALL_PASSWORD: return "password";
    case HIT_INSTALL_CONFIRM: return "confirm";
    case HIT_INSTALL_CURRENT_PASSWORD: return "current_password";
    }
    if (a->focus >= HIT_INSTALL_NIC_BASE && a->focus < HIT_INSTALL_NIC_BASE + INSTALL_MAX_NICS) {
        snprintf(buf, sizeof buf, "nic%%d", a->focus - HIT_INSTALL_NIC_BASE);
        return buf;
    }
    snprintf(buf, sizeof buf, "id%%d", a->focus);
    return buf;
}

static void show(const App *a, const char *tok) {
    const InstallState *s = &a->install;
    printf("%%s view=%%d focus=%%s secondary=%%d nic=%%d static=%%d disk=%%d ack=%%d\n", tok,
           s->view, focus_name(a), s->secondary, s->nic, s->static_mode, s->disk, s->disk_ack);
}

static App app;

/* argv: BRIDGE-CMD VIEW TOKEN... -- the demo state on VIEW, focus as on
 * arrival (refocus), then one line of state after every token. */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    install_demo(a, atoi(argv[2]));
    refocus(a);
    show(a, "start");
    for (int i = 3; i < argc; i++) {
        const char *t = argv[i];
        Event e = { 0 };
        if (!strcmp(t, "only-disk-0")) { a->install.disks[1].iso = 1; show(a, t); continue; }
        if (!strcmp(t, "technical-open")) { a->install.technical_open = 1; show(a, t); continue; }
        if (!strcmp(t, "SPACE")) { e.type = UIEV_CHAR; e.ch = ' '; }
        else {
            e.type = UIEV_KEY;
            e.key = !strcmp(t, "UP") ? KEYSYM_UP : !strcmp(t, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(t, "TAB") ? KEYSYM_TAB : !strcmp(t, "BTAB") ? KEYSYM_BACKTAB
                  : !strcmp(t, "ENTER") ? KEYSYM_ENTER : !strcmp(t, "ESC") ? KEYSYM_ESC : 0;
            if (!e.key) { fprintf(stderr, "unknown token %%s\n", t); return 2; }
        }
        install_handle(a, &b, &e);
        show(a, t);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """נבנה פעם אחת למודול; הסימון `NATIVE` מדלג/נופל לפני שמגיעים לכאן."""
    tmp = tmp_path_factory.mktemp("install-keys")
    source = tmp / "install_keys.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_keys"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _press(driver: Path, tmp_path: Path, view: int, *tokens: str) -> tuple[list[dict], list[str]]:
    """מריץ את הרצף ומחזיר (מצב אחרי כל מקש, פעולות הגשר שנשלחו)."""
    log = tmp_path / "bridge.log"
    log.write_text("")
    proc = subprocess.run([str(driver), f'echo >>"{log}"', str(view), *tokens],
                          capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        tok, *pairs = line.split()
        state = dict(pair.split("=", 1) for pair in pairs)
        state["key"] = tok
        states.append(state)
    assert [s["key"] for s in states] == ["start", *tokens], proc.stdout
    return states, log.read_text().split()


def _col(states: list[dict], name: str) -> list[str]:
    return [s[name] for s in states]


@NATIVE
def test_role_arrows_switch_the_role_and_reach_the_url_and_the_check_button(driver, tmp_path):
    """מסך 2: בלי Tab לא הייתה דרך לחזור ל"שרת ראשי" או להגיע ל"בדוק חיבור"."""
    states, bridge = _press(driver, tmp_path, ROLE, "UP", "UP", "DOWN", "DOWN", "DOWN", "SPACE")
    assert _col(states, "focus") == [
        "primary_url", "secondary", "primary", "secondary", "primary_url", "check", "check"]
    # נחיתה על אפשרות בוחרת אותה: ‏UP השני עבר ל"ראשי", ‏DOWN החזיר ל"משני"
    assert _col(states, "secondary") == ["1", "1", "0", "1", "1", "1", "1"]
    assert bridge == ["check-primary"], "רווח על 'בדוק חיבור' לוחץ עליו — ורק עליו"


@NATIVE
def test_network_arrows_pick_the_nic_and_the_mode_and_reach_the_static_fields(driver, tmp_path):
    states, _ = _press(driver, tmp_path, NETWORK, "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "UP", "UP")
    assert _col(states, "focus") == [
        "nic0", "nic1", "nic2", "dhcp", "static", "address", "static", "dhcp"]
    assert _col(states, "nic") == ["0", "1", "2", "2", "2", "2", "2", "2"]
    assert _col(states, "static") == ["0", "0", "0", "0", "1", "1", "1", "0"]


@NATIVE
def test_network_space_flips_to_static_without_walking_past_the_other_nics(driver, tmp_path):
    """‏ens19 + כתובת סטטית: הליכה בחצים עד "סטטית" הייתה בוחרת את ens20
    בדרך. רווח מחליף מצב במקום, והפוקוס עובר לשדה ה-IP."""
    states, _ = _press(driver, tmp_path, NETWORK, "DOWN", "SPACE", "UP", "UP", "SPACE")
    assert _col(states, "focus") == ["nic0", "nic1", "address", "static", "dhcp", "address"]
    assert _col(states, "nic") == ["0", "1", "1", "1", "1", "1"]
    assert _col(states, "static") == ["0", "0", "1", "1", "0", "1"]


@NATIVE
def test_admin_arrows_move_between_the_fields_like_tab(driver, tmp_path):
    """מסך 5: נמדד ש-Tab עובר מהמשתמש לסיסמה — אבל Tab לא מגיע מהדפדפן."""
    states, _ = _press(driver, tmp_path, ADMIN, "DOWN", "DOWN", "DOWN", "UP", "TAB", "BTAB")
    assert _col(states, "focus") == [
        "admin_user", "password", "confirm", "admin_user", "confirm", "admin_user", "confirm"]


@NATIVE
def test_disk_arrow_that_changes_nothing_keeps_the_acknowledgement(driver, tmp_path):
    """VM עם דיסק אחד ו-ISO: החץ לא החליף דיסק, ובכל זאת מחק את הסימון
    "אני מבין" — ונראה כאילו החצים לא עושים כלום."""
    states, _ = _press(driver, tmp_path, DISK, "only-disk-0", "DOWN", "UP")
    assert _col(states, "disk") == ["0", "0", "0", "0"]
    assert _col(states, "ack") == ["1", "1", "1", "1"]


@NATIVE
def test_disk_arrow_that_changes_the_disk_still_clears_the_acknowledgement(driver, tmp_path):
    """השומר מהצד השני: דיסק אחר = אישור חדש. רווח מאשר שוב."""
    states, _ = _press(driver, tmp_path, DISK, "DOWN", "SPACE", "DOWN")
    assert _col(states, "disk") == ["0", "1", "1", "0"]  # ‏sr0 היא המדיה ומדולגת
    assert _col(states, "ack") == ["1", "0", "1", "0"]


@NATIVE
def test_a_leftover_technical_open_does_not_steal_the_arrows(driver, tmp_path):
    """‏technical_open נשאר דלוק אחרי ריצה שנכשלה ו"נסה שוב"; החצים גללו
    פלט שאינו על המסך במקום לזוז בין הפקדים."""
    states, _ = _press(driver, tmp_path, ROLE, "technical-open", "UP")
    assert _col(states, "focus")[-1] == "secondary"


@NATIVE
def test_escape_goes_back_and_enter_validates_forward_unchanged(driver, tmp_path):
    """השומר מפני תיקון-יתר: Enter ו-Esc עבדו לפני #1206 וממשיכים לעבוד."""
    states, bridge = _press(driver, tmp_path, ROLE, "ESC", "ENTER", "ENTER")
    assert _col(states, "view") == [str(ROLE), str(DISK), str(ROLE), str(NETWORK)]
    assert bridge == ["validate", "validate"]
