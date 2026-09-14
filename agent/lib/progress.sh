# progress.sh -- how the server learns what this machine is doing: the
# progress report (interfaces.md section 4).
# Pure sh assembly so it is testable without jq.
# POSIX sh (busybox ash).
#
# A report needs a session id. For a class round it arrives in the hello
# answer; for a unicast pull nothing hands it over and the station asks for
# one -- that request is pull.sh, split out in #562 and sourced right after
# this file, because pull_close ends a job through report_final below.
#
# State lives in files, so the reporter can run as a background process:
#   $RUN_DIR/state                 top-level state word
#   $RUN_DIR/targets/<dev>/state   waiting/writing/verifying/done/failed
#   $RUN_DIR/targets/<dev>/base    compressed bytes from finished partitions
#   $RUN_DIR/targets/<dev>/bytes.raw   pv output for the current partition
#   $RUN_DIR/targets/<dev>/counter the file the live counter is read from
#   $RUN_DIR/targets/<dev>/total   total compressed bytes for the whole image
#   $RUN_DIR/targets/<dev>/error   error text (only when failed)
#
# The live counter is a *pointer*, not a fixed name, because the two restore
# paths measure in different places. A classroom station has one pv per
# target and writes bytes.raw. A cloning machine has one pv for the whole
# machine -- the same number for all three drawers, which is no measurement
# of any of them (#25) -- so its drawers point this at fanout's per-drawer
# counter instead. Whoever runs the write declares where the truth is; the
# reporter never guesses between two files.

target_init() {
    # $1 = dev, $2 = bytes_total for the full image.
    _t="$RUN_DIR/targets/$1"
    mkdir -p "$_t"
    echo "waiting" > "$_t/state"
    echo 0 > "$_t/base"
    : > "$_t/bytes.raw"
    echo "$_t/bytes.raw" > "$_t/counter"
    echo "$2" > "$_t/total"
    rm -f "$_t/error"
}

target_counter() {
    # $1 = dev, $2 = the file the live counter is appended to (pv format:
    # a decimal number per line, the last one wins).
    echo "$2" > "$RUN_DIR/targets/$1/counter"
}

_counter_of() {
    _c=$(cat "$RUN_DIR/targets/$1/counter" 2>/dev/null)
    [ -n "$_c" ] || _c="$RUN_DIR/targets/$1/bytes.raw"
    echo "$_c"
}

target_set() {
    # $1 = dev, $2 = state, $3 = optional error text.
    _t="$RUN_DIR/targets/$1"
    echo "$2" > "$_t/state"
    [ -n "${3:-}" ] && echo "$3" > "$_t/error"
}

# ‏#100: המונה הוא ה-stderr של `pv`, ולכן הוא מכיל גם את הודעות השגיאה
# שלו — ``pv: write failed: Broken pipe`` נכתב לאותו קובץ בדיוק ברגע
# שה-curl של ההעלאה נופל. ‏`tail -n 1` הרים את השורה הזאת אל
# ‏``$((...))``. מדוד תחת busybox ash: תת-המעטפת של `$( )` מתה והערך
# חזר **ריק**, ומשם שתי תוצאות — ``"bytes_written":,`` שהוא JSON פגום
# שהשרת פוסל כולו, וקובץ `base` ריק שמאפס את הבייטים של כל המחיצות
# שכבר הסתיימו. ‏`|| echo 0` לא היה עוזר: החשבון מת לפני שיש קוד יציאה.
_last_number() {
    # השורה **המספרית** האחרונה. לא השורה האחרונה, ולא `n + 0`:
    # "אין שורה מספרית" ו"אפס" הם שני מצבים שונים. ‏`waits.sh` מבדיל
    # ביניהם כדי לדעת אם הבייט הראשון הגיע, והחזרת 0 קבוע נראתה לו
    # כמו התקדמות — כלומר החליפה את תקרת ההמתנה. הקוראים כאן
    # מוסיפים 0 בעצמם, מיד אחרי הקריאה.
    tr -d '\r' < "$1" 2>/dev/null \
        | awk '/^[0-9]+$/ { n = $0 } END { if (n != "") print n }'
}

target_bytes() {
    # $1 = dev. Finished partitions + the live pv counter.
    _t="$RUN_DIR/targets/$1"
    _base=$(_last_number "$_t/base")
    _cur=$(_last_number "$(_counter_of "$1")")
    [ -n "$_base" ] || _base=0
    [ -n "$_cur" ] || _cur=0
    echo $((_base + _cur))
}

target_partition_done() {
    # $1 = dev. Fold the finished partition's counter into the base.
    _t="$RUN_DIR/targets/$1"
    echo "$(target_bytes "$1")" > "$_t/base"
    : > "$(_counter_of "$1")"
}

_source_progress_json() {
    # $1 = dev. Prints ,"source_progress":{...} for the partition partclone is
    # reading now, or nothing at all when the denominator or the percent is
    # unknown (#435). The axis is the *uncompressed* one -- partclone's own
    # block count -- because the pv counter measures compressed bytes and their
    # total cannot be known before the stream ends. "Unknown" prints nothing on
    # purpose: the console then draws an indeterminate bar, never a measured 0%
    # (עיקרון 5). Only the capture path calls this (build_progress gates on the
    # task id); a round measures compressed bytes and a block axis would wrongly
    # win over them in guistate.sh.
    #
    # ‏Starting to clone (/dev/sdaN) פותח מחיצה ומאפס: פסקת הלוג האחרונה
    # לבדה נספרת, ולכן `Completed:` של מחיצה קודמת אינו נמשך אל החדשה. כל
    # השוואה מספרית עם `+0` — תחת busybox awk השוואת מחרוזת-למספר מכריעה
    # לפי תווים, ו-"754688" > "1665979" יצא אמת.
    _sp_log="$RUN_DIR/targets/$1/partclone.log"
    [ -f "$_sp_log" ] || return 0
    tr -d '\r' < "$_sp_log" 2>/dev/null | awk '
        /Starting to clone/ { part=""; total=""; pct="";
            if (match($0, /\/dev\/[A-Za-z0-9]+/)) {
                d = substr($0, RSTART, RLENGTH)
                if (match(d, /[0-9]+$/)) part = substr(d, RSTART) } }
        /Space in use/ { if (match($0, /[0-9]+[ ]*Blocks/)) {
            b = substr($0, RSTART, RLENGTH); gsub(/[^0-9]/, "", b); total = b } }
        /Completed:/ { c = $0; sub(/.*Completed:[ \t]*/, "", c); sub(/%.*/, "", c);
            if (c ~ /^[0-9]+(\.[0-9]+)?$/) pct = c }
        END {
            if (part == "" || total == "" || total + 0 <= 0 || pct == "") exit
            r = int(pct / 100.0 * total)
            if (r + 0 > total + 0) r = total; if (r + 0 < 0) r = 0
            printf ",\"source_progress\":{\"partition\":%d,\"blocks_read\":%d,\"blocks_total\":%d}", \
                part, r, total }'
}

build_progress() {
    # $1 = session id, $2 = mac, $3 = task id (capture instead of a round).
    # Exactly one of session/task identifies the work -- see interface 4.
    _state=$(cat "$RUN_DIR/state" 2>/dev/null || echo "waiting")
    _targets=""
    for _d in "$RUN_DIR"/targets/*/; do
        [ -d "$_d" ] || continue
        _dev=$(basename "$_d")
        _tstate=$(cat "$_d/state" 2>/dev/null || echo "waiting")
        _total=$(cat "$_d/total" 2>/dev/null || echo 0)
        _entry=$(printf '{"dev":"%s","bytes_written":%s,"bytes_total":%s,"state":"%s"' \
            "$_dev" "$(target_bytes "$_dev")" "$_total" "$_tstate")
        # ‏#435: רק בקליטה (task id). המכנה בציר הבלוקים, לצד bytes_* הדחוסים.
        [ -n "${3:-}" ] && _entry="$_entry$(_source_progress_json "$_dev")"
        if [ -f "$_d/error" ]; then
            _entry="$_entry,\"error\":\"$(json_escape "$(cat "$_d/error")")\""
        fi
        _entry="$_entry}"
        [ -n "$_targets" ] && _targets="$_targets,"
        _targets="$_targets$_entry"
    done
    if [ -n "${3:-}" ]; then
        _who=$(printf '"task_id":"%s"' "$3")
    else
        _who=$(printf '"session_id":"%s"' "$1")
    fi
    printf '{%s,"mac":"%s","state":"%s","targets":[%s]}' \
        "$_who" "$2" "$_state" "$_targets"
}

progress_send() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    # One report, synchronously, and the exit code of the send.
    #
    # The loop below sends and forgets, which is right for a report that will
    # be repeated in two seconds. The *last* report of a job is not repeated:
    # it is what tells the server the work ended. That one gets an answer read.
    build_progress "$1" "$2" "${4:-}" > "$RUN_DIR/progress.json"
    # ‏#557: התשובה נשמרת במקום להיזרק. הקוד היוצא נשאר הקוד היוצא —
    # מי שקורא לזה בלולאה ממשיך להתעלם ממנו — אבל **גוף התשובה זמין
    # למי שכן צריך לדעת**, וזה מה שעוצר לולאה על סבב שנסגר.
    http_post_json "$3/api/v1/agent/progress" "$RUN_DIR/progress.json" \
        > "$RUN_DIR/progress.reply" 2>/dev/null
}

progress_refused_for_good() {
    # האם התשובה האחרונה אומרת שאין טעם לנסות שוב.
    #
    # ⚠️ **רק `not_open`, ורק הוא.** שגיאת רשת, ‏500, או תשובה ריקה
    # הן "לא ידענו" — ולולאה שנעצרת עליהן היא מכונה שמפסיקה לדווח
    # כי השרת אותחל לרגע. **"לא הצלחנו לשאול" אינו "נענינו בלא".**
    # ‏`json_get` מקבל **נתיב קובץ**, לא מחרוזת — העברת התוכן עצמו
    # מחזירה `null` בשקט, וזה נראה בדיוק כמו "אין קוד".
    [ -s "$RUN_DIR/progress.reply" ] || return 1
    case "$(json_get "$RUN_DIR/progress.reply" ".code")" in
        not_open) return 0 ;;
        *)        return 1 ;;
    esac
}

progress_loop() {
    # $1 = session id, $2 = mac, $3 = server URL, $4 = task id (optional).
    #
    # The `|| true` here is the right one and it stays: a report that will be
    # repeated in two seconds owes nobody an answer. What must never travel
    # through this loop is the *last* report -- see report_final.
    while :; do
        progress_send "$1" "$2" "$3" "${4:-}" || true
        # ‏#557: `|| true` נשאר — דיווח שיחזור בעוד שתי שניות אינו חייב
        # תשובה. אבל **סבב שנסגר לא יפתח את עצמו מחדש**, ולולאה
        # שממשיכה עליו היא מכונה שלא תצטרף לגל הבא. נמדד 08/09:
        # שתי מכונות דיווחו 50 דקות על סבב סגור, ולכן נשארו בחוץ.
        if progress_refused_for_good; then
            log "progress: the server says this session is closed -- stopping"
            return 0
        fi
        sleep "${PROGRESS_INTERVAL_S:-2}"
    done
}

# --- the last report of a job (#101) -----------------------------------------
#
# Three paths end a job, and until #101 only one of them read the answer. The
# other two stopped the loop and slept: `sleep 6  # let 'done' reach the
# server`. Six seconds is a hope, not evidence -- with --max-time 10 --retry 3
# they may not hold one completed attempt -- and for a class round the cost of
# guessing wrong is not a missing line on a screen. `session_members.done`
# stays 0, the session stays `running`, and the machine's next hello is
# answered with the same restore: it writes the same 40GB again, up to the
# boot guard's budget (#75), and is finally labelled "boot loop" rather than
# "the closing report never arrived".
#
# So the closing report is sent from here: the loop is stopped first (one
# writer on progress.json), the send is synchronous, and the exit code is the
# answer. A report the server refused comes back as 400, and `curl -sfS`
# turns that into a non-zero exit -- so 0 means 200, and nothing else does.
#
# The ceiling counts tries and not seconds, because a try is a whole curl with
# its own retries: a number of seconds here would be a second guess about how
# long one of them takes. Five tries against a server that is not answering
# is minutes of real attempts, which covers a server being restarted; what it
# does not cover is a server that is gone, and that is the caller's business.
FINAL_REPORT_TRIES="${FINAL_REPORT_TRIES:-5}"
FINAL_REPORT_GAP_S="${FINAL_REPORT_GAP_S:-3}"

report_final() {
    # $1 = reporter pid ("" when no loop is running), $2 = session id,
    # $3 = mac, $4 = server URL, $5 = task id (optional).
    # 0 only on positive evidence that the server took the report.
    [ -n "${1:-}" ] && kill "$1" 2>/dev/null
    _f_try=0
    while :; do
        _f_try=$((_f_try + 1))
        if progress_send "$2" "$3" "$4" "${5:-}"; then
            [ "$_f_try" -gt 1 ] && log "the final report got through on try $_f_try"
            return 0
        fi
        [ "$_f_try" -ge "$FINAL_REPORT_TRIES" ] && break
        log "the final report was not accepted (try $_f_try of $FINAL_REPORT_TRIES)"
        sleep "$FINAL_REPORT_GAP_S"
    done
    log "WARNING: the server did not acknowledge the final report after" \
        "$FINAL_REPORT_TRIES tries"
    return 1
}
