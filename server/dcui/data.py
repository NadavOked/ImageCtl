"""Read-only data collection for the physical console.

The renderer consumes :class:`Snapshot` and never reads the host itself.  This
keeps every 80x25 screen deterministic and lets tests replace all operating
system commands.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from server import (console_tls, deploy_net, ifaddr, netcfg, netcfg_host,
                    netcfg_rollback)
from server import storage_nodes
from server.db import connect, get_setting

DATA_DIR = Path("/var/lib/imagectl")
STATUS_FILE = Path("/etc/imagectl/firstboot.status")
SERVER_OVERRIDE = Path("/etc/systemd/system/imagectl-server.service.d/override.conf")
SYS_NET = Path("/sys/class/net")


@dataclass(frozen=True)
class CommandResult:
    returncode: int | None
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str]], CommandResult]


def run_argv(argv: list[str]) -> CommandResult:
    """Run one argv-only command; inability to run is a named result."""
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(None, stderr=str(exc))
    return CommandResult(done.returncode, done.stdout, done.stderr)


@dataclass(frozen=True)
class ServiceState:
    active: str = "unknown"
    enabled: str = "unknown"

    def text(self) -> str:
        active = self.active or "unknown"
        if self.enabled == "enabled":
            return active
        if self.enabled in {"disabled", "masked", "static"}:
            return f"{active} ({self.enabled})"
        return f"{active} (enablement unknown)"


@dataclass(frozen=True)
class NicInfo:
    name: str
    address: str = ""
    mode: str = "unknown"
    link: str = "unknown"
    speed: str = "unknown"
    mac: str = ""
    model: str = ""
    address_checked: bool = True


@dataclass(frozen=True)
class DeployRound:
    round_id: str
    label: str
    state: str
    writing: int
    targets: int
    started: str = ""


@dataclass(frozen=True)
class Snapshot:
    hostname: str
    version: str
    console_url: str
    no_address: bool
    fingerprint: str
    address_lookup_failed: bool = False
    certificate_sans: tuple[str, ...] = ()
    certificate_not_after: str = "unknown"
    management: NicInfo = field(default_factory=lambda: NicInfo("unknown"))
    interfaces: tuple[NicInfo, ...] = ()
    deploy: NicInfo | None = None
    services: dict[str, ServiceState] = field(default_factory=dict)
    storage_role: str = "standalone"
    secondary_count: int = 0
    deploy_round: DeployRound | None = None
    refreshed: str = "00:00:00"
    rollback_interface: str = ""
    rollback_seconds: int = 0


def _command_text(result: CommandResult) -> str:
    return (result.stdout or result.stderr or "").strip().splitlines()[0] \
        if (result.stdout or result.stderr).strip() else ""


def service_state(name: str, runner: Runner = run_argv) -> ServiceState:
    active_result = runner(["systemctl", "is-active", name])
    enabled_result = runner(["systemctl", "is-enabled", name])
    active = _command_text(active_result) or "unknown"
    enabled = _command_text(enabled_result) or "unknown"
    return ServiceState(active=active, enabled=enabled)


def _read_key_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for line in lines:
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def _override_interface(path: Path = SERVER_OVERRIDE) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    match = re.search(r"(?:^|\s)--console-host\s+([A-Za-z0-9._-]+)", text)
    return match.group(1) if match else ""


def _read(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def _interface_rows(sys_net: Path, runner: Runner) -> list[dict]:
    """List real interfaces from sysfs; ``ip -j`` only enriches them."""
    if not sys_net.is_dir():
        return []
    result = runner(["ip", "-j", "address", "show"])
    by_name: dict[str, dict] = {}
    address_checked = False
    if result.returncode == 0:
        try:
            parsed = json.loads(result.stdout)
            if isinstance(parsed, list):
                by_name = {str(row.get("ifname")): row for row in parsed
                           if isinstance(row, dict) and row.get("ifname")}
                address_checked = True
        except (json.JSONDecodeError, TypeError):
            by_name = {}
    rows = []
    for entry in sorted(sys_net.iterdir(), key=lambda item: item.name):
        if entry.name == "lo" or not re.fullmatch(r"[A-Za-z0-9._-]+", entry.name):
            continue
        live = by_name.get(entry.name, {})
        addresses = [
            f"{addr.get('local')}/{addr.get('prefixlen')}"
            for addr in live.get("addr_info", [])
            if addr.get("family") == "inet" and addr.get("local")
        ]
        device = entry / "device"
        model = _read(device / "model") or _read(device / "product")
        rows.append({
            "name": entry.name,
            "addresses": addresses,
            "mac": _read(entry / "address"),
            "link": _read(entry / "operstate", str(live.get("operstate", "unknown"))).lower(),
            "speed": _read(entry / "speed", "unknown"),
            "model": model,
            "address_checked": address_checked,
        })
    return rows


def _mode(conn, name: str, address: str) -> str:
    raw = get_setting(conn, netcfg.SETTING_PREFIX + name)
    try:
        configured = netcfg.NetConfig.from_json(name, raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        configured = netcfg.NetConfig(name)
    if configured.mode in (netcfg.MODE_DHCP, netcfg.MODE_STATIC):
        return configured.mode
    host_conf = netcfg_host.read_conf(name) or ""
    if re.search(rf"^\s*iface\s+{re.escape(name)}\s+inet\s+dhcp\s*$",
                 host_conf, re.MULTILINE):
        return "dhcp"
    if re.search(rf"^\s*iface\s+{re.escape(name)}\s+inet\s+static\s*$",
                 host_conf, re.MULTILINE):
        return "static"
    lease = ifaddr.dhclient_lease(name)
    return "dhcp" if lease is not None and lease.address == address.split("/")[0] else "static"


def _management_name(conn, rows: list[dict], status_file: Path,
                     override_file: Path, deploy_name: str) -> str:
    names = {row["name"] for row in rows}
    candidates = [
        get_setting(conn, "dcui:management_interface") or "",
        _read_key_values(status_file).get("servers_if", ""),
        _override_interface(override_file),
    ]
    for candidate in candidates:
        if candidate in names:
            return candidate
    for row in rows:
        raw = get_setting(conn, netcfg.SETTING_PREFIX + row["name"])
        if row["name"] != deploy_name and raw:
            return row["name"]
    return next((row["name"] for row in rows if row["name"] != deploy_name),
                rows[0]["name"] if rows else "unknown")


def _nic(conn, row: dict | None) -> NicInfo:
    if not row:
        return NicInfo("unknown", address_checked=False)
    address = row["addresses"][0] if row["addresses"] else ""
    return NicInfo(
        name=row["name"], address=address, mode=_mode(conn, row["name"], address),
        link=row["link"], speed=row["speed"], mac=row["mac"], model=row["model"],
        address_checked=bool(row.get("address_checked")),
    )


def _round(conn) -> DeployRound | None:
    row = conn.execute(
        "SELECT id, prefix, state, expected_clients, started_at, created_at "
        "FROM sessions WHERE state IN ('open', 'running') "
        "ORDER BY CASE state WHEN 'running' THEN 0 ELSE 1 END, created_at DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    writing = conn.execute(
        "SELECT COUNT(*) AS n FROM session_members "
        "WHERE session_id = ? AND state = 'writing'", (row["id"],)
    ).fetchone()["n"]
    return DeployRound(
        round_id=row["id"], label=row["prefix"] or row["id"], state=row["state"],
        writing=writing, targets=row["expected_clients"],
        started=(row["started_at"] or row["created_at"] or ""),
    )


def _version(runner: Runner, repo_dir: Path) -> str:
    result = runner(["git", "-C", str(repo_dir), "describe", "--tags", "--always"])
    return _command_text(result) if result.returncode == 0 else "unknown"


def _certificate_not_after(cert_path: Path) -> str:
    """Read OpenSSL's decoded notAfter using the stdlib ssl module."""
    try:
        import ssl
        decoded = ssl._ssl._test_decode_cert(str(cert_path))  # type: ignore[attr-defined]
        return str(decoded.get("notAfter") or "unknown")
    except (OSError, ValueError, AttributeError, TypeError):
        return "unknown"


def collect_snapshot(data_dir: Path = DATA_DIR, *, runner: Runner = run_argv,
                     sys_net: Path = SYS_NET, status_file: Path = STATUS_FILE,
                     override_file: Path = SERVER_OVERRIDE,
                     repo_dir: Path = Path("/opt/imagectl"),
                     now: datetime | None = None, conn=None) -> Snapshot:
    conn = conn or connect(data_dir / "imagectl.db")
    rows = _interface_rows(sys_net, runner)
    deploy_state = deploy_net.resolve(conn, None, None)
    deploy_name = deploy_state.interface or ""
    management_name = _management_name(
        conn, rows, status_file, override_file, deploy_name)
    all_nics = tuple(_nic(conn, row) for row in rows)
    management = next((nic for nic in all_nics if nic.name == management_name),
                      NicInfo("unknown", address_checked=False))
    deploy = _nic(conn, next((r for r in rows if r["name"] == deploy_name), None)) \
        if deploy_state.configured else None
    address = management.address.split("/")[0] if management.address else ""
    console_url = f"https://{address or '127.0.0.1'}:8081"
    try:
        tls = console_tls.ensure_console_cert(
            data_dir, address or management.name or "127.0.0.1",
            hostname=socket.gethostname())
        fingerprint = tls.fingerprint_sha256
        certificate_sans = tls.sans
        certificate_not_after = _certificate_not_after(tls.cert_path)
    except (console_tls.ConsoleTLSError, OSError, ValueError, ImportError):
        # A broken certificate is shown as unavailable; it must not remove the
        # physical password-reset/reboot recovery path along with the web UI.
        fingerprint = "unavailable (certificate read failed)"
        certificate_sans = ()
        certificate_not_after = "unknown (certificate read failed)"
    marker = netcfg_rollback.read_pending(data_dir / "netcfg")
    current = now or datetime.now()
    seconds = max(0, int(marker.deadline - current.timestamp())) if marker else 0
    services = {name: service_state(name, runner) for name in (
        "imagectl-server", "dnsmasq", "nftables")}
    return Snapshot(
        hostname=socket.gethostname(), version=_version(runner, repo_dir),
        console_url=console_url, no_address=not bool(address),
        address_lookup_failed=not management.address_checked,
        fingerprint=fingerprint, certificate_sans=certificate_sans,
        certificate_not_after=certificate_not_after,
        management=management, interfaces=all_nics, deploy=deploy, services=services,
        storage_role=storage_nodes.role(conn),
        secondary_count=conn.execute(
            "SELECT COUNT(*) AS n FROM storage_nodes").fetchone()["n"],
        deploy_round=_round(conn), refreshed=current.strftime("%H:%M:%S"),
        rollback_interface=marker.interface if marker else "",
        rollback_seconds=seconds,
    )


def demo_snapshot() -> Snapshot:
    return Snapshot(
        hostname="imagectl-ta", version="v0.48.0",
        console_url="https://10.44.9.12:8081", no_address=False,
        fingerprint=("3F:0A:9C:71:E2:5B:88:D4:16:AF:C3:09:7E:B2:44:D1:"
                     "6A:0F:93:C8:25:E7:1B:6C:D9:42:8F:A0:35:7D:1E:96"),
        certificate_sans=("10.44.9.12", "imagectl-ta"),
        certificate_not_after="Sep 19 12:00:00 2036 GMT",
        management=NicInfo("ens18", "10.44.9.12/24", "dhcp", "up", "1000",
                           "52:54:00:aa:10:01", "Intel I219-LM"),
        interfaces=(
            NicInfo("ens18", "10.44.9.12/24", "dhcp", "up", "1000",
                    "52:54:00:aa:10:01", "Intel I219-LM"),
            NicInfo("ens20", "", "static", "up", "1000",
                    "52:54:00:aa:14:01", "Intel I350"),
            NicInfo("ens21", "", "dhcp", "down", "unknown",
                    "52:54:00:aa:99:01", "Intel I350"),
        ),
        deploy=None,
        services={name: ServiceState("active", "enabled") for name in
                  ("imagectl-server", "nftables")} | {
                      "dnsmasq": ServiceState("inactive", "disabled")},
        storage_role="standalone", secondary_count=1,
        deploy_round=DeployRound("ses_demo", "LAB303", "running", 2, 3,
                                 "2026-09-19T08:31:00+03:00"),
        refreshed="08:54:12", rollback_interface="ens20", rollback_seconds=47,
    )
