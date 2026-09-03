"""הרצת השרת: python -m server.main --server-url http://10.99.12.10:8080

אותה כתובת משמשת את תפריט ה-GRUB ואת הסוכן — היא מה שנכתב לשורת
הפקודה של הקרנל, ולכן חייבת להיות הכתובת שהלקוחות רואים, לא localhost.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.parse


def _interface_for(server_url: str) -> str | None:
    """הכרטיס שנושא את כתובת ה-server-url — הוא ממשק וילן ההפצה.

    השידור (udp-sender) חייב לצאת דווקא ממנו: בלי ‎--interface‏ udpcast
    בוחר את ברירת המחדל של הניתוב, וברשת עם שני כרטיסים זה ה-LAN —
    השידור לא פוגש אף מקבל ונכשל, וגרוע מזה, הוא מדבר ברשת הרגילה (#19).
    """
    host = urllib.parse.urlsplit(server_url).hostname
    try:
        out = subprocess.run(
            ["ip", "-json", "addr"], capture_output=True, text=True,
            check=True, stdin=subprocess.DEVNULL,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None            # לא לינוקס (שרת פיתוח) — השידור ממילא מזויף
    for nic in json.loads(out):
        for addr in nic.get("addr_info") or []:
            if addr.get("local") == host:
                return nic.get("ifname")
    return None


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

    interface = args.interface or _interface_for(args.server_url)
    if interface:
        print(f"broadcast interface: {interface}")
    else:
        print("broadcast interface: not found -- udp-sender will use the "
              "routing default; on a two-NIC server pass --interface")

    import uvicorn

    from .app import create_app

    app = create_app(args.data_dir, args.images, args.server_url,
                     boot_dir=args.boot_dir, interface=interface,
                     sender_portbase=args.sender_portbase,
                     extra_cmdline=tuple(args.extra_cmdline.split()))
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
