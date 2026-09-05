"""הגדרות הרשת של השרת — כל מה שנוגע במכונה עצמה (‏#55/#57).

‏`netcfg.py` הוא הלוגיקה הטהורה: מקבל הגדרות, מחזיר טקסט. כאן יושב הצד
המלוכלך — לכתוב את הקובץ ל-`interfaces.d`, להריץ `ifdown`/`ifup`,
ולקרוא בחזרה מה **באמת** קרה. ההפרדה היא גם מה שמאפשר לבדיקות להזריק
hooks ולעולם לא לגעת ברשת האמיתית, בדיוק כמו `dhcp_hooks`.

**שלושה מצבים, לא שניים** — הלב של המשימה:

1. "כתבנו את הקובץ" — ‏`write_conf` הצליח.
2. "ההגדרה הוחלה" — ‏`ifup` יצא באפס.
3. "הכתובת באמת שם" — ‏`ip addr` מראה אותה.

רק השלישי הוא הצלחה. שניים הראשונים נכונים גם כשקובץ `interfaces`
הראשי לא עושה `source interfaces.d/*`, כשהכרטיס מנוהל בפועל בידי משהו
אחר, או כשהמסכה נדחתה בשקט — ובכל אלה המסך היה מציג "נשמר" ומפעיל
היה מאמין לו. ‏`read_state()` היא הראיה החיובית, באותה תבנית של
‏`read_listeners()` ב-`ssh_switch.py`: ‏`checked=False` פירושו "לא
הצלחנו לשאול", ולא "אין".
"""

from __future__ import annotations

import ipaddress
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import netcfg

INTERFACES_DIR = "/etc/network/interfaces.d"
INTERFACES_MAIN = "/etc/network/interfaces"
RESOLV_CONF = "/etc/resolv.conf"
#: איפה יושב סמן "ממתין לאישור" והפירורים שזרוע ההחזרה משאירה (‏#56).
STATE_DIR = "/var/lib/imagectl/netcfg"
#: היחידה שמחזירה הגדרה שלא אושרה — היא, ולא השרת, היא ההגנה.
ROLLBACK_TIMER = "imagectl-netrollback.timer"


def conf_path(name: str, root: str | Path = INTERFACES_DIR) -> Path:
    return Path(root) / netcfg.conf_name(name)


# --- כתיבה -------------------------------------------------------------------


def read_conf(name: str, root: str | Path = INTERFACES_DIR) -> str | None:
    """הקובץ כפי שהוא עכשיו, או None אם אינו קיים.

    ‏None אינו "ריק": זה מה שנשמר בסמן ההחזרה כדי שהחזרה תמחק את הקובץ
    במקום להשאיר אותו ריק ומנוהל.
    """
    try:
        return conf_path(name, root).read_text(encoding="utf-8")
    except OSError:
        return None


def write_conf(name: str, text: str | None,
               root: str | Path = INTERFACES_DIR) -> str | None:
    """כותב (או מוחק, כש-`text is None`) את הקובץ. מחזיר שגיאה או None."""
    path = conf_path(name, root)
    try:
        if text is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def write_resolv(text: str, path: str | Path = RESOLV_CONF) -> str | None:
    try:
        Path(path).write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def sourced(main: str | Path = INTERFACES_MAIN,
            root: str | Path = INTERFACES_DIR) -> bool | None:
    """האם `/etc/network/interfaces` בכלל טוען את `interfaces.d`.

    בלי זה כל מה שנכתב שם הוא קובץ שאיש לא קורא — הצורה הנקייה ביותר
    של "כתבנו ולא קרה כלום". ‏None = לא הצלחנו לקרוא את הקובץ הראשי,
    וזה **אינו** "כן".
    """
    try:
        text = Path(main).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    marker = str(root).rstrip("/")
    return any(line.strip().startswith("source") and marker in line
               for line in text.splitlines())


# --- החלה --------------------------------------------------------------------


def _run(cmd: list[str], timeout: int = 40) -> tuple[int | None, str]:
    try:
        done = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False,
                              stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return done.returncode, (done.stderr or done.stdout or "").strip()


def apply_interface(name: str) -> str | None:
    """‏`ifdown` ואז `ifup`. מחזיר הודעת שגיאה או None.

    ‏`ifdown` על כרטיס שאינו מוגדר כרגע יוצא בשגיאה לגיטימית ("not
    configured"), ולכן קוד היציאה שלו אינו קובע — אבל הוא גם אינו
    נזרק: הוא נכנס להודעה שחוזרת לקונסולה, כדי ש"הורדנו ולא ירד" לא
    ייעלם. ‏`ifup` הוא זה שכישלון שלו הוא שגיאה.

    ‏**None כאן פירושו "הפקודות רצו", ולא "הכתובת השתנתה".** את זה
    קובעת רק `read_state()`.
    """
    down_code, down_text = _run(["ifdown", "--force", name])
    up_code, up_text = _run(["ifup", name])
    if up_code is None:
        return f"{name}: לא ניתן להריץ ifup ({up_text})"
    if up_code != 0:
        note = ""
        if down_code not in (0, None):
            note = f" (ה-ifdown שלפניו החזיר {down_code}: {down_text[:120]})"
        return f"{name}: ifup החזיר {up_code}: {up_text[:200]}{note}"
    return None


def timer_active(unit: str = ROLLBACK_TIMER) -> tuple[bool, str]:
    """האם זרוע ההחזרה מותקנת ורצה — ‏(פעילה, מה נקרא).

    שלושה מצבים מקופלים לשניים בכוונה **לצד הבטוח**: כל דבר שאינו
    ‏`active` מדויק, כולל "לא הצלחנו להריץ systemctl", מחזיר False.
    ההגנה שלא הוכחה אינה הגנה, ובלעדיה אסור להחיל שינוי שיכול לנתק.
    """
    code, text = _run(["systemctl", "is-active", unit], timeout=10)
    if code is None:
        return False, f"‏systemctl לא רץ ({text[:120]})"
    return text.strip() == "active", text.strip() or f"קוד {code}"


# --- הראיה: מה באמת מוגדר עכשיו ----------------------------------------------


@dataclass(frozen=True)
class LiveState:
    """מה שנקרא מהמכונה — או למה לא נקרא.

    ‏`checked=False` אינו "אין כתובות". הוא "לא שאלנו", והוא לעולם אינו
    נחשב התאמה.
    """

    checked: bool
    addresses: dict[str, list[str]] = field(default_factory=dict)
    routes: list[str] = field(default_factory=list)
    nameservers: list[str] = field(default_factory=list)
    reason: str = ""


def parse_addr(text: str) -> dict[str, list[str]]:
    """פלט `ip -4 -o addr show` → {כרטיס: ["10.44.9.10/24", …]}."""
    found: dict[str, list[str]] = {}
    for line in text.splitlines():
        fields = line.split()
        # ‎"2: eth0    inet 10.44.9.10/24 brd … scope global eth0"
        if len(fields) < 4 or fields[2] != "inet":
            continue
        found.setdefault(fields[1], []).append(fields[3])
    return found


def parse_routes(text: str) -> list[str]:
    """פלט `ip -4 route show` → שורות מנורמלות, ‏"<יעד> via <שער>".

    ‏`default` נשמר כפי שהוא: זה השער, וזו הצורה שבה מחפשים אותו.
    """
    found = []
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        destination = fields[0]
        via = fields[fields.index("via") + 1] if "via" in fields else ""
        found.append(f"{destination} via {via}" if via else destination)
    return found


def parse_resolv(text: str) -> list[str]:
    return [line.split()[1] for line in text.splitlines()
            if line.split()[:1] == ["nameserver"] and len(line.split()) > 1]


def read_state(resolv: str | Path = RESOLV_CONF) -> LiveState:
    """‏`ip addr`, ‏`ip route` ו-`/etc/resolv.conf` — המצב בפועל.

    כל שלושת המקורות חייבים להיקרא. אחד שנכשל מחזיר `checked=False`
    עם הסיבה: מצב שנקרא חלקית הוא בדיוק המקום שבו "לא בדקנו" מתחפש
    ל"בדקנו, הכל תקין".
    """
    problems = []
    addr_code, addr_text = _run(["ip", "-4", "-o", "addr", "show"], timeout=10)
    if addr_code != 0:
        problems.append(f"‏ip addr: {addr_text[:120] or 'לא רץ'}")
    route_code, route_text = _run(["ip", "-4", "route", "show"], timeout=10)
    if route_code != 0:
        problems.append(f"‏ip route: {route_text[:120] or 'לא רץ'}")
    try:
        resolv_text = Path(resolv).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        resolv_text = ""
        problems.append(f"{resolv}: {exc.strerror or exc}")
    if problems:
        return LiveState(False, reason=" · ".join(problems))
    return LiveState(True, parse_addr(addr_text), parse_routes(route_text),
                     parse_resolv(resolv_text))


# --- השוואה: מה ביקשנו מול מה שנקרא ------------------------------------------


def _same_address(wanted: str, seen: str) -> bool:
    try:
        return ipaddress.IPv4Interface(wanted) == ipaddress.IPv4Interface(seen)
    except (ipaddress.AddressValueError, ValueError):
        return wanted.strip() == seen.strip()


def mismatches(cfg: netcfg.NetConfig, state: LiveState) -> list[str]:
    """מה שביקשנו ולא נמצא בקריאה החוזרת. רשימה ריקה = ראיה חיובית.

    ‏`checked=False` מחזיר סיבה אחת ולא רשימה ריקה — "לא ידוע" נופל
    לצד הכושל, תמיד.
    """
    if not state.checked:
        return [f"המצב בפועל לא נקרא ({state.reason}) — אי אפשר לאשר שהשינוי תפס"]
    if not cfg.managed:
        return []
    found = []
    live = state.addresses.get(cfg.name, [])
    if cfg.mode == netcfg.MODE_STATIC:
        if not any(_same_address(cfg.cidr, seen) for seen in live):
            found.append(f"‏{cfg.name} אינו נושא את {cfg.cidr} "
                         f"(‏ip addr מראה: {', '.join(live) or 'כלום'})")
        if cfg.gateway and f"default via {cfg.gateway}" not in state.routes:
            found.append(f"שער ברירת המחדל {cfg.gateway} אינו בטבלת הניתוב")
        for route in cfg.routes:
            if f"{route.cidr} via {route.gateway}" not in state.routes:
                found.append(f"הנתיב {route.cidr} דרך {route.gateway} "
                             "אינו בטבלת הניתוב")
    elif not live:
        found.append(f"‏{cfg.name} לא קיבל שום כתובת מ-DHCP")
    for server in cfg.dns:
        if server not in state.nameservers:
            found.append(f"‏{server} אינו ב-{RESOLV_CONF}")
    return found


def default_hooks() -> dict:
    """מה שמוזרק ב-`create_app`. בבדיקות כל אלה מוחלפים, ולכן שום בדיקה
    אינה כותבת קובץ רשת ואינה מריצה `ifup` — אותה תבנית של `dhcp_hooks`."""
    return {
        "netcfg_read_conf": read_conf,
        "netcfg_write_conf": write_conf,
        "netcfg_write_resolv": write_resolv,
        "netcfg_apply": apply_interface,
        "netcfg_state": read_state,
        "netcfg_sourced": sourced,
        "netcfg_timer_active": timer_active,
    }
