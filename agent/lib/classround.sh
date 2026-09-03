# classround.sh -- opening a class round from one station (flow 13.3).
# POSIX sh (busybox ash).
#
# The operator signs in at any machine, picks a class and an image, and the
# server wakes the rest of the class over WoL. The round lives on the server,
# so once it is open the operator can walk away -- this station joins it like
# any other machine, through the normal hello loop.

class_menu() {
    # Prints the chosen group id, or fails. Uses $RUN_DIR/classes.json.
    http_get "$SERVER/api/v1/agent/groups" > "$RUN_DIR/classes.json" || return 1
    _count=$(jq 'length' "$RUN_DIR/classes.json" 2>/dev/null)
    [ -n "$_count" ] && [ "$_count" -gt 0 ] || {
        echo "  No classes are defined on the server yet." >&2
        return 1
    }

    echo "  Classes:" >&2
    _i=0
    jq -r '.[] | "\(.id)|\(.label)|\(.machines)"' "$RUN_DIR/classes.json" \
    | while IFS='|' read -r _id _label _machines; do
        _i=$((_i + 1))
        printf '    %s) %s  (%s machines)\n' "$_i" "$_label" "$_machines" >&2
        echo "$_id" >> "$RUN_DIR/class_ids.txt"
    done
    printf "  Choose a class [1-%s], or 0 to go back: " "$_count" >&2
    read -r _c
    case "$_c" in *[!0-9]*|"") return 1 ;; esac
    [ "$_c" -ge 1 ] && [ "$_c" -le "$_count" ] || return 1
    sed -n "${_c}p" "$RUN_DIR/class_ids.txt"
}

image_menu() {
    # Prints the chosen image id from allowed_images, or fails.
    _ids=$(json_get_join "$RESP" ".allowed_images")
    [ -n "$_ids" ] || { echo "  No images are available." >&2; return 1; }
    echo "  Images:" >&2
    # The counter is bumped *after* the manifest arrives, not before (#99).
    # It plays three roles at once -- the number on the screen, the upper
    # bound of the check below, and (implicitly) the line number in the file
    # -- and bumping it first split all three apart at the first `continue`:
    # the screen offered 3 while the file had 2 lines, so the operator's
    # choice either came back empty or, worse, silently selected a different
    # image and wiped a classroom with it.
    : > "$RUN_DIR/image_ids.txt"
    _i=0
    for _id in $_ids; do
        if ! http_get "$SERVER/api/v1/images/$_id/manifest" \
                > "$RUN_DIR/cm.next.json" 2>/dev/null; then
            # Named, not dropped: an image missing from the list is a fact
            # the operator has to see, or he looks for one that is not there.
            printf '    (skipped %s -- its manifest could not be read)\n' \
                "$_id" >&2
            continue
        fi
        _i=$((_i + 1))
        mv -f "$RUN_DIR/cm.next.json" "$RUN_DIR/cm.$_i.json"
        printf '    %s) %s  [%s GB family]\n' \
            "$_i" "$(json_get "$RUN_DIR/cm.$_i.json" ".name")" \
            "$(json_get "$RUN_DIR/cm.$_i.json" ".family")" >&2
        echo "$_id" >> "$RUN_DIR/image_ids.txt"
    done
    [ "$_i" -gt 0 ] || { echo "  No image could be read." >&2; return 1; }
    printf "  Choose an image [1-%s]: " "$_i" >&2
    read -r _c
    case "$_c" in *[!0-9]*|"") return 1 ;; esac
    [ "$_c" -ge 1 ] && [ "$_c" -le "$_i" ] || return 1
    sed -n "${_c}p" "$RUN_DIR/image_ids.txt"
}

machine_menu() {
    # $1 group id. Prints a JSON array of the chosen MACs, or nothing at
    # all for the whole class. The list shows names -- the MAC is only
    # the identifier that goes back to the server.
    http_get "$SERVER/api/v1/agent/groups/$1/machines" \
        > "$RUN_DIR/machines.json" || return 1
    _count=$(jq 'length' "$RUN_DIR/machines.json" 2>/dev/null)
    [ -n "$_count" ] && [ "$_count" -gt 0 ] || {
        echo "  The class has no registered machines." >&2
        return 1
    }

    echo "  Machines in this class:" >&2
    rm -f "$RUN_DIR/machine_macs.txt"
    _i=0
    jq -r '.[] | "\(.mac)|\(.name)"' "$RUN_DIR/machines.json" \
    | while IFS='|' read -r _mac _name; do
        _i=$((_i + 1))
        printf '    %2s) %s\n' "$_i" "$_name" >&2
        echo "$_mac" >> "$RUN_DIR/machine_macs.txt"
    done
    printf "  Machines to deploy (e.g. 1 4 7), or Enter for the whole class: " >&2
    read -r _picks
    [ -n "$_picks" ] || return 0

    _macs=""
    for _p in $_picks; do
        case "$_p" in *[!0-9]*|"") echo "  Not a number: $_p" >&2; return 1 ;; esac
        { [ "$_p" -ge 1 ] && [ "$_p" -le "$_count" ]; } || {
            echo "  No machine number $_p in the list." >&2
            return 1
        }
        _mac=$(sed -n "${_p}p" "$RUN_DIR/machine_macs.txt")
        _macs="$_macs${_macs:+,}\"$_mac\""
    done
    printf '[%s]' "$_macs"
}

open_round_body() {
    # $1 group, $2 image, $3 optional JSON array of chosen MACs.
    # Credentials from the recovery login. Without $3 the server deploys
    # to the whole class. Printed on stdout only -- see open_round_post.
    printf '{"username":"%s","password":"%s","mac":"%s","group_id":"%s","image_id":"%s"' \
        "$(json_escape "$RECOVERY_USER")" "$(json_escape "$RECOVERY_PASS")" \
        "$MAC" "$1" "$2"
    [ -n "${3:-}" ] && printf ',"macs":%s' "$3"
    printf '}'
}

open_round_post() {
    # $1 group, $2 image, $3 optional MAC array, $4 response file.
    #
    # The body carries the operator's console password, so it reaches curl
    # over a pipe and never becomes a file. It used to be written to
    # $RUN_DIR/open.json and removed after the POST -- but $RUN_DIR is tmpfs
    # on a machine that stands in a classroom, and that `rm` only ran if curl
    # came back. A hang, a power switch or a panic left the password sitting
    # there. A cleanup that has to succeed for the fix to hold is not a fix.
    #
    # `--data-binary @-` reads stdin into memory before sending, so it still
    # carries a Content-Length and --retry can replay the body.
    open_round_body "$1" "$2" "$3" | curl -sfS \
        --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -H "Content-Type: application/json" \
        --data-binary @- -o "$4" "$SERVER/api/v1/agent/sessions"
}

class_round_flow() {
    rm -f "$RUN_DIR/class_ids.txt" "$RUN_DIR/image_ids.txt"
    _group=$(class_menu) || return 1
    _machines=$(machine_menu "$_group") || return 1
    _image=$(image_menu) || return 1

    if ! open_round_post "$_group" "$_image" "$_machines" \
            "$RUN_DIR/open_resp.json" 2>/dev/null; then
        echo "  Could not open the round (is another round already active?)"
        sleep 5
        return 1
    fi

    ui_clear; ui_header
    echo "  Round $(json_get "$RUN_DIR/open_resp.json" ".prefix") is open."
    echo "  The server is waking the class over the network."
    echo
    echo "  This station will join automatically. You can walk away --"
    echo "  the round runs on the server, not on this computer."
    sleep 4
    # חוזרים ללולאה הראשית: ה-hello הבא ימצא סבב פתוח ויצטרף אליו.
}
