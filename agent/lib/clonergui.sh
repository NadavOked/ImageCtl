# clonergui.sh -- cloner GUI selection and locally authoritative state.
# POSIX sh (busybox ash).
#
# The cloning machine now has a screen (owner decision): it shows per-drawer
# progress and a graphical SMART choice, replacing the text menu of #666 when
# the GUI is up. Two rules never bend:
#   * A display that will not draw NEVER stops the write. gui_cloner_parent
#     always returns 0 -- do_restore_drawers must run headless all the same.
#   * A display failure is NEVER an approval to write (principle 5). If the
#     GUI vanishes while a SMART choice is pending, gui_smart_choice fails and
#     the text menu takes over, where EOF still means "skip".

# Both processes agree on one directory: the agent (gui_smart_choice) and the
# kiosk (cloner_gui_state) exchange smart-request/smart-decision here, and it
# is the same $RUN_DIR/gui that guiparent.sh and gui_main use.
GUI_DIR="${GUI_DIR:-$RUN_DIR/gui}"

gui_cloner_parent() {
    [ "$D_ROLE" = cloner ] || return 0
    gui_parent || {
        [ "${_cloner_gui_failed_logged:-0}" = 1 ] ||
            log 'cloner: native GUI unavailable -- continuing with text display'
        _cloner_gui_failed_logged=1
        return 0
    }
}

gui_restore_parent() {
    case "$D_ROLE" in
        classroom)
            gui_parent || log 'restore: using text display'
            ;;
        cloner)
            gui_cloner_parent
            ;;
    esac
}

# #410: rate and ETA of one drawer, from the local counter (the same truth
# the record is built from). Sets _cg_rate (bytes/s) and _cg_eta (seconds);
# -1 = not measured -- never 0, which would read "stopped" (rule 5). The
# rate is the average since the first sample this kiosk saw with bytes on
# the drawer ($GUI_DIR/pace.<dev> = "epoch bytes"); a drawer that is not
# writing drops its anchor so the next wave starts a new average.
cloner_gui_pace() {
    # $1 = dev, $2 = state, $3 = bytes, $4 = total
    _cg_rate=-1; _cg_eta=-1
    if [ "$2" != writing ] || [ "$3" -le 0 ]; then
        rm -f "$GUI_DIR/pace.$1"; return 0
    fi
    _cg_now=$(date +%s)
    _cg_t0=; _cg_b0=
    [ -r "$GUI_DIR/pace.$1" ] && read -r _cg_t0 _cg_b0 < "$GUI_DIR/pace.$1"
    case "$_cg_t0:$_cg_b0" in
        *[!0-9:]*|:*|*:) printf '%s %s\n' "$_cg_now" "$3" > "$GUI_DIR/pace.$1"; return 0 ;;
    esac
    _cg_dt=$((_cg_now - _cg_t0)); _cg_db=$(($3 - _cg_b0))
    [ "$_cg_dt" -gt 0 ] && [ "$_cg_db" -gt 0 ] || return 0
    _cg_rate=$((_cg_db / _cg_dt))
    [ "$_cg_rate" -gt 0 ] || { _cg_rate=-1; return 0; }
    [ "$4" -gt "$3" ] && _cg_eta=$((($4 - $3) / _cg_rate))
    return 0
}

cloner_gui_state() {
    # Written by the kiosk process. The per-drawer files are the local source
    # of truth (progress.sh), so the screen keeps updating even when a server
    # report fails. Sorted/labelled by port happens in the native screen.
    _cg_next="$GUI_DIR/state.next"
    : > "$_cg_next" || return 1

    _cg_name=
    [ -f "$RUN_DIR/manifest.json" ] &&
        _cg_name=$(json_get "$RUN_DIR/manifest.json" ".name")
    case "$_cg_name" in null) _cg_name= ;; esac
    printf 'cloner=%s\n' "$_cg_name" >> "$_cg_next" || return 1

    _cg_any=0
    for _cg_dir in "$RUN_DIR"/targets/*/; do
        [ -d "$_cg_dir" ] || continue
        _cg_any=1
        _cg_dev=$(basename "$_cg_dir")
        _cg_port=$(disk_port "$_cg_dev")
        _cg_state=$(cat "$_cg_dir/state" 2>/dev/null)
        _cg_total=$(cat "$_cg_dir/total" 2>/dev/null)
        _cg_bytes=$(target_bytes "$_cg_dev")
        _cg_error=
        [ -f "$_cg_dir/error" ] && _cg_error=$(cat "$_cg_dir/error")

        case "$_cg_port" in ''|*[!0-9]*) _cg_port=0 ;; esac
        case "$_cg_bytes" in ''|*[!0-9]*) _cg_bytes=0 ;; esac
        case "$_cg_total" in ''|*[!0-9]*) _cg_total=0 ;; esac

        # A field separator or newline inside the error would corrupt the
        # record; fold them to spaces before the value reaches the parser.
        _cg_error=$(printf '%s' "$_cg_error" | tr '\r\n|' '   ')
        # #410: fields 7-8 are rate (bytes/s) and ETA (s); -1 = not measured.
        cloner_gui_pace "$_cg_dev" "$_cg_state" "$_cg_bytes" "$_cg_total"
        printf 'drawer=%s|%s|%s|%s|%s|%s|%s|%s\n' \
            "$_cg_port" "$_cg_dev" "$_cg_state" \
            "$_cg_bytes" "$_cg_total" "$_cg_error" \
            "$_cg_rate" "$_cg_eta" >> "$_cg_next" ||
            return 1
    done

    # Idle -- no round targets yet: list the physically connected disks so the
    # screen shows what is plugged in (count, slot, brand, size), not a bare
    # "waiting". The record is cdisk=port|dev|model|size|smart|cause -- NOT "disk=", which
    # the build machine already uses for its local disk list (#688).
    # ‏#867: ממוין לפי מספר הדיסק (השדה הראשון), לא לפי סדר הגילוי של
    # list_disks -- במחשב 2 sda הוא דיסק 3, והמסך הציג "3, 1, 2". השורות
    # נאספות בלי הקידומת, עם דגל "אין חריץ" לפניהן (port 0 מגיע אחרון ולא
    # ראשון), ממוינות לקובץ, ורק אז מקבלות cdisk=.
    if [ "$_cg_any" = 0 ]; then
        _cg_idle="$GUI_DIR/cdisk.unsorted"
        : > "$_cg_idle" || return 1
        disk_failures_refresh || :   # #874: זיכרון השרת מה-hello האחרון; כשל = הקובץ הקודם
        for _cg_d in $(list_disks); do
            _cg_dport=$(disk_port "$_cg_d")
            case "$_cg_dport" in ''|*[!0-9]*) _cg_dport=0 ;; esac
            _cg_last=0; [ "$_cg_dport" = 0 ] && _cg_last=1
            _cg_sect=$(cat "$SYSROOT/sys/block/$_cg_d/size" 2>/dev/null)
            case "$_cg_sect" in ''|*[!0-9]*) _cg_sect=0 ;; esac
            _cg_dsize=$((_cg_sect * 512))
            _cg_dmodel=$(trim "$(cat "$SYSROOT/sys/block/$_cg_d/device/model" 2>/dev/null)")
            _cg_dmodel=$(printf '%s' "$_cg_dmodel" | tr '\r\n|' '   ')
            # #709: cached SMART verdict (never probes), ok->passed for the GUI.
            _cg_dsmart=$(smart_hello_field "$_cg_d")
            case "$_cg_dsmart" in ok) _cg_dsmart=passed ;; warn|fail|passed) ;; *) _cg_dsmart=unchecked ;; esac
            # ‏#867/#874: זיכרון השרת גובר על SMART -- דיסק שכשל בכתיבה (לפי סידורי
            # או חריץ) עובר SMART. השדה השישי הוא הסיבה שהשרת סיווג (cable/disk),
            # ריק כשירוק. ‏disk_failure_cause יושב ב-failmark.sh: גם השער קורא אותו.
            _cg_dcause=$(disk_failure_cause "$_cg_d") && _cg_dsmart=failed_last
            printf '%s|%s|%s|%s|%s|%s|%s\n' "$_cg_last" "$_cg_dport" "$_cg_d" \
                "$_cg_dmodel" "$_cg_dsize" "$_cg_dsmart" "$_cg_dcause" >> "$_cg_idle" || return 1
        done
        sort -t'|' -k1,1n -k2,2n "$_cg_idle" > "$_cg_idle.sorted" || return 1
        sed 's/^[01]|/cdisk=/' "$_cg_idle.sorted" >> "$_cg_next" || return 1
    fi

    if [ -f "$GUI_DIR/smart-request" ]; then
        # ‏smart-request הוא nonce|port|verdict|reason. ה-nonce **זורם
        # ל-GUI** ומצויר עם ה-prompt, כדי שהקליק יחזיר אותו: הוא הזהות
        # הבלתי-משתנה של הבקשה שצוירה. בלי זה קליק מושהה על אותו port
        # (דיסק שנתבקש שוב, nonce חדש) היה מאושר לבקשה הלא-נכונה.
        IFS='|' read -r _cg_nonce _cg_sport _cg_verdict _cg_reason \
            < "$GUI_DIR/smart-request" || return 1
        printf 'smart_prompt=%s|%s|%s|%s\n' \
            "$_cg_nonce" "$_cg_sport" "$_cg_verdict" "$_cg_reason" >> "$_cg_next" ||
            return 1
    fi

    # #410: when this snapshot was taken -- the screen shows its age.
    printf 'updated=%s\n' "$(date +%s)" >> "$_cg_next" || return 1
    mv "$_cg_next" "$GUI_DIR/state"
}

cloner_gui_state_loop() {
    while :; do
        if ! cloner_gui_state; then
            printf 'native-gui: cloner state could not be verified\n' >&2
        fi
        sleep 1
    done
}

gui_smart_choice() {
    # $1=disk, $2=verdict, $3=reason. stdout is the decision (replace/rescue/
    # skip). Returns non-zero when the GUI cannot answer -- the caller then
    # falls back to the text menu. A display failure is never an approval.
    #
    # ‏**כל בקשה נושאת nonce ייחודי**, וההחלטה מתקבלת רק אם היא כבולה
    # לאותו nonce. בלי זה קליק מושהה/כפול על דיסק אחד היה מאושר לדיסק
    # הבא -- ובמקרה הגרוע `rescue` ישן היה מאשר כתיבה לדיסק החשוד הלא-
    # נכון, בדיוק מה ש-SMART נועד למנוע (עיקרון 5/7). ה-nonce מגובה-קובץ
    # מונוטוני, כי הפונקציה רצה בתת-מעטפת של `$( )` ומשתנה מקומי לא היה
    # שורד בין דיסקים.
    [ "$D_ROLE" = cloner ] || return 1
    [ -n "${_gui_pid:-}" ] && kill -0 "$_gui_pid" 2>/dev/null || return 1

    _gsc_port=$(disk_port "$1")
    case "$_gsc_port" in ''|*[!0-9]*) return 1 ;; esac

    _gsc_seq=$(cat "$GUI_DIR/smart-seq" 2>/dev/null)
    case "$_gsc_seq" in ''|*[!0-9]*) _gsc_seq=0 ;; esac
    _gsc_seq=$((_gsc_seq + 1))
    echo "$_gsc_seq" > "$GUI_DIR/smart-seq" || return 1
    _gsc_nonce="$_gsc_seq-$_gsc_port"

    rm -f "$GUI_DIR/smart-decision"
    printf '%s|%s|%s|%s\n' "$_gsc_nonce" "$_gsc_port" "$2" "$3" \
        > "$GUI_DIR/smart-request.next" || return 1
    mv "$GUI_DIR/smart-request.next" "$GUI_DIR/smart-request" || return 1

    while kill -0 "$_gui_pid" 2>/dev/null; do
        if [ -s "$GUI_DIR/smart-decision" ]; then
            IFS='|' read -r _gsc_dnonce _gsc_dec < "$GUI_DIR/smart-decision" || {
                rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
                return 1
            }
            # רק החלטה שכבולה ל-nonce של הבקשה הזאת, ורק ערך חוקי. החלטה
            # ישנה/לא-תואמת/פגומה נמחקת וההמתנה נמשכת -- לא משוחררת.
            if [ "$_gsc_dnonce" = "$_gsc_nonce" ]; then
                case "$_gsc_dec" in
                    replace|rescue|skip)
                        rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
                        printf '%s\n' "$_gsc_dec"
                        return 0
                        ;;
                esac
            fi
            rm -f "$GUI_DIR/smart-decision"
        fi
        sleep 1
    done

    # The GUI died with no decision: clear the handshake and fail. The caller
    # writes the disk only after a positive choice, never on this path.
    rm -f "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
    return 1
}

# ‏#695: קובע את $_disks לרשימת המגירות שנבחרו לסבב. סבב עם בחירה
# (‏session.target_ports הוא מערך) כותב **רק** לפורטים האלה, וכל פורט
# חייב למפות לדיוק דיסק אחד — אחרת die_local, בלי נפילה ל"כל הדיסקים"
# (עיקרון 4/5/7). סבב ישן (null) שומר על ההתנהגות הישנה (כל המגירות).
# מריץ בשל המעטפת של הסוכן (לא בתת-מעטפת), כדי ש-die_local יעצור באמת.
resolve_target_drawers() {
    _rt_ports=$(json_get "$RESP" ".session.target_ports")
    if [ "$_rt_ports" = "null" ]; then
        _disks=$(list_drawers)
        return 0
    fi
    _disks=""
    _rt_list=$(jq -er '.session.target_ports
        | select(type == "array" and length > 0)
        | .[] | select(type == "number" and floor == .)' "$RESP") \
        || die_local "room target selection is missing or malformed"
    for _rt_port in $_rt_list; do
        _rt_match=""
        for _rt_dev in $(list_drawers); do
            [ "$(disk_port "$_rt_dev")" = "$_rt_port" ] && _rt_match="$_rt_match $_rt_dev"
        done
        # shellcheck disable=SC2086 # word-splitting the match list is intended
        set -- $_rt_match
        [ "$#" -eq 1 ] \
            || die_local "selected drawer $_rt_port does not map to exactly one disk"
        _disks="$_disks $1"
    done
    [ -n "$_disks" ] || die_local "the room selected no writable drawers"
}
