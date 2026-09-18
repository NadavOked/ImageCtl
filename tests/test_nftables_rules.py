"""#1074 — מחולל nftables לפי וילן, והמתקין שקורא לו.

שלוש תצורות מול snapshot: ראשי, משני עם primary-ip, ו-mcast-ports שונה.
האינווריאנטים נבדקים גם בלי snapshot (8443, 8081, policy drop) כדי
שסטיה בפורמט לא תסתיר כלל שגוי.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "install" / "nftables-rules.sh"
INSTALLER = REPO / "install" / "setup-boot-server.sh"
FIXTURES = REPO / "tests" / "fixtures" / "nftables"
UPGRADE = REPO / "tools" / "server-upgrade.sh"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
needs_bash = requires_native(("bash", BASH), why="הרצת המחולל POSIX")


def generate(*args: str) -> str:
    assert BASH
    proc = subprocess.run(
        [BASH, str(GEN), *args],
        capture_output=True, text=True, encoding="utf-8", check=False,
        stdin=subprocess.DEVNULL,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.replace("\r\n", "\n")


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8").replace("\r\n", "\n")


PRIMARY_ARGS = ("--deploy-if", "eth-deploy", "--servers-if", "eth-srv")


@needs_bash
def test_primary_snapshot_matches_fixture() -> None:
    assert generate(*PRIMARY_ARGS) == fixture("primary.conf")


@needs_bash
def test_secondary_snapshot_matches_fixture() -> None:
    got = generate(*PRIMARY_ARGS, "--primary-ip", "10.10.10.8")
    assert got == fixture("secondary.conf")


@needs_bash
def test_custom_mcast_ports_snapshot_matches_fixture() -> None:
    got = generate(*PRIMARY_ARGS, "--mcast-ports", "2232-2233")
    assert got == fixture("mcast-custom.conf")


@needs_bash
def test_primary_does_not_open_8443() -> None:
    """הראשי יוזם — אין כלל כניסה ל-8443, רק הערה."""
    got = generate(*PRIMARY_ARGS)
    assert "tcp dport 8443" not in got
    assert "ip saddr" not in got
    assert "policy drop" in got


@needs_bash
def test_secondary_opens_8443_only_from_primary_ip() -> None:
    got = generate(*PRIMARY_ARGS, "--primary-ip", "10.10.10.8")
    assert 'iifname "eth-srv" ip saddr 10.10.10.8 tcp dport 8443 accept' in got
    assert 'iifname "eth-deploy" ip saddr' not in got
    assert "tcp dport 8443" in got


@needs_bash
def test_console_8081_is_on_servers_if_not_deploy() -> None:
    got = generate(*PRIMARY_ARGS)
    assert 'iifname "eth-srv" tcp dport 8081 accept' in got
    assert 'iifname "eth-deploy" tcp dport 8081' not in got
    assert 'iifname "eth-deploy" tcp dport 8080 accept' in got
    assert 'iifname "eth-deploy" tcp dport 8082 accept' in got


@needs_bash
def test_input_and_forward_are_drop_output_is_accept() -> None:
    got = generate(*PRIMARY_ARGS)
    assert "chain input" in got
    assert "policy drop" in got
    assert "chain output" in got and "policy accept" in got
    assert "chain forward" in got
    # input + forward = שני policy drop; output = accept.
    assert got.count("policy drop") == 2
    assert got.count("policy accept") == 1


@needs_bash
def test_custom_mcast_replaces_production_ports() -> None:
    got = generate(*PRIMARY_ARGS, "--mcast-ports", "2232-2233")
    assert "udp dport 2232-2233 accept" in got
    assert "udp dport 9000-9001" not in got   # פורט-הפצה-מכוון: מוודא שברירת המחדל הוחלפה


@needs_bash
def test_deploy_opens_dhcp_tftp_and_not_ssh() -> None:
    got = generate(*PRIMARY_ARGS)
    assert 'iifname "eth-deploy" udp dport 67 accept' in got
    assert 'iifname "eth-deploy" udp dport 69 accept' in got
    assert 'iifname "eth-srv" tcp dport 22 accept' in got
    assert 'iifname "eth-deploy" tcp dport 22' not in got
    assert 'log prefix "imagectl-drop "' in got
    assert "limit rate 5/minute" in got


@needs_bash
def test_without_deploy_if_only_the_servers_vlan_opens() -> None:
    """‏#1088: במסירה רשת ההפצה טרם הוגדרה — המחולל רץ בלי --deploy-if,
    פותח את וילן השרתים (קונסולה, SSH) ו**סוגר** את כל פורטי ההפצה.
    הקונסולה מריצה אותו שוב עם הכרטיס כשמודלק DHCP (deploy_net)."""
    text = generate("--servers-if", "eth-srv")
    assert 'iifname "eth-srv" tcp dport 8081 accept' in text
    assert 'iifname "eth-srv" tcp dport 22 accept' in text
    for closed in ("udp dport 67", "udp dport 69", "tcp dport 8080",
                   "tcp dport 8082", "udp dport 9000-9001"):   # פורט-הפצה-מכוון: סגור בלי כרטיס
        assert closed not in text, closed
    assert "1088" in text
    assert "policy drop" in text


@needs_bash
def test_missing_servers_if_still_fails() -> None:
    proc = subprocess.run(
        [BASH, str(GEN), "--deploy-if", "eth-deploy"],
        capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
    )
    assert proc.returncode != 0
    assert "servers-if" in proc.stderr


def _nft_check(path) -> subprocess.CompletedProcess:
    # `nft -c` פותח netlink גם בבדיקה יבשה — דורש CAP_NET_ADMIN. במעבדה
    # רצים כ-root; ב-GitHub Actions לא, אבל יש sudo בלי סיסמה. בלי אף אחד
    # מהשניים — דילוג בשם, לא "עבר" (עיקרון 5).
    cmd = ["nft", "-c", "-f", str(path)]
    if getattr(os, "geteuid", lambda: 0)() != 0:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True,
                          stdin=subprocess.DEVNULL).returncode != 0:
            pytest.skip("nft -c דורש CAP_NET_ADMIN ואין root או sudo -n")
        cmd = ["sudo", "-n", *cmd]
    return subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", stdin=subprocess.DEVNULL,
    )


@requires_native("nft", why="בדיקת תחביר nftables על דביאן — דילוג בווינדוס")
def test_nft_accepts_the_three_snapshots() -> None:
    for name in ("primary.conf", "secondary.conf", "mcast-custom.conf"):
        proc = _nft_check(FIXTURES / name)
        assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_installer_installs_nftables_and_has_no_firewall_flag() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert "--no-firewall" in text
    assert "R20-F1" in text
    assert "nftables-rules.sh" in text
    assert "nft -c -f" in text
    assert "apt-get install" in text and "nftables" in text
    assert "systemctl enable --now nftables" in text


def test_installer_binds_tftp_to_the_deploy_interface() -> None:
    """R20-F1: TFTP בלי interface= מאזין על כל הכרטיסים. המתקין כותב
    את ההגבלה בקובץ הבסיס (imagectl.conf) כשיש כרטיס הפצה — ובלעדיו
    (#1088) אינו מפעיל dnsmasq כלל, עד שהקונסולה תכתוב את השורה."""
    text = INSTALLER.read_text(encoding="utf-8")
    assert "interface=$IFACE" in text
    assert "bind-interfaces" in text
    assert "systemctl disable --now dnsmasq" in text


def test_upgrade_does_not_enable_nftables() -> None:
    """שדרוג של שרת קיים לא ינתק אותו — אין enable nftables בלי דגל."""
    text = UPGRADE.read_text(encoding="utf-8")
    assert "enable --now nftables" not in text
    assert "nftables-rules.sh" not in text
