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
#: #989 item 2: the beat while a SMART question is on the GUI.
ATTENDED_ASK_BEAT_S="${ATTENDED_ASK_BEAT_S:-3}"

attended_hello() {
    # $1 = the question on the screen ("" = plain heartbeat, as hold_beat
    # always sent). Non-joining, like hold_beat: a waiting machine does not
    # ask into a wave. The answer is discarded -- the main loop's
    # response.json must not change under a running task.
    _ah_body=$(build_hello false) || return 1
    if [ -n "${1:-}" ]; then
        _ah_body="${_ah_body%\}},\"waiting_for\":\"operator\",\"prompt\":\"$(json_escape "$1")\"}"
    fi
    # #989 item 2: a SMART question on the cloner's GUI (smart-request) is also
    # offered to the console: `ask`. Without a live request -- a text menu,
    # nothing here would read the answer -- there is no `ask`.
    _ah_req="${GUI_DIR:-/nonexistent}/smart-request"
    _ah_nonce=""
    if [ -s "$_ah_req" ]; then
        IFS='|' read -r _ah_nonce _ah_port _ah_verdict _ah_reason < "$_ah_req" || _ah_nonce=""
        case "$_ah_nonce$_ah_port" in *[!0-9-]*|'') _ah_nonce="" ;; esac
    fi
    if [ -n "$_ah_nonce" ]; then
        _ah_body="${_ah_body%\}},\"ask\":{\"nonce\":\"$_ah_nonce\",\"port\":${_ah_port:-0},\"verdict\":\"$(json_escape "$_ah_verdict")\",\"reason\":\"$(json_escape "$_ah_reason")\"}}"
    fi
    printf '%s' "$_ah_body" > "$RUN_DIR/attended.json" || return 1
    http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/attended.json" > "$RUN_DIR/attended.resp" || return 1
    [ -n "$_ah_nonce" ] || return 0
    # The console answered: hand it to the GUI wait loop as if the button was
    # pressed -- same file, same nonce binding (clonergui.sh gui_smart_choice).
    # A local click already there wins; the claim in gui_smart_choice settles it.
    _ah_ans=$(smart_ask_answer "$RUN_DIR/attended.resp" "$_ah_nonce") || return 0
    [ ! -e "${GUI_DIR}/smart-decision" ] || return 0
    printf '%s|%s\n' "$_ah_nonce" "$_ah_ans" > "${GUI_DIR}/smart-decision.next" &&
        mv "${GUI_DIR}/smart-decision.next" "${GUI_DIR}/smart-decision"
}

smart_ask_answer() {
    # $1 = a hello response, $2 = the nonce asked. Prints the answer the
    # server says won for exactly that question; non-zero when there is none.
    jq -er --arg n "$2" '.ask_answer | select(.nonce == $n) | .answer
        | select(. == "replace" or . == "rescue" or . == "skip")' "$1" 2>/dev/null
}

attended_claim() {
    # $1 = nonce, $2 = what the person at the machine chose. The server settles
    # who answered first (console or here) and its reply carries the WINNING
    # answer: that is what gets done. No reply -- the server is down or the
    # reply is unreadable -- the local choice stands, said out loud on stderr:
    # a cloner does not stop because the server did (principle 1), but this is
    # not "the server agreed".
    _ac_body=$(build_hello false) &&
        _ac_body="${_ac_body%\}},\"ask_answered\":{\"nonce\":\"$1\",\"answer\":\"$2\"}}" &&
        printf '%s' "$_ac_body" > "$RUN_DIR/attended-claim.json" &&
        http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/attended-claim.json" \
            > "$RUN_DIR/attended-claim.resp" &&
        _ac_win=$(smart_ask_answer "$RUN_DIR/attended-claim.resp" "$1") &&
        { printf '%s\n' "$_ac_win"; return 0; }
    log "smart: the server did not confirm the answer -- keeping the local choice ($2)" >&2
    printf '%s\n' "$2"
}

#: The questions being waited on, innermost first, one per line -- a
#: prompt is one screen line. `attended` keeps it; nobody else reads it.
ATTENDED_NL='
'

attended_start() {
    # $1 = the question. A failed beat is not retried faster and not logged
    # per beat: the screen the person is reading must not scroll.
    #
    # One beat at a time (#912): a question asked inside another one -- the
    # sign-in inside a menu, say -- replaces the running beat instead of
    # joining it. Two beats would alternate their prompts on the server, and
    # the one that started first would outlive both stops as an orphan
    # (measured 16/09: 6 hellos for 0.7 s of waiting, 3 more after the end).
    attended_stop
    (
        while :; do
            if command -v watchdog_beat >/dev/null; then watchdog_beat; fi
            attended_hello "$1" || :
            # A SMART question on the GUI is answerable from the console
            # (#989 item 2), so its beat is faster: the answer is on screen
            # within ATTENDED_ASK_BEAT_S of the click, not 10 s.
            _at_t=0
            while [ "$_at_t" -lt "$ATTENDED_BEAT_S" ]; do
                sleep 1; _at_t=$((_at_t + 1))
                [ -s "${GUI_DIR:-/nonexistent}/smart-request" ] &&
                    [ $((_at_t % ATTENDED_ASK_BEAT_S)) = 0 ] && break
            done
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
    # Its stdout and exit code pass through untouched. When the command
    # returns, the beat of the question this one was asked inside -- if
    # any -- resumes: the person is back at that screen.
    ATTENDED_STACK="$1$ATTENDED_NL${ATTENDED_STACK:-}"
    attended_start "$1"; shift
    "$@"; _at_rc=$?
    attended_stop
    ATTENDED_STACK="${ATTENDED_STACK#*"$ATTENDED_NL"}"
    [ -z "$ATTENDED_STACK" ] || attended_start "${ATTENDED_STACK%%"$ATTENDED_NL"*}"
    return "$_at_rc"
}
