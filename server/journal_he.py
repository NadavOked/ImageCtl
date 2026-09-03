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
    "agent_login": "כניסה ממסך תחנה",
    "agent_login_failed": "ניסיון כניסה כושל במסך תחנה",
    "agent_role_refused": "פתיחת סבב נדחתה — התפקיד אינו רשאי",
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
    "room_done": "סבב חדר השיכפולים הושלם",
    "room_close": "סבב חדר השיכפולים נסגר",
    "send_start": "השידור יצא לדרך",
    "send_done": "השידור הסתיים",
    "send_failed": "השידור נכשל",
    "send_stopped": "השידור נעצר",
    "unknown_mac": "מחשב לא רשום ניסה לעלות",
    "boot_loop_local": "מחשב אתחל שוב ושוב — נשלח לדיסק המקומי",
    "boot_loop_unverified": "ספירת האתחולים נכשלה — המחשב נשלח לדיסק המקומי",
    "agent_loop": "מחשב הגיע לסוכן בלי משימה ובלי סבב",
    "agent_loop_unverified": "ספירת ההגעות לסוכן נכשלה",
    "report_from_nonmember": "דיווח ממחשב שאינו בסבב",
    "setting_change": "הגדרה שונתה",
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
    "net_config": "הגדרת הרשת של כרטיס בשרת שונתה",
    "net_config_unverified": "שינוי הרשת לא אומת מול המצב בפועל",
    "net_rollback_armed": "שינוי רשת ממתין לאישור — בלעדיו יוחזר",
    "net_confirmed": "שינוי הרשת אושר — החיבור עדיין חי",
    "net_rollback": "הוחזרה תצורת רשת קודמת, השינוי לא אושר",
    "net_rollback_unreadable": "הוחזרה תצורת רשת, הפרטים לא ניתנים לקריאה",
    "ssh_stations": "מתג ה-SSH בתחנות שונה",
    "ssh_server": "מתג ה-SSH של השרת שונה על כרטיס רשת",
    "ssh_unverified": "שינוי SSH לא אומת מול המצב בפועל",
}

SETTINGS_HE = {
    "recovery_require_login": "שחזור בודד דורש כניסה",
    "session_wait_seconds": "המתנה מהמצטרף האחרון (שניות)",
    "console_idle_seconds": "ניתוק אוטומטי בחוסר פעילות (שניות)",
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
                text = f'{group} — "{image}", קידומת {m.group(4)}'
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
