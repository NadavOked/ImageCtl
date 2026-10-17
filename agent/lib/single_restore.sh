# single_restore.sh -- the erase+write of ONE library image onto the internal
# disk, behind the graphical restore screen (#706). POSIX sh.
#
# The GUI chooses the image and the bridge checks the typed machine name; the
# caller (guiparent.sh) re-validates the choice against FRESH allowed_images,
# then this writes. It never reads stdin and never reboots (the caller
# decides). The text recovery wizard that shared it was removed (owner, 11/10:
# GUI only).

single_restore_run() {
    # $1 = image id, already chosen and confirmed on the GUI. The caller
    # re-validates the (possibly stale) choice against fresh allowed_images
    # before calling this. A deleted image is caught here by the manifest
    # fetch (principle 5).
    [ -n "$1" ] || die_local "restore: no image id"
    http_get "$SERVER/api/v1/images/$1/manifest" > "$RUN_DIR/sr-manifest.json" \
        || die_local "restore: manifest fetch failed"
    _sr_disk=$(pick_internal_disk) || die_local "no internal disk found"
    _sr_total=$(json_get "$RUN_DIR/sr-manifest.json" ".total_compressed_bytes")
    target_init "$_sr_disk" "$_sr_total"

    # The server is told before the first byte moves (a pull nobody can watch is
    # useless, #63); a failed open is non-fatal -- the disk is erased either way
    # (rule 1), and pull_open has already said so in the journal.
    #
    # No password: the operator signed in on the GUI, whose password never
    # leaves the kiosk's sign-in process. Instead the pull carries that
    # console session ($PULL_JAR, the cookie jar the bridge handed over), and
    # the server accepts it as the authorization (11/10) -- the restore is
    # linked to the console user and followed there. The jar is single-use.
    _sr_ppid=""
    if pull_open "$SERVER" "$MAC" "$1" "" ""; then
        progress_loop "$PULL_SESSION" "$MAC" "$SERVER" &
        _sr_ppid=$!
    fi
    [ -z "${PULL_JAR:-}" ] || rm -f "$PULL_JAR"
    if run_restore "unicast" "$_sr_disk" "$SERVER" "$1" "$RUN_DIR/sr-manifest.json"; then
        # #720: driver staging before the closing report -- its outcome rides
        # in that report. Never fatal; a Linux image is skipped inside.
        stage_drivers "$_sr_disk" "$RUN_DIR/sr-manifest.json"
        pull_close "$_sr_ppid" "$PULL_SESSION" "$MAC" "$SERVER"
        return 0
    fi
    # A failed restore stays visible until a person clears it (#133/#108); the
    # reporter keeps running when pull_open succeeded so the console sees it.
    single_restore_failed_screen "$_sr_disk" || log "restore failed -- no GUI for the failure screen, the console line only"
    ui_error_hold "restore did not complete" hold_beat
    return 1
}

SR_GUI_BIN="${SR_GUI_BIN:-/usr/bin/imagectl-station-gui}"

single_restore_failed_screen() {
    # $1 = disk. #1545 (metal 17/10): the bridge released the display for the
    # write (guiparent.sh), so a failure was English on the bare tty -- no
    # Hebrew font there (ui.sh) -- and never said why. The GUI comes back as
    # a display-only message screen with the target's own reason; its clicks
    # go nowhere (stdout is /dev/null), and the tty hold under it keeps the
    # heartbeat. The GUI calls a state older than 10 s stale, so the file is
    # touched for as long as that GUI process lives -- and no longer.
    [ -x "$SR_GUI_BIN" ] || return 1
    _sf="$RUN_DIR/gui/failed.state"
    mkdir -p "$RUN_DIR/gui" || return 1
    _sw=$(head -n 1 "$RUN_DIR/targets/$1/error" 2>/dev/null | tr -d '\r|')
    # msg_sub is 200 bytes in the GUI; a longer reason would be cut mid-letter.
    if [ -z "$_sw" ] || [ "$(printf '%s' "$_sw" | wc -c)" -gt 190 ]; then _sw="הסיבה המלאה ביומן הסוכן ובקונסולה"; fi
    printf 'message=השחזור נכשל — השאירו את המחשב דלוק ופנו ל-IT|%s\n' "$_sw" > "$_sf" || return 1
    # shellcheck disable=SC2046 # --backend/--fb-file are words (guibridge.sh)
    FONTCONFIG_FILE=/etc/imagectl/fonts.conf "$SR_GUI_BIN" $(gui_backend_args) \
        --mac "$MAC" --ip "${IP:-}" --state "$_sf" > /dev/null 2>> "$LOG_FILE" &
    _sf_pid=$!
    ( while kill -0 "$_sf_pid" 2>/dev/null; do touch "$_sf"; sleep 2; done ) &
    log "restore failed -- shown on the GUI: $_sw"
}

pick_internal_disk() {
    # First non-removable real disk. The classroom machines have exactly one.
    # Lived in recovery.sh until the text wizard was removed (11/10).
    for _n in $(list_disks); do
        _rm=$(cat "$SYSROOT/sys/block/$_n/removable" 2>/dev/null || echo 1)
        [ "$_rm" = "0" ] && { echo "$_n"; return 0; }
    done
    return 1
}
