#!/usr/bin/env python3
"""
skip_bug_citation_scan.py -- structural (AST-based) skip-citation scanner
for Python test files.

lint_skip_bug_gate.sh enforces two things about every skip annotation that
cites a follow-up bug: (1) the citation must not be a placeholder ID, and
(2) any real BUG-NNNN it cites must have a matching file in bugs/. Both
checks used to be line-oriented greps applied uniformly to every file
under team/tests/, Python included. A grep sees text, not code, so it was
wrong in both directions on the Python surface: its character class
cannot cross a newline, so a skip call whose citation sits on a
continuation line was invisible to it; and its "reason=" alternative
matched that keyword anywhere on a line, so a fixture's docstring or a
piece of sample data that happens to mention a bug-shaped identifier was
flagged as if it were an executed skip.

This module fixes that for the Python surface only. It parses each *.py
file with the standard-library `ast` module and identifies the actual
skip statements -- `pytest.skip(...)`, a bare `skip(...)` (e.g. via
`from pytest import skip`), and `pytest.mark.skip(...)` /
`pytest.mark.skipif(...)` decorators -- as AST nodes, whatever line shape
their arguments are written in. Only the source text belonging to one of
those nodes is a candidate for a citation; a string or comment that
merely mentions a bug identifier elsewhere in the file is never examined,
because it is never the argument list of a skip call.

Shell (*.sh) and Markdown (*.md) test-tree surfaces have no AST. They are
not this module's concern -- lint_skip_bug_gate.sh keeps its original
line-oriented grep for everything that is not a *.py file. See that
script's own comments for why grep remains the right tool there.

Usage (invoked by lint_skip_bug_gate.sh; not intended to be run
interactively, though it is a normal, arguments-only CLI):

    python3 skip_bug_citation_scan.py --tests-dir PATH --mode {placeholder,citation}
    python3 skip_bug_citation_scan.py --files PATH [PATH ...] --mode {placeholder,citation}

--tests-dir walks the directory for every *.py file (skipping any
directory named by --exclude-dir, default artifacts). --files is
named-file mode: it scans exactly the paths given, whatever their suffix
or location, and walks nothing -- the caller has already chosen the
files. Exactly one of the two is required.

Output: one `path:lineno:content` line per finding, on stdout -- the same
shape `grep -rn` produces, so the calling gate's existing hit-counting
(`wc -l`) and ID-extraction (`grep -oE 'BUG-[0-9]{4,}'`) work unchanged
against this module's output.

Exit codes: 0 on a completed scan (findings, if any, are on stdout -- this
module never itself decides pass/fail; the calling gate does). 2 if
--tests-dir does not exist, or if any --files path is not an existing
regular file.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from pathlib import Path

# A skip statement is a Call whose function resolves to one of these
# names, however it is referenced:
#   pytest.skip(...)        -> Attribute(attr="skip")
#   pytest.mark.skip(...)   -> Attribute(attr="skip")
#   pytest.mark.skipif(...) -> Attribute(attr="skipif")
#   skip(...)               -> Name(id="skip")            (bare import)
_SKIP_CALL_NAMES = {"skip", "skipif"}

# GATE 1 -- placeholder citation: BUG-SKIP, or a BUG- ID whose suffix is
# a letter rather than a digit (an informal identifier standing in for
# the canonical BUG-NNNN form). Case-sensitive, matching the line-oriented
# gate's original pattern (no -i flag).
PLACEHOLDER_CITATION_RE = re.compile(r"BUG-SKIP|BUG-[A-Za-z]")

# GATE 2 -- a real BUG-NNNN citation. Case-insensitive, matching the
# line-oriented gate's original pattern (-i flag).
REAL_BUG_ID_RE = re.compile(r"BUG-[0-9]{4,}", re.IGNORECASE)

_MODE_PATTERNS = {
    "placeholder": PLACEHOLDER_CITATION_RE,
    "citation": REAL_BUG_ID_RE,
}


class SkipCitationFinding:
    """One skip statement whose citation text matched a gate's pattern."""

    __slots__ = ("path", "lineno", "snippet")

    def __init__(self, path: Path, lineno: int, snippet: str) -> None:
        self.path = path
        self.lineno = lineno
        self.snippet = snippet

    def render(self) -> str:
        """Render as `path:lineno:content`, matching `grep -rn` output."""
        return f"{self.path}:{self.lineno}:{self.snippet}"


def _iter_skip_call_nodes(tree: ast.AST):
    """Yield every ast.Call node in *tree* that is a skip statement.

    ast.walk descends into every reachable node -- module body, nested
    function bodies, `if` branches, and decorator lists alike -- so a
    decorator skip (`@pytest.mark.skipif(...)`) and an inline conditional
    skip (`if x: pytest.skip(...)`) are found the same way a bare
    top-level call would be. Line shape never enters into it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _SKIP_CALL_NAMES:
            yield node
        elif isinstance(func, ast.Name) and func.id in _SKIP_CALL_NAMES:
            yield node


def _flatten(segment: str) -> str:
    """Collapse a (possibly multi-line) source segment onto one line.

    Used only to build a readable diagnostic string; citation detection
    itself runs against the original, un-flattened segment so it does not
    matter which physical line within the call the citation sits on.
    """
    return " ".join(segment.split())


def scan_source(path: Path, source: str, mode: str) -> list[SkipCitationFinding]:
    """Return every skip statement in *source* whose citation text matches
    the pattern for *mode* ("placeholder" or "citation").

    A SyntaxError in *source* yields no findings for this file -- a file
    this scanner cannot parse structurally is not a file it can reason
    about structurally, and one unparseable file must not silence
    findings in every other file the caller scans.
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    pattern = _MODE_PATTERNS[mode]
    findings: list[SkipCitationFinding] = []
    for node in _iter_skip_call_nodes(tree):
        segment = ast.get_source_segment(source, node)
        if segment is None or not pattern.search(segment):
            continue
        findings.append(SkipCitationFinding(path, node.lineno, _flatten(segment)))
    return findings


def iter_python_files(tests_dir: Path, exclude_dirs: set[str]):
    """Yield every *.py file under *tests_dir*, skipping *exclude_dirs*.

    Mirrors `grep -r --exclude-dir=NAME`: a directory named exactly one of
    exclude_dirs is skipped wherever it occurs in the tree, not only at
    the top level.
    """
    for dirpath, dirnames, filenames in os.walk(tests_dir):
        dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield Path(dirpath) / name


def scan_tree(
    tests_dir: Path, mode: str, exclude_dirs: set[str]
) -> list[SkipCitationFinding]:
    """Return the skip-citation findings for every Python file under *tests_dir*.

    Files are scanned in sorted order with :func:`scan_source` in *mode*;
    directories named in *exclude_dirs* are skipped, and an unreadable file
    is passed over.
    """
    findings: list[SkipCitationFinding] = []
    for filepath in sorted(iter_python_files(tests_dir, exclude_dirs)):
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_source(filepath, source, mode))
    return findings


def scan_files(paths: list[Path], mode: str) -> list[SkipCitationFinding]:
    """Scan exactly *paths*, in the order given, for skip citations.

    Named-file mode: no directory is walked and no exclusion applies --
    the caller has already chosen which files to check. A file that
    cannot be read yields no findings, matching scan_tree().
    """
    findings: list[SkipCitationFinding] = []
    for filepath in paths:
        try:
            source = filepath.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        findings.extend(scan_source(filepath, source, mode))
    return findings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="skip_bug_citation_scan.py",
        description=(
            "Structurally scan *.py files under --tests-dir (or exactly the "
            "--files paths) for skip "
            "statements (pytest.skip / mark.skip / mark.skipif / bare "
            "skip()) whose citation text matches the pattern for --mode. "
            "Invoked by lint_skip_bug_gate.sh for the Python surface only."
        ),
    )
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tests-dir", metavar="PATH", help="Directory to scan.")
    scope.add_argument(
        "--files",
        metavar="PATH",
        nargs="+",
        help=(
            "Named-file mode: scan exactly these files (no directory walk, "
            "no --exclude-dir filtering). Each must be an existing file."
        ),
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=sorted(_MODE_PATTERNS),
        help=(
            "placeholder: BUG-SKIP or an informal (non-numeric) BUG- suffix. "
            "citation: a real BUG-NNNN citation."
        ),
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=None,
        metavar="NAME",
        help="Directory name to skip (repeatable). Default: artifacts.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the skip-citation scan and print each finding.

    The scan never decides pass/fail itself; the calling gate does. Exit
    codes: 0 the scan completed (findings, if any, are on stdout); 2
    ``--tests-dir`` does not exist, or a ``--files`` path is not an existing
    regular file.
    """
    args = _parse_args(argv)
    if args.files is not None:
        missing = [raw for raw in args.files if not Path(raw).is_file()]
        if missing:
            print(
                "skip_bug_citation_scan.py: ERROR: --files path(s) not found "
                "or not a regular file: " + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        for finding in scan_files([Path(raw) for raw in args.files], args.mode):
            print(finding.render())
        return 0

    tests_dir = Path(args.tests_dir)
    if not tests_dir.is_dir():
        print(
            f"skip_bug_citation_scan.py: ERROR: tests directory not found: {tests_dir}",
            file=sys.stderr,
        )
        return 2

    exclude_dirs = set(args.exclude_dir) if args.exclude_dir else {"artifacts"}
    for finding in scan_tree(tests_dir, args.mode, exclude_dirs):
        print(finding.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
