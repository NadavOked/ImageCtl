#!/usr/bin/env bash
# install/vmupdate-host.sh — מכין את המארח ל"עדכון אימג' ב-VM" (#1334):
# החבילות מ-install/vmupdate-packages.txt (המקור היחיד), ומשתמש המערכת
# imagectl-vm (QEMU רץ תחתיו; חומת האש של ה-VM מגבילה את ה-uid שלו).
#
# נקרא משני מקומות: setup-boot-server.sh (התקנה) ו-tools/server-upgrade.sh
# (העדכון מהכפתור — שרת שהותקן לפני #1334 מקבל את הכול כאן). אידמפוטנטי.
#
# ‏0 = הכול מותקן **ונבדק בחזרה** (dpkg-query לכל חבילה, ‏id למשתמש).
# אחרת: השורה האחרונה ב-stdout היא הסיבה, וקוד היציאה אומר איזה שלב:
#   2 רשימה ריקה/לא נקראה · 3 apt-get נכשל · 4 apt "הצליח" וחבילה אינה
#   מותקנת · 5 המשתמש לא נוצר. "apt יצא 0" אינו ראיה (עיקרון 5).
#
# בדיקות מחליפות את הכלים: IMAGECTL_APT, ‏IMAGECTL_DPKG_QUERY,
# ‏IMAGECTL_USERADD, ‏IMAGECTL_ID.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIST="${IMAGECTL_VMUPDATE_PACKAGES:-$HERE/vmupdate-packages.txt}"
APT="${IMAGECTL_APT:-apt-get}"
DPKG_QUERY="${IMAGECTL_DPKG_QUERY:-dpkg-query}"
USERADD="${IMAGECTL_USERADD:-useradd}"
ID="${IMAGECTL_ID:-id}"
VM_USER=imagectl-vm

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

echo "מתקין: ${pkgs[*]}"
if ! out="$(env DEBIAN_FRONTEND=noninteractive "$APT" install -y -qq "${pkgs[@]}" 2>&1)"; then
    printf '%s\n' "$out" | tail -n 5
    echo "apt-get install נכשל: $(printf '%s\n' "$out" | grep -v '^\s*$' | tail -n 1)"
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
