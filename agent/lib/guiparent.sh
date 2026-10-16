# guiparent.sh -- GUI lifecycle in the existing agent poll loop. POSIX sh.
#
# GUI only (owner, 11/10): there is no text menu or prompt behind the native
# GUI any more. A screen that needs a person and gets no GUI -- not installed,
# libraries missing, the process gone -- ends in gui_unavailable_local below.

#: How long the "GUI did not start" line stays on the console before the
#: reboot, so a person standing there can read it.
GUI_FAIL_SHOW_S="${GUI_FAIL_SHOW_S:-10}"

gui_unavailable_local() {
    # $1 = which screen needed the GUI (for the journal). ASCII on purpose:
    # the Linux console has no Hebrew font and no RTL (ui.sh), so the Hebrew
    # line would reach the person as squares.
    log "native GUI unavailable for the ${1:-operator} screen -- no text fallback, booting from the local disk"
    # 11/10: the next boot goes to the local disk (BootNext), not back here
    # -- and the console hears about it (localboot.sh).
    _gu_bn=$(boot_next_local); _gu_state=${_gu_bn%%:*}; _gu_why=${_gu_bn#*:}
    log "BootNext to the local disk: $_gu_state (the Hebrew detail is in the console journal)"   # log() also prints to the ASCII tty
    gui_failed_report "${1:-operator}" "$_gu_state" "$_gu_why" || :
    ui_clear; ui_header
    echo "  The graphical interface did not start."
    if [ "$_gu_state" = set ]; then
        echo "  Booting from the local disk."
    else
        echo "  Rebooting. The next boot could NOT be pointed at the local disk:"
        echo "  (details: see the server console journal)"
    fi
    [ "${IMAGECTL_TEST:-0}" = "1" ] || sleep "$GUI_FAIL_SHOW_S"
    die_local "native GUI unavailable (${1:-operator} screen)"
}

gui_restore_authorized() {
    # The authorization recovery_gate gave the restore before 11/10, kept
    # when the text sign-in went (security review): an image name in a
    # handoff file is NOT permission to erase this disk. Fail-closed, on
    # positive evidence only:
    #   1. `.ui.require_login` must be a known answer -- anything but
    #      true/false is "the server did not say", and refuses (principle 5).
    #      Both known answers then need a session: the GUI always signs in.
    #   2. the console session of THIS GUI login -- the bridge hands its
    #      cookie jar over with the handoff ($RUN_DIR/gui/handoff.jar) --
    #      must still be live: the server answers 200 on a session-only
    #      console route. Missing jar, 401, no answer: refused.
    # The jar is single-use: a refusal deletes it here; an accepted one is
    # kept for the pull, which carries it to the server (11/10: the restore
    # is linked to this console session) and deletes it (single_restore_run).
    _gra_jar="$RUN_DIR/gui/handoff.jar"
    case "$(json_get "$RESP" ".ui.require_login")" in
        true|false) ;;
        *) rm -f "$_gra_jar"; log "restore: the server did not say whether a login is needed -- refused"; return 1 ;;
    esac
    [ -s "$_gra_jar" ] || { log "restore: no console session from the GUI -- refused"; return 1; }
    _gra_code=$(curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$_gra_jar" -o "$RUN_DIR/gui/restore-auth.json" -w '%{http_code}' \
        "$SERVER/api/console/folders" 2>> "$LOG_FILE")
    [ "$_gra_code" = 200 ] && return 0
    rm -f "$_gra_jar"
    log "restore: the GUI's console session is not live (http ${_gra_code:-000}) -- refused"
    return 1
}

gui_parent() {
    [ "${_gui_disabled:-0}" = 0 ] || return 1
    if [ -n "${_gui_pid:-}" ]; then
        if [ -f "$RUN_DIR/gui/handoff" ]; then
            IFS= read -r _handoff < "$RUN_DIR/gui/handoff" || return 1
            [ "$_handoff" = restore-start ] || return 1
            # The bridge stops the display and releases input before exiting.
            wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI handoff" || true
            _gui_pid=; _gui_disabled=1
            # #706: the graphical picker already chose the image and the
            # bridge checked the typed name against the server (#1073). There
            # is no second, text sign-in any more (owner, 11/10: GUI only);
            # the GUI's own console session is re-checked instead
            # (gui_restore_authorized). Without the password the pull is
            # registered only where the server waives the login; elsewhere
            # pull_open says so and the write goes on.
            _gui_img=$(sed -n '2p' "$RUN_DIR/gui/handoff")
            case "$_gui_img" in ''|*[!A-Za-z0-9_.-]*) rm -f "$RUN_DIR/gui/handoff.jar"; die_local "restore: malformed image id from the GUI" ;; esac
            gui_restore_authorized || die_local "restore: not authorized -- nothing was written"
            # A GUI state is up to 2 s stale: re-validate the choice
            # against fresh allowed_images before erasing (principle 5/7).
            http_get "$SERVER/api/v1/agent/state?mac=$MAC" \
                > "$RUN_DIR/gui/restore-state.json" 2>/dev/null &&
                jq -e --arg id "$_gui_img" '.allowed_images[]? | select(.id == $id)' \
                    "$RUN_DIR/gui/restore-state.json" >/dev/null \
                || { rm -f "$RUN_DIR/gui/handoff.jar"; die_local "restore: image is no longer allowed for this machine"; }
            PULL_JAR="$RUN_DIR/gui/handoff.jar"   # the pull goes under this console session
            if single_restore_run "$_gui_img"; then
                [ "${IMAGECTL_TEST:-0}" = "1" ] && return 0
                sync; reboot -f
            fi
            _build_standby=1
            return 0
        fi
        if kill -0 "$_gui_pid" 2>/dev/null; then return 0; fi
        wait_pid "$_gui_pid" "$WAIT_HELPER_S" "native GUI exit" || true
        log 'native GUI exited -- there is no text menu behind it'
        _gui_disabled=1; _gui_pid=
        return 1
    fi
    [ -x /usr/bin/imagectl-kiosk ] && [ -x /usr/bin/imagectl-station-gui ] || return 1
    # --help exercises the dynamic loader without taking the display.
    if ! /usr/bin/imagectl-station-gui --help >> "$LOG_FILE" 2>&1; then
        log 'native GUI libraries unavailable'
        _gui_disabled=1
        return 1
    fi
    GUI_SCREEN=
    [ "$D_ROLE" = classroom ] && GUI_SCREEN=class
    [ "$D_ROLE" = cloner ] && GUI_SCREEN=cloner
    export SERVER MAC IP RUN_DIR LIB_DIR GUI_SCREEN
    /usr/bin/imagectl-kiosk >> "$LOG_FILE" 2>&1 &
    _gui_pid=$!
    return 0
}
