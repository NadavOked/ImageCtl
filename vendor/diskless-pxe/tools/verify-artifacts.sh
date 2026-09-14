#!/bin/sh
# Reject missing boot artefacts and destructive commands in the overlay.
#
# `if grep ... 2>/dev/null` treated grep's 2 as clean (#304): a missing
# scan root, including one that already had a match, printed PASS.
# Three outcomes by name: 0 = found (fail), 1 = clean, else = scan broke.
# No args: ROOT from $0, not cwd.

set -eu

if [ -n "${1:-}" ]; then
    ROOT=$1
else
    # shellcheck disable=SC1007
    ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
fi
fail=0

for f in out/boot.ipxe rootfs/packages.txt rootfs/overlay/etc/local.d/imagectl.start rootfs/overlay/usr/local/bin/start-kiosk.sh; do
    [ -s "$ROOT/$f" ] || { echo "MISSING: $f" >&2; fail=1; }
done

OVERLAY_ROOT="$ROOT/rootfs/overlay"
IPXE_ROOT="$ROOT/ipxe"
for dir in "$OVERLAY_ROOT" "$IPXE_ROOT"; do
    if [ ! -d "$dir" ]; then
        echo "ERROR scan root is not a directory: $dir" >&2
        exit 2
    fi
done
overlay_files=$(find "$OVERLAY_ROOT" -type f | wc -l | tr -d ' ')
ipxe_files=$(find "$IPXE_ROOT" -type f | wc -l | tr -d ' ')
scanned=$((overlay_files + ipxe_files))
[ "$scanned" -gt 0 ] || { echo "ERROR scanned 0 files under $OVERLAY_ROOT $IPXE_ROOT" >&2; exit 2; }

# Guard against accidentally shipping destructive defaults.
PATTERN='(^|[;&| ])(dd|wipefs|mkfs|sgdisk|parted)[[:space:]]'
set +e
hits=$(grep -R -nE "$PATTERN" "$OVERLAY_ROOT" "$IPXE_ROOT" 2>&1)
rc=$?
set -e
case "$rc" in
    0)
        printf '%s\n' "$hits" >&2
        echo "ERROR: destructive command found in bootable overlay" >&2
        fail=1
        ;;
    1)
        ;;
    *)
        printf '%s\n' "$hits" >&2
        echo "ERROR grep exited $rc -- the scan failed, nothing was proven" >&2
        exit 2
        ;;
esac

if [ "$fail" -ne 0 ]; then
    echo "artifact validation: FAIL ($scanned files scanned)" >&2
    exit 1
fi
echo "artifact validation: PASS ($scanned files scanned, 0 hits)"
