#!/usr/bin/env bash
# server-upgrade.sh — מסלול השדרוג של #748/#1193. רץ דרך
# `systemd-run --collect`, מנותק מתהליך
# imagectl-server, כי הצעד האחרון כאן הוא restart שלו.
#
# שימוש: server-upgrade.sh <tag> <repo-dir>
#
# מה שהוא עושה, בסדר הזה, ולמה בסדר הזה (‏#1131 ס' 5–6):
#   0. סבב פתוח/רץ = לא משדרגים (גם כשמופעל ידנית, לא רק מהקונסולה).
#   1. התג הקודם — מה ש-update.py שמר (update_previous) — הוא יעד החזרה.
#   2. fetch, אימות חתימת התג (#1277), checkout — מחוץ לארגז החול של שרת ה-web.
#   3. יחידות systemd — קודם, כדי ש-daemon-reload יראה כל שינוי בהן.
#   4. initrd (#1230) — tools/boot-payload-build.sh לפי הדגלים השמורים, מול
#      הקרנל שנגזר עכשיו. בלי דגלים ובלי ראיה שזה שרת ISO — חזרה, לא דילוג.
#   5. restart, ואז **ראיה חיובית** שהחדש רץ: is-active + `/health/live`
#      שמדווח בדיוק את התג + verify-boot-payload.sh.
#   6. כשל בראיה → checkout לתג הקודם, יחידות, restart, וסטטוס `failed`
#      עם הסיבה ב-DB (דרך update._set_status) — הקונסולה מציגה אותו.
#   ‏#1301: כל מעבר שלב נכתב לסטטוס (stage + חותמת ב-stages), וכשל נושא reason
#      שמכונה קוראת — מסך העדכון בקונסולה מציג לפיהם, לא לפי הטקסט העברי.
#
# ‏#1074: שדרוג אינו מפעיל את חומת האש הראשית. שרת קיים לא ינותק. (‏#1334:
# חבילת nftables מותקנת בצעד 3ב — הטבלה של ה-VM בלבד, לא ה-ruleset הראשי.)
#
# ‏#1334, צעד 3ב: install/vmupdate-host.sh של התג החדש (חבילות + המשתמש
# imagectl-vm). כשל **אינו** מפיל את העדכון — השרת מתעדכן, ו"עדכון ב-VM"
# לא זמין — וגם אינו נבלע: הסיבה נכתבת לסטטוס (`warning`), והקונסולה מציגה
# "אומת — אבל …". המנוע עצמו מסרב בשם (missing_tool/vm_user) עד שזה יתוקן.
#
# משתני סביבה (לבדיקות — בייצור ברירות המחדל): IMAGECTL_DATA_DIR,
# IMAGECTL_UNIT_DIR, IMAGECTL_INITRD_FLAGS, IMAGECTL_ISO_MANIFEST,
# IMAGECTL_HTTP_ROOT, IMAGECTL_UPGRADE_LOG, IMAGECTL_CONSOLE_URL,
# IMAGECTL_LIVE_WAIT.
set -euo pipefail

die() { echo "server-upgrade: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: server-upgrade.sh <tag> <repo-dir>"
TAG=$1
REPO_DIR=$2
DATA_DIR="${IMAGECTL_DATA_DIR:-/var/lib/imagectl}"
DB="$DATA_DIR/imagectl.db"
UNIT_DIR="${IMAGECTL_UNIT_DIR:-/etc/systemd/system}"
FLAGS_FILE="${IMAGECTL_INITRD_FLAGS:-/etc/imagectl/initrd.flags}"
ISO_MANIFEST="${IMAGECTL_ISO_MANIFEST:-/etc/imagectl/iso-release.json}"
HTTP_ROOT="${IMAGECTL_HTTP_ROOT:-/srv/imagectl/boot}"
PREV=""         # התג הקודם — נקרא בשלב 1; ריק עד אז (set_status קורא אותו)
INITRD_NOTE=""   # נקבע בחזרה — האם המטען הקודם הוחזר
# ‏#1287: עותק המטען הקודם (initrd/vmlinuz) — מוחזר יחד עם הקוד בחזרה.
PAYLOAD_SAVE="$HTTP_ROOT/.pre-upgrade"
PAYLOAD_SAVED=0  # 1 רק אחרי שהעותק של **הריצה הזו** הושלם
LOG="${IMAGECTL_UPGRADE_LOG:-/var/log/imagectl/server-upgrade.log}"
CONSOLE_URL="${IMAGECTL_CONSOLE_URL:-https://127.0.0.1:8081}"
LIVE_WAIT="${IMAGECTL_LIVE_WAIT:-90}"
CERT="$DATA_DIR/console-tls/console.crt"
[[ -f "$CERT" ]] || CERT=""

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
exec >>"$LOG" 2>&1
echo "--- $(date -Is) upgrading to $TAG in $REPO_DIR ---"

cd "$REPO_DIR" || die "no such repo dir: $REPO_DIR"

# ‏#1301: הריצה הזו — מתי התחילה. אם update.py כתב זה עתה `running` לאותו תג
# (עם started_at/by/from), זו אותה ריצה והשעון בקונסולה ממשיך ממנו; אחרת
# (הפעלה ידנית) — עכשיו. כשל הקריאה אינו עוצר שדרוג: השעה מ-date.
RUN_STARTED="$(python3 - "$REPO_DIR" "$DB" "$TAG" <<'PYEOF'
import json, sys
repo, db, tag = sys.argv[1:4]
sys.path.insert(0, repo)
from server.db import connect, get_setting, now_iso
from server import update
try:
    old = json.loads(get_setting(connect(db), update.STATUS_KEY) or "null")
except ValueError:
    old = None
ok = isinstance(old, dict) and old.get("state") == "running" and old.get("tag") == tag
print(old["started_at"] if ok and old.get("started_at") else now_iso())
PYEOF
)" || RUN_STARTED=""
[[ -n "$RUN_STARTED" ]] || RUN_STARTED="$(date -u +%Y-%m-%dT%H:%M:%S+00:00)"
STAGE=""   # fetch | verify | install | restart | done — נכתב ב-stage()
# ‏#1334: אזהרה של הריצה (‏"עדכון ב-VM לא זמין: …") — נקבעת בצעד 3ב ועוברת בכל
# כתיבה מכאן והלאה, כך ש-stage()/done אינם מוחקים אותה. ריק = אין.
UPGRADE_WARNING=""

# סטטוס לקונסולה — אותו מפתח ואותה פונקציה של update.py, לא עותק.
# ‏#1301: בכל כתיבה — השלב, ‏started_at וחותמת לכל שלב (‏stages), ובכשל `reason`
# (‏round|fetch|signature|install|timeout|rollback) ו-`detail` (סיבת הכשל בלבד, בלי
# תוצאת החזרה). ‏`error` נשאר הטקסט המלא כמו קודם. רק ‏API יציב של server/ (db,
# ‏update.STATUS_KEY/_set_status): אחרי חזרה ה-python הוא של התג הקודם.
set_status() {   # set_status <state> [<error>] [<reason>] [<detail>]
    python3 - "$REPO_DIR" "$DB" "$TAG" "$PREV" "$RUN_STARTED" "$STAGE" "$1" "${2:-}" "${3:-}" "${4:-}" "$UPGRADE_WARNING" <<'PYEOF' || echo "server-upgrade: כתיבת הסטטוס ל-DB נכשלה"
import json, sys
repo, db, tag, prev, started, stage, state, error, reason, detail, warning = sys.argv[1:12]
sys.path.insert(0, repo)
from server.db import connect, get_setting, journal, now_iso
from server import update
conn = connect(db)
try:
    old = json.loads(get_setting(conn, update.STATUS_KEY) or "null")
except ValueError:
    old = None
same = isinstance(old, dict) and old.get("started_at") == started and old.get("tag") == tag
stages = dict(old.get("stages") or {}) if same else {}
if stage and stage not in stages:
    stages[stage] = now_iso()
doc = {"state": state, "tag": tag, "started_at": started, "stages": stages,
       "from": (old.get("from") if same else None) or prev or None,
       "by": old.get("by") if same else None}
if stage:
    doc["stage"] = stage
for key, value in (("error", error), ("reason", reason), ("detail", detail), ("warning", warning)):
    if value:
        doc[key] = value
update._set_status(conn, doc)
if state in ("failed", "done"):
    journal(conn, "update_apply_" + state,
            f"{tag}: {reason or stage}" + (f" — {error}" if error else ""),
            doc["by"] or "server-upgrade")
PYEOF
}
stage() { STAGE=$1; set_status running; }

# 0. סבב פתוח/רץ? — אותה שאילתה של update._active_round; DB שלא נקרא = לא
# יודעים = לא משדרגים (עיקרון 5: "לא הצלחנו לבדוק" אינו "אין סבב").
ROUND="$(python3 - "$DB" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute("SELECT 1 FROM sessions WHERE state IN ('open', 'running') LIMIT 1").fetchone()
print("open" if row else "none")
PYEOF
)" || die "לא הצלחתי לבדוק אם יש סבב פתוח ($DB) — לא משדרגים"
[[ "$ROUND" == "none" ]] || { set_status failed "יש סבב פתוח/רץ — השדרוג לא התחיל" round "יש סבב פתוח/רץ — השדרוג לא התחיל"; die "יש סבב פתוח/רץ — לא משדרגים"; }

# 1. התג הקודם — יעד החזרה. update.py שומר אותו לפני ה-checkout.
PREV="$(python3 - "$REPO_DIR" "$DB" <<'PYEOF'
import sys
repo, db = sys.argv[1:3]
sys.path.insert(0, repo)
from server.db import connect, get_setting
from server import update
print(get_setting(connect(db), update.PREVIOUS_KEY) or "")
PYEOF
)" || die "קריאת התג הקודם מה-DB נכשלה"
[[ -n "$PREV" ]] || echo "אין תג קודם שמור (update_previous) — בכשל לא תהיה חזרה אוטומטית"

# 2. רק היחידה המנותקת רשאית לכתוב לעץ. כשל git נכתב לסטטוס כלשונו,
# ולפני שנגענו ביחידות, ב-initrd או בשירות הרץ.
stage fetch
if ! git_error="$(git -C "$REPO_DIR" fetch --tags 2>&1)"; then
    why="git fetch --tags נכשל: $git_error"
    set_status failed "$why" fetch "$why"
    die "$why"
fi
# ‏#1277: התג חתום במפתח שהעץ **המותקן** סומך עליו — לפני ה-checkout, כלומר
# לפני שקוד מהתג רץ. המאמת וקובץ המפתחות נקראים מהעץ הנוכחי (ה-fetch אינו
# נוגע בעץ העבודה): גרסה שנחתמה במפתח זר אינה מביאה איתה את המפתח שיאשר
# אותה. "לא נבדק" (2) הוא סירוב בדיוק כמו "לא חתום" (1), והצלחה דורשת ראיה
# חיובית — קוד 0 **וגם** שורת `signed:` (עיקרון 5).
SIGNERS="$REPO_DIR/install/release-signers"
stage verify
if sig="$(python3 -I "$REPO_DIR/tools/verify-release-tag.py" "$REPO_DIR" "$TAG" "$SIGNERS" 2>&1)" \
        && [[ "$sig" == "signed: "* ]]; then
    echo "חתימה: $TAG $sig"
else
    why="התג $TAG לא אומת כחתום — העדכון נדחה, העץ לא הוחלף: ${sig:-המאמת לא החזיר דבר}"
    set_status failed "$why" signature "$why"
    die "$why"
fi
stage install
if ! git_error="$(git -C "$REPO_DIR" checkout --detach "$TAG" 2>&1)"; then
    why="git checkout --detach $TAG נכשל: $git_error"
    set_status failed "$why" install "$why"
    die "$why"
fi
install_units_and_restart() {
    install -m 0644 install/*.service "$UNIT_DIR/"
    systemctl daemon-reload
    systemctl restart imagectl-server
}

# ‏#1287: הקבצים שהבנייה מחליפה — ה-vmlinuz וכל --output, באותו פענוח של
# tools/boot-payload-build.sh (שורה לבנייה, ‏# ושורה ריקה מדולגות). כאן ולא
# שם: יעד ישן שאין לו מצב "רשימה" היה חוסם חזרה לגרסה ישנה.
payload_files() {
    echo "$HTTP_ROOT/vmlinuz"
    if [[ "${payload_args[0]}" == --iso-default ]]; then
        printf '%s\n' "$HTTP_ROOT/initrd.img" "$HTTP_ROOT/initrd.img.gui"
        return
    fi
    local line words i
    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -n "$line" && "$line" != \#* ]] || continue
        read -r -a words <<<"$line"
        for i in "${!words[@]}"; do
            if [[ "${words[$i]}" == --output ]]; then echo "${words[$((i + 1))]:-}"; fi
        done
    done < "$FLAGS_FILE"   # קובץ שלא נקרא = סטטוס לא-אפס, לא רשימה חלקית
}

# מעתיק כל קובץ קיים ל-$PAYLOAD_SAVE/<n>, ורושם "<n> <נתיב>" ב-list. נקרא
# כ-`save_payload || rollback`, ושם `set -e` כבוי — לכן כל צעד נבדק בשמו.
save_payload() {
    local n=0 path files
    files="$(payload_files)" || return 1
    rm -rf "$PAYLOAD_SAVE" && mkdir -p "$PAYLOAD_SAVE" && : > "$PAYLOAD_SAVE/list" || return 1
    while IFS= read -r path; do
        [[ -n "$path" && -e "$path" ]] || continue
        cp -p "$path" "$PAYLOAD_SAVE/$n" && echo "$n $path" >> "$PAYLOAD_SAVE/list" || return 1
        n=$((n + 1))
    done <<<"$files"
    PAYLOAD_SAVED=1
    echo "המטען הקודם נשמר ב-$PAYLOAD_SAVE ($n קבצים)"
}

# מחזיר כל קובץ במקומו (העתקה ל-.restore ואז mv — מכונה שמורידה לא תקבל
# חצי קובץ) ומאמת ב-cmp. כל כשל = 1, והעותק נשאר לשחזור ידני.
restore_payload() {
    local n path
    while read -r n path; do
        cp -p "$PAYLOAD_SAVE/$n" "$path.restore" && mv -f "$path.restore" "$path" \
            && cmp -s "$PAYLOAD_SAVE/$n" "$path" || return 1
    done < "$PAYLOAD_SAVE/list" || return 1
    rm -rf "$PAYLOAD_SAVE"
}

# ‏#1301: <סוג> = ה-reason כשלא חזרנו — install (ברירת מחדל) או timeout (השרת החדש
# לא ענה בזמן). חזרה שהצליחה היא reason=rollback, כי זה מה שהמפעיל צריך לדעת: השרת
# עובד, על הגרסה הקודמת.
rollback() {   # rollback <סיבה> [<סוג>]
    local why="$1" cause="${2:-install}"
    trap - ERR
    echo "--- $(date -Is) upgrade to $TAG FAILED: $why ---"
    if [[ -z "$PREV" ]]; then
        set_status failed "$why · אין תג קודם שמור — לא הוחזר" "$cause" "$why"
        die "$why (אין תג קודם — לא הוחזר)"
    fi
    echo "מחזיר ל-$PREV"
    if git checkout --detach "$PREV"; then
        # המטען חוזר לפני ה-restart: השרת הישן עולה כבר מול ה-initrd שלו.
        if [[ "$PAYLOAD_SAVED" == 1 ]]; then
            if restore_payload; then
                INITRD_NOTE=" (וה-initrd הקודם הוחזר)"
            else
                INITRD_NOTE=" (החזרת ה-initrd הקודם נכשלה — העותק ב-$PAYLOAD_SAVE)"
            fi
            echo "מטען האתחול:$INITRD_NOTE"
        fi
        if install_units_and_restart; then
            set_status failed "$why · הוחזר ל-$PREV$INITRD_NOTE" rollback "$why"
            die "$why — הוחזר ל-$PREV"
        fi
    fi
    set_status failed "$why · והחזרה ל-$PREV נכשלה גם היא" "$cause" "$why"
    die "$why — והחזרה ל-$PREV נכשלה"
}

# ‏#1185: מכאן העץ כבר על התג החדש — והוא מה שהשרת יריץ באתחול הבא. לכן
# כל כשל מכאן והלאה חוזר לתג הקודם, גם כשל שאיש לא כתב לו `|| rollback`:
# בלי ה-trap, ‏`set -e` היה יוצא עם העץ על החדש, השירות/היחידות/ה-initrd
# על הישן, וסטטוס `running` לתמיד ("לא ידוע" שנראה כמו "בתהליך").
trap 'rollback "server-upgrade.sh נכשל בשורה $LINENO (קוד $?) — הפירוט ב-$LOG"' ERR
current=$(git rev-parse HEAD 2>&1) || rollback "git rev-parse HEAD נכשל אחרי checkout: $current"
target=$(git rev-list -n 1 "$TAG" 2>&1) || rollback "git rev-list נכשל עבור $TAG אחרי checkout: $target"
# שתי תשובות ריקות אינן "אותו קומיט" -- ראיה חיובית בלבד (עיקרון 5).
[[ -n "$current" && "$current" == "$target" ]] \
    || rollback "העץ ב-$REPO_DIR אינו על $TAG (HEAD הוא ${current:-ריק}, התג הוא ${target:-ריק})"

# 3. יחידות + 4. initrd
install -m 0644 install/*.service "$UNIT_DIR/" || rollback "התקנת יחידות systemd ל-$UNIT_DIR נכשלה"
systemctl daemon-reload || rollback "systemctl daemon-reload נכשל"

# 3ב. ‏#1334 — עדכון ב-VM. גרסה בלי הסקריפט = גרסה בלי היכולת (חזרה לתג ישן),
# לא כשל. ה-`if` מנטרל את ה-trap של ERR: הכשל כאן נרשם, לא מחזיר אחורה.
VMU_HOST="install/vmupdate-host.sh"
if [[ -f "$VMU_HOST" ]]; then
    if vmu_out="$(bash "$VMU_HOST" 2>&1)"; then
        echo "$vmu_out"
    else
        vmu_rc=$?
        echo "$vmu_out"
        vmu_why="עדכון ב-VM לא זמין: $(printf '%s\n' "$vmu_out" | tail -n 1) (install/vmupdate-host.sh, קוד $vmu_rc)"
        echo "server-upgrade: $vmu_why"
        UPGRADE_WARNING="$vmu_why"
        set_status running
    fi
else
    echo "$VMU_HOST אינו בתג $TAG — גרסה בלי עדכון ב-VM"
fi

# ‏#1230: "העדכון עבר והמכונות על initrd ישן" אסור — סוכן ישן מול שרת חדש.
# הבנייה היא tools/boot-payload-build.sh, אותו קוד של firstboot: הקרנל וה-
# epoch נגזרים עכשיו (לא מהקובץ), ו-vmlinuz של הקרנל הזה מועתק. שלושה מצבים:
#   * ‏$FLAGS_FILE קיים — הבניות שנרשמו בהתקנה (תפקיד + --output בלבד).
#   * חסר, בשרת שהותקן מה-ISO (יש $ISO_MANIFEST) — firstboot של לפני #1230
#     בנה בדיוק את ברירת המחדל של ה-ISO ולא רשם אותה. בונים אותה ורושמים.
#   * חסר, ואין ראיה איך השרת נבנה — לא מנחשים דגלים (עיקרון 5): חזרה
#     לתג הקודם, בשם. מי שבנה ידנית כותב את הקובץ ומנסה שוב.
if [[ -e "$FLAGS_FILE" ]]; then
    payload_args=(--flags-file "$FLAGS_FILE")
elif [[ -e "$ISO_MANIFEST" ]]; then
    echo "$FLAGS_FILE חסר בשרת מה-ISO — בונה את ברירת המחדל של ה-ISO ורושם אותה"
    payload_args=(--iso-default --write-flags "$FLAGS_FILE")
else
    rollback "$FLAGS_FILE חסר ואין $ISO_MANIFEST — לא ידוע אילו initrd לבנות, והמכונות היו נשארות על initrd ישן"
fi
save_payload || rollback "שמירת עותק של המטען הקודם ב-$PAYLOAD_SAVE נכשלה — לא בונים בלי דרך חזרה"
bash tools/boot-payload-build.sh --http-root "$HTTP_ROOT" --manifest "$ISO_MANIFEST" "${payload_args[@]}" \
    || rollback "בניית ה-initrd נכשלה (הפירוט ב-$LOG)"

# 5. restart + ראיה חיובית
stage restart
systemctl restart imagectl-server || rollback "systemctl restart imagectl-server נכשל"
systemctl is-active --quiet imagectl-server || rollback "imagectl-server אינו active אחרי restart"
bash install/console-live.sh "$CONSOLE_URL" "$TAG" "$LIVE_WAIT" "$CERT" \
    || rollback "השרת לא ענה ב-/health/live עם הגרסה $TAG תוך $LIVE_WAIT ש'" timeout

# כתובת הסוכן ל-verify-boot-payload: ‏IMAGECTL_URL מהיחידה (cli — המעבדה),
# אחרת מה שהקונסולה רשמה (deploy:url), אחרת loopback (רשת הפצה שטרם
# הוגדרה, #1088). ‏sed ולא grep -o: "לא נמצא" הוא תשובה, לא כישלון (pipefail).
AGENT_URL="$(systemctl show -p Environment --value imagectl-server \
    | sed -n 's/.*IMAGECTL_URL=\([^ "]*\).*/\1/p' | head -n1)"
if [[ -z "$AGENT_URL" ]]; then
    AGENT_URL="$(python3 - "$DB" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute("SELECT value FROM settings WHERE key = 'deploy:url'").fetchone()
print(row[0] if row and row[0] else "http://127.0.0.1:8080")
PYEOF
)" || AGENT_URL="http://127.0.0.1:8080"
fi
bash install/verify-boot-payload.sh --app-dir "$REPO_DIR" --http-root "$HTTP_ROOT" --server-url "$AGENT_URL" \
    || rollback "verify-boot-payload.sh נכשל מול $AGENT_URL"

echo "--- $(date -Is) upgrade to $TAG done and verified ---"
STAGE="done"
set_status "done"
# אחרי האימות בלבד. כשל כאן אינו כשל שדרוג (השרת החדש רץ ואומת) — נרשם
# בלוג, והריצה הבאה מוחקת את העותק לפני שהיא שומרת חדש (PAYLOAD_SAVED).
trap - ERR
rm -rf "$PAYLOAD_SAVE" || echo "server-upgrade: לא נמחק $PAYLOAD_SAVE — העותק נשאר על הדיסק"
