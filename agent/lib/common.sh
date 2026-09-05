# common.sh -- logging, HTTP, kernel command line, fail-safe reboot.
# POSIX sh (busybox ash). No bashisms.

AGENT_VERSION="0.1.0"
RUN_DIR="${RUN_DIR:-/run/imagectl}"
LOG_FILE="$RUN_DIR/agent.log"

log() {
    # Timestamped line to the log file and the console.
    _ts=$(date -u +%H:%M:%S 2>/dev/null || echo "--:--:--")
    echo "[$_ts] $*" >> "$LOG_FILE" 2>/dev/null
    echo "imagectl: $*"
}

die_local() {
    # The safety principle: every unclear state ends at the local disk.
    #
    # A reboot is NOT a descent to the local disk -- it only hopes for one.
    # GRUB asks the server for a menu, and while a session is open that menu
    # answers "boot the agent" with timeout=0: the machine lands right back
    # here and fails again, for ever (#75). Nothing this side of the wire can
    # promise where the next boot goes, because the server writes the menu.
    #
    # So this stays the loud, fast failure it always was, and the guarantee
    # lives where the evidence is: the server counts the agent boots it hands
    # out for one job, and after a small budget serves a local-only menu. An
    # agent that dies before it has a network -- the case that was reproduced
    # on hardware -- never reports anything, and is covered all the same.
    log "FATAL: $1 -- rebooting; the server decides where the next boot goes"
    sleep 3
    if [ "${IMAGECTL_TEST:-0}" = "1" ]; then
        echo "TEST-REBOOT: $1"
        exit 86
    fi
    sync
    reboot -f
}

# --- kernel command line -----------------------------------------------------
# Only two keys are ever read: imagectl.server and imagectl.mode.
# Nothing about the task travels here -- the task comes from hello (section 3
# of the interfaces). Any other imagectl.* key is a bug elsewhere: ignore it
# loudly so it cannot become a second, undocumented interface.

parse_cmdline() {
    _file="${CMDLINE_FILE:-/proc/cmdline}"
    IMAGECTL_SERVER=""
    IMAGECTL_MODE="normal"
    IMAGECTL_DEBUG="0"
    for _word in $(cat "$_file" 2>/dev/null); do
        case "$_word" in
            imagectl.server=*)
                IMAGECTL_SERVER="${_word#imagectl.server=}"
                ;;
            imagectl.mode=recovery)
                IMAGECTL_MODE="recovery"
                ;;
            imagectl.debug=1)
                # מעטפת ניפוי לטכנאי (ראו imagectl-agent). לא ברירת מחדל:
                # המכונות עומדות בכיתות.
                IMAGECTL_DEBUG="1"
                ;;
            imagectl.*)
                log "WARNING: ignoring unknown kernel parameter: $_word"
                ;;
        esac
    done
    unset _word _file
    export IMAGECTL_SERVER IMAGECTL_MODE IMAGECTL_DEBUG
}

# --- HTTP --------------------------------------------------------------------
# All server traffic goes through these two helpers.

HTTP_RETRIES="${HTTP_RETRIES:-3}"
HTTP_TIMEOUT="${HTTP_TIMEOUT:-10}"
# ‏העברה ארוכה (מחיצה, העלאת קליטה) לא מקבלת תקרת *משך* — מחיצה של
# 100GB על כונן איטי לוקחת דקות וזה תקין. מה שהיא כן מקבלת הוא תקרת
# *חוסר התקדמות*: פחות מבייט לשנייה במשך כך וכך שניות = חיבור מת, ו-curl
# יוצא עם שגיאה במקום להיתלות. אותו עיקרון של WAIT_STREAM_STALL_S.
HTTP_STALL_TIMEOUT="${HTTP_STALL_TIMEOUT:-120}"

http_post_json() {
    # $1 = URL, $2 = file holding the JSON body. Response on stdout.
    curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -H "Content-Type: application/json" \
        --data-binary "@$2" "$1"
}

http_get() {
    # $1 = URL. Response on stdout.
    curl -sfS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" "$1"
}

http_get_stream() {
    # $1 = URL. Long transfer: no total timeout, fail fast on connect --
    # and abort a transfer that stopped moving instead of waiting for ever.
    curl -sfS --connect-timeout "$HTTP_TIMEOUT" \
        --speed-limit 1 --speed-time "$HTTP_STALL_TIMEOUT" "$1"
}

# --- boot trace (#400) -------------------------------------------------------
# A cloning machine is headless by design (#17): no screen, no keyboard, no
# mouse. When it stops between the GRUB menu and the first hello there is
# nothing at all to look at -- that is what blocked the first test on real
# hardware. So the agent says where it is, over HTTP, in one short request.
#
# Two rules, and they pull in opposite directions on purpose:
#
#   1. This NEVER fails its caller. A diagnostic that stops a machine from
#      booting is damage. trace_step always returns 0.
#   2. It is never silent. A failed delivery is logged here, and the server
#      side has the full list of steps -- so a step that never arrives is
#      itself the finding, not a gap.
TRACE_TIMEOUT="${TRACE_TIMEOUT:-3}"

trace_step() {
    # $1 = step name. Uses $SERVER and $MAC, which the agent sets before it
    # can do anything else anyway.
    [ -n "$SERVER" ] && [ -n "$MAC" ] || return 0
    if curl -sf -m "$TRACE_TIMEOUT" \
        "$SERVER/boot/step?mac=$MAC&s=$1" > /dev/null 2>&1; then
        return 0
    fi
    log "boot step '$1' was not delivered -- continuing"
    return 0
}

# --- misc --------------------------------------------------------------------

json_escape() {
    # Escape a string for embedding inside a JSON string literal:
    # backslash, double quote and every control character below 0x20.
    # Newlines become \n, CR is dropped (as it always was), and any other
    # control character becomes \u00XX. Everything else -- UTF-8 Hebrew
    # included -- passes through byte for byte.
    #
    # Not gsub(), on purpose. In the *replacement* string of gsub a
    # backslash is an escape of its own, and gawk, mawk and busybox awk
    # read those escapes differently. That is what made the previous
    # version replace every character with itself and escape nothing at
    # all: it passed under mawk on the workstation and did nothing under
    # the busybox awk the agent actually runs, so a password or a
    # partclone error holding a quote produced broken JSON and a 400 that
    # never reached the password check (#145).
    #
    # A lookup table has one meaning in every awk: the value is printed
    # as it stands, with no second round of interpretation. LC_ALL=C so
    # the scan is over bytes -- a multi-byte character is copied through
    # one byte at a time and is never split by a table hit.
    printf '%s' "$1" | LC_ALL=C awk 'BEGIN {
        ORS = ""
        for (i = 1; i < 32; i++) esc[sprintf("%c", i)] = sprintf("\\u%04x", i)
        esc[sprintf("%c", 8)] = "\\b"
        esc[sprintf("%c", 9)] = "\\t"
        esc[sprintf("%c", 12)] = "\\f"
        esc[sprintf("%c", 13)] = ""
        esc["\\"] = "\\\\"
        esc["\""] = "\\\""
    }
    {
        if (NR > 1) printf "%s", "\\n"
        out = ""
        n = length($0)
        for (i = 1; i <= n; i++) {
            c = substr($0, i, 1)
            if (c in esc) out = out esc[c]
            else out = out c
            # Appending to a string costs its whole length in awk, so a
            # long line would cost the square of it. Flushing a short
            # buffer keeps that flat: 48KB takes 69ms instead of 210ms,
            # and a normal field (tens of bytes) is unaffected.
            if (length(out) >= 256) { printf "%s", out; out = "" }
        }
        printf "%s", out
    }'
}

trim() {
    # Strip leading/trailing whitespace.
    printf '%s' "$1" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}
