#!/bin/sh
set -eu
ROOT=${1:-.}
fail=0
for f in out/boot.ipxe rootfs/packages.txt rootfs/overlay/etc/local.d/imagectl.start rootfs/overlay/usr/local/bin/start-kiosk.sh; do
  [ -s "$ROOT/$f" ] || { echo "MISSING: $f" >&2; fail=1; }
done
# Guard against accidentally shipping destructive defaults.
if grep -R -nE '(^|[;&| ])(dd|wipefs|mkfs|sgdisk|parted)[[:space:]]' "$ROOT/rootfs/overlay" "$ROOT/ipxe" 2>/dev/null; then
  echo "ERROR: destructive command found in bootable overlay" >&2; fail=1
fi
[ "$fail" -eq 0 ] && echo "artifact validation: PASS"
exit "$fail"
