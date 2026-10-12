from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


SHIM = Path(__file__).with_name("jq_shim.py")


def test_jq_shim_uses_utf8_with_redirected_stdio():
    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)
    payload = json.dumps({"name": "מחשב תלמיד"}, ensure_ascii=False).encode("utf-8")

    proc = subprocess.run(
        [sys.executable, str(SHIM), "-r", ".name"],
        input=payload,
        capture_output=True,
        env=env,
        check=True,
    )

    assert proc.stdout.decode("utf-8") == "מחשב תלמיד\n"
    assert proc.stderr == b""
