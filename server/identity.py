"""זהות מכונה — ‏MAC מוצהר **וגם** כתובת מקור שתואמת את חכירת ה-DHCP (‏#855).

ה-API של הסוכן זיהה מכונה לפי ה-MAC שהפונה הצהיר עליו. בוילן ההפצה
זה נתן לכל מכונה את מה שמגיע לכל מכונה אחרת: אסימון הקליטה של מחשב
הבנייה (‏hello עם ה-MAC שלו), הצטרפות לסבב בשם משכפל, ודיווח בשם חבר
סבב. הסוכן ב-initramfs חסר-מצב ואין לו איפה להחזיק סוד — ולכן הראיה
היא **הרשת**: השרת הוא ה-DHCP של וילן ההפצה (‏#702), וכל מכונה שעלתה
ב-PXE קיבלה ממנו חכירה. ‏hello שמגיע מכתובת אחרת מזו שבחכירה של ה-MAC
שהוא מצהיר עליו — אינו המכונה.

שלושה מצבים, לא שניים (עיקרון 5):

- **תואם** — ממשיכים.
- **לא תואם** (`identity_refused`) — יש חכירה, והכתובת אחרת. 403.
- **לא ניתן לבדוק** (`identity_unverifiable`) — אין חכירה ל-MAC, או שקובץ
  החכירות לא נקרא (‏DHCP חיצוני, קובץ שנמחק, הרשאות). **גם זה 403**:
  "לא הצלחנו לבדוק" אינו "בדקנו, תקין". מי שמריץ DHCP חיצוני מכבה את
  המתג `identity_check` **בגלוי** מהקונסולה, וזה נרשם ביומן ונאמר
  במסך הבריאות.

זיוף MAC ברמת הכבל (מכונה שמשנה גם את ה-MAC וגם מבקשת חכירה עליו) הוא
port-security בסוויץ' — מחוץ להיקף (‏#702).

המודול טהור: אינו מכיר FastAPI. הצד שנוגע בקובץ (`LeaseFile`) מוזרק
דרך `identity_hooks` ב-`create_runtime`, כמו `dhcp_hooks`; ‏`server.main`
מתקין אותו תמיד, ובדיקות היחידה מזריקות קובץ משלהן.
"""

from __future__ import annotations

import ipaddress
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from boot.grub_menu import normalize_mac as lenient_mac

from .db import get_setting

#: המתג בטבלת `settings`. **חסר = דלוק**: הבדיקה כבויה רק כשמישהו כתב
#: "false" במפורש מהקונסולה (‏`setting_change` ביומן אומר מי).
SETTING = "identity_check"

#: קובץ החכירות של האינסטנס הראשי של dnsmasq בדביאן — ה-DHCP של וילן
#: ההפצה (‏`install/setup-boot-server.sh` אינו קובע `dhcp-leasefile`,
#: ולכן זו ברירת המחדל של החבילה). ‏`--dhcp-leases` ב-`server.main` מחליף.
DEFAULT_LEASES = "/var/lib/misc/dnsmasq.leases"

REFUSED = "identity_refused"
UNVERIFIABLE = "identity_unverifiable"


@dataclass(frozen=True)
class Lookup:
    """תוצאת חיפוש חכירה — **שלושה** מצבים, כמו `ProbeResult` (‏#53).

    ‏`read=False` — הקובץ לא נקרא בכלל; ‏`read=True, ip=None` — נקרא ואין
    חכירה ל-MAC; ‏`ip` — הכתובת שבחכירה. האובייקט truthy תמיד, בכוונה.
    """

    read: bool
    ip: str | None = None


def parse_leases(text: str) -> dict[str, str]:
    """‏`dnsmasq.leases` → ‏{mac קנוני: ip}. שורה: ‏`<פקיעה> <mac> <ip> <שם> <client-id>`.

    ‏dnsmasq כותב לאותו קובץ גם שורת `duid` ורשומות IPv6 (‏IAID במקום
    ‏MAC); שורה שאין בה MAC בעמודה השנייה או IPv4 בשלישית אינה חכירה
    שאפשר להשוות אליה ומדולגת. השורה **האחרונה** ל-MAC גוברת — זה גם
    הסדר שבו dnsmasq כותב את הקובץ מחדש.
    """
    leases: dict[str, str] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mac = lenient_mac(fields[1])
        if mac is None:
            continue
        try:
            ip = ipaddress.IPv4Address(fields[2])
        except (ipaddress.AddressValueError, ValueError):
            continue
        leases[mac] = str(ip)
    return leases


class LeaseFile:
    """מקור החכירות — קובץ dnsmasq. נקרא **בכל** בדיקה: הקובץ קטן (שורה
    למכונה), dnsmasq כותב אותו מחדש בכל חכירה, ומטמון היה מחזיר "אין
    חכירה" על מכונה שעלתה הרגע."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _read(self) -> dict[str, str] | None:
        try:
            return parse_leases(self.path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            return None

    def lookup(self, mac: str) -> Lookup:
        leases = self._read()
        if leases is None:
            return Lookup(read=False)
        return Lookup(read=True, ip=leases.get(mac))

    def count(self) -> int | None:
        """כמה חכירות בקובץ, או ``None`` כשאינו נקרא — למסך הבריאות."""
        leases = self._read()
        return None if leases is None else len(leases)


def enabled(conn: sqlite3.Connection) -> bool:
    """‏"false" מפורש מכבה; כל ערך אחר — כולל הגדרה חסרה — דלוק."""
    return get_setting(conn, SETTING) != "false"


@dataclass(frozen=True)
class Verdict:
    ok: bool
    event: str = ""      # ‏REFUSED / UNVERIFIABLE — שם האירוע ביומן וקוד השגיאה
    detail: str = ""     # מה שנכתב ליומן: ‏MAC מוצהר, כתובת מקור, כתובת בחכירה
    message: str = ""    # מה שהפונה מקבל ב-`error`


def _same_address(a: str, b: str) -> bool:
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        return a == b


def verify(conn: sqlite3.Connection, leases: LeaseFile,
           mac: str, client_ip: str | None) -> Verdict:
    """‏`mac` קנוני (הקורא נרמל); ‏`client_ip` — ה-peer של החיבור, לא כותרת."""
    if not enabled(conn):
        return Verdict(ok=True)
    if not client_ip:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac}: no source address on the connection",
                       message="machine identity could not be verified: no source address")
    lookup = leases.lookup(mac)
    if not lookup.read:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac} from {client_ip}: leases file {leases.path} not readable",
                       message="machine identity could not be verified: leases file not readable")
    if lookup.ip is None:
        return Verdict(ok=False, event=UNVERIFIABLE,
                       detail=f"{mac} from {client_ip}: no DHCP lease for this mac",
                       message="machine identity could not be verified: no DHCP lease for this mac")
    if not _same_address(lookup.ip, client_ip):
        return Verdict(ok=False, event=REFUSED,
                       detail=f"{mac} from {client_ip}, lease says {lookup.ip}",
                       message="source address does not match this mac's DHCP lease")
    return Verdict(ok=True)


def health_status(conn: sqlite3.Connection, leases: LeaseFile | None) -> tuple[str, str]:
    """‏(מצב, פירוט) לשורה "זהות מכונה" במסך הבריאות — לפי מה שנקרא
    בחזרה מהקובץ, לא לפי ההגדרה. ‏`health.collect` עוטף ב-`check`."""
    if leases is None:
        return "off", "לא מותקנת בתהליך הזה — אין מקור חכירות (הרצת בדיקות)"
    if not enabled(conn):
        return "warn", ("לא נבדקת — המתג identity_check כבוי (DHCP חיצוני); "
                        "כל MAC מוצהר מתקבל, מכל כתובת")
    count = leases.count()
    if count is None:
        return "bad", (f"המתג דלוק אבל {leases.path} לא נקרא — כל hello מסורב "
                       "(identity_unverifiable). לתקן את הקובץ, או לכבות את המתג בהגדרות")
    return "ok", f"נבדקת מול {leases.path} — {count} חכירות"
