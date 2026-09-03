# hold.sh -- what a machine that has stopped does while it waits for a person.
#
# A hold is not a pause before another attempt. The machine has stopped, and it
# will do nothing more (#64): it does not reboot, it does not retry the write,
# it does not power off. The one thing left for it to do is to keep saying that
# it is here -- `last_seen` on the server is written by hello and by nothing
# else, so a hold that goes quiet is drawn on the console as a machine that is
# OFF. That is the worst sentence this system can say to a technician: "off"
# sends him after a power cable, while the machine stands lit with the error
# message he actually came to read.
#
# The screens live in ui.sh; what the machine keeps *saying* lives here, so the
# two can be read apart. Both directions of the pairing are runtime calls, and
# neither file needs the other loaded first.
#
# POSIX sh (busybox ash). No bashisms.

HOLD_BEAT_S="${HOLD_BEAT_S:-10}"

hold_beat() {
    # The heartbeat of a machine stopped on an error: it says "I am here"
    # without asking to join a wave. The *answer* is thrown away -- this
    # machine does not act on anything any more -- but the exit code is not.
    # It is the only evidence that anyone heard, and it is what hold_watch
    # below is built to read.
    build_hello false > "$RUN_DIR/beat.json" || return 1
    http_post_json "$SERVER/api/v1/agent/hello" "$RUN_DIR/beat.json" > /dev/null
}

hold_unheard() {
    # $1 = session id (empty for a capture task), $2 = task id (optional),
    # $3 = the line for the screen (optional).
    #
    # The work ended and the server never said it heard. Rule 1 sends every
    # *unclear* state to the local disk; this one is not unclear -- we know the
    # work ended and we know the server does not, and a boot from here is
    # answered by GRUB with the same running session and the same restore, to
    # the boot guard's budget (#75). A capture is worse still: the next step
    # there is `poweroff -f` with the drive about to be pulled, and a task left
    # open against a machine that is off is a state no operator can read (#127).
    #
    # So it holds, named, with the reporter back up to keep saying the closing
    # word if the server returns, and with the heartbeat so the console keeps
    # drawing the machine as awake while it says it.
    progress_loop "$1" "$MAC" "$SERVER" "${2:-}" &
    ui_error_hold \
        "${3:-the work finished but the server never confirmed it}" hold_beat
}

hold_watch() {
    # $1 = the heartbeat command, or "" for a hold whose caller deliberately
    # left its progress loop running instead. Never returns.
    #
    # Until #109 the beat ran as `"$2" > /dev/null 2>&1 || true` -- stdout,
    # stderr and the exit code thrown away in one line. That is the shape rule
    # 5 names: the heartbeat is not a report that will be repeated in two
    # seconds (`progress_loop` earns its `|| true` that way, and keeps it), it
    # is the single piece of evidence that this machine is alive. When it stops
    # arriving, `last_seen` goes stale past AWAKE_SECONDS and the console draws
    # the machine as off -- the exact symptom #64 closed -- while nothing, on
    # the screen or in the journal, said so.
    #
    # What changes on failure is what the machine *says*, and nothing else:
    #
    #   * The loop is never broken. A hold that stopped beating is a machine
    #     that is neither visible nor recoverable when the link returns.
    #   * No backoff, and no ceiling to grow through. Six requests a minute is
    #     not load, and a beat that has slowed down is a machine that stays
    #     invisible for minutes after the network comes back.
    #   * One line per *transition* -- lost, and heard again -- never one per
    #     beat. This screen scrolls and never clears: a note every ten seconds
    #     would push the FAILED line off the top, and that line is the one the
    #     technician walked over to read.
    #   * What curl said is kept, not sent to /dev/null: "connection refused"
    #     and "could not resolve host" send a technician to two different
    #     places. It is held in a file that each beat overwrites and copied
    #     into the journal at the transition -- the reason for an outage is
    #     one sentence, not the same sentence six times a minute.
    _beat_fails=0
    _beat_out="$RUN_DIR/beat.err"
    while :; do
        if [ -n "${1:-}" ]; then
            if "$1" > "$_beat_out" 2>&1; then
                if [ "$_beat_fails" -gt 0 ]; then
                    log "the server is answering again" \
                        "(after $_beat_fails missed heartbeats)"
                    echo "  The server can see this machine again."
                    _beat_fails=0
                fi
            else
                _beat_fails=$((_beat_fails + 1))
                if [ "$_beat_fails" = "1" ]; then
                    log "WARNING: the heartbeat is not getting through --" \
                        "the console will show this machine as off." \
                        "$(tr '\n' ' ' < "$_beat_out")"
                    echo
                    echo "  NOTE: no contact with the server since" \
                        "$(date -u +%H:%M:%S 2>/dev/null || echo '--:--:--')."
                    echo "        The console cannot see this machine, so it"
                    echo "        draws it as OFF -- check the network, not"
                    echo "        the power. The message above still stands."
                fi
            fi
        fi
        sleep "$HOLD_BEAT_S"
    done
}
