# selfrestore.sh -- "restore an image from the server onto THIS disk" on the
# build machine's text menu (#1073, Nadav's ruling of 2026-09-18, item (c)).
# POSIX sh (busybox ash).
#
# The images offered are THIS server's library: /api/v1/agent/state lists
# allowed_images from the local library, already filtered to what fits the
# disk (#706). A secondary (Haifa) therefore offers only what is already in
# Haifa -- an image that sits in Tel Aviv and was not transferred is not on
# the list. The write itself is single_restore.sh, shared with the graphical
# restore screen; it erases the build machine's internal disk, so the
# confirmation is the machine's registered name typed back (principle 7).
# Not ERASE: that is the recovery wizard's word for a station, and a word
# that is the same on every machine is not evidence the operator knows
# which machine is about to be wiped.

self_restore_state() {
    # Fresh state from the server: the images this machine may take, and its
    # registered name. Sets SELF_NAME; writes $RUN_DIR/self_images.txt as
    # id|name|folder lines. A list we could not read is not an empty list.
    http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$RUN_DIR/self_state.json" || {
        echo "  The server did not answer. Nothing was changed."
        sleep 5
        return 1
    }
    SELF_NAME=$(jq -er '.name | select(type == "string" and length > 0)' \
        "$RUN_DIR/self_state.json" 2>> "$LOG_FILE") || {
        echo "  This computer has no name in the server's registry."
        echo "  Give it one in the console first -- the name is the confirmation."
        sleep 6
        return 1
    }
    jq -r 'if (.allowed_images | type) != "array" then error("bad state") else
        .allowed_images[] | "\(.id)|\(.name)|\(.folder // "")" end' \
        "$RUN_DIR/self_state.json" > "$RUN_DIR/self_images.txt" 2>> "$LOG_FILE" || {
        echo "  The image list could not be read. Nothing was changed."
        sleep 5
        return 1
    }
}

self_restore_flow() {
    self_restore_state || return 1
    _n=$(awk 'END { print NR }' "$RUN_DIR/self_images.txt")
    ui_clear; ui_header
    echo "  Restore an image from this server onto THIS computer's disk."
    echo
    if [ "$_n" -lt 1 ]; then
        echo "  No image in this server's library fits this computer's disk."
        sleep 5
        return 1
    fi
    _i=0
    while IFS='|' read -r _id _nm _fd; do
        _i=$((_i + 1))
        printf '    %s) %s%s\n' "$_i" "$_nm" "${_fd:+  [$_fd]}"
    done < "$RUN_DIR/self_images.txt"
    echo
    printf "  Choose an image [1-%s], or 0 to go back: " "$_n"
    read -r _c
    case "$_c" in 0|"") return 1 ;; *[!0-9]*) return 1 ;; esac
    { [ "$_c" -ge 1 ] && [ "$_c" -le "$_n" ]; } || return 1
    _img=$(sed -n "${_c}p" "$RUN_DIR/self_images.txt" | cut -d'|' -f1)
    _img_name=$(sed -n "${_c}p" "$RUN_DIR/self_images.txt" | cut -d'|' -f2)
    _disk=$(pick_internal_disk) || { echo "  No internal disk found."; sleep 5; return 1; }
    echo
    echo "  Image:  $_img_name"
    echo "  Target: /dev/$_disk -- ALL DATA ON THIS COMPUTER'S DISK WILL BE ERASED."
    printf "  Type this computer's name (%s) to continue: " "$SELF_NAME"
    read -r _typed
    if [ "$_typed" != "$SELF_NAME" ]; then
        log "self restore: the name did not match -- nothing written"
        echo "  The name did not match. Nothing was written."
        sleep 4
        return 1
    fi
    log "self restore: $_img onto /dev/$_disk, confirmed by name"
    echo
    echo "  Writing... (progress is visible on the console as well)"
    single_restore_run "$_img" || return 1
    echo
    echo "  Done. This computer restarts into the restored system."
    sleep 4
    [ "${IMAGECTL_TEST:-0}" = "1" ] && return 0
    sync; reboot -f
}
