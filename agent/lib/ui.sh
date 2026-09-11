# ui.sh -- the classroom screens. Plain text on the console: the Linux
# console cannot render RTL text, so agent screens are English by design
# (image names from the manifest are shown as-is).
# POSIX sh (busybox ash).

ui_clear() {
    [ "${IMAGECTL_TEST:-0}" = "1" ] || clear 2>/dev/null || printf '\033c'
}

ui_header() {
    echo "==============================================="
    echo "  ImageCtl"
    echo "==============================================="
    echo
}

ui_waiting_draw() {
    # $1 prefix, $2 joined, $3 expected, $4 starts_in_seconds
    ui_clear
    ui_header
    echo "  A deployment round is open: $1"
    echo
    echo "  Machines joined:  $2 / $3"
    if [ -n "$4" ] && [ "$4" != "null" ]; then
        echo "  Starting in:      ${4}s (or when everyone joins)"
    fi
    echo
    echo "  This machine is registered. Nothing to do -- do not"
    echo "  turn the computer off."
}

ui_unknown() {
    # $1 = mac
    ui_clear
    ui_header
    echo "  This computer is not registered ($1)."
    echo "  The console has been notified."
    echo
    echo "  Booting from the local disk shortly."
}

ui_error_hold() {
    # $1 = message. $2 = optional heartbeat command.
    #
    # A failed write leaves a broken disk -- do not reboot into it. Stay
    # powered so the console sees 'failed' and IT can act.
    #
    # "Stay powered" only means something if the server keeps hearing us:
    # `last_seen` is written by hello alone, so a silent hold made the
    # console draw the machine as "off" while it sat lit with an error on
    # its screen (#64). The heartbeat is a NON-joining hello -- it refreshes
    # `last_seen` without volunteering the machine for the next wave.
    #
    # The loop itself is hold_watch in hold.sh: this screen was drawn once and
    # then ran the heartbeat as `"$2" > /dev/null 2>&1 || true`, which threw
    # away the very evidence the heartbeat exists to produce (#109).
    ui_clear
    ui_header
    echo "  FAILED: $1"
    echo
    echo "  Leave the computer on and contact IT."
    if [ "${IMAGECTL_TEST:-0}" = "1" ]; then
        echo "TEST-HOLD"
        return
    fi
    hold_watch "$2"
}
