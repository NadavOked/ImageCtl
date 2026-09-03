#!/bin/sh
set -eu
. /usr/local/bin/imagectl-env.sh
LOG=/var/log/imagectl/kiosk.log
mkdir -p "$(dirname "$LOG")"
while :; do
  if /usr/local/bin/wait-online.sh "$KIOSK_URL" 30; then
    /usr/local/bin/start-kiosk.sh >>"$LOG" 2>&1 || true
  else
    echo "$(date -Iseconds 2>/dev/null || date) UI unavailable: $KIOSK_URL" >>"$LOG"
  fi
  sleep 2
done
