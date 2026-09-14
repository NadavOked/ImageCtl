#!/bin/sh
set -eu
# A package declared in packages.txt must appear as an exact `P:<name>`
# line in the given APKINDEX files (#306). Required packages that appear
# in those indexes must be declared (#307). Optional kiosk packages
# (cage, cog) WARN if undeclared or absent from the index. No index is
# "we did not check", not a pass. Extract APKINDEX from Alpine's
# APKINDEX.tar.gz (main and community) before calling, or pass already-
# extracted text.

if [ "$#" -lt 2 ]; then
    echo "usage: check-runtime-deps.sh <packages.txt> <APKINDEX> [APKINDEX...]" >&2
    echo "APKINDEX required: cannot verify package availability" >&2
    exit 2
fi

PKG=$1
shift

required="curl iproute2 util-linux partclone zstd udpcast"
optional="cage cog"

if [ ! -f "$PKG" ]; then
    echo "packages file not found: $PKG" >&2
    exit 2
fi

for idx in "$@"; do
    if [ ! -f "$idx" ]; then
        echo "APKINDEX not found: $idx" >&2
        exit 2
    fi
done

# A tarball, an empty file, or a binary is "we did not check".
set +e
grep -q '^P:' "$@"
idx_rc=$?
set -e
if [ "$idx_rc" -eq 1 ]; then
    echo "APKINDEX has no P: records" >&2
    exit 2
fi
if [ "$idx_rc" -ne 0 ]; then
    echo "failed to read APKINDEX" >&2
    exit 2
fi

fail=0
checked=0

for p in $required; do
    checked=$((checked + 1))
    set +e
    grep -Eq "^${p}([[:space:]]|$)" "$PKG"
    declared=$?
    grep -Fxq "P:${p}" "$@"
    present=$?
    set -e
    if [ "$declared" -ne 0 ] && [ "$declared" -ne 1 ]; then
        echo "could not read packages.txt" >&2
        exit 2
    fi
    if [ "$present" -ne 0 ] && [ "$present" -ne 1 ]; then
        echo "failed to read APKINDEX" >&2
        exit 2
    fi
    if [ "$declared" -eq 0 ] && [ "$present" -eq 1 ]; then
        echo "FAIL required package not in APKINDEX: $p" >&2
        fail=1
    elif [ "$declared" -eq 1 ] && [ "$present" -eq 0 ]; then
        echo "FAIL required package not declared: $p" >&2
        fail=1
    elif [ "$declared" -eq 1 ]; then
        echo "WARN package not declared: $p" >&2
    fi
done

for p in $optional; do
    checked=$((checked + 1))
    set +e
    grep -Eq "^${p}([[:space:]]|$)" "$PKG"
    declared=$?
    grep -Fxq "P:${p}" "$@"
    present=$?
    set -e
    if [ "$declared" -eq 1 ]; then
        echo "WARN optional package not declared: $p" >&2
    fi
    if [ "$present" -eq 1 ]; then
        echo "WARN optional package not in APKINDEX: $p" >&2
    fi
done

# Every declared package — required, optional, or otherwise — must be
# in some APKINDEX. A declared name that Alpine does not ship is #306.
missing=""
n=0
while IFS= read -r line || [ -n "$line" ]; do
    line=$(printf '%s' "$line" | tr -d '\r')
    case "$line" in
        ''|\#*) continue ;;
    esac
    case "$line" in
        *' '*) pkg=${line%% *} ;;
        *) pkg=$line ;;
    esac
    [ -n "$pkg" ] || continue
    n=$((n + 1))
    found=0
    for idx in "$@"; do
        set +e
        grep -Fxq "P:${pkg}" "$idx"
        rc=$?
        set -e
        if [ "$rc" -eq 0 ]; then
            found=1
            break
        fi
        if [ "$rc" -ne 1 ]; then
            echo "grep failed on $idx (rc=$rc)" >&2
            exit 2
        fi
    done
    if [ "$found" -eq 0 ]; then
        missing="$missing $pkg"
    fi
done < "$PKG"

if [ "$n" -eq 0 ]; then
    echo "no packages declared in $PKG" >&2
    exit 2
fi

if [ -n "$missing" ]; then
    echo "missing from APKINDEX:$missing" >&2
    fail=1
fi

[ "$checked" -gt 0 ] || { echo "checked 0 packages" >&2; exit 2; }

if [ "$fail" -ne 0 ]; then
    echo "check-runtime-deps: FAIL ($checked packages checked)" >&2
    exit 1
fi
echo "check-runtime-deps: PASS ($checked packages checked)"
