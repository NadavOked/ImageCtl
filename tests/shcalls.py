"""A small POSIX-sh scanner: which commands does a script call, and which
functions does it define?

Written for ``test_kiosk_functions_defined.py``. The question it answers is
narrow -- "what word sits in command position, and inside which function?" --
so it is a lexer with just enough grammar to get *that* right, not a shell
parser. What it understands: quotes, ``$(..)`` / backticks / ``${..}`` /
``$((..))`` (recursively, because a call inside ``$(..)`` is a call), heredocs
(the body is skipped; an unquoted one is scanned for ``$(..)``), ``case``
patterns, ``for`` headers, ``[[ .. ]]``, assignments, redirections, comments,
``name() {`` definitions (the owner of a call is the function whose braces it
sits in), and the wrappers that take a command as an argument (``exec``,
``nice``, ``timeout``, ``trap``, ``eval``, ``sh -c`` ...).

What it deliberately does not do: resolve a command word that is itself an
expansion (``$cmd args``). Those are reported as ``dynamic`` so the caller can
see how much it cannot see -- never silently dropped (CLAUDE.md principle 5).
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field

_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")
_RESERVED = {"if", "then", "elif", "else", "while", "until", "do", "!", "time"}
_CLOSERS = {"fi", "done"}
_SEP = set(";&|()<>")
_SPACE = " \t"

#: wrappers whose real command is one of their later arguments, with the
#: options of each that consume a value (so the value is not taken for the
#: command: ``timeout -s KILL 5 cmd``).
_WRAPPERS = {
    "exec": set(), "nice": {"-n"}, "nohup": set(), "setsid": set(), "time": set(),
    "timeout": {"-s", "-k"}, "env": set(), "ionice": {"-c", "-n"},
}
_SHELLS = {"sh", "bash", "ash", "dash"}


@dataclass(frozen=True)
class Call:
    name: str
    line: int
    owner: str      # the function the call sits in ("" = file top level)
    via: str = ""   # the wrapper it was found behind ("trap", "exec", "sh -c" ...)


@dataclass
class Scan:
    calls: list[Call] = field(default_factory=list)
    defs: dict[str, int] = field(default_factory=dict)                  # function -> line
    dynamic: list[tuple[int, str, str]] = field(default_factory=list)   # (line, owner, word)
    guards: set[tuple[str, str]] = field(default_factory=set)           # (owner, name) tested by command -v / type


class _Lexer:
    def __init__(self, text: str, scan: Scan, base_line: int = 1, owner: str = "",
                 runners: dict[str, int] | None = None):
        self.runners = runners or {}
        self.s = text
        self.i = 0
        self.n = len(text)
        self.scan = scan
        self.owner = owner
        self.base = base_line - 1
        self._nl = [m.start() for m in re.finditer("\n", text)]
        self.heredocs: list[tuple[str, bool, bool]] = []
        self.depth = 0                                    # keyword-brace depth
        self.fn_stack: list[tuple[str, str, int]] = []    # (function, outer owner, depth at its "{")
        self.pending_fn = ""
        self.word_line = 1
        self.in_bt = False                                # inside `...`: a backtick ends the word

    # -- helpers ---------------------------------------------------------
    def line(self) -> int:
        return self.base + bisect.bisect_left(self._nl, self.i) + 1

    def peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def skip_ws(self) -> None:
        while self.i < self.n and self.s[self.i] in _SPACE + "\n":
            self.i += 1

    # -- words -----------------------------------------------------------
    def read_word(self) -> tuple[str, bool]:
        """Read one word at ``self.i``. Returns (text with quotes removed,
        whether it contained an expansion). Nested ``$(..)`` are scanned."""
        out: list[str] = []
        expanded = False
        while self.i < self.n:
            c = self.s[self.i]
            if c in _SPACE or c == "\n" or c in _SEP or (c == "`" and self.in_bt):
                break
            if c == "\\":
                if self.peek(1) == "\n":
                    self.i += 2
                    continue
                out.append(self.peek(1))
                self.i += 2
            elif c == "'":
                j = self.s.find("'", self.i + 1)
                j = self.n if j < 0 else j
                out.append(self.s[self.i + 1:j])
                self.i = j + 1
            elif c == '"':
                self.i += 1
                text, ex = self.read_dq()
                out.append(text)
                expanded = expanded or ex
            elif c == "$" or c == "`":
                expanded = True
                self.read_expansion()
                out.append("$")
            else:
                out.append(c)
                self.i += 1
        return "".join(out), expanded

    def read_dq(self, stop_at: int | None = None) -> tuple[str, bool]:
        """Inside double quotes (the opening quote already consumed), up to
        the closing one -- or, for an unquoted heredoc body, up to ``stop_at``."""
        out: list[str] = []
        expanded = False
        while self.i < (self.n if stop_at is None else stop_at):
            c = self.s[self.i]
            if c == '"' and stop_at is None:
                self.i += 1
                break
            if c == "\\":
                out.append(self.peek(1))
                self.i += 2
            elif c == "$" or c == "`":
                expanded = True
                self.read_expansion()
                out.append("$")
            else:
                out.append(c)
                self.i += 1
        return "".join(out), expanded

    def read_expansion(self) -> None:
        c = self.s[self.i]
        if c == "`":
            self.i += 1
            self.commands("`")
            return
        nxt = self.peek(1)
        if nxt == "(" and self.peek(2) == "(":            # $(( arithmetic ))
            self.i += 3
            depth = 2
            while self.i < self.n and depth:
                ch = self.s[self.i]
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                elif ch == "$" and self.peek(1) == "(" and self.peek(2) != "(":
                    self.read_expansion()
                    continue
                self.i += 1
        elif nxt == "(":                                    # $( command )
            self.i += 2
            self.commands(")")
        elif nxt == "{":                                    # ${ param }
            self.i += 2
            self.skip_braces()
        else:                                               # $name, $1, $@, $?
            self.i += 1
            if self.i < self.n:
                ch = self.s[self.i]
                if ch.isalpha() or ch == "_":
                    while self.i < self.n and (self.s[self.i].isalnum() or self.s[self.i] == "_"):
                        self.i += 1
                elif ch.isalnum() or ch in "@*#?$!-":
                    self.i += 1

    def skip_braces(self) -> None:
        depth = 1
        while self.i < self.n and depth:
            c = self.s[self.i]
            if c == "\\":
                self.i += 2
            elif c == "'":
                j = self.s.find("'", self.i + 1)
                self.i = (self.n if j < 0 else j) + 1
            elif c == '"':
                self.i += 1
                self.read_dq()
            elif c == "$" and self.peek(1) == "{":
                depth += 1
                self.i += 2
            elif c == "$" or c == "`":
                self.read_expansion()
            else:
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                self.i += 1

    # -- heredocs --------------------------------------------------------
    def eat_heredocs(self) -> None:
        for delim, strip, quoted in self.heredocs:
            start = self.i
            body_end = self.n
            while self.i < self.n:
                eol = self.s.find("\n", self.i)
                eol = self.n if eol < 0 else eol
                row = self.s[self.i:eol]
                if (row.lstrip("\t") if strip else row) == delim:
                    body_end = self.i
                    self.i = min(eol + 1, self.n)
                    break
                self.i = min(eol + 1, self.n)
            if not quoted:
                end = self.i
                self.i = start
                self.read_dq(stop_at=body_end)
                self.i = end
        self.heredocs = []

    # -- commands --------------------------------------------------------
    def commands(self, term: str | None) -> None:
        """Scan until the matching ``term`` (``)``, a backtick) or EOF."""
        outer, self.in_bt = self.in_bt, term == "`"
        try:
            self._commands(term)
        finally:
            self.in_bt = outer

    def _commands(self, term: str | None) -> None:
        cmd_pos: bool | None = True    # None: words are not commands (for-header)
        cur: list[tuple[str, bool, int]] = []     # words of the current simple command
        cases = 0
        pattern = False
        paren = 0

        def flush() -> None:
            nonlocal cur
            if cur:
                self.finish(cur)
            cur = []

        while self.i < self.n:
            c = self.s[self.i]
            if c in _SPACE:
                self.i += 1
            elif c == "\\" and self.peek(1) == "\n":
                self.i += 2
            elif c == "\n":
                flush()
                self.i += 1
                if self.heredocs:
                    self.eat_heredocs()
                cmd_pos = not pattern
            elif c == "#":
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c == ";":
                flush()
                if self.peek(1) == ";":
                    self.i += 2
                    pattern = cases > 0
                    cmd_pos = False
                else:
                    self.i += 1
                    cmd_pos = not pattern
            elif c == "&" and self.peek(1) == ">":
                self.i += 2
                self.read_word()
            elif c == "&" or c == "|":
                flush()
                self.i += 2 if self.peek(1) == c else 1
                cmd_pos = not (pattern and c == "|")
            elif c == "(":
                self.i += 1
                if pattern:
                    pass
                elif self.peek() == "(" and cmd_pos:            # (( arith ))
                    self.i += 1
                    depth = 2
                    while self.i < self.n and depth:
                        depth += {"(": 1, ")": -1}.get(self.s[self.i], 0)
                        self.i += 1
                else:
                    flush()
                    paren += 1
                    cmd_pos = True
            elif c == ")":
                self.i += 1
                if pattern:
                    pattern = False
                    cmd_pos = True
                elif paren:
                    flush()
                    paren -= 1
                    cmd_pos = False
                elif term == ")":
                    flush()
                    return
            elif c == "`":
                if term == "`":
                    flush()
                    self.i += 1
                    return
                self.read_word()
            elif c in "<>" or (c.isdigit() and self.redirect_ahead()):
                self.redirect()
            else:
                self.word_line = self.line()
                start = self.i
                w, expanded = self.read_word()
                if self.i == start:        # a lone char we do not model: step over it
                    self.i += 1
                    continue
                if pattern:
                    if w == "esac" and not expanded:
                        cases -= 1
                        pattern = False
                        cmd_pos = False
                    continue
                if cmd_pos is None:
                    continue
                if not cmd_pos:
                    cur.append((w, expanded, self.word_line))
                    continue
                # ---- a word in command position ----
                if _ASSIGN.match(w):
                    continue
                if expanded:
                    self.scan.dynamic.append((self.word_line, self.owner, self.s[start:self.i]))
                    cur.append((w, True, self.word_line))
                    cmd_pos = False
                elif w == "{":
                    if self.pending_fn:
                        self.fn_stack.append((self.pending_fn, self.owner, self.depth))
                        self.owner, self.pending_fn = self.pending_fn, ""
                    self.depth += 1
                elif w == "}":
                    self.depth -= 1
                    if self.fn_stack and self.fn_stack[-1][2] == self.depth:
                        _, self.owner, _ = self.fn_stack.pop()
                    cmd_pos = False
                elif w in _RESERVED:
                    pass
                elif w in _CLOSERS:
                    cmd_pos = False
                elif w == "esac":
                    cases = max(cases - 1, 0)
                    cmd_pos = False
                elif w in ("for", "select"):
                    self.skip_ws()
                    self.read_word()
                    cmd_pos = None
                elif w == "case":
                    self.skip_ws()
                    self.read_word()               # the subject
                    self.skip_ws()
                    self.read_word()               # `in`
                    cases += 1
                    pattern = True
                    cmd_pos = False
                elif w == "[[":
                    self.dbl_bracket()
                    cmd_pos = False
                elif w == "function":
                    self.skip_ws()
                    name, _ = self.read_word()
                    self.define(name)
                elif self.is_definition(w):
                    self.define(w)
                else:
                    cur.append((w, False, self.word_line))
                    cmd_pos = False
        flush()

    def is_definition(self, w: str) -> bool:
        if not _NAME.match(w):
            return False
        j = self.i
        while j < self.n and self.s[j] in _SPACE:
            j += 1
        if j < self.n and self.s[j] == "(":
            k = j + 1
            while k < self.n and self.s[k] in _SPACE:
                k += 1
            if k < self.n and self.s[k] == ")":
                self.i = k + 1
                return True
        return False

    def define(self, name: str) -> None:
        self.scan.defs.setdefault(name, self.word_line)
        self.pending_fn = name

    def redirect_ahead(self) -> bool:
        j = self.i
        while j < self.n and self.s[j].isdigit():
            j += 1
        return j < self.n and self.s[j] in "<>"

    def redirect(self) -> None:
        while self.peek().isdigit():
            self.i += 1
        op_start = self.i
        while self.peek() in ("<", ">"):
            self.i += 1
        op = self.s[op_start:self.i]
        if op == "<<" and self.peek() == "-":
            op = "<<-"
            self.i += 1
        if self.peek() in ("&", "|"):
            self.i += 1
        while self.peek() in _SPACE:
            self.i += 1
        if op.startswith("<<") and not op.startswith("<<<"):
            start = self.i
            self.read_word()
            raw = self.s[start:self.i]
            quoted = any(q in raw for q in "'\"\\")
            self.heredocs.append((re.sub(r"['\"\\]", "", raw), op == "<<-", quoted))
            return
        self.read_word()

    def dbl_bracket(self) -> None:
        while self.i < self.n:
            self.skip_ws()
            if self.s.startswith("]]", self.i):
                self.i += 2
                return
            if self.i < self.n and self.s[self.i] in "()&|<>!":
                self.i += 1
                continue
            before = self.i
            self.read_word()
            if self.i == before:
                self.i += 1

    # -- finishing a simple command -------------------------------------
    def finish(self, words: list[tuple[str, bool, int]]) -> None:
        name, dyn, line = words[0]
        if dyn:
            return
        args = [(w, e) for w, e, _ in words[1:]]
        if name == "command" and args and args[0][0] in ("-v", "-V"):
            self.guard(args[1:])           # a test, not a call
            return
        self.record(name, line)
        if name == "type":
            self.guard(args)
        elif name.rsplit("/", 1)[-1] in _WRAPPERS:
            self.unwrap(name.rsplit("/", 1)[-1], args, line)
        elif name in self.runners:
            k = self.runners[name]
            if len(args) > k:
                w, e = args[k]
                if e:
                    self.scan.dynamic.append((line, self.owner, w))
                else:
                    self.scan.calls.append(Call(w, line, self.owner, name))
        elif name == "trap" and args and not args[0][1] and args[0][0] and not args[0][0].startswith("-"):
            self.sub(args[0][0], line, "trap")
        elif name == "eval" and args and not any(e for _, e in args):
            self.sub(" ".join(w for w, _ in args), line, "eval")
        elif name in _SHELLS and any(w == "-c" for w, _ in args):
            k = [w for w, _ in args].index("-c")
            if k + 1 < len(args) and not args[k + 1][1]:
                self.sub(args[k + 1][0], line, f"{name} -c")

    def guard(self, args: list[tuple[str, bool]]) -> None:
        for w, e in args:
            if not e and not w.startswith("-"):
                self.scan.guards.add((self.owner, w))

    def unwrap(self, wrapper: str, args: list[tuple[str, bool]], line: int) -> None:
        takes = _WRAPPERS[wrapper]
        k = 0
        while k < len(args):
            w, _ = args[k]
            if w in takes:
                k += 2
            elif w.startswith("-") or re.fullmatch(r"\d+[smhd]?", w) or _ASSIGN.match(w):
                k += 1
            else:
                break
        if k < len(args):
            w, e = args[k]
            if e:
                self.scan.dynamic.append((line, self.owner, w))
            else:
                self.scan.calls.append(Call(w, line, self.owner, wrapper))
                if w.rsplit("/", 1)[-1] in _WRAPPERS:        # exec nice -n 10 cmd
                    self.unwrap(w.rsplit("/", 1)[-1], args[k + 1:], line)

    def record(self, name: str, line: int) -> None:
        self.scan.calls.append(Call(name, line, self.owner))

    def sub(self, script: str, line: int, via: str) -> None:
        inner = _Lexer(script, self.scan, base_line=line, owner=self.owner, runners=self.runners)
        before = len(self.scan.calls)
        inner.commands(None)
        for k in range(before, len(self.scan.calls)):
            c = self.scan.calls[k]
            self.scan.calls[k] = Call(c.name, c.line, c.owner, via)


def scan_sh(text: str, runners: dict[str, int] | None = None) -> Scan:
    """Scan a whole script into calls, definitions and existence guards.

    ``runners`` names repo helpers that run one of their own arguments as a
    command, with the index of that argument: ``{"capped_run": 2}`` makes
    ``capped_run 5 out cmd args`` a call to ``cmd``."""
    scan = Scan()
    _Lexer(text, scan, runners=runners).commands(None)
    return scan
