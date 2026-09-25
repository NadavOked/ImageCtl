# roomdraw.sh -- rendering the cloning-room screen for the build machine's
# text console (spec 29). Split out of roomflow.sh (#418) to stay under the
# 300-line ceiling (CLAUDE.md, tests/sizelimit.py): roomflow.sh drives the
# round (open/wake/start) and calls room_draw(); this file only reads
# $RUN_DIR/room.json, written by room_status_get in roomflow.sh, and prints
# it. POSIX sh (busybox ash).

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
    # #715: a round fed from the build machine's own disk says so, and says
    # whether that machine has finished reading it (the wave waits for that).
    if [ "$(json_get "$RUN_DIR/room.json" ".round.source.kind")" = "build_disk" ]; then
        echo "  Source:     build machine $(json_get "$RUN_DIR/room.json" ".round.source.name") disk $(json_get "$RUN_DIR/room.json" ".round.source.disk") -- manifest ready: $(json_get "$RUN_DIR/room.json" ".round.source.manifest_ready")"
    fi
    echo "  Drives:     $_written of $_target written, $_left to go"
    echo "  Ready now:  $_ready fresh drawers in machines that joined"
    room_draw_pace
    echo
    echo "  Machines:"
    # No 2>/dev/null: a machine list we failed to read is not an empty room,
    # and an empty room is exactly what somebody would act on (rule 5).
    # #105: "0/3 fresh" alone is the screen that told nobody why. A drive
    # with no serial is counted on its own, and listed by slot further down.
    if jq -r '.machines[] | [.name, (if .awake then "on" else "off" end),
            "\(.fresh_drawers)/\(.drawers)", (.state // "-"),
            ([(.drawer_list // [])[] | select((.serial // "") == "")] | length
             | if . > 0 then "\(.) unidentified" else "" end)] | @tsv' \
            "$RUN_DIR/room.json" > "$RUN_DIR/room_rows.txt"; then
        while IFS="$(printf '\t')" read -r _nm _on _dr _st _un; do
            printf '    %-12s %-3s  %-6s fresh  %s  %s\n' \
                "$_nm" "$_on" "$_dr" "$_st" "$_un"
        done < "$RUN_DIR/room_rows.txt"
    else
        echo "    (the machine list could not be read -- see the journal)"
        log "cloning room: the machine list did not parse"
    fi
    echo
    room_draw_writing
    room_draw_done
    room_draw_unidentified
    room_draw_failures
}

# #410: "how long, how fast, how much longer" -- the three numbers the
# operator did not have on 05/09, plus how old this screen is. The server
# derives them (room.py:_pace/_wave_pace) from the bytes each drawer
# reported and the wave's start; the screen only prints them. null is
# printed as "(not measured)", never as 0: a rate of 0 reads as "stopped",
# and a wave that simply has no measurement yet is not stopped (rule 5).
room_hms() {
    # $1 = seconds or "null" -> "1h 02m 03s" / "(not measured)"
    case "$1" in ''|null|*[!0-9]*) echo "(not measured)"; return 0 ;; esac
    if [ "$1" -ge 3600 ]; then
        printf '%dh %02dm %02ds\n' $(($1 / 3600)) $(($1 % 3600 / 60)) $(($1 % 60))
    else
        printf '%dm %02ds\n' $(($1 / 60)) $(($1 % 60))
    fi
}

room_draw_pace() {
    _el=$(json_get "$RUN_DIR/room.json" ".round.elapsed_s")
    _rt=$(json_get "$RUN_DIR/room.json" ".round.rate_bps")
    _eta=$(json_get "$RUN_DIR/room.json" ".round.eta_s")
    case "$_rt" in ''|null|*[!0-9]*) _rt="(not measured)" ;;
        *) _rt="$(($_rt / 1000000)) MB/s (slowest drawer)" ;; esac
    echo "  Elapsed:    $(room_hms "$_el")"
    echo "  Rate:       $_rt"
    echo "  ETA:        $(room_hms "$_eta")"
    # Freshness: when room_status_get last succeeded (roomflow.sh writes
    # the epoch). A screen that keeps an old picture must say how old.
    if [ -r "$RUN_DIR/room_fetched_at" ]; then
        _at=$(cat "$RUN_DIR/room_fetched_at")
        case "$_at" in ''|*[!0-9]*) echo "  Updated:    (fetch time unreadable)" ;;
            *) echo "  Updated:    $(( $(date +%s) - _at ))s ago" ;; esac
    else
        echo "  Updated:    (never fetched)"
    fi
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
        (.stream_stalled // false) as $stream
        | [.machines[] | . as $m | (.drawer_list // [])[]
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
                   then (if $stream
                         then "slow (stream) \(.stalled_s / 60 | floor)m"
                         else "STALLED \(.stalled_s / 60 | floor)m" end)
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

# #418: a drawer already written, in the same actionable shape as the
# failure list below (#553) -- slot, serial, model. `fresh == false` WITH
# a serial is the server's own positive evidence that this exact serial finished
# writing in this round (room.py:_tally): it is set from the round's
# `written_serials`, so unlike `state` -- which only exists for the
# CURRENT wave's live members -- it still reads correctly for a drawer
# that finished in an EARLIER wave of the same round, after the machine
# has rotated out of the live member list. Nadav asked "which disk
# succeeded on cloning machine 2" and the answer available at the time
# was `sdb` -- a device name, not a drawer. This line gives the slot and
# the serial together, so pulling the right drive needs no memory.
room_draw_done() {
    jq -r '[.machines[] | . as $m | (.drawer_list // [])[]
            | select(.fresh == false and (.serial // "") != "")
            | {name: $m.name, port, dev, model, serial}]
           | .[] | [ .name,
                     (if .port then "drive \(.port) (SATA \(.port - 1))"
                     else (.dev // "?") end),
                     ((.model // "unknown model")[0:20]), .serial ] | @tsv' \
        "$RUN_DIR/room.json" > "$RUN_DIR/room_done.txt" 2>/dev/null || {
        echo "  (the written-drive list could not be read -- see the journal)"
        log "cloning room: the written-drive list did not parse"
        return 0
    }
    [ -s "$RUN_DIR/room_done.txt" ] || return 0
    echo "  WRITTEN:"
    while IFS="$(printf '\t')" read -r _nm _where _model _serial; do
        printf '    %-8s %-16s %-20s %s\n' "$_nm" "$_where" "$_model" "$_serial"
    done < "$RUN_DIR/room_done.txt"
    echo
}

# #105: a drive that reported no serial. The round counts by serial
# (room.py:_tally), so for this drive `fresh` has no answer -- and before
# this an EMPTY drive with no serial came back `fresh: false` and was
# listed above as WRITTEN, where it gets pulled as done. It is never
# listed as written. It gets its own line, by slot, with the only evidence
# there is: this wave's own report for that device (`state`), or none.
# No machine joins a wave for it alone (room.py:fresh_serials); the
# operator sees it here and decides.
room_draw_unidentified() {
    jq -r '[.machines[] | . as $m | (.drawer_list // [])[]
            | select((.serial // "") == "") | {name: $m.name, port, dev, model, state}]
           | .[] | [ .name,
                     (if .port then "drive \(.port) (SATA \(.port - 1))"
                     else (.dev // "?") end),
                     ((.model // "unknown model")[0:20]),
                     (if .state == "done" then "written this wave (agent)"
                      elif .state == "failed" then "failed (see below)"
                      elif .state == null then "not known if written"
                      else .state end) ] | @tsv' \
        "$RUN_DIR/room.json" > "$RUN_DIR/room_unid.txt" 2>/dev/null || {
        echo "  (the unidentified-drive list could not be read -- see the journal)"
        log "cloning room: the unidentified-drive list did not parse"
        return 0
    }
    [ -s "$RUN_DIR/room_unid.txt" ] || return 0
    echo "  CANNOT IDENTIFY THE DRIVE (no serial -- the round cannot count it):"
    while IFS="$(printf '\t')" read -r _nm _where _model _ev; do
        printf '    %-8s %-16s %-20s %s\n' "$_nm" "$_where" "$_model" "$_ev"
    done < "$RUN_DIR/room_unid.txt"
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
