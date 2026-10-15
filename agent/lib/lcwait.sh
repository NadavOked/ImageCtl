# lcwait.sh -- #1367: what the local clone's screens do while they wait for a
# person (localclone.sh), split off before the 300-line wall. The machine kept
# no hello on these screens and had no ceiling: a cloner went silent for an
# hour, missed the wave, and the console's power-off never reached it.
#
# * the ceiling: no touch for LC_QUIET_S and the machine goes on from the
#   server (lc_quiet_reset / lc_quiet_over);
# * the console's power request that the beat (attended.sh) kept: carried out
#   on the screens (lc_power_check), and during a copy only when the operator
#   confirmed it over the warning (lc_power_during_copy -- Nadav 14/10).
# POSIX sh (busybox ash).

lc_quiet_reset() { _lq_at=$(date +%s); touch "$LC/quiet.mark"; }   # touch: a truncate may keep the mtime

lc_quiet_over() {
    # 0 = nobody touched the screen for LC_QUIET_S. A key or a click is the
    # kiosk's idle.touch (#434, at most every 10 s): newer than the mark is a
    # touch. A find that fails is not one -- the count runs on, toward the
    # server (principle 1), never toward a wait for ever.
    if [ -f "$RUN_DIR/idle.touch" ] && _lq_n=$(find "$LC/quiet.mark" -newer "$RUN_DIR/idle.touch" 2>/dev/null) &&
            [ -z "$_lq_n" ]; then
        lc_quiet_reset; return 1
    fi
    [ $(( $(date +%s) - ${_lq_at:-0} )) -ge "${_lq_max:-600}" ]
}

lc_copy_busy() {
    # 0 = a local copy is writing now: from lc_run's first write to its end.
    case "$(cat "${LC:-/nonexistent}/phase" 2>/dev/null)" in
        copying|shrinking|verifying|restoring) return 0 ;;
    esac
    return 1
}

lc_power_during_copy() {
    # #1367 (Nadav 14/10): the operator saw "in the middle of a local clone --
    # the target will be broken" and confirmed (power_confirmed). Runs in the
    # beat (attended.sh), since lc_run holds the screen's loop. Wake-on-LAN
    # first for a power-off (#587): not armed = nothing is done, the copy goes
    # on, aloud. Then the targets are marked failed, the journal is told
    # (lc_report, best effort) -- and only then the power goes.
    mv "$RUN_DIR/power.resp" "$RUN_DIR/power.taken" 2>/dev/null || return 0
    _pd_a=$(json_get "$RUN_DIR/power.taken" .power_action)
    log "local clone: the console confirmed '$_pd_a' in the middle of the copy -- the targets are marked failed"
    if [ "$_pd_a" = poweroff ]; then
        safe_poweroff "local clone: confirmed during copy" lc_power_commit || \
            log "local clone: wake-on-LAN was not armed -- not powering off, the copy goes on"
        return 0
    fi
    lc_power_commit
    _pk_r="${RESP:-}"; RESP="$RUN_DIR/power.taken"; power_check; RESP="$_pk_r"
}

lc_power_commit() {
    for _pd_t in $(cat "$LC/run/targets" 2>/dev/null); do
        fail_written_target "$_pd_t" "המחשב כובה מהקונסולה באמצע שיכפול מקומי — הדיסק שבור ואינו שמיש"
    done
    _lr_src=$(cat "$LC/run/src" 2>/dev/null); _lr_r="$LC/run"
    date +%s > "$_lr_r/ended"; echo 1 > "$_lr_r/stopped"; lc_report
}

lc_power_check() {
    # #1367: the console's power-off/reboot (#980) came in a beat's answer --
    # attended.sh kept it. Carried out by power_check's own rule (idle.sh:
    # Wake-on-LAN first, or the machine stays on and says so).
    [ -s "$RUN_DIR/power.resp" ] && mv "$RUN_DIR/power.resp" "$RUN_DIR/power.taken" || return 0
    log "local clone: the console asked to $(json_get "$RUN_DIR/power.taken" .power_action) -- carrying it out"
    _pk_r="${RESP:-}"; RESP="$RUN_DIR/power.taken"; power_check; RESP="$_pk_r"
}
