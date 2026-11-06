#!/usr/bin/env python3
"""
lint_dashboard_network_git_gate.py
===================================
A lint/grep gate that reports every network-capable git invocation
(``ls-remote``, ``fetch``, ``pull``, ``push``, ``clone``) that appears
anywhere under ``team/scripts/dashboard/`` — no exceptions, including the ``4:git``
window scripts (``git-status.sh``, ``git-recent-tags.sh``).

WHY THIS EXISTS
---------------
The dashboard render path is meant to be strictly local-only: it reads git
state from the local dev tree to display it, and never fetches, pushes, or
otherwise contacts origin from a display process. Support machinery that
refreshes on a timer must not perform agent-class network operations —
doing so risks wedging a fresh environment on an interactive credential
prompt, or silently sending network traffic from what looks like a passive
status display. Behavioral verification of a fixed tree does not stay
fixed on its own — a future edit can silently reintroduce a ``git fetch``
or ``git ls-remote`` call. This lint is the structural guard that makes
such a regression surface as a finding instead of shipping quietly.

SCOPE
-----
Every ``.sh`` file under ``team/scripts/dashboard/``, recursively (so
``team/scripts/dashboard/lib/*.sh`` is included). There are NO exemptions —
not for the ``4:git`` window scripts, not for any file — the dashboard's
design law is that the entire dashboard is local-only, including the git
window itself.

DETECTION
---------
A violation is a non-comment line (or a non-comment logical span across a
backslash line-continuation) that contains a git invocation naming one of
the five network-capable verbs:

    ls-remote | fetch | pull | push | clone

Two patterns are checked:

  1. ``git`` followed — within a handful of tokens (covering flags like
     ``-C <path>``) — by one of the five verbs.  This is the general
     shape every real call site in this codebase takes
     (``git -C "$dev_tree" fetch origin --tags``, ``git ls-remote --tags
     origin``, etc).
  2. A bare ``ls-remote`` token anywhere, since that subcommand name is
     unambiguous even without a preceding literal "git" token in the same
     match window (e.g. a variable or wrapper function name built around
     it).

Comment lines (first non-whitespace character ``#``) are excluded — several
dashboard scripts document their OWN local-only guarantee in prose using
these exact verb words ("no fetch, pull, push, clone, or ls-remote is ever
invoked here"), and flagging that documentation would be a false positive
this lint must not produce.

FINDINGS
--------
CONVERT (quality class, team/SOP-core.md "Gates and Refusals"): a network
verb on the render path is a design-law breach with zero runtime-failure
risk to the run that finds it.  Each violation is printed and recorded as
one finding (file, line, verb); with --findings-file the findings are
written as a JSON array (the shape pgai_agent_kanban.lint.findings
defines), which the gated runners merge per instance with
gated_runner_merge_findings_file.

EXIT CODES
----------
  0   Scan ran, whatever it found (violations are findings, not failures).
  2   Usage error, or the team/ or dashboard/ directory was not found.

USAGE
-----
  python3 team/scripts/lint_dashboard_network_git_gate.py [OPTIONS]

  --team-dir PATH       Root of the team/ directory (default: parent of
                        this script's directory, i.e. team/ when the script
                        lives at team/scripts/).
  --findings-file PATH  Write the findings as a JSON array to PATH (parent
                        directories created; written even when empty).
  --verbose, -v         Print each file as it is checked.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_dashboard_network_git_gate.py

  # With explicit team dir:
  python3 team/scripts/lint_dashboard_network_git_gate.py --team-dir /path/to/team

  # Record findings for a downstream merge:
  python3 team/scripts/lint_dashboard_network_git_gate.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/dashboard_network_git_gate.json"
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# The finding shape and the error-contract emitter live in the team package
# beside scripts/; this script is run directly as well as imported, so the
# package root is put on sys.path from this file's own location.
_TEAM_DIR = Path(__file__).resolve().parent.parent  # team/
if str(_TEAM_DIR) not in sys.path:
    sys.path.insert(0, str(_TEAM_DIR))
from pgai_agent_kanban.lib.error_contract import emit_error_contract  # noqa: E402
from pgai_agent_kanban.lint.findings import (  # noqa: E402
    display_path,
    quality_finding,
    write_findings_file,
)

from pgai_agent_kanban.lint.source_text import mask_comment_lines as _mask_comment_lines  # noqa: E402

_LINT_NAME = "dashboard_network_git_gate"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The five network-capable git verbs forbidden on the dashboard render
# path, with no exceptions.
_NETWORK_VERBS: tuple[str, ...] = ("ls-remote", "fetch", "pull", "push", "clone")

# Pattern 1: "git" followed, within up to 6 intervening whitespace-separated
# tokens (covers flags such as "-C <path>" or "--no-pager"), by one of the
# network-capable verbs.  Loose on purpose: dashboard call sites vary in how
# many flags/args sit between "git" and the subcommand.
_GIT_VERB_RE = re.compile(r"\bgit\b(?:\s+\S+){0,6}?\s+(" + "|".join(_NETWORK_VERBS) + r")\b")

# Pattern 2: a bare "ls-remote" token, unambiguous as a git subcommand name
# regardless of what (if anything) immediately precedes it.
_BARE_LS_REMOTE_RE = re.compile(r"\bls-remote\b")

_DASHBOARD_SUBDIR = "dashboard"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    """A single lint violation."""

    path: Path
    line: int
    snippet: str
    verb: str


# ---------------------------------------------------------------------------
# Comment masking (reused technique from lint_env_bootstrap.py's approach:
# exclude documentation prose from the scan while preserving line numbers)
# ---------------------------------------------------------------------------


def _line_number_for_offset(text: str, offset: int) -> int:
    """Return the 1-based line number containing character *offset* in *text*."""
    return text.count("\n", 0, offset) + 1


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def scan_file_for_network_git(path: Path) -> list[Violation]:
    """Scan a single file for network-capable git invocations.

    Returns a list of Violation entries (empty when the file is clean).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_comment_lines(text)

    violations: list[Violation] = []
    seen_lines: set[int] = set()

    for m in _GIT_VERB_RE.finditer(masked):
        lineno = _line_number_for_offset(masked, m.start())
        if lineno in seen_lines:
            continue
        seen_lines.add(lineno)
        raw_line = text.splitlines()[lineno - 1].strip()
        violations.append(Violation(path=path, line=lineno, snippet=raw_line, verb=m.group(1)))

    for m in _BARE_LS_REMOTE_RE.finditer(masked):
        lineno = _line_number_for_offset(masked, m.start())
        if lineno in seen_lines:
            continue
        seen_lines.add(lineno)
        raw_line = text.splitlines()[lineno - 1].strip()
        violations.append(Violation(path=path, line=lineno, snippet=raw_line, verb="ls-remote"))

    violations.sort(key=lambda v: v.line)
    return violations


def scan_dashboard_dir(dashboard_dir: Path, *, verbose: bool = False) -> list[Violation]:
    """Recursively scan every ``.sh`` file under *dashboard_dir* for violations.

    No file is exempt — including files classified elsewhere as the
    ``4:git`` window (``git-status.sh``, ``git-recent-tags.sh``). The
    dashboard's local-only design law explicitly names those files as in
    scope.
    """
    all_violations: list[Violation] = []

    for sh_path in sorted(dashboard_dir.rglob("*.sh")):
        if not sh_path.is_file():
            continue
        if verbose:
            print(f"  [dashboard-git-gate] {sh_path}")
        all_violations.extend(scan_file_for_network_git(sh_path))

    return all_violations


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _build_causes(violations: list[Violation]) -> list[str]:
    """Return one cause bullet per violation, naming path:line, verb, and snippet."""
    return [f"{v.path}:{v.line}: network-capable git verb '{v.verb}' found — {v.snippet!r}" for v in violations]


def _build_fix_commands(violations: list[Violation], *, team_dir: Path, lint_script: Path) -> list[str]:
    """Return the numbered, verbatim-runnable fix commands for *violations*.

    Each violation gets one single-line ``sed`` command that comments out
    the exact offending line by 1-based line number (``s/^/# /``, prepended
    before any existing indentation) — safe against whatever shell
    metacharacters the offending line itself contains, since the command
    targets the line by number, not by matching its text. A commented line
    is unconditionally treated as non-violating by ``_mask_comment_lines``,
    so this deterministically clears the gate. It is a stop-gap, not the
    final fix: the design law here permits no exemption, so the commented
    call must eventually be replaced with a genuine local-only equivalent —
    but that replacement's logic is authored code no gate can synthesize.

    The final command re-runs this lint with the exact --team-dir used at
    detection time, proving the fix cleared the gate.
    """
    commands = [f"sed -i '{v.line}s/^/# /' \"{v.path}\"" for v in violations]
    commands.append(f'python3 "{lint_script}" --team-dir "{team_dir}"')
    return commands


def _format_remedy_block(violations: list[Violation], *, team_dir: Path, lint_script: Path) -> str:
    """Assemble the full three-part remedy block required by
    docs/coding-standards.md ("Gates prescribe, not just diagnose"): cause(s)
    with concrete location, a numbered fix block of verbatim-runnable
    commands ending with the re-lint check, then the one-line rule the gate
    enforces.
    """
    causes = _build_causes(violations)
    commands = _build_fix_commands(violations, team_dir=team_dir, lint_script=lint_script)

    lines = [
        f"lint_dashboard_network_git_gate: {len(violations)} violation(s)",
        "",
        "Cause(s):",
    ]
    for cause in causes:
        lines.append(f"  - {cause}")
    lines.append("")
    lines.append("To fix:")
    for i, command in enumerate(commands, start=1):
        lines.append(f"  {i}. {command}")
    lines.append("")
    lines.append(
        "Note: this comments out the offending line as a deterministic "
        "stop-gap that clears the gate immediately. There is no exemption "
        "mechanism for this gate — replace the commented line with the "
        "intended local-only equivalent (no network-capable git verb) "
        "before shipping; a permanently dead network call is not the "
        "design intent either."
    )
    lines.append("")
    lines.append(
        "Rule: team/scripts/dashboard/ is local-only — no network-capable "
        "git verb (ls-remote|fetch|pull|push|clone) may appear anywhere "
        "under it, with no exemptions, including the git-window scripts."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_dashboard_network_git_gate",
        description=(
            "Reports every network-capable git invocation "
            "(ls-remote|fetch|pull|push|clone) anywhere under "
            "team/scripts/dashboard/ — no exceptions, including the 4:git window. "
            "Quality class: each violation is a finding, and the lint exits 0 "
            "whatever it finds."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  Scan ran, whatever it found (violations are findings).\n"
            "  2  Usage error or directory not found.\n"
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
        "--findings-file",
        metavar="PATH",
        default=None,
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created; written even when empty).  Findings are printed either way."
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


def _findings(violations: list[Violation], *, team_dir: Path, lint_script: Path) -> list[dict]:
    """One quality finding per violation, each carrying its own runnable
    remedy: the stop-gap ``sed`` that comments the line out, then the re-check."""
    repo_root = team_dir.parent
    relint = f'python3 "{lint_script}" --team-dir "{team_dir}"'
    return [
        quality_finding(
            _LINT_NAME,
            f"{display_path(v.path, repo_root)}:{v.line}: network-capable git verb "
            f"'{v.verb}' on the dashboard render path -- {v.snippet!r}",
            f"sed -i '{v.line}s/^/# /' \"{v.path}\" && {relint}",
            file=display_path(v.path, repo_root),
            line=v.line,
            verb=v.verb,
        )
        for v in violations
    ]


def main(argv: list[str] | None = None) -> int:
    """Run the dashboard network-git gate.

    Exit codes: 0 whatever was found (each violation is a quality finding,
    printed and written to --findings-file); 2 usage error, or the team/ or
    dashboard/ directory not found.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        # This script lives at team/scripts/lint_dashboard_network_git_gate.py.
        team_dir = Path(__file__).resolve().parent.parent

    if not team_dir.is_dir():
        print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
        return 2

    dashboard_dir = team_dir / "scripts" / _DASHBOARD_SUBDIR
    if not dashboard_dir.is_dir():
        print(f"ERROR: dashboard directory not found: {dashboard_dir}", file=sys.stderr)
        return 2

    if args.verbose:
        print(f"[dashboard-git-gate] Scanning {dashboard_dir} ...")

    violations = scan_dashboard_dir(dashboard_dir, verbose=args.verbose)
    lint_script = Path(__file__).resolve()
    findings = _findings(violations, team_dir=team_dir, lint_script=lint_script)
    write_findings_file(args.findings_file, findings, lint="lint_dashboard_network_git_gate")

    if violations:
        print(
            "\n" + _format_remedy_block(violations, team_dir=team_dir, lint_script=lint_script),
            flush=True,
        )
        # CENSUS-ANCHOR: dashboard-network-git-gate-01
        # CONVERT: a network verb on the render path is a design-law breach
        # with zero runtime-failure risk -- recorded as findings; returns 0.
        emit_error_contract(
            "lint.dashboard-network-git",
            f"{len(findings)} network-capable git invocation(s) under {dashboard_dir}; "
            "the dashboard render path must stay local-only. Each is listed above.",
            findings[0]["remedy"],
        )
        return 0

    print(f"lint_dashboard_network_git_gate: OK ({dashboard_dir} is local-only clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
