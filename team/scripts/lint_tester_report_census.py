#!/usr/bin/env python3
"""
lint_tester_report_census.py
============================
Reports kanban code that parses a TESTER ``report.md`` itself instead of
calling the one parser module, ``team/pgai_agent_kanban/reports/tester_report.py``.

That module is the only code that reads a TESTER report: every consumer
calls ``parse_report`` (or its CLI / bash wrapper) and reads the returned
``TesterReport`` fields. A second reader with its own idea of what a finding
heading or a field line looks like is how two consumers come to disagree
about the same report, so this lint names every place outside the module
whose code text matches a report-parsing pattern.

ENFORCEMENT CLASS
-----------------
CONVERT (quality class, ``team/SOP-core.md`` "Blocking Severity: only real
damage blocks"). A second reader is a maintainability finding, not a runtime
failure: the lint narrates each finding, writes them to ``--findings-file``
for the gated runner's aggregate, and ALWAYS exits 0 -- it never fails a
runner and never blocks a release.

WHAT IS SCANNED
---------------
Every ``*.py`` and ``*.sh`` file under ``team/`` (relative to ``--root``),
except:

  * test code -- any file with a directory named ``tests`` or ``test``
    between ``team/`` and the file (``team/tests/``,
    ``team/pgai_agent_kanban/api/tests/``, ...);
  * the parser module itself, ``team/pgai_agent_kanban/reports/tester_report.py``;
  * every path listed in the allowlist (below).

Markdown documents and templates are not code and are never scanned.

Comment text is not code: comment-only lines in shell files, and comments
plus string-expression statements (docstrings) in Python files, are blanked
before matching. Inline Python inside a shell heredoc IS scanned -- it runs.

WHAT COUNTS AS A MATCH
----------------------
Six report-parsing patterns, matched case-insensitively:

  ``### Finding``         a finding heading (three or more ``#``)
  ``## Recommendation``   the recommendation section heading (two or more ``#``)
  ``**Systemic Risk:**``  a bold field label
  ``**Fix Effort:**``     a bold field label
  ``**Filed As:**``       a bold field label
  ``**Regression:**``     a bold field label

Readers usually hold these as regular expressions rather than as literal
text, so each line is first normalised: a regex whitespace token (``\\s+``,
``\\s*``, ``[ ]+``, ``[[:space:]]*``, ``\\ ``) becomes one space, word-boundary and anchor
escapes (``\\b``, ``\\A``, ``\\Z``) are dropped, and every remaining
backslash is removed. The patterns then accept the shapes a reader writes:

  * a heading's hashes literally (``###``) or as a quantified hash
    (``#{3,6}``, ``#+``), with regex grouping punctuation between the hashes
    and the word -- ``r"^(#{3,6})\\s+finding\\b"`` matches;
  * a field label's bold marker as ``**``, ``*{2}``, ``[*]{2}`` or
    ``[*][*]`` (``r"\\*\\*Systemic Risk:\\*\\*"`` matches), the colon inside
    or outside the bold (``**Filed As**:``), and a group or alternation
    opening between the marker and the label (``**(?:Systemic Risk|...)``).

A label mentioned in plain prose without its bold marker (``"Fix Effort"``
as a dictionary key, say) is not one of the six patterns and is not reported.

ALLOWLIST
---------
``team/config/tester-report-census-allowlist.txt`` (override with
``--allowlist``). One repository-relative path per line
(``team/scripts/lib/example.sh``); blank lines are ignored and text after a
``#`` is a comment -- give every entry its reason in a comment on the line
above it. A listed file is not scanned. The list ships empty: the goal is
one parser, so an entry is a deliberate, explained exception. A whole-tree
run also reports an entry that has gone stale -- the file is gone, or no
longer matches any pattern -- so the list only ever shrinks back.

OUTPUT
------
One line per finding, then a totals line:

    tester_report_census: <file>:<line>: matches <pattern> -- <fix>
    lint_tester_report_census: totals: <N> file(s) scanned; <F> finding(s)

``--findings-file PATH`` writes the findings as a JSON array (keys
``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``,
``remedy``) in the shape ``team/scripts/lib/gated_runner_lint.sh``'s
``gated_runner_merge_findings_file`` merges; ``[]`` when clean.

USAGE
-----
  python3 team/scripts/lint_tester_report_census.py [--root PATH]
      [--paths PATH ...] [--allowlist PATH] [--findings-file PATH]

  --root PATH           Repository root (default: two levels above this
                        script's directory). Paths are reported, excluded
                        and allowlisted relative to it.
  --paths PATH ...      Scan exactly these files instead of the whole tree.
                        The scope rules above still apply: a test file, the
                        parser module, an allowlisted path, or a file that
                        is not *.py / *.sh is skipped. Stale allowlist
                        entries are reported only on a whole-tree run.
  --allowlist PATH      Allowlist file (default:
                        <root>/team/config/tester-report-census-allowlist.txt);
                        a missing file is read as empty.
  --findings-file PATH  Write findings as a JSON array to PATH.

  The gated unit runner (team/scripts/run-unit-tests.sh) runs it on every
  certification run; run it by hand from the repository root with:

  python3 team/scripts/lint_tester_report_census.py

EXIT CODES
----------
  0   Always, whatever was found (quality class). An argument the parser
      rejects exits 2 through argparse before any scan.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple

LINT_NAME = "tester_report_census"
SCAN_TOP = "team"
PARSER_REL = "team/pgai_agent_kanban/reports/tester_report.py"
ALLOWLIST_REL = "team/config/tester-report-census-allowlist.txt"
TEST_DIR_NAMES = frozenset({"tests", "test"})
SCANNED_SUFFIXES = (".py", ".sh")
FIX = (
    "read the report through pgai_agent_kanban.reports.tester_report "
    "(parse_report, its --json CLI, or its bash wrapper) instead of matching "
    f"report text, or list the path with its reason in {ALLOWLIST_REL}"
)
STALE_FIX = f"remove the entry from {ALLOWLIST_REL}"
REMEDY = "python3 team/scripts/lint_tester_report_census.py"

# ---------------------------------------------------------------------------
# Line normalisation: turn a regex spelling of a pattern back into its text.
# ---------------------------------------------------------------------------

_QUANT = r"(?:[+*?]|\{\d*,?\d*\})?"
# \s+, \s*, \s{1,}, [ ]+, [ \t]*, [[:space:]]*, [[:blank:]]+ and an escaped
# space all mean "whitespace".
_REGEX_SPACE_RE = re.compile(
    r"\\s" + _QUANT
    + r"|\[ (?:\\t)?\]" + _QUANT
    + r"|\[\[:(?:space|blank):\]\]" + _QUANT
    + r"|\\ "
)
# Zero-width escapes carry no text of their own.
_REGEX_ANCHOR_RE = re.compile(r"\\[bBAZ]")


def normalise_line(line: str) -> str:
    """Return *line* with regex whitespace, anchors and escapes taken out.

    ``r"^(#{3,6})\\s+finding\\b"`` becomes ``^(#{3,6}) finding`` and
    ``r"\\*\\*Systemic Risk:\\*\\*"`` becomes ``**Systemic Risk:**``, so one
    set of patterns reads a literal and its regex spelling alike.
    """
    line = _REGEX_SPACE_RE.sub(" ", line)
    line = _REGEX_ANCHOR_RE.sub("", line)
    return line.replace("\\", "")


# ---------------------------------------------------------------------------
# The six patterns, applied to a normalised line.
# ---------------------------------------------------------------------------

# A bold marker as a reader writes it: literal, quantified, or as a class.
_BOLD = r"(?:\*\*|\*\{2\}|\[\*\]\{2\}|\[\*\]\[\*\])"
# Regex grouping punctuation that may sit between a bold marker and a label:
# "(", "(?:", "(?P<name>", "|", "^", ")?" from an optional group.
_LABEL_GAP = r"(?:[\s()|?:^]|P<\w+>){0,12}"
# Whitespace, grouping and class punctuation between a heading's hashes and
# its word: "(#{3,6}) finding", "###[ ]+Finding", "## (?:Recommendation".
_HEADING_GAP = r"(?:[\s()\[\]|?:^+*]|P<\w+>){0,16}"


def _heading_re(min_hashes: int, word: str) -> re.Pattern[str]:
    """Match a heading of *min_hashes* or more hashes that opens with *word*."""
    hashes = rf"(?:#{{{min_hashes},}}|#{{1,{min_hashes}}}(?:\{{\d*,?\d*\}}|[+*]))"
    return re.compile(hashes + _HEADING_GAP + re.escape(word), re.IGNORECASE)


def _field_re(label: str) -> re.Pattern[str]:
    """Match a bold field label: colon inside or outside the bold."""
    return re.compile(
        _BOLD + _LABEL_GAP + label + r"\b(?:\s*\??\s*:|[^*\n]{0,40}?" + _BOLD + ")",
        re.IGNORECASE,
    )


# The patterns are tabulated as parts and their display names rendered from
# them, so this file's own source never spells a pattern and the lint stays
# clean against itself without exempting its own path.
_HEADINGS = ((3, "Finding"), (2, "Recommendation"))
_FIELD_LABELS = ("Systemic Risk", "Fix Effort", "Filed As", "Regression")


def _label_regex(label: str) -> str:
    """``Systemic Risk`` -> ``systemic[\\s_-]+risk`` (any separator run)."""
    return r"[\s_-]+".join(re.escape(word.lower()) for word in label.split())


PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("#" * _HEADINGS[0][0] + " " + _HEADINGS[0][1], _heading_re(*_HEADINGS[0])),
    *((f"**{label}:**", _field_re(_label_regex(label))) for label in _FIELD_LABELS),
    ("#" * _HEADINGS[1][0] + " " + _HEADINGS[1][1], _heading_re(*_HEADINGS[1])),
)


def match_patterns(line: str) -> list[str]:
    """Return the name of every pattern *line* matches, in PATTERNS order."""
    text = normalise_line(line)
    return [name for name, pattern in PATTERNS if pattern.search(text)]


# ---------------------------------------------------------------------------
# Comment masking: comments and docstrings document, they do not parse.
# ---------------------------------------------------------------------------


def _mask_comment_only_lines(lines: list[str]) -> list[str]:
    """Blank every line whose first non-blank character is ``#``."""
    return ["" if line.lstrip().startswith("#") else line for line in lines]


def code_lines_shell(text: str) -> list[str]:
    """Return *text*'s lines with comment-only lines blanked (numbers kept)."""
    return _mask_comment_only_lines(text.splitlines())


def code_lines_python(text: str) -> list[str]:
    """Return *text*'s lines with comments and string statements blanked.

    A string-expression statement (a docstring, or a bare string used as a
    block comment) is blanked line for line; a ``#`` comment is cut from
    the line it sits on. A file that does not parse or tokenize falls back
    to blanking comment-only lines, so a broken file is still scanned.
    """
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, ValueError, tokenize.TokenError, IndentationError):
        return _mask_comment_only_lines(lines)

    masked = list(lines)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            end = getattr(node, "end_lineno", None) or node.lineno
            for number in range(node.lineno, end + 1):
                if number - 1 < len(masked):
                    masked[number - 1] = ""
    for token in tokens:
        if token.type == tokenize.COMMENT:
            row, col = token.start
            if row - 1 < len(masked):
                masked[row - 1] = masked[row - 1][:col]
    return masked


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


class Finding(NamedTuple):
    """One report-parsing match, or one stale allowlist entry."""

    file: str
    line: int
    kind: str
    summary: str

    def to_dict(self) -> dict:
        """Return the finding as the gated runner's per-instance JSON object.

        Keys: ``lint``, ``severity``, ``kind``, ``file``, ``line``, ``summary``, ``remedy``.
        """
        return {
            "lint": LINT_NAME,
            "severity": "quality",
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "summary": self.summary,
            "remedy": REMEDY,
        }


def display_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* in posix form, or as given outside it."""
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def is_excluded(rel: str) -> bool:
    """True for test code and for the parser module (never scanned)."""
    parts = Path(rel).parts
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return parts[-len(Path(PARSER_REL).parts):] == Path(PARSER_REL).parts


def read_allowlist(path: Path) -> list[str]:
    """Return the repository-relative paths *path* lists, in file order.

    Text after ``#`` is a comment, blank lines are skipped, a leading
    ``./`` is dropped. A missing file is an empty list.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    entries: list[str] = []
    for line in raw.splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry.startswith("./"):
            entry = entry[2:]
        if entry and entry not in entries:
            entries.append(entry)
    return entries


def tree_files(root: Path) -> list[Path]:
    """Every *.py / *.sh file under <root>/team, sorted."""
    top = root / SCAN_TOP
    if not top.is_dir():
        return []
    return sorted(
        p for p in top.rglob("*") if p.suffix in SCANNED_SUFFIXES and p.is_file()
    )


def scan_file(path: Path, rel: str) -> list[Finding]:
    """Return a Finding for every code line of *path* matching a pattern."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    code = code_lines_python(text) if path.suffix == ".py" else code_lines_shell(text)
    raw_lines = text.splitlines()
    findings: list[Finding] = []
    for number, line in enumerate(code, start=1):
        if not line.strip():
            continue
        names = match_patterns(line)
        if names:
            source = raw_lines[number - 1].strip()[:120]
            matched = ", ".join(f"'{name}'" for name in names)
            findings.append(
                Finding(rel, number, "report-parsing-outside-parser", f"matches {matched}: {source}")
            )
    return findings


def run(
    root: Path, allowlist: list[str], paths: list[Path] | None = None
) -> tuple[int, list[Finding]]:
    """Scan *root* (or exactly *paths*); return ``(files_scanned, findings)``."""
    whole_tree = paths is None
    candidates = tree_files(root) if whole_tree else paths
    allowed = set(allowlist)
    findings: list[Finding] = []
    allowlisted_hits: dict[str, bool] = {}
    scanned = 0
    for path in candidates:
        if not path.is_file():
            print(f"lint_{LINT_NAME}: skipped {path}: not a file", file=sys.stderr)
            continue
        rel = display_path(path, root)
        if path.suffix not in SCANNED_SUFFIXES or is_excluded(rel):
            continue
        if rel in allowed:
            # An allowlisted file is still read, only to tell whether its
            # entry is still needed; its matches are never reported.
            allowlisted_hits[rel] = bool(scan_file(path, rel))
            continue
        scanned += 1
        findings.extend(scan_file(path, rel))

    if whole_tree:
        for entry in allowlist:
            if not allowlisted_hits.get(entry, False):
                findings.append(
                    Finding(
                        ALLOWLIST_REL,
                        0,
                        "stale-allowlist-entry",
                        f"entry '{entry}' names no scanned file that matches a "
                        "report-parsing pattern",
                    )
                )
    return scanned, findings


def main(argv: list[str] | None = None) -> int:
    """Run the TESTER-report census lint, printing each finding and writing ``--findings-file``.

    Exit codes: 0 always, whatever was found (quality class); 2 usage error
    (argparse) before any scan.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Report TESTER report parsing outside "
            "pgai_agent_kanban/reports/tester_report.py (quality class; always exits 0)."
        )
    )
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="repository root (default: this checkout)",
    )
    parser.add_argument(
        "--paths",
        nargs="+",
        default=None,
        metavar="PATH",
        help="scan exactly these files instead of the whole tree",
    )
    parser.add_argument(
        "--allowlist",
        default=None,
        metavar="PATH",
        help=f"allowlist file (default: <root>/{ALLOWLIST_REL})",
    )
    parser.add_argument("--findings-file", default=None, help="write findings as a JSON array here")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    allowlist_path = Path(args.allowlist) if args.allowlist else root / ALLOWLIST_REL
    allowlist = read_allowlist(allowlist_path)
    paths = [Path(p) for p in args.paths] if args.paths else None

    scanned, findings = run(root, allowlist, paths)
    for finding in findings:
        fix = STALE_FIX if finding.kind == "stale-allowlist-entry" else FIX
        print(f"{LINT_NAME}: {finding.file}:{finding.line}: {finding.summary} -- {fix}")
    print(
        f"lint_{LINT_NAME}: totals: {scanned} file(s) scanned; "
        f"{len(allowlist)} allowlisted; {len(findings)} finding(s)"
    )

    if args.findings_file:
        out = Path(args.findings_file)
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps([f.to_dict() for f in findings], indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"lint_{LINT_NAME}: could not write findings file {out}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
