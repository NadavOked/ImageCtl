# lcplan.sh -- #1322 local clone: the source described as a manifest, the
# temporary shrink of the source, and the read-back of each target's table.
# POSIX sh (busybox ash).
#
# The copy reuses the restore path end to end, so the source is described in
# the shape that path already reads: a manifest exactly like capture.sh writes
# (index, type_guid, unique_guid, attrs, role, fs, uuid, start_sector,
# size_bytes, expandable, disk_guid, sector_size). apply_gpt writes it to each
# target with the disk GUID and every partition's unique GUID (#26/#1212),
# expand_last grows the last system partition to the end of a larger target
# and moves what follows it to the tail (#58/#648), verify_table reads the
# GUIDs back. Nothing here re-implements those -- see expand.sh.
#
# A smaller target (owner, 13/10): the source's NTFS system partitions are
# shrunk IN PLACE with the capture's own code (shrink.sh, #87/#929), the
# compacted layout is copied, and the source is grown back after -- the
# server records the original layout first (#926). The question shrink.sh
# asks a person is the confirm dialog itself: the start record carries it.

LC_ERROR=""

lc_source_parts() {
    # $1 = source disk. $LC/run/parts.txt (capture's parts.txt shape) and
    # $LC/run/entries (idx|name|attrs). 1 + LC_ERROR on anything unread.
    LC_ERROR=""; _sp_r="$LC/run"; mkdir -p "$_sp_r"
    lc_parts "$1" "$_sp_r/parts.txt" || { LC_ERROR="לא הצלחנו לקרוא את טבלת המחיצות של המקור"; return 1; }
    [ -s "$_sp_r/parts.txt" ] || { LC_ERROR="אין מחיצות על דיסק המקור"; return 1; }
    LC_ERROR=$(unique_guid_gate "$_sp_r/parts.txt" "$_sp_r/parts.txt.fail")
    [ -z "$LC_ERROR" ] || return 1
    : > "$_sp_r/entries"
    while IFS='|' read -r _sp_i _sp_rest; do
        _shrink_entry "$1" "$_sp_i" > "$_sp_r/entry" || { LC_ERROR=$SHRINK_ERROR; return 1; }
        printf '%s|%s\n' "$_sp_i" "$(cat "$_sp_r/entry")" >> "$_sp_r/entries"
    done < "$_sp_r/parts.txt"
}

lc_entry() {
    # $1 = idx, $2 = 2 (name) or 3 (attrs), from $LC/run/entries.
    awk -F'|' -v i="$1" -v f="$2" '$1 == i { print $f; exit }' "$LC/run/entries"
}

lc_shrink_source() {
    # $1 = source disk. The capture's shrink, unchanged, in a subshell whose
    # only adapter is the question: the person already answered it in the
    # confirm dialog (lc-start carried shrink=1). shrink_before_capture opens
    # the server record (#926) before the first write and rewrites parts.txt
    # to the compacted layout. 1 + LC_ERROR = the source was not shrunk, or
    # was put back (shrink.sh's own rule).
    LC_ERROR=""; rm -f "$LC/run/shrink.err"
    (
        attended() { echo continue; }
        CAPTURE_SINK=upload
        shrink_before_capture "$1" "$LC/run/parts.txt" 512 \
            || { printf '%s' "${SHRINK_ERROR:-הכיווץ נכשל}" > "$LC/run/shrink.err"; exit 1; }
    ) < /dev/null && return 0
    LC_ERROR=$(cat "$LC/run/shrink.err" 2>/dev/null)
    # A failure after the first write leaves the mark: put the source back now.
    shrink_restore_source "$1" || LC_ERROR="$LC_ERROR; $(cat "$RUN_DIR/targets/$1/error" 2>/dev/null)"
    return 1
}

lc_manifest() {
    # $1 = source disk, $2 = parts file (compacted if the source was shrunk).
    # Writes $LC/run/manifest.json. The file field is "local:pN": apply_gpt
    # refuses a non-swap partition without one (#424), and there is no file.
    _mf_r="$LC/run"; _mf_j=""
    _mf_sec=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null); case "$_mf_sec" in ''|*[!0-9]*) return 1 ;; esac
    _mf_dg=$(sgdisk -p "$DEVROOT/$1" 2>> "$LOG_FILE" | awk -F': ' '/Disk identifier/ { print $2 }' | awk '{print $1}')
    valid_guid "$_mf_dg" || { LC_ERROR="לא הצלחנו לקרוא את ה-GUID של דיסק המקור"; return 1; }
    while IFS='|' read -r _mf_i _mf_t _mf_u _mf_f _mf_s; do
        _mf_n=$(partition_node "$1" "$_mf_i"); _mf_fs=$(_fs_of "$_mf_n"); _mf_role=$(_partition_role "$_mf_t")
        [ "$_mf_fs" = swap ] && _mf_role=swap
        _mf_uuid=$(_uuid_of "$_mf_n"); if [ -n "$_mf_uuid" ]; then _mf_uuid="\"$_mf_uuid\""; else _mf_uuid=null; fi
        _mf_file="\"local:p$_mf_i\""; [ "$_mf_role" = swap ] && _mf_file=null
        _mf_used=$(awk -F'|' -v i="$_mf_i" '$1 == i { print $2; exit }' "$LC/inv/$1.used" 2>/dev/null)
        case "$_mf_used" in ''|*[!0-9]*) _mf_used=null ;; esac
        _mf_j="$_mf_j{\"index\":$_mf_i,\"type_guid\":\"$_mf_t\",\"unique_guid\":\"$_mf_u\",\"attrs\":\"$(lc_entry "$_mf_i" 3)\",\"uuid\":$_mf_uuid,\"role\":\"$_mf_role\",\"fs\":\"$_mf_fs\",\"start_sector\":$_mf_f,\"size_bytes\":$((_mf_s * 512)),\"used_bytes\":$_mf_used,\"file\":$_mf_file,\"sha256\":null,\"expandable\":false$(shrink_json_extra "$_mf_i" "$_mf_f" "$_mf_s" 512)},"
    done < "$2"
    _mf_j=$(_mark_expandable "${_mf_j%,}")
    printf '{"schema":1,"scheme":"gpt","sector_size":512,"disk_guid":"%s","source_disk_bytes":%s,"partitions":[%s]}\n' \
        "$_mf_dg" "$((_mf_sec * 512))" "$_mf_j" > "$_mf_r/manifest.json"
}

lc_set_names() {
    # $1 = target. The partition names ("Basic data partition", ...): apply_gpt
    # does not carry them, and a table that is "identical" carries them.
    while IFS='|' read -r _sn_i _sn_name _sn_a; do
        [ -n "$_sn_name" ] || continue
        sgdisk -c "$_sn_i:$_sn_name" "$DEVROOT/$1" >> "$LOG_FILE" 2>&1 || return 1
    done < "$LC/run/entries"
}

_tc_v() { printf '%s\n' "$_tc_info" | awk -F': ' -v k="$1" '$1 == k { print $2; exit }' | awk '{print $1}'; }
_tc_up() { printf '%s' "$1" | tr 'a-f' 'A-F'; }

lc_table_check() {
    # $1 = target, $2 = manifest. The table read back from the target, field by
    # field, against the source's: disk GUID, count, and per partition the
    # type, unique GUID, attributes, name, start and size. Two allowances, both
    # expand_last's and both recorded: the grown partition may be larger, and
    # what sat after it on a target that grew starts later (moved to the
    # tail), at its own size. Writes $RUN_DIR/targets/$1/lc_table:
    # grown_idx|grown_bytes|source_bytes|unallocated_bytes. 1 + LC_ERROR.
    LC_ERROR=""; _tc_n="$DEVROOT/$1"
    _tc_p=$(sgdisk -p "$_tc_n" 2>> "$LOG_FILE") || { LC_ERROR="sgdisk לא קרא את הטבלה מהיעד"; return 1; }
    _tc_dg=$(printf '%s\n' "$_tc_p" | awk -F': ' '/Disk identifier/ { print $2 }' | awk '{print $1}')
    _tc_want=$(json_get "$2" .disk_guid)
    [ "$_tc_dg" = "$_tc_want" ] || { LC_ERROR="ה-GUID של הדיסק ביעד הוא ${_tc_dg:-ריק}, לא $_tc_want"; return 1; }
    manifest_plan "$2" > "$LC/run/check.plan" || { LC_ERROR="לא הצלחנו לקרוא את תוכנית המחיצות"; return 1; }
    _tc_cnt=$(printf '%s\n' "$_tc_p" | awk '/^ *[0-9]+ / { n++ } END { print n + 0 }')
    _tc_wcnt=$(awk 'END { print NR }' "$LC/run/check.plan")
    [ "$_tc_cnt" = "$_tc_wcnt" ] || { LC_ERROR="ביעד $_tc_cnt מחיצות, במקור $_tc_wcnt"; return 1; }
    _tc_grow=$(cut -d'|' -f1 "$RUN_DIR/targets/$1/expanded" 2>/dev/null)
    _tc_gstart=$(awk -F'|' -v i="$_tc_grow" '$1 == i { print $5 }' "$LC/run/check.plan")
    _tc_end=0; _tc_gb=0; _tc_sb=0
    while IFS='|' read -r _tc_i _tc_t _tc_r _tc_fs _tc_st _tc_sz _tc_f _tc_sha _tc_x _tc_u _tc_uuid _tc_a; do
        _tc_info=$(sgdisk -i "$_tc_i" "$_tc_n" 2>> "$LOG_FILE") || { LC_ERROR="מחיצה $_tc_i: sgdisk -i נכשל ביעד"; return 1; }
        _tc_gt=$(_tc_v "Partition GUID code"); _tc_gu=$(_tc_v "Partition unique GUID")
        _tc_ga=$(_tc_v "Attribute flags"); _tc_gf=$(_tc_v "First sector"); _tc_gs=$(_tc_v "Partition size")
        _tc_gn=$(printf '%s\n' "$_tc_info" | sed -n "s/^Partition name: '\(.*\)'.*/\1/p")
        _tc_ws=$((_tc_sz / 512)); _tc_wn=$(lc_entry "$_tc_i" 2); _tc_wa=$(lc_entry "$_tc_i" 3)
        if [ "$(_tc_up "$_tc_gt")" != "$(_tc_up "$_tc_t")" ]; then LC_ERROR="מחיצה $_tc_i: סוג $_tc_gt במקום $_tc_t"
        elif [ -n "$_tc_u" ] && [ "$(_tc_up "$_tc_gu")" != "$(_tc_up "$_tc_u")" ]; then LC_ERROR="מחיצה $_tc_i: GUID ייחודי $_tc_gu במקום $_tc_u"
        elif [ "$_tc_ga" != "$_tc_wa" ]; then LC_ERROR="מחיצה $_tc_i: דגלים $_tc_ga במקום $_tc_wa"
        elif [ "$_tc_gn" != "$_tc_wn" ]; then LC_ERROR="מחיצה $_tc_i: השם '$_tc_gn' במקום '$_tc_wn'"
        elif [ "$_tc_i" = "$_tc_grow" ]; then
            { [ "$_tc_gf" = "$_tc_st" ] && [ "$_tc_gs" -ge "$_tc_ws" ]; } || LC_ERROR="מחיצה $_tc_i (שהוגדלה): התחלה $_tc_gf/$_tc_st, גודל $_tc_gs/$_tc_ws"
            _tc_gb=$((_tc_gs * 512)); _tc_sb=$((_tc_ws * 512))
        elif [ -n "$_tc_grow" ] && [ "$_tc_st" -gt "${_tc_gstart:-0}" ]; then
            { [ "$_tc_gf" -ge "$_tc_st" ] && [ "$_tc_gs" = "$_tc_ws" ]; } || LC_ERROR="מחיצה $_tc_i (שהוזזה לסוף): התחלה $_tc_gf, גודל $_tc_gs במקום $_tc_ws"
        else
            { [ "$_tc_gf" = "$_tc_st" ] && [ "$_tc_gs" = "$_tc_ws" ]; } || LC_ERROR="מחיצה $_tc_i: התחלה $_tc_gf במקום $_tc_st, גודל $_tc_gs במקום $_tc_ws"
        fi
        [ -z "$LC_ERROR" ] || return 1
        [ $((_tc_gf + _tc_gs)) -gt "$_tc_end" ] && _tc_end=$((_tc_gf + _tc_gs))
    done < "$LC/run/check.plan"
    verify_unique_guids "$1" "$LC/run/check.plan" || { LC_ERROR=$PLAN_ERROR; return 1; }
    _tc_sec=$(cat "$SYSROOT/sys/block/$1/size" 2>/dev/null); case "$_tc_sec" in ''|*[!0-9]*) _tc_sec=0 ;; esac
    _tc_free=$(( (_tc_sec - _tc_end) * 512 - LC_GPT_TAIL )); [ "$_tc_free" -gt 0 ] || _tc_free=0
    echo "${_tc_grow:-0}|$_tc_gb|$_tc_sb|$_tc_free" > "$RUN_DIR/targets/$1/lc_table"
    log "$1: the table came back identical to the source's ($_tc_cnt partitions, GUIDs included)"
}
