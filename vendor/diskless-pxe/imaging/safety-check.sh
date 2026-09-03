#!/bin/sh
set -eu
TARGET=${1:-}
[ -b "$TARGET" ] || { echo "target is not a block device" >&2; exit 2; }
[ "$(lsblk -dn -o TYPE "$TARGET")" = disk ] || { echo "target is not a whole disk" >&2; exit 3; }
[ "$(lsblk -dn -o RO "$TARGET")" = 0 ] || { echo "target is read-only" >&2; exit 4; }
case "$TARGET" in /dev/nvme*n*|/dev/sd[a-z]|/dev/vd[a-z]) ;; *) echo "unsupported target naming" >&2; exit 5;; esac
echo "OK $TARGET"
