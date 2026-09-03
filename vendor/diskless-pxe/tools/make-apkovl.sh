#!/bin/sh
set -eu
ROOT=${1:-rootfs/overlay}
OUT=${2:-out/imagectl.apkovl.tar.gz}
mkdir -p "$(dirname "$OUT")"
( cd "$ROOT" && tar -czf "$OLDPWD/$OUT" . )
echo "$OUT"
