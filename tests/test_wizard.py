"""First-boot web wizard (#910): validation, secret handoff, and terminal states."""

from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest

from server.wizard.__main__ import main
from server.wizard.core import WizardConfig, WizardPaths, WizardState, validate_config

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "wizard" / "static"
NICS = [{"name": "eth-test", "mac": "02:00:00:00:00:01", "link": "up",
         "addresses": ["10.44.10.37/24"], "current_ip": "10.44.10.37/24"}]


def valid(**changes) -> WizardConfig:
    base = WizardConfig(
        role="standalone", primary_url="", interface="eth-test", mode="dhcp",
        address="", netmask="", gateway="", dns="", hostname="imagectl-haifa",
        password="Imag3ctl!haifa", password_confirm="Imag3ctl!haifa",
    )
    return replace(base, **changes)


@pytest.mark.parametrize(("changes", "field", "message"), [
    ({"role": "secondary", "primary_url": ""}, "primary_url", "חובה להזין את כתובת השרת הראשי עבור שרת משני."),
    ({"interface": "not-real"}, "interface", "יש לבחור כרטיס רשת קיים מרשימת הכרטיסים."),
    ({"mode": "static", "address": "999.1.1.1", "netmask": "255.255.255.0"}, "address", "כתובת ה-IP אינה תקינה (נדרש מבנה IPv4 תקין)."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.0.255.0"}, "netmask", "מסכת הרשת אינה תקינה."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.255.255.0", "gateway": "bad"}, "gateway", "כתובת השער אינה תקינה (נדרש מבנה IPv4 תקין)."),
    ({"mode": "static", "address": "10.1.1.2", "netmask": "255.255.255.0", "dns": "10.1.1.1,no"}, "dns", "כתובת ה-DNS אינה תקינה (יש להפריד כתובות IPv4 בפסיק)."),
    ({"hostname": "Bad_Name"}, "hostname", "שם השרת יכול להכיל אותיות אנגליות קטנות, ספרות ומקפים בלבד; אסור להתחיל או לסיים במקף."),
    ({"password": "short!1", "password_confirm": "short!1"}, "password", "הסיסמה חייבת להכיל לפחות 8 תווים"),
    ({"password_confirm": "Imag3ctl!other"}, "password_confirm", "הסיסמאות שהזנת אינן תואמות."),
])
def test_server_validation_uses_the_hebrew_screen_messages(changes, field, message) -> None:
    errors = validate_config(valid(**changes), {"eth-test"})
    assert errors[field] == message


def test_rerun_requires_existing_password_field() -> None:
    errors = validate_config(valid(current_password=""), {"eth-test"}, rerun=True)
    assert errors["current_password"] == "בהרצה חוזרת חובה להזין את סיסמת admin הקיימת."


def _paths(tmp_path: Path, installer: Path, verify: Path) -> WizardPaths:
    return WizardPaths(
        source_dir=REPO, app_dir=REPO, data_dir=tmp_path / "data",
        status_file=tmp_path / "etc" / "firstboot.status",
        stamp_file=tmp_path / "data" / ".firstboot-done",
        installer=installer, verify=verify, http_root=tmp_path / "boot",
    )


def _write_fake(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def test_installer_gets_password_on_fd_not_argv(tmp_path: Path) -> None:
    installer = tmp_path / "fake-installer.py"
    verify = tmp_path / "fake-verify.py"
    record = tmp_path / "installer-record.json"
    _write_fake(installer, """import json, os, pathlib, sys
fd = int(sys.argv[sys.argv.index('--admin-pass-fd') + 1])
password = os.read(fd, 4096).decode()
pathlib.Path(__file__).with_name('installer-record.json').write_text(
    json.dumps({'argv': sys.argv[1:], 'password': password}), encoding='utf-8')
""")
    _write_fake(verify, "import sys\nsys.exit(0)\n")
    state = WizardState(_paths(tmp_path, installer, verify), dev=False,
                        interfaces_provider=lambda: NICS,
                        hostname_setter=lambda _hostname: 0)
    state.start_apply(valid())
    state.wait(20)
    assert state.progress()["state"] == "done"
    got = json.loads(record.read_text(encoding="utf-8"))
    assert got["password"] == "Imag3ctl!haifa"
    assert "Imag3ctl!haifa" not in got["argv"]
    assert got["argv"][-4:] == ["--admin-user", "admin", "--admin-pass-fd", "3" if os.name == "posix" else "0"]
    assert got["argv"][:4] == ["--servers-if", "eth-test", "--servers-mode", "dhcp"]


def test_failing_installer_sets_named_state_and_never_writes_stamp(tmp_path: Path) -> None:
    state = WizardState(_paths(tmp_path, tmp_path / "unused", tmp_path / "unused2"),
                        dev=True, interfaces_provider=lambda: NICS)
    state.dev = False
    state._run = lambda argv, **kwargs: 7 if "setup-boot-server" in " ".join(argv) or "unused" in " ".join(argv) else 0  # type: ignore[method-assign]
    state.start_apply(valid())
    state.wait(5)
    assert state.progress()["state"] == "installer-failed"
    assert not state.paths.stamp_file.exists()
    assert "state=installer-failed" in state.paths.status_file.read_text(encoding="utf-8")


def test_cli_refuses_existing_install_without_rerun(tmp_path: Path, capsys) -> None:
    stamp = tmp_path / ".firstboot-done"
    stamp.touch()
    assert main(["--data-dir", str(tmp_path), "--stamp-file", str(stamp)]) == 2
    assert "--rerun" in capsys.readouterr().err


def test_html_has_six_approved_titles_and_forbidden_wording_is_absent() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for title in ("תפקיד השרת במערכת", "הגדרת רשת הניהול", "שם השרת",
                  "חשבון הניהול", "סיכום לפני החלה", "סיום ההתקנה"):
        assert title in html
    joined = "\n".join(p.read_text(encoding="utf-8") for p in STATIC.iterdir() if p.suffix in {".html", ".js"})
    assert "SSH" not in joined
    assert "עצמאי" not in joined


def test_wizard_static_files_have_no_external_resources() -> None:
    for path in STATIC.iterdir():
        if path.suffix not in {".html", ".js", ".css"}:
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?:src|href)=[\"']https?://", text), path
