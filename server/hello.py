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
import re
import sqlite3
from urllib.parse import urlsplit

from . import bootguard, capabilities, direct, disk_failures, inventory, probe, registry, room, shrink_records
from .db import get_setting, journal, net_seen
from .images import ImageLibrary
from .sessions import SessionStore
from .tasks import active_task

log = logging.getLogger("imagectl.hello")

#: ‏#839: סוד המוניטור — 16 בייטים אקראיים שהסוכן הגריל באתחול, כ-32
#: ספרות hex קטנות. אורך קבוע כי תשובת ה-VNC Authentication היא 16 בייטים
#: בדיוק; קנוני (lowercase) כדי שהשוואה בשרת תהיה השוואת מחרוזות פשוטה.
MONITOR_SECRET_RE = re.compile(r"^[0-9a-f]{32}$")


def well_formed_monitor_secret(value: object) -> bool:
    return isinstance(value, str) and MONITOR_SECRET_RE.match(value) is not None


def well_formed_monitor_auth(value: object) -> bool:
    """‏#1077: הערך היחיד שמתקבל הוא ``hmac``. כל דבר אחר — כולל חסר —
    הוא סוכן ישן, והפרוקסי יסרב."""
    return value == "hmac"


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

    ‏#498: ‏``server_base`` עם שם מארח היה ספק **קבוע** — כל בקשה, תמיד
    "בפנים", וחובת הכניסה מחוץ לווילן (#42) מתה בשקט. לכן ``server.main``
    מסרב לעלות עם ‎--server-url שאינו כתובת מספרית; בשרת רץ ה-``False``
    של ספק נשאר רק למקרים החד-פעמיים (scope חסר, ‏TestClient).
    """
    try:
        host, _port = scope.get("server")
        configured = urlsplit(server_base).hostname
        return ipaddress.ip_address(host) != ipaddress.ip_address(configured)
    except Exception:  # noqa: BLE001 — כאן זו בדיוק הכוונה
        return False


#: ‏#976: הכותרת שבה הבדיקה העצמית של השרת (‏`health.default_hooks`) מסמנת
#: את בקשות `/boot/*` של עצמה. שם הכותרת כפי ש-ASGI מוסר אותו: bytes,
#: אותיות קטנות. ‏GRUB אינו שולח כותרות מותאמות, וגם אם ישלח — ראה
#: `self_probe`: מבחוץ הכותרת אינה שווה דבר.
PROBE_HEADER = b"x-imagectl-probe"


def self_probe(scope: dict | None) -> bool:
    """האם בקשת `/boot/*` הזו היא הבדיקה העצמית של השרת (‏#976).

    שני תנאים, ושניהם חובה: הכותרת `X-ImageCtl-Probe: 1` נמצאת, **והפונה
    הוא השרת עצמו** — ‏`scope["client"]` הוא loopback, או שווה לכתובת
    המקומית שעליה החיבור התקבל (‏`scope["server"]`). השני נחוץ כי
    בייצור הבדיקה פונה ל-`http://<כתובת ההפצה>:8080`, וחיבור מקומי
    לכתובת כזו יוצא **ממנה** ולא מ-`127.0.0.1`. מכונה ברשת אינה יכולה
    להשלים לחיצת יד TCP מכתובת השרת, ולכן הכותרת אינה דלת אחורית:
    מבחוץ היא מתעלמת, והבקשה נרשמת כמו כל GRUB.

    לעולם לא זורקת, ובכל ספק עונה False — כלומר "כמו היום": הבקשה
    נרשמת. הכיוון הבטוח כאן הוא לרשום, כי בדיקה עצמית שנרשמה בטעות
    היא לכלוך על המסך, ואילו GRUB שלא נרשם הוא מכונה שנעלמה.
    """
    try:
        if dict(scope.get("headers") or ()).get(PROBE_HEADER) != b"1":
            return False
        client = ipaddress.ip_address(scope.get("client")[0])
        return client.is_loopback or client == ipaddress.ip_address(
            scope.get("server")[0])
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


def class_deploy_enabled(conn: sqlite3.Connection) -> bool:
    """‏#880: המתג של v1 "מהדורת שיכפול". ‏hello למחשב הבנייה מכריז
    עליו (הכרטיס "הפצה לכיתות" מוצג רק כשהוא דלוק), ו-`station.py` אוכף
    אותו שוב בפתיחת הסבב — תפריט שמציע מה שהשרת יסרב לו הוא תפריט
    שמשקר. כל ערך שאינו "true" — כולל הגדרה חסרה — הוא כבוי."""
    return get_setting(conn, "class_deploy_enabled") == "true"


def _unknown(conn: sqlite3.Connection, mac: str, client_ip: str | None,
             off_vlan: bool = False, record_journal: bool = True) -> dict:
    if record_journal:
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
        # #1081: v1 hides classrooms. Missing = off (principle 1). v2 turns this on.
        "classrooms": capabilities.classrooms(),
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
    record_seen: bool = True,
    all_macs: list[str] | None = None,
    monitor_secret: str | None = None,
    monitor_auth: str | None = None,
    hw_inventory: dict | None = None,
    hw_probe: dict | None = None,
    multicast: dict | None = None,
    prompt: str | None = None,
    record_journal: bool = True,
) -> dict:
    # כל מגע של המכונה נרשם ברשימת ההתקנים, גם של מכונה שאינה רשומה
    # בטבלה — ככה מתגלה MAC לא מוכר, וזה חלק מעיקרון 1. ‏hello הוא POST
    # שבו המכונה מדווחת על עצמה, ולכן הוא תמיד רושם (ברירת המחדל).
    #
    # ‏#585: ‏`record_seen=False` בא ממסלול **התפריט** מחוץ לווילן ההפצה.
    # ‏`GET /boot/menu?mac=<MAC>` הוא בקשה בלי גוף ובלי עוגייה שכל פונה
    # שולח; מחוץ לווילן ההפצה אין ראיה שהפונה הוא המכונה, ולכן אסור שהוא
    # יקבע את ה-`ip`/`last_seen` שהשרת "מכיר" עבור אותו MAC — אחרת מכונה
    # כבויה נראית חיה, וכל בינוי זהות עתידי שיסתמך על הכתובת הזו מעגלי.
    # הרישום **אינו מופסק** — הוא רק מותנה בדיוק כמו ספירת האתחול (#536).
    if record_seen:
        net_seen(conn, mac, reported_ip or client_ip,
                 disks_json=json.dumps(disks) if disks is not None else None,
                 monitor_secret=monitor_secret, monitor_auth=monitor_auth,
                 prompt=prompt)
        # ‏#720: המלאי החומרתי (schema 2) נשמר מגורסת — שורה חדשה רק כשהשתנה.
        # ‏None = הסוכן לא שלח (schema 1) או שלח פגום: הגרסה הקודמת נשארת.
        if hw_inventory is not None:
            inventory.record(conn, mac, hw_inventory)
        # ‏#1049 שלב ב' + #1048: בדיקת המכונה (כולל netprobe בתוך אותו JSON)
        # — None = לא נשלח/פגום, הגרסה הקודמת נשארת; רק תוכן יציב שהשתנה
        # פותח גרסה.
        if hw_probe is not None:
            probe.record(conn, mac, hw_probe)

    machine = registry.lookup(conn, mac, all_macs)
    if machine is None:
        return _unknown(conn, mac, client_ip, off_vlan, record_journal)

    # ‏#880: רק מחשב הבנייה מקבל את השדה — הוא היחיד עם התפריט. סוכן ישן
    # מתעלם משדה שאינו מכיר; סוכן חדש שלא קיבל אותו מתנהג ככבוי.
    extra = ({"class_deploy_enabled": class_deploy_enabled(conn)}
             if machine["role"] == "build" else {})
    answer: dict = {
        **extra,
        # #1081: edition flag. v1 = False (hardcoded). v2 turns this on.
        # Missing = off. Not an operator setting.
        "classrooms": capabilities.classrooms(),
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
        # ‏#874: הזיכרון בשרת במקום סימון על הדיסק — הרשומות הפתוחות שתואמות
        # לסידוריים שהמכונה שלחה או לחריץ באותה מכונה. הסוכן צובע מהן אדום
        # לפני הסבב; סוכן ישן מתעלם משדה שאינו מכיר (schema נשאר 1).
        "disk_failures": disk_failures.open_for(conn, mac, disks),
        # ‏#926: דיסק שכווץ בקליטה ולא הוחזר לגודלו — לפי הסידורי שהמכונה
        # שלחה. מחשב הבנייה מציע להחזיר (‏shrinkmem.sh); סוכן ישן מתעלם.
        "shrink_open": shrink_records.open_for(conn, disks),
    }

    # משימה גוברת על סבב: היא מופנית למכונה הזו, לא לקבוצה.
    if answer["task"] is not None:
        # ‏#715: מחשב הבנייה שהוא המקור מקבל גם את מצב הגל ופרמטרי
        # השידור (‏`task.direct`, ממשק 3). ‏`multicast` הוא של `SenderEngine`
        # (‏api.py); בלעדיו — ברירות המחדל של המנוע.
        if answer["task"]["type"] == direct.DIRECT_SEND:
            answer["task"]["direct"] = direct.task_block(
                conn, store, answer["task"], multicast)
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
        # ‏#59: "auto" כשהסבב לא נפתח דרך הבחירה (pulls.py, station.py
        # שלא עודכנו) — סוכן ישן שלא מכיר את השדה מתעלם ממנו וממשיך
        # בבחירה האוטומטית בדיוק כמו קודם (עיקרון 1).
        "expand_partition": session["expand_partition"] or "auto",
    }
    # ‏#695: לסבב חדר, המכונה מקבלת את **הפורטים שנבחרו לכתיבה עבורה בלבד**.
    # ‏null שמור בכוונה לסבב ישן (התנהגות "כל הדיסקים"); קבוצה ריקה = לא נבחרה.
    if machine["role"] == "cloner" and session["prefix"] == "ROOM":
        ports = room._selected_ports(room.active_round(conn), mac)
        answer["session"]["target_ports"] = None if ports is None else sorted(ports)
    answer["ui"] = _ui(conn, has_open_session=session["state"] == "open",
                       off_vlan=off_vlan)
    return answer


def make_resolver(conn: sqlite3.Connection, library: ImageLibrary,
                  store: SessionStore, server_base: str | None = None):
    """ה-Resolver ש-boot/http.py מצפה לו: (mac, client_ip) → ממשק 3.

    בלי הצטרפות ובלי דיסקים — תפריט אתחול רק שואל, לא מחייב.

    ‏`scope` הוא של הבקשה הנוכחית, והוא כאן בשביל שתי שאלות בלבד: על
    **איזו** מכתובות השרת היא התקבלה (‏#536), והאם היא הבדיקה העצמית של
    השרת (‏#976, ‏`self_probe`). מי שמעביר אותו הוא `server/app.py`,
    שקושר את ה-resolver מחדש בכל בקשה.
    """

    def resolve(mac: str, client_ip: str | None,
                scope: dict | None = None) -> dict:
        off_vlan = off_deploy_vlan(scope, server_base)
        probe = self_probe(scope)
        answer = build_answer(
            conn, library, store, mac, client_ip=client_ip, joining=False,
            # ‏#585: בקשת תפריט מחוץ לווילן ההפצה אינה כותבת `net_seen` —
            # אותה הכרעה בדיוק כמו הספירה למטה, ומאותו טעם (‏MAC בשאילתה
            # אינו זהות). על וילן ההפצה התפריט כן רושם, כמו היום.
            # ‏#976: וגם לא הבדיקה העצמית של השרת — היא אינה המכונה.
            record_seen=not (off_vlan or probe),
            record_journal=not probe,
        )
        if probe:
            # ‏#976: הבדיקה העצמית מקבלת את התפריט האמיתי — זו הראיה
            # החיובית שהיא באה בשבילו — אבל אינה אתחול: לא `net_seen`,
            # ולא ספירה בתקציב הלולאה (‏#75). ארבע כניסות לקונסולה במהלך
            # סבב פתוח שלחו את המכונה הרשומה הראשונה לדיסק המקומי.
            log.info("boot menu for %s is the server's own probe — served, "
                     "not recorded (#976)", mac)
            return answer
        if off_vlan:
            # ‏#536: ‏MAC מהשאילתה אינו זהות. ‏`ATTEMPT_LIMIT` הוא 3,
            # ולכן ארבע בקשות `GET /boot/menu?mac=<תחנה>` — בלי גוף,
            # בלי עוגייה, בלי להיות המכונה — גמרו את תקציב האתחולים של
            # תחנת כיתה חיה, והשומר שנועד להציל אותה מלולאה הוא זה
            # שהוציא אותה מהסבב. מה שכן ניתן לאמת הוא על איזו כתובת
            # מקומית הבקשה התקבלה, וזו בדיוק העמדה של #42: מחוץ לווילן
            # ההפצה השרת אינו מגיש את שרשרת האתחול, ולכן בקשה כזו אינה
            # ראיה שהוא שלח את המכונה הזו לסוכן.
            #
            # התשובה עצמה אינה משתנה — התראה ולא שער (‏#137), והתחנה
            # שמושכת תפריט מרשת אחרת (תרחיש 3, ‏#39) ממשיכה לקבל את
            # הסבב שלה. רק **הספירה** אינה מתרחשת.
            #
            # ‏`off_deploy_vlan` עונה False בכל ספק, ולכן ספק נספר כמו
            # היום. זה **אינו** "לא הצלחנו לבדוק ולכן בסדר": כאן הכיוון
            # הבטוח הוא לספור — שומר שאינו סופר אינו שומר (‏#75).
            log.warning("boot menu for %s (from %s) did not arrive on the "
                        "deployment vlan — served, not counted", mac, client_ip)
            return answer
        # רק כאן, ולא ב-hello: בקשת התפריט היא האתחול, והיא גם הרגע
        # היחיד שבו אפשר להבטיח לאן ילך האתחול הבא (‏#75).
        return bootguard.guard(conn, mac, answer)

    return resolve
