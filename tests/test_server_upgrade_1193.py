"""‏#1193 — git writes happen inside the detached upgrade script.

The real bash script runs against PATH shims.  The assertions cover the
observable command order and the persisted status; no git, systemd, curl, or
installed unit on the test host is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH,
    _fake_repo,
    _seed_db,
    _status,
    _upgrade_env,
    calls,
    fake_tool,
    posix,
    run_bash,
)

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _git_shim(fetch: str = "exit 0", checkout: str = "exit 0") -> str:
    return f'''\
if [ "$1" = -C ]; then shift 2; fi
case "$1" in
  fetch) {fetch} ;;
  checkout) {checkout} ;;
  describe) echo "$FAKE_TAG" ;;
  *) echo "unexpected git $*" >&2; exit 64 ;;
esac
'''


def _run(tmp_path: Path, bindir: Path, repo: Path, data: Path, tag: str = "v0.53.0"):
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag}
    return run_bash(
        f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
        tmp_path,
        bindir,
        env=env,
    )


def _success_tools(bindir: Path) -> None:
    fake_tool(bindir, "git", _git_shim())
    fake_tool(
        bindir,
        "systemctl",
        'case "$1" in show) echo "IMAGECTL_URL=http://10.10.10.8:8080 X=y" ;; esac; exit 0',
    )
    fake_tool(bindir, "curl", "exit 0")


def test_fetch_and_checkout_precede_units_restart_and_verification(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _success_tools(bindir)

    proc = _run(tmp_path, bindir, repo, data)
    log = calls(tmp_path)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    fetch = next(i for i, row in enumerate(log) if row.startswith("git -C ") and row.endswith(" fetch --tags"))
    checkout = next(i for i, row in enumerate(log) if row.startswith("git -C ") and " checkout --detach v0.53.0" in row)
    units = log.index("systemctl daemon-reload")
    restart = log.index("systemctl restart imagectl-server")
    active = log.index("systemctl is-active --quiet imagectl-server")
    assert fetch < checkout < units < restart < active


def test_fetch_failure_records_git_error_and_touches_no_units_or_service(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(
        bindir,
        "git",
        _git_shim(fetch='echo "cannot open .git/FETCH_HEAD: Read-only file system" >&2; exit 23'),
    )
    fake_tool(bindir, "systemctl", 'echo "must not run" >&2; exit 70')
    fake_tool(bindir, "curl", 'echo "must not run" >&2; exit 71')

    proc = _run(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    status = _status(data)

    assert proc.returncode != 0
    assert status and status["state"] == "failed" and status["tag"] == "v0.53.0"
    assert "git fetch --tags" in status["error"]
    assert "Read-only file system" in status["error"]
    assert not any(" checkout " in row for row in log)
    assert not any(row.startswith("systemctl ") for row in log)
    assert not (tmp_path / "units" / "imagectl-server.service").exists()


def test_checkout_failure_records_git_error_and_does_not_restart(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(
        bindir,
        "git",
        _git_shim(checkout='echo "fatal: reference is not a tree" >&2; exit 128'),
    )
    fake_tool(bindir, "systemctl", 'echo "must not run" >&2; exit 70')
    fake_tool(bindir, "curl", 'echo "must not run" >&2; exit 71')

    proc = _run(tmp_path, bindir, repo, data)
    status = _status(data)

    assert proc.returncode != 0
    assert status and status["state"] == "failed"
    assert "git checkout --detach v0.53.0" in status["error"]
    assert "reference is not a tree" in status["error"]
    assert not any(row.startswith("systemctl ") for row in calls(tmp_path))

