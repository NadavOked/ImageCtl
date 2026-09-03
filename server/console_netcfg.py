"""לשונית "רשת", הצד השני של הכרטיס: איך השרת עצמו מחובר (‏#55–#57).

‏`console_dhcp.py` מגדיר מה השרת **מחלק**; כאן מגדירים את הכתובת שלו
עצמו, את השער, את ה-DNS ואת הנתיבים הסטטיים — ל-`interfaces.d`, כלומר
ששורדים אתחול. זה מה ש-#50 ביקש: `ip route add` שלא נעלם.

שלוש שכבות, בדיוק כפי שה-PRD מונה אותן:

1. **תצוגה מקדימה** — הקובץ שייכתב, לפני ואחרי, לפני שנוגעים במשהו.
2. **הקלדת שם הכרטיס** — שינוי כתובת הוא הרסני-בפועל (עיקרון 7).
3. **החזרה אוטומטית** כשנוגעים במה שיכול לנתק את הקונסולה — לא
   תהליכון כאן אלא יחידת systemd (‏`netcfg_rollback.py`), כי שינוי
   שהפיל את הרשת מפיל לפעמים גם את השרת.

ו**ההצלחה נקבעת בקריאה חוזרת בלבד** (עיקרון 5): ‏`ip addr`, ‏`ip route`
ו-`/etc/resolv.conf`, ולא ההגדרה ששמרנו. ‏"כתבנו את הקובץ", "ההגדרה
הוחלה" ו"הכתובת השתנתה" הם שלושה מצבים שונים.
"""

from __future__ import annotations

import time
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from . import auth, dhcp, netcfg, netcfg_host, netcfg_rollback
from .api import ServerContext
from .db import get_setting, journal, set_setting

Hooks = dict[str, Callable]


def default_hooks() -> Hooks:
    return {
        **netcfg_host.default_hooks(),
        # אותה רשימת כרטיסים של לשונית ה-DHCP — שני הצדדים של אותו
        # כרטיס חייבים להסכים מי קיים במכונה.
        "interfaces": dhcp.list_interfaces,
        "netcfg_now": time.time,
        "netcfg_boot_id": netcfg_rollback.boot_id,
        # מאיזו כתובת של השרת הגיעה הבקשה. ‏uvicorn שם ב-scope את
        # ה-sockname של החיבור — כלומר הכתובת שהקונסולה באמת מדברת
        # אליה, ולא זו שהוגדרה בשורת הפקודה.
        "netcfg_local_address": lambda request: (request.scope.get("server")
                                                 or ("", 0))[0],
    }


# --- הפירורים של זרוע ההחזרה → שורות יומן ------------------------------------


def drain_crumbs(ctx, state_dir, hooks: Hooks | None = None) -> int:
    """ממיר כל פירור שזרוע ההחזרה השאירה לשורת יומן, ומוחק אותו.

    זו ההשלמה של #56: ההחזרה בעלייה רצה **כשהשרת לא רץ**, ולכן אין לה
    חיבור ל-DB. היא משאירה פירור, וכאן הוא הופך לעברית — עם **זמן
    ההחזרה** ולא זמן הקריאה. בלי זה מגלים שהשינוי לא נתפס רק כשמשהו
    מפסיק לעבוד, וזה עלול להיות חודשים אחר כך.

    נקרא בעליית השרת וגם בכל קריאת מצב, כדי שהחזרה שקרתה בזמן שהשרת
    חי תגיע ליומן מיד ולא רק בהפעלה הבאה.
    """
    crumbs = netcfg_rollback.read_crumbs(state_dir)
    for crumb in crumbs:
        if crumb is None or not crumb.get("at"):
            journal(ctx.conn, "net_rollback_unreadable", "")
            continue
        detail = (f"{crumb.get('interface') or '?'} at={crumb['at']} "
                  f"reason={crumb.get('reason') or '?'}")
        errors = crumb.get("errors") or []
        if errors:
            detail += f" errors={len(errors)}: {' · '.join(str(e) for e in errors)[:200]}"
        journal(ctx.conn, "net_rollback", detail)
        # ה-DB חייב לחזור יחד עם המכונה: הגדרה שמוצגת בקונסולה ואינה
        # זו שעל הכרטיס היא בדיוק אותו שקר בכיוון ההפוך.
        if crumb.get("setting") is not None and crumb.get("interface"):
            set_setting(ctx.conn, netcfg.SETTING_PREFIX + crumb["interface"],
                        crumb["setting"])
    if crumbs:
        netcfg_rollback.clear_crumbs(state_dir)
    return len(crumbs)


def create_netcfg_router(ctx: ServerContext, state_dir,
                         hooks: Hooks | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/console/net/config")
    _current_user, admin_only = auth.dependencies(ctx.conn)
    hooks = {**default_hooks(), **(hooks or {})}

    def load(name: str) -> netcfg.NetConfig:
        return netcfg.NetConfig.from_json(
            name, get_setting(ctx.conn, netcfg.SETTING_PREFIX + name))

    def all_configs() -> list[netcfg.NetConfig]:
        rows = ctx.conn.execute(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (netcfg.SETTING_PREFIX + "%",)).fetchall()
        return [netcfg.NetConfig.from_json(
            r["key"][len(netcfg.SETTING_PREFIX):], r["value"]) for r in rows]

    def dhcp_of(name: str) -> dhcp.InterfaceConfig:
        return dhcp.InterfaceConfig.from_json(
            name, get_setting(ctx.conn, dhcp.SETTING_PREFIX + name))

    def marker():
        return netcfg_rollback.read_pending(state_dir)

    def active_interface(request: Request, live: dict) -> str | None:
        """הכרטיס שדרכו הגיעה הבקשה — או None כשלא ניתן לקבוע.

        ‏None אינו "לא הכרטיס הזה". הקורא מתייחס אליו כאל *כל* כרטיס
        (‏`touches_console`), כי מי שלא יודע דרך מה הוא מחובר חייב
        להניח שהוא מנתק את עצמו.
        """
        try:
            address = hooks["netcfg_local_address"](request)
        except Exception:                              # noqa: BLE001 — כוונה
            return None
        if not address:
            return None
        for nic in live.values():
            if any(a.split("/")[0] == address for a in nic.get("addresses") or []):
                return nic["name"]
        return None

    def touches_console(name: str, before: netcfg.NetConfig,
                        after: netcfg.NetConfig, active: str | None) -> bool:
        """האם השינוי יכול לנתק את הקונסולה — ולכן חייב החזרה אוטומטית.

        שער, ‏DNS ונתיבים נחשבים "נגיעה בכרטיס הפעיל" גם על כרטיס אחר
        (‏#57): כולם משנים דרך מי הקונסולה נענית.
        """
        if active is None or active == name:
            return True
        return any(f in netcfg.REACH_FIELDS
                   for f in netcfg.changed(before, after))

    def state_view(cfg: netcfg.NetConfig, live: dict | None,
                   state: netcfg_host.LiveState) -> dict:
        return {
            "name": cfg.name, "mode": cfg.mode, "mode_he": netcfg.MODES_HE[cfg.mode],
            "address": cfg.address, "netmask": cfg.netmask,
            "gateway": cfg.gateway, "dns": cfg.dns,
            "routes": [r.__dict__ for r in cfg.routes],
            "present": live is not None,
            "state": (live or {}).get("state", "missing"),
            # הראיה, ולא ההגדרה: מה ש-`ip addr` מראה עכשיו.
            "live_addresses": state.addresses.get(cfg.name, []) if state.checked else [],
            "mismatches": netcfg_host.mismatches(cfg, state),
        }

    def rollback_view(now: float) -> dict:
        pending, armed = marker(), hooks["netcfg_timer_active"]()
        return {
            "armed": bool(armed[0]), "armed_detail": armed[1],
            "unit": netcfg_host.ROLLBACK_TIMER,
            "window_seconds": netcfg_rollback.WINDOW_SECONDS,
            "pending": pending is not None,
            "interface": pending.interface if pending else "",
            "seconds_left": max(0, int(pending.deadline - now)) if pending else 0,
            "expired": netcfg_rollback.expired(pending, now),
            "corrupt": netcfg_rollback.corrupt_pending(state_dir),
        }

    @router.get("")
    def read(user=Depends(admin_only)):
        """ההגדרה של כל כרטיס, ולצידה מה שנקרא בחזרה מהמכונה."""
        drain_crumbs(ctx, state_dir, hooks)
        live = {i["name"]: i for i in hooks["interfaces"]()}
        state = hooks["netcfg_state"]()
        names = sorted(set(live) | {c.name for c in all_configs()})
        return {
            "interfaces": [state_view(load(n), live.get(n), state) for n in names],
            "live": {"checked": state.checked, "reason": state.reason,
                     "routes": state.routes, "nameservers": state.nameservers},
            "sourced": hooks["netcfg_sourced"](),
            "resolv_path": netcfg_host.RESOLV_CONF,
            "rollback": rollback_view(hooks["netcfg_now"]()),
        }

    def build(name: str, body: dict) -> netcfg.NetConfig:
        return netcfg.NetConfig(
            name=name,
            mode=str(body.get("mode") or netcfg.MODE_MANUAL).strip(),
            address=str(body.get("address", "")).strip(),
            netmask=str(body.get("netmask") or "255.255.255.0").strip(),
            gateway=str(body.get("gateway", "")).strip(),
            dns=[d.strip() for d in _as_list(body.get("dns")) if d.strip()],
            routes=[netcfg.StaticRoute.from_any(r)
                    for r in (body.get("routes") or [])],
        )

    def check(cfg: netcfg.NetConfig) -> list[str]:
        others = [c for c in all_configs() if c.name != cfg.name] + [cfg]
        return netcfg.problems(cfg, others, dhcp_of(cfg.name))

    @router.post("/{name}/preview")
    async def preview(name: str, request: Request, user=Depends(admin_only)):
        """הקובץ שייכתב, לפני ואחרי — ואילו בעיות יעצרו אותו.

        אותה תבנית של "קובץ dnsmasq שנוצר", ובאותה כוונה: מי שרואה את
        הטקסט לפני ההחלה תופס טעות כשהיא עדיין טקסט.
        """
        cfg = build(name, await request.json())
        after = [c for c in all_configs() if c.name != name] + [cfg]
        return {
            "path": str(netcfg_host.conf_path(name)),
            "before": hooks["netcfg_read_conf"](name, netcfg_host.INTERFACES_DIR) or "",
            "after": netcfg.render(cfg),
            "resolv_path": netcfg_host.RESOLV_CONF,
            "resolv_after": netcfg.render_resolv(sorted(after, key=lambda c: c.name)),
            "problems": check(cfg),
            "changed": netcfg.changed(load(name), cfg),
        }

    @router.put("/{name}")
    async def configure(name: str, request: Request, user=Depends(admin_only)):
        body = await request.json()
        live = {i["name"]: i for i in hooks["interfaces"]()}
        if name not in live and not body.get("allow_missing"):
            raise HTTPException(404, "כרטיס רשת כזה לא קיים במכונה")
        before, cfg = load(name), build(name, body)
        found = check(cfg)
        if found:
            raise HTTPException(400, " · ".join(found))
        # עיקרון 7: שינוי כתובת הוא הרסני-בפועל — הוא מנתק מחשבים, ועל
        # הכרטיס הפעיל גם את המפעיל עצמו. הקלדת השם, כמו בהדלקת DHCP.
        if body.get("confirm") != name:
            raise HTTPException(
                409, f"לשינוי הגדרות הרשת יש להקליד את שם הכרטיס בדיוק: {name}")
        now = hooks["netcfg_now"]()
        if netcfg_rollback.read_pending(state_dir) is not None:
            raise HTTPException(
                409, "שינוי רשת קודם עדיין ממתין לאישור — אשרו אותו או "
                "המתינו שיוחזר, לפני שינוי נוסף")
        arm = touches_console(name, before, cfg, active_interface(request, live))
        if arm:
            ready, detail = hooks["netcfg_timer_active"]()
            if not ready:
                raise HTTPException(
                    409, f"השינוי הזה יכול לנתק את הקונסולה, וההחזרה "
                    f"האוטומטית ({netcfg_host.ROLLBACK_TIMER}) אינה פעילה: "
                    f"{detail}. בלי הזרוע הזו אין דרך חזרה — התקינו אותה "
                    "לפי docs/server-install.md.")
            error = _arm(name, before, cfg, now)
            if error:
                raise HTTPException(409, error)
        set_setting(ctx.conn, netcfg.SETTING_PREFIX + name, cfg.to_json())
        journal(ctx.conn, "net_config",
                f"{netcfg.summary(cfg)} changed={','.join(netcfg.changed(before, cfg))}",
                user[0])
        return _apply(name, cfg, arm, now, user[0], live.get(name))

    def _arm(name: str, before: netcfg.NetConfig, after: netcfg.NetConfig,
             now: float) -> str | None:
        """כותב את סמן ההחזרה **לפני** שנוגעים בקובץ כלשהו.

        הסדר הוא ההגנה: מכונה שמתה בין הכתיבה להחלה חייבת להשאיר סמן
        פתוח. סמן שנכתב אחרי השינוי היה משאיר חלון שבו השינוי כבר
        חי ואין מה שיחזיר אותו.
        """
        marker = netcfg_rollback.Pending(
            interface=name,
            deadline=now + netcfg_rollback.WINDOW_SECONDS,
            armed_at=_iso(now),
            boot=hooks["netcfg_boot_id"](),
            files=[{"name": name,
                    "text": hooks["netcfg_read_conf"](
                        name, netcfg_host.INTERFACES_DIR)}],
            resolv=_read_resolv(),
            setting=before.to_json(),
        )
        error = netcfg_rollback.write_pending(state_dir, marker)
        if error:
            return (f"{error}. בלי סמן ההחזרה השינוי הזה הוא חד-כיווני, "
                    "ולכן הוא לא בוצע.")
        journal(ctx.conn, "net_rollback_armed",
                f"{name} window={netcfg_rollback.WINDOW_SECONDS}s")
        return None

    def _read_resolv() -> str | None:
        try:
            return open(netcfg_host.RESOLV_CONF, encoding="utf-8").read()
        except OSError:
            return None

    def _apply(name: str, cfg: netcfg.NetConfig, armed: bool, now: float,
               who: str, live: dict | None) -> dict:
        """כותב, מחיל, ואז **קורא בחזרה**. ‏ok נקבע רק מהקריאה."""
        configs = sorted([c for c in all_configs() if c.name != name] + [cfg],
                         key=lambda c: c.name)
        errors = [hooks["netcfg_write_conf"](name, netcfg.render(cfg),
                                             netcfg_host.INTERFACES_DIR),
                  hooks["netcfg_write_resolv"](netcfg.render_resolv(configs),
                                               netcfg_host.RESOLV_CONF)]
        error = " · ".join(e for e in errors if e) or None
        if error is None:
            error = hooks["netcfg_apply"](name)
        state = hooks["netcfg_state"]()
        gaps = netcfg_host.mismatches(cfg, state)
        verified = not gaps
        if error or not verified:
            journal(ctx.conn, "net_config_unverified",
                    f"{name} {error or ' · '.join(gaps)[:200]}", who)
        if not armed:
            netcfg_rollback.clear_pending(state_dir)
        return {"ok": error is None and verified, "verified": verified,
                "apply_error": error, "mismatches": gaps,
                "interface": state_view(cfg, live, state),
                "rollback": rollback_view(now)}

    @router.post("/confirm")
    async def confirm(request: Request, user=Depends(admin_only)):
        """"אני עדיין רואה את הקונסולה" — הראיה החיובית שהשינוי שרד.

        היעדר ניתוק אינו אישור: בלי הבקשה הזאת ההגדרה הקודמת חוזרת.
        אישור **אחרי** שהחלון נסגר נדחה — ההחזרה כבר קרתה או עומדת
        לקרות, ו-"ביטול" שלה היה משאיר מצב שאיש לא בחר בו.
        """
        body = await request.json()
        now = hooks["netcfg_now"]()
        pending = marker()
        if pending is None:
            drain_crumbs(ctx, state_dir, hooks)
            raise HTTPException(409, "אין שינוי רשת שממתין לאישור — ייתכן "
                                "שהוא כבר הוחזר. בדקו את היומן ואת לשונית הרשת.")
        if body.get("interface") and body["interface"] != pending.interface:
            raise HTTPException(409, f"הממתין לאישור הוא {pending.interface}")
        if netcfg_rollback.expired(pending, now):
            raise HTTPException(409, f"חלון האישור על {pending.interface} נסגר. "
                                "ההגדרה הקודמת חוזרת, ואי אפשר לבטל את זה מכאן.")
        netcfg_rollback.clear_pending(state_dir)
        journal(ctx.conn, "net_confirmed", pending.interface, user[0])
        return {"ok": True, "interface": pending.interface,
                "rollback": rollback_view(now)}

    return router


def _iso(epoch: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _as_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return value.replace(";", ",").split(",")
    return []
