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

room_draw() {
    _wave=$(json_get "$RUN_DIR/room.json" ".round.wave_number")
    _state=$(json_get "$RUN_DIR/room.json" ".round.wave_state")
    _image=$(json_get "$RUN_DIR/room.json" ".round.image_name")
    _written=$(json_get "$RUN_DIR/room.json" ".round.written_drives")
    _target=$(json_get "$RUN_DIR/room.json" ".round.target_drives")
    _left=$(json_get "$RUN_DIR/room.json" ".round.remaining_drives")
    _ready=$(json_get "$RUN_DIR/room.json" ".round.ready_drives")

    ui_clear; ui_header
    echo "  Cloning room -- wave $_wave ($_state)"
    echo "  Image:      $_image"
    echo "  Drives:     $_written of $_target written, $_left to go"
    echo "  Ready now:  $_ready fresh drawers in machines that joined"
    echo
    echo "  Machines:"
    # No 2>/dev/null: a machine list we failed to read is not an empty room,
    # and an empty room is exactly what somebody would act on (rule 5).
    if jq -r '.machines[] | [.name, (if .awake then "on" else "off" end),
            "\(.fresh_drawers)/\(.drawers)", (.state // "-")] | @tsv' \
            "$RUN_DIR/room.json" > "$RUN_DIR/room_rows.txt"; then
        while IFS="$(printf '\t')" read -r _nm _on _dr _st; do
            printf '    %-12s %-3s  %-6s fresh  %s\n' \
                "$_nm" "$_on" "$_dr" "$_st"
        done < "$RUN_DIR/room_rows.txt"
    else
        echo "    (the machine list could not be read -- see the journal)"
        log "cloning room: the machine list did not parse"
    fi
    echo
    room_draw_writing
    room_draw_failures
}

# #552: how many seconds a drawer's byte counter may stand still before
# the line says STALLED. The threshold is derived from measurement, not
# picked. 1,896 samples from the 08/09 round: a HEALTHY drawer stood
# still for up to 120s (median 5, p95 5), and the one that actually died
# stood still for 31 minutes.
#
# 30 seconds -- what this was going to be before the numbers were read --
# would have lit up on three of the four healthy drawers. 180 gives 1.5x
# the widest healthy pause seen, and ten times the margin below the real
# failure.
ROOM_STALL_S="${ROOM_STALL_S:-180}"

room_draw_writing() {
    # The lines the operator did not have. On 08/09 the screen said
    # "0 of 5 written" for 75 minutes: that counter counts drawers that
    # FINISHED, so it reads 0 both when everything is running well and
    # when everything is stuck. Those two must not look the same -- an
    # operator who cannot tell them apart stops a healthy round, and
    # stopping mid-write leaves a drawer half done.
    #
    # "(no stall data)" is not decoration. A server without the field, or
    # a timestamp that did not parse, comes back null -- and a blank there
    # would tell the operator "measured, not stalled". That is exactly the
    # collapse rule 5 forbids.
    jq -r --argjson stall "$ROOM_STALL_S" '
        def gb: if . == null or . == 0 then "?"
                else "\(. / 1073741824 * 10 | floor / 10)G" end;
        [.machines[] | . as $m | (.drawer_list // [])[]
           | select(.state == "writing")
           | {name: $m.name, port, dev, bytes_written, bytes_total, stalled_s}]
        | .[] | [ .name,
                  (if .port then "drive \(.port) (SATA \(.port - 1))"
                     else (.dev // "?") end),
                  (if (.bytes_total // 0) > 0
                     then "\((.bytes_written // 0) * 100 / .bytes_total | floor)%"
                     else "--" end),
                  (.bytes_written | gb),
                  (.bytes_total | gb),
                  (if .stalled_s == null then "(no stall data)"
                   elif .stalled_s >= $stall
                   then "STALLED \(.stalled_s / 60 | floor)m"
                   else "" end) ] | @tsv' \
        "$RUN_DIR/room.json" > "$RUN_DIR/room_writing.txt" 2>/dev/null || {
        # Rule 5: a list we failed to read is not "nobody is writing",
        # and that is exactly the difference this screen exists to show.
        echo "  (the progress list could not be read -- see the journal)"
        log "cloning room: the progress list did not parse"
        return 0
    }
    [ -s "$RUN_DIR/room_writing.txt" ] || return 0
    echo "  Writing now:"
    # The server decides this, not the agent, and not fanout: only the
    # server sees every drawer at once. Measured on 08/09 -- the four
    # healthy drawers froze in the SAME samples, 81%-100% overlap, two of
    # them in a different physical machine. A per-drawer "STALLED" label
    # there would blame a drive for a pause of the stream, which is the
    # same mistake as "fanout: buffer overrun (drive too slow)".
    if [ "$(json_get "$RUN_DIR/room.json" ".stream_stalled")" = "true" ]; then
        echo "    ALL drawers stopped together -- this is the stream, not a drive."
    fi
    while IFS="$(printf '\t')" read -r _nm _where _pct _done _all _flag; do
        printf '    %-8s %-16s %4s  %8s of %-8s %s\n' \
            "$_nm" "$_where" "$_pct" "$_done" "$_all" "$_flag"
    done < "$RUN_DIR/room_writing.txt"
    echo
}

room_draw_failures() {
    # #553: a failed drive, in a line somebody can act on.
    #
    # The slot, never the device name. sda/sdb/sdc are NOT the order of the
    # SATA ports: measured on HP2 on 08/09, sdb sits in slot 3 and sdc in
    # slot 2. A line that says "sdb" and gets read as "drawer 2" sends the
    # technician to the middle drawer instead of the bottom one -- and he
    # pulls a healthy drive while the faulty one stays in.
    #
    # `port` is the ataN the agent reported, and the kernel numbers from 1:
    # ata1 is the first slot on the controller, i.e. the top drawer (#27).
    #
    # #567: the line prints BOTH numbers, because they do not agree. The
    # kernel counts slots from 1; the board is silkscreened from 0. So the
    # drive on the connector labelled SATA 0 is ata1. Printing only "drawer
    # 2" sends a technician looking for the label "SATA 2" -- the third
    # connector -- and he pulls a healthy drive. Off by one, same damage as
    # printing the device name. "drive 2 (SATA 1)" needs no translation.
    #
    # This holds for any chassis because it is Nadav's own wiring rule,
    # stated 08/09: SATA 0 -> drive 1, SATA 1 -> drive 2, SATA 2 -> drive 3.
    # A declaration, not a measurement -- so no per-model calibration. The
    # one assumption left is that ata1 is the connector the board calls
    # SATA 0, which is worth checking once per new model and never again.
    #
    # With no `port` -- NVMe, a VM, a non-ATA controller -- no number is
    # invented. The serial alone beats a wrong drawer number.
    jq -r '[.machines[] | . as $m | (.drawer_list // [])[]
            | select(.state == "failed")
            | {name: $m.name, port, dev, serial, model, error}]
           | .[] | [ .name,
                     (if .port then "drive \(.port) (SATA \(.port - 1))"
                     else (.dev // "?") end),
                     # The model is cut to fit; the serial never is. On an
                     # 80-column VGA console a long model name pushed the
                     # line past the edge and the serial wrapped -- and the
                     # serial is the only field that identifies the drive
                     # in the technician hand. A model is a hint; a cut
                     # serial matches the wrong drive.
                     ((.model // "unknown model")[0:20]),
                     (.serial // "no serial"),
                     (.error // "") ] | @tsv' \
        "$RUN_DIR/room.json" > "$RUN_DIR/room_failed.txt" 2>/dev/null || {
        # A read that failed is not "no failures" -- rule 5.
        echo "  (the failure list could not be read -- see the journal)"
        log "cloning room: the failure list did not parse"
        return 0
    }
    [ -s "$RUN_DIR/room_failed.txt" ] || return 0
    echo "  FAILED DRIVES:"
    while IFS="$(printf '\t')" read -r _nm _where _model _serial _err; do
        printf '    %-8s %-16s %-20s %s\n' "$_nm" "$_where" "$_model" "$_serial"
        [ -n "$_err" ] && printf '               %s\n' "$_err"
    done < "$RUN_DIR/room_failed.txt"
    echo
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
