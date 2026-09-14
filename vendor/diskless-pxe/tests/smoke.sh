#!/bin/sh
# Package smoke: syntax of every shell file, required artefacts, no
# destructive primitives in automatic startup.
#
# Two ways this used to print PASS without checking (#304):
#   * `find` returning nothing left the `sh -n` loop unrun, fail=0.
#   * `if grep ... 2>/dev/null` treated grep's 2 (scan error, including
#     a missing root that still had a match) as "clean".
# ROOT is already from $0. 0 files scanned is a failure. grep 0/1/other
# are handled by name.

set -eu

# shellcheck disable=SC1007
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
fail=0
scanned=0

set +e
files=$(find "$ROOT" -type f \( -name '*.sh' -o -path '*/etc/local.d/*.start' \))
frc=$?
set -e
[ "$frc" -eq 0 ] || { echo "smoke: FAIL find exited $frc under $ROOT" >&2; exit 2; }

# Default IFS: paths in this tree have no spaces. Empty `find` must not
# look like "every file is syntactically fine".
# shellcheck disable=SC2086
for f in $files; do
    scanned=$((scanned + 1))
    sh -n "$f" || fail=1
done
[ "$scanned" -gt 0 ] || { echo "smoke: FAIL found 0 shell scripts under $ROOT" >&2; exit 1; }

for f in "$ROOT/ipxe/boot.ipxe" "$ROOT/ipxe/fallback.ipxe" "$ROOT/ipxe/boot.template.ipxe"; do
    [ -s "$f" ] || { echo "missing $f" >&2; fail=1; }
done
for f in README.md INTEGRATION.md config.example docs/ACCEPTANCE_TESTS.md docs/SECURITY.md; do
    [ -s "$ROOT/$f" ] || { echo "missing $f" >&2; fail=1; }
done

OVERLAY_ETC="$ROOT/rootfs/overlay/etc"
OVERLAY_BIN="$ROOT/rootfs/overlay/usr/local/bin"
for dir in "$OVERLAY_ETC" "$OVERLAY_BIN"; do
    [ -d "$dir" ] || { echo "smoke: FAIL scan root is not a directory: $dir" >&2; exit 2; }
done

# Destructive primitives must not appear in automatic startup.
PATTERN='(^|[;&| ])(dd|partclone\.[a-z0-9_-]+).*of=/dev|mkfs\.|wipefs|sgdisk.*--zap'
set +e
hits=$(grep -R -nE "$PATTERN" "$OVERLAY_ETC" "$OVERLAY_BIN" 2>&1)
grc=$?
set -e
case "$grc" in
    0)
        printf '%s\n' "$hits" >&2
        echo 'destructive command found in automatic boot overlay' >&2
        fail=1
        ;;
    1)
        ;;
    *)
        printf '%s\n' "$hits" >&2
        echo "smoke: FAIL grep exited $grc -- the scan failed, nothing was proven" >&2
        exit 2
        ;;
esac

if [ "$fail" -ne 0 ]; then
    echo "smoke: FAIL ($scanned files scanned)" >&2
    exit 1
fi
echo "smoke: PASS ($scanned files scanned)"
