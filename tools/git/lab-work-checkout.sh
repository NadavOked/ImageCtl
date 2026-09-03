#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: lab-work-checkout.sh <issue-number> [--host root@10.98.10.8] [--key <path>] [--force]

Create /root/ic-<N> on the lab at the fetched origin/main commit.
EOF
}

host="root@10.98.10.8"
key=""
force=0
issue=""

while (($#)); do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --host)
            (($# >= 2)) || { echo "error: --host requires a value" >&2; exit 2; }
            host=$2
            shift 2
            ;;
        --key)
            (($# >= 2)) || { echo "error: --key requires a path" >&2; exit 2; }
            key=$2
            shift 2
            ;;
        --force)
            force=1
            shift
            ;;
        -* )
            echo "error: unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            [[ -z "$issue" ]] || { echo "error: too many arguments" >&2; exit 2; }
            issue=$1
            shift
            ;;
    esac
done

[[ "$issue" =~ ^[0-9]+$ ]] || { echo "error: issue number must contain digits only" >&2; usage >&2; exit 2; }
if [[ -n "$key" && ! -r "$key" ]]; then
    echo "error: SSH key is not readable: $key" >&2
    exit 2
fi

ssh_args=()
if [[ -n "$key" ]]; then
    ssh_args=(-i "$key")
fi

# המקור פרוס מתג ולכן ה-HEAD המנותק שלו נשמר; רק ref המעקב מתרענן.
ssh "${ssh_args[@]}" "$host" bash -s -- "$issue" "$force" <<'REMOTE'
set -euo pipefail

issue=$1
force=$2
source_repo=/root/ImageCtl
checkout="/root/ic-$issue"
worker_tmp="/root/ic-$issue-tmp"

[[ "$issue" =~ ^[0-9]+$ ]] || { echo "error: invalid issue number on lab" >&2; exit 2; }
[[ -d "$source_repo/.git" ]] || { echo "error: source repo is missing: $source_repo" >&2; exit 1; }

if [[ -e "$checkout" || -e "$worker_tmp" ]]; then
    if [[ "$force" -ne 1 ]]; then
        echo "error: $checkout or $worker_tmp already exists; pass --force to replace it" >&2
        exit 1
    fi
    # הנתיבים נבנו רק ממספר מאומת; כך --force אינו יכול להרחיב את המחיקה.
    rm -rf -- "$checkout" "$worker_tmp"
fi

mkdir -p "$worker_tmp"
export TMPDIR=$worker_tmp

source_head_before=$(git -C "$source_repo" rev-parse HEAD)
git -C "$source_repo" fetch origin
source_head_after=$(git -C "$source_repo" rev-parse HEAD)
if [[ "$source_head_before" != "$source_head_after" ]]; then
    echo "error: source HEAD moved during fetch" >&2
    echo "source-before: $source_head_before" >&2
    echo "source-after:  $source_head_after" >&2
    exit 1
fi

remote_sha=$(git -C "$source_repo" rev-parse --verify refs/remotes/origin/main)
git clone --shared --no-checkout "$source_repo" "$checkout"
git -C "$checkout" checkout -B main "$remote_sha"
checkout_sha=$(git -C "$checkout" rev-parse --verify HEAD)

echo "origin/main: $remote_sha"
echo "checkout HEAD: $checkout_sha"
if [[ "$checkout_sha" != "$remote_sha" ]]; then
    echo "error: checkout HEAD does not match fetched origin/main" >&2
    exit 1
fi
echo "verified: $checkout is exactly at origin/main"
echo "TMPDIR: $worker_tmp"
REMOTE
