#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: after-merge-check.sh [<sha>]

Wait for every GitHub check-run on a commit to conclude success.
Defaults to the current refs/remotes/origin/main commit.
EOF
}

if (($# > 1)); then
    usage >&2
    exit 2
fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
    exit 0
fi

remote_url=$(git remote get-url origin)
case "$remote_url" in
    https://github.com/*|http://github.com/*)
        repo_path=${remote_url#*github.com/}
        ;;
    git@github.com:*)
        repo_path=${remote_url#git@github.com:}
        ;;
    ssh://git@github.com/*)
        repo_path=${remote_url#ssh://git@github.com/}
        ;;
    *)
        echo "error: cannot derive GitHub owner/repo from origin: $remote_url" >&2
        exit 1
        ;;
esac
repo_path=${repo_path%.git}
[[ "$repo_path" =~ ^[^/]+/[^/]+$ ]] || { echo "error: invalid GitHub repository path: $repo_path" >&2; exit 1; }

if (($# == 1)); then
    sha=$1
else
    # ‏ref המעקב המקומי יכול לפגר, וזה בדיוק מה שקרה כאן ב-#190: מקור
    # שיושב ב-HEAD מנותק, ‏`main` מקומי מיושן, וכל מי שנשען עליו בדק
    # את הקומיט הלא נכון **בלי שום סימן**. בלי fetch, הסקריפט הזה היה
    # מדווח "‏main ירוק" על main שאינו main.
    if ! git fetch --quiet origin main; then
        echo "error: git fetch origin main failed; cannot establish which commit main is" >&2
        exit 1
    fi
    sha=$(git rev-parse --verify FETCH_HEAD)
fi
[[ "$sha" =~ ^[0-9a-fA-F]{7,40}$ ]] || { echo "error: invalid commit SHA: $sha" >&2; exit 2; }

timeout_seconds=${AFTER_MERGE_TIMEOUT_SECONDS:-600}
poll_seconds=${AFTER_MERGE_POLL_SECONDS:-10}
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "error: timeout must be a positive integer" >&2; exit 2; }
[[ "$poll_seconds" =~ ^[1-9][0-9]*$ ]] || { echo "error: poll interval must be a positive integer" >&2; exit 2; }

deadline=$((SECONDS + timeout_seconds))
last_checks=""
while :; do
    # פלט מובנה אחד קושר את המונה והשורות לאותה תשובת API.
    if ! response=$(gh api --method GET "repos/$repo_path/commits/$sha/check-runs?per_page=100" \
        --jq '.total_count as $n | "COUNT\t\($n)", (.check_runs[] | "CHECK\t\(.name)\t\(.status)\t\(.conclusion // "pending")")'); then
        echo "error: GitHub API query failed for $repo_path@$sha" >&2
        exit 1
    fi

    count=""
    seen=0
    pending=0
    failed=0
    last_checks=""
    while IFS=$'\t' read -r kind first second third; do
        case "$kind" in
            COUNT) count=$first ;;
            CHECK)
                seen=$((seen + 1))
                last_checks+="$first"$'\t'"$third"$'\n'
                if [[ "$second" != "completed" ]]; then
                    pending=1
                elif [[ "$third" != "success" ]]; then
                    failed=1
                fi
                ;;
            *) echo "error: could not parse GitHub API response" >&2; exit 1 ;;
        esac
    done <<< "$response"

    [[ "$count" =~ ^[0-9]+$ ]] || { echo "error: API returned no check-run count" >&2; exit 1; }
    if ((count == 0)); then
        echo "error: zero check-runs returned for $sha" >&2
        exit 1
    fi
    if ((seen != count)); then
        echo "error: API reported $count check-runs but returned $seen; result is incomplete" >&2
        exit 1
    fi
    if ((failed == 1 || pending == 0)); then
        break
    fi
    if ((SECONDS >= deadline)); then
        printf '%s' "$last_checks"
        echo "verdict: FAILED — checks still pending after ${timeout_seconds}s" >&2
        exit 1
    fi
    sleep "$poll_seconds"
done

printf '%s' "$last_checks"
if ((failed == 1)); then
    echo "verdict: FAILED — at least one check did not conclude success" >&2
    exit 1
fi
echo "verdict: SUCCESS — all $count check-runs concluded success"
