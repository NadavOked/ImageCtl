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
    # The shrink question is a GUI dialog only (#928; owner 11/10: no text
    # screens). A capture ordered from the web console can land before the
    # GUI was ever started -- start it now. No GUI = the question cannot be
    # asked, and shrink_ask cancels the capture (nothing shrinks without a yes).
    gui_parent || log "capture: native GUI unavailable -- a shrink question will cancel the capture"

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
            # ‏#409: "power off, or back to the menu?" -- a GUI dialog since
            # 11/10 (askgui.sh). Power off is the default: no GUI, no answer
            # within CAPTURE_END_ASK_S -- the drive is pulled from a machine
            # that is off, the protection that was here before #409.
            case "$(attended "Capture complete: power off, or back to the menu" \
                    gui_ask_choice capture-end "${CAPTURE_END_ASK_S:-20}" "" "${CAPTURE_END_ASK_S:-20}")" in
                menu)
                    log "capture complete -- back to the menu (operator's choice)"
                    _build_standby=0; return 0 ;;
                poweroff) log "capture complete -- powering off (operator's choice)" ;;
                *) log "capture complete -- powering off" ;;
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
    # _build_standby keeps this screen up: build_console_screen does not boot
    # the machine to its local disk for want of a GUI after a finished task.
    log "task complete -- wol not armed, staying powered on"
    ui_clear; ui_header
    echo "  $1"
    echo "  Wake-on-LAN could not be armed, so this computer stays on."
    _build_standby=1
}
