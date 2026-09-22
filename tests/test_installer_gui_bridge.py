"""‏installer/gui-bridge.sh — הדלת היחידה של גואי המתקין אל המנוע (#1190 חלק 2).

הגואי שולח JSON על stdin ופעולה ב-argv, ומקבל key=value. כאן המנוע הוא
shim שרושם מה קיבל ומחזיר מה שהטסט מכתיב — כמו בטסטי הסוכן. הבדיקות:
המלאי ממזג דיסקים וכרטיסים (עם ניסוחי #1183); ‏apply כותב answers ב-0600
ומתחיל את המנוע עם --state; ‏validate מחזיר שגיאה לשדה הנכון מקוד הכשל של
המנוע; כללי הסיסמה; ‏eject שנכשל מדווח ולא נבלע; ‏reboot רק אחרי eject.
דורש sh + jq (‏Linux/Testrunner; בווינדוס אין jq — דילוג מפורש)."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BRIDGE = REPO / "installer" / "gui-bridge.sh"
SH = shutil.which("sh")
JQ = shutil.which("jq")
pytestmark = pytest.mark.skipif(SH is None or JQ is None or os.name == "nt",
                                reason="sh + jq on Linux (Testrunner) — the bridge is busybox-sh with jq")


def _machine(tmp_path: Path) -> dict:
    root = tmp_path / "root"
    for nic, mac, oper, carrier in (("ens33", "00:50:56:01:02:01", "up", "1"), ("ens34", "00:50:56:01:02:02", "down", "")):
        d = root / "sys/class/net" / nic; (d / "device").mkdir(parents=True)
        (d / "address").write_text(mac + "\n"); (d / "operstate").write_text(oper + "\n")
        if carrier:
            (d / "carrier").write_text(carrier + "\n")
        (d / "device/vendor").write_text("0x15ad\n"); (d / "device/device").write_text("0x07b0\n")
    run = tmp_path / "run"; (run / "dhcp").mkdir(parents=True)
    (run / "dhcp/ens33").write_text("state=lease\naddress=10.10.10.8/24\ngateway=10.10.10.1\ndns=10.10.10.1\n")
    (run / "dhcp/ens34").write_text("state=no-carrier\n")
    bin_dir = tmp_path / "bin"; bin_dir.mkdir()
    calls = tmp_path / "calls"
    engine = tmp_path / "engine"
    engine.write_text(f"""#!/bin/sh
printf '%s\\n' "$*" >> {calls}
case "$1" in
  --inventory) printf 'disk=/dev/sda|model=VMware Virtual disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0\\n'; exit 0 ;;
esac
cp "$1" {tmp_path}/answers.seen
if grep -q '^hostname=bad host$' "$1"; then printf 'installer: [invalid-hostname] hostname must be an RFC-1123 label\\n' >&2; exit 2; fi
if [ "$2" = --dry-run ]; then exit 0; fi
printf 'state=partitioning\\npct=5\\ntitle=partitioning\\n' > "$4"
""")
    engine.chmod(engine.stat().st_mode | stat.S_IEXEC)
    for tool, body in (("eject", f"printf 'eject %s\\n' \"$@\" >> {calls}; exit ${{EJECT_RC:-0}}\n"),
                       ("reboot", f"printf 'reboot %s\\n' \"$@\" >> {calls}\n"),
                       ("umount", "exit 0\n"), ("sync", "exit 0\n"),
                       ("ip", "exit 0\n"), ("curl", "exit 7\n")):
        p = bin_dir / tool; p.write_text("#!/bin/sh\n" + body); p.chmod(0o755)
    return {"root": root, "run": run, "bin": bin_dir, "calls": calls, "engine": engine}


def bridge(m: dict, action: str, form: dict | None = None, **env: str) -> tuple[int, dict[str, list[str]], str]:
    e = {**os.environ, "PATH": f"{m['bin']}:{os.environ['PATH']}", "SYSROOT": str(m["root"]),
         "RUN_DIR": str(m["run"]), "ENGINE": str(m["engine"]), "DHCP_DIR": str(m["run"] / "dhcp"),
         "STATE_FILE": str(m["run"] / "install.state"), "ANSWERS": str(m["run"] / "answers"),
         "ENGINE_LOG": str(m["run"] / "install.log"), "MEDIA_FILE": str(m["run"] / "install-media"), **env}
    proc = subprocess.run([SH, str(BRIDGE), action], input=json.dumps(form or {}), capture_output=True,
                          text=True, env=e, stdin=None if False else None) if False else \
        subprocess.run([SH, str(BRIDGE), action], input=json.dumps(form or {}), capture_output=True, text=True, env=e)
    out: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1); out.setdefault(k, []).append(v)
    return proc.returncode, out, proc.stderr


FORM = {"disk": "/dev/sda", "role": "standalone", "primary_url": "", "interface": "ens33", "mode": "dhcp",
        "address": "", "netmask": "", "gateway": "", "dns": "", "hostname": "imagectl-ta",
        "admin_user": "nadav", "password": "Aa123456!", "password_confirm": "Aa123456!"}


def test_inventory_merges_disks_and_nics_with_the_1183_link_words(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "inventory")
    assert rc == 0, err
    assert out["disk"] == ["/dev/sda|model=VMware Virtual disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0"]
    rows = {r.split("|")[0]: r.split("|") for r in out["nic"]}
    assert rows["ens33"][1:6] == ["VMware 0x07b0", "00:50:56:01:02:01", "מחובר", "10.10.10.8/24", "DHCP · שער 10.10.10.1"]
    assert rows["ens33"][6:] == ["10.10.10.8", "255.255.255.0", "10.10.10.1", "10.10.10.1"]
    assert rows["ens34"][3] == "לא מודלק" and rows["ens34"][4] == "—"
    assert out["interface"] == ["ens33"] and out["mode"] == ["dhcp"] and out["admin_user"] == ["admin"]


def test_apply_writes_the_answers_0600_and_starts_the_engine_with_a_state_file(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "apply", FORM)
    assert rc == 0, err
    assert "job" in out and "errors.password" not in out
    answers = m["run"] / "answers"
    assert stat.S_IMODE(answers.stat().st_mode) == 0o600
    text = answers.read_text()
    assert "admin_user=nadav\n" in text and "admin_pass=Aa123456!\n" in text and "servers_mac=00:50:56:01:02:01\n" in text
    assert "servers_if=ens33\n" in text and "servers_mode=dhcp\n" in text and "servers_addr=\n" in text
    import time
    for _ in range(40):  # the real run is started in the background; give the shim a moment
        lines = m["calls"].read_text().splitlines()
        if (m["run"] / "install.state").exists() and lines and "--dry-run" not in lines[-1]:
            break
        time.sleep(0.05)
    calls = m["calls"].read_text()
    assert f"--dry-run --state" in calls, "the engine's own validation runs first (dry-run)"
    assert any(l.startswith(str(answers)) and "--state" in l and "--dry-run" not in l for l in calls.splitlines()), "the real run"
    assert (m["run"] / "install.state").read_text().startswith("state=partitioning")


def test_validate_maps_the_engine_failure_code_to_the_field_and_never_touches_a_disk(tmp_path):
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "validate", {**FORM, "hostname": "bad host", "step": "3"})
    assert rc == 0, err
    assert out["errors.hostname"] == ["hostname must be an RFC-1123 label"]
    calls = m["calls"].read_text()
    assert "--dry-run" in calls and all("--dry-run" in l for l in calls.splitlines() if "--inventory" not in l)
    assert not (m["run"] / "answers").exists(), "validate never writes the real answers"


def test_password_rules_live_in_the_bridge(tmp_path):
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "validate", {**FORM, "password": "short", "password_confirm": "other", "step": "4"})
    assert rc == 0
    assert "לפחות 8 תווים" in out["errors.password"] and "נדרשת לפחות ספרה אחת" in out["errors.password"]
    assert out["errors.password_confirm"] == ["הסיסמאות שהזנת אינן תואמות"]
    rc, out, _ = bridge(m, "apply", {**FORM, "password": "short", "password_confirm": "short"})
    assert "errors.password" in out and "job" not in out and not (m["run"] / "answers").exists()


def test_progress_strips_ansi_and_marks_the_engine_log_lines(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install.state").write_text("state=packages\npct=40\ntitle=installing packages\n")
    (m["run"] / "install.log").write_text("\x1b[32m==>\x1b[0m unpacking\nSetting up grub\n")
    rc, out, _ = bridge(m, "progress")
    assert rc == 0 and out["state"] == ["packages"] and out["pct"] == ["40"]
    assert out["output"] == ["==> unpacking", "Setting up grub"]


def test_eject_failure_is_reported_and_reboot_only_after_eject(tmp_path):
    m = _machine(tmp_path)
    (m["run"] / "install-media").write_text("/dev/sr0\n")
    rc, out, _ = bridge(m, "eject", EJECT_RC="1")
    assert rc == 0 and out["ejected"] == ["0"] and "eject /dev/sr0 failed" in out["error"][0]
    rc, out, _ = bridge(m, "boot-local")
    calls = m["calls"].read_text().splitlines()
    assert calls.index("eject /dev/sr0") < calls.index("reboot -f")


def test_check_primary_failure_uses_the_mockup_text(tmp_path):
    m = _machine(tmp_path)
    rc, out, _ = bridge(m, "check-primary", {"primary_url": "https://10.10.10.8:8443"})
    assert rc == 0 and out["ok"] == ["0"]
    assert out["error"] == ["התקשרות אל 10.10.10.8:8443 בפורט 8443 נכשלה. ודא כי חומת האש שבין האתרים פתוחה."]
    rc, out, _ = bridge(m, "check-primary", {"primary_url": "http://x"})
    assert out["ok"] == ["0"] and "https://HOST:8443" in out["error"][0]


def test_bridge_is_posix_sh_and_under_the_line_wall():
    text = BRIDGE.read_text(encoding="utf-8")
    assert text.startswith("#!/bin/sh\n")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    for bashism in ("[[ ", "function ", "$(( ", "==", "declare ", "local "):
        assert bashism not in code, bashism
    assert len(text.splitlines()) <= 300


def test_validation_before_the_admin_step_does_not_demand_a_password(tmp_path):
    """הגואי מאמת כל מסך בנפרד; לפני מסך 4 הסיסמה ריקה בצדק — המנוע לא אמור
    לענות 'admin_pass must not be empty' על מסך הרשת (נראה ב-ESXi 21/09)."""
    m = _machine(tmp_path)
    rc, out, err = bridge(m, "validate", {**FORM, "password": "", "password_confirm": "", "step": "2"})
    assert rc == 0, err
    assert not [k for k in out if k.startswith("errors.")], out
    seen = (tmp_path / "answers.seen").read_text()
    assert "admin_pass=not-yet-asked-1!" in seen, "placeholder for the dry-run, never the real answers"
    assert not (m["run"] / "answers").exists()
