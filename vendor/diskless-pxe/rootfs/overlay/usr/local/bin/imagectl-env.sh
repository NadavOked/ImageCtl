#!/bin/sh
# Parse imagectl.* kernel parameters. A cmdline that cannot be read, or
# that has no imagectl.server, is a failure — not localhost (#307).
# IMAGECTL_CMDLINE is a test hook; production leaves it unset.
CMDLINE=${IMAGECTL_CMDLINE:-/proc/cmdline}

_imagectl_env_die() {
    echo "imagectl-env: $1" >&2
    return 1 2>/dev/null || exit 1
}

if [ ! -r "$CMDLINE" ]; then
    _imagectl_env_die "cannot read kernel cmdline ($CMDLINE)" || return 1 2>/dev/null || exit 1
fi

IMAGECTL_SERVER=
KIOSK_URL=
# Kernel params are space-separated; values here have no spaces.
# shellcheck disable=SC2013
for arg in $(cat "$CMDLINE"); do
    case "$arg" in
        imagectl.server=*) IMAGECTL_SERVER=${arg#*=} ;;
        imagectl.kiosk=*) KIOSK_URL=${arg#*=} ;;
    esac
done

if [ -z "$IMAGECTL_SERVER" ]; then
    _imagectl_env_die "imagectl.server is missing from kernel cmdline" || return 1 2>/dev/null || exit 1
fi

: "${KIOSK_URL:=${IMAGECTL_SERVER}/}"
export IMAGECTL_SERVER KIOSK_URL
