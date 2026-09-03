#!/bin/sh
set -eu
for _ in $(seq 1 30); do
  ip route | grep -q '^default ' && exit 0
  sleep 1
done
echo "network did not become ready" >&2
exit 1
