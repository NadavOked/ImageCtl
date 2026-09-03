#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
DIST="$ROOT/dist"
mkdir -p "$DIST"
VERSION=$(cat "$ROOT/VERSION")
NAME="imagectl-diskless-pxe-$VERSION"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT INT TERM
mkdir -p "$TMP/$NAME"
( cd "$ROOT" && tar --exclude='./dist' -cf - . ) | ( cd "$TMP/$NAME" && tar -xf - )
tar -C "$TMP" -czf "$DIST/$NAME.tar.gz" "$NAME"
if command -v zip >/dev/null 2>&1; then (cd "$TMP" && zip -qr "$DIST/$NAME.zip" "$NAME"); fi
printf 'Created %s\n' "$DIST/$NAME.tar.gz"
