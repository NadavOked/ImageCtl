# Reference: the module closure of tools/build_initramfs.sh as it was at
# origin/integration/v0743-speed 7e5f4427 (16/10), verbatim -- before the one-pass
# rewrite (#1464). tests/test_initramfs_closure_1464.py runs it next to the current
# block on the same fake module trees and requires the same result. The build never
# runs it; do not edit it (a change here is a change of the reference).
[ -f "$MODSRC/modules.softdep" ] || {
    echo "$MODSRC/modules.softdep is missing -- soft dependencies cannot be verified" >&2
    exit 1
}
_closure_round=0
_closure_changed=1
while [ "$_closure_changed" -eq 1 ]; do
    _closure_changed=0
    _closure_round=$((_closure_round + 1))
    for _rel in $(cd "$ROOT/lib/modules/$KVER" && find . -name '*.ko*' | sed 's|^\./||'); do
        for _dep in $(awk -F: -v k="$_rel" '$1 == k {print $2}' "$MODSRC/modules.dep"); do
            if [ ! -f "$ROOT/lib/modules/$KVER/$_dep" ] && [ -f "$MODSRC/$_dep" ]; then
                mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_dep")"
                cp -a "$MODSRC/$_dep" "$ROOT/lib/modules/$KVER/$_dep"
                _closure_changed=1
            fi
        done
        _module=$(basename "$_rel")
        _module=${_module%%.ko*}
        _module=$(printf '%s' "$_module" | tr '-' '_')
        for _soft in $(awk -v want="$_module" '
            $1 == "softdep" {
                mod=$2; gsub(/-/, "_", mod)
                if (mod == want) for (i=3; i<=NF; i++)
                    if ($i != "pre:" && $i != "post:") print $i
            }' "$MODSRC/modules.softdep"); do
            _soft=$(printf '%s' "$_soft" | tr '-' '_')
            _soft_found=0
            # a file, not a pipe: the flags set inside must survive the loop,
            # and the tests run this snippet under dash, so no process
            # substitution either (SC2044 on the public run, 22/09)
            _cands=$(mktemp "$ROOT/module-candidates.XXXXXX"); find "$MODSRC" -type f -name '*.ko*' > "$_cands"
            while IFS= read -r _candidate; do
                _candidate_name=$(basename "$_candidate")
                _candidate_name=${_candidate_name%%.ko*}
                _candidate_name=$(printf '%s' "$_candidate_name" | tr '-' '_')
                case "$_candidate_name" in
                    "$_soft"|"$_soft"_*)
                        _soft_found=1
                        _dep=${_candidate#"$MODSRC"/}
                        if [ ! -f "$ROOT/lib/modules/$KVER/$_dep" ]; then
                            mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_dep")"
                            cp -a "$_candidate" "$ROOT/lib/modules/$KVER/$_dep"
                            _closure_changed=1
                        fi ;;
                esac
            done < "$_cands"; rm -f "$_cands"
            # A softdep names a *capability*, not always a file: btrfs wants
            # blake2b-256, which the alias table maps to blake2b_generic
            # (`alias crypto-blake2b-256 blake2b_generic`). Resolve through
            # modules.alias before declaring it missing (found on the first
            # --installer build, 21/09).
            if [ "$_soft_found" -eq 0 ] && [ -f "$MODSRC/modules.alias" ]; then
                _soft_dash=$(printf '%s' "$_soft" | tr '_' '-')
                for _alias_mod in $(awk -v a="crypto-$_soft_dash" -v b="$_soft_dash" '$1 == "alias" && ($2 == a || $2 == b) { print $3 }' "$MODSRC/modules.alias" | sort -u); do
                    _alias_mod=$(printf '%s' "$_alias_mod" | tr '-' '_')
                    _cands=$(mktemp "$ROOT/module-candidates.XXXXXX"); find "$MODSRC" -type f -name '*.ko*' > "$_cands"
                    while IFS= read -r _candidate; do
                        _candidate_name=$(basename "$_candidate"); _candidate_name=${_candidate_name%%.ko*}
                        _candidate_name=$(printf '%s' "$_candidate_name" | tr '-' '_')
                        [ "$_candidate_name" = "$_alias_mod" ] || continue
                        _soft_found=1
                        _dep=${_candidate#"$MODSRC"/}
                        if [ ! -f "$ROOT/lib/modules/$KVER/$_dep" ]; then
                            mkdir -p "$ROOT/lib/modules/$KVER/$(dirname "$_dep")"
                            cp -a "$_candidate" "$ROOT/lib/modules/$KVER/$_dep"
                            _closure_changed=1
                        fi
                    done < "$_cands"; rm -f "$_cands"
                    if [ "$_soft_found" -eq 0 ] && awk -v want="$_alias_mod" '
                        { n=$0; sub(/^.*\//, "", n); sub(/\.ko.*$/, "", n); gsub(/-/, "_", n) }
                        n == want { found=1 } END { exit !found }' "$MODSRC/modules.builtin"; then
                        _soft_found=1
                    fi
                done
            fi
            if [ "$_soft_found" -eq 0 ]; then
                if ! awk -v want="$_soft" '
                    { n=$0; sub(/^.*\//, "", n); sub(/\.ko.*$/, "", n); gsub(/-/, "_", n) }
                    n == want { found=1 } END { exit !found }' "$MODSRC/modules.builtin"
                then
                    echo "soft dependency $_soft of $_module is missing under $MODSRC" >&2
                    exit 1
                fi
            fi
        done
    done
    # תקרה: גרף התלויות של הקרנל אינו מעגלי, אבל לולאה אינסופית בבנייה
    # גרועה מאימג' חסר.
    [ "$_closure_round" -ge 10 ] && break
done
echo "module dependency closure: $(find "$ROOT/lib/modules/$KVER" -name '*.ko*' | wc -l) modules after $_closure_round rounds"
