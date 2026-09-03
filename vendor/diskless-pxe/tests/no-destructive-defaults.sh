#!/bin/sh
# Refuse to ship destructive primitives in the defaults that boot automatically.
#
# The previous version of this file was a guard that could not fail (#231):
#
#     set -eu
#     ! grep -R -nE '(dd|wipefs|mkfs|sgdisk|parted)' "$ROOT/rootfs/overlay" ...
#     echo "destructive-default guard: PASS"
#
# In POSIX, `set -e` does not apply to a pipeline preceded by `!`. So when grep
# DID find a match -- the exact case this guard exists for -- the script printed
# the hit, fell through to the next line, printed PASS and exited 0.
#
# The rewrite therefore does three things the original did not:
#
#   1. It never negates a command. The exit status is read explicitly, and each
#      of grep's three outcomes is handled by name: 0 = found (fail), 1 = clean,
#      anything else = the scan itself broke (fail, loudly).
#   2. "Could not check" is not "checked, clean". A missing scan root, or a grep
#      that errors out, exits 2 -- it does not fall through to PASS.
#   3. PASS carries positive evidence: the number of files actually scanned. A
#      guard pointed at an empty or wrong directory reports 0 hits and looks
#      identical to a guard that did its job; requiring a non-zero file count is
#      what separates the two.
#
# ROOT is resolved from $0, not from the caller's cwd, so the guard scans the
# same tree no matter where it is invoked from.

set -eu

# `CDPATH= cd` clears CDPATH for that one cd and is deliberate, not a typo.
# The explanation goes on its own line: shellcheck stops parsing a directive
# that carries trailing prose, which silently disables every later check.
# shellcheck disable=SC1007
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
# The separator class is `[;&|[:space:]]` and not `[;&| ]`: a tab-indented
# `\tdd if=/dev/zero of=/dev/sda` is valid shell and the literal-space version
# did not match it. A guard with a whitespace-shaped hole in it is worse than
# no guard, because it reads as coverage.
PATTERN='(^|[;&|[:space:]])(dd|wipefs|mkfs|sgdisk|parted)[[:space:]]'

# The roots are separate variables and every expansion is quoted. A single
# space-separated string word-splits on a checkout path that contains a space
# ("C:\Program Files\..."), and the guard then dies on a path that does not
# exist instead of scanning the package.
OVERLAY_ROOT="$ROOT/rootfs/overlay"
IPXE_ROOT="$ROOT/ipxe"

die() {
    printf 'destructive-default guard: %s\n' "$1" >&2
    exit "$2"
}

# --- positive evidence: something was actually scanned -----------------------
# The `-d` check stays OUT of the command substitutions below on purpose: `die`
# runs `exit`, and inside `$(...)` that only ends the subshell -- the parent
# would sail on with an empty count. That is the same class of bug this file
# exists to remove.
for dir in "$OVERLAY_ROOT" "$IPXE_ROOT"; do
    [ -d "$dir" ] || die "ERROR scan root is not a directory: $dir" 2
done
overlay_files=$(find "$OVERLAY_ROOT" -type f | wc -l | tr -d ' ')
ipxe_files=$(find "$IPXE_ROOT" -type f | wc -l | tr -d ' ')
scanned=$((overlay_files + ipxe_files))
[ "$scanned" -gt 0 ] || die "ERROR scanned 0 files under $OVERLAY_ROOT $IPXE_ROOT" 2

# --- the scan itself ---------------------------------------------------------
# stderr is folded into the captured output on purpose: hiding it behind
# 2>/dev/null is how a broken scan becomes indistinguishable from a clean one.
set +e
hits=$(grep -R -nE "$PATTERN" "$OVERLAY_ROOT" "$IPXE_ROOT" 2>&1)
rc=$?
set -e

case "$rc" in
    0)
        printf '%s\n' "$hits" >&2
        die "FAIL destructive command in defaults that boot automatically" 1
        ;;
    1)
        ;;
    *)
        printf '%s\n' "$hits" >&2
        die "ERROR grep exited $rc -- the scan failed, nothing was proven" 2
        ;;
esac

printf 'destructive-default guard: PASS (%s files scanned, 0 hits)\n' "$scanned"
