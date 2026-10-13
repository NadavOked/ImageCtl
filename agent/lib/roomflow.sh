# roomflow.sh -- the cloning-room round the build machine's GUI opens and
# drives (#135, spec 29). POSIX sh (busybox ash). No bashisms.
#
# The round lives on the server (server/room.py); the GUI room screen reads
# it (guistate.sh) and its buttons land here through guibridge.sh: open the
# round, wake the room, send now. Walking away is allowed: the wave finishes,
# the machines power off for the drawer swap, and the next wave opens by
# itself.
#
# Worth knowing while reading this: /api/console/room and its /start and
# /wake are `current_user` only. Any signed-in account -- admin or deploy --
# may open a room round.
#
# The text room screen (its image picker, its refresh loop and its
# renderer) was removed with the rest of the text screens (owner, 11/10:
# GUI only).

room_open() {
    # $1 = image id, $2 = how many drives the round has to produce -- both
    # from the GUI record (guibridge.sh checks their syntax, and so does
    # this: the server is the last word, not the first).
    _img=$1; _target=$2
    case "$_img" in ''|*[!A-Za-z0-9_.-]*) log "room round: bad image id"; return 1 ;; esac
    case "$_target" in
        ""|*[!0-9]*) log "room round: not a number of drives: $_target"; return 1 ;;
    esac
    [ "$_target" -ge 1 ] || { log "room round: at least one drive"; return 1; }

    # ROOM_OPEN_EXTRA: the GUI's ticked drawers (#1275) and drivers (#1269),
    # from room_open_extra.
    printf '{"image_id":"%s","target_drives":%s%s}' "$_img" "$_target" \
        "${ROOM_OPEN_EXTRA:-}" > "$RUN_DIR/room_open.json"
    _code=$(console_post "room" "$RUN_DIR/room_open.json" \
        "$RUN_DIR/room_open_resp.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The round was not opened"
        return 1
    fi
    log "cloning room round opened from the build machine: $_img x$_target"
    return 0
}

# The GUI's `slots=<mac>@<port>,<port>/<mac>@<port>` record value (a MAC has
# colons of its own) -> the target_slots JSON array. Exit 1 on anything
# malformed, and nothing is posted: a selection that was not read is not
# "no selection" -- that one writes every drawer (#1275, principle 4/5).
# Was inline in guibridge.sh (#715); the library round uses it too.
room_slots_json() {
    case "$1" in ''|*[!0-9a-f:/,@]*) return 1 ;; esac
    printf '%s' "$1" | awk -v RS='/' -F@ '{
        gsub(/\n/, "", $2)
        if (NF != 2 || $1 == "" || $2 !~ /^[0-9]+(,[0-9]+)*$/) exit 1
        printf "%s{\"mac\":\"%s\",\"ports\":[%s]}", (n++ ? "," : "["), $1, $2 }
        END { if (!n) exit 1; printf "]" }'
}

# #1269: the GUI's `drivers=<mac>|<port>|<vendor>|<model>|...` -- four fields
# per disk that has a model; a disk with "no drivers" is simply not there.
# The state file never carries `|` inside a value (guistate.sh replaces it),
# so the fields cannot run into each other. -> the driver_models JSON array;
# exit 1 on anything malformed. The server checks the model against the
# library again and keeps its own spelling.
room_drivers_json() {
    jq -cn --arg d "$1" '$d | split("|") |
        if length == 0 or length % 4 != 0 then error("bad drivers") else
        [range(0; length; 4) as $i | .[$i:$i + 4] |
         if (.[0] | test("^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")) and (.[1] | test("^[1-9][0-9]?$"))
            and .[2] != "" and .[3] != ""
         then {mac: .[0], port: (.[1] | tonumber), vendor: .[2], model: .[3]}
         else error("bad drivers") end] end'
}

# The extra fields of a library round opened from the GUI. #1275: the ticked
# drawers go as target_slots whenever there is a selection, drivers or not --
# none ticked = empty, the round exactly as before (every fresh drawer, #695).
# #1269: driver_models only with drivers, and only on a selection: the server
# binds a model only to a disk chosen as a target (there is no "every disk").
room_open_extra() {
    _rx=''
    if [ -n "$1" ]; then
        _rs=$(room_slots_json "$1") || return 1
        _rx=",\"target_slots\":$_rs"
    fi
    if [ -n "$2" ]; then
        [ -n "$1" ] || return 1
        _rd=$(room_drivers_json "$2") || return 1
        _rx="$_rx,\"driver_models\":$_rd"
    fi
    printf '%s' "$_rx"
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
    log "Wake-on-LAN sent: $(json_get "$RUN_DIR/room_action.json" ".sent") machines, $(json_get "$RUN_DIR/room_action.json" ".failed") failed"
}

# #65 "power it off": one cloner the open wave shows as missing, so its drawers
# can be swapped. $1 = its MAC, $2 = its name (the GUI's confirm window,
# #1270). The server checks all of it again -- still missing, the wave still
# open, the name the machine's -- and the agent powers off on its next hello,
# after arm_wol (#587). "Requested" is not "powered off": the evidence is
# the hello that stops.
room_poweroff() {
    case "$1" in ''|*[!0-9a-f:]*) return 1 ;; esac
    jq -n --arg c "$2" '{confirm_name:$c}' > "$RUN_DIR/room_poweroff.json" || return 1
    _code=$(console_post "room/missing/$1/poweroff" "$RUN_DIR/room_poweroff.json" \
        "$RUN_DIR/room_action.json") || return 1
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The machine was not asked to power off"
        return 1
    fi
    log "cloning room: power-off requested for the missing cloner $1"
}
