"""Storage Nodes — צפייה במשני מהראשי: מכונות ומוניטור (#655 v1).

אותם שני שרתים אמיתיים על loopback כמו ב-``test_storage_transfer``: הראשי
הוא האפליקציה המלאה, המשני הוא ``InterserverTLSServer``. (א) הראשי שואל
את המשני על המכונות שלו בערוץ המאומת, ומציג "לא מחובר" — לא 5xx ולא
רשימה ריקה-שנראית-כאמת — כשהמשני לא עונה. (ב) המוניטור למכונה של המשני
עובר **דרך המשני**: המשני מדבר עם המכונה בסוד שלו, הראשי מקבל זרם RFB
שכבר עבר אימות, והדפדפן רואה בדיוק את מה שהוא רואה מול מוניטור מקומי.
"""

from __future__ import annotations

import socket
import struct
import threading

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db, interserver_api, interserver_auth, monitor, registry, storage_nodes
from server.db import net_seen
from test_storage_transfer import paired, secondary          # noqa: F401 — fixtures

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except Exception:                                             # noqa: BLE001
    TestClient = None


pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

MAC = "aa:bb:cc:dd:ee:01"
SECRET = "00112233445566778899aabbccddeeff"
SECRET_BYTES = bytes.fromhex(SECRET)
CHALLENGE = bytes(range(16))
SERVER_INIT = b"\x00\x40\x00\x20" + b"\x00" * 16 + b"\x00\x00\x00\x00"


def _register(conn, ip: str, *, secret: str | None = SECRET) -> None:
    registry.add_machine(conn, MAC, "Remote", "grp_BUILD", "test")
    net_seen(conn, MAC, ip, monitor_secret=secret)


# --- (א) רשימת המכונות של המשני, דרך הראשי -----------------------------------

def test_machines_of_the_secondary_are_listed_through_the_primary(server, secondary,
                                                                   paired):
    _register(secondary["conn"], "10.20.0.77")
    resp = server["admin"].get(f"/api/console/storage-nodes/{paired}/machines")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["connected"] is True and body["error"] is None
    assert body["node_id"] == secondary["ident"]["node_id"]
    assert body["machines"] == monitor.machine_rows(secondary["conn"])
    assert body["machines"][0]["mac"] == MAC and body["machines"][0]["online"] is True
    # הראשי עצמו אינו מכיר את המכונה — הרשימה היא של המשני.
    assert monitor.machine_rows(server["ctx"].conn) == []


def test_unreachable_secondary_is_reported_not_hidden(server, secondary, paired):
    secondary["server"].stop()
    resp = server["admin"].get(f"/api/console/storage-nodes/{paired}/machines")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False and body["machines"] == []
    assert body["error"]


def test_machines_route_guards(server, paired):
    admin, deploy = server["admin"], server["deploy"]
    assert deploy.get(f"/api/console/storage-nodes/{paired}/machines").status_code == 403
    assert admin.get("/api/console/storage-nodes/nope/machines").status_code == 404
    storage_nodes.set_node_disabled(server["ctx"].conn, paired, True, ("noc", "admin"))
    body = admin.get(f"/api/console/storage-nodes/{paired}/machines").json()
    assert body["connected"] is False and "מושבת" in body["error"]
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    assert admin.get(f"/api/console/storage-nodes/{paired}/machines").status_code == 409


def test_machines_ingress_requires_the_bound_token(secondary, paired):
    """שכבת ה-service במשני: טוקן שגוי → 401, גם עם תעודה נכונה."""
    from test_storage_transfer import _peer
    ident = secondary["ident"]
    del ident
    primary, _ = interserver_auth.generate_self_signed("p2", ip_sans=["127.0.0.1"])
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.list_machines(secondary["ctx"], _peer(primary), token="0" * 64,
                                      protocol_version="2.1")
    assert e.value.status == 401


# --- (ב) מוניטור למכונה של המשני, דרך המשני ----------------------------------

def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        piece = sock.recv(n - len(buf))
        if not piece:
            raise ConnectionError("closed")
        buf += piece
    return buf


class FakeRfbServer(threading.Thread):
    """מוניטור מזויף על TCP אמיתי (‏127.0.0.1, פורט אקראי): לחיצת-יד RFB 3.8
    עם סוג 2, ואז הד — כל מה שמגיע נרשם ומוחזר עם הקידומת ``echo:``."""

    def __init__(self, *, offered: bytes = b"\x02", result: int = 0, reason: bytes = b""):
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(2)
        self.port = self.sock.getsockname()[1]
        self.offered, self.result, self.reason = offered, result, reason
        self.response: bytes | None = None
        self.writes: list[bytes] = []
        self.connected = threading.Event()
        self.closed = threading.Event()

    def run(self) -> None:
        try:
            self.sock.settimeout(20)
            c, _ = self.sock.accept()
        except OSError:
            return
        self.connected.set()
        try:
            c.sendall(b"RFB 003.008\n")
            _recv_exact(c, 12)
            c.sendall(bytes([len(self.offered)]) + self.offered)
            if not self.offered:
                return
            _recv_exact(c, 1)
            c.sendall(CHALLENGE)
            self.response = _recv_exact(c, 16)
            verdict = struct.pack(">I", self.result)
            if self.result:
                verdict += struct.pack(">I", len(self.reason)) + self.reason
            c.sendall(verdict)
            if self.result:
                return
            self.writes.append(_recv_exact(c, 1))               # ClientInit
            c.sendall(SERVER_INIT)
            while True:
                data = c.recv(65536)
                if not data:
                    return
                self.writes.append(data)
                c.sendall(b"echo:" + data)
        except OSError:
            return
        finally:
            c.close()
            self.closed.set()

    def stop(self) -> None:
        self.sock.close()


@pytest.fixture()
def machine(secondary, monkeypatch):
    """מכונה של המשני עם מוניטור מזויף חי; הפורט מוזרק במקום 5900."""
    fake = FakeRfbServer()
    fake.start()
    monkeypatch.setattr(monitor, "MONITOR_PORT", fake.port)
    _register(secondary["conn"], "127.0.0.1")
    yield fake
    fake.stop()


def _browser_handshake(ws) -> None:
    """מה ש-monitor.js עושה, בייט-בייט: גרסה, None, ‏ClientInit."""
    assert ws.receive_bytes() == b"RFB 003.008\n"
    ws.send_bytes(b"RFB 003.008\n")
    assert ws.receive_bytes() == b"\x01\x01"
    ws.send_bytes(b"\x01")
    assert ws.receive_bytes() == b"\x00\x00\x00\x00"
    ws.send_bytes(b"\x01")


def _ws_path(nid, mac=MAC):
    return f"/api/console/storage-nodes/{nid}/monitor/{mac}"


def _close_code(client, path):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(path) as ws:
            ws.receive_bytes()
    return e.value.code, e.value.reason


def test_remote_monitor_reaches_the_machine_through_the_secondary(server, secondary,
                                                                   paired, machine):
    """הראיה החיובית: המכונה קיבלה את הסוד **מהמשני**, הדפדפן — שמדבר רק עם
    הראשי — עבר לחיצת-יד רגילה, קיבל ServerInit, ומה ששלח חזר בהד."""
    admin = server["admin"]
    with admin.websocket_connect(_ws_path(paired)) as ws:
        _browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
        ws.send_bytes(b"client-rfb-data")
        assert ws.receive_bytes() == b"echo:client-rfb-data"
    assert machine.closed.wait(5)
    assert machine.response == SECRET_BYTES                  # הסוד יצא מהמשני
    assert machine.writes[0] == b"\x01"                      # ה-ClientInit של הדפדפן
    assert b"client-rfb-data" in machine.writes
    # הראשי לא ידע את הסוד: המכונה אינה רשומה בו ואין לו שום סוד מוניטור.
    assert server["ctx"].conn.execute(
        "SELECT COUNT(*) AS n FROM net_devices WHERE monitor_secret IS NOT NULL"
    ).fetchone()["n"] == 0
    assert secondary["conn"].execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'storage_monitor_tunnel'"
    ).fetchone()["n"] == 1


def test_remote_monitor_gate_closes_before_any_tunnel(server, secondary, paired, machine):
    """‏deploy/אנונימי נסגרים בראשי לפני שהמשני — או המכונה — נוגעים בכלל."""
    assert _close_code(server["deploy"], _ws_path(paired))[0] == monitor.WS_FORBIDDEN
    assert _close_code(server["anon"], _ws_path(paired))[0] == monitor.WS_UNAUTHENTICATED
    assert not machine.connected.is_set()
    assert secondary["conn"].execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'storage_monitor_tunnel'"
    ).fetchone()["n"] == 0


def test_remote_monitor_rejection_arrives_after_accept(server, secondary, paired, machine):
    """‏#904 סעיף 4: גם דרך המשני — הכניסה ל-with מצליחה (101), והקוד עם
    הסיבה העברית מגיע ב-receive, לא כ-HTTP 403 אילם. ‏`_close_code` סובלני
    לשני המצבים, ולכן הבדיקה כאן היא שהכניסה **אינה** זורקת."""
    with server["admin"].websocket_connect(_ws_path("nope")) as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_bytes()
    assert (e.value.code, e.value.reason) == (4404, "שרת משני לא קיים")
    assert not machine.connected.is_set()


def test_remote_monitor_unknown_node_and_mac(server, secondary, paired, machine):
    assert _close_code(server["admin"], _ws_path("nope"))[0] == 4404
    assert _close_code(server["admin"], _ws_path(paired, "not-a-mac"))[0] == 4404
    # ‏MAC תקין שהמשני אינו מכיר — הכשל הוא של המשני, ומגיע עם הקוד שלו.
    code, reason = _close_code(server["admin"], _ws_path(paired, "aa:bb:cc:dd:ee:99"))
    assert code == 4404 and "מוכרת" in reason
    assert not machine.connected.is_set()


def test_remote_monitor_machine_without_secret_is_refused_by_the_secondary(
        server, secondary, paired, monkeypatch):
    fake = FakeRfbServer()
    fake.start()
    monkeypatch.setattr(monitor, "MONITOR_PORT", fake.port)
    _register(secondary["conn"], "127.0.0.1", secret=None)
    code, reason = _close_code(server["admin"], _ws_path(paired))
    assert code == 4512 and "סוד" in reason
    assert not fake.connected.is_set()                       # לפני TCP
    fake.stop()


def test_remote_monitor_machine_rejecting_the_secret(server, secondary, paired,
                                                     monkeypatch):
    fake = FakeRfbServer(result=1, reason=b"bad secret")
    fake.start()
    monkeypatch.setattr(monitor, "MONITOR_PORT", fake.port)
    _register(secondary["conn"], "127.0.0.1")
    code, reason = _close_code(server["admin"], _ws_path(paired))
    assert code == 4512 and "bad secret" in reason
    fake.stop()


def test_remote_monitor_secondary_down(server, secondary, paired, machine):
    secondary["server"].stop()
    code, _reason = _close_code(server["admin"], _ws_path(paired))
    assert code == 4502
    assert not machine.connected.is_set()


def test_remote_monitor_wrong_pin_never_reaches_the_machine(server, secondary, paired,
                                                            machine):
    """‏SPKI מוצמד שאינו של המשני → החיבור נדחה בראשי אחרי ה-handshake ולפני
    בקשה; המשני לא פתח TCP למכונה ולא רשם מנהרה."""
    conn = server["ctx"].conn
    with db._write_lock, db.writing(conn):
        conn.execute("UPDATE storage_nodes SET pinned_spki = ? WHERE id = ?",
                     ("ab" * 32, paired))
    code, reason = _close_code(server["admin"], _ws_path(paired))
    assert code == 4502 and "SPKI" in reason
    assert not machine.connected.is_set()
    assert secondary["conn"].execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'storage_monitor_tunnel'"
    ).fetchone()["n"] == 0


def test_remote_monitor_second_tunnel_to_same_machine_is_busy(server, secondary,
                                                              paired, machine):
    admin = server["admin"]
    with admin.websocket_connect(_ws_path(paired)) as ws:
        _browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
        code, reason = _close_code(server["admin"], _ws_path(paired))
        assert code == 4409 and "כבר פתוח" in reason
    assert machine.closed.wait(5)


def test_remote_monitor_refused_on_a_secondary_server(server, secondary, paired, machine):
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    code, _reason = _close_code(server["admin"], _ws_path(paired))
    assert code == 4409
    assert not machine.connected.is_set()
