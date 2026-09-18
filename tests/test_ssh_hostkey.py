"""‏#1080 — מפתח ה-host של התחנה ב-hello, known_hosts, וחיווי בכרטיס.

צד הסוכן (bind + sshd_json) נבדק ב-`test_agent.py`. כאן: הצורה הקנונית,
היסטוריה מגורסת, hello בלי השדה → null, known_hosts שורה אחת ל-IP,
שינוי באותו boot_id → כתום, אתחול → אפור, וכל ssh של השרת בודק מפתח.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import setup_classroom  # noqa: E402
from server import ssh_hostkey  # noqa: E402

MAC = "b4:2e:99:07:1a:c4"
IP = "10.44.12.187"
IP2 = "10.44.12.188"

PUB_A = ("ssh-ed25519 "
         "AAAAC3NzaC1lZDI1NTE5AAAAIAABAgMEBQYHCAkKCwwNDg8QERITFBUWFxgZGhscHR4f")
FP_A = "SHA256:ZkAslGjFiUHdGf/WUL8rQvkib4PTvQatUV0OUQSncCA"
PUB_B = ("ssh-ed25519 "
         "AAAAC3NzaC1lZDI1NTE5AAAAIBERERERERERERERERERERERERERERERERERERERERER")
FP_B = "SHA256:SQfC+vTbLURn9cTkVxIS8fGQ3FKNAJWeB0o139+gV4M"

BOOT_1 = "11111111-2222-3333-4444-555555555555"
BOOT_2 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

REPO = Path(__file__).resolve().parent.parent
SERVER_DIR = REPO / "server"


def _hk(pub, fp, boot=BOOT_1) -> dict:
    return {"type": "ed25519", "fingerprint": fp, "pubkey": pub,
            "boot_id": boot}


GOOD = _hk(PUB_A, FP_A)


def _hello(server, **fields):
    return server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": MAC, "all_macs": [MAC], "ip": IP,
        "disks": [], **fields})


def _machine(server) -> dict:
    return next(m for m in server["admin"].get("/api/console/machines").json()
                if m["mac"] == MAC)


def _data_dir(server) -> Path:
    return server["app"].state.data_dir


# --- הצורה ------------------------------------------------------------------


def test_a_well_formed_hostkey_keeps_type_fingerprint_and_pubkey():
    got = ssh_hostkey.well_formed(GOOD)
    assert got["type"] == "ed25519"
    assert got["fingerprint"] == FP_A
    assert got["pubkey"] == PUB_A
    assert got["boot_id"] == BOOT_1


def test_fingerprint_must_match_the_pubkey():
    """טביעה שקרית נזנחת — אחרת הכרטיס מציג מפתח וה-known_hosts מצמיד אחר."""
    assert ssh_hostkey.well_formed(_hk(PUB_A, FP_B)) is None


@pytest.mark.parametrize("bad", [
    None, [], "x", 5,
    {"type": "rsa", "fingerprint": FP_A, "pubkey": PUB_A},
    {"type": "ed25519", "fingerprint": FP_A},                  # pubkey חסר
    {"type": "ed25519", "pubkey": PUB_A},                      # fingerprint חסר
    {"type": "ed25519", "fingerprint": "md5:aa", "pubkey": PUB_A},
    {"type": "ed25519", "fingerprint": FP_A, "pubkey": "ssh-ed25519 AAAA"},
    {"type": "ed25519", "fingerprint": FP_A, "pubkey": PUB_A, "boot_id": 1},
], ids=lambda b: str(b)[:50])
def test_a_malformed_hostkey_is_none(bad):
    assert ssh_hostkey.well_formed(bad) is None


def test_a_comment_on_the_pubkey_is_stripped():
    got = ssh_hostkey.well_formed(_hk(PUB_A + " root@station", FP_A))
    assert got["pubkey"] == PUB_A


def test_missing_boot_id_is_null_not_rejected():
    raw = {"type": "ed25519", "fingerprint": FP_A, "pubkey": PUB_A}
    assert ssh_hostkey.well_formed(raw)["boot_id"] is None


# --- היסטוריה מגורסת --------------------------------------------------------


def test_the_same_hostkey_twice_is_one_row(server):
    conn = server["ctx"].conn
    assert ssh_hostkey.latest(conn, MAC) is None
    assert ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(GOOD)) is True
    assert ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(GOOD)) is False
    assert len(ssh_hostkey.history(conn, MAC)) == 1


def test_a_key_change_opens_a_new_version(server):
    conn = server["ctx"].conn
    ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(GOOD))
    later = ssh_hostkey.well_formed(_hk(PUB_B, FP_B, BOOT_1))
    assert ssh_hostkey.record(conn, MAC, later) is True
    hist = ssh_hostkey.history(conn, MAC)
    assert [h["ssh_hostkey"]["fingerprint"] for h in hist] == [FP_B, FP_A]


def test_a_change_in_the_same_boot_is_flagged_orange(server):
    conn = server["ctx"].conn
    ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(GOOD))
    ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(_hk(PUB_B, FP_B, BOOT_1)))
    latest = ssh_hostkey.latest(conn, MAC)
    assert latest["changed_in_boot"] is True
    assert latest["new_from_reboot"] is False


def test_a_change_after_reboot_is_grey_not_orange(server):
    conn = server["ctx"].conn
    ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(GOOD))
    ssh_hostkey.record(conn, MAC, ssh_hostkey.well_formed(_hk(PUB_B, FP_B, BOOT_2)))
    latest = ssh_hostkey.latest(conn, MAC)
    assert latest["changed_in_boot"] is False
    assert latest["new_from_reboot"] is True


def test_without_boot_id_a_change_is_not_called_the_same_boot(server):
    """בלי ראיה שזה אותו אתחול — לא כתום (עיקרון 5)."""
    conn = server["ctx"].conn
    a = ssh_hostkey.well_formed({"type": "ed25519", "fingerprint": FP_A,
                                 "pubkey": PUB_A})
    b = ssh_hostkey.well_formed({"type": "ed25519", "fingerprint": FP_B,
                                 "pubkey": PUB_B})
    ssh_hostkey.record(conn, MAC, a)
    ssh_hostkey.record(conn, MAC, b)
    latest = ssh_hostkey.latest(conn, MAC)
    assert latest["changed_in_boot"] is False
    assert latest["new_from_reboot"] is True


# --- hello + כרטיס המכונה ---------------------------------------------------


def test_hello_stores_the_hostkey_and_the_machine_card_shows_it(server):
    setup_classroom(server)
    assert _hello(server, ssh_hostkey=GOOD).status_code == 200
    m = _machine(server)
    assert m["ssh_hostkey"] == {"type": "ed25519", "fingerprint": FP_A,
                                "pubkey": PUB_A}
    assert m["ssh_hostkey_seen_at"]
    assert m["ssh_hostkey_changed_in_boot"] is False
    assert m["ssh_hostkey_new_from_reboot"] is False


def test_hello_without_the_field_leaves_null_not_an_error(server):
    setup_classroom(server)
    m = _machine(server)
    assert m["ssh_hostkey"] is None and m["ssh_hostkey_seen_at"] is None
    assert m["ssh_hostkey_changed_in_boot"] is False
    assert _hello(server).status_code == 200
    assert _machine(server)["ssh_hostkey"] is None


def test_a_malformed_hostkey_does_not_erase_the_previous_version(server):
    setup_classroom(server)
    assert _hello(server, ssh_hostkey=GOOD).status_code == 200
    assert _hello(server, ssh_hostkey={"type": "ed25519"}).status_code == 200
    assert _hello(server).status_code == 200
    assert _machine(server)["ssh_hostkey"]["fingerprint"] == FP_A


def test_hello_change_in_the_same_boot_sets_the_orange_flag(server):
    setup_classroom(server)
    _hello(server, ssh_hostkey=GOOD)
    _hello(server, ssh_hostkey=_hk(PUB_B, FP_B, BOOT_1))
    m = _machine(server)
    assert m["ssh_hostkey"]["fingerprint"] == FP_B
    assert m["ssh_hostkey_changed_in_boot"] is True
    assert m["ssh_hostkey_new_from_reboot"] is False


def test_hello_change_after_reboot_sets_the_grey_flag(server):
    setup_classroom(server)
    _hello(server, ssh_hostkey=GOOD)
    _hello(server, ssh_hostkey=_hk(PUB_B, FP_B, BOOT_2))
    m = _machine(server)
    assert m["ssh_hostkey_changed_in_boot"] is False
    assert m["ssh_hostkey_new_from_reboot"] is True


# --- known_hosts ------------------------------------------------------------


def test_hello_writes_one_known_hosts_line_per_ip(server):
    assert _hello(server, ssh_hostkey=GOOD).status_code == 200
    path = ssh_hostkey.known_hosts_path(_data_dir(server))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [f"{IP} {PUB_A}"]


def test_a_second_hello_from_the_same_ip_replaces_the_line(server):
    _hello(server, ssh_hostkey=GOOD)
    _hello(server, ssh_hostkey=_hk(PUB_B, FP_B, BOOT_2))
    path = ssh_hostkey.known_hosts_path(_data_dir(server))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == [f"{IP} {PUB_B}"]


def test_a_new_ip_adds_a_line_without_dropping_the_old_one(server):
    _hello(server, ssh_hostkey=GOOD)
    _hello(server, ip=IP2, ssh_hostkey=_hk(PUB_B, FP_B, BOOT_2))
    path = ssh_hostkey.known_hosts_path(_data_dir(server))
    lines = set(path.read_text(encoding="utf-8").splitlines())
    assert lines == {f"{IP} {PUB_A}", f"{IP2} {PUB_B}"}


def test_a_garbage_ip_is_not_written_to_known_hosts(server):
    _hello(server, ip="not-an-ip", ssh_hostkey=GOOD)
    path = ssh_hostkey.known_hosts_path(_data_dir(server))
    assert not path.exists()


# --- ssh שהשרת פותח ---------------------------------------------------------


def test_client_opts_pin_the_hello_key():
    opts = ssh_hostkey.client_opts(Path("/data"))
    joined = " ".join(opts)
    assert "StrictHostKeyChecking=yes" in joined
    assert f"UserKnownHostsFile={ssh_hostkey.known_hosts_path('/data')}" in joined
    assert "StrictHostKeyChecking=no" not in joined


def test_the_server_never_disables_host_key_checking():
    """grep על הקוד: אף ssh ב-server/ אינו StrictHostKeyChecking=no."""
    hits = []
    for path in SERVER_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "StrictHostKeyChecking=no" in text:
            hits.append(path.name)
        if "UserKnownHostsFile=/dev/null" in text:
            hits.append(path.name)
    assert hits == []
    # והסמל החיובי קיים — אחרת הטסט הזה עובר גם בלי התיקון.
    src = (SERVER_DIR / "ssh_hostkey.py").read_text(encoding="utf-8")
    assert '"StrictHostKeyChecking=yes"' in src
    assert "UserKnownHostsFile=" in src
