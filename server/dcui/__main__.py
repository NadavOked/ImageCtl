"""Entry point for ``python3 -m server.dcui``."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .app import render_demo, run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ImageCtl physical server console")
    parser.add_argument("--data-dir", type=Path, default=Path("/var/lib/imagectl"))
    parser.add_argument("--render-demo", action="store_true",
                        help="print the five approved 80x25 screens")
    args = parser.parse_args(argv)
    if args.render_demo:
        print(render_demo())
        return 0
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        print("imagectl-dcui: root privileges are required", file=sys.stderr)
        return 3
    run(args.data_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
