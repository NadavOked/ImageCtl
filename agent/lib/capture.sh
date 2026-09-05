# capture.sh -- reads a source disk into a new image (flow 13.1). POSIX sh (busybox ash).
#
# The inverse of restore: partclone reads only the used blocks, zstd
# compresses, and each partition is uploaded as it is produced. Nothing is
# staged here -- a build machine has no room for a second copy of its own disk.
#
# The manifest is written last and uploaded last. That ordering is the server's
# signal that the capture is complete: it validates every sha256 against the
# manifest before the image enters the library.
#
# תיאור הדיסק במניפסט — ‏role, ‏fs, ‏uuid, ‏used_bytes, ‏os ו-expandable — יושב
# ב-manifest.sh; שני הקבצים נטענים יחד. כאן נשאר מה שמזרים את הבייטים עצמם.

# ‏#72, נמדד ולא נאמד (‏4GiB מראש `p3` של tiny11, שרת המעבדה): רמה 9 עולה
# פי 3.1 בזמן ומחזירה 1.1% — ‏~80MB על 7.3GB, ‏4 שניות בשידור שאורך 5:54.
CAPTURE_LEVEL="${CAPTURE_LEVEL:-3}"
# מספר מפורש ולא `-T0` שנגזר מליבות היעד (‏OOM על 512MB, ‏#21): שתי ליבות
# נמדדו מהירות כמו `-T0` ובשיא 72MB — פחות מ-112MB של רמה 9 שהייתה כאן.
CAPTURE_THREADS="${CAPTURE_THREADS:-2}"

# ‏#87: הכונן ה**קטן ביותר** שהאימג' הזה חייב להיכנס אליו, בבייטים.
# ריק = לא נבדק — וזה נרשם במניפסט כ-`null` ולא כמספר, כי "לא בדקנו"
# ו"בדקנו, נכנס" הם שני מצבים שונים (עיקרון 5). הערך אינו נגזר מהמשפחה:
# ‏`family` היא תווית בת שתי מחלקות ולא מידה, ושני כוננים באותה משפחה
# נבדלים בעשרות ג'יגה. מי שיודע את המספר הוא מי שמדד כונן יעד אמיתי.
CAPTURE_TARGET_BYTES="${CAPTURE_TARGET_BYTES:-}"

_capture_failed() {
    # $1 = disk, $2 = הסיבה. היומן לבדו נעלם (‏tmpfs, ו-ui_clear מוחק את המסך),
    # ולכן הסיבה נכתבת גם לשדה `error` של היעד — אותו מסלול ככשל מחיצה (#106).
    log "capture failed on $1: $2"
    target_set "$1" "failed" "$2"
    echo "failed" > "$RUN_DIR/state"
}

capture_disk() {
    # $1 = task id, $2 = disk name. Emits the manifest path on success.
    _task="$1"; _disk="$2"
    _node_base="$DEVROOT/$_disk"

    # ‏target_init לפני הבדיקה הראשונה: על יעד שלא נרשם אין לאן לכתוב סיבה,
    # וארבעת הסירובים שלמטה קורים לפני שבייט אחד זז — עד #106 הם הגיעו
    # לקונסולה כ-failed ריק. ‏GPT בלבד נשארת החלטת אפיון; מה שהשתנה הוא
    # שהסירוב אומר *מה כן נמצא*, כי "mbr" הוא אבחנה ו-"failed" אינו.
    # (‏node_is_block ולא `[ -b ]` ישיר, מאותה סיבה שב-restore.sh.)
    target_init "$_disk" 0
    node_is_block "$_node_base" || { _capture_failed "$_disk" "אין דיסק כזה: /dev/$_disk"; return 1; }
    _scheme=$(disk_scheme "$_node_base")
    [ "$_scheme" = "gpt" ] || { _capture_failed "$_disk" "הכונן אינו GPT (נמצא: $_scheme)"; return 1; }

    _sectors=$(cat "$SYSROOT/sys/block/$_disk/size" 2>/dev/null || echo 0)
    _disk_bytes=$((_sectors * 512))
    # שתי משפחות בלבד (סעיף 9): מתחת ל-300GB = 256, אחרת 500.
    if [ "$_disk_bytes" -lt 322122547200 ]; then _family=256; else _family=500; fi

    echo "capturing" > "$RUN_DIR/state"
    # מה שבאמת ירוץ, ליד ה-stderr של zstd ביומן: ‏zstd שהודר בלי ריבוי
    # תהליכונים מזהיר ומתעלם מ-`-T`, וזה ההפרש בין "ביקשנו" ל"רצנו".
    log "compressing with zstd -$CAPTURE_LEVEL -T$CAPTURE_THREADS"

    _parts="$RUN_DIR/parts.txt"
    # ה-GUID הייחודי של הדיסק ושל כל מחיצה חייבים לשרוד את השחזור:
    # ה-BCD של Windows מאתר את מחיצת המערכת לפי הזוג הזה, וטבלה עם
    # GUIDים חדשים = ‏winload.efi 0xc000000e על כל מחשב משוחזר (#26).
    _disk_guid=$(sgdisk -p "$_node_base" 2>/dev/null \
                 | awk -F': ' '/Disk identifier/ { print $2 }' | awk '{print $1}')
    # index:start:size:type-guid, from the partition table itself.
    sgdisk -p "$_node_base" 2>/dev/null | awk '/^ *[0-9]+ / { print $1 }' > "$RUN_DIR/idx.txt"
    : > "$_parts"
    while read -r _idx; do
        [ -n "$_idx" ] || continue
        _guid=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                | awk -F': ' '/Partition GUID code/ { print $2 }' | awk '{print $1}')
        _uguid=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                | awk -F': ' '/Partition unique GUID/ { print $2 }' | awk '{print $1}')
        _first=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                 | awk -F': ' '/First sector/ { print $2 }' | awk '{print $1}')
        _size=$(sgdisk -i "$_idx" "$_node_base" 2>/dev/null \
                | awk -F': ' '/Partition size/ { print $2 }' | awk '{print $1}')
        echo "$_idx|$_guid|$_uguid|$_first|$_size" >> "$_parts"
    done < "$RUN_DIR/idx.txt"
    # כותרת GPT תקינה וטבלה ריקה — מצב אחר לגמרי מ"אינו GPT" (עיקרון 5).
    [ -s "$_parts" ] || { _capture_failed "$_disk" "לא נמצאו מחיצות על /dev/$_disk"; return 1; }

    # ‏min_target_bytes הוא מה שהאימג' באמת צריך — סוף המחיצה האחרונה שבטבלה
    # ועוד מגה לגיבוי ה-GPT וליישור — ולא גודל דיסק המקור (#82): דיסק VM
    # ‏"256GB" הוא 256 GiB, שבעה אחוזים יותר מכל כונן פיזי מאותה מחלקה, וכל עוד
    # הדרישה הייתה גודל המקור אף אימג' זהב שנבנה ב-VM לא נכנס לשום ברזל. הרצפה
    # היא הגודל שבו נקלטה כל מחיצה ולא כמה תפוס בה — partclone מסרב לשחזר לתוך
    # מחיצה קטנה ממנה — ולכן אין כאן used_bytes; אותו כלל בשרת
    # (server/images.py). השדות: $4 סקטור התחלה, $5 גודל בסקטורים.
    _min_target=$(awk -F'|' -v disk="$_disk_bytes" '
        { e = ($4 + $5) * 512; if (e > end) end = e }
        END { need = int((end + 2097151) / 1048576) * 1048576
              if (disk >= end && disk < need) need = disk
              printf "%.0f\n", need }' "$_parts")
    # "לא הצלחנו לחשב" אינו "אין מחיצות" ואינו כשל מחיצה (עיקרון 5).
    [ -n "$_min_target" ] || { _capture_failed "$_disk" "לא הצלחנו לחשב את פריסת המחיצות"; return 1; }

    # ‏#87, השער החמישי: פריסה שאינה נכנסת לכונן היעד נתפסת **כאן**, לפני
    # שבייט אחד זז, ולא מול כיתה. דיסק בנייה גדול מכונן הכיתה — או דיסק
    # ‏VM ‏"256GB" שהוא 256 GiB — מייצר אימג' תקין לגמרי שאין לאן לכתוב
    # אותו, ובדיקה 2.7 בשחזור תחסום אותו בצדק על כל תחנה בנפרד. הסירוב
    # נושא את שלושת המספרים שהופכים אותו לפעולה: מה צריך, מה יש, וכמה
    # לכווץ. הכיווץ עצמו אינו כאן — הוא נוגע בדיסק המקור (‏#87 פתוח).
    _floor_json="null"
    if [ -n "$CAPTURE_TARGET_BYTES" ]; then
        # רצפה שאינה מספר אינה "אין רצפה": ערך שגוי שמדלג על הבדיקה הוא
        # בדיוק "לא הצלחנו לבדוק" שנראה כמו "בדקנו, הכל תקין".
        #
        # ‏`0?*` פוסל אפסים מובילים, וזה **לא קפדנות**: הערך נכתב למניפסט
        # כמספר JSON, ו-`010000000000` אינו JSON תקין. ההשוואה המספרית
        # למטה דווקא מצליחה, ולכן הקליטה הייתה רצה עד סופה — המניפסט
        # נכתב אחרון — ורק אז השרת היה דוחה את האימג'. שעה של קריאת
        # דיסק, ואז כישלון שאין לו שום קשר נראה לערך שהוקלד.
        case "$CAPTURE_TARGET_BYTES" in
            *[!0-9]* | 0?*)
                _capture_failed "$_disk" \
                    "רצפת כונן היעד אינה מספר בייטים תקין (ספרות בלבד, בלי אפסים מובילים): $CAPTURE_TARGET_BYTES"
                return 1 ;;
        esac
        if [ "$_min_target" -gt "$CAPTURE_TARGET_BYTES" ]; then
            _capture_failed "$_disk" \
                "הפריסה גדולה מכונן היעד: האימג' צריך $_min_target בייט, הקטן שביעדים מחזיק $CAPTURE_TARGET_BYTES — יש לכווץ $((_min_target - CAPTURE_TARGET_BYTES)) בייט לפני הקליטה"
            return 1
        fi
        _floor_json="$CAPTURE_TARGET_BYTES"
    fi

    _json_parts=""
    _total=0
    # ‏#85: ה-ESP נקרא שוב אחרי הלולאה, לגזירת החותם של מטעני האתחול.
    _esp_node=""
    while IFS='|' read -r _idx _guid _uguid _first _sizesec; do
        _node=$(partition_node "$_disk" "$_idx")
        _fs=$(_fs_of "$_node")
        _role=$(_partition_role "$_guid")
        # A generic linux-data GUID holding swap is still swap; a swap GUID
        # is swap regardless of what blkid says.
        [ "$_fs" = "swap" ] && _role="swap"
        [ "$_role" = "esp" ] && _esp_node="$_node"
        if [ "$_role" = "swap" ]; then
            # Nothing in swap is worth keeping (spec section 14): it is described
            # in the manifest and recreated on restore, never read. ה-UUID כן
            # נשמר — בלעדיו שורת ה-swap ב-fstab לא תמצא את החתימה (#48).
            _uuid=$(_uuid_of "$_node")
            if [ -n "$_uuid" ]; then _ujson="\"$_uuid\""; else _ujson="null"; fi
            log "partition $_idx (swap): recorded, not read"
            _json_parts="$_json_parts{\"index\":$_idx,\"type_guid\":\"$_guid\",\"unique_guid\":\"$_uguid\",\"uuid\":$_ujson,\"role\":\"swap\",\"fs\":\"swap\",\"start_sector\":$_first,\"size_bytes\":$((_sizesec * 512)),\"used_bytes\":0,\"file\":null,\"sha256\":null,\"expandable\":false},"
            continue
        fi
        _file="p$_idx.$_role.pcl.zst"
        _used=$(_used_bytes "$_node" "$_fs")
        log "partition $_idx ($_role, $_fs): reading"

        _out="$RUN_DIR/upload.$_idx"
        rm -f "$_out"
        mkfifo "$_out"
        # The upload runs while partclone is still reading: nothing is
        # staged on this machine.
        (
            # ‏-T ולא --data-binary: ‏--data-binary קורא את כל ה-FIFO לזיכרון כדי
            # לחשב Content-Length — מחיצה גדולה מה-RAM נהרגת ב-OOM (‏#15). ‏-T
            # מזרים ב-chunked. ‏--max-time 0 = בלי תקרת משך (100GB לוקחים זמן),
            # אבל עם תקרת חוסר-התקדמות: חיבור שנפל באמצע יוצא, לא נתלה.
            curl -sfS --max-time 0 \
                --speed-limit 1 --speed-time "$HTTP_STALL_TIMEOUT" \
                -H "Content-Type: application/octet-stream" \
                -T "$_out" \
                "$SERVER/api/v1/capture/$_task/files/$_file" > "$RUN_DIR/up.$_idx.out" 2>> "$LOG_FILE"
            echo "$?" > "$RUN_DIR/up.$_idx.rc"
        ) &
        _uppid=$!

        rm -f "$RUN_DIR/sha.$_idx" "$RUN_DIR/pcl.$_idx.rc"
        _pcl=$(partclone_for_fs "$_fs")
        # ה-rc של partclone נלכד במפורש: ב-busybox ash אין pipefail, ו-$? של
        # הצינור הוא של sha256sum — שמצליח גם על קלט ריק, וככה כשל קריאה הפך
        # פעם לקובץ ריק "מוצלח" (נתפס במעבדה, #12). ברקע, עם עין על מונה ה-pv:
        # ‏`tee "$_out"` נחסם ב-open() עד שה-curl יפתח את ה-fifo לקריאה, ו-curl
        # שנפל מיד (שרת שסירב) משאיר אותו חסום לנצח. כונן איטי לעומת זאת פשוט
        # מתקדם לאט — ולכן המדד הוא חוסר התקדמות, לא משך.
        (
            # shellcheck disable=SC2046 # דגלי partclone_mode הם רשימת מילים
            { "$_pcl" $(partclone_mode "$_pcl" -c) -s "$_node" \
                  -L "$RUN_DIR/targets/$_disk/partclone.log" 2>> "$LOG_FILE"
              echo "$?" > "$RUN_DIR/pcl.$_idx.rc"; } \
                | zstd -"$CAPTURE_LEVEL" -T"$CAPTURE_THREADS" -c 2>> "$LOG_FILE" \
                | pv -n -b -i 2 2>> "$RUN_DIR/targets/$_disk/bytes.raw" \
                | tee "$_out" \
                | sha256sum > "$RUN_DIR/sha.$_idx"
        ) &
        _readpid=$!
        if wait_progress "$_readpid" "$RUN_DIR/targets/$_disk/bytes.raw" \
                "$WAIT_STREAM_START_S" "$WAIT_STREAM_STALL_S" \
                "קריאת מחיצה $_idx מ-$_disk"; then
            _rc=$(cat "$RUN_DIR/pcl.$_idx.rc" 2>/dev/null || echo 1)
        else
            _rc="$WAIT_TIMED_OUT"
        fi
        # ממתינים להעלאה בלבד, בשם: wait ריק תופס גם את דמון ההתקדמות
        # שרץ ברקע — וכשהפונקציה רצה ב-shell הראשי זה נעצר לנצח (מעבדה #12).
        # ובתקרה: ‏curl שנתקע בפתיחת ה-fifo לא ייתלה כאן לנצח.
        wait_pid "$_uppid" "$WAIT_HELPER_S" "העלאת מחיצה $_idx" \
            || echo "$WAIT_TIMED_OUT" > "$RUN_DIR/up.$_idx.rc"
        rm -f "$_out"

        _uprc=$(cat "$RUN_DIR/up.$_idx.rc" 2>/dev/null || echo 1)
        if [ "$_rc" -ne 0 ] || [ "$_uprc" -ne 0 ]; then
            # הסיבה מהכלי עצמו — זה מה שהקונסולה תציג ליד failed.
            _why=$(tail -n 1 "$RUN_DIR/targets/$_disk/partclone.log" 2>/dev/null | tr -d '\r')
            _capture_failed "$_disk" \
                "partition $_idx: ${_why:-capture failed} (read=$_rc upload=$_uprc)"
            return 1
        fi

        _sha=$(awk '{print $1}' "$RUN_DIR/sha.$_idx")
        # הבייטים של המחיצה הזו בלבד — מונה ה-pv שלה. target_bytes מחזיר
        # מצטבר-סבב, וחיבורו בכל סיבוב ניפח את total_compressed_bytes
        # פי כמה (האימג' הראשון במעבדה: 1.8GB במקום 490MB, ‏#12).
        _bytes=$(tr -d '\r' < "$RUN_DIR/targets/$_disk/bytes.raw" 2>/dev/null | tail -n 1)
        [ -n "$_bytes" ] || _bytes=0
        _total=$((_total + _bytes))
        target_partition_done "$_disk"

        # Every partition is written not expandable; _mark_expandable picks
        # the one candidate once the whole list is known.
        _json_parts="$_json_parts{\"index\":$_idx,\"type_guid\":\"$_guid\",\"unique_guid\":\"$_uguid\",\"role\":\"$_role\",\"fs\":\"$_fs\",\"start_sector\":$_first,\"size_bytes\":$((_sizesec * 512)),\"used_bytes\":$_used,\"file\":\"$_file\",\"sha256\":\"$_sha\",\"expandable\":false},"
    done < "$_parts"

    _json_parts=${_json_parts%,}
    _json_parts=$(_mark_expandable "$_json_parts")
    _os=$(_image_os "$_json_parts")
    # ‏#85, אחרי שהבייטים כבר עברו: החותם הוא מידע על האימג', לא שער עליו.
    # ‏boot_ca_json לעולם אינו נכשל — הוא מחזיר `null` וסיבה.
    _bootca=$(boot_ca_json "$_esp_node")
    # תשובה ריקה היתה מייצרת `,,` — מניפסט שאינו JSON, אחרי שכל הבייטים
    # כבר עברו והשרת כבר קיבל אותם. "לא הצלחנו לגזור" הוא מצב מוכר ויש
    # לו ייצוג; מניפסט פגום אינו מצב, הוא תקלה שמתגלה בסוף שעה של קריאה.
    [ -n "$_bootca" ] \
        || _bootca='"boot_ca":null,"boot_ca_error":"החותם לא נגזר בקליטה הזו"'

    # הנתיב קבוע והקורא מכיר אותו. בלי echo של הנתיב ל-stdout: log()
    # מדבר גם הוא ל-stdout, ולכידת $(capture_disk) החזירה פעם בליל
    # שורות לוג במקום נתיב — וה-curl של המניפסט נכשל בשקט (מעבדה, #12).
    printf '{"schema":1,"family":%s,"os":"%s",%s,"source_disk_bytes":%s,"min_target_bytes":%s,"target_floor_bytes":%s,"scheme":"gpt","sector_size":512,"disk_guid":"%s","partitions":[%s],"total_compressed_bytes":%s,"compression":"zstd-%s"}\n' \
        "$_family" "$_os" "$_bootca" "$_disk_bytes" "$_min_target" "$_floor_json" "$_disk_guid" "$_json_parts" "$_total" "$CAPTURE_LEVEL" \
        > "$RUN_DIR/new-manifest.json"
}

upload_manifest() {
    # $1 = task id, $2 = manifest path.
    curl -sfS -X PUT -H "Content-Type: application/json" \
        --data-binary "@$2" \
        "$SERVER/api/v1/capture/$1/manifest" >> "$LOG_FILE" 2>&1
}
