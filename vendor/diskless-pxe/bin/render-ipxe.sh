#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
CONF=${1:-$ROOT/config.example}
OUT=${2:-$ROOT/out/boot.ipxe}
# shellcheck disable=SC1090
. "$(realpath "$CONF")"
: "${IMAGECTL_SERVER:?IMAGECTL_SERVER required}"
mkdir -p "$(dirname "$OUT")"
sed "s#@@SERVER@@#$IMAGECTL_SERVER#g" "$ROOT/ipxe/boot.template.ipxe" > "$OUT"
printf 'Rendered %s\n' "$OUT"
