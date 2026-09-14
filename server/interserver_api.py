"""Storage Nodes — כניסת ה-enrollment הבין-שרתי על המשני (#740, tracer 2.1).

זה הצד ש**מקבל** את הראשי: ‏pair-begin / pair-complete / ping מעל mTLS 1.3
עם channel-binding (RFC 9266) ו-SPKI-pin. שכבת ה-TLS עצמה מסתיימת ב-
terminator של pyOpenSSL (ראו ``interserver_auth`` — stdlib ssl אינו יכול
לקלוט תעודת-לקוח לא-מוכרת ואין לו exporter); כאן יושבת הלוגיקה שמעליו.

**האמון אינו מ-uvicorn ואינו מכותרות HTTP.** זהות ה-peer — גרסת ה-TLS,
תעודת-הלקוח וה-exporter — מגיעה מ-``tls_peer_provider`` שה-terminator
מזין, ולעולם לא מ-header שניתן לזייף. כל שומר נאכף **גם** בשכבת ה-service
(עיקרון 5): קריאה ישירה לפונקציה עם peer שגוי נכשלת בדיוק כמו דרך ה-route.

⚠️ **בלי ``from __future__ import annotations``** — האנוטציה ``Request``
ב-dependencies חייבת להיות אובייקט אמיתי ל-FastAPI, אחרת ה-endpoint מחזיר
422 (אותו באג של ``auth.py``/``boot/http.py``, ‏CLAUDE.md).
"""

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import interserver_auth, storage_nodes
from .db import now_iso

#: תוקף ה-handle של pair-begin עד pair-complete — קצר, אותה session.
HANDLE_TTL_SECONDS = 60


@dataclass
class TlsPeer:
    """זהות ה-peer כפי שה-terminator של ה-TLS ראה אותה — לא HTTP.

    ``tls_version`` כמו ``"TLSv1.3"``; ``cert_der`` תעודת-הלקוח הגולמית
    (או ``None`` אם לא הוצגה); ``exporter`` ערך ה-tls-exporter של החיבור."""
    tls_version: str | None
    cert_der: bytes | None
    exporter: bytes | None


class PairError(Exception):
    """כשל בכניסה הבין-שרתית, עם קוד HTTP מפורש (route מתרגם אותו כפי שהוא)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --- שומרי ה-peer, נאכפים בשכבת ה-service -----------------------------------

def _require_secure_peer(peer: TlsPeer) -> None:
    """‏TLS 1.3 בדיוק **וגם** תעודת-לקוח נוכחת. ראיה חיובית: גרסה לא-מדווחת
    או ``None`` נדחית, לא "כנראה בסדר" (עיקרון 5, downgrade)."""
    if peer.tls_version != "TLSv1.3":
        raise PairError(421, "נדרש TLS 1.3 בדיוק")
    if not peer.cert_der:
        raise PairError(421, "נדרשת תעודת-לקוח (mTLS)")


def _require_protocol(version: str) -> None:
    if version != interserver_auth.PROTOCOL_VERSION:
        raise PairError(426, f"גרסת פרוטוקול לא נתמכת: {version!r}")


def _require_secondary(conn) -> None:
    if storage_nodes.role(conn) != storage_nodes.ROLE_SECONDARY:
        raise PairError(409, "כניסה בין-שרתית מותרת רק על שרת משני (secondary)")


# --- pair-begin -------------------------------------------------------------

def pair_begin(conn, handles: dict, peer: TlsPeer, body: dict, *,
               data_dir=None, now: datetime | None = None) -> dict:
    """מתחיל pairing: מאמת mTLS 1.3, הצמדת SPKI הדדית, ומחזיר handle כרוך
    ל-session. **לפני** כל קוד — כדי שהצמדה שגויה תיכשל בלי לחשוף אותו."""
    now = now or _utcnow()
    _require_secure_peer(peer)
    _require_protocol(str(body.get("protocol_version")))
    _require_secondary(conn)
    if peer.exporter is None:
        raise PairError(421, "אין channel-binding (tls-exporter) לחיבור")

    ident = storage_nodes.identity(conn, data_dir=data_dir)
    expected = str(body.get("expected_secondary_spki") or "")
    # הצמדה הדדית: הראשי מצהיר איזה SPKI הוא מצפה שלמשני יש. אם אינו
    # תואם את שלנו — הראשי מדבר עם המכונה הלא נכונה. נדחה לפני הקוד.
    if not interserver_auth.verify_pinned_spki_value(ident["server_spki"], expected):
        raise PairError(400, "expected_secondary_spki אינו תואם את זהות המשני")

    primary_spki = interserver_auth.spki_sha256(peer.cert_der)
    primary_ref = interserver_auth.certificate_ref(peer.cert_der)
    handle = secrets.token_hex(16)
    server_nonce = secrets.token_hex(16)
    _sweep_handles(handles, now)
    handles[handle] = {
        "exporter": peer.exporter,
        "primary_ref": primary_ref,
        "primary_spki": primary_spki,
        "primary_id": str(body.get("primary_id") or ""),
        "client_nonce": str(body.get("client_nonce") or ""),
        "server_nonce": server_nonce,
        "protocol": interserver_auth.PROTOCOL_VERSION,
        "expires_at": now + timedelta(seconds=HANDLE_TTL_SECONDS),
    }
    return {
        "handle": handle,
        "secondary_id": ident["node_id"],
        "secondary_spki": ident["server_spki"],
        "server_nonce": server_nonce,
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
    }


def _sweep_handles(handles: dict, now: datetime) -> None:
    for hid in [h for h, v in handles.items() if v["expires_at"] <= now]:
        del handles[hid]


# --- pair-complete ----------------------------------------------------------

def pair_complete(conn, handles: dict, peer: TlsPeer, body: dict, *,
                  now: datetime | None = None) -> dict:
    """משלים pairing על **אותה** session: מאמת את ה-handle, את הכריכה
    לערוץ (exporter) ולתעודת-הלקוח, את הקוד החד-פעמי ואת אב-יחיד; מנפיק
    את הטוקן פעם אחת. טרנזאקציה אחת עם אכיפת אב-יחיד ברמת ה-DB."""
    now = now or _utcnow()
    _require_secure_peer(peer)
    _require_protocol(str(body.get("protocol_version")))
    _require_secondary(conn)

    _sweep_handles(handles, now)
    handle = handles.get(str(body.get("handle") or ""))
    if handle is None:
        raise PairError(400, "handle לא קיים או פג")

    # channel-binding: complete חייב לרוץ על אותה session TLS כמו begin.
    # ‏begin על A, complete על B → exporters שונים → נדחה (RFC 9266).
    if not _exporter_matches(peer.exporter, handle["exporter"]):
        raise PairError(400, "אי-התאמת channel-binding — begin ו-complete על session שונה")
    # אותה תעודת-לקוח בדיוק שפתחה את ה-handle.
    if not _cert_binding_ok(interserver_auth.certificate_ref(peer.cert_der),
                            handle["primary_ref"]):
        raise PairError(400, "תעודת-הלקוח שונה מזו של pair-begin")

    code = str(body.get("code") or "")
    if not interserver_auth.verify_and_consume_code(conn, code, now=now):
        raise PairError(401, "קוד pairing שגוי, פג, או שאין חלון פתוח")

    token = interserver_auth.generate_token()
    try:
        storage_nodes.record_parent(
            conn,
            parent_id=handle["primary_id"] or handle["primary_spki"][:16],
            token_hash=interserver_auth.hash_token(token),
            bound_cert_ref=handle["primary_ref"],
            pinned_parent_spki=handle["primary_spki"],
            protocol_version=interserver_auth.PROTOCOL_VERSION,
        )
    except storage_nodes.ParentAlreadyEnrolledError:
        raise PairError(409, "כבר קיים אב רשום — נתקו אותו תחילה (revoke-first)")
    del handles[str(body["handle"])]
    return {
        "token": token,
        "parent_id": handle["primary_id"] or handle["primary_spki"][:16],
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
    }


def _exporter_matches(a: bytes | None, b: bytes | None) -> bool:
    if a is None or b is None:
        return False
    return secrets.compare_digest(a, b)


def _cert_binding_ok(peer_ref: str, bound_ref: str) -> bool:
    """השוואת הפניית תעודת-הלקוח (RFC 8705, thumbprint מלא)."""
    return secrets.compare_digest(peer_ref, bound_ref)


def _peer_bound(peer_cert_der: bytes, cred) -> bool:
    """‏**נקודת הבקרה השלילית המכריעה של #740** (RFC 8705 sender-constraint).

    שני תנאים: ה-SPKI של הפונה תואם את ה-pin (המפתח הנכון), **וגם**
    ה-thumbprint של התעודה תואם את ``bound_cert_ref`` (אותה תעודה בדיוק).
    אם מנטרלים את הבדיקה הזו, טוקן גנוב עם תעודת-לקוח אחרת עובר — וזו
    ההוכחה שהכריכה היא שמגינה, לא הטוקן לבדו (ראו הטסט המכריע)."""
    if not interserver_auth.verify_pinned_spki(peer_cert_der, cred["pinned_parent_spki"]):
        return False
    return _cert_binding_ok(interserver_auth.certificate_ref(peer_cert_der),
                            cred["bound_cert_ref"])


# --- ping -------------------------------------------------------------------

def ping(conn, peer: TlsPeer, *, token: str, protocol_version: str,
         has_console_cookie: bool = False) -> dict:
    """‏ping מאומת: ‏TLS 1.3, תעודת-לקוח כרוכה לטוקן, SPKI תואם ה-pin,
    בלי console cookie, ותפקיד secondary. אי-התאמת SPKI/תעודה = 401 קשה,
    בלי TOFU. עיקרון 5: כל שומר נאכף גם כאן, לא רק ב-route."""
    if has_console_cookie:
        raise PairError(401, "עוגיית קונסולה אינה תקפה בערוץ הבין-שרתי")
    _require_secure_peer(peer)
    _require_protocol(protocol_version)
    _require_secondary(conn)

    cred = conn.execute(
        "SELECT parent_id, token_hash, bound_cert_ref, pinned_parent_spki,"
        " protocol_version FROM parent_credentials WHERE singleton = 1"
    ).fetchone()
    if cred is None:
        raise PairError(401, "אין אב רשום")

    # הצמדת SPKI+תעודה שלאחר-ה-pairing (RFC 8705): שינוי מפתח או תעודה
    # אחרת = כשל קשה 401, בלי TOFU. בלי הכריכה, טוקן גנוב היה מספיק.
    if not _peer_bound(peer.cert_der, cred):
        raise PairError(401, "תעודת-הלקוח/SPKI אינם כרוכים לטוקן")
    if not interserver_auth.verify_token(token or "", cred["token_hash"]):
        raise PairError(401, "טוקן שגוי")

    return {
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
        "node_id": storage_nodes.identity(conn)["node_id"],
        "role": storage_nodes.ROLE_SECONDARY,
        "parent_id": cred["parent_id"],
        "ok": True,
    }


# --- אפליקציית ה-FastAPI המבודדת --------------------------------------------

def create_interserver_app(ctx, tls_peer_provider):
    """אפליקציה **מבודדת** לכניסה הבין-שרתית — לא הקונסולה ולא הסוכן.

    שלושה ניתובים בלבד, בלי ‏/console, ‏/boot או עוגיות. ``tls_peer_provider``
    הוא callable(request)->TlsPeer שה-terminator מזין; הוא **מקור זהות
    ה-TLS היחיד** (עיקרון 5: לא header, לא request.client)."""
    from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

    app = FastAPI(title="ImageCtl interserver", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.state.handles = {}
    router = APIRouter(prefix="/api/interserver/v1")

    def _peer(request: Request) -> TlsPeer:
        return tls_peer_provider(request)

    def _http(exc: PairError) -> HTTPException:
        return HTTPException(exc.status, exc.detail)

    @router.post("/pair-begin")
    async def pair_begin_route(request: Request, peer: TlsPeer = Depends(_peer)):
        body = await request.json()
        try:
            return pair_begin(ctx.conn, app.state.handles, peer, body,
                              data_dir=getattr(ctx, "data_dir", None))
        except PairError as exc:
            raise _http(exc)

    @router.post("/pair-complete")
    async def pair_complete_route(request: Request, peer: TlsPeer = Depends(_peer)):
        body = await request.json()
        try:
            return pair_complete(ctx.conn, app.state.handles, peer, body)
        except PairError as exc:
            raise _http(exc)

    @router.get("/ping")
    def ping_route(request: Request, peer: TlsPeer = Depends(_peer)):
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth[:7].lower() == "bearer " else ""
        version = request.headers.get("imagectl-protocol-version", "")
        # דחיית עוגיית קונסולה: הערוץ הבין-שרתי אינו מכיר אימות-קונסולה.
        has_cookie = bool(request.cookies)
        try:
            return ping(ctx.conn, peer, token=token, protocol_version=version,
                        has_console_cookie=has_cookie)
        except PairError as exc:
            raise _http(exc)

    app.include_router(router)
    return app


# --- terminator TLS של pyOpenSSL (מאזין ה-enrollment בייצור ובבדיקות) -------
#
# ‏uvinicorn/stdlib-ssl אינם יכולים לקלוט תעודת-לקוח לא-מוכרת ולחשוף
# exporter (ראו interserver_auth). לכן המאזין הבין-שרתי הוא terminator
# עצמאי: הוא מסיים את ה-mTLS, בונה ``TlsPeer`` מהחיבור החי, וקורא לאותן
# פונקציות service בדיוק שה-app ומבחני היחידה משתמשים בהן — מקור אמת אחד.

def _read_http_request(conn):
    """קורא בקשת HTTP/1.1 אחת מחיבור pyOpenSSL. מחזיר (method, path, headers, body)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    method, path, _ = (lines[0].decode() + "  ").split(" ", 2)
    headers = {}
    for line in lines[1:]:
        if b":" in line:
            k, _, v = line.partition(b":")
            headers[k.strip().lower().decode()] = v.strip().decode()
    length = int(headers.get("content-length", "0"))
    body = rest
    while len(body) < length:
        chunk = conn.recv(4096)
        if not chunk:
            break
        body += chunk
    return method, path, headers, body[:length]


def _http_response(status: int, payload: dict) -> bytes:
    import json
    body = json.dumps(payload).encode()
    reason = {200: "OK"}.get(status, "ERROR")
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: keep-alive\r\n\r\n"
            ).encode() + body


class InterserverTLSServer:
    """מאזין mTLS 1.3 threaded לכניסה הבין-שרתית, מבוסס pyOpenSSL.

    ``ctx`` הוא ה-ServerContext (עם ``conn``); ``data_dir`` לזהות. מסיים
    את ה-TLS, בונה TlsPeer, ומנתב ל-``pair_begin``/``pair_complete``/``ping``.
    """

    def __init__(self, ctx, host: str, port: int, cert_pem: bytes, key_pem: bytes,
                 *, data_dir=None):
        import socket
        import threading
        self.ctx = ctx
        self.data_dir = data_dir
        self.handles: dict = {}
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((host, port))
        self._sock.listen(16)
        self.port = self._sock.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)

    def start(self) -> "InterserverTLSServer":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass

    def _serve(self) -> None:
        from OpenSSL import SSL
        tls_ctx = interserver_auth.build_server_tls_context(self._cert_pem, self._key_pem)
        while not self._stop.is_set():
            try:
                raw, _addr = self._sock.accept()
            except OSError:
                break
            import threading
            threading.Thread(target=self._handle, args=(tls_ctx, raw),
                             daemon=True).start()

    def _handle(self, tls_ctx, raw) -> None:
        from OpenSSL import SSL
        conn = SSL.Connection(tls_ctx, raw)
        conn.set_accept_state()
        try:
            conn.do_handshake()
            peer_cert = conn.get_peer_certificate()
            peer = TlsPeer(
                tls_version=conn.get_protocol_version_name(),
                cert_der=(interserver_auth._to_der(peer_cert) if peer_cert else None),
                exporter=conn.export_keying_material(interserver_auth.EXPORTER_LABEL,
                                                     interserver_auth.EXPORTER_LENGTH),
            )
            while not self._stop.is_set():
                req = _read_http_request(conn)
                if req is None:
                    break
                method, path, headers, body = req
                conn.sendall(self._dispatch(peer, method, path, headers, body))
                if headers.get("connection", "").lower() == "close":
                    break
        except SSL.Error:
            pass
        finally:
            try:
                conn.shutdown()
            except Exception:                                # noqa: BLE001
                pass
            conn.close()
            raw.close()

    def _dispatch(self, peer, method, path, headers, body) -> bytes:
        import json
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return _http_response(400, {"detail": "גוף JSON לא תקין"})
        try:
            if method == "POST" and path.endswith("/pair-begin"):
                return _http_response(200, pair_begin(
                    self.ctx.conn, self.handles, peer, payload, data_dir=self.data_dir))
            if method == "POST" and path.endswith("/pair-complete"):
                return _http_response(200, pair_complete(
                    self.ctx.conn, self.handles, peer, payload))
            if method == "GET" and path.endswith("/ping"):
                auth = headers.get("authorization", "")
                token = auth[7:] if auth[:7].lower() == "bearer " else ""
                cookie = headers.get("cookie", "")
                return _http_response(200, ping(
                    self.ctx.conn, peer, token=token,
                    protocol_version=headers.get("imagectl-protocol-version", ""),
                    has_console_cookie="imagectl_session" in cookie))
            return _http_response(404, {"detail": "לא קיים"})
        except PairError as exc:
            return _http_response(exc.status, {"detail": exc.detail})
