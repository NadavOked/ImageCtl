# guibridge.sh -- native GUI process and record dispatcher. POSIX sh.
# gui_main runs inside imagectl-kiosk. imagectl-agent also sources this file
# (function defs only, inert there) so the load gate (#84) sees every lib.
gui_error() {
    printf 'native-gui bridge: %s\n' "$*" >&2
    printf 'toast=%s\nform_error=%s\nroom_error=%s\nclass_error=%s\n' \
        "$*" "$*" "$*" "$*" > "$GUI_DIR/error.next" &&
        mv "$GUI_DIR/error.next" "$GUI_DIR/error"
    return 1
}

gui_role() {
    _gc=$(console_get me "$RUN_DIR/gui-me.json") || return 1
    [ "$_gc" = 200 ] || return 1
    GUI_ROLE=$(jq -er '.role | select(. == "admin" or . == "deploy")' \
        "$RUN_DIR/gui-me.json") || return 1
}

gui_auth() {
    rm -f "$CONSOLE_JAR" || return 1
    IFS= read -r RECOVERY_USER && IFS= read -r RECOVERY_PASS || return 1
    [ -n "$SERVER" ] && [ -n "$RECOVERY_USER" ] && [ -n "$RECOVERY_PASS" ] || return 1
    # stdout belongs exclusively to the role; never persist the password.
    if console_signin >&2 && gui_role; then
        unset RECOVERY_PASS
        printf '%s\n' "$GUI_ROLE"
        return 0
    fi
    unset RECOVERY_PASS
    rm -f "$CONSOLE_JAR"
    gui_error 'Login was refused or could not be verified; see agent log' >&2
}

gui_capture() {
    [ "$GUI_ROLE" = admin ] || return 1
    build_name_ok "$name" || return 1
    # Check against fresh server inventory, not a GUI-supplied device path.
    http_get "$SERVER/api/v1/agent/state?mac=$MAC" > "$RUN_DIR/gui-inventory.json" || return 1
    jq -e --arg dev "$dev" '.known == true and .role == "build" and
        any(.disks[]; .dev == $dev and .removable == false)' \
        "$RUN_DIR/gui-inventory.json" >/dev/null || return 1
    case "$folder_new" in
        yes)
            build_name_ok "$folder" || return 1
            # Reuse the text flow with its input adapter in a subshell.
            (build_ask_name() { printf '%s\n' "$folder"; }; build_new_folder) || return 1
            ;;
        no) ;;
        *) return 1 ;;
    esac
    build_capture_post "$name" "${dev#/dev/}" "$folder" "$desc"
}

gui_dispatch() {
    # The cloner's SMART choice needs no login: a cloning machine is headless
    # and never signs in. Handle it before gui_role so the role check (which
    # would fail with no session) does not swallow the decision. Only on the
    # cloner screen, and only the three valid tokens.
    case "$token" in
        smart-*)
            # ה-GUI שולח `smart-<action>|<nonce>` -- ה-nonce של הבקשה
            # ש**צוירה**. הגשר מקבל רק אם ה-nonce הזה תואם ל-nonce של
            # הבקשה הפעילה. ‏nonce ייחודי לכל בקשה (מונוטוני), ולכן גם
            # דיסק שנתבקש שוב (אותו port, nonce חדש) דוחה קליק מושהה עם
            # ה-nonce הישן -- מה שבדיקת port בלבד לא תפסה. קליק בלי בקשה
            # פעילה או על מסך אחר נדחה גם הוא.
            [ "${GUI_SCREEN:-}" = cloner ] || return 1
            case "$token" in *'|'*) ;; *) return 1 ;; esac    # ה-nonce חובה
            _sd_action=${token%%|*}
            case "$_sd_action" in smart-replace|smart-rescue|smart-skip) ;; *) return 1 ;; esac
            _sd_click_nonce=${token#*|}
            case "$_sd_click_nonce" in ''|*[!0-9-]*) return 1 ;; esac
            [ -s "$GUI_DIR/smart-request" ] || return 1
            IFS='|' read -r _sd_nonce _ < "$GUI_DIR/smart-request" || return 1
            [ -n "$_sd_nonce" ] || return 1
            [ "$_sd_click_nonce" = "$_sd_nonce" ] || return 1
            printf '%s|%s\n' "$_sd_nonce" "${_sd_action#smart-}" \
                > "$GUI_DIR/smart-decision.next" &&
                mv "$GUI_DIR/smart-decision.next" "$GUI_DIR/smart-decision"
            return $?
            ;;
    esac
    gui_role || { gui_error 'Session/role could not be verified; restart and sign in'; return 1; }
    case "$token" in
        capture|room|classes|restore) printf '%s\n' "$token" > "$GUI_DIR/mode" ;;
        back|again) printf 'menu\n' > "$GUI_DIR/mode" ;;
        capture-start) gui_capture ;;
        room-open)
            case "$image" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            case "$target" in ''|*[!0-9]*) return 1 ;; esac
            # room_open owns validation/body/POST; adapt only its prompts.
            (room_pick_image() { printf '%s\n' "$image"; }
             printf '%s\n' "$target" | room_open)
            ;;
        room-wake) room_action wake ;;
        room-start) room_action start ;;
        room-close|class-start|class-close)
            if [ "$token" = room-close ]; then
                _gp=room/close
            else
                # Act on the published view, never fetch a replacement round
                # here and accidentally start/close a different classroom.
                _gs=$(jq -er '.session | select(.group_role == "classroom") | .id' "$GUI_DIR/session-shown.json") || return 1
                case "$_gs" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
                _gp="sessions/$_gs/${token#class-}"
            fi
            jq -n --arg c "$confirm" '{confirm_name:$c}' > "$RUN_DIR/gui-action.json" || return 1
            _gc=$(console_post "$_gp" "$RUN_DIR/gui-action.json" "$RUN_DIR/gui-result.json") || return 1
            [ "$_gc" = 200 ] && jq -e '.ok == true' "$RUN_DIR/gui-result.json" >/dev/null
            ;;
        class-pick)
            case "$group" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            printf '%s\n%s\n' "$token" "$group" > "$GUI_DIR/handoff.next" &&
                mv "$GUI_DIR/handoff.next" "$GUI_DIR/handoff" || return 1
            return 2
            ;;
        restore-start)
            # #706: erase+write one image onto THIS machine. The GUI already
            # confirmed ERASE; re-check it here, and validate the id syntax.
            # The image itself is re-validated against fresh allowed_images in
            # single_restore_run before a byte is written (principle 5/7).
            case "$image" in ''|*[!A-Za-z0-9_.-]*) return 1 ;; esac
            [ "$confirm" = "ERASE" ] || return 1
            printf 'restore-start\n%s\n' "$image" > "$GUI_DIR/handoff.next" &&
                mv "$GUI_DIR/handoff.next" "$GUI_DIR/handoff" || return 1
            return 2
            ;;
        *) gui_error "Unknown action: $token" ;;
    esac
}

gui_records() {
    while IFS= read -r token; do
        dev='' name='' desc='' folder='' folder_new='' image='' target='' confirm='' group=''
        _complete=0; _bad=0; _seen='|'
        while IFS= read -r line; do
            [ -n "$line" ] || { _complete=1; break; }
            _key=${line%%=*}; _value=${line#*=}
            case "$_seen" in *"|$_key|"*) _bad=1 ;; esac
            _seen="$_seen$_key|"
            case "$line" in
                dev=*) dev=$_value ;; name=*) name=$_value ;; desc=*) desc=$_value ;;
                folder=*) folder=$_value ;; folder_new=*) folder_new=$_value ;;
                image=*) image=$_value ;; target=*) target=$_value ;;
                confirm=*) confirm=$_value ;; group=*) group=$_value ;;
                *) _bad=1 ;;
            esac
        done
        [ "$_complete" = 1 ] && [ "$_bad" = 0 ] || { gui_error 'Malformed/incomplete action record'; return 1; }
        rm -f "$GUI_DIR/error" || return 1
        gui_dispatch
        _gr=$?
        [ "$_gr" = 2 ] && return 0
        [ "$_gr" = 0 ] || gui_error "Action $token failed; no success confirmed (see agent log)"
    done
}

gui_cleanup() {
    for _pid in "${GUI_PID:-}" "${GUI_STATE_PID:-}"; do
        [ -n "$_pid" ] || continue
        if kill -0 "$_pid" 2>/dev/null; then
            kill "$_pid" || printf 'native-gui: cannot stop %s\n' "$_pid" >&2
        fi
        # Ceiling, never a bare wait: a GUI child that ignores SIGTERM must
        # not hang cleanup for ever -- wait_pid escalates to SIGKILL (waits.sh).
        wait_pid "$_pid" "$WAIT_HELPER_S" "native GUI process $_pid" || true
    done
    # A stale click must never authorize the next disk (principle 5).
    rm -f "$CONSOLE_JAR" "$GUI_DIR/actions" \
        "$GUI_DIR/smart-request" "$GUI_DIR/smart-decision"
}

gui_main() {
    . "$LIB_DIR/common.sh"
    . "$LIB_DIR/jsonq.sh"
    . "$LIB_DIR/waits.sh"
    . "$LIB_DIR/buildmenu.sh"
    . "$LIB_DIR/buildcapture.sh"
    . "$LIB_DIR/roomflow.sh"
    . "$LIB_DIR/guistate.sh"
    . "$LIB_DIR/sysinfo.sh"   # disk_port, for the cloner state producer
    . "$LIB_DIR/progress.sh"  # target_bytes, the per-drawer local truth
    . "$LIB_DIR/clonergui.sh"
    GUI_DIR=${GUI_DIR:-$RUN_DIR/gui}
    CONSOLE_JAR="$GUI_DIR/console.jar"
    export SERVER MAC IP RUN_DIR GUI_DIR CONSOLE_JAR HTTP_TIMEOUT HTTP_RETRIES
    [ "${1:-}" = --auth ] && { gui_auth; return $?; }
    umask 077
    mkdir -p "$GUI_DIR" || return 1
    rm -f "$GUI_DIR/handoff" "$GUI_DIR/error" "$CONSOLE_JAR" "$GUI_DIR/actions" || return 1
    if [ "${GUI_SCREEN:-}" = class ]; then _initial=classes; else _initial=menu; fi
    printf '%s\n' "$_initial" > "$GUI_DIR/mode" || return 1
    printf 'message=Connecting to server|Waiting for verified station state\n' > "$GUI_DIR/state" || return 1
    mkfifo "$GUI_DIR/actions" || return 1
    trap gui_cleanup EXIT
    trap 'exit 1' HUP INT TERM
    export FONTCONFIG_FILE=/etc/imagectl/fonts.conf
    if [ "${GUI_SCREEN:-}" = cloner ]; then
        cloner_gui_state_loop & GUI_STATE_PID=$!
    else
        gui_state_loop & GUI_STATE_PID=$!
    fi
    set --
    [ "${GUI_SCREEN:-}" = class ] && set -- --screen class
    [ "${GUI_SCREEN:-}" = cloner ] && set -- --screen cloner
    # ‏fbdev כברירת מחדל: על i915 אמיתי מסלול ה-DRM/KMS עושה modeset אבל
    # מצייר פריים שחור; ‏/dev/fb0 של fbcon (שבו GRUB ולוג האתחול משתמשים)
    # הוא הפלט המוכח על החומרה הזו (#641). לעקוף עם GUI_BACKEND=drm|auto.
    /usr/bin/imagectl-station-gui "$@" --backend "${GUI_BACKEND:-fbdev}" --mac "$MAC" --ip "$IP" \
        --state "$GUI_DIR/state" --auth-cmd '/usr/bin/imagectl-kiosk --auth' > "$GUI_DIR/actions" &
    GUI_PID=$!
    gui_records < "$GUI_DIR/actions"
}
