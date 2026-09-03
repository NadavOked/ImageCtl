#!/bin/sh
set -eu
# Run on Alpine Linux as root. Builds an initramfs/rootfs staging tree without
# modifying ImageCtl. Exact driver set must be validated on target classroom PCs.
OUT=${OUT:-$(pwd)/out}
ROOT=${ROOT:-$(pwd)/work/rootfs}
mkdir -p "$OUT" "$ROOT"
apk --root "$ROOT" --initdb add $(grep -v '^#' packages.txt | tr '\n' ' ')
cp -a overlay/. "$ROOT/"
chmod +x "$ROOT/etc/local.d/imagectl.start"
echo "Rootfs staged at $ROOT"
echo "Next integration step: generate/initramfs with the kernel modules required by target hardware."
