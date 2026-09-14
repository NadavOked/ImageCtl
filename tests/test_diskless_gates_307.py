"""בודקי diskless-PXE אינם מדווחים תקין כשמצאו בעיה, או כשלא בדקו (#307).

חמישה כלים: check-host / check-runtime-deps מדפיסים WARN ויוצאים 0;
wait-online מתעלם מה-URL; imagectl-env נופל ל-localhost; detect-target
מאבד דיסק ש-MODEL שלו מכיל רווחים, ו-plan.sh מקפל שלושה מצבים ל-NONE.
הטסט מריץ אותם כתהליך POSIX אמיתי.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "vendor" / "diskless-pxe"
CHECK_HOST = PACKAGE / "tools" / "check-host.sh"
CHECK_DEPS = PACKAGE / "tools" / "check-runtime-deps.sh"
WAIT_NETWORK = PACKAGE / "network" / "wait-online.sh"
WAIT_OVERLAY = PACKAGE / "rootfs" / "overlay" / "usr" / "local" / "bin" / "wait-online.sh"
ENV_SH = PACKAGE / "rootfs" / "overlay" / "usr" / "local" / "bin" / "imagectl-env.sh"
DETECT = PACKAGE / "imaging" / "detect-target.sh"
PLAN = PACKAGE / "imaging" / "plan.sh"

REQUIRED_PKGS = ("curl", "iproute2", "util-linux", "partclone", "zstd", "udpcast")
STUB = "#!/bin/sh\nexit 0\n"

LSBLK_STUB = """#!/bin/sh
p=0
for a in "$@"; do
  case "$a" in
    -P) p=1 ;;
    -*P*) p=1 ;;
  esac
done
if [ "$p" -eq 1 ]; then
  printf '%s\\n' 'NAME="nvme0n1" TYPE="disk" SIZE="476.9G" MODEL="Samsung SSD 970 EVO Plus" RO="0"'
else
  printf '%s\\n' 'nvme0n1 disk 476.9G Samsung SSD 970 EVO Plus 0'
fi
"""

LSBLK_EMPTY = "#!/bin/sh\nexit 0\n"
LSBLK_FAIL = "#!/bin/sh\necho lsblk: boom >&2\nexit 1\n"


def _posix_shells() -> list[list[str]]:
    shells: list[list[str]] = []
    for name in ("dash", "sh", "ash"):
        found = shutil.which(name)
        if found:
            shells.append([found])
    busybox = shutil.which("busybox")
    if busybox:
        shells.append([busybox, "ash"])
    return shells


SHELLS = _posix_shells()
SHELL_IDS = [" ".join(Path(part).name for part in argv) for argv in SHELLS]


def test_a_posix_shell_is_available():
    """בלי מעטפת POSIX אין מה לבדוק — וזה כישלון, לא דילוג."""
    assert SHELLS, (
        "לא נמצאה אף מעטפת POSIX (dash/sh/ash/busybox). בלעדיה הטסטים "
        "למטה היו עוברים בלי להריץ את הבודק — כישלון שנראה כמו הצלחה"
    )


def _write_lf(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(mode)


def posix(p: Path | str) -> str:
    text = str(p).replace("\\", "/")
    if len(text) > 1 and text[1] == ":":
        text = "/" + text[0].lower() + text[2:]
    return text


def path_entry(p: Path | str) -> str:
    return posix(p)


def _run(argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
         timeout: int = 30):
    return subprocess.run(
        argv,
        cwd=None if cwd is None else str(cwd),
        env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=timeout,
    )


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def _box(root: Path, names: list[str], bodies: dict[str, str] | None = None) -> Path:
    box = root / "bin"
    box.mkdir(parents=True, exist_ok=True)
    bodies = bodies or {}
    for name in names:
        _write_lf(box / name, bodies.get(name, STUB), 0o755)
    return box


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_host_fails_on_missing_required_tool(shell, tmp_path):
    """sha256sum חסר הוא FAIL ויציאה לא-אפס — לא WARN ויציאה 0."""
    box = _box(tmp_path, ["curl", "gzip", "cpio"])
    env = dict(os.environ)
    env["PATH"] = path_entry(box)
    proc = _run(shell + [posix(CHECK_HOST)], env=env)
    output = _out(proc)
    assert proc.returncode != 0, f"hostcheck יצא 0 בלי sha256sum:\n{output}"
    assert "sha256sum" in output
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_host_passes_when_only_optional_is_missing(shell, tmp_path):
    """qemu/shellcheck חסרים נשארים OPTIONAL, והבדיקה עוברת עם ספירה."""
    box = _box(tmp_path, ["curl", "gzip", "cpio", "sha256sum"])
    env = dict(os.environ)
    env["PATH"] = path_entry(box)
    proc = _run(shell + [posix(CHECK_HOST)], env=env)
    output = _out(proc)
    assert proc.returncode == 0, f"hostcheck נכשל על אופציונלי חסר:\n{output}"
    assert "OPTIONAL missing: qemu-system-x86_64" in output
    assert "PASS" in proc.stdout
    assert "tools checked" in proc.stdout


def _apkindex(names: list[str]) -> str:
    return "".join(f"P:{n}\nV:1\n\n" for n in names)


def _packages_txt(names: list[str]) -> str:
    return "".join(f"{n}\n" for n in names)


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_runtime_deps_fails_when_apkindex_is_missing(shell, tmp_path):
    """בלי APKINDEX לא נבדק קיום ב-Alpine — וזה כישלון, לא WARN."""
    env = dict(os.environ)
    env.pop("APKINDEX", None)
    proc = _run(shell + [posix(CHECK_DEPS)], cwd=tmp_path, env=env)
    output = _out(proc)
    assert proc.returncode != 0, f"יצא 0 בלי APKINDEX:\n{output}"
    assert "APKINDEX" in output
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_runtime_deps_fails_when_required_package_not_in_apkindex(shell, tmp_path):
    """curl כתוב ב-packages.txt אבל חסר מ-APKINDEX — FAIL, לא WARN."""
    names = [n for n in REQUIRED_PKGS if n != "curl"]
    pkg = tmp_path / "packages.txt"
    idx = tmp_path / "APKINDEX"
    _write_lf(pkg, _packages_txt(list(REQUIRED_PKGS)))
    _write_lf(idx, _apkindex(names))
    proc = _run(shell + [posix(CHECK_DEPS), posix(pkg), posix(idx)])
    output = _out(proc)
    assert proc.returncode != 0, f"חבילה נדרשת חסרה מ-APKINDEX ויצא 0:\n{output}"
    assert "curl" in output
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_runtime_deps_fails_when_required_package_not_declared(shell, tmp_path):
    """curl נדרש ולא כתוב ב-packages.txt."""
    names = [n for n in REQUIRED_PKGS if n != "curl"]
    pkg = tmp_path / "packages.txt"
    idx = tmp_path / "APKINDEX"
    _write_lf(pkg, _packages_txt(names))
    _write_lf(idx, _apkindex(list(REQUIRED_PKGS)))
    proc = _run(shell + [posix(CHECK_DEPS), posix(pkg), posix(idx)])
    output = _out(proc)
    assert proc.returncode != 0, f"חבילה נדרשת לא הוצהרה ויצא 0:\n{output}"
    assert "curl" in output
    assert "not declared" in output


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_check_runtime_deps_warns_but_passes_on_missing_optional(shell, tmp_path):
    """cog/cage חסרים מ-APKINDEX הם WARN, לא FAIL — ו-PASS נושא ספירה."""
    pkg = tmp_path / "packages.txt"
    idx = tmp_path / "APKINDEX"
    _write_lf(pkg, _packages_txt(list(REQUIRED_PKGS)))
    _write_lf(idx, _apkindex(list(REQUIRED_PKGS)))
    proc = _run(shell + [posix(CHECK_DEPS), posix(pkg), posix(idx)])
    output = _out(proc)
    assert proc.returncode == 0, f"אופציונלי חסר הפיל:\n{output}"
    assert "WARN optional package not in APKINDEX: cog" in output
    assert "PASS" in proc.stdout
    assert "packages checked" in proc.stdout


WAIT_SCRIPTS = [WAIT_NETWORK, WAIT_OVERLAY]
WAIT_IDS = ["network", "overlay"]


@pytest.mark.parametrize("script", WAIT_SCRIPTS, ids=WAIT_IDS)
@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_wait_online_fails_on_a_dead_url(shell, script):
    """כתובת מתה: exit != 0. הישן התעלם מה-URL ובדק default route."""
    url = "http://127.0.0.1:1/"
    proc = _run(shell + [posix(script), url, "1"], timeout=45)
    output = _out(proc)
    assert proc.returncode != 0, f"wait-online יצא 0 מול שרת מת:\n{output}"
    assert url in output, f"ה-URL לא הופיע בכישלון — אולי עדיין מתעלמים ממנו:\n{output}"
    assert "network did not become ready" not in output


@pytest.mark.parametrize("script", WAIT_SCRIPTS, ids=WAIT_IDS)
@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_wait_online_fails_without_a_url_argument(shell, script):
    """בלי URL — כישלון מיידי, לא לולאת ip route."""
    proc = _run(shell + [posix(script)], timeout=45)
    output = _out(proc)
    assert proc.returncode != 0, f"בלי URL יצא 0:\n{output}"
    assert "URL" in output
    assert "network did not become ready" not in output


@pytest.mark.parametrize("script", WAIT_SCRIPTS, ids=WAIT_IDS)
@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_wait_online_succeeds_when_the_url_answers(shell, script):
    """ולא תיקון-יתר: URL חי יוצא 0."""

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *_args):
            return

    httpd = HTTPServer(("127.0.0.1", 0), H)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{port}/"
        proc = _run(shell + [posix(script), url, "2"], timeout=45)
        assert proc.returncode == 0, f"URL חי נכשל:\n{_out(proc)}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _source_env(shell: list[str], cmdline_path: str):
    env = dict(os.environ)
    env["IMAGECTL_CMDLINE"] = cmdline_path
    env.pop("IMAGECTL_SERVER", None)
    env.pop("KIOSK_URL", None)
    body = (
        "set -e; "
        f". {posix(ENV_SH)!r}; "
        "printf 'SERVER=%s\\n' \"$IMAGECTL_SERVER\"; "
        "printf 'KIOSK=%s\\n' \"$KIOSK_URL\""
    )
    return _run(shell + ["-c", body], env=env)


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_imagectl_env_fails_without_server_in_cmdline(shell, tmp_path):
    """cmdline בלי imagectl.server אינו localhost."""
    cmd = tmp_path / "cmdline"
    _write_lf(cmd, "console=tty0 quiet\n")
    proc = _source_env(shell, posix(cmd))
    output = _out(proc)
    assert proc.returncode != 0, f"בלי server יצא 0:\n{output}"
    assert "127.0.0.1" not in output
    assert "imagectl.server" in output


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_imagectl_env_fails_when_cmdline_is_unreadable(shell):
    """cmdline שלא נקרא אינו localhost."""
    proc = _source_env(shell, posix(Path("/no/such/cmdline")))
    output = _out(proc)
    assert proc.returncode != 0, f"cmdline חסר יצא 0:\n{output}"
    assert "127.0.0.1" not in output
    assert "cannot read" in output or "cmdline" in output


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_imagectl_env_exports_server_from_cmdline(shell, tmp_path):
    """ולא תיקון-יתר: imagectl.server בשורה מיוצא."""
    cmd = tmp_path / "cmdline"
    _write_lf(cmd, "console=tty0 imagectl.server=http://10.10.10.1:8080\n")
    proc = _source_env(shell, posix(cmd))
    output = _out(proc)
    assert proc.returncode == 0, f"server תקין נכשל:\n{output}"
    assert "SERVER=http://10.10.10.1:8080" in proc.stdout
    assert "KIOSK=http://10.10.10.1:8080/" in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_detect_target_keeps_a_disk_with_a_two_word_model(shell, tmp_path):
    """MODEL דו-מילתי אינו מפיל את הדיסק מהרשימה."""
    box = _box(tmp_path, ["lsblk"], {"lsblk": LSBLK_STUB})
    env = dict(os.environ)
    env["PATH"] = path_entry(box) + os.pathsep + os.environ.get("PATH", "")
    proc = _run(shell + [posix(DETECT)], env=env)
    output = _out(proc)
    assert proc.returncode == 0, f"detect-target נכשל על דגם דו-מילתי:\n{output}"
    assert "/dev/nvme0n1" in proc.stdout, f"הדיסק נעלם:\n{output}"
    assert "Samsung SSD 970 EVO Plus" in proc.stdout, (
        f"MODEL התפצל או אבד:\n{output}"
    )


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_plan_none_when_there_are_no_disks(shell, tmp_path):
    """אין דיסקים: NONE (no writable disks), יציאה 0 — לא קריסה."""
    box = _box(tmp_path, ["lsblk"], {"lsblk": LSBLK_EMPTY})
    env = dict(os.environ)
    env["PATH"] = path_entry(box) + os.pathsep + os.environ.get("PATH", "")
    proc = _run(shell + [posix(PLAN)], env=env)
    output = _out(proc)
    assert proc.returncode == 0, f"אין דיסקים נכשל:\n{output}"
    assert "NONE (no writable disks)" in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_plan_fails_when_detect_target_fails(shell, tmp_path):
    """lsblk שנכשל אינו NONE. plan.sh מבחין בין 'אין דיסקים' ל'הבדיקה נכשלה'."""
    box = _box(tmp_path, ["lsblk"], {"lsblk": LSBLK_FAIL})
    env = dict(os.environ)
    env["PATH"] = path_entry(box) + os.pathsep + os.environ.get("PATH", "")
    proc = _run(shell + [posix(PLAN)], env=env)
    output = _out(proc)
    assert proc.returncode != 0, f"lsblk שנכשל הפך ל-NONE:\n{output}"
    assert "NONE (no writable disks)" not in proc.stdout
    assert "failed" in output
