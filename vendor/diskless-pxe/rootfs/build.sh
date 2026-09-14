#!/bin/sh
set -eu
# Run on Alpine Linux as root. Builds an initramfs/rootfs staging tree without
# modifying ImageCtl. Exact driver set must be validated on target classroom PCs.
#
# packages.txt and overlay are resolved from $0, not cwd: a relative grep of
# packages.txt is #385 — missing/empty list, apk add with no packages exits 0,
# and this script printed "Rootfs staged at" over an empty tree.

# `CDPATH= cd` clears CDPATH for that one cd and is deliberate, not a typo.
# shellcheck disable=SC1007
HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PACKAGES_FILE="$HERE/packages.txt"
OVERLAY="$HERE/overlay"

OUT=${OUT:-$(pwd)/out}
ROOT=${ROOT:-$(pwd)/work/rootfs}

die() {
    printf 'rootfs/build.sh: %s\n' "$1" >&2
    exit "$2"
}

# Read grep's status by name. A pipeline (`grep | tr`) hides grep's 1/2 behind
# tr's 0, and POSIX discards command-substitution status, so `set -e` would
# not fire either. `set -o pipefail` is not POSIX and is not a fix here.
set +e
raw=$(grep -v '^#' "$PACKAGES_FILE")
rc=$?
set -e

case "$rc" in
    0)
        ;;
    1)
        die "packages.txt has no packages (empty or comments only): $PACKAGES_FILE" 1
        ;;
    *)
        die "could not read packages.txt (grep exited $rc): $PACKAGES_FILE" 2
        ;;
esac

# Default IFS: blank lines collapse. Package names have no spaces.
# shellcheck disable=SC2086
set -- $raw
count=$#
[ "$count" -gt 0 ] || die "packages.txt has no packages: $PACKAGES_FILE" 1

mkdir -p "$OUT" "$ROOT"
apk --root "$ROOT" --initdb add "$@"
cp -a "$OVERLAY"/. "$ROOT/"
chmod +x "$ROOT/etc/local.d/imagectl.start"
echo "added $count packages from $PACKAGES_FILE"
echo "Rootfs staged at $ROOT"
echo "Next integration step: generate/initramfs with the kernel modules required by target hardware."
