#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
"$ROOT/tests/smoke.sh"
"$ROOT/tests/static-contract.sh"
"$ROOT/tests/no-destructive-defaults.sh"
# A missing shellcheck used to print "shellcheck not installed; skipped" and let
# the run exit 0 (#231). That is the second half of the same bug the guard above
# had: the one tool that would have caught SC2251 on the guard was skipped in
# silence, and the CI summary was green with zero static analysis behind it.
# "We did not check" is not "we checked and it is fine".
if command -v shellcheck >/dev/null 2>&1; then
  # `find ... | xargs shellcheck` reports the exit status of xargs only -- POSIX
  # sh has no pipefail -- so a traversal that died halfway (permissions, a path
  # that vanished) linted whatever it managed to list and returned success. The
  # list goes to a file, find's status is read, and a count of zero is a failure
  # and not "everything is clean".
  list=$(mktemp)
  trap 'rm -f "$list"' EXIT INT TERM
  if ! find "$ROOT" -type f -name '*.sh' -not -path '*/dist/*' -print0 > "$list"; then
    echo "ci: FAIL could not enumerate shell scripts under $ROOT" >&2
    exit 1
  fi
  found=$(tr -cd '\0' < "$list" | wc -c | tr -d ' ')
  if [ "$found" -eq 0 ]; then
    echo "ci: FAIL found 0 shell scripts to analyse under $ROOT" >&2
    exit 1
  fi
  echo "ci: shellcheck on $found shell scripts"
  xargs -0 shellcheck < "$list"
else
  echo "ci: FAIL shellcheck is not installed -- static analysis cannot be skipped" >&2
  echo "ci: install it (apt-get install shellcheck) and run again" >&2
  exit 1
fi
./tests/overlay-static.sh
./tests/no-host-block-passthrough.sh
./tools/lint-ipxe.sh
