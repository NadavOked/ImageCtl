"""Pure 80x25 renderers and the curses event loop for tty1."""

from __future__ import annotations

import socket
import time
from dataclasses import dataclass, field
from pathlib import Path

from server import netcfg, netcfg_rollback
from server.db import connect

from . import actions
from .data import (DATA_DIR, NicInfo, Snapshot, collect_snapshot,
                   demo_snapshot, run_argv)

WIDTH = 80
HEIGHT = 25
MENU_ITEMS = (
    "Configure Management Network",
    "Reset Admin Password",
    "Restart Management Network",
    "Restart Services",
    "Display TLS Certificate",
    "Re-run Initial Setup Wizard",
)


class Canvas:
    def __init__(self, title: str, footer: str):
        self.rows = [[" "] * WIDTH for _ in range(HEIGHT)]
        self.hline(0)
        self.hline(2)
        self.hline(22)
        self.hline(24)
        for row in range(1, 24):
            self.rows[row][0] = "|"
            self.rows[row][-1] = "|"
        self.put(1, 2, title)
        self.put(23, 2, footer)

    def hline(self, row: int) -> None:
        self.rows[row] = list("+" + "-" * (WIDTH - 2) + "+")

    def put(self, row: int, column: int, text: object, width: int | None = None) -> None:
        if not 0 <= row < HEIGHT or column >= WIDTH - 1:
            return
        clean = str(text).encode("ascii", "replace").decode("ascii")
        if width is not None:
            clean = clean[:width].ljust(width)
        room = max(0, WIDTH - 1 - max(1, column))
        for index, char in enumerate(clean[:room]):
            self.rows[row][max(1, column) + index] = char

    def lines(self) -> list[str]:
        return ["".join(row) for row in self.rows]


def _title(left: str, right: str = "") -> str:
    usable = WIDTH - 4
    if not right:
        return left[:usable]
    spaces = max(1, usable - len(left) - len(right))
    return (left + " " * spaces + right)[:usable]


def _service(snapshot: Snapshot, name: str) -> str:
    state = snapshot.services.get(name)
    if name == "dnsmasq" and snapshot.deploy is None and state and state.active != "active":
        return "inactive (Unconfigured - no deploy network)"
    return state.text() if state else "unknown (enablement unknown)"


def _fingerprint_lines(value: str) -> tuple[str, str]:
    parts = value.split(":")
    if len(parts) >= 32:
        return ":".join(parts[:16]), ":".join(parts[16:32])
    midpoint = min(len(value), 47)
    return value[:midpoint], value[midpoint:midpoint + 47]


def render_main(snapshot: Snapshot, message: str = "") -> list[str]:
    canvas = Canvas(
        _title("ImageCtl Server Operations Console",
               f"{snapshot.version}  {snapshot.storage_role}"),
        "<F2> Customize System    <F12> Restart/Shutdown    <F5> Refresh",
    )
    fp1, fp2 = _fingerprint_lines(snapshot.fingerprint)
    canvas.put(4, 4, "Hostname:")
    canvas.put(4, 23, snapshot.hostname)
    canvas.put(5, 4, "Console:")
    console = snapshot.console_url
    if snapshot.address_lookup_failed:
        console += "   IPv4 address lookup failed"
    elif snapshot.no_address:
        console += "   No DHCP address assigned"
    canvas.put(5, 23, console)
    canvas.put(6, 4, "Certificate:")
    canvas.put(6, 23, f"SHA256 {fp1}")
    canvas.put(7, 30, fp2)
    mgmt = snapshot.management
    speed = f" {mgmt.speed}" if mgmt.speed and mgmt.speed != "unknown" else ""
    canvas.put(9, 4, "Management NIC:")
    canvas.put(9, 23, f"{mgmt.name}  {mgmt.address or 'no IPv4'}  "
                       f"{mgmt.mode.upper()}  link {mgmt.link}{speed}")
    canvas.put(10, 4, "Deploy NIC:")
    if snapshot.deploy is None:
        canvas.put(10, 23, "not configured (set from the console)")
    else:
        deploy = snapshot.deploy
        canvas.put(10, 23, f"{deploy.name}  {deploy.address or 'no IPv4'}  link {deploy.link}")
    canvas.put(12, 4, "imagectl-server:")
    canvas.put(12, 23, _service(snapshot, "imagectl-server"))
    canvas.put(13, 4, "dnsmasq:")
    canvas.put(13, 23, _service(snapshot, "dnsmasq"))
    canvas.put(14, 4, "nftables:")
    canvas.put(14, 23, _service(snapshot, "nftables"))
    plural = "secondary" if snapshot.secondary_count == 1 else "secondaries"
    canvas.put(15, 4, "Storage Role:")
    canvas.put(15, 23, f"{snapshot.storage_role}  ({snapshot.secondary_count} {plural} paired)")
    canvas.put(17, 4, "Deploy round:")
    round_text = "none"
    if snapshot.deploy_round:
        item = snapshot.deploy_round
        round_text = f"{item.label}  {item.state}  {item.writing}/{item.targets} writing"
    canvas.put(17, 23, round_text)
    if message:
        canvas.put(19, 4, message, 72)
    else:
        canvas.put(19, 4, "This screen is read-only. Configuration requires the admin password.")
    canvas.put(21, 4, f"Last refresh: {snapshot.refreshed}   (every 5 s)")
    return canvas.lines()


def render_preinstall(snapshot: Snapshot, message: str = "") -> list[str]:
    canvas = Canvas(
        _title(f"ImageCtl {snapshot.version} - installation"),
        "F2  Set a static address       F5  Refresh",
    )
    canvas.put(4, 4, "Management network is not configured yet.")
    canvas.put(6, 4, "Addresses obtained by DHCP:")
    for index, nic in enumerate(snapshot.interfaces[:6]):
        address = nic.address or "-"
        state = "link up" if nic.link == "up" else "no carrier"
        canvas.put(8 + index, 6,
                   f"{nic.name:<8}{nic.mac:<20}{address:<19}({state})", 68)
    canvas.put(15, 4, "Continue the installation from a browser:")
    if snapshot.console_url:
        canvas.put(17, 6, snapshot.console_url)
    else:
        canvas.put(17, 6, "No address is available. Press F2 to set a static address.")
    if message:
        canvas.put(20, 4, message, 70)
    return canvas.lines()


def render_preinstall_network(interfaces: list[NicInfo], form: dict[str, str],
                              field_index: int = 0, error: str = "") -> list[str]:
    canvas = Canvas(
        _title("Set a Temporary Static Address", "installation"),
        "<Space> Interface  <Tab> Next field  <Enter> Apply  <Esc> Cancel",
    )
    canvas.put(4, 4, "This address lasts only until permanent setup is applied.")
    canvas.put(7, 4, "Interface:")
    for index, nic in enumerate(interfaces[:4]):
        marker = "*" if nic.name == form.get("interface") else " "
        canvas.put(8 + index, 8,
                   f"({marker}) {nic.name:<8} {nic.mac:<17} ({nic.link})", 62)
    cursor = "_"
    canvas.put(14, 4, "IPv4 address / prefix:")
    canvas.put(14, 30, f"[ {form.get('address', '')[:23]:<23}"
               f"{cursor if field_index == 1 else ' '} ]")
    canvas.put(16, 4, "Gateway:")
    canvas.put(16, 30, f"[ {form.get('gateway', '')[:23]:<23}"
               f"{cursor if field_index == 2 else ' '} ]")
    canvas.put(19, 4, error or
               "Applies: ip addr flush/add and ip route replace default.", 70)
    return canvas.lines()


def render_auth(failures: int = 0, lockout_seconds: int = 0,
                password_length: int = 0, message: str = "") -> list[str]:
    canvas = Canvas(
        _title("ImageCtl Server Operations Console", "Authentication Required"),
        "<Enter> OK                    <Esc> Cancel (back to main screen)",
    )
    canvas.put(5, 9, "+----------------------------------------------------------+")
    for row in range(6, 16):
        canvas.put(row, 9, "|")
        canvas.put(row, 68, "|")
    canvas.put(16, 9, "+----------------------------------------------------------+")
    canvas.put(6, 12, "Enter the console admin password to continue.")
    canvas.put(8, 12, "Login Name:   admin                         (fixed)")
    masked = "*" * min(password_length, 25) + "_"
    canvas.put(10, 12, f"Password:     [ {masked:<27} ]")
    canvas.put(12, 12, "The password is verified locally against the server DB;")
    canvas.put(13, 12, "it works even when imagectl-server is not running.")
    if lockout_seconds:
        status = f"Locked after 3 failures. Try again in {lockout_seconds} s."
    else:
        status = f"Wrong password: {failures} of 3 attempts (30 s lockout)."
    canvas.put(15, 12, status)
    canvas.put(18, 9, message or "Authentication is required for F2 and F12 actions.")
    return canvas.lines()


def render_menu(snapshot: Snapshot, selected: int = 0, seconds: int = 90) -> list[str]:
    canvas = Canvas(
        _title("System Customization", f"admin  (auto-logout in {seconds} s)"),
        "<Up/Down> Select   <Enter> Open   <Esc> Back to main screen",
    )
    for row in range(3, 22):
        canvas.put(row, 35, "|")
    for index, item in enumerate(MENU_ITEMS):
        canvas.put(4 + index, 2, ("> " if index == selected else "  ") + item, 32)
    canvas.put(12, 3, "Not available here:")
    canvas.put(13, 4, "- delete images")
    canvas.put(14, 4, "- remove secondaries")
    canvas.put(15, 4, "- deploy network / MAC tables")
    canvas.put(16, 3, "(use the web console)")
    descriptions = (
        ("Configure Management Network", "The NIC the web console listens on",
         "(HTTPS 8081). DHCP client or a static", "address, gateway and DNS.",
         f"Current: {snapshot.management.name}  {snapshot.management.address or 'no IPv4'}  {snapshot.management.mode.upper()}",
         "Changes have a 60 s rollback window."),
        ("Reset Admin Password", "Sets admin/admin and forces a password", "change at the next web console login.",
         "The reset is journaled."),
        ("Restart Management Network", "Re-applies the saved configuration for", f"{snapshot.management.name} with rollback armed.",
         "Live state is read back before success."),
        ("Restart Services", "Restarts imagectl-server and dnsmasq only", "when dnsmasq is enabled, then reads back", "each service state."),
        ("Display TLS Certificate", "Shows SHA-256, certificate SANs and", "the not-after date read from the cert."),
        ("Re-run Initial Setup Wizard", "Starts imagectl-wizard-rerun.service.", "The wizard is shown on HTTPS 8081."),
    )
    for row, line in enumerate(descriptions[selected], start=4):
        canvas.put(row, 38, line, 39)
    canvas.put(14, 38, "The deploy network is not set here:")
    canvas.put(15, 38, "use the console (Network > Deploy).")
    return canvas.lines()


def render_network(snapshot: Snapshot, interfaces: list[NicInfo], form: dict[str, str],
                   field_index: int = 0, seconds: int = 90,
                   rollback_seconds: int = 0, error: str = "") -> list[str]:
    canvas = Canvas(
        _title("Configure Management Network", f"admin  (auto-logout in {seconds} s)"),
        "<Space> Toggle  <Tab> Next field  <Enter> Apply (60 s window)  <Esc> Cancel",
    )
    canvas.put(3, 3, "Interface (web console, HTTPS port 8081):")
    selected = form.get("interface", "")
    for index, nic in enumerate(interfaces[:3]):
        marker = "*" if nic.name == selected else " "
        current = " <- current" if nic.name == snapshot.management.name else ""
        model = (nic.model + " ") if nic.model else ""
        canvas.put(5 + index, 5,
                   f"({marker}) {nic.name:<7} {model[:18]:<18} {nic.mac:<17} "
                   f"link {nic.link} {nic.speed}{current}", 70)
    canvas.put(9, 3, "Address:")
    canvas.put(11, 5, f"({'*' if form.get('mode') == 'dhcp' else ' '}) DHCP client")
    canvas.put(12, 5, f"({'*' if form.get('mode') == 'static' else ' '}) Static")
    values = (
        ("IPv4 Address:", "address"), ("Prefix / Mask:", "netmask"),
        ("Gateway:", "gateway"), ("DNS:", "dns"),
    )
    for index, (label, key) in enumerate(values):
        cursor = "_" if field_index == index + 2 else ""
        canvas.put(14 + index, 8, f"{label:<16} [ {form.get(key, '')[:22]:<22}{cursor} ]")
    canvas.put(19, 3, "After <Enter>: applied for 60 s, then rolled back unless confirmed.")
    if rollback_seconds:
        canvas.put(20, 5, f"[C] Confirm   [R] Rollback now   (auto-rollback in {rollback_seconds} s)")
    elif error:
        canvas.put(20, 5, error, 70)
    else:
        canvas.put(20, 5, "Apply (60 s rollback) after reviewing every field.")
    return canvas.lines()


def render_power(snapshot: Snapshot, choice: str = "restart", typed: str = "",
                 seconds: int = 90, message: str = "") -> list[str]:
    canvas = Canvas(
        _title("Restart / Shut Down", f"admin  (auto-logout in {seconds} s)"),
        "<Enter> Confirm (only when the name matches)       <Esc> Cancel",
    )
    canvas.put(5, 9, "+----------------------------------------------------------+")
    for row in range(6, 18):
        canvas.put(row, 9, "|")
        canvas.put(row, 68, "|")
    canvas.put(18, 9, "+----------------------------------------------------------+")
    canvas.put(6, 12, "What should the server do?")
    canvas.put(8, 14, f"({'*' if choice == 'restart' else ' '}) Restart")
    canvas.put(9, 14, f"({'*' if choice == 'shutdown' else ' '}) Shut down")
    if snapshot.deploy_round and snapshot.deploy_round.state == "running":
        item = snapshot.deploy_round
        canvas.put(11, 12, f"WARNING: A deploy round is running: {item.writing} machines writing", 55)
        canvas.put(12, 14, f"{item.label}  {item.writing} of {item.targets} targets are writing", 52)
        canvas.put(13, 12, "Restarting now fails active targets; warn only, do not block.", 55)
    else:
        canvas.put(11, 12, "No running deploy round was found.")
    canvas.put(15, 12, "Type the hostname to confirm:")
    canvas.put(16, 14, f"{snapshot.hostname}:  [ {typed[:24]:<24}_ ]")
    if snapshot.rollback_interface:
        canvas.put(20, 9, f"Network rollback pending on {snapshot.rollback_interface} "
                          f"({snapshot.rollback_seconds} s left).", 62)
    else:
        canvas.put(20, 9, message or "The exact hostname is required before the command can run.", 62)
    return canvas.lines()


def render_tls(snapshot: Snapshot, seconds: int = 90) -> list[str]:
    canvas = Canvas(
        _title("TLS Certificate", f"admin  (auto-logout in {seconds} s)"),
        "<Esc> Back to System Customization",
    )
    fp1, fp2 = _fingerprint_lines(snapshot.fingerprint)
    canvas.put(5, 4, "SHA-256 fingerprint:")
    canvas.put(7, 6, fp1)
    canvas.put(8, 6, fp2)
    canvas.put(11, 4, "Subject Alternative Names:")
    for index, value in enumerate(snapshot.certificate_sans[:5]):
        canvas.put(12 + index, 6, f"- {value}")
    canvas.put(18, 4, f"Not after: {snapshot.certificate_not_after}")
    return canvas.lines()


def render_message(title: str, message: str, details: tuple[str, ...] = (),
                   seconds: int = 90) -> list[str]:
    canvas = Canvas(_title(title, f"admin  (auto-logout in {seconds} s)"),
                    "<Enter/Esc> Back to System Customization")
    canvas.put(6, 5, message, 68)
    for index, detail in enumerate(details[:8]):
        canvas.put(8 + index, 7, detail, 66)
    return canvas.lines()


def render_demo() -> str:
    snapshot = demo_snapshot()
    interfaces = [snapshot.management,
                  NicInfo("ens20", "", "static", "up", "1000",
                          "52:54:00:aa:14:01", "Intel I350"),
                  NicInfo("ens21", "", "dhcp", "down", "unknown",
                          "52:54:00:aa:99:01", "Intel I350")]
    form = {"interface": "ens20", "mode": "static", "address": "10.44.9.12",
            "netmask": "255.255.255.0", "gateway": "10.44.9.254",
            "dns": "10.44.9.2"}
    screens = [
        render_main(snapshot), render_auth(1, 0, 11), render_menu(snapshot),
        render_network(snapshot, interfaces, form, rollback_seconds=47),
        render_power(snapshot, typed="imagectl-t"),
    ]
    return "\n\n".join("\n".join(screen) for screen in screens)


@dataclass
class Runtime:
    data_dir: Path
    conn: object
    snapshot: Snapshot
    screen: str = "main"
    auth_target: str = "menu"
    password: str = ""
    auth: actions.AuthGuard = field(default_factory=actions.AuthGuard)
    authenticated_at: float = 0.0
    last_key_at: float = 0.0
    menu_index: int = 0
    message: str = ""
    result: actions.ActionResult | None = None
    power_choice: str = "restart"
    typed_hostname: str = ""
    form: dict[str, str] = field(default_factory=dict)
    form_field: int = 0
    interfaces: list[NicInfo] = field(default_factory=list)
    last_refresh: float = 0.0

    def seconds(self, now: float) -> int:
        return max(0, actions.AUTH_IDLE_SECONDS - int(now - self.last_key_at))


def _initial_form(snapshot: Snapshot) -> dict[str, str]:
    nic = snapshot.management
    address = nic.address.split("/")[0] if nic.address else ""
    prefix = nic.address.split("/", 1)[1] if "/" in nic.address else "255.255.255.0"
    return {"interface": nic.name, "mode": nic.mode if nic.mode in {"dhcp", "static"} else "dhcp",
            "address": address, "netmask": prefix, "gateway": "", "dns": ""}


def _preinstall_form(snapshot: Snapshot) -> dict[str, str]:
    nic = snapshot.management
    address = nic.address if nic.address else ""
    return {"interface": nic.name, "address": address, "gateway": ""}


def _refresh(runtime: Runtime) -> None:
    db_path = runtime.data_dir / "imagectl.db"
    if runtime.conn is None and db_path.is_file():
        runtime.conn = connect(db_path)
    runtime.snapshot = collect_snapshot(runtime.data_dir, conn=runtime.conn)
    runtime.last_refresh = time.monotonic()
    runtime.interfaces = list(runtime.snapshot.interfaces or (runtime.snapshot.management,))
    if not runtime.form:
        runtime.form = _initial_form(runtime.snapshot)


def _current_lines(runtime: Runtime, now: float) -> list[str]:
    seconds = runtime.seconds(now)
    if runtime.screen == "main":
        if runtime.snapshot.pre_install:
            return render_preinstall(runtime.snapshot, runtime.message)
        lock = runtime.auth.seconds_left(time.time())
        message = runtime.message or (f"Authentication locked for {lock} more seconds." if lock else "")
        return render_main(runtime.snapshot, message)
    if runtime.screen == "preinstall_network":
        return render_preinstall_network(
            runtime.interfaces, runtime.form, runtime.form_field, runtime.message)
    if runtime.screen == "auth":
        return render_auth(runtime.auth.failures, runtime.auth.seconds_left(time.time()),
                           len(runtime.password), runtime.message)
    if runtime.screen == "menu":
        return render_menu(runtime.snapshot, runtime.menu_index, seconds)
    if runtime.screen == "network":
        return render_network(runtime.snapshot, runtime.interfaces, runtime.form,
                              runtime.form_field, seconds, error=runtime.message)
    if runtime.screen == "network_confirm":
        marker = netcfg_rollback.read_pending(runtime.data_dir / "netcfg")
        left = max(0, int(marker.deadline - time.time())) if marker else 0
        return render_network(runtime.snapshot, runtime.interfaces, runtime.form,
                              runtime.form_field, seconds, rollback_seconds=left,
                              error=runtime.message)
    if runtime.screen == "power":
        return render_power(runtime.snapshot, runtime.power_choice,
                            runtime.typed_hostname, seconds, runtime.message)
    if runtime.screen == "tls":
        return render_tls(runtime.snapshot, seconds)
    if runtime.screen == "reset_confirm":
        return render_message("Reset Admin Password",
                              "Press Y to set admin/admin and require a change; Esc cancels.",
                              seconds=seconds)
    if runtime.screen == "wizard_confirm":
        return render_message("Re-run Initial Setup Wizard",
                              "Press Y to start imagectl-wizard-rerun.service; Esc cancels.",
                              (f"URL: {runtime.snapshot.console_url}",), seconds)
    result = runtime.result or actions.ActionResult(False, runtime.message or "No result.")
    return render_message("Action Result", result.message, result.details, seconds)


def _authenticate(runtime: Runtime) -> None:
    if runtime.auth.authenticate(runtime.conn, runtime.password):
        runtime.screen = runtime.auth_target
        runtime.last_key_at = time.monotonic()
        runtime.authenticated_at = runtime.last_key_at
        runtime.message = ""
    elif runtime.auth.locked_until:
        runtime.screen = "main"
        runtime.message = "Authentication locked for 30 seconds after 3 failures."
    else:
        runtime.message = "Wrong password."
    runtime.password = ""


def _open_menu_item(runtime: Runtime) -> None:
    item = runtime.menu_index
    runtime.message = ""
    if item == 0:
        runtime.screen = "network"
        runtime.form = _initial_form(runtime.snapshot)
        runtime.form_field = 0
    elif item == 1:
        runtime.screen = "reset_confirm"
    elif item == 2:
        runtime.result = actions.restart_management_network(
            runtime.conn, runtime.snapshot.management.name, runtime.data_dir)
        runtime.screen = "result"
    elif item == 3:
        runtime.result = actions.restart_services(runtime.conn)
        runtime.screen = "result"
    elif item == 4:
        actions.note_tls_display(runtime.conn)
        runtime.screen = "tls"
    else:
        runtime.screen = "wizard_confirm"


def _network_key(runtime: Runtime, key: object) -> None:
    if key in (9, "\t"):
        runtime.form_field = (runtime.form_field + 1) % 6
        return
    if key == " ":
        if runtime.form_field == 0 and runtime.interfaces:
            names = [nic.name for nic in runtime.interfaces]
            index = names.index(runtime.form["interface"]) if runtime.form["interface"] in names else 0
            runtime.form["interface"] = names[(index + 1) % len(names)]
        elif runtime.form_field == 1:
            runtime.form["mode"] = "static" if runtime.form["mode"] == "dhcp" else "dhcp"
        return
    field_names = ("address", "netmask", "gateway", "dns")
    if 2 <= runtime.form_field <= 5:
        field = field_names[runtime.form_field - 2]
        if key in (8, 127, "KEY_BACKSPACE"):
            runtime.form[field] = runtime.form[field][:-1]
        elif isinstance(key, str) and len(key) == 1 and key in "0123456789.,":
            runtime.form[field] += key


def _preinstall_network_key(runtime: Runtime, key: object) -> None:
    if key in (9, "\t"):
        runtime.form_field = (runtime.form_field + 1) % 3
        return
    if key == " " and runtime.form_field == 0 and runtime.interfaces:
        names = [nic.name for nic in runtime.interfaces]
        current = runtime.form.get("interface", "")
        index = names.index(current) if current in names else 0
        runtime.form["interface"] = names[(index + 1) % len(names)]
        return
    if runtime.form_field in (1, 2):
        field = "address" if runtime.form_field == 1 else "gateway"
        if key in (8, 127, "KEY_BACKSPACE"):
            runtime.form[field] = runtime.form[field][:-1]
        elif isinstance(key, str) and len(key) == 1 and key in "0123456789./":
            runtime.form[field] += key


def _handle_key(runtime: Runtime, key: object, curses_module) -> bool:
    now = time.monotonic()
    if key != -1:
        runtime.last_key_at = now
    enter = key in (10, 13, curses_module.KEY_ENTER)
    escape = key == 27
    if runtime.screen != "main" and runtime.screen != "auth" and runtime.seconds(now) <= 0:
        runtime.screen = "main"
        runtime.message = "Session expired after 90 seconds without a key."
        return True
    if runtime.screen == "main":
        if runtime.snapshot.pre_install:
            if key == curses_module.KEY_F5:
                _refresh(runtime)
            elif key == curses_module.KEY_F2:
                runtime.screen = "preinstall_network"
                runtime.form = _preinstall_form(runtime.snapshot)
                runtime.form_field = 0
                runtime.message = ""
            return True
        if key == curses_module.KEY_F5:
            _refresh(runtime)
        elif key in (curses_module.KEY_F2, curses_module.KEY_F12):
            if runtime.auth.seconds_left(time.time()):
                runtime.message = "Authentication is temporarily locked."
            else:
                runtime.auth_target = "menu" if key == curses_module.KEY_F2 else "power"
                runtime.screen = "auth"
                runtime.password = ""
                runtime.message = ""
        return True
    if runtime.screen == "auth":
        if escape:
            runtime.screen = "main"
        elif enter:
            _authenticate(runtime)
        elif key in (8, 127, curses_module.KEY_BACKSPACE):
            runtime.password = runtime.password[:-1]
        elif isinstance(key, str) and key.isprintable() and len(key) == 1:
            runtime.password += key
        return True
    if runtime.screen == "preinstall_network":
        if escape:
            runtime.screen = "main"
            runtime.message = ""
        elif enter:
            runtime.result = actions.apply_preinstall_static(
                runtime.form, {nic.name for nic in runtime.interfaces})
            runtime.message = runtime.result.message
            if runtime.result.ok:
                runtime.screen = "main"
                _refresh(runtime)
        else:
            _preinstall_network_key(runtime, key)
        return True
    if escape:
        if runtime.screen == "network_confirm":
            active, _detail = actions.netcfg_host.timer_active()
            runtime.result = actions.ActionResult(
                active,
                ("Rollback remains armed; the previous configuration will return."
                 if active else "Rollback timer is not active; recovery was not verified."),
            )
            runtime.screen = "result"
            return True
        runtime.screen = "main" if runtime.screen in {"menu", "power"} else "menu"
        return True
    if runtime.screen == "menu":
        if key == curses_module.KEY_UP:
            runtime.menu_index = (runtime.menu_index - 1) % len(MENU_ITEMS)
        elif key == curses_module.KEY_DOWN:
            runtime.menu_index = (runtime.menu_index + 1) % len(MENU_ITEMS)
        elif enter:
            _open_menu_item(runtime)
    elif runtime.screen == "network":
        if enter:
            runtime.result = actions.apply_network(
                runtime.conn, runtime.form, runtime.data_dir,
                {nic.name for nic in runtime.interfaces})
            if runtime.result.ok:
                runtime.screen = "network_confirm"
            else:
                runtime.message = runtime.result.message
        else:
            _network_key(runtime, key)
    elif runtime.screen == "network_confirm":
        if key in ("c", "C"):
            runtime.result = actions.confirm_network(runtime.conn, runtime.data_dir)
            runtime.screen = "result"
        elif key in ("r", "R"):
            runtime.result = actions.request_network_rollback(runtime.conn, runtime.data_dir)
            runtime.screen = "result"
    elif runtime.screen == "power":
        if key in (curses_module.KEY_UP, curses_module.KEY_DOWN, " "):
            runtime.power_choice = "shutdown" if runtime.power_choice == "restart" else "restart"
        elif enter:
            runtime.result = actions.power_action(
                runtime.conn, runtime.power_choice, runtime.typed_hostname,
                runtime.snapshot.hostname)
            runtime.screen = "result"
        elif key in (8, 127, curses_module.KEY_BACKSPACE):
            runtime.typed_hostname = runtime.typed_hostname[:-1]
        elif isinstance(key, str) and key.isprintable() and len(key) == 1:
            runtime.typed_hostname += key
    elif runtime.screen == "reset_confirm" and key in ("y", "Y"):
        runtime.result = actions.reset_admin_password(runtime.conn)
        runtime.screen = "result"
    elif runtime.screen == "wizard_confirm" and key in ("y", "Y"):
        runtime.result = actions.start_wizard(
            runtime.conn, runtime.snapshot.console_url, run_argv)
        runtime.screen = "result"
    elif runtime.screen in {"result", "tls"} and enter:
        runtime.screen = "menu"
    return True


def _draw(stdscr, lines: list[str], curses_module) -> None:
    stdscr.erase()
    height, width = stdscr.getmaxyx()
    if height < HEIGHT or width < WIDTH:
        message = "terminal too small (80x25 needed)"
        try:
            stdscr.addstr(0, 0, message[:max(0, width - 1)])
        except curses_module.error:
            pass
        stdscr.refresh()
        return
    top, left = (height - HEIGHT) // 2, (width - WIDTH) // 2
    for index, line in enumerate(lines):
        attr = curses_module.A_NORMAL
        if "WARNING:" in line and curses_module.has_colors():
            attr = curses_module.color_pair(1) | curses_module.A_BOLD
        try:
            stdscr.addstr(top + index, left, line, attr)
        except curses_module.error:
            # ncurses may report ERR after successfully painting bottom-right.
            pass
    stdscr.refresh()


def curses_main(stdscr, data_dir: Path = DATA_DIR) -> None:
    import curses
    curses.curs_set(0)
    stdscr.keypad(True)
    stdscr.timeout(200)
    if curses.has_colors():
        curses.start_color()
        curses.init_pair(1, curses.COLOR_RED, curses.COLOR_BLACK)
    db_path = data_dir / "imagectl.db"
    conn = connect(db_path) if db_path.is_file() else None
    snapshot = collect_snapshot(data_dir, conn=conn)
    runtime = Runtime(data_dir, conn, snapshot,
                      last_key_at=time.monotonic(), last_refresh=time.monotonic())
    runtime.form = _initial_form(snapshot)
    runtime.interfaces = list(snapshot.interfaces or (snapshot.management,))
    while True:
        now = time.monotonic()
        if runtime.screen == "main" and now - runtime.last_refresh >= 5:
            _refresh(runtime)
        if runtime.screen not in {"main", "auth"} and runtime.seconds(now) <= 0:
            runtime.screen = "main"
            runtime.message = "Session expired after 90 seconds without a key."
        if (runtime.screen == "network_confirm"
                and netcfg_rollback.read_pending(runtime.data_dir / "netcfg") is None):
            active, detail = actions.netcfg_host.timer_active()
            runtime.result = actions.ActionResult(
                active,
                "The pending marker is gone; the timed rollback has completed.",
                (str(detail),),
            )
            runtime.screen = "result"
        _draw(stdscr, _current_lines(runtime, now), curses)
        try:
            key = stdscr.get_wch()
        except curses.error:
            key = -1
        _handle_key(runtime, key, curses)


def run(data_dir: Path = DATA_DIR) -> None:
    import curses
    curses.wrapper(curses_main, data_dir)
