# lccopy.sh -- #1322 local clone: the copy itself. The source is read ONCE
# per partition and fanned out to every target in parallel -- the drawers
# pipeline of a round (drawers.sh), with partclone reading a local partition
# where a round has udp-receiver. POSIX sh (busybox ash).
#
#   partclone -c <source part> > src.fifo          (used blocks only; an
#     unknown filesystem goes through partclone.dd -- the whole partition)
#   fanout -H sha.out -P bytes.raw <buffer> t1/feed t2/feed ... < src.fifo
#   per target: fanout -H <its sha.out> <buf> relay < feed  (hashes, forwards; 1MiB pipe)
#               partclone -r -O <target part> < relay
#
# The evidence for one target and one partition is four records, all of
# which must agree (the lesson of verdict.sh, #440/#520/#527): fanout said
# "ok", partclone exited 0, the target relay (fanout) exited 0, and the xxh64 of what
# the target pipeline consumed equals the xxh64 of what was read from the
# source. A failing target fails alone; the source failing fails every one.
# No full read-back of the data (owner, 13/10): it would double the time.

LC_STALL_S="${LC_STALL_S:-${WAIT_STREAM_STALL_S:-300}}"

_lc_gpt_why() {
    # #1455: apply_gpt's own steps (zap, -n, -U, -e, rereadpt) log their cause
    # but set no PLAN_ERROR; the card then said only "not written". The log's
    # last lines name the step and sgdisk's/blockdev's own words.
    [ -n "${PLAN_ERROR:-}" ] && { printf '%s
' "$PLAN_ERROR"; return; }
    printf 'טבלת המחיצות לא נכתבה: %s
' "$(tail -n 3 "$LOG_FILE" 2>/dev/null | tr '
' ' ' | cut -c1-240)"
}

lc_prepare_target() {
    # $1 = target, $2 = manifest. The table exactly as on the source (GUIDs
    # included), grown to a larger disk -- run_restore_drawers' own sequence.
    target_set "$1" writing
    disk_fits "$1" "$2" || return 1
    if ! apply_gpt "$1" "$2"; then
        fail_written_target "$1" "$(_lc_gpt_why)"; return 1
    fi
    if ! EXPAND_OVERRIDE=auto expand_last "$1" "$2"; then
        log "WARNING: expansion failed on $1 -- writing at the source's own size"
        apply_gpt "$1" "$2" || { fail_written_target "$1" "$(_lc_gpt_why)"; return 1; }
    fi
    lc_set_names "$1" || { fail_written_target "$1" "שמות המחיצות לא נכתבו (sgdisk -c)"; return 1; }
    blockdev --rereadpt "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 \
        || { fail_written_target "$1" "הקרנל לא קרא את הטבלה החדשה"; return 1; }
}

# ‏#1438: ‏xxh64 בתוך fanout, לא md5sum/sha256sum בתהליך משלו. על מחשב שיכפול 2
# (i5-650) הצינור pv|tee|md5sum|fanout נתן 84MB/s על 4GiB: ה-CPU ‏21% פנוי
# ו-34% sys, כל תהליך 5-10% — המחיר הוא מספר התהליכים והקפיצות בין צינורות,
# לא הגיבוב (גיבוב יחיד: 87; ‏partclone -c לבדו: 170). לכן fanout מגבב
# ומונה בעצמו (‏-H, ‏-P). הגיבוב משווה את מה שנקרא למה שכל יעד לקח (שגיאת
# העברה), לא מגן מתוקף. ראיה = 16 תווי hex קטנים בדיוק; כל דבר אחר = לא חושב.
_lc_hash_ok() { case "$1" in *[!0-9a-f]*|'') return 1 ;; esac; [ "${#1}" -eq 16 ]; }

_lc_target_pipeline() {
    # $1 = target, $2 = fs, $3 = idx. Reads its fifo, hashes what it takes,
    # writes the partition. Every exit code is caught on its own: busybox ash
    # has no pipefail (#527).
    _lt_t="$RUN_DIR/targets/$1"; _lt_node=$(partition_node "$1" "$3"); _lt_pcl=$(partclone_for_fs "$2")
    (
        # ‏#1438: fanout מגבב ומעביר ל-partclone (צינור 1MB) במקום tee של busybox,
        # שמעתיק 1KB בכל קריאה. נמדד במחשב שיכפול 2: ‏195 → 246MB/s (בלי גיבוב: 258).
        # shellcheck disable=SC2046 # partclone_mode flags are a word list
        { "$_lt_pcl" $(partclone_mode "$_lt_pcl" -r) -s - -O "$_lt_node" \
            -L "$_lt_t/partclone.log" < "$_lt_t/relay" 2>> "$LOG_FILE"
          echo "$?" > "$_lt_t/pipeline.rc"; } &
        _lt_pp=$!
        fanout -H "$_lt_t/sha.out" 16777216 "$_lt_t/relay" < "$_lt_t/feed" \
            > "$_lt_t/relay.out" 2>> "$LOG_FILE"
        echo "$?" > "$_lt_t/tee.rc"   # ‏rc של הממסר: 0 רק אם partclone לקח הכול
        wait_pid "$_lt_pp" "$WAIT_DRAWER_S" "partclone של יעד $1 (מחיצה $3)" \
            || echo "$WAIT_TIMED_OUT" > "$_lt_t/pipeline.rc"
    ) &
    echo "$!" > "$_lt_t/pipeline.pid"
}

_lc_wait_stream() {
    # $1 = pid of the stream. Watches the source counter: a slow disk delays,
    # a stopped stream ends here. A stop request (Esc → "עצור") ends the
    # source reader; everything downstream then sees EOF. 2 = stopped,
    # 1 = stalled, 0 = the stream ended by itself.
    _lw_seen=""; _lw_still=0; _lw_rc=0
    while kill -0 "$1" 2>/dev/null; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        sleep 1
        if [ -f "$LC/stop.req" ] && [ "$_lw_rc" = 0 ]; then
            log "local clone: stop requested -- ending the source reader"
            kill "$(cat "$LC/run/src.pid" 2>/dev/null)" 2>/dev/null; _lw_rc=2
        fi
        _lw_now=$(_last_number "$LC/run/bytes.raw")
        if [ "$_lw_now" != "$_lw_seen" ]; then _lw_seen=$_lw_now; _lw_still=0; continue; fi
        _lw_still=$((_lw_still + 1))
        if [ "$_lw_still" -ge "$LC_STALL_S" ] && [ "$_lw_rc" = 0 ]; then
            log "local clone: no progress for $LC_STALL_S s -- ending the source reader"
            kill "$(cat "$LC/run/src.pid" 2>/dev/null)" 2>/dev/null; _lw_rc=1
        fi
    done
    wait_pid "$1" "$WAIT_HELPER_S" "הזרם של המחיצה"
    return "$_lw_rc"
}

lc_stream() {
    # $1 = idx, $2 = fs, $3 = source, $4.. = targets. 0 = at least one target
    # took the partition, 1 = none did, 2 = stopped.
    _ls_i="$1"; _ls_fs="$2"; _ls_src="$3"; shift 3
    _ls_r="$LC/run"; _ls_pcl=$(partclone_for_fs "$_ls_fs"); _ls_feeds=""
    for _ls_t in "$@"; do
        _ls_d="$RUN_DIR/targets/$_ls_t"
        [ "$(cat "$_ls_d/state")" = failed ] && continue
        rm -f "$_ls_d/feed" "$_ls_d/feed.bytes" "$_ls_d/relay" "$_ls_d/relay.bytes" "$_ls_d/relay.out" \
              "$_ls_d/sha.out" "$_ls_d/pipeline.rc" "$_ls_d/tee.rc" "$_ls_d/pipeline.pid"
        mkfifo "$_ls_d/feed" "$_ls_d/relay" || { fail_written_target "$_ls_t" "מחיצה $_ls_i: mkfifo נכשל"; continue; }
        target_counter "$_ls_t" "$_ls_d/feed.bytes"
        echo "$_ls_i" > "$_ls_d/lc_part"
        _lc_target_pipeline "$_ls_t" "$_ls_fs" "$_ls_i"
        _ls_feeds="$_ls_feeds $_ls_d/feed"
    done
    [ -n "$_ls_feeds" ] || return 1
    rm -f "$_ls_r/src.fifo" "$_ls_r/sha.out" "$_ls_r/src.rc" "$_ls_r/fanout.rc" "$_ls_r/fanout.out" "$_ls_r/partclone.err"
    : > "$_ls_r/bytes.raw"; echo "$_ls_i|copy" > "$_ls_r/cur"
    mkfifo "$_ls_r/src.fifo" || return 1
    # ntfs: -I, as in capture -- the read is read-only, and an identical copy
    # carries the source's own state (a fast-started Windows resumes the same).
    _ls_ign=""; [ "$_ls_fs" = ntfs ] && _ls_ign="-I"
    # ‏#1438: בלי checksum של partclone (‏-a0) — ה-xxh64 כבר משווה מקור ליעד. נמדד
    # על מחשב שיכפול 2: ‏108 → 157MB/s. ה-r- קורא את מצב ה-checksum מהכותרת.
    # חוצץ 512KB (‏-z): ‏160 → 170MB/s, ‏1MB נמדד פעמיים (160/160); 4MB ‏114, ‏16MB ‏106.
    [ "$_ls_pcl" = partclone.dd ] || _ls_ign="$_ls_ign -a0 -z 524288"
    (
        # shellcheck disable=SC2046 # partclone_mode flags are a word list
        "$_ls_pcl" $(partclone_mode "$_ls_pcl" -c) $_ls_ign -s "$(partition_node "$_ls_src" "$_ls_i")" \
            -L "$_ls_r/partclone.log" > "$_ls_r/src.fifo" 2>> "$_ls_r/partclone.err" &
        echo "$!" > "$_ls_r/src.pid"; wait "$!"; echo "$?" > "$_ls_r/src.rc"
    ) &
    # ‏#1438: fanout מגבב (‏-H, רק בסוף זרם שנקרא כולו) ומונה (‏-P, פורמט pv -n)
    # בעצמו. sha.out נמחק למעלה — קובץ ממחיצה קודמת היה ראיה כוזבת.
    # shellcheck disable=SC2086 # the feed list is a word list on purpose
    ( fanout -H "$_ls_r/sha.out" -P "$_ls_r/bytes.raw" "$(_fanout_buffer "$(echo "$_ls_feeds" | wc -w)")" \
        $_ls_feeds < "$_ls_r/src.fifo" > "$_ls_r/fanout.out" 2>> "$LOG_FILE"
      echo "$?" > "$_ls_r/fanout.rc" ) &
    _lc_wait_stream "$!"; _ls_w=$?
    # The stream is over: a target whose fifo never got a writer (fanout that
    # did not start, #49) is released now, not after a drawer's ceiling.
    for _ls_f in $_ls_feeds; do unblock_fifo "$_ls_f"; done
    for _ls_t in "$@"; do
        _ls_pf="$RUN_DIR/targets/$_ls_t/pipeline.pid"
        [ -f "$_ls_pf" ] || continue
        wait_pid "$(cat "$_ls_pf")" "$WAIT_DRAWER_S" "יעד $_ls_t (מחיצה $_ls_i)" && continue
        unblock_fifo "$RUN_DIR/targets/$_ls_t/feed"
        fail_written_target "$_ls_t" "מחיצה $_ls_i: פג הזמן — היעד הפסיק לכתוב"
    done
    [ ! -f "$_ls_r/partclone.err" ] || cat "$_ls_r/partclone.err" >> "$LOG_FILE"
    _ls_b=$(_last_number "$_ls_r/bytes.raw"); _ls_b0=$(_last_number "$_ls_r/src.base")
    echo $(( ${_ls_b0:-0} + ${_ls_b:-0} )) > "$_ls_r/src.base"; : > "$_ls_r/bytes.raw"
    _ls_src_rc=$(cat "$_ls_r/src.rc" 2>/dev/null); _ls_sha=$(awk '{print $1}' "$_ls_r/sha.out" 2>/dev/null)
    _ls_why=""
    if [ "$_ls_w" = 2 ]; then _ls_why="השיכפול נעצר באמצע מחיצה $_ls_i — היעד אינו שמיש"
    elif [ "$_ls_w" = 1 ]; then _ls_why="מחיצה $_ls_i: המקור לא התקדם $LC_STALL_S שניות"
    elif [ "$_ls_src_rc" != 0 ]; then _ls_why="מחיצה $_ls_i: קריאת המקור נכשלה (partclone rc=${_ls_src_rc:-לא נרשם}) — המקור לא שונה"
    elif ! _lc_hash_ok "$_ls_sha"; then _ls_why="מחיצה $_ls_i: ה-xxh64 של המקור לא חושב — אין ראיה למה שנקרא"
    fi
    if [ -n "$_ls_why" ]; then
        _fail_alive "$*" "$_ls_why"; echo "$_ls_i|failed" > "$_ls_r/cur"
        [ "$_ls_w" = 2 ] && return 2
        return 1
    fi
    echo "$_ls_i|$_ls_sha" >> "$_ls_r/source.sha"
    for _ls_t in "$@"; do lc_judge "$_ls_t" "$_ls_i" "$_ls_sha"; done
    echo "$_ls_i|done" > "$_ls_r/cur"
    _any_alive "$*"
}

lc_judge() {
    # $1 = target, $2 = idx, $3 = the source's xxh64. The four records.
    _lj_d="$RUN_DIR/targets/$1"
    [ "$(cat "$_lj_d/state")" = failed ] && return 0
    _lj_line=$(grep -F "$_lj_d/feed " "$LC/run/fanout.out" 2>/dev/null)
    _lj_rc=$(cat "$_lj_d/pipeline.rc" 2>/dev/null); _lj_tee=$(cat "$_lj_d/tee.rc" 2>/dev/null)
    _lj_sha=$(awk '{print $1}' "$_lj_d/sha.out" 2>/dev/null)
    _lj_at=$(awk -v b="$(target_bytes "$1")" 'BEGIN { printf "%.1f GB", b / 1e9 }')
    _lj_log=$(tail -n 1 "$_lj_d/partclone.log" 2>/dev/null | tr -d '\r')
    case "$_lj_line" in
        *" ok") ;;
        *" failed "*) fail_written_target "$1" "מחיצה $2: שגיאת כתיבה אחרי $_lj_at$LC_RLM — ${_lj_line#* failed }${_lj_log:+ · $_lj_log}"; return 0 ;;
        *) fail_written_target "$1" "מחיצה $2: אין דיווח מהפיצול לאחר $_lj_at$LC_RLM — אין ראיה שנכתב"; return 0 ;;
    esac
    if [ "$_lj_rc" != 0 ] || [ "$_lj_tee" != 0 ]; then
        fail_written_target "$1" "מחיצה $2: הכתיבה נכשלה אחרי $_lj_at$LC_RLM (partclone rc=${_lj_rc:-לא נרשם}, ממסר rc=${_lj_tee:-לא נרשם})${_lj_log:+ · $_lj_log}"
    elif ! _lc_hash_ok "$_lj_sha"; then
        fail_written_target "$1" "מחיצה $2: ה-xxh64 של מה שהיעד קיבל לא חושב — אין ראיה"
    elif [ "$_lj_sha" != "$3" ]; then
        fail_written_target "$1" "מחיצה $2: xxh64 של מה שהיעד קיבל ($(printf '%.12s' "$_lj_sha")) שונה מהמקור ($(printf '%.12s' "$3"))"
    else
        target_partition_done "$1"; echo "$2" >> "$_lj_d/lc_ok"
    fi
}

lc_copy() {
    # $1 = manifest, $2 = source, $3.. = targets. 0 = every partition was
    # streamed (some targets may have failed on the way), 1 = no target
    # left, 2 = stopped.
    _lk_m="$1"; _lk_src="$2"; shift 2
    manifest_plan "$_lk_m" > "$LC/run/copy.plan" || { _fail_alive "$*" "לא הצלחנו לקרוא את תוכנית המחיצות"; return 1; }
    while IFS='|' read -r _lk_i _lk_g _lk_r _lk_fs _lk_s _lk_z _lk_f _lk_sha _lk_x _lk_u _lk_uuid _lk_a <&3; do
        [ -f "$LC/stop.req" ] && { _fail_alive "$*" "השיכפול נעצר לפני מחיצה $_lk_i — היעד אינו שמיש"; return 2; }
        if is_swap_partition "$_lk_fs"; then
            for _lk_t in "$@"; do
                [ "$(cat "$RUN_DIR/targets/$_lk_t/state")" = failed ] && continue
                if make_swap "$(partition_node "$_lk_t" "$_lk_i")" "$_lk_uuid"; then echo "$_lk_i" >> "$RUN_DIR/targets/$_lk_t/lc_ok"
                else fail_written_target "$_lk_t" "מחיצה $_lk_i: mkswap נכשל"; fi
            done
            echo "$_lk_i|done" >> "$LC/run/swaps"
        else
            log "local clone: partition $_lk_i ($_lk_r, $_lk_fs) -> $*"
            lc_stream "$_lk_i" "$_lk_fs" "$_lk_src" "$@"; _lk_rc=$?
            [ "$_lk_rc" = 2 ] && return 2
        fi
        _any_alive "$*" || return 1
    done 3< "$LC/run/copy.plan"
}

lc_finish() {
    # $1 = manifest, $2.. = targets. The table read back, then the grown
    # filesystem follows its partition (finish_grow, best-effort as in a round).
    _lf_m="$1"; shift
    for _lf_t in "$@"; do
        [ "$(cat "$RUN_DIR/targets/$_lf_t/state")" = failed ] && continue
        target_set "$_lf_t" verifying
        if ! lc_table_check "$_lf_t" "$_lf_m"; then
            fail_written_target "$_lf_t" "טבלת המחיצות שנקראה בחזרה מהיעד אינה זהה למקור: $LC_ERROR"
            continue
        fi
        finish_grow "$_lf_t"
    done
}
