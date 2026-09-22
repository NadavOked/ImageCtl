# idle.sh -- idle power-off for a machine that waits for nobody (#434). POSIX sh.
#
# Three states that looked the same from the outside:
#   waiting for a person  -> this clock runs   -> power off
#   working               -> poll_sleep is never called: no clock
#   failed, message shown -> ui_error_hold never returns: no clock
# The clock is one file, the epoch of the last activity; poll_sleep asks
# idle_check once per beat. Activity: a changed hello answer, an open round
# for this group (a wave about to start is not idle), a key press in the
# native GUI (the kiosk writes idle.touch), or the stay_on file. Never on a
# failure screen -- the hold loops never reach poll_sleep, by construction.
# IDLE_POWEROFF_S=0 disables; the warning shows IDLE_WARN_S seconds first.

IDLE_POWEROFF_S="${IDLE_POWEROFF_S:-300}"
IDLE_WARN_S="${IDLE_WARN_S:-30}"

idle_reset() { date +%s > "$RUN_DIR/idle.since"; rm -f "$RUN_DIR/idle.warn"; }

idle_touched() {
    # The kiosk touches idle.touch on a key press; not older than the clock =
    # activity (same second counts -- the safe direction is staying on).
    [ -f "$RUN_DIR/idle.touch" ] && [ ! "$RUN_DIR/idle.since" -nt "$RUN_DIR/idle.touch" ]
}

idle_exempt() {
    [ "$IDLE_POWEROFF_S" -eq 0 ] 2>/dev/null && return 0
    [ -f "$RUN_DIR/stay_on" ] && return 0            # a screen that must stay lit
    [ "${D_SESSION_STATE:-}" = "open" ] && return 0  # a wave is about to start
    [ "${D_TASK:-null}" != "null" ] && return 0       # work was just handed to us
    return 1
}

idle_poweroff() {
    # The same rule as finish_and_stop's power-off branch: only after the
    # wake-on-LAN read-back. A machine that cannot be woken stays on, named.
    if ! arm_wol; then log "idle: wol not armed -- staying powered on"; idle_reset; return 1; fi
    log "idle: no activity for ${IDLE_POWEROFF_S}s -- powering off"
    sync; poweroff -f
}

idle_check() {
    [ -f "$RUN_DIR/idle.since" ] || { idle_reset; return 0; }
    if idle_exempt || idle_touched || [ "${POLL_MARK_CHANGED:-0}" = 1 ]; then idle_reset; return 0; fi
    _now=$(date +%s); _since=$(cat "$RUN_DIR/idle.since" 2>/dev/null || echo "$_now")
    _idle=$((_now - _since))
    [ "$_idle" -ge "$IDLE_POWEROFF_S" ] || return 0
    # the warning: the GUI reads idle.warn (guistate.sh -> idle_poweroff_at=)
    # and any key press cancels it through idle.touch
    _at=$((_now + IDLE_WARN_S)); printf '%s\n' "$_at" > "$RUN_DIR/idle.warn"
    log "idle: ${_idle}s without activity -- power-off in ${IDLE_WARN_S}s unless a key is pressed"
    _n=0
    while [ "$_n" -lt "$IDLE_WARN_S" ]; do
        sleep 1; _n=$((_n + 1))
        if idle_touched || [ -f "$RUN_DIR/stay_on" ]; then log "idle: cancelled by activity"; idle_reset; return 0; fi
    done
    idle_poweroff
}
