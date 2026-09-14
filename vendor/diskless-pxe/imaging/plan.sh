#!/bin/sh
# Non-destructive planning helper. It NEVER writes an image.
#
# `detect-target.sh 2>/dev/null || true` folded three states into NONE
# (#307): no disks, lsblk missing, script crash. Those are not the same.

set -eu
# shellcheck disable=SC1007
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

set +e
target=$("$DIR/detect-target.sh")
rc=$?
set -e

case "$rc" in
    0)
        if [ -z "$target" ]; then
            printf 'Detected target: NONE (no writable disks)\n'
        else
            printf 'Detected target: %s\n' "$target"
        fi
        ;;
    *)
        printf 'detect-target failed (exit %s)\n' "$rc" >&2
        exit "$rc"
        ;;
esac
printf '%s\n' 'No write command executed. Integration must explicitly enable imaging after server authorization and safety checks.'
