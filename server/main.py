"""הרצת השרת: python -m server.main --server-url http://10.44.12.10:8080

אותה כתובת משמשת את תפריט ה-GRUB ואת הסוכן — היא מה שנכתב לשורת
הפקודה של הקרנל, ולכן חייבת להיות הכתובת שהלקוחות רואים, לא localhost.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import urllib.parse


class InterfaceDetectionError(RuntimeError):
    """‏`ip` קיים ורץ, אבל אף כרטיס אינו נושא את כתובת ה-server-url — או
    שהבדיקה עצמה נשברה. זהו שרת לינוקס אמיתי שהשידור שלו ייצא לכרטיס הלא
    נכון, ולכן עדיף להיכשל בגלוי מלשדר לרשת הרגילה (#514, #19)."""


def _interface_for(server_url: str) -> str | None:
    """הכרטיס שנושא את כתובת ה-server-url — הוא ממשק וילן ההפצה.

    השידור (udp-sender) חייב לצאת דווקא ממנו: בלי ‎--interface‏ udpcast
    בוחר את ברירת המחדל של הניתוב, וברשת עם שני כרטיסים זה ה-LAN —
    השידור לא פוגש אף מקבל ונכשל, וגרוע מזה, הוא מדבר ברשת הרגילה (#19).

    מחזיר `None` **רק** כשאין `ip` כלל — תחנת פיתוח (לא לינוקס), שם
    השידור ממילא מזויף. ‏`ip` שקיים ונכשל, או שרץ ולא מצא התאמה, הם שרת
    אמיתי במצב שגוי — ומעלים `InterfaceDetectionError` במקום `None` שקט
    (עיקרון 5: פעולה שלא הצליחה לבדוק נכשלת, לא מוותרת) (#514).
    """
    host = urllib.parse.urlsplit(server_url).hostname
    try:
        out = subprocess.run(
            ["ip", "-json", "addr"], capture_output=True, text=True,
            check=True, stdin=subprocess.DEVNULL,
        ).stdout
    except FileNotFoundError:
        return None            # אין `ip` — לא לינוקס (תחנת פיתוח), השידור מזויף
    except (OSError, subprocess.CalledProcessError) as exc:
        # ‏`ip` קיים אבל נכשל — שרת לינוקס אמיתי שהבדיקה עליו נשברה.
        raise InterfaceDetectionError(f"'ip -json addr' נכשל: {exc}") from exc
    # ‏--server-url עם שם מארח (ולא IP) לא יתאים ל-addr.local לעולם —
    # פותרים אותו לכתובותיו לפני ההשוואה.
    candidates = {host}
    try:
        candidates |= {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, OSError):
        pass
    try:
        nics = json.loads(out)
    except json.JSONDecodeError as exc:
        # ‏`ip` יצא 0 אבל הפלט אינו JSON תקין — שרת אמיתי שהבדיקה נשברה
        # עליו, לא None שקט (עיקרון 5; Codex #2 על #514).
        raise InterfaceDetectionError(f"פלט 'ip -json addr' אינו JSON: {exc}") from exc
    for nic in nics:
        for addr in nic.get("addr_info") or []:
            if addr.get("local") in candidates:
                return nic.get("ifname")
    raise InterfaceDetectionError(
        f"אף כרטיס אינו נושא את {host} — udp-sender היה משדר לכרטיס הלא "
        f"נכון. העבר ‎--interface‏ במפורש.")


def build_parser() -> argparse.ArgumentParser:
    """הדגלים של השרת, בנפרד מההרצה — כדי שאפשר יהיה לפרוס שורת פקודה
    בבדיקה בלי להרים uvicorn (‏#201: ה-e2e מרכיב argv, וצריך ראיה שהוא
    מתפרס כאן ולא נבלע)."""
    parser = argparse.ArgumentParser(description="ImageCtl server + console")
    parser.add_argument("--server-url", required=True,
                        help="הכתובת שהלקוחות רואים, http בלבד (כמו במתקין)")
    parser.add_argument("--data-dir", default="/var/lib/imagectl")
    parser.add_argument("--images", default="/srv/imagectl/images")
    parser.add_argument("--boot-dir", default="/srv/imagectl/boot",
                        help="הקרנל וה-initramfs שהמתקין הניח; מוגש תחת ‎/boot")
    parser.add_argument("--interface", default=None,
                        help="ממשק השידור; ברירת מחדל: הכרטיס של --server-url")
    parser.add_argument("--extra-cmdline",
                        default=os.environ.get("IMAGECTL_EXTRA_CMDLINE", ""),
                        help="תוספות לשורת הקרנל של הסוכן (למשל קונסולה "
                             "טורית ו-debug במעבדה); גם IMAGECTL_EXTRA_CMDLINE")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    # ‏#201: הפורט שהשידור תופס בפועל (וגם portbase+1). בייצור לא נוגעים
    # בו — udpcast של הסוכן מצפה ל-9000, וזה מה שהמתקין והיחידה מריצים.
    # הדגל קיים בשביל מי שמריץ שרת אמיתי במקום שאסור לו להתנגש בהפצה:
    # ‏`tools/e2e/harness.py`. **בכוונה דגל ולא משתנה סביבה** — משתנה
    # סביבה שנשאר תקוע ב-shell או ביחידה משנה את פורט ההפצה של הייצור
    # בשקט, ודווקא כאן זה הכשל שמנסים למנוע. דגל נראה ב-`ps`.
    parser.add_argument("--sender-portbase", type=int, default=None,
                        help="portbase של udp-sender; ברירת המחדל היא 9000 "
                             "(הייצור). לשימוש הסימולציה בלבד")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.server_url.startswith("http://"):
        # אותו כלל כמו במתקין: ה-GRUB החתום נבנה בלי TLS.
        parser.error("--server-url חייב להתחיל ב-http://")

    if args.interface:
        interface = args.interface
    else:
        try:
            interface = _interface_for(args.server_url)
        except InterfaceDetectionError as exc:
            # שרת אמיתי שלא ניתן לזהות לו ממשק שידור לא עולה בשקט על
            # הכרטיס הלא נכון — הוא עוצר ומבקש ‎--interface‏ (#514).
            parser.error(str(exc))
    if interface:
        print(f"broadcast interface: {interface}")
    else:
        print("broadcast interface: none (no `ip` — dev station, "
              "broadcast is simulated)")

    import uvicorn

    from .app import create_app

    app = create_app(args.data_dir, args.images, args.server_url,
                     boot_dir=args.boot_dir, interface=interface,
                     sender_portbase=args.sender_portbase,
                     extra_cmdline=tuple(args.extra_cmdline.split()))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
