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
    # fetch (principle 5). 0 = written, 1 = failed (held), 3 = cancelled on
    # the GUI and the disk left empty (#1561).
    [ -n "$1" ] || sr_refused no-image "restore: no image id" || die_local "restore: no image id"
    http_get "$SERVER/api/v1/images/$1/manifest" > "$RUN_DIR/sr-manifest.json" \
        || sr_refused manifest "restore: manifest fetch failed" || die_local "restore: manifest fetch failed"
    _sr_disk=$(pick_internal_disk) || sr_refused no-disk "no internal disk found" || die_local "no internal disk found"
    # #1732: a red disk (SMART fail) is never a target -- refused before a byte, whatever the GUI sent
    # #1742: a read-only disk is the same red, refused with its own reason
    if smart_red "$_sr_disk"; then   # the probe first: the reason is read from its cache
        _sr_why="failed SMART"; _sr_key=smart-red
        [ "$(smart_field "$_sr_disk" 2)" != "readonly" ] || { _sr_why="is read-only"; _sr_key="readonly"; }
        sr_refused "$_sr_key" "restore: $_sr_disk $_sr_why -- never written (#1732)" || die_local "restore: $_sr_disk $_sr_why"
    fi
    _sr_total=$(json_get "$RUN_DIR/sr-manifest.json" ".total_compressed_bytes")
    target_init "$_sr_disk" "$_sr_total"
    # The progress on the GUI the handoff brought up (srgui.sh), before the
    # pull opens: the image, the disk, then the partitions as they go.
    sr_gui_start "$RUN_DIR/sr-manifest.json" "$_sr_disk" || log "restore: no GUI for the progress -- the console lines only"

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
    # #1561 (Nadav 17/10): "ביטול השחזור" on the GUI stops the stream (srgui.sh);
    # a cancel, whatever the write's own end, leaves an empty disk -- 3.
    sr_cancel_watch "$1" "$_sr_disk" & _sr_cw=$!
    run_restore "unicast" "$_sr_disk" "$SERVER" "$1" "$RUN_DIR/sr-manifest.json"; _sr_rc=$?
    kill "$_sr_cw" 2>/dev/null; wait "$_sr_cw" 2>/dev/null
    if sr_cancel_requested; then
        sr_cancelled "$_sr_disk" "$_sr_ppid" && return 3
        ui_error_hold "restore cancelled, but the partition table was not wiped" hold_beat
        return 1
    fi
    if [ "$_sr_rc" -eq 0 ]; then
        # #720: driver staging before the closing report -- its outcome rides
        # in that report. Never fatal; a Linux image is skipped inside.
        stage_drivers "$_sr_disk" "$RUN_DIR/sr-manifest.json"
        pull_close "$_sr_ppid" "$PULL_SESSION" "$MAC" "$SERVER"
        sr_gui_done
        return 0
    fi
    # A failed restore stays visible until a person clears it (#133/#108); the
    # reporter keeps running when pull_open succeeded so the console sees it.
    single_restore_failed_screen "$_sr_disk" || log "restore failed -- no GUI for the failure screen, the console line only"
    ui_error_hold "restore did not complete" hold_beat
    return 1
}

single_restore_failed_screen() {
    # $1 = disk. #1545 (metal 17/10): the bridge released the display for the
    # write (guiparent.sh), so a failure was English on the bare tty -- no
    # Hebrew font there (ui.sh) -- and never said why. The GUI comes back as
    # a display-only message screen with the target's own reason -- on the
    # progress GUI itself when it is still up (srgui.sh sr_gui_message); its
    # clicks go nowhere, and the tty hold under it keeps the heartbeat.
    _sw=$(head -n 1 "$RUN_DIR/targets/$1/error" 2>/dev/null)
    sr_gui_message "השחזור נכשל — השאירו את המחשב דלוק ופנו ל-IT" "$_sw" || return 1
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
