# monitor.sh -- supervised RFB monitor, independently gated from SSH.
# POSIX sh (busybox ash).

MONITOR_PORT="${MONITOR_PORT:-5900}"
MONITOR_BIN="${MONITOR_BIN:-/usr/bin/imagectl-monitor}"

_monitor_spawn() {
    setsid sh -c '
        trap "" HUP
        while :; do
            "$@" </dev/null
            rc=$?
            echo "monitor: helper exited (rc=$rc); restarting in 5s" >&2
            sleep 5 || echo "monitor: restart sleep interrupted" >&2
        done
    ' sh "$@" </dev/null >> "$LOG_FILE" 2>&1 &
    _monitor_pid=$!
}

monitor_start() {
    [ "${IMAGECTL_MONITOR:-0}" = 1 ] || return 0
    [ -n "${_monitor_pid:-}" ] &&
        kill -0 "$_monitor_pid" 2>/dev/null && return 0

    case "${D_ROLE:-}" in
        build)
            _monitor_input=--input
            ;;
        cloner)
            _monitor_input=
            ;;
        classroom)
            # Deliberate future gate: classroom monitoring is not built.
            return 0
            ;;
        *)
            return 0
            ;;
    esac

    [ -n "${IP:-}" ] || {
        log "monitor: management IP unavailable; not starting"
        return 1
    }
    [ -x "$MONITOR_BIN" ] || {
        log "monitor: binary absent from image"
        return 1
    }

    # shellcheck disable=SC2086 # optional --input is intentionally a word.
    _monitor_spawn "$MONITOR_BIN" --bind "$IP" --port "$MONITOR_PORT" \
        --fps 10 $_monitor_input
    log "monitor: supervisor started on $IP:$MONITOR_PORT" \
        "$([ -n "$_monitor_input" ] && echo '(input enabled)' ||
            echo '(view only)')"
}
