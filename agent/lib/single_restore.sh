# single_restore.sh -- the erase+write of ONE library image onto the internal
# disk, shared by the text recovery flow (single_station_flow) and the graphical
# restore screen (#706). POSIX sh.
#
# The CALLER validates the ERASE confirmation and lets the operator choose the
# image; this helper re-validates the choice against FRESH allowed_images, then
# writes. It never reads stdin (the GUI path is headless) and never reboots (the
# caller decides), so the same write runs identically from both entry points.
single_restore_run() {
    # $1 = image id, already chosen and ERASE-confirmed by the caller. The GUI
    # caller re-validates the (possibly stale) choice against fresh
    # allowed_images before calling this; the text picker chose it live. A
    # deleted image is caught here by the manifest fetch (principle 5).
    [ -n "$1" ] || die_local "restore: no image id"
    http_get "$SERVER/api/v1/images/$1/manifest" > "$RUN_DIR/sr-manifest.json" \
        || die_local "restore: manifest fetch failed"
    _sr_disk=$(pick_internal_disk) || die_local "no internal disk found"
    _sr_total=$(json_get "$RUN_DIR/sr-manifest.json" ".total_compressed_bytes")
    target_init "$_sr_disk" "$_sr_total"

    # The server is told before the first byte moves (a pull nobody can watch is
    # useless, #63); a failed open is non-fatal -- the disk is erased either way
    # (rule 1), and pull_open has already said so in the journal.
    _sr_ppid=""
    if pull_open "$SERVER" "$MAC" "$1" "${RECOVERY_USER:-}" "${RECOVERY_PASS:-}"; then
        progress_loop "$PULL_SESSION" "$MAC" "$SERVER" &
        _sr_ppid=$!
    fi
    if run_restore "unicast" "$_sr_disk" "$SERVER" "$1" "$RUN_DIR/sr-manifest.json"; then
        # #720: driver staging before the closing report -- its outcome rides
        # in that report. Never fatal; a Linux image is skipped inside.
        stage_drivers "$_sr_disk" "$RUN_DIR/sr-manifest.json"
        pull_close "$_sr_ppid" "$PULL_SESSION" "$MAC" "$SERVER"
        return 0
    fi
    # A failed restore stays visible until a person clears it (#133/#108); the
    # reporter keeps running when pull_open succeeded so the console sees it.
    ui_error_hold "restore did not complete" hold_beat
    return 1
}
