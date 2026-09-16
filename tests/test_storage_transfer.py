"""Storage Nodes — העברת אימג' ראשי→משני (#655 v1).

**שני שרתים אמיתיים על loopback**: הראשי הוא האפליקציה המלאה (``server``
fixture, standalone, עם ספריית אימג'ים), והמשני הוא ``InterserverTLSServer``
(‏mTLS 1.3 של pyOpenSSL) עם ספרייה משלו. ה-pairing נעשה באמת (חלון + קוד
+ טוקן + קובץ 0600 + רשומה), ואז הקונסולה של הראשי מזמינה העברה, משימת
הרקע דוחפת, והמשני מכניס דרך ``import_tar``.

מה שמוכח כאן: (א) הקובץ שנוחת במשני זהה בייט-בייט; (ב) בייט פגום בזרם
**לא** נכנס לספריית המשני ומדווח בראשי כ-``failed`` עם הסיבה; (ג) ניתוק
באמצע = ``failed`` גלוי, לא "נתקע", ובלי תיקייה חלקית במשני; (ד) RBAC
וההיררכיה החד-כיוונית נאכפים בשכבת ה-service וב-route.
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from conftest import MANIFEST_256, PARTITION_BYTES, write_image
from server import (archive, db, interserver_api, interserver_auth,
                    storage_client, storage_nodes, storage_transfer)
from server.images import ImageLibrary

try:
    from fastapi.testclient import TestClient
except Exception:                                             # noqa: BLE001
    TestClient = None


pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

IMAGE = MANIFEST_256["id"]


class _Ctx:
    def __init__(self, conn, library, data_dir):
        self.conn = conn
        self.library = library
        self.data_dir = data_dir


@pytest.fixture()
def secondary(tmp_path):
    """שרת משני חי: מאזין mTLS על 127.0.0.1, ספריית אימג'ים ריקה משלו."""
    conn = db.connect(tmp_path / "sec.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    root = tmp_path / "sec_images"
    root.mkdir()
    ctx = _Ctx(conn, ImageLibrary(root), str(tmp_path / "sec"))
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec",
                                          host_sans=["127.0.0.1"])
    server = interserver_api.InterserverTLSServer(
        ctx, "127.0.0.1", 0, ident["cert_pem"], ident["key_pem"],
        data_dir=str(tmp_path / "sec")).start()
    yield {"conn": conn, "ctx": ctx, "ident": ident, "server": server,
           "port": server.port, "root": root}
    server.stop()


@pytest.fixture()
def paired(server, secondary, tmp_path):
    """‏pairing אמיתי: הראשי נרשם למשני, כותב טוקן 0600 ורושם את המשני.
    מחזיר את מזהה רשומת המשני על הראשי."""
    data_dir = tmp_path / "data"
    ident = storage_nodes.ensure_identity(server["ctx"].conn, data_dir,
                                          host_sans=["127.0.0.1"])
    code = interserver_auth.open_pairing_window(secondary["conn"])
    url = f"https://127.0.0.1:{secondary['port']}/api/interserver/v1"
    result = storage_client.pair_secondary(
        url, code=code, expected_secondary_spki=secondary["ident"]["server_spki"],
        cert_pem=ident["cert_pem"], key_pem=ident["key_pem"],
        primary_id=ident["node_id"])
    cred_dir = data_dir / "secondaries"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_path = cred_dir / f"{result['secondary_id']}.token"
    interserver_auth.write_credential_0600(cred_path, result["token"])
    nid = storage_nodes.enroll_node(
        server["ctx"].conn, ("noc", "admin"), label="חיפה", base_url=url,
        node_id=result["secondary_id"], pinned_spki=secondary["ident"]["server_spki"],
        client_cert_ref=interserver_auth.certificate_ref(ident["cert_pem"]),
        credential_ref=str(cred_path), protocol_version="2.1")
    return nid


def _wait(admin, nid, tid, timeout=30.0) -> dict:
    """ממתין עד שההעברה יוצאת ממצב פעיל, ומחזיר את השורה."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = admin.get(f"/api/console/storage-nodes/{nid}/transfers").json()
        row = next(r for r in rows if r["id"] == tid)
        if row["state"] in ("done", "failed"):
            return row
        time.sleep(0.1)
    raise AssertionError(f"ההעברה לא הסתיימה תוך {timeout}s: {row}")


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)).replace("\\", "/"): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


# --- (א) קצה-לקצה: האימג' נוחת זהה, אחרי אימות sha256 במשני ------------------

def test_transfer_end_to_end_lands_verified_copy(server, secondary, paired):
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/transfer",
                      json={"image_id": IMAGE})
    assert resp.status_code == 200, resp.text
    tid = resp.json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "done", row
    assert row["bytes_sent"] == row["bytes_total"] > 0
    assert row["error"] is None

    # המשני: האימג' בספרייה, מהדיסק (עיקרון 3), והקבצים זהים בייט-בייט.
    got = secondary["ctx"].library.scan()
    assert IMAGE in got
    src = Path(server["ctx"].library.get(IMAGE)["_dir"])
    dst = Path(got[IMAGE]["_dir"])
    assert _tree(dst) == _tree(src)
    assert dst.parent == secondary["root"]
    # לא נשאר קובץ זמני/אזור ביניים במשני.
    leftovers = [p.name for p in secondary["root"].iterdir() if p.name.startswith(".")
                 or p.suffix == ".transfer"]
    assert leftovers == []
    # יומן: התחלה+סיום בראשי, קליטה במשני.
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'storage_transfer_%'")]
    assert events == ["storage_transfer_start", "storage_transfer_done"]
    assert secondary["conn"].execute(
        "SELECT COUNT(*) AS n FROM journal WHERE event = 'storage_image_received'"
    ).fetchone()["n"] == 1
    # ההעברה נראית גם ברשימה הכללית.
    assert any(t["id"] == tid for t in admin.get("/api/console/storage-transfers").json())


def test_tar_size_matches_the_stream(server):
    directory = Path(server["ctx"].library.get(IMAGE)["_dir"])
    assert archive.tar_size(directory) == len(b"".join(archive.tar_stream(directory, IMAGE)))


# --- (ב) בייט פגום בזרם: לא נכנס לספרייה, ומדווח ------------------------------

def test_corrupted_stream_is_rejected_at_the_secondary(server, secondary, paired,
                                                        monkeypatch):
    """הופכים בייט אחד בתוך קובץ המחיצה תוך כדי הזרמה. ה-tar עצמו תקין,
    המניפסט תקין, ורק ה-sha256 של המחיצה אינו תואם — וזה בדיוק מה שאסור
    שיעבור (עיקרון 6). הבקרה השלילית: בלי בדיקת ה-sha256 ב-import_tar
    האימג' הפגום היה נכנס לספריית המשני כאימג' כשר."""
    real = archive.tar_stream

    def corrupting(directory, arcname):
        for chunk in real(directory, arcname):
            if PARTITION_BYTES in chunk:
                chunk = chunk.replace(PARTITION_BYTES, b"X" + PARTITION_BYTES[1:], 1)
            yield chunk

    monkeypatch.setattr(archive, "tar_stream", corrupting)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/transfer",
                     json={"image_id": IMAGE}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed", row
    assert "sha256" in row["error"]
    assert secondary["ctx"].library.scan() == {}
    assert not (secondary["root"] / IMAGE).exists()
    assert [p for p in secondary["root"].iterdir()] == []
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'storage_transfer_%'")]
    assert events == ["storage_transfer_start", "storage_transfer_failed"]


# --- (ג) ניתוק/קטיעה: כשל גלוי, לא "נתקע", בלי שאריות -------------------------

def test_source_failure_mid_stream_is_a_visible_failure(server, secondary, paired,
                                                        monkeypatch):
    real = archive.tar_stream

    def dying(directory, arcname):
        gen = real(directory, arcname)
        yield next(gen)
        raise OSError("הדיסק של הראשי נעלם באמצע")

    monkeypatch.setattr(archive, "tar_stream", dying)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/transfer",
                     json={"image_id": IMAGE}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed"
    assert "נעלם באמצע" in row["error"]
    assert secondary["ctx"].library.scan() == {}
    assert [p for p in secondary["root"].iterdir()] == []


def test_truncated_body_at_the_secondary_is_named_and_cleaned(tmp_path):
    """שכבת ה-service במשני: גוף קצר מ-Content-Length = כשל בשם עם המספרים,
    בלי import ובלי קובץ זמני שנשאר."""
    root = tmp_path / "root"
    root.mkdir()
    ctx = _Ctx(None, ImageLibrary(root), None)
    plan = interserver_api.ReceivePlan(image_id=IMAGE, content_length=10_000, root=root)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image(ctx, plan, [b"x" * 4096])
    assert e.value.status == 400
    assert "4096" in e.value.detail and "10000" in e.value.detail
    assert list(root.iterdir()) == []


# --- (ד) דחיות לפני שליחה, RBAC וההיררכיה ---------------------------------

def test_already_present_at_secondary_fails_before_sending(server, secondary, paired):
    write_image(secondary["root"], MANIFEST_256)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/transfer",
                     json={"image_id": IMAGE}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed" and "כבר קיים" in row["error"]
    assert row["bytes_sent"] == 0


def test_transfer_route_guards(server, paired):
    admin, deploy = server["admin"], server["deploy"]
    path = f"/api/console/storage-nodes/{paired}/transfer"
    assert deploy.post(path, json={"image_id": IMAGE}).status_code == 403
    assert deploy.get(f"/api/console/storage-nodes/{paired}/transfers").status_code == 403
    assert admin.post("/api/console/storage-nodes/nope/transfer",
                      json={"image_id": IMAGE}).status_code == 404
    assert admin.post(path, json={"image_id": "img_zzzzzz"}).status_code == 404
    assert admin.get("/api/console/storage-nodes/nope/transfers").status_code == 404
    storage_nodes.set_node_disabled(server["ctx"].conn, paired, True, ("noc", "admin"))
    assert admin.post(path, json={"image_id": IMAGE}).status_code == 409


def test_transfer_refused_on_a_secondary_server(server, paired):
    """חד-כיווני: משני אינו דוחף — גם עם רשומה ואימג' קיימים → 409."""
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/transfer",
                                json={"image_id": IMAGE})
    assert resp.status_code == 409
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_transfer.start_transfer(server["ctx"], "x", paired, IMAGE,
                                        ("noc", "admin"), run_in_thread=False)


def test_duplicate_active_transfer_is_refused(server, paired, tmp_path):
    ctx = server["ctx"]
    storage_transfer.start_transfer(ctx, tmp_path / "data", paired, IMAGE,
                                    ("noc", "admin"), run_in_thread=False)
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/transfer",
                                json={"image_id": IMAGE})
    assert resp.status_code == 409


# --- (ה) שומרי הכניסה במשני, בשכבת ה-service -------------------------------

def _peer(cert_pem, exporter=b"E" * 32, ver="TLSv1.3"):
    return interserver_api.TlsPeer(tls_version=ver,
                                   cert_der=interserver_auth._to_der(cert_pem),
                                   exporter=exporter)


@pytest.fixture()
def paired_ctx(tmp_path):
    """משני (service בלבד, בלי TLS חי) עם אב רשום; מחזיר (ctx, peer, token)."""
    conn = db.connect(tmp_path / "s.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    root = tmp_path / "root"
    root.mkdir()
    ctx = _Ctx(conn, ImageLibrary(root), str(tmp_path / "s"))
    ident = storage_nodes.ensure_identity(conn, tmp_path / "s", host_sans=["127.0.0.1"])
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    peer = _peer(primary)
    handles = {}
    begin = interserver_api.pair_begin(conn, handles, peer, {
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"],
        "primary_id": "p1"}, data_dir=str(tmp_path / "s"))
    code = interserver_auth.open_pairing_window(conn)
    comp = interserver_api.pair_complete(conn, handles, peer, {
        "handle": begin["handle"], "code": code, "protocol_version": "2.1"})
    return ctx, peer, comp["token"]


def _creds(token):
    return {"token": token, "protocol_version": "2.1"}


def test_ingress_wrong_token_and_cookie_and_role_are_rejected(paired_ctx):
    ctx, peer, token = paired_ctx
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_present(ctx, peer, image_id=IMAGE, token="0" * 64,
                                      protocol_version="2.1")
    assert e.value.status == 401
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image_precheck(
            ctx, peer, image_id=IMAGE, content_length=10, has_console_cookie=True,
            **_creds(token))
    assert e.value.status == 401
    db.set_setting(ctx.conn, storage_nodes.ROLE_KEY, "standalone")
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image_precheck(
            ctx, peer, image_id=IMAGE, content_length=10, **_creds(token))
    assert e.value.status == 409


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "img with space"])
def test_ingress_bad_image_id_rejected(paired_ctx, bad):
    ctx, peer, token = paired_ctx
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image_precheck(
            ctx, peer, image_id=bad, content_length=10, **_creds(token))
    assert e.value.status == 400


def test_ingress_present_and_length_and_space_prechecks(paired_ctx, monkeypatch):
    ctx, peer, token = paired_ctx
    assert interserver_api.image_present(
        ctx, peer, image_id=IMAGE, **_creds(token))["present"] is False
    for length in (None, "abc", 0, -5):
        with pytest.raises(interserver_api.PairError) as e:
            interserver_api.receive_image_precheck(
                ctx, peer, image_id=IMAGE, content_length=length, **_creds(token))
        assert e.value.status == 411
    # אין מקום: 507, לא "ננסה".
    monkeypatch.setattr(interserver_api.shutil, "disk_usage",
                        lambda _p: type("U", (), {"free": 1, "total": 1})())
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image_precheck(
            ctx, peer, image_id=IMAGE, content_length=10, **_creds(token))
    assert e.value.status == 507
    write_image(Path(ctx.library.root), MANIFEST_256)
    assert interserver_api.image_present(
        ctx, peer, image_id=IMAGE, **_creds(token))["present"] is True
    monkeypatch.undo()
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image_precheck(
            ctx, peer, image_id=IMAGE, content_length=10, **_creds(token))
    assert e.value.status == 409


def test_ingress_manifest_id_must_match_the_path(paired_ctx, tmp_path):
    """‏tar כשר של אימג' A שנשלח בנתיב של B — נכנס לרגע דרך import_tar
    (‏sha256 תקין), ומוסר מיד: לא נשאר בספרייה ולא בשם מטעה."""
    ctx, peer, token = paired_ctx
    src = tmp_path / "src"
    write_image(src, MANIFEST_256)
    body = b"".join(archive.tar_stream(src / IMAGE, IMAGE))
    plan = interserver_api.receive_image_precheck(
        ctx, peer, image_id="img_0ffe01", content_length=len(body), **_creds(token))
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.receive_image(ctx, plan, [body])
    assert e.value.status == 400 and "img_0ffe01" in e.value.detail
    assert ctx.library.scan() == {}
    assert list(Path(ctx.library.root).iterdir()) == []


def test_ingress_receive_via_fastapi_routes(paired_ctx, tmp_path):
    """שכבת ה-routes של האפליקציה המבודדת: PUT זורם → 200 ונוכחות; PUT עם
    בייט פגום → 400 ושום דבר בספרייה."""
    if TestClient is None:
        pytest.skip("fastapi required")
    ctx, peer, token = paired_ctx
    app = interserver_api.create_interserver_app(ctx, lambda _req: peer)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "ImageCtl-Protocol-Version": "2.1"}
    src = tmp_path / "src"
    write_image(src, MANIFEST_256)
    body = b"".join(archive.tar_stream(src / IMAGE, IMAGE))
    bad = body.replace(PARTITION_BYTES, b"X" + PARTITION_BYTES[1:], 1)
    resp = client.put(f"/api/interserver/v1/images/{IMAGE}", content=bad, headers=headers)
    assert resp.status_code == 400 and "sha256" in resp.text
    assert ctx.library.scan() == {}
    resp = client.put(f"/api/interserver/v1/images/{IMAGE}", content=body, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == IMAGE
    assert client.get(f"/api/interserver/v1/images/{IMAGE}", headers=headers).json()["present"] is True
    assert client.get(f"/api/interserver/v1/images/{IMAGE}",
                      headers={"ImageCtl-Protocol-Version": "2.1"}).status_code == 401
    assert hashlib.sha256(PARTITION_BYTES).hexdigest() == \
        ctx.library.get(IMAGE)["partitions"][0]["sha256"]
