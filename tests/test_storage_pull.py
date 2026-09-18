"""Storage Nodes — משיכת אימג' משני→ראשי (#1071).

שני שרתים אמיתיים על loopback כמו ``test_storage_transfer``: הראשי הוא
``create_app``, המשני הוא ``InterserverTLSServer``. היוזם תמיד הראשי;
המשני רק עונה GET. sha256 מאומת **לפני** שהאימג' נכנס לספריית הראשי.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from conftest import MANIFEST_256, PARTITION_BYTES, write_image
from server import (db, interserver_api, interserver_auth, storage_client,
                    storage_nodes, storage_transfer)
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
PULL = {
    **MANIFEST_256,
    "id": "img_c0ffee",
    "name": "Haifa Local",
    "folder": "Haifa",
}
PULL_ID = PULL["id"]


class _Ctx:
    def __init__(self, conn, library, data_dir):
        self.conn = conn
        self.library = library
        self.data_dir = data_dir


@pytest.fixture()
def secondary(tmp_path):
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = admin.get(f"/api/console/storage-nodes/{nid}/transfers").json()
        row = next(r for r in rows if r["id"] == tid)
        if row["state"] in ("done", "failed"):
            return row
        time.sleep(0.1)
    raise AssertionError(f"ההעברה לא הסתיימה תוך {timeout}s: {row}")


def _incoming(server) -> Path:
    return Path(server["ctx"].library.root) / storage_transfer.INCOMING


def _peer(cert_pem, exporter=b"E" * 32, ver="TLSv1.3"):
    return interserver_api.TlsPeer(tls_version=ver,
                                   cert_der=interserver_auth._to_der(cert_pem),
                                   exporter=exporter)


@pytest.fixture()
def paired_ctx(tmp_path):
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


# --- קצה-לקצה: האימג' נוחת בראשי אחרי אימות sha256 --------------------------

def test_pull_end_to_end_lands_verified_copy(server, secondary, paired):
    write_image(secondary["root"], PULL)
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID})
    assert resp.status_code == 200, resp.text
    tid = resp.json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "done", row
    assert row["direction"] == "pull"
    assert row["bytes_sent"] == row["bytes_total"] > 0
    assert row["error"] is None

    got = server["ctx"].library.scan()
    assert PULL_ID in got
    src = secondary["root"] / PULL_ID
    dst = Path(got[PULL_ID]["_dir"])
    assert (dst / "p1.esp.pcl.zst").read_bytes() == (src / "p1.esp.pcl.zst").read_bytes()
    assert (dst / "p3.win.pcl.zst").read_bytes() == (src / "p3.win.pcl.zst").read_bytes()
    assert json.loads((dst / "manifest.json").read_text(encoding="utf-8")) == \
        json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    leftover = _incoming(server) / PULL_ID
    assert not leftover.exists()
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'storage_transfer_%'")]
    assert events == ["storage_transfer_start", "storage_transfer_done"]
    listed = admin.get(f"/api/console/storage-nodes/{paired}/images").json()
    assert listed["connected"] is True
    assert any(i["id"] == PULL_ID for i in listed["images"])


def test_forged_sha_on_secondary_is_refused_and_leaves_no_incoming(
        server, secondary, paired):
    write_image(secondary["root"], PULL)
    (secondary["root"] / PULL_ID / "p1.esp.pcl.zst").write_bytes(b"X" * 20)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL_ID}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed", row
    assert "sha256" in row["error"]
    assert PULL_ID not in server["ctx"].library.scan()
    leftover = _incoming(server) / PULL_ID
    assert not leftover.exists()
    parent = _incoming(server)
    assert (not parent.exists()) or PULL_ID not in {p.name for p in parent.iterdir()}


def test_existing_id_on_primary_is_409_not_overwritten(server, secondary, paired):
    write_image(secondary["root"], MANIFEST_256)
    marker = Path(server["ctx"].library.get(IMAGE)["_dir"]) / "p1.esp.pcl.zst"
    marker.write_bytes(b"DO-NOT-CLOBBER")
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": IMAGE})
    assert resp.status_code == 409, resp.text
    assert "כבר קיים" in resp.text
    assert marker.read_bytes() == b"DO-NOT-CLOBBER"


def test_file_range_resume_completes_after_disconnect(server, secondary, paired,
                                                      tmp_path, monkeypatch):
    write_image(secondary["root"], PULL)
    real = storage_client.PinnedMTLSClient.get_stream
    calls = {"n": 0}

    def flaky(self, path, token, dest, **kwargs):
        dest = Path(dest)
        if "/files/" in path and calls["n"] == 0:
            calls["n"] += 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(PARTITION_BYTES[:8])
            raise storage_client.InterserverClientError("נותק אחרי 8 בייטים")
        return real(self, path, token, dest, **kwargs)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", flaky)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL_ID}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed"
    leftover = _incoming(server) / PULL_ID
    assert leftover.exists()
    first = leftover / "p1.esp.pcl.zst"
    assert first.read_bytes() == PARTITION_BYTES[:8]

    sizes = []

    def tracking(self, path, token, dest, **kwargs):
        dest = Path(dest)
        sizes.append((path, dest.stat().st_size if dest.is_file() else 0))
        return real(self, path, token, dest, **kwargs)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", tracking)
    tid2 = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID}).json()["id"]
    row2 = _wait(admin, paired, tid2)
    assert row2["state"] == "done", row2
    assert any(sz == 8 for _path, sz in sizes), sizes
    got = Path(server["ctx"].library.get(PULL_ID)["_dir"])
    assert (got / "p1.esp.pcl.zst").read_bytes() == PARTITION_BYTES
    assert hashlib.sha256(PARTITION_BYTES).hexdigest() == \
        server["ctx"].library.get(PULL_ID)["partitions"][0]["sha256"]


def test_get_stream_honors_range_against_the_terminator(server, secondary, paired,
                                                        tmp_path):
    write_image(secondary["root"], PULL)
    node = storage_nodes.node_row(server["ctx"].conn, paired)
    client, token = storage_nodes.node_client(
        server["ctx"].conn, tmp_path / "data", node)
    dest = tmp_path / "partial"
    dest.write_bytes(PARTITION_BYTES[:5])
    with client:
        n = client.get_stream(f"/images/{PULL_ID}/files/p1.esp.pcl.zst", token, dest)
    assert dest.read_bytes() == PARTITION_BYTES
    assert n == len(PARTITION_BYTES)


def test_files_name_outside_manifest_is_404(paired_ctx):
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_file_plan(
            ctx, peer, image_id=PULL_ID, filename="not-in-manifest.bin",
            **_creds(token))
    assert e.value.status == 404
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_file_plan(
            ctx, peer, image_id=PULL_ID, filename="../evil",
            **_creds(token))
    assert e.value.status == 404


def test_secondary_exposes_no_new_post_or_put(paired_ctx):
    if TestClient is None:
        pytest.skip("fastapi required")
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    app = interserver_api.create_interserver_app(ctx, lambda _req: peer)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "ImageCtl-Protocol-Version": "2.1"}
    listed = client.get("/api/interserver/v1/images", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(i["id"] == PULL_ID for i in listed.json())
    man = client.get(f"/api/interserver/v1/images/{PULL_ID}/manifest", headers=headers)
    assert man.status_code == 200 and man.json()["id"] == PULL_ID
    body = client.get(f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst",
                      headers=headers)
    assert body.status_code == 200 and body.content == PARTITION_BYTES
    ranged = client.get(
        f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst",
        headers={**headers, "Range": "bytes=5-"})
    assert ranged.status_code == 206
    assert ranged.content == PARTITION_BYTES[5:]
    missing = client.get(
        f"/api/interserver/v1/images/{PULL_ID}/files/nope.bin", headers=headers)
    assert missing.status_code == 404
    for method, path in (
        ("post", "/api/interserver/v1/images"),
        ("put", "/api/interserver/v1/images"),
        ("post", f"/api/interserver/v1/images/{PULL_ID}/manifest"),
        ("put", f"/api/interserver/v1/images/{PULL_ID}/manifest"),
        ("post", f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst"),
        ("put", f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst"),
    ):
        resp = getattr(client, method)(path, headers=headers)
        assert resp.status_code in (404, 405), (method, path, resp.status_code, resp.text)


def test_pull_route_guards(server, paired):
    admin, deploy = server["admin"], server["deploy"]
    path = f"/api/console/storage-nodes/{paired}/pull"
    assert deploy.post(path, json={"image_id": PULL_ID}).status_code == 403
    assert deploy.get(f"/api/console/storage-nodes/{paired}/images").status_code == 403
    assert admin.post("/api/console/storage-nodes/nope/pull",
                      json={"image_id": PULL_ID}).status_code == 404
    assert admin.post(path, json={"image_id": "not-an-id"}).status_code == 400


def test_pull_refused_on_a_secondary_server(server, paired):
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/pull",
                                json={"image_id": PULL_ID})
    assert resp.status_code == 409
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_transfer.start_pull(server["ctx"], "x", paired, PULL_ID,
                                    ("noc", "admin"), run_in_thread=False)


def test_cancel_push_is_refused(server, paired, tmp_path):
    tid = storage_transfer.start_transfer(
        server["ctx"], tmp_path / "data", paired, IMAGE, ("noc", "admin"),
        run_in_thread=False)
    with pytest.raises(storage_transfer.TransferError) as e:
        storage_transfer.cancel_transfer(server["ctx"].conn, tid, ("noc", "admin"))
    assert e.value.status == 409


def test_list_images_and_manifest_from_disk(paired_ctx):
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    rows = interserver_api.list_images(ctx, peer, **_creds(token))
    assert len(rows) == 1
    assert rows[0]["id"] == PULL_ID
    assert rows[0]["name"] == "Haifa Local"
    assert rows[0]["size_bytes"] == len(PARTITION_BYTES) * 2
    assert "p1.esp.pcl.zst" in rows[0]["sha256"]
    man = interserver_api.image_manifest(
        ctx, peer, image_id=PULL_ID, **_creds(token))
    assert man["id"] == PULL_ID and "_dir" not in man
