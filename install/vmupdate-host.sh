#!/usr/bin/env bash
# install/vmupdate-host.sh — מכין את המארח ל"עדכון אימג' ב-VM" (#1334):
# החבילות מ-install/vmupdate-packages.txt (המקור היחיד), ומשתמש המערכת
# imagectl-vm (QEMU רץ תחתיו; חומת האש של ה-VM מגבילה את ה-uid שלו).
#
# נקרא משלושה מקומות: setup-boot-server.sh (התקנה), tools/server-upgrade.sh
# (העדכון מהכפתור — שרת שהותקן לפני #1334 מקבל את הכול כאן), והשרת עצמו
# בעלייה כשחסר משהו (‏#1372, ‏server/vmhost.py — עדכון שרץ בסקריפט של התג
# הישן לא הגיע לכאן). אידמפוטנטי.
#
# ‏0 = הכול מותקן **ונבדק בחזרה** (dpkg-query לכל חבילה, ‏id למשתמש).
# אחרת: השורה האחרונה ב-stdout היא הסיבה, וקוד היציאה אומר איזה שלב:
#   2 רשימה ריקה/לא נקראה · 3 apt-get נכשל · 4 apt "הצליח" וחבילה אינה
#   מותקנת · 5 המשתמש לא נוצר · 6 מקורות apt של שרת מה-ISO לא הוכנו.
#   "apt יצא 0" אינו ראיה (עיקרון 5).
#
# ‏#1371 — שרת מה-ISO (יש sources.list.d/imagectl-iso.list): המקור היחיד שלו
# הוא המאגר המקומי, ו-ISO ישן אינו נושא את חבילות ה-VM כלל. לכן, לפני apt:
#   * המאגר המקומי מפרסם רק מה שיש בו (install/apt-repo-consistent.py) —
#     שרת מ-ISO של לפני #1371 פרסם כ-500 חבילות בלי הקבצים.
#   * ‏Debian מהאינטרנט ב-https, **בעדיפות נמוכה** (‏Pin-Priority 100 מול 500
#     של המקומי): חבילה שקיימת מקומית נלקחת מהמאגר המקומי; חבילה חדשה —
#     מהאינטרנט, כשחומת האש פותחת 443. סגור = מה שבמאגר המקומי בלבד.
#   ואז `apt-get update` עם תקרת זמן — מקור שאינו נגיש אינו תוקע את ההתקנה.
#
# בדיקות מחליפות את הכלים: IMAGECTL_APT, ‏IMAGECTL_DPKG_QUERY,
# ‏IMAGECTL_USERADD, ‏IMAGECTL_ID, ‏IMAGECTL_PYTHON; ואת הנתיבים:
# ‏IMAGECTL_APT_ETC (‏/etc/apt), ‏IMAGECTL_APT_REPO (‏/var/lib/imagectl/apt-repo).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="${IMAGECTL_VMUPDATE_PACKAGES:-$HERE/vmupdate-packages.txt}"
APT="${IMAGECTL_APT:-apt-get}"
DPKG_QUERY="${IMAGECTL_DPKG_QUERY:-dpkg-query}"
USERADD="${IMAGECTL_USERADD:-useradd}"
ID="${IMAGECTL_ID:-id}"
PYTHON="${IMAGECTL_PYTHON:-python3}"
APT_ETC="${IMAGECTL_APT_ETC:-/etc/apt}"
LOCAL_REPO="${IMAGECTL_APT_REPO:-/var/lib/imagectl/apt-repo}"
VM_USER=imagectl-vm
#: ‏#1371: המקור מהאינטרנט ועדיפותו. ‏500 היא ברירת המחדל של כל מקור אחר.
NET_SOURCE="deb https://deb.debian.org/debian trixie main contrib"
NET_PRIORITY=100

pkgs=()
if [[ -r "$LIST" ]]; then
    while IFS= read -r line || [[ -n "$line" ]]; do
        line="${line%%#*}"
        for word in $line; do pkgs+=("$word"); done
    done < "$LIST"
fi
if [[ ${#pkgs[@]} -eq 0 ]]; then
    echo "רשימת החבילות של עדכון ה-VM לא נקראה ($LIST)"
    exit 2
fi

if [[ -e "$APT_ETC/sources.list.d/imagectl-iso.list" ]]; then
    if ! repo_out="$("$PYTHON" "$HERE/apt-repo-consistent.py" "$LOCAL_REPO" 2>&1)"; then
        printf '%s\n' "$repo_out"
        echo "המאגר המקומי $LOCAL_REPO לא נבדק: $(printf '%s\n' "$repo_out" | tail -n 1)"
        exit 6
    fi
    printf '%s\n' "$repo_out"
    if ! { printf '%s\n' "$NET_SOURCE" > "$APT_ETC/sources.list.d/imagectl-debian.list" \
           && mkdir -p "$APT_ETC/preferences.d" \
           && printf 'Package: *\nPin: origin "deb.debian.org"\nPin-Priority: %s\n' "$NET_PRIORITY" \
                > "$APT_ETC/preferences.d/imagectl-debian.pref"; }; then
        echo "מקור Debian מהאינטרנט לא נכתב ל-$APT_ETC"
        exit 6
    fi
    echo "מקורות: המאגר המקומי, ו-deb.debian.org (https, עדיפות $NET_PRIORITY)"
fi

# רשימות עדכניות — אחרת מקור שנוסף זה עתה אינו קיים בעיני apt. כשל כאן אינו
# סוף הדרך (המאגר המקומי עשוי להספיק): ההתקנה והבדיקה בחזרה מכריעות, והסיבה
# נוספת לשורת הכשל שלהן. "לא נגיש" הוא ב-apt אזהרה ויציאה 0 — ולכן נקרא מהפלט.
net_note=""
upd_out="$(env DEBIAN_FRONTEND=noninteractive "$APT" update -qq -o Acquire::Retries=0 \
    -o Acquire::http::Timeout=20 -o Acquire::https::Timeout=20 2>&1)" \
    || net_note="apt-get update נכשל: $(printf '%s\n' "$upd_out" | grep -v '^\s*$' | tail -n 1)"
if [[ -z "$net_note" ]] && grep -qE '^(W|E): .*deb\.debian\.org' <<<"$upd_out"; then
    net_note="deb.debian.org לא נגיש (חומת האש סוגרת 443?) — רק מה שבמאגר המקומי"
fi
[[ -z "$net_note" ]] || echo "$net_note"

echo "מתקין: ${pkgs[*]}"
if ! out="$(env DEBIAN_FRONTEND=noninteractive "$APT" install -y -qq "${pkgs[@]}" 2>&1)"; then
    printf '%s\n' "$out" | tail -n 5
    echo "apt-get install נכשל: $(printf '%s\n' "$out" | grep -v '^\s*$' | tail -n 1)${net_note:+ · $net_note}"
    exit 3
fi

missing=()
for pkg in "${pkgs[@]}"; do
    status="$("$DPKG_QUERY" -W -f='${Status}' "$pkg" 2>/dev/null)" || status=""
    [[ "$status" == "install ok installed" ]] || missing+=("$pkg")
done
if [[ ${#missing[@]} -gt 0 ]]; then
    echo "apt-get יצא 0, אבל לא מותקנות: ${missing[*]}"
    exit 4
fi

if ! "$ID" -u "$VM_USER" >/dev/null 2>&1; then
    "$USERADD" --system --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin "$VM_USER" \
        || { echo "יצירת משתמש המערכת $VM_USER נכשלה"; exit 5; }
fi
if ! "$ID" -u "$VM_USER" >/dev/null 2>&1; then
    echo "משתמש המערכת $VM_USER אינו קיים אחרי useradd"
    exit 5
fi
echo "עדכון ב-VM: ${#pkgs[@]} חבילות מותקנות, המשתמש $VM_USER קיים"
