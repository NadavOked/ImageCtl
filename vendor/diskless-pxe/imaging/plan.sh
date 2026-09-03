#!/bin/sh
# Non-destructive planning helper. It NEVER writes an image.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target=$($DIR/detect-target.sh 2>/dev/null || true)
printf 'Detected target: %s\n' "${target:-NONE}"
printf '%s\n' 'No write command executed. Integration must explicitly enable imaging after server authorization and safety checks.'
