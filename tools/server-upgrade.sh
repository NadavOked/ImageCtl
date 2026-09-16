#!/usr/bin/env bash
# server-upgrade.sh — מסלול השדרוג של #748, אחרי שה-checkout כבר בוצע
# (server/update.py). רץ דרך `systemd-run --collect`, מנותק מתהליך
# imagectl-server, כי הצעד האחרון כאן הוא restart שלו.
#
# שימוש: server-upgrade.sh <tag> <repo-dir>
#
# מה שהוא עושה, בסדר הזה, ולמה בסדר הזה:
#   1. יחידות systemd — קודם, כדי ש-daemon-reload יראה כל שינוי בהן.
#   2. initrd, פעמיים — לפי דגלים שמורים, ולא בונה בלי דגלים (מדווח
#      זאת בשמה, לא נכשל בשקט — עיקרון 5).
#   3. restart אחרון: השרת עצמו מוחלף רק אחרי שהכול מוכן, לא באמצע.
set -euo pipefail

die() { echo "server-upgrade: $*" >&2; exit 1; }

[[ $# -eq 2 ]] || die "usage: server-upgrade.sh <tag> <repo-dir>"
TAG=$1
REPO_DIR=$2
FLAGS_FILE=/etc/imagectl/initrd.flags
LOG=/var/log/imagectl/server-upgrade.log

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true
exec >>"$LOG" 2>&1
echo "--- $(date -Is) upgrading to $TAG in $REPO_DIR ---"

cd "$REPO_DIR" || die "no such repo dir: $REPO_DIR"

# ה-checkout כבר בוצע ב-server/update.py (git fetch --tags + git checkout
# --detach); כאן רק מוודאים שהעץ באמת על התג המבוקש לפני שממשיכים.
current=$(git describe --tags 2>/dev/null || true)
[[ "$current" == "$TAG" ]] || die "העץ ב-$REPO_DIR אינו על $TAG (הוא $current) — לא ממשיך"

install -m 0644 install/*.service /etc/systemd/system/
systemctl daemon-reload

if [[ -r "$FLAGS_FILE" ]]; then
    # ‏$FLAGS_FILE: שורה אחת לכל בנייה שהמתקין הריץ בהתקנה המקורית (רגיל
    # וגרפי, #32) — בדיוק דגלי ה---output של tools/build_initramfs.sh,
    # לא מנוחשים כאן. שורה ריקה/‏# מדולגת.
    built=0
    while IFS= read -r line; do
        [[ -n "$line" && "$line" != \#* ]] || continue
        # shellcheck disable=SC2086
        bash tools/build_initramfs.sh $line
        built=$((built + 1))
    done < "$FLAGS_FILE"
    ((built > 0)) || echo "initrd לא נבנה: $FLAGS_FILE ריק"
else
    echo "initrd לא נבנה: אין דגלים ($FLAGS_FILE חסר)"
fi

systemctl restart imagectl-server
echo "--- $(date -Is) upgrade to $TAG done, restart issued ---"
