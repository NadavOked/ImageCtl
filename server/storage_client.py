"""Storage Nodes — לקוח ה-enrollment היוצא של הראשי (#740, tracer 2.1).

הראשי מציג את זהות ה-TLS שלו כתעודת-לקוח, ומצמיד את ה-SPKI של המשני
(hard-fail, בלי TOFU). לעולם לא עוקב אחר redirect ולעולם לא נופל ל-HTTP
או ל-TLS 1.2 — כתובת שאינה ``https://host:port`` מפורש נדחית עוד לפני
החיבור (עיקרון 5: downgrade הוא כשל, לא "קרוב מספיק").

ה-transport הוא HTTP/1.1 מינימלי מעל ``OpenSSL.SSL`` — ‏pair-begin
ו-pair-complete רצים על **אותו** חיבור (keep-alive), ולכן על אותה session
TLS, וזה מה שקושר אותם ב-channel-binding בצד המשני.
"""

from __future__ import annotations

import json
import socket

from . import interserver_auth
from .interserver_auth import parse_interserver_url   # re-export לנוחות

__all__ = ["parse_interserver_url", "PinnedMTLSClient", "fetch_server_spki",
           "pair_secondary", "ping_secondary", "InterserverClientError"]


class InterserverClientError(RuntimeError):
    """כשל בשיחה הבין-שרתית מצד הראשי (חיבור, SPKI, או תשובת שגיאה)."""


def _read_http_response(conn) -> tuple[int, dict, bytes]:
    """קורא תשובת HTTP/1.1 אחת (status, headers, body) מחיבור ה-TLS.

    קורא עד סוף הכותרות, ואז בדיוק ``Content-Length`` בייטים. אין chunked —
    השרת הבין-שרתי מחזיר תמיד אורך מפורש (תשובות זעירות)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            raise InterserverClientError("החיבור נסגר לפני סוף הכותרות")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    lines = head.split(b"\r\n")
    status = int(lines[0].split(b" ")[1])
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
            raise InterserverClientError("החיבור נסגר לפני סוף הגוף")
        body += chunk
    return status, headers, body[:length]


class PinnedMTLSClient:
    """חיבור mTLS 1.3 יחיד למשני, עם SPKI מוצמד ותעודת-לקוח של הראשי."""

    def __init__(self, host: str, port: int, *, expected_secondary_spki: str,
                 cert_pem: bytes, key_pem: bytes, path_prefix: str = "/api/interserver/v1",
                 timeout: float = 10.0):
        self.host = host
        self.port = port
        self.expected_spki = expected_secondary_spki
        self._cert_pem = cert_pem
        self._key_pem = key_pem
        self.path_prefix = path_prefix.rstrip("/")
        self.timeout = timeout
        self._conn = None

    def __enter__(self) -> "PinnedMTLSClient":
        self.open()
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def open(self) -> None:
        from OpenSSL import SSL
        ctx = interserver_auth.build_pinned_client_context(
            self.expected_spki, self._cert_pem, self._key_pem)
        raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
        # ‏socket עם timeout הוא non-blocking ל-pyOpenSSL (WantReadError ב-
        # handshake). ה-timeout שירת את ה-connect; מכאן ה-TLS חוסם רגיל.
        raw.settimeout(None)
        conn = SSL.Connection(ctx, raw)
        conn.set_connect_state()
        try:
            conn.do_handshake()
        except SSL.Error as exc:
            raw.close()
            # אי-התאמת SPKI מפילה את ה-handshake כאן — hard fail, לא המשך.
            raise InterserverClientError(
                f"‏handshake mTLS נכשל (SPKI לא תואם או TLS<1.3?): {exc}") from exc
        if conn.get_protocol_version_name() != "TLSv1.3":
            conn.close(); raw.close()
            raise InterserverClientError("השרת ירד מ-TLS 1.3 — נדחה")
        self._raw = raw
        self._conn = conn

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.shutdown()
            except Exception:                                # noqa: BLE001
                pass
            self._conn.close()
            self._raw.close()
            self._conn = None

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 headers: dict | None = None) -> tuple[int, dict, dict]:
        if self._conn is None:
            raise InterserverClientError("החיבור אינו פתוח")
        payload = json.dumps(body).encode() if body is not None else b""
        lines = [
            f"{method} {self.path_prefix}{path} HTTP/1.1",
            f"Host: {self.host}",
            "Connection: keep-alive",
            f"ImageCtl-Protocol-Version: {interserver_auth.PROTOCOL_VERSION}",
        ]
        for k, v in (headers or {}).items():
            lines.append(f"{k}: {v}")
        if body is not None:
            lines.append("Content-Type: application/json")
            lines.append(f"Content-Length: {len(payload)}")
        else:
            lines.append("Content-Length: 0")
        request = ("\r\n".join(lines) + "\r\n\r\n").encode() + payload
        self._conn.sendall(request)
        status, _resp_headers, raw_body = _read_http_response(self._conn)
        try:
            parsed = json.loads(raw_body) if raw_body else {}
        except json.JSONDecodeError:
            parsed = {"detail": raw_body.decode("utf-8", "replace")}
        return status, _resp_headers, parsed

    def pair(self, *, code: str, primary_id: str, client_nonce: str = "") -> dict:
        """‏pair-begin ואז pair-complete על אותו חיבור. מחזיר את תשובת ה-complete
        (הטוקן וזהות האב) או מעלה חריגה עם ה-detail של השרת."""
        import secrets
        begin_status, _, begin = self._request("POST", "/pair-begin", body={
            "primary_id": primary_id,
            "expected_secondary_spki": self.expected_spki,
            "protocol_version": interserver_auth.PROTOCOL_VERSION,
            "client_nonce": client_nonce or secrets.token_hex(16),
        })
        if begin_status != 200:
            raise InterserverClientError(
                f"pair-begin נכשל ({begin_status}): {begin.get('detail')}")
        comp_status, _, comp = self._request("POST", "/pair-complete", body={
            "handle": begin["handle"],
            "code": code,
            "protocol_version": interserver_auth.PROTOCOL_VERSION,
        })
        if comp_status != 200:
            raise InterserverClientError(
                f"pair-complete נכשל ({comp_status}): {comp.get('detail')}")
        comp["secondary_id"] = begin.get("secondary_id")
        comp["secondary_spki"] = begin.get("secondary_spki")
        return comp

    def ping(self, token: str) -> dict:
        status, _, body = self._request("GET", "/ping", headers={
            "Authorization": f"Bearer {token}",
        })
        if status != 200:
            raise InterserverClientError(f"ping נכשל ({status}): {body.get('detail')}")
        return body


def fetch_server_spki(host: str, port: int, *, timeout: float = 10.0) -> str:
    """קורא את תעודת השרת (בלי הצמדה ובלי תעודת-לקוח) ומחזיר את ה-SPKI שלה,
    כדי שהמנהל יַשווה אותו מול מה שהמשני מציג לפני שהוא שולח את הקוד."""
    from OpenSSL import SSL
    ctx = SSL.Context(SSL.TLS_METHOD)
    ctx.set_min_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_max_proto_version(SSL.TLS1_3_VERSION)
    ctx.set_verify(SSL.VERIFY_NONE, lambda *a: True)
    raw = socket.create_connection((host, port), timeout=timeout)
    conn = SSL.Connection(ctx, raw)
    conn.set_connect_state()
    try:
        conn.do_handshake()
        return interserver_auth.spki_sha256(conn.get_peer_certificate())
    finally:
        try:
            conn.close()
        finally:
            raw.close()


def pair_secondary(url: str, *, code: str, expected_secondary_spki: str,
                   cert_pem: bytes, key_pem: bytes, primary_id: str) -> dict:
    """נוחות: פותח כתובת בין-שרתית מפורשת, מבצע pairing, ומחזיר את הטוקן."""
    host, port, _path = parse_interserver_url(url)
    with PinnedMTLSClient(host, port, expected_secondary_spki=expected_secondary_spki,
                          cert_pem=cert_pem, key_pem=key_pem) as client:
        return client.pair(code=code, primary_id=primary_id)


def ping_secondary(url: str, *, token: str, expected_secondary_spki: str,
                   cert_pem: bytes, key_pem: bytes) -> dict:
    host, port, _path = parse_interserver_url(url)
    with PinnedMTLSClient(host, port, expected_secondary_spki=expected_secondary_spki,
                          cert_pem=cert_pem, key_pem=key_pem) as client:
        return client.ping(token)
