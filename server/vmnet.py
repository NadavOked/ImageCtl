"""בידוד הרשת של ה-VM לעדכון (‏#1334) — חומת אש לפי המשתמש שמריץ את QEMU.

**האיום:** ‏Windows שמתעדכן מהאינטרנט הוא אורח לא-מהימן. ב-NAT של QEMU
(‏`-netdev user`, ‏slirp) כל חיבור של האורח הופך ל-socket של **תהליך QEMU
על המארח** — כלומר הוא מגיע לכל מה שהשרת מגיע אליו: ‏`10.0.2.2` ממופה
ל-loopback של המארח (הקונסולה, ה-API של הסוכן, הערוץ הבין-שרתי), ורשת
ההפצה וחדרי הכיתה נגישים דרך כרטיס ההפצה.

**הבידוד:** ‏QEMU רץ כמשתמש מערכת ייעודי (`imagectl-vm`, ‏`-run-with user=`),
ולכן כל תעבורה שהאורח מייצר יוצאת מהמארח עם ה-uid הזה — ואת ה-uid הזה
טבלת nftables נפרדת (`inet imagectl_vm`, שרשרת output) מגבילה:

* **מותר:** יציאה דרך **כרטיס הניהול בלבד** (הכרטיס שהקרנל בוחר לאינטרנט,
  ושנבדק שאינו כרטיס ההפצה), לכתובות שאינן פרטיות; ו-DNS (‏53) לשרתי השמות
  של השרת — וזה החריג היחיד לכתובת פרטית/loopback, ורק לפורט 53.
* **אסור:** כל כתובת של המארח עצמו (`fib daddr type local`), ‏loopback,
  ‏link-local, ‏RFC1918 ו-CGNAT (שם יושבות רשת ההפצה והכיתות), ‏multicast
  ו-broadcast, וכל IPv6. כל כרטיס שאינו כרטיס הניהול.
* **נכנס:** אין. ל-slirp אין מאזין בלי `hostfwd`, ו-`hostfwd`/`guestfwd`/
  ‏`smb` אינם בשורת הפקודה (נבדק בטסט).

**למה uid ולא tap בתוך network namespace:** ‏tap דורש DHCP על המארח (עבור
האורח), ‏`ip_forward` ו-NAT — שלושה דברים ששרת שמשדר לכיתות לא צריך להדליק.
‏slirp עושה את ה-DHCP והתרגום **בתוך התהליך**, וה-uid הוא נקודת חנק אחת
שכל תעבורת האורח עוברת דרכה. אותן הבטחות, בלי `ip_forward` ובלי DHCP על
אף כרטיס אמיתי.

**כשל = סירוב.** הטבלה נטענת לפני כל הדלקה ונקראת בחזרה (`nft list
table`); טבלה שלא נטענה או שלא חזרה עם כלל ה-uid — ה-VM אינו מודלק.
"""

from __future__ import annotations

import ipaddress
import re
import subprocess
from pathlib import Path

TABLE = "imagectl_vm"
VM_USER = "imagectl-vm"
#: הכתובות שהאורח לעולם אינו מגיע אליהן (מלבד DNS לשרתי השמות).
DENIED = ("0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16",
          "172.16.0.0/12", "192.0.0.0/24", "192.168.0.0/16", "198.18.0.0/15",
          "224.0.0.0/4", "240.0.0.0/4")
_IFNAME = re.compile(r"[A-Za-z0-9_.:-]{1,15}")


def resolvers(path: Path = Path("/etc/resolv.conf")) -> list[str]:
    """שרתי השמות (IPv4) של השרת, כפי ש-slirp ישתמש בהם."""
    found = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return found
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "nameserver":
            try:
                found.append(str(ipaddress.IPv4Address(parts[1])))
            except ValueError:
                continue
    return list(dict.fromkeys(found))


def ruleset(uid: int, egress_if: str, dns: list[str]) -> str:
    """הטבלה — טקסט ל-`nft -f -`. ‏delete+create בטרנזאקציה אחת."""
    if type(uid) is not int or uid <= 0:
        raise ValueError(f"uid לא תקין: {uid!r}")
    if not _IFNAME.fullmatch(egress_if or ""):
        raise ValueError(f"שם כרטיס לא תקין: {egress_if!r}")
    dns = [str(ipaddress.IPv4Address(a)) for a in dns]
    lines = [
        f"add table inet {TABLE}",
        f"delete table inet {TABLE}",
        f"table inet {TABLE} {{",
        # #1405: positive match only. An orphaned socket (the server closed it while
        # data is still queued -- every Connection: close boot download) has no
        # owner, so `skuid != uid` does NOT match it and the old chain fell through
        # to `oifname != ... drop`: GRUB's kernel/initrd tails were dropped while a
        # VM update was loaded. Only packets that ARE the VM user's go to `vm`.
        "  chain out {",
        "    type filter hook output priority 0; policy accept;",
        f"    meta skuid {uid} jump vm",
        "  }",
        "  chain vm {",
        "    meta nfproto ipv6 drop",
    ]
    if dns:
        resolver_set = ", ".join(dns)
        lines.append(f"    ip daddr {{ {resolver_set} }} meta l4proto {{ tcp, udp }} "
                     "th dport 53 accept")
    lines += [
        f'    oifname != "{egress_if}" drop',
        "    fib daddr type { local, broadcast, multicast, anycast } drop",
        f"    ip daddr {{ {', '.join(DENIED)} }} drop",
        "    accept",
        "  }",
        "}",
    ]
    return "\n".join(lines) + "\n"


def apply(uid: int, egress_if: str, dns: list[str], *, run=subprocess.run) -> str | None:
    """טוען וקורא בחזרה. ‏None = הטבלה במקומה; אחרת הסיבה."""
    text = ruleset(uid, egress_if, dns)
    try:
        loaded = run(["nft", "-f", "-"], input=text, capture_output=True, text=True, timeout=15)
        if loaded.returncode != 0:
            return f"nft -f נכשל (rc={loaded.returncode}): {loaded.stderr.strip()}"
        back = run(["nft", "list", "table", "inet", TABLE], capture_output=True, text=True,
                   timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"nft לא רץ: {exc}"
    if back.returncode != 0:
        return f"הטבלה לא נקראה בחזרה (rc={back.returncode}): {back.stderr.strip()}"
    listed = back.stdout
    for evidence in (f"skuid {uid} jump vm", f'oifname != "{egress_if}" drop', "10.0.0.0/8"):
        if evidence not in listed:
            return f"הטבלה נקראה בחזרה בלי '{evidence}' — לא ידוע מה נטען"
    return None
