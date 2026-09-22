#!/usr/bin/env bash
#
# ImageCtl — ה-repo המקומי של ה-ISO (#1139): כל חבילה מ-packages.txt
# **וכל התלויות שלה**, כקבצי .deb, ואינדקסי apt מעליהם.
#
#   sudo tools/iso/make-pool.sh --out /root/ic-iso-tmp/repo
#   sudo tools/iso/make-pool.sh --index-only <עץ-ISO>
#
# רץ על דביאן 13 עם אינטרנט (שרת המעבדה). **התקנה מה-ISO רצה בלי
# אינטרנט**, ולכן כל חבילה שחסרה כאן נכשלת רק מול שרת אמיתי — הסקריפט
# בודק לפני ההורדה שלכל שם יש מועמד ב-apt, ואחרי ההורדה שלכל שם יש
# ‏.deb ב-pool (ראיה חיובית, עיקרון 5). "apt לא התלונן" אינו ראיה.
#
# איך מקבלים את **כל** התלויות ולא רק את מה שחסר במכונה הזאת: ‏apt-get
# רץ מול קובץ status **ריק** (‏Dir::State::status), כלומר מנקודת מבטו
# שום חבילה אינה מותקנת — והוא מוריד את הסגירה המלאה, כולל libc6. זה
# יותר ממה שהמותקן צריך (הבסיס מגיע מה-netinst עצמו) ואינו מזיק: אותו
# קובץ באותה גרסה מחליף את עצמו.
#
# ‏Recommends נכללים: כך `apt-get install` של המתקין מתקין על שרת רגיל,
# וה-ISO צריך להתקין את אותו דבר (נמדד 19/09: ‏+45MB, ואין MTA בסגירה).
#
# הפלט (‏--out DIR):
#   DIR/pool/{main,contrib,...}/imagectl/*.deb   לפי ה-Section של החבילה
#   DIR/dists/trixie/<רכיב>/binary-amd64/{Packages.gz,Release}
#   DIR/dists/trixie/Release                     לא חתום — כמו ה-netinst הרשמי
#                                                עצמו; ‏d-i מוסיף את הדיסק דרך
#                                                apt-cdrom ו-TrustCDROM
# ‏--index-only DIR כותב רק את dists/ מעל pool/ קיים — כך build-iso.sh
# מאנדקס מחדש את עץ ה-ISO אחרי שה-pool שלנו מוזג לתוך זה של ה-netinst.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PACKAGES_FILE="$SCRIPT_DIR/packages.txt"
OUT=""
INDEX_ONLY=""
SUITE="trixie"
ARCH="amd64"

usage() {
    cat <<'EOF'
ImageCtl — בניית ה-repo המקומי של ה-ISO.

  sudo tools/iso/make-pool.sh --out DIR [--packages FILE]
  sudo tools/iso/make-pool.sh --index-only DIR

  --out DIR         לאן להוריד (pool/ + dists/ נכתבים מתחתיו)
  --packages FILE   ברירת מחדל: tools/iso/packages.txt
  --index-only DIR  רק dists/ מעל DIR/pool קיים (בלי apt)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)        OUT="${2:?}"; shift 2 ;;
        --packages)   PACKAGES_FILE="${2:?}"; shift 2 ;;
        --index-only) INDEX_ONLY="${2:?}"; shift 2 ;;
        -h|--help)    usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

die() { printf 'make-pool: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# אינדקס: Packages.gz לכל רכיב + Release אחד עם גיבוב לכל קובץ ב-dists
# ---------------------------------------------------------------------------
write_index() {
    local root="$1" comp packages_dir tmp_plain
    [[ -d "$root/pool" ]] || die "אין $root/pool — אין מה לאנדקס"
    command -v dpkg-scanpackages >/dev/null || die "חסר dpkg-scanpackages (חבילת dpkg-dev)"
    for comp_dir in "$root"/pool/*/; do
        comp=$(basename "$comp_dir")
        packages_dir="$root/dists/$SUITE/$comp/binary-$ARCH"
        install -d "$packages_dir"
        tmp_plain=$(mktemp)
        # ‏-m: שתי גרסאות של אותה חבילה (זו של ה-netinst וזו של
        # ‏trixie-updates) נשארות שתיהן — apt בוחר סט עקבי.
        ( cd "$root" && dpkg-scanpackages -m --arch "$ARCH" "pool/$comp" /dev/null ) \
            > "$tmp_plain" 2>"$tmp_plain.err" \
            || { cat "$tmp_plain.err" >&2; die "dpkg-scanpackages נכשל על pool/$comp"; }
        gzip -9 -n -c "$tmp_plain" > "$packages_dir/Packages.gz"
        rm -f "$tmp_plain" "$tmp_plain.err"
        [[ -f "$packages_dir/Release" ]] || cat > "$packages_dir/Release" <<EOF
Archive: stable
Origin: Debian
Label: Debian
Component: $comp
Architecture: $ARCH
EOF
        printf 'index: %s — %s חבילות\n' "$comp" \
            "$(zcat "$packages_dir/Packages.gz" | grep -c '^Package: ')"
    done

    local dists="$root/dists/$SUITE" comps rel rel_tmp
    comps=$(cd "$dists" && find . -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort | tr '\n' ' ')
    rel="$dists/Release"
    # נכתב מחוץ ל-dists: קובץ זמני בתוכו היה נכנס לרשימת הגיבובים של עצמו.
    rel_tmp=$(mktemp)
    {
        printf 'Origin: Debian\nLabel: Debian\nSuite: stable\nCodename: %s\n' "$SUITE"
        printf 'Date: %s\n' "$(date -u -R)"
        printf 'Architectures: %s\nComponents: %s\n' "$ARCH" "${comps% }"
        printf 'Description: Debian 13 + ImageCtl offline installer repository\n'
        # ‏apt מזהה אינדקס לפי השם **הלא-דחוס** (main/binary-amd64/Packages)
        # ורק אז בוחר דחיסה שרשומה. ‏Release שמונה רק את Packages.gz גורם
        # ל-apt לדלג על הרכיב **בשקט** — נמדד 19/09 מול ה-ISO: "Unable to
        # locate package" על כל הרשימה. לכן לכל .gz נרשם גם התוכן הפרוס.
        for algo in md5sum sha256sum; do
            case "$algo" in md5sum) printf 'MD5Sum:\n' ;; *) printf 'SHA256:\n' ;; esac
            while IFS= read -r f; do
                printf ' %s %16d %s\n' "$($algo < "$dists/$f" | cut -d' ' -f1)" "$(stat -c %s "$dists/$f")" "$f"
                case "$f" in
                    *.gz) printf ' %s %16d %s\n' "$(zcat "$dists/$f" | $algo | cut -d' ' -f1)" \
                              "$(zcat "$dists/$f" | wc -c)" "${f%.gz}" ;;
                esac
            done < <(cd "$dists" && find . -type f ! -name Release ! -name 'Release.gpg' ! -name InRelease -printf '%P\n' | LC_ALL=C sort)
        done
    } > "$rel_tmp"
    mv "$rel_tmp" "$rel"
    # חתימה ישנה מעל אינדקס חדש היא שגיאה, לא "לא חתום" — מסירים אותה.
    rm -f "$dists/Release.gpg" "$dists/InRelease"
    printf 'index: %s נכתב (%s)\n' "$rel" "${comps% }"
}

if [[ -n "$INDEX_ONLY" ]]; then
    write_index "$INDEX_ONLY"
    exit 0
fi

# ---------------------------------------------------------------------------
# הורדה
# ---------------------------------------------------------------------------
[[ -n "$OUT" ]] || { usage >&2; exit 2; }
[[ -f "$PACKAGES_FILE" ]] || die "אין $PACKAGES_FILE"
[[ $EUID -eq 0 ]] || die "צריך root (apt-get)"
command -v apt-get >/dev/null || die "אין apt-get — זה רץ על דביאן"

mapfile -t PKGS < <(sed 's/#.*//' "$PACKAGES_FILE" | tr -s ' \t' '\n' | grep -v '^$' | LC_ALL=C sort -u)
(( ${#PKGS[@]} )) || die "packages.txt ריק"
printf 'make-pool: %d חבילות מבוקשות\n' "${#PKGS[@]}"

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || die "apt-get update נכשל — בלי רשימות עדכניות אין מה להוריד"

# שלב 1 — לכל שם יש מועמד. שם בלי מועמד (typo, חבילה שירדה, contrib לא
# מופעל) הוא הכישלון ש-R25 מזהיר ממנו, והוא נעצר כאן ולא מול שרת.
missing=()
for p in "${PKGS[@]}"; do
    cand=$(apt-cache policy "$p" 2>/dev/null | awk '/Candidate:/ {print $2}')
    [[ -n "$cand" && "$cand" != "(none)" ]] || missing+=("$p")
done
if (( ${#missing[@]} )); then
    printf 'make-pool: אין מועמד ב-apt ל: %s\n' "${missing[*]}" >&2
    printf 'make-pool: fonts-ibm-plex יושבת ב-contrib — ודא ש-sources.list כולל contrib (#120)\n' >&2
    exit 1
fi

DL="$OUT/download"
install -d "$DL/partial" "$OUT/pool"
EMPTY_STATUS="$OUT/.empty-status"
: > "$EMPTY_STATUS"

printf 'make-pool: מוריד את הסגירה המלאה אל %s\n' "$DL"
apt-get -y --download-only \
    -o Dir::State::status="$EMPTY_STATUS" \
    -o Dir::Cache::archives="$DL" \
    -o Debug::NoLocking=1 \
    -o APT::Install-Recommends=true \
    install "${PKGS[@]}" \
    || die "apt-get --download-only נכשל"

# שלב 2 — מיון לרכיבים לפי ה-Section שבחבילה עצמה (contrib/fonts → contrib).
n=0
for deb in "$DL"/*.deb; do
    [[ -e "$deb" ]] || die "לא הורד אף .deb אל $DL"
    section=$(dpkg-deb -f "$deb" Section)
    case "$section" in
        */*) comp="${section%%/*}" ;;
        *)   comp="main" ;;
    esac
    install -d "$OUT/pool/$comp/imagectl"
    mv -f "$deb" "$OUT/pool/$comp/imagectl/"
    n=$((n + 1))
done
rm -rf "$DL" "$EMPTY_STATUS"
printf 'make-pool: %d קבצי .deb ב-%s/pool\n' "$n" "$OUT"

# שלב 3 — ראיה חיובית: לכל שם מבוקש יש קובץ. שם ללא קובץ = כישלון.
missing=()
for p in "${PKGS[@]}"; do
    found=$(find "$OUT/pool" -name "${p}_*.deb" -print -quit)
    [[ -n "$found" ]] || missing+=("$p")
done
if (( ${#missing[@]} )); then
    printf 'make-pool: הורדה הסתיימה אבל אין .deb ל: %s\n' "${missing[*]}" >&2
    exit 1
fi

write_index "$OUT"
du -sh "$OUT/pool" | awk '{print "make-pool: גודל ה-pool: " $1}'
