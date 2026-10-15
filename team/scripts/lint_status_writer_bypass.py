#!/usr/bin/env python3
"""
lint_status_writer_bypass.py
==============================
A structural lint that flags a task's ``## State``, ``## Needs Human``, ``##
Summary``, or ``## Blockers`` field being mutated directly — via a regex
substitution or an in-place stream edit — in an agent-facing script, instead
of going through the status-writer tool
(``team/pgai_agent_kanban/task_status.py`` / ``team/scripts/task-status.sh``).

WHY THIS EXISTS
---------------
``task_status.py`` exists to be the single sanctioned mutator of these four
fields: it validates ``--state`` against the canonical six-value vocabulary,
writes atomically, and preserves every other section byte-for-byte. That
guarantee only holds while every agent-facing code path actually calls it.
A future edit that reaches for a bespoke ``re.sub``/``sed -i`` to poke one of
these fields directly — in a script an agent invokes as part of its own
documented procedure — silently reopens the small-model reformatting
failure the tool was built to close, and this lint is the structural guard
that keeps that regression out. CONVERT-class (``team/SOP-core.md`` "Gates
and Refusals"): it reports a finding and never blocks a run, because a
stray direct-mutation call site is a quality/maintainability regression
risk, not a runtime failure by itself.

SCOPE
-----
Default scan surface: every ``*.sh`` file directly under ``team/scripts/``
(non-recursive) — the flat, agent-invocable top-level script surface every
role file's documented Procedure references (``bash team/scripts/<name>.sh
...``). This deliberately excludes:

  - ``team/scripts/lib/**`` — the wake substrate (``wake_task_state.sh``,
    ``wake_lifecycle.sh``, ``wake_driver*.sh``, and siblings). These run as
    part of the wake harness's own automatic queue/state bookkeeping around
    an agent's turn (BACKLOG/WORKING promotion, model stamping, queue-marker
    sync) — the framework's own substrate, not something an agent itself
    invokes. They predate this tool and are out of scope for conversion
    here; team/SOP-core.md's "Gates and Refusals" governs that layer.
  - ``team/scripts/cm/**`` and ``team/pgai_agent_kanban/**`` — CM release
    internals and the Python ops/API library are invoked by other scripts or
    by the dashboard/REST layer, never directly by an agent's own procedure.
  - Two named top-level exemptions (``_EXEMPT_BASENAMES`` below):

      * ``task-status.sh`` — the canonical wrapper this lint exists to
        route everything else through; it is the exception, not a bypass.
      * ``unwind-rc.sh`` — an operator-only bulk RC-cancellation script (its
        own header names it "Operator script:"; ``docs/operator-commands.md``
        and ``docs/operator-api.md`` are its only documented entry points).
        It flips MANY task folders to WONT-DO as one administrative sweep
        when an RC is unwound — a different operation than an agent editing
        its own single task's status — and sits in the same operator-CLI
        exclusion class this codebase already applies to ``show.sh``,
        ``delete.sh``, ``close.sh``, and ``wontdo.sh``
        (``docs/blocking-site-census.md``, "Scope decision (documented)").

Pass ``--paths`` to scan a different, explicit file list instead (used by
this lint's own unit tests against synthetic fixtures).

DETECTION
---------
A violation is a literal occurrence of one of the four managed heading
strings — ``## State``, ``## Needs Human``, ``## Summary``, ``## Blockers``
— appearing within ``_WINDOW_LINES`` lines of a mutating operation: a Python
``re.sub(``/``re.subn(``/``.subn(`` call, or a bash ``sed -i``/``sed
--in-place`` invocation. A file that only *reads* one of these headings (a
bare ``re.search``/``re.match``, an ``awk`` scan, a plain ``grep``) is not
flagged — reading a task's own state to make a decision is not the failure
mode this lint exists to catch; the tool it protects is a WRITE path, not a
read path. A file that writes a brand-new status.md from scratch (a
heredoc ``cat > "$status_file" <<EOF`` template, used at task-creation time
by ``pm-agent.sh`` and by this lint's own test fixtures) is likewise not
flagged: task_status.py itself "does not create task folders" (see its
module docstring) — creating a new file is a different operation from
mutating an existing one, and carries no mutating-regex signal for this
scan to trip on.

Comment-only lines (first non-whitespace character ``#``) are masked before
scanning, so prose describing the four heading names in isolation — without
a nearby mutating call — never trips the scan.

EXIT CODES
----------
  0   No violations found (lint is green).
  1   One or more violations found; see stdout for details.
  2   Usage error or a named scan path does not exist.

USAGE
-----
  python3 team/scripts/lint_status_writer_bypass.py [OPTIONS]

  --team-dir PATH   Root of the team/ directory (default: parent of this
                     script's directory, i.e. team/ when the script lives
                     at team/scripts/).
  --paths PATH...   Explicit file list to scan instead of the default scope
                     (every team/scripts/*.sh file, minus the exemptions
                     named in the SCOPE section above).
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

# Two named exemptions from the default flat team/scripts/*.sh scan surface.
# See the module docstring's SCOPE section for the rationale behind each.
_EXEMPT_BASENAMES = frozenset({
    "task-status.sh",
    "unwind-rc.sh",
})

# The four fields team_status.py manages, and no other agent-facing code
# path may mutate directly. Matches the exact heading text (case-sensitive,
# word-bounded so "## Summary" does not also match a hypothetical
# "## Summary Extended") task_status.py's own _set_section_body regex uses.
_MANAGED_HEADINGS = ("State", "Needs Human", "Summary", "Blockers")
_HEADING_RE = re.compile(
    r"##\s+(?:" + "|".join(re.escape(h) for h in _MANAGED_HEADINGS) + r")\b"
)

# A mutating operation: Python re.sub/re.subn/<pattern>.subn(, or a bash
# in-place stream edit. Deliberately excludes re.search/re.match/re.findall
# — a read is not the failure mode this lint exists to catch.
_MUTATION_SIGNAL_RE = re.compile(
    r"re\.sub\(|re\.subn\(|\.subn\(|sed\s+-i|sed\s+--in-place"
)

# How many lines (in either direction) a mutation signal must appear within
# of a managed-heading occurrence to be treated as the same edit. Generous
# enough to span a multi-line regex-pattern literal assigned above its own
# re.sub(...) call, narrow enough that an unrelated heredoc template and an
# unrelated sed call elsewhere in the same file do not co-trigger a finding.
_WINDOW_LINES = 4


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
# Comment masking (same technique as lint_raw_marker_read.py /
# lint_dashboard_network_git_gate.py: exclude documentation prose from the
# scan while preserving line numbers)
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


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def scan_file_for_bypass(path: Path) -> list[Violation]:
    """Scan a single file for a direct managed-field mutation.

    Returns a list of Violation entries (empty when the file is clean).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    masked_lines = _mask_comment_lines(text).splitlines()
    raw_lines = text.splitlines()

    heading_lines = [
        i for i, line in enumerate(masked_lines) if _HEADING_RE.search(line)
    ]
    mutation_lines = [
        i for i, line in enumerate(masked_lines) if _MUTATION_SIGNAL_RE.search(line)
    ]
    if not heading_lines or not mutation_lines:
        return []

    violations: list[Violation] = []
    reported: set[int] = set()
    for h_idx in heading_lines:
        nearby = any(
            abs(h_idx - m_idx) <= _WINDOW_LINES for m_idx in mutation_lines
        )
        if not nearby or h_idx in reported:
            continue
        reported.add(h_idx)
        snippet = raw_lines[h_idx].strip() if h_idx < len(raw_lines) else ""
        violations.append(
            Violation(
                path=path,
                line=h_idx + 1,
                snippet=snippet,
                kind="managed status field mutated directly, bypassing task_status.py",
            )
        )

    violations.sort(key=lambda v: v.line)
    return violations


def scan_paths(paths: list[Path], *, verbose: bool = False) -> list[Violation]:
    """Scan every path in *paths* for a direct status-writer bypass."""
    all_violations: list[Violation] = []
    for p in paths:
        if verbose:
            print(f"  [status-writer-bypass] {p}")
        all_violations.extend(scan_file_for_bypass(p))
    return all_violations


def _default_scan_targets(team_dir: Path) -> list[Path]:
    """Every team/scripts/*.sh file, minus the named exemptions."""
    scripts_dir = team_dir / "scripts"
    return sorted(
        p
        for p in scripts_dir.glob("*.sh")
        if p.name not in _EXEMPT_BASENAMES
    )


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _build_causes(violations: list[Violation]) -> list[str]:
    return [f"{v.path}:{v.line}: {v.kind} — {v.snippet!r}" for v in violations]


def _format_remedy_block(violations: list[Violation], *, lint_script: Path) -> str:
    causes = _build_causes(violations)
    lines = [
        f"lint_status_writer_bypass: FAIL (quality-class, non-blocking) — {len(violations)} finding(s)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix:")
    lines.append(
        "  1. Replace the flagged direct mutation with a call to the status-writer "
        "tool: bash team/scripts/task-status.sh --project <name> --key <task-id> "
        "--state <STATE> [--needs-human yes|no] [--summary TEXT] [--blockers TEXT]."
    )
    lines.append(f"  2. python3 \"{lint_script}\"")
    lines.append("")
    lines.append(
        "Rule: ## State, ## Needs Human, ## Summary, and ## Blockers are mutated "
        "through team/pgai_agent_kanban/task_status.py only — the single "
        "sanctioned writer that validates --state, writes atomically, and "
        "preserves every other section byte-for-byte (team/SOP-core.md "
        "'Standard Task States')."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_status_writer_bypass",
        description=(
            "Flags a task's ## State, ## Needs Human, ## Summary, or ## "
            "Blockers field being mutated directly in an agent-facing "
            "script, instead of through team/pgai_agent_kanban/task_status.py."
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
            "(every team/scripts/*.sh file under --team-dir, minus the "
            "named exemptions)."
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
    """Run the status-writer-bypass lint and return an exit code."""
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
            # This script lives at team/scripts/lint_status_writer_bypass.py.
            team_dir = Path(__file__).resolve().parent.parent
        if not team_dir.is_dir():
            print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
            return 2
        scan_targets = _default_scan_targets(team_dir)
        if not scan_targets:
            print(
                f"ERROR: no team/scripts/*.sh files found under {team_dir}",
                file=sys.stderr,
            )
            return 2

    if args.verbose:
        print(f"[status-writer-bypass] Scanning {[str(p) for p in scan_targets]} ...")

    violations = scan_paths(scan_targets, verbose=args.verbose)

    if violations:
        lint_script = Path(__file__).resolve()
        print("\n" + _format_remedy_block(violations, lint_script=lint_script), flush=True)
        return 1

    print(f"lint_status_writer_bypass: OK ({len(scan_targets)} file(s) clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
