# directsend.sh -- the build machine as the multicast source (#715).
# POSIX sh (busybox ash). No bashisms.
#
# "Deploy from this disk": the server stays the only orchestrator (session,
# multicast parameters, receiver count, start, progress, console); what
# changes is the SOURCE. Instead of the server's SenderEngine streaming a
# stored image, this machine runs udp-sender over its own disk, read-only,
# and nothing is written to the server. The receivers (restore.sh) do not
# change -- they cannot tell who is sending.
#
# Two reads of the disk, on purpose. The receivers compare every partition
# to the sha256 in the manifest they fetched BEFORE the stream (restore.sh),
# so the manifest -- and the sha256 of the compressed bytes -- must exist
# before the first datagram. Pass 1 is capture_disk with the upload sink
# swapped for a bit bucket: same gates (GPT only, hibernation #651,
# BitLocker #671), same manifest, no byte leaves the machine. Pass 2 is the
# broadcast, hashed again on the wire and compared to pass 1: a source that
# sent something other than what it promised fails HERE, by name, and not
# as "sha256 mismatch" on every drawer in the room.

#: udp-sender prints this once receivers joined and the transfer began
#: (#438). Exit 0 without it is a start-timeout with nobody, not a send.
DIRECT_TRANSFER_STARTED="Starting transfer:"

direct_udp_send() {
    # stdin = one compressed partition, exactly what the receivers hash.
    # $1 portbase, $2 min receivers, $3 max-wait, $4 start-timeout,
    # $5 retries-until-drop, $6 max-bitrate ("" = none) -- the server's
    # own values (task.direct.multicast, interface 3), so the stream behaves
    # exactly like a library round; only --interface is ours ($IFACE from
    # net.conf) and there is no --file: the partition arrives on stdin.
    # ‏--nokbd כמו בשרת: אין מקלדת שתעצור שידור באמצע. הפלט (stdout+stderr,
    # כמו run_process בשרת) הולך לאן שהקורא מפנה אותו -- לוג לכל מחיצה,
    # שבו נבדקת שורת ה-Starting transfer; לא למסך, ולא לצינור.
    # shellcheck disable=SC2086 # --max-bitrate <value> הוא זוג מילים מכוון
    udp-sender --interface "$IFACE" --portbase "$1" \
        --min-receivers "$2" --max-wait "$3" --start-timeout "$4" \
        --retries-until-drop "$5" --nokbd ${6:+--max-bitrate "$6"}
}

_direct_failed() {
    # $1 = disk, $2 = reason. Same road as _capture_failed (#106): the
    # reason lands in the target's error field, which is what the console
    # shows; the log alone is tmpfs and gone at the next boot.
    log "direct send failed on $1: $2"
    target_set "$1" "failed" "$2"
    echo "failed" > "$RUN_DIR/state"
}

direct_manifest_put() {
    # $1 = task id, $2 = manifest path. The manifest -- and only the
    # manifest -- goes to the server, so the receivers can fetch it as
    # /api/v1/images/<image_id>/manifest. The bytes never do (Nadav 12/09).
    curl -sfS -X PUT -H "Content-Type: application/json" \
        -H "X-Imagectl-Task-Token: ${TASK_TOKEN:-}" \
        --data-binary "@$2" "$SERVER/api/v1/direct/$1/manifest" >> "$LOG_FILE" 2>&1
}

direct_wait_running() {
    # $1 = task id. Polls hello until the server says the wave is running --
    # the selected drawers cover the target, or somebody pressed "send now".
    # 0 = send. 1 = the task is gone or the wave closed: not ours to decide,
    # and a source that starts anyway broadcasts to nobody (#438).
    # No ceiling on the wait itself -- the room has no timer, a person
    # stands there (spec 29) -- but a server that stops answering is
    # counted the way the main loop counts it, and 30 misses is a failure.
    _dw_miss=0
    while :; do
        if send_hello; then
            _dw_miss=0
            [ "$(json_get "$RESP" ".task.id")" = "$1" ] \
                || { log "direct: task $1 is no longer ours -- not sending"; return 1; }
            case "$(json_get "$RESP" ".task.direct.session_state")" in
                running) return 0 ;;
                closed)  log "direct: the wave closed before it started"; return 1 ;;
            esac
            ui_clear; ui_header
            echo "  Waiting for the cloning machines to join the wave."
            echo "  Joined so far: $(json_get "$RESP" ".task.direct.receivers")"
            echo "  Sending starts from the screen or the console ('send now')."
        else
            _dw_miss=$((_dw_miss + 1))
            [ "$_dw_miss" -ge 30 ] && { log "direct: server unreachable while waiting"; return 1; }
        fi
        sleep "${DIRECT_POLL_S:-2}"
    done
}

direct_send_one() {
    # $1 disk, $2 index, $3 fs, $4 sha256 from pass 1, then the six
    # multicast values of direct_udp_send. Reads the partition once more,
    # compresses exactly as pass 1 did, hashes what goes on the wire, and
    # sends. 0 only when udp-sender exited 0, actually transferred, and the
    # wire hash equals the manifest hash.
    _do_node=$(partition_node "$1" "$2"); _do_pcl=$(partclone_for_fs "$3")
    _do_ignore=""; [ "$3" = ntfs ] && _do_ignore="-I"
    _do_log="$RUN_DIR/dsend.$2.log"; _do_fifo="$RUN_DIR/dsha.$2.fifo"
    # #957: the first partition waits for the wave to start; the later ones
    # wait for the slowest drawer in the room, and their udp-sender carries
    # the server's later max-wait/start-timeout (up to 780s) -- so the
    # progress ceiling here must outlast it (900, waits.sh).
    next_stream_ceiling
    rm -f "$_do_log" "$_do_fifo" "$RUN_DIR/dsha.$2" "$RUN_DIR/dsend.$2.rc" \
        "$RUN_DIR/dpcl.$2.rc" "$RUN_DIR/dzstd.$2.rc" "$RUN_DIR/dtee.$2.rc"
    mkfifo "$_do_fifo"
    sha256sum < "$_do_fifo" > "$RUN_DIR/dsha.$2" &
    _do_shapid=$!
    log "partition $2 ($3): sending"
    # Each stage's rc is captured by name (no pipefail in busybox ash), and
    # the pipeline runs in the background under wait_progress, watching the
    # pv counter: udp-sender waits for its receivers before it reads a byte,
    # and a slow drawer is allowed to slow it -- a stopped stream is not.
    # shellcheck disable=SC2046 # partclone_mode's flags are a word list
    (
        { "$_do_pcl" $(partclone_mode "$_do_pcl" -c) $_do_ignore -s "$_do_node" \
              -L "$RUN_DIR/targets/$1/partclone.log" 2>> "$LOG_FILE"
          echo "$?" > "$RUN_DIR/dpcl.$2.rc"; } \
            | { zstd -"$CAPTURE_LEVEL" -T"$CAPTURE_THREADS" -c 2>> "$LOG_FILE"
                echo "$?" > "$RUN_DIR/dzstd.$2.rc"; } \
            | { pv -n -b -i 2 2>> "$RUN_DIR/targets/$1/bytes.raw"; } \
            | { tee "$_do_fifo"; echo "$?" > "$RUN_DIR/dtee.$2.rc"; } \
            | { direct_udp_send "$5" "$6" "$7" "$8" "$9" "${10}" >> "$_do_log" 2>&1
                echo "$?" > "$RUN_DIR/dsend.$2.rc"; }
    ) &
    _do_pid=$!
    if wait_progress "$_do_pid" "$RUN_DIR/targets/$1/bytes.raw" \
            "$STREAM_START_CEILING" "$WAIT_STREAM_STALL_S" "שידור מחיצה $2 מ-$1"; then
        _do_stage=none
        for _do_s in dsend dtee dzstd dpcl; do
            _do_rc=$(cat "$RUN_DIR/$_do_s.$2.rc" 2>/dev/null || echo 1)
            [ "$_do_rc" -eq 0 ] || { _do_stage="$_do_s"; break; }
        done
    else
        _do_rc="$WAIT_TIMED_OUT"; _do_stage=timeout
    fi
    wait_pid "$_do_shapid" "$WAIT_HELPER_S" "חישוב ה-sha256 של מחיצה $2"
    rm -f "$_do_fifo"
    if [ "$_do_rc" -ne 0 ]; then
        _direct_failed "$1" "partition $2: send failed (stage=$_do_stage rc=$_do_rc) $(tail -n 1 "$_do_log" 2>/dev/null | tr -d '\r')"
        return 1
    fi
    # Exit 0 is not a transfer (#438): without receivers udp-sender times
    # out quietly and still exits 0. The line in its own log is the evidence.
    grep -qF "$DIRECT_TRANSFER_STARTED" "$_do_log" 2>/dev/null \
        || { _direct_failed "$1" "partition $2: no cloning machine joined the stream (udp-sender never started the transfer)"; return 1; }
    _do_got=$(awk '{print $1}' "$RUN_DIR/dsha.$2" 2>/dev/null)
    if ! is_sha256 "$_do_got"; then
        _direct_failed "$1" "partition $2: the wire sha256 was not computed -- no evidence the stream matched the manifest"
        return 1
    elif [ "$_do_got" != "$4" ]; then
        _direct_failed "$1" "partition $2: the bytes sent differ from the manifest (disk changed between reads, or the pipeline is not deterministic)"
        return 1
    fi
    target_partition_done "$1"
}

direct_send_run() {
    # $1 = task id, $2 = disk. 0 only when every streamed partition went
    # out, was received by someone, and hashed to what the manifest says.
    echo "capturing" > "$RUN_DIR/state"
    CAPTURE_SINK=discard capture_disk "$1" "$2" || return 1
    _dr_manifest="$RUN_DIR/new-manifest.json"
    direct_manifest_put "$1" "$_dr_manifest" \
        || { _direct_failed "$2" "the server did not accept the live manifest"; return 1; }
    echo "waiting" > "$RUN_DIR/state"
    direct_wait_running "$1" || { _direct_failed "$2" "the wave did not start"; return 1; }

    # The multicast parameters come from the hello that said "running" --
    # the server's, so the stream behaves like a library round (#437/#438).
    # max_wait_later/start_timeout_later (#957): partitions 2+ wait for the
    # slowest drawer, not for the operator -- the server's rule, not ours.
    _dr_mc=$(jq -r '.task.direct.multicast | [.portbase, .min_receivers, .max_wait,
        .start_timeout, .max_wait_later, .start_timeout_later, .retries_until_drop,
        (.max_bitrate // "")] | map(tostring) | join("|")' \
        "$RESP" 2>/dev/null)
    _dr_pb=${_dr_mc%%|*}; _dr_rest=${_dr_mc#*|}
    _dr_minr=${_dr_rest%%|*}; _dr_rest=${_dr_rest#*|}
    _dr_maxw=${_dr_rest%%|*}; _dr_rest=${_dr_rest#*|}
    _dr_startt=${_dr_rest%%|*}; _dr_rest=${_dr_rest#*|}
    _dr_maxw_l=${_dr_rest%%|*}; _dr_rest=${_dr_rest#*|}
    _dr_startt_l=${_dr_rest%%|*}; _dr_rest=${_dr_rest#*|}
    _dr_retries=${_dr_rest%%|*}; _dr_bitrate=${_dr_rest#*|}
    for _dr_v in "$_dr_pb" "$_dr_minr" "$_dr_maxw" "$_dr_startt" \
                 "$_dr_maxw_l" "$_dr_startt_l" "$_dr_retries"; do
        case "$_dr_v" in ''|*[!0-9]*)
            _direct_failed "$2" "multicast parameters unreadable: '$_dr_mc'"; return 1 ;;
        esac
    done
    _dr_plan="$RUN_DIR/direct.plan"
    manifest_plan "$_dr_manifest" > "$_dr_plan" \
        || { _direct_failed "$2" "could not read the partition plan from the live manifest"; return 1; }
    _dr_total=$(json_get "$_dr_manifest" ".total_compressed_bytes")
    case "$_dr_total" in ''|*[!0-9]*) _dr_total=0 ;; esac
    target_init "$2" "$_dr_total"
    echo "sending" > "$RUN_DIR/state"
    # swap is described in the manifest and recreated by the receiver; it
    # is never streamed (spec 14) -- exactly streamed_partitions() on the server.
    _dr_expected=$(awk -F'|' '$7 != "" && $7 != "null" { n++ } END { print n + 0 }' "$_dr_plan")
    _dr_sent=0; STREAMED_PARTITIONS=0
    while IFS='|' read -r _dr_idx _dr_g _dr_role _dr_fs _dr_st _dr_sz _dr_f _dr_sha _dr_e _dr_ug _dr_uu <&3; do
        case "$_dr_f" in ''|null) continue ;; esac
        # The first stream waits for the operator; every later one waits for
        # the drawers still writing the previous partition (#957).
        _dr_w="$_dr_maxw"; _dr_t="$_dr_startt"
        [ "$_dr_sent" -gt 0 ] && { _dr_w="$_dr_maxw_l"; _dr_t="$_dr_startt_l"; }
        direct_send_one "$2" "$_dr_idx" "$_dr_fs" "$_dr_sha" \
            "$_dr_pb" "$_dr_minr" "$_dr_w" "$_dr_t" "$_dr_retries" "$_dr_bitrate" || return 1
        _dr_sent=$((_dr_sent + 1))
    done 3< "$_dr_plan"
    # done is a count against the manifest, never "nothing failed" (#51).
    if [ "$_dr_expected" -lt 1 ] || [ "$_dr_sent" -ne "$_dr_expected" ]; then
        _direct_failed "$2" "sent $_dr_sent of $_dr_expected partitions"
        return 1
    fi
    log "direct send complete: $_dr_sent partitions from $2"
}

direct_send_task() {
    # $1 = task id; the answer that carried the task is in $RESP. Mirrors
    # do_task for a capture: both roads out end at report_final (#127), and
    # "the server never heard" holds instead of powering off.
    _dt_disk=$(json_get "$RESP" ".task.disk")
    ui_clear; ui_header
    echo "  Deploying /dev/$_dt_disk directly to the cloning machines."
    echo "  Source disk: read only -- nothing is written to it."
    echo "  Do not power off."
    progress_loop "" "$MAC" "$SERVER" "$1" &
    _dt_ppid=$!
    if direct_send_run "$1" "$_dt_disk"; then
        echo "done" > "$RUN_DIR/state"
        report_final "$_dt_ppid" "" "$MAC" "$SERVER" "$1" \
            || { hold_unheard "" "$1"; return 1; }
        ui_clear; ui_header
        echo "  Direct deployment complete. The drawers were written."
        [ "${IMAGECTL_TEST:-0}" = "1" ] && exit 0
        sleep 20
        finish_and_stop
    fi
    echo "failed" > "$RUN_DIR/state"
    report_final "$_dt_ppid" "" "$MAC" "$SERVER" "$1" \
        || { hold_unheard "" "$1" \
             "direct deployment did not complete, and the server was not told"; return 1; }
    ui_error_hold "direct deployment did not complete" hold_beat
    return 1
}
