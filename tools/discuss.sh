#!/usr/bin/env bash
# הודעה מסוכן אל לוח הדיונים — כדי שהתקשורת בין הסוכנים תהיה במקום
# אחד שנדב יכול לעקוב אחריו, ולא בטרנסקריפטים נפרדים שאיש לא רואה.
#
# הסוכנים כאן רצים בכלים שונים (Claude, ‏Codex, ‏Gemini) ואינם יכולים
# לקרוא זה לזה. ‏Discussions הוא המצע המשותף היחיד שכולם מגיעים אליו,
# ולכן הוא גם המקום שבו "מי עושה מה" נשאר גלוי.
set -euo pipefail

REPO="${IMAGECTL_REPO:-NadavOked/ImageCtl}"

usage() {
    cat <<'EOF'
Usage:
  discuss.sh comment <discussion-number> <file|-->   הודעה לשרשור קיים
  discuss.sh new <category-slug> <title> <file|-->   שרשור חדש

  הגוף נקרא מקובץ, או מ-stdin כאשר הארגומנט הוא -
  קטגוריות: general · ideas · q-a · announcements · show-and-tell

דוגמה:
  echo "סיימתי את #201, PR פתוח" | tools/discuss.sh comment 4 -
EOF
}

die() { echo "error: $*" >&2; exit 1; }

read_body() {   # $1 = file or -
    # זהות הסוכן: כל ההודעות נשלחות בטוקן של נדב ולכן נראות כמוהו.
    # ‏IMAGECTL_AGENT מוסיף כותרת מובחנת — אימוג'י+שם — כדי שיהיה
    # ברור מי כתב. זו חתימה, לא אימות; זהות אמיתית = GitHub App.
    case "${IMAGECTL_AGENT:-}" in
        "")        ;;
        claude)    printf '🎩 **Claude — המתאם**\n\n' ;;
        grok)      printf '🦊 **Grok — סוקר**\n\n' ;;
        gemini-1)  printf '💎 **Gemini 1 — סוקר שוטף**\n\n' ;;
        gemini-2)  printf '🔷 **Gemini 2 — סוקר שוטף**\n\n' ;;
        codex)     printf '🐙 **Codex — סוקר אדוורסרי**\n\n' ;;
        runner)    printf '⚙️ **Runner — הרץ המתוזמן**\n\n' ;;
        *)         printf '🤖 **%s**\n\n' "$IMAGECTL_AGENT" ;;
    esac
    if [ "$1" = "-" ]; then cat; else
        [ -r "$1" ] || die "לא ניתן לקרוא את הקובץ: $1"
        cat "$1"
    fi
}

# הגוף עובר תמיד כמשתנה ל-GraphQL (‏-F body=@file), לעולם לא משורשר
# לתוך שאילתה. תוכן שנכתב על ידי סוכן הוא נתונים, לא קוד.
post_comment() {   # $1 = discussion number, $2 = body file
    local id url
    id=$(gh api graphql -F owner="${REPO%%/*}" -F name="${REPO##*/}" \
        -F number="$1" -q '.data.repository.discussion.id' -f query='
        query($owner:String!,$name:String!,$number:Int!){
          repository(owner:$owner,name:$name){ discussion(number:$number){ id } } }')
    [ -n "$id" ] || die "לא נמצא שרשור מספר $1"
    # ‏gh נכשל על שגיאת GraphQL (נמדד: EXIT=1), אבל `-q` על נתיב ריק
    # מחזיר 0 עם כלום — ה-URL שחוזר הוא הראיה, לא קוד היציאה.
    url=$(gh api graphql -F discussionId="$id" -F body=@"$2" \
        -q '.data.addDiscussionComment.comment.url' -f query='
        mutation($discussionId:ID!,$body:String!){
          addDiscussionComment(input:{discussionId:$discussionId,body:$body}){
            comment{ url } } }')
    [ -n "$url" ] || die "המוטציה חזרה ריקה — ההודעה לא נשלחה"
    printf '%s
' "$url"
}

create_thread() {   # $1 = category slug, $2 = title, $3 = body file
    local repo_id cat_id url
    repo_id=$(gh api graphql -F owner="${REPO%%/*}" -F name="${REPO##*/}" \
        -q '.data.repository.id' -f query='
        query($owner:String!,$name:String!){ repository(owner:$owner,name:$name){ id } }')
    cat_id=$(gh api graphql -F owner="${REPO%%/*}" -F name="${REPO##*/}" \
        -q ".data.repository.discussionCategories.nodes[] | select(.slug==\"$1\") | .id" -f query='
        query($owner:String!,$name:String!){
          repository(owner:$owner,name:$name){
            discussionCategories(first:25){ nodes{ id slug } } } }')
    [ -n "$cat_id" ] || die "אין קטגוריה בשם $1"
    url=$(gh api graphql -F repositoryId="$repo_id" -F categoryId="$cat_id" \
        -F title="$2" -F body=@"$3" \
        -q '.data.createDiscussion.discussion.url' -f query='
        mutation($repositoryId:ID!,$categoryId:ID!,$title:String!,$body:String!){
          createDiscussion(input:{repositoryId:$repositoryId,categoryId:$categoryId,
            title:$title,body:$body}){ discussion{ url } } }')
    [ -n "$url" ] || die "המוטציה חזרה ריקה — השרשור לא נוצר"
    printf '%s
' "$url"
}

case "${1:-}" in
    -h|--help|"") usage; [ -n "${1:-}" ] || exit 2; exit 0 ;;
    comment)
        [ $# -eq 3 ] || { usage >&2; exit 2; }
        [[ "$2" =~ ^[0-9]+$ ]] || die "מספר שרשור חייב להיות ספרות"
        tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
        read_body "$3" > "$tmp"
        [ -s "$tmp" ] || die "גוף ההודעה ריק — לא שולח"
        post_comment "$2" "$tmp"
        ;;
    new)
        [ $# -eq 4 ] || { usage >&2; exit 2; }
        # הסלאג משורשר לתוך מסנן jq — בלי הסינון הזה, מרכאה בסלאג
        # שוברת את הביטוי (ממצא סקירת Gemini על PR #209).
        [[ "$2" =~ ^[a-z0-9-]{1,50}$ ]] || die "סלאג קטגוריה: אותיות קטנות/ספרות/מקף בלבד"

        tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
        read_body "$4" > "$tmp"
        [ -s "$tmp" ] || die "גוף ההודעה ריק — לא שולח"
        create_thread "$2" "$3" "$tmp"
        ;;
    *) die "פקודה לא מוכרת: $1" ;;
esac
