# directflow.sh -- "deploy this disk directly to the cloning machines" on
# the build machine's text console (#715). POSIX sh (busybox ash).
#
# The same round as roomflow.sh, with the source swapped: instead of an
# image from the library, THIS machine's disk, read-only. No image picker.
# The targets are an explicit choice -- the server refuses a direct round
# without target_slots (there is no "every drawer in the room") -- so the
# operator picks machines here, and every fresh drawer with a known slot
# in each chosen machine is written. Once the round is open the screen is
# roomflow's: wake, send now, watch.
#
# Text only, ASCII only, like the rest of the build menu.

direct_pick_targets() {
    # Prints the target_slots JSON on stdout; the screen goes to stderr.
    # Reads $RUN_DIR/room.json (room_status_get). Only machines that are
    # awake and have at least one fresh drawer with a slot number are
    # offered: a drawer without a port cannot be selected (#695/#701).
    #
    # No 2>/dev/null on jq: a list we failed to read is not an empty room.
    if ! jq -r '.machines | to_entries[] | .value as $m | select($m.awake)
            | ([$m.drawer_list[]? | select(.fresh and (.port != null)) | .port]
               | sort) as $ports
            | select(($ports | length) > 0)
            | "\($m.mac)|\($m.name)|\($ports | map(tostring) | join(","))"' \
            "$RUN_DIR/room.json" > "$RUN_DIR/direct_targets.txt" 2>> "$LOG_FILE"; then
        echo "  The machine list could not be read." >&2
        sleep 5
        return 1
    fi
    _n=$(awk 'END { print NR }' "$RUN_DIR/direct_targets.txt")
    if [ "$_n" -lt 1 ]; then
        echo "  No awake cloning machine has a fresh drawer with a slot." >&2
        echo "  Wake the room first ([1] on the room screen), then try again." >&2
        sleep 5
        return 1
    fi
    ui_clear >&2; ui_header >&2
    echo "  Cloning machines that can be written now:" >&2
    echo >&2
    _i=0
    while IFS='|' read -r _mac _nm _ports; do
        _i=$((_i + 1))
        printf '    %s) %s  drawers %s\n' "$_i" "$_nm" "$_ports" >&2
    done < "$RUN_DIR/direct_targets.txt"
    echo >&2
    printf "  Machines to write, e.g. 1,3 or 'all' (0 = back): " >&2
    read -r _c
    case "$_c" in
        0|"") return 1 ;;
        all) _c=$(awk 'BEGIN { ORS="," } { print NR }' "$RUN_DIR/direct_targets.txt") ;;
        *[!0-9,]*) echo "  Numbers separated by commas." >&2; sleep 3; return 1 ;;
    esac
    _json=""
    _picked=" "
    _old_ifs=$IFS; IFS=','
    for _k in $_c; do
        [ -n "$_k" ] || continue
        { [ "$_k" -ge 1 ] && [ "$_k" -le "$_n" ]; } 2>/dev/null \
            || { IFS=$_old_ifs; echo "  $_k is not on the list." >&2; sleep 3; return 1; }
        case "$_picked" in *" $_k "*) continue ;; esac
        _picked="$_picked$_k "
        _row=$(sed -n "${_k}p" "$RUN_DIR/direct_targets.txt")
        _mac=${_row%%|*}; _ports=${_row##*|}
        _json="$_json{\"mac\":\"$_mac\",\"ports\":[$_ports]},"
    done
    IFS=$_old_ifs
    [ -n "$_json" ] || return 1
    echo "[${_json%,}]"
}

direct_open() {
    _disk=$(pick_internal_disk) || {
        echo "  No internal disk to send from."
        sleep 5
        return 1
    }
    _slots=$(direct_pick_targets) || return 1
    ui_clear; ui_header
    echo "  About to deploy directly from this disk:"
    echo
    echo "    Source:   /dev/$_disk  (read only -- nothing is written to it)"
    echo "    Targets:  $_slots"
    echo
    echo "  The server does NOT keep a copy. Only the drawers above are written."
    printf "  Open the round and wake the chosen machines? [y/N]: "
    read -r _yes
    case "$_yes" in y|Y|yes|YES) ;; *) return 1 ;; esac
    printf '{"source":{"kind":"build_disk","mac":"%s","disk":"%s"},"target_slots":%s}' \
        "$MAC" "$_disk" "$_slots" > "$RUN_DIR/room_open.json"
    _code=$(console_post "room" "$RUN_DIR/room_open.json" \
        "$RUN_DIR/room_open_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The direct round was not opened"
        return 1
    fi
    log "direct round opened from the build machine: /dev/$_disk -> $_slots"
    echo
    echo "  Opened. This machine reads the disk once now, then streams it"
    echo "  when the room is ready. Stand by -- the work starts by itself."
    sleep 6
    return 0
}

direct_flow() {
    console_signin || return 1
    room_status_get || return 1
    if [ "$(json_get "$RUN_DIR/room.json" ".round")" != "null" ]; then
        # A round is already open (from here, the GUI or the console) --
        # nothing to choose; the room screen shows it, and then the menu.
        room_flow
        return 1
    fi
    direct_open || return 1
    # The task arrives with the next hello; standing by is what lets it.
    build_standby "Direct deployment ordered. It starts in a moment."
    return 0
}
