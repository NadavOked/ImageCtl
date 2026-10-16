# ntfsboot.sh -- #1487: the NTFS backup boot sector, after partclone -r.
# POSIX sh (busybox ash).
#
# partclone.ntfs -r writes the volume's clusters. The backup boot sector
# lives in the sector right after the volume -- sector number_of_sectors
# (boot sector bytes 0x28..0x2F, little-endian), which is the partition's
# last sector when the volume fills it -- outside the cluster area, so it
# was never written. Measured on cloner 2, 16/10: every NTFS target's last
# sector differed from its sector 0, except the partition ntfsresize grew
# (ntfsresize rewrites the backup), and `ntfsfix -n` said "Failed to fix the
# alternate boot sector". The table and xxh64 checks all passed: they compare
# the stream, and this sector is not in it.
#
# The fix copies sector 0 to sector number_of_sectors, as ntfsclone and
# ntfsresize do, then drops the cache and reads it back (principle 5): a
# write that "succeeded" but did not land is a failed target, by name.
# Never past the partition's end. Called once per NTFS partition, right
# after its own write was judged, so before finish_grow's ntfsresize.

_nb_bytes() {
    # $1 = file, $2 = offset, $3 = count. The bytes as unsigned decimals.
    od -An -tu1 -j "$2" -N "$3" "$1" 2>> "$LOG_FILE"
}

ntfs_backup_boot() {
    # $1 = partition node. 0 = sector 0 is at sector number_of_sectors and was
    # read back equal. 1 = NTFS_BOOT_ERROR says why.
    NTFS_BOOT_ERROR=""
    _nb_n="$1"; _nb_t="$RUN_DIR/ntfsboot.${1##*/}"
    _nb_oem=$(od -An -tx1 -j 3 -N 8 "$_nb_n" 2>> "$LOG_FILE" | tr -d ' \n')
    if [ "$_nb_oem" != 4e54465320202020 ]; then
        NTFS_BOOT_ERROR="סקטור 0 אינו סקטור אתחול של NTFS (${_nb_oem:-לא נקרא})"; return 1
    fi
    # shellcheck disable=SC2046 # the bytes are a word list on purpose
    set -- $(_nb_bytes "$_nb_n" 11 2)
    [ "$#" -eq 2 ] || { NTFS_BOOT_ERROR="גודל הסקטור לא נקרא מסקטור האתחול"; return 1; }
    _nb_bps=$(( $1 + ($2 << 8) ))
    case "$_nb_bps" in
        512|1024|2048|4096) ;;
        *) NTFS_BOOT_ERROR="גודל סקטור $_nb_bps בסקטור האתחול אינו תקין"; return 1 ;;
    esac
    # shellcheck disable=SC2046
    set -- $(_nb_bytes "$_nb_n" 40 8)
    [ "$#" -eq 8 ] || { NTFS_BOOT_ERROR="מספר הסקטורים לא נקרא מסקטור האתחול"; return 1; }
    _nb_tot=$(( $1 + ($2 << 8) + ($3 << 16) + ($4 << 24) + ($5 << 32) + ($6 << 40) + ($7 << 48) + ($8 << 56) ))
    _nb_size=$(blockdev --getsize64 "$_nb_n" 2>> "$LOG_FILE")
    case "$_nb_size" in
        ''|*[!0-9]*) NTFS_BOOT_ERROR="גודל המחיצה לא נקרא (blockdev) — לא נכתב דבר"; return 1 ;;
    esac
    # (tot + 1) * bps <= size, written without the multiplication (no overflow).
    if [ "$_nb_tot" -lt 1 ] || [ "$_nb_tot" -gt $(( _nb_size / _nb_bps - 1 )) ]; then
        NTFS_BOOT_ERROR="הווליום מצהיר על $_nb_tot סקטורים של $_nb_bps, והמחיצה $_nb_size בתים — לא נכתב דבר"; return 1
    fi
    rm -f "$_nb_t.0" "$_nb_t.back"
    if ! dd if="$_nb_n" of="$_nb_t.0" bs="$_nb_bps" count=1 2>> "$LOG_FILE" \
            || [ "$(( $(wc -c < "$_nb_t.0") ))" -ne "$_nb_bps" ]; then
        NTFS_BOOT_ERROR="סקטור 0 לא נקרא"; rm -f "$_nb_t.0"; return 1
    fi
    dd if="$_nb_t.0" of="$_nb_n" bs="$_nb_bps" seek="$_nb_tot" count=1 conv=notrunc,fsync 2>> "$LOG_FILE"
    _nb_rc=$?
    if [ "$_nb_rc" -ne 0 ]; then
        NTFS_BOOT_ERROR="הכתיבה לסקטור $_nb_tot נכשלה (dd rc=$_nb_rc)"; rm -f "$_nb_t.0"; return 1
    fi
    # The read-back must come from the disk, not from the cache dd just filled.
    if ! blockdev --flushbufs "$_nb_n" >> "$LOG_FILE" 2>&1; then
        NTFS_BOOT_ERROR="המטמון לא רוקן (blockdev --flushbufs) — אין קריאה חוזרת מהדיסק"; rm -f "$_nb_t.0"; return 1
    fi
    dd if="$_nb_n" of="$_nb_t.back" bs="$_nb_bps" skip="$_nb_tot" count=1 2>> "$LOG_FILE"
    _nb_rc=$?
    # cmp: 0 = equal, 1 = differ (an answer), 2 = the check itself broke.
    cmp -s "$_nb_t.0" "$_nb_t.back"; _nb_c=$?
    rm -f "$_nb_t.0" "$_nb_t.back"
    if [ "$_nb_rc" -ne 0 ]; then NTFS_BOOT_ERROR="הקריאה החוזרת של סקטור $_nb_tot נכשלה (dd rc=$_nb_rc)"; return 1; fi
    case "$_nb_c" in
        0) return 0 ;;
        1) NTFS_BOOT_ERROR="סקטור $_nb_tot שנקרא בחזרה שונה מסקטור 0 — הכתיבה לא נקלטה" ;;
        *) NTFS_BOOT_ERROR="ההשוואה לסקטור 0 לא בוצעה (cmp rc=$_nb_c)" ;;
    esac
    return 1
}

ntfs_boot_target() {
    # $1 = disk, $2 = partition index, $3 = fs. 0 = not NTFS, or the backup was
    # written and read back (the index is recorded in targets/<disk>/ntfs_boot
    # for the end screen). 1 = NTFS_BOOT_ERROR names the partition and why;
    # the caller fails the target with it.
    [ "$3" = ntfs ] || return 0
    if ntfs_backup_boot "$(partition_node "$1" "$2")"; then
        echo "$2" >> "$RUN_DIR/targets/$1/ntfs_boot"
        log "$1: partition $2: NTFS backup boot sector written and read back"
        return 0
    fi
    NTFS_BOOT_ERROR="מחיצה $2: סקטור אתחול הגיבוי של NTFS — $NTFS_BOOT_ERROR"
    log "$1: $NTFS_BOOT_ERROR"
    return 1
}
