#!/usr/bin/env python3
"""
lint_echo_pipe_grep_q.py
========================
A ratchet lint that reports ``echo`` / ``printf`` output piped into a quiet
``grep`` (``grep -q``, ``grep --quiet``, ``grep --silent``) in non-test bash.

WHY THE SHAPE IS A BUG
----------------------
``echo "$text" | grep -q pattern`` races under ``set -o pipefail``.
``grep -q`` exits at its first match without reading the rest of its
input. If ``echo`` is still writing, it is killed by SIGPIPE and exits 141,
and ``pipefail`` makes the whole pipeline non-zero: text that matches is
reported as "no match". The miss depends on timing and input size, so it
surfaces as an intermittent failure far from its cause.

Race-free forms test the same text without a pipe:

    grep -qE "$re" <<< "$text"        # here-string: same pattern, same flags
    [[ $text =~ $re ]]                # bash regex match (ERE, no grep flags)

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). The lint narrates its
findings, writes them to ``--findings-file`` for the gated runner's
aggregate, and ALWAYS exits 0. It never fails a certification run or a
release. The pre-merge check run
(``team/pgai_agent_kanban/testing/pre_merge.py``) runs it as a ratchet lint
and treats any finding as a failed check, as it does for its sibling
ratchet lints.

WHAT IS SCANNED
---------------
Every file under the scan paths (default: ``team`` and ``install.sh``,
relative to the repository root) that is bash: a ``*.sh`` or ``*.bash``
file, or a file with no extension whose first line is a ``bash`` / ``sh``
shebang. Test files are skipped: any file with a directory named ``tests``
between the repository root and the file, and any file named ``test-*.sh``
or ``test_*.sh``. Hidden directories and ``__pycache__`` are not entered.

WHAT IS REPORTED
----------------
A pipeline in which a stage whose command is ``echo`` or ``printf`` comes
before a stage whose command is ``grep`` (or ``egrep`` / ``fgrep``) run
quietly. The command word may carry a path (``/bin/echo``) or follow
``command`` / ``builtin`` / ``env``, variable assignments, redirections and
the keywords ``if``, ``while``, ``until``, ``!`` and friends. Quiet means a
short-option cluster carrying ``q`` (``-q``, ``-qE``, ``-Eq``, ``-iqx``;
letters after an option that takes an argument, as in ``-eq``, are that
argument) or ``--quiet`` / ``--silent``, anywhere before ``--``. Stages in
between do not matter: ``echo "$x" | tr a b | grep -q y`` is reported, since
every stage feeding ``grep -q`` can be cut off.

The scanner reads shell syntax rather than matching lines: quotes, ``$(...)``
and backtick substitutions (including inside double quotes), ``${...}``
expansions, comments, backslash-newline continuations, a pipe that ends a
line and continues on the next, and heredoc bodies (skipped) are all
understood. So a pipe inside a quoted string, an ``||``, a comment, or a
``[[ $x =~ a|b ]]`` alternation is never mistaken for a pipeline. A
subshell or brace group used as a pipeline stage (``echo x | (grep -q y)``)
is not followed.

THE ALLOWLIST AND THE RATCHET RULE
----------------------------------
``team/config/echo-pipe-grep-q-allowlist.txt`` holds one line per file:

    <path>\\t<sites>

``<path>`` is relative to the repository root. Fields may be separated by
any run of whitespace; blank lines and lines starting with ``#`` are
ignored. Reading rules:

  * An unlisted file must have 0 sites.            -> one ``unlisted`` finding per site
  * A listed file may not exceed its count.        -> one ``above`` finding per site
  * A listed file below its count must have its
    entry tightened in the same change.            -> finding ``below``
  * An entry naming a file that is not scanned.    -> finding ``below``
  * An unparseable allowlist line.                 -> finding ``allowlist-malformed``
  * No allowlist file at all.                      -> finding ``allowlist-missing``

The ratchet only turns one way: entries are lowered or removed, never
raised or added. ``--emit-allowlist`` prints the measured counts in
allowlist format, for writing a tightened entry.

OUTPUT
------
One narration line per finding, then a totals line:

    lint_echo_pipe_grep_q: totals: <N> file(s) scanned, <S> site(s); <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (one object per
finding, keys ``lint``, ``severity``, ``kind``, ``file``, ``line``,
``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s ``gated_runner_merge_findings_file``
merges. The file is always written, as ``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_echo_pipe_grep_q.py [OPTIONS]

  --root PATH            Repository root (default: two levels above this
                         script's directory).
  --scan PATH            A file or directory to scan, relative to --root or
                         absolute; repeatable (default: team and install.sh).
  --allowlist PATH       Allowlist file, relative to --root or absolute
                         (default: team/config/echo-pipe-grep-q-allowlist.txt).
  --findings-file PATH   Write findings as a JSON array to PATH.
  --emit-allowlist       Print the measured per-file counts in allowlist
                         format to stdout and exit 0 (no findings check).
  --verbose, -v          Print every site found, allowlisted or not.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# team/ import path -- needed for the dual-channel error-contract emitter.
_TEAM_DIR = Path(__file__).resolve().parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

LINT_NAME = "echo_pipe_grep_q"
DEFAULT_SCAN_PATHS = ("team", "install.sh")
DEFAULT_ALLOWLIST = "team/config/echo-pipe-grep-q-allowlist.txt"
TEST_DIR_NAMES = frozenset({"tests"})
SKIPPED_DIR_NAMES = frozenset({"__pycache__", "node_modules"})
BASH_SUFFIXES = frozenset({".sh", ".bash"})
RUN_COMMAND = "python3 team/scripts/lint_echo_pipe_grep_q.py"
REMEDY = (
    "test the text without a pipe -- grep -q PATTERN <<< \"$text\" (same pattern and "
    f"flags) or [[ $text =~ $re ]] -- then re-run: {RUN_COMMAND}"
)

#: A run of characters with no shell meaning, consumed in one step.
_PLAIN_RUN_RE = re.compile(r"[^\s\\'\"$`|&;()<>#]+")
#: Inside double quotes: a run with no backslash, quote, $ or backtick.
_DQ_RUN_RE = re.compile(r'[^\\"$`]+')
_BLANK_RUN_RE = re.compile(r"[ \t\r]+")
_PRODUCER_HINT_RE = re.compile(r"\b(?:echo|printf)\b")
_QUIET_GREP_HINT_RE = re.compile(r"grep\b(?:[^\n]|\\\n)*?(?:\s-[A-Za-z]*q|--quiet|--silent)")
_TEST_FILE_RE = re.compile(r"^test[-_].*\.sh$")
_SHEBANG_RE = re.compile(r"^#!\s*\S*/(?:env\s+)?(?:ba)?sh\b")
_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]*\])?\+?=")
_REDIRECT_RE = re.compile(r"^(?:\d+|&)?(?:>>?|<|>&|<&|&>>?|>\|)")
_BARE_REDIRECT_RE = re.compile(r"^(?:\d+|&)?(?:>>?|<|>&|<&|&>>?|>\|)$")

#: Words skipped before a stage's command word.
_PREFIX_KEYWORDS = frozenset({
    "if", "then", "elif", "else", "do", "while", "until", "!", "time",
    "{", "}", "fi", "done", "esac", "command", "builtin", "exec", "nohup",
})
_PRODUCERS = frozenset({"echo", "printf"})
_GREPS = frozenset({"grep", "egrep", "fgrep"})
#: grep short options that take an argument: the rest of their cluster, or
#: the next word, is that argument.
_GREP_ARG_OPTIONS = frozenset("efmABCdD")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Token(NamedTuple):
    """One shell token: a word (``kind == "word"``) or an operator."""

    kind: str  # "word" or "op"
    text: str
    line: int


class Site(NamedTuple):
    """One echo/printf-into-quiet-grep pipeline."""

    file: str
    line: int
    producer: str
    text: str


class Finding(NamedTuple):
    """One lint finding."""

    kind: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """The finding in the gated runners' findings-file shape: this
        tuple's fields plus the lint's name and the quality severity."""
        return {"lint": LINT_NAME, "severity": "quality", **self._asdict()}


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


class _Tokenizer:
    """A small bash tokenizer: enough syntax to find pipelines reliably.

    Words come out with their quotes removed. Operators are ``|``
    (``|&`` is reported as ``|``), ``||``, ``&&``, ``;``, ``&``, ``(``,
    ``)`` and ``NL`` (an unquoted newline that ends a command). A command
    or process substitution is emitted as ``(`` ... ``)`` around its own
    tokens, so the pipelines inside it are seen as pipelines of their own.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.pos = 0
        self.line = 1
        self.tokens: list[Token] = []
        self._word: list[str] = []
        self._word_line = 1
        self._word_started = False
        self._heredocs: list[tuple[str, bool]] = []

    # -- helpers ---------------------------------------------------------

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.text[i] if i < self.n else ""

    def _advance(self, count: int = 1) -> str:
        end = self.pos + count
        chunk = self.text[self.pos:end]
        if "\n" in chunk:
            self.line += chunk.count("\n")
        self.pos = end
        return chunk

    def _start_word(self) -> None:
        if not self._word_started:
            self._word_started = True
            self._word_line = self.line

    def _flush(self) -> None:
        if self._word_started:
            self.tokens.append(Token("word", "".join(self._word), self._word_line))
        self._word = []
        self._word_started = False

    def _op(self, text: str) -> None:
        self._flush()
        self.tokens.append(Token("op", text, self.line))

    # -- quoting and expansions -----------------------------------------

    def _single_quoted(self) -> None:
        self._advance()  # opening '
        end = self.text.find("'", self.pos)
        end = self.n if end == -1 else end
        self._word.append(self.text[self.pos:end])
        self._advance(end - self.pos)
        if self.pos < self.n:
            self._advance()  # closing '

    def _ansi_c_quoted(self) -> None:
        self._advance(2)  # $'
        while self.pos < self.n:
            ch = self._peek()
            if ch == "\\":
                self._word.append(self._advance(2))
                continue
            if ch == "'":
                self._advance()
                return
            self._word.append(self._advance())

    def _double_quoted(self) -> None:
        self._advance()  # opening "
        while self.pos < self.n:
            ch = self._peek()
            if ch == "\\":
                self._advance()
                if self.pos < self.n:
                    self._word.append(self._advance())
                continue
            if ch == '"':
                self._advance()
                return
            run = _DQ_RUN_RE.match(self.text, self.pos)
            if run:
                self._word.append(self._advance(run.end() - self.pos))
                continue
            if ch == "$" and self._peek(1) == "(" and self._peek(2) != "(":
                self._substitution(2, ")")
                continue
            if ch == "`":
                self._substitution(1, "`")
                continue
            if ch == "$" and self._peek(1) == "{":
                self._word.append(self._balanced("{", "}"))
                continue
            self._word.append(self._advance())

    def _balanced(self, opener: str, closer: str, prefix: int = 1) -> str:
        """Consume *prefix* characters (the ``$``), *opener*, and everything
        to the matching *closer* as plain word text (``${...}``,
        ``$((...))``, a bare ``((...))``), honouring quotes and backslashes."""
        start = self.pos
        self._advance(prefix + len(opener))
        depth = 1
        while self.pos < self.n and depth:
            ch = self._peek()
            if ch == "\\":
                self._advance(2)
            elif ch == "'":
                end = self.text.find("'", self.pos + 1)
                self._advance((self.n if end == -1 else end + 1) - self.pos)
            elif ch == '"':
                self._advance()
                while self.pos < self.n and self._peek() != '"':
                    self._advance(2 if self._peek() == "\\" else 1)
                self._advance()
            elif self.text.startswith(opener, self.pos):
                depth += 1
                self._advance(len(opener))
            elif self.text.startswith(closer, self.pos):
                depth -= 1
                self._advance(len(closer))
            else:
                self._advance()
        return self.text[start:self.pos]

    def _substitution(self, skip: int, closer: str) -> None:
        """A command or process substitution: its tokens go between ``(``
        and ``)`` operators, and the enclosing word resumes after it."""
        resume_word = self._word
        resume_started = self._word_started
        resume_line = self._word_line
        self._word, self._word_started = [], False
        self.tokens.append(Token("op", "(", self.line))
        self._advance(skip)
        self._scan(closer)
        self._flush()
        self.tokens.append(Token("op", ")", self.line))
        self._word, self._word_started, self._word_line = resume_word, resume_started, resume_line
        # The substitution's value is part of the enclosing word.
        self._start_word()
        self._word.append("$(...)")

    # -- heredocs ----------------------------------------------------------

    def _heredoc_opener(self) -> None:
        dash = self._peek(2) == "-"
        self._advance(3 if dash else 2)
        while self._peek() in (" ", "\t"):
            self._advance()
        delim: list[str] = []
        while self.pos < self.n and self._peek() not in " \t\n;&|<>()":
            ch = self._peek()
            if ch in "'\"":
                end = self.text.find(ch, self.pos + 1)
                end = self.n if end == -1 else end
                delim.append(self.text[self.pos + 1:end])
                self._advance(end + 1 - self.pos)
            elif ch == "\\":
                self._advance()
                delim.append(self._advance())
            else:
                delim.append(self._advance())
        if delim:
            self._heredocs.append(("".join(delim), dash))

    def _skip_heredoc_bodies(self) -> None:
        for delim, dash in self._heredocs:
            while self.pos < self.n:
                end = self.text.find("\n", self.pos)
                end = self.n if end == -1 else end
                body_line = self.text[self.pos:end]
                if dash:
                    body_line = body_line.lstrip("\t")
                self._advance(min(end + 1, self.n) - self.pos)
                if body_line.rstrip("\r") == delim:
                    break
        self._heredocs = []

    # -- main scan -------------------------------------------------------

    def _last_op(self) -> str | None:
        if self._word_started or not self.tokens:
            return None
        last = self.tokens[-1]
        return last.text if last.kind == "op" else None

    def _scan(self, closer: str | None) -> None:
        depth = 0
        text, n = self.text, self.n
        while self.pos < n:
            ch = text[self.pos]
            nxt = text[self.pos + 1] if self.pos + 1 < n else ""
            if closer == "`" and ch == "`":
                self._advance()
                return
            if ch == "\\" and nxt == "\n":
                self._flush()
                self._advance(2)
            elif ch == "\\":
                self._start_word()
                self._advance()
                if self.pos < self.n:
                    self._word.append(self._advance())
            elif ch in " \t\r":
                self._flush()
                run = _BLANK_RUN_RE.match(self.text, self.pos)
                self.pos = run.end()
            elif ch == "\n":
                # A newline after |, ||, && continues the command.
                if self._last_op() not in ("|", "||", "&&"):
                    self._op("NL")
                else:
                    self._flush()
                self._advance()
                if self._heredocs:
                    self._skip_heredoc_bodies()
            elif ch == "#" and not self._word_started:
                end = self.text.find("\n", self.pos)
                self._advance((self.n if end == -1 else end) - self.pos)
            elif ch == "'":
                self._start_word()
                self._single_quoted()
            elif ch == '"':
                self._start_word()
                self._double_quoted()
            elif ch == "$" and nxt == "'":
                self._start_word()
                self._ansi_c_quoted()
            elif ch == "$" and nxt == "(" and self._peek(2) == "(":
                self._start_word()
                self._word.append(self._balanced("((", "))"))
            elif ch == "$" and nxt == "(":
                self._start_word()
                self._substitution(2, ")")
            elif ch == "$" and nxt == "{":
                self._start_word()
                self._word.append(self._balanced("{", "}"))
            elif ch == "(" and nxt == "(" and not self._word_started:
                # An arithmetic command: its << and | are operators on
                # numbers, never a heredoc or a pipe.
                self._start_word()
                self._word.append(self._balanced("((", "))", prefix=0))
            elif ch == "`":
                self._start_word()
                self._substitution(1, "`")
            elif ch in "<>" and nxt == "(":
                self._flush()
                self._substitution(2, ")")
                self._flush()
            elif ch == "<" and nxt == "<" and self._peek(2) == "<":
                # A here-string operator: one word, never a heredoc opener.
                self._flush()
                self.tokens.append(Token("word", self._advance(3), self.line))
            elif ch == "<" and nxt == "<":
                self._flush()
                self._heredoc_opener()
            elif ch == "|":
                if nxt == "|":
                    self._op("||")
                    self._advance(2)
                else:
                    self._op("|")
                    self._advance(2 if nxt == "&" else 1)
            elif ch == "&":
                if nxt == "&":
                    self._op("&&")
                    self._advance(2)
                elif nxt == ">" or (self._word and self._word[-1].endswith((">", "<"))):
                    self._start_word()
                    self._word.append(self._advance())
                else:
                    self._op("&")
                    self._advance()
            elif ch == ";":
                self._op(";")
                self._advance(2 if nxt in ";&" else 1)
            elif ch == "(":
                depth += 1
                self._op("(")
                self._advance()
            elif ch == ")":
                if closer == ")" and depth == 0:
                    self._advance()
                    return
                depth -= 1
                self._op(")")
                self._advance()
            else:
                self._start_word()
                run = _PLAIN_RUN_RE.match(self.text, self.pos)
                self._word.append(self._advance(run.end() - self.pos if run else 1))

    def run(self) -> list[Token]:
        self._scan(None)
        self._flush()
        return self.tokens


def tokenize(text: str) -> list[Token]:
    """Tokenize bash *text* into words and operators (see :class:`_Tokenizer`)."""
    return _Tokenizer(text).run()


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------


def _command_word(words: list[Token]) -> tuple[Token | None, list[Token]]:
    """The word naming the command a stage runs and the words after it;
    ``(None, [])`` when the stage runs no command word."""
    i = 0
    while i < len(words):
        text = words[i].text
        if text in _PREFIX_KEYWORDS or _ASSIGNMENT_RE.match(text):
            i += 1
        elif text == "env":
            i += 1
            while i < len(words) and (words[i].text.startswith("-") or _ASSIGNMENT_RE.match(words[i].text)):
                i += 1
        elif _REDIRECT_RE.match(text):
            i += 2 if _BARE_REDIRECT_RE.match(text) else 1
        else:
            return words[i], words[i + 1:]
    return None, []


def grep_is_quiet(args: list[str]) -> bool:
    """True when a grep with argument words *args* runs quietly
    (``-q`` in a short-option cluster, ``--quiet`` or ``--silent``)."""
    i = 0
    while i < len(args):
        arg = args[i]
        i += 1
        if arg == "--":
            return False
        if arg in ("--quiet", "--silent"):
            return True
        if arg.startswith("--") or not arg.startswith("-") or arg == "-":
            continue
        for k, letter in enumerate(arg[1:], start=1):
            if letter == "q":
                return True
            if letter in _GREP_ARG_OPTIONS:
                if k == len(arg) - 1:
                    i += 1  # the option's argument is the next word
                break
    return False


def find_sites_in_tokens(tokens: list[Token]) -> list[tuple[int, str]]:
    """Every pipeline in *tokens* where an echo/printf stage comes before a
    quiet-grep stage: ``(line of the producer, producer)`` per pipeline."""
    sites: list[tuple[int, str]] = []
    # One open pipeline per nesting level: a list of stages, each a list of
    # word tokens.
    stack: list[list[list[Token]]] = [[[]]]

    def close(pipeline: list[list[Token]]) -> None:
        producer: tuple[int, str] | None = None
        for stage in pipeline:
            word, rest = _command_word(stage)
            command = word.text.rsplit("/", 1)[-1] if word else ""
            if producer is None and command in _PRODUCERS:
                producer = (word.line, command)
            elif producer is not None and command in _GREPS and grep_is_quiet([w.text for w in rest]):
                sites.append(producer)
                return

    for tok in tokens:
        if tok.kind == "word":
            stack[-1][-1].append(tok)
        elif tok.text == "|":
            stack[-1].append([])
        elif tok.text == "(":
            stack.append([[]])
        elif tok.text == ")" and len(stack) > 1:
            close(stack.pop())
        else:  # ||, &&, ;, &, NL, or a ) with no ( open (a case pattern)
            close(stack[-1])
            stack[-1] = [[]]
    while stack:
        close(stack.pop())
    return sorted(set(sites))


def scan_text(text: str, rel_path: str = "<text>") -> list[Site]:
    """Every echo/printf-into-quiet-grep site in bash *text*."""
    lines = text.splitlines()
    sites = []
    for line, producer in find_sites_in_tokens(tokenize(text)):
        source = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        sites.append(Site(rel_path, line, producer, source))
    return sites


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


def _rel(path: Path, root: Path) -> str:
    """*path* relative to *root* when it is inside it, else as given.

    Lexical first (cheap, and what every walked path needs); resolved only
    when a path given on the command line reaches the root another way.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        pass
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_bash(path: Path) -> bool:
    if path.suffix in BASH_SUFFIXES:
        return True
    if path.suffix:
        return False
    try:
        with path.open("rb") as fh:
            first = fh.readline(200).decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(_SHEBANG_RE.match(first))


def _is_test_path(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return any(part in TEST_DIR_NAMES for part in parts[:-1]) or bool(_TEST_FILE_RE.match(parts[-1]))


def iter_scan_files(root: Path, scan_paths: list[Path]) -> list[Path]:
    """Every non-test bash file under *scan_paths* (files or directories).

    A directory named ``tests``, a hidden directory and ``__pycache__`` are
    never entered, so the walk stays cheap on a large test tree.
    """
    found: set[Path] = set()
    for scan in scan_paths:
        if scan.is_file():
            candidates = [scan]
        elif scan.is_dir():
            candidates = []
            for dirpath, dirnames, filenames in os.walk(scan):
                dirnames[:] = [
                    d for d in dirnames
                    if not d.startswith(".") and d not in SKIPPED_DIR_NAMES and d not in TEST_DIR_NAMES
                ]
                candidates.extend(Path(dirpath, name) for name in filenames)
        else:
            continue
        for path in candidates:
            if _is_test_path(_rel(path, root)) or not path.is_file() or not _is_bash(path):
                continue
            found.add(path)
    return sorted(found)


def measure(root: Path, scan_paths: list[Path]) -> tuple[list[str], dict[str, list[Site]]]:
    """Scan every file: ``(paths scanned, {path: sites})``, paths relative
    to *root*, the mapping holding only files that carry a site."""
    scanned: list[str] = []
    by_file: dict[str, list[Site]] = {}
    for path in iter_scan_files(root, scan_paths):
        rel = _rel(path, root)
        scanned.append(rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        # Cheap pre-check: a file with no echo/printf word, or no grep
        # followed by a quiet option, cannot carry a site, so it is not
        # tokenized. Deliberately looser than the scan itself.
        if not _PRODUCER_HINT_RE.search(text) or not _QUIET_GREP_HINT_RE.search(text):
            continue
        sites = scan_text(text, rel)
        if sites:
            by_file[rel] = sites
    return scanned, by_file


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def load_allowlist(path: Path, allowlist_rel: str) -> tuple[dict[str, tuple[int, int]], list[Finding]]:
    """Read the allowlist into ``{path: (sites, lineno)}``.

    Liberal reader: any whitespace run separates fields; blank and ``#``
    lines are skipped. Unparseable lines become findings and are ignored.
    """
    entries: dict[str, tuple[int, int]] = {}
    findings: list[Finding] = []
    if not path.is_file():
        findings.append(Finding(
            "allowlist-missing", allowlist_rel, 0,
            f"allowlist {allowlist_rel} not found; every site is reported as unlisted",
            f"restore {allowlist_rel} from the source branch (git checkout HEAD -- {allowlist_rel}), "
            f"then re-run: {RUN_COMMAND}",
        ))
        return entries, findings
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2 or not fields[1].isdigit():
            findings.append(Finding(
                "allowlist-malformed", allowlist_rel, lineno,
                f"allowlist line is not '<path> <sites>' and was ignored: {raw!r}",
                f"{RUN_COMMAND} --emit-allowlist",
            ))
            continue
        entries[fields[0]] = (int(fields[1]), lineno)
    return entries, findings


def format_allowlist(by_file: dict[str, list[Site]]) -> str:
    """Render measured counts in allowlist format (files with sites only)."""
    rows = [f"{path}\t{len(sites)}" for path, sites in sorted(by_file.items())]
    return "\n".join(rows) + ("\n" if rows else "")


def compare(
    by_file: dict[str, list[Site]], scanned: set[str],
    allowlist: dict[str, tuple[int, int]], allowlist_rel: str,
) -> list[Finding]:
    """Compare measured sites with the allowlist; return findings."""
    findings: list[Finding] = []
    for path, sites in sorted(by_file.items()):
        entry = allowlist.get(path)
        allowed = entry[0] if entry else 0
        if len(sites) <= allowed:
            continue
        kind = "above" if entry else "unlisted"
        why = (
            f"{len(sites)} site(s), above its allowlist entry of {allowed}"
            if entry else "not in the allowlist"
        )
        for site in sites:
            findings.append(Finding(
                kind, path, site.line,
                f"{path}:{site.line} pipes {site.producer} into a quiet grep, which misses a "
                f"match under pipefail when grep exits first ({why}): {site.text}",
                REMEDY,
            ))
    for path, (allowed, lineno) in sorted(allowlist.items()):
        measured = len(by_file.get(path, []))
        if measured >= allowed:
            continue
        if path not in scanned:
            summary = (
                f"allowlist entry '{path}' ({allowed}, {allowlist_rel} line {lineno}) names a "
                "file that is not scanned: tighten the entry (remove it)"
            )
        else:
            action = f"set it to '{path}\t{measured}'" if measured else "remove it"
            summary = (
                f"{path} has {measured} site(s), below its allowlist entry of {allowed} "
                f"({allowlist_rel} line {lineno}): tighten the entry ({action})"
            )
        findings.append(Finding("below", path, 0, summary, f"{RUN_COMMAND} --emit-allowlist"))
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_echo_pipe_grep_q",
        description=(
            "Ratchet lint: report echo/printf output piped into a quiet grep "
            "(grep -q, --quiet, --silent) in non-test bash under team/ and install.sh. "
            "Under set -o pipefail that pipeline can report 'no match' for text that "
            "matches; test the text with a here-string or [[ =~ ]] instead. "
            "Quality class: always exits 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Race-free forms:\n"
            "  grep -qE \"$re\" <<< \"$text\"\n"
            "  [[ $text =~ $re ]]\n\n"
            "Allowlist format: one '<path>\\t<sites>' line per file, path relative\n"
            "to the repository root. Entries are only ever lowered or removed, never\n"
            "raised or added.\n\n"
            "Exit codes:\n  0  Always (findings are narrated, never fatal).\n"
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--scan", metavar="PATH", action="append", default=None,
                        help="File or directory to scan, relative to --root or absolute; repeatable "
                             f"(default: {' and '.join(DEFAULT_SCAN_PATHS)}).")
    parser.add_argument("--allowlist", metavar="PATH", default=DEFAULT_ALLOWLIST,
                        help=f"Allowlist file, relative to --root or absolute (default: {DEFAULT_ALLOWLIST}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    parser.add_argument("--emit-allowlist", action="store_true",
                        help="Print measured counts in allowlist format and exit 0.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every site found, allowlisted or not.")
    return parser


def _resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def _write_findings(path_str: str | None, findings: list[Finding]) -> None:
    if not path_str:
        return
    path = Path(path_str)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps([f.to_dict() for f in findings], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"lint_echo_pipe_grep_q: could not write findings file {path}: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    scan_paths = [_resolve(root, value) for value in (args.scan or DEFAULT_SCAN_PATHS)]
    allowlist_path = _resolve(root, args.allowlist)
    allowlist_rel = _rel(allowlist_path, root)

    scanned, by_file = measure(root, scan_paths)

    if args.emit_allowlist:
        sys.stdout.write(format_allowlist(by_file))
        return 0

    entries, findings = load_allowlist(allowlist_path, allowlist_rel)
    findings.extend(compare(by_file, set(scanned), entries, allowlist_rel))
    _write_findings(args.findings_file, findings)

    if args.verbose:
        for sites in by_file.values():
            for site in sites:
                print(f"  [echo-pipe-grep-q] {site.file}:{site.line}: {site.text}")
    for f in findings:
        print(f"lint_echo_pipe_grep_q: [{f.kind}] {f.summary}")
    total_sites = sum(len(sites) for sites in by_file.values())
    print(
        f"lint_echo_pipe_grep_q: totals: {len(scanned)} file(s) scanned, "
        f"{total_sites} site(s); {len(findings)} finding(s)"
    )

    if findings:
        message = (
            f"{len(findings)} echo/printf-into-quiet-grep finding(s) against {allowlist_rel}; "
            "see the lines printed above"
        )
        # CENSUS-ANCHOR: echo-pipe-grep-q-01
        # CONVERT: a quality-class finding with zero runtime-failure risk to
        # the run itself -- narrated and filed, never fatal; returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.echo-pipe-grep-q", message, REMEDY)
        except ImportError:
            print(f"error:   lint.echo-pipe-grep-q\nmessage: {message}\nremedy:  {REMEDY}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
