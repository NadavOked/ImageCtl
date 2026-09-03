"""המתג של שתי דלתות ה-SSH, ומה שנקרא בחזרה אחרי כל לחיצה (‏#83).

‏`ssh_switch.py` יודע לקרוא ולכתוב; כאן יושבת ההתנהגות שהמפעיל רואה.

**ברירת המחדל סגורה**, בשתי הדלתות, וגם כשההגדרה פגומה או חסרה.

**מה מוגן בהקלדת שם (עיקרון 7).** הכיוון ההרסני כאן הוא *הפתיחה*, לא
הסגירה: פתיחה מעמידה דלת על וילן שיש בו סטודנטים, ולחיצה מקרית עליה
אינה נראית בשום מקום עד שמישהו נכנס. לכן כל מעבר ל"פתוח" דורש הקלדת
השם המדויק — בדיוק כמו הדלקת DHCP. סגירה, לעומת זאת, היא הכיוון אל
ברירת המחדל; להקשות עליה יותר מאשר על הפתיחה היה מאמן מפעילים להשאיר
דלתות פתוחות. יוצא דופן אחד: סגירת **הממשק הפתוח האחרון** — אחריה אין
‏SSH לשרת בכלל, וזה המצב שממנו חוזרים רק מהקונסולה או מהמקלדת הפיזית.
היא כן דורשת הקלדת שם.

**מה *לא* מגן כאן.** ההחזרה האוטומטית של #56 (יחידת systemd שמחזירה
הגדרה שלא אושרה) אינה קיימת עדיין, וזה לא מנגנון שמומש כאן. מה שכן
מגן: ‏`reload` משאיר חיבורי SSH קיימים חיים, והקונסולה — ‏HTTP, פורט
אחר, לא מושפעת — נשארת דרך החזרה. זה שונה מ-#56, ששם השינוי הורג את
הקונסולה עצמה ולכן חייב מנגנון חיצוני.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from . import auth, ssh_switch
from .db import journal, set_setting

#: מה שמקלידים כדי לפתוח את דלת התחנות. ‏ASCII, וזהה למה שמופיע
#: בשורת הקרנל — מי שמקליד אותו יודע בדיוק מה הוא מדליק.
STATION_CONFIRM = ssh_switch.DEBUG_PREFIX

#: כמה פעמים לקרוא בחזרה אחרי החלה, לפני שמכריזים "לא אומת".
VERIFY_TRIES = 3


# --- הראיה, מורכבת למסך אחד -------------------------------------------------


#: סימן ההיכר של תפריט שיש בו שורת קרנל בכלל. תפריט "דיסק מקומי
#: בלבד" — מה שמקבל MAC לא רשום, וגם מכונה שנחסמה אחרי כישלונות
#: חוזרים (#75) — אינו נושא שורת קרנל, ולכן היעדר הדגל בו אינו ראיה
#: לכלום. זו בדיוק המלכודת: בדיקה מול MAC מזויף הייתה מחזירה "סגור"
#: לנצח, כולל כשהדלת פתוחה לרווחה.
KERNEL_LINE = "imagectl.server="


def _probe_macs(ctx) -> list[str]:
    """מכונות רשומות שאפשר לבקש עבורן תפריט אמיתי. יותר מאחת, כי
    מכונה בודדת עלולה להיות חסומה ולקבל תפריט דיסק-מקומי."""
    try:
        rows = ctx.conn.execute(
            "SELECT mac FROM machines ORDER BY mac LIMIT 3").fetchall()
    except Exception:                                   # noqa: BLE001 — כוונה
        return []
    return [r["mac"] for r in rows]


def _stations_evidence(ctx, hooks, server_base: str) -> tuple[str, str]:
    """מה שהשרת **באמת** מגיש ב-GRUB עכשיו, ולא מה שההגדרה אומרת.

    זו הראיה החיובית של דלת התחנות: התפריט נמשך בחזרה ונקרא, עבור
    מכונה רשומה — כלומר תפריט שיש בו שורת קרנל להסתכל בה. תשובה
    שלא הגיעה, או תפריט בלי שורת קרנל, אינם "סגור": הם "לא ידוע",
    והם נצבעים אדום.
    """
    base = server_base.rstrip("/") + "/boot/menu?mac="
    problems = []
    for mac in _probe_macs(ctx) or ["00:00:00:00:00:00"]:
        try:
            status, text = hooks["http_text"](base + mac)
        except Exception as exc:                        # noqa: BLE001 — כוונה
            problems.append(f"{mac}: קריאת התפריט נכשלה ({exc})")
            continue
        if status != 200 or not text:
            problems.append(f"{mac}: התפריט לא נקרא (קוד {status})")
            continue
        if KERNEL_LINE not in text:
            problems.append(f"{mac}: התפריט שהוגש הוא דיסק-מקומי בלבד, "
                            "ואין בו שורת קרנל לבדוק")
            continue
        if ssh_switch.DEBUG_PREFIX in text:
            return "open", f"‏imagectl.debug בשורת הקרנל שמוגשת ל-{mac}"
        return "closed", f"שורת הקרנל שמוגשת ל-{mac} אינה נושאת imagectl.debug"
    return "unknown", (" · ".join(problems)
                       or "אין מכונה רשומה לבקש עבורה תפריט")


def snapshot(ctx, hooks: dict, server_base: str) -> dict:
    """מצב שתי הדלתות: מה ביקשנו, ולצידו מה שנקרא בחזרה."""
    listeners = hooks["listeners"]()
    nics = hooks["interfaces"]()
    open_now = ssh_switch.exposure(listeners, nics)
    wanted = set(ssh_switch.enabled_interfaces(ctx.conn))
    evidence, detail = _stations_evidence(ctx, hooks, server_base)
    return {
        "stations": {
            "enabled": ssh_switch.stations_enabled(ctx.conn),
            "evidence": evidence,
            "detail": detail,
            "confirm_word": STATION_CONFIRM,
        },
        "listeners": {
            "checked": listeners.checked,
            "addresses": list(listeners.addresses),
            "wildcard": listeners.wildcard,
            "reason": listeners.reason,
            "port": ssh_switch.SSH_PORT,
        },
        "stray": ssh_switch.stray_addresses(listeners, nics),
        "interfaces": [
            {
                "name": nic["name"],
                "state": nic.get("state", "unknown"),
                "addresses": nic.get("addresses") or [],
                "enabled": nic["name"] in wanted,
                # שלושה מצבים, לא שניים: None = לא נבדק.
                "listening": open_now.get(nic["name"]) if listeners.checked else None,
            }
            for nic in nics
        ],
    }


def _matches(state: dict) -> bool:
    """האם מה שמאזין תואם למה שביקשנו — על **כל** ממשק."""
    if not state["listeners"]["checked"]:
        return False
    return all(nic["enabled"] == bool(nic["listening"])
               for nic in state["interfaces"]) and not state["stray"]


# --- ההחלה, ואחריה הקריאה החוזרת --------------------------------------------


def _apply_and_verify(ctx, hooks: dict, server_base: str) -> dict:
    """מחילים, ואז **קוראים בחזרה**. הצלחה נקבעת רק לפי הקריאה.

    ‏`apply_sshd` שהחזיר None אומר רק שהפקודות רצו. ‏sshd_config ראשי
    עם ‏ListenAddress משלו, יחידה בלי ‏ReadWritePaths, ‏Include שהוסר —
    כל אלה מסתיימים ב"הפקודה הצליחה" ובדלת שנשארה פתוחה.
    """
    nics = hooks["interfaces"]()
    live = {nic["name"]: nic for nic in nics}
    addresses = [
        ssh_switch.bare_address(address)
        for name in ssh_switch.enabled_interfaces(ctx.conn)
        for address in (live.get(name, {}).get("addresses") or [])
    ]
    error = hooks["apply_sshd"](ssh_switch.render_sshd_conf(addresses))
    state = snapshot(ctx, hooks, server_base)
    for _ in range(VERIFY_TRIES - 1):
        if _matches(state):
            break
        hooks["settle"]()
        state = snapshot(ctx, hooks, server_base)
    return {"apply_error": error, "verified": _matches(state), "state": state}


def _confirm_or_409(body: dict, expected: str, what: str) -> None:
    if body.get("confirm") != expected:
        raise HTTPException(409, f"{what} — יש להקליד בדיוק: {expected}")


def create_ssh_router(ctx, hooks: dict, server_base: str) -> APIRouter:
    """‏admin בלבד. מתג SSH הוא ניהול, ולא אימג'ים או סבבים — משתמש
    ‏deploy מקבל 403 גם על הקריאה, כי גם רשימת הדלתות הפתוחות היא מידע
    ניהולי.

    התחילית יחסית: הראוטר נתלה בתוך ראוטר הבריאות (`/api/console`), כי
    המתג והחיווי הם שני צדדים של אותו מסך ושל אותם hooks מוזרקים.
    הנתיבים בפועל הם ‎/api/console/ssh/…

    **כל ה-endpoints כאן הם `def` ולא `async def`, ולא במקרה.** הראיה
    של דלת התחנות נמשכת ב-HTTP מהשרת עצמו; ‏endpoint אסינכרוני היה
    חוסם את לולאת האירועים שאמורה לענות לאותה בקשה, והאימות היה נכשל
    בפקיעת זמן — כלומר "לא אומת" על כל שינוי, על כל שרת אמיתי. ‏FastAPI
    מריץ `def` רגיל במאגר התהליכונים, והלולאה נשארת פנויה. נתפס מול
    שרת חי, לא בבדיקה עם hook מוזרק.
    """
    router = APIRouter(prefix="/ssh")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    @router.get("")
    def read(user=Depends(admin_only)):
        return snapshot(ctx, hooks, server_base)

    @router.put("/stations")
    def set_stations(body: dict, user=Depends(admin_only)):
        """שער `imagectl.debug` — מעטפת טכנאי *ו*-SSH בכל תחנה שעולה."""
        want = bool(body.get("enabled", False))
        if want:
            _confirm_or_409(body, STATION_CONFIRM,
                            "פתיחת SSH ומעטפת טכנאי בכל התחנות")
        set_setting(ctx.conn, ssh_switch.STATION_KEY, ssh_switch.flag_json(want))
        journal(ctx.conn, "ssh_stations",
                "on" if want else "off", user[0])
        state = snapshot(ctx, hooks, server_base)
        expected = "open" if want else "closed"
        verified = state["stations"]["evidence"] == expected
        if not verified:
            journal(ctx.conn, "ssh_unverified",
                    f"stations want={expected} saw={state['stations']['evidence']}",
                    user[0])
        return {"ok": verified, "verified": verified, "state": state}

    @router.put("/interfaces/{name}")
    def set_interface(name: str, body: dict, user=Depends(admin_only)):
        """‏sshd של השרת, לכל ממשק בנפרד: וילן ההפצה ווילן הכיתות
        אינם אותו סיכון, ולכן אינם אותו מתג."""
        want = bool(body.get("enabled", False))
        live = {nic["name"]: nic for nic in hooks["interfaces"]()}
        if name not in live:
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")
        before = ssh_switch.enabled_interfaces(ctx.conn)
        if want:
            if not (live[name].get("addresses") or []):
                raise HTTPException(
                    409, f"ל-{name} אין כתובת IPv4 — אי אפשר להגביל את sshd "
                    "לממשק בלי כתובת, וההגבלה היא כל מה שסוגר את השאר")
            _confirm_or_409(body, name, f"פתיחת SSH לשרת על {name}")
        elif before == [name]:
            # הדלת האחרונה. אחריה אין SSH לשרת מאף וילן.
            _confirm_or_409(body, name,
                            f"סגירת הממשק האחרון שפתוח ל-SSH ({name})")

        set_setting(ctx.conn, ssh_switch.IFACE_PREFIX + name,
                    ssh_switch.flag_json(want))
        journal(ctx.conn, "ssh_server",
                f"{name} {'on' if want else 'off'}", user[0])
        result = _apply_and_verify(ctx, hooks, server_base)
        if result["apply_error"] or not result["verified"]:
            journal(ctx.conn, "ssh_unverified",
                    f"{name} {result['apply_error'] or 'ההאזנה בפועל אינה תואמת'}",
                    user[0])
        return {"ok": result["apply_error"] is None and result["verified"],
                **result}

    return router
