#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
fail=0
for f in $(find "$ROOT" -type f -name '*.sh' -o -path '*/etc/local.d/*.start'); do
  sh -n "$f" || fail=1
done
for f in "$ROOT/ipxe/boot.ipxe" "$ROOT/ipxe/fallback.ipxe" "$ROOT/ipxe/boot.template.ipxe"; do
  [ -s "$f" ] || { echo "missing $f" >&2; fail=1; }
done
for f in README.md INTEGRATION.md config.example docs/ACCEPTANCE_TESTS.md docs/SECURITY.md; do
  [ -s "$ROOT/$f" ] || { echo "missing $f" >&2; fail=1; }
done
# Destructive primitives must not appear in automatic startup.
if grep -R -nE '(^|[;&| ])(dd|partclone\.[a-z0-9_-]+).*of=/dev|mkfs\.|wipefs|sgdisk.*--zap' "$ROOT/rootfs/overlay/etc" "$ROOT/rootfs/overlay/usr/local/bin" 2>/dev/null; then
  echo 'destructive command found in automatic boot overlay' >&2; fail=1
fi
[ "$fail" -eq 0 ]
echo 'smoke: PASS'
