#!/bin/sh
set -eu
# Integration boundary only. Intentionally does NOT guess ImageCtl API contracts.
# Usage: imaging-adapter.sh <target> <receive-command...>
TARGET=${1:?target required}; shift
"$(dirname "$0")/safety-check.sh" "$TARGET" >/dev/null
[ "$#" -gt 0 ] || { echo "receive command required" >&2; exit 6; }
echo "Starting imaging target=$TARGET" >&2
# The supplied receiver must emit the image stream to stdout. Integration can use
# existing ImageCtl udpcast/zstd/partclone flow rather than curl|dd.
"$@" | dd of="$TARGET" bs=4M conv=fsync status=progress
sync
