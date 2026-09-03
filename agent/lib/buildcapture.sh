# buildcapture.sh -- ordering a capture from the build machine itself (#135).
# POSIX sh (busybox ash). No bashisms.
#
# Spec 26 step 3 is "name, description, target folder", and that is the order
# here: the existing folders, one of them or a new one, then the image name,
# then the source disk. The POST is the same one the console sends, so the
# work itself runs through the ordinary task path -- the next hello brings
# `task.type=capture` back and do_task does exactly what it always did.
#
# Reading the folders is open to every signed-in user; creating one is
# admin_only. The whole capture is admin_only too, so the two agree -- but
# the menu still has to know, because offering an option that comes back 403
# is worse than not offering it.

build_pick_folder() {
    # Prints the chosen folder name on stdout; the screen goes to stderr.
    _code=$(console_get "folders" "$RUN_DIR/folders.json")
    if [ "$_code" != "200" ]; then
        console_say "$_code" "Could not read the folders" >&2
        return 1
    fi
    _count=$(jq 'length' "$RUN_DIR/folders.json" 2>/dev/null)
    case "$_count" in
        ""|*[!0-9]*)
            # A 200 whose body is not a list is not an empty library.
            echo "  The server sent a folder list this agent cannot read." >&2
            sleep 5
            return 1
            ;;
    esac

    # Everything this function draws goes to stderr, including ui_clear and
    # `log`: its stdout is the folder name the caller reads back.
    rm -f "$RUN_DIR/folder_names.txt"
    ui_clear >&2; ui_header >&2
    echo "  Target folder for the new image:" >&2
    echo >&2
    jq -r '.[] | "\(.name)|\(.images)"' "$RUN_DIR/folders.json" \
    | { _i=0
        while IFS='|' read -r _nm _cnt; do
            _i=$((_i + 1))
            printf '    %s) %s  (%s images)\n' "$_i" "$_nm" "$_cnt" >&2
            echo "$_nm" >> "$RUN_DIR/folder_names.txt"
        done; }
    _new=$((_count + 1))
    printf '    %s) A new folder\n' "$_new" >&2
    echo >&2
    printf "  Choose [1-%s], or 0 to go back: " "$_new" >&2
    read -r _c
    case "$_c" in
        0|"") return 1 ;;
        *[!0-9]*) return 1 ;;
    esac
    [ "$_c" = "$_new" ] && { build_new_folder; return $?; }
    { [ "$_c" -ge 1 ] && [ "$_c" -le "$_count" ]; } || return 1
    sed -n "${_c}p" "$RUN_DIR/folder_names.txt"
}

build_new_folder() {
    # Prints the name of the folder it created. POST /folders is admin_only;
    # a 403 here means the account is not what the menu was built from, and
    # that stops the flow instead of carrying on with a folder that does not
    # exist.
    _nm=$(build_ask_name "New folder name") || return 1
    printf '{"name":"%s"}' "$(json_escape "$_nm")" \
        > "$RUN_DIR/folder_new.json"
    _code=$(console_post "folders" "$RUN_DIR/folder_new.json" \
        "$RUN_DIR/folder_resp.json")
    if [ "$_code" != "200" ]; then
        console_say "$_code" "Could not create the folder" >&2
        return 1
    fi
    log "folder created from the build machine: $_nm" >&2
    echo "$_nm"
}

build_capture_post() {
    # $1 name, $2 disk, $3 folder. The same body the console sends.
    printf '{"mac":"%s","name":"%s","disk":"%s","folder":"%s"}' \
        "$MAC" "$(json_escape "$1")" "$(json_escape "$2")" \
        "$(json_escape "$3")" > "$RUN_DIR/capture_req.json"
    _code=$(console_post "tasks/capture" "$RUN_DIR/capture_req.json" \
        "$RUN_DIR/capture_resp.json")
    if [ "$_code" != "200" ]; then
        console_say "$_code" "The capture was not created"
        return 1
    fi
    # 200 is not a task; the id is. An answer we cannot read is a capture we
    # cannot claim was ordered (rule 5).
    _tid=$(json_get "$RUN_DIR/capture_resp.json" ".id")
    case "$_tid" in
        ""|null)
            echo "  The server answered 200 without a task id."
            log "capture order: 200 without an id"
            sleep 5
            return 1
            ;;
    esac
    log "capture ordered from the build machine: $_tid folder=$3 name=$1"
    return 0
}

build_capture_flow() {
    console_signin || return 1
    _folder=$(build_pick_folder) || return 1
    _name=$(build_ask_name "Image name") || return 1
    _disk=$(pick_internal_disk) || {
        echo "  No internal disk to capture from."
        sleep 5
        return 1
    }

    ui_clear; ui_header
    echo "  About to capture:"
    echo
    echo "    Image:   $_name"
    echo "    Folder:  $_folder"
    echo "    Source:  /dev/$_disk  (read only -- nothing is written to it)"
    echo
    printf "  Start the capture? [y/N]: "
    read -r _yes
    case "$_yes" in
        y|Y|yes|YES) ;;
        *) return 1 ;;
    esac
    build_capture_post "$_name" "$_disk" "$_folder"
}
