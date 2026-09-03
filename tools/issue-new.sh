#!/bin/sh
# issue-new.sh — פותח Issue **מלא**, או לא פותח בכלל.
#
# למה הכלי הזה קיים: ‏CONTRIBUTING.md ו-ISSUES.md מכתיבים את אותה
# רביעיית פקודות מילה במילה (יצירה → לוח → תלות → קריאה חוזרת), ואיש
# לא הריץ אותה שלמה. התוצאה נמדדה ב-2026-08-31 — **70 Issues סגורים
# בלי milestone** ושניים בלי label. הפער לא נוצר מהחלטה אלא מכך שכל
# Issue בודד נפתח "מהר" ואיש לא חזר להשלים.
#
# שני עקרונות מנחים כאן:
#   * **הסירוב לפני `gh`.** ‏Issue בלי milestone הוא בדיוק מצב הכשל
#     שנמדד, ולכן הוא נעצר *לפני* שנוצר משהו שצריך לחזור אליו.
#   * **עיקרון 5 — הצלחה לפי ראיה חיובית.** ‏`gh issue create` שהחזיר
#     URL אינו ראיה שהשדות נקבעו. הכלי קורא אותם בחזרה ומוודא שכל אחד
#     אינו ריק. אין `|| true`, אין `2>/dev/null`, ואין `set +e`.
#
# ‏Issue חצי-מוגמר **אינו נמחק**: הוא מדווח עם ה-URL שלו. מחיקה שקטה
# היא בדיוק "לא הצלחנו לבדוק" שמתחפש ל"הכול תקין".
#
# POSIX sh (busybox ash) — אין bashisms, אין מערכים, אין `local`.
set -eu

usage() {
    cat <<'EOF'
Usage: issue-new.sh --title <כותרת> --label <תווית> --milestone <אבן דרך>
                    [--body-file <קובץ>] [--label <תווית> ...]
                    [--project <מספר>] [--blocked-by <#N> ...] [--repo <owner/name>]

חובה (נבדק לפני שנוגעים ב-gh):
  --title <טקסט>        כותרת ה-Issue
  --label <תווית>       תווית — ניתן לחזור. דרושות **סוג** (bug/enhancement/task)
                        **ורכיב** (server/agent/boot/console/installer/lab/ci/tests)
  --milestone <שם>      אבן דרך. זהו מצב הכשל שנמדד — ולכן הוא חובה

רשות:
  --body-file <קובץ>    גוף ה-Issue מקובץ. הגוף הוא ראיה, לא תיאור
  --project <מספר>      לוח העבודה (ברירת מחדל: 2)
  --blocked-by <#N>     Issue חוסם — ניתן לחזור. נרשם כקשר, לא כטקסט
  --repo <owner/name>   ברירת המחדל נגזרת מ-`git remote get-url origin`
  -h, --help            העזרה הזו

הכלי מריץ: יצירה → הוספה ללוח → תלויות → **קריאה חוזרת של השדות**.
כישלון בכל שלב הוא יציאה בקוד שאינו אפס, עם ה-URL על המסך.
EOF
}

die() {
    echo "error: $1" >&2
    exit "${2:-1}"
}

# --- קריאת הדגלים -----------------------------------------------------------

TITLE=""
BODY_FILE=""
LABELS=""
MILESTONE=""
PROJECT="2"
BLOCKED_BY=""
REPO=""

while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --title)      [ $# -ge 2 ] || die "‏--title דורש ערך" 2;      TITLE="$2";      shift 2 ;;
        --body-file)  [ $# -ge 2 ] || die "‏--body-file דורש ערך" 2;  BODY_FILE="$2";  shift 2 ;;
        --milestone)  [ $# -ge 2 ] || die "‏--milestone דורש ערך" 2;  MILESTONE="$2";  shift 2 ;;
        --project)    [ $# -ge 2 ] || die "‏--project דורש ערך" 2;    PROJECT="$2";    shift 2 ;;
        --repo)       [ $# -ge 2 ] || die "‏--repo דורש ערך" 2;       REPO="$2";       shift 2 ;;
        --label)
            [ $# -ge 2 ] || die "‏--label דורש ערך" 2
            [ -n "$2" ] || die "‏--label קיבל ערך ריק" 2
            if [ -z "$LABELS" ]; then LABELS="$2"; else LABELS="$LABELS,$2"; fi
            shift 2 ;;
        --blocked-by)
            [ $# -ge 2 ] || die "‏--blocked-by דורש ערך" 2
            # ‏"#123" ו-"123" הם אותו דבר לבני אדם; מנרמלים ומאמתים.
            dep=$(printf '%s' "$2" | sed 's/^#//')
            case "$dep" in
                ''|*[!0-9]*) die "‏--blocked-by מצפה למספר Issue, קיבל: $2" 2 ;;
            esac
            BLOCKED_BY="$BLOCKED_BY $dep"
            shift 2 ;;
        *) echo "ארגומנט לא מוכר: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# --- הסירוב: לפני שנוגעים ב-gh בכלל -----------------------------------------
#
# כאן נעצר מצב הכשל שנמדד. אחרי `gh issue create` כבר קיים Issue חלקי
# בריפו ציבורי, ומישהו צריך לחזור אליו — וזה בדיוק מה שלא קרה 70 פעם.

MISSING=""
[ -n "$TITLE" ]     || MISSING="$MISSING --title"
[ -n "$LABELS" ]    || MISSING="$MISSING --label"
[ -n "$MILESTONE" ] || MISSING="$MISSING --milestone"
if [ -n "$MISSING" ]; then
    echo "error: לא נפתח Issue. חסר:$MISSING" >&2
    echo "‏Issue אינו נחשב שנוצר עד שה-metadata שלו מלא (ISSUES.md)." >&2
    echo "‏milestone חסר הוא מצב הכשל שנמדד: 70 Issues סגורים ב-2026-08-31." >&2
    usage >&2
    exit 2
fi
if [ -n "$BODY_FILE" ] && [ ! -f "$BODY_FILE" ]; then
    die "‏--body-file אינו קיים: $BODY_FILE" 2
fi
case "$PROJECT" in
    ''|*[!0-9]*) die "‏--project מצפה למספר לוח, קיבל: $PROJECT" 2 ;;
esac

# --- הריפו נגזר, לא מוקשח ---------------------------------------------------
#
# הכלי הזה אמור לעבוד גם על ריפו אחר (otogit). ‏owner/name קשיח בקוד
# פותח Issue בריפו הלא נכון בשקט.

if [ -z "$REPO" ]; then
    if ! REMOTE_URL=$(git remote get-url origin); then
        die "אין remote בשם origin, ולא נמסר --repo" 2
    fi
    case "$REMOTE_URL" in
        https://github.com/*|http://github.com/*) REPO=${REMOTE_URL#*github.com/} ;;
        git@github.com:*)                         REPO=${REMOTE_URL#git@github.com:} ;;
        ssh://git@github.com/*)                   REPO=${REMOTE_URL#ssh://git@github.com/} ;;
        *) die "לא ניתן לגזור owner/repo מ-origin: $REMOTE_URL" 2 ;;
    esac
    REPO=${REPO%.git}
fi
OWNER=${REPO%%/*}
NAME=${REPO#*/}
if [ -z "$OWNER" ] || [ -z "$NAME" ] || [ "$OWNER" = "$REPO" ]; then
    die "‏--repo חייב להיות בצורת owner/name, התקבל: $REPO" 2
fi

# --- 1. יצירה ---------------------------------------------------------------

set -- --repo "$REPO" --title "$TITLE" --label "$LABELS" --milestone "$MILESTONE"
if [ -n "$BODY_FILE" ]; then
    set -- "$@" --body-file "$BODY_FILE"
fi
if ! URL=$(gh issue create "$@"); then
    die "‏gh issue create נכשל — לא נוצר Issue"
fi
# ‏gh מדפיס שורות נוספות בהזדמנויות שונות; ה-URL הוא השורה שנראית כמותו.
URL=$(printf '%s\n' "$URL" | grep -E '^https://[^ ]*/issues/[0-9]+$' | tail -n 1)
[ -n "$URL" ] || die "‏gh issue create הצליח אך לא החזיר URL של Issue"
NUMBER=${URL##*/}
case "$NUMBER" in
    ''|*[!0-9]*) die "לא ניתן לחלץ מספר Issue מ-$URL" ;;
esac
echo "נוצר: $URL"

# מכאן והלאה קיים Issue. כל כישלון חייב להציג את ה-URL — ולא למחוק דבר.
die_with_url() {
    echo "error: $1" >&2
    echo "ה-Issue קיים וחסר לו metadata — השלם ידנית: $URL" >&2
    exit "${2:-1}"
}

# --- 2. הלוח ----------------------------------------------------------------

if ! gh project item-add "$PROJECT" --owner "$OWNER" --url "$URL"; then
    die_with_url "‏gh project item-add נכשל (לוח $PROJECT, בעלים $OWNER)"
fi

# --- 3. תלויות --------------------------------------------------------------
#
# ה-API מצפה ל-id הפנימי של ה-Issue החוסם, לא למספר שלו. שתי הקריאות
# נפרדות, ולכן שתיהן נבדקות.

for dep in $BLOCKED_BY; do
    if ! DEP_ID=$(gh api "repos/$REPO/issues/$dep" --jq '.id'); then
        die_with_url "לא נמצא Issue חוסם #$dep בריפו $REPO"
    fi
    case "$DEP_ID" in
        ''|*[!0-9]*) die_with_url "‏id לא תקין ל-#$dep: $DEP_ID" ;;
    esac
    if ! gh api --method POST \
        "repos/$REPO/issues/$NUMBER/dependencies/blocked_by" \
        -F "issue_id=$DEP_ID" >/dev/null; then
        die_with_url "רישום התלות ב-#$dep נכשל"
    fi
done

# --- 4. קריאה חוזרת — הראיה החיובית -----------------------------------------
#
# עיקרון 5. ששלוש הפקודות למעלה יצאו באפס אינו אומר שהשדות נקבעו:
# ‏label שאינו קיים בריפו, milestone שהוקלד לא נכון, ולוח שהבעלים שלו
# אחר — כולם מצבים שבהם היציאה מוצלחת והשדה ריק.

if ! READBACK=$(gh issue view "$NUMBER" --repo "$REPO" \
    --json labels,milestone,projectItems \
    --jq '[(.labels | length), (if .milestone == null then 0 else 1 end), (.projectItems | length)] | @tsv'); then
    die_with_url "הקריאה החוזרת נכשלה — לא ניתן לאמת שה-metadata נקבע"
fi

N_LABELS=$(printf '%s' "$READBACK" | cut -f1)
N_MILESTONE=$(printf '%s' "$READBACK" | cut -f2)
N_PROJECT=$(printf '%s' "$READBACK" | cut -f3)

EMPTY=""
[ "${N_LABELS:-0}" -gt 0 ]    || EMPTY="$EMPTY labels"
[ "${N_MILESTONE:-0}" -gt 0 ] || EMPTY="$EMPTY milestone"
[ "${N_PROJECT:-0}" -gt 0 ]   || EMPTY="$EMPTY projectItems"

# התלות נבדקת באותה מידה: הבקשה שיצאה באפס אינה הקשר עצמו.
if [ -n "$BLOCKED_BY" ]; then
    if ! N_DEPS=$(gh api "repos/$REPO/issues/$NUMBER/dependencies/blocked_by" --jq 'length'); then
        die_with_url "לא ניתן לקרוא בחזרה את התלויות"
    fi
    [ "${N_DEPS:-0}" -gt 0 ] || EMPTY="$EMPTY blocked_by"
fi

if [ -n "$EMPTY" ]; then
    die_with_url "שדות ריקים אחרי היצירה:$EMPTY"
fi

echo "אומת: labels=$N_LABELS milestone=$N_MILESTONE project=$N_PROJECT"
echo "$URL"
