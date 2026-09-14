#!/bin/sh
# Local bridge contract tests: no server, GUI, disks, or root required.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
RUN_DIR=$(mktemp -d)
trap 'rm -rf "$RUN_DIR"' EXIT
GUI_DIR=$RUN_DIR/gui
mkdir "$GUI_DIR"
. "$ROOT/agent/lib/common.sh"
. "$ROOT/agent/lib/buildmenu.sh"
. "$ROOT/agent/lib/guibridge.sh"
CONSOLE_JAR=$GUI_DIR/console.jar
SERVER=http://invalid.example
RECOVERY_USER=operator RECOVERY_PASS=secret
sleep() { :; }
# Mock only the transport. Production HTTP-code and cookie checks run.
curl() {
    cat >/dev/null
    case "$scenario" in
        good) printf 'localhost\tFALSE\t/\tFALSE\t0\timagectl_session\tticket\n' > "$CONSOLE_JAR"; printf 200 ;;
        no_cookie) : > "$CONSOLE_JAR"; printf 200 ;;
        empty_cookie) printf 'localhost\tFALSE\t/\tFALSE\t0\timagectl_session\t\n' > "$CONSOLE_JAR"; printf 200 ;;
        refused) printf 401 ;;
        transport) printf 200; return 7 ;;
    esac
}
scenario=good
console_signin > "$RUN_DIR/out"
for scenario in no_cookie empty_cookie refused transport; do
    if console_signin > "$RUN_DIR/out"; then echo "FAIL accepted $scenario"; exit 1; fi
    [ ! -e "$CONSOLE_JAR" ]
done
echo 'PASS HTTP/cookie rejection, including transport failure with HTTP 200'
# Auth must return just a role, and must reject role lookup failure.
gui_role() { [ "$role_ok" = yes ] || return 1; GUI_ROLE=admin; }
scenario=good role_ok=yes
printf 'operator\nsecret\n' | gui_auth > "$RUN_DIR/auth"
[ "$(cat "$RUN_DIR/auth")" = admin ]
role_ok=no
if printf 'operator\nsecret\n' | gui_auth > "$RUN_DIR/auth"; then exit 1; fi
[ ! -s "$RUN_DIR/auth" ] && [ ! -e "$CONSOLE_JAR" ]
echo 'PASS auth stdout and unresolved-role refusal'
# Production record parser: multiline values, no eval, duplicate/EOF refusal.
gui_dispatch() { printf '%s|%s|%s\n' "$token" "$name" "$desc" >> "$RUN_DIR/dispatch"; }
printf 'capture-start\nname=Office 2024\ndesc=$(touch BAD) = literal\n\n' | gui_records
[ "$(cat "$RUN_DIR/dispatch")" = 'capture-start|Office 2024|$(touch BAD) = literal' ]
for record in 'capture-start\nname=a\nname=b\n\n' 'capture-start\nname=a\n'; do
    if printf '%b' "$record" | gui_records; then echo 'FAIL malformed record accepted'; exit 1; fi
done
[ "$(wc -l < "$RUN_DIR/dispatch" | tr -d ' ')" = 1 ]
echo 'PASS record boundaries, duplicate/partial rejection, literal shell text'
