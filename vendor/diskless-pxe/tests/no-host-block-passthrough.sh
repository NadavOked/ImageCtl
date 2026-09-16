#!/bin/sh
# Refuse host block-device passthrough in qemu/rootfs/ipxe.
#
# The previous version could not fail (#304):
#
#     if grep -RInE '<pattern>' qemu rootfs ipxe 2>/dev/null; then
#       echo 'FAIL ...'; exit 1
#     fi
#     echo 'PASS no-host-block-passthrough'
#
# Paths were relative to cwd, and GNU grep returns 2 when a scan root is
# missing — even if it already found a match. `if grep` treats 2 as false
# and falls through to PASS. `2>/dev/null` hid the only line that would
# have shown the scan never ran.
#
# Three grep outcomes, by name: 0 = found (fail), 1 = clean, anything
# else = the scan itself broke (fail). PASS carries the file count.
# ROOT is resolved from $0, not from the caller's cwd.

set -eu

# shellcheck disable=SC1007
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PATTERN='(/dev/(sd[a-z]|nvme[0-9]+n[0-9]+).*(of=|format=raw)|-drive[[:space:]]+file=/dev/)'

die() {
    printf 'no-host-block-passthrough: %s\n' "$1" >&2
    exit "$2"
}

# GNU grep --exclude is not busybox-safe. List files, skip this checker
# by name, then grep. Paths in this tree have no spaces.
set --
for dir in qemu rootfs ipxe; do
    path="$ROOT/$dir"
    [ -d "$path" ] || die "ERROR scan root is not a directory: $path" 2
    # shellcheck disable=SC2046
    for f in $(find "$path" -type f); do
        case "$f" in
            */no-host-block-passthrough.sh) continue ;;
        esac
        set -- "$@" "$f"
    done
done
scanned=$#
[ "$scanned" -gt 0 ] || die "ERROR scanned 0 files under $ROOT" 2

set +e
hits=$(grep -nE "$PATTERN" "$@" 2>&1)
rc=$?
set -e

case "$rc" in
    0)
        printf '%s\n' "$hits" >&2
        die "FAIL host block-device passthrough pattern found" 1
        ;;
    1)
        ;;
    *)
        printf '%s\n' "$hits" >&2
        die "ERROR grep exited $rc -- the scan failed, nothing was proven" 2
        ;;
esac

printf 'PASS no-host-block-passthrough (%s files scanned, 0 hits)\n' "$scanned"
