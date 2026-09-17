"""‏#988 — נראות ראשי/משני לפי תפקיד: ארבעת הכללים של נדב (17/09) בטבלה אחת.

1. admin בראשי רואה גם את המשניים (סניפים, מכונות המשני, מוניטור דרך
   הראשי, העברות).
2. deploy בראשי רואה רק מה שמותר לו (אימג'ים, סבבים) **מהראשי בלבד** —
   לא סניפים, לא מכונות משני, לא העברות.
3. admin במשני רואה רק את השרת שלו — אין עץ סניפים, ואין "ראשי" מלבד
   מצב ה-pairing.
4. deploy במשני — אותה הגבלה כמו deploy בראשי, על המשני.

הטבלה היא **תפקיד-שרת × תפקיד-משתמש × מסלול**, וכל תא הוא קוד תשובה
מהשרת — לא הסתרה בקונסולה. ההסתרה בעץ (#954 גל 7) נבנית לפי אותם
קודים: ‏403 (deploy) ו-409 (משני) שניהם "אין צומת", ולכן הקודים כאן
הם החוזה של הקונסולה, לא פרט מימוש.

‏WebSocket: השער נסגר **אחרי** accept (#904) בקוד 4403/4409, ולכן התא
הוא קוד הסגירה. deploy נסגר לפני שהשרת פותח TCP/מנהרה לאיש.
"""

from __future__ import annotations

import pytest

from server import storage_nodes
from server.db import now_iso, set_setting

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except Exception:                                             # noqa: BLE001
    TestClient = None

NODE = "node1"
MAC = "aa:bb:cc:dd:ee:01"
LOGINS = {"admin": ("noc", "admin-pass-123"),
          "deploy": ("labtech", "deploy-pass-1")}


@pytest.fixture()
def clients(server):
    """לקוח מחובר לכל תפקיד, מ-loopback — כדי ש-``/storage-pairing-window``
    (‏loopback בלבד, #740) ייבחן על התפקיד ולא על כתובת ה-peer.
    משני אחד רשום (פעיל, עם credential שאינו קיים — אין רשת בטסט)."""
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref,"
        " enrolled_at) VALUES (?, ?, ?, ?, ?)",
        (NODE, "סניף א'", "https://node1.example:8443/api/interserver/v1",
         "cred:missing", now_iso()))
    conn.commit()
    out = {}
    for role, (user, pw) in LOGINS.items():
        client = TestClient(server["app"], client=("127.0.0.1", 40000))
        assert client.post("/api/console/login",
                           json={"username": user, "password": pw}).status_code == 200
        out[role] = client
    return out


def _http(client, method, path):
    resp = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    return resp.status_code


def _ws(client, _method, path):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(path) as ws:
            ws.receive_bytes()
    return e.value.code


#: (מסלול, ‏method, ‏קורא) → תאים: ראשי/admin, ראשי/deploy, משני/admin, משני/deploy.
#:
#: ‏admin בראשי "עובר את השער" — הקוד שם הוא מה שהמסלול עונה **אחרי**
#: הרשאה: ‏400 = גוף ריק ל-enroll/preview, ‏409 = חלון pairing אינו של
#: ראשי, ‏4500 = המשני הרשום בטסט אינו ניתן לפנייה (credential חסר).
#: ‏admin במשני עובר את השער רק בחלון ה-pairing (כלל 3) ובמוניטור המקומי.
ROWS = [
    ("/api/console/storage-nodes", "get", _http,
     (200, 403, 409, 403)),
    ("/api/console/storage-node-groups", "get", _http,
     (200, 403, 409, 403)),
    ("/api/console/storage-transfers", "get", _http,
     (200, 403, 409, 403)),
    (f"/api/console/storage-nodes/{NODE}/transfers", "get", _http,
     (200, 403, 409, 403)),
    (f"/api/console/storage-nodes/{NODE}/machines", "get", _http,
     (200, 403, 409, 403)),
    ("/api/console/storage-nodes/enroll/preview", "post", _http,
     (400, 403, 409, 403)),
    ("/api/console/storage-pairing-window", "get", _http,
     (409, 403, 200, 403)),
    ("/api/console/monitor/machines", "get", _http,
     (200, 403, 200, 403)),
    (f"/api/console/monitor/{MAC}", "ws", _ws,
     (4404, 4403, 4404, 4403)),
    (f"/api/console/storage-nodes/{NODE}/monitor/{MAC}", "ws", _ws,
     (4500, 4403, 4409, 4403)),
    # מה ש-deploy **כן** רואה — אימג'ים וסבבים — מהשרת שהוא מחובר אליו.
    ("/api/console/images", "get", _http,
     (200, 200, 200, 200)),
    ("/api/console/overview", "get", _http,
     (200, 200, 200, 200)),
]

COLUMNS = (("standalone", "admin"), ("standalone", "deploy"),
           ("secondary", "admin"), ("secondary", "deploy"))


@pytest.mark.parametrize(
    "row", ROWS, ids=[f"{m}:{p.removeprefix('/api/console/')}" for p, m, _, _ in ROWS])
def test_visibility_matrix(server, clients, row):
    """שורה אחת מהטבלה — ארבעת התאים שלה על שרת אחד, ומדווחים **כל** תא
    שסטה, לא רק הראשון. (פרמטר לכל תא היה בונה שרת מלא 52 פעמים.)"""
    path, method, call, expected = row
    mismatched = []
    for (server_role, user_role), want in zip(COLUMNS, expected):
        set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, server_role)
        got = call(clients[user_role], method, path)
        if got != want:
            mismatched.append(f"{server_role}/{user_role}: got {got}, want {want}")
    assert not mismatched, f"{method.upper()} {path}: " + "; ".join(mismatched)


@pytest.mark.parametrize(("server_role", "user_role", "caps"), [
    ("standalone", "admin",
     {"interbranch_transfer": True, "enroll_secondary": True, "open_local_pairing": False}),
    ("standalone", "deploy",
     {"interbranch_transfer": False, "enroll_secondary": False, "open_local_pairing": False}),
    ("secondary", "admin",
     {"interbranch_transfer": False, "enroll_secondary": False, "open_local_pairing": True}),
    ("secondary", "deploy",
     {"interbranch_transfer": False, "enroll_secondary": False, "open_local_pairing": False}),
])
def test_me_capabilities_follow_the_same_table(server, clients, server_role, user_role, caps):
    """הדגלים שמהם הקונסולה בונה את העץ — אותה טבלה, מהצד של ``/me``.
    ‏admin בראשי מקבל ``interbranch_transfer`` כי יש משני פעיל אחד."""
    set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, server_role)
    me = clients[user_role].get("/api/console/me").json()
    assert me["role"] == user_role
    assert me["capabilities"] == caps
