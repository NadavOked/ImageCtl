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

    for _cg_dir in "$RUN_DIR"/targets/*/; do
        [ -d "$_cg_dir" ] || continue
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
        printf 'drawer=%s|%s|%s|%s|%s|%s\n' \
            "$_cg_port" "$_cg_dev" "$_cg_state" \
            "$_cg_bytes" "$_cg_total" "$_cg_error" >> "$_cg_next" ||
            return 1
    done

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
            IFS='|' read -r _gsc_dnonce _gsc_dec < "$GUI_DIR/smart-decision" || return 1
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
