# drawers.sh -- cloning-room mode: one stream, several drawers, isolated.
# POSIX sh (busybox ash).
#
# The stream arrives once and is fanned out to a per-drawer pipeline. Each
# drawer decompresses and writes on its own, so a slow or failing drive stops
# only itself (interfaces.md section 7). The isolation lives in `fanout`,
# which reports each drawer by name; this file turns that report into the
# per-target states of interface 4.

FANOUT_BUFFER="${FANOUT_BUFFER:-268435456}"     # 256MB per drawer, at most

_fanout_buffer() {
    # $1 = כמה מגירות. 256MB למגירה הוא אידאל, לא הבטחה: על מכונת 512MB
    # שלוש מגירות = 768MB, וה-OOM killer הורג את fanout באמצע הזרם (#21).
    # לוקחים את הזמין פחות רזרבה לצנרת (128MB), מחלקים במגירות, ולא
    # יורדים מתחת ל-2MB — מאגר קטן מ-READ_CHUNK נכשל בשקט (הלקח מ-#12).
    _avail=$(awk '/MemAvailable/ {print $2}' "$SYSROOT/proc/meminfo" 2>/dev/null)
    if [ -z "$_avail" ]; then echo "$FANOUT_BUFFER"; return; fi
    _budget=$(( (_avail - 131072) * 1024 / $1 ))
    [ "$_budget" -gt "$FANOUT_BUFFER" ] && _budget="$FANOUT_BUFFER"
    [ "$_budget" -lt 2097152 ] && _budget=2097152
    echo "$_budget"
}

list_drawers() {
    # Every non-removable disk with no OS of its own is a drawer. The
    # cloning machines boot from the network, so nothing here is a system
    # disk -- unlike a classroom station, where the internal disk is the target.
    for _n in $(list_disks); do
        _rm=$(cat "$SYSROOT/sys/block/$_n/removable" 2>/dev/null || echo 1)
        [ "$_rm" = "0" ] && echo "$_n"
    done
}

_drawer_pipeline() {
    # $1 = disk, $2 = fs, $3 = partition index. Reads its fifo, writes the disk.
    _tdir="$RUN_DIR/targets/$1"
    _node=$(partition_node "$1" "$3")
    _pcl=$(partclone_for_fs "$2")
    (
        # ה-rc של zstd נלכד לחוד, כמו `source.rc` מטה וכמו `capture.sh:177`:
        # ב-busybox ash אין `pipefail`, ו-`$?` של צינור הוא של האחרון בלבד.
        # ‏zstd שנפל באמצע ו-partclone שקיבל EOF ויצא 0 הגיעו לכאן
        # כ-`pipeline.rc=0`, והמגירה נחתמה `done` על כתיבה קצרה (#527).
        #
        # ⚠️ **לא `set -o pipefail`** — הוא אינו קיים ב-busybox ash. הוא
        # היה עובר בטסט (Git Bash) ומשאיר את הבאג על המכונה.
        # shellcheck disable=SC2046,SC2094 # fifo opened once; partclone_mode flags are a word list
        { zstd -dc < "$_tdir/feed" 2>> "$LOG_FILE"
          echo "$?" > "$_tdir/zstd.rc"; } \
            | "$_pcl" $(partclone_mode "$_pcl" -r) -s - -O "$_node" \
                -L "$_tdir/partclone.log" 2>> "$LOG_FILE"
        echo "$?" > "$_tdir/pipeline.rc"
    ) &
    echo "$!" > "$_tdir/pipeline.pid"
}

restore_partition_drawers() {
    # $1=mode $2=server $3=image_id $4=index $5=fs $6=file $7=sha256 $8=uuid
    # $9..=disks
    _mode="$1"; _server="$2"; _image="$3"; _idx="$4"; _fs="$5"; _file="$6"
    _sha="$7"; _uuid="$8"
    shift 8
    _disks="$*"

    # ‏swap אינה משודרת (אפיון סעיף 14): אין קובץ, אין sha256, ואין מה
    # להזרים — ‏fanout על רשומה כזו היה מזין את המגירות בזרם ריק ונכשל על
    # אי-התאמת sha256 (#49). כל מגירה בוראת אותה בעצמה, פעם אחת, על
    # המיקום שההרחבה כבר קבעה בזנב (#46). מגירה שנכשלה בה לא עוצרת את
    # השאר — כמו כל כשל אחר של מגירה בודדת.
    if is_swap_partition "$_fs"; then
        for _d in $_disks; do
            [ "$(cat "$RUN_DIR/targets/$_d/state")" = "failed" ] && continue
            if make_swap "$(partition_node "$_d" "$_idx")" "$_uuid"; then
                target_partition_done "$_d"
            else
                target_set "$_d" "failed" "partition $_idx: mkswap failed"
            fi
        done
        _any_alive "$_disks"
        return $?
    fi

    _feeds=""
    for _d in $_disks; do
        _t="$RUN_DIR/targets/$_d"
        [ "$(cat "$_t/state")" = "failed" ] && continue
        rm -f "$_t/feed" "$_t/feed.bytes" "$_t/pipeline.rc" "$_t/pipeline.pid" \
              "$_t/zstd.rc"
        mkfifo "$_t/feed"
        # ההתקדמות של המגירה הזו נמדדת אצל fanout, לא אצל ה-pv שלפניו:
        # ‏pv אחד מודד את הזרם של המכונה — אותו מספר לשלוש המגירות, ולכן
        # אינו מדידה של אף אחת מהן, וכל הפסים נשארו על 0% (#25).
        target_counter "$_d" "$_t/feed.bytes"
        _drawer_pipeline "$_d" "$_fs" "$_idx"
        _feeds="$_feeds $_t/feed"
    done
    [ -n "$_feeds" ] || { log "no drawers left"; return 1; }

    # sha256 is taken on the compressed bytes as received, once for the
    # machine: every drawer is fed the same bytes, so one check covers them all.
    rm -f "$RUN_DIR/shafifo" "$RUN_DIR/sha.out"
    mkfifo "$RUN_DIR/shafifo"
    sha256sum < "$RUN_DIR/shafifo" > "$RUN_DIR/sha.out" &
    _shapid=$!

    # ברקע, עם עין על מונה ה-pv: זרם שלא הזיז בייט חייב להסתיים בדיווח.
    # shellcheck disable=SC2086 # word splitting of the fifo list is wanted
    rm -f "$RUN_DIR/fanout.rc" "$RUN_DIR/source.rc"
    (
        # ה-rc של המקור נלכד לחוד: ב-POSIX sh ‏$? של צינור הוא של האחרון
        # בלבד, ולכן זרם שנקטע באמצע (‏udp-receiver שפקע, ‏curl שנפל)
        # הגיע לכאן כ-fanout שיצא 0 על בייטים חלקיים (#73).
        { stream_source "$_mode" "$_server" "$_image" "$_file"
          echo "$?" > "$RUN_DIR/source.rc"; } \
            | pv -n -b -i 2 2>> "$RUN_DIR/bytes.raw" \
            | tee "$RUN_DIR/shafifo" \
            | fanout "$(_fanout_buffer "$(echo "$_feeds" | wc -w)")" $_feeds \
                > "$RUN_DIR/fanout.out" 2>> "$LOG_FILE"
        echo "$?" > "$RUN_DIR/fanout.rc"
    ) &
    _streampid=$!
    if wait_progress "$_streampid" "$RUN_DIR/bytes.raw" \
            "$WAIT_STREAM_START_S" "$WAIT_STREAM_STALL_S" \
            "הזרם של מחיצה $_idx ($_file)"; then
        _fanout_rc=$(cat "$RUN_DIR/fanout.rc" 2>/dev/null || echo 1)
    else
        _fanout_rc="$WAIT_TIMED_OUT"
    fi

    # ממתינים לצינור לפי PID: בלי זה pipeline.rc עוד לא קיים (#20),
    # ו-wait ריק תופס גם את דמון ההתקדמות (#12). תקרה: fifo שלא נפתח
    # (#49) לא ייגמר מעצמו; אחרי פקיעת הזרם התקרה הקצרה.
    _dwait="$WAIT_DRAWER_S"
    [ "$_fanout_rc" = "$WAIT_TIMED_OUT" ] && _dwait="$WAIT_HELPER_S"
    for _d in $_disks; do
        _pidf="$RUN_DIR/targets/$_d/pipeline.pid"
        [ -f "$_pidf" ] || continue
        wait_pid "$(cat "$_pidf")" "$_dwait" "המגירה $_d (מחיצה $_idx)" && continue
        # בידוד: המגירה הזו לבדה נכשלת, הלולאה ממשיכה לשכנות.
        unblock_fifo "$RUN_DIR/targets/$_d/feed"
        target_set "$_d" "failed" \
            "partition $_idx: פג הזמן -- המגירה לא סיימה לכתוב"
    done
    wait_pid "$_shapid" "$WAIT_HELPER_S" "חישוב ה-sha256 של מחיצה $_idx"
    rm -f "$RUN_DIR/shafifo"

    # ה-sha התואם הוא הראיה החיובית שהזרם הגיע שלם, ולכן הוא נשאל ראשון:
    # מקור שיצא בקוד מוזר על זרם שאומת אינו כישלון. כשאין ראיה — שואלים
    # **למה**, ולכל סיבה הודעה משלה (#73). עד כאן היה ענף sha אחד שבלע גם
    # את המקרים שבהם הבדיקה כלל לא נעשתה: ‏fanout שנהרג (‏OOM, ‏#21) או
    # מקור שנקטע משאירים בייטים חלקיים, ה-sha בהכרח אינו תואם, וההודעה
    # שיצאה שלחה את הטכנאי לחפש בספרייה אימג' פגום שאינו קיים.
    _src=$(cat "$RUN_DIR/source.rc" 2>/dev/null)
    _got=$(awk '{print $1}' "$RUN_DIR/sha.out" 2>/dev/null)
    _why=""
    if is_sha256 "$_got" && [ "$_got" = "$_sha" ]; then
        _why=""
    elif [ "$_fanout_rc" = "$WAIT_TIMED_OUT" ]; then
        _why="פג הזמן -- הזרם לא הגיע"
    elif [ "$_fanout_rc" != "0" ] && [ "$_fanout_rc" != "1" ]; then
        # ‏2 של fanout, ‏137 של OOM (#21) = ההפצה עצמה נפלה.
        _why="הפצת הזרם למגירות נכשלה (fanout rc=${_fanout_rc:-לא נרשם})"
    elif ! is_sha256 "$_got"; then
        # אין ערך שנקרא בחזרה, ולכן אין ראיה גם לכך שהבייטים שגויים.
        _why="ה-sha256 לא חושב -- אין ראיה שהזרם הגיע שלם"
    elif [ "$_fanout_rc" = "1" ]; then
        # מגירה נפלה, וה-sha אינו תואם. שאלת ה**ייחוס** (#440) ושאלת
        # ה**שלמות** (#520) נפרדות, וההבחנה ביניהן היא **האם מישהו שרד**:
        #
        #   אין שורת ok  -- כל המגירות מתו, ‏fanout הפסיק לקרוא, המקור
        #                   קיבל 141 **בגללנו**, וה-sha חלקי כתוצאה.
        #                   זו תקלת דיסק, ואין להאשים את הרשת (#440).
        #   יש שורת ok   -- הזרם נקרא עד סופו, והבייטים נמדדו ונמצאו
        #                   שגויים. המגירה ההיא עומדת להיחתם ‏done,
        #                   ואסור בלי ראיה (#520).
        #
        # ‏grep: ‏0 = נמצא, ‏1 = לא נמצא (תשובה!), ‏2 = הבדיקה נשברה.
        grep -q ' ok$' "$RUN_DIR/fanout.out" 2>/dev/null
        _ok_rc=$?
        if [ "$_ok_rc" = "0" ]; then
            _why="אי-התאמת sha256 בקובץ $_file"
        elif [ "$_ok_rc" = "1" ]; then
            _why=""
        else
            _why="דוח fanout לא נקרא -- אין ראיה מי מהמגירות שרדה"
        fi
    elif [ "$_src" != "0" ] && [ "$_src" != "141" ]; then
        # fanout ראה EOF. ‏141 = אנחנו סגרנו את stdout, לא הרשת.
        _why="הזרם נקטע לפני סופו (מקור rc=${_src:-לא נרשם})"
    else
        # The bytes themselves were wrong: every drawer got the same bad data.
        _why="אי-התאמת sha256 בקובץ $_file"
    fi
    if [ -n "$_why" ]; then
        log "partition $_idx ($_file): $_why -- failing all drawers"
        for _d in $_disks; do
            target_set "$_d" "failed" "partition $_idx: $_why"
        done
        return 1
    fi

    _read_fanout_report "$_idx" $_disks
    _any_alive "$_disks"
}

run_restore_drawers() {
    # $1 = mode, $2 = server, $3 = image id, $4 = manifest, $5.. = disks
    _mode="$1"; _server="$2"; _image="$3"; _manifest="$4"
    shift 4
    _disks="$*"

    echo "writing" > "$RUN_DIR/state"
    for _d in $_disks; do
        target_set "$_d" "writing"
        # אותה בדיקה 2.7 של התחנה הבודדת, לכל מגירה בנפרד: מגירה קטנה
        # מדי נפסלת **לפני** ה---zap-all של apply_gpt, ובשמה. עד כאן
        # היא נמחקה ואז דיווחה "could not write the partition table",
        # ו"הכונן קטן מדי" נראה כמו תקלת חומרה. מגירה אחת שנפסלה אינה
        # עוצרת את השאר — continue, כמו כל כשל אחר בלולאה הזו.
        if ! disk_fits "$_d" "$_manifest"; then
            continue
        fi
        if ! apply_gpt "$_d" "$_manifest"; then
            target_set "$_d" "failed" "${PLAN_ERROR:-could not write the partition table}"
            continue
        fi
        # ההרחבה קורית לפני הזרם, בדיוק כמו בתחנה בודדת: מחיצת swap
        # נגררת עוברת לזנב עכשיו כדי שלא תחסום את מה שלפניה (#46).
        # מגירה שההרחבה נכשלה בה חוזרת לטבלת המניפסט ונכתבת בגודל המקורי.
        if ! expand_last "$_d" "$_manifest"; then
            log "WARNING: expansion failed on $_d -- writing at the image's own size"
            apply_gpt "$_d" "$_manifest" \
                || target_set "$_d" "failed" "${PLAN_ERROR:-could not write the partition table}"
        fi
    done
    _any_alive "$_disks" || { echo "failed" > "$RUN_DIR/state"; return 1; }

    # תוכנית לקובץ, לא לצינור: בלי pipefail קוד היציאה של manifest_plan
    # נבלע, לולאה על אפס שורות נראית כמו סיום, והמגירות → done (#426).
    # אותו דפוס כמו run_restore: מספר מול מספר, לא היעדר כישלון.
    _plan="$RUN_DIR/restore.plan"
    if ! manifest_plan "$_manifest" > "$_plan"; then
        _fail_alive "$_disks" "could not read the partition plan from the manifest"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi
    _expected=$(awk 'END { print NR }' "$_plan")
    _written=0
    while IFS='|' read -r _i _g _role _fs _s _sz _f _sha _exp _ug _uuid <&3; do
        if is_swap_partition "$_fs"; then
            log "partition $_i (swap): recreated on each drawer, not fed"
        else
            log "partition $_i ($_role): feeding $(echo "$_disks" | wc -w) drawers"
        fi
        restore_partition_drawers "$_mode" "$_server" "$_image" \
            "$_i" "$_fs" "$_f" "$_sha" "$_uuid" $_disks || break
        _written=$((_written + 1))
    done 3< "$_plan"
    if [ "$_expected" -lt 1 ] || [ "$_written" -ne "$_expected" ]; then
        _fail_alive "$_disks" "wrote $_written of $_expected partitions"
        echo "failed" > "$RUN_DIR/state"
        return 1
    fi

    echo "verifying" > "$RUN_DIR/state"
    for _d in $_disks; do
        [ "$(cat "$RUN_DIR/targets/$_d/state")" = "failed" ] && continue
        finish_grow "$_d"
    done

    # שלושה מצבים, לא שניים (#67). ‏`partial` מסיים את הגל כמו `done`
    # ומחזיר 0: המכונה עשתה את חלקה בגל, והצעד הבא בחדר הוא כיבוי
    # והחלפת מגירות. מה שהוא *לא* עושה זה להיספר כהצלחה מלאה — המגירה
    # שנכשלה נשארת טרייה ונכתבת בגל הבא, והמסך מציג "הושלם חלקית"
    # במקום "הסתיים". (מה שמוצג על מסך המכונה עצמה נקבע ב-imagectl-agent.)
    _final=$(_machine_state "$_disks")
    echo "$_final" > "$RUN_DIR/state"
    [ "$_final" = "failed" ] && return 1
    return 0
}
