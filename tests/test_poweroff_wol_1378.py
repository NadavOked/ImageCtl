"""#1378: every agent S5 path arms WoL and reports an unverified arm."""

from conftest import hello_body, setup_classroom
from test_agent import AGENT


def test_every_poweroff_path_uses_the_one_guard():
    files = [AGENT / "lib/common.sh", AGENT / "lib/idle.sh",
             AGENT / "lib/localclone.sh", AGENT / "lib/lcwait.sh",
             AGENT / "monitor.c"]
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    calls = [line for line in text.splitlines() if line.strip() == "poweroff"]
    assert len(calls) == 1, "only safe_poweroff may invoke poweroff"
    assert "poweroff -f" not in text
    for path in files[1:]:
        body = path.read_text(encoding="utf-8")
        if "poweroff" in body:
            assert "safe_poweroff" in body, path


def test_failed_arm_is_in_the_next_hello_contract():
    common = (AGENT / "lib/common.sh").read_text(encoding="utf-8")
    hello = (AGENT / "lib/sysinfo.sh").read_text(encoding="utf-8")
    assert 'false > "$RUN_DIR/wol_armed"' in common
    assert '\"wol_armed\":false' in hello
    assert common.index('false > "$RUN_DIR/wol_armed"') < common.index("send_hello")


def test_server_journals_a_machine_that_reports_wol_not_armed(server):
    mac = setup_classroom(server)["mac1"]
    body = hello_body(mac)
    body["wol_armed"] = False
    response = server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    row = server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'wol_arm_failed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert row is not None and row["detail"] == mac
