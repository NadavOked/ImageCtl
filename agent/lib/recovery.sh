# recovery.sh -- the station recovery wizard: login, then deployment-type
# choice, then (for a single station) image pick and confirm. POSIX sh
# (busybox ash).
#
# It lived inside ui.sh until #564, on the reasoning progress.sh/pull.sh drew
# for the same wall in #562: one file was carrying two different jobs. The
# four functions left behind in ui.sh (ui_clear, ui_header, ui_waiting_draw,
# ui_unknown, ui_error_hold) are screens with nothing to decide -- they draw
# and return. Everything below reads an answer, checks it against the
# server, and decides what happens next: a login gate, a menu, a disk pick,
# a typed confirmation. That is the wizard, and it is the reason ui.sh had
# crossed 300 lines.
#
# Sourced after ui.sh: nothing here calls back into it, but recovery_flow is
# reached the same way ui_unknown and ui_waiting_draw are, from the same
# branch in imagectl-agent's main loop.

login_body() {
    # $1 = username, $2 = password. The interface body, built by hand
    # like hello -- so tests can parse it with real JSON.
    printf '{"username":"%s","password":"%s","mac":"%s"}' \
        "$(json_escape "$1")" "$(json_escape "$2")" "$MAC"
}

login_post() {
    # $1 = body file, $2 = response file. Prints the HTTP status code.
    #
    # Deliberately not http_post_json: `curl -f` collapses every answer
    # from 400 up into exit 22, and 401 ("we asked, and the password is
    # wrong") is not 503 or 000 ("we never got an answer"). Folding those
    # together is exactly the shape rule 5 is about -- so this asks for the
    # code itself, and the caller decides on positive evidence.
    curl -sS --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -o "$2" -w '%{http_code}' \
        -H "Content-Type: application/json" \
        --data-binary "@$1" "$SERVER/api/v1/agent/login" 2>/dev/null
}

recovery_login() {
    # Three attempts against the console users (spec 15: the password
    # lives on the server, never on a machine students control).
    # The credentials are kept for the wizard: opening a class round
    # authenticates with them again on the server side.
    #
    # Sets RECOVERY_LOGIN_FAIL on failure: "rejected" (the server checked
    # and said no) or "unverified" (it never answered). A retry only makes
    # sense for the first one -- typing the password again does not fix a
    # cable, and telling a technician "wrong username or password" when
    # nothing was checked sends them after the wrong fault.
    RECOVERY_USER=""
    RECOVERY_PASS=""
    RECOVERY_LOGIN_FAIL=""
    _try=0
    while [ "$_try" -lt 3 ]; do
        _try=$((_try + 1))
        printf "  Username: "
        read -r _user
        printf "  Password: "
        stty -echo 2>/dev/null
        read -r _pass
        stty echo 2>/dev/null
        echo
        login_body "$_user" "$_pass" > "$RUN_DIR/login.json"
        _code=$(login_post "$RUN_DIR/login.json" "$RUN_DIR/login_resp.json")
        case "$_code" in
            200)
                rm -f "$RUN_DIR/login.json"
                RECOVERY_USER="$_user"
                RECOVERY_PASS="$_pass"
                log "recovery login: $_user"
                return 0
                ;;
            401)
                RECOVERY_LOGIN_FAIL="rejected"
                echo "  Wrong username or password."
                ;;
            *)
                rm -f "$RUN_DIR/login.json"
                RECOVERY_LOGIN_FAIL="unverified"
                log "recovery login: no verdict from the server (http $_code)"
                echo "  The server did not answer -- the password was not checked."
                return 1
                ;;
        esac
    done
    rm -f "$RUN_DIR/login.json"
    return 1
}

login_failed() {
    # Both roads end at the local disk (rule 1), but the journal has to say
    # which one it was: a wrong password is fixed by a person, an unanswered
    # login is fixed by a cable or a server.
    if [ "${RECOVERY_LOGIN_FAIL:-}" = "unverified" ]; then
        die_local "the server never checked the password"
    fi
    die_local "login failed"
}

recovery_flow() {
    # The station wizard, reached via ESC at the GRUB menu:
    # login -> deployment type -> class/image (flows 13.2 and 13.3).
    #
    # The login is first (#80). Not only because being stopped after
    # choosing is rude: the menu is the list of what this server can be
    # told to do -- including that "class round" exists at all -- and that
    # list is not for someone who has no account here. Whoever does not get
    # past the three attempts never sees it, and boots from the local disk.
    # Uses: RESP (server answer file), SERVER, MAC.
    recovery_gate
    recovery_menu
}

recovery_gate() {
    # Three answers, not two: a login is required, a login is waived, or
    # the server never said. Only the middle one skips the screen -- and
    # "we could not tell" is an unclear state, which ends where every
    # unclear state ends.
    case "$(json_get "$RESP" ".ui.require_login")" in
        false)
            # A station on the deployment vlan with a round open (#42):
            # there is no login screen there today, and moving the login
            # earlier must not invent one.
            return 0
            ;;
        true) ;;
        *) die_local "the server did not say whether recovery needs a login" ;;
    esac

    ui_clear; ui_header
    echo "  Recovery. Sign in with your console account."
    echo
    recovery_login || login_failed
}

recovery_menu() {
    ui_clear; ui_header
    echo "  Deployment type:"
    echo
    echo "    1) Single station -- restore this computer only"
    echo "    2) Class round -- wake a whole class and clone it"
    echo
    printf "  Choose [1-2], or 0 to boot normally: "
    read -r _mode
    case "$_mode" in
        0|"") die_local "user cancelled" ;;
        1) single_station_flow ;;
        2)
            # Opening a round is a decision -- it always authenticates.
            # Normally the gate already did; the one path that arrives here
            # without credentials is the deployment-vlan station, where the
            # server waives the login screen for plain restores (#42).
            if [ -z "${RECOVERY_USER:-}" ]; then
                ui_clear; ui_header
                echo "  Opening a class round. Sign in with your console account."
                echo
                recovery_login || login_failed
            fi
            class_round_flow && return 0
            die_local "class round was not opened"
            ;;
        *) die_local "invalid choice" ;;
    esac
}

single_station_flow() {
    _ids=$(json_get_join "$RESP" ".allowed_images")
    [ -n "$_ids" ] || die_local "no images are allowed for this machine"

    ui_clear; ui_header
    echo "  Single-station restore. Available images:"
    echo
    _i=0
    for _id in $_ids; do
        _i=$((_i + 1))
        http_get "$SERVER/api/v1/images/$_id/manifest" \
            > "$RUN_DIR/manifest.$_i.json" || die_local "manifest fetch failed"
        _nm=$(json_get "$RUN_DIR/manifest.$_i.json" ".name")
        _fam=$(json_get "$RUN_DIR/manifest.$_i.json" ".family")
        echo "    $_i) $_nm  [$_fam GB family]"
        echo "$_id" > "$RUN_DIR/choice.$_i.id"
    done
    echo
    printf "  Choose an image [1-%s], or 0 to boot normally: " "$_i"
    read -r _c
    case "$_c" in
        0) die_local "user cancelled recovery" ;;
        *[!0-9]*|"") die_local "invalid choice" ;;
    esac
    [ "$_c" -ge 1 ] && [ "$_c" -le "$_i" ] || die_local "invalid choice"

    _disk=$(pick_internal_disk) || die_local "no internal disk found"
    _img=$(cat "$RUN_DIR/choice.$_c.id")
    _manifest="$RUN_DIR/manifest.$_c.json"

    echo
    echo "  Image:  $(json_get "$_manifest" ".name")"
    echo "  Target: /dev/$_disk -- ALL DATA ON IT WILL BE ERASED."
    printf "  Type ERASE to continue: "
    read -r _confirm
    [ "$_confirm" = "ERASE" ] || die_local "user cancelled recovery"

    _total=$(json_get "$_manifest" ".total_compressed_bytes")
    target_init "$_disk" "$_total"

    # The server is told before the first byte moves. A pull announced at the
    # end is a pull nobody could watch, and watching is the whole point (#63).
    # If the stream does not open -- an older server, a server that just fell
    # over -- pull_open has already said so in the journal and the restore
    # goes on regardless (rule 1): the disk is being erased either way, and a
    # person is standing at this machine waiting for it.
    _ppid=""
    if pull_open "$SERVER" "$MAC" "$_img" \
            "${RECOVERY_USER:-}" "${RECOVERY_PASS:-}"; then
        progress_loop "$PULL_SESSION" "$MAC" "$SERVER" &
        _ppid=$!
    fi

    echo
    echo "  Writing... (progress is visible on the console as well)"
    if run_restore "unicast" "$_disk" "$SERVER" "$_img" "$_manifest"; then
        pull_close "$_ppid" "$PULL_SESSION" "$MAC" "$SERVER"
        echo
        echo "  Done. Set the computer name from Windows -- a single-station"
        echo "  restore has no round prefix to build it from."
        echo "  Press Enter to reboot."
        read -r _
        [ "${IMAGECTL_TEST:-0}" = "1" ] && return 0
        sync; reboot -f
    else
        # The reporter is left running when there is one: a failed pull stays
        # on the console until a person clears it. But it only exists if
        # `pull_open` succeeded -- otherwise `_ppid` is empty and this hold
        # was silent (#133), the worst case and not the mildest: the bytes
        # went to the disk anyway (rule 1), and the machine stands with a
        # disk in an unknown state while the console draws it as off.
        # `hold_beat` is a `hello` with joining:false -- no membership, right for a machine whose open failed (#108).
        ui_error_hold "restore did not complete" hold_beat
    fi
}

pick_internal_disk() {
    # First non-removable real disk. The classroom machines have exactly one.
    for _n in $(list_disks); do
        _rm=$(cat "$SYSROOT/sys/block/$_n/removable" 2>/dev/null || echo 1)
        [ "$_rm" = "0" ] && { echo "$_n"; return 0; }
    done
    return 1
}
