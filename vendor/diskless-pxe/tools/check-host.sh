#!/bin/sh
set -eu
need="curl gzip cpio sha256sum"
for x in $need; do command -v "$x" >/dev/null 2>&1 || echo "WARN missing: $x"; done
for x in qemu-system-x86_64 shellcheck; do command -v "$x" >/dev/null 2>&1 || echo "OPTIONAL missing: $x"; done
