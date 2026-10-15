#!/usr/bin/env python3
"""
affected.py — choose the test files a change should run, by a simple
identifier search over the test tree, plus a search for tests that list a
changed file's directory.

The public entry point is :func:`affected_tests`::

    affected_tests(diff_names, tests_root, cap) -> list[pathlib.Path]

It never runs pytest; it only selects. A caller (the pre-merge check
runner) runs what it returns.

What ``diff_names`` holds
-------------------------
An iterable of plain name strings: the scripts, modules, functions and
command names a change touched, and the directories the changed files sit
in. :func:`names_from_diff` derives them from ``git diff <base>...<head>``
in a local repository (no fetch, pull or push):

* **Every changed path** contributes one name. A Python file contributes
  its module name (the file stem: ``team/pgai_agent_kanban/lib/semver.py``
  gives ``semver``), so ``from pgai_agent_kanban.lib import semver``,
  ``lib.semver`` and ``semver.py`` all cite it. Any other file contributes
  its full file name (``team/scripts/run-unit-tests.sh`` gives
  ``run-unit-tests.sh``), which is how a test invokes a script or command.
  Deleted files count, so tests still citing a removed script are found.
* **Every changed path's directory** contributes a directory name: its
  repository-relative path with a trailing slash
  (``team/scripts/dashboard/attention.sh`` gives
  ``team/scripts/dashboard/``). A file at the top of the repository or
  directly inside a top-level directory (``install.sh``,
  ``docs/OPERATIONS.md``) contributes none: its directory is the root of
  the tree or of a whole subtree, too broad to say anything about a test.
  A changed test file contributes no directory (below). Any name ending in
  ``/`` is a directory name, so ``--names team/scripts/dashboard/`` asks
  the same question by hand.
* **Changed functions.** For each changed ``.py`` file, every function or
  class whose body (decorators included) holds a changed line contributes
  its name, together with the names of the functions and classes enclosing
  it -- a changed method gives both the method and its class. For each
  changed ``.sh`` / ``.bash`` file, every shell function whose body holds a
  changed line contributes its name. A removal with nothing added in its
  place changes a function only when the removed lines sat inside it,
  indented deeper than its definition: deleting a whole function does not
  count against the function above it. A changed test file is the
  exception: see below.
* **Definitions on changed lines.** Added and removed lines are scanned for
  definitions: ``def`` / ``class`` and column-0 assignments in Python,
  function definitions and upper-case variable assignments in shell. A
  removed or renamed function is therefore still a name, so tests that call
  it by its old name are selected. A changed test file is the exception
  here too. Only code lines count. A Python ``def`` / ``class`` line counts
  only in definition shape -- the name followed by ``(``, ``:`` or ``[`` --
  so a prose line such as "class whose members ..." contributes nothing. A
  Python line that begins inside a string literal (the second and later
  lines of a docstring or other multi-line string, judged against the file
  on the side of the diff the line belongs to) contributes nothing, and
  neither does a comment line.

**A changed test file contributes its own stem and nothing else.** A file
that :func:`is_test_file` accepts (``test_*.py`` or ``*_test.py``, outside
fixture, artifact, cache and dot directories) gives its stem
(``test_semver``) like any other Python file, but neither its changed
functions nor the definitions on its changed lines: its private helpers,
module constants and fixture methods (``_TEAM_DIR``, ``_write``, a fixture
class's ``git``) are its own, and the same names in other test files are
other definitions, so they select nothing else. A test file whose stem or
file name is a changed name is selected on that alone -- an edited test
always reruns. Shared test helpers (``conftest.py``, helper modules,
anything under ``fixtures/``) are not test files and contribute their
definitions like production code, so a changed helper still selects the
tests that call it.

The selection rule
------------------
1. **Test files only.** A test file is a ``test_*.py`` or ``*_test.py``
   file under ``tests_root`` with no directory between it and
   ``tests_root`` named ``fixtures``, ``artifacts``, ``__pycache__`` or
   starting with a dot. ``conftest.py``, helper modules and fixture data --
   including ``test_*.py`` files kept as fixture data -- are never returned.
2. **Identifier search.** A test file references a name when the name
   occurs in its text with no word character (letter, digit, underscore) or
   hyphen immediately before or after it. ``semver`` matches
   ``lib.semver`` and ``semver.py`` but not ``semver_lt`` or
   ``pysemver``.
3. **Directory search.** A test that finds its subjects by listing a
   directory never names them, so a test file also references a changed
   directory when it lists that directory. Two things must hold, judged on
   the directory's last two components (``scripts`` and ``dashboard`` for
   ``team/scripts/dashboard/``), so a path anchored at the repository and
   one anchored at ``team/`` both count:

   * **It cites the directory itself.** A line holds the two components
     joined as a path -- ``scripts/dashboard``,
     ``"scripts" / "dashboard"``, ``"scripts", "dashboard"`` -- or the last
     one joined with ``/`` to a variable whose name holds the first
     (``_TEAM_DIR / "scripts"``, case and ``-``/``_`` ignored). For a
     directory of exactly two components, whose first is a top-level
     directory, a variable whose name holds ``root`` also counts
     (``ROOT / "scripts"``, ``team_root / "scripts"``). A citation
     continued by a further path component (``scripts/dashboard/kill.sh``,
     ``"dashboard" / "lib"``) names something inside the directory, not the
     directory, and does not count; one continued by a glob pattern
     (``scripts/dashboard/*.sh``) does.
   * **It lists that directory.** The citing line itself calls ``glob``,
     ``rglob``, ``iterdir``, ``listdir``, ``scandir`` or ``walk``, or it
     assigns the directory to a name (``_DASHBOARD_DIR = ...``) that the
     file lists elsewhere (``_DASHBOARD_DIR.glob("*.sh")``,
     ``os.listdir(_DASHBOARD_DIR)``). The assignment may span several
     lines: a citing line inside a parenthesized or backslash-continued
     expression assigns to the name its statement starts with
     (``_DIR = (`` then ``ROOT / "scripts" / "dashboard"`` then ``)``).

   **A recursive listing of an ancestor lists the directory too.** A test
   that walks a whole subtree covers every directory below it, so it also
   references a changed directory when it cites one of that directory's
   ancestors -- by the same two-component citation rule, so the ancestor
   itself has at least two components (``team/scripts/`` for
   ``team/scripts/dashboard/``; never ``team/``) -- and lists the ancestor
   recursively: ``rglob``, ``walk``, or a ``glob`` whose pattern holds
   ``**``, on the citing line or on a name the citing line assigns
   (``_SCRIPTS = _TEAM_DIR / "scripts"`` then ``_SCRIPTS.rglob("*.sh")``).
   A non-recursive ``glob``, ``iterdir``, ``listdir`` or ``scandir`` of an
   ancestor lists only the ancestor's own entries, so it is not selected
   by a change in a subdirectory.

   A test that only builds a path into the directory, or adds it to
   ``sys.path``, is not selected by it. A test that lists the directory or
   an ancestor of it counts once per changed directory it lists, so it
   ranks as a directory listing (rule 5), below every name match.
4. **Generic names are ignored.** Names that would select most of the tree
   carry no signal: Python dunders (``__init__``, ``__main__``), names
   shorter than three characters, directory names with fewer than two
   components (``docs/``, ``/``), and the fixed set in
   :data:`GENERIC_NAMES` (``main``, ``run``, ``parse_args``, ``README.md``
   and similar entry-point and boilerplate names). They are dropped before
   the search; :func:`split_generic_names` reports which were dropped. The
   set includes ubiquitous member and attribute names -- result flags such
   as ``failed`` and ``passed``, status callbacks such as ``say``, and the
   ``git`` / ``commit`` helpers of repository fixtures -- and common
   English words used as function names (``exists``, ``has``, ``line``,
   ``lines``, ``module``, ``function`` and the like), because a change to
   one of them in a single module would otherwise select every test that
   uses the word. The module's own name still selects its tests.
5. **Ranking.** Every referencing file is ranked by how many distinct
   changed names it cites (most first), then by how many changed
   directories it lists (most first), then by its total number of
   occurrences of those names and directories (most first), then by its
   path relative to ``tests_root`` (ascending), so ties order the same on
   every run. A file that cites a changed name therefore always ranks
   above one that only lists a changed directory, and the cap leaves out
   directory-only files first.
6. **Cap.** At most ``cap`` files are returned: the top of the ranking.
   The result is an :class:`AffectedTests` -- a ``list`` of paths that
   also carries ``matched_count`` and ``cap_applied``, so a caller can say
   "60 of 143 affected test files" without a second search.

Command line (for debugging a selection)
----------------------------------------
    python3 team/pgai_agent_kanban/testing/affected.py --base ai_main
    python3 team/pgai_agent_kanban/testing/affected.py --names semver run-unit-tests.sh

Prints one selected test file per line, relative to the parent of the
tests root (``tests/unit/test_x.py`` for ``team/tests``, the form the
runners' ``--files`` takes), and a one-line summary on stderr. ``--json``
prints the whole selection as one JSON object instead. Exit 0 on any
selection, empty included; exit 2 on an invocation error (an unknown base
ref, a directory that is not a git repository, a missing tests root).
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import io
import json
import pathlib
import re
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

# Run by file path (``python3 .../affected.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.error_contract import format_error_contract  # noqa: E402

# ---------------------------------------------------------------------------
# Selection rule constants
# ---------------------------------------------------------------------------

#: Names dropped before the search because they occur across most of the
#: test tree and would select nearly every file: entry-point and argument
#: parsing boilerplate, one-word verbs every module defines, ubiquitous
#: member and attribute names (result flags, status callbacks, repository
#: fixture helpers), common English words that production code uses as
#: function names, and file names every directory has. The common words are
#: those a word-boundary search finds in roughly one test file in six or
#: more (``line``, ``exists``, ``has``, ``lines``, ``module`` and
#: ``function`` each appear in 200+ files of the kanban's own test tree);
#: project vocabulary such as ``release`` is not generic and stays searched.
GENERIC_NAMES: frozenset[str] = frozenset({
    "main", "_main", "run", "_run", "cli", "_cli",
    "parse_args", "_parse_args", "build_parser", "_build_parser",
    "parser", "args", "argv", "help", "usage",
    "setup", "teardown", "init", "test", "tests", "conftest",
    "get", "set", "load", "save", "read", "write", "parse", "render",
    "log", "logger", "_log", "die", "fail", "error", "info", "warn",
    "path", "name", "data", "value", "result", "self", "cls",
    "util", "utils", "common", "helpers", "lib", "config",
    "failed", "passed", "skipped", "say", "git", "commit",
    "exists", "has", "holds", "line", "lines", "module", "function",
    "code", "field", "record", "step", "add", "describe", "format",
    "heading", "changed", "last",
    "README.md", "__init__.py", "conftest.py",
})

#: Names shorter than this are dropped as generic.
MIN_NAME_LENGTH = 3

#: File-name patterns that make a file under the tests root a test file.
TEST_FILE_PATTERNS: tuple[str, ...] = ("test_*.py", "*_test.py")

#: Directory names that hold fixture data, captured output or caches; no
#: file beneath one is a test file, whatever it is called.
NON_TEST_DIRS: frozenset[str] = frozenset({"fixtures", "artifacts", "__pycache__"})

_DUNDER_RE = re.compile(r"^__\w+__$")

# A name's boundary: not a word character and not a hyphen, so script names
# such as run-unit-tests.sh are not found inside longer hyphenated names.
_BOUNDARY = r"[\w-]"

#: A directory name has at least this many path components; fewer is the
#: tree root or a whole top-level subtree, dropped as generic.
MIN_DIRECTORY_COMPONENTS = 2

# Between two components of a cited directory: a slash inside one string
# (``scripts/dashboard``), or the end of one quoted string and the start of
# the next across pathlib's ``/`` or an ``os.path.join`` comma
# (``"scripts" / "dashboard"``, ``"scripts", "dashboard"``).
_DIR_JOIN = r"""(?:/|["']\s*[/,]\s*["'])"""

# After a cited directory: a further named component means the line names
# something inside the directory (``dashboard/kill.sh``,
# ``"dashboard" / "lib"``), not the directory itself. A component holding a
# ``*`` is a glob pattern over the directory and does not count as one.
_DIR_CONTINUED = r"""(?:/\s*[\w.-]|["']\s*[/,]\s*["'](?![^"'\n]*\*))"""

# A call that lists a directory: pathlib's glob / rglob / iterdir / walk,
# os.listdir / scandir / walk, glob.glob.
_LISTING_CALL = r"(?:r?glob|iterdir|listdir|scandir|walk)\s*\("
_LISTING_CALL_RE = re.compile(rf"(?<!\w){_LISTING_CALL}")

# A call that lists a directory recursively, reaching every subdirectory:
# pathlib's rglob / walk, os.walk, or a glob whose pattern on the same line
# descends with ``**`` (``d.glob("**/*.sh")``, ``glob.glob(..., "**", ...)``).
# The lookahead keeps the match ending at the ``(`` like _LISTING_CALL.
_RECURSIVE_CALL = r"(?:(?:rglob|walk)\s*\(|glob\s*\((?=[^\n]*\*\*))"
_RECURSIVE_CALL_RE = re.compile(rf"(?<!\w){_RECURSIVE_CALL}")

# A line that assigns to a plain name (``_DASHBOARD_DIR = ...``,
# ``SCRIPTS: Path = ...``), not a comparison.
_ASSIGNED_NAME_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]*)?=(?!=)")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class AffectedTests(list):
    """The selected test files, best-referencing first.

    A plain ``list`` of :class:`pathlib.Path` (each ``tests_root`` joined
    with the file's path below it), carrying two more facts about the
    selection:

    * ``matched_count`` -- how many test files referenced a changed name
      or listed a changed directory before the cap was applied.
    * ``cap`` -- the cap the selection was made with (``None`` for none).

    ``cap_applied`` is true exactly when the cap left referencing files out.
    """

    def __init__(
        self, paths: Iterable[pathlib.Path] = (), *, matched_count: int = 0,
        cap: int | None = None,
    ) -> None:
        super().__init__(paths)
        self.matched_count = matched_count
        self.cap = cap

    @property
    def cap_applied(self) -> bool:
        """True when more test files matched than were returned."""
        return self.matched_count > len(self)


@dataclass(frozen=True)
class _Reference:
    """How strongly one test file references the changed names and
    directories: ``names`` it cites, ``directories`` it lists, and the
    ``occurrences`` of both."""

    relative: pathlib.PurePosixPath
    names: frozenset[str]
    occurrences: int
    directories: frozenset[str] = frozenset()

    def rank_key(self) -> tuple[int, int, int, str]:
        return (
            -len(self.names), -len(self.directories), -self.occurrences,
            self.relative.as_posix(),
        )


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def split_generic_names(diff_names: Iterable[str]) -> tuple[list[str], list[str]]:
    """Split *diff_names* into ``(searched, dropped)``, each sorted and unique.

    ``dropped`` holds the names the selection rule ignores as generic
    (dunders, names under :data:`MIN_NAME_LENGTH` characters, members of
    :data:`GENERIC_NAMES`, directory names with fewer than
    :data:`MIN_DIRECTORY_COMPONENTS` components); ``searched`` holds the
    rest. Blank entries and surrounding whitespace are discarded, and a
    searched directory name is written in its canonical form
    (``team/scripts/dashboard/``: no ``./``, no doubled or leading slash).
    """
    if isinstance(diff_names, (str, bytes)):
        raise TypeError(
            "diff_names must be an iterable of name strings, not a single "
            f"string; wrap it in a list: [{diff_names!r}]"
        )
    searched: set[str] = set()
    dropped: set[str] = set()
    for raw in diff_names:
        name = str(raw).strip()
        if not name:
            continue
        if is_directory_name(name):
            components = directory_components(name)
            if len(components) < MIN_DIRECTORY_COMPONENTS:
                dropped.add(name)
            else:
                searched.add("/".join(components) + "/")
            continue
        if len(name) < MIN_NAME_LENGTH or name in GENERIC_NAMES or _DUNDER_RE.match(name):
            dropped.add(name)
        else:
            searched.add(name)
    return sorted(searched), sorted(dropped)


def is_test_file(relative: pathlib.PurePath) -> bool:
    """True when *relative* (a path below the tests root) is a test file.

    The file name must match :data:`TEST_FILE_PATTERNS`, and no directory
    on the way down may be one of :data:`NON_TEST_DIRS` or a dot directory.
    :func:`names_from_diff` applies the same test to repository-relative
    diff paths to tell a changed test file from other changed code.
    """
    if not any(fnmatch.fnmatchcase(relative.name, p) for p in TEST_FILE_PATTERNS):
        return False
    return not any(
        part in NON_TEST_DIRS or part.startswith(".") for part in relative.parts[:-1]
    )


def iter_test_files(tests_root: pathlib.Path) -> list[pathlib.PurePosixPath]:
    """Return every test file below *tests_root*, as sorted relative paths."""
    found = []
    for path in tests_root.rglob("*.py"):
        if not path.is_file():
            continue
        relative = pathlib.PurePosixPath(path.relative_to(tests_root).as_posix())
        if is_test_file(relative):
            found.append(relative)
    return sorted(found)


def is_directory_name(name: str) -> bool:
    """True when *name* is a directory name: it ends with ``/``."""
    return name.endswith("/")


def directory_components(name: str) -> list[str]:
    """The path components of directory name *name*, without empty and
    ``.`` components (``./team//scripts/`` gives ``["team", "scripts"]``)."""
    return [part for part in name.split("/") if part and part != "."]


def directory_name(path: str) -> str | None:
    """The directory name a changed *path* contributes: its parent
    directory with a trailing slash, or None when the parent has fewer than
    :data:`MIN_DIRECTORY_COMPONENTS` components (the tree root or a
    top-level directory)."""
    parent = directory_components(pathlib.PurePosixPath(path).parent.as_posix())
    if len(parent) < MIN_DIRECTORY_COMPONENTS:
        return None
    return "/".join(parent) + "/"


def ancestor_directories(name: str) -> list[str]:
    """The directories above directory name *name* that are not too broad
    to search (at least :data:`MIN_DIRECTORY_COMPONENTS` components),
    nearest first: ``team/scripts/dashboard/lib/`` gives
    ``["team/scripts/dashboard/", "team/scripts/"]``; ``team/scripts/``
    gives none."""
    components = directory_components(name)
    return [
        "/".join(components[:count]) + "/"
        for count in range(len(components) - 1, MIN_DIRECTORY_COMPONENTS - 1, -1)
    ]


def _name_pattern(names: Sequence[str]) -> re.Pattern[str]:
    """One alternation over *names*, longest first, with name boundaries."""
    alternation = "|".join(re.escape(n) for n in sorted(names, key=lambda n: (-len(n), n)))
    return re.compile(rf"(?<!{_BOUNDARY})(?:{alternation})(?!{_BOUNDARY})")


def _directory_citation(name: str) -> re.Pattern[str]:
    """A pattern for a line citing directory *name* itself, judged on its
    last two components (module docstring, selection rule 3)."""
    components = directory_components(name)
    parent, last = components[-2:]
    quoted = rf"(?<!{_BOUNDARY}){re.escape(parent)}{_DIR_JOIN}{re.escape(last)}"
    forms = [quoted]
    # The parent held in a variable: ``_TEAM_DIR / "scripts"``. Only when
    # the parent can appear inside an identifier at all. A parent that is
    # itself a top-level directory is also held by a variable named for a
    # root (``ROOT / "scripts"``), the usual name for the tree a test
    # anchors at.
    held = parent.replace("-", "_")
    if re.fullmatch(r"\w+", held):
        holder = re.escape(held)
        if len(components) == MIN_DIRECTORY_COMPONENTS:
            holder = rf"(?:{holder}|root)"
        forms.append(rf"(?<!\w)(?i:\w*{holder}\w*)\s*/\s*[\"']{re.escape(last)}")
    return re.compile(rf"(?:{'|'.join(forms)})(?!{_BOUNDARY})(?!{_DIR_CONTINUED})")


def _lists_name(text: str, holder: str, *, recursive: bool = False) -> bool:
    """True when *text* lists the directory held in variable *holder*:
    ``holder.glob(...)`` and kin, or *holder* inside a listing call's
    arguments (``os.listdir(holder)``, ``glob.glob(str(holder / "*"))``).
    With *recursive*, only a recursive listing counts
    (``holder.rglob(...)``, ``os.walk(holder)``, ``holder.glob("**/*")``)."""
    name = re.escape(holder)
    call = _RECURSIVE_CALL if recursive else _LISTING_CALL
    return bool(
        re.search(rf"(?<![\w.]){name}\s*\.\s*{call}", text)
        or re.search(rf"(?<!\w){call}[^)\n]*(?<![\w.]){name}(?!\w)", text)
    )


# Tokens that do not start a statement: layout, comments and the stream's
# own markers.
_LAYOUT_TOKENS = frozenset({
    tokenize.NL, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT,
    tokenize.ENCODING, tokenize.ENDMARKER,
})


def _statement_starts(text: str, line_count: int) -> list[int] | None:
    """For each of the *line_count* lines of Python *text* (0-based), the
    0-based line its statement starts on. A line inside a parenthesized,
    bracketed or backslash-continued expression, or inside a multi-line
    string, maps to the statement's first line; every other line maps to
    itself. None when *text* does not tokenize: each line then stands
    alone."""
    starts = list(range(line_count))
    first: int | None = None
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in _LAYOUT_TOKENS:
                continue
            if first is None:
                first = token.start[0] - 1
            if token.type == tokenize.NEWLINE:
                for row in range(first, min(token.end[0], line_count)):
                    starts[row] = first
                first = None
    except (tokenize.TokenError, SyntaxError):
        return None
    return starts


def _lists_directory(
    text: str, citation: re.Pattern[str], *, recursive: bool = False,
) -> int:
    """How many lines of *text* cite a directory (*citation*) and list it:
    the line calls a listing function itself, or its statement assigns the
    directory to a name the file lists elsewhere -- the line's own
    assignment, or the one a multi-line statement starts with. With
    *recursive*, only a recursive listing (:data:`_RECURSIVE_CALL`) counts.
    0 when the file does not list it."""
    call_re = _RECURSIVE_CALL_RE if recursive else _LISTING_CALL_RE
    # Either way of listing needs the listing call somewhere in the file;
    # the call search is far cheaper than the citation's, so it goes first.
    if not call_re.search(text) or not citation.search(text):
        return 0
    lines = text.splitlines()
    starts: list[int] | None = None
    tokenized = False
    listing_lines = 0
    holders: set[str] = set()
    for index, line in enumerate(lines):
        if not citation.search(line):
            continue
        if call_re.search(line):
            listing_lines += 1
            continue
        match = _ASSIGNED_NAME_RE.match(line)
        if match is None:
            # Inside a multi-line statement: the name it assigns, if any.
            if not tokenized:
                starts, tokenized = _statement_starts(text, len(lines)), True
            if starts is not None and starts[index] != index:
                match = _ASSIGNED_NAME_RE.match(lines[starts[index]])
        if match is not None:
            holders.add(match.group(1))
    listing_lines += sum(
        1 for holder in sorted(holders) if _lists_name(text, holder, recursive=recursive)
    )
    return listing_lines


@dataclass(frozen=True)
class _DirectorySearch:
    """A changed directory name, the pattern citing it, and the patterns
    citing each ancestor a recursive listing of which also lists it."""

    name: str
    citation: re.Pattern[str]
    ancestors: tuple[re.Pattern[str], ...] = ()

    @classmethod
    def for_directory(cls, name: str) -> _DirectorySearch:
        return cls(
            name, _directory_citation(name),
            tuple(_directory_citation(a) for a in ancestor_directories(name)),
        )

    def listings(self, text: str) -> int:
        """How many lines of *text* list this directory: directly, or by a
        recursive listing of one of its ancestors (module docstring,
        selection rule 3)."""
        return _lists_directory(text, self.citation) + sum(
            _lists_directory(text, ancestor, recursive=True) for ancestor in self.ancestors
        )


def _reference(
    tests_root: pathlib.Path, relative: pathlib.PurePosixPath,
    pattern: re.Pattern[str] | None, names: frozenset[str],
    directories: Sequence[_DirectorySearch] = (),
) -> _Reference | None:
    """Measure how *relative* references *names* and lists *directories*;
    None when it does neither."""
    text = (tests_root / relative).read_text(encoding="utf-8", errors="replace")
    cited: set[str] = set()
    occurrences = 0
    if pattern is not None:
        for match in pattern.finditer(text):
            cited.add(match.group(0))
            occurrences += 1
    # A changed test file is affected by definition, cited or not.
    for own_name in (relative.stem, relative.name):
        if own_name in names:
            cited.add(own_name)
            occurrences += 1
    listed: set[str] = set()
    for directory in directories:
        if count := directory.listings(text):
            listed.add(directory.name)
            occurrences += count
    if not cited and not listed:
        return None
    return _Reference(relative, frozenset(cited), occurrences, frozenset(listed))


def _check_cap(cap: int | None) -> None:
    if cap is None:
        return
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ValueError(
            f"cap must be a whole number of test files, 1 or more, or None for "
            f"no cap; got {cap!r}. The pre-merge runner passes "
            f"[testing] pre_merge_affected_cap (default 60)."
        )


def affected_tests(
    diff_names: Iterable[str], tests_root: str | pathlib.Path, cap: int | None,
) -> AffectedTests:
    """Select the test files under *tests_root* that cite a changed name
    or list a changed directory.

    Args:
        diff_names: the names a change touched -- module names, script file
            names, function and class names, and directory names ending in
            ``/`` (see :func:`names_from_diff`). Generic names are dropped
            first (:func:`split_generic_names`).
        tests_root: the directory holding the test tree, e.g. ``team/tests``.
        cap: the most test files to return, 1 or more; ``None`` for no cap.

    Returns:
        An :class:`AffectedTests` list of paths (``tests_root`` joined with
        each file's relative path), ranked by distinct names cited, then by
        distinct directories listed, then by total occurrences, then by
        relative path, and cut to *cap*. ``matched_count`` and
        ``cap_applied`` say whether the cap bit.

    Raises:
        TypeError: *diff_names* is a single string rather than an iterable.
        ValueError: *cap* is not a positive whole number or None.
        NotADirectoryError: *tests_root* is not an existing directory.
    """
    _check_cap(cap)
    root = pathlib.Path(tests_root)
    if not root.is_dir():
        raise NotADirectoryError(
            f"tests root {str(root)!r} is not a directory; pass the directory "
            f"holding the test tree, for example team/tests in the repository"
        )
    searched, _dropped = split_generic_names(diff_names)
    if not searched:
        return AffectedTests(matched_count=0, cap=cap)
    names = frozenset(searched)
    plain = [n for n in searched if not is_directory_name(n)]
    pattern = _name_pattern(plain) if plain else None
    directories = [_DirectorySearch.for_directory(d) for d in searched if is_directory_name(d)]
    references = [
        ref for relative in iter_test_files(root)
        if (ref := _reference(root, relative, pattern, names, directories)) is not None
    ]
    references.sort(key=_Reference.rank_key)
    kept = references if cap is None else references[:cap]
    return AffectedTests(
        (root / ref.relative for ref in kept), matched_count=len(references), cap=cap,
    )


# ---------------------------------------------------------------------------
# Names from a git diff
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class DiffReadError(RuntimeError):
    """git could not produce the diff; carries the dual-channel fields: a
    stable machine ``error`` key, a plain ``message`` and a runnable
    ``remedy``."""

    error: str
    message: str
    remedy: str

    def __post_init__(self) -> None:
        super().__init__(self.message)


@dataclass
class _Deletion:
    """A pure-deletion hunk: removed lines with nothing added in their place.

    ``after`` is the new file's line the removed lines followed; ``indent``
    is the smallest indentation among the removed non-blank lines, or None
    when every removed line was blank.
    """

    after: int
    indent: int | None = None


@dataclass
class _FileChange:
    """What one file's ``-U0`` hunks touched.

    ``added_text`` maps each added line's number in the new file to its
    text; ``removed_text`` maps each removed line's number in the old file
    (the diff's merge base) to its text.
    """

    added_lines: set[int] = field(default_factory=set)
    deletions: list[_Deletion] = field(default_factory=list)
    added_text: dict[int, str] = field(default_factory=dict)
    removed_text: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _Scope:
    """A function or class: its name, line span and definition indentation."""

    name: str
    start: int
    end: int
    indent: int

    def holds(self, change: _FileChange) -> bool:
        """True when *change* altered this scope's body.

        An added line inside the span alters it. A pure deletion alters it
        when the removed lines followed a line inside the span and sat
        deeper than the definition -- so removing a whole top-level function
        is not blamed on the function above it.
        """
        if any(self.start <= n <= self.end for n in change.added_lines):
            return True
        return any(
            d.indent is not None and d.indent > self.indent and self.start <= d.after <= self.end
            for d in change.deletions
        )


_HUNK_RE = re.compile(r"^@@+\s+-(\d+)(?:,\d+)?\s+\+(\d+)(?:,(\d+))?\s+@@")

# A definition, not prose that happens to start with the word: the name is
# followed by an argument list or base list ``(``, a class body ``:``, or
# type parameters ``[``. "class whose members ..." does not match.
_PY_DEF_RE = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)\s*[(:\[]")
_PY_ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=]*)?=(?!=)")
_SH_FUNC_RE = re.compile(
    r"^(\s*)(?:function\s+([A-Za-z_][\w:.-]*)\s*(?:\(\s*\))?|([A-Za-z_][\w:.-]*)\s*\(\s*\))"
)
_SH_ASSIGN_RE = re.compile(
    r"^(?:export\s+|readonly\s+|declare\s+(?:-\w+\s+)*)?([A-Z][A-Z0-9_]+)="
)

_SHELL_SUFFIXES = (".sh", ".bash")


def _git(repo: pathlib.Path, *args: str) -> str:
    """Run one local git command in *repo*; raise DiffReadError on failure."""
    argv = ["git", "-C", str(repo), "-c", "core.quotePath=false", *args]
    try:
        done = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        raise DiffReadError(
            "affected.git_missing",
            f"git is not installed or not on PATH ({exc})",
            "command -v git || sudo dnf install -y git",
        ) from exc
    if done.returncode != 0:
        raise DiffReadError(
            "affected.git_failed",
            f"'{' '.join(argv)}' exited {done.returncode}: {done.stderr.strip()}",
            f"git -C {repo} status",
        )
    return done.stdout


def _verify_ref(repo: pathlib.Path, ref: str) -> None:
    try:
        _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    except DiffReadError as exc:
        raise DiffReadError(
            "affected.ref_unknown",
            f"git cannot resolve {ref!r} to a commit in {repo}; the diff base "
            f"and head must be local refs or commits (nothing is fetched)",
            f"git -C {repo} branch --list",
        ) from exc


def _source_at(repo: pathlib.Path, ref: str, path: str) -> str | None:
    """The text of *path* at *ref*, or None when the file is absent there."""
    try:
        return _git(repo, "show", f"{ref}:{path}")
    except DiffReadError:
        return None


def path_name(path: str) -> str:
    """The name a changed *path* contributes: a Python file's module name
    (its stem), any other file's full file name."""
    pure = pathlib.PurePosixPath(path)
    return pure.stem if pure.suffix == ".py" else pure.name


def _indent_of(text: str) -> int:
    return len(text) - len(text.lstrip())


def _parse_unified_diff(text: str) -> dict[str, _FileChange]:
    """Read ``git diff -U0 --no-prefix`` output into per-file changes.

    Keyed by the new path, or the old path for a deleted file. Hunk line
    numbers (``added_lines``, ``deletions``) are the new file's: each added
    line's own number, and for a pure deletion the line the removed lines
    followed. ``added_text`` and ``removed_text`` carry each changed line
    under its number in its own side's file.
    """
    changes: dict[str, _FileChange] = {}
    old_path = None
    current: _FileChange | None = None
    deletion: _Deletion | None = None
    old_number = new_number = 0
    for line in text.splitlines():
        if line.startswith("diff --git "):
            old_path, current, deletion = None, None, None
        elif line.startswith("--- ") and current is None:
            old_path = line[4:].rstrip("\t")
        elif line.startswith("+++ ") and current is None:
            new_path = line[4:].rstrip("\t")
            key = old_path if new_path == "/dev/null" else new_path
            if key and key != "/dev/null":
                current = changes.setdefault(key, _FileChange())
        elif current is not None and (match := _HUNK_RE.match(line)):
            old_number = int(match.group(1))
            start = new_number = int(match.group(2))
            count = 1 if match.group(3) is None else int(match.group(3))
            deletion = None
            if count == 0:
                deletion = _Deletion(after=start)
                current.deletions.append(deletion)
            else:
                current.added_lines.update(range(start, start + count))
        elif current is not None and line[:1] == "+":
            current.added_text[new_number] = line[1:]
            new_number += 1
        elif current is not None and line[:1] == "-":
            body = line[1:]
            current.removed_text[old_number] = body
            old_number += 1
            if deletion is not None and body.strip():
                indent = _indent_of(body)
                deletion.indent = indent if deletion.indent is None else min(deletion.indent, indent)
    return changes


def _python_scopes(source: str) -> list[_Scope]:
    """Every function and class in Python *source*, decorators included in
    its span; empty when the source does not parse."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    scopes = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            scopes.append(_Scope(node.name, start, node.end_lineno or node.lineno, node.col_offset))
    return scopes


def _shell_scopes(source: str) -> list[_Scope]:
    """Every shell function in *source*.

    A function ends at the first later line whose ``}`` sits at the same
    indentation as its definition; a one-line function ends where it starts.
    """
    text_lines = source.splitlines()
    scopes = []
    for index, line in enumerate(text_lines):
        match = _SH_FUNC_RE.match(line)
        if not match:
            continue
        indent, name = match.group(1), match.group(2) or match.group(3)
        end = index
        if not line.rstrip().endswith("}"):
            for later in range(index + 1, len(text_lines)):
                if text_lines[later].startswith(indent + "}"):
                    end = later
                    break
        scopes.append(_Scope(name, index + 1, end + 1, len(indent)))
    return scopes


def _string_interior_lines(source: str) -> set[int] | None:
    """Line numbers of Python *source* that begin inside a string literal.

    Every line after the first of a multi-line string -- a docstring's
    body, a triple-quoted constant, a multi-line f-string -- starts inside
    the string, so nothing on it is code. None when the source does not
    parse, meaning "cannot tell".
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    inside: set[int] = set()
    for node in ast.walk(tree):
        is_string = isinstance(node, ast.JoinedStr) or (
            isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes))
        )
        if is_string and node.end_lineno and node.end_lineno > node.lineno:
            inside.update(range(node.lineno + 1, node.end_lineno + 1))
    return inside


def _code_lines(numbered: dict[int, str], source: str | None) -> list[str]:
    """The texts in *numbered* (line number -> text) whose line in Python
    *source* does not begin inside a string literal. All of them when the
    source is absent or does not parse: :data:`_PY_DEF_RE`'s definition
    shape is then the only filter."""
    inside = _string_interior_lines(source) if source is not None else None
    if not inside:
        return list(numbered.values())
    return [text for number, text in numbered.items() if number not in inside]


def _definitions_on_lines(path: str, text_lines: Iterable[str]) -> set[str]:
    """Names defined or assigned on added and removed lines of *path*.

    A comment line matches no pattern here, so it contributes nothing;
    callers drop Python lines inside string literals beforehand
    (:func:`_code_lines`).
    """
    names: set[str] = set()
    is_python = path.endswith(".py")
    is_shell = path.endswith(_SHELL_SUFFIXES)
    for line in text_lines:
        if is_python:
            for pattern in (_PY_DEF_RE, _PY_ASSIGN_RE):
                if match := pattern.match(line):
                    names.add(match.group(1))
        elif is_shell:
            if match := _SH_FUNC_RE.match(line):
                names.add(match.group(2) or match.group(3))
            elif match := _SH_ASSIGN_RE.match(line):
                names.add(match.group(1))
    return names


def names_from_diff(
    repo: str | pathlib.Path, base: str, head: str = "HEAD",
) -> list[str]:
    """Return the sorted, unique names changed in ``git diff base...head``.

    Reads only the local repository at *repo*. See the module docstring for
    which names a change contributes, directory names (ending in ``/``)
    included. Generic names are kept here and dropped by the selection, so
    a caller can show them.

    Raises:
        DiffReadError: git is missing, *repo* is not a repository, or
            *base* or *head* does not resolve to a local commit.
    """
    # Diff paths are relative to the repository top, so every later git
    # command runs there; this also reports a non-repository directory.
    repo_path = pathlib.Path(_git(pathlib.Path(repo), "rev-parse", "--show-toplevel").strip())
    _verify_ref(repo_path, base)
    _verify_ref(repo_path, head)
    span = f"{base}...{head}"
    common = ("--no-color", "--no-renames", "--no-ext-diff", "--no-textconv")
    listed = _git(repo_path, "diff", *common, "--name-only", "-z", span)
    paths = [p for p in listed.split("\0") if p]
    names = {path_name(p) for p in paths}

    # A changed test file contributes its stem (above) and nothing more: the
    # helpers, constants and fixture methods it defines are its own, and the
    # same names in other test files are other definitions, so searching for
    # them would select unrelated tests. Nor does it contribute its
    # directory: a new or edited test is not a subject other tests list.
    names |= {
        directory for p in paths
        if not is_test_file(pathlib.PurePosixPath(p)) and (directory := directory_name(p))
    }
    code_paths = [
        p for p in paths
        if p.endswith((".py", *_SHELL_SUFFIXES)) and not is_test_file(pathlib.PurePosixPath(p))
    ]
    if code_paths:
        unified = _git(repo_path, "diff", *common, "-U0", "--no-prefix", span, "--", *code_paths)
        # base...head diffs the merge base against head: removed lines are
        # numbered in the merge base's copy of the file.
        old_side = _git(repo_path, "merge-base", base, head).strip()
        for path, change in _parse_unified_diff(unified).items():
            head_source = _source_at(repo_path, head, path)
            added, removed = list(change.added_text.values()), list(change.removed_text.values())
            if path.endswith(".py"):
                # A line inside a docstring or other string literal is prose,
                # whatever word it starts with.
                added = _code_lines(change.added_text, head_source)
                if change.removed_text:
                    removed = _code_lines(change.removed_text, _source_at(repo_path, old_side, path))
            names |= _definitions_on_lines(path, [*added, *removed])
            if head_source is None or not (change.added_lines or change.deletions):
                continue  # deleted at head: its removed lines were scanned above
            scopes = _python_scopes(head_source) if path.endswith(".py") else _shell_scopes(head_source)
            names |= {scope.name for scope in scopes if scope.holds(change)}
    names.discard("")
    return sorted(names)



# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="affected.py",
        description=(
            "Print the test files a change should run: test files under the "
            "tests root that cite a script, module, function or command name "
            "changed in 'git diff BASE...HEAD', or that list the directory "
            "a changed file sits in (directly, or by a recursive listing of "
            "an ancestor such as team/scripts/), most-referencing first. "
            "Selection only; nothing is run."
        ),
        epilog=(
            "Examples:\n"
            "  python3 team/pgai_agent_kanban/testing/affected.py --base ai_main\n"
            "  python3 team/pgai_agent_kanban/testing/affected.py --base ai_main --cap 60 --json\n"
            "  python3 team/pgai_agent_kanban/testing/affected.py --names semver run-unit-tests.sh\n"
            "\n"
            "Exit codes: 0 selection printed (an empty one included); "
            "2 invocation error (unknown ref, not a git repository, missing tests root)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--base", metavar="REF",
        help="diff base: names come from 'git diff REF...HEAD' in --repo "
             "(local refs only; nothing is fetched)",
    )
    source.add_argument(
        "--names", metavar="NAME", nargs="+",
        help="search for these names directly instead of reading a diff; a "
             "name ending in '/' is a directory (team/scripts/dashboard/)",
    )
    parser.add_argument(
        "--head", metavar="REF", default="HEAD",
        help="diff head used with --base (default: HEAD)",
    )
    parser.add_argument(
        "--repo", metavar="DIR", default=".",
        help="git repository to read the diff from (default: the current directory)",
    )
    parser.add_argument(
        "--tests-root", metavar="DIR",
        help="directory holding the test tree (default: team/tests at the "
             "top of --repo)",
    )
    parser.add_argument(
        "--cap", metavar="N", type=int, default=None,
        help="return at most N test files, the most-referencing (default: no cap)",
    )
    parser.add_argument(
        "--show-names", action="store_true",
        help="also print, on stderr, the names searched for and the generic names dropped",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the whole selection as one JSON object on stdout",
    )
    return parser


def _default_tests_root(repo: pathlib.Path) -> pathlib.Path:
    toplevel = _git(repo, "rev-parse", "--show-toplevel").strip()
    return pathlib.Path(toplevel) / "team" / "tests"


def _display_path(path: pathlib.Path, tests_root: pathlib.Path) -> str:
    """*path* relative to the tests root's parent (``tests/unit/test_x.py``)."""
    return path.relative_to(tests_root.parent).as_posix()


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point; returns the process exit code."""
    args = _build_arg_parser().parse_args(argv)
    repo = pathlib.Path(args.repo)
    # Invocation errors of a diagnostic command, not a gate: reported in the
    # dual-channel shape so the remedy is runnable, and mapped to exit 2.
    try:
        names = list(args.names) if args.names else names_from_diff(repo, args.base, args.head)
        tests_root = (
            pathlib.Path(args.tests_root) if args.tests_root else _default_tests_root(repo)
        )
        selection = affected_tests(names, tests_root, args.cap)
    except DiffReadError as exc:
        print(format_error_contract(exc.error, exc.message, exc.remedy), file=sys.stderr)
        return 2
    except (NotADirectoryError, ValueError) as exc:
        print(format_error_contract(
            "affected.bad_argument", str(exc),
            "python3 team/pgai_agent_kanban/testing/affected.py --help",
        ), file=sys.stderr)
        return 2

    searched, dropped = split_generic_names(names)
    shown = [_display_path(p, tests_root) for p in selection]
    if args.json:
        print(json.dumps({
            "base": args.base, "head": args.head if args.base else None,
            "tests_root": str(tests_root), "names": searched, "generic_names": dropped,
            "matched": selection.matched_count, "cap": selection.cap,
            "cap_applied": selection.cap_applied, "tests": shown,
        }, indent=2))
        return 0
    if args.show_names:
        print(f"names searched ({len(searched)}): {' '.join(searched) or '(none)'}", file=sys.stderr)
        print(f"generic names dropped ({len(dropped)}): {' '.join(dropped) or '(none)'}", file=sys.stderr)
    for line in shown:
        print(line)
    summary = f"affected tests: {selection.matched_count} matched {len(searched)} names"
    if selection.cap_applied:
        summary += f"; cap {selection.cap} applied, showing the {len(selection)} most-referencing"
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
