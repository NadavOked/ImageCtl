# verdict.sh -- turning the evidence a drawer left behind into its state.
# POSIX sh (busybox ash).
#
# `drawers.sh` runs the stream; this file answers "so did it work?". The
# split is deliberate: the evidence a drawer leaves is three separate
# records -- `fanout`'s report line, `zstd.rc`, and `pipeline.rc` -- and
# every bug this file has carried came from folding two of them into one.
# ‏#440 (a dead disk reported as the network), #527 (a lost zstd exit
# code), #520 (a sha waived by a dead sibling): all of them are the same
# shape, and all of them lived here.
#
# Loaded by drawers.sh. Nothing else sources it.

_write_fail() {
    # $1=dev $2=idx $3=סיבת fanout. הקבצים היו על הדיסק ולא נקראו —
    # לכן דיסק מת דווח כרשת (#440). חסר ≠ דיסק תקין ≠ רשת.
    _t="$RUN_DIR/targets/$1"
    _prc=$(cat "$_t/pipeline.rc" 2>/dev/null)
    _plog=$(tail -n 1 "$_t/partclone.log" 2>/dev/null | tr -d '\r')
    fail_written_target "$1" \
        "partition $2: הכתיבה לדיסק נכשלה (fanout: ${3:-אין דיווח}; partclone rc=${_prc:-לא נרשם}${_plog:+; $_plog})"
}

_read_fanout_report() {
    # דוח fanout + קודי הצינור → מצב המגירה. ‏done רק כשכולם הסכימו.
    _idx="$1"; shift
    for _d in "$@"; do
        _t="$RUN_DIR/targets/$_d"
        [ "$(cat "$_t/state")" = "failed" ] && continue
        _line=$(grep -F "$_t/feed " "$RUN_DIR/fanout.out" 2>/dev/null)
        case "$_line" in
            *" ok")
                # שלושה מסכימים, לא שניים: ‏fanout מסר את הבייטים,
                # ‏zstd פרס אותם, ו-partclone כתב. ‏zstd שנפל בשקט
                # משאיר partclone שיוצא 0 על קלט חלקי (#527).
                _rc=$(cat "$_t/pipeline.rc" 2>/dev/null)
                _zrc=$(cat "$_t/zstd.rc" 2>/dev/null)
                if [ "$_rc" = "0" ] && [ "$_zrc" = "0" ]; then
                    target_partition_done "$_d"
                elif [ "$_rc" = "0" ] && [ -z "$_zrc" ]; then
                    # אין קובץ = לא נמדד. זה אינו "zstd הצליח" ואינו
                    # כישלון של partclone — מצב שלישי בשם (עיקרון 5).
                    _write_fail "$_d" "$_idx" "ok (zstd rc לא נרשם)"
                else
                    _write_fail "$_d" "$_idx" "ok"
                fi
                ;;
            *" failed "*)
                _write_fail "$_d" "$_idx" "${_line#* failed }"
                ;;
            *)
                _write_fail "$_d" "$_idx" "אין דיווח"
                ;;
        esac
    done
}

_any_alive() {
    for _d in $1; do
        [ "$(cat "$RUN_DIR/targets/$_d/state")" != "failed" ] && return 0
    done
    return 1
}

_fail_alive() {
    # $1 = disks, $2 = reason. מגירות שכבר failed לא נדרסות.
    for _d in $1; do
        [ "$(cat "$RUN_DIR/targets/$_d/state" 2>/dev/null)" = "failed" ] && continue
        fail_written_target "$_d" "$2"
    done
}

_machine_state() {
    # $1 = disks. המצב של המחשב כולו לפי המגירות: הכול נכתב, חלק נכתב,
    # או כלום. עד #67 היו כאן שני מצבים בלבד — `_any_alive` אמת גם על
    # מגירה אחת ששרדה מתוך שלוש, ומחשב שאיבד מגירה דיווח "done" בדיוק
    # כמו מחשב שכל מגירותיו נכתבו. ברמת המגירה הכשל היה גלוי כל הזמן
    # (‏targets_json), וברמת המחשב הוא נבלע — וזה מה שהמפעיל רואה.
    _alive=0; _dead=0
    for _d in $1; do
        if [ "$(cat "$RUN_DIR/targets/$_d/state" 2>/dev/null)" = "failed" ]; then
            _dead=$((_dead + 1))
        else
            _alive=$((_alive + 1))
        fi
    done
    if [ "$_alive" -eq 0 ]; then echo "failed"
    elif [ "$_dead" -eq 0 ]; then echo "done"
    else echo "partial"; fi
}
