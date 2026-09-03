#!/bin/sh
# Parse imagectl.* kernel parameters without hard-coding project API contracts.
for arg in $(cat /proc/cmdline 2>/dev/null); do
  case "$arg" in
    imagectl.server=*) IMAGECTL_SERVER=${arg#*=} ;;
    imagectl.kiosk=*) KIOSK_URL=${arg#*=} ;;
  esac
done
: "${IMAGECTL_SERVER:=http://127.0.0.1:8080}"
: "${KIOSK_URL:=${IMAGECTL_SERVER}/}"
export IMAGECTL_SERVER KIOSK_URL
