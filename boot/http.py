"""ImageCtl — הקצה שמגיש ל-GRUB את התפריט ואת קבצי האתחול.

זה הקצה הדק בלבד. כל ההיגיון יושב ב-grub_menu.py, וה-lookup עצמו שייך
לשרת — הוא מוזרק פנימה כפונקציה. ככה המחולל לא נוגע ב-DB ולא ממציא ממשק.

למה ASGI גולמי ולא FastAPI: ה-HTTP של GRUB מזהה את Content-Length רק
באותיות גדולות. Starlette ממקטין כל כותרת, ואז GRUB לא יודע מתי הקובץ
נגמר — הוא מחכה ~30 שניות, מוותר, והמכונה עולה מהדיסק במקום מהסוכן.
uvicorn עצמו משמר את האותיות כפי שנשלחו, ולכן הנתיבים של GRUB עוקפים
את Starlette לגמרי. נמצא במעבדת ה-VM (issue #12) — GRUB אמיתי, שרת
אמיתי, ותפריט שאושר ב-ACK אבל מעולם לא בוצע.

חיבור בצד השרת:

    app.mount("/boot", create_boot_asgi(
        resolve=my_lookup,                       # ראו חתימת Resolver למטה
        config=GrubConfig(server_base="http://10.99.0.10:8080"),
        boot_dir="/srv/imagectl/boot",           # vmlinuz + initrd.img
    ))
"""

import ipaddress
import logging
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs

from .grub_menu import GrubConfig, normalize_mac, render, render_local_only

log = logging.getLogger("imagectl.boot")

#: כותרות שחייבות ללוות כל תשובה — באותיות גדולות, בשביל GRUB.
#: בלי no-store, proxy ברשת המכללה יכול להגיש למחשב את ההחלטה של מחשב
#: אחר — או את זו של אתמול. Connection: close הוא חגורה נוספת ל-GRUB:
#: גם קורא-עד-EOF מקבל סוף קובץ אמיתי.
BASE_HEADERS = [
    (b"Cache-Control", b"no-store, no-cache, must-revalidate, max-age=0"),
    (b"Pragma", b"no-cache"),
    (b"Connection", b"close"),
]

MEDIA_TYPE = "text/plain; charset=us-ascii"
FILE_CHUNK = 1 << 20


class Resolver(Protocol):
    """מה שהשרת מספק: MAC ו-IP מקור פנימה, תשובת שרת (ממשק 3) החוצה.

    כאן גם מקומה של שכבת האימות השנייה מסעיף 6 באפיון: אם ה-MAC רשום
    כתחנת כיתה אבל הבקשה הגיעה מרשת חדר השיכפולים, ה-resolver הוא שמחליט
    להחזיר known=false או task=null. המחולל לא מכיר טופולוגיית רשת.
    """

    def __call__(self, mac: str, client_ip: str | None) -> dict: ...


def build_config_text(
    raw_mac: object,
    client_ip: str | None,
    resolve: Resolver,
    config: GrubConfig,
) -> str:
    """הליבה, בלי שכבת HTTP — נוח לבדיקות וגם לשרת אחר.

    לעולם לא זורקת. כל כישלון מחזיר קובץ שעולה מהדיסק המקומי.
    """
    mac = normalize_mac(raw_mac)
    if mac is None:
        log.warning("boot request with unusable mac %r from %s", raw_mac, client_ip)
        return render_local_only("missing or malformed mac")

    try:
        answer = resolve(mac, client_ip)
    except Exception:
        log.exception("resolver failed for %s from %s", mac, client_ip)
        return render_local_only("server lookup failed")

    text = render(answer, config)
    log.info("served boot config for %s from %s", mac, client_ip)
    return text


def config_for_scope(scope: dict, config: GrubConfig) -> GrubConfig:
    """אותה תצורה, אבל עם הכתובת שאליה הלקוח באמת התחבר (issue #39).

    תחנה מחוץ לווילן ההפצה (תרחיש 3) מגיעה דרך ‎$net_default_server, ואם
    התפריט שהיא מקבלת מפנה את הקרנל לכתובת ההפצה הקבועה — היא לא תגיע
    אליה, ותיפול ל-chain_local. המקור כאן הוא ‎scope["server"], שאותו
    uvicorn ממלא מה-sockname של החיבור שהתקבל. מכוון: *לא* כותרת Host,
    שהיא קלט של הלקוח ולכן תשתול כתובת שרירותית בשורת הקרנל.

    לעולם לא זורקת: כל ערך שאינו זוג IPv4+פורט — חסר, IPv6, שם מארח,
    פורט ריק — חוזר לתצורה הקבועה, כמו לפני #39.
    """
    try:
        host, port = scope.get("server")
        if not isinstance(host, str) or isinstance(port, bool):
            return config
        if not isinstance(port, int) or not 0 < port < 65536:
            return config
        if ipaddress.ip_address(host).version != 4:
            return config
        return GrubConfig(
            server_base=f"http://{host}:{port}",
            kernel_path=config.kernel_path,
            initrd_path=config.initrd_path,
            extra_cmdline=config.extra_cmdline,
        )
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return config


def _response_headers(content_type: bytes, length: int) -> list:
    return [(b"Content-Type", content_type),
            (b"Content-Length", str(length).encode())] + BASE_HEADERS


def create_boot_asgi(resolve: Resolver, config: GrubConfig,
                     boot_dir: str | Path | None = None):
    """אפליקציית ASGI לנתיבי ‎/boot: התפריט + קבצי הקרנל וה-initramfs.

    ממופה ב-mount על "/boot", ולכן ה-path שמגיע לכאן הוא היחסי:
    "/menu", "/vmlinuz", "/initrd.img".
    """
    root = Path(boot_dir).resolve() if boot_dir else None

    async def send_all(send, status: int, body: bytes,
                       content_type: bytes = b"text/plain") -> None:
        await send({"type": "http.response.start", "status": status,
                    "headers": _response_headers(content_type, len(body))})
        await send({"type": "http.response.body", "body": body})

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        # Starlette חדש משאיר את הנתיב המלא ושם את התחילית ב-root_path;
        # ישן מקצץ בעצמו. מטפלים בשניהם — ה-mount לא אמור להכתיב לנו גרסה.
        path = scope["path"]
        prefix = scope.get("root_path", "")
        if prefix and path.startswith(prefix):
            path = path[len(prefix):] or "/"
        client_ip = scope.get("client")[0] if scope.get("client") else None

        if path in ("/menu", "/menu/"):
            macs = parse_qs(scope.get("query_string", b"").decode()).get("mac")
            # הכתובת שאליה התחנה באמת התחברה, ולא בהכרח זו שבתצורה (#39).
            body = build_config_text(macs[0] if macs else None, client_ip,
                                     resolve, config_for_scope(scope, config))
            # תמיד 200. GRUB מטפל גרוע בקודי שגיאה, ומחשב שנתקע במסך
            # אתחול גרוע בהרבה ממחשב שעלה מהדיסק שלו.
            await send_all(send, 200, body.encode("ascii", "replace"),
                           MEDIA_TYPE.encode())
            return

        # קובץ מתוך תיקיית האתחול — שם קובץ בלבד, בלי תתי-תיקיות.
        name = path.lstrip("/")
        target = (root / name) if root and name and "/" not in name else None
        if target is None or not target.is_file() or target.parent != root:
            await send_all(send, 404, b"not found")
            return

        size = target.stat().st_size
        await send({"type": "http.response.start", "status": 200,
                    "headers": _response_headers(b"application/octet-stream", size)})
        with target.open("rb") as handle:
            while True:
                chunk = handle.read(FILE_CHUNK)
                if not chunk:
                    await send({"type": "http.response.body", "body": b""})
                    break
                await send({"type": "http.response.body", "body": chunk,
                            "more_body": True})
        log.info("served boot file %s (%d bytes) to %s", name, size, client_ip)

    return app


__all__ = ["Resolver", "build_config_text", "config_for_scope",
           "create_boot_asgi", "BASE_HEADERS", "MEDIA_TYPE"]
