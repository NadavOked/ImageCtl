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
import select
import shutil
import socket
import struct
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from . import interserver_auth, storage_nodes
from .db import journal, now_iso

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


# --- אימות שלאחר-ה-pairing (ping, אימג'ים, מכונות, מוניטור) -----------------

def authenticate_parent(conn, peer: TlsPeer, *, token: str, protocol_version: str,
                        has_console_cookie: bool = False):
    """השומר המשותף לכל קריאה מאומתת של האב: ‏TLS 1.3, תעודת-לקוח כרוכה
    לטוקן, SPKI תואם ה-pin, בלי console cookie, ותפקיד secondary.
    אי-התאמת SPKI/תעודה = 401 קשה, בלי TOFU. מחזיר את רשומת האב.
    עיקרון 5: נאכף בשכבת ה-service, לא רק ב-route."""
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
    return cred


def ping(conn, peer: TlsPeer, *, token: str, protocol_version: str,
         has_console_cookie: bool = False) -> dict:
    """‏ping מאומת — ראו :func:`authenticate_parent`."""
    cred = authenticate_parent(conn, peer, token=token,
                               protocol_version=protocol_version,
                               has_console_cookie=has_console_cookie)
    return {
        "protocol_version": interserver_auth.PROTOCOL_VERSION,
        "node_id": storage_nodes.identity(conn)["node_id"],
        "role": storage_nodes.ROLE_SECONDARY,
        "parent_id": cred["parent_id"],
        "ok": True,
    }


# --- קליטת אימג' מהאב (#655 v1, העברה ראשי→משני) -----------------------------
#
# הגוף הוא ה-tar של תיקיית האימג' (``archive.tar_stream``), והכניסה לספרייה
# היא **אותו** ``archive.import_tar`` של העלאה מהדפדפן: חילוץ לאזור ביניים,
# ‏sha256 של כל מחיצה מול המניפסט, ורק אז ``rename`` לספרייה (עיקרון 6).
# קטוע/לא-תואם — נמחק ומדווח בגלוי; לעולם לא תיקייה חלקית בשם אימג'.
#
# שני שלבים, כי הגוף הוא עשרות ג'יגה: ``receive_image_precheck`` רץ **לפני**
# שבייט אחד של גוף נקרא (אימות, תפקיד, מזהה, "כבר קיים", מקום פנוי) —
# הראשי שולח ``Expect: 100-continue`` ומחכה ל-``100`` — ואז
# ``receive_image`` כותב את הזרם לקובץ זמני ומכניס לספרייה.

#: כמה מקום פנוי דורשים לפני קליטה: ה-tar עצמו + החילוץ (עותק) + שוליים.
FREE_SPACE_FACTOR = 2
FREE_SPACE_MARGIN = 256 * 1024 * 1024
#: תקרה לגוף JSON בערוץ הבין-שרתי (pair/ping/מכונות): לא זרם, לא ג'יגה.
MAX_JSON_BODY = 1024 * 1024


@dataclass
class ReceivePlan:
    """מה שנקבע ב-precheck ומועבר לקליטה עצמה."""
    image_id: str
    content_length: int
    root: Path


def _library(ctx):
    library = getattr(ctx, "library", None)
    if library is None:
        raise PairError(503, "אין ספריית אימג'ים בשרת הזה")
    return library


def image_present(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                  image_id: str, has_console_cookie: bool = False) -> dict:
    """האם האימג' כבר בספריית המשני — מהדיסק (עיקרון 3), לא מטבלה."""
    from .images import valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    manifest = _library(ctx).get(image_id)
    return {"id": image_id, "present": manifest is not None,
            "name": manifest["name"] if manifest else None}


def receive_image_precheck(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                           image_id: str, content_length: object,
                           has_console_cookie: bool = False) -> ReceivePlan:
    """כל מה שאפשר לדחות **לפני** קריאת הגוף."""
    from .images import valid_image_id
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    library = _library(ctx)
    if not valid_image_id(image_id):
        raise PairError(400, f"מזהה אימג' לא תקין: {image_id!r}")
    if library.get(image_id) is not None:
        raise PairError(409, f"האימג' {image_id} כבר קיים בספריית המשני")
    try:
        length = int(content_length)
    except (TypeError, ValueError):
        raise PairError(411, "נדרש Content-Length מספרי")
    if length <= 0:
        raise PairError(411, "נדרש Content-Length חיובי")
    root = Path(library.root)
    root.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(root).free
    except OSError as exc:
        # לא הצלחנו לבדוק ≠ יש מקום (עיקרון 5).
        raise PairError(507, f"לא ניתן לקרוא את המקום הפנוי בספרייה: {exc}")
    need = length * FREE_SPACE_FACTOR + FREE_SPACE_MARGIN
    if free < need:
        raise PairError(507, f"אין די מקום פנוי במשני: נדרשים {need} בייטים, "
                             f"פנויים {free}")
    return ReceivePlan(image_id=image_id, content_length=length, root=root)


def receive_image(ctx, plan: ReceivePlan, chunks: Iterable[bytes]) -> dict:
    """כותב את הזרם לקובץ זמני **בתוך** הספרייה (אותה מערכת קבצים), ומכניס
    דרך ``import_tar`` — שם נעשה אימות ה-sha256. מחזיר את המניפסט שנקלט."""
    from .archive import ArchiveError, import_tar
    from .images import validate_display_name
    library = _library(ctx)
    handle = tempfile.NamedTemporaryFile(delete=False, dir=plan.root,
                                         suffix=".transfer")
    temp = Path(handle.name)
    received = 0
    try:
        with handle:
            for chunk in chunks:
                handle.write(chunk)
                received += len(chunk)
                if received > plan.content_length:
                    raise PairError(400, "הגוף ארוך מ-Content-Length המוצהר")
        if received != plan.content_length:
            # קטוע = כשל בשם, עם המספרים (עיקרון 4: אין זריקת בייטים בשקט).
            raise PairError(400, f"ההעברה נקטעה: התקבלו {received} מתוך "
                                 f"{plan.content_length} בייטים")
        try:
            manifest = import_tar(temp, plan.root, set(library.scan()))
        except ArchiveError as exc:
            raise PairError(400, f"האימג' נדחה בקליטה: {exc}")
        except Exception as exc:                       # noqa: BLE001 — tar פגום
            raise PairError(400, f"הארכיון לא נקרא: {exc}")
    finally:
        temp.unlink(missing_ok=True)
    # המניפסט נקרא מהזרם; מזהה שאינו זה שבנתיב הוא אימג' אחר שנכנס בשם
    # מטעה — מוסר מהספרייה ונדחה בגלוי. אותה בדיקת-שם כמו העלאה (#138).
    try:
        if manifest["id"] != plan.image_id:
            raise ValueError(f"המניפסט מצהיר על {manifest['id']!r} ולא על "
                             f"{plan.image_id!r}")
        validate_display_name(manifest["name"], "שם האימג'")
        if manifest.get("folder"):
            validate_display_name(manifest["folder"], "שם התיקייה")
    except ValueError as exc:
        library.delete(manifest["id"])
        raise PairError(400, str(exc))
    journal(ctx.conn, "storage_image_received",
            f'{manifest["id"]} "{manifest["name"]}" {plan.content_length}',
            "")
    return {"ok": True, "id": manifest["id"], "name": manifest["name"],
            "bytes": plan.content_length}


# --- צפייה מהאב: רשימת המכונות של המשני (#655 v1) -----------------------------

def list_machines(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                  has_console_cookie: bool = False) -> dict:
    """מה שדף המוניטור של המשני מראה — ‏``monitor.machine_rows`` — לאב
    המאומת. קריאה בלבד; ``node_id`` מצורף כדי שהאב ידע ממי קיבל."""
    from .monitor import machine_rows
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    return {"node_id": storage_nodes.identity(ctx.conn)["node_id"],
            "machines": machine_rows(ctx.conn)}


# --- מנהרת מוניטור מהאב (#655 v1): פרוקסי-של-פרוקסי ----------------------------
#
# הראשי לעולם אינו רואה סוד של מכונה. הוא מבקש ``GET /monitor/{mac}`` בערוץ
# המאומת; **המשני** פותח TCP למכונה, מבצע את לחיצת-היד של #839 בסוד
# שהמכונה דיווחה **לו**, ורק אחרי SecurityResult OK עונה ``200`` — ומכאן
# אותה session TLS הופכת למנהרת בייטים גולמית (כמו CONNECT): כל מה שהאב
# שולח נכנס למכונה (ClientInit ואילך), וכל מה שהמכונה מחזירה חוזר לאב.
# הראשי מגשר את זה לדפדפן ב-``monitor.bridge_browser`` — הדפדפן רואה
# בדיוק מוניטור מקומי.
#
# הממסר הוא **תהליכון אחד עם select** על שני ה-sockets: אובייקט SSL של
# OpenSSL אינו בטוח לקריאה וכתיבה מקבילות משני תהליכונים, ולכן לא
# "תהליכון לכל כיוון".

#: כמה זמן בלי בייט באף כיוון עד שהמנהרה נסגרת.
TUNNEL_IDLE_SECONDS = 600
#: מכונה שכבר יש אליה מנהרה מהאב — השנייה נדחית (409), כמו במוניטור המקומי.
_tunnels: set[str] = set()
_tunnels_lock = threading.Lock()


class MachineAuthError(Exception):
    """לחיצת-היד מול המוניטור במכונה נכשלה — הסיבה בטקסט."""


def monitor_target(ctx, peer: TlsPeer, *, token: str, protocol_version: str,
                   mac: str, has_console_cookie: bool = False) -> tuple[str, str, str]:
    """‏``(mac קנוני, ip, secret)`` של מכונה שמותר לפתוח אליה מוניטור מהאב —
    אותם תנאים של המוניטור המקומי (``monitor._target``: רשומה, build/cloner,
    נראתה ב-90 השניות האחרונות). בלי סוד = 512 (סוכן ישן), לפני TCP."""
    from fastapi import HTTPException
    from . import monitor as monitor_module
    from . import registry
    authenticate_parent(ctx.conn, peer, token=token, protocol_version=protocol_version,
                        has_console_cookie=has_console_cookie)
    try:
        ip, _role, secret = monitor_module._target(ctx, mac)
    except HTTPException as exc:
        raise PairError(exc.status_code, str(exc.detail))
    if secret is None:
        raise PairError(512, "המכונה לא דיווחה סוד מוניטור — סוכן ישן?")
    return registry.normalize_mac(mac), ip, secret


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        piece = sock.recv(n - len(buf))
        if not piece:
            raise MachineAuthError(f"המוניטור סגר את החיבור אחרי {len(buf)}/{n} בייטים")
        buf += piece
    return bytes(buf)


def _reason_sync(sock: socket.socket) -> str:
    length = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if length > 200:
        return "(סיבה ארוכה מדי)"
    return _recv_exact(sock, length).decode("utf-8", "replace")[:40]


def authenticate_machine_sync(sock: socket.socket, secret: str) -> None:
    """‏#839 על socket חוסם — מראה של ``monitor.authenticate_machine``
    (שהוא asyncio): גרסה, סוג 2 בלבד (None לעולם לא), 16 בייטי הסוד,
    ‏SecurityResult. אחרי ההצלחה המכונה ממתינה ל-ClientInit — מהאב."""
    from .monitor import RFB_SECURITY_SECRET, RFB_VERSION
    banner = _recv_exact(sock, 12)
    if not banner.startswith(b"RFB 003."):
        raise MachineAuthError("על 5900 עונה משהו שאינו RFB")
    sock.sendall(RFB_VERSION)
    count = _recv_exact(sock, 1)[0]
    if count == 0:
        raise MachineAuthError(f"המוניטור דחה את החיבור: {_reason_sync(sock)}")
    offered = _recv_exact(sock, count)
    if RFB_SECURITY_SECRET not in offered:
        raise MachineAuthError(
            "המוניטור במכונה אינו דורש סוד — סוכן ישן? (סוגי אבטחה "
            f"{sorted(offered)})")
    sock.sendall(bytes([RFB_SECURITY_SECRET]))
    _recv_exact(sock, 16)                                 # ה-challenge; לא בשימוש
    sock.sendall(bytes.fromhex(secret))
    result = struct.unpack(">I", _recv_exact(sock, 4))[0]
    if result != 0:
        raise MachineAuthError(f"המוניטור דחה את סוד השרת: {_reason_sync(sock)}")


def relay_tunnel(tls_conn, raw_sock: socket.socket, machine: socket.socket, *,
                 initial: bytes = b"", idle_seconds: float = TUNNEL_IDLE_SECONDS) -> None:
    """ממסר דו-כיווני בתהליכון אחד: ‏TLS(האב) ↔ TCP(המכונה), עד שצד נסגר
    או עד ``idle_seconds`` בלי תנועה. ‏non-blocking + select, ו-Want*Error
    של OpenSSL מטופלים כ"עוד לא", לא ככשל."""
    from OpenSSL import SSL
    raw_sock.setblocking(False)
    machine.setblocking(False)
    to_machine = bytearray(initial)
    to_tls = bytearray()
    while True:
        rlist = [raw_sock, machine]
        wlist = []
        if to_machine:
            wlist.append(machine)
        if to_tls:
            wlist.append(raw_sock)
        if tls_conn.pending():
            readable, writable = [raw_sock], []
            if wlist:
                _, writable, _ = select.select([], wlist, [], 0)
        else:
            readable, writable, _ = select.select(rlist, wlist, [], idle_seconds)
            if not readable and not writable:
                return                                        # idle
        if raw_sock in readable:
            try:
                data = tls_conn.recv(65536)
            except SSL.WantReadError:
                data = None
            except (SSL.ZeroReturnError, SSL.SysCallError, SSL.Error, OSError):
                return
            if data == b"":
                return
            if data:
                to_machine += data
        if machine in readable:
            try:
                data = machine.recv(65536)
            except BlockingIOError:
                data = None
            except OSError:
                return
            if data == b"":
                return
            if data:
                to_tls += data
        if to_machine and machine in writable:
            try:
                sent = machine.send(bytes(to_machine))
                del to_machine[:sent]
            except BlockingIOError:
                pass
            except OSError:
                return
        if to_tls and raw_sock in writable:
            try:
                sent = tls_conn.send(bytes(to_tls[:16384]))
                del to_tls[:sent]
            except (SSL.WantWriteError, SSL.WantReadError):
                pass
            except (SSL.ZeroReturnError, SSL.SysCallError, SSL.Error, OSError):
                return


# --- אפליקציית ה-FastAPI המבודדת --------------------------------------------

def _bearer(headers) -> str:
    auth = headers.get("authorization", "")
    return auth[7:] if auth[:7].lower() == "bearer " else ""


def create_interserver_app(ctx, tls_peer_provider):
    """אפליקציה **מבודדת** לכניסה הבין-שרתית — לא הקונסולה ולא הסוכן.

    בלי ‏/console, ‏/boot או עוגיות. ``tls_peer_provider`` הוא
    callable(request)->TlsPeer שה-terminator מזין; הוא **מקור זהות ה-TLS
    היחיד** (עיקרון 5: לא header, לא request.client). המאזין בייצור הוא
    ``InterserverTLSServer`` למטה; האפליקציה הזאת היא אותן פונקציות
    service מאחורי routes של FastAPI, לבדיקות ולתיעוד הצורה."""
    from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

    app = FastAPI(title="ImageCtl interserver", docs_url=None, redoc_url=None)
    app.state.ctx = ctx
    app.state.handles = {}
    router = APIRouter(prefix="/api/interserver/v1")

    def _peer(request: Request) -> TlsPeer:
        return tls_peer_provider(request)

    def _http(exc: PairError) -> HTTPException:
        return HTTPException(exc.status, exc.detail)

    def _creds(request: Request) -> dict:
        # דחיית עוגיית קונסולה: הערוץ הבין-שרתי אינו מכיר אימות-קונסולה.
        return {"token": _bearer(request.headers),
                "protocol_version": request.headers.get("imagectl-protocol-version", ""),
                "has_console_cookie": bool(request.cookies)}

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
        try:
            return ping(ctx.conn, peer, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/machines")
    def machines_route(request: Request, peer: TlsPeer = Depends(_peer)):
        try:
            return list_machines(ctx, peer, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.get("/images/{image_id}")
    def image_present_route(image_id: str, request: Request,
                            peer: TlsPeer = Depends(_peer)):
        try:
            return image_present(ctx, peer, image_id=image_id, **_creds(request))
        except PairError as exc:
            raise _http(exc)

    @router.put("/images/{image_id}")
    async def receive_image_route(image_id: str, request: Request,
                                  peer: TlsPeer = Depends(_peer)):
        try:
            plan = receive_image_precheck(
                ctx, peer, image_id=image_id,
                content_length=request.headers.get("content-length"),
                **_creds(request))
        except PairError as exc:
            raise _http(exc)
        chunks = []
        async for chunk in request.stream():
            chunks.append(chunk)
        try:
            return receive_image(ctx, plan, chunks)
        except PairError as exc:
            raise _http(exc)

    app.include_router(router)
    return app


# --- terminator TLS של pyOpenSSL (המאזין הבין-שרתי בייצור ובבדיקות) ---------
#
# ‏uvinicorn/stdlib-ssl אינם יכולים לקלוט תעודת-לקוח לא-מוכרת ולחשוף
# exporter (ראו interserver_auth). לכן המאזין הבין-שרתי הוא terminator
# עצמאי: הוא מסיים את ה-mTLS, בונה ``TlsPeer`` מהחיבור החי, וקורא לאותן
# פונקציות service בדיוק שה-app ומבחני היחידה משתמשים בהן — מקור אמת אחד.
#
# גוף של אימג' (``PUT /images/{id}``) **זורם** לקובץ ואינו נאסף בזיכרון;
# כל שאר הגופים הם JSON קטן, ומוגבלים ב-``MAX_JSON_BODY``.

_PREFIX = "/api/interserver/v1"


def _read_http_head(conn):
    """קורא שורת-בקשה וכותרות של בקשת HTTP/1.1 אחת מחיבור pyOpenSSL.
    מחזיר ``(method, path, headers, rest)`` — ‏``rest`` הם בייטי הגוף שכבר
    הגיעו עם הכותרות — או ``None`` כשהחיבור נסגר."""
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
    return method, path, headers, rest


def _body_chunks(conn, rest: bytes, length: int, chunk_size: int = 1024 * 1024):
    """מחולל את גוף הבקשה — בדיוק ``length`` בייטים, או פחות אם החיבור
    נסגר (הקורא סופר ומכשיל; כאן לא מסתירים קטיעה)."""
    got = 0
    if rest:
        piece = rest[:length]
        got += len(piece)
        yield piece
    while got < length:
        chunk = conn.recv(min(chunk_size, length - got))
        if not chunk:
            return
        got += len(chunk)
        yield chunk


def _read_http_request(conn):
    """גרסת "הכול בזיכרון" — לבקשות JSON קטנות. ``None`` = החיבור נסגר."""
    head = _read_http_head(conn)
    if head is None:
        return None
    method, path, headers, rest = head
    length = int(headers.get("content-length", "0"))
    body = b"".join(_body_chunks(conn, rest, length))
    return method, path, headers, body


def _http_response(status: int, payload: dict, *, close: bool = False) -> bytes:
    import json
    body = json.dumps(payload).encode()
    reason = {200: "OK", 100: "Continue"}.get(status, "ERROR")
    connection = "close" if close else "keep-alive"
    return (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: {connection}\r\n\r\n"
            ).encode() + body


_CONTINUE = b"HTTP/1.1 100 Continue\r\n\r\n"


class InterserverTLSServer:
    """מאזין mTLS 1.3 threaded לכניסה הבין-שרתית, מבוסס pyOpenSSL.

    ``ctx`` הוא ה-ServerContext (עם ``conn`` ו-``library``); ``data_dir``
    לזהות. מסיים את ה-TLS, בונה TlsPeer, ומנתב לפונקציות ה-service.
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
                head = _read_http_head(conn)
                if head is None:
                    break
                method, path, headers, rest = head
                if method == "PUT" and _image_id_of(path) is not None:
                    keep = self._receive_stream(conn, peer, path, headers, rest)
                elif method == "GET" and _monitor_mac_of(path) is not None:
                    self._monitor_tunnel(conn, raw, peer, path, headers, rest)
                    keep = False
                else:
                    keep = self._small_request(conn, peer, method, path, headers, rest)
                if not keep or headers.get("connection", "").lower() == "close":
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

    def _small_request(self, conn, peer, method, path, headers, rest) -> bool:
        """בקשת JSON: קוראים את הגוף (עד התקרה) ומשיבים. מחזיר האם
        להמשיך ב-keep-alive."""
        length = int(headers.get("content-length", "0") or 0)
        if length > MAX_JSON_BODY:
            conn.sendall(_http_response(413, {"detail": "גוף גדול מדי"}, close=True))
            return False
        body = b"".join(_body_chunks(conn, rest, length))
        conn.sendall(self._dispatch(peer, method, path, headers, body))
        return True

    def _receive_stream(self, conn, peer, path, headers, rest) -> bool:
        """‏``PUT /images/{id}``: precheck → ‏``100 Continue`` → זרם לקובץ →
        תשובה. דחייה ב-precheck נשלחת **בלי** לקרוא גוף, והחיבור נסגר —
        כדי שלקוח שלא חיכה ל-100 לא ישאיר עשרות ג'יגה בצנרת."""
        try:
            plan = receive_image_precheck(
                self.ctx, peer, image_id=_image_id_of(path),
                content_length=headers.get("content-length"),
                token=_bearer(headers),
                protocol_version=headers.get("imagectl-protocol-version", ""),
                has_console_cookie="imagectl_session" in headers.get("cookie", ""))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, {"detail": exc.detail}, close=True))
            return False
        if "100-continue" in headers.get("expect", "").lower():
            conn.sendall(_CONTINUE)
        try:
            result = receive_image(self.ctx, plan,
                                   _body_chunks(conn, rest, plan.content_length))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, {"detail": exc.detail}, close=True))
            return False
        conn.sendall(_http_response(200, result))
        return True

    def _monitor_tunnel(self, conn, raw, peer, path, headers, rest) -> None:
        """‏``GET /monitor/{mac}``: אימות → יעד → מנעול → TCP למכונה →
        לחיצת-יד בסוד של המשני → ``200`` → ממסר גולמי עד סגירה."""
        from . import monitor as monitor_module
        try:
            mac, ip, secret = monitor_target(
                self.ctx, peer, mac=_monitor_mac_of(path), token=_bearer(headers),
                protocol_version=headers.get("imagectl-protocol-version", ""),
                has_console_cookie="imagectl_session" in headers.get("cookie", ""))
        except PairError as exc:
            conn.sendall(_http_response(exc.status, {"detail": exc.detail}, close=True))
            return
        with _tunnels_lock:
            if mac in _tunnels:
                conn.sendall(_http_response(
                    409, {"detail": "כבר פתוח מוניטור למכונה הזאת"}, close=True))
                return
            _tunnels.add(mac)
        machine = None
        try:
            try:
                machine = socket.create_connection((ip, monitor_module.MONITOR_PORT),
                                                   timeout=3.0)
            except OSError as exc:
                conn.sendall(_http_response(
                    502, {"detail": f"שירות המוניטור במכונה אינו זמין: {exc}"},
                    close=True))
                return
            try:
                machine.settimeout(5.0)
                authenticate_machine_sync(machine, secret)
            except MachineAuthError as exc:
                conn.sendall(_http_response(512, {"detail": str(exc)}, close=True))
                return
            except (OSError, socket.timeout):
                conn.sendall(_http_response(
                    512, {"detail": "המוניטור לא השלים את לחיצת-היד תוך 5 שניות"},
                    close=True))
                return
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream"
                         b"\r\nConnection: close\r\n\r\n")
            journal(self.ctx.conn, "storage_monitor_tunnel", mac, "")
            relay_tunnel(conn, raw, machine, initial=rest)
        finally:
            if machine is not None:
                try:
                    machine.close()
                except OSError:
                    pass
            with _tunnels_lock:
                _tunnels.discard(mac)

    def _dispatch(self, peer, method, path, headers, body) -> bytes:
        import json
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return _http_response(400, {"detail": "גוף JSON לא תקין"})
        creds = {
            "token": _bearer(headers),
            "protocol_version": headers.get("imagectl-protocol-version", ""),
            "has_console_cookie": "imagectl_session" in headers.get("cookie", ""),
        }
        image_id = _image_id_of(path)
        try:
            if method == "POST" and path == f"{_PREFIX}/pair-begin":
                return _http_response(200, pair_begin(
                    self.ctx.conn, self.handles, peer, payload, data_dir=self.data_dir))
            if method == "POST" and path == f"{_PREFIX}/pair-complete":
                return _http_response(200, pair_complete(
                    self.ctx.conn, self.handles, peer, payload))
            if method == "GET" and path == f"{_PREFIX}/ping":
                return _http_response(200, ping(self.ctx.conn, peer, **creds))
            if method == "GET" and path == f"{_PREFIX}/machines":
                return _http_response(200, list_machines(self.ctx, peer, **creds))
            if method == "GET" and image_id is not None:
                return _http_response(200, image_present(
                    self.ctx, peer, image_id=image_id, **creds))
            return _http_response(404, {"detail": "לא קיים"})
        except PairError as exc:
            return _http_response(exc.status, {"detail": exc.detail})


def _monitor_mac_of(path: str) -> str | None:
    """ה-MAC מ-``/api/interserver/v1/monitor/{mac}`` (רכיב יחיד), או ``None``."""
    prefix = f"{_PREFIX}/monitor/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if not rest or "/" in rest or "?" in rest:
        return None
    return rest


def _image_id_of(path: str) -> str | None:
    """המזהה מ-``/api/interserver/v1/images/{id}`` (רכיב יחיד), או ``None``."""
    prefix = f"{_PREFIX}/images/"
    if not path.startswith(prefix):
        return None
    rest = path[len(prefix):]
    if not rest or "/" in rest or "?" in rest:
        return None
    return rest
