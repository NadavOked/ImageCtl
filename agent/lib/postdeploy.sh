# postdeploy.sh -- driver staging into the restored Windows (#720;
# interfaces.md section 17). Runs after restore+grow and the hostname,
# before the reboot. POSIX sh (busybox ash).
#
# This is NOT an offline injection (no DISM from Linux): the packages the
# server matched to this machine's inventory are copied to
# \ImageCtl\Drivers\<package>\ on the restored NTFS, and that folder is
# added to DevicePath (SOFTWARE hive, REG_EXPAND_SZ -- hivewrite -x) so
# that Windows searches it on first boot when it meets a device without a
# driver. "staged" is not "installed"; a storage driver Windows needs in
# order to boot at all is out of scope until the WinPE stage (v2).
#
# Never fatal, never silent (עיקרון 5): the restore is complete and a
# machine that boots without its NIC driver is fixable in a minute. The
# outcome -- staged / no_match / skipped / failed <reason> -- is written to
# $RUN_DIR/drivers.json and travels in the final report (progress.sh), so
# "done, drivers not staged" is a state the console shows, not a lost log
# line. Every file's sha256 is checked twice: after the download, and again
# on the NTFS after the copy -- "copied" is what reads back, not cp's exit.

DRIVERS_WIN_DIR="ImageCtl/Drivers"
DEVICE_PATH_KEY='Microsoft\Windows\CurrentVersion'
DEVICE_PATH_ENTRY='%SystemRoot%\..\ImageCtl\Drivers'

_drivers_result() {
    # $1 = state, $2 = JSON array body of package names ("" = none),
    # $3 = error/reason text (optional). Writes drivers.json and logs.
    if [ -n "${3:-}" ]; then
        _key="error"; [ "$1" = "skipped" ] && _key="reason"
        printf '{"state":"%s","packages":[%s],"%s":"%s"}\n' \
            "$1" "$2" "$_key" "$(json_escape "$3")" > "$RUN_DIR/drivers.json"
    else
        printf '{"state":"%s","packages":[%s]}\n' "$1" "$2" > "$RUN_DIR/drivers.json"
    fi
    log "drivers: $(cat "$RUN_DIR/drivers.json")"
}

_drivers_fail() {
    # $1 = reason. Unmounts if a mount is open, records, returns 0 (never fatal).
    [ -n "${_dr_mnt:-}" ] && { umount "$_dr_mnt" >> "$LOG_FILE" 2>&1; _dr_mnt=""; }
    _drivers_result "failed" "" "$1"
    return 0
}

_file_sha256() {
    # $1 = file. The digest, or nothing when it cannot be computed.
    sha256sum "$1" 2>/dev/null | awk '{ print $1 }'
}

_drivers_fetch() {
    # Downloads every file of every matched package into $RUN_DIR/drivers/,
    # verifying sha256 as it goes. Reads the rows file ($1): name|path|sha|url.
    # Returns 1 with the reason in $_dr_why.
    while IFS='|' read -r _pkg _path _sha _url; do
        case "$_pkg" in ''|*/*|*..*|.*) _dr_why="bad package name '$_pkg'"; return 1 ;; esac
        case "$_path" in ''|/*|*..*|*\\*) _dr_why="bad file path '$_path' in $_pkg"; return 1 ;; esac
        _dst="$RUN_DIR/drivers/$_pkg/$_path"
        mkdir -p "${_dst%/*}"
        if ! http_get_stream "$SERVER$_url" > "$_dst" 2>> "$LOG_FILE"; then
            _dr_why="download failed: $_pkg/$_path"; return 1
        fi
        _got=$(_file_sha256 "$_dst")
        if ! is_sha256 "$_got"; then
            _dr_why="sha256 not computed: $_pkg/$_path"; return 1
        elif [ "$_got" != "$_sha" ]; then
            _dr_why="sha256 mismatch: $_pkg/$_path"; return 1
        fi
    done < "$1"
    return 0
}

_drivers_copy() {
    # $1 = mount point, $2 = rows file. Copies each package folder onto the
    # NTFS and reads every file back against the manifest's sha256.
    while IFS='|' read -r _pkg _path _sha _url; do
        _to="$1/$DRIVERS_WIN_DIR/$_pkg/$_path"
        mkdir -p "${_to%/*}" 2>> "$LOG_FILE" || { _dr_why="mkdir failed on the disk: $_pkg"; return 1; }
        if ! cp "$RUN_DIR/drivers/$_pkg/$_path" "$_to" 2>> "$LOG_FILE"; then
            _dr_why="copy failed: $_pkg/$_path"; return 1
        fi
        if [ "$(_file_sha256 "$_to")" != "$_sha" ]; then
            _dr_why="read-back mismatch on the disk: $_pkg/$_path"; return 1
        fi
    done < "$2"
    return 0
}

_device_path_add() {
    # $1 = SOFTWARE hive. Appends the drivers folder to DevicePath, keeping
    # what is there ("%SystemRoot%\inf" at least), as REG_EXPAND_SZ -- the
    # value's own type, without which %SystemRoot% is not expanded. Idempotent.
    _cur=$(hivewrite -g "$1" "$DEVICE_PATH_KEY" DevicePath 2>> "$LOG_FILE")
    _grc=$?
    if [ "$_grc" -ne 0 ]; then _dr_why="could not read DevicePath (rc=$_grc)"; return 1; fi
    case "$_cur" in
        *ImageCtl\\Drivers*) log "DevicePath already lists ImageCtl\\Drivers"; return 0 ;;
        '') _new="$DEVICE_PATH_ENTRY" ;;
        *)  _new="$_cur;$DEVICE_PATH_ENTRY" ;;
    esac
    hivewrite -x "$1" "$DEVICE_PATH_KEY" DevicePath "$_new" >> "$LOG_FILE" 2>&1
    _wrc=$?
    _back=$(hivewrite -g "$1" "$DEVICE_PATH_KEY" DevicePath 2>> "$LOG_FILE")
    if [ "$_wrc" -ne 0 ] || [ "$_back" != "$_new" ]; then
        _dr_why="DevicePath write not verified (rc=$_wrc, read back '$_back')"; return 1
    fi
    log "DevicePath = $_new"
    return 0
}

stage_drivers() {
    # $1 = disk name, $2 = manifest file. Always returns 0, and always leaves
    # the top-level state at "done": the restore IS complete, and a member
    # left in "staging" would never be terminal for the server (reports.py).
    _stage_drivers "$@"
    echo "done" > "$RUN_DIR/state"
    return 0
}

_stage_drivers() {
    _dr_mnt=""; _dr_why=""
    rm -f "$RUN_DIR/drivers.json"
    if ! manifest_plan "$2" 2>/dev/null | awk -F'|' '$3 == "windows"' | grep -q .; then
        _drivers_result "skipped" "" "not_windows"
        return 0
    fi
    echo "staging" > "$RUN_DIR/state"

    _ans="$RUN_DIR/drivers.answer"
    if ! http_get "$SERVER/api/v1/agent/drivers?mac=$MAC" > "$_ans" 2>> "$LOG_FILE"; then
        _drivers_fail "the server did not answer the drivers query"; return 0
    fi
    [ "$(json_get "$_ans" ".ok")" = "true" ] || { _drivers_fail "the drivers answer was not read"; return 0; }
    # עיקרון 5: "אין מלאי בשרת" אינו "אין חבילה תואמת" -- הראשון הוא כשל.
    [ "$(json_get "$_ans" ".inventory")" = "true" ] || { _drivers_fail "the server has no inventory for this machine"; return 0; }

    _rows="$RUN_DIR/drivers.rows"
    json_get "$_ans" '.packages[] as $p | $p.files[] | "\($p.name)|\(.path)|\(.sha256)|\(.url)"' > "$_rows"
    _names=$(json_get "$_ans" '.packages[].name')
    case "$_names" in
        null) _drivers_fail "could not read the package list"; return 0 ;;
        '')   _drivers_result "no_match" ""; return 0 ;;
    esac
    _list=""
    for _n in $_names; do _list="$_list${_list:+,}\"$(json_escape "$_n")\""; done
    log "drivers: staging $(echo "$_names" | tr '\n' ' ')"

    rm -rf "$RUN_DIR/drivers"
    _drivers_fetch "$_rows" || { _drivers_fail "$_dr_why"; return 0; }

    _dr_mnt=$(_mount_windows "$1" "$2") || { _dr_mnt=""; _drivers_fail "could not mount the windows partition"; return 0; }
    _hive="$_dr_mnt/Windows/System32/config/SOFTWARE"
    [ -f "$_hive" ] || { _drivers_fail "SOFTWARE hive not found"; return 0; }
    for _n in $_names; do rm -rf "${_dr_mnt:?}/${DRIVERS_WIN_DIR:?}/$_n"; done   # SC2115: לעולם לא "/"
    _drivers_copy "$_dr_mnt" "$_rows" || { _drivers_fail "$_dr_why"; return 0; }
    _device_path_add "$_hive" || { _drivers_fail "$_dr_why"; return 0; }
    _umount_checked "$_dr_mnt"
    _umrc=$?
    _dr_mnt=""
    [ "$_umrc" -eq 0 ] || { _drivers_fail "could not unmount after staging (rc=$_umrc)"; return 0; }

    _drivers_result "staged" "$_list"
    return 0
}
