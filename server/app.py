"""הרכבת האפליקציה — כאן כל החלקים מתחברים.

זה הקובץ שסוגר את הלולאה: אותו resolver שעונה ל-hello מוזרק גם ל-endpoint
של תפריט ה-GRUB (boot/http.py). אין שאילתה שנייה ואין ממשק שני.

‏#731 (tracer 1 של #703): המצב המשותף — ‏DB, ספרייה, שולח, משימות-רקע
והאתחול החד-פעמי — יושב ב-`ServerRuntime` **אחד** ש-`create_runtime`
בונה פעם אחת. שתי האפליקציות (`create_agent_app` לסוכן, `create_console_app`
לקונסולה) נבנות מאותו runtime, ולכן אין שני DB, אין שני sweep ואין שני
תהליכי-רקע. ‏`create_app` נשאר כמעטפת דקה שמרכיבה את שתיהן על אפליקציה
אחת — התנהגות זהה למה שהיה, בשביל הבדיקות וה-e2e.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from boot.grub_menu import GrubConfig
from boot.http import create_boot_asgi, gui_initrd_path

from . import boottrace, dhcp, registry
from .api import ServerContext, create_agent_router
from .console_api import create_console_router
from .console_source_guard import ConsoleSourceGuard, Network
from .console_storage import create_storage_router
from .db import journal
from . import wol
from . import direct
from .sender import SenderEngine
from .station import create_station_router
from .console_library import create_library_router
from .console_drivers import create_agent_drivers_router, create_drivers_router   # #720
from .drivers import DriverLibrary
from .branding import create_branding_router
from .capture import create_agent_capture_router, create_console_capture_router
from .console_net import create_net_router
from .console_dhcp import create_dhcp_router
from .console_netcfg import create_netcfg_router, drain_crumbs
from .db import connect
from .health import create_health_router
from .update import (PUBLIC_UPDATE_URL, create_update_router, current_version,
                     default_hooks as default_update_hooks)
from .hello import make_resolver, off_deploy_vlan
from .images import ImageLibrary
from .kiosk import create_kiosk_router
from . import room
from .room import CLONERS_GROUP, create_room_router
from .sessions import SessionStore
from .users import ensure_admin
from . import ssh_switch
from . import storage_nodes
from . import monitor
from .monitor import create_monitor_router
from .work_areas import sweep as sweep_work_areas

log = logging.getLogger("imagectl.app")

STATIC_DIR = Path(__file__).parent / "static"

#: כל כמה שניות שעון-הרקע מריץ את הדופק של חדר השיכפולים (#456). זהו רק
#: רשת-הביטחון לחדר **שקט לגמרי**: כשמכונות מדברות, ה-hello שלהן מקדם את
#: ה-tick בקצב ~2ש' ממילא. הסף להכרזת אובדן הוא `room.LOST_SECONDS` (180),
#: ולכן דגימה כל 5ש' מכריזה אובד תוך ~5ש' אחרי שהסף חלף — מספיק דק.
ROOM_CLOCK_SECONDS = 5.0


def _off_vlan_decline(mac: str, step: str) -> bool:
    """רושם שאינו רושם: פירור אתחול (#400) שהתקבל מחוץ לווילן ההפצה (#584).

    ‏`GET /boot/step?mac=&s=` לוקח את ה-MAC מהשאילתה, וזה אינו זהות;
    מחוץ לווילן ההפצה אין ראיה שהפונה הוא המכונה. מחזיר False —
    **ראיה חיובית** שהפירור לא נרשם, לא היעדר חריגה — ורושם ביומן מי
    ניסה ומה. ‏`record_step` מחזיר ל-GRUB 200 בכל מקרה (שקט כלפי מסך
    האתחול, רועש ביומן), ולכן האבחון נשאר אמין בלי להפיל אתחול."""
    log.warning("boot step %s for %s not recorded — off deployment vlan (#584)",
                step, mac)
    return False


class ConsoleNoStaleCache:
    """קבצי הקונסולה מתעדכנים עם השרת. בלי revalidation, עדכון גרסה
    משאיר אצל המשתמשים JS ישן מול API חדש — באגים בלתי ניתנים לשחזור.

    middleware גולמי ולא @app.middleware: העטיפה של Starlette בונה כל
    תשובה מחדש וממקטינה את שמות הכותרות — כולל של ‎/boot, שם GRUB חייב
    "Content-Length" באותיות גדולות (ראו boot/http.py). כאן נוגעים אך
    ורק בתשובות הקונסולה; כל השאר עובר כמות שהוא."""

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


@dataclass
class ServerRuntime:
    """המצב המשותף לשתי האפליקציות — בעל יחיד (#731/#703).

    ‏`create_runtime` בונה אותו **פעם אחת** ומריץ בו את האתחול החד-פעמי
    (‏`sweep_work_areas`, ‏`drain_crumbs`, ‏`ensure_admin`); ‏`create_agent_app`
    ו-`create_console_app` מקבלים אותו כמות שהוא. שני runtime = שני DB,
    שני sweep, שני תהליכי-רקע — וזה בדיוק מה שהפיצול הזה מונע."""

    ctx: ServerContext
    conn: Connection
    library: ImageLibrary
    store: SessionStore
    server_base: str
    data_dir: Path
    netcfg_dir: Path
    wol_send: object | None = None
    boot_dir: str | Path | None = None
    extra_cmdline: tuple[str, ...] = ()
    dhcp_hooks: dict | None = None
    health_hooks: dict | None = None
    netcfg_hooks: dict | None = None
    known_macs_hooks: dict | None = None
    #: #748: תיקיית עץ השרת (ל-``git describe``/``fetch``/``checkout``)
    #: וה-hooks של בדיקת/הרצת העדכון. ברירת המחדל בפועל נקבעת ב-main.py.
    repo_dir: str | Path | None = None
    update_hooks: dict | None = None
    #: הקצב של שעון-הרקע של החדר (#456). ברירת המחדל היא הייצור; בדיקות
    #: מעבירות ערך קצר כדי שהלולאה תדגום מהר בלי להמתין 5ש'.
    room_clock_interval: float = ROOM_CLOCK_SECONDS
    # ‏#151: רשתות מותרות ל-`/console` ו-`/api/console` לפי כתובת מקור —
    # שכבה בתוך השרת, לא תלויה בחומת אש חיצונית. ``None``/ריק = השומר
    # אינו פעיל (התנהגות היום — ‎--console-host בלבד), בדיוק כמו
    # ‏`storage_role`: לא לשבור פריסה קיימת שלא ביקשה את השכבה הזו.
    console_allowed_networks: tuple[Network, ...] | None = None


async def _room_clock(rt: ServerRuntime, interval: float) -> None:
    """לולאת הרקע שמריצה את הדופק של חדר השיכפולים בקצב קבוע — #456.

    ‏**גל שאיש אינו צופה בו חייב להתקדם.** בלי הלולאה הזו `tick` רץ רק
    מ-hello של משכפל או מ-GET `/overview`; חדר שכל מכונותיו נעלמו אינו
    מייצר אף אחד מהם, ו-`_mark_lost` לעולם אינו מפקיע את החברים השקטים
    (#456, והזנב של #659). היא המקור ה**בלתי-תלוי-בצופה** ל-tick.

    ‏`tick` עושה I/O סינכרוני מול sqlite; מריצים אותו ב-worker thread כדי
    לא לחסום את לולאת האירועים המשותפת לשלושת השרתים. ‏`db.Database` נותן
    חיבור לכל תהליכון (WAL) — אותו מודל בדיוק כמו מסלול ה-hello של uvicorn,
    שגם הוא רץ בתהליכון מהמאגר. ‏`room.sweep` לעולם אינו זורק, ולכן דגימה
    שנכשלה נרשמת ביומן וממשיכה — הלולאה אינה מתה על תקלה חולפת.
    """
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(room.sweep, rt.conn, rt.store)


def _install_room_clock(app: FastAPI, rt: ServerRuntime) -> None:
    """מתקין את שעון-הרקע של החדר על מחזור-החיים (`lifespan`) של האפליקציה.

    מותקן על **אפליקציה אחת בלבד בכל תהליך** — הקונסולה בייצור, והמעטפת
    המשולבת בבדיקות/e2e — אחרת שלוש האפליקציות שחולקות runtime היו מריצות
    שלושה שעונים על אותו DB. ‏`lifespan` (ולא תשתית תזמון חדשה) הוא ההכרעה
    ב-#456: המשימה עולה ב-startup ומבוטלת ב-shutdown, בלי מודול scheduler.
    """
    interval = rt.room_clock_interval

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        task = asyncio.create_task(_room_clock(rt, interval))
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app.router.lifespan_context = lifespan


def create_runtime(
    data_dir: str | Path,
    images_root: str | Path,
    server_base: str,
    *,
    now_fn=None,
    sender_runner=None,
    sender_portbase: int | None = None,
    # שתי תקרות ההמתנה של udpcast. ‏None = ברירת המחדל של `sender.py`
    # (כמו `sender_portbase`): הערך בייצור אינו זז, ומי שצריך תקרה קצרה —
    # מעבדה, טסט, ‏e2e — מעביר אותה כאן במקום לחכות דקות (#341).
    sender_max_wait: int | None = None,
    sender_start_timeout: float | None = None,
    wol_send=None,
    interface: str | None = None,
    dhcp_hooks: dict | None = None,
    health_hooks: dict | None = None,
    netcfg_hooks: dict | None = None,
    known_macs_hooks: dict | None = None,
    # ‏#748: ברירת המחדל None משאירה את ``repo_dir`` כתיקיית העבודה של
    # ``server/update.py`` בזמן הריצה (main.py הוא היחיד שמעביר ערך אמיתי).
    repo_dir: str | Path | None = None,
    update_hooks: dict | None = None,
    netcfg_state_dir: str | Path | None = None,
    boot_dir: str | Path | None = None,
    extra_cmdline: tuple[str, ...] = (),
    # ‏Storage Nodes (#655/#723): תפקיד ההתקנה. ‏None = לא הועבר (בדיקות,
    # וקוד ישן) ואז ההגדרה נשארת כפי שהיא — התפקיד נקרא ממילא fail-closed
    # (‏storage_nodes.role). ‏main מעביר תמיד את הערך מהדגל (ברירת מחדל
    # standalone), וזו הכתיבה בהפעלה.
    storage_role: str | None = None,
    primary_url: str | None = None,
    # ‏#151: ``None``/ריק = השומר אינו מותקן. ``main`` מעביר את הערך של
    # ‎--console-allow-from (מפוענח ומאומת שם); הבדיקות מעבירות ישירות.
    console_allowed_networks: tuple[Network, ...] | None = None,
    # ‏#456: הקצב של שעון-הרקע של החדר. ברירת המחדל היא הייצור; בדיקות
    # מעבירות ערך קצר כדי שהלולאה תדגום מהר.
    room_clock_interval: float = ROOM_CLOCK_SECONDS,
) -> ServerRuntime:
    """בונה את המצב המשותף פעם אחת ומריץ את האתחול החד-פעמי.

    כל מה שהיה בראש `create_app` עד בניית ה-ctx יושב כאן עכשיו: פתיחת
    ה-DB, ה-sweep, ה-drain, יצירת המנהל. שתי האפליקציות נבנות ממנו."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    conn = connect(data_dir / "imagectl.db")

    # ‏#655/#723: תצורת ה-Storage Node נכתבת בהפעלה כשהיא הועברה (main).
    # ‏None = לא הועבר — משאירים את ההגדרה הקיימת; התפקיד נקרא ממילא
    # fail-closed. הוולידציה (משני מחייב כתובת-אב) נעשתה כבר ב-main.
    if storage_role is not None:
        storage_nodes.persist_config(conn, storage_role, primary_url)

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
        **({"max_wait": sender_max_wait} if sender_max_wait is not None else {}),
        **({"start_timeout": sender_start_timeout}
           if sender_start_timeout is not None else {}),
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

    def on_running(session: dict) -> None:
        # ‏#715: גל שהמקור שלו הוא מחשב בנייה — המנוע של השרת **אינו**
        # משדר; מחשב הבנייה רואה `running` ב-hello הבא ומשדר בעצמו.
        # ביומן, כדי שגל שלא יצא ייראה כמה שהוא ולא כ"המנוע לא התחיל".
        if direct.is_direct_wave(conn, session["id"]):
            journal(conn, "send_delegated", f'{session["id"]} {session["image_id"]}')
            return
        sender.start(session)

    store = SessionStore(
        conn,
        on_running=on_running,
        on_closed=lambda session_id: sender.stop(session_id),
        on_opened=wake_class,
        **({"now_fn": now_fn} if now_fn else {}),
    )
    # ‏#720: ספריית חבילות הדרייברים — תיקייה ליד ה-DB, לא בספריית האימג'ים.
    ctx = ServerContext(conn=conn, library=library, store=store, sender=sender,
                        drivers=DriverLibrary(data_dir / "drivers"))
    drain_crumbs(ctx, netcfg_dir)

    # בהתקנה טרייה: משתמש admin עם סיסמה חד-פעמית, מודפסת לטרמינל בלבד.
    password = ensure_admin(conn)
    if password:
        print(f"\n  first run: console user 'admin', password: {password}\n", flush=True)

    # ‏#141: KNOWN_MACS_CONF הוא נגזרת של טבלת המכונות, לא מקור — קובץ
    # שנמחק או לא נכתב מעולם משאיר **כל** מכונה בלי dhcp-boot (עיקרון 5).
    # נכתב מחדש כאן, בעליית השרת, ולא רק כשמישהו עורך מכונה מהקונסולה
    # (console_api._sync_known_macs, אותם hooks בדיוק). ‏`known_macs_hooks`
    # הוא `None` כברירת מחדל בכוונה: ‏main.py הוא היחיד שמדליק אותו
    # במפורש — בלעדיו זו הרצת בדיקות, ואסור לה לגעת בדיסק/ב-dnsmasq.
    if known_macs_hooks is not None:
        error = known_macs_hooks["apply"](dhcp.render_known_macs(registry.all_macs(conn)))
        if error:
            journal(conn, "known_macs_apply_failed", error)

    return ServerRuntime(
        ctx=ctx, conn=conn, library=library, store=store,
        server_base=server_base, data_dir=data_dir, netcfg_dir=netcfg_dir,
        wol_send=wol_send, boot_dir=boot_dir, extra_cmdline=extra_cmdline,
        dhcp_hooks=dhcp_hooks, health_hooks=health_hooks, netcfg_hooks=netcfg_hooks,
        console_allowed_networks=console_allowed_networks,
        known_macs_hooks=known_macs_hooks,
        repo_dir=repo_dir, update_hooks=update_hooks,
        room_clock_interval=room_clock_interval,
    )


def _boot_asgi(rt: ServerRuntime):
    """אפליקציית ה-ASGI הגולמית של ‎/boot, בנויה מ-runtime משותף.

    כל מה ש-GRUB נוגע בו — התפריט, הקרנל וה-initramfs — מוגש מכאן:
    ה-HTTP של GRUB מזהה Content-Length רק באותיות גדולות, ו-Starlette
    ממקטין כותרות. ההסבר המלא ב-boot/http.py (issue #12). ‏extra_cmdline:
    תוספות מפעיל לשורת הקרנל דרך הגדרה — לא בעריכת קוד על השרת (#18).

    התצורה נבנית **בכל בקשה**, כי `imagectl.debug` כבר אינו תוספת מפעיל
    אלא מתג בקונסולה (#83): הוא פותח SSH ומעטפת טכנאי בכל תחנה שעולה,
    וצריך להיות ניתן לכיבוי בלי לגעת ביחידת systemd ובלי להפעיל מחדש.
    ‏`station_cmdline` גם *מסיר* את הדגל מתוספות המפעיל כשהמתג כבוי —
    שני מקורות אמת לאותה דלת נגמרים בכך שהישן גובר בשקט."""
    conn = rt.conn
    resolve = make_resolver(conn, rt.library, rt.store, rt.server_base)

    async def boot_asgi(scope, receive, send):
        # ‏#584: שביל הפירורים (#400) נרשם רק אם הבקשה התקבלה על וילן
        # ההפצה — אותה הכרעה בדיוק כמו הספירה של #536, ומאותו טעם:
        # ה-MAC שבשאילתה אינו זהות, ומחוץ לווילן אין ראיה שהפונה הוא
        # המכונה. פירור זר היה הופך "לא ידוע" ל"בדקנו, הגיע" (עיקרון 5)
        # ומאפס את `first_at` של שביל אמיתי. ההכרעה המודעת (#584): פירור
        # מחוץ לווילן **אינו נרשם**; המחיר המוצהר הוא שתחנת תרחיש-3 (#39)
        # שמאתחלת מחוץ לווילן מאבדת את שביל האבחון שלה. ‏boot/ אינו מכיר
        # טופולוגיית רשת, ולכן ההחלטה יושבת כאן, בדיוק כמו ה-resolver.
        off_vlan = off_deploy_vlan(scope, rt.server_base)
        await create_boot_asgi(
            # ‏#536: הסקופ של **הבקשה הזו** נכנס ל-resolver, שרק ממנו
            # אפשר לדעת על איזו מכתובות השרת היא התקבלה. הספירה של
            # ‏`bootguard` תלויה בזה, ולכן ה-resolver נקשר כאן ולא פעם
            # אחת למעלה. ‏`boot/` אינו מכיר טופולוגיית רשת ואינו צריך.
            resolve=lambda mac, ip: resolve(mac, ip, scope),
            config=GrubConfig(
                server_base=rt.server_base,
                # ‏#32: נבדק בכל בקשה, ומאותה סיבה שהתצורה כולה נבנית
                # בכל בקשה — ‏initramfs גרפי שנוסף או הוסר מתיקיית האתחול
                # תופס מיד, בלי הפעלה מחדש של השרת. הבדיקה זולה (stat
                # אחד), ובקשת אתחול היא ממילא אירוע נדיר לכל מכונה.
                gui_initrd_path=gui_initrd_path(rt.boot_dir),
                # #690: chain the monitor flag onto the SSH chain -- one source
                # of truth per gate, independent of each other (like SSH).
                extra_cmdline=monitor.station_cmdline(
                    ssh_switch.station_cmdline(
                        rt.extra_cmdline, ssh_switch.stations_enabled(conn)),
                    monitor.stations_enabled(conn)),
            ),
            boot_dir=rt.boot_dir,
            # ‏#400: שביל הפירורים. מוזרק כמו ה-resolver — ‏`boot/` אינו
            # מכיר DB, והשרת אינו מכיר את תחביר ה-GRUB. ‏#584: מחוץ לווילן
            # ההפצה הרושם דוחה את הפירור בגלוי במקום לכתוב אותו.
            record=(_off_vlan_decline if off_vlan
                    else lambda mac, step: boottrace.record(conn, mac, step)),
        )(scope, receive, send)

    return boot_asgi


def _add_agent_routes(app: FastAPI, rt: ServerRuntime) -> None:
    """הצד שהסוכן (initramfs) והקושחה רואים: ‎/api/v1 (‏hello/login/pulls/
    progress/disk-event/images), ‏capture הסוכן, מסך התחנה, ומאזין ה-‎/boot.

    ‏#824: `create_station_router` (‏`/api/v1/agent/{state,groups,sessions}`)
    הוא גם ה-API שהסוכן על המכונה צורך — ‏guistate.sh/guibridge.sh/
    guiparent.sh/classround.sh קוראים אותו דרך `$SERVER`, שהוא פורט הסוכן.
    ‏#738 חילץ אותו מהקונסולה לקיוסק בלבד, והסוכן קיבל 404 בכל דגימה;
    המעטפת המשולבת של הבדיקות (`create_app`) הסתירה זאת. הנתיבים האלה
    מחזירים את מה ש-hello מחזיר ממילא למכונה (ראה ה-docstring של
    ‏`station_state`), ואין ביניהם נתיב ניהול — הפעולה היחידה, פתיחת סבב,
    דורשת שם וסיסמה. ולכן הם חיים כאן **וגם** על הקיוסק (הדפדפן קורא
    אותם יחסית מ-‎:8082), ולא על הקונסולה (#703: הגבול הוא ה-socket)."""
    app.include_router(create_agent_router(rt.ctx, rt.server_base))
    app.include_router(create_agent_drivers_router(rt.ctx))   # #720
    app.include_router(create_agent_capture_router(rt.ctx))
    app.include_router(direct.create_direct_router(rt.ctx))   # #715
    app.include_router(create_station_router(rt.ctx))
    app.mount("/boot", _boot_asgi(rt))


def _kiosk_room_wake(rt: ServerRuntime):
    """שליחת ה-WoL של חדר השיכפולים — אותה פונקציה בקונסולה ובקיוסק."""
    conn = rt.conn
    return lambda: wol.wake_group(
        conn, CLONERS_GROUP, **({"send": rt.wol_send} if rt.wol_send else {})
    )


def _add_console_routes(app: FastAPI, rt: ServerRuntime) -> None:
    """הצד של הקונסולה/הניהול. ‏#738 (tracer 2 של #703): הקיוסק
    (`create_station_router` + הדף `/console/station`) חולץ מכאן ל-
    `kiosk_app`; הקונסולה שומרת את נתיבי הניהול שהיא-עצמה צורכת (כולל
    ‏room, שהוא גם מסך ניהול). מסך התחנה `/console/station/` עדיין מוגש
    מכאן כקובץ סטטי (חלק מעץ `/console`), אבל ה-API `/api/v1/agent/*` שלו
    כבר לא — הוא חי על הקיוסק ועל הסוכן (#824)."""
    ctx = rt.ctx
    # ‏#748: תיקיית העץ נופלת לתיקיית הקוד עצמה כשאין ``--repo-dir``
    # מפורש (בדיקות/הרצה ישירה) — ראו ``main.py``.
    repo_dir = rt.repo_dir or Path(__file__).resolve().parents[1]
    # ‏#915: הגרסה שמוצגת בקונסולה נקראת מאותו מקור כמו כפתור "עדכן"
    # (‏`git describe`, לא מחרוזת קשיחה ב-HTML) — ונקראת מחדש בכל
    # ‏`/me`, כי אחרי ``apply`` העץ כבר על תג אחר.
    update_hooks = {**default_update_hooks(), **(rt.update_hooks or {})}
    app.include_router(create_console_router(
        ctx, rt.known_macs_hooks,
        version=lambda: current_version(update_hooks, repo_dir)))
    app.include_router(create_storage_router(ctx, rt.data_dir))   # #727/#740
    app.include_router(create_library_router(ctx))
    app.include_router(create_drivers_router(ctx))   # #720
    app.include_router(create_net_router(ctx))
    app.include_router(create_dhcp_router(ctx, rt.dhcp_hooks))
    app.include_router(create_netcfg_router(ctx, rt.netcfg_dir, rt.netcfg_hooks))
    app.include_router(create_health_router(ctx, rt.server_base, rt.health_hooks))
    app.include_router(create_update_router(
        ctx, repo_dir, rt.server_base, rt.update_hooks,
        public_url=PUBLIC_UPDATE_URL))
    app.include_router(create_branding_router(ctx, rt.data_dir))
    app.include_router(create_monitor_router(ctx))   # #690: admin RFB proxy + settings
    app.include_router(create_console_capture_router(ctx))
    app.include_router(create_room_router(ctx, wake=_kiosk_room_wake(rt)))
    app.mount("/console", StaticFiles(directory=STATIC_DIR, html=True), name="console")
    # ‏#151: השומר לפי כתובת מקור, כשמוגדר — נרשם **לפני** ConsoleNoStaleCache
    # (‏add_middleware ראשון = השכבה החיצונית ביותר, נבדקת ראשונה, כדי
    # שבקשה חסומה לא תעבור אפילו דרך עיבוד התשובה של הקונסולה).
    # ‏None/ריק משאיר את ההתנהגות של היום (‎--console-host בלבד).
    if rt.console_allowed_networks:
        app.add_middleware(ConsoleSourceGuard, networks=rt.console_allowed_networks)
    app.add_middleware(ConsoleNoStaleCache)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/console/")


def create_agent_app(rt: ServerRuntime) -> FastAPI:
    """האפליקציה שהסוכן והקושחה מדברים איתה (‎:8080 בייצור)."""
    app = FastAPI(title="ImageCtl agent", docs_url=None, redoc_url=None)
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_agent_routes(app, rt)
    return app


def create_console_app(rt: ServerRuntime) -> FastAPI:
    """אפליקציית הקונסולה/הניהול (‎:8081 בייצור)."""
    app = FastAPI(title="ImageCtl console", docs_url=None, redoc_url=None)
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_console_routes(app, rt)
    # ‏#456: שעון-הרקע של החדר יושב על הקונסולה — היא האפליקציה שרצה תמיד
    # בייצור, והחדר הוא ממילא מסך ניהול. אפליקציה **אחת** בכל תהליך.
    _install_room_clock(app, rt)
    return app


def _add_kiosk_routes(app: FastAPI, rt: ServerRuntime) -> None:
    """הצד של הקיוסק (‎:8082 בייצור): רק ‏allowlist הקיוסק + הדף הסטטי.

    ‏#738: זו רשימת-ההיתר הקשיחה — capture/סבב/חדר בלבד, בלי שום נתיב
    ניהול. הדף הסטטי מוגש מעץ `/console` (מסך התחנה מפנה ל-`../console.css`
    ולכן צריך את השורש), אבל בלי ה-API של הניהול הדפים הניהוליים באותו
    עץ אינם מתפקדים — הגבול האמיתי הוא ה-socket וה-API allowlist. ‏`/`
    מפנה למסך התחנה, שהוא פני הקיוסק."""
    app.include_router(create_kiosk_router(rt.ctx, room_wake=_kiosk_room_wake(rt)))
    app.mount("/console", StaticFiles(directory=STATIC_DIR, html=True), name="console")
    app.add_middleware(ConsoleNoStaleCache)

    @app.get("/")
    def root() -> RedirectResponse:
        return RedirectResponse("/console/station/")


def create_kiosk_app(rt: ServerRuntime) -> FastAPI:
    """אפליקציית הקיוסק (‎:8082 בייצור) — ‏allowlist קשיח (#738)."""
    app = FastAPI(title="ImageCtl kiosk", docs_url=None, redoc_url=None)
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_kiosk_routes(app, rt)
    return app


def create_app(
    data_dir: str | Path,
    images_root: str | Path,
    server_base: str,
    **kwargs,
) -> FastAPI:
    """מעטפת דקה: runtime אחד + שני צדי הראוטרים על אפליקציה אחת.

    זו ההתנהגות הישנה בדיוק (הכול על פורט אחד), והיא נשמרת בשביל בדיקות
    היחידה וה-TestClient שמדברים עם שני הצדדים דרך לקוח אחד. הייצור
    (`main.py`) מריץ את `create_agent_app`/`create_console_app` על שני
    פורטים; הפיצול עצמו נבדק ב-`tests/test_app_split.py`.

    הפרמטרים הם של `create_runtime` (ראה שם); מועברים דרך `**kwargs`
    כדי שמקור אמת אחד יגדיר אותם."""
    rt = create_runtime(data_dir, images_root, server_base, **kwargs)
    app = FastAPI(title="ImageCtl", docs_url=None, redoc_url=None)
    app.state.ctx = rt.ctx
    app.state.runtime = rt
    app.state.data_dir = rt.data_dir
    _add_agent_routes(app, rt)
    _add_console_routes(app, rt)
    # ‏#824: נתיבי התחנה (`create_station_router`) מגיעים מ-`_add_agent_routes`
    # — לא נוספים כאן בנפרד. תוספת מפורשת למעטפת בלבד היא בדיוק מה שהסתיר
    # את ה-404 של הסוכן בייצור אחרי #738.
    # ‏#456: המעטפת המשולבת (בדיקות/e2e) מריצה גם את שעון-הרקע של החדר,
    # כדי שההתנהגות תהיה זהה לייצור. אפליקציה אחת → שעון אחד.
    _install_room_clock(app, rt)
    return app
