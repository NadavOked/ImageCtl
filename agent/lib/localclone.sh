# localclone.sh -- #1322: a cloning machine booted from the network asks,
# once, "from the server or local clone?", and the local flow copies one of
# its disks to the others with no server. The controller; the read-only
# inventory is lcinv.sh, the plan lcplan.sh, the copy lccopy.sh, and the
# screen's side (state records, the clicks) lcgui.sh. POSIX sh (busybox ash).
#
# Unattended behaviour does not change (CLAUDE.md QA, #17): with no touch for
# LC_BOOT_S the choice is "from the server" and the machine goes on to the
# standby screen exactly as before. "Not chosen" is the server, never local
# (principle 1): a GUI that dies, a decision that cannot be read, a single
# disk, no GUI at all -- all of them are the server. Nothing is written to a
# disk before the confirm dialog's red button (principle 7), and the kernel
# command line is not read for any of it (principle 2).
#
# The GUI talks through files in $LC, one per question, written by the
# kiosk's bridge (lcgui.sh) and consumed here: boot.decision (server|local|
# pause), measure.req (<dev>), start.req (<src>|<t1,t2>|<shrink 0/1>),
# stop.req, end.req (poweroff|again|back).

LC_BOOT_S="${LC_BOOT_S:-20}"

lc_gui_alive() { [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null; }

local_clone_arm() {
    # Before the GUI starts on the first idle hello: the boot screen is the
    # first thing it draws, never a flash of the standby screen. Only a cloner,
    # only once a boot, only with two disks or more (owner: one disk = nothing
    # to copy to, the choice is not shown).
    [ "$D_ROLE" = cloner ] && [ -z "${_lc_armed:-}" ] || return 0
    _lc_armed=1
    [ "$(list_drawers | wc -l)" -ge 2 ] || { log "local clone: fewer than two disks -- no boot choice"; return 0; }
    mkdir -p "$LC" || return 0
    rm -f "$LC/boot.decision" "$LC/measure.req" "$LC/start.req" "$LC/stop.req" "$LC/end.req"
    echo "0|0" > "$LC/boot"; echo boot > "$LC/screen"
}

local_clone_offer() {
    # After gui_cloner_parent. Returns when the machine goes on from the server.
    [ -f "$LC/screen" ] || return 0
    if ! lc_gui_alive; then
        log "local clone: the GUI is not up -- from the server, as before"
        rm -f "$LC/screen"; return 0
    fi
    LC_PAUSED=0
    while :; do
        lc_boot_choice
        [ "$LC_CHOICE" = local ] || break
        lc_session
        [ "$LC_END" = back ] || break
        LC_PAUSED=1      # back from the source screen: the choice, not counting
    done
    rm -f "$LC/screen" "$LC/boot"
    log "local clone: going on from the server"
}

lc_boot_choice() {
    # Sets LC_CHOICE (server|local). The countdown is the agent's: the GUI
    # only draws the deadline written here, so a frozen screen cannot hold
    # the machine -- it is the server when the time is up.
    LC_CHOICE=server
    rm -f "$LC/boot.decision"
    if [ "$LC_PAUSED" = 1 ]; then echo "0|1" > "$LC/boot"
    else _bc_dl=$(( $(date +%s) + LC_BOOT_S )); echo "$_bc_dl|0" > "$LC/boot"; fi
    echo boot > "$LC/screen"
    log "local clone: boot choice on screen (${LC_BOOT_S}s to the server unless touched)"
    while :; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_gui_alive || { log "local clone: the GUI is gone -- from the server"; return 0; }
        _bc_d=""; [ -f "$LC/boot.decision" ] && { _bc_d=$(cat "$LC/boot.decision"); rm -f "$LC/boot.decision"; }
        case "$_bc_d" in
            server) log "local clone: 'from the server' chosen"; return 0 ;;
            local) log "local clone: 'local clone' chosen"; LC_CHOICE=local; return 0 ;;
            pause) [ "$LC_PAUSED" = 1 ] || log "local clone: touched -- the countdown stops"
                   LC_PAUSED=1; echo "0|1" > "$LC/boot" ;;
        esac
        if [ "$LC_PAUSED" != 1 ] && [ "$(date +%s)" -ge "$_bc_dl" ]; then
            log "local clone: no touch for ${LC_BOOT_S}s -- from the server"; return 0
        fi
        sleep 1
    done
}

lc_session() {
    # The local flow until the person leaves it. Sets LC_END (back), or does
    # not return (power off).
    LC_END=""
    rm -rf "$LC/run"; rm -f "$LC/measure.req" "$LC/start.req" "$LC/stop.req" "$LC/end.req"
    echo idle > "$LC/phase"; echo local > "$LC/screen"
    lc_inventory
    while :; do
        if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi
        lc_gui_alive || { log "local clone: the GUI is gone -- leaving the local flow"; LC_END=back; return 0; }
        if [ -f "$LC/measure.req" ]; then
            _ss_m=$(cat "$LC/measure.req"); rm -f "$LC/measure.req"
            if [ -f "$LC/inv/$_ss_m.line" ]; then echo "$_ss_m" > "$LC/source"; lc_measure "$_ss_m"; fi
        fi
        if [ -f "$LC/start.req" ]; then
            IFS='|' read -r _ss_s _ss_t _ss_k < "$LC/start.req"; rm -f "$LC/start.req"
            lc_run "$_ss_s" "$(printf '%s' "$_ss_t" | tr ',' ' ')" "$_ss_k"
            # stopped (the mockup: back to choosing): the drawers are read again --
            # what is on the targets now is half a copy, and the screen must say so
            if [ "$(cat "$LC/phase")" = stopped ]; then
                rm -rf "$LC/run"; echo idle > "$LC/phase"; lc_inventory
                _ss_m=$(cat "$LC/source" 2>/dev/null)   # the screen keeps its choice: measure it again
                [ -f "$LC/inv/$_ss_m.line" ] && lc_measure "$_ss_m"
            fi
        fi
        if [ -f "$LC/end.req" ]; then
            _ss_e=$(cat "$LC/end.req"); rm -f "$LC/end.req"
            case "$_ss_e" in
                poweroff) lc_poweroff ;;
                again) rm -rf "$LC/run"; rm -f "$LC/stop.req"; echo idle > "$LC/phase"; lc_inventory ;;
                back) LC_END=back; rm -rf "$LC/run"; echo idle > "$LC/phase"; return 0 ;;
            esac
        fi
        sleep 1
    done
}

lc_run() {
    # $1 = source, $2 = targets (words), $3 = 1 when the confirm dialog showed
    # and the person accepted a temporary shrink of the source.
    _lr_src="$1"; _lr_k="$3"; _lr_r="$LC/run"
    rm -rf "$_lr_r" "$LC/stop.req"; mkdir -p "$_lr_r"; : > "$_lr_r/targets"
    # The cloner's dead-man (watchdog.sh, 600 s) must not reboot the machine
    # in the middle of ntfsresize on the SOURCE: shrinking a big volume or
    # growing it back has no byte counter to beat on. A person started this
    # and is at the machine; the copy's own waits still end a stalled stream.
    ( while :; do if command -v watchdog_beat >/dev/null 2>&1; then watchdog_beat; fi; sleep 20; done ) < /dev/null > /dev/null 2>&1 &
    _lr_beat=$!
    echo copying > "$LC/phase"; date +%s > "$_lr_r/started"; echo "$_lr_src" > "$_lr_r/src"
    for _lr_t in $2; do target_init "$_lr_t" 0; rm -f "$RUN_DIR/targets/$_lr_t/lc_ok" "$RUN_DIR/targets/$_lr_t/lc_table" "$RUN_DIR/targets/$_lr_t/lc_part"; echo "$_lr_t" >> "$_lr_r/targets"; done
    _lr_all=$(tr '\n' ' ' < "$_lr_r/targets"); _lr_small=""
    log "local clone: $_lr_src -> $_lr_all"
    # Fresh evidence, not the screen's 2-second-old picture (principle 5/7).
    if [ "$(lc_inv_field "$_lr_src" 12)" != 1 ] || [ "$(_lc_clean "$(disk_serial "$_lr_src")")" != "$(lc_inv_field "$_lr_src" 4)" ]; then
        _fail_alive "$_lr_all" "דיסק המקור אינו מה שנבדק — חזרו ובחרו שוב. לא נכתב דבר"; lc_end; return 1
    fi
    [ -f "$LC/measure/$_lr_src" ] && [ "$(cat "$LC/source" 2>/dev/null)" = "$_lr_src" ] || lc_measure "$_lr_src"
    for _lr_t in $_lr_all; do
        if [ "$_lr_t" = "$_lr_src" ] || [ ! -f "$LC/inv/$_lr_t.line" ] || [ "$(_lc_clean "$(disk_serial "$_lr_t")")" != "$(lc_inv_field "$_lr_t" 4)" ]; then
            fail_written_target "$_lr_t" "היעד אינו מה שנבדק — לא נכתב"; continue
        fi
        IFS='|' read -r _lr_v _lr_n _lr_w <<EOF
$(lc_verdict_for "$_lr_src" "$_lr_t")
EOF
        case "$_lr_v" in
            fits) ;;
            shrink) if [ "$_lr_k" = 1 ]; then _lr_small="$_lr_small $_lr_t"
                    else fail_written_target "$_lr_t" "נדרש כיווץ זמני של המקור, וחלון האישור לא הציג אותו — לא נכתב"; fi ;;
            short) fail_written_target "$_lr_t" "לא נכנס: חסרים $(lc_gb_short "$_lr_n" "$(lc_inv_field "$_lr_t" 5)") — לא נכתב" ;;
            *) fail_written_target "$_lr_t" "לא נבדק אם נכנס${_lr_w:+: $_lr_w} — לא נכתב" ;;
        esac
    done
    _any_alive "$_lr_all" || { lc_end; return 1; }
    lc_source_parts "$_lr_src" || { _fail_alive "$_lr_all" "$LC_ERROR — לא נכתב דבר"; lc_end; return 1; }
    rm -f "$RUN_DIR/parts.orig.txt" "$RUN_DIR/targets/$_lr_src/shrunk"
    if [ -n "$_lr_small" ]; then
        echo shrinking > "$LC/phase"
        if lc_shrink_source "$_lr_src"; then echo 1 > "$_lr_r/shrunk"
        else for _lr_t in $_lr_small; do fail_written_target "$_lr_t" "המקור לא כווץ, ולכן היעד לא נכתב: $LC_ERROR"; done; fi
        echo copying > "$LC/phase"
    fi
    _any_alive "$_lr_all" || { lc_end; return 1; }
    lc_manifest "$_lr_src" "$_lr_r/parts.txt" || { _fail_alive "$_lr_all" "${LC_ERROR:-לא הצלחנו לתאר את המקור}"; lc_end; return 1; }
    jq -r '[.partitions[] | if .fs == "swap" then 0 elif .used_bytes != null then .used_bytes else .size_bytes end] | add' \
        "$_lr_r/manifest.json" > "$_lr_r/total" 2>> "$LOG_FILE"
    for _lr_t in $_lr_all; do
        [ "$(cat "$RUN_DIR/targets/$_lr_t/state")" = failed ] && continue
        cp "$_lr_r/total" "$RUN_DIR/targets/$_lr_t/total"
        lc_prepare_target "$_lr_t" "$_lr_r/manifest.json"
    done
    # shellcheck disable=SC2086 # the target list is a word list on purpose
    lc_copy "$_lr_r/manifest.json" "$_lr_src" $_lr_all; _lr_rc=$?
    # shellcheck disable=SC2086
    [ "$_lr_rc" = 2 ] || { echo verifying > "$LC/phase"; lc_finish "$_lr_r/manifest.json" $_lr_all; }
    [ "$_lr_rc" = 2 ] && echo 1 > "$_lr_r/stopped"
    lc_end
}

lc_end() {
    # Whatever happened: the source back to its size, the result, the report.
    if [ -f "$RUN_DIR/targets/$_lr_src/shrunk" ]; then
        echo restoring > "$LC/phase"
        if shrink_restore_source "$_lr_src"; then echo ok > "$_lr_r/source_restore"
        else printf 'הנתונים במקור שלמים, אבל הוא נשאר קטן מגודלו המקורי: %s\n' \
                 "$(_lc_clean "$(cat "$RUN_DIR/targets/$_lr_src/error" 2>/dev/null)")" > "$_lr_r/source_restore"; fi
    fi
    date +%s > "$_lr_r/ended"
    if [ -f "$_lr_r/stopped" ]; then echo stopped > "$LC/phase"; else echo "done" > "$LC/phase"; fi
    lc_report
    kill "$_lr_beat" 2>/dev/null; wait_pid "$_lr_beat" "$WAIT_HELPER_S" "local clone watchdog beat"
}

lc_gb_short() { awk -v n="$1" -v h="$2" 'BEGIN { printf "%d GB", int((n - h + 999999999) / 1e9) }'; }

lc_report() {
    # #1322 D: best effort, labelled -- the copy did not pass through the
    # server, the machine reports what it did when the server answers.
    # Nothing here can change the result on the screen.
    _rp_r="$LC/run"; _rp_t=""
    for _rp_d in $(cat "$_rp_r/targets" 2>/dev/null); do
        _rp_s=$(cat "$RUN_DIR/targets/$_rp_d/state" 2>/dev/null); _rp_e=$(cat "$RUN_DIR/targets/$_rp_d/error" 2>/dev/null)
        _rp_t="$_rp_t${_rp_t:+,}{\"dev\":\"$_rp_d\",\"port\":$(_lc_num "$(disk_port "$_rp_d")"),\"serial\":\"$(json_escape "$(disk_serial "$_rp_d")")\",\"state\":\"$_rp_s\",\"error\":\"$(json_escape "$_rp_e")\"}"
    done
    printf '{"mac":"%s","source":{"dev":"%s","port":%s,"serial":"%s"},"targets":[%s],"shrunk":%s,"source_restore":"%s","stopped":%s,"bytes":%s,"elapsed_s":%s}\n' \
        "${MAC:-}" "$_lr_src" "$(_lc_num "$(disk_port "$_lr_src")")" "$(json_escape "$(disk_serial "$_lr_src")")" "$_rp_t" \
        "$([ -f "$_rp_r/shrunk" ] && echo true || echo false)" "$(json_escape "$(cat "$_rp_r/source_restore" 2>/dev/null)")" \
        "$([ -f "$_rp_r/stopped" ] && echo true || echo false)" "$(_lc_num "$(cat "$_rp_r/total" 2>/dev/null)")" \
        "$(( $(cat "$_rp_r/ended") - $(cat "$_rp_r/started") ))" > "$_rp_r/report.json"
    _rp_c=$(curl -s -o "$_rp_r/report.resp" -w '%{http_code}' --max-time 10 -H "Content-Type: application/json" \
        --data-binary "@$_rp_r/report.json" "${SERVER:-http://0.0.0.0}/api/v1/agent/local-clone" 2>/dev/null)
    if [ "$_rp_c" = 200 ]; then echo sent > "$_rp_r/report"; log "local clone: reported to the server journal"
    else echo "failed|${_rp_c:-000}" > "$_rp_r/report"; log "local clone: the server did not take the report (http ${_rp_c:-000}) -- the copy stands"; fi
}

lc_poweroff() {
    # "כבה את המחשב": the person asked. Wake-on-LAN first when it can be
    # armed (#587: a remote power-on needs it), then off -- by request.
    log "local clone: powering off at the operator's request"
    arm_wol || log "local clone: wake-on-LAN not armed -- powering off anyway (asked at the machine)"
    [ "${IMAGECTL_TEST:-0}" = 1 ] && { echo "TEST-POWEROFF"; exit 0; }
    sync; poweroff -f
}
