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
            ui_clear; ui_header
            echo "  Capture complete. The image is in the library."
            echo "  You can power off and remove the drive."
            [ "${IMAGECTL_TEST:-0}" = "1" ] && exit 0
            sleep 20
            finish_and_stop
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
