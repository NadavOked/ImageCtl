#!/bin/sh
# Tools the build host must have. Missing required is FAIL; missing
# optional is OPTIONAL. The previous version printed WARN and exited 0
# for a host without sha256sum (#307) — `|| echo` is a command that
# succeeds, so set -e never fired.
set -eu

# Walk PATH for an executable. Busybox ash with FEATURE_SH_STANDALONE
# reports applets via `command -v` even when they are not on PATH.
on_path() {
    _cmd=$1
    _ifs=$IFS
    IFS=:
    for _dir in $PATH; do
        IFS=$_ifs
        [ -n "$_dir" ] || _dir=.
        if [ -x "$_dir/$_cmd" ]; then
            return 0
        fi
    done
    IFS=$_ifs
    return 1
}

need="curl gzip cpio sha256sum"
optional="qemu-system-x86_64 shellcheck"
fail=0
checked=0

for x in $need; do
    checked=$((checked + 1))
    if ! on_path "$x"; then
        echo "FAIL missing required: $x" >&2
        fail=1
    fi
done
for x in $optional; do
    checked=$((checked + 1))
    if ! on_path "$x"; then
        echo "OPTIONAL missing: $x" >&2
    fi
done

[ "$checked" -gt 0 ] || { echo "FAIL checked 0 tools" >&2; exit 2; }

if [ "$fail" -ne 0 ]; then
    echo "hostcheck: FAIL ($checked tools checked)" >&2
    exit 1
fi
echo "hostcheck: PASS ($checked tools checked)"
