# attended.sh -- hello while the agent waits for a person (#906).
#
# smart_choice (and the cloner screen behind it) blocks until the operator
# answers. Nothing else sends hello in that time: `last_seen` freezes, the
# console draws the machine as not seen, and the monitor -- the one thing
# the operator needs to read the question from his desk -- is refused
# (403) past ONLINE_SECONDS. Measured 16/09 on cloner 2.
#
# The wait itself is not shortened (Nadav: cloning is always with a person).
# The machine only keeps saying, at the hold beat's rate, "I am here, and I
# am waiting for you: <question>" -- interfaces.md section 2, `waiting_for`
# and `prompt`. A hello without the fields clears the prompt on the server.
#
# POSIX sh (busybox ash): no `read -t`, no job control. The beat is a
# background subshell started before the wait and killed after it. Its
# only children are `curl` (bounded by HTTP_TIMEOUT) and `sleep`; there is
# no stream under it to orphan. Every descriptor of the subshell goes to
# /dev/null: the caller runs inside `$( )`, and an inherited stdout would
# keep that capture open until the last `sleep` ended.

#: Same rate as HOLD_BEAT_S -- the other wait-for-a-person -- and never
#: faster than the idle poll ladder's first rung (poll.sh).
ATTENDED_BEAT_S="${ATTENDED_BEAT_S:-10}"

attended_hello() {
    # $1 = the question on the screen ("" = plain heartbeat, as hold_beat
    # always sent). Non-joining, like hold_beat: a waiting machine does not
    # ask into a wave. The answer is discarded -- the main loop's
    # response.json must not change under a running task.
    _ah_body=$(build_hello false) || return 1
    if [ -n "${1:-}" ]; then
        _ah_body="${_ah_body%\}},\"waiting_for\":\"operator\",\"prompt\":\"$(json_escape "$1")\"}"
    fi
    printf '%s' "$_ah_body" > "$RUN_DIR/attended.json" || return 1
    http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/attended.json" > /dev/null
}

attended_start() {
    # $1 = the question. A failed beat is not retried faster and not logged
    # per beat: the screen the person is reading must not scroll.
    (
        while :; do
            attended_hello "$1" || :
            sleep "$ATTENDED_BEAT_S"
        done
    ) < /dev/null > /dev/null 2>&1 &
    ATTENDED_PID=$!
}

attended_stop() {
    [ -n "${ATTENDED_PID:-}" ] || return 0
    kill "$ATTENDED_PID" 2>/dev/null
    ATTENDED_PID=""
}

attended() {
    # $1 = the question; $2.. = the command that waits for the answer.
    # Its stdout and exit code pass through untouched.
    attended_start "$1"; shift
    "$@"; _at_rc=$?
    attended_stop
    return "$_at_rc"
}
