"""‏DHCP — כל מה שנוגע במכונה עצמה (אפיון סעיף 24).

‏dhcp.py הוא הלוגיקה הטהורה: מקבל הגדרות, מחזיר טקסט. כאן יושב הצד
המלוכלך — לקרוא אילו כרטיסים קיימים, לשאול אם מישהו כבר מחלק כתובות,
לכתוב את קבצי ה-conf ולהפעיל שירותים. הפרדה זו גם מה שמאפשר לבדיקות
להזריק `dhcp_hooks` ולעולם לא לגעת במכונה.

שני יעדים, בכוונה (‏#36):

- `DEFAULT_CONF` — בתוך `/etc/dnsmasq.d`, נטען לאינסטנס הראשי של
  dnsmasq (ה-DHCP של וילן ההפצה).
- `PROXY_CONF` — **מחוץ** ל-`/etc/dnsmasq.d`, כדי שהאינסטנס הראשי לא
  יטען אותו. הוא נטען ביחידה `imagectl-proxy` בלבד. ‏dnsmasq 2.91 קופא
  על בקשת PXE לפורט 4011 במצב proxy, וכל עוד זה כך — הקפיאה חייבת
  להישאר בתהליך שאפשר לאבד.
"""

from __future__ import annotations

import re
import secrets
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONF = "/etc/dnsmasq.d/imagectl-dhcp.conf"
PROXY_CONF = "/etc/imagectl/dnsmasq-proxy.conf"
PROXY_UNIT = "imagectl-proxy"


# --- מה יש במכונה -----------------------------------------------------------


def list_interfaces(sys_root: str | Path = "/sys/class/net") -> list[dict]:
    """כרטיסי הרשת הפיזיים והווירטואליים, בלי loopback. נקרא מ-/sys כדי
    שלא יהיה צורך ב-`ip` בזמן הבדיקות; הכתובות נמשכות בנפרד."""
    root = Path(sys_root)
    found = []
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        if entry.name == "lo":
            continue
        try:
            state = (entry / "operstate").read_text().strip()
        except OSError:
            state = "unknown"
        try:
            mac = (entry / "address").read_text().strip()
        except OSError:
            mac = ""
        found.append({"name": entry.name, "state": state, "mac": mac,
                      "addresses": _addresses(entry.name)})
    return found


def _addresses(name: str) -> list[str]:
    try:
        out = subprocess.run(
            ["ip", "-4", "-o", "addr", "show", "dev", name],
            capture_output=True, text=True, timeout=3, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.split()[3] for line in out.splitlines() if len(line.split()) > 3]


# --- מי כבר עונה ל-DHCP כאן? ------------------------------------------------


@dataclass(frozen=True)
class ProbeResult:
    """תוצאת ה-DHCPDISCOVER על ממשק — **שלושה** מצבים, לא שניים.

    - ‏`checked=False` — הבדיקה לא רצה בכלל. זה *לא* "נקי": שום דבר לא
      נשלל, ואסור לתת לזה לפתוח את ההדלקה.
    - ‏`checked=True` עם `servers` ריק — שאלנו, ואיש לא ענה.
    - ‏`servers` — מי ענה.

    האובייקט אמיתי (truthy) תמיד, בכוונה: קודם הוחזרו `None`/`[]`/רשימה,
    ו-`if found:` קיפל את שני הראשונים לאחד — ככה אפשר היה להדליק DHCP
    על ה-trunk של המכללה כשהבדיקה רק *נכשלה* ‏(#53). עכשיו `if` מקרי
    נופל לצד החוסם, לא לצד המדליק.
    """

    checked: bool
    servers: tuple[str, ...] = ()


def probe_existing_dhcp(iface: str, timeout: float = 2.0) -> ProbeResult:
    """שולח DHCPDISCOVER על הממשק ומחזיר מי ענה — ואם בכלל הצלחנו לשאול.

    בלי הרשאות או כשפורט 68 תפוס (לקוח DHCP על הכרטיס) מוחזר
    `ProbeResult(checked=False)`. זה מה שמונע את התקלה הגרועה ביותר:
    להדליק DHCP שני על רשת שכבר יש בה.
    """
    xid = secrets.token_bytes(4)
    try:
        mac = bytes.fromhex(
            (Path("/sys/class/net") / iface / "address").read_text().strip().replace(":", "")
        )
    except (OSError, ValueError):
        mac = b"\x02" + secrets.token_bytes(5)          # locally administered, random
    packet = (
        b"\x01\x01\x06\x00"           # op=BOOTREQUEST, htype=ethernet, hlen=6, hops
        + xid
        + b"\x00\x00"                   # secs
        + b"\x80\x00"                   # flags: broadcast -- answer where we can hear it
        + b"\x00" * 16                   # ciaddr, yiaddr, siaddr, giaddr
        + mac.ljust(16, b"\x00")         # chaddr
        + b"\x00" * 64 + b"\x00" * 128  # sname, file
        + b"\x63\x82\x53\x63"           # magic cookie
        + b"\x35\x01\x01"               # option 53: DHCPDISCOVER
        + b"\xff"
    )
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, 25, iface.encode() + b"\0")  # SO_BINDTODEVICE
        sock.bind(("", 68))
        sock.settimeout(timeout)
        sock.sendto(packet, ("255.255.255.255", 67))
    except OSError:
        return ProbeResult(checked=False)
    seen: list[str] = []
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            if len(data) > 8 and data[4:8] == xid and addr[0] not in seen:
                seen.append(addr[0])
    except socket.timeout:
        pass
    except OSError:
        # שגיאה באמצע ההאזנה: מי שכבר ענה נספר, אבל שקט כאן אינו ראיה.
        return ProbeResult(checked=bool(seen), servers=tuple(seen))
    finally:
        sock.close()
    return ProbeResult(checked=True, servers=tuple(seen))


# --- האם מצב proxy בטוח בגרסת dnsmasq שמותקנת כאן? (#36) --------------------

#: הגרסה שבה הקפיאה שוחזרה במעבדה — ארבע פעמים, בארבעה וריאנטים של
#: התצורה, ולכן זו לא שגיאה בתחביר שאנחנו מייצרים.
PROXY_BROKEN = ("2.91",)

#: הגרסאות שבהן מצב proxy נבדק **בפועל** מול תחנת UEFI ועבד. ריקה
#: בכוונה: אף גרסה עוד לא עברה את הבדיקה הזו. כשאחת תעבור — מוסיפים
#: אותה כאן, וההגנה נפתחת מעצמה בלי לגעת בשום מקום אחר. אסור למלא
#: את הרשימה לפי CHANGELOG: היעדר אזכור של באג אינו ראיה לתיקון,
#: והרשימה הזו היא רשימת *ראיות חיוביות* (עיקרון 5).
PROXY_VERIFIED: tuple[str, ...] = ()

_VERSION_LINE = re.compile(r"version\s+(\d+\.\d+(?:\.\d+)?)", re.IGNORECASE)


@dataclass(frozen=True)
class ProxySupport:
    """האם מותר להדליק proxy בלי אישור מפורש — **שלושה** מצבים, לא שניים.

    בדיוק כמו `ProbeResult` (‏#53): ‏`read=False` הוא "לא הצלחנו לקרוא
    את הגרסה", ולא "הגרסה תקינה". רק `verified` פותח, והוא דורש ראיה
    חיובית — גרסה שנקראה ונמצאת ב-`PROXY_VERIFIED`. האובייקט truthy
    תמיד, בכוונה, כדי ש-`if support:` מקרי לא ייקרא כאישור.
    """

    read: bool
    version: str = ""

    @property
    def verified(self) -> bool:
        return self.read and self.version in PROXY_VERIFIED

    @property
    def broken(self) -> bool:
        return self.read and self.version in PROXY_BROKEN

    def reason(self) -> str:
        """למה חוסמים — האמת, לא "לא זמין". מפעיל שלא יודע *למה* יעקוף."""
        if self.broken:
            head = (f"‏dnsmasq {self.version} המותקן כאן נתקע על בקשת PXE "
                    "לפורט 4011 במצב proxy: ‏100% מעבד, מפסיק לענות לכל "
                    "הסוקטים, ולא מגיב ל-SIGTERM. באג upstream, שוחזר "
                    "במעבדה (‏#36).")
        elif self.read:
            head = (f"‏dnsmasq {self.version} המותקן כאן לא נבדק במצב proxy. "
                    "הקפיאה של #36 שוחזרה ב-2.91, ואין ראיה חיובית שהיא "
                    "תוקנה בגרסה הזו — \"לא נבדק\" אינו \"תקין\".")
        else:
            head = ("לא ניתן לקרוא את גרסת dnsmasq המותקנת — הבדיקה עצמה "
                    "לא רצה. זה \"לא יודעים\", ולא \"הגרסה תקינה\".")
        return (head + " ה-proxy רץ באינסטנס נפרד (imagectl-proxy), ולכן "
                "קפיאה שלו לא מורידה את ה-DHCP של וילן ההפצה — אבל אתחול "
                "רשת דרך המצב הזה לא יעבוד. להדלקה בכל זאת, כבדיקה "
                "מכוונת: confirm_proxy_broken.")


def proxy_support(raw: str | None) -> ProxySupport:
    """מתרגם את פלט `dnsmasq --version` להחלטה. ‏None/זבל = לא נקרא."""
    match = _VERSION_LINE.search(raw or "")
    return ProxySupport(read=bool(match), version=match.group(1) if match else "")


def dnsmasq_version() -> str | None:
    """פלט `dnsmasq --version`, או None אם לא הצלחנו להריץ אותו.

    קריאה בלבד — לא נוגעת בשירות. ‏None אינו "אין בעיה": הקורא חייב
    להתייחס אליו כמו לגרסה שלא נבדקה.
    """
    try:
        result = subprocess.run(
            ["dnsmasq", "--version"], capture_output=True, text=True,
            timeout=5, check=False, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


# --- החלה -------------------------------------------------------------------


def apply(text: str, conf_path: str | Path = DEFAULT_CONF) -> str | None:
    """האינסטנס הראשי: כותב את הקובץ ומפעיל מחדש את dnsmasq.

    מחזיר הודעת שגיאה או None. כתיבה שנכשלת (הרשאות) לא מפילה את השרת:
    ההגדרה כבר נשמרה ב-DB, והודעה ברורה בקונסולה עדיפה על 500.
    """
    return _write(text, conf_path) or _systemctl("restart", "dnsmasq")


def apply_proxy(text: str, active: bool,
                conf_path: str | Path = PROXY_CONF) -> str | None:
    """אינסטנס ה-proxy: כותב את הקובץ הנפרד ומפעיל/עוצר את היחידה שלו.

    כשאף ממשק אינו במצב proxy היחידה נעצרת — תהליך dnsmasq שני שרץ
    על קובץ ריק הוא רק עוד משהו שיכול להיתקע. הכתיבה קודמת ל-systemctl,
    כך שכשל בכתיבה חוזר בלי לגעת בשירותים.
    """
    return _write(text, conf_path) or _systemctl(
        "restart" if active else "stop", PROXY_UNIT)


def _write(text: str, conf_path: str | Path) -> str | None:
    path = Path(conf_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        return f"לא ניתן לכתוב את {path}: {exc.strerror or exc}"
    return None


def _systemctl(action: str, unit: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", action, unit],
            capture_output=True, text=True, timeout=20, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{unit}: הפקודה systemctl {action} נכשלה ({exc})"
    if result.returncode != 0:
        return (f"{unit} לא הגיב ל-{action}: "
                f"{(result.stderr or result.stdout).strip()[:300]}")
    return None
