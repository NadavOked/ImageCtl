#!/bin/sh
# Record the latest hello attempt separately from its response.  A blank code
# means transport failed before an HTTP response; it must not look like code 0.

hello_post() {
    # $1 = URL, $2 = request JSON, $3 = response JSON.
    _hp_code=$(curl -sS -f --max-time "$HTTP_TIMEOUT" --retry "$HTTP_RETRIES" \
        -o "$3.next" -w '%{http_code}' -H "Content-Type: application/json" \
        --data-binary "@$2" "$1")
    _hp_rc=$?
    case "$_hp_code" in 000|*[!0-9]*|"") _hp_code="" ;; esac
    printf '%s|%s\n' "$(date +%s)" "$_hp_code" > "$RUN_DIR/hello.state.next" || return 1
    mv "$RUN_DIR/hello.state.next" "$RUN_DIR/hello.state" || return 1
    if [ "$_hp_rc" -ne 0 ]; then rm -f "$3.next"; return "$_hp_rc"; fi
    mv "$3.next" "$3"
}
