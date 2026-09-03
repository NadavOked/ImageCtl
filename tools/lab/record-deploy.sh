#!/bin/bash
# רישום פריסה ב-GitHub Environments — עם ראיה, לא עם הצהרה.
#
# למה זה קיים: "השרת מעודכן?" הייתה שאלה חוזרת לאורך כל הפרויקט, ולא
# הייתה לה תשובה שאפשר להסתכל עליה. ב-2026-08-31 נמצא ששרת המעבדה רץ
# על v0.16.2 בזמן ש-main כבר היה על v0.16.5 — שלוש גרסאות, ושום דבר
# לא צעק את זה.
#
# **למה לא runner מותקן על השרת:** GitHub מזהירים מפורשות לא להריץ
# self-hosted runner בריפו ציבורי — כל fork יכול להריץ קוד שרירותי
# עליו, כלומר על שרת שיושב ברשת הפנימית. הפריסה נשארת ידנית; מה
# שנרשם הוא **התוצאה שנמדדה**.
#
# שימוש:  bash tools/lab/record-deploy.sh lab root@10.98.10.8
#
# שם היחידה הוא `imagectl-server`, לא `imagectl` — הגרסה הראשונה של
# הסקריפט הזה בדקה את השם השגוי, וזה התגלה רק כשהורץ בפועל.
set -euo pipefail

ENVIRONMENT="${1:?שם סביבה: lab או college}"
TARGET="${2:?יעד SSH, למשל root@10.98.10.8}"
KEY="${IMAGECTL_LAB_KEY:-/c/ImageCtl-Lab/lab_key}"
REPO="NadavOked/ImageCtl"

ssh_() { ssh -i "$KEY" -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$TARGET" "$@"; }

echo "אוסף ראיות מ-$TARGET ..."
EVIDENCE=$(ssh_ 'set -e
  cd /opt/imagectl
  echo "version=$(git describe --tags 2>/dev/null || echo unknown)"
  echo "dirty=$(git status --porcelain | wc -l)"
  echo "active=$(systemctl is-active imagectl-server 2>/dev/null || echo unknown)"
  echo "routes=$(curl -sf http://127.0.0.1:8080/openapi.json | python3 -c "import json,sys; print(len(json.load(sys.stdin)[\"paths\"]))" 2>/dev/null || echo 0)"
  echo "served=$(curl -sf http://127.0.0.1:8080/boot/initrd.img | sha256sum | cut -d" " -f1)"
  echo "ondisk=$(sha256sum /srv/imagectl/boot/initrd.img 2>/dev/null | cut -d" " -f1)"
') || { echo "❌ איסוף הראיות נכשל — לא נרשמת פריסה." >&2; exit 1; }

# פענוח מפורש: רק המפתחות שאנחנו מצפים להם, ובלי eval על פלט מרוחק.
get() { printf '%s
' "$EVIDENCE" | sed -n "s/^$1=//p" | head -1; }
version=$(get version); dirty=$(get dirty); active=$(get active)
routes=$(get routes); served=$(get served); ondisk=$(get ondisk)

# ‏עיקרון 5: כל אחד מאלה שנכשל הוא "לא הצלחנו לבדוק", ולכן כישלון —
# ולא רישום פריסה שנראית תקינה.
FAIL=""
[ "$version" = "unknown" ]        && FAIL="$FAIL\n  אין git describe על /opt/imagectl"
[ "$dirty" != "0" ]               && FAIL="$FAIL\n  $dirty שינויים לא-מחויבים בעץ הפרוס"
[ "$active" != "active" ]         && FAIL="$FAIL\n  השירות אינו active (‏$active)"
[ "$routes" -lt 1 ] 2>/dev/null   && FAIL="$FAIL\n  ה-API לא החזיר מסלולים"
[ -z "$served" ] || [ -z "$ondisk" ] && FAIL="$FAIL\n  לא ניתן לקרוא sha256 של ה-initrd"
[ "$served" != "$ondisk" ]        && FAIL="$FAIL\n  ה-initrd המוגש שונה מזה שבדיסק"

if [ -n "$FAIL" ]; then
  printf "❌ האימות נכשל — לא נרשמת פריסה:%b\n" "$FAIL" >&2
  exit 1
fi

echo "  version=$version  routes=$routes  initrd=${served:0:12}"

SHA=$(git rev-parse HEAD)
# ‏required_contexts חייב להיות מערך JSON אמיתי — עם -F הוא נשלח כמחרוזת
# ומוחזר 422. לכן הגוף נבנה כ-JSON ונכנס דרך --input.
ID=$(python -c "
import json,sys
json.dump({'ref': sys.argv[1], 'environment': sys.argv[2],
           'description': sys.argv[3], 'auto_merge': False,
           'required_contexts': []}, sys.stdout)
" "$SHA" "$ENVIRONMENT" "$version · $routes routes · initrd ${served:0:12}"   | gh api "repos/$REPO/deployments" -X POST --input - -q .id)

gh api "repos/$REPO/deployments/$ID/statuses" -X POST \
  -f state=success \
  -f description="אומת: שירות פעיל, $routes מסלולים, initrd מוגש == בדיסק" \
  -f environment_url="http://10.98.10.8:8080" >/dev/null

echo "✅ נרשמה פריסה #$ID של $version לסביבת $ENVIRONMENT"
