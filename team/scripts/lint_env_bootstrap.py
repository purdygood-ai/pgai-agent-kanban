#!/usr/bin/env python3
"""
lint_env_bootstrap.py
=====================
Class-closer lint that asserts the env-bootstrap unification contract on
both runtimes: bash entry points and Python entry points.

BASH-SIDE CHECK
---------------
Every executable non-lib ``.sh`` file under the swept script directories
(``scripts/``, ``scripts/cm/``, ``scripts/dashboard/``) that contains a
non-comment reference to ``PGAI_AGENT_KANBAN_ROOT_PATH`` must source either
``env_bootstrap.sh`` (the canonical prelude) or ``wake_common.sh`` (the
accepted equivalent for wake-family entry points).

A file that references the root env var without sourcing one of the two
approved preludes is a violation — it means the script may fail silently
on unsourced shells, exactly the class of bug this RC closes.

PYTHON-SIDE CHECK
-----------------
Every Python file outside ``pgai_agent_kanban/env.py`` (the canonical
resolver) and outside test files must NOT access
``PGAI_AGENT_KANBAN_ROOT_PATH`` directly via ``os.environ``.  Python entry
points must import ``resolve_kanban_root`` from ``pgai_agent_kanban.env``
and call it; bypassing the resolver is a violation.

Comment lines (lines whose first non-whitespace character is ``#``) are
excluded from both checks — documentation references do not imply a runtime
dependency.

SCOPE
-----
Bash directories scanned (non-recursive in each, then recursive into
``cm/`` and ``dashboard/`` subdirectories):

  scripts/                  top-level entry points
  scripts/cm/               CM scripts
  scripts/dashboard/        dashboard pane scripts

Directories NOT in scope (cleanup/, harness/) were not part of the
env-bootstrap adoption sweep and still use conventional defaults by design.

Python scope:

  scripts/*.py              Python CLI entry points
  pgai_agent_kanban/**/*.py package modules (excluding env.py and tests)

EXIT CODES
----------
  0   All checks pass (lint is green for both sides).
  1   One or more violations found; see stdout for details.
  2   Usage error or the specified directory was not found.

USAGE
-----
  python3 scripts/lint_env_bootstrap.py [OPTIONS]

  --team-dir PATH         Root of the team/ directory (default: parent of
                          this script's directory, i.e. team/ when the
                          script lives at team/scripts/).
  --bash-only             Run only the bash-side check.
  --python-only           Run only the Python-side check.
  --verbose, -v           Print each file as it is checked.

EXAMPLES
--------
  # From repo root:
  python3 team/scripts/lint_env_bootstrap.py

  # With explicit team dir:
  python3 team/scripts/lint_env_bootstrap.py --team-dir /path/to/team

  # Bash side only:
  python3 team/scripts/lint_env_bootstrap.py --bash-only

  # Python side only:
  python3 team/scripts/lint_env_bootstrap.py --python-only
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# The root env var that entry points must NOT access directly.
_ROOT_VAR = "PGAI_AGENT_KANBAN_ROOT_PATH"

# Approved bash prelude source lines (structural check — presence of the
# filename is sufficient; the full path form may vary).
_BASH_APPROVED_PRELUDES: tuple[str, ...] = (
    "env_bootstrap.sh",
    "wake_common.sh",
)

# Bash script directories to sweep, relative to team/scripts/.
# Each tuple is (base_dir_relative_to_scripts, recursive).
_BASH_SCRIPT_SUBDIRS: tuple[str, ...] = (
    "",          # scripts/ itself (non-recursive, only direct children)
    "cm",        # scripts/cm/
    "dashboard", # scripts/dashboard/
)

# Python module directories to scan, relative to team/.
# The canonical resolver itself is excluded.
_PYTHON_SCAN_DIRS: tuple[str, ...] = (
    "scripts",
    "pgai_agent_kanban",
)

# The canonical resolver file — excluded from the Python check so it can
# define the access pattern it owns.
_CANONICAL_RESOLVER_NAME = "env.py"
_CANONICAL_RESOLVER_PACKAGE = "pgai_agent_kanban"

# Pattern: a non-comment line in a Python file that reads the root env var
# directly via os.environ (both dict-style and .get() form).
_PY_DIRECT_ENV_READ_RE = re.compile(
    r'os\.environ\s*(?:\[|\.get\s*\()\s*["\']'
    + re.escape(_ROOT_VAR)
    + r'["\']'
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    """A single lint violation."""

    path: Path
    message: str


# ---------------------------------------------------------------------------
# Bash-side check helpers
# ---------------------------------------------------------------------------


def _line_is_comment(line: str) -> bool:
    """Return True when the line is a shell comment (first non-ws char is ``#``)."""
    return line.lstrip().startswith("#")


def _heredoc_body_line_numbers(lines: list[str]) -> set[int]:
    """Return the set of 1-based line numbers that fall inside a heredoc body.

    A heredoc body is the content between a ``<<TOKEN`` (or ``<<'TOKEN'``,
    ``<<"TOKEN"``, ``<<-TOKEN``, etc.) opener and its matching close-tag line.
    Body lines are documentation, not executable code, so references to env
    vars inside them must not be treated as runtime usage.

    The opener is detected on any non-comment line that contains the pattern
    ``<<[-]?(['"]?)WORD\\1`` (with optional ``-`` for tab-stripping and optional
    quoting around the delimiter).  The close-tag is a line whose stripped
    content equals the unquoted WORD (bash ignores leading tabs when ``<<-``
    was used; this implementation strips leading whitespace from close-tag
    candidates conservatively).

    Lines that ARE the opener or close-tag themselves are not included in the
    returned set — only the body lines between them.
    """
    # Regex to detect a heredoc opener embedded anywhere in a non-comment line.
    # Captures: optional '-' (tab-strip flag), optional quote char, the TOKEN.
    _HEREDOC_OPEN_RE = re.compile(
        r'<<(-?)([\'"]?)([A-Za-z_][A-Za-z0-9_]*)\2'
    )

    body_lines: set[int] = set()
    # Stack of active heredoc delimiters (unquoted TOKEN strings).
    # Multiple heredocs can be opened on the same command line (rare but valid).
    active_delimiters: list[tuple[str, bool]] = []  # (token, tab_strip)

    for lineno, line in enumerate(lines, start=1):
        stripped = line.lstrip()

        if active_delimiters:
            # We are inside one or more heredoc bodies.
            # Check if this line closes the innermost heredoc.
            token, tab_strip = active_delimiters[-1]
            # Close-tag: line stripped of leading whitespace (tabs when <<-) equals TOKEN.
            close_candidate = line.strip() if tab_strip else line.rstrip("\n").rstrip("\r")
            if close_candidate == token:
                # This line is the close-tag; pop the delimiter but do NOT add it to body.
                active_delimiters.pop()
            else:
                # This line is a body line — mark it.
                body_lines.add(lineno)
            # Even inside a heredoc body, bash does NOT allow nested heredoc openers
            # to be parsed (they are literal text).  Skip opener detection for body lines.
            continue

        # Not currently inside a heredoc body.  Skip comment lines for opener detection.
        if stripped.startswith("#"):
            continue

        # Detect all heredoc openers on this (non-comment) line.
        for m in _HEREDOC_OPEN_RE.finditer(line):
            tab_flag = m.group(1) == "-"
            token = m.group(3)
            active_delimiters.append((token, tab_flag))
        # Opener line itself is NOT added to body_lines.

    return body_lines


def _file_references_root_var(text: str) -> bool:
    """Return True when the file contains a non-comment, non-heredoc-body line
    referencing the root env var.
    """
    lines = text.splitlines()
    heredoc_body = _heredoc_body_line_numbers(lines)
    for lineno, line in enumerate(lines, start=1):
        if lineno in heredoc_body:
            continue
        if not _line_is_comment(line) and _ROOT_VAR in line:
            return True
    return False


def _file_sources_approved_prelude(text: str) -> bool:
    """Return True when the file contains a source/. call for an approved prelude.

    Checks for both ``source <name>`` and ``. <name>`` forms anywhere in the
    file content (including the shellcheck-source comment directive is not
    sufficient; the actual runtime source call must be present).
    """
    for line in text.splitlines():
        stripped = line.strip()
        for prelude in _BASH_APPROVED_PRELUDES:
            # Match: source "...env_bootstrap.sh" or . "...env_bootstrap.sh"
            # or source '...' or the bare form: source lib/env_bootstrap.sh
            if (
                re.search(
                    r'\b(source|\.)\b.*' + re.escape(prelude),
                    stripped,
                )
                and not stripped.startswith("#")
            ):
                return True
    return False


def _source_precedes_first_usage(text: str) -> tuple[bool, int, int]:
    """Return whether the approved prelude source appears BEFORE the first usage.

    The predicate gap this lint targets: a script can source an approved
    prelude anywhere in the file and pass the old presence-only check — even
    when the source appears AFTER the first non-comment runtime use of
    PGAI_AGENT_KANBAN_ROOT_PATH.  Such a script fails from a fresh shell because
    the env var is read before the bootstrap has a chance to set it.

    This function closes that hole by checking LINE ORDER, not just presence.

    Heredoc body lines are excluded from the usage scan.  A reference to
    PGAI_AGENT_KANBAN_ROOT_PATH inside a ``<<HELPTEXT ... HELPTEXT`` block is
    documentation (for example, a ``--help`` output block), not executable code.
    Including heredoc body text in the first-use scan produces false ordering
    violations when ``--help`` heredocs that describe the env var appear above
    the ``source env_bootstrap.sh`` line.

    Parameters
    ----------
    text:
        Full file content as a string.

    Returns
    -------
    tuple[bool, int, int]
        (ordering_ok, first_source_line, first_usage_line)
        ``ordering_ok`` is True when the source appears before the first usage
        (or when the file has a source but no runtime usage — exempt).
        Line numbers are 1-based; 0 means "not found".
        When ``ordering_ok`` is False, the caller should report both line numbers
        in the violation message so the operator knows exactly what to move.
    """
    lines = text.splitlines()
    heredoc_body = _heredoc_body_line_numbers(lines)
    first_source_line: int = 0
    first_usage_line: int = 0

    for lineno, line in enumerate(lines, start=1):
        # Skip heredoc body lines — they are documentation, not executable code.
        if lineno in heredoc_body:
            continue

        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue

        # Detect the first approved-prelude source call.
        if first_source_line == 0:
            for prelude in _BASH_APPROVED_PRELUDES:
                if re.search(r'\b(source|\.)\b.*' + re.escape(prelude), stripped):
                    first_source_line = lineno
                    break

        # Detect the first non-comment, non-heredoc-body runtime use of the root env var.
        if first_usage_line == 0 and _ROOT_VAR in line:
            first_usage_line = lineno

    # Ordering is correct when:
    #  - There is a source AND it precedes the first usage.
    #  - There is a source but no usage (the bootstrap call itself may not
    #    count, and pure-bootstrap helpers don't use the var directly).
    if first_source_line == 0:
        # No source found — not an ordering issue, handled by _file_sources_approved_prelude.
        return True, 0, first_usage_line
    if first_usage_line == 0:
        # Source present, no runtime usage detected — ordering is fine.
        return True, first_source_line, 0
    return first_source_line < first_usage_line, first_source_line, first_usage_line


def check_bash_side(
    scripts_dir: Path,
    *,
    verbose: bool = False,
) -> list[Violation]:
    """Check all bash entry points in the swept directories.

    For each executable ``.sh`` file in ``scripts/``, ``scripts/cm/``, and
    ``scripts/dashboard/`` (non-recursive within each), if the file contains
    a non-comment reference to ``PGAI_AGENT_KANBAN_ROOT_PATH``, assert that
    it sources ``env_bootstrap.sh`` or ``wake_common.sh``.

    Parameters
    ----------
    scripts_dir:
        Path to the ``scripts/`` directory (i.e. ``team/scripts/``).
    verbose:
        When True, print each file checked to stdout.

    Returns
    -------
    list[Violation]
        Violations found; empty list means green.
    """
    violations: list[Violation] = []

    for subdir in _BASH_SCRIPT_SUBDIRS:
        if subdir:
            target_dir = scripts_dir / subdir
        else:
            target_dir = scripts_dir

        if not target_dir.is_dir():
            if verbose:
                print(f"  [bash] directory not found, skipping: {target_dir}")
            continue

        # Non-recursive glob: only direct children of this directory.
        candidates = sorted(target_dir.glob("*.sh"))

        for sh_path in candidates:
            # Skip lib/ directory and non-executable files.
            if sh_path.parent.name == "lib":
                continue

            if not sh_path.is_file():
                continue

            is_exec = os.access(sh_path, os.X_OK)
            if verbose:
                exec_marker = "x" if is_exec else "-"
                print(f"  [bash] [{exec_marker}] {sh_path}")

            if not is_exec:
                # Only executable entry-point scripts are in scope.
                continue

            text = sh_path.read_text(encoding="utf-8", errors="replace")

            if not _file_references_root_var(text):
                # Script does not use the root env var — exempt.
                continue

            if not _file_sources_approved_prelude(text):
                violations.append(
                    Violation(
                        path=sh_path,
                        message=(
                            f"references {_ROOT_VAR} but does not source an approved prelude "
                            f"(env_bootstrap.sh or wake_common.sh)"
                        ),
                    )
                )
                continue

            # Ordering check: the approved prelude source must appear BEFORE
            # the first non-comment runtime use of the root env var.
            # A source present but placed AFTER the first usage is the predicate
            # hole this lint closes: the script still fails from a fresh shell
            # even though the old presence-only predicate reported it as clean.
            ordering_ok, src_line, use_line = _source_precedes_first_usage(text)
            if not ordering_ok:
                violations.append(
                    Violation(
                        path=sh_path,
                        message=(
                            f"sources approved prelude at line {src_line} but "
                            f"{_ROOT_VAR} is first used at line {use_line} — "
                            f"the source must appear before the first usage so "
                            f"the script bootstraps correctly from a fresh shell"
                        ),
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Python-side check helpers
# ---------------------------------------------------------------------------


def _py_line_is_comment(line: str) -> bool:
    """Return True when the Python line is a comment (first non-ws char is ``#``)."""
    return line.lstrip().startswith("#")


def _py_is_string_literal_context(line: str, match_start: int) -> bool:
    """Return True when the match appears to be inside a docstring or string literal.

    Heuristic: the line content before the match starts with triple-quote
    or is part of a multi-line string.  This is a lightweight filter; the
    check is conservative (errs toward reporting rather than suppressing).
    """
    # If the line has a triple-quote before the match, consider it a string context.
    prefix = line[:match_start]
    return '"""' in prefix or "'''" in prefix


def check_python_file_for_direct_env_reads(
    py_path: Path,
    *,
    verbose: bool = False,
) -> list[tuple[int, str]]:
    """Scan a Python file for non-comment direct ``os.environ`` reads of the root var.

    Parameters
    ----------
    py_path:
        Path to the Python file to check.
    verbose:
        When True, print the file path being checked.

    Returns
    -------
    list[tuple[int, str]]
        List of (line_number, line_content) for each violation line.
    """
    if verbose:
        print(f"  [python] {py_path}")

    hits: list[tuple[int, str]] = []
    text = py_path.read_text(encoding="utf-8", errors="replace")

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _py_line_is_comment(line):
            continue
        m = _PY_DIRECT_ENV_READ_RE.search(line)
        if m and not _py_is_string_literal_context(line, m.start()):
            hits.append((lineno, line.rstrip()))

    return hits


def _should_exclude_python_file(
    py_path: Path,
    *,
    team_dir: Path,
) -> bool:
    """Return True when the Python file should be excluded from the Python-side check.

    Excluded files:
      - ``pgai_agent_kanban/env.py``  — the canonical resolver itself owns
                                        the env-var access pattern
      - Any ``test_*.py`` or ``conftest.py`` file under team_dir — test
        files legitimately monkeypatch the env var
      - Any file whose path RELATIVE TO team_dir has a ``tests`` component
        (i.e. files under team/tests/, team/pgai_agent_kanban/api/tests/, etc.)

    The relative-path check avoids false exclusions when the team_dir itself
    is located under a directory named ``tests`` (which can happen when pytest
    places tmp_path under /tmp/.../tests/...).
    """
    name = py_path.name

    # The canonical resolver owns the env-var access pattern.
    if (
        name == _CANONICAL_RESOLVER_NAME
        and _CANONICAL_RESOLVER_PACKAGE in str(py_path)
    ):
        return True

    # Test files and conftest files may monkeypatch the env var.
    if name.startswith("test_") or name == "conftest.py":
        return True

    # Files under any directory named "tests" RELATIVE to team_dir are excluded.
    # Use relative_to() so we check the path within the scanned tree only,
    # not ancestor directories outside the scan root.
    try:
        relative = py_path.relative_to(team_dir)
    except ValueError:
        # py_path is not under team_dir — leave it to the caller to handle.
        return False

    if "tests" in relative.parts:
        return True

    return False


def check_python_side(
    team_dir: Path,
    *,
    verbose: bool = False,
) -> list[Violation]:
    """Check Python entry points and package modules for direct env-var reads.

    Scans ``team/scripts/*.py`` and ``team/pgai_agent_kanban/**/*.py``
    (excluding the canonical resolver and test files) for lines that access
    ``PGAI_AGENT_KANBAN_ROOT_PATH`` directly via ``os.environ``.

    Parameters
    ----------
    team_dir:
        Path to the ``team/`` directory.
    verbose:
        When True, print each file checked.

    Returns
    -------
    list[Violation]
        Violations found; empty list means green.
    """
    violations: list[Violation] = []

    for scan_subdir in _PYTHON_SCAN_DIRS:
        scan_dir = team_dir / scan_subdir
        if not scan_dir.is_dir():
            if verbose:
                print(f"  [python] directory not found, skipping: {scan_dir}")
            continue

        if scan_subdir == "scripts":
            # Only direct .py files in scripts/ — not subdirectories.
            candidates = sorted(scan_dir.glob("*.py"))
        else:
            # Recursive scan of pgai_agent_kanban/ package.
            candidates = sorted(scan_dir.rglob("*.py"))

        for py_path in candidates:
            if _should_exclude_python_file(py_path, team_dir=team_dir):
                if verbose:
                    print(f"  [python] [excluded] {py_path}")
                continue

            hits = check_python_file_for_direct_env_reads(py_path, verbose=verbose)
            for lineno, line_content in hits:
                violations.append(
                    Violation(
                        path=py_path,
                        message=(
                            f"line {lineno}: direct os.environ read of {_ROOT_VAR} "
                            f"outside the canonical resolver — "
                            f"import resolve_kanban_root from pgai_agent_kanban.env instead: "
                            f"{line_content!r}"
                        ),
                    )
                )

    return violations


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _report_violations(violations: list[Violation], side: str) -> None:
    """Print violation messages to stdout, one per line."""
    for v in violations:
        print(f"[{side}] FAIL {v.path}: {v.message}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lint_env_bootstrap",
        description=(
            "Class-closer lint: assert the env-bootstrap contract on bash entry points "
            "(must source env_bootstrap.sh or wake_common.sh) and Python entry points "
            "(must not read PGAI_AGENT_KANBAN_ROOT_PATH via os.environ directly)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Exit codes:\n"
            "  0  All checks pass.\n"
            "  1  One or more violations found.\n"
            "  2  Usage error or directory not found.\n"
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        default=None,
        help=(
            "Root of the team/ directory (default: parent of this script's directory, "
            "i.e. team/ when the script lives at team/scripts/)."
        ),
    )
    parser.add_argument(
        "--bash-only",
        action="store_true",
        default=False,
        help="Run only the bash-side check.",
    )
    parser.add_argument(
        "--python-only",
        action="store_true",
        default=False,
        help="Run only the Python-side check.",
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
    """Run both-sides env-bootstrap lint and return an exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Resolve team_dir from --team-dir or from this script's own location.
    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        # Default: team/ is the parent of the directory that contains this script.
        # This script lives at team/scripts/lint_env_bootstrap.py.
        team_dir = Path(__file__).resolve().parent.parent

    scripts_dir = team_dir / "scripts"

    if not team_dir.is_dir():
        print(f"ERROR: team directory not found: {team_dir}", file=sys.stderr)
        return 2

    if not scripts_dir.is_dir():
        print(f"ERROR: scripts directory not found: {scripts_dir}", file=sys.stderr)
        return 2

    run_bash = not args.python_only
    run_python = not args.bash_only

    all_violations: list[Violation] = []

    if run_bash:
        if args.verbose:
            print(f"[bash] Scanning entry points under {scripts_dir} ...")
        bash_violations = check_bash_side(scripts_dir, verbose=args.verbose)
        _report_violations(bash_violations, side="bash")
        all_violations.extend(bash_violations)

    if run_python:
        if args.verbose:
            print(f"[python] Scanning Python files under {team_dir} ...")
        python_violations = check_python_side(team_dir, verbose=args.verbose)
        _report_violations(python_violations, side="python")
        all_violations.extend(python_violations)

    if all_violations:
        count = len(all_violations)
        sides = []
        if run_bash:
            sides.append("bash")
        if run_python:
            sides.append("python")
        print(
            f"\nlint_env_bootstrap: {count} violation(s) found "
            f"(sides checked: {', '.join(sides)})"
        )
        return 1

    sides = []
    if run_bash:
        sides.append("bash")
    if run_python:
        sides.append("python")
    print(f"lint_env_bootstrap: OK (sides checked: {', '.join(sides)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
