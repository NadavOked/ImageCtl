#!/usr/bin/env bash
#
# ImageCtl — בניית ISO ההתקנה (#1139): דביאן 13 netinst רשמי + preseed +
# ‏pool/ מקומי + הקוד + firstboot, כ-ISO hybrid (BIOS + UEFI/Secure Boot).
#
#   sudo tools/iso/build-iso.sh --out /root/ic-iso-tmp \
#        [--netinst debian-13.7.0-amd64-netinst.iso] [--pool /root/ic-iso-tmp/repo] \
#        [--ref v0.48.0] [--root-password '...' | IMAGECTL_ROOT_PASSWORD=...]
#
# רץ על דביאן 13 (שרת המעבדה). מה שהוא **אינו** עושה: לא מאתחל שום
# מכונה. הבדיקה שה-ISO עולה היא test-iso.sh (QEMU/OVMF) או VM אמיתי.
#
# למה ה-ISO נבנה מה-netinst ולא מאפס (R25 §1, R57): ‏shim ו-GRUB החתומים
# של דביאן מגיעים ממנו, ו-`xorriso -boot_image any replay` משכפל את ציוד
# האתחול (El Torito ×2, MBR, GPT) בלי לפרק אותו. ‏grub.cfg אינו חתום
# ואינו נאכף — עריכתו אינה שוברת Secure Boot.
#
# החבילות (R63): ה-netinst הוא כבר repo של apt (dists/ + pool/). ה-pool
# שלנו (make-pool.sh) מתמזג לתוכו ו-dists/ מאונדקס מחדש — וההתקנה
# מקבלת אותן דרך apt-cdrom, המסלול של כל התקנה מ-CD. אין file:/cdrom
# ב-chroot ואין מפתח ארעי.
#
# ‏Reproducible (R61): ‏SOURCE_DATE_EPOCH = זמן הקומיט של --ref; ‏git
# archive --mtime; touch על העץ; gzip -n; xorriso -volume_date. שתי
# בניות מאותו ref + אותו netinst + אותו pool אמורות לתת אותו SHA256 —
# **לא נמדד עדיין** (ראה README).
#
# הפלט: ‏$OUT/imagectl-<tag>-amd64.iso, ‏SHA256SUMS, ‏*.el_torito.txt (הראיה
# ששני קטעי האתחול נשמרו), ו-imagectl-iso.json (המניפסט, גם בתוך ה-ISO).

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$SCRIPT_DIR/../.." && pwd)
OUT=""
NETINST=""
POOL=""
REF="HEAD"
ROOT_PASSWORD="${IMAGECTL_ROOT_PASSWORD:-}"
CDIMAGE_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd"

usage() {
    cat <<'EOF'
ImageCtl — בניית ISO ההתקנה.

  sudo tools/iso/build-iso.sh --out DIR [אפשרויות]

  --out DIR            תיקיית העבודה והפלט (נדרשים ~4GB פנויים)
  --netinst FILE       ISO של דביאן 13 netinst; בלעדיו מוריד את הנוכחי מ-cdimage.debian.org
  --pool DIR           פלט של make-pool.sh --out; בלעדיו מריץ אותו (דורש אינטרנט)
  --ref REF            הקומיט/התג של הקוד שייכנס ל-ISO (ברירת מחדל HEAD)
  --root-password PW   סיסמת root הראשונית (מוחלפת בכניסה הראשונה); או IMAGECTL_ROOT_PASSWORD
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --out)           OUT="${2:?}"; shift 2 ;;
        --netinst)       NETINST="${2:?}"; shift 2 ;;
        --pool)          POOL="${2:?}"; shift 2 ;;
        --ref)           REF="${2:?}"; shift 2 ;;
        --root-password) ROOT_PASSWORD="${2:?}"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *) printf 'unknown option: %s  (try --help)\n' "$1" >&2; exit 2 ;;
    esac
done

die() { printf 'build-iso: %s\n' "$*" >&2; exit 1; }
say() { printf 'build-iso: %s\n' "$*"; }

[[ -n "$OUT" ]] || { usage >&2; exit 2; }
[[ -n "$ROOT_PASSWORD" ]] || die "חסרה סיסמת root: --root-password או IMAGECTL_ROOT_PASSWORD (הגיבוב אינו נשמר ב-git)"
for tool in xorriso dpkg-scanpackages git python3 sha256sum openssl gzip tar; do
    command -v "$tool" >/dev/null || die "חסר $tool"
done
[[ -f "$SCRIPT_DIR/preseed.cfg" && -f "$SCRIPT_DIR/packages.txt" ]] || die "אין preseed.cfg/packages.txt ליד הסקריפט"

install -d "$OUT"
OUT=$(cd "$OUT" && pwd)
WORK="$OUT/work"
ISO_TREE="$WORK/iso"

# --- הקוד: ref → תג, קומיט, epoch -----------------------------------------------
COMMIT=$(git -C "$REPO" rev-parse --verify "$REF^{commit}") || die "ref לא מוכר: $REF"
TAG=$(git -C "$REPO" describe --tags --always "$COMMIT")
EPOCH=$(git -C "$REPO" log -1 --format=%ct "$COMMIT")
export SOURCE_DATE_EPOCH="$EPOCH"
say "קוד: $TAG ($COMMIT), SOURCE_DATE_EPOCH=$EPOCH"

# --- ה-netinst ---------------------------------------------------------------
if [[ -z "$NETINST" ]]; then
    say "מוריד את רשימת ה-SHA256 מ-$CDIMAGE_URL"
    sums=$(curl -fsSL "$CDIMAGE_URL/SHA256SUMS") || die "הורדת SHA256SUMS נכשלה"
    line=$(printf '%s\n' "$sums" | grep -E ' debian-13\.[0-9.]+-amd64-netinst\.iso$' | head -n1)
    [[ -n "$line" ]] || die "אין debian-13.x-amd64-netinst.iso ב-SHA256SUMS — דביאן 13 כבר לא current?"
    expected=${line%% *}; name=${line##* }
    NETINST="$OUT/$name"
    if [[ ! -f "$NETINST" ]]; then
        say "מוריד $name"
        curl -fSL -o "$NETINST.part" "$CDIMAGE_URL/$name" || die "הורדת $name נכשלה"
        mv "$NETINST.part" "$NETINST"
    fi
    actual=$(sha256sum "$NETINST" | cut -d' ' -f1)
    [[ "$actual" == "$expected" ]] || die "sha256 של $name אינו תואם: $actual ≠ $expected"
    say "netinst אומת: $name"
fi
[[ -f "$NETINST" ]] || die "אין $NETINST"
NETINST=$(readlink -f "$NETINST")
NETINST_SHA=$(sha256sum "$NETINST" | cut -d' ' -f1)

# --- ה-pool ------------------------------------------------------------------
if [[ -z "$POOL" ]]; then
    POOL="$OUT/repo"
    say "אין --pool — מריץ make-pool.sh --out $POOL"
    bash "$SCRIPT_DIR/make-pool.sh" --out "$POOL" || die "make-pool.sh נכשל"
fi
[[ -d "$POOL/pool" ]] || die "אין $POOL/pool (פלט של make-pool.sh --out)"

# --- חילוץ ה-netinst ----------------------------------------------------------
rm -rf "$WORK"; install -d "$ISO_TREE"
say "מחלץ את ה-netinst"
xorriso -osirrox on -indev "$NETINST" -extract / "$ISO_TREE" >"$WORK/xorriso-extract.log" 2>&1 \
    || { tail -n 20 "$WORK/xorriso-extract.log" >&2; die "xorriso -extract נכשל"; }
chmod -R u+w "$ISO_TREE"
for must in isolinux/isolinux.cfg isolinux/menu.cfg boot/grub/grub.cfg install.amd/vmlinuz install.amd/initrd.gz dists pool .disk/info; do
    [[ -e "$ISO_TREE/$must" ]] || die "ה-netinst אינו מכיל $must — זה לא netinst של דביאן amd64?"
done

# --- מיזוג ה-pool ואינדוקס מחדש ------------------------------------------------
say "ממזג את ה-pool ($(find "$POOL/pool" -name '*.deb' | wc -l) קבצים)"
cp -a "$POOL/pool/." "$ISO_TREE/pool/"
bash "$SCRIPT_DIR/make-pool.sh" --index-only "$ISO_TREE" || die "אינדוקס dists/ נכשל"
for comp in main contrib; do
    [[ -s "$ISO_TREE/dists/trixie/$comp/binary-amd64/Packages.gz" ]] || die "אין Packages.gz ל-$comp אחרי האינדוקס"
done

# --- הקוד -----------------------------------------------------------------------
say "מכניס את הקוד ($REF) כ-imagectl-src/"
git -C "$REPO" archive --format=tar --prefix=imagectl-src/ --mtime="@$EPOCH" "$COMMIT" \
    | tar -x -C "$ISO_TREE" || die "git archive נכשל"
[[ -f "$ISO_TREE/imagectl-src/server/main.py" && -f "$ISO_TREE/imagectl-src/install/setup-boot-server.sh" ]] \
    || die "imagectl-src חסר את server/main.py או install/setup-boot-server.sh"

# --- preseed + firstboot --------------------------------------------------------
mapfile -t PKGS < <(sed 's/#.*//' "$SCRIPT_DIR/packages.txt" | tr -s ' \t' '\n' | grep -v '^$' | LC_ALL=C sort -u)
(( ${#PKGS[@]} )) || die "packages.txt ריק"
HASH=$(openssl passwd -6 "$ROOT_PASSWORD")
python3 - "$SCRIPT_DIR/preseed.cfg" "$ISO_TREE/preseed.cfg" "${PKGS[*]}" "$HASH" <<'PY'
import re, sys
src, dst, pkgs, pwhash = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
text = text.replace("@PKGSEL_INCLUDE@", pkgs).replace("@ROOT_PASSWORD_HASH@", pwhash)
left = re.findall(r"@[A-Z_]+@", text)
if left:
    sys.exit(f"placeholders left in preseed: {left}")
open(dst, "w", encoding="utf-8", newline="\n").write(text)
PY
install -d "$ISO_TREE/imagectl"
install -m 0755 "$SCRIPT_DIR/firstboot.sh" "$ISO_TREE/imagectl/firstboot.sh"
install -m 0755 "$SCRIPT_DIR/late-command.sh" "$ISO_TREE/imagectl/late-command.sh"
install -m 0644 "$SCRIPT_DIR/imagectl-firstboot.service" "$ISO_TREE/imagectl/imagectl-firstboot.service"

# --- תפריטי האתחול: ערך ImageCtl (ומשני) ב-BIOS וב-UEFI ---------------------------
# הפרמטרים לפני `---` הם של d-i ואינם עוברים לקרנל המותקן (R60: התפקיד
# נלכד ב-late-command.sh מ-/proc/cmdline של המתקין). אין timeout: התפריט
# ממתין לאדם — מחיקת דיסק אינה מתחילה לבד.
DI_ARGS="auto=true priority=critical preseed/file=/cdrom/preseed.cfg locale=en_US keymap=us"
cat > "$ISO_TREE/isolinux/imagectl.cfg" <<EOF
label imagectl
	menu label ^ImageCtl server install (wipes the first disk)
	menu default
	kernel /install.amd/vmlinuz
	append vga=788 initrd=/install.amd/initrd.gz $DI_ARGS --- quiet
label imagectl-secondary
	menu label ImageCtl ^secondary server install (wipes the first disk)
	kernel /install.amd/vmlinuz
	append vga=788 initrd=/install.amd/initrd.gz $DI_ARGS imagectl.role=secondary --- quiet
EOF
sed -i '0,/^include stdmenu.cfg$/s//include stdmenu.cfg\ninclude imagectl.cfg/' "$ISO_TREE/isolinux/menu.cfg"
grep -q '^include imagectl.cfg$' "$ISO_TREE/isolinux/menu.cfg" || die "הזרקת imagectl.cfg ל-isolinux/menu.cfg נכשלה"
# נמדד ב-QEMU (19/09): ‏gtk.cfg נושא `menu default` משלו — והאחרון מנצח, כך
# ש"Graphical install" נשאר מסומן; ו-spkgtk.cfg מגדיר `timeout 300` +
# ‏ontimeout שמפעיל **התקנה קולית אינטראקטיבית** אחרי 30 שניות בלי מגע.
# שניהם מוסרים: הערך שלנו הוא ברירת המחדל, והתפריט ממתין לאדם בלי גבול.
sed -i '/^[[:space:]]*menu default$/d' "$ISO_TREE/isolinux/gtk.cfg"
sed -i '/^timeout 300$/d; /^ontimeout /d; /^menu autoboot /d' "$ISO_TREE/isolinux/spkgtk.cfg"
if grep -q 'menu default' "$ISO_TREE/isolinux/gtk.cfg"; then die "gtk.cfg עדיין נושא menu default"; fi
if grep -qE '^(timeout|ontimeout|menu autoboot)' "$ISO_TREE/isolinux/spkgtk.cfg"; then die "spkgtk.cfg עדיין מפעיל התקנה קולית בטיימר"; fi
n_default=$(cat "$ISO_TREE/isolinux/imagectl.cfg" "$ISO_TREE/isolinux/gtk.cfg" "$ISO_TREE/isolinux/txt.cfg" | grep -cE '^[[:space:]]+menu default$' || true)
[[ "$n_default" -eq 1 ]] || die "לא בדיוק menu default אחד בתפריט הראשי (נמצאו $n_default)"

GRUB_ENTRIES="$WORK/grub-entries.cfg"
cat > "$GRUB_ENTRIES" <<EOF
set default=0
menuentry --hotkey=m 'ImageCtl server install (wipes the first disk)' {
    set background_color=black
    linux    /install.amd/vmlinuz vga=788 $DI_ARGS --- quiet
    initrd   /install.amd/initrd.gz
}
menuentry --hotkey=s 'ImageCtl secondary server install (wipes the first disk)' {
    set background_color=black
    linux    /install.amd/vmlinuz vga=788 $DI_ARGS imagectl.role=secondary --- quiet
    initrd   /install.amd/initrd.gz
}
EOF
python3 - "$ISO_TREE/boot/grub/grub.cfg" "$GRUB_ENTRIES" <<'PY'
import sys
path, entries = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read()
i = text.index("\nmenuentry ")
open(path, "w", encoding="utf-8", newline="\n").write(text[:i + 1] + open(entries, encoding="utf-8").read() + text[i + 1:])
PY
grep -q "ImageCtl server install" "$ISO_TREE/boot/grub/grub.cfg" || die "הזרקת הערך ל-boot/grub/grub.cfg נכשלה"

# --- המניפסט (R61) --------------------------------------------------------------
say "כותב imagectl-iso.json"
python3 - "$ISO_TREE" "$OUT/imagectl-iso.json" "$TAG" "$COMMIT" "$EPOCH" "$(basename "$NETINST")" "$NETINST_SHA" "${PKGS[*]}" <<'PY'
import hashlib, json, os, subprocess, sys
tree, out, tag, commit, epoch, netinst, netinst_sha, pkgs = sys.argv[1:9]
debs = []
for root, _dirs, files in os.walk(os.path.join(tree, "pool")):
    for f in sorted(files):
        if not f.endswith(".deb") or os.path.basename(root) != "imagectl":
            continue
        p = os.path.join(root, f)
        ctl = subprocess.run(["dpkg-deb", "-f", p, "Package", "Version"], check=True,
                             capture_output=True, text=True).stdout
        fields = dict(l.split(": ", 1) for l in ctl.strip().splitlines())
        debs.append({"package": fields["Package"], "version": fields["Version"],
                     "path": os.path.relpath(p, tree),
                     "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()})
debs.sort(key=lambda d: d["path"])
manifest = {
    "imagectl_tag": tag, "git_commit": commit, "source_date_epoch": int(epoch),
    "netinst": netinst, "netinst_sha256": netinst_sha,
    "xorriso": subprocess.run(["xorriso", "-version"], capture_output=True, text=True).stdout.splitlines()[0],
    "requested_packages": pkgs.split(),
    "pool_debs": debs,
}
s = json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True) + "\n"
for dst in (out, os.path.join(tree, "imagectl-iso.json")):
    open(dst, "w", encoding="utf-8", newline="\n").write(s)
print(f"build-iso: manifest: {len(debs)} debs")
PY

# --- זמנים אחידים (R61) ---------------------------------------------------------
find "$ISO_TREE" -exec touch -h -d "@$EPOCH" {} +

# --- ה-ISO עצמו --------------------------------------------------------------
ISO="$OUT/imagectl-$TAG-amd64.iso"
rm -f "$ISO"
say "בונה $ISO (replay של ציוד האתחול מה-netinst)"
xorriso -indev "$NETINST" -outdev "$ISO" \
    -boot_image any replay \
    -volume_date all_file_dates "=$EPOCH" \
    -update_r "$ISO_TREE" / -- \
    >"$WORK/xorriso-build.log" 2>&1 \
    || { tail -n 30 "$WORK/xorriso-build.log" >&2; die "xorriso נכשל"; }
[[ -s "$ISO" ]] || die "ה-ISO לא נכתב"

# --- הראיה: שני קטעי El Torito (BIOS 0x00 + UEFI 0xEF) נשמרו --------------------
REPORT="$ISO.el_torito.txt"
{
    xorriso -indev "$ISO" -report_el_torito plain
    echo
    xorriso -indev "$ISO" -report_el_torito as_mkisofs
} > "$REPORT" 2>/dev/null
n_boot=$(grep -c '^El Torito boot img' "$REPORT" || true)
grep -q 'El Torito boot img.*BIOS' "$REPORT" || die "אין קטע אתחול BIOS ב-$ISO (ראה $REPORT)"
grep -q 'El Torito boot img.*UEFI' "$REPORT" || die "אין קטע אתחול UEFI ב-$ISO (ראה $REPORT)"
say "El Torito: $n_boot קטעי אתחול (BIOS + UEFI) — $REPORT"

( cd "$OUT" && sha256sum "$(basename "$ISO")" > SHA256SUMS )
say "מוכן: $ISO ($(du -h "$ISO" | cut -f1))"
cat "$OUT/SHA256SUMS"
