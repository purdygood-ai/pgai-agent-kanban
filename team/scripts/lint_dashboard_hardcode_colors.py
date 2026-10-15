#!/usr/bin/env python3
"""
lint_dashboard_hardcode_colors.py
==================================
A lint/grep gate that fails when a raw ANSI escape sequence literal
(``\\033[...m``) or raw tmux ``#[fg=...]`` markup literal appears anywhere
under ``team/scripts/dashboard/`` or ``team/scripts/lib/dashboard/``.

WHY THIS EXISTS
---------------
The single-source semantic dashboard palette
(``team/config/default_palette.txt``, ``team/scripts/lib/dashboard_palette.sh``,
``pgai_agent_kanban.lib.palette``) exists so every dashboard renderer looks
up a color by name (a base ANSI color name via ``ansi_code()``, or a
task/entry color MEANING via ``dashboard_semantic_color()`` /
``dashboard_semantic_tmux_fg()``) instead of hand-transcribing its own copy
of the escape-code table. A renderer that writes a raw escape literal
directly bypasses that single source and can silently drift from it (a
`kanban.cfg` color override changing the shared table's output while a
hand-written literal stays frozen at whatever byte sequence someone typed
once). Behavioral verification of a fixed tree does not stay fixed on its
own — a future edit can reintroduce a raw literal. This lint is the
structural guard that makes such a regression fail CI instead of shipping
quietly.

CLASSIFICATION
--------------
CONVERT. A raw hardcoded color literal renders correctly today regardless
of whether it is present as a literal or resolved through the shared
primitive — the shipped artefact's byte output is unaffected either way,
so this is zero runtime-failure risk. The cost is exclusively to the
single-source design (drift risk against future palette/config changes),
exactly the class of finding the gated runner's quality-class lane exists
to carry forward without stopping the loop (team/SOP-core.md, "Gates and
Refusals" and "The Loop"). This lint's own process exit code is therefore
never treated as fatal by its caller: ``gated_runner_run_quality_lint``
records a non-zero exit as one finding and continues to pytest regardless.

SCOPE
-----
Every ``.sh`` and ``.py`` file under ``team/scripts/dashboard/`` and
``team/scripts/lib/dashboard/``, recursively. The two canonical homes for
the actual escape-code tables this lint exists to protect —
``team/scripts/lib/script_common.sh`` (ansi_code()) and
``pgai_agent_kanban/lib/palette.py`` (the Python ansi_code()/semantic-key
readers) — sit OUTSIDE both scanned roots by design, so they are never
scanned and never need an exemption.

A file whose path contains a ``fixtures`` or ``golden`` path segment, or
whose name matches ``test_*.py`` / ``*_test.py``, is exempt — per the
ticket's own constraint that "the hardcode lint must allow escapes in test
fixtures and golden files." No dashboard renderer script under either
scanned root currently lives in such a path; the exemption exists so a
future test fixture placed alongside a renderer (for example, a golden
transcript of expected colored output) is never mistaken for a renderer
that needs converting.

DETECTION
---------
A violation is a non-comment line containing either:

  1. A raw ANSI SGR escape sequence literal — the literal source text
     ``\\033[`` followed by digits/semicolons and a closing ``m`` (matches
     bash ``$'\\033[0;31m'``/``"\\033[0;31m"`` and Python ``"\\033[0;31m"``
     alike, since both spellings place that literal text in the file).
     A dynamically-built truecolor escape such as
     ``f"\\033[38;2;{r};{g};{b}m"`` does NOT match, because the
     interpolation braces break the required digits/semicolons-only
     pattern between ``[`` and ``m`` — exactly the class of legitimate,
     hex-driven, already-single-sourced project-tag color construction
     this lint must not flag (see team/scripts/lib/dashboard/
     column_render_frame.sh's ``project_tag_color()`` for the real
     example this exemption protects).
  2. A raw tmux ``#[fg=...]`` markup literal.

Comment lines (first non-whitespace character ``#``) are excluded — this
lint's own module docstring, and several renderer scripts' inline
comments, document the escape/markup shapes above in prose, and flagging
that documentation would be a false positive this lint must not produce.

EXIT CODES
----------
  0   No violations found (lint is green).
  1   One or more violations found; see stdout for details.
  2   Usage error or the specified directory was not found.

USAGE
-----
  python3 team/scripts/lint_dashboard_hardcode_colors.py [OPTIONS]

  --team-dir PATH   Root of the team/ directory (default: parent of this
                     script's directory, i.e. team/ when the script lives
                     at team/scripts/).
  --verbose, -v      Print each file as it is checked.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_dashboard_hardcode_colors.py

  # With explicit team dir:
  python3 team/scripts/lint_dashboard_hardcode_colors.py --team-dir /path/to/team
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

# Raw ANSI SGR escape literal: the literal source text "\033[" followed by
# one or more digits/semicolons and a closing "m". Requires the digits/
# semicolons-only body so an f-string truecolor build
# (f"\033[38;2;{r};{g};{b}m") never matches -- the interpolation braces are
# not in the [0-9;] class.
_RAW_ANSI_ESCAPE_RE = re.compile(r"\\033\[[0-9;]+m")

# Raw tmux fg= markup literal: "#[fg=<anything but ]>]".
_RAW_TMUX_FG_RE = re.compile(r"#\[fg=[^\]]*\]")

_SCANNED_SUBPATHS: tuple[str, ...] = (
    "scripts/dashboard",
    "scripts/lib/dashboard",
)

_EXEMPT_PATH_SEGMENTS: tuple[str, ...] = ("fixtures", "golden")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    """A single lint violation."""

    path: Path
    line: int
    snippet: str
    kind: str  # "ansi_escape" | "tmux_fg"


# ---------------------------------------------------------------------------
# Comment masking (same technique as lint_dashboard_network_git_gate.py:
# exclude documentation prose from the scan while preserving line numbers)
# ---------------------------------------------------------------------------


def _mask_comment_lines(text: str) -> str:
    """Return *text* with every comment-only line blanked out (same length).

    A line is a "comment line" when its first non-whitespace character is
    ``#`` -- the shared line-comment convention both bash and Python use,
    matching team/config/default_palette.txt's own documented parse rule.
    Blanking (rather than deleting) preserves line numbers for subsequent
    regex matches and their derived line-number reporting.
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


def _is_exempt(path: Path) -> bool:
    """Return True when *path* is a test-fixture or golden-file location.

    Per the ticket's constraint: "the hardcode lint must allow escapes in
    test fixtures and golden files." Matches a ``fixtures``/``golden`` path
    segment anywhere in the path, or a ``test_*.py``/``*_test.py`` filename.
    """
    if any(segment in _EXEMPT_PATH_SEGMENTS for segment in path.parts):
        return True
    name = path.name
    if name.startswith("test_") and name.endswith(".py"):
        return True
    if name.endswith("_test.py"):
        return True
    return False


# ---------------------------------------------------------------------------
# Core scan
# ---------------------------------------------------------------------------


def scan_file_for_hardcoded_colors(path: Path) -> list[Violation]:
    """Scan a single file for raw ANSI escape / tmux fg= markup literals.

    Returns a list of Violation entries (empty when the file is clean).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    masked = _mask_comment_lines(text)
    raw_lines = text.splitlines()

    violations: list[Violation] = []
    seen: set[tuple[int, str]] = set()

    for pattern, kind in ((_RAW_ANSI_ESCAPE_RE, "ansi_escape"), (_RAW_TMUX_FG_RE, "tmux_fg")):
        for m in pattern.finditer(masked):
            lineno = _line_number_for_offset(masked, m.start())
            key = (lineno, kind)
            if key in seen:
                continue
            seen.add(key)
            snippet = raw_lines[lineno - 1].strip() if lineno - 1 < len(raw_lines) else ""
            violations.append(Violation(path=path, line=lineno, snippet=snippet, kind=kind))

    violations.sort(key=lambda v: v.line)
    return violations


def scan_dashboard_dirs(team_dir: Path, *, verbose: bool = False) -> list[Violation]:
    """Recursively scan every in-scope file under both dashboard roots.

    Returns an empty list (a legitimate green, not a skip) when neither
    scanned root exists -- callers that need to distinguish "clean" from
    "nothing to scan" should check the roots themselves before calling.
    """
    all_violations: list[Violation] = []

    for subpath in _SCANNED_SUBPATHS:
        root = team_dir / subpath
        if not root.is_dir():
            continue
        candidates = sorted(root.rglob("*.sh")) + sorted(root.rglob("*.py"))
        for file_path in sorted(candidates):
            if not file_path.is_file():
                continue
            if _is_exempt(file_path):
                if verbose:
                    print(f"  [dashboard-hardcode-colors] (exempt) {file_path}")
                continue
            if verbose:
                print(f"  [dashboard-hardcode-colors] {file_path}")
            all_violations.extend(scan_file_for_hardcoded_colors(file_path))

    return all_violations


# ---------------------------------------------------------------------------
# Remedy block (docs/coding-standards.md "Gates prescribe, not just diagnose")
# ---------------------------------------------------------------------------


def _build_causes(violations: list[Violation]) -> list[str]:
    """Return one cause bullet per violation, naming path:line, kind, and snippet."""
    kind_label = {"ansi_escape": "raw ANSI escape literal", "tmux_fg": "raw tmux #[fg=...] markup literal"}
    return [
        f"{v.path}:{v.line}: {kind_label[v.kind]} found — {v.snippet!r}"
        for v in violations
    ]


def _build_fix_commands(
    violations: list[Violation], *, team_dir: Path, lint_script: Path
) -> list[str]:
    """Return the numbered, verbatim-runnable fix commands for *violations*.

    Unlike lint_dashboard_network_git_gate.py's "comment it out" stop-gap,
    there is no safe mechanical rewrite for a color literal -- the correct
    fix depends on which semantic meaning (or base color name) the literal
    represents, which only a human/agent reading the surrounding code can
    decide. The remedy therefore names the shared primitives to call
    instead of prescribing a sed rewrite, and ends with the same re-lint
    verification step every gate in this codebase ends with.
    """
    commands = [
        "For each violation above, replace the raw literal with a call "
        "to the shared primitive: ansi_code(<name>) for a base color "
        "(team/scripts/lib/script_common.sh, or "
        "pgai_agent_kanban.lib.palette.ansi_code in a Python heredoc), or "
        "dashboard_semantic_color(<key>) / dashboard_semantic_tmux_fg(<key>) "
        "(team/scripts/lib/dashboard_palette.sh) / "
        "pgai_agent_kanban.lib.palette.resolve_semantic_color for a "
        "task/entry color meaning already declared in "
        "team/config/default_palette.txt.",
    ]
    commands.append(f'python3 "{lint_script}" --team-dir "{team_dir}"')
    return commands


def _format_remedy_block(
    violations: list[Violation], *, team_dir: Path, lint_script: Path
) -> str:
    """Assemble the full three-part remedy block."""
    causes = _build_causes(violations)
    commands = _build_fix_commands(violations, team_dir=team_dir, lint_script=lint_script)

    lines = [
        f"lint_dashboard_hardcode_colors: FAIL — {len(violations)} violation(s)",
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
        "Rule: team/scripts/dashboard/ and team/scripts/lib/dashboard/ "
        "never contain a raw ANSI escape literal or tmux #[fg=...] markup "
        "literal (test fixtures and golden files under a fixtures/ or "
        "golden/ path segment, or test_*.py/*_test.py files, are exempt) "
        "-- every color is looked up by name through the single-source "
        "palette."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_dashboard_hardcode_colors",
        description=(
            "Fails when a raw ANSI escape literal or tmux #[fg=...] markup "
            "literal appears anywhere under team/scripts/dashboard/ or "
            "team/scripts/lib/dashboard/."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  No violations found.\n"
            "  1  One or more violations found.\n"
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
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Print each file as it is checked.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the dashboard hardcoded-color gate and return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        # This script lives at team/scripts/lint_dashboard_hardcode_colors.py.
        team_dir = Path(__file__).resolve().parent.parent

    if not team_dir.is_dir():
        print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
        return 2

    scanned_roots = [team_dir / subpath for subpath in _SCANNED_SUBPATHS]
    if not any(root.is_dir() for root in scanned_roots):
        print(
            "ERROR: neither scanned root exists: "
            + ", ".join(str(r) for r in scanned_roots),
            file=sys.stderr,
        )
        return 2

    if args.verbose:
        print(f"[dashboard-hardcode-colors] Scanning {', '.join(str(r) for r in scanned_roots)} ...")

    violations = scan_dashboard_dirs(team_dir, verbose=args.verbose)

    if violations:
        lint_script = Path(__file__).resolve()
        print(
            "\n"
            + _format_remedy_block(violations, team_dir=team_dir, lint_script=lint_script),
            flush=True,
        )
        return 1

    print(
        "lint_dashboard_hardcode_colors: OK "
        f"({' and '.join(str(r) for r in scanned_roots)} are single-source clean)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
