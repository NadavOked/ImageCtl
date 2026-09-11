#!/bin/sh
# Build and run the Hebrew bidi smoke test. Exit status is the test's own:
# 0 only when every positional check passed. The PNG is for the human eye;
# the checks are the evidence.
#   tools/run-smoke.sh [out.png] [--any-font]
set -eu
cd "$(dirname "$0")/.."
make hebrew-bidi-smoke
out=hebrew-bidi.png
extra=""
for arg in "$@"; do
    case "$arg" in
        --any-font) extra="--any-font" ;;
        *) out="$arg" ;;
    esac
done
# shellcheck disable=SC2086  # $extra is either empty or one flag
./hebrew-bidi-smoke "$out" $extra
echo "rendered $out -- to the eye: שלום at the far right, 'Office 2024' to its left, '12' at the far left"
