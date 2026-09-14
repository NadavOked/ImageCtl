"""Run the operator and shell-contract tests in the normal pytest suite.

Node's built-in runner needs no package install. Missing Node is a failure,
not a silently skipped operator surface. Node >= 18 and POSIX sh are required.
"""

from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize("script", ["operator_progress.test.cjs",
                                    "couldnt_read_negative.test.cjs",])
def test_operator_surface(script):
    node = shutil.which("node")
    assert node, "Node >= 18 is required to test the operator surface"
    result = subprocess.run([node, "--test", str(Path(__file__).with_name(script))],
                            cwd=Path(__file__).resolve().parents[1],
                            stdin=subprocess.DEVNULL, capture_output=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
