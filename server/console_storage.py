"""API הקונסולה לניהול מרשם המשניים (#655, tracer #2 / #727).

ניהול **קבוצות** וניהול **רשומות משניים קיימות** — שינוי שם, שיוך
לקבוצה, השבתה/הפעלה ומחיקה. אין כאן הוספת משני: הוספה היא enrollment
(כתובת + אישורי מנהל + טביעת TLS) והיא שלב נפרד בשרשרת (#3), כמו
הוספת מארח ל-vCenter.

RBAC כפול (עיקרון 5 + ההיררכיה החד-כיוונית של #655):

- ``admin_only`` — deploy מקבל 403, בדיוק כמו בשאר מסכי הניהול.
- ``require_standalone`` — משני מקבל 409: הוא אינו מנהל ילדים, וההיררכיה
  זורמת מלמעלה למטה בלבד. האכיפה חוזרת גם בשכבת ה-service, כדי שקריאה
  ישירה לפונקציה על משני תיכשל בדיוק כמו דרך ה-API.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from starlette.websockets import WebSocketDisconnect

from . import (auth, interserver_auth, monitor, registry, storage_client,
               storage_nodes, storage_transfer)
from .api import ServerContext


def create_storage_router(ctx: ServerContext, data_dir=None) -> APIRouter:
    router = APIRouter(prefix="/api/console")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    def require_standalone(user=Depends(admin_only)) -> tuple[str, str]:
        """מנהל **וגם** שרת ראשי. deploy נופל ב-403 מ-``admin_only``
        עוד לפני שמגיעים לכאן; משני נופל כאן ב-409."""
        if storage_nodes.role(ctx.conn) != storage_nodes.ROLE_STANDALONE:
            raise HTTPException(
                409, "ניהול שרתים משניים אפשרי רק בשרת ראשי (standalone)")
        return user

    # --- קבוצות אחסון -------------------------------------------------------

    @router.get("/storage-node-groups")
    def list_groups(user=Depends(require_standalone)):
        return storage_nodes.list_groups(ctx.conn, user)

    @router.post("/storage-node-groups")
    async def create_group(request: Request, user=Depends(require_standalone)):
        label = (await request.json()).get("label", "")
        try:
            gid = storage_nodes.create_group(ctx.conn, label, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"id": gid}

    @router.post("/storage-node-groups/order")
    async def reorder_groups(request: Request, user=Depends(require_standalone)):
        ids = (await request.json()).get("ids")
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise HTTPException(400, "צריך רשימת מזהים")
        try:
            storage_nodes.reorder_groups(ctx.conn, ids, user)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True}

    @router.put("/storage-node-groups/{gid}")
    async def rename_group(gid: str, request: Request,
                           user=Depends(require_standalone)):
        label = (await request.json()).get("label", "")
        try:
            storage_nodes.rename_group(ctx.conn, gid, label, user)
        except ValueError as exc:
            raise HTTPException(404 if "לא קיימת" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.delete("/storage-node-groups/{gid}")
    def delete_group(gid: str, user=Depends(require_standalone)):
        try:
            storage_nodes.delete_group(ctx.conn, gid, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    # --- משניים רשומים ------------------------------------------------------

    @router.get("/storage-nodes")
    def list_nodes(user=Depends(require_standalone)):
        return storage_nodes.list_nodes(ctx.conn, user)

    @router.put("/storage-nodes/{nid}")
    async def edit_node(nid: str, request: Request,
                        user=Depends(require_standalone)):
        """שינוי שם ו/או שיוך לקבוצה, ב**טרנזאקציה אחת** (#732).
        ``group_id`` הנוכח בגוף — כולל ``null`` — משמעו שיוך מפורש
        (``null`` = ניתוק). שדה שאינו בגוף אינו נוגע בערך הקיים."""
        body = await request.json()
        kwargs = {}
        if "label" in body:
            kwargs["label"] = body["label"]
        if "group_id" in body:
            kwargs["group_id"] = body["group_id"]
        try:
            storage_nodes.edit_node(ctx.conn, nid, user, **kwargs)
        except ValueError as exc:
            raise HTTPException(404 if "לא קיים" in str(exc) else 400, str(exc))
        return {"ok": True}

    @router.post("/storage-nodes/{nid}/disabled")
    async def set_disabled(nid: str, request: Request,
                           user=Depends(require_standalone)):
        # ‏#732: פרסור בוליאני קפדני. ``bool("false")`` הוא ``True``,
        # ולכן ``{"disabled": "false"}`` היה **משבית** משני. מקבלים רק
        # בוליאני אמיתי; מחרוזת/מספר/אובייקט נדחים ב-400.
        disabled = (await request.json()).get("disabled")
        if not isinstance(disabled, bool):
            raise HTTPException(400, "השדה disabled חייב להיות בוליאני (true/false)")
        try:
            storage_nodes.set_node_disabled(ctx.conn, nid, disabled, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    @router.delete("/storage-nodes/{nid}")
    def delete_node(nid: str, user=Depends(require_standalone)):
        try:
            storage_nodes.delete_node(ctx.conn, nid, user)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return {"ok": True}

    # --- העברת אימג' ראשי→משני (#655 v1) ------------------------------------
    #
    # ‏admin+standalone. ההעברה היא משימת רקע; כאן רק פתיחה ותצוגה. הכניסה
    # לספריית המשני עוברת אימות sha256 **שם** (import_tar) — הראשי מדווח
    # ``done`` רק אחרי 200 שהמשני החזיר אחרי האימות.

    @router.post("/storage-nodes/{nid}/transfer")
    async def start_transfer(nid: str, request: Request,
                             user=Depends(require_standalone)):
        image_id = str((await request.json()).get("image_id") or "")
        try:
            tid = storage_transfer.start_transfer(ctx, data_dir, nid, image_id, user)
        except storage_transfer.TransferError as exc:
            raise HTTPException(exc.status, exc.detail)
        return {"id": tid}

    @router.get("/storage-nodes/{nid}/transfers")
    def node_transfers(nid: str, user=Depends(require_standalone)):
        if ctx.conn.execute("SELECT 1 FROM storage_nodes WHERE id = ?",
                            (nid,)).fetchone() is None:
            raise HTTPException(404, "שרת משני לא קיים")
        return storage_transfer.list_transfers(ctx.conn, user, node_id=nid)

    @router.get("/storage-transfers")
    def all_transfers(user=Depends(require_standalone)):
        return storage_transfer.list_transfers(ctx.conn, user)

    # --- צפייה במשני: המכונות שלו (#655 v1) ----------------------------------
    #
    # הראשי שואל את המשני בערוץ המאומת ומחזיר את התשובה כפי שהיא. כשל
    # חיבור אינו 5xx אלא ``connected:false`` עם הסיבה — כרטיס הסניף מציג
    # "לא מחובר" במקום להיעלם (עיקרון 5: לא-הצלחנו-לשאול ≠ אין מכונות).

    @router.get("/storage-nodes/{nid}/machines")
    def node_machines(nid: str, user=Depends(require_standalone)):
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            raise HTTPException(404, "שרת משני לא קיים")
        if node["disabled_at"]:
            return {"connected": False, "error": "השרת המשני מושבת", "machines": []}
        try:
            client, token = storage_nodes.node_client(ctx.conn, data_dir, node)
            with client:
                answer = client.get_json("/machines", token)
        except Exception as exc:                             # noqa: BLE001
            return {"connected": False,
                    "error": interserver_auth.redact_secrets(str(exc)),
                    "machines": []}
        machines = answer.get("machines")
        if not isinstance(machines, list):
            return {"connected": False, "error": "תשובה לא צפויה מהמשני",
                    "machines": []}
        return {"connected": True, "error": None, "machines": machines,
                "node_id": answer.get("node_id")}

    # --- מוניטור למכונה של המשני, דרך המשני (#655 v1) ------------------------
    #
    # פרוקסי-של-פרוקסי: הדפדפן ↔ הראשי (WebSocket) ↔ המשני (מנהרה בערוץ
    # המאומת) ↔ המכונה (RFB). הראשי לעולם אינו רואה סוד של מכונה — המשני
    # מזדהה מולה בסוד שלו (#846) ומעביר לראשי זרם שכבר עבר SecurityResult.
    # קודי הסגירה כמו במוניטור המקומי (סעיף 14 ב-interfaces.md); כשל שהגיע
    # מהמשני חוזר כ-4000+הקוד שלו עם ה-detail שלו כסיבה.

    @router.websocket("/storage-nodes/{nid}/monitor/{mac}")
    async def remote_monitor(websocket: WebSocket, nid: str, mac: str):
        import asyncio
        found = auth.check(ctx.conn, websocket.cookies.get(auth.COOKIE_NAME))
        if found is None:
            await websocket.close(code=monitor.WS_UNAUTHENTICATED, reason="נדרשת התחברות")
            return
        if found[1] != "admin":
            await websocket.close(code=monitor.WS_FORBIDDEN, reason="פעולה למנהל בלבד")
            return
        if storage_nodes.role(ctx.conn) != storage_nodes.ROLE_STANDALONE:
            await websocket.close(code=4409, reason="צפייה במשני אפשרית רק משרת ראשי")
            return
        node = storage_nodes.node_row(ctx.conn, nid)
        if node is None:
            await websocket.close(code=4404, reason="שרת משני לא קיים")
            return
        if node["disabled_at"]:
            await websocket.close(code=4409, reason="השרת המשני מושבת")
            return
        canonical = registry.normalize_mac(mac)
        if canonical is None:
            await websocket.close(code=4404, reason="מכונה לא מוכרת")
            return
        try:
            token = interserver_auth.load_credential(node["credential_ref"])
            ident = storage_nodes.identity(ctx.conn, data_dir=data_dir)
            host, port, _ = interserver_auth.parse_interserver_url(node["base_url"])
        except Exception as exc:                             # noqa: BLE001
            await websocket.close(code=4500, reason=interserver_auth.redact_secrets(str(exc))[:120])
            return
        writer = None
        try:
            try:
                reader, writer = await asyncio.wait_for(storage_client.open_tunnel(
                    host, port, expected_secondary_spki=node["pinned_spki"],
                    cert_path=ident["server_cert_ref"], key_path=ident["server_key_ref"],
                    path=f"/monitor/{canonical}", token=token), timeout=15.0)
            except storage_client.InterserverTunnelRefused as exc:
                await websocket.close(code=4000 + exc.status, reason=exc.detail[:120])
                return
            except (OSError, asyncio.TimeoutError, storage_client.InterserverClientError) as exc:
                await websocket.close(
                    code=4502,
                    reason=f"השרת המשני אינו זמין: {interserver_auth.redact_secrets(str(exc))}"[:120])
                return
            await monitor.bridge_browser(websocket, reader, writer)
        except (WebSocketDisconnect, asyncio.CancelledError):
            pass
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:                            # noqa: BLE001
                    pass

    # --- הצד המקומי של המשני: חלון pairing ו-break-glass (#740) --------------
    #
    # פעולות שמנהל מפעיל **מקומית** על המשני. שלושה שומרים, וכולם
    # נאכפים: ``admin_only`` (deploy→403), locality (peer שאינו loopback
    # →403 — נאכף על כתובת ה-peer של ASGI, לא על ``X-Forwarded-For``),
    # ותפקיד secondary (ראשי/עצמאי→409, בשכבת ה-service). היות מנהל
    # מאומת אינו מספיק לבדו — כך דורש הדגל הנעול.

    def require_local(request: Request, user=Depends(admin_only)) -> tuple[str, str]:
        client = request.client
        if not interserver_auth.is_loopback(client.host if client else None):
            raise HTTPException(403, "פעולת pairing מקומית מותרת מ-loopback בלבד")
        return user

    def _pairing_http(exc: Exception) -> HTTPException:
        if isinstance(exc, storage_nodes.NodeManagementUnauthorized):
            return HTTPException(403, str(exc))
        return HTTPException(409, str(exc))   # NotSecondary / ParentAlreadyEnrolled

    @router.post("/storage-pairing-window")
    def open_pairing_window(user=Depends(require_local)):
        """פותח חלון pairing מקומי ומחזיר את הקוד **פעם אחת בלבד**."""
        try:
            return storage_nodes.open_local_pairing(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.ParentAlreadyEnrolledError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)

    @router.get("/storage-pairing-window")
    def pairing_window_status(user=Depends(require_local)):
        """מצב החלון — **בלי הקוד**. refresh/status לעולם אינם מחזירים אותו."""
        try:
            return storage_nodes.local_pairing_status(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)

    @router.delete("/storage-parent")
    def unbind_parent(user=Depends(require_local)):
        """פינוי מקומי (break-glass): מנתק את האב הרשום וסוגר חלון פתוח."""
        try:
            storage_nodes.unbind_parent(ctx.conn, user)
        except (storage_nodes.NotSecondaryError,
                storage_nodes.NodeManagementUnauthorized) as exc:
            raise _pairing_http(exc)
        return {"ok": True}

    # --- הצד של הראשי: הוספת משני דרך enrollment מוקשח (#740) ----------------
    #
    # ‏admin+standalone (``require_standalone``). המנהל מזין כתובת מפורשת,
    # קוד חד-פעמי, ואת ה-SPKI של המשני כפי שהוא מוצג שם. ``preview`` קורא
    # את ה-SPKI של השרת כדי שהמנהל יַשווה **לפני** שהוא שולח את הקוד; אין
    # כאן שם משתמש/סיסמה של המשני (הדגם הנעול הסיר את זה).

    def _identity():
        if data_dir is None:
            raise HTTPException(500, "data_dir לא הוגדר — אין זהות TLS בין-שרתית")
        return storage_nodes.ensure_identity(ctx.conn, data_dir)

    @router.post("/storage-nodes/enroll/preview")
    async def enroll_preview(request: Request, user=Depends(require_standalone)):
        """קורא את ה-SPKI של המשני שבכתובת, להשוואה ידנית מול תצוגת המשני."""
        url = (await request.json()).get("url", "")
        try:
            host, port, _ = interserver_auth.parse_interserver_url(url)
        except interserver_auth.InterserverURLError as exc:
            raise HTTPException(400, str(exc))
        try:
            spki = storage_client.fetch_server_spki(host, port)
        except Exception as exc:                             # noqa: BLE001
            raise HTTPException(502, f"לא ניתן לקרוא את תעודת המשני: {exc}")
        return {"secondary_spki": spki, "node_id": interserver_auth.node_id_from_spki(spki)}

    @router.post("/storage-nodes/enroll")
    async def enroll(request: Request, user=Depends(require_standalone)):
        """מבצע pairing מוקשח מול המשני, כותב את הטוקן לקובץ 0600, ורק אז
        רושם את המשני. הטוקן לעולם אינו חוזר בתשובה ואינו נכתב ל-DB/יומן."""
        body = await request.json()
        url = body.get("url", "")
        version = str(body.get("protocol_version", ""))
        if version != interserver_auth.PROTOCOL_VERSION:
            raise HTTPException(426, f"גרסת פרוטוקול חייבת להיות {interserver_auth.PROTOCOL_VERSION}")
        expected_spki = str(body.get("expected_secondary_spki") or "")
        if not expected_spki:
            raise HTTPException(400, "חסר expected_secondary_spki (הצמד את ה-SPKI של המשני)")
        try:
            interserver_auth.parse_interserver_url(url)
        except interserver_auth.InterserverURLError as exc:
            raise HTTPException(400, str(exc))

        ident = _identity()
        try:
            result = storage_client.pair_secondary(
                url, code=str(body.get("code") or ""),
                expected_secondary_spki=expected_spki,
                cert_pem=ident["cert_pem"], key_pem=ident["key_pem"],
                primary_id=ident["node_id"])
        except storage_client.InterserverIdentityMismatch as exc:
            # ‏#883: ההצהרה סותרת את המפתח — כשל בקול, לפני שהקוד נשלח.
            raise HTTPException(400, interserver_auth.redact_secrets(str(exc)))
        except storage_client.InterserverClientError as exc:
            # הודעת ה-detail עלולה לשאת חומר — מסתירים לפני שהיא עוזבת.
            raise HTTPException(502, interserver_auth.redact_secrets(str(exc)))

        # ‏#883: המזהה — וממנו שם קובץ הטוקן — נגזר מה-SPKI שהמפעיל הצמיד
        # וה-handshake אימת, לא מהצהרת ה-JSON של המשני. חגורה שנייה על
        # שם הקובץ: רק ``sn_<hex>`` נכנס ל-``secondaries/``.
        secondary_id = interserver_auth.node_id_from_spki(expected_spki)
        if not storage_nodes.valid_node_id(secondary_id):
            raise HTTPException(400, f"מזהה משני לא תקין: {secondary_id!r}")
        cred_dir = Path(data_dir) / "secondaries"
        cred_dir.mkdir(parents=True, exist_ok=True)
        cred_path = cred_dir / f"{secondary_id}.token"
        # קודם הקובץ העמיד (0600), ורק אחרי שהצליח — רשומת ה-DB.
        interserver_auth.write_credential_0600(cred_path, result["token"])
        try:
            node_row = storage_nodes.enroll_node(
                ctx.conn, user, label=body.get("label", ""), base_url=url,
                node_id=secondary_id, pinned_spki=expected_spki,
                client_cert_ref=interserver_auth.certificate_ref(ident["cert_pem"]),
                credential_ref=str(cred_path),
                protocol_version=interserver_auth.PROTOCOL_VERSION,
                group_id=body.get("group_id"))
        except (ValueError, sqlite3.IntegrityError) as exc:
            # הרשומה נכשלה אבל המשני כבר קשור — לא re-enroll שקט: מנקים את
            # הקובץ היתום ומדווחים שהשחזור הוא ניתוק מקומי על המשני.
            cred_path.unlink(missing_ok=True)
            raise HTTPException(
                409, f"רישום המשני נכשל ({exc}). המשני כבר קשור — נתקו אותו "
                     f"מקומית (break-glass) לפני רישום מחדש.")
        return {"ok": True, "id": node_row, "node_id": secondary_id,
                "secondary_spki": expected_spki}

    return router
