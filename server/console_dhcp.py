"""לשונית "רשת", החצי השני: DHCP לכל כרטיס רשת (אפיון סעיף 24).

console_net.py מראה מה השרת ראה; כאן מגדירים מה השרת *מחלק*. ההגדרות
נשמרות בטבלת settings (מפתח `dhcp:<ממשק>`), ואחרי כל שינוי נכתב קובץ
dnsmasq ו-dnsmasq מופעל מחדש.

שכבות הבטיחות, לפי הסדר שבאפיון:
1. ברירת המחדל לכל ממשק — כבוי. ממשק בלי רשומה = כבוי.
2. הדלקה דורשת `confirm` שווה בדיוק לשם הממשק.
3. אם השרת רואה DHCP קיים על הממשק — סירוב, אלא אם `ignore_existing`.
4. ממשק שסומן trunk דורש גם `confirm_trunk: true`.
"""

from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, dhcp
from .api import ServerContext
from .db import get_setting, journal, set_setting

Hooks = dict[str, Callable]

#: תיאור חופשי לכרטיס (settings) — "700" בשביל וילן 700, וכדומה.
DESC_PREFIX = "nicdesc:"


def _checked_name(name: str) -> str:
    """שם כרטיס תקין, או 400 בעברית. הכלל עצמו יושב ב-`dhcp.validate_name`
    ‏— מקום אחד שכל הכותבים עוברים דרכו, ולא עותק שני של הביטוי כאן."""
    try:
        dhcp.validate_name(name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return name


def default_hooks() -> Hooks:
    return {
        "interfaces": dhcp.list_interfaces,
        "probe": dhcp.probe_existing_dhcp,
        "apply": dhcp.apply,
        "apply_proxy": dhcp.apply_proxy,
        "dnsmasq_version": dhcp.dnsmasq_version,
    }


def create_dhcp_router(ctx: ServerContext, hooks: Hooks | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console/net")
    current_user, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    def load(name: str) -> dhcp.InterfaceConfig:
        return dhcp.InterfaceConfig.from_json(
            name, get_setting(ctx.conn, dhcp.SETTING_PREFIX + name)
        )

    def all_configs() -> list[dhcp.InterfaceConfig]:
        rows = ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (dhcp.SETTING_PREFIX + "%",),
        ).fetchall()
        return [
            dhcp.InterfaceConfig.from_json(r["key"][len(dhcp.SETTING_PREFIX):], r["value"])
            for r in rows
        ]

    def apply_all(what: str, user_id) -> str | None:
        """מחילים על שני האינסטנסים (‏#36): הראשי מחלק כתובות, וה-proxy
        רץ בתהליך משלו כדי שקפיאה שלו לא תוריד את וילן ההפצה. כשל באחד
        לא מסתיר את השני — שתי ההודעות חוזרות לקונסולה וליומן."""
        configs = all_configs()
        try:
            texts = dhcp.render(configs), dhcp.render_proxy(configs)
        except ValueError as exc:      # רשומה פסולה שנשמרה לפני #102
            journal(ctx.conn, "dhcp_apply_failed", f"{what} {exc}", user_id)
            return str(exc)
        errors = [
            hooks["apply"](texts[0]),
            hooks["apply_proxy"](texts[1], bool(dhcp.proxy_only(configs))),
        ]
        error = " · ".join(e for e in errors if e) or None
        if error:
            journal(ctx.conn, "dhcp_apply_failed", f"{what} {error}", user_id)
        return error

    def view(cfg: dhcp.InterfaceConfig, live: dict | None) -> dict:
        data = {
            "name": cfg.name, "enabled": cfg.enabled, "proxy": cfg.proxy,
            "trunk": cfg.trunk, "range_start": cfg.range_start,
            "range_end": cfg.range_end, "netmask": cfg.netmask,
            "gateway": cfg.gateway, "dns": cfg.dns, "lease": cfg.lease,
            "server_ip": cfg.server_ip,
            # תיאור חופשי לכרטיס — למשל מספר ה-VLAN שהוא מחובר אליו.
            "description": get_setting(ctx.conn, DESC_PREFIX + cfg.name) or "",
            "state": (live or {}).get("state", "missing"),
            "mac": (live or {}).get("mac", ""),
            "addresses": (live or {}).get("addresses", []),
            "present": live is not None,
        }
        return data

    @router.get("/interfaces")
    def interfaces(user=Depends(current_user)):
        """כל כרטיס רשת במכונה + ההגדרה שלו. כרטיס בלי הגדרה = כבוי."""
        live = {i["name"]: i for i in hooks["interfaces"]()}
        names = sorted(set(live) | {c.name for c in all_configs()})
        return [view(load(n), live.get(n)) for n in names]

    @router.get("/interfaces/{name}/probe")
    def probe(name: str, user=Depends(admin_only)):
        """מי כבר עונה ל-DHCP על הממשק. הבדיקה שמונעת את התקלה הגרועה ביותר.

        אין לו עדיין קורא בקונסולה — הוא הצורה הבדוקה של אותה בדיקה
        שההדלקה מריצה, ומאפשר לראות *למה* היא חסמה: `checked=false`
        פירושו שהבדיקה לא רצה, לא שהכרטיס נקי.
        """
        found = hooks["probe"](name)
        return {"interface": name, "checked": found.checked,
                "servers": list(found.servers)}

    @router.get("/proxy-support")
    def proxy_support(user=Depends(admin_only)):
        """מה גרסת ה-dnsmasq המותקנת אומרת על מצב proxy (‏#36).

        המסך מציג את `reason` כלשונו ולא מנסח משלו — כך שההסבר שהמפעיל
        רואה הוא בדיוק זה שה-API יסרב בו, ושניהם לא יכולים להתפצל.
        """
        support = dhcp.proxy_support(hooks["dnsmasq_version"]())
        return {"read": support.read, "version": support.version,
                "verified": support.verified, "broken": support.broken,
                "reason": support.reason()}

    @router.put("/interfaces/{name}")
    async def configure(name: str, request: Request, user=Depends(admin_only)):
        # השם מגיע מנתיב ה-URL, ו-uvicorn מפענח %0A לשורה חדשה. הבדיקה
        # ראשונה בכוונה: עם allow_missing אין שום שלב אחר שרואה את השם
        # לפני שהוא נכתב לקובץ של dnsmasq (‏#102).
        _checked_name(name)
        body = await request.json()
        live = {i["name"]: i for i in hooks["interfaces"]()}
        if name not in live and not body.get("allow_missing"):
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")

        before = load(name)
        cfg = dhcp.InterfaceConfig(
            name=name,
            enabled=bool(body.get("enabled", False)),
            proxy=bool(body.get("proxy", False)),
            trunk=bool(body.get("trunk", before.trunk)),
            range_start=str(body.get("range_start", "")).strip(),
            range_end=str(body.get("range_end", "")).strip(),
            netmask=str(body.get("netmask") or "255.255.255.0").strip(),
            gateway=str(body.get("gateway", "")).strip(),
            dns=[d.strip() for d in _as_list(body.get("dns")) if d.strip()],
            lease=str(body.get("lease") or dhcp.DEFAULT_LEASE).strip(),
            server_ip=str(body.get("server_ip", "")).strip(),
        )
        if cfg.enabled and cfg.proxy:
            raise HTTPException(400, "DHCP מלא ו-proxy על אותו ממשק סותרים זה את זה")

        # שכבה 5 (‏#36): מצב proxy נשען על תכונה שבורה ב-dnsmasq המותקן.
        # ההגנה היא אישור מפורש, לא כפתור מנוטרל — במעבדה *חייבים* להיות
        # מסוגלים להדליק אותו כדי לבדוק גרסה חדשה, וכפתור אטום בלי דרך
        # חוקית עובר בקלות ל-curl ישיר. גם המסך וגם ה-API עוברים כאן.
        # הבדיקה על המעבר ל-proxy — כולל מעבר מ-DHCP מלא, שאינו "הדלקה".
        risky_proxy = None
        if cfg.proxy and not before.proxy:
            support = dhcp.proxy_support(hooks["dnsmasq_version"]())
            if not support.verified:
                if body.get("confirm_proxy_broken") is not True:
                    raise HTTPException(409, support.reason())
                risky_proxy = support.version or "unknown"
        try:
            dhcp.validate(cfg)
        except ValueError as exc:
            raise HTTPException(400, str(exc))

        turning_on = (cfg.enabled or cfg.proxy) and not (before.enabled or before.proxy)
        if turning_on or (cfg.enabled and not before.enabled):
            # שכבה 2: השם המדויק. לחיצה מקרית לא מדליקה DHCP.
            if body.get("confirm") != name:
                raise HTTPException(
                    409, f"להדלקה יש להקליד את שם הממשק בדיוק: {name}"
                )
            # שכבה 4: trunk. המכללה כולה מאחורי הממשק הזה.
            if cfg.trunk and cfg.enabled and body.get("confirm_trunk") is not True:
                raise HTTPException(
                    409, "הממשק מסומן כמחובר לרשת המכללה — נדרש אישור נוסף (confirm_trunk)"
                )
            # שכבה 3: מישהו כבר מחלק כתובות כאן?
            if cfg.enabled and not body.get("ignore_existing"):
                found = hooks["probe"](name)
                # בדיקה שלא רצה אינה "נקי" — היא "לא יודעים" (‏#53), וברשת
                # המכללה "לא יודעים" עולה רשת שלמה. פורט 68 תפוס הוא בדיוק
                # מה שקורה על כרטיס trunk שמקבל כתובת מהמכללה.
                if not found.checked:
                    raise HTTPException(
                        409, f"לא ניתן לבדוק אם כבר יש DHCP על {name} — הבדיקה "
                        "עצמה לא רצה (נדרשות הרשאות root, ופורט 68 חייב להיות "
                        "פנוי; לקוח DHCP שרץ על הכרטיס תופס אותו). לא נמצא "
                        "שרת אחר — פשוט לא נבדק. אם אתה יודע שהכרטיס אינו "
                        "מחובר לרשת המכללה, הדלק עם ignore_existing.",
                    )
                if found.servers:
                    raise HTTPException(
                        409, "נמצא שרת DHCP פעיל על הממשק: "
                        + ", ".join(found.servers)
                        + ". הדלקת DHCP שני תשבית את הרשת.",
                    )

        set_setting(ctx.conn, dhcp.SETTING_PREFIX + name, cfg.to_json())
        state = "on" if cfg.enabled else ("proxy" if cfg.proxy else "off")
        journal(ctx.conn, "dhcp_set", f"{name} {state}", user[0])
        if risky_proxy:
            # מי הדליק proxy על גרסה שלא נבדקה, ועל איזו גרסה — כשמישהו
            # ישאל למה ה-DHCP קפא, זו השורה שעונה.
            journal(ctx.conn, "dhcp_proxy_risk",
                    f"{name} dnsmasq={risky_proxy}", user[0])

        error = apply_all(name, user[0])
        return {"ok": error is None, "interface": view(cfg, live.get(name)),
                "apply_error": error}

    @router.post("/interfaces")
    async def add_interface(request: Request, user=Depends(admin_only)):
        """הוספת כרטיס ידנית — תת-ממשק של וילן שעוד לא הוגדר במכונה.

        נוצרת רשומת תצורה כבויה, כדי שהכרטיס יופיע בטבלה ויהיה אפשר
        לתאר אותו ולהגדיר עליו DHCP עוד לפני שהוא קיים פיזית.
        """
        body = await request.json()
        name = _checked_name((body.get("name") or "").strip())
        # כרטיס חי מותר "להוסיף" — זו קליטה שלו: תצורה כבויה + תיאור.
        if get_setting(ctx.conn, dhcp.SETTING_PREFIX + name):
            raise HTTPException(409, "הכרטיס כבר הוגדר")
        set_setting(ctx.conn, dhcp.SETTING_PREFIX + name,
                    dhcp.InterfaceConfig(name).to_json())
        if (body.get("description") or "").strip():
            set_setting(ctx.conn, DESC_PREFIX + name, body["description"].strip())
        journal(ctx.conn, "nic_add", name, user[0])
        return {"name": name}

    @router.put("/interfaces/{name}/description")
    async def describe(name: str, request: Request, user=Depends(admin_only)):
        """תיאור חופשי לכרטיס — למשל איזה VLAN מחובר אליו.

        השם עובר את אותה בדיקה כמו בשני המסלולים האחרים (#102). כאן זו
        אינה הזרקה ל-dnsmasq — ‏`all_configs()` קוראת רק מפתחות
        ‏`dhcp:` — אלא **כישלון שקט**: ‏``nicdesc:eth0 `` (עם רווח) הוא
        מפתח אחר מ-``nicdesc:eth0``, ולכן התיאור נשמר, מוחזר
        ``{"ok": true}``, ולעולם אינו מוצג.
        """
        _checked_name(name)
        body = await request.json()
        set_setting(ctx.conn, DESC_PREFIX + name,
                    (body.get("description") or "").strip())
        journal(ctx.conn, "net_describe",
                f'{name} {body.get("description", "")}', user[0])
        return {"ok": True}

    @router.delete("/interfaces/{name}")
    def forget(name: str, user=Depends(admin_only)):
        """מסיר את מה שנשמר על הכרטיס — הגדרת DHCP ותיאור.

        כרטיס חי חוזר לברירת המחדל (כבוי); כרטיס שכבר לא קיים במכונה
        נעלם מהרשימה. אם היה עליו DHCP פעיל — dnsmasq מתעדכן מיד.

        **כאן אין `_checked_name` בכוונה.** זו המחיקה, והיא הדרך
        היחידה להסיר מפתח שנכתב בשם פסול לפני שהבדיקה נוספה (#130).
        אימות כאן היה נועל את הזבל לתמיד. מחיקה לפי מפתח מדויק אינה
        יכולה לכתוב דבר, ולכן שם פסול פשוט לא מוחק כלום.
        """
        was = load(name)
        ctx.conn.execute("DELETE FROM settings WHERE key IN (?, ?)",
                         (dhcp.SETTING_PREFIX + name, DESC_PREFIX + name))
        ctx.conn.commit()
        journal(ctx.conn, "nic_forget", name, user[0])
        error = apply_all(name, user[0]) if (was.enabled or was.proxy) else None
        return {"ok": error is None, "apply_error": error}

    @router.get("/dnsmasq")
    def preview(user=Depends(admin_only)):
        """הקבצים שייכתבו — לעין, לפני ואחרי. שניים, כי ה-proxy רץ
        באינסטנס נפרד (‏#36)."""
        configs = all_configs()
        try:
            text, proxy_text = dhcp.render(configs), dhcp.render_proxy(configs)
        except ValueError as exc:      # רשומה פסולה שנשמרה לפני #102
            raise HTTPException(500, str(exc))
        return {"text": text, "path": dhcp.DEFAULT_CONF,
                "proxy_text": proxy_text,
                "proxy_path": dhcp.PROXY_CONF, "proxy_unit": dhcp.PROXY_UNIT}

    return router


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [p for p in value.replace(";", ",").split(",")]
    return []
