#!/bin/sh
set -eu
# Integration boundary only. Intentionally does NOT guess ImageCtl API contracts.
# Usage: imaging-adapter.sh <target> <expected-bytes> <expected-sha256> <receive-command...>
#
# A POSIX pipeline reports only the LAST command's status, and the bash option
# that changes that does not exist in busybox ash -- writing it here would pass
# under bash and break in the initramfs. A receiver that died mid-stream left
# the writer at an early EOF, the writer exited 0, and this script declared
# success over a truncated disk. So each link is checked on its own through a
# status file, and the write is then proved by reading the target back -- a
# receiver that exits 0 after a short body (curl with a truncated
# Content-Length) still fails here.
#
# The byte count deliberately does NOT come from `dd`. GNU dd prints
# "N bytes ... copied" on stderr; busybox dd prints only "0+1 records in/out"
# and no byte total at all, so parsing dd's stderr failed 100% of the time on
# the one shell that actually runs in the initramfs. It cannot come from the
# target either: the real target is a whole block device, `wc -c` on it would
# read the entire disk and return the DEVICE size, so a severed stream would
# report "wrote <disk size> of <image size>" -- a number that is not a lie by
# accident but by construction. The only place the truth exists is the stream,
# so the stream is counted, by `wc -c` as the last link of the pipeline.
TARGET=${1:?target required}; shift
EXPECT_BYTES=${1:?expected byte count required}; shift
EXPECT_SHA=${1:?expected sha256 required}; shift

case "$EXPECT_BYTES" in
  ''|*[!0-9]*) echo "expected byte count must be digits: '$EXPECT_BYTES'" >&2; exit 7 ;;
esac
[ "$EXPECT_BYTES" -gt 0 ] || { echo "expected byte count must be > 0" >&2; exit 7; }
case "$EXPECT_SHA" in
  *[!0-9a-f]*) echo "expected sha256 must be lowercase hex: '$EXPECT_SHA'" >&2; exit 7 ;;
esac
[ "${#EXPECT_SHA}" -eq 64 ] || { echo "expected sha256 must be 64 hex chars" >&2; exit 7; }

"$(dirname "$0")/safety-check.sh" "$TARGET" >/dev/null
[ "$#" -gt 0 ] || { echo "receive command required" >&2; exit 6; }
for tool in sha256sum tee wc; do
  command -v "$tool" >/dev/null || { echo "$tool missing, cannot verify" >&2; exit 8; }
done

WORK=$(mktemp -d) || { echo "mktemp failed, cannot record link status" >&2; exit 8; }
trap 'rm -rf "$WORK"' EXIT INT TERM HUP
SRC_RC="$WORK/src.rc"; WRITE_RC="$WORK/write.rc"

echo "Starting imaging target=$TARGET bytes=$EXPECT_BYTES sha256=$EXPECT_SHA" >&2
# `set +e` in each subshell so it reaches the echo even when its command dies:
# the status is recorded and enforced below, not discarded. `tee` writes the
# target and passes the stream on, so `wc -c` -- the last link, whose status is
# the pipeline's -- counts exactly the bytes that went to the disk. `dd
# conv=fsync` is gone with dd; the `sync` below is what flushes the write.
WROTE=$(
  ( set +e; "$@"; echo "$?" >"$SRC_RC" ) \
  | ( set +e; tee "$TARGET"; echo "$?" >"$WRITE_RC" ) \
  | wc -c
) || WROTE=''
sync
WROTE=$(printf '%s' "$WROTE" | tr -cd '0-9')

case "${WROTE:-}" in
  ''|*[!0-9]*) echo "FAILED target=$TARGET: the stream was not counted, so nothing was verified" >&2; exit 8 ;;
esac
[ -s "$SRC_RC" ] || { echo "FAILED target=$TARGET: receive command left no exit status; wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 4; }
SRC_STATUS=$(cat "$SRC_RC")
case "$SRC_STATUS" in
  ''|*[!0-9]*) echo "FAILED target=$TARGET: receive status unreadable; wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 4 ;;
esac
[ -s "$WRITE_RC" ] || { echo "FAILED target=$TARGET: the write left no exit status; wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 5; }
WRITE_STATUS=$(cat "$WRITE_RC")
case "$WRITE_STATUS" in
  ''|*[!0-9]*) echo "FAILED target=$TARGET: write status unreadable; wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 5 ;;
esac
[ "$WRITE_STATUS" -eq 0 ] || { echo "FAILED target=$TARGET: write exited $WRITE_STATUS after $WROTE of $EXPECT_BYTES bytes" >&2; exit 5; }
[ "$SRC_STATUS" -eq 0 ] || { echo "FAILED target=$TARGET: receive command exited $SRC_STATUS; wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 4; }
[ "$WROTE" -eq "$EXPECT_BYTES" ] || { echo "FAILED target=$TARGET: wrote $WROTE of $EXPECT_BYTES bytes" >&2; exit 4; }

ACTUAL_SHA=$(head -c "$EXPECT_BYTES" "$TARGET" | sha256sum | cut -d' ' -f1)
[ "$ACTUAL_SHA" = "$EXPECT_SHA" ] || { echo "FAILED target=$TARGET: sha256 read back $ACTUAL_SHA over $EXPECT_BYTES bytes, expected $EXPECT_SHA" >&2; exit 9; }
echo "OK target=$TARGET bytes=$WROTE/$EXPECT_BYTES sha256=$ACTUAL_SHA"
