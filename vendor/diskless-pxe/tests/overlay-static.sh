#!/bin/sh
# Syntax-check the overlay entrypoints. A glob that matches nothing used
# to skip every file and still print PASS (#304): relative paths from the
# wrong cwd left the unexpanded pattern, `[ -f ]` continued, and the loop
# checked zero files. ROOT is resolved from $0; 0 files is a failure.

set -eu

# shellcheck disable=SC1007
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

die() {
    printf 'overlay-static: %s\n' "$1" >&2
    exit "$2"
}

checked=0
for dir in \
    "$ROOT/rootfs/overlay/usr/local/bin" \
    "$ROOT/rootfs/overlay/usr/local/sbin" \
    "$ROOT/rootfs/overlay/etc/init.d"
do
    [ -d "$dir" ] || die "ERROR not a directory: $dir" 2
    for f in "$dir"/*; do
        [ -f "$f" ] || continue
        sh -n "$f" || die "syntax error: $f" 1
        checked=$((checked + 1))
    done
done
[ "$checked" -gt 0 ] || die "ERROR checked 0 files" 1

conf="$ROOT/rootfs/overlay/etc/imagectl/client.conf.example"
[ -f "$conf" ] || die "ERROR missing $conf" 2
set +e
grep -q 'ALLOW_IMAGING=0' "$conf"
grc=$?
set -e
case "$grc" in
    0)
        ;;
    1)
        die "FAIL ALLOW_IMAGING=0 not found in $conf" 1
        ;;
    *)
        die "ERROR grep exited $grc on $conf" 2
        ;;
esac

printf 'PASS overlay-static (%s files scanned)\n' "$checked"
