#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
grep -q '^#!ipxe' "$ROOT/out/boot.ipxe"
grep -q 'IMAGECTL_SERVER' "$ROOT/config.example"
"$ROOT/tools/verify-artifacts.sh" "$ROOT"
echo "static contract: PASS"
