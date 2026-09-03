#!/bin/sh
set -eu
URL=${KIOSK_URL:-http://127.0.0.1:8080/}
curl -fsS --connect-timeout 2 --max-time 5 "$URL" >/dev/null
