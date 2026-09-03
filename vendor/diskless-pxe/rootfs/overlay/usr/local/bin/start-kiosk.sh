#!/bin/sh
set -eu
URL=${KIOSK_URL:-http://127.0.0.1:8080/}
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/0}
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
# Cage owns the display; Cog is the single fullscreen web application.
exec cage -- cog --platform=wl "$URL"
