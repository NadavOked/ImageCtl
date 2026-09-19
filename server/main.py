"""הרצת השרת: python -m server.main --server-url http://10.44.12.10:8080

אותה כתובת משמשת את תפריט ה-GRUB ואת הסוכן — היא מה שנכתב לשורת
הפקודה של הקרנל, ולכן חייבת להיות הכתובת שהלקוחות רואים, לא localhost.
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import os
import socket
import subprocess
import urllib.parse
from pathlib import Path

from . import deploy_net, identity


class InterfaceDetectionError(RuntimeError):
    """‏`ip` קיים ורץ, אבל אף כרטיס אינו נושא את כתובת ה-server-url — או
    שהבדיקה עצמה נשברה. זהו שרת לינוקס אמיתי שהשידור שלו ייצא לכרטיס הלא
    נכון, ולכן עדיף להיכשל בגלוי מלשדר לרשת הרגילה (#514, #19)."""


def _ip_nics() -> list | None:
    """כרטיסים מ-`ip -json addr`. ‏None רק כשאין `ip` (תחנת פיתוח)."""
    try:
        out = subprocess.run(
            ["ip", "-json", "addr"], capture_output=True, text=True,
            check=True, stdin=subprocess.DEVNULL,
        ).stdout
    except FileNotFoundError:
        return None            # אין `ip` — לא לינוקס (תחנת פיתוח)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InterfaceDetectionError(f"'ip -json addr' נכשל: {exc}") from exc
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise InterfaceDetectionError(f"פלט 'ip -json addr' אינו JSON: {exc}") from exc


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
    nics = _ip_nics()
    if nics is None:
        return None
    # ‏--server-url עם שם מארח (ולא IP) לא יתאים ל-addr.local לעולם —
    # פותרים אותו לכתובותיו לפני ההשוואה.
    candidates = {host}
    try:
        candidates |= {info[4][0] for info in socket.getaddrinfo(host, None)}
    except (socket.gaierror, OSError):
        pass
    for nic in nics:
        for addr in nic.get("addr_info") or []:
            if addr.get("local") in candidates:
                return nic.get("ifname")
    raise InterfaceDetectionError(
        f"אף כרטיס אינו נושא את {host} — udp-sender היה משדר לכרטיס הלא "
        f"נכון. העבר ‎--interface‏ במפורש.")


def _address_for_interface(name: str) -> str | None:
    """IPv4 של הכרטיס — כתובת ה-bind של הסוכן/קיוסק (#1074).

    ‏None רק כשאין `ip` (תחנת פיתוח). כרטיס שקיים בלי IPv4, או `ip`
    שנכשל — `InterfaceDetectionError`, לא נפילה ל-0.0.0.0 בשקט.
    """
    nics = _ip_nics()
    if nics is None:
        return None
    for nic in nics:
        if nic.get("ifname") != name:
            continue
        for addr in nic.get("addr_info") or []:
            local = addr.get("local") or ""
            if local.count(".") == 3:
                return local
        raise InterfaceDetectionError(
            f"לכרטיס {name} אין כתובת IPv4 — הסוכן היה נקשר לכל הכרטיסים")
    raise InterfaceDetectionError(f"לא נמצא כרטיס בשם {name}")


def build_parser() -> argparse.ArgumentParser:
    """הדגלים של השרת, בנפרד מההרצה — כדי שאפשר יהיה לפרוס שורת פקודה
    בבדיקה בלי להרים uvicorn (‏#201: ה-e2e מרכיב argv, וצריך ראיה שהוא
    מתפרס כאן ולא נבלע)."""
    parser = argparse.ArgumentParser(description="ImageCtl server + console")
    # ‏#1088: אינו חובה עוד. ריק = הכתובת נקראת ממה שנרשם מהקונסולה
    # (`deploy:url`, בהדלקת DHCP על כרטיס), ואם גם זה חסר — "רשת ההפצה לא
    # הוגדרה": הסוכן והקיוסק על loopback בלבד, והקונסולה אומרת מה לעשות.
    # היחידה מעבירה ``${IMAGECTL_URL}`` גם כשהוא ריק (ארגומנט ריק אחד).
    parser.add_argument("--server-url", default="",
                        help="הכתובת שהלקוחות רואים, http בלבד (כמו במתקין). "
                             "ריק = מה שהוגדר מהקונסולה (#1088)")
    parser.add_argument("--tftp-root", default="/srv/tftp",
                        help="שורש ה-TFTP שהמתקין פרס אליו — לשם נכתב grub.cfg "
                             "כשרשת ההפצה מוגדרת מהקונסולה (#1088)")
    parser.add_argument("--data-dir", default="/var/lib/imagectl")
    parser.add_argument("--images", default="/srv/imagectl/images")
    # ‏#748: עץ הקוד עצמו — משם ``git describe``/``fetch``/``checkout``
    # של כפתור העדכון. ברירת המחדל היא תיקיית הקוד (ImageStore ->
    # server/main.py -> parents[1]), שזהה ל-``WorkingDirectory`` ביחידת
    # systemd (‏/opt/imagectl) בלי לצטט אותה כאן כברירת מחדל שנייה.
    parser.add_argument("--repo-dir", default=None,
                        help="עץ הגיט של השרת; ברירת מחדל: תיקיית הקוד")
    parser.add_argument("--boot-dir", default="/srv/imagectl/boot",
                        help="הקרנל וה-initramfs שהמתקין הניח; מוגש תחת ‎/boot")
    parser.add_argument("--dhcp-leases", default=identity.DEFAULT_LEASES,
                        help="קובץ החכירות של dnsmasq — שומר הזהות (#855) "
                             "משווה אליו את כתובת המקור של hello/progress")
    parser.add_argument("--interface", default=None,
                        help="ממשק השידור; ברירת מחדל: הכרטיס של --server-url")
    parser.add_argument("--extra-cmdline",
                        default=os.environ.get("IMAGECTL_EXTRA_CMDLINE", ""),
                        help="תוספות לשורת הקרנל של הסוכן (למשל קונסולה "
                             "טורית ו-debug במעבדה); גם IMAGECTL_EXTRA_CMDLINE")
    # ‏#1074 (R20-F1): ברירת המחדל היא כתובת כרטיס ההפצה, לא 0.0.0.0.
    # ‏None כאן = "לא ביקשו"; ‏main ממלא מ-`--interface` (או מ-server-url
    # כשאין `ip`). ‏0.0.0.0 רק בבקשה מפורשת, ועם אזהרה ביומן.
    parser.add_argument("--host", default=None,
                        help="כתובת ה-bind של הסוכן והקיוסק. ברירת מחדל: "
                             "כתובת כרטיס ההפצה (--interface). 0.0.0.0 רק "
                             "בבקשה מפורשת (R20-F1)")
    parser.add_argument("--port", type=int, default=8080,
                        help="פורט הסוכן: hello/pulls/progress/images ו-/boot")
    # ‏#731 (tracer 1 של #703): הקונסולה/הניהול מאזינים על פורט **נפרד**
    # מהסוכן, כדי שחומת אש תוכל לחשוף רק אותו לווילן הניהול.
    parser.add_argument("--console-port", type=int, default=8081,
                        help="פורט הקונסולה/הניהול: /api/console ו-/console")
    # ‏#770 (tracer 4 של #703): הקונסולה נקשרת פר-ממשק — לכרטיס הניהול/
    # המשרדים בלבד, ולא ל-`--host` (וילן ההפצה). קשירה ל-`0.0.0.0` חשפה
    # את API הניהול בכל הכרטיסים (כולל ההפצה/כיתות) והתנגשה עם ה-preseed
    # על 8081. ברירת המחדל **fail-closed** היא loopback ולא `0.0.0.0`:
    # בלי כתובת ניהול מפורשת הקונסולה נגישה מקומית בלבד, ואינה תופסת 8081
    # בממשקים אחרים — "הגבול הוא ה-socket" (#703), ועיקרון 5 (השומר עובד
    # כברירת מחדל, לא רק אם המפעיל זכר דגל). המפעיל קובע את כתובת הניהול
    # דרך היחידה (systemctl edit). TLS לקונסולה הוא tracer 5.
    # ‏#1088: הדגל מקבל גם **שם כרטיס** (`eth0`/`ens18`) — כרטיס וילן השרתים
    # הוא לקוח DHCP של המכללה; השרת פותר את הכתובת בעלייה ומאזין מחדש
    # כשהיא משתנה (‏`ifaddr.AddressWatcher`). בלי כתובת על הכרטיס (lease
    # שעוד לא הגיע) הקונסולה על 127.0.0.1 בלבד, עד שתגיע.
    parser.add_argument("--console-host", default="127.0.0.1",
                        help="כתובת ה-bind של הקונסולה — כרטיס הניהול/"
                             "המשרדים בלבד: כתובת, או שם כרטיס (ואז מאזין "
                             "מחדש בהחלפת כתובת, #1088). ברירת מחדל loopback "
                             "(fail-closed); לעולם לא 0.0.0.0 בלי בקשה מפורשת")
    # ‏#703 (tracer 5): הקונסולה מוגשת ב-HTTPS בלבד — תעודה חתומה-עצמית
    # שנוצרת פעם אחת ל-`<data_dir>/console-tls/` ונשארת. ‏HTTP על 8081
    # אינו מוגש (fail-closed, בלי הפניה). ‏`off` מותר **רק** על loopback —
    # הרצת פיתוח ו-`tools/e2e`; על כרטיס ניהול הסיסמה הייתה עוברת גלויה.
    parser.add_argument("--console-tls", choices=("on", "off"), default="on",
                        help="TLS לקונסולה (8081). off מותר רק עם "
                             "--console-host על loopback (127.0.0.1) — "
                             "פיתוח/e2e; ברירת מחדל on")
    # ‏#738 (tracer 2 של #703): הקיוסק (מסך התחנה על וילן ההפצה) מאזין
    # על פורט **נפרד** מהקונסולה — allowlist קשיח, בלי נתיבי ניהול.
    # ‏#1074: אותו `--host` כמו הסוכן — כתובת כרטיס ההפצה, לא 0.0.0.0.
    parser.add_argument("--kiosk-port", type=int, default=8082,
                        help="פורט הקיוסק: מסך התחנה (capture/סבב/חדר) "
                             "ו-/console/station")
    # ‏#151: שומר לפי כתובת מקור בתוך אפליקציית הקונסולה — שכבה שלא תלויה
    # בחומת אש חיצונית. רשת/כתובת אחת לכל דגל (CIDR או כתובת בודדת),
    # ‏ניתן להעביר כמה פעמים (למשל MGMT ו-DEPLOY בנפרד). ברירת המחדל
    # ``None`` משאירה את ההתנהגות של היום — הגבול היחיד הוא ‎--console-host
    # (#770) — כדי לא לשבור פריסה קיימת שלא ביקשה את השכבה הנוספת.
    # **הרשימה אינה מקודדת קשיח**: ערכי המעבדה (10.10.10.1, 10.44.0.0/24)
    # אינם ברירת מחדל בקוד — המפעיל מזין אותם דרך היחידה, כמו כל שאר
    # ההבדלים בין מעבדה למכללה.
    parser.add_argument("--console-allow-from", action="append", default=None,
                        metavar="CIDR",
                        help="רשת/כתובת מותרת לגישה לקונסולה (‎/console, "
                             "‎/api/console); חוזר על עצמו לכמה רשתות. "
                             "ברירת מחדל: השומר אינו פעיל")
    # ‏#201: הפורט שהשידור תופס בפועל (וגם portbase+1). בייצור לא נוגעים
    # בו — udpcast של הסוכן מצפה ל-9000, וזה מה שהמתקין והיחידה מריצים.
    # הדגל קיים בשביל מי שמריץ שרת אמיתי במקום שאסור לו להתנגש בהפצה:
    # ‏`tools/e2e/harness.py`. **בכוונה דגל ולא משתנה סביבה** — משתנה
    # סביבה שנשאר תקוע ב-shell או ביחידה משנה את פורט ההפצה של הייצור
    # בשקט, ודווקא כאן זה הכשל שמנסים למנוע. דגל נראה ב-`ps`.
    parser.add_argument("--sender-portbase", type=int, default=None,
                        help="portbase של udp-sender; ברירת המחדל היא 9000 "
                             "(הייצור). לשימוש הסימולציה בלבד")
    # ‏Storage Nodes (#655/#723): תפקיד ההתקנה של השרת. **תצורת שרת
    # בלבד** — אינה נכנסת לשורת הפקודה של הקרנל (עיקרון 2), רק להגדרות.
    # ‏#732: ברירת המחדל היא ``None`` (לא ``standalone``). ריסטארט בלי
    # הדגל **אינו** כותב תפקיד, ולכן אינו דורס ``secondary`` שמור בשקט
    # (fail-open קריטי — משני שהופך לראשי חושף ניהול+בין-סניפים). התפקיד
    # נקרא ממילא fail-closed; כותבים רק כשהדגל ניתן במפורש.
    parser.add_argument("--storage-role", choices=("standalone", "secondary"),
                        default=None,
                        help="תפקיד השרת: standalone (רשאי להחזיק משניים) או "
                             "secondary. ללא הדגל — התפקיד השמור נשאר כפי שהוא")
    parser.add_argument("--primary-url", default=None,
                        help="כתובת השרת הראשי — חובה ל-secondary, מתעלמים "
                             "ב-standalone")
    # ‏#740 (tracer 2.1): מאזין ה-enrollment הבין-שרתי — **נפרד** מהסוכן/
    # קונסולה/קיוסק, TLS 1.3 + mTLS בלבד, וללא נתיבי ניהול או עוגיות. עולה
    # רק כשמסופק ‏--interserver-host/port והשרת הוא secondary. ‏--interserver-url
    # הוא הכתובת המפורשת שהמשני מציג (מאומתת קפדנית: https+פורט מפורש).
    parser.add_argument("--interserver-host", default=None,
                        help="כתובת ה-bind של מאזין ה-enrollment הבין-שרתי — "
                             "כתובת או שם כרטיס (#1088, כמו --console-host)")
    parser.add_argument("--interserver-port", type=int, default=8443,
                        help="פורט מאזין ה-enrollment הבין-שרתי (mTLS 1.3)")
    parser.add_argument("--interserver-url", default=None,
                        help="הכתובת הבין-שרתית המפורשת שהמשני מציג "
                             "(https://host:port/…, פורט מפורש חובה)")
    return parser


def _deploy_state(args) -> deploy_net.DeployState:
    """‏#1088: מקור כתובת ההפצה — הדגל, מה שנרשם מהקונסולה, או "לא הוגדרה".

    ה-DB נפתח כאן לרגע **רק** כשאין דגל: עם `--server-url` אין מה לקרוא,
    ובדיקות שמזייפות את כל מה שאחרי הפרסור אינן נוגעות בדיסק.
    """
    if args.server_url:
        return deploy_net.DeployState("cli", args.interface or None, args.server_url)
    from .db import connect
    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = connect(data_dir / "imagectl.db")
    try:
        return deploy_net.resolve(conn, None, None)
    finally:
        conn.close()


def _resolve_bind_host(parser, flag: str, value: str | None) -> tuple[str | None, str | None]:
    """‏#1088: ‏(כתובת, שם-כרטיס) לדגל bind שמקבל כתובת **או** שם כרטיס.

    כתובת → ‏(כתובת, None). שם כרטיס → הכתובת שלו עכשיו (או None כשאין
    lease עדיין) ושמו. ‏`ip` שנכשל, או כרטיס שאינו קיים — ‏`parser.error`:
    כתובת שלא הצלחנו לקרוא אינה "אין כתובת" (עיקרון 5).
    """
    from . import ifaddr
    if not value or not ifaddr.is_interface_name(value):
        return value, None
    try:
        return ifaddr.read_ipv4(value), value
    except ifaddr.AddressLookupError as exc:
        parser.error(f"{flag} {value}: {exc}")
    return None, None                                    # pragma: no cover


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    from . import ifaddr
    deploy = _deploy_state(args)

    if deploy.source == "cli":
        if not args.server_url.startswith("http://"):
            # אותו כלל כמו במתקין: ה-GRUB החתום נבנה בלי TLS.
            parser.error("--server-url חייב להתחיל ב-http://")

        # ‏#498: המארח ב---server-url חייב להיות כתובת IP מספרית, לא שם.
        # ‏`hello.off_deploy_vlan` משווה את ה-sockname של החיבור (תמיד כתובת)
        # ל-hostname של הכתובת הזו; שם מארח לעולם אינו שווה לכתובת, ה-except
        # בלע את ה-ValueError, ו**כל** מכונה סווגה כ"בתוך וילן ההפצה" — כלומר
        # חובת הכניסה מחוץ לווילן (#42) לא נורתה לעולם. "לא הצלחנו לסווג"
        # אינו "בפנים" (עיקרון 5). פתרון DNS פעם אחת בעלייה היה נשבר בשקט
        # כשהרשומה משתנה — ולכן השרת מסרב לעלות ואומר למה. המתקין ממילא גוזר
        # את הכתובת מהכרטיס (install/setup-boot-server.sh).
        host = urllib.parse.urlsplit(args.server_url).hostname
        try:
            ipaddress.ip_address(host)
        except ValueError:
            parser.error(f"--server-url חייב לשאת כתובת IP מספרית ולא שם מארח "
                         f"(התקבל: {host!r}). עם שם, השרת אינו יכול לדעת אם "
                         f"hello הגיע מווילן ההפצה, וחובת הכניסה מחוץ לווילן "
                         f"לא נאכפת (#498, #42)")

    # תצורת ה-Storage Node מאומתת כאן, לפני הרמת השרת: משני בלי כתובת
    # שרת ראשי נכשל בקול (עיקרון 5), ולא מתגלגל ל-standalone בשקט.
    # ‏#732: רק כשהדגל ניתן. בלעדיו התפקיד השמור נשאר, ואין מה לאמת.
    if args.storage_role is not None:
        from .storage_nodes import StorageConfigError, normalize_config
        try:
            normalize_config(args.storage_role, args.primary_url)
        except StorageConfigError as exc:
            parser.error(str(exc))

    # ‏#740: הכתובת הבין-שרתית מאומתת כאן, לפני הרמת השרת — https+פורט מפורש,
    # בלי אישורים/query/fragment (עיקרון 5: downgrade נדחה, לא "מתוקן" בשקט).
    if args.interserver_url is not None:
        from .interserver_auth import (InterserverURLError,
                                       parse_interserver_url)
        try:
            parse_interserver_url(args.interserver_url)
        except InterserverURLError as exc:
            parser.error(str(exc))

    # ‏#151: פרסור ואימות רשתות הקונסולה כאן, לפני הרמת השרת — כמו כל
    # אימות אחר בבלוק הזה (עיקרון 5: ``CIDR`` שגוי נדחה בגלוי, לא נופל
    # לברירת מחדל שקטה שפותחת או סוגרת הכול).
    console_allowed_networks = None
    if args.console_allow_from:
        from .console_source_guard import parse_networks
        try:
            console_allowed_networks = parse_networks(args.console_allow_from)
        except ValueError as exc:
            parser.error(f"--console-allow-from: {exc}")

    # ‏#1088: ‏--console-host כשם כרטיס — הכתובת נפתרת עכשיו; ‏None = הכרטיס
    # קיים בלי כתובת (lease שעוד לא הגיע), והקונסולה עולה על loopback עד
    # שהדוגם יראה כתובת. אותו דבר ל---interserver-host.
    console_addr, console_iface = _resolve_bind_host(parser, "--console-host",
                                                     args.console_host)
    inter_addr, inter_iface = _resolve_bind_host(parser, "--interserver-host",
                                                 args.interserver_host)
    machine_hostname = socket.gethostname()

    # ‏#703 (tracer 5): TLS לקונסולה — הדגל מאומת כאן (off רק על loopback),
    # והתעודה נוצרת/נטענת **לפני** הרמת השרת: חצי זהות או תיקייה שאי
    # אפשר לכתוב אליה עוצרים בקול, לא קונסולה שעלתה בלי TLS (עיקרון 5).
    # ‏#1088: עם שם כרטיס ה-SAN הוא הכתובת **הנוכחית** + שם המארח; בחילוף
    # כתובת התעודה אינה מתחדשת (הדפדפן כבר אישר אותה) — היא תקפה לפי השם.
    from .console_tls import (ConsoleTLSError, check_tls_flag,
                              ensure_console_cert)
    console_tls = None
    try:
        check_tls_flag(args.console_tls, args.console_host)
        if args.console_tls == "on":
            console_tls = ensure_console_cert(
                args.data_dir, console_addr or "127.0.0.1", hostname=machine_hostname)
    except ConsoleTLSError as exc:
        parser.error(str(exc))
    if console_tls is not None and console_addr and console_addr not in console_tls.sans:
        # התעודה נשארת (הדפדפן כבר אישר אותה); הפער נאמר, לא מתוקן בשקט.
        print(f"warning: console TLS certificate SANs {list(console_tls.sans)} "
              f"do not include --console-host {console_addr}; remove "
              f"{console_tls.cert_path.parent} to issue a new one")

    # ‏#1088: כתובת ההפצה וכרטיסה — מהדגל (המעבדה, שרת שהותקן עם כרטיס
    # הפצה), ממה שנרשם מהקונסולה, או "לא הוגדרה". שלושת המקורות נאמרים.
    if deploy.source == "cli":
        server_url = args.server_url
        if args.interface:
            interface = args.interface
        else:
            try:
                interface = _interface_for(server_url)
            except InterfaceDetectionError as exc:
                # שרת אמיתי שלא ניתן לזהות לו ממשק שידור לא עולה בשקט על
                # הכרטיס הלא נכון — הוא עוצר ומבקש ‎--interface‏ (#514).
                parser.error(str(exc))
    elif deploy.source == "console":
        # הכרטיס שנרשם חייב לשאת את הכתובת שנרשמה — אחרת השרת **אינו**
        # נופל (זה היה משאיר את המפעיל בלי קונסולה ובלי SSH) אלא חוזר
        # ל"לא הוגדרה", ואומר זאת ביומן ובמסך הבריאות.
        server_url, interface = deploy.url, deploy.interface
        try:
            found = _interface_for(server_url)
        except InterfaceDetectionError as exc:
            print(f"warning: deploy network {deploy.interface} {deploy.url} — {exc}; "
                  "back to 'not configured'")
            deploy = deploy_net.DeployState("none", None, None)
        else:
            if found not in (None, interface):
                print(f"warning: deploy address {deploy.url} is on {found}, not on "
                      f"{interface} — using {found}")
                interface = found
    if not deploy.configured:
        server_url, interface = deploy_net.deploy_url(deploy_net.UNCONFIGURED_HOST,
                                                      args.port), None
        print("deploy network: not configured — agent and kiosk on "
              f"{deploy_net.UNCONFIGURED_HOST} only; set it from the console "
              "(network page: address, then DHCP) (#1088)")
    if interface:
        print(f"broadcast interface: {interface}")
    elif deploy.configured:
        print("broadcast interface: none (no `ip` — dev station, "
              "broadcast is simulated)")
    host = urllib.parse.urlsplit(server_url).hostname

    # ‏#1074: סוכן 8080 וקיוסק 8082 נקשרים לכתובת כרטיס ההפצה. ‏0.0.0.0
    # רק אם המפעיל ביקש במפורש — ואז נאמר, לא בשקט (R20-F1).
    if args.host is None:
        nic_addr = None
        if interface:
            try:
                nic_addr = _address_for_interface(interface)
            except InterfaceDetectionError as exc:
                parser.error(str(exc))
        args.host = nic_addr or host
    if args.host == "0.0.0.0":
        print("warning: --host 0.0.0.0 — הסוכן והקיוסק מאזינים על כל "
              "הכרטיסים (R20-F1). ברירת המחדל היא כתובת כרטיס ההפצה.")

    import uvicorn

    from . import dhcp_host, ports
    from .app import (create_agent_app, create_console_app, create_kiosk_app,
                      create_runtime)
    from .db import journal

    # ‏#996: מנהל המאזינים — נבנה לפני ה-runtime כי הוא מוזרק אליו
    # (‏health_hooks["port_listeners"], כמו כל hook אחר), ומאוכלס אחרי
    # שהאפליקציות קיימות.
    listeners = ports.Listeners()

    # ‏#1088: מה שהקונסולה צריכה כדי להשלים את ההתקנה בהדלקת DHCP —
    # ומה שמסך הבריאות מציג על רשת ההפצה ועל כתובת כרטיס השרתים.
    primary_ip = None
    if args.primary_url:
        primary_host = urllib.parse.urlsplit(args.primary_url).hostname or ""
        try:
            ipaddress.ip_address(primary_host)
            primary_ip = primary_host
        except ValueError:
            pass
    deploy_ctx = deploy_net.DeployContext(
        state=deploy, agent_port=args.port, tftp_root=Path(args.tftp_root),
        repo_dir=Path(args.repo_dir) if args.repo_dir else Path(__file__).resolve().parents[1],
        servers_interface=console_iface, primary_ip=primary_ip)

    def servers_nic_status():
        if not console_iface:
            return None
        try:
            address = ifaddr.read_ipv4(console_iface)
        except ifaddr.AddressLookupError as exc:
            return ifaddr.servers_nic_status(console_iface, None, None,
                                             lookup_failed=str(exc))
        return ifaddr.servers_nic_status(console_iface, address,
                                         ifaddr.dhclient_lease(console_iface),
                                         hostname=machine_hostname)

    # ‏#731/#738: runtime **אחד** — ‏DB, ספרייה, שולח, ה-sweep החד-פעמי —
    # ושלוש אפליקציות מעליו. תהליך אחד, שלושה מאזינים; לא שלושה תהליכים
    # על אותו SQLite (זה היה נגמר ב-"cannot commit" אקראי, ‏CLAUDE.md).
    #
    # ‏#141: ‏known_macs_hooks מודלק כאן, ורק כאן — במפורש, לא כברירת
    # מחדל בתוך create_runtime, כי הוספה/מחיקה של מכונה מהקונסולה קוראת
    # לאותם hooks בכל בדיקה בחבילה (registry.all_macs → dhcp.render_known_macs
    # → apply_known_macs). ברירת מחדל אמיתית שם הייתה כותבת ל-
    # ‏/etc/imagectl/known-macs ומריצה systemctl reload dnsmasq בכל הרצת
    # ‏pytest.
    runtime = create_runtime(args.data_dir, args.images, server_url,
                             boot_dir=args.boot_dir, interface=interface,
                             sender_portbase=args.sender_portbase,
                             storage_role=args.storage_role,
                             primary_url=args.primary_url,
                             known_macs_hooks={"apply": dhcp_host.apply_known_macs},
                             # ‏#1013: קובץ ה-dnsmasq הראשי נגזר מה-DB בעלייה —
                             # כאן בלבד, מאותו טעם כמו known_macs_hooks.
                             sync_dnsmasq=True,
                             # ‏#855: מותקן כאן תמיד — כמו known_macs_hooks,
                             # ומאותו טעם: ברירת מחדל ב-create_runtime הייתה
                             # קוראת את קובץ המכונה בכל הרצת pytest.
                             identity_hooks={"leases": identity.LeaseFile(args.dhcp_leases)},
                             extra_cmdline=tuple(args.extra_cmdline.split()),
                             console_allowed_networks=console_allowed_networks,
                             repo_dir=args.repo_dir,
                             console_tls=console_tls,
                             health_hooks={"port_listeners": listeners,
                                           "deploy": lambda: deploy_ctx.state,
                                           "servers_nic": servers_nic_status},
                             deploy=deploy_ctx)
    agent_app = create_agent_app(runtime)
    console_app = create_console_app(runtime)
    kiosk_app = create_kiosk_app(runtime)

    # ‏#996: ה-`Config` נבנה פעם אחת; ה-`Server` נבנה מחדש בכל פתיחה
    # (‏uvicorn.Server אינו רב-פעמי), ולכן המנהל מקבל **יצרנים**.
    agent_config = uvicorn.Config(
        agent_app, host=args.host, port=args.port, log_level="info")
    # ‏#770: הקונסולה על כרטיס הניהול (`--console-host`) בלבד — לא `--host`.
    # ‏#703 (tracer 5): ‏`ssl_certfile`/`ssl_keyfile` של uvicorn (stdlib ssl)
    # על **כל** מאזין קונסולה — ורק עליהם; הסוכן והקיוסק נשארים http.
    console_ssl = console_tls.uvicorn_kwargs() if console_tls else {}
    console_scheme = "https" if console_tls else "http"
    from .interserver_auth import is_loopback

    def console_servers(addr: str | None) -> tuple[list, list[str]]:
        """היצרנים והכתובות של מאזין הקונסולה לכתובת נתונה — פונקציה, כי
        ‏#1088 בונה אותם מחדש בכל חילוף כתובת של כרטיס השרתים.

        ‏#904: כשכרטיס הניהול אינו loopback, הקונסולה מאזינה **גם** על
        ‏127.0.0.1 — חלון ה-pairing והפינוי של המשני (`require_local`,
        ‏#740) נאכפים על כתובת ה-peer, ומחיבור לכרטיס הניהול ה-peer לעולם
        אינו loopback: במעבדה `POST /storage-pairing-window` החזיר 403 תמיד,
        והמפעיל נאלץ ל-`ssh -L`. ‏uvicorn 0.32 (דביאן 13) קושר `host` יחיד,
        ולכן זה `Server` שני על אותו loop — כמו 8080/8081. loopback אינו
        מרחיב את החשיפה (אותה מכונה בלבד), ולכן אינו שובר את fail-closed
        של #770. בלי כתובת על הכרטיס (#1088) — loopback בלבד, עד שתגיע.
        """
        hosts = [addr] if addr else []
        if not addr or not is_loopback(addr):
            hosts.append("127.0.0.1")
        configs = [uvicorn.Config(console_app, host=h, port=args.console_port,
                                  log_level="info", **console_ssl) for h in hosts]
        return [(lambda cfg=cfg: uvicorn.Server(cfg)) for cfg in configs], hosts

    console_factories, console_hosts = console_servers(console_addr)
    console_binds = " + ".join(f"{console_scheme}://{h}:{args.console_port}"
                               for h in console_hosts)
    kiosk_config = uvicorn.Config(
        kiosk_app, host=args.host, port=args.kiosk_port, log_level="info")
    listeners.add("http_boot", [lambda: uvicorn.Server(agent_config)],
                  port=args.port, hosts=[args.host])
    listeners.add("http_console", console_factories,
                  port=args.console_port, hosts=console_hosts)
    listeners.add("kiosk", [lambda: uvicorn.Server(kiosk_config)],
                  port=args.kiosk_port, hosts=[args.host])
    print(f"agent on {args.host}:{args.port}"
          f"  console on {console_binds}"
          f"  kiosk on {args.host}:{args.kiosk_port}")
    if console_iface:
        print(f"console interface: {console_iface} "
              f"({console_addr or 'no address yet — loopback until a lease arrives'})")
    if console_tls is not None:
        # טביעת האצבע מודפסת כדי שהמפעיל ישווה אותה למה שהדפדפן מציג
        # באישור החד-פעמי — ולא יאשר סתם (#703).
        print(f"console TLS: self-signed certificate {console_tls.cert_path}"
              f"  SHA-256 {console_tls.fingerprint_sha256}")
    else:
        print("console TLS: off (loopback only)")

    watchers = []

    def watch(iface: str, current: str | None, rebind) -> None:
        """‏#1088: דוגם את כתובת הכרטיס; בשינוי — יומן + מאזין מחדש.
        ‏rebind שנכשל מחזיר False כדי שהדוגם ינסה שוב בדגימה הבאה (למשל
        לפני שלולאת האירועים עלתה)."""
        def on_change(old, new):
            label = f"{iface} {old or '-'} → {new or '-'}"
            print(f"address changed on {label}")
            try:
                rebind(new)
            except Exception as exc:                                # noqa: BLE001
                print(f"rebind on {label} failed: {exc}")
                return False
            journal(runtime.conn, "bind_address_changed", label)
            return True

        watchers.append(ifaddr.AddressWatcher(
            iface, current=current, on_change=on_change,
            on_error=lambda msg: print(f"address lookup on {iface} failed: {msg}")).start())

    if console_iface:
        def rebind_console(new: str | None) -> None:
            factories, hosts = console_servers(new)
            listeners.request_rebind("http_console", hosts=hosts, factories=factories)
            if new and console_tls is not None and new not in console_tls.sans:
                journal(runtime.conn, "console_tls_by_name_only",
                        f"הכתובת השתנתה ל-{new} — התעודה תקפה לפי שם "
                        f"{machine_hostname} בלבד")
        watch(console_iface, console_addr, rebind_console)

    # ‏#740: מאזין ה-enrollment הבין-שרתי (mTLS 1.3) עולה רק על משני, וכשניתן
    # ‏--interserver-host. הוא threaded (pyOpenSSL terminator) לצד ה-uvicorn —
    # ‏#996: גם הוא במנהל המאזינים, כמתג `interserver` (start/stop של ה-thread).
    from . import storage_nodes
    if args.interserver_host and storage_nodes.role(runtime.conn) == "secondary":
        from . import interserver_api
        if inter_iface:
            host_sans = [h for h in (inter_addr, machine_hostname) if h]
        else:
            host_sans = [args.interserver_host]
        if args.interserver_url:
            from .interserver_auth import parse_interserver_url
            host_sans.append(parse_interserver_url(args.interserver_url)[0])
        ident = storage_nodes.ensure_identity(runtime.conn, args.data_dir,
                                              host_sans=host_sans)

        def make_interserver_start(bind_addr: str | None):
            def start_interserver():
                if not bind_addr:
                    raise OSError(f"interserver: לכרטיס {inter_iface} אין כתובת עדיין")
                server = interserver_api.InterserverTLSServer(
                    runtime.ctx, bind_addr, args.interserver_port,
                    ident["cert_pem"], ident["key_pem"], data_dir=args.data_dir).start()
                print(f"interserver (mTLS) on {bind_addr}:{server.port}"
                      f"  node_id {ident['node_id']}  spki {ident['server_spki'][:16]}…")
                return server
            return start_interserver

        listeners.add_threaded("interserver", start=make_interserver_start(inter_addr),
                               stop=lambda server: server.stop(),
                               port=args.interserver_port,
                               hosts=[inter_addr or args.interserver_host])
        if inter_iface:
            watch(inter_iface, inter_addr, lambda new: listeners.request_rebind(
                "interserver", hosts=[new or inter_iface],
                start=make_interserver_start(new)))

    # המצב השמור (‏`port:<id>` ב-DB) קובע מה נקשר בעלייה.
    listeners.want_open = lambda port_id: ports.enabled(runtime.conn, port_id)
    try:
        asyncio.run(serve_all(listeners))
    finally:
        for watcher in watchers:
            watcher.stop()


async def serve_all(listeners) -> None:
    """מריץ את כל המאזינים בתהליך אחד, עם כיבוי מתואם — ‏#996: דרך
    `ports.Listeners`, שגם סוגר ופותח אותם בזמן ריצה.

    ‏`listeners.want_open(port_id)` הוא המצב השמור ב-DB: פורט שהמפעיל כיבה אינו
    מופעל כלל בעלייה. ברגע שמאזין יצא **שלא כי ביקשנו** — Ctrl-C או
    חריגה — כל השאר מתבקשים לצאת, ואז ממתינים להם לפני החזרה. חריגה
    (כשל bind בעלייה הוא הנפוץ) מתגלגלת החוצה **אחרי** שכולם נעצרו: שרת
    ניהול שלא הצליח לתפוס את הפורט אינו 'עלה בהצלחה', ואסור שיֵרָאה כך
    בזמן שהסוכן ממשיך לבדו (עיקרון 5). נבדק ב-tests/test_port_listeners.py."""
    # מה שהמפעיל כיבה בדף הפורטים לא עולה — ונאמר, כדי שהשורה
    # "agent on … kiosk on …" למעלה לא תיקרא כאילו הכול מאזין.
    disabled = [p for p in listeners.ids() if not listeners.want_open(p)]
    if disabled:
        print(f"ports disabled by operator (not bound): {', '.join(disabled)}")
    await listeners.serve()


if __name__ == "__main__":
    main()
