#!/bin/sh
# Read-only capture guards: NTFS fast-startup/hibernation (#651) and
# BitLocker (#671). Both run before a byte moves; capture.sh calls them.

CAPTURE_HIBERNATED_MESSAGE="הדיסק מהובר — כבה את Windows כיבוי מלא (fast-startup כבוי) וקלוט מחדש"

capture_ntfs_hibernation_reason() {
    # $1=partition node, $2=private mount point. Empty output means clean.
    # Non-empty output always means capture must stop; rc 1=hibernated, rc 2=not checked.
    _hn_node="$1"; _hn_mnt="$2"
    command -v ntfs-3g.probe >/dev/null 2>&1 || {
        echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: הכלי ntfs-3g.probe חסר"
        return 2
    }

    # Probing read-write mountability is itself read-only; it exposes dirty (15)
    # and hibernated (14), both of which remain mountable read-only.
    ntfs-3g.probe --readwrite "$_hn_node" >/dev/null 2>&1
    _hn_rc=$?
    case "$_hn_rc" in
        0) ;;
        14|15) echo "$CAPTURE_HIBERNATED_MESSAGE"; return 1 ;;
        *) echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node (ntfs-3g.probe rc=$_hn_rc)"; return 2 ;;
    esac

    # 17/09, measured on the lab build machine: hiberfil.sys (3.36 GB) exists
    # on every Windows with hibernation/fast-startup *enabled*, also after a
    # full shutdown (probe rc=0, ntfsresize clean) -- its presence is not the
    # hibernated state. ntfs-3g.probe reads the file header (hibr/HIBR) and
    # returns 14 exactly when Windows is hibernated or fast-started; that is
    # the whole check. The old "file exists" rule refused every such disk.
    return 0
}

_bitlocker_reason() {
    # $1=idx $2=fs $3=node. FOG -FVE-FS-; empty=ok, text=refuse (#671).
    case "$2" in *[Bb]it[Ll]ocker*) echo "מחיצה $1 מוצפנת ב-BitLocker — כבו את BitLocker לפני הקליטה"; return ;; esac
    command -v dd >/dev/null && command -v grep >/dev/null && command -v tr >/dev/null \
        || { echo "לא הצלחנו לבדוק BitLocker במחיצה $1"; return; }
    # ‏LC_ALL=C ו-F: החתימה בייטים קבועים, לא טקסט מקומי. בלי -i — היא
    # תמיד באותיות גדולות, ו-`grep -i` על קלט בינרי תחת locale של UTF-8
    # קורס (SIGABRT) ב-MSYS, מה שהיה הופך "לא נמצא" ל"לא הצלחנו לבדוק".
    dd if="$3" bs=512 count=1 2>/dev/null | tr -d '\0' | LC_ALL=C grep -qF -- '-FVE-FS-'
    _blrc=$?
    [ "$_blrc" -eq 1 ] && return
    [ "$_blrc" -eq 0 ] && echo "מחיצה $1 מוצפנת ב-BitLocker — כבו את BitLocker לפני הקליטה" \
        || echo "לא הצלחנו לבדוק BitLocker במחיצה $1"
}
