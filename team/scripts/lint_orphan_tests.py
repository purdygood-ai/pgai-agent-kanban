#!/usr/bin/env python3
"""
lint_orphan_tests.py
====================
Pre-flight lint that fails when any test_*.py file under team/ is unreachable
from the gated runners' pytest argv.

An "orphan test" is a test file that exists under team/ but is not located
inside any directory collected by a gated runner (run-unit-tests.sh or
run-integration-tests.sh).  When an orphan exists, the gated UNIT_EXIT /
INTEGRATION_EXIT signal does not cover that test, so regressions it catches
are invisible to CI.

Design: static allow-list
-------------------------
This script uses a static allow-list of collected roots rather than parsing
the runner scripts dynamically.  Dynamic parsing is fragile (shell quoting
and script structure can change).  The allow-list is the authoritative record
of which directories each runner covers; it must be updated when either
runner's PYTEST_ARGS is changed.

Collected roots (paths relative to team/):
  Unit runner (run-unit-tests.sh PYTEST_ARGS):
    - tests/unit/
    - pgai_agent_kanban/api/tests/

  Integration runner (run-integration-tests.sh PYTEST_ARGS):
    - tests/integration/

Exclusions
----------
The following directories are excluded from the "all test files" enumeration:
  - __pycache__   compiled bytecode; not source
  - .venv         virtual environment; third-party packages, not project tests
  - fixtures/     deliberate bad-example fixtures for lint unit tests; they
                  intentionally contain anti-patterns and are not collected by
                  gated runners by design

Exit codes
----------
  0   No orphan test files found; all tests are covered by a gated runner.
  1   One or more orphan test files found; see stdout for the list.
  2   Usage error or internal failure (e.g., team_dir not found).

Usage
-----
  python3 scripts/lint_orphan_tests.py [--team-dir PATH] [--verbose]

  --team-dir PATH   Root of the team/ directory to scan (default: parent of
                    this script's directory, i.e., team/ relative to the
                    script's location at team/scripts/).
  --verbose, -v     Print each scanned file name.

Examples
--------
  # From repo root (team/ is the working directory):
  python3 team/scripts/lint_orphan_tests.py

  # With explicit path:
  python3 team/scripts/lint_orphan_tests.py --team-dir /path/to/team
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Collected roots — update this list when a runner's PYTEST_ARGS changes.
#
# Paths are relative to team/.  Each entry is a directory; any test_*.py
# file whose Path is under one of these roots (i.e., the root is one of the
# file's parents) is considered "covered."
# ---------------------------------------------------------------------------

_COLLECTED_ROOTS: list[str] = [
    # run-unit-tests.sh PYTEST_ARGS
    "tests/unit",
    "pgai_agent_kanban/api/tests",
    # run-integration-tests.sh PYTEST_ARGS
    "tests/integration",
]

# ---------------------------------------------------------------------------
# Directories excluded from the "all test files" enumeration.
# ---------------------------------------------------------------------------

_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".venv",
    "fixtures",
})


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _collect_test_files(team_dir: Path, verbose: bool) -> list[Path]:
    """Return all test_*.py files under team_dir, excluding _EXCLUDED_DIRS."""
    test_files: list[Path] = []
    for path in sorted(team_dir.rglob("test_*.py")):
        # Skip any path that has an excluded directory component.
        if any(part in _EXCLUDED_DIRS for part in path.parts):
            continue
        if verbose:
            print(f"  found: {path.relative_to(team_dir)}", flush=True)
        test_files.append(path)
    return test_files


def _build_collected_roots(team_dir: Path) -> list[Path]:
    """Resolve _COLLECTED_ROOTS against team_dir into absolute Paths."""
    return [team_dir / root for root in _COLLECTED_ROOTS]


def _is_covered(test_file: Path, collected_roots: list[Path]) -> bool:
    """Return True if test_file is located under at least one collected root."""
    for root in collected_roots:
        try:
            test_file.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _find_orphans(
    team_dir: Path,
    verbose: bool = False,
) -> list[Path]:
    """Return a list of test files not covered by any collected root."""
    test_files = _collect_test_files(team_dir, verbose)
    collected_roots = _build_collected_roots(team_dir)
    return [f for f in test_files if not _is_covered(f, collected_roots)]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_orphan_tests.py",
        description=(
            "Lint team/ for test_*.py files that are not reachable from any "
            "gated runner's pytest argv.  See module docstring for the static "
            "allow-list and exclusion rules."
        ),
    )
    parser.add_argument(
        "--team-dir",
        metavar="PATH",
        help=(
            "Root of the team/ directory to scan.  Default: parent of this "
            "script's directory (team/scripts/ -> team/)."
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print each test file found during enumeration.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    # Resolve team_dir: explicit arg > default (parent of this script).
    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent   # team/scripts/
        team_dir = script_dir.parent                   # team/

    if not team_dir.is_dir():
        print(
            f"ERROR: team directory not found: {team_dir}\n"
            "Use --team-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    print(f"lint_orphan_tests: scanning {team_dir}", flush=True)
    print(
        "lint_orphan_tests: collected roots — "
        + ", ".join(_COLLECTED_ROOTS),
        flush=True,
    )

    orphans = _find_orphans(team_dir, verbose=args.verbose)

    if orphans:
        print(
            f"\nlint_orphan_tests: FAIL — {len(orphans)} orphan test file(s) "
            "not reachable from any gated runner:\n",
            flush=True,
        )
        for path in orphans:
            try:
                rel = path.relative_to(team_dir)
            except ValueError:
                rel = path
            print(f"  {rel}", flush=True)
        print(
            "\nFix: either add the file to a collected root or extend a "
            "runner's PYTEST_ARGS and update _COLLECTED_ROOTS in "
            "team/scripts/lint_orphan_tests.py.",
            flush=True,
        )
        return 1

    total = len(_collect_test_files(team_dir, verbose=False))
    print(
        f"lint_orphan_tests: ok — {total} test file(s) covered, "
        "no orphan tests found.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
