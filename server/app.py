"""הרכבת האפליקציה — כאן כל החלקים מתחברים.

זה הקובץ שסוגר את הלולאה: אותו resolver שעונה ל-hello מוזרק גם ל-endpoint
של תפריט ה-GRUB (boot/http.py). אין שאילתה שנייה ואין ממשק שני.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi

from .api import ServerContext, create_agent_router
from .console_api import create_console_router
from .db import journal
from . import wol
from .sender import SenderEngine
from .station import create_station_router
from .console_library import create_library_router
from .branding import create_branding_router
from .capture import create_agent_capture_router, create_console_capture_router
from .console_net import create_net_router
from .console_dhcp import create_dhcp_router
from .console_netcfg import create_netcfg_router, drain_crumbs
from .db import connect
from .health import create_health_router
from .hello import make_resolver
from .images import ImageLibrary
from .room import CLONERS_GROUP, create_room_router
from .sessions import SessionStore
from .users import ensure_admin
from . import ssh_switch
from .work_areas import sweep as sweep_work_areas

STATIC_DIR = Path(__file__).parent / "static"


def create_app(
    data_dir: str | Path,
    images_root: str | Path,
    server_base: str,
    *,
    now_fn=None,
    sender_runner=None,
    sender_portbase: int | None = None,
    wol_send=None,
    interface: str | None = None,
    dhcp_hooks: dict | None = None,
    health_hooks: dict | None = None,
    netcfg_hooks: dict | None = None,
    netcfg_state_dir: str | Path | None = None,
    boot_dir: str | Path | None = None,
    extra_cmdline: tuple[str, ...] = (),
) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(data_dir / "imagectl.db")
    library = ImageLibrary(images_root)

    # ‏#88: מה שקליטה שהשרת מת באמצעה השאירה בשורש הספרייה נסחף כאן —
    # אבל רק מה שטבלת המשימות מוכיחה שהוא יתום. ההנמקה המלאה, כולל למה
    # אוטומטי ולא דיווח-והמפעיל-ימחק, בראש work_areas.py. עולה מיד אחרי
    # פתיחת ה-DB: זה מקור הראיה, ואין מה לסחוף בלעדיו.
    sweep_work_areas(conn, library.root)

    # ‏#56: זרוע ההחזרה (`imagectl-netrollback`) רצה כשהשרת **לא** רץ —
    # זו כל מטרתה — ולכן אין לה חיבור ל-DB. היא משאירה פירור על הדיסק,
    # והוא הופך לשורת יומן כאן, בהפעלה שאחריה, עם זמן ההחזרה האמיתי.
    # מיד אחרי פתיחת ה-DB ולפני שהקונסולה יכולה לענות: מי שפותח את
    # היומן אחרי אתחול חייב לראות שם ששינוי הרשת שלו לא נתפס.
    #
    # ברירת המחדל של התיקייה נגזרת מ-`--data-dir`, וביחידה זה בדיוק
    # `netcfg_host.STATE_DIR` — שני הצדדים חייבים להסכים, אחרת הסמן
    # נכתב במקום אחד ונקרא באחר, וההגנה פשוט לא קיימת.
    netcfg_dir = Path(netcfg_state_dir) if netcfg_state_dir else data_dir / "netcfg"
    netcfg_dir.mkdir(parents=True, exist_ok=True)

    # ‏WoL יוצא על **וילן ההפצה בלבד**. בלי הכפייה הזו השידור הולך לפי
    # טבלת הניתוב — ובשרת דו-כרטיסי ברירת המחדל היא הרשת הרגילה של
    # המכללה. אז מחשבי השיכפול לא מתעוררים (הם בוילן ההפצה), והמכללה
    # מקבלת ברודקאסט שאין לה מה לעשות איתו. נמדד במעבדה (בדיקה 7.2,
    # 2026-08-29): ‏12 חבילות על eth1 ואפס על eth0.
    #
    # ‏#44 הוסיף את היכולת לכפות ממשק ואת הטסטים סביבה — אבל הטסטים
    # מזריקים שולח, ואיש לא בנה אחד למסלול האמיתי: `wol_send` נשאר
    # `None` וכל שידור נפל לברירת המחדל חסרת-הממשק. יכולת שקיימת ואינה
    # מחוברת נראית בדיוק כמו יכולת שעובדת (עיקרון 5).
    if wol_send is None and interface:
        wol_send = wol.broadcast_sender(interface)

    # מנוע השידור מחובר למעברי המצב של הסבב: running מתחיל לשדר,
    # סגירה עוצרת. ניהול הסבב עצמו לא יודע מה זה udpcast.
    #
    # תהליכון השידור כותב ליומן, ופעם היה צריך לשם כך חיבור משלו:
    # sqlite לא סובל שני תהליכונים שמנהלים טרנזאקציות על אותו חיבור.
    # היום ההפרדה הזאת נמצאת ב-db.Database, שנותן חיבור לכל תהליכון —
    # לא רק לתהליכון השידור אלא גם לתהליכוני המאגר של uvicorn, שמריצים
    # כל endpoint סינכרוני וכל dependency סינכרוני של הקונסולה.
    # ‏#201: `sender_portbase` הוא הפורט שהשידור **באמת** יתפוס. הוא נשאר
    # `None` בייצור, ואז נופלים על `DEFAULT_PORTBASE` (9000) — ברירת המחדל
    # לא זזה. מי שמעביר ערך הוא מי שאסור לו להתנגש בהפצה אמיתית: ה-e2e,
    # שמריץ שרת אמיתי בתת-תהליך ולכן אין לאן להזריק לו שולח מזויף. הגנה
    # שאינה תלויה בכך שמישהו יזכור לנקות — פורט שאינו יכול להתנגש (#156).
    sender = SenderEngine(
        library,
        interface=interface,
        on_event=lambda event, detail: journal(conn, event, detail),
        **({"runner": sender_runner} if sender_runner else {}),
        **({"portbase": sender_portbase} if sender_portbase is not None else {}),
    )
    def wake_class(group_id: str, opener_mac: str | None,
                   roster: list[str] | None = None) -> None:
        # סבב שנפתח — מכל מקור — מעיר את מחשבי הכיתה, חוץ מזה שפתח.
        # סבב עם בחירת מחשבים מעיר רק את הנבחרים.
        woken = wol.wake_group(conn, group_id, exclude_mac=opener_mac,
                               only=set(roster) if roster else None,
                               **({"send": wol_send} if wol_send else {}))
        if woken:
            journal(conn, "wol_sent", f"{group_id} count={woken}")

    store = SessionStore(
        conn,
        on_running=sender.start,
        on_closed=lambda session_id: sender.stop(session_id),
        on_opened=wake_class,
        **({"now_fn": now_fn} if now_fn else {}),
    )
    ctx = ServerContext(conn=conn, library=library, store=store, sender=sender)
    drain_crumbs(ctx, netcfg_dir)

    app = FastAPI(title="ImageCtl", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.state.data_dir = data_dir

    app.include_router(create_agent_router(ctx, server_base))
    app.include_router(create_console_router(ctx))
    app.include_router(create_library_router(ctx))
    app.include_router(create_net_router(ctx))
    app.include_router(create_dhcp_router(ctx, dhcp_hooks))
    app.include_router(create_netcfg_router(ctx, netcfg_dir, netcfg_hooks))
    app.include_router(create_health_router(ctx, server_base, health_hooks))
    app.include_router(create_branding_router(ctx, data_dir))
    app.include_router(create_agent_capture_router(ctx))
    app.include_router(create_console_capture_router(ctx))
    app.include_router(create_station_router(ctx))
    app.include_router(create_room_router(
        ctx,
        wake=lambda: wol.wake_group(
            conn, CLONERS_GROUP, **({"send": wol_send} if wol_send else {})
        ),
    ))
    # כל מה ש-GRUB נוגע בו — התפריט, הקרנל וה-initramfs — מוגש מאפליקציית
    # ASGI גולמית: ה-HTTP של GRUB מזהה Content-Length רק באותיות גדולות,
    # ו-Starlette ממקטין כותרות. ההסבר המלא ב-boot/http.py (issue #12).
    # ‏extra_cmdline: תוספות מפעיל לשורת הקרנל (קונסולה טורית) דרך הגדרה
    # — לא בעריכת קוד על השרת, שהולידה fork חי (#18).
    #
    # התצורה נבנית **בכל בקשה**, כי `imagectl.debug` כבר אינו תוספת
    # מפעיל אלא מתג בקונסולה (#83): הוא פותח SSH ומעטפת טכנאי בכל תחנה
    # שעולה, וצריך להיות ניתן לכיבוי בלי לגעת ביחידת systemd ובלי
    # להפעיל מחדש. ‏`station_cmdline` גם *מסיר* את הדגל מתוספות המפעיל
    # כשהמתג כבוי — שני מקורות אמת לאותה דלת נגמרים בכך שהישן גובר בשקט.
    resolve = make_resolver(conn, library, store)

    async def boot_asgi(scope, receive, send):
        await create_boot_asgi(
            resolve=resolve,
            config=GrubConfig(
                server_base=server_base,
                extra_cmdline=ssh_switch.station_cmdline(
                    extra_cmdline, ssh_switch.stations_enabled(conn)),
            ),
            boot_dir=boot_dir,
        )(scope, receive, send)

    app.mount("/boot", boot_asgi)

    app.mount("/console", StaticFiles(directory=STATIC_DIR, html=True), name="console")

    # middleware גולמי ולא @app.middleware: העטיפה של Starlette בונה כל
    # תשובה מחדש וממקטינה את שמות הכותרות — כולל של ‎/boot, שם GRUB חייב
    # "Content-Length" באותיות גדולות (ראו boot/http.py). כאן נוגעים אך
    # ורק בתשובות הקונסולה; כל השאר עובר כמות שהוא.
    class ConsoleNoStaleCache:
        """קבצי הקונסולה מתעדכנים עם השרת. בלי revalidation, עדכון גרסה
        משאיר אצל המשתמשים JS ישן מול API חדש — באגים בלתי ניתנים לשחזור."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] != "http" or not scope["path"].startswith("/console"):
                await self.inner(scope, receive, send)
                return

            async def send_no_cache(message):
                if message["type"] == "http.response.start":
                    headers = [(k, v) for k, v in message.get("headers", [])
                               if k.lower() != b"cache-control"]
                    headers.append((b"Cache-Control", b"no-cache"))
                    message = {**message, "headers": headers}
                await send(message)

            await self.inner(scope, receive, send_no_cache)

    app.add_middleware(ConsoleNoStaleCache)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/console/")

    # בהתקנה טרייה: משתמש admin עם סיסמה חד-פעמית, מודפסת לטרמינל בלבד.
    password = ensure_admin(conn)
    if password:
        print(f"\n  first run: console user 'admin', password: {password}\n", flush=True)

    return app
