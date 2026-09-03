"""DHCP לכל כרטיס רשת — הלוגיקה הטהורה (אפיון סעיף 24).

השרת הוא גם שרת ה-DHCP של וילן ההפצה, וההגדרה נעשית מהקונסולה. זו
ההגדרה המסוכנת ביותר במערכת: DHCP שהודלק בטעות על הממשק שמחובר לרשת
המכללה משבית רשת שלמה. לכן כל מה שכאן נבנה סביב "כבוי אלא אם":

- ברירת המחדל לכל ממשק היא כבוי.
- הדלקה דורשת את שם הממשק באישור, וממשק שסומן כ-trunk דורש אישור נוסף.
- לפני הדלקה בודקים אם מישהו אחר כבר עונה ל-DHCP על אותו ממשק.

**שני קבצים, שני תהליכי dnsmasq** ‏(#36): ‏DHCP מלא נכתב לקובץ של
האינסטנס הראשי, ומצב proxy — לקובץ נפרד שרץ ביחידת systemd משלו
(`imagectl-proxy`). ‏dnsmasq 2.91 קופא ‏(100% CPU, מפסיק לענות לכל
הסוקטים) על בקשת PXE לפורט 4011 במצב proxy, ובאינסטנס משותף הקפיאה
הזו הורגת גם את ה-DHCP של וילן ההפצה. ההפרדה אינה מתקנת את הבאג — היא
מונעת ממנו להפיל רשת שלמה, עד שתיבדק גרסת dnsmasq חדשה יותר במעבדה.
וכיוון שהבאג עדיין שם, הדלקת proxy דורשת אישור מפורש ונפרד כל עוד
הגרסה המותקנת לא נבדקה — ‏`proxy_support()` ו-`PROXY_VERIFIED`
ב-`dhcp_host.py` (שם, ולא כאן, כדי שלא יהיה עותק שני שמתיישן).

המודול הזה לא נוגע ב-DB, ב-HTTP ובמכונה: הוא מקבל הגדרות, מאמת קלט
ומחזיר טקסט dnsmasq. מה שנוגע במכונה יושב ב-`dhcp_host.py` (ומיוצא
מכאן, כדי שיישאר מקום אחד לייבא ממנו); הראוטר ב-`console_dhcp.py`.
"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import asdict, dataclass, field

from .dhcp_host import (  # noqa: F401  — ה-API של המודול נשאר `dhcp.<שם>`
    DEFAULT_CONF,
    PROXY_CONF,
    PROXY_UNIT,
    ProbeResult,
    ProxySupport,
    apply,
    apply_proxy,
    dnsmasq_version,
    list_interfaces,
    probe_existing_dhcp,
    proxy_support,
)

SETTING_PREFIX = "dhcp:"
DEFAULT_LEASE = "12h"
#: שורש ה-TFTP שהמתקין פורס אליו (install/setup-boot-server.sh).
DEFAULT_TFTP_ROOT = "/srv/tftp"
#: קובץ חכירות משלו לאינסטנס ה-proxy — שני תהליכים על אותו קובץ מפסידים.
PROXY_LEASES = "/var/lib/imagectl/dnsmasq-proxy.leases"


@dataclass
class InterfaceConfig:
    """ההגדרה של ממשק אחד, כפי שהיא נשמרת בטבלת settings (JSON)."""

    name: str
    enabled: bool = False          # DHCP מלא — מחלק כתובות
    proxy: bool = False            # עונה על PXE בלבד, בלי לחלק כתובות
    trunk: bool = False            # מחובר לרשת המכללה — דורש אישור נוסף
    range_start: str = ""
    range_end: str = ""
    netmask: str = "255.255.255.0"
    gateway: str = ""
    dns: list[str] = field(default_factory=list)
    lease: str = DEFAULT_LEASE
    server_ip: str = ""            # כתובת השרת בוילן — next-server ו-TFTP

    @classmethod
    def from_json(cls, name: str, raw: str | None) -> "InterfaceConfig":
        if not raw:
            return cls(name=name)
        data = json.loads(raw)
        data.pop("name", None)
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(name=name, **known)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


# --- אימות ------------------------------------------------------------------


def _ip(value: str, label: str) -> ipaddress.IPv4Address:
    try:
        return ipaddress.IPv4Address(value.strip())
    except (ipaddress.AddressValueError, ValueError):
        raise ValueError(f"{label}: כתובת לא תקינה ({value!r})")


#: שם כרטיס — **רשימת-היתר**, כמו שם משתמש (‏#111), ומאותה סיבה: על
#: רשימת-איסור צריך לנחש נכון כל תו שדמון שרץ כ-root עושה בו משהו,
#: ועל רשימת-היתר צריך רק לדעת איך נראה שם כרטיס. 15 תווים הם
#: IFNAMSIZ של הקרנל — שם ארוך יותר לא יכול להתאים לכרטיס אמיתי.
_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,14}")

NAME_ERROR = ("שם כרטיס — אותיות/ספרות באנגלית ואחריהן . _ : או -, "
              "עד 15 תווים, למשל eth1.700")


def validate_name(name: str) -> None:
    """זורק ValueError בעברית על שם כרטיס שאסור לכתוב לקובץ dnsmasq.

    זה השדה היחיד בתצורה שנכתב לקובץ כטקסט חופשי, בלי מרכאות ובלי
    בריחה, ולכן שורה חדשה בתוכו מוסיפה לקובץ **הוראה שלמה** — למשל
    ‏`dhcp-range` שני על ממשק שאיש לא הגדיר — ורווח או `#` מפילים את
    dnsmasq בעלייה, כלומר את כל וילן ההפצה (‏#102).

    השם נאמר בחזרה למפעיל כ-`repr`, כדי שתו בלתי-נראה ייראה בהודעה.
    שם פסול אינו מנוקה בשקט: `eth 0` שהופך ל-`eth0` הוא ניחוש איזה
    כרטיס התכוונו אליו.
    """
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        shown = name if isinstance(name, str) else str(name)
        raise ValueError(f"{NAME_ERROR} — התקבל {shown[:40]!r}")


def validate(cfg: InterfaceConfig) -> None:
    """זורק ValueError בעברית על הגדרה שלא תעבוד.

    שדה שנכתב לקובץ נבדק **לפני** היציאה המוקדמת על ממשק כבוי: ממשק
    ב-proxy הוא `proxy and not enabled`, כלומר הוא נכנס ל-`render_proxy`
    בדיוק דרך הענף שדילג על הבדיקות כולן (‏#102). מה שכן נשאר מאחורי
    היציאה הוא רק מה ש-`render` לבדו כותב — טווח, מסכה, שער, חכירה —
    כי לממשק שאינו מחלק כתובות אין להם משמעות.
    """
    validate_name(cfg.name)
    if cfg.server_ip:
        # ‏`render_proxy` כותב אותה כ-`dhcp-range=set:...,<server_ip>,proxy`.
        _ip(cfg.server_ip, "כתובת השרת")
    if not cfg.enabled:
        return
    start = _ip(cfg.range_start, "תחילת הטווח")
    end = _ip(cfg.range_end, "סוף הטווח")
    mask = _ip(cfg.netmask, "מסכת רשת")
    server = _ip(cfg.server_ip, "כתובת השרת")
    if start > end:
        raise ValueError("תחילת הטווח גדולה מסופו")
    network = ipaddress.IPv4Network(f"{start}/{mask}", strict=False)
    if end not in network:
        raise ValueError("סוף הטווח מחוץ לרשת שהמסכה מגדירה")
    if server not in network:
        raise ValueError("כתובת השרת אינה ברשת של הטווח")
    if start <= server <= end:
        raise ValueError("כתובת השרת נמצאת בתוך הטווח שמחולק")
    if cfg.gateway:
        if _ip(cfg.gateway, "שער") not in network:
            raise ValueError("השער אינו ברשת של הטווח")
    for entry in cfg.dns:
        _ip(entry, "DNS")
    lease = cfg.lease.strip().lower()
    if not lease or lease[-1] not in "mhd" or not lease[:-1].isdigit():
        raise ValueError("זמן חכירה: מספר ואחריו m/h/d, למשל 12h")


# --- רינדור dnsmasq ---------------------------------------------------------


def full_dhcp(configs: list[InterfaceConfig]) -> list[InterfaceConfig]:
    """הממשקים שמחלקים כתובות — האינסטנס הראשי."""
    return [c for c in configs if c.enabled]


def proxy_only(configs: list[InterfaceConfig]) -> list[InterfaceConfig]:
    """הממשקים שעונים על PXE בלבד — האינסטנס הנפרד. ‏enabled גובר, כדי
    ששום ממשק לא יופיע בשני הקבצים גם אם ה-DB מכיל את שניהם."""
    return [c for c in configs if c.proxy and not c.enabled]


#: אופציה 93: הקושחה מצהירה מי היא, והטוען נבחר לפי זה — ‏UEFI מקבל את
#: ה-shim החתום, ‏Legacy BIOS ‏(מחשבי השיכפול, ‏#38) מקבל GRUB ‏i386-pc.
#: שניהם קוראים את אותו grub.cfg — מחולל תפריט אחד. נדרש בשני הקבצים.
_ARCH_MATCH = [
    "dhcp-match=set:bios,option:client-arch,0",
    "dhcp-match=set:efi-x86_64,option:client-arch,7",
    "dhcp-match=set:efi-x86_64,option:client-arch,9",
    "",
]


def _guard_names(configs: list[InterfaceConfig]) -> None:
    """השער האחרון לפני הקובץ שדמון root קורא.

    ‏`validate` חוסמת בכניסה, וזו אותה בדיקה מאותו מקום — לא כלל שני —
    על מה שנשלף מה-DB. רשומה שנכתבה לפני #102 לא תהפוך פה לקובץ שבור
    בשקט: הרינדור נכשל, והקורא (‏`apply_all`) מדווח למפעיל וליומן.
    """
    for cfg in configs:
        validate_name(cfg.name)


def _head(what: str) -> list[str]:
    return [f"# ImageCtl -- {what}, generated from the console (spec 24).",
            "# Do not edit by hand: the next change in the console rewrites it.",
            ""]


def render(configs: list[InterfaceConfig], tftp_root: str | None = None) -> str:
    """הקובץ של האינסטנס הראשי — ‏DEFAULT_CONF, בתוך /etc/dnsmasq.d.

    dnsmasq קורא את כל /etc/dnsmasq.d, וקובץ ההתקנה (imagectl.conf) כבר
    מגדיר את ה-TFTP ואת שורש ההגשה. הקובץ הזה מוסיף ממשקים וטווחים.

    ממשק במצב proxy לא מופיע כאן בשום צורה (‏#36) — גם לא כ-`interface`
    להגשת TFTP: הוא רץ בתהליך אחר, ושני תהליכים לא יכולים לתפוס את
    אותם פורטים על אותו כרטיס. `except-interface` מוציא אותו מפורשות,
    והוא גובר על כל השאר גם אם מישהו יוסיף `interface` בעתיד.
    """
    lines = _head("DHCP per interface")
    full, proxied = full_dhcp(configs), proxy_only(configs)
    _guard_names(full + proxied)
    if not full and not proxied:
        lines.append("# No interface has DHCP or proxy enabled.")
        return "\n".join(lines) + "\n"
    lines += ["bind-interfaces", ""]
    if proxied:
        lines.append("# Proxy interfaces belong to the imagectl-proxy instance (#36):")
        lines += [f"except-interface={c.name}" for c in proxied] + [""]
    if tftp_root:
        lines += ["enable-tftp", f"tftp-root={tftp_root}", ""]
    if not full:
        lines.append("# No interface hands out addresses -- TFTP only.")
        return "\n".join(lines) + "\n"
    lines += _ARCH_MATCH
    for cfg in full:
        tag = f"if-{cfg.name}"
        lines.append(f"# --- {cfg.name} ---")
        lines.append(f"interface={cfg.name}")
        lines.append(
            f"dhcp-range=set:{tag},{cfg.range_start},{cfg.range_end},"
            f"{cfg.netmask},{cfg.lease}"
        )
        if cfg.gateway:
            lines.append(f"dhcp-option=tag:{tag},option:router,{cfg.gateway}")
        if cfg.dns:
            lines.append(f"dhcp-option=tag:{tag},option:dns-server,{','.join(cfg.dns)}")
        lines.append(f"dhcp-option=tag:{tag},option:tftp-server,{cfg.server_ip}")
        lines.append(f"dhcp-boot=tag:{tag},tag:bios,grub/i386-pc/core.0,,{cfg.server_ip}")
        lines.append(f"dhcp-boot=tag:{tag},tag:efi-x86_64,bootx64.efi,,{cfg.server_ip}")
        lines.append("")
    return "\n".join(lines) + "\n"


def render_proxy(configs: list[InterfaceConfig],
                 tftp_root: str = DEFAULT_TFTP_ROOT) -> str:
    """הקובץ של אינסטנס ה-proxy — ‏PROXY_CONF, מחוץ ל-/etc/dnsmasq.d.

    מצב proxy הוא לרשת שיש בה DHCP קיים ואסור להתנגש בו: עונים על
    שאלות PXE בלבד, כתובות ממשיכות להגיע מהשרת הקיים. כאן זה גם התהליך
    שמותר לו למות: ‏dnsmasq 2.91 קופא על בקשת PXE:4011 ‏(#36).

    הקובץ עומד בפני עצמו — היחידה מריצה אותו עם `--conf-file`, בלי
    `/etc/dnsmasq.conf` ובלי `/etc/dnsmasq.d` — ולכן הוא מביא את הכל
    בעצמו: בלי DNS ‏(`port=0`), ‏TFTP משלו על הכרטיסים שלו בלבד, וקובץ
    חכירות נפרד. `bind-interfaces` בשני הקבצים הוא מה שמאפשר לשני
    התהליכים לחיות על אותה מכונה.
    """
    lines = _head("PXE proxy instance")
    active = proxy_only(configs)
    _guard_names(active)
    if not active:
        lines.append("# No interface is in proxy mode -- the unit stays stopped.")
        return "\n".join(lines) + "\n"
    lines += [
        "port=0",                                     # בלי DNS, בלי שאילתות
        "bind-interfaces",
        f"dhcp-leasefile={PROXY_LEASES}",
        "",
        "enable-tftp",
        f"tftp-root={tftp_root}",
        "",
    ]
    lines += _ARCH_MATCH
    for cfg in active:
        tag = f"if-{cfg.name}"
        lines.append(f"# --- {cfg.name} ---")
        lines.append(f"interface={cfg.name}")
        # ל-pxe-service לא נמסרת כאן כתובת שרת (השדה האחרון, שהוא רשות):
        # כשיש רק שם קובץ, dnsmasq מגיש אותו מה-TFTP שלו עצמו ושם ב-siaddr
        # את כתובתו שלו על הממשק שדרכו הגיעה הבקשה. זו בדיוק הכתובת
        # שהתחנה יכולה להגיע אליה מהרשת שלה — וממנה GRUB ממלא את
        # net_default_server, שהוא הכתובת הראשית ב-grub.cfg (‎#37).
        # כתובת מפורשת כאן הייתה מקבעת את וילן ההפצה ושוברת בדיוק את זה.
        # שם הקובץ נכתב עם הסיומת בכוונה: ל-basename בלי סיומת dnsmasq
        # מוסיף את מספר השכבה (".0"), ולשם מלא — לא.
        subnet = cfg.server_ip or "0.0.0.0"
        lines.append(f"dhcp-range=set:{tag},{subnet},proxy")
        lines.append(
            f'pxe-service=tag:{tag},tag:bios,x86PC,"ImageCtl",grub/i386-pc/core.0'
        )
        lines.append(
            f'pxe-service=tag:{tag},tag:efi-x86_64,x86-64_EFI,"ImageCtl",bootx64.efi'
        )
        lines.append("")
    return "\n".join(lines) + "\n"
