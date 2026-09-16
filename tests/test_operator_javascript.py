"""Run the operator and shell-contract tests in the normal pytest suite.

Node's built-in runner needs no package install. Missing Node is a failure,
not a silently skipped operator surface. Node >= 18 and POSIX sh are required.

Every ``tests/*.test.cjs`` is collected by glob (#826): a hand-kept list let
``console_rewrite.test.cjs`` sit red on main for days without any pytest run
ever seeing it. A new ``.test.cjs`` file runs the moment it lands, and one
that breaks fails the moment it breaks.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

TESTS_DIR = Path(__file__).resolve().parent
NODE_SCRIPTS = sorted(p.name for p in TESTS_DIR.glob("*.test.cjs"))


def test_node_scripts_are_collected():
    assert NODE_SCRIPTS, "no tests/*.test.cjs found — the glob is broken, not the suite"
    assert "console_rewrite.test.cjs" in NODE_SCRIPTS


@pytest.mark.parametrize("script", NODE_SCRIPTS)
def test_operator_surface(script):
    node = shutil.which("node")
    assert node, "Node >= 18 is required to test the operator surface"
    result = subprocess.run([node, "--test", str(TESTS_DIR / script)],
                            cwd=TESTS_DIR.parent,
                            stdin=subprocess.DEVNULL, capture_output=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
