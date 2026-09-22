"""Authenticated writes performed by the physical console.

All host commands are argv lists and accept an injectable runner.  Network
changes deliberately use the same ``netcfg``/``netcfg_host``/
``netcfg_rollback`` primitives as the web console.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from server import netcfg, netcfg_host, netcfg_rollback, users
from server.db import (_write_lock, get_setting, journal, set_setting,
                       writing)

from .data import CommandResult, Runner, run_argv

LOCKOUT_ATTEMPTS = 3
LOCKOUT_SECONDS = 30
AUTH_IDLE_SECONDS = 90
WIZARD_UNIT = "imagectl-wizard-rerun.service"


@dataclass
class AuthGuard:
    failures: int = 0
    locked_until: float = 0.0

    def seconds_left(self, now: float | None = None) -> int:
        current = time.time() if now is None else now
        return max(0, int(self.locked_until - current + 0.999))

    def authenticate(self, conn, password: str, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        if self.locked_until > current:
            journal(conn, "dcui_auth_locked", "admin", "dcui")
            return False
        if self.locked_until and self.locked_until <= current:
            self.failures = 0
            self.locked_until = 0.0
        role = users.verify(conn, "admin", password)
        if role == "admin":
            self.failures = 0
            journal(conn, "dcui_auth_success", "admin", "dcui")
            return True
        self.failures += 1
        journal(conn, "dcui_auth_failure", f"admin attempt={self.failures}", "dcui")
        if self.failures >= LOCKOUT_ATTEMPTS:
            self.locked_until = current + LOCKOUT_SECONDS
        return False


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    details: tuple[str, ...] = ()


def reset_admin_password(conn) -> ActionResult:
    """Set admin/admin, invalidate sessions, and force the next web login change."""
    users.update(conn, "admin", by="dcui", password="admin", check_policy=False)
    with _write_lock, writing(conn):
        conn.execute(
            "UPDATE users SET must_change_password = 1 WHERE username = 'admin'"
        )
    users.bump_auth_epoch(conn, "admin")
    verified = (users.verify(conn, "admin", "admin") == "admin"
                and users.flags(conn, "admin")["must_change_password"])
    journal(conn, "admin_password_reset_dcui",
            f"admin default password; forced change verified={str(verified).lower()}",
            "dcui")
    if not verified:
        return ActionResult(False, "Password reset could not be verified.")
    return ActionResult(
        True,
        "Password reset. Log in to the console as admin/admin and set a new one.",
    )


def _text(result: CommandResult) -> str:
    return (result.stderr or result.stdout or "").strip()


def restart_services(conn, runner: Runner = run_argv) -> ActionResult:
    units = ["imagectl-server"]
    enabled = runner(["systemctl", "is-enabled", "dnsmasq"])
    if enabled.returncode == 0 and enabled.stdout.strip() == "enabled":
        units.append("dnsmasq")
    failures = []
    for unit in units:
        result = runner(["systemctl", "restart", unit])
        if result.returncode != 0:
            failures.append(f"{unit}: {_text(result) or 'restart failed'}")
    states = []
    for unit in units:
        state = runner(["systemctl", "is-active", unit])
        value = (state.stdout or state.stderr).strip() or "unknown"
        states.append(f"{unit}: {value}")
        if value != "active":
            failures.append(f"{unit} did not read back active")
    ok = not failures
    journal(conn, "dcui_services_restart",
            f"units={','.join(units)} ok={str(ok).lower()}", "dcui")
    return ActionResult(ok, "Services restarted." if ok else "Service restart failed.",
                        tuple(states + failures))


def start_wizard(conn, console_url: str, runner: Runner = run_argv) -> ActionResult:
    """Start the dedicated unit whose ExecStart carries ``--rerun``."""
    stopped = runner(["systemctl", "stop", "imagectl-server"])
    if stopped.returncode != 0:
        journal(conn, "dcui_wizard_rerun", "imagectl-server stop failed", "dcui")
        return ActionResult(False, "The web server could not release HTTPS port 8081.",
                            (_text(stopped) or "systemctl returned no detail",))
    result = runner(["systemctl", "start", WIZARD_UNIT])
    active = runner(["systemctl", "is-active", WIZARD_UNIT]) \
        if result.returncode == 0 else CommandResult(result.returncode, "", "")
    ok = result.returncode == 0 and active.stdout.strip() == "active"
    if not ok:
        # A failed rerun must not strand the ordinary console offline.
        runner(["systemctl", "start", "imagectl-server"])
    journal(conn, "dcui_wizard_rerun", f"unit={WIZARD_UNIT} ok={str(ok).lower()}",
            "dcui")
    if ok:
        return ActionResult(True, f"Initial setup wizard started at {console_url}.")
    return ActionResult(False, "Initial setup wizard did not start.",
                        (_text(result) or _text(active) or
                         "systemctl did not read the wizard back as active",))


def note_tls_display(conn) -> None:
    journal(conn, "dcui_tls_display", "console certificate", "dcui")


def power_action(conn, choice: str, typed_hostname: str, hostname: str,
                 runner: Runner = run_argv) -> ActionResult:
    command = "reboot" if choice == "restart" else "poweroff"
    if typed_hostname != hostname:
        journal(conn, "dcui_power_refused", f"action={command} hostname mismatch",
                "dcui")
        return ActionResult(False, "The hostname does not match exactly.")
    journal(conn, "dcui_power_action", command, "dcui")
    result = runner(["systemctl", command])
    if result.returncode == 0:
        return ActionResult(True, f"System {command} requested.")
    return ActionResult(False, f"System {command} failed.",
                        (_text(result) or "systemctl returned no detail",))


def _ipv4(value: str) -> bool:
    try:
        return ipaddress.ip_address(value).version == 4
    except ValueError:
        return False


def _netmask(value: str) -> str | None:
    raw = value.strip()
    try:
        if raw.isdigit():
            prefix = int(raw)
            if not 0 <= prefix <= 32:
                return None
            return str(ipaddress.IPv4Network(f"0.0.0.0/{prefix}").netmask)
        network = ipaddress.IPv4Network(f"0.0.0.0/{raw}")
        return str(network.netmask)
    except (ValueError, ipaddress.NetmaskValueError):
        return None


def validate_network_form(form: dict[str, str], interface_names: set[str]) -> dict[str, str]:
    """Wizard-equivalent IPv4 validation with ASCII English VT messages."""
    errors: dict[str, str] = {}
    name = form.get("interface", "").strip()
    mode = form.get("mode", "").strip()
    if name not in interface_names:
        errors["interface"] = "Select an existing network interface."
    if mode not in {netcfg.MODE_DHCP, netcfg.MODE_STATIC}:
        errors["mode"] = "Select DHCP or Static."
    if mode == netcfg.MODE_STATIC:
        address = form.get("address", "").strip()
        mask = form.get("netmask", "").strip()
        gateway = form.get("gateway", "").strip()
        dns_values = [item.strip() for item in form.get("dns", "").split(",")
                      if item.strip()]
        if not _ipv4(address):
            errors["address"] = "Invalid IPv4 address."
        if _netmask(mask) is None:
            errors["netmask"] = "Invalid prefix or subnet mask."
        if gateway and not _ipv4(gateway):
            errors["gateway"] = "Invalid gateway IPv4 address."
        if any(not _ipv4(item) for item in dns_values):
            errors["dns"] = "Invalid DNS IPv4 address; separate entries with commas."
    return errors


def network_config(form: dict[str, str]) -> netcfg.NetConfig:
    mode = form["mode"].strip()
    mask = _netmask(form.get("netmask", "")) or form.get("netmask", "").strip()
    return netcfg.NetConfig(
        name=form["interface"].strip(), mode=mode,
        address=form.get("address", "").strip() if mode == netcfg.MODE_STATIC else "",
        netmask=mask or "255.255.255.0",
        gateway=form.get("gateway", "").strip() if mode == netcfg.MODE_STATIC else "",
        dns=[item.strip() for item in form.get("dns", "").split(",") if item.strip()],
    )


def apply_preinstall_static(form: dict[str, str], interface_names: set[str],
                            runner: Runner = run_argv) -> ActionResult:
    """Apply an in-memory-only address while the installer wizard is running."""
    address_with_prefix = form.get("address", "").strip()
    if "/" in address_with_prefix:
        address, prefix = address_with_prefix.rsplit("/", 1)
    else:
        address, prefix = address_with_prefix, ""
    static_form = {
        **form, "mode": netcfg.MODE_STATIC, "dns": "",
        "address": address, "netmask": prefix,
    }
    errors = validate_network_form(static_form, interface_names)
    if not form.get("gateway", "").strip():
        errors["gateway"] = "Gateway is required."
    if errors:
        return ActionResult(False, "Static address form is invalid.",
                            tuple(errors.values()))
    cfg = network_config(static_form)
    prefix = ipaddress.IPv4Network(f"0.0.0.0/{cfg.netmask}").prefixlen
    commands = (
        ["ip", "addr", "flush", "dev", cfg.name],
        ["ip", "addr", "add", f"{cfg.address}/{prefix}", "dev", cfg.name],
        ["ip", "route", "replace", "default", "via", cfg.gateway,
         "dev", cfg.name],
    )
    for argv in commands:
        result = runner(argv)
        if result.returncode != 0:
            return ActionResult(
                False, f"Temporary network command failed: {' '.join(argv[:3])}.",
                (_text(result) or "ip returned no detail",),
            )
    return ActionResult(True, f"Temporary address applied to {cfg.name}.")


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).astimezone().isoformat(
        timespec="seconds")


def _read_resolv(path: str = netcfg_host.RESOLV_CONF) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _all_configs(conn, replacement: netcfg.NetConfig) -> list[netcfg.NetConfig]:
    rows = conn.execute(
        "SELECT key, value FROM settings WHERE key LIKE ?",
        (netcfg.SETTING_PREFIX + "%",),
    ).fetchall()
    found = []
    for row in rows:
        name = row["key"][len(netcfg.SETTING_PREFIX):]
        if name != replacement.name:
            try:
                found.append(netcfg.NetConfig.from_json(name, row["value"]))
            except (ValueError, TypeError, json.JSONDecodeError):
                continue
    return sorted([*found, replacement], key=lambda item: item.name)


def default_network_hooks() -> dict[str, Callable]:
    return {
        **netcfg_host.default_hooks(),
        "boot_id": netcfg_rollback.boot_id,
        "now": time.time,
        "hostname": socket.gethostname,
        "read_resolv": _read_resolv,
    }


def apply_network(conn, form: dict[str, str], data_dir: Path,
                  interface_names: set[str], hooks: dict[str, Callable] | None = None) -> ActionResult:
    errors = validate_network_form(form, interface_names)
    if errors:
        journal(conn, "net_config_refused",
                f"validation fields={','.join(sorted(errors))}", "dcui")
        return ActionResult(False, "Network form is invalid.", tuple(errors.values()))
    cfg = network_config(form)
    before_raw = get_setting(conn, netcfg.SETTING_PREFIX + cfg.name)
    try:
        before = netcfg.NetConfig.from_json(cfg.name, before_raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        before = netcfg.NetConfig(cfg.name)
    validation = netcfg.problems(cfg, _all_configs(conn, cfg))
    if validation:
        # netcfg's operator-facing messages are Hebrew; tty1 is ASCII-only.
        journal(conn, "net_config_refused", f"{cfg.name} conflicts={len(validation)}",
                "dcui")
        return ActionResult(False, "Network configuration conflicts with existing settings.",
                            tuple(f"Conflict {index + 1}" for index in range(len(validation))))
    state_dir = data_dir / "netcfg"
    if netcfg_rollback.read_pending(state_dir) is not None:
        journal(conn, "net_config_refused", f"{cfg.name} pending change", "dcui")
        return ActionResult(False, "A previous network change still awaits confirmation.")
    active_hooks = {**default_network_hooks(), **(hooks or {})}
    ready, detail = active_hooks["netcfg_timer_active"]()
    if not ready:
        journal(conn, "net_config_refused", f"{cfg.name} rollback timer inactive", "dcui")
        return ActionResult(False, "The automatic network rollback timer is not active.",
                            (str(detail),))
    now = float(active_hooks["now"]())
    marker = netcfg_rollback.Pending(
        interface=cfg.name,
        deadline=now + netcfg_rollback.WINDOW_SECONDS,
        armed_at=_iso(now), boot=str(active_hooks["boot_id"]()),
        files=[{"name": cfg.name,
                "text": active_hooks["netcfg_read_conf"](
                    cfg.name, netcfg_host.INTERFACES_DIR)}],
        resolv=active_hooks["read_resolv"](), setting=before.to_json(),
    )
    marker_error = netcfg_rollback.write_pending(state_dir, marker)
    if marker_error:
        journal(conn, "net_config_refused", f"{cfg.name} marker write failed", "dcui")
        return ActionResult(False, "Could not arm automatic network rollback.")
    journal(conn, "net_rollback_armed",
            f"{cfg.name} window={netcfg_rollback.WINDOW_SECONDS}s", "dcui")
    set_setting(conn, netcfg.SETTING_PREFIX + cfg.name, cfg.to_json())
    journal(conn, "net_config",
            f"{netcfg.summary(cfg)} changed={','.join(netcfg.changed(before, cfg))}",
            "dcui")
    configs = _all_configs(conn, cfg)
    apply_errors = [
        active_hooks["netcfg_write_conf"](
            cfg.name, netcfg.render(cfg, hostname=active_hooks["hostname"]()),
            netcfg_host.INTERFACES_DIR),
        active_hooks["netcfg_write_resolv"](
            netcfg.render_resolv(configs), netcfg_host.RESOLV_CONF),
    ]
    error = next((item for item in apply_errors if item), None)
    if error is None:
        error = active_hooks["netcfg_apply"](cfg.name)
    live = active_hooks["netcfg_state"]()
    gaps = netcfg_host.mismatches(cfg, live)
    if error or gaps:
        journal(conn, "net_config_unverified",
                f"{cfg.name} {error or 'live state mismatch'}", "dcui")
        detail_lines = (["Apply failed; automatic rollback remains armed."] if error else [])
        detail_lines += ["Live read-back did not match the requested configuration."] if gaps else []
        return ActionResult(False, "Network change was not verified.", tuple(detail_lines))
    return ActionResult(True, "Network change applied; confirm within 60 seconds.")


def restart_management_network(conn, interface: str, data_dir: Path,
                               hooks: dict[str, Callable] | None = None) -> ActionResult:
    raw = get_setting(conn, netcfg.SETTING_PREFIX + interface)
    if not raw:
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false no saved configuration", "dcui")
        return ActionResult(False, "The management interface has no saved configuration.")
    try:
        cfg = netcfg.NetConfig.from_json(interface, raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        journal(conn, "dcui_management_network_restart",
                f"{interface} ok=false invalid saved configuration", "dcui")
        return ActionResult(False, "The saved management network configuration is invalid.")
    form = {
        "interface": cfg.name, "mode": cfg.mode, "address": cfg.address,
        "netmask": cfg.netmask, "gateway": cfg.gateway, "dns": ",".join(cfg.dns),
    }
    result = apply_network(conn, form, data_dir, {interface}, hooks)
    journal(conn, "dcui_management_network_restart",
            f"{interface} ok={str(result.ok).lower()}", "dcui")
    return result


def confirm_network(conn, data_dir: Path, now: float | None = None) -> ActionResult:
    state_dir = data_dir / "netcfg"
    marker = netcfg_rollback.read_pending(state_dir)
    current = time.time() if now is None else now
    if marker is None:
        journal(conn, "dcui_net_confirm_refused", "no pending change", "dcui")
        return ActionResult(False, "No network change is waiting for confirmation.")
    if netcfg_rollback.expired(marker, current):
        journal(conn, "dcui_net_confirm_refused", f"{marker.interface} expired", "dcui")
        return ActionResult(False, "The confirmation window closed; rollback will run.")
    netcfg_rollback.clear_pending(state_dir)
    if netcfg_rollback.pending_path(state_dir).exists():
        journal(conn, "dcui_net_confirm_refused",
                f"{marker.interface} marker still present", "dcui")
        return ActionResult(False, "The rollback marker could not be cleared.")
    set_setting(conn, "dcui:management_interface", marker.interface)
    if get_setting(conn, "dcui:management_interface") != marker.interface:
        journal(conn, "dcui_net_confirm_refused",
                f"{marker.interface} management selection not read back", "dcui")
        return ActionResult(False, "The management interface selection was not verified.")
    journal(conn, "net_confirmed", marker.interface, "dcui")
    return ActionResult(True, "Network change confirmed.")


def request_network_rollback(conn, data_dir: Path,
                             runner: Runner = run_argv) -> ActionResult:
    marker = netcfg_rollback.read_pending(data_dir / "netcfg")
    if marker is None:
        journal(conn, "dcui_net_rollback_requested", "no pending change", "dcui")
        return ActionResult(False, "No network change is waiting for rollback.")
    result = runner(["systemctl", "start", "imagectl-netrollback.service"])
    ok = (result.returncode == 0
          and not netcfg_rollback.pending_path(data_dir / "netcfg").exists())
    journal(conn, "dcui_net_rollback_requested",
            f"{marker.interface} ok={str(ok).lower()}", "dcui")
    return ActionResult(ok, "Network rollback requested." if ok
                        else "Network rollback could not be started.")
