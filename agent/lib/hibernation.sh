#!/bin/sh
# Read-only capture guards: NTFS fast-startup/hibernation (#651) and
# BitLocker (#671). Both run before a byte moves; capture.sh calls them.

CAPTURE_HIBERNATED_MESSAGE="הדיסק מהובר — כבה את Windows כיבוי מלא (fast-startup כבוי) וקלוט מחדש"

capture_ntfs_hibernation_reason() {
    # $1=partition node, $2=private mount point. Empty output means clean.
    # Non-empty output always means capture must stop; rc 1=hibernated, rc 2=not checked.
    _hn_node="$1"; _hn_mnt="$2"
    for _hn_tool in ntfs-3g.probe ntfs-3g umount; do
        command -v "$_hn_tool" >/dev/null 2>&1 || {
            echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: הכלי $_hn_tool חסר"
            return 2
        }
    done

    # Probing read-write mountability is itself read-only; it exposes dirty (15)
    # and hibernated (14), both of which remain mountable read-only.
    ntfs-3g.probe --readwrite "$_hn_node" >/dev/null 2>&1
    _hn_rc=$?
    case "$_hn_rc" in
        0) ;;
        14|15) echo "$CAPTURE_HIBERNATED_MESSAGE"; return 1 ;;
        *) echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node (ntfs-3g.probe rc=$_hn_rc)"; return 2 ;;
    esac

    mkdir -p "$_hn_mnt" || {
        echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: יצירת נקודת עיגון נכשלה"
        return 2
    }
    ntfs-3g -o ro "$_hn_node" "$_hn_mnt" >/dev/null 2>&1 || {
        echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: עיגון קריאה-בלבד נכשל"
        return 2
    }
    if [ -e "$_hn_mnt/hiberfil.sys" ]; then _hn_found=1; else _hn_found=0; fi
    umount "$_hn_mnt" >/dev/null 2>&1 || {
        echo "לא הצלחנו לבדוק מצב שינה ב-$_hn_node: ניתוק עיגון נכשל"
        return 2
    }
    [ "$_hn_found" -eq 0 ] || { echo "$CAPTURE_HIBERNATED_MESSAGE"; return 1; }
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
