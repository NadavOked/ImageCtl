"""tests/shcalls.py -- the scanner behind test_kiosk_functions_defined.py.

A scanner that over-reports buries the real findings; one that under-reports
is the bug it exists to catch. So each shape below is one the kiosk chain
really contains (or could), pinned in both directions: the word that IS a call
is found, and the word that merely looks like one is not.
"""

from __future__ import annotations

import pytest

from shcalls import scan_sh


def calls(src: str, **kw) -> list[str]:
    return [c.name for c in scan_sh(src, **kw).calls]


@pytest.mark.parametrize("src, expected", [
    ("foo bar baz\n", ["foo"]),
    ("a | b && c || d; e &\n", ["a", "b", "c", "d", "e"]),
    ("if x; then y; elif z; then w; else v; fi\n", ["x", "y", "z", "w", "v"]),
    ("while read -r l; do p \"$l\"; done < f\n", ["read", "p"]),
    ("! neg arg\n", ["neg"]),
    ("A=1 B=2 cmd arg\n", ["cmd"]),
    ("A=$(inner) \n", ["inner"]),
    ("foo >out 2>&1 <in\n", ["foo"]),
    ("foo \\\n  bar\n", ["foo"]),                       # continuation: bar is an argument
    ("foo # not_a_call\n", ["foo"]),
    ("foo; # c\nbar\n", ["foo", "bar"]),
])
def test_command_position_only(src, expected):
    assert calls(src) == expected


@pytest.mark.parametrize("src, expected", [
    ('x=$(a | b)\n', ["a", "b"]),
    ('echo "$(a) and `b`"\n', ["echo", "a", "b"]),
    ('echo "${v:-$(a)}"\n', ["echo", "a"]),
    ('echo $((1 + $(a)))\n', ["echo", "a"]),
    ("echo '$(not_a_call)' \"\\$(nor_this)\"\n", ["echo"]),
    ("x=$( (a; b) )\n", ["a", "b"]),
    ("echo ${#v} ${v%%x*}\n", ["echo"]),
])
def test_substitutions_are_scanned_and_quotes_are_not(src, expected):
    assert sorted(calls(src)) == sorted(expected)


def test_a_multiline_single_quoted_program_is_one_word():
    src = "awk -F'|' '\n  /x/ { print $1 }\n  END { not_a_call }\n' file | sort\n"
    assert calls(src) == ["awk", "sort"]


def test_heredocs_skip_the_body_but_an_unquoted_one_expands():
    quoted = "cat <<'EOF'\nnot_a_call $(nor_this)\nEOF\nafter\n"
    assert calls(quoted) == ["cat", "after"]
    unquoted = "cat <<EOF\nplain words\n$(does_run) x\nEOF\nafter\n"
    assert calls(unquoted) == ["cat", "does_run", "after"]
    tabbed = "cat <<-EOF\n\tbody\n\tEOF\nafter\n"
    assert calls(tabbed) == ["cat", "after"]


def test_case_patterns_are_not_commands_but_clause_bodies_are():
    src = ('case "$x" in\n  a|b) one ;;\n  "c d") two; three ;;\n  *) four ;;\nesac\nfive\n')
    assert calls(src) == ["one", "two", "three", "four", "five"]
    assert calls('case $x in a) one ;; esac; case $y in b) two\nesac\n') == ["one", "two"]


def test_a_case_inside_a_substitution_does_not_end_it_early():
    assert calls('r=$(case $x in a) one ;; *) two ;; esac); after\n') == ["one", "two", "after"]


def test_for_headers_and_double_brackets():
    assert calls("for f in a b c; do g \"$f\"; done\n") == ["g"]
    assert calls('[[ -n "$a" && ( "$b" = c || -z $(d) ) ]] && e\n') == ["d", "e"]


def test_definitions_and_the_owner_of_each_call():
    src = ("top1\nf() {\n    if x; then\n        { y; }\n    fi\n    z\n}\ng() { w; }\ntop2\n"
           "function h {\n    v\n}\n")
    scan = scan_sh(src)
    assert set(scan.defs) == {"f", "g", "h"}
    owners = {c.name: c.owner for c in scan.calls}
    assert owners == {"top1": "", "x": "f", "y": "f", "z": "f", "w": "g", "top2": "", "v": "h"}


def test_line_numbers_follow_the_source_even_inside_substitutions():
    scan = scan_sh("a\n\nb \"$(c\nd)\"\n")
    assert sorted((c.name, c.line) for c in scan.calls) == [("a", 1), ("b", 3), ("c", 3), ("d", 4)]


def test_wrappers_resolve_to_the_command_they_run():
    assert calls("timeout -s KILL 5 zork a b\n") == ["timeout", "zork"]      # not KILL, not 5
    assert calls("nice -n 10 cmd\n") == ["nice", "cmd"]
    assert calls("env A=1 cmd\n") == ["env", "cmd"]
    assert calls("exec /usr/bin/nice -n 10 /usr/bin/gui --x\n") == ["exec", "/usr/bin/nice", "/usr/bin/gui"]
    assert calls("/usr/bin/nice -n 10 /usr/bin/gui --x\n") == ["/usr/bin/nice", "/usr/bin/gui"]


def test_trap_eval_and_sh_dash_c_hold_commands_in_strings():
    assert calls("trap cleanup EXIT\n") == ["trap", "cleanup"]
    assert calls("trap 'a; b' INT TERM\n") == ["trap", "a", "b"]
    assert calls("trap - EXIT\n") == ["trap"]
    assert "later" in calls("eval 'later arg'\n")
    assert "child_fn" in calls("sh -c 'child_fn x'\n")
    assert calls('trap "$h" EXIT\n') == ["trap"]          # dynamic: not guessed


def test_runners_make_their_argument_a_call():
    src = "capped_run 5 \"$out\" smartctl -H\n"
    assert "smartctl" in calls(src, runners={"capped_run": 2})
    assert "smartctl" not in calls(src)
    scan = scan_sh("capped_run 5 o \"$X\" -H\n", runners={"capped_run": 2})
    assert [d[0] for d in scan.dynamic] == [1]


def test_a_command_word_that_is_an_expansion_is_dynamic_not_a_call():
    scan = scan_sh("f() {\n    $cmd arg\n    \"$@\" x\n    ok\n}\n")
    assert [c.name for c in scan.calls] == ["ok"]
    assert [(d[0], d[1]) for d in scan.dynamic] == [(2, "f"), (3, "f")]


def test_command_v_and_type_are_existence_tests_not_calls():
    scan = scan_sh("f() {\n  command -v opt >/dev/null && opt\n  command -V opt2 || :\n  type opt3\n}\n")
    assert scan.guards == {("f", "opt"), ("f", "opt2"), ("f", "opt3")}
    assert [c.name for c in scan.calls] == ["opt", ":", "type"]      # `command -v` itself is not a call
