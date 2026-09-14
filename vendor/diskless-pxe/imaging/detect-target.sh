#!/bin/sh
# Enumerate writable physical disks; never auto-select when ambiguous.
#
# Column output from `lsblk -o ... MODEL ...` splits on spaces inside
# MODEL ("Samsung SSD 970 EVO Plus"), so awk's $5 is not RO and the disk
# vanishes with no message (#307). `-P` emits KEY="value" pairs that do
# not depend on whitespace. Empty lsblk output is "no disks" (exit 0);
# lsblk missing or failing is exit 2 — plan.sh must not fold those.

set -eu

if ! command -v lsblk >/dev/null 2>&1; then
    echo "detect-target: lsblk is not installed" >&2
    exit 2
fi

set +e
raw=$(lsblk -dn -P -o NAME,TYPE,SIZE,MODEL,RO)
rc=$?
set -e
if [ "$rc" -ne 0 ]; then
    echo "detect-target: lsblk failed (exit $rc)" >&2
    exit 2
fi

# Busybox awk: walk KEY="value" tokens. Values may contain spaces.
printf '%s\n' "$raw" | awk '
{
    name = ""; type = ""; size = ""; model = ""; ro = ""
    line = $0
    while (match(line, /[A-Z]+="[^"]*"/)) {
        kv = substr(line, RSTART, RLENGTH)
        eq = index(kv, "=")
        key = substr(kv, 1, eq - 1)
        val = substr(kv, eq + 1)
        gsub(/"/, "", val)
        if (key == "NAME") name = val
        else if (key == "TYPE") type = val
        else if (key == "SIZE") size = val
        else if (key == "MODEL") model = val
        else if (key == "RO") ro = val
        line = substr(line, RSTART + RLENGTH)
    }
    if (type == "disk" && ro == "0" && name != "")
        printf "/dev/%s\t%s\t%s\n", name, size, model
}
'
