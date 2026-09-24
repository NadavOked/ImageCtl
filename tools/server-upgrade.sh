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
#   2. fetch + checkout לתג המבוקש — מחוץ לארגז החול של שרת ה-web.
#   3. יחידות systemd — קודם, כדי ש-daemon-reload יראה כל שינוי בהן.
#   4. initrd (#1230) — tools/boot-payload-build.sh לפי הדגלים השמורים, מול
#      הקרנל שנגזר עכשיו. בלי דגלים ובלי ראיה שזה שרת ISO — חזרה, לא דילוג.
#   5. restart, ואז **ראיה חיובית** שהחדש רץ: is-active + `/health/live`
#      שמדווח בדיוק את התג + verify-boot-payload.sh.
#   6. כשל בראיה → checkout לתג הקודם, יחידות, restart, וסטטוס `failed`
#      עם הסיבה ב-DB (דרך update._set_status) — הקונסולה מציגה אותו.
#
# ‏#1074: שדרוג אינו מתקין ואינו מפעיל nftables. שרת קיים לא ינותק.
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
INITRD_NOTE=""   # נקבע רק אחרי שה-initrd החדש הוחלף בפועל
LOG="${IMAGECTL_UPGRADE_LOG:-/var/log/imagectl/server-upgrade.log}"
CONSOLE_URL="${IMAGECTL_CONSOLE_URL:-https://127.0.0.1:8081}"
LIVE_WAIT="${IMAGECTL_LIVE_WAIT:-90}"
CERT="$DATA_DIR/console-tls/console.crt"
[[ -f "$CERT" ]] || CERT=""

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
exec >>"$LOG" 2>&1
echo "--- $(date -Is) upgrading to $TAG in $REPO_DIR ---"

cd "$REPO_DIR" || die "no such repo dir: $REPO_DIR"

# סטטוס לקונסולה — אותו מפתח ואותה פונקציה של update.py, לא עותק.
set_status() {   # set_status <state> [<error>]
    python3 - "$REPO_DIR" "$DB" "$TAG" "$1" "${2:-}" <<'PYEOF' || echo "server-upgrade: כתיבת הסטטוס ל-DB נכשלה"
import sys
repo, db, tag, state, error = sys.argv[1:6]
sys.path.insert(0, repo)
from server.db import connect
from server import update
doc = {"state": state, "tag": tag}
if error:
    doc["error"] = error
update._set_status(connect(db), doc)
PYEOF
}

# 0. סבב פתוח/רץ? — אותה שאילתה של update._active_round; DB שלא נקרא = לא
# יודעים = לא משדרגים (עיקרון 5: "לא הצלחנו לבדוק" אינו "אין סבב").
ROUND="$(python3 - "$DB" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute("SELECT 1 FROM sessions WHERE state IN ('open', 'running') LIMIT 1").fetchone()
print("open" if row else "none")
PYEOF
)" || die "לא הצלחתי לבדוק אם יש סבב פתוח ($DB) — לא משדרגים"
[[ "$ROUND" == "none" ]] || { set_status failed "יש סבב פתוח/רץ — השדרוג לא התחיל"; die "יש סבב פתוח/רץ — לא משדרגים"; }

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
if ! git_error="$(git -C "$REPO_DIR" fetch --tags 2>&1)"; then
    why="git fetch --tags נכשל: $git_error"
    set_status failed "$why"
    die "$why"
fi
if ! git_error="$(git -C "$REPO_DIR" checkout --detach "$TAG" 2>&1)"; then
    why="git checkout --detach $TAG נכשל: $git_error"
    set_status failed "$why"
    die "$why"
fi
current=$(git describe --tags 2>&1) || {
    why="git describe --tags נכשל אחרי checkout: $current"
    set_status failed "$why"
    die "$why"
}
if [[ "$current" != "$TAG" ]]; then
    why="העץ ב-$REPO_DIR אינו על $TAG (הוא $current) — לא ממשיך"
    set_status failed "$why"
    die "$why"
fi

install_units_and_restart() {
    install -m 0644 install/*.service "$UNIT_DIR/"
    systemctl daemon-reload
    systemctl restart imagectl-server
}

rollback() {   # rollback <סיבה>
    local why="$1"
    echo "--- $(date -Is) upgrade to $TAG FAILED: $why ---"
    if [[ -z "$PREV" ]]; then
        set_status failed "$why · אין תג קודם שמור — לא הוחזר"
        die "$why (אין תג קודם — לא הוחזר)"
    fi
    echo "מחזיר ל-$PREV"
    if git checkout --detach "$PREV" && install_units_and_restart; then
        set_status failed "$why · הוחזר ל-$PREV$INITRD_NOTE"
        die "$why — הוחזר ל-$PREV"
    fi
    set_status failed "$why · והחזרה ל-$PREV נכשלה גם היא"
    die "$why — והחזרה ל-$PREV נכשלה"
}

# 3. יחידות + 4. initrd
install -m 0644 install/*.service "$UNIT_DIR/"
systemctl daemon-reload

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
bash tools/boot-payload-build.sh --http-root "$HTTP_ROOT" --manifest "$ISO_MANIFEST" "${payload_args[@]}" \
    || rollback "בניית ה-initrd נכשלה (הפירוט ב-$LOG)"
INITRD_NOTE=" (ה-initrd שנבנה מ-$TAG נשאר)"

# 5. restart + ראיה חיובית
systemctl restart imagectl-server || rollback "systemctl restart imagectl-server נכשל"
systemctl is-active --quiet imagectl-server || rollback "imagectl-server אינו active אחרי restart"
bash install/console-live.sh "$CONSOLE_URL" "$TAG" "$LIVE_WAIT" "$CERT" \
    || rollback "השרת לא ענה ב-/health/live עם הגרסה $TAG תוך $LIVE_WAIT ש'"

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
