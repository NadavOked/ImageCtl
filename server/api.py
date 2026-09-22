"""נקודות הקצה שהסוכן צורך — ממשקים 2, 3, 4 ו-7.

הכללים כאן זהים לצד השני של הרשת: לא זורקים חריגות החוצה, כל כשל
מוחזר כתשובה שסופה דיסק מקומי או שגיאה מסודרת בתבנית המוסכמת.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from sqlite3 import Connection

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac

from . import (agent_loops, disk_events, foreign_vlan, identity, inventory,
               login_guard, probe, pulls, registry, reports, shrink_records, ssh_hostkey,
               users)
from .db import journal
from . import direct
from .hello import (build_answer, hello_payload_oversized, login_required, off_deploy_vlan,
                    well_formed_monitor_auth, well_formed_monitor_secret)
from .images import ImageLibrary, restore_refusal
from .sessions import SessionError, SessionStore
from .tasks import TOKEN_HEADER

log = logging.getLogger("imagectl.api")

#: ‏#906: אורך השאלה שנשמרת מ-hello (`prompt`). שורת מסך אחת של הסוכן
#: היא ~60 תווים; מעבר לזה נקצץ, לא נדחה.
PROMPT_MAX_CHARS = 200

#: ‏#402: הערכים של `disk_probe` בממשק 2. `unchecked` ≠ `no_disks`
#: (עיקרון 5): "לא הצלחנו לספור פורטים" אינו "אפס פורטים".
DISK_PROBE_VALUES = frozenset({"drives", "no_disks", "no_ports", "unchecked"})


@dataclass
class ServerContext:
    conn: Connection
    library: ImageLibrary
    store: SessionStore
    sender: object | None = None      # SenderEngine; None בבדיקות יחידה
    drivers: object | None = None     # DriverLibrary (#720); None בבדיקות ישנות
    #: ‏#855: מקור חכירות ה-DHCP (`identity.LeaseFile`) — הצד שנוגע בקובץ,
    #: מוזרק כמו `dhcp_hooks`. ‏`server.main` מתקין אותו תמיד; ‏None =
    #: הרצת בדיקות בלי מקור, ואז שומר הזהות אינו מותקן (וה-/health אומר).
    leases: object | None = None
    #: ‏#1128: תיקיית הנתונים — לגיבוי ההגדרות מהקונסולה; None בבדיקות ישנות.
    data_dir: Path | None = None


def _error(status: int, message: str, code: str) -> JSONResponse:
    """תבנית השגיאות מהמוסכמות הרוחביות."""
    return JSONResponse(
        {"ok": False, "error": message, "code": code}, status_code=status
    )


def _identity_refusal(ctx: ServerContext, verdict: identity.Verdict,
                      where: str) -> JSONResponse | None:
    """‏`None` = ממשיכים. סירוב = 403 בשם (`identity_refused` /
    `identity_unverifiable`) ושורת יומן שנוקבת בנתיב — **לפני** כל רישום:
    ‏`net_seen`, ספירת הלולאות, ההצטרפות לסבב, רשומת הכיווץ ואירוע הדיסק
    לא מתרחשים, כי הפונה אינו המכונה (‏#585)."""
    if verdict.ok:
        return None
    journal(ctx.conn, verdict.event, f"{verdict.detail} ({where})")
    log.warning("%s: %s (%s)", verdict.event, verdict.detail, where)
    return _error(403, verdict.message, verdict.event)


def identity_gate(ctx: ServerContext, mac: str, request: Request,
                  where: str) -> JSONResponse | None:
    """‏#855/#997: ‏MAC מוצהר + כתובת המקור תואמת את חכירת ה-DHCP של אותו MAC.

    שומר **אחד** לכל נתיב שמזהה מכונה לפי MAC מהגוף — ‏hello, ‏progress,
    ‏login, ‏pulls, ‏disk-event, ‏shrink-*, וגם `station.py` (‏sessions)
    ו-`direct.py` (המניפסט החי, לפי ה-MAC של המשימה). ‏`ctx.leases is None`
    = השומר אינו מותקן (הרצת בדיקות בלי מקור; ‏`server.main` מתקין תמיד).
    """
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    return _identity_refusal(
        ctx, identity.verify(ctx.conn, ctx.leases, mac, client_ip), where)


def source_gate(ctx: ServerContext, request: Request, where: str) -> JSONResponse | None:
    """‏#997: זהות בלי MAC מוצהר — `GET /images/…/manifest|files`. הסוכן
    מושך אותם ב-`http_get` בלי MAC ובלי כותרת (‏`agent/lib/common.sh`), ולכן
    הכיוון הפוך: כתובת המקור → החכירה שיושבת עליה → MAC רשום ב-`machines`.
    אותו מתג, אותם קודים. הסוכן אינו משתנה ואין initrd חדש."""
    if ctx.leases is None:
        return None
    client_ip = request.client.host if request.client else None
    verdict = identity.verify_source(
        ctx.conn, ctx.leases, client_ip,
        lambda mac: registry.lookup(ctx.conn, mac) is not None)
    return _identity_refusal(ctx, verdict, where)


def create_agent_router(ctx: ServerContext,
                        server_base: str | None = None,
                        data_dir: Path | None = None) -> APIRouter:
    """‏server_base — כתובת וילן ההפצה (מ---server-url). ‏hello שהתקבל על
    כתובת מקומית אחרת דורש כניסה תמיד (#42); בלעדיה אין עם מה להשוות,
    וההתנהגות היא הישנה.
    """
    router = APIRouter(prefix="/api/v1")

    @router.post("/agent/hello")
    async def agent_hello(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")

        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # ‏#1127: המלאי/ה-probe/מפתח ה-SSH נשמרים כ-JSON בלי תקרה; הסוכן
        # שלנו לעולם לא מתקרב לזו, ומי שכן — נאמר לו, לא נשמר.
        oversized = hello_payload_oversized(body)
        if oversized:
            return _error(413, f"hello field {oversized} exceeds the size or depth cap", "payload_too_large")
        refused = identity_gate(ctx, mac, request, "hello")
        if refused is not None:
            return refused

        # ‏#524: זיהוי לפי כל כרטיס שהמכונה דיווחה, לא רק כרטיס האתחול.
        # ערך פגום מתעלמים ממנו — כמו שדה לא ידוע, לא כמו MAC ראשי חסר.
        all_macs: list[str] = []
        raw_all = body.get("all_macs")
        if isinstance(raw_all, list):
            for item in raw_all:
                extra = lenient_mac(item)
                if extra is not None and extra not in all_macs:
                    all_macs.append(extra)

        client_ip = request.client.host if request.client else None
        disks = body.get("disks") if isinstance(body.get("disks"), list) else None
        reported_ip = body.get("ip") if isinstance(body.get("ip"), str) else None
        off_vlan = off_deploy_vlan(request.scope, server_base)
        # ‏hello של דופק (`joining: false`) אומר "אני חי" בלי לבקש להצטרף.
        # מכונה שנעצרה על שגיאה חייבת להישאר נראית בקונסולה, אבל אסור
        # שתיספר כמצטרפת לגל שהיא לא תבצע (#64). סוכן ישן אינו שולח את
        # השדה — והיעדרו נשאר "מצטרף", כמו תמיד.
        joining = body.get("joining")
        if not isinstance(joining, bool):
            joining = True
        # ‏#839: סוד המוניטור של האתחול הזה — 32 ספרות hex קנוניות, כמו
        # שמונה-עשר הבייטים של MAC. ערך פגום נזנח כמו שדה לא ידוע; סוכן
        # ישן אינו שולח אותו כלל, והשורה שומרת את הסוד הקודם (COALESCE).
        monitor_secret = body.get("monitor_secret")
        if not well_formed_monitor_secret(monitor_secret):
            monitor_secret = None
        # ‏#1077: נרשם רק כשיש סוד תקין באותו hello — אחרת COALESCE היה
        # משאיר hmac על סוכן שחזר אחורה.
        monitor_auth = body.get("monitor_auth")
        if monitor_secret is None or not well_formed_monitor_auth(monitor_auth):
            monitor_auth = None
        # ‏#720 (schema 2): המלאי החומרתי — DMI, PCI, TPM — למיפוי דרייברים.
        # פגום נזנח כמו שדה לא ידוע; סוכן ישן אינו שולח אותו, והגרסה
        # השמורה (אם יש) נשארת.
        hw_inventory = inventory.well_formed(body.get("inventory"))
        # ‏#1049 שלב ב' + #1048: בדיקת המכונה (probe) ו-netprobe (כבל/LLDP).
        # netprobe מגיע ב-hello כשדה אח (מקביל ל-probe) ונשמר בתוך אותה
        # שורת machine_probe. חסר/פגום → None, הגרסה הקודמת נשארת.
        raw_probe = body.get("probe")
        if isinstance(raw_probe, dict) and "netprobe" in body:
            raw_probe = {**raw_probe, "netprobe": body.get("netprobe")}
        elif raw_probe is None and "netprobe" in body:
            raw_probe = {"netprobe": body.get("netprobe")}
        hw_probe = probe.well_formed(raw_probe)
        # ‏#1080: מפתח ה-host של dropbear. חסר/פגום → None, הגרסה הקודמת
        # נשארת; known_hosts מתעדכן רק על ערך תקין + IP מספרי.
        hw_ssh = ssh_hostkey.well_formed(body.get("ssh_hostkey"))
        # ‏#906: המכונה ממתינה לאדם (שאלת SMART/אדום, מסך FAILED) ואומרת
        # על מה. רק `waiting_for: "operator"` עם `prompt` מחרוזת לא-ריקה
        # נשמר (קצוץ ל-PROMPT_MAX_CHARS); כל צורה אחרת — וגם היעדר השדה,
        # שהוא "כבר לא ממתינה" — מנקה. ‏net_seen כותב אותו תמיד, בלי COALESCE.
        prompt = body.get("prompt")
        if (body.get("waiting_for") != "operator" or not isinstance(prompt, str)
                or not prompt.strip()):
            prompt = None
        else:
            prompt = prompt.strip()[:PROMPT_MAX_CHARS]
        # ‏#402: מה הסוכן מצא כשספר אפס דיסקים (ממשק 2). רק ארבעת הערכים
        # המוכרים נשמרים; כל צורה אחרת — וגם היעדר (סוכן ישן) — היא None,
        # והשורה שומרת את הערך הקודם (COALESCE), כמו disks_json.
        disk_probe = body.get("disk_probe")
        if disk_probe not in DISK_PROBE_VALUES:
            disk_probe = None
        # ‏#500: הקושחה (uefi/bios) ומצב ה-Secure Boot כפי שהסוכן קרא מה-EFI
        # var. רק הצורה המדויקת נשמרת; כל דבר אחר = None והשורה שומרת את
        # הערך הקודם (COALESCE) — "לא דווח" אינו "כבוי".
        firmware = body.get("firmware")
        if firmware not in ("uefi", "bios"):
            firmware = None
        secure_boot = body.get("secure_boot")
        secure_boot = int(secure_boot) if isinstance(secure_boot, bool) else None
        answer = build_answer(
            ctx.conn, ctx.library, ctx.store, mac,
            disks=disks, client_ip=client_ip, joining=joining,
            reported_ip=reported_ip, off_vlan=off_vlan,
            all_macs=all_macs, monitor_secret=monitor_secret,
            monitor_auth=monitor_auth,
            hw_inventory=hw_inventory, hw_probe=hw_probe, hw_ssh=hw_ssh,
            prompt=prompt, disk_probe=disk_probe,
            firmware=firmware, secure_boot=secure_boot,
            # ‏#715: פרמטרי השידור למקור שהוא מחשב בנייה — של המנוע, אם יש.
            multicast=ctx.sender.multicast_params() if ctx.sender is not None else None,
        )
        if hw_ssh is not None and data_dir is not None:
            ssh_hostkey.upsert_known_host(
                data_dir, reported_ip or "", hw_ssh)
        log.info("hello from %s (%s): known=%s off_vlan=%s",
                 mac, client_ip, answer["known"], off_vlan)
        # ‏hello מוכיח שהסוכן רץ. אם השרת שלח את המכונה הזאת לדיסק
        # המקומי ובכל זאת הסוכן ענה — השרשור לדיסק נכשל, וזה מה שמסך
        # הבריאות מראה (#112). ניטור בלבד: לא נוגע בתשובה שנשלחת.
        agent_loops.note(ctx.conn, ctx.store, mac, answer, off_vlan=off_vlan)
        # ובשורה נפרדת משלו: המכונה מדברת איתנו מרשת שאינה וילן ההפצה
        # (#137). לא לולאה — ולכן לא נספר שם — אבל כן אירוע שהמפעיל
        # צריך לראות, בין אם הוא עומד ליד המחשב בכוונה ובין אם המחשב
        # חובר לשקע הלא נכון. התראה, לא שער: התשובה למעלה כבר נבנתה.
        foreign_vlan.note(ctx.conn, mac, request.scope, off_vlan=off_vlan)
        return JSONResponse(answer)

    @router.post("/agent/login")
    async def agent_login(request: Request) -> JSONResponse:
        """כניסה ממסך השחזור בתחנה (סעיף 13.2: סיסמה → תחנה בודדת).

        אותם משתמשים של הקונסולה — אין סיסמה מקומית שיכולה לדלוף ממכונה
        שתלמידים שולטים בה (סעיף 15). ההצלחה אינה מחזירה טוקן: הסוכן
        ממשיך באותה שיחה, וכל פעולה ממילא עוברת דרך השרת.
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        username = body.get("username", "")
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        # ‏#997: הזהות לפני הסיסמה — זר אינו מנסה סיסמאות בשם מכונה, ואינו
        # מייצר `agent_login_failed` על מכונה שאינה שלו.
        refused = identity_gate(ctx, mac, request, "login")
        if refused is not None:
            return refused
        ip = request.client.host if request.client else "?"
        try:
            login_guard.check(ctx.conn, username, ip)
        except login_guard.LoginBlocked as exc:
            return _error(exc.status, exc.message,
                          "login_locked" if exc.status == 403 else "login_delayed")
        role = users.verify(ctx.conn, username, body.get("password", ""))
        if role is None:
            login_guard.record_failure(ctx.conn, username, ip)
            journal(ctx.conn, "agent_login_failed", f"{username} at {mac}")
            return _error(401, "wrong username or password", "bad_login")
        login_guard.clear(ctx.conn, username, ip)
        info = users.flags(ctx.conn, username)
        if info["must_change_password"]:
            return _error(403, "החלף סיסמה בקונסולה קודם", "password_change_required")
        journal(ctx.conn, "agent_login", f"{username} at {mac}")
        return JSONResponse({"ok": True, "role": role})

    @router.post("/agent/pulls")
    async def open_pull(request: Request) -> JSONResponse:
        """פתיחת משיכת יוניקאסט — התחנה מודיעה לשרת שהיא מתחילה למשוך.

        לא "בקשת רשות למשוך": קבצי האימג' מוגשים ממילא (‏`/api/v1/images`),
        וההרשמה כאן היא כדי שהעבודה תיראה — במבט-העל, ביומן ובדיווחי
        ההתקדמות. מה שכן נאכף כאן הוא בדיוק מה ש-hello מכריז עליו:
        כניסה, לפי ההגדרה ולפי הווילן (#42) — הצהרה שהסוכן מציית לה
        אינה אכיפה.

        הזרם עצמו אינו תופס את חריץ השידור: כמה משיכות במקביל, וגם
        בזמן סבב כיתה (#60).
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")

        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        refused = identity_gate(ctx, mac, request, "pulls")      # #997
        if refused is not None:
            return refused
        machine = registry.lookup(ctx.conn, mac)
        if machine is None:
            # עיקרון 1: מכונה שאיננה מכירים לא מקבלת עבודה, גם לא משלה.
            pulls.journal_refusal(ctx.conn, mac, "MAC לא רשום")
            return _error(403, "this mac is not registered", "unknown_mac")
        image_id = body.get("image_id", "")
        manifest = ctx.library.get(image_id)
        if manifest is None:
            pulls.journal_refusal(ctx.conn, mac, f"אימג' {image_id} לא קיים")
            return _error(404, "unknown image", "no_image")
        # ‏#381: אימג' שנקלט ממחשב כיתה שייך למכונה שנקלט ממנה. הסירוב
        # **גלוי** — הודעה שאומרת של מי הוא ומה ביקשנו — ולא נפילה שקטה
        # לדיסק מקומי (עיקרון 5).
        refusal = restore_refusal(manifest, [mac])
        if refusal is not None:
            pulls.journal_refusal(ctx.conn, mac, refusal)
            return _error(403, refusal, "image_bound_to_another_machine")

        session = ctx.store.active_for_group(machine["group_id"])
        has_open = (session is not None and session["state"] == "open"
                    and ctx.store.in_roster(session, mac))
        username = str(body.get("username") or "").strip()
        if login_required(ctx.conn, has_open,
                          off_deploy_vlan(request.scope, server_base)):
            if users.verify(ctx.conn, username, body.get("password", "")) is None:
                journal(ctx.conn, "agent_login_failed", f"{username} at {mac} pull")
                return _error(401, "wrong username or password", "bad_login")

        try:
            session_id = pulls.open_pull(
                ctx.conn, ctx.store, mac, machine["group_id"], image_id, username,
            )
        except SessionError as exc:
            pulls.journal_refusal(ctx.conn, mac, str(exc))
            return _error(409, str(exc), "pull_conflict")
        log.info("unicast pull %s for %s (%s)", session_id, mac, image_id)
        return JSONResponse({"id": session_id, "kind": "unicast",
                             "image_id": image_id})

    @router.post("/agent/progress")
    async def agent_progress(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        # ‏#855: הזהות נבדקת לפני הדיווח, בסבב ובמשימה כאחד — ורק כשיש MAC
        # לבדוק; ‏MAC שאינו נקרא נשאר `bad_mac` של `reports.ingest`, כי
        # "לא הצלחנו לקרוא את המזהה" קודם ל"המזהה אינו תואם" (עיקרון 5).
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "progress")
            if refused is not None:
                return refused
        result = reports.ingest(ctx.conn, body,
                                token=request.headers.get(TOKEN_HEADER, ""))
        if result.get("code") == "bad_token":     # #855: כמו ההעלאה — 403
            return JSONResponse(result, status_code=403)
        return JSONResponse(result, status_code=200 if (result.get("ok") or result.get("code") == "not_open") else 400)

    @router.post("/agent/disk-event")
    async def agent_disk_event(request: Request) -> JSONResponse:
        """בריאות SMART וההכרעה על דיסק יעד (#652), לצפייה ולריבוט-החלפה.

        best-effort מצד הסוכן: כשל כאן אינו מפיל שחזור. השרת שומר את
        התמונה החיה ואינו מכריע ממנה — ההכרעה נעשתה בסוכן, ליד המכונה.
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        # ‏#997: כמו progress — הזהות נבדקת כשיש MAC לבדוק; MAC שאינו נקרא
        # נשאר `bad_mac`/`bad_event` של `disk_events.ingest`.
        mac = lenient_mac(body.get("mac"))
        if mac is not None:
            refused = identity_gate(ctx, mac, request, "disk-event")
            if refused is not None:
                return refused
        result = disk_events.ingest(ctx.conn, body)
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.post("/agent/shrink-open")
    async def agent_shrink_open(request: Request) -> JSONResponse:
        """‏#926: הפריסה המקורית של מחיצת המקור, **לפני** `ntfsresize -s`.

        ההפך מ-disk-event: **לא** best-effort. הסוכן מכווץ רק אחרי 2xx
        עם `ok` — בלי רשומה בשרת אין כתיבה למקור (עיקרון 5).
        """
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            body = {}
        mac = lenient_mac(body.get("mac"))
        if mac is not None:                       # #997; None → bad_mac של open_record
            refused = identity_gate(ctx, mac, request, "shrink-open")
            if refused is not None:
                return refused
        try:
            row = shrink_records.open_record(ctx.conn, body)
        except shrink_records.BadRecord as exc:
            return _error(400, str(exc), exc.code)
        except shrink_records.AlreadyOpen as exc:
            return JSONResponse({"ok": False, "error": "this disk already has an open shrink record",
                                 "code": "already_open", "id": exc.record_id,
                                 "opened_at": exc.opened_at}, status_code=409)
        return JSONResponse({"ok": True, "id": row["id"]})

    def _shrink_identity(body: dict, request: Request, where: str) -> JSONResponse | None:
        # ‏#997: הסידורי הוא הזהות של הרשומה, אבל ה-MAC — שהסוכן שולח תמיד
        # (‏`shrinkmem.sh`) — הוא הזהות של **הפונה**. בלעדיו אין מה להשוות לחכירה.
        mac = lenient_mac(body.get("mac"))
        if mac is None:
            return _error(400, "missing or malformed mac", "bad_mac")
        return identity_gate(ctx, mac, request, where)

    @router.post("/agent/shrink-close")
    async def agent_shrink_close(request: Request) -> JSONResponse:
        """‏#926: המקור הוחזר לגודלו. סגירה של מה שאינו פתוח היא 404."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        refused = _shrink_identity(body, request, "shrink-close")
        if refused is not None:
            return refused
        record_id = body.get("id") if isinstance(body.get("id"), int) else None
        if not shrink_records.close_record(ctx.conn, body.get("serial"), record_id):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.post("/agent/shrink-note")
    async def agent_shrink_note(request: Request) -> JSONResponse:
        """‏#926 (סקירת Fable): למה הרשומה עדיין פתוחה — הטבלה הוחזרה אך
        מערכת הקבצים לא נמתחה. best-effort מצד הסוכן; 404 על מה שאינו פתוח."""
        try:
            body = await request.json()
        except ValueError:
            return _error(400, "body is not JSON", "bad_json")
        if not isinstance(body, dict):
            return _error(400, "body is not an object", "bad_json")
        refused = _shrink_identity(body, request, "shrink-note")
        if refused is not None:
            return refused
        note = body.get("note")
        if not isinstance(note, str) or not note.strip():
            return _error(400, "note must be a non-empty string", "bad_note")
        record_id = body.get("id") if isinstance(body.get("id"), int) else None
        if not shrink_records.note_record(ctx.conn, body.get("serial"), record_id, note):
            return _error(404, "no open shrink record for this serial", "not_open")
        return JSONResponse({"ok": True})

    @router.get("/agent/branding/logo")
    def branding_logo():
        """‏#1168: הלוגו שהועלה בקונסולה, על פורט הסוכן — הגואי הקטן במחשבי
        הבנייה/השיכפול מושך אותו בעלייה. ‏204 = אין לוגו, הגואי מצייר את
        ה-brandmark הקבוע. קריאה בלבד, כמו המניפסטים."""
        from fastapi.responses import Response  # noqa: PLC0415
        from .branding import SERVE_HEADERS, TYPES, find_logo  # noqa: PLC0415
        path = find_logo(data_dir) if data_dir is not None else None
        if path is None:
            return Response(status_code=204)
        media = next(t for t, s in TYPES.items() if s == path.suffix)
        return FileResponse(path, media_type=media, headers=SERVE_HEADERS)

    @router.get("/images/{image_id}/manifest")
    def image_manifest(image_id: str, request: Request):
        refused = source_gate(ctx, request, "manifest")      # #997
        if refused is not None:
            return refused
        manifest = ctx.library.get(image_id)
        if manifest is None:
            # ‏#715: מזהה חי (`live_…`) — המניפסט שמחשב הבנייה דיווח. אותו
            # נתיב בדיוק, כדי שהמקבלים לא ידעו מי משדר. לפני שהגיע: 404.
            manifest = direct.live_manifest(ctx.conn, image_id)
        if manifest is None:
            return _error(404, "unknown image", "no_image")
        public = {k: v for k, v in manifest.items() if not k.startswith("_")}
        return JSONResponse(public)

    @router.get("/images/{image_id}/files/{filename}")
    def image_file(image_id: str, filename: str, request: Request):
        refused = source_gate(ctx, request, "files")         # #997
        if refused is not None:
            return refused
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        path = ctx.library.file_path(image_id, filename)
        if path is None:
            return _error(404, "file not in this image's manifest", "no_file")
        return FileResponse(path, media_type="application/octet-stream")

    return router
