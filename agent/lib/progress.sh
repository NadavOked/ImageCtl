# progress.sh -- how the server learns what this machine is doing: the
# progress report (interfaces.md section 4), and -- for a single-station
# restore -- the registration of the stream those reports are addressed to.
# Pure sh assembly so it is testable without jq.
# POSIX sh (busybox ash).
#
# The two belong together: a report needs a session id, and for a class round
# the id arrives in the hello answer, while for a unicast pull nothing hands
# it over -- the station asks for one. That request lives here rather than in
# ui.sh because it is a reporting concern, not a screen (#63).
#
# State lives in files, so the reporter can run as a background process:
#   $RUN_DIR/state                 top-level state word
#   $RUN_DIR/targets/<dev>/state   waiting/writing/verifying/done/failed
#   $RUN_DIR/targets/<dev>/base    compressed bytes from finished partitions
#   $RUN_DIR/targets/<dev>/bytes.raw   pv output for the current partition
#   $RUN_DIR/targets/<dev>/counter the file the live counter is read from
#   $RUN_DIR/targets/<dev>/total   total compressed bytes for the whole image
#   $RUN_DIR/targets/<dev>/error   error text (only when failed)
#
# The live counter is a *pointer*, not a fixed name, because the two restore
# paths measure in different places. A classroom station has one pv per
# target and writes bytes.raw. A cloning machine has one pv for the whole
# machine -- the same number for all three drawers, which is no measurement
# of any of them (#25) -- so its drawers point this at fanout's per-drawer
# counter instead. Whoever runs the write declares where the truth is; the
# reporter never guesses between two files.

target_init() {
    # $1 = dev, $2 = bytes_total for the full image.
    _t="$RUN_DIR/targets/$1"
    mkdir -p "$_t"
    echo "waiting" > "$_t/state"
    echo 0 > "$_t/base"
    : > "$_t/bytes.raw"
    echo "$_t/bytes.raw" > "$_t/counter"
    echo "$2" > "$_t/total"
    rm -f "$_t/error"
}

target_counter() {
    # $1 = dev, $2 = the file the live counter is appended to (pv format:
    # a decimal number per line, the last one wins).
    echo "$2" > "$RUN_DIR/targets/$1/counter"
}

_counter_of() {
    _c=$(cat "$RUN_DIR/targets/$1/counter" 2>/dev/null)
    [ -n "$_c" ] || _c="$RUN_DIR/targets/$1/bytes.raw"
    echo "$_c"
}

target_set() {
    # $1 = dev, $2 = state, $3 = optional error text.
    _t="$RUN_DIR/targets/$1"
    echo "$2" > "$_t/state"
    [ -n "${3:-}" ] && echo "$3" > "$_t/error"
}

# ‏#100: המונה הוא ה-stderr של `pv`, ולכן הוא מכיל גם את הודעות השגיאה
# שלו — ``pv: write failed: Broken pipe`` נכתב לאותו קובץ בדיוק ברגע
# שה-curl של ההעלאה נופל. ‏`tail -n 1` הרים את השורה הזאת אל
# ‏``$((...))``. מדוד תחת busybox ash: תת-המעטפת של `$( )` מתה והערך
# חזר **ריק**, ומשם שתי תוצאות — ``"bytes_written":,`` שהוא JSON פגום
# שהשרת פוסל כולו, וקובץ `base` ריק שמאפס את הבייטים של כל המחיצות
# שכבר הסתיימו. ‏`|| echo 0` לא היה עוזר: החשבון מת לפני שיש קוד יציאה.
_last_number() {
    # השורה **המספרית** האחרונה. לא השורה האחרונה, ולא `n + 0`:
    # "אין שורה מספרית" ו"אפס" הם שני מצבים שונים. ‏`waits.sh` מבדיל
    # ביניהם כדי לדעת אם הבייט הראשון הגיע, והחזרת 0 קבוע נראתה לו
    # כמו התקדמות — כלומר החליפה את תקרת ההמתנה. הקוראים כאן
    # מוסיפים 0 בעצמם, מיד אחרי הקריאה.
    tr -d '\r' < "$1" 2>/dev/null \
        | awk '/^[0-9]+$/ { n = $0 } END { if (n != "") print n }'
}

target_bytes() {
    # $1 = dev. Finished partitions + the live pv counter.
    _t="$RUN_DIR/targets/$1"
    _base=$(_last_number "$_t/base")
    _cur=$(_last_number "$(_counter_of "$1")")
    [ -n "$_base" ] || _base=0
    [ -n "$_cur" ] || _cur=0
    echo $((_base + _cur))
}

target_partition_done() {
    # $1 = dev. Fold the finished partition's counter into the base.
    _t="$RUN_DIR/targets/$1"
    echo "$(target_bytes "$1")" > "$_t/base"
    : > "$(_counter_of "$1")"
}

build_progress() {
    # $1 = session id, $2 = mac, $3 = task id (capture instead of a round).
    # Exactly one of session/task identifies the work -- see interface 4.
    _state=$(cat "$RUN_DIR/state" 2>/dev/null || echo "waiting")
    _targets=""
    for _d in "$RUN_DIR"/targets/*/; do
        [ -d "$_d" ] || continue
        _dev=$(basename "$_d")
        _tstate=$(cat "$_d/state" 2>/dev/null || echo "waiting")
        _total=$(cat "$_d/total" 2>/dev/null || echo 0)
        _entry=$(printf '{"dev":"%s","bytes_written":%s,"bytes_total":%s,"state":"%s"' \
            "$_dev" "$(target_bytes "$_dev")" "$_total" "$_tstate")
        if [ -f "$_d/error" ]; then
            _entry="$_entry,\"error\":\"$(json_escape "$(cat "$_d/error")")\""
        fi
        _entry="$_entry}"
        [ -n "$_targets" ] && _targets="$_targets,"
        _targets="$_targets$_entry"
    done
    if [ -n "${3:-}" ]; then
        _who=$(printf '"task_id":"%s"' "$3")
    else
        _who=$(printf '"session_id":"%s"' "$1")
    fi
    printf '{%s,"mac":"%s","state":"%s","targets":[%s]}' \
        "$_who" "$2" "$_state" "$_targets"
}

progress_send() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    # One report, synchronously, and the exit code of the send.
    #
    # The loop below sends and forgets, which is right for a report that will
    # be repeated in two seconds. The *last* report of a job is not repeated:
    # it is what tells the server the work ended. That one gets an answer read.
    build_progress "$1" "$2" "${4:-}" > "$RUN_DIR/progress.json"
    http_post_json "$3/api/v1/agent/progress" "$RUN_DIR/progress.json" \
        > /dev/null 2>&1
}

progress_loop() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    #
    # The `|| true` here is the right one and it stays: a report that will be
    # repeated in two seconds owes nobody an answer. What must never travel
    # through this loop is the *last* report -- see report_final.
    while :; do
        progress_send "$1" "$2" "$3" "${4:-}" || true
        sleep "${PROGRESS_INTERVAL_S:-2}"
    done
}

# --- the last report of a job (#101) -----------------------------------------
#
# Three paths end a job, and until #101 only one of them read the answer. The
# other two stopped the loop and slept: `sleep 6  # let 'done' reach the
# server`. Six seconds is a hope, not evidence -- with --max-time 10 --retry 3
# they may not hold one completed attempt -- and for a class round the cost of
# guessing wrong is not a missing line on a screen. `session_members.done`
# stays 0, the session stays `running`, and the machine's next hello is
# answered with the same restore: it writes the same 40GB again, up to the
# boot guard's budget (#75), and is finally labelled "boot loop" rather than
# "the closing report never arrived".
#
# So the closing report is sent from here: the loop is stopped first (one
# writer on progress.json), the send is synchronous, and the exit code is the
# answer. A report the server refused comes back as 400, and `curl -sfS`
# turns that into a non-zero exit -- so 0 means 200, and nothing else does.
#
# The ceiling counts tries and not seconds, because a try is a whole curl with
# its own retries: a number of seconds here would be a second guess about how
# long one of them takes. Five tries against a server that is not answering
# is minutes of real attempts, which covers a server being restarted; what it
# does not cover is a server that is gone, and that is the caller's business.
FINAL_REPORT_TRIES="${FINAL_REPORT_TRIES:-5}"
FINAL_REPORT_GAP_S="${FINAL_REPORT_GAP_S:-3}"

report_final() {
    # $1 = reporter pid ("" when no loop is running), $2 = session id,
    # $3 = mac, $4 = server URL, $5 = task id (optional).
    # 0 only on positive evidence that the server took the report.
    [ -n "${1:-}" ] && kill "$1" 2>/dev/null
    _f_try=0
    while :; do
        _f_try=$((_f_try + 1))
        if progress_send "$2" "$3" "$4" "${5:-}"; then
            [ "$_f_try" -gt 1 ] && log "the final report got through on try $_f_try"
            return 0
        fi
        [ "$_f_try" -ge "$FINAL_REPORT_TRIES" ] && break
        log "the final report was not accepted (try $_f_try of $FINAL_REPORT_TRIES)"
        sleep "$FINAL_REPORT_GAP_S"
    done
    log "WARNING: the server did not acknowledge the final report after" \
        "$FINAL_REPORT_TRIES tries"
    return 1
}

# --- the unicast pull (interfaces.md, "משיכת יוניקאסט לתחנה בודדת") ---------
#
# A single-station restore pulls the image over HTTP. The bytes move whether
# or not the server was told -- /api/v1/images/... serves them to any machine
# in the registry -- so opening a pull is not asking permission. It is how the
# work becomes *visible*: a session id to address the reports above to, a line
# in the journal, and a row on the console. Until the agent called it, a
# station pulled for twenty minutes while the operator watched an idle
# server (#60 built the server side, #63 is this side).

pull_body() {
    # $1 = mac, $2 = image id, $3 = username, $4 = password.
    # Assembled by hand like hello and login -- nothing writes JSON with jq.
    printf '{"mac":"%s","image_id":"%s","username":"%s","password":"%s"}' \
        "$(json_escape "$1")" "$(json_escape "$2")" \
        "$(json_escape "$3")" "$(json_escape "$4")"
}

pull_post() {
    # $1 = server, $2 = body file, $3 = response file. Prints the HTTP code.
    #
    # Deliberately not http_post_json, for the reason login_post gives: `curl
    # -f` collapses every answer from 400 up into exit 22, and the two answers
    # that matter most here are opposite diagnoses. 404 means this server is
    # older than the endpoint and the restore should simply go on unwatched;
    # 503 means the server that the restore is about to pull 40GB *from* is
    # falling over. Folding them into "the pull failed" sends a technician
    # after the wrong fault.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        --data-binary "@$2" "$1/api/v1/agent/pulls" 2>/dev/null
}

pull_open() {
    # $1 = server, $2 = mac, $3 = image id, $4 = username, $5 = password.
    #
    # Sets PULL_SESSION and returns 0 on positive evidence only: a 200 that
    # carried an id back. "No error" is not an open stream -- a 200 with an
    # empty body would report progress into a session that does not exist.
    #
    # Every other road returns 1 *and writes a line*. Rule 1 decides what the
    # caller does with that (the restore goes on; a person is standing there
    # and the disk is already being erased), but a pull that failed to open
    # silently would be the same blindness #60 set out to end.
    PULL_SESSION=""
    _resp="$RUN_DIR/pull_resp.json"
    : > "$_resp"
    pull_body "$2" "$3" "$4" "$5" > "$RUN_DIR/pull.json"
    _code=$(pull_post "$1" "$RUN_DIR/pull.json" "$_resp")
    rm -f "$RUN_DIR/pull.json"

    if [ "$_code" = "200" ]; then
        _sid=$(json_get "$_resp" ".id")
        if [ -n "$_sid" ] && [ "$_sid" != "null" ]; then
            PULL_SESSION="$_sid"
            log "unicast pull registered as $_sid"
            return 0
        fi
        log "pull not opened: the server answered 200 without a session id"
        return 1
    fi

    _why=$(json_get "$_resp" ".code")
    if [ "$_code" = "000" ]; then
        log "pull not opened: no answer from the server -- restoring unwatched"
    elif [ "$_code" = "404" ] && [ "$_why" = "null" ]; then
        # Our own refusals carry {"ok":false,"code":...}. A 404 without one is
        # the router saying the path does not exist: a server older than #60.
        log "pull not opened: this server has no /api/v1/agent/pulls" \
            "(older than the pull view) -- restoring unwatched"
    else
        log "pull not opened: the server refused it (http $_code, $_why)" \
            "-- restoring unwatched"
    fi
    return 1
}

pull_close() {
    # $1 = reporter pid ("" when no stream was opened), $2 = session id,
    # $3 = mac, $4 = server URL.
    #
    # The server closes a pull on positive evidence: a report that says
    # `done`. So the closing report *is* the close, and it goes through
    # report_final like every other closing report -- one mechanism, not
    # three, which is what let the other two paths drift into `sleep 6`.
    #
    # Nothing to close when no stream was ever opened (an older server, a
    # server that refused): the restore went on unwatched and pull_open has
    # already said so.
    [ -n "${1:-}" ] || return 0
    if report_final "$1" "$2" "$3" "$4"; then
        log "unicast pull $2 closed"
        return 0
    fi
    log "WARNING: the closing report for pull $2 did not get through --" \
        "it may stay 'running' on the console until an operator clears it"
    return 1
}
