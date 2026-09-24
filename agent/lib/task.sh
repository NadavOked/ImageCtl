# task.sh -- do_task: the answer was a task (decide = task) -- a capture, or
# a direct send (#715). POSIX sh (busybox ash).
# פוצל מ-imagectl-agent ב-#1217 לפני קיר 300 השורות, בלי שינוי התנהגות. הלולאה
# הראשית ושני מסלולי השחזור נשארו שם; המשתנים שהפונקציה קוראת (‏RESP, ‏SERVER,
# ‏MAC) נקבעים שם לפני הלולאה, והיא נקראת רק ממנה.

do_task() {
    _type=$(json_get "$RESP" ".task.type")
    _tid=$(json_get "$RESP" ".task.id")
    # ‏#530: האסימון מגיע בתשובת ה-hello, ורק למכונה שה-MAC שלה תואם,
    # וכל כתיבה על המשימה דורשת אותו בחזרה. **בלי `export`**: שני
    # אתרי השימוש ב-`capture.sh` הם הרחבת מעטפת בקובץ שנטען ב-`.`,
    # ותת-מעטפת יורשת משתנה גם בלי ייצוא. אין שם `sh -c`.
    TASK_TOKEN=$(json_get "$RESP" ".task.token")
    case "$_type" in
        capture) ;;
        direct_send) direct_send_task "$_tid"; return $? ;;   # #715
        # A task type this build does not know is not a reason to guess.
        *) log "unknown task type '$_type' -- ignoring it"; sleep 5; return 0 ;;
    esac

    _disk=$(json_get "$RESP" ".task.disk")
    ui_clear; ui_header
    echo "  Capturing $(json_get "$RESP" ".task.name")"
    echo "  Source disk: /dev/$_disk"
    echo
    echo "  Reading used blocks only. Do not power off."

    progress_loop "" "$MAC" "$SERVER" "$_tid" &
    _ppid=$!

    # Both roads out of here end at report_final (#127). `sleep 4; kill` was
    # the same gamble #101 took out of the round and the drawers, and on this
    # path it is the worst of the three: the next step is `poweroff -f` with
    # the drive about to be pulled out, so a capture the server never heard
    # about stays open for ever against a machine that is off.
    if capture_disk "$_tid" "$_disk"; then
        if upload_manifest "$_tid" "$RUN_DIR/new-manifest.json"; then
            echo "done" > "$RUN_DIR/state"
            report_final "$_ppid" "" "$MAC" "$SERVER" "$_tid" \
                || { hold_unheard "" "$_tid"; return 1; }
            # ‏#409: המפעיל בוחר -- תפריט (להפיץ מיד) או כיבוי. בלי תשובה:
            # כיבוי, ההגנה שהייתה כאן -- מי שיצא מהחדר מוצא מכונה כבויה.
            shrink_gui_release "the end-of-capture question"
            case "$(attended "Capture complete: back to the menu, or power off" capture_end_ask)" in
                menu)
                    log "capture complete -- back to the menu (operator's choice)"
                    _build_standby=0; return 0 ;;   # build_console_screen draws the menu again
                poweroff) log "capture complete -- powering off (operator's choice)" ;;
                *) log "capture complete -- no answer in ${CAPTURE_END_ASK_S:-20}s, powering off" ;;
            esac
            [ "${IMAGECTL_TEST:-0}" = "1" ] && exit 0
            finish_and_stop
            # #1222: finish_and_stop returns only when no NIC took Wake-on-LAN
            # (#587) -- the machine stays on, like the idle power-off. The
            # capture is done and reported; this is not the failure path.
            task_done_stays_on "Capture complete. The image is in the library."
            return 0
        fi
        log "manifest upload failed"
    fi
    echo "failed" > "$RUN_DIR/state"
    report_final "$_ppid" "" "$MAC" "$SERVER" "$_tid" \
        || { hold_unheard "" "$_tid" \
             "capture did not complete, and the server was not told"; return 1; }
    ui_error_hold "capture did not complete" hold_beat
    return 1
}

task_done_stays_on() {
    # $1 = what completed. A finished task on a machine that could not be
    # armed for Wake-on-LAN: say so on the screen and go back to the loop.
    # _build_standby keeps this screen up instead of the menu (build_standby).
    log "task complete -- wol not armed, staying powered on"
    ui_clear; ui_header
    echo "  $1"
    echo "  Wake-on-LAN could not be armed, so this computer stays on."
    _build_standby=1
}

capture_end_ask() {
    # ‏#409: the end of a capture on the build machine. The screen goes to
    # stderr and only the answer to stdout (the caller reads it in `$( )`,
    # like smart_ask/shrink_ask): menu / poweroff / none.
    #
    # ‏`read -t` on any stdin, not only a tty (roomflow.sh reads it only on
    # a tty): busybox returns 1 on the timeout AND on EOF (Debian 1.37,
    # measured 19/09), and here both mean the same thing -- nobody answered
    # -- so a closed console and a silent one both end in the power-off.
    _ce_s="${CAPTURE_END_ASK_S:-20}"
    while :; do
        {
            ui_clear; ui_header
            echo "  Capture complete. The image is in the library."
            echo
            echo "    [1] Back to the menu"
            echo "    [2] Power off (then remove the drive safely)"
            echo
            echo "  Powering off in $_ce_s seconds if nothing is chosen."
            printf "  Choose [1-2]: "
        } >&2
        # shellcheck disable=SC3045 # busybox ash has read -t; see above
        read -r -t "$_ce_s" _ce_c || { echo none; return 0; }
        case "$_ce_c" in
            1) echo menu; return 0 ;;
            2) echo poweroff; return 0 ;;
            *) ;;   # not a choice: draw again, never pick silently
        esac
    done
}
