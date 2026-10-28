#!/usr/bin/env python3
"""
lint_section_parsers.py
=======================
A ratchet lint that reports hand-rolled ``## `` section parsers outside the
one section reader.

Every ``## Heading`` section of a status.md, release-state.md, task README,
requirement or bug file is read by ``pgai_agent_kanban.task_status.read_section``
(Python) or by ``read_md_field`` (bash, ``team/scripts/lib/md_field.sh``,
which runs the reader's own CLI). A second copy of that logic drifts from
the reader the first time a format rule changes -- fenced code, blank
lines, trailing whitespace, CRLF -- so this lint reports any new code that
finds a section by detecting its heading itself. The tree still carries
sites that detect a heading without reading a value (a section writer, a
heading counter), so the lint does not demand zero: it compares every site
it finds with an allowlist recorded from the tree, and reports any site the
allowlist does not name.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Gates and Refusals" and
"Blocking Severity: only real damage blocks"). A hand-rolled parser carries
no runtime-failure risk by itself, so this lint narrates its findings,
writes them to ``--findings-file`` for the gated runner's aggregate, and
ALWAYS exits 0. It never fails a runner or a release.

WHAT IS SCANNED
---------------
Every non-test ``*.py`` file under ``team/pgai_agent_kanban``,
``team/pm-agent`` and ``team/scripts``, and every non-test ``*.sh`` file
under ``team/scripts``. A file is a test file, and is skipped, when any
directory between its scan directory and the file is named ``tests`` or
``test``, or its name is ``conftest.py``, ``test_*.py`` or ``*_test.py``.

Two files are exempt by design and never scanned: the reader itself,
``team/pgai_agent_kanban/task_status.py``, and its bash library,
``team/scripts/lib/md_field.sh``.

WHAT COUNTS AS A SITE
---------------------
A line that detects a level-2 heading in one of these shapes:

  * ``startswith``  -- ``line.startswith("## ")``, ``startswith("##")`` or a
    tuple of prefixes opening with one;
  * ``equals``      -- a comparison with a ``"## ..."`` literal, either side
    (``stripped == "## Queue"``, bash ``[[ $line == "## "* ]]``);
  * ``anchored``    -- a pattern anchored on ``^##``, optionally after a
    whitespace class (``^\\s*##``, ``^[ \\t]*##``, ``^[[:space:]]*##``,
    ``^ {0,3}##``) or a group opener (``^(##``, ``^(?:##``): a Python regex,
    an awk, sed or grep address, or a bash ``[[ =~ ]]`` test;
  * ``re-match``    -- ``re.match`` / ``re.fullmatch`` with a pattern
    opening on ``##``, which is anchored by the call;
  * ``unanchored``  -- a grep (``grep``, ``egrep``, ``fgrep``, ``rg``)
    whose pattern opens on ``##`` (``grep -A1 "## State"``), or an awk
    program matching ``##`` without an anchor (``$0 ~ /## Goal/``,
    ``/## Goal/ {f=1}``, ``index($0, "## Goal")``) on the line that runs
    ``awk``;
  * ``variable``    -- a comparison (``==``, ``!=``, ``startswith(``; bash
    ``=``, ``==``, ``!=``) against a name the same file assigns a string
    opening on ``##`` (``target = f"## {heading}"`` then
    ``if line.strip() == target:``; bash ``target="## $h"`` then
    ``[[ $line == "$target" ]]``). The comparison line is the site.
  * ``cli-read``    -- bash only: a direct call of the section reader's own
    CLI, ``pgai_agent_kanban.task_status read`` (``--section``, ``--raw``,
    ``--value`` or the default ``## State`` read), which ``read_md_field``
    wraps. The command is read whole across ``\\`` line continuations and
    its first line is the site. A ``--field`` record read is not a site:
    ``read_md_field`` has no field mode, so that read stays on the CLI.

Wherever these shapes name ``##``, a quantified hash run whose count can
be two is read the same way: ``#{2}``, ``#{1,3}``, ``#{2,}``, ``#{,6}``,
``#+`` (``#{3}`` and ``#{3,6}`` are not level-2 headings and are not
sites), as is ``\\#\\#``.

Not covered: an awk program body on a line after the one that runs
``awk``, an unanchored sed address (``/## State/``), and a heading built in
one file and compared in another.

Comment lines (first non-blank character ``#``) are never scanned, nor is
a Python docstring. Each matching line is one site, reported once whatever
number of shapes it carries.

THE ALLOWLIST AND THE RATCHET RULE
----------------------------------
``team/config/section-parser-allowlist.txt`` holds one line per site, four
fields separated by tabs (a run of two or more spaces is accepted too):

    <path>\\t<class>\\t<reason>\\t<code>

``<path>`` is relative to the repository root; ``<code>`` is the site's
line as written, its whitespace runs read as one space, so an entry
survives the site moving within its file. ``<class>`` names why the site
reads no value, one of:

  section-writer   writes or rewrites a section (and the check that
                   finds where)
  ledger-heading   matches the per-entry heading of a multi-entry ledger
  heading-counter  counts or lists headings, reading no body
  structure-lint   a lint of a document's heading structure
  presence-check   tests that a heading exists, to gate a write or a stop
  remedy-text      a command printed for a reader, never run here
  header-exempt    a script whose header forbids calling the reader

A value-reading site is never allowlisted: it moves to the reader.
Reading rules:

  * A site no entry names.                          -> finding ``unlisted``
  * An entry that names no site -- its file is
    gone, no longer scanned, or exempt, or its
    line changed or was removed -- is reported for
    removal.                                        -> finding ``stale``
  * An unparseable line, an unknown class or an
    empty reason.                                   -> finding ``allowlist-malformed``

Two identical lines in one file are two sites and need two entries. The
ratchet only turns one way: an entry is removed when its site is, and a new
one is added only for a new site of one of the classes above.
``--list-sites`` prints every site found, for writing an entry.

OUTPUT
------
One narration line per finding, then a totals line:

    lint_section_parsers: totals: <N> file(s) scanned (<P> python, <B> bash),
    <S> site(s), <A> allowlisted; <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (one object per
finding, keys ``lint``, ``severity``, ``kind``, ``file``, ``line``,
``summary``, ``remedy``) in the shape
``team/scripts/lib/gated_runner_lint.sh``'s ``gated_runner_merge_findings_file``
merges. The file is always written, as ``[]`` when there is nothing to report.

USAGE
-----
  python3 team/scripts/lint_section_parsers.py [OPTIONS]

  --root PATH            Repository root (default: two levels above this
                         script's directory).
  --python-dir PATH      A directory whose *.py files are scanned, relative
                         to --root or absolute; repeatable (default:
                         team/pgai_agent_kanban, team/pm-agent, team/scripts).
  --bash-dir PATH        A directory whose *.sh files are scanned;
                         repeatable (default: team/scripts).
  --allowlist PATH       Allowlist file, relative to --root or absolute
                         (default: team/config/section-parser-allowlist.txt).
  --findings-file PATH   Write findings as a JSON array to PATH.
  --list-sites           Print every site found, one per line as
                         "<path>:<line>  <shape>  <listed|UNLISTED>  <code>",
                         and exit 0 (no findings check).
  --verbose, -v          Print each file's site count as it is scanned.

EXIT CODES
----------
  0   Always, whatever was found (quality class).
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import NamedTuple

# team/ import path -- needed for the dual-channel error-contract emitter.
_TEAM_DIR = Path(__file__).resolve().parent.parent
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))

LINT_NAME = "section_parsers"
DEFAULT_PYTHON_DIRS = ("team/pgai_agent_kanban", "team/pm-agent", "team/scripts")
DEFAULT_BASH_DIRS = ("team/scripts",)
DEFAULT_ALLOWLIST = "team/config/section-parser-allowlist.txt"
RUN_COMMAND = "python3 team/scripts/lint_section_parsers.py"
TEST_DIR_NAMES = frozenset({"tests", "test"})

#: Files never scanned: the reader and its bash library (repository-relative).
EXEMPT_FILES = frozenset({
    "team/pgai_agent_kanban/task_status.py",
    "team/scripts/lib/md_field.sh",
})

#: The classes of site an allowlist entry may name: none of them reads a value.
SITE_CLASSES = frozenset({
    "section-writer",
    "ledger-heading",
    "heading-counter",
    "structure-lint",
    "presence-check",
    "remedy-text",
    "header-exempt",
})

# A string-literal opener: optional prefix letters, then a quote.
_Q = r"""[rRbBuUfF]{0,2}["']"""
# A whitespace class a pattern may put between its anchor and the hashes:
# \s* \s+ [ \t]* [[:space:]]* " *" " {0,3}" \t*.
_WS = r"""(?:(?:\\s|\[(?:\[:[a-z]+:\]|[^\]\n])*\]|\\t| )(?:[*+?]|\{\d*(?:,\d*)?\})?)"""
# Two hashes, spelled any way a pattern can: ``##``, ``\#\#``, or a
# quantified run (``#{1,3}``, ``#{2,}``, ``#+``); whether a quantifier's
# count can be two is checked by _admits_level_two.
_HASHES = r"""(?:##|\\#\\#|#\{\d*(?:,\d*)?\}|#\+)"""
# An optional group opener between an anchor and the hashes: ``(`` ``(?:``.
_GROUP = r"""(?:\((?:\?:)?)?"""
_QUANTIFIER_RE = re.compile(r"#\{(\d*)(,?)(\d*)\}")

#: Heading-detection shapes, checked in this order; the first that matches
#: names the site.
SHAPES: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("startswith", re.compile(r"\.startswith\(\s*\(?\s*" + _Q + r"\s*##")),
    ("equals", re.compile(r"[=!]=\s*" + _Q + r"##|" + _Q + r"##[^\"'\n]*[\"']\*?\s*[=!]=")),
    ("anchored", re.compile(r"\^" + _WS + r"*" + _GROUP + _HASHES)),
    ("re-match", re.compile(r"\bre\.(?:match|fullmatch)\(\s*" + _Q + _WS + r"?" + _GROUP + _HASHES)),
    ("unanchored", re.compile(
        r"\b(?:[ef]?grep|rg)\b[^|;&\n]*?\s(?:-e\s*)?[\"']?" + _HASHES
        + r"|\bawk\b.*?(?:/|~\s*[\"']|index\([^,\n]*,\s*[\"'])[ \t]*" + _HASHES
    )),
)

#: A direct call of the section reader's CLI from bash (the ``cli-read``
#: shape); a ``--field`` record read is excluded by :func:`_cli_read_sites`.
_CLI_READ_RE = re.compile(r"\btask_status\s+read\b")
_CLI_FIELD_RE = re.compile(r"(?:^|\s)--field(?:=|\s|$)")

# A Python assignment whose value opens on a ``##`` string: ``target =
# f"## {heading}"``, ``self._head: str = "## " + name``.
_PY_HEADING_ASSIGN_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][\w.]*)\s*(?::[^=\n]+)?=\s*" + _Q + r"\s*" + _HASHES
)
# A bash assignment whose value opens on ``##``: ``target="## $h"``,
# ``local head='## State'``.
_SH_HEADING_ASSIGN_RE = re.compile(
    r"^\s*(?:(?:local|declare|readonly|export)\s+(?:-\w+\s+)*)?(?P<name>[A-Za-z_]\w*)=[\"']?" + _HASHES
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


class Site(NamedTuple):
    """One heading-detection line found in a scanned file."""

    path: str  # repository-relative
    line: int  # 1-based
    shape: str
    code: str  # the line, whitespace runs collapsed


class Entry(NamedTuple):
    """One allowlist line."""

    path: str
    site_class: str
    reason: str
    code: str
    lineno: int


class Finding(NamedTuple):
    """One lint finding."""

    kind: str
    file: str
    line: int
    summary: str
    remedy: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object:
        its own fields plus ``lint`` (this lint's name) and ``severity``
        (always ``quality``)."""
        record = self._asdict()
        record.update(lint=LINT_NAME, severity="quality")
        return record


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


def normalise_code(text: str) -> str:
    """Return *text* stripped, every whitespace run read as one space."""
    return " ".join(text.split())


def docstring_lines(text: str) -> set[int]:
    """Return the 1-based line numbers of every docstring in Python *text*.

    The module's, each class's and each function's. Source that does not
    parse has none, so every line of it is scanned.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    return lines


def _admits_level_two(matched: str) -> bool:
    """True unless every hash quantifier in *matched* excludes a count of two.

    ``#{1,3}`` and ``#{2,}`` admit two; ``#{3}`` and ``#{3,6}`` do not. Text
    with no ``#{...}`` quantifier (``##``, ``#+``) admits two.
    """
    for low, comma, high in _QUANTIFIER_RE.findall(matched):
        lo = int(low) if low else 0
        hi = int(high) if high else (None if comma else lo)
        if lo <= 2 and (hi is None or hi >= 2):
            return True
    return not _QUANTIFIER_RE.search(matched)


def heading_variables(lines) -> frozenset[str]:
    """Return every name *lines* assign a string opening on ``##``.

    Python (``target = f"## {heading}"``) and bash (``target="## $h"``)
    assignments both count; comment lines are skipped.
    """
    names: set[str] = set()
    for line in lines:
        if line.strip().startswith("#"):
            continue
        for pattern in (_PY_HEADING_ASSIGN_RE, _SH_HEADING_ASSIGN_RE):
            m = pattern.match(line)
            if m is not None and _admits_level_two(m.group(0)):
                names.add(m.group("name"))
    return frozenset(names)


def _compares_with(line: str, name: str) -> bool:
    """True when *line* compares something with the variable *name*."""
    word = re.escape(name)
    py = (
        rf"[=!]=\s*{word}(?![\w.(\[])|(?<![\w.]){word}\s*[=!]="
        rf"|\.startswith\(\s*\(?\s*{word}(?![\w.(\[])"
    )
    sh = (
        rf"\s[=!]=?\s+\"?\$\{{?{word}(?!\w)"
        rf"|\$\{{?{word}\}}?\"?\s+[=!]=?\s"
    )
    return re.search(py, line) is not None or re.search(sh, line) is not None


def line_shape(line: str, heading_vars: frozenset[str] = frozenset()) -> str | None:
    """Return the name of the first heading-detection shape *line* carries, or None.

    *heading_vars* names the variables of *line*'s file that hold a ``##``
    heading (:func:`heading_variables`); a comparison with one is the
    ``variable`` shape.
    """
    for name, pattern in SHAPES:
        if any(_admits_level_two(m.group(0)) for m in pattern.finditer(line)):
            return name
    if not (_PY_HEADING_ASSIGN_RE.match(line) or _SH_HEADING_ASSIGN_RE.match(line)):
        if any(_compares_with(line, var) for var in heading_vars):
            return "variable"
    return None


def _cli_read_sites(lines: list[str], rel_path: str, taken: set[int]) -> list[Site]:
    """The ``cli-read`` sites of bash *lines*: each command, read whole across
    backslash continuations, that runs ``task_status read`` without
    ``--field``. The command's first line is the site; a line already in
    *taken* (another shape's site) is not reported twice."""
    sites: list[Site] = []
    idx = 0
    while idx < len(lines):
        start = idx
        command = lines[idx]
        while command.rstrip().endswith("\\") and idx + 1 < len(lines):
            idx += 1
            command = command.rstrip()[:-1] + " " + lines[idx]
        idx += 1
        stripped = lines[start].strip()
        if not stripped or stripped.startswith("#") or (start + 1) in taken:
            continue
        if _CLI_READ_RE.search(command) and not _CLI_FIELD_RE.search(command):
            sites.append(Site(rel_path, start + 1, "cli-read", normalise_code(lines[start])))
    return sites


def scan_text(text: str, rel_path: str, *, python: bool) -> list[Site]:
    """Return every site in one file's *text*."""
    skip = docstring_lines(text) if python else set()
    lines = text.splitlines()
    heading_vars = heading_variables(line for n, line in enumerate(lines, 1) if n not in skip)
    sites: list[Site] = []
    for lineno, line in enumerate(lines, 1):
        if lineno in skip:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        shape = line_shape(line, heading_vars)
        if shape is not None:
            sites.append(Site(rel_path, lineno, shape, normalise_code(line)))
    if not python:
        sites.extend(_cli_read_sites(lines, rel_path, {site.line for site in sites}))
        sites.sort(key=lambda site: site.line)
    return sites


def _is_test_file(path: Path, scan_dir: Path) -> bool:
    if any(part in TEST_DIR_NAMES for part in path.relative_to(scan_dir).parts[:-1]):
        return True
    name = path.name
    return name == "conftest.py" or (name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")))


def repo_path(path: Path, root: Path) -> str:
    """Return *path* as the allowlist names it: relative to *root* (already
    resolved) in POSIX form when it lies under *root*, else its absolute
    POSIX form."""
    absolute = path.resolve()
    if absolute.is_relative_to(root):
        return absolute.relative_to(root).as_posix()
    return absolute.as_posix()


def iter_scan_files(root: Path, dirs: list[Path], suffix: str) -> list[tuple[Path, str]]:
    """Every non-test, non-exempt ``*<suffix>`` file under *dirs*, as ``(path, rel)``, sorted."""
    seen: dict[str, Path] = {}
    for scan_dir in dirs:
        if not scan_dir.is_dir():
            continue
        for p in scan_dir.rglob(f"*{suffix}"):
            if not p.is_file() or _is_test_file(p, scan_dir):
                continue
            rel = repo_path(p, root)
            if rel in EXEMPT_FILES:
                continue
            seen.setdefault(rel, p)
    return sorted(((p, rel) for rel, p in seen.items()), key=lambda item: item[1])


def measure(
    root: Path, python_dirs: list[Path], bash_dirs: list[Path], *, verbose: bool = False
) -> tuple[list[Site], int, int]:
    """Scan every file; return ``(sites, python files, bash files)``."""
    sites: list[Site] = []
    counts = []
    for dirs, suffix, python in ((python_dirs, ".py", True), (bash_dirs, ".sh", False)):
        files = iter_scan_files(root, dirs, suffix)
        counts.append(len(files))
        for path, rel in files:
            found = scan_text(path.read_text(encoding="utf-8", errors="replace"), rel, python=python)
            if verbose:
                print(f"  [section-parsers] {rel}: {len(found)} site(s)")
            sites.extend(found)
    return sites, counts[0], counts[1]


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------


def _split_entry(line: str) -> list[str]:
    """Split one allowlist line into its fields: tabs, else runs of 2+ spaces."""
    if "\t" in line:
        return [f.strip() for f in re.split(r"\t+", line, maxsplit=3)]
    return [f.strip() for f in re.split(r" {2,}", line, maxsplit=3)]


def load_allowlist(path: Path, allow_rel: str) -> tuple[list[Entry], list[Finding]]:
    """Read the allowlist into entries; unparseable lines become findings.

    Liberal reader: blank and ``#`` lines are skipped, and fields may be
    separated by tabs or by runs of two or more spaces.
    """
    entries: list[Entry] = []
    findings: list[Finding] = []
    if not path.is_file():
        findings.append(
            Finding(
                kind="allowlist-missing",
                file=allow_rel,
                line=0,
                summary=(
                    f"allowlist {allow_rel} not found; every heading-detection site is "
                    "reported as unlisted"
                ),
                remedy=f"{RUN_COMMAND} --list-sites",
            )
        )
        return entries, findings
    for lineno, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = _split_entry(line)
        problem = ""
        if len(fields) != 4 or not all(fields):
            problem = "is not '<path>\\t<class>\\t<reason>\\t<code>' with every field filled"
        elif fields[1] not in SITE_CLASSES:
            problem = (
                f"names the class {fields[1]!r}, which is not one of "
                f"{', '.join(sorted(SITE_CLASSES))}"
            )
        if problem:
            findings.append(
                Finding(
                    kind="allowlist-malformed",
                    file=allow_rel,
                    line=lineno,
                    summary=f"allowlist line {lineno} {problem} and was ignored: {raw!r}",
                    remedy=f"{RUN_COMMAND} --list-sites",
                )
            )
            continue
        entries.append(Entry(fields[0], fields[1], fields[2], normalise_code(fields[3]), lineno))
    return entries, findings


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def match_sites(sites: list[Site], entries: list[Entry]) -> tuple[list[Site], list[Entry]]:
    """Pair sites with entries by ``(path, code)``, one entry per site.

    Returns ``(unlisted sites, stale entries)``.
    """
    available: dict[tuple[str, str], list[Entry]] = {}
    for entry in entries:
        available.setdefault((entry.path, entry.code), []).append(entry)
    unlisted: list[Site] = []
    for site in sites:
        bucket = available.get((site.path, site.code))
        if bucket:
            bucket.pop(0)
        else:
            unlisted.append(site)
    stale = sorted((e for bucket in available.values() for e in bucket), key=lambda e: e.lineno)
    return unlisted, stale


def compare(sites: list[Site], entries: list[Entry], allowlist_rel: str) -> list[Finding]:
    """Compare the sites found with the allowlist; return findings."""
    unlisted, stale = match_sites(sites, entries)
    findings: list[Finding] = []
    for site in unlisted:
        findings.append(
            Finding(
                kind="unlisted",
                file=site.path,
                line=site.line,
                summary=(
                    f"{site.path}:{site.line} finds a '## ' heading itself ({site.shape}) "
                    f"outside the section reader: {site.code}"
                ),
                remedy=(
                    "read the section through pgai_agent_kanban.task_status.read_section "
                    "(Python) or read_md_field from team/scripts/lib/md_field.sh (bash); a site "
                    f"that reads no value gets an entry in {allowlist_rel} naming its class, "
                    f"then re-run: {RUN_COMMAND}"
                ),
            )
        )
    for entry in stale:
        findings.append(
            Finding(
                kind="stale",
                file=entry.path,
                line=0,
                summary=(
                    f"allowlist entry for '{entry.path}' ({allowlist_rel} line {entry.lineno}) "
                    f"names no site on the tree -- the file is gone or not scanned, or the "
                    f"line changed or was removed: remove the entry ({entry.code})"
                ),
                remedy=f"{RUN_COMMAND} --list-sites",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_section_parsers",
        description=(
            "Ratchet lint: report code that finds a markdown '## Heading' section by "
            "detecting the heading itself, outside the section reader "
            "(task_status.read_section and read_md_field), unless the allowlist names "
            "the site as one that reads no value. Quality class: always exits 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Allowlist format: one '<path>\\t<class>\\t<reason>\\t<code>' line per site,\n"
            "path relative to the repository root, class one of:\n  "
            + ", ".join(sorted(SITE_CLASSES))
            + ".\nA value-reading site is never listed: it moves to the reader.\n\n"
            "Exit codes:\n  0  Always (findings are narrated, never fatal).\n"
        ),
    )
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root (default: two levels above this script's directory).")
    parser.add_argument("--python-dir", metavar="PATH", action="append", default=None,
                        help="Directory whose *.py files are scanned; repeatable "
                             f"(default: {', '.join(DEFAULT_PYTHON_DIRS)}).")
    parser.add_argument("--bash-dir", metavar="PATH", action="append", default=None,
                        help=f"Directory whose *.sh files are scanned; repeatable (default: {', '.join(DEFAULT_BASH_DIRS)}).")
    parser.add_argument("--allowlist", metavar="PATH", default=DEFAULT_ALLOWLIST,
                        help=f"Allowlist file, relative to --root or absolute (default: {DEFAULT_ALLOWLIST}).")
    parser.add_argument("--findings-file", metavar="PATH", default=None,
                        help="Write findings as a JSON array to PATH (always written).")
    parser.add_argument("--list-sites", action="store_true",
                        help="Print every site found and whether the allowlist names it, then exit 0.")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print each file's site count as it is scanned.")
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
        print(f"lint_section_parsers: could not write findings file {path}: {exc}", file=sys.stderr)


def _list_sites(sites: list[Site], entries: list[Entry]) -> None:
    unlisted = Counter((s.path, s.line) for s in match_sites(sites, entries)[0])
    for site in sites:
        mark = "UNLISTED" if unlisted[(site.path, site.line)] else "listed"
        print(f"{site.path}:{site.line}\t{site.shape}\t{mark}\t{site.code}")


def main(argv: list[str] | None = None) -> int:
    """Run the lint. Always returns 0."""
    args = _build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent.parent
    python_dirs = [_resolve(root, d) for d in (args.python_dir or DEFAULT_PYTHON_DIRS)]
    bash_dirs = [_resolve(root, d) for d in (args.bash_dir or DEFAULT_BASH_DIRS)]
    allowlist_path = _resolve(root, args.allowlist)
    allowlist_rel = repo_path(allowlist_path, root)

    findings: list[Finding] = []
    for scan_dir in (*python_dirs, *bash_dirs):
        if not scan_dir.is_dir():
            findings.append(
                Finding(
                    kind="scan-dir-missing",
                    file=repo_path(scan_dir, root),
                    line=0,
                    summary=f"scan directory {scan_dir} does not exist; nothing under it was scanned",
                    remedy=f"{RUN_COMMAND} --root {root}",
                )
            )
    sites, python_files, bash_files = measure(root, python_dirs, bash_dirs, verbose=args.verbose)
    entries, allow_findings = load_allowlist(allowlist_path, allowlist_rel)

    if args.list_sites:
        _list_sites(sites, entries)
        return 0

    findings.extend(allow_findings)
    findings.extend(compare(sites, entries, allowlist_rel))
    _write_findings(args.findings_file, findings)

    for f in findings:
        print(f"lint_section_parsers: [{f.kind}] {f.summary}")
    unlisted = sum(1 for f in findings if f.kind == "unlisted")
    print(
        f"lint_section_parsers: totals: {python_files + bash_files} file(s) scanned "
        f"({python_files} python, {bash_files} bash), {len(sites)} site(s), "
        f"{len(sites) - unlisted} allowlisted; {len(findings)} finding(s)"
    )

    if findings:
        message = (
            f"{len(findings)} section-parser finding(s) against {allowlist_rel}; "
            "see the lines printed above"
        )
        remedy = (
            "read each unlisted section through task_status.read_section or read_md_field, "
            "remove every stale allowlist entry, then re-run: "
            f"{RUN_COMMAND}"
        )
        # CENSUS-ANCHOR: section-parsers-01
        # CONVERT: a quality-class finding with zero runtime-failure risk --
        # narrated and filed, never fatal; the lint returns 0 below.
        sys.stdout.flush()
        try:
            from pgai_agent_kanban.lib.error_contract import emit_error_contract

            emit_error_contract("lint.section-parsers", message, remedy)
        except ImportError:
            print(f"error:   lint.section-parsers\nmessage: {message}\nremedy:  {remedy}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
