# lcgui.sh -- #1322: the local clone's side of the kiosk -- the state records
# the native screen draws (boot choice, drawers and verdicts, progress,
# result) and the clicks it sends back. Runs in imagectl-kiosk; the agent
# sources it only for the load gate. POSIX sh (busybox ash).
#
# The screen decides nothing: every click becomes a request file in $LC for
# the controller (localclone.sh), which checks it again against fresh
# evidence before a byte is written. A click this file cannot validate is
# refused here, and refused is never "go ahead" (principle 5).

lc_gui_active() { [ -f "$LC/screen" ]; }

_lc_part_label() {
    case "$1" in esp) echo EFI ;; msr) echo MSR ;; windows) echo Windows ;; recovery) echo Recovery ;;
        linux) echo Linux ;; swap) echo swap ;; *) echo "נתונים" ;; esac
}

lc_gui_state() {
    # Written by the kiosk every second instead of cloner_gui_state while
    # $LC/screen exists. One file, renamed into place: never half a picture.
    _gs_n="$GUI_DIR/state.next"; _gs_scr=$(cat "$LC/screen" 2>/dev/null)
    { printf 'lc_screen=%s\n' "$_gs_scr"; gui_header_state "$RUN_DIR/response.json";
        gui_hello_state || :; } > "$_gs_n" || return 1
    # #1367: the round on the server as the agent's beat last heard it
    # (attended.sh). Only "open" draws anything; empty = not known.
    printf 'lc_round=%s\n' "$(cat "$RUN_DIR/attended.round" 2>/dev/null)" >> "$_gs_n" || return 1
    if [ "$_gs_scr" = boot ]; then
        IFS='|' read -r _gs_dl _gs_p < "$LC/boot" 2>/dev/null || { _gs_dl=0; _gs_p=0; }
        printf 'lc_boot=%s|%s|%s\n' "${_gs_dl:-0}" "${_gs_p:-0}" "$(date +%s)" >> "$_gs_n"
        # 16/10: 1 = the local clone card is offered (two disks or more); else not
        printf 'lc_local=%s\n' "$(cat "$LC/boot.local" 2>/dev/null)" >> "$_gs_n"
    elif [ "$_gs_scr" = pull ]; then
        lc_pull_state >> "$_gs_n" || return 1   # 16/10: the restore screen (lcpull.sh)
    else
        printf 'lc_inv=%s\nlc_phase=%s\n' "$(cat "$LC/inv.state" 2>/dev/null)" "$(cat "$LC/phase" 2>/dev/null)" >> "$_gs_n"
        # by the drawer of the mapping, a disk not in it last, then by port (#867).
        # #1495: fields 14-15 = disk_drawer (slotnames.sh), "<drawer>|<connection>".
        drawer_slots_refresh || :
        for _gs_f in "$LC"/inv/*.line; do
            [ -f "$_gs_f" ] || continue
            IFS='|' read -r _gs_p _gs_d _gs_x < "$_gs_f"; _gs_m=$(disk_drawer "$_gs_d" "${_gs_p:-0}")
            printf '%s|%s|%s|%s\n' "$([ "${_gs_m%%|*}" = 0 ] && echo 1 || echo 0)" "${_gs_m%%|*}" "$(cat "$_gs_f")" "$_gs_m"
        done | sort -t'|' -k1,1n -k2,2n -k3,3n | sed 's/^[01]|[0-9]*|/lc_disk=/' >> "$_gs_n"
        _gs_src=$(cat "$LC/source" 2>/dev/null)
        if [ -n "$_gs_src" ]; then
            # #1443: + copy|why -- the bytes the copy moves (lcsize.sh); -1|why = not measured, empty = not yet
            printf 'lc_src=%s|%s|%s\n' "$_gs_src" "$(cut -d'|' -f1 "$LC/measure/$_gs_src" 2>/dev/null)" \
                "$(cat "$LC/measure/$_gs_src.copy" 2>/dev/null || echo '|')" >> "$_gs_n"
            for _gs_f in "$LC"/inv/*.line; do
                [ -f "$_gs_f" ] || continue
                _gs_d=$(basename "$_gs_f" .line); [ "$_gs_d" = "$_gs_src" ] && continue
                printf 'lc_fit=%s|%s\n' "$_gs_d" "$(lc_verdict_for "$_gs_src" "$_gs_d" | tr -d '\r\n')" >> "$_gs_n"
            done
        fi
        [ -d "$LC/run" ] && lc_gui_run >> "$_gs_n"
    fi
    printf 'updated=%s\n' "$(date +%s)" >> "$_gs_n" || return 1
    mv "$_gs_n" "$GUI_DIR/state"
}

lc_gui_run() {
    # The copy as records: lc_run (the whole), lc_part (each source partition),
    # lc_tgt (each target). Bytes are what was read from the source / what
    # fanout handed each target -- measured, never estimated.
    _gr_r="$LC/run"; _gr_now=$(date +%s)
    _gr_t0=$(cat "$_gr_r/started" 2>/dev/null); _gr_t1=$(cat "$_gr_r/ended" 2>/dev/null)
    case "$_gr_t0" in ''|*[!0-9]*) _gr_t0=$_gr_now ;; esac
    case "$_gr_t1" in ''|*[!0-9]*) _gr_t1=$_gr_now ;; esac
    # #1484: no counter file yet = not started, said as 0 -- not a read that failed.
    # #1619: one file per partition (bytes.<idx>, lccopy.sh), each read once and
    # only ever growing: the sum cannot count a finished partition twice.
    _gr_cp=0
    for _gr_f in "$_gr_r"/bytes.[0-9]*; do
        [ -f "$_gr_f" ] || continue   # no match leaves the pattern itself
        _gr_n=$(_last_number "$_gr_f"); _gr_cp=$((_gr_cp + ${_gr_n:-0}))
    done
    _gr_cur=$(cat "$_gr_r/cur" 2>/dev/null)
    # #1477: the shrink's path as lc_shrink_source wrote it (snapshot|refused);
    # anything else is empty -- the screen does not guess it.
    _gr_sp=$(cat "$_gr_r/shrink_path" 2>/dev/null); case "$_gr_sp" in snapshot|refused) ;; *) _gr_sp="" ;; esac
    printf 'lc_run=%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$(cat "$_gr_r/src" 2>/dev/null)" "$(_lc_num "$(cat "$_gr_r/total" 2>/dev/null)")" \
        "$_gr_cp" "$((_gr_t1 - _gr_t0))" "$([ -f "$_gr_r/shrunk" ] && echo 1 || echo 0)" \
        "$(cat "$_gr_r/report" 2>/dev/null | cut -d'|' -f1)" "${_gr_cur%%|*}" \
        "$(_lc_clean "$(cat "$_gr_r/source_restore" 2>/dev/null)")" "$_gr_sp"
    # #1483: what became of "כבה את המחשב" -- powering off, or why not
    [ -s "$_gr_r/notice" ] && printf 'toast=%s\n' "$(_lc_clean "$(cat "$_gr_r/notice")")"
    if [ -f "$_gr_r/manifest.json" ]; then
        # #1440: a used_bytes that was not measured is -1, said "לא נמדד" -- never the
        # partition's size under the word "בשימוש" (496 GB shown, 203 GB in use).
        jq -r '.partitions[] | "\(.index)|\(.role)|\(.fs)|\(if .fs == "swap" then 0 elif .used_bytes != null then .used_bytes else -1 end)|\(.size_bytes)"' \
            "$_gr_r/manifest.json" 2>/dev/null | while IFS='|' read -r _gr_i _gr_role _gr_fs _gr_b _gr_z; do
            _gr_st="wait"
            if cat "$_gr_r/source.sha" "$_gr_r/swaps" 2>/dev/null | awk -F'|' -v i="$_gr_i" '$1 == i { f = 1 } END { exit !f }'; then _gr_st="done"
            elif [ "$_gr_cur" = "$_gr_i|copy" ]; then _gr_st=copy
            elif [ "$_gr_cur" = "$_gr_i|failed" ]; then _gr_st=failed; fi
            _gr_in=0
            if [ "$_gr_st" = copy ] && [ -f "$_gr_r/bytes.$_gr_i" ]; then _gr_in=$(_last_number "$_gr_r/bytes.$_gr_i"); fi
            printf 'lc_part=%s|%s|%s|%s|%s|%s|%s\n' "$_gr_i" "$(_lc_part_label "$_gr_role")" "$_gr_fs" "$_gr_b" "$_gr_z" "$_gr_st" "${_gr_in:-0}"
        done
    fi
    for _gr_d in $(cat "$_gr_r/targets" 2>/dev/null); do
        _gr_t="$RUN_DIR/targets/$_gr_d"
        _gr_ok=$(awk 'END { print NR }' "$_gr_t/lc_ok" 2>/dev/null)
        _gr_nb=$(awk 'END { print NR }' "$_gr_t/ntfs_boot" 2>/dev/null)   # #1487: NTFS backup boot sectors read back
        printf 'lc_tgt=%s|%s|%s|%s|%s|%s|%s|%s|%s\n' "$_gr_d" "$(cat "$_gr_t/state" 2>/dev/null)" "$(target_bytes "$_gr_d")" \
            "$(cat "$_gr_t/lc_part" 2>/dev/null)" "${_gr_ok:-0}" "$(cat "$_gr_t/lc_table" 2>/dev/null | tr '|' ',')" \
            "$(_lc_clean "$(cat "$_gr_t/error" 2>/dev/null)")" "$(cat "$_gr_t/total" 2>/dev/null)" "${_gr_nb:-0}"
    done
}

_lc_req() { printf '%s\n' "$2" > "$LC/$1.next" && mv "$LC/$1.next" "$LC/$1"; }

_lc_dev_ok() {
    # $1 = a dev name from the screen: an inventoried drawer, nothing else.
    case "$1" in ''|*[!a-z0-9]*) return 1 ;; esac
    [ -f "$LC/inv/$1.line" ]
}

lc_gui_dispatch() {
    # $token from the screen. boot-server|boot-local|boot-pull|boot-pause,
    # lc-pull|<image>|<p1,p2> (lcpull.sh),
    # lc-measure|<dev>, lc-start|<src>|<t1,t2>|<shrink 0/1>, lc-stop,
    # lc-poweroff, lc-again, lc-back. 1 = refused.
    _gd_scr=$(cat "$LC/screen" 2>/dev/null); _gd_ph=$(cat "$LC/phase" 2>/dev/null)
    case "$token" in
        boot-server|boot-local|boot-pull|boot-pause)
            [ "$_gd_scr" = boot ] || return 1
            [ "$token" != boot-local ] || [ "$(cat "$LC/boot.local" 2>/dev/null)" = 1 ] || return 1
            _lc_req boot.decision "${token#boot-}" ;;
        "lc-pull|"*)
            [ "$_gd_scr" = pull ] || return 1
            IFS='|' read -r _gd_x _gd_i _gd_p <<EOF
$token
EOF
            case "$_gd_i" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            lc_pull_ports "$_gd_p" > /dev/null || return 1   # drawers of this machine, now
            _lc_req pull.req "$_gd_i|$_gd_p" ;;
        "lc-measure|"*)
            [ "$_gd_scr" = local ] && [ "$_gd_ph" = idle ] || return 1
            _gd_d=${token#lc-measure|}; _lc_dev_ok "$_gd_d" || return 1
            _lc_req measure.req "$_gd_d" ;;
        "lc-start|"*)
            [ "$_gd_scr" = local ] && [ "$_gd_ph" = idle ] || return 1
            IFS='|' read -r _gd_x _gd_s _gd_t _gd_k <<EOF
$token
EOF
            _lc_dev_ok "$_gd_s" || return 1
            case "$_gd_k" in 0|1) ;; *) return 1 ;; esac
            [ -n "$_gd_t" ] || return 1
            for _gd_d in $(printf '%s' "$_gd_t" | tr ',' ' '); do
                _lc_dev_ok "$_gd_d" && [ "$_gd_d" != "$_gd_s" ] || return 1
            done
            _lc_req start.req "$_gd_s|$_gd_t|$_gd_k" ;;
        lc-stop)
            case "$_gd_ph" in copying|shrinking) _lc_req stop.req stop ;; *) return 1 ;; esac ;;
        lc-poweroff|lc-again)
            case "$_gd_ph" in done|stopped) _lc_req end.req "${token#lc-}" ;;
                *) [ "$token" = lc-poweroff ] && [ -d "$LC/run" ] && \
                       _lc_req run/notice "המחשב לא כובה: השיכפול עוד לא הסתיים בצד הסוכן ($_gd_ph). נסו שוב בעוד רגע."   # #1483: aloud
                   return 1 ;; esac ;;
        lc-back)
            { [ "$_gd_scr" = local ] && [ "$_gd_ph" = idle ]; } || [ "$_gd_scr" = pull ] || return 1
            _lc_req end.req back ;;
        *) return 1 ;;
    esac
}
