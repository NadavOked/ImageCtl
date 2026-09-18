# buildmenu.sh -- the build machine's text menu (#135).
# POSIX sh (busybox ash). No bashisms, no job control.
#
# A person stands in front of this machine. They sign in, and then they get
# the three or four things this server can be told to do from here (#1073,
# Nadav's ruling of 2026-09-18 -- the v1 role model):
#
#   deploy  deploy to the cloning room from an image on THIS server, deploy
#           this disk directly to the cloning room (#715), restore an image
#           from THIS server onto this machine's own disk (selfrestore.sh)
#   admin   the same three, plus capture this disk into the library
#
# "Deploy to a classroom" exists in the code and is hidden in v1: classrooms
# are v2 (BUILD_MENU_CLASSROOMS below; #1081 turns it into a server flag).
# A deploy account has no web console at all -- the console refuses it at
# sign-in -- so this menu and the GUI are the whole of what deploy can do.
#
# Text only. #32 (the kiosk) was closed "not planned": there is no graphical
# stack on an edge machine, and the Linux console has neither a Hebrew font
# nor RTL. Everything printed here is ASCII, exactly like ui.sh.
#
# What the menu offers (the items, their labels, the role and classroom
# gates) lives in buildmenuitems.sh; this file is how the menu runs.
#
# There is no race with the poll loop. The server is passive -- it answers
# hello, it never pushes -- so while somebody is reading a menu this machine
# is not asking for work: the only hello it sends is the attended beat (#908,
# attended.sh -- non-joining, its answer discarded), so the console keeps
# seeing it instead of "not seen" after ONLINE_SECONDS. Standby (0) hands it
# back to the loop, so a capture ordered from the console still lands here.

#: The console session cookie. The console API (folders, capture, room) is
#: cookie-authenticated; /api/v1/agent/login proves the password but issues
#: no cookie, so the credentials are exchanged once more here.
CONSOLE_JAR="${CONSOLE_JAR:-$RUN_DIR/console.jar}"

#: The cookie the server sets (server/auth.py:COOKIE_NAME).
CONSOLE_COOKIE="imagectl_session"

console_signin() {
    # Exchanges the operator's console credentials for a session cookie.
    #
    # The password goes to curl over a pipe and never becomes a file, for
    # the reason classround.sh:open_round_post spells out: $RUN_DIR is tmpfs
    # on a machine that stands in a room, and a cleanup that has to succeed
    # for the fix to hold is not a fix.
    #
    # Not `curl -f`: it collapses every answer from 400 up into exit 22, and
    # 401 ("checked, wrong") is not 000 ("never asked"). The verdict is the
    # status code itself, and then the cookie -- 200 without a session
    # cookie is not a session (rule 5: positive evidence, twice).
    #
    # A fresh session every time an option is chosen, on purpose: a cookie
    # kept from earlier can have expired (12h) and would come back as a 401
    # in the middle of a flow, which is a stale-credential bug for the price
    # of one POST. It also puts every action in the journal under its user.
    rm -f "$CONSOLE_JAR"
    _code=$(printf '{"username":"%s","password":"%s"}' \
        "$(json_escape "$RECOVERY_USER")" "$(json_escape "$RECOVERY_PASS")" \
    | curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -c "$CONSOLE_JAR" -o "$RUN_DIR/console_login.json" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary @- \
        "$SERVER/api/console/login") || {
        log "console sign-in: transport failed" >&2
        rm -f "$CONSOLE_JAR"
        return 1
    }
    if [ "$_code" = "401" ]; then
        # Not "your session expired": the agent login accepted this account
        # a moment ago, so a refusal here means the account changed under us.
        rm -f "$CONSOLE_JAR"
        log "console sign-in: refused after the agent login accepted it"
        echo "  The console refused this account. Ask an administrator."
        sleep 5
        return 1
    fi
    if [ "$_code" != "200" ]; then
        rm -f "$CONSOLE_JAR"
        log "console sign-in refused (http ${_code:-000})"
        console_say "$_code" "Could not open a console session"
        return 1
    fi
    # Fails closed: a jar that cannot be read is not a session we hold.
    if ! awk -F '\t' -v cookie="$CONSOLE_COOKIE" \
        '$6 == cookie && length($7) > 0 { found=1 } END { exit !found }' "$CONSOLE_JAR"; then
        rm -f "$CONSOLE_JAR"
        log "console sign-in: 200 without a session cookie"
        echo "  The server accepted the password but issued no session."
        sleep 5
        return 1
    fi
    return 0
}

console_get() {
    # $1 = path under /api/console, $2 = output file. Prints the HTTP code.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$CONSOLE_JAR" -o "$2" -w '%{http_code}' \
        "$SERVER/api/console/$1"
}

console_post() {
    # $1 = path, $2 = JSON body file, $3 = output file. Prints the HTTP code.
    # Bodies here carry no passwords -- names, a mac and a disk -- so unlike
    # the sign-in they may live in a file.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -b "$CONSOLE_JAR" -o "$3" -w '%{http_code}' \
        -H "Content-Type: application/json" --data-binary "@$2" \
        "$SERVER/api/console/$1"
}

console_say() {
    # $1 = HTTP code, $2 = what was being attempted. Prints one English line.
    #
    # The server's own `detail` is Hebrew and is deliberately NOT printed:
    # this console has no Hebrew font, so it would reach the operator as
    # squares. The raw answer stays in $RUN_DIR for a technician.
    case "$1" in
        401) echo "  $2: the console session expired. Sign in again." ;;
        403) echo "  $2: this account may not do that." ;;
        409) echo "  $2: something is already open (a round, or a task)." ;;
        400) echo "  $2: the server rejected the request." ;;
        200) echo "  $2: unexpected answer from the server." ;;
        *)   echo "  $2: no answer from the server (http ${1:-000})." ;;
    esac
    sleep 5
}

build_name_ok() {
    # Folder and image names are ASCII: letters, digits, dot, underscore,
    # dash and spaces. Nadav's ruling of 2026-08-30 -- "English or numbers".
    #
    # This is a courtesy check at the keyboard, not enforcement: the server
    # still accepts any name through the console, so a Hebrew folder created
    # there will show up here unreadable. That gap is #135's open finding.
    case "$1" in
        "") return 1 ;;
        *[!A-Za-z0-9._\ -]*) return 1 ;;
    esac
    [ "${#1}" -le 60 ]
}

build_ask_name() {
    # $1 = prompt. Prints the accepted name on stdout, everything else on
    # stderr. Three tries and then back to the menu: a name that cannot be
    # drawn on a classroom screen does not get better by typing it again.
    _t=0
    while [ "$_t" -lt 3 ]; do
        _t=$((_t + 1))
        printf "  %s: " "$1" >&2
        read -r _v
        if build_name_ok "$_v"; then
            echo "$_v"
            return 0
        fi
        echo "  Letters A-Z a-z, digits, . _ - and spaces only, up to 60." >&2
        echo "  The classroom console cannot display Hebrew." >&2
    done
    return 1
}

build_menu_gate() {
    # Sign in first, for the reason #80 gives on the station: the menu is
    # the list of what this server can be told to do, and that list is not
    # for somebody who has no account here.
    #
    # The role is read from the answer recovery_login already stored --
    # /api/v1/agent/login returns it, so there is nothing more to ask.
    [ -n "${BUILD_ROLE:-}" ] && return 0
    ui_clear; ui_header
    echo "  Build machine. Sign in with your console account."
    echo
    recovery_login || { login_failed; return 1; }
    BUILD_ROLE=$(json_get "$RUN_DIR/login_resp.json" ".role")
    for _r in $BUILD_MENU_ROLES; do
        [ "$BUILD_ROLE" = "$_r" ] && return 0
    done
    log "build menu: role '$BUILD_ROLE' is not allowed here"
    echo "  This account may not capture or deploy from this machine."
    sleep 5
    BUILD_ROLE=""
    RECOVERY_USER=""
    RECOVERY_PASS=""
    return 1
}

build_standby() {
    # $1 = optional first line. The machine returns to the poll loop, so
    # work ordered from the console still reaches it; the menu is not
    # redrawn over a screen somebody may be reading.
    _build_standby=1
    # Hygiene, not the guarantee: the jar lives in tmpfs and dies with the
    # boot either way, but there is no reason to leave a live session token
    # sitting on a machine nobody is standing at.
    rm -f "$CONSOLE_JAR"
    ui_clear; ui_header
    [ -n "${1:-}" ] && echo "  $1"
    echo "  Standing by. Work ordered from the console starts here."
    echo
    echo "  Restart this computer to use the menu again."
}

build_menu() {
    while :; do
        build_menu_options
        _n=$(wc -l < "$RUN_DIR/build_menu.txt"); _n=$((_n))
        ui_clear; ui_header
        echo "  Build machine. Signed in as $RECOVERY_USER ($BUILD_ROLE)."
        echo
        _i=0
        while read -r _act; do
            _i=$((_i + 1))
            printf '    %s) %s\n' "$_i" "$(build_menu_label "$_act")"
        done < "$RUN_DIR/build_menu.txt"
        echo
        printf "  Choose [1-%s], or 0 to stand by: " "$_n"
        # EOF is not a choice: a closed console must not spin the menu.
        read -r _c || { build_standby; return 0; }
        case "$_c" in
            0|"") build_standby; return 0 ;;
            *[!0-9]*) continue ;;
        esac
        { [ "$_c" -ge 1 ] && [ "$_c" -le "$_n" ]; } || continue
        case "$(sed -n "${_c}p" "$RUN_DIR/build_menu.txt")" in
            capture)
                build_capture_flow && {
                    build_standby "Capture ordered. It starts in a moment."
                    return 0
                }
                ;;
            room) room_flow ;;
            direct)
                # #715: the source is this disk; the task lands with the
                # next hello, so the menu steps aside like after a capture.
                direct_flow && return 0
                ;;
            self)
                # #1073 (c): erases this machine's disk and reboots into the
                # restored system; a refusal (name mismatch, no image, no
                # answer) comes back to the menu with nothing written.
                self_restore_flow && return 0
                ;;
            class)
                class_round_flow || continue
                # classround.sh tells a station "you will join automatically".
                # This machine will not: it is a build machine, not a member
                # of the class, and saying nothing would leave that standing.
                echo
                echo "  This build machine is not part of the class and will"
                echo "  not join the round."
                sleep 6
                build_standby
                return 0
                ;;
        esac
    done
}

build_menu_flow() {
    # Entry point from the main loop. The menu is a wait for a person, and
    # hello keeps going through it (#908, the pattern of #906): the beat
    # says "menu" and stops when the menu hands back. The gate's own wait
    # -- the sign-in -- carries its beat inside recovery_login (#912).
    build_menu_gate || return 0
    attended "menu" build_menu
}
