"""DCUI rendering and host-action tests (no real systemctl or network writes)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from server import netcfg_host, netcfg_rollback, users
from server.db import connect, get_setting
from server.dcui import actions
from server.dcui.app import (render_auth, render_main, render_menu,
                             render_network, render_power)
from server.dcui.data import CommandResult, NicInfo, _round, demo_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _db(tmp_path):
    conn = connect(tmp_path / "imagectl.db")
    users.create(conn, "admin", "before", "admin", by="test",
                 is_builtin=True, check_policy=False)
    return conn


def _assert_screen(lines, labels):
    assert len(lines) == 25
    assert all(len(line) <= 80 for line in lines)
    joined = "\n".join(lines)
    joined.encode("ascii")
    for label in labels:
        assert label in joined


def test_all_approved_screens_are_pure_80x25_ascii():
    snapshot = demo_snapshot()
    form = {"interface": "ens20", "mode": "static", "address": "10.44.9.12",
            "netmask": "255.255.255.0", "gateway": "10.44.9.254",
            "dns": "10.44.9.2"}
    cases = [
        (render_main(snapshot), ("Hostname:", "Certificate:", "<F2>", "<F12>")),
        (render_auth(1, 0, 8), ("Login Name:", "Password:", "30 s lockout")),
        (render_menu(snapshot), ("Configure Management Network", "Not available here")),
        (render_network(snapshot, list(snapshot.interfaces), form, rollback_seconds=47),
         ("Prefix / Mask:", "Apply (60 s window)", "auto-rollback")),
        (render_power(snapshot, typed="imagectl-t"),
         ("Restart / Shut Down", "Type the hostname", "deploy round is running")),
    ]
    for lines, labels in cases:
        _assert_screen(lines, labels)


def test_three_wrong_passwords_lock_auth_for_30_seconds(tmp_path):
    conn = _db(tmp_path)
    guard = actions.AuthGuard()
    assert not guard.authenticate(conn, "wrong-1", now=100.0)
    assert not guard.authenticate(conn, "wrong-2", now=101.0)
    assert not guard.authenticate(conn, "wrong-3", now=102.0)
    assert guard.failures == 3
    assert guard.locked_until == 132.0
    assert guard.seconds_left(103.0) == 29
    events = conn.execute(
        "SELECT event, user FROM journal WHERE event = 'dcui_auth_failure'"
    ).fetchall()
    assert len(events) == 3 and {row["user"] for row in events} == {"dcui"}


def test_reset_password_is_admin_admin_and_forces_change(tmp_path):
    conn = _db(tmp_path)
    result = actions.reset_admin_password(conn)
    assert result.ok
    assert users.verify(conn, "admin", "admin") == "admin"
    assert users.flags(conn, "admin")["must_change_password"] is True
    row = conn.execute(
        "SELECT user FROM journal WHERE event = 'admin_password_reset_dcui'"
    ).fetchone()
    assert row["user"] == "dcui"


def test_power_action_requires_exact_hostname_before_runner(tmp_path):
    conn = _db(tmp_path)
    calls = []

    def fake(argv):
        calls.append(argv)
        return CommandResult(0, "", "")

    refused = actions.power_action(conn, "restart", "imagectl-T", "imagectl-ta", fake)
    assert not refused.ok
    assert calls == []
    accepted = actions.power_action(conn, "shutdown", "imagectl-ta", "imagectl-ta", fake)
    assert accepted.ok
    assert calls == [["systemctl", "poweroff"]]


def test_network_form_validation_reports_each_bad_field():
    errors = actions.validate_network_form({
        "interface": "missing", "mode": "static", "address": "999.1.1.1",
        "netmask": "255.0.255.0", "gateway": "bad", "dns": "10.0.0.1,nope",
    }, {"ens18"})
    assert set(errors) == {"interface", "address", "netmask", "gateway", "dns"}
    assert all(value.isascii() for value in errors.values())


def test_prefix_is_accepted_and_converted_to_netmask():
    form = {"interface": "ens18", "mode": "static", "address": "10.0.0.10",
            "netmask": "24", "gateway": "10.0.0.1", "dns": "10.0.0.2"}
    assert actions.validate_network_form(form, {"ens18"}) == {}
    assert actions.network_config(form).netmask == "255.255.255.0"


def test_network_apply_uses_injected_host_path_and_arms_then_confirms(tmp_path):
    conn = _db(tmp_path)
    calls = []
    hooks = {
        "netcfg_timer_active": lambda: (True, "active"),
        "now": lambda: 100.0,
        "boot_id": lambda: "boot-demo",
        "netcfg_read_conf": lambda name, root: "old config\n",
        "read_resolv": lambda: "nameserver 192.0.2.1\n",
        "netcfg_write_conf": lambda name, text, root: calls.append(
            ("write_conf", name, text, root)),
        "netcfg_write_resolv": lambda text, path: calls.append(
            ("write_resolv", text, path)),
        "netcfg_apply": lambda name: calls.append(("apply", name)),
        "netcfg_state": lambda: netcfg_host.LiveState(
            True, addresses={"ens18": ["192.0.2.10/24"]}),
        "hostname": lambda: "imagectl-test",
    }
    form = {"interface": "ens18", "mode": "dhcp", "address": "",
            "netmask": "255.255.255.0", "gateway": "", "dns": ""}
    result = actions.apply_network(conn, form, tmp_path, {"ens18"}, hooks)
    assert result.ok
    pending = netcfg_rollback.read_pending(tmp_path / "netcfg")
    assert pending is not None and pending.deadline == 160.0
    assert [entry[0] for entry in calls] == ["write_conf", "write_resolv", "apply"]
    assert actions.confirm_network(conn, tmp_path, now=120.0).ok
    assert netcfg_rollback.read_pending(tmp_path / "netcfg") is None
    assert get_setting(conn, "dcui:management_interface") == "ens18"


def test_running_round_from_db_is_rendered_as_power_warning(tmp_path):
    conn = _db(tmp_path)
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients, "
        "wait_seconds, state, opened_by, created_at, last_join_at, started_at) "
        "VALUES ('ses_dcui', 'grp_CLONERS', 'img_demo', 'LAB303', 3, 30, "
        "'running', 'admin', '2026-09-19T08:30:00+03:00', 1, "
        "'2026-09-19T08:31:00+03:00')"
    )
    conn.execute(
        "INSERT INTO session_members (session_id, mac, state, updated_at) "
        "VALUES ('ses_dcui', '52:54:00:00:00:01', 'writing', 'now'), "
        "('ses_dcui', '52:54:00:00:00:02', 'writing', 'now')"
    )
    conn.commit()
    running = _round(conn)
    assert running is not None and running.writing == 2
    screen = "\n".join(render_power(replace(demo_snapshot(), deploy_round=running)))
    assert "A deploy round is running: 2 machines writing" in screen


def test_restart_services_only_restarts_enabled_dnsmasq(tmp_path):
    conn = _db(tmp_path)
    calls = []

    def fake(argv):
        calls.append(argv)
        if argv == ["systemctl", "is-enabled", "dnsmasq"]:
            return CommandResult(1, "disabled\n", "")
        if argv[:2] == ["systemctl", "is-active"]:
            return CommandResult(0, "active\n", "")
        return CommandResult(0, "", "")

    result = actions.restart_services(conn, fake)
    assert result.ok
    assert ["systemctl", "restart", "imagectl-server"] in calls
    assert ["systemctl", "restart", "dnsmasq"] not in calls


def test_wizard_rerun_releases_8081_and_starts_the_part_one_unit(tmp_path):
    conn = _db(tmp_path)
    calls = []

    def fake(argv):
        calls.append(argv)
        if argv == ["systemctl", "is-active", "imagectl-wizard.service"]:
            return CommandResult(0, "active\n", "")
        return CommandResult(0, "", "")

    result = actions.start_wizard(conn, "https://192.0.2.10:8081", fake)
    assert result.ok
    assert calls[:3] == [
        ["systemctl", "set-environment", "IMAGECTL_WIZARD_MODE=--rerun"],
        ["systemctl", "stop", "imagectl-server"],
        ["systemctl", "start", "imagectl-wizard.service"],
    ]
    assert ["systemctl", "unset-environment", "IMAGECTL_WIZARD_MODE"] in calls


def test_demo_snapshot_exposes_real_picker_shape():
    snapshot = demo_snapshot()
    assert len(snapshot.interfaces) == 3
    assert all(isinstance(nic, NicInfo) and nic.name != "lo"
               for nic in snapshot.interfaces)


def test_dcui_unit_owns_tty1_without_requiring_the_web_server():
    unit = (ROOT / "install" / "imagectl-dcui.service").read_text(encoding="utf-8")
    for required in (
        "Conflicts=getty@tty1.service", "After=imagectl-server.service",
        "TTYPath=/dev/tty1", "TTYReset=yes", "Restart=always", "User=root",
        "ProtectSystem=strict", "ReadWritePaths=/var/lib/imagectl",
    ):
        assert required in unit
    assert "Requires=imagectl-server.service" not in unit


def test_wizard_unit_has_an_explicit_systemd_rerun_argument():
    unit = (ROOT / "install" / "imagectl-wizard.service").read_text(encoding="utf-8")
    assert "$IMAGECTL_WIZARD_MODE" in unit
    assert "ConditionPathExists" not in unit
