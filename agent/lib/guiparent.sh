# guiparent.sh -- GUI lifecycle in the existing agent poll loop. POSIX sh.
gui_parent() {
    [ "${_gui_disabled:-0}" = 0 ] || return 1
    if [ -n "${_gui_pid:-}" ]; then
        if [ -f "$RUN_DIR/gui/handoff" ]; then
            IFS= read -r _handoff < "$RUN_DIR/gui/handoff" || return 1
            case "$_handoff" in restore-start|class-pick) ;; *) return 1 ;; esac
            # The bridge stops the display and releases input before exiting.
            wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI handoff" || true
            _gui_pid=; _gui_disabled=1
            case "$_handoff" in
                restore-start)
                    # #706: the graphical picker already chose+confirmed ERASE.
                    # recovery_gate handles the login-or-waive decision exactly
                    # as the text flow did; then the shared write runs, and a
                    # success reboots (a failure is held visibly inside it).
                    _gui_img=$(sed -n '2p' "$RUN_DIR/gui/handoff")
                    case "$_gui_img" in ''|*[!A-Za-z0-9_.-]*) login_failed; return 0 ;; esac
                    recovery_gate
                    # A GUI state is up to 2 s stale: re-validate the choice
                    # against fresh allowed_images before erasing (principle 5/7).
                    http_get "$SERVER/api/v1/agent/state?mac=$MAC" \
                        > "$RUN_DIR/gui/restore-state.json" 2>/dev/null &&
                        jq -e --arg id "$_gui_img" '.allowed_images[]? | select(.id == $id)' \
                            "$RUN_DIR/gui/restore-state.json" >/dev/null \
                        || die_local "restore: image is no longer allowed for this machine"
                    if single_restore_run "$_gui_img"; then
                        [ "${IMAGECTL_TEST:-0}" = "1" ] && return 0
                        sync; reboot -f
                    fi
                    ;;
                class-pick)
                    _gui_group=$(sed -n '2p' "$RUN_DIR/gui/handoff")
                    recovery_login || { login_failed; return 0; }
                    class_round_flow "$_gui_group"
                    ;;
            esac
            _build_standby=1
            return 0
        fi
        if kill -0 "$_gui_pid" 2>/dev/null; then return 0; fi
        wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI exit" || true
        log 'native GUI exited -- returning to the text menu'
        _gui_disabled=1; _gui_pid=
        return 1
    fi
    [ -x /usr/bin/imagectl-kiosk ] && [ -x /usr/bin/imagectl-station-gui ] || return 1
    # --help exercises the dynamic loader without taking the display.
    if ! /usr/bin/imagectl-station-gui --help >> "$LOG_FILE" 2>&1; then
        log 'native GUI libraries unavailable -- using text menu'
        _gui_disabled=1
        return 1
    fi
    gui_fetch_logo
    GUI_SCREEN=
    [ "$D_ROLE" = classroom ] && GUI_SCREEN=class
    [ "$D_ROLE" = cloner ] && GUI_SCREEN=cloner
    export SERVER MAC IP RUN_DIR LIB_DIR GUI_SCREEN
    /usr/bin/imagectl-kiosk >> "$LOG_FILE" 2>&1 &
    _gui_pid=$!
    return 0
}
