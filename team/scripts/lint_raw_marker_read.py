#!/usr/bin/env python3
"""
lint_raw_marker_read.py
========================
A structural lint that flags a queue-marker CHARACTER being read back out of
a backlog line and used to make a decision, inside the discovery pipeline's
idle/RC-in-flight decision surface.

WHY THIS EXISTS
---------------
A task's queue-marker (``[ ]``, ``[x]``, ``[W]``, ``[A]``, ``[B]``) is a
rendering hint the wake script writes for humans and dashboards. The one
place a task's real state lives is its own ``status.md`` ``## State`` field
(SOP: "Per-Agent Queues and Marker Correctness" — "the marker is a hint, not
ground truth"). Before this lint existed, the discovery pipeline's chain-idle
and PM-ticket-in-flight gates decided readiness by extracting the marker
character straight out of a backlog line and comparing it to a literal —
exactly the class of bug this project's status-integrity work exists to
close: a marker left stale by an interrupted wake idled the whole chain even
though the task it named had already reached a terminal state. Fixing the
two known call sites does not, by itself, stop a future edit from
reintroducing the same shape. This lint is the structural guard that keeps
it out: CONVERT-class (SOP.md "Gates and Refusals") — it reports a finding
and never blocks a run, because a stray marker-content test is a quality
regression risk, not a runtime failure by itself.

SCOPE
-----
Exactly the two files that make the discovery pipeline's idle/RC-in-flight
decisions: ``team/scripts/lib/discovery_guards.sh`` (the gate primitives)
and ``team/scripts/lib/discovery.sh`` (the driver that calls them). Every
other backlog-marker touchpoint in the tree is deliberately out of scope:
a script that WRITES a marker (``mark_backlog``, ``promote_*_to_backlog`` in
``wake_task_state.sh``), a dashboard that RENDERS one for display, or a
stage file that parses a backlog line to extract something other than the
marker's own content (a task ID, say) is not making the kind of decision
this lint exists to police, and flagging it would be exactly the false
positive that gets a check turned off by the next reader. Pass ``--paths``
to scan a different file set (used by this lint's own unit tests against
synthetic fixtures).

DETECTION
---------
A queue-entry line looks like ``- [MARKER] TASK-ID description``. Extracting
the *task ID* (or any other field after the closing bracket) never reads the
marker's own content, so a bracket regex used purely to skip past it — the
generic, uncaptured, star-quantified form ``\\[[^]]*\\]`` — is not flagged.
A violation is a regex fragment, anywhere in a scoped file, whose brackets
are used to inspect the MARKER'S OWN CONTENT instead of skipping past it:

  1. A capturing group anchored directly inside the brackets —
     ``\\[(...)\\]`` — the textual signature of "extract this marker's
     character(s) into a variable for a decision" (e.g. the pre-fix
     ``\\[([^]]*)\\]`` this lint exists to catch a regression of).
  2. A short, specific marker-content test embedded directly in the
     brackets — a single-any-char wildcard (``\\[.\\]``), a literal marker
     letter (``\\[x\\]``, ``\\[X\\]``, ``\\[W\\]``, ...), or a negated class
     naming specific marker letters (``\\[([^xX])\\]``) — as opposed to the
     generic "whatever is in here, I don't care" skip form.

Comment-only lines (first non-whitespace character ``#``) are excluded, so
prose describing marker shapes in plain brackets (``[x]``, without a
preceding backslash) never trips the scan — the detection only fires on the
literal backslash-escaped regex-source shape (``\\[...\\]``), which appears
only inside an actual pattern literal, never in ordinary comment prose.

EXIT CODES
----------
  0   No violations found (lint is green).
  1   One or more violations found; see stdout for details.
  2   Usage error or a named scan path does not exist.

USAGE
-----
  python3 team/scripts/lint_raw_marker_read.py [OPTIONS]

  --team-dir PATH   Root of the team/ directory (default: parent of this
                     script's directory, i.e. team/ when the script lives
                     at team/scripts/).
  --paths PATH...   Explicit file list to scan instead of the default scope
                     (team/scripts/lib/discovery_guards.sh and
                     team/scripts/lib/discovery.sh under --team-dir).
  --verbose, -v      Print each file as it is checked.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Default scan scope: the discovery pipeline's idle/RC-in-flight decision
# surface. See the module docstring's SCOPE section for why every other
# marker touchpoint in the tree is deliberately excluded from the default.
_DEFAULT_SCOPE_RELATIVE = (
    "scripts/lib/discovery_guards.sh",
    "scripts/lib/discovery.sh",
)

# Case 1: a capturing group anchored directly inside brackets — extracts the
# literal marker content for use elsewhere (e.g. a decision variable). The
# `(?!\?)` guard skips non-capturing groups (`(?:...)`), which are not the
# marker-extraction idiom this case targets.
_MARKER_CAPTURE_RE = re.compile(r"\\\[\((?!\?)[^)]*\)\\\]")

# Case 2: a restrictive marker-content test embedded directly in the
# brackets: a single-char wildcard, a literal marker letter, or a negated
# class of up to 6 characters — as opposed to the generic star-quantified
# skip form ([^]]* / [^]]+) legitimate task-ID extraction uses.
_MARKER_LITERAL_TEST_RE = re.compile(r"\\\[(\.|[a-zA-Z]|\^[^\]]{0,6})\\\]")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    """A single lint violation."""

    path: Path
    line: int
    snippet: str
    kind: str


# ---------------------------------------------------------------------------
# Comment masking (same technique as lint_dashboard_network_git_gate.py:
# exclude documentation prose from the scan while preserving line numbers)
# ---------------------------------------------------------------------------


def _mask_comment_lines(text: str) -> str:
    """Return *text* with every comment-only line blanked out (same length).

    A line is a "comment line" when its first non-whitespace character is
    ``#``. Blanking (rather than deleting) preserves line numbers for
    subsequent regex matches and their derived line-number reporting.
    """
    masked_lines = []
    for line in text.splitlines(keepends=False):
        if line.lstrip().startswith("#"):
            masked_lines.append("")
        else:
            masked_lines.append(line)
    return "\n".join(masked_lines)


def _line_number_for_offset(text: str, offset: int) -> int:
    """Return the 1-based line number containing character *offset* in *text*."""
    return text.count("\n", 0, offset) + 1


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def scan_file_for_raw_marker_reads(path: Path) -> list[Violation]:
    """Scan a single file for raw-marker-read regex shapes.

    Returns a list of Violation entries (empty when the file is clean).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_comment_lines(text)
    raw_lines = text.splitlines()

    violations: list[Violation] = []
    seen_lines: set[int] = set()

    for pattern, kind in (
        (_MARKER_CAPTURE_RE, "marker captured directly out of brackets"),
        (_MARKER_LITERAL_TEST_RE, "marker content tested directly in brackets"),
    ):
        for m in pattern.finditer(masked):
            lineno = _line_number_for_offset(masked, m.start())
            if lineno in seen_lines:
                continue
            seen_lines.add(lineno)
            raw_line = raw_lines[lineno - 1].strip() if lineno - 1 < len(raw_lines) else ""
            violations.append(Violation(path=path, line=lineno, snippet=raw_line, kind=kind))

    violations.sort(key=lambda v: v.line)
    return violations


def scan_paths(paths: list[Path], *, verbose: bool = False) -> list[Violation]:
    """Scan every path in *paths* for raw-marker-read regex shapes."""
    all_violations: list[Violation] = []
    for p in paths:
        if verbose:
            print(f"  [raw-marker-read] {p}")
        all_violations.extend(scan_file_for_raw_marker_reads(p))
    return all_violations


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _build_causes(violations: list[Violation]) -> list[str]:
    return [
        f"{v.path}:{v.line}: {v.kind} — {v.snippet!r}"
        for v in violations
    ]


def _format_remedy_block(violations: list[Violation], *, lint_script: Path) -> str:
    causes = _build_causes(violations)
    lines = [
        f"lint_raw_marker_read: FAIL (quality-class, non-blocking) — {len(violations)} finding(s)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix:")
    lines.append(
        "  1. Replace the flagged marker-content read with a status.md ## State "
        "read for the task the backlog line names (see _disc_task_terminal in "
        "team/scripts/lib/discovery_guards.sh for the established pattern)."
    )
    lines.append(f"  2. python3 \"{lint_script}\"")
    lines.append("")
    lines.append(
        "Rule: the discovery pipeline's idle/RC-in-flight decisions read a "
        "task's own status.md ## State, never a queue-marker character — a "
        "queue marker is a rendering hint, not ground truth (SOP: 'Per-Agent "
        "Queues and Marker Correctness')."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_raw_marker_read",
        description=(
            "Flags a queue-marker character being extracted from a backlog "
            "line and used to make a decision, in the discovery pipeline's "
            "idle/RC-in-flight decision surface."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  No violations found.\n"
            "  1  One or more violations found.\n"
            "  2  Usage error or a named scan path does not exist.\n"
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        default=None,
        help=(
            "Root of the team/ directory (default: parent of this script's "
            "directory, i.e. team/ when the script lives at team/scripts/)."
        ),
    )
    parser.add_argument(
        "--paths",
        metavar="PATH",
        nargs="+",
        default=None,
        help=(
            "Explicit file list to scan instead of the default scope "
            "(discovery_guards.sh and discovery.sh under --team-dir)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Print each file as it is checked.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the raw-marker-read lint and return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.paths:
        scan_targets = [Path(p) for p in args.paths]
        for p in scan_targets:
            if not p.is_file():
                print(f"ERROR: scan path not found: {p}", file=sys.stderr)
                return 2
    else:
        if args.team_dir:
            team_dir = Path(args.team_dir).resolve()
        else:
            # This script lives at team/scripts/lint_raw_marker_read.py.
            team_dir = Path(__file__).resolve().parent.parent
        if not team_dir.is_dir():
            print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
            return 2
        scan_targets = [team_dir / rel for rel in _DEFAULT_SCOPE_RELATIVE]
        missing = [p for p in scan_targets if not p.is_file()]
        if missing:
            print(
                f"ERROR: expected scan file(s) not found: {[str(p) for p in missing]}",
                file=sys.stderr,
            )
            return 2

    if args.verbose:
        print(f"[raw-marker-read] Scanning {[str(p) for p in scan_targets]} ...")

    violations = scan_paths(scan_targets, verbose=args.verbose)

    if violations:
        lint_script = Path(__file__).resolve()
        print("\n" + _format_remedy_block(violations, lint_script=lint_script), flush=True)
        return 1

    print(f"lint_raw_marker_read: OK ({len(scan_targets)} file(s) clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
