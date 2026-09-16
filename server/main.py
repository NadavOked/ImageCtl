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
    parser.add_argument("--console-host", default="127.0.0.1",
                        help="כתובת ה-bind של הקונסולה — כרטיס הניהול/"
                             "המשרדים בלבד. ברירת מחדל loopback (fail-closed); "
                             "לעולם לא 0.0.0.0 בלי בקשה מפורשת")
    # ‏#738 (tracer 2 של #703): הקיוסק (מסך התחנה על וילן ההפצה) מאזין
    # על פורט **נפרד** מהקונסולה — allowlist קשיח, בלי נתיבי ניהול.
    # כאן עדיין `0.0.0.0` וללא TLS (משמר-התנהגות); ‏bind פר-ממשק הוא
    # tracer 4.
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
                        help="כתובת ה-bind של מאזין ה-enrollment הבין-שרתי")
    parser.add_argument("--interserver-port", type=int, default=8443,
                        help="פורט מאזין ה-enrollment הבין-שרתי (mTLS 1.3)")
    parser.add_argument("--interserver-url", default=None,
                        help="הכתובת הבין-שרתית המפורשת שהמשני מציג "
                             "(https://host:port/…, פורט מפורש חובה)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

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

    from . import dhcp_host
    from .app import (create_agent_app, create_console_app, create_kiosk_app,
                      create_runtime)

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
    runtime = create_runtime(args.data_dir, args.images, args.server_url,
                             boot_dir=args.boot_dir, interface=interface,
                             sender_portbase=args.sender_portbase,
                             storage_role=args.storage_role,
                             primary_url=args.primary_url,
                             known_macs_hooks={"apply": dhcp_host.apply_known_macs},
                             extra_cmdline=tuple(args.extra_cmdline.split()),
                             console_allowed_networks=console_allowed_networks)
    agent_app = create_agent_app(runtime)
    console_app = create_console_app(runtime)
    kiosk_app = create_kiosk_app(runtime)

    agent_server = uvicorn.Server(uvicorn.Config(
        agent_app, host=args.host, port=args.port, log_level="info"))
    # ‏#770: הקונסולה על כרטיס הניהול (`--console-host`) בלבד — לא `--host`.
    console_server = uvicorn.Server(uvicorn.Config(
        console_app, host=args.console_host, port=args.console_port,
        log_level="info"))
    kiosk_server = uvicorn.Server(uvicorn.Config(
        kiosk_app, host=args.host, port=args.kiosk_port, log_level="info"))
    print(f"agent on {args.host}:{args.port}"
          f"  console on {args.console_host}:{args.console_port}"
          f"  kiosk on {args.host}:{args.kiosk_port}")

    # ‏#740: מאזין ה-enrollment הבין-שרתי (mTLS 1.3) עולה רק על משני, וכשניתן
    # ‏--interserver-host. הוא threaded (pyOpenSSL terminator) לצד ה-uvicorn.
    interserver = None
    from . import storage_nodes
    if args.interserver_host and storage_nodes.role(runtime.conn) == "secondary":
        from . import interserver_api
        host_sans = [args.interserver_host]
        if args.interserver_url:
            from .interserver_auth import parse_interserver_url
            host_sans.append(parse_interserver_url(args.interserver_url)[0])
        ident = storage_nodes.ensure_identity(runtime.conn, args.data_dir,
                                              host_sans=host_sans)
        interserver = interserver_api.InterserverTLSServer(
            runtime.ctx, args.interserver_host, args.interserver_port,
            ident["cert_pem"], ident["key_pem"], data_dir=args.data_dir).start()
        print(f"interserver (mTLS) on {args.interserver_host}:{interserver.port}"
              f"  node_id {ident['node_id']}  spki {ident['server_spki'][:16]}…")

    try:
        asyncio.run(serve_all([agent_server, console_server, kiosk_server]))
    finally:
        if interserver is not None:
            interserver.stop()


async def serve_all(servers) -> None:
    """מריץ כמה `uvicorn.Server` במקביל בתהליך אחד, עם כיבוי מתואם.

    ברגע שאחד יצא — נורמלית (Ctrl-C) או בחריגה — כל השאר מתבקשים לצאת
    (`should_exit`), ואז אנחנו ממתינים להם לפני החזרה. חריגה מכל שרת
    (כשל bind הוא הנפוץ — uvicorn עושה `sys.exit` ל-SystemExit) מתגלגלת
    החוצה **אחרי** שכולם נעצרו: שרת ניהול שלא הצליח לתפוס את הפורט אינו
    'עלה בהצלחה', ואסור שיֵרָאה כך בזמן שהסוכן ממשיך לבדו (עיקרון 5)."""
    tasks = [asyncio.ensure_future(s.serve()) for s in servers]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # מספיק שאחד יצא כדי להוריד את כולם — אין מצב חצי-שרת.
        for server in servers:
            server.should_exit = True
    pending = [t for t in tasks if not t.done()]
    if pending:
        await asyncio.wait(pending)
    # מגלגלים החוצה את החריגה הראשונה שנפלה (bind וכו'), אם הייתה.
    for task in tasks:
        if not task.cancelled() and task.exception() is not None:
            raise task.exception()


if __name__ == "__main__":
    main()
