"""בניית תשובת השרת — ממשק 3. הלב של צד השרת.

אותה פונקציה משרתת את שני הצרכנים:
- POST /api/v1/agent/hello — עם joining=True (hello הוא גם ההצטרפות)
- ה-resolver של תפריט ה-GRUB — עם joining=False (תפריט לא מצרף לסבב)

כל נתיב שלא מסתיים בהוראה מפורשת מסתיים ב-task:null + session:null,
שפירושם אצל הסוכן ואצל המחולל אותו דבר: דיסק מקומי.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import sqlite3
from urllib.parse import urlsplit

from . import bootguard, registry, room
from .db import get_setting, journal, net_seen
from .images import ImageLibrary
from .sessions import SessionStore
from .tasks import active_task

log = logging.getLogger("imagectl.hello")


def off_deploy_vlan(scope: dict | None, server_base: str | None) -> bool:
    """האם הבקשה התקבלה על כתובת מקומית שאינה כתובת וילן ההפצה (issue #42).

    המקור הוא ‎scope["server"] — ה-sockname של החיבור, שאותו uvicorn ממלא
    (אותו מקור כמו ב-#39). מכוון: *לא* כותרת Host, שהיא קלט של הלקוח
    ולכן תאפשר לתחנה להכריז על עצמה כ"בתוך הווילן".

    לעולם לא זורקת, ובכל ספק עונה False — כלומר "כמו היום". השוואה
    נעשית רק בין שתי כתובות IP: scope חסר, שם מארח (‏TestClient ממלא
    ("testserver", 80)), או ‎server_base בלי כתובת מספרית — כולם ספק.
    ערך ה-server חייב להיות זוג, כמו ב-#39; הפורט עצמו לא נשקל, שכן
    אותה מכונה על אותה כתובת היא אותו וילן.
    """
    try:
        host, _port = scope.get("server")
        configured = urlsplit(server_base).hostname
        return ipaddress.ip_address(host) != ipaddress.ip_address(configured)
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return False


def login_required(conn: sqlite3.Connection, has_open_session: bool,
                   off_vlan: bool = False) -> bool:
    """הכלל של אשף השחזור, במקום אחד.

    ‏hello מכריז עליו ב-`ui.require_login`, והשרת אוכף אותו שוב כשמשיכת
    יוניקאסט נפתחת — הצהרה שהסוכן מציית לה אינה אכיפה.
    """
    if off_vlan:
        # מחוץ לווילן ההפצה אין "הגישה הפיזית היא השמירה": כניסה תמיד,
        # גם בסבב פתוח וגם כשההגדרה כבויה (#42).
        return True
    if has_open_session:
        # בסבב פתוח אין סיסמה — זה מה שחוסך 29 הקלדות.
        return False
    return get_setting(conn, "recovery_require_login") == "true"


def _ui(conn: sqlite3.Connection, has_open_session: bool,
        off_vlan: bool = False) -> dict:
    return {"language": "he",
            "require_login": login_required(conn, has_open_session, off_vlan)}


def _unknown(conn: sqlite3.Connection, mac: str, client_ip: str | None,
             off_vlan: bool = False) -> dict:
    journal(conn, "unknown_mac", f"{mac} from {client_ip or '?'}")
    return {
        "schema": 1,
        "known": False,
        "role": "unknown",
        "group": None,
        "task": None,
        "session": None,
        "allowed_images": [],
        "ui": _ui(conn, has_open_session=False, off_vlan=off_vlan),
    }


def build_answer(
    conn: sqlite3.Connection,
    library: ImageLibrary,
    store: SessionStore,
    mac: str,
    *,
    disks: list[dict] | None = None,
    client_ip: str | None = None,
    joining: bool = False,
    reported_ip: str | None = None,
    off_vlan: bool = False,
) -> dict:
    # כל מגע נרשם ברשימת ההתקנים, גם של מכונה שאינה רשומה בטבלה.
    net_seen(conn, mac, reported_ip or client_ip,
             disks_json=json.dumps(disks) if disks else None)

    machine = registry.lookup(conn, mac)
    if machine is None:
        return _unknown(conn, mac, client_ip, off_vlan)

    answer: dict = {
        "schema": 1,
        "known": True,
        "role": machine["role"],
        "group": {
            "id": machine["group_id"],
            "label": machine["label"],
            "suffix": machine["suffix"],
        },
        "task": active_task(conn, mac),
        "session": None,
        "allowed_images": library.allowed_for_disks(disks),
        "ui": _ui(conn, has_open_session=False, off_vlan=off_vlan),
    }

    # משימה גוברת על סבב: היא מופנית למכונה הזו, לא לקבוצה.
    if answer["task"] is not None:
        return answer

    if machine["role"] == "cloner":
        # מחשבי השיכפול דוגמים כל הזמן — ה-hello שלהם הוא גם הדופק
        # שמקדם את סבב החדר (סיום גל, פתיחת הגל הבא). ‏`pulse` ולא
        # `tick`: קידום הסבב לא מפיל את ה-hello, בדיוק כמו
        # `agent_loops.note` שיושב באותו מסלול — והכישלון נרשם ביומן
        # ולא נבלע (#177).
        room.pulse(conn, store, mac)
        if not room.has_fresh_drawers(conn, mac):
            # המגירות של המכונה כבר נכתבו בסבב הזה ולא הוחלפו —
            # היא לא מצטרפת לגל, וממשיכה להמתין (wait_poll).
            return answer

    session = store.active_for_group(machine["group_id"])
    if session is None:
        return answer

    if not store.in_roster(session, mac):
        # סבב עם בחירת מחשבים, והמכונה לא ברשימה: היא לא הוזמנה —
        # דיסק מקומי, כאילו אין סבב.
        return answer

    session = store.maybe_start(session)

    if store.member_done(session["id"], mac):
        # שחזר וסיים. הסבב לא מוצע שוב — אחרת לולאת שחזור אחרי כל אתחול.
        return answer

    if session["state"] == "open" and joining:
        store.record_hello(session, mac)

    if session["state"] == "running" and not store.is_member(session["id"], mac):
        # מאחרים נכנסים לסבב הבא (סעיף 13.3) — עכשיו: דיסק מקומי.
        return answer

    answer["session"] = {
        "id": session["id"],
        "state": session["state"],
        "image_id": session["image_id"],
        "prefix": session["prefix"],
        "expected_clients": session["expected_clients"],
        "joined": store.joined_count(session["id"]),
        "starts_in_seconds": store.starts_in_seconds(session)
        if session["state"] == "open"
        else 0,
    }
    answer["ui"] = _ui(conn, has_open_session=session["state"] == "open",
                       off_vlan=off_vlan)
    return answer


def make_resolver(conn: sqlite3.Connection, library: ImageLibrary, store: SessionStore):
    """ה-Resolver ש-boot/http.py מצפה לו: (mac, client_ip) → ממשק 3.

    בלי הצטרפות ובלי דיסקים — תפריט אתחול רק שואל, לא מחייב.
    """

    def resolve(mac: str, client_ip: str | None) -> dict:
        answer = build_answer(
            conn, library, store, mac, client_ip=client_ip, joining=False
        )
        # רק כאן, ולא ב-hello: בקשת התפריט היא האתחול, והיא גם הרגע
        # היחיד שבו אפשר להבטיח לאן ילך האתחול הבא (‏#75).
        return bootguard.guard(conn, mac, answer)

    return resolve
