#!/usr/bin/env bash
# Refuse lab access material in an installer initrd unless explicitly allowed.
set -euo pipefail

[[ $# -ge 1 && $# -le 2 ]] || { echo "usage: initrd-lab-gate.sh INITRD [--lab]" >&2; exit 2; }
INITRD=$1
ALLOW_LAB=false
[[ ${2:-} == --lab ]] && ALLOW_LAB=true

listing=$(mktemp)
trap 'rm -f "$listing"' EXIT
(zstd -dc "$INITRD" 2>/dev/null || gzip -dc "$INITRD" 2>/dev/null || cat "$INITRD") \
    | cpio -t >"$listing" 2>/dev/null \
    || { echo "build-iso: cannot inspect initrd contents" >&2; exit 1; }

if grep -Eq '(^|/)dropbear$|(^|/)etc/imagectl/authorized_keys$' "$listing"; then
    $ALLOW_LAB || {
        echo "build-iso: initrd contains lab SSH access; pass --lab to build a marked lab ISO" >&2
        exit 1
    }
    echo true
else
    echo false
fi
