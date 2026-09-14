#!/bin/sh
# Wait until URL ($1) answers, up to TIMEOUT ($2, default 30) seconds.
# The previous version ignored both arguments and grepped `ip route` for
# a default gateway (#307). The kiosk watchdog calls this as
# `wait-online.sh "$KIOSK_URL" 30`; a dead server looked "online" in
# zero seconds.
set -eu

URL=${1:-}
TIMEOUT=${2:-30}

if [ -z "$URL" ]; then
    echo "wait-online: URL argument is required" >&2
    exit 2
fi
case "$TIMEOUT" in
    ''|*[!0-9]*)
        echo "wait-online: timeout must be a non-negative integer, got: $TIMEOUT" >&2
        exit 2
        ;;
esac

if ! command -v curl >/dev/null 2>&1; then
    echo "wait-online: curl is not installed — cannot check $URL" >&2
    exit 2
fi

i=0
while [ "$i" -lt "$TIMEOUT" ]; do
    if curl -fsS --connect-timeout 2 --max-time 5 "$URL" >/dev/null; then
        exit 0
    fi
    i=$((i + 1))
    if [ "$i" -lt "$TIMEOUT" ]; then
        sleep 1
    fi
done
echo "wait-online: $URL did not become ready in ${TIMEOUT}s" >&2
exit 1
