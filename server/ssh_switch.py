"""שתי דלתות ה-SSH, וההבדל בין "מה ביקשנו" ל"מה באמת מאזין" (‏#83).

שתי דלתות נפרדות, ולשתיהן לא היה מתג ולא היה חיווי:

* **התחנות** — ‏`imagectl.debug=1` בשורת הקרנל פותח מעטפת טכנאי *וגם*
  ‏dropbear בכל מכונה שעולה. עד כאן זה הגיע ממשתנה סביבה על השרת.
* **השרת עצמו** — ‏`sshd` על ‎0.0.0.0 מאזין גם בצד וילן הכיתות.

הכלל היחיד שחשוב כאן: **ההגדרה אינה המצב.** מתג שנכשל ומציג "כבוי"
הוא המצב המסוכן ביותר — מפעיל שמאמין שסגר ולא סגר. לכן כל מה שהמסך
מציג מגיע מקריאה חוזרת של המצב בפועל: טבלת הסוקטים של הקרנל לשרת,
וטקסט התפריט שהשרת באמת מגיש לתחנות. ההגדרה משמשת רק לשני דברים —
להחליט מה *לנסות*, ולהשוות מול מה שנקרא בחזרה.

שלושה מצבים, לא שניים (אותו לקח של ‏`ProbeResult` ב-#53): מאזין ·
לא מאזין · **לא ידוע**. ‏"לא ידוע" אינו "סגור", והוא נצבע אדום —
הדלת שאי אפשר לראות היא גרועה מדלת פתוחה שיודעים עליה.
"""

from __future__ import annotations

import ipaddress
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .db import get_setting

#: המתג של התחנות (שער `imagectl.debug`), והמתג של השרת לכל ממשק.
STATION_KEY = "ssh:stations"
IFACE_PREFIX = "ssh:iface:"

#: הפרמטר עצמו. כשהמתג כבוי הוא אינו נכנס לשורת הקרנל — גם אם המפעיל
#: העביר אותו ב-`--extra-cmdline`, כי אחרת היו שני מקורות אמת והישן
#: היה גובר בשקט.
DEBUG_PARAM = "imagectl.debug=1"
DEBUG_PREFIX = "imagectl.debug"

SSH_PORT = 22
#: ‏Debian מכניס `Include /etc/ssh/sshd_config.d/*.conf` בראש הקובץ.
#: ‏ListenAddress הוא מצטבר: אם הקובץ הראשי מכריז אחד משלו, הדלת שלנו
#: לא באמת נסגרת — ובדיוק בשביל זה יש קריאה חוזרת.
SSHD_DROP_IN = "/etc/ssh/sshd_config.d/imagectl-ssh.conf"
SSHD_UNIT = "ssh"
#: כשאף ממשק אינו פתוח, ‏sshd עדיין חייב כתובת אחת — בלי ‏ListenAddress
#: בכלל הוא חוזר לברירת המחדל שלו, שהיא *כל* הממשקים. לולאה מקומית
#: אינה על אף וילן, ומשאירה דרך פנימה למי שכבר על המכונה.
LOOPBACK = "127.0.0.1"

Hooks = dict[str, Callable]


# --- ההגדרה (מה ביקשנו) ------------------------------------------------------


def _flag(conn, key: str) -> bool:
    """הגדרה שאי אפשר לקרוא או להבין = כבוי. כשל סוגר דלת, לא פותח."""
    try:
        raw = get_setting(conn, key)
    except Exception:                                  # noqa: BLE001 — כוונה
        return False
    if not raw:
        return False
    try:
        return json.loads(raw).get("enabled") is True
    except (ValueError, AttributeError):
        return False


def stations_enabled(conn) -> bool:
    return _flag(conn, STATION_KEY)


def interface_enabled(conn, name: str) -> bool:
    return _flag(conn, IFACE_PREFIX + name)


def enabled_interfaces(conn) -> list[str]:
    try:
        rows = conn.execute(
            "SELECT key FROM settings WHERE key LIKE ?", (IFACE_PREFIX + "%",)
        ).fetchall()
    except Exception:                                  # noqa: BLE001 — כוונה
        return []
    names = [r["key"][len(IFACE_PREFIX):] for r in rows]
    return sorted(n for n in names if interface_enabled(conn, n))


def flag_json(enabled: bool) -> str:
    return json.dumps({"enabled": bool(enabled)})


def station_cmdline(extra: tuple[str, ...], enabled: bool) -> tuple[str, ...]:
    """שורת הקרנל בפועל: כל התוספות של המפעיל **חוץ** מדגל הניפוי,
    ואחריהן הדגל — אך ורק אם המתג בקונסולה דלוק."""
    kept = tuple(p for p in extra if not p.startswith(DEBUG_PREFIX))
    return kept + ((DEBUG_PARAM,) if enabled else ())


# --- הראיה (מה באמת מאזין) ---------------------------------------------------


@dataclass(frozen=True)
class Listeners:
    """מה שנקרא מטבלת הסוקטים של הקרנל — או למה לא נקרא.

    ‏`checked=False` אינו "אין מאזינים". הוא "לא שאלנו", וזה מצב אדום.
    """

    checked: bool
    addresses: tuple[str, ...] = ()
    wildcard: bool = False
    reason: str = ""


#: ‏TCP_LISTEN בטבלה של הקרנל.
_LISTEN = "0A"
_WILDCARD = {"0.0.0.0", "::"}


def _address(hex_part: str) -> str | None:
    """‎"0100007F" → 127.0.0.1. הקרנל כותב כל מילה של 4 בתים הפוכה."""
    try:
        raw = bytes.fromhex(hex_part)
    except ValueError:
        return None
    if len(raw) == 4:
        return str(ipaddress.IPv4Address(raw[::-1]))
    if len(raw) == 16:
        return str(ipaddress.IPv6Address(
            b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))))
    return None


def parse_proc_net_tcp(text: str, port: int = SSH_PORT) -> tuple[bool, list[str]]:
    """‏(האם הטבלה נקראה, הכתובות שמאזינות על הפורט).

    שורת הכותרת היא הראיה שקראנו טבלה אמיתית. מחרוזת ריקה — קובץ חסר,
    הרשאה חסרה, `ss` שלא רץ — מחזירה `False`, ולא "אין מאזינים". זה
    בדיוק ההבדל שבין ‎http=000 לבין 200.
    """
    lines = text.splitlines()
    if not lines or "local_address" not in lines[0]:
        return False, []
    suffix = ":%04X" % port
    found = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 4 or fields[3] != _LISTEN:
            continue
        local = fields[1]
        if not local.upper().endswith(suffix):
            continue
        address = _address(local.rsplit(":", 1)[0])
        if address is not None:
            found.append(address)
    return True, found


def read_listeners(proc_net: str | Path = "/proc/net",
                   port: int = SSH_PORT) -> Listeners:
    """טבלת הסוקטים של הקרנל — ‏IPv4 ו-IPv6. בלי `ss` ובלי root."""
    root = Path(proc_net)
    checked = False
    addresses: list[str] = []
    problems = []
    for name in ("tcp", "tcp6"):
        try:
            text = (root / name).read_text()
        except OSError as exc:
            problems.append(f"{name}: {exc.strerror or exc}")
            continue
        ok, found = parse_proc_net_tcp(text, port)
        if not ok:
            problems.append(f"{name}: לא נראה כמו טבלת סוקטים")
            continue
        checked = True
        addresses.extend(found)
    if not checked:
        return Listeners(False, reason=" · ".join(problems) or "אין /proc/net")
    return Listeners(True, tuple(sorted(set(addresses))),
                     any(a in _WILDCARD for a in addresses))


# --- מיפוי כתובת → ממשק ------------------------------------------------------


def bare_address(address: str) -> str:
    """‎"10.10.10.8/24" → "10.10.10.8"."""
    return address.split("/")[0].strip()


def exposure(listeners: Listeners, interfaces: list[dict]) -> dict[str, bool]:
    """אילו ממשקים באמת חשופים ל-SSH, לפי הראיה בלבד.

    ‏0.0.0.0 פירושו *כל* ממשק — כולל וילן הכיתות. זה המצב שבו שרת
    המעבדה נמצא, וזה מה שאף מסך לא אמר עד היום.
    """
    if not listeners.checked:
        return {}
    if listeners.wildcard:
        return {nic["name"]: True for nic in interfaces}
    live = set(listeners.addresses)
    return {
        nic["name"]: any(bare_address(a) in live for a in nic.get("addresses") or [])
        for nic in interfaces
    }


def stray_addresses(listeners: Listeners, interfaces: list[dict]) -> list[str]:
    """כתובות שמאזינות ואינן של אף ממשק מוכר (ולא לולאה מקומית)."""
    if not listeners.checked or listeners.wildcard:
        return []
    known = {bare_address(a) for nic in interfaces for a in nic.get("addresses") or []}
    return [a for a in listeners.addresses
            if a not in known and not _loopback(a)]


def _loopback(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_loopback
    except ValueError:
        return False


# --- החלה על המכונה ----------------------------------------------------------


def render_sshd_conf(addresses: list[str]) -> str:
    """קובץ ה-drop-in. ריק מכתובות = לולאה מקומית בלבד, לא "ברירת מחדל"."""
    lines = [
        "# ImageCtl -- managed from the console (issue #83). Do not edit by hand.",
        "# Every line here is re-generated on each change; the console verifies",
        "# the result by reading the kernel socket table back.",
    ]
    for address in addresses or [LOOPBACK]:
        lines.append(f"ListenAddress {address}")
    if not addresses:
        lines.append("# no VLAN-facing interface is switched on")
    return "\n".join(lines) + "\n"


def apply_sshd(text: str, path: str = SSHD_DROP_IN,
               unit: str = SSHD_UNIT) -> str | None:
    """כותב את ה-drop-in ומבקש מ-sshd לקרוא אותו מחדש.

    ‏reload ולא restart: ‏sshd מבצע re-exec, מחבר מחדש את הסוקטים,
    וחיבורים קיימים שורדים. מנהל שסוגר את הדלת שהוא עומד בה לא מנותק
    באותו רגע — יש לו את הקונסולה (‏HTTP, ‏פורט אחר) כדי לפתוח שוב.

    מחזיר הודעת שגיאה, או None. ‏None כאן פירושו "הפקודות הצליחו",
    ‏**לא** "הדלת נסגרה" — את זה קובעת רק הקריאה החוזרת.
    """
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="ascii")
    except OSError as exc:
        return f"כתיבת {path} נכשלה: {exc.strerror or exc}"
    try:
        done = subprocess.run(["systemctl", "reload-or-restart", unit],
                              capture_output=True, text=True, timeout=20,
                              check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"טעינת {unit} מחדש נכשלה: {exc}"
    if done.returncode != 0:
        return (f"systemctl reload-or-restart {unit} החזיר "
                f"{done.returncode}: {(done.stderr or '').strip()[:200]}")
    return None


def default_hooks() -> Hooks:
    return {
        "listeners": read_listeners,
        "apply_sshd": apply_sshd,
        # השהיה קצרה בין ההחלה לקריאה החוזרת. מוזרקת, כדי שהבדיקות
        # לא יישנו ולא ייגעו בשעון.
        "settle": lambda: time.sleep(0.5),
    }
