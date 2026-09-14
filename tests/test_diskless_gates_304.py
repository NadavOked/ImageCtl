"""שערי diskless-PXE אינם מדפיסים PASS כשהסריקה לא רצה (#304).

ארבעה שערים שילבו נתיבים יחסיים עם `if grep ... 2>/dev/null`. ‏GNU grep
מחזיר 2 כשאירעה שגיאה — גם אם כבר מצא התאמה — וה-`if` קורא זאת כנקי.
מ-cwd שאינו שורש החבילה, או כששורש סריקה חסר, הפלט היה PASS על אפס
קבצים. שלושת מצבי grep נקראים בשם (0/1/אחר), כמו ב-#231; PASS נושא
כמה קבצים נסרקו; אפס הוא כישלון.

הטסט מריץ את השער כתהליך POSIX אמיתי על עץ מסונתז, לא קורא את הקוד.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGE = REPO / "vendor" / "diskless-pxe"
PASSTHROUGH = PACKAGE / "tests" / "no-host-block-passthrough.sh"
SMOKE = PACKAGE / "tests" / "smoke.sh"
OVERLAY_STATIC = PACKAGE / "tests" / "overlay-static.sh"
VERIFY = PACKAGE / "tools" / "verify-artifacts.sh"
CI_RUN = PACKAGE / "ci" / "run.sh"

HIT = "-drive file=/dev/sda,if=virtio\n"
HARMLESS = "#!/bin/sh\necho ok\n"
CONF = "ALLOW_IMAGING=0\n"
IPXE = "#!ipxe\necho placeholder\n"


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
        "למטה היו עוברים בלי להריץ את השער — כישלון שנראה כמו הצלחה"
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


def _run(argv: list[str], cwd: Path | None = None):
    return subprocess.run(
        argv,
        cwd=None if cwd is None else str(cwd),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=60,
    )


def _out(proc: subprocess.CompletedProcess) -> str:
    return proc.stdout + proc.stderr


def _passthrough_tree(root: Path, *, qemu: bool = True, rootfs: bool = True,
                      ipxe: bool = True, hit: bool = False) -> tuple[Path, Path]:
    pkg = root / "pkg"
    (pkg / "tests").mkdir(parents=True)
    for name, want in (("qemu", qemu), ("rootfs", rootfs), ("ipxe", ipxe)):
        if not want:
            continue
        d = pkg / name
        d.mkdir(parents=True)
        _write_lf(d / "keep.txt", "ok\n")
    if hit:
        (pkg / "qemu").mkdir(parents=True, exist_ok=True)
        _write_lf(pkg / "qemu" / "bad.conf", HIT)
    gate = pkg / "tests" / PASSTHROUGH.name
    _write_lf(gate, PASSTHROUGH.read_text(encoding="utf-8"), 0o755)
    return pkg, gate


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_passthrough_fails_on_a_hit(shell, tmp_path):
    """הפרת passthrough ב-qemu/ חייבת להפיל, לא להדפיס PASS מתחתיה."""
    pkg, gate = _passthrough_tree(tmp_path, hit=True)
    proc = _run(shell + [posix(gate)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"השער יצא 0 על passthrough:\n{output}"
    assert "PASS" not in proc.stdout, f"הודפס PASS על הפרה:\n{output}"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_passthrough_fails_when_a_scan_root_is_missing(shell, tmp_path):
    """שורש סריקה חסר = לא נבדק. זה #304: grep 2 נקרא כנקי."""
    pkg, gate = _passthrough_tree(tmp_path, ipxe=False)
    proc = _run(shell + [posix(gate)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"שורש חסר הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_passthrough_fails_on_zero_files(shell, tmp_path):
    """תיקיות ריקות: 0 קבצים נסרקו אינו נקי."""
    pkg = tmp_path / "pkg"
    for name in ("tests", "qemu", "rootfs", "ipxe"):
        (pkg / name).mkdir(parents=True)
    _write_lf(pkg / "tests" / PASSTHROUGH.name, PASSTHROUGH.read_text(encoding="utf-8"), 0o755)
    proc = _run(shell + [posix(pkg / "tests" / PASSTHROUGH.name)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"עץ ריק הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_passthrough_passes_on_a_clean_tree_with_count(shell, tmp_path):
    """ולא תיקון-יתר: עץ נקי עובר, עם מספר הקבצים כראיה חיובית."""
    pkg, gate = _passthrough_tree(tmp_path)
    proc = _run(shell + [posix(gate)], cwd=pkg)
    assert proc.returncode == 0, f"עץ נקי נכשל:\n{_out(proc)}"
    assert "PASS" in proc.stdout
    assert "files scanned" in proc.stdout, f"PASS בלי ספירה:\n{proc.stdout}"


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_passthrough_from_another_cwd_still_scans(shell, tmp_path):
    """הרצה מ-cwd אחר עדיין סורקת — הנתיבים מ-$0, לא מה-cwd."""
    _pkg, gate = _passthrough_tree(tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    proc = _run(shell + [posix(gate)], cwd=cwd)
    assert proc.returncode == 0, f"מ-cwd אחר נכשל:\n{_out(proc)}"
    assert "files scanned" in proc.stdout


@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_passthrough_shipped_package_is_clean(shell):
    """ועל החבילה האמיתית — השער עובר."""
    proc = _run(shell + [posix(PASSTHROUGH)])
    assert proc.returncode == 0, f"החבילה עצמה נכשלת:\n{_out(proc)}"
    assert "files scanned" in proc.stdout


def _smoke_min_tree(pkg: Path, *, overlay: bool = True, scripts: bool = False) -> None:
    for rel in (
        "ipxe/boot.ipxe", "ipxe/fallback.ipxe", "ipxe/boot.template.ipxe",
        "README.md", "INTEGRATION.md", "config.example",
        "docs/ACCEPTANCE_TESTS.md", "docs/SECURITY.md",
    ):
        _write_lf(pkg / rel, IPXE if rel.endswith(".ipxe") else "ok\n")
    if overlay:
        _write_lf(pkg / "rootfs/overlay/etc/keep.txt", "ok\n")
        _write_lf(pkg / "rootfs/overlay/usr/local/bin/keep.txt", "ok\n")
    if scripts:
        _write_lf(pkg / "tools/ok.sh", HARMLESS, 0o755)


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_smoke_fails_on_zero_scripts(shell, tmp_path):
    """אפס קבצי מעטפת: הלולאה לא רצה, וזה כישלון — לא smoke: PASS."""
    pkg = tmp_path / "pkg"
    _smoke_min_tree(pkg)
    # בלי סיומת .sh כדי ש-find לא יספור את השער עצמו.
    gate = pkg / "tests" / "run-smoke"
    _write_lf(gate, SMOKE.read_text(encoding="utf-8"), 0o755)
    proc = _run(shell + [posix(gate)])
    output = _out(proc)
    assert proc.returncode != 0, f"אפס סקריפטים הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_smoke_fails_when_overlay_scan_root_is_missing(shell, tmp_path):
    """overlay חסר: grep 2 אינו נקי."""
    pkg = tmp_path / "pkg"
    _smoke_min_tree(pkg, overlay=False, scripts=True)
    gate = pkg / "tests" / SMOKE.name
    _write_lf(gate, SMOKE.read_text(encoding="utf-8"), 0o755)
    proc = _run(shell + [posix(gate)])
    output = _out(proc)
    assert proc.returncode != 0, f"שורש overlay חסר הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS[:1], ids=SHELL_IDS[:1])
def test_smoke_from_another_cwd_still_scans(shell, tmp_path):
    """smoke.sh כבר פתר ROOT מ-$0; אחרי התיקון PASS נושא ספירה."""
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    proc = _run(shell + [posix(SMOKE)], cwd=cwd)
    assert proc.returncode == 0, f"מ-cwd אחר נכשל:\n{_out(proc)}"
    assert "files scanned" in proc.stdout


def _overlay_static_tree(root: Path, *, files: bool = True) -> tuple[Path, Path]:
    pkg = root / "pkg"
    for d in (
        "tests",
        "rootfs/overlay/usr/local/bin",
        "rootfs/overlay/usr/local/sbin",
        "rootfs/overlay/etc/init.d",
        "rootfs/overlay/etc/imagectl",
    ):
        (pkg / d).mkdir(parents=True, exist_ok=True)
    _write_lf(pkg / "rootfs/overlay/etc/imagectl/client.conf.example", CONF)
    if files:
        _write_lf(pkg / "rootfs/overlay/usr/local/bin/ok.sh", HARMLESS, 0o755)
    gate = pkg / "tests" / OVERLAY_STATIC.name
    _write_lf(gate, OVERLAY_STATIC.read_text(encoding="utf-8"), 0o755)
    return pkg, gate


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_overlay_static_fails_on_zero_files(shell, tmp_path):
    """גלוב שלא התרחב בדק אפס קבצים והדפיס PASS — עכשיו זה כישלון."""
    pkg, gate = _overlay_static_tree(tmp_path, files=False)
    proc = _run(shell + [posix(gate)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"אפס קבצים הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_overlay_static_from_another_cwd_still_scans(shell, tmp_path):
    """מ-cwd אחר השער עדיין בודק קבצים, עם ספירה ב-PASS."""
    _pkg, gate = _overlay_static_tree(tmp_path, files=True)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    proc = _run(shell + [posix(gate)], cwd=cwd)
    assert proc.returncode == 0, f"מ-cwd אחר נכשל:\n{_out(proc)}"
    assert "files scanned" in proc.stdout


def _verify_tree(root: Path, *, ipxe: bool = True, hit: bool = False) -> tuple[Path, Path]:
    pkg = root / "pkg"
    _write_lf(pkg / "out/boot.ipxe", IPXE)
    _write_lf(pkg / "rootfs/packages.txt", "curl\n")
    _write_lf(pkg / "rootfs/overlay/etc/local.d/imagectl.start", HARMLESS)
    _write_lf(pkg / "rootfs/overlay/usr/local/bin/start-kiosk.sh", HARMLESS)
    if ipxe:
        _write_lf(pkg / "ipxe/boot.ipxe", IPXE)
    if hit:
        _write_lf(pkg / "rootfs/overlay/evil.sh", "#!/bin/sh\ndd if=/dev/zero of=/dev/sda\n")
    else:
        _write_lf(pkg / "rootfs/overlay/ok.sh", HARMLESS)
    gate = pkg / "tools" / VERIFY.name
    _write_lf(gate, VERIFY.read_text(encoding="utf-8"), 0o755)
    return pkg, gate


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_verify_artifacts_fails_when_scan_root_is_missing(shell, tmp_path):
    """ipxe חסר: grep 2 אינו artifact validation: PASS."""
    pkg, gate = _verify_tree(tmp_path, ipxe=False)
    proc = _run(shell + [posix(gate), posix(pkg)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"שורש ipxe חסר הוכרז PASS:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_verify_artifacts_fails_on_a_destructive_hit(shell, tmp_path):
    """ולא תיקון-יתר בכיוון השני: התאמה הרסנית מפילה."""
    pkg, gate = _verify_tree(tmp_path, hit=True)
    proc = _run(shell + [posix(gate), posix(pkg)], cwd=pkg)
    output = _out(proc)
    assert proc.returncode != 0, f"dd בחפיסה יצא 0:\n{output}"
    assert "PASS" not in proc.stdout


@pytest.mark.parametrize("shell", SHELLS, ids=SHELL_IDS)
def test_verify_artifacts_from_another_cwd_without_args(shell, tmp_path):
    """בלי ארגומנט, ROOT מ-$0 — לא מ-cwd."""
    _pkg, gate = _verify_tree(tmp_path)
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    proc = _run(shell + [posix(gate)], cwd=cwd)
    assert proc.returncode == 0, f"מ-cwd אחר בלי ארגומנט נכשל:\n{_out(proc)}"
    assert "files scanned" in proc.stdout


def test_ci_run_invokes_gates_by_absolute_path():
    """ci/run.sh קורא לשערים בנתיב מוחלט מ-$ROOT, לא ב-./ היחסי ל-cwd."""
    text = CI_RUN.read_text(encoding="utf-8")
    assert "./tests/overlay-static.sh" not in text
    assert "./tests/no-host-block-passthrough.sh" not in text
    assert "$ROOT/tests/overlay-static.sh" in text
    assert "$ROOT/tests/no-host-block-passthrough.sh" in text
