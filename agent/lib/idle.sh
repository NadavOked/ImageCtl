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
    # `find -newer` and not `-nt`: -nt is undefined in POSIX sh (SC3013).
    # find prints idle.since only when it is strictly newer than the touch;
    # a find that fails prints nothing = "touched" = stay on, the safe side.
    [ -f "$RUN_DIR/idle.touch" ] || return 1
    [ -z "$(find "$RUN_DIR/idle.since" -newer "$RUN_DIR/idle.touch" 2>/dev/null)" ]
}

idle_exempt() {
    [ "$IDLE_POWEROFF_S" -eq 0 ] 2>/dev/null && return 0
    [ -f "$RUN_DIR/stay_on" ] && return 0            # a screen that must stay lit
    [ "${D_SESSION_STATE:-}" = "open" ] && return 0  # a wave is about to start
    [ "${D_TASK:-null}" != "null" ] && return 0       # work was just handed to us
    idle_round_shown && return 0                     # #1560: a round this machine opened or shows
    return 1
}

idle_round_shown() {
    # #1560 (metal 17/10, twice): the build machine powered off mid-round,
    # 91% on its screen -- only keys and the mouse counted. Not idle: a round
    # opened from this GUI that is still active (roomflow.sh room.watch), or
    # an active round / class session the kiosk is drawing now -- its read
    # newer than the clock (it rewrites it every beat; a kiosk that died
    # stops, and the clock runs again). A read that fails is no round.
    [ -f "$RUN_DIR/room.watch" ] && return 0
    _ir_g="${GUI_DIR:-$RUN_DIR/gui}"
    case "$(cat "$_ir_g/mode" 2>/dev/null)" in
        room|direct) _ir_f="$_ir_g/room.json"; _ir_q='.round.state == "active"' ;;
        classes) _ir_f="$_ir_g/session-shown.json"; _ir_q='.session.state == "open" or .session.state == "running"' ;;
        *) return 1 ;;
    esac
    [ -n "$(find "$_ir_f" -newer "$RUN_DIR/idle.since" 2>/dev/null)" ] &&
        jq -e "$_ir_q" "$_ir_f" >/dev/null 2>&1
}

idle_poweroff() {
    safe_poweroff "idle: no activity for ${IDLE_POWEROFF_S}s" || { idle_reset; return 1; }
}

power_check() {
    # #980: the console's "power off the room" rides the hello answer
    # (power_action) at the same rate -- no new polling. Same rule as the
    # idle power-off: only after the wake-on-LAN read-back (#587), or the room
    # could not be woken again. The server hands it out once.
    # #977 section 4: "reboot" from the machine drawer, same channel. No
    # arm_wol: a rebooted machine comes back by itself.
    case "$(json_get "$RESP" ".power_action")" in
        poweroff) ;;
        reboot) log "power: the console asked to reboot -- rebooting"; sync; reboot -f; return ;;
        *) return 0 ;;
    esac
    safe_poweroff "power: the console asked to power off"
}

idle_check() {
    power_check || return 1
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

# --- the end of a task (Nadav, 17/10, #1560) ----------------------------------
# A cloner after any of its tasks -- local clone, a round, restore from the
# server, a direct send it received -- done or failed: no question. The
# machine powers off in END_POWEROFF_S, or at once on the GUI's "כבה עכשיו";
# the power-off is finish_and_stop -> safe_poweroff (WoL armed first, #587).
# Some or all targets failed (partial|failed): the count can be stopped to
# read the results on the screen behind it -- then the power-off is
# END_HOLD_S away, still counted and still one button (no screen without an
# action, metal 17/10). The build machine's ends ask instead (task.sh).
#   $RUN_DIR/end.poweroff  <epoch>|<kind>|<done>|<targets>|<held 0/1> while the
#                          count runs; every GUI screen draws it (gui_hello_state)
#   $RUN_DIR/end.now       the button: the bridge writes it on end-poweroff
#   $RUN_DIR/end.hold      "stop the count": the bridge, on end-hold (partial|failed)
END_POWEROFF_S="${END_POWEROFF_S:-20}"
END_HOLD_S="${END_HOLD_S:-300}"

end_tally() {
    # $@ = target disks. _et_ok = how many ended "done", _et_n = how many.
    _et_ok=0; _et_n=0
    for _et_d in "$@"; do
        _et_n=$((_et_n + 1))
        [ "$(cat "$RUN_DIR/targets/$_et_d/state" 2>/dev/null)" != done ] || _et_ok=$((_et_ok + 1))
    done
}

end_record() {
    # $1 = kind, $2/$3 = done/targets. The count's record, its epoch from _ec_lim.
    printf '%s|%s|%s|%s|%s\n' "$(( $(date +%s) + _ec_lim ))" "$1" "$2" "$3" "$_ec_held" \
        > "$RUN_DIR/end.poweroff"
}

end_countdown() {
    # $1 = kind (drawers|partial|localclone|failed), $2 = what ended (journal),
    # $3/$4 = targets written / targets ("" = not said). Returns 1 when the
    # machine stayed on (the screen says why), 2 when END_ABORT_FILE came.
    # No kiosk: it runs all the same.
    rm -f "$RUN_DIR/end.now" "$RUN_DIR/end.hold"
    _ec_lim=$END_POWEROFF_S; _ec_held=0
    end_record "$1" "${3:-}" "${4:-}"
    log "$2 -- powering off in ${END_POWEROFF_S}s, or at once on 'כבה עכשיו'"
    _ec_n=0   # counted like idle_check's warning; the screen counts to the epoch
    while [ "$_ec_n" -lt "$_ec_lim" ]; do
        if [ -f "$RUN_DIR/end.now" ]; then log "$2 -- 'כבה עכשיו' pressed"; break; fi
        # a request of the screen's own (END_ABORT_FILE, lcwait.sh: the local clone's
        # end.req) is the person's: the count gives way to it, its caller acts on it
        if [ -n "${END_ABORT_FILE:-}" ] && [ -s "$END_ABORT_FILE" ]; then
            log "$2 -- the screen's own request came: the count stops"
            rm -f "$RUN_DIR/end.poweroff" "$RUN_DIR/end.hold"; return 2
        fi
        if [ "$_ec_held" = 0 ] && [ -f "$RUN_DIR/end.hold" ]; then
            _ec_held=1; _ec_lim=$END_HOLD_S; _ec_n=0; end_record "$1" "${3:-}" "${4:-}"
            log "$2 -- the count was stopped to read the results: power-off in ${END_HOLD_S}s, or on 'כבה עכשיו'"
        fi
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        sleep 1; _ec_n=$((_ec_n + 1))
    done
    rm -f "$RUN_DIR/end.poweroff" "$RUN_DIR/end.now" "$RUN_DIR/end.hold"
    finish_and_stop && return 0
    # safe_poweroff refused (#1483): the countdown is gone and the reason stays
    # on every screen (power.fail, gui_hello_state) -- a WoL refusal too.
    [ "${POWEROFF_WHY:-}" != wol ] ||
        echo "המחשב לא כובה: Wake-on-LAN לא נדרך בכרטיס הרשת, ובלעדיו אי אפשר להדליק אותו מרחוק. כבו אותו בכפתור ההפעלה." > "$RUN_DIR/power.fail"
    log "$2 -- the machine stayed on (${POWEROFF_WHY:-unknown})"
    return 1
}

end_failed_round() {
    # $1 = the line, $2 = session id, $3 = the reporter's pid. A cloner round
    # (a room round, "restore from the server" (lcpull.sh), a direct send it
    # received) where no drawer completed. Metal 17/10: cloner 2 stayed on after such a failure; its
    # hold beat does not join a wave and WoL wakes nothing that is on, so the
    # next round went on without it. Now the failure is reported -- the
    # console keeps it -- shown for the count, and the machine powers off.
    # Metal 17/10 (room_72308383, link down mid-stream): an unconfirmed report
    # was hold_unheard, which never returns -- lit after the server answered
    # again. Nadav: a cloner powers off in every state. The report goes on
    # until then; a wave that wrote nothing fails the round on the server.
    if ! report_final "$3" "$2" "$MAC" "$SERVER"; then
        log "the failure report was not confirmed -- the count runs all the same, reporting until the power-off"
        progress_loop "$2" "$MAC" "$SERVER" &
    fi
    # shellcheck disable=SC2086 # the disk list is a word list on purpose
    end_tally $_disks
    end_countdown failed "$1" "$_et_ok" "$_et_n" || ui_error_hold "$1" hold_beat
    return 1
}
