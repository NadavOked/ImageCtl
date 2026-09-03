#!/bin/sh
set -eu
bad=0
for f in ipxe/*.ipxe; do
  grep -q '^#!ipxe' "$f" || { echo "FAIL $f missing shebang"; bad=1; }
  grep -nE '(^|[[:space:]])(sanboot|exit|chain|kernel|initrd|boot)([[:space:]]|$)' "$f" >/dev/null || echo "WARN $f has no boot action"
done
exit "$bad"
