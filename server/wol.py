"""Wake-on-LAN — השרת מעיר את מחשבי הכיתה כשנפתח סבב.

זה מה שמאפשר לפתוח סבב מתחנה אחת וללכת: שאר המחשבים נדלקים לבד,
עולים ב-PXE, ומצטרפים. (WoL חייב להיות מופעל ב-BIOS — באחריות נדב.)

חבילת הקסם: שש פעמים FF ואז ה-MAC שש-עשרה פעמים, ב-UDP לכתובת השידור.
מחשב דלוק מתעלם ממנה; לכן מעירים את כל הקבוצה בלי לבדוק מי כבר ער.

**טבלת ה-MAC היא המקור** — לא רשימת מי שדיבר עם השרת לאחרונה. מכונה
רשומה שמעולם לא עלתה היא בדיוק המכונה שצריך להעיר; אילו התבססנו על
`net_devices` היא הייתה נשארת כבויה לנצח.

השליחה מוזרקת (`send`) כדי שהבדיקות יתפסו את החבילות בלי רשת אמיתית.
"""

from __future__ import annotations

import logging
import socket
from pathlib import Path
from typing import Callable

from .db import journal
from .registry import normalize_mac

log = logging.getLogger("imagectl.wol")

WOL_PORT = 9
BROADCAST = "255.255.255.255"

SYSFS_NET = "/sys/class/net"

LINK_UP = "up"
LINK_DOWN = "down"
LINK_UNKNOWN = "unknown"

#: ‏operstate שמשמעותו "אין קישור" — כאן sysfs אומר לנו דבר חיובי ולא
#: סתם שותק. ‏`notpresent` הוא ממשק שהדרייבר שלו לא טען.
_DEAD_OPERSTATES = {"down", "lowerlayerdown", "notpresent"}


def _read(path: Path) -> str | None:
    """תוכן קובץ sysfs, או None אם **הקריאה עצמה** נכשלה.

    ‏None כאן הוא "לא הצלחנו לבדוק", לא "הערך שלילי" — שני מצבים שונים
    (עיקרון 5), ומי שקורא חייב להבחין ביניהם. ‏`carrier` של ממשק כבוי
    מחזיר EINVAL, ולכן דווקא כאן קל להתבלבל.
    """
    try:
        return path.read_text().strip()
    except OSError:
        return None


def link_state(interface: str, *, sysfs: str | Path = SYSFS_NET) -> tuple[str, str]:
    """מצב הקישור של ממשק: ‏(`LINK_UP`/`LINK_DOWN`/`LINK_UNKNOWN`, סיבה).

    ‏`sendto` על UDP לממשק בלי carrier **אינו מחזיר שגיאה** — הקרנל
    מקבל את החבילה ומשליך אותה (#74, אומת ב-tcpdump: אפס חבילות).
    כלומר היעדר חריגה אינו ראיה לכלום, וצריך ראיה חיובית אחרת. sysfs
    הוא הראיה: ‏`operstate` ו-`carrier` נקראים לפני השליחה.

    שלושה מצבים, ובכוונה לא שניים:

    * ‏`LINK_DOWN` — **ראיה חיובית שאין קישור**: operstate מת, ‏carrier
      הוא "0", או שהממשק כלל אינו קיים ב-sysfs שקיים ונקרא. כאן שליחה
      היא בזבוז בייטים, והיא נכשלת בגלוי.
    * ‏`LINK_UP` — ראיה חיובית שיש קישור.
    * ‏`LINK_UNKNOWN` — **לא הצלחנו לבדוק**: אין ‏/sys בכלל (מכונה שאינה
      לינוקס, קונטיינר), או שהקבצים לא נקראים. זה *לא* אותו דבר כמו
      "אין carrier", ואסור שיחסום שליחה תקינה: הבדיקה היא כלי אבחון,
      ואם היא בעצמה לא זמינה — התקלה שלה לא אמורה לכבות את WoL ולהפיל
      כיתה שלמה. לכן שולחים — אבל לא מתחזים לאימות: הסיבה נכתבת ל-log
      ברמת WARNING, כך ש"נשלח ואומת" ו"נשלח בלי לבדוק" נשארים שני
      דברים שאפשר להבדיל ביניהם אחר כך.
    """
    root = Path(sysfs)
    entry = root / interface
    if not (entry.exists() or entry.is_symlink()):
        if not root.is_dir():
            return LINK_UNKNOWN, (
                f"link-unverified: אי אפשר לבדוק את מצב הממשק {interface} — "
                f"אין {root} במכונה הזאת")
        return LINK_DOWN, (
            f"iface-missing: אין ממשק בשם {interface} בשרת — בדקו את שם "
            "הממשק שהשרת הופעל איתו (‎--interface)")

    operstate = _read(entry / "operstate")
    carrier = _read(entry / "carrier")
    # ‏carrier לפני operstate, ולא להפך: כרטיס אמיתי שהכבל שלו נשלף
    # מדווח **גם** ‏operstate=down (או lowerlayerdown), ואילו ממשק
    # שהורד ביד מחזיר EINVAL על carrier. כלומר ‏carrier="0" הוא
    # האבחנה הצרה — כבל — ומי שמדווח אותה קודם שולח את הטכנאי לכבל
    # במקום להציע לו להריץ `ip link set up` על ממשק שכבר למעלה.
    # ‏operstate נכנס להודעה ממילא, כדי שהראיה הגולמית תישאר לעיניים.
    if carrier == "0":
        return LINK_DOWN, (
            f"no-carrier: אין carrier על {interface} "
            f"(operstate={operstate or 'לא נקרא'}) — הכבל של וילן ההפצה מנותק "
            "או שהפורט במתג כבוי; אף חבילת הערה לא תצא עד שיחובר")
    if operstate in _DEAD_OPERSTATES:
        return LINK_DOWN, (
            f"iface-down: הממשק {interface} כבוי (operstate={operstate}) — "
            f"יש להעלות אותו: ip link set {interface} up")
    if carrier == "1" or operstate == LINK_UP:
        return LINK_UP, ""
    return LINK_UNKNOWN, (
        f"link-unverified: לא ניתן לקרוא את מצב הקישור של {interface} "
        f"(operstate={operstate or 'לא נקרא'}) — נשלח בלי אימות")


class WakeResult(int):
    """כמה חבילות נשלחו — ולמה השאר לא.

    יורש מ-`int` ולא NamedTuple בכוונה: המונה **הוא** התשובה, וכל מי
    שסופר אותו ממשיך לעבוד בלי לדעת. הסיבה נוסעת לצידו כדי שהמסך יוכל
    לומר **מה** קרה ולא רק "0 מחשבים" — ‏"נכשל" בלי סיבה שולח את
    הטכנאי לאותם 12 BIOSים בדיוק כמו שקט (#74).
    """

    def __new__(cls, sent: int, failed=(), reasons=()):
        self = super().__new__(cls, sent)
        self.failed = list(failed)
        self.reasons = list(reasons)
        return self


def magic_packet(mac: str) -> bytes:
    """‏MAC בכל אחת משלוש הווריאציות (סעיף 10) → חבילת הקסם, 102 בייט.

    ‏MAC לא תקין הוא ValueError ולא חבילה ריקה: מוטב שהקריאה תיכשל
    בגלוי מאשר שתישלח לרשת חבילה שלא תעיר איש.
    """
    canonical = normalize_mac(mac)
    if canonical is None:
        raise ValueError(f"not a mac address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(canonical.replace(":", "")) * 16


def broadcast_sender(
    interface: str | None = None,
    *,
    address: str = BROADCAST,
    socket_factory: Callable[..., socket.socket] = socket.socket,
    sysfs: str | Path = SYSFS_NET,
) -> Callable[[bytes], None]:
    """שולח חבילות קסם ב-UDP broadcast — על `interface` בלבד.

    בלי כפיית ממשק, שידור ל-255.255.255.255 יוצא מהממשק שטבלת הניתוב
    בוחרת — בשרת דו-כרטיסי בדרך כלל ברירת המחדל, כלומר **הרשת הרגילה
    של המכללה** ולא וילן ההפצה. זו גם דליפה (בדיקה 7.2) וגם תקלה:
    הכיתה לא מתעוררת. לכן `interface` נכפה ב-SO_BINDTODEVICE.

    בפלטפורמה בלי SO_BINDTODEVICE (ווינדוס) לא משדרים "לכל מקום
    ליתר ביטחון" — זורקים OSError. עדיף כשל גלוי מדליפה שקטה.

    שני דברים נבדקים כאן כ**ראיה חיובית** ולא כהיעדר חריגה (#74):
    מצב הקישור של הממשק לפני השליחה (`link_state`), וכמה בייטים
    ‏`sendto` דיווח שקיבל. בלי ממשק כפוי אין מה לבדוק — הניתוב יבחר —
    ואז הבדיקה מדלגת; זו עוד סיבה להזריק ממשק.
    """
    bind_to_device = getattr(socket, "SO_BINDTODEVICE", None)

    def send(packet: bytes) -> None:
        if interface and bind_to_device is None:
            raise OSError(
                f"cannot pin wol to {interface}: SO_BINDTODEVICE is not "
                "available here; refusing to broadcast on every interface"
            )
        if interface:
            state, why = link_state(interface, sysfs=sysfs)
            if state == LINK_DOWN:
                raise OSError(why)
            if state == LINK_UNKNOWN:
                log.warning("wol on %s: %s", interface, why)
        with socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if interface:
                sock.setsockopt(socket.SOL_SOCKET, bind_to_device,
                                interface.encode() + b"\0")
            written = sock.sendto(packet, (address, WOL_PORT))
            if written != len(packet):
                raise OSError(
                    f"short-send: הקרנל קיבל {written} מתוך {len(packet)} "
                    "בייט של חבילת ההערה — היא לא נשלחה שלמה")

    return send


#: ברירת המחדל כשאיש לא הזריק שולח. ראו broadcast_sender: בלי ממשק
#: השידור הולך לפי טבלת הניתוב — app.py אמור להזריק שולח עם הממשק.
_send_broadcast = broadcast_sender()


def wake_group(
    conn,
    group_id: str,
    exclude_mac: str | None = None,
    only: set[str] | None = None,
    send: Callable[[bytes], None] = _send_broadcast,
) -> WakeResult:
    """מעיר את מכונות הקבוצה **מטבלת ה-MAC**. מחזיר כמה חבילות נשלחו.

    `exclude_mac` — המכונה שפתחה את הסבב: היא כבר דולקת, ואין טעם
    להרעיש עליה. `only` — סבב עם בחירת מחשבים מעיר רק את הנבחרים.

    כשל שליחה לא עוצר את השאר — מוטב 29 ערות ואחת לא מאשר אפס — אבל
    הוא גם לא נבלע: מי שנכשל נרשם ביומן (`wol_failed`), כדי שמפעיל
    שרואה כיתה חצי-ישנה ידע שהשרת יודע. כשל שקט כאן נראה בדיוק כמו
    מחשב שה-WoL כבוי לו ב-BIOS, ואי אפשר לאבחן ככה כלום.

    ולכן **הסיבה** נכנסת ליומן ולא רק המונה: "נכשל" שולח את הטכנאי
    לאותם 12 BIOSים בדיוק כמו שקט. שורת ה-`wol_failed` נושאת את מה
    שהשולח אמר ("אין carrier על eth0..."), פעם אחת לכל סיבה — כשכבל
    אחד מנותק כל 12 הכשלים הם אותו משפט.
    """
    skip = normalize_mac(exclude_mac) if exclude_mac else None
    chosen = None
    if only is not None:
        chosen = {normalize_mac(m) or m for m in only}

    sent = 0
    failed: list[str] = []
    reasons: list[str] = []
    rows = conn.execute(
        "SELECT mac FROM machines WHERE group_id = ? ORDER BY mac", (group_id,)
    ).fetchall()
    for row in rows:
        mac = row["mac"]
        if mac == skip:
            continue
        if chosen is not None and mac not in chosen:
            continue
        try:
            send(magic_packet(mac))
            sent += 1
        except (OSError, ValueError) as exc:
            failed.append(mac)
            if str(exc) and str(exc) not in reasons:
                reasons.append(str(exc))
            log.error("wol to %s failed: %s", mac, exc)

    if failed:
        detail = f"{group_id} failed={len(failed)} {' '.join(failed)}"
        if reasons:
            detail += " | " + " | ".join(reasons)
        journal(conn, "wol_failed", detail)
    log.info("wol %s: %d sent, %d failed", group_id, sent, len(failed))
    return WakeResult(sent, failed, reasons)
