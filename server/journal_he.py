"""תרגום היומן לעברית קריאה — לאדם, לא למתכנת.

השורות נשמרות גולמיות (MAC, מזהים) כדי שלא יישברו כששמות משתנים;
התרגום קורה בקריאה: MAC הופך ל"שם · קבוצה" לפי הטבלה הנוכחית,
מזהה אימג' הופך לשם שלו מהספרייה, ושמות האירועים מקבלים עברית.
"""

from __future__ import annotations

import re
import sqlite3

from .images import ImageLibrary

EVENTS_HE = {
    "login": "כניסה לקונסולה",
    "login_failed": "ניסיון כניסה כושל",
    "login_refused_console": "כניסה לקונסולה סורבה — משתמש הפצה (#1073)",
    "login_lockout": "חשבון ננעל אחרי ניסיונות כושלים",
    "password_changed": "סיסמה הוחלפה",
    "password_reset": "סיסמה אופסה",
    "mfa_setup": "הקמת MFA",
    "mfa_enabled": "MFA הופעל",
    "mfa_disabled": "MFA נוטרל",
    "sessions_revoked": "הפעלות בוטלו",
    "agent_login": "כניסה ממסך תחנה",
    "agent_login_failed": "ניסיון כניסה כושל במסך תחנה",
    "agent_role_refused": "פתיחת סבב נדחתה — התפקיד אינו רשאי",
    "class_deploy_refused": "פתיחת סבב כיתה נדחתה — הפצה לכיתות כבויה",
    "user_create": "משתמש נוצר",
    "user_edit": "משתמש עודכן",
    "user_delete": "משתמש נמחק",
    "group_create": "קבוצה נוצרה",
    "group_edit": "שם קבוצה שונה",
    "group_reorder": "סדר הקבוצות שונה",
    "group_delete": "קבוצה נמחקה",
    "mac_import": "ייבוא טבלת MAC",
    "machine_add": "מכונה נוספה",
    "machine_edit": "מכונה עודכנה",
    "machine_delete": "מכונה נמחקה",
    "session_open": "סבב נפתח",
    "session_start_auto": "השידור התחיל — ההמתנה הסתיימה",
    "session_start_manual": "השידור התחיל ידנית",
    "session_close": "סבב נסגר",
    # ‏#411: פיוס בעליית השרת — סבב שהיה `running` בלי משדר.
    "session_orphaned": "השרת עלה מחדש — המשדר אבד, הסבב נסגר ככישלון",
    "session_image_bound": "פתיחת סבב נדחתה — האימג' קשור למכונה אחרת",
    "pull_open": "משיכת יוניקאסט התחילה",
    "pull_done": "משיכת יוניקאסט הסתיימה",
    "pull_refused": "משיכת יוניקאסט נדחתה",
    "client_done": "מחשב סיים לכתוב",
    "client_failed": "כתיבה נכשלה במחשב",
    "wol_sent": "המחשבים הוערו (WoL)",
    "wol_failed": "הערת WoL נכשלה",
    "room_open": "סבב חדר שיכפולים נפתח",
    "room_wave": "גל חדש בחדר השיכפולים",
    "room_wave_lost": "הגל של סבב החדר נסגר לפני הזמן — ממתין לגל הבא",
    "room_drawer_changed": "המגירה הוחלפה אחרי סיום הכתיבה",
    "room_done": "סבב חדר השיכפולים הושלם",
    "room_close": "סבב חדר השיכפולים נסגר",
    # ‏#980: כיבוי החדר עובר דרך הסוכן (hello) — בקשה, מסירה, או פקיעה.
    "room_poweroff": "התבקש כיבוי כל מחשבי השיכפול",
    "power_delivered": "בקשת הכיבוי נמסרה לסוכן",
    "power_expired": "בקשת הכיבוי פגה — המכונה לא ענתה בזמן, ולא נכבתה",
    "send_start": "השידור יצא לדרך",
    "send_done": "השידור הסתיים",
    "send_failed": "השידור נכשל",
    "send_stopped": "השידור נעצר",
    # ‏#715: הפצה ישירה — המקור הוא מחשב הבנייה, השרת מתזמר בלבד.
    "room_open_direct": "סבב הפצה ישירה ממחשב הבנייה נפתח",
    "send_delegated": "הגל יצא — מחשב הבנייה משדר מהדיסק שלו",
    "direct_manifest": "מחשב הבנייה סיים לקרוא את הדיסק — המניפסט הגיע",
    "direct_done": "השידור ממחשב הבנייה הסתיים",
    "direct_failed": "השידור ממחשב הבנייה נכשל",
    "direct_cancel": "השידור ממחשב הבנייה בוטל",
    "unknown_mac": "מחשב לא רשום ניסה לעלות",
    "disk_failure": "דיסק נכשל בכתיבה — נרשם לזיכרון הכשלים",
    "disk_failure_cleared": "רשומת כשל דיסק נוקתה",
    "boot_loop_local": "מחשב אתחל שוב ושוב — נשלח לדיסק המקומי",
    "boot_loop_unverified": "ספירת האתחולים נכשלה — המחשב נשלח לדיסק המקומי",
    "agent_loop": "מחשב הגיע לסוכן בלי משימה ובלי סבב",
    "agent_loop_unverified": "ספירת ההגעות לסוכן נכשלה",
    "report_from_nonmember": "דיווח ממחשב שאינו בסבב",
    # ‏#855: שומר הזהות — MAC מוצהר מול חכירת ה-DHCP.
    "identity_refused": "פנייה בשם מחשב מכתובת שאינה שלו — סורבה",
    "identity_unverifiable": "זהות מחשב לא ניתנת לבדיקה — סורבה",
    "setting_change": "הגדרה שונתה",
    "storage_group_create": "קבוצת סניפים נוצרה",
    "storage_group_edit": "שם קבוצת סניפים שונה",
    "storage_group_reorder": "סדר קבוצות הסניפים שונה",
    "storage_group_delete": "קבוצת סניפים נמחקה",
    "storage_node_edit": "שרת סניף עודכן",
    "storage_node_disable": "שרת סניף הושבת",
    "storage_node_enable": "שרת סניף הופעל מחדש",
    "storage_node_delete": "שרת סניף הוסר",
    "storage_node_enroll": "שרת סניף נרשם",
    "storage_transfer_start": "העברת אימג' לסניף התחילה",
    "storage_transfer_done": "העברת אימג' לסניף הושלמה",
    "storage_transfer_failed": "העברת אימג' לסניף נכשלה",
    "storage_image_received": "אימג' התקבל מהשרת הראשי",
    "storage_monitor_tunnel": "מוניטור למכונה נפתח מהשרת הראשי",
    "monitor_connected": "מוניטור נפתח למכונה",
    "monitor_closed": "מוניטור נסגר",
    "monitor_power": "פקודת כוח דרך המוניטור",
    "logo_set": "לוגו הוחלף",
    "logo_refused": "העלאת לוגו נדחתה",
    "logo_clear": "הלוגו הוסר",
    "image_edit": "אימג' עודכן",
    "image_delete": "אימג' נמחק",
    "capture_start": "קליטת אימג' הוזמנה",
    "capture_done": "אימג' נקלט",
    "capture_failed": "קליטת אימג' נכשלה",
    "capture_cancel": "קליטת אימג' בוטלה",
    "work_area_swept": "אזורי עבודה יתומים נמחקו",
    "work_area_kept": "אזורי עבודה נשארו — לא הוכח שהם יתומים",
    "image_download": "אימג' הורד למחשב",
    "image_upload": "אימג' הועלה מהמחשב",
    "driver_upload": "חבילת דרייברים יובאה",
    "driver_delete": "חבילת דרייברים נמחקה",
    "tools_selection": "בחירת ארגז הכלים נשמרה",   # #649
    "drivers_staged": "דרייברים הונחו על הדיסק המשוחזר",
    "drivers_failed": "השחזור הושלם, דרייברים לא הונחו",
    "folder_create": "תיקייה נוצרה",
    "folder_reorder": "סדר התיקיות שונה",
    "folder_edit": "תיקייה עודכנה",
    "folder_delete": "תיקייה נמחקה",
    "net_add": "התקן נוסף לרשימת הרשת",
    "net_describe": "תיאור התקן עודכן",
    "net_forget": "התקן הוסר מרשימת הרשת",
    "dhcp_set": "הגדרת DHCP על כרטיס רשת שונתה",
    "dhcp_proxy_risk": "מצב proxy הודלק על גרסת dnsmasq שלא נבדקה",
    "nic_add": "כרטיס רשת נוסף",
    "nic_forget": "הגדרות כרטיס רשת הוסרו",
    "dhcp_apply_failed": "החלת הגדרת DHCP נכשלה",
    "known_macs_apply_failed": "עדכון רשימת המכונות הרשומות ל-dnsmasq נכשל",
    "net_config": "הגדרת הרשת של כרטיס בשרת שונתה",
    "net_config_unverified": "שינוי הרשת לא אומת מול המצב בפועל",
    "net_rollback_armed": "שינוי רשת ממתין לאישור — בלעדיו יוחזר",
    "net_confirmed": "שינוי הרשת אושר — החיבור עדיין חי",
    "net_rollback": "הוחזרה תצורת רשת קודמת, השינוי לא אושר",
    "net_rollback_unreadable": "הוחזרה תצורת רשת, הפרטים לא ניתנים לקריאה",
    "ssh_stations": "מתג ה-SSH בתחנות שונה",
    "ssh_server": "מתג ה-SSH של השרת שונה על כרטיס רשת",
    "ssh_unverified": "שינוי SSH לא אומת מול המצב בפועל",
    # ‏#292: 55 אירועים שנכתבו בפועל ולא היו כאן — הגיעו למסך כמפתח באנגלית.
    # ‏tests/test_journal_events_he.py מצליב מעכשיו כל `journal(conn, "…")` מול הטבלה.
    "admin_password_reset_dcui": "סיסמת המנהל אופסה ממסך השרת (DCUI)",
    "bind_address_changed": "כתובת ההאזנה של השרת השתנתה",
    "client_lost": "מכונה אבדה באמצע גל — לא דיווחה בזמן",
    "console_tls_by_name_only": "תעודת הקונסולה תקפה לשם בלבד, לא לכתובת",
    "dcui_auth_failure": "ניסיון כניסה כושל במסך השרת (DCUI)",
    "dcui_auth_locked": "מסך השרת ננעל אחרי ניסיונות כושלים",
    "dcui_auth_success": "כניסה למסך השרת (DCUI)",
    "dcui_management_network_restart": "רשת הניהול הופעלה מחדש ממסך השרת",
    "dcui_net_confirm_refused": "אישור שינוי רשת ממסך השרת נדחה — אין שינוי ממתין",
    "dcui_net_rollback_requested": "התבקשה החזרת רשת ממסך השרת",
    "dcui_power_action": "כיבוי/הפעלה מחדש ממסך השרת",
    "dcui_power_refused": "כיבוי/הפעלה מחדש ממסך השרת נדחו — שם השרת לא תאם",
    "dcui_services_restart": "השירותים הופעלו מחדש ממסך השרת",
    "dcui_tls_display": "תעודת הקונסולה הוצגה במסך השרת",
    "dcui_wizard_rerun": "אשף ההתקנה הופעל מחדש ממסך השרת",
    "deploy_net_failed": "הגדרת רשת ההפצה נכשלה",
    "deploy_net_set": "רשת ההפצה הוגדרה",
    "dhcp_synced_at_startup": "תצורת DHCP/TFTP נכתבה מחדש מה-DB בעלייה",
    "disk_decision": "הכרעה על דיסק (SMART) נרשמה",
    "machine_drawer_count": "מספר המגירות של מכונה עודכן",
    "monitor_stations": "מתג המוניטור בתחנות שונה",
    "net_config_refused": "שינוי רשת נדחה",
    "off_vlan_contact": "מכונה פנתה לשרת מחוץ לוילן ההפצה",
    "off_vlan_unverified": "לא ניתן היה לאמת מאיזו רשת פנתה המכונה",
    "port_toggle": "מתג פורט שונה",
    "port_unverified": "שינוי פורט לא אומת מול המצב בפועל",
    "pull_retry": "משיכה נפתחה מחדש למכונה",
    "report_on_closed_session": "דיווח הגיע לסבב שכבר נסגר",
    "report_on_closed_task": "דיווח הגיע למשימה שכבר נסגרה",
    "room_role_denied": "פעולה בחדר המשכפלים נדחתה — התפקיד אינו רשאי",
    "room_tick_failed": "שעון חדר המשכפלים נכשל — החדר אינו מתקדם",
    "session_dedupe": "סבב כפול אוחד",
    "session_role_denied": "פעולה בסבב נדחתה — התפקיד אינו רשאי",
    "shrink_cleared": "רשומת כיווץ נוקתה",
    "shrink_note": "הערת כיווץ נרשמה לדיסק",
    "storage_iscsi_login": "התחברות iSCSI למיקום אחסון",
    "storage_location_connect": "התחברות למיקום אחסון",
    "storage_location_connected": "מיקום אחסון מחובר",
    "storage_location_create": "מיקום אחסון נוצר",
    "storage_location_delete": "מיקום אחסון נמחק",
    "storage_location_disconnect": "מיקום אחסון נותק",
    "storage_location_format": "מיקום אחסון פורמט",
    "storage_location_mount": "מיקום אחסון עוגן",
    "storage_location_unreachable": "מיקום אחסון אינו נגיש",
    "storage_monitor_tunnel_closed": "מנהרת מוניטור למכונה בסניף נסגרה",
    "storage_pairing_open": "צימוד שרת משני נפתח",
    "storage_parent_cert_renewed": "תעודת השרת הראשי חודשה במשני",
    "storage_parent_unbind": "השרת המשני נותק מהראשי",
    "task_token_refused": "אסימון משימה נדחה",
    "settings_backup_downloaded": "גיבוי ההגדרות הורד",
    "update_apply_failed": "החלת עדכון נכשלה",
    "update_apply_started": "החלת עדכון החלה",
    "update_check": "בדיקת עדכון מול הריפו הציבורי",
    "user_disabled": "משתמש הושבת",
    "user_enabled": "משתמש הופעל מחדש",
    "send_simulated": "שידור מדומה (סימולציה בלבד — אין udp-sender)",
    "shrink_open": "רשומת כיווץ נפתחה לדיסק — הטבלה המקורית נשמרה בשרת",
}

#: ‏#968: חומרת האירוע לציר-הזמן ולעמודת הצבע ביומן — נקבעת **כאן**, לצד
#: התרגום, ולא בהיוריסטיקה על שם ה-event בקונסולה (שהתיישנה עם כל
#: אירוע חדש). ארבעה ערכים: ``ok`` הושלם/אושר · ``warn`` דורש תשומת לב ·
#: ``err`` כשל/סירוב · ``info`` (ברירת המחדל לכל אירוע שאינו ברשימה,
#: כולל אירוע שאינו ב-EVENTS_HE — לא נופל, לא צובע).
SEVERITY_LEVELS = ("info", "ok", "warn", "err")
_SEVERITY_ERR = {
    "login_failed", "login_refused_console", "login_lockout",
    "agent_login_failed", "agent_role_refused", "class_deploy_refused",
    "session_image_bound", "pull_refused", "client_failed", "wol_failed",
    "room_wave_lost", "send_failed", "direct_failed", "disk_failure",
    "session_orphaned", "identity_refused", "storage_transfer_failed", "logo_refused",
    "capture_failed", "drivers_failed", "dhcp_apply_failed",
    "known_macs_apply_failed",
}
_SEVERITY_WARN = {
    "unknown_mac", "boot_loop_local", "boot_loop_unverified", "agent_loop",
    "agent_loop_unverified", "report_from_nonmember", "identity_unverifiable",
    "capture_cancel", "direct_cancel", "send_stopped", "dhcp_proxy_risk",
    "net_config_unverified", "net_rollback_armed", "net_rollback",
    "net_rollback_unreadable", "ssh_unverified", "work_area_kept",
    "storage_node_disable",
    "room_drawer_changed",
    "power_expired",
}
_SEVERITY_OK = {
    "login", "capture_done", "client_done", "pull_done", "room_done",
    "send_done", "direct_done", "storage_transfer_done", "net_confirmed",
    "disk_failure_cleared", "drivers_staged", "storage_image_received",
    "wol_sent",
}


def severity(event: str) -> str:
    if event in _SEVERITY_ERR:
        return "err"
    if event in _SEVERITY_WARN:
        return "warn"
    if event in _SEVERITY_OK:
        return "ok"
    return "info"


SETTINGS_HE = {
    "recovery_require_login": "שחזור בודד דורש כניסה",
    "session_wait_seconds": "המתנה מהמצטרף האחרון (שניות)",
    "console_idle_seconds": "ניתוק אוטומטי בחוסר פעילות (שניות)",
    "class_deploy_enabled": "הפצה לכיתות ממחשב הבנייה",
}

ROLES_HE = {"classroom": "כיתה", "cloner": "חדר שיכפולים", "build": "מחשב בנייה"}

FIELDS_HE = {"name": "שם", "description": "תיאור", "folder": "תיקייה", "sort": "סדר"}

#: למה הוחזרה תצורת הרשת (‏#56). שתי הסיבות היחידות שהזרוע כותבת.
ROLLBACK_HE = {"expired": "לא אושר בתוך חלון הזמן",
               "boot": "המכונה אותחלה לפני שהשינוי אושר"}

NETFIELDS_HE = {"mode": "מצב", "address": "כתובת", "netmask": "מסכה",
                "gateway": "שער", "dns": "DNS", "routes": "נתיבים סטטיים"}

_MAC = re.compile(r"\b([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b")
_IMG = re.compile(r"\b(img_[0-9a-f]+)\b")
_SES = re.compile(r"\b(ses_[0-9a-f]+)\b")
_GRP = re.compile(r"\b(grp_\w+)\b")
_EXPAND = re.compile(r"expand=(\S+)")


def _expand_note(detail: str) -> str:
    """#59: מה שנכתב ל-`session_open`/`room_open` כשההרחבה לא הייתה
    "auto" — כיבוי או בחירה ידנית. "auto" עצמו אינו מגיע לכאן כלל
    (הוא לא נכתב לפרטים מלכתחילה), ולכן שקט הוא ברירת המחדל."""
    m = _EXPAND.search(detail)
    if not m:
        return ""
    value = m.group(1)
    if value == "none":
        return " · הרחבה כבויה"
    return f" · הרחבת מחיצה {value} (בחירה ידנית)"


class JournalTranslator:
    """בונה את מילוני השמות פעם אחת לכל בקשת יומן — לא שאילתה לשורה."""

    def __init__(self, conn: sqlite3.Connection, library: ImageLibrary):
        self._machines = {
            row["mac"]: f'{row["suffix"]} · {row["label"]}'
            for row in conn.execute(
                "SELECT m.mac, m.suffix, g.label FROM machines m"
                " JOIN groups g ON g.id = m.group_id"
            )
        }
        self._groups = {
            row["id"]: row["label"]
            for row in conn.execute("SELECT id, label FROM groups")
        }
        self._images = {
            image_id: manifest["name"]
            for image_id, manifest in library.scan().items()
        }
        self._sessions = {
            row["id"]: f'{row["prefix"]} · {self._groups.get(row["group_id"], row["group_id"])}'
            for row in conn.execute("SELECT id, prefix, group_id FROM sessions")
        }

    def _names(self, text: str) -> str:
        text = _MAC.sub(lambda m: self._machines.get(m.group(1), m.group(1)), text)
        text = _IMG.sub(lambda m: f'"{self._images[m.group(1)]}"'
                        if m.group(1) in self._images else m.group(1), text)
        text = _GRP.sub(lambda m: self._groups.get(m.group(1), m.group(1)), text)
        return text

    def translate(self, event: str, detail: str) -> tuple[str, str]:
        """(תווית בעברית, פירוט בעברית). נופל חזרה לגולמי, לא נשבר."""
        label = EVENTS_HE.get(event, event)
        text = detail

        if event == "mac_import":
            m = re.search(r"group=(\S+) saved=(\d+) rejected=(\d+)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group}: נשמרו {m.group(2)}, נדחו {m.group(3)}"
        elif event == "session_open":
            m = re.search(r"(ses_\w+) (\S+) (\S+) prefix=(\S+)", detail)
            if m:
                group = self._groups.get(m.group(2), m.group(2))
                image = self._images.get(m.group(3), m.group(3))
                text = (f'{group} — "{image}", קידומת {m.group(4)}'
                        f'{_expand_note(detail)}')
        elif event == "room_open":
            text = detail + _expand_note(detail)
        elif event == "pull_open":
            m = re.search(r"(ses_\w+) (\S+) (\S+) mac=(\S+)", detail)
            if m:
                image = self._images.get(m.group(3), m.group(3))
                text = f'{m.group(4)} — "{image}"'
        elif event == "pull_done":
            text = _SES.sub(lambda m: self._sessions.get(m.group(1), m.group(1)), detail)
        elif event in ("client_done", "client_failed", "report_from_nonmember"):
            text = self._names(_SES.sub("הסבב", detail)).replace(" in ", " בתוך ")
        elif event in ("session_start_auto", "session_start_manual", "session_close",
                       "send_done", "send_stopped"):
            text = _SES.sub(lambda m: self._sessions.get(m.group(1), m.group(1)), detail)
        elif event == "send_start":
            m = re.search(r"(ses_\w+) (img_\w+) partitions=(\d+)", detail)
            if m:
                where = self._sessions.get(m.group(1), m.group(1))
                image = self._images.get(m.group(2), m.group(2))
                text = f'{where} — "{image}", {m.group(3)} מחיצות'
        elif event in ("boot_loop_local", "boot_loop_unverified"):
            m = re.search(r"(\S+) (session|task):(\S+)", detail)
            if m:
                where = (self._sessions.get(m.group(3), m.group(3))
                         if m.group(2) == "session" else f"משימה {m.group(3)}")
                text = f"{self._machines.get(m.group(1), m.group(1))} — {where}"
                attempts = re.search(r"attempts=(\d+)", detail)
                if attempts:
                    text += f", {attempts.group(1)} ניסיונות אתחול"
        elif event == "group_create":
            m = re.search(r"(\S+) \((\w+)\)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group} ({ROLES_HE.get(m.group(2), m.group(2))})"
        elif event in ("image_download", "image_upload"):
            m = re.search(r'img_\w+ "(.+)"', detail)
            if m:
                text = f'"{m.group(1)}"'
        elif event == "image_edit":
            m = re.search(r"(img_\w+) (.+)", detail, flags=re.DOTALL)
            if m:
                name = self._images.get(m.group(1), m.group(1))
                changed = [FIELDS_HE.get(c.split("=", 1)[0], c.split("=", 1)[0])
                           for c in m.group(2).split(", ")]
                text = f'"{name}" — עודכנו: {", ".join(changed)}'
        elif event == "work_area_swept":
            m = re.search(r"swept=(\d+) freed=(\S+)", detail)
            if m:
                text = f"{m.group(1)} אזורי עבודה · {m.group(2)} התפנו"
        elif event == "wol_sent":
            m = re.search(r"(\S+) count=(\d+)", detail)
            if m:
                group = self._groups.get(m.group(1), m.group(1))
                text = f"{group} — {m.group(2)} מחשבים"
        elif event == "dhcp_proxy_risk":
            m = re.search(r"(\S+) dnsmasq=(\S+)", detail)
            if m:
                version = ("הגרסה לא נקראה" if m.group(2) == "unknown"
                           else f"dnsmasq {m.group(2)}")
                text = f"{m.group(1)} — {version}"
        elif event == "net_rollback":
            # הזמן שמדווח הוא **זמן ההחזרה** שבפירור, ולא זמן הקריאה:
            # השורה נכתבת בהפעלה שאחרי, ולפעמים ימים אחרי האירוע (‏#56).
            m = re.search(r"(\S+) at=(\S+) reason=(\S+)", detail)
            if m:
                text = (f"{m.group(1)} — {ROLLBACK_HE.get(m.group(3), m.group(3))}, "
                        f"בוצע ב-{m.group(2).replace('T', ' ')[:16]}")
                if "errors=" in detail:
                    text += f" · ההחזרה עצמה דיווחה שגיאות: {detail.split(': ', 1)[-1]}"
        elif event == "net_config":
            m = re.search(r"(\S+) (\S+)(?: (\S+))? changed=(\S*)$", detail)
            if m:
                mode = {"static": "כתובת סטטית", "dhcp": "לקוח DHCP",
                        "manual": "לא מנוהל מהקונסולה"}.get(m.group(2), m.group(2))
                changed = [NETFIELDS_HE.get(c, c) for c in m.group(4).split(",") if c]
                text = f"{m.group(1)} — {mode}"
                if m.group(3):
                    text += f" {m.group(3)}"
                if changed:
                    text += f" · עודכנו: {', '.join(changed)}"
        elif event == "net_rollback_armed":
            m = re.search(r"(\S+) window=(\S+)", detail)
            if m:
                text = (f"{m.group(1)} — יש לאשר תוך {m.group(2)} שהחיבור "
                        "לקונסולה עדיין חי")
        elif event == "setting_change":
            m = re.search(r"(\S+)=(\S+)", detail)
            if m:
                name = SETTINGS_HE.get(m.group(1), m.group(1))
                value = {"true": "פעיל", "false": "כבוי"}.get(m.group(2), m.group(2))
                text = f"{name}: {value}"
        elif event in ("monitor_closed", "monitor_power"):
            # ‏#1129: "<mac> <סיבה/פעולה>[ node=<nid>]" — המילה השנייה מתורגמת.
            words = {"browser": "הדפדפן סגר", "machine": "המכונה סגרה",
                     "idle": "נסגר אחרי 10 דק' בלי תעבורה",
                     "refused": "נסגר — הודעה אסורה מהדפדפן", "error": "כשל",
                     "reboot": "הפעלה מחדש", "poweroff": "כיבוי"}
            m = re.match(r"(\S+) (\S+)(.*)$", detail)
            text = self._names(detail) if m is None else " · ".join(
                part for part in (self._machines.get(m.group(1), m.group(1)),
                                  words.get(m.group(2), m.group(2)),
                                  m.group(3).strip()) if part)
        elif event in ("machine_add", "machine_edit", "machine_delete"):
            m = re.search(r"([0-9a-f:]{17})(?: name=(\S+))?(?: group=(\S+))?", detail)
            if m:
                parts = [m.group(1)]
                if m.group(2):
                    parts.append(f"השם: {m.group(2)}")
                if m.group(3):
                    parts.append(self._groups.get(m.group(3), m.group(3)))
                text = " · ".join(parts)
        else:
            text = self._names(detail)

        return label, text
