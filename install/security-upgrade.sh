#!/usr/bin/env bash
# install/security-upgrade.sh — עדכוני האבטחה של Debian, רק מתוך הגרסה החתומה (#1411).
#
# הכרעת נדב (15/10): לשרת אין מקור apt חי. כל גרסה של ImageSrv נושאת מאגר apt
# קטן — חבילות trixie-security של מה שהשרת מתקין (tools/iso/make-pool.sh
# --security) — והגיבוב שלו כתוב ב-install/security-pool.json **שבעץ התג**.
# חתימת התג (#1277) היא לכן החתימה על המאגר: tar שאינו תואם לגיבוב נדחה.
#
#   install/security-upgrade.sh <tag> <repo-dir>
#
# נקרא מ-tools/server-upgrade.sh אחרי אימות החתימה וה-checkout (העץ = התג).
# הסדר: גרסה בלי security-pool.json → אין מה לעשות (0) · המאגר הזה כבר הוחל
# (‏$DATA_DIR/security-pool.json, אותו sha256) → 0 · הורדה, גודל + sha256 מול
# התג · אילו חבילות **מותקנות** מפגרות אחרי המאגר (dpkg) · אם יש: apt-get
# install שלהן בשמן, שרואה **רק** את המאגר הזה (sources/lists/cache משלו), בלי הסרות,
# בתקרת זמן · ראיה חיובית: אף חבילה מותקנת אינה מפגרת אחריו · רישום המצב.
#
# ‏0 = הכול מעודכן ונבדק בחזרה. אחרת השורה האחרונה ב-stdout היא הסיבה:
#   2 security-pool.json לא תקין · 3 הורדה/גיבוב · 4 apt-get update נכשל או
#   בדיקת הגרסאות לא רצה (לא הותקן דבר) · 5 apt-get install נכשל/פקע או רצה להסיר (ייתכן
#   שחלק הותקן; dpkg --configure -a הורץ) · 6 apt יצא 0 וחבילות עדיין מפגרות.
#   החבילות **אינן מוחזרות** לגרסה קודמת בכשל — החזרה היא של הקוד (server-upgrade).
#
# משתני סביבה (בדיקות; בייצור ברירות המחדל): IMAGECTL_DATA_DIR,
# ‏IMAGECTL_SECURITY_POOL_URL (התיקייה שבה ה-tar; ברירת מחדל: ה-Release של התג
# ב-github, לפי origin), ‏IMAGECTL_SECURITY_TIMEOUT (שניות ל-apt, 1200),
# ‏IMAGECTL_SECURITY_DL_TIMEOUT (שניות להורדה, 1800), ‏IMAGECTL_APT, ‏IMAGECTL_DPKG,
# ‏IMAGECTL_DPKG_QUERY, ‏IMAGECTL_CURL, ‏IMAGECTL_PYTHON.
set -uo pipefail

TAG=${1:?usage: security-upgrade.sh <tag> <repo-dir>}
REPO=${2:?usage: security-upgrade.sh <tag> <repo-dir>}
DATA_DIR="${IMAGECTL_DATA_DIR:-/var/lib/imagectl}"
STATE="$DATA_DIR/security-pool.json"
WORK="$DATA_DIR/security-pool"
APT="${IMAGECTL_APT:-apt-get}"
DPKG="${IMAGECTL_DPKG:-dpkg}"
DPKG_QUERY="${IMAGECTL_DPKG_QUERY:-dpkg-query}"
CURL="${IMAGECTL_CURL:-curl}"
PYTHON="${IMAGECTL_PYTHON:-python3}"
APT_TIMEOUT="${IMAGECTL_SECURITY_TIMEOUT:-1200}"
DL_TIMEOUT="${IMAGECTL_SECURITY_DL_TIMEOUT:-1800}"
LOCK="$REPO/install/security-pool.json"

fail() { echo "$2"; exit "$1"; }

if [[ ! -e "$LOCK" ]]; then
    echo "עדכוני מערכת: הגרסה $TAG אינה נושאת מאגר עדכוני אבטחה — אין מה להתקין"
    exit 0
fi
lock_out="$("$PYTHON" -I "$REPO/install/security-pool-lock.py" "$LOCK" 2>&1)" \
    || fail 2 "עדכוני מערכת: install/security-pool.json של $TAG לא תקין: $lock_out"
read -r ASSET SHA SIZE DATE <<<"$lock_out"

applied_sha="$("$PYTHON" -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha256",""))' "$STATE" 2>/dev/null)" \
    || applied_sha=""
if [[ "$applied_sha" == "$SHA" ]]; then
    echo "עדכוני מערכת מתאריך $DATE כבר מותקנים (המאגר $ASSET)"
    exit 0
fi

cleanup() { rm -rf "$WORK"; }
trap cleanup EXIT
rm -rf "$WORK" && mkdir -p "$WORK/repo" "$WORK/lists/partial" "$WORK/cache/archives/partial" "$WORK/sources.list.d" \
    || fail 3 "עדכוני מערכת: לא נוצרה תיקיית העבודה $WORK"

# --- הורדה: מה-Release של התג, וראיה חיובית — גודל ו-sha256 מול התג החתום ----------
if [[ -n "${IMAGECTL_SECURITY_POOL_URL:-}" ]]; then
    base="${IMAGECTL_SECURITY_POOL_URL%/}"
else
    origin="$(git -C "$REPO" remote get-url origin 2>&1)" \
        || fail 3 "עדכוני מערכת: לא נקראה כתובת המקור של הגרסאות (git remote): $origin"
    origin="${origin%/}"; origin="${origin%.git}"
    [[ "$origin" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
        || fail 3 "עדכוני מערכת: המקור $origin אינו ריפו ב-github — לא ידוע מאיפה להוריד את $ASSET"
    base="$origin/releases/download/$TAG"
fi
echo "עדכוני מערכת: מוריד $ASSET ($SIZE בתים) מ-$base"
if ! dl_out="$("$CURL" -fsSL --proto '=https,file' --proto-redir '=https' --connect-timeout 20 \
        --speed-limit 10240 --speed-time 60 --max-time "$DL_TIMEOUT" \
        -o "$WORK/$ASSET" "$base/$ASSET" 2>&1)"; then
    fail 3 "עדכוני מערכת: הורדת $ASSET נכשלה: ${dl_out:-curl לא מסר סיבה}"
fi
got_size="$(stat -c %s "$WORK/$ASSET" 2>/dev/null)" || got_size=""
got_sha="$(sha256sum "$WORK/$ASSET" 2>/dev/null | cut -d' ' -f1)" || got_sha=""
[[ "$got_size" == "$SIZE" && "$got_sha" == "$SHA" ]] \
    || fail 3 "עדכוני מערכת: $ASSET שהורד אינו תואם לגיבוב שבגרסה החתומה (גודל ${got_size:-?}, sha256 ${got_sha:-?}) — נדחה"
tar -xf "$WORK/$ASSET" -C "$WORK/repo" --no-same-owner \
    || fail 3 "עדכוני מערכת: פתיחת $ASSET נכשלה"
comps=()
for d in "$WORK"/repo/dists/trixie/*/; do [[ -d "$d" ]] && comps+=("$(basename "$d")"); done
[[ ${#comps[@]} -gt 0 ]] || fail 3 "עדכוני מערכת: ב-$ASSET אין dists/trixie — אין מאגר"

# --- אילו חבילות מותקנות מפגרות אחרי המאגר. ‏dpkg-query: 1 = "לא מותקנת" (תשובה), --
# כל קוד אחר = הבדיקה עצמה נשברה (CLAUDE.md, "1 הוא תשובה").
# אינדקס שלא נפתח, או ריק, אינו "הכול מעודכן" — הבדיקה נכשלת (2).
behind() {
    local idx pkg ver st rc
    idx="$(zcat "$WORK"/repo/dists/trixie/*/binary-*/Packages.gz | awk '/^Package: /{p=$2} /^Version: /{print p, $2}')" \
        && [[ -n "$idx" ]] || return 2
    while read -r pkg ver; do
        st="$("$DPKG_QUERY" -W -f='${Status} ${Version}' "$pkg" 2>/dev/null)"; rc=$?
        if [[ $rc -eq 1 ]]; then continue; fi
        [[ $rc -eq 0 ]] || return 2
        [[ "$st" == "install ok installed "* ]] || continue
        "$DPKG" --compare-versions "${st#install ok installed }" ge "$ver" || echo "$pkg"
    done <<<"$idx"
}
before="$(behind)" || fail 4 "עדכוני מערכת: בדיקת הגרסאות המותקנות מול המאגר לא רצה — לא הותקן דבר"

if [[ -n "$before" ]]; then
    echo "עדכוני מערכת: $(wc -l <<<"$before") חבילות לעדכון: $(tr '\n' ' ' <<<"$before")"
    printf 'deb [trusted=yes] file:%s trixie %s\n' "$WORK/repo" "${comps[*]}" > "$WORK/sources.list"
    opts=(-o "Dir::Etc::SourceList=$WORK/sources.list" -o "Dir::Etc::SourceParts=$WORK/sources.list.d"
          -o "Dir::State::Lists=$WORK/lists" -o "Dir::Cache=$WORK/cache" -o "Acquire::Retries=0"
          -o "DPkg::Lock::Timeout=120")
    upd="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${opts[@]}" update -qq 2>&1)" \
        || fail 4 "עדכוני מערכת: apt-get update מול המאגר נכשל — לא הותקן דבר: $(tail -n 1 <<<"$upd")"
    # ‏install של החבילות המפגרות בשמן, ולא `upgrade`: על TA (‏17/10) ‏`upgrade
    # --with-new-pkgs` השאיר בצד את linux-image-amd64, שתלויה בחבילת קרנל חדשה, ויצא 0.
    # ‏--no-remove: אם apt רוצה להסיר חבילה כלשהי — הוא נכשל, ואנחנו איתו.
    # ‏--no-install-recommends: רק המפגרות ומה שהן חייבות, לא תוספות מהמאגר.
    mapfile -t names <<<"$before"
    up="$(timeout --kill-after=30 "$APT_TIMEOUT" env DEBIAN_FRONTEND=noninteractive "$APT" "${opts[@]}" \
            -y -q --no-remove --no-install-recommends \
            -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            install "${names[@]}" 2>&1)"; rc=$?
    printf '%s\n' "$up" | tail -n 20
    if [[ $rc -ne 0 ]]; then
        why="apt-get install נכשל (קוד $rc)"; [[ $rc -eq 124 ]] && why="apt-get install לא הסתיים תוך $APT_TIMEOUT ש'"
        timeout --kill-after=30 600 env DEBIAN_FRONTEND=noninteractive "$DPKG" --configure -a \
            || why="$why; גם dpkg --configure -a נכשל"
        fail 5 "עדכוני מערכת: $why: $(grep -v '^\s*$' <<<"$up" | tail -n 1)"
    fi
    after="$(behind)" || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל בדיקת הגרסאות אחריו לא רצה"
    [[ -z "$after" ]] || fail 6 "עדכוני מערכת: apt-get יצא 0, אבל לא עודכנו: $(tr '\n' ' ' <<<"$after")"
fi

state_tmp="$STATE.tmp"
"$PYTHON" -I -c 'import json,sys
k = ("date", "sha256", "asset", "tag", "upgraded")
print(json.dumps(dict(zip(k, sys.argv[1:6])), ensure_ascii=False))' \
    "$DATE" "$SHA" "$ASSET" "$TAG" "$( [[ -n "$before" ]] && wc -l <<<"$before" || echo 0)" > "$state_tmp" \
    && mv -f "$state_tmp" "$STATE" \
    || fail 6 "עדכוני מערכת הותקנו, אבל $STATE לא נכתב"
if grep -q '^linux-image-' <<<"$before"; then
    echo "אזהרה: קרנל חדש הותקן מעדכוני המערכת — בשרת עצמו הוא ייכנס לתוקף באתחול הבא"
fi
echo "עדכוני מערכת מתאריך $DATE מותקנים ($ASSET)"
