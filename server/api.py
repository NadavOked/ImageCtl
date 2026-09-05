"""נקודות הקצה שהסוכן צורך — ממשקים 2, 3, 4 ו-7.

הכללים כאן זהים לצד השני של הרשת: לא זורקים חריגות החוצה, כל כשל
מוחזר כתשובה שסופה דיסק מקומי או שגיאה מסודרת בתבנית המוסכמת.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from sqlite3 import Connection

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from boot.grub_menu import normalize_mac as lenient_mac

from . import agent_loops, foreign_vlan, pulls, registry, reports, users
from .db import journal
from .hello import build_answer, login_required, off_deploy_vlan
from .images import ImageLibrary, restore_refusal
from .sessions import SessionError, SessionStore

log = logging.getLogger("imagectl.api")


@dataclass
class ServerContext:
    conn: Connection
    library: ImageLibrary
    store: SessionStore
    sender: object | None = None      # SenderEngine; None בבדיקות יחידה


def _error(status: int, message: str, code: str) -> JSONResponse:
    """תבנית השגיאות מהמוסכמות הרוחביות."""
    return JSONResponse(
        {"ok": False, "error": message, "code": code}, status_code=status
    )


def create_agent_router(ctx: ServerContext,
                        server_base: str | None = None) -> APIRouter:
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
        answer = build_answer(
            ctx.conn, ctx.library, ctx.store, mac,
            disks=disks, client_ip=client_ip, joining=joining,
            reported_ip=reported_ip, off_vlan=off_vlan,
        )
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
        mac = lenient_mac(body.get("mac")) or "?"
        role = users.verify(ctx.conn, username, body.get("password", ""))
        if role is None:
            journal(ctx.conn, "agent_login_failed", f"{username} at {mac}")
            return _error(401, "wrong username or password", "bad_login")
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
        result = reports.ingest(ctx.conn, body if isinstance(body, dict) else {})
        return JSONResponse(result, status_code=200 if result.get("ok") else 400)

    @router.get("/images/{image_id}/manifest")
    def image_manifest(image_id: str):
        manifest = ctx.library.get(image_id)
        if manifest is None:
            return _error(404, "unknown image", "no_image")
        public = {k: v for k, v in manifest.items() if not k.startswith("_")}
        return JSONResponse(public)

    @router.get("/images/{image_id}/files/{filename}")
    def image_file(image_id: str, filename: str):
        # רשימה לבנה: מוגש רק קובץ שהמניפסט מכריז עליו בשמו המדויק.
        path = ctx.library.file_path(image_id, filename)
        if path is None:
            return _error(404, "file not in this image's manifest", "no_file")
        return FileResponse(path, media_type="application/octet-stream")

    return router
