"""חסימת נתיבי הקונסולה לפי כתובת מקור — הגנה שלא תלויה ב-NGFW (#151).

`install/firewall-rules.md` בונה את ההפרדה בין הקונסולה לכיתות על חומת
אש חיצונית עם HTTP/URL inspection או proxy שקוף — כי עד #731/#738/#770
הקונסולה חלקה פורט אחד עם הסוכן, ואי אפשר היה להפריד ביניהם לפי פורט
בלבד (#150). הפיצול ל-`create_console_app` כבר נותן לחומת אש רגילה
מסלול לחסום לפי פורט, אבל זו עדיין תלות ברכיב חיצוני: אם הוא לא
מותקן, מוגדר לא נכון, או נופל — הקונסולה נחשפת בשקט, וכל השאר ממשיך
לעבוד כרגיל.

השומר כאן הוא שכבה נוספת **בתוך** השרת עצמו, לא תחליף לחומת האש.
כשהוא מופעל (``console_allowed_networks`` לא ריק), כל בקשה על
אפליקציית הקונסולה נבדקת מול כתובת ה-peer של ה-ASGI — לא
``X-Forwarded-For``/``Forwarded``, שהן כותרות שניתן לזייף, בדיוק כמו
``interserver_auth.is_loopback``.

עיקרון 5: אם אי אפשר לקבוע מאיפה הגיעה הבקשה — חוסמים, לא פותחים.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable

log = logging.getLogger("imagectl.console_guard")

Network = ipaddress.IPv4Network | ipaddress.IPv6Network

DENIED_BODY = (
    b"403 forbidden: console access is restricted by source address (#151)"
)


def parse_networks(values: Iterable[str]) -> tuple[Network, ...]:
    """הופך רשימת מחרוזות CIDR/כתובת בודדת לרשימת רשתות מותרות.

    ``strict=False`` כדי ש-``10.44.0.5/24`` (עם ביטים של מארח דלוקים)
    לא ייכשל — טעות הקלדה נפוצה בזמן פריסה, וההכוונה ברורה בכל זאת.
    כתובת בודדת בלי ``/`` (למשל ``10.10.10.1``, כמו MGMT במעבדה) הופכת
    לרשת ``/32`` (או ``/128`` ל-IPv6) — בדיוק רשת אחת שמכילה אותה בלבד.
    ``ValueError`` על קלט שאי אפשר לפענח מתגלגלת לקורא — אין ניחוש.
    """
    return tuple(ipaddress.ip_network(v, strict=False) for v in values)


class ConsoleSourceGuard:
    """middleware גולמי — חוסם כל בקשת HTTP שכתובת ה-peer שלה אינה
    באחת מ-``networks``.

    לא ``@app.middleware``: אותה סיבה שכתובה ב-``ConsoleNoStaleCache``
    ב-`server/app.py` — עוטפים אפליקציה שכולה קונסולה (`create_console_app`),
    ואין צורך לגעת בגוף הבקשה או בכותרות התשובה. הבדיקה היא לפי כתובת
    מקור, לא לפי סוג חיבור: ``http`` **וגם** ``websocket`` נבדקים (#859 —
    המוניטור של #690 הוא WebSocket על אותו פורט, ואותו peer שנחסם ב-HTTP
    עבר בו בלי בדיקה). ``lifespan`` אין לו client ועובר כרגיל — אחרת
    השרת לא עולה.

    ‏WebSocket שנחסם נסגר **לפני** ``websocket.accept`` ב-``websocket.close``
    עם קוד 4403 (אותה משפחה כמו ``WS_FORBIDDEN`` במוניטור). לפי מפרט ASGI
    ‏close לפני accept הוא דחיית ה-handshake, ו-uvicorn ממיר אותו ל-HTTP
    403 על ה-handshake בשני המימושים (``wsproto_impl`` ו-``websockets_impl``);
    ה-TestClient של Starlette מציג אותו כ-``WebSocketDisconnect(4403)``.
    """

    def __init__(self, inner, networks: Iterable[Network]):
        self.inner = inner
        self.networks = tuple(networks)

    async def __call__(self, scope, receive, send) -> None:
        kind = scope["type"]
        if kind not in ("http", "websocket") or self._allowed(scope):
            await self.inner(scope, receive, send)
            return
        client = scope.get("client")
        log.warning("console %s %s from %s denied by source guard (#151)",
                    kind, scope.get("path", "?"),
                    client[0] if client else "unknown")
        if kind == "websocket":
            await send({"type": "websocket.close", "code": 4403,
                        "reason": "console access is restricted by source address"})
            return
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send({"type": "http.response.body", "body": DENIED_BODY})

    def _allowed(self, scope) -> bool:
        client = scope.get("client")
        if not client:
            # אין ראיה מאיפה הגיעה הבקשה (למשל TestClient/ASGI פנימי
            # בלי scope["client"]) — חוסמים, לא מניחים "מקומי" (עיקרון 5).
            return False
        try:
            addr = ipaddress.ip_address(client[0])
        except (ValueError, TypeError):
            return False   # כתובת שאי אפשר לפענח — לא "כנראה מותר"
        return any(addr in net for net in self.networks)
