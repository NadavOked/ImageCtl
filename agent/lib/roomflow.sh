# roomflow.sh -- driving the cloning room from the build machine (#135).
# POSIX sh (busybox ash). No bashisms.
#
# Spec 29, in the order it is written there: pick an image and how many
# drives the round has to produce, wake the room, watch how many machines
# are up and how many fresh drawers are in them, then send.
#
# The round lives on the server (server/room.py) -- this screen only opens
# it and reads it back. Walking away is allowed: the wave finishes, the
# machines power off for the drawer swap, and the next wave opens by itself.
#
# Worth knowing while reading this: /api/console/room and its /start and
# /wake are `current_user` only. Any signed-in account -- admin or deploy --
# may open a room round, unlike the class round, whose opener role IS
# checked on the server (station.py:ROUND_OPENER_ROLES).
#
# room_draw() and everything it prints live in roomdraw.sh (#418) -- split
# out to stay under the 300-line ceiling (tests/sizelimit.py). Sourced
# alongside this file wherever it is loaded.

room_status_get() {
    _code=$(console_get "room" "$RUN_DIR/room.json")
    [ "$_code" = "200" ] && return 0
    console_say "$_code" "Could not read the cloning room"
    return 1
}

room_pick_image() {
    # Prints the chosen image id; the screen goes to stderr. The whole
    # library, not `allowed_images`: that list is filtered by THIS machine's
    # disk, and the drives being written are in the cloning machines.
    _code=$(console_get "images" "$RUN_DIR/room_images.json")
    if [ "$_code" != "200" ]; then
        console_say "$_code" "Could not read the image library" >&2
        return 1
    fi
    _count=$(jq 'length' "$RUN_DIR/room_images.json" 2>/dev/null)
    case "$_count" in
        ""|*[!0-9]*) echo "  Unreadable image list." >&2; sleep 5; return 1 ;;
        0) echo "  The library is empty." >&2; sleep 5; return 1 ;;
    esac

    rm -f "$RUN_DIR/room_image_ids.txt"
    ui_clear >&2; ui_header >&2
    echo "  Image to write to the drawers:" >&2
    echo >&2
    jq -r '.[] | "\(.id)|\(.name)|\(.family)"' "$RUN_DIR/room_images.json" \
    | { _i=0
        while IFS='|' read -r _id _nm _fam; do
            _i=$((_i + 1))
            printf '    %s) %s  [%s GB family]\n' "$_i" "$_nm" "$_fam" >&2
            echo "$_id" >> "$RUN_DIR/room_image_ids.txt"
        done; }
    echo >&2
    printf "  Choose [1-%s], or 0 to go back: " "$_count" >&2
    read -r _c
    case "$_c" in 0|""|*[!0-9]*) return 1 ;; esac
    { [ "$_c" -ge 1 ] && [ "$_c" -le "$_count" ]; } || return 1
    sed -n "${_c}p" "$RUN_DIR/room_image_ids.txt"
}

room_open() {
    _img=$(room_pick_image) || return 1
    printf "  How many drives should this round produce? "
    read -r _target
    case "$_target" in
        ""|*[!0-9]*)
            echo "  That is not a number of drives."
            sleep 4
            return 1
            ;;
    esac
    [ "$_target" -ge 1 ] || { echo "  At least one drive."; sleep 4; return 1; }

    printf '{"image_id":"%s","target_drives":%s}' "$_img" "$_target" \
        > "$RUN_DIR/room_open.json"
    _code=$(console_post "room" "$RUN_DIR/room_open.json" \
        "$RUN_DIR/room_open_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The round was not opened"
        return 1
    fi
    log "cloning room round opened from the build machine: $_img x$_target"
    return 0
}

room_action() {
    # $1 = "wake" or "start". The server answers 200 or says why not, and
    # "no answer" is not "it worked".
    echo '{}' > "$RUN_DIR/room_empty.json"
    _code=$(console_post "room/$1" "$RUN_DIR/room_empty.json" \
        "$RUN_DIR/room_action.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The room did not accept '$1'"
        return 1
    fi
    log "cloning room: $1 accepted"
    [ "$1" = "wake" ] || return 0
    # The counts are the point: "0 machines" with no reason sends a
    # technician to check WoL in twelve BIOSes, when the fault is one
    # cable in the server (#74).
    _sent=$(json_get "$RUN_DIR/room_action.json" ".sent")
    _failed=$(json_get "$RUN_DIR/room_action.json" ".failed")
    echo "  Wake-on-LAN sent: $_sent machines, $_failed failed."
    sleep 4
}

room_flow() {
    console_signin || return 1
    while :; do
        room_status_get || return 1
        if [ "$(json_get "$RUN_DIR/room.json" ".round")" = "null" ]; then
            ui_clear; ui_header
            echo "  No cloning-room round is open."
            echo
            room_open || return 0
            continue
        fi
        room_draw
        printf "  [1] wake the room  [2] send now  [Enter] refresh  [0] back: "
        # A closed console reads EOF for ever. Without this the refresh loop
        # would spin against the server with nobody watching.
        read -r _c || return 0
        case "$_c" in
            0) return 0 ;;
            1) room_action wake ;;
            2) room_action start ;;
        esac
    done
}
