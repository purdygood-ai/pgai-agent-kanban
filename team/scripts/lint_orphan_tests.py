#!/usr/bin/env python3
"""
lint_orphan_tests.py
====================
Pre-flight lint that reports every test_*.py file under team/ that is
unreachable from the gated runners' pytest argv.

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

  Cold-build runner (run-cold-build-tests.sh PYTEST_ARGS):
    - tests/cold/

Exclusions
----------
The following directories are excluded from the "all test files" enumeration:
  - __pycache__   compiled bytecode; not source
  - .venv         virtual environment; third-party packages, not project tests
  - fixtures/     deliberate bad-example fixtures for lint unit tests; they
                  intentionally contain anti-patterns and are not collected by
                  gated runners by design

Findings
--------
CONVERT (quality class, team/SOP-core.md "Gates and Refusals"): an orphan
test is a coverage gap, not a runtime failure.  Each orphan is printed and
recorded as one finding; with --findings-file the findings are written as a
JSON array (the shape pgai_agent_kanban.lint.findings defines), which the
gated runners merge per instance with gated_runner_merge_findings_file.

Exit codes
----------
  0   Scan ran, whatever it found (orphans are findings, not failures).
  2   Usage error, or the --team-dir directory does not exist.

Usage
-----
  python3 scripts/lint_orphan_tests.py [--team-dir PATH] [--findings-file PATH]
                                       [--verbose]

  --team-dir PATH       Root of the team/ directory to scan (default: parent
                        of this script's directory, i.e., team/ relative to
                        the script's location at team/scripts/).
  --findings-file PATH  Write the findings as a JSON array to PATH (parent
                        directories created; written even when empty).
  --verbose, -v         Print each scanned file name.

Examples
--------
  # From repo root (team/ is the working directory):
  python3 team/scripts/lint_orphan_tests.py

  # With explicit path:
  python3 team/scripts/lint_orphan_tests.py --team-dir /path/to/team

  # Record findings for a downstream merge:
  python3 team/scripts/lint_orphan_tests.py \\
      --findings-file "${PGAI_AGENT_KANBAN_TEMP_DIR}/lint/orphan_tests.json"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

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

_LINT_NAME = "orphan_tests"

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
    # run-cold-build-tests.sh PYTEST_ARGS
    "tests/cold",
]

# ---------------------------------------------------------------------------
# Directories excluded from the "all test files" enumeration.
# ---------------------------------------------------------------------------

_EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".venv",
        "fixtures",
    }
)


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
            "allow-list and exclusion rules.  Quality class: each orphan is a "
            "finding, and the lint exits 0 whatever it finds (2 on a usage error)."
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
        "--findings-file",
        metavar="PATH",
        help=(
            "Write the findings as a JSON array to PATH (parent directories "
            "created; written even when empty).  Findings are printed either way."
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
    """Run the orphan-test lint: report test files no gated runner collects.

    Exit codes: 0 whatever was found (each orphan is a quality finding,
    printed and written to --findings-file); 2 usage error, or the team
    directory not found.
    """
    args = _parse_args(argv)

    # Resolve team_dir: explicit arg > default (parent of this script).
    if args.team_dir:
        team_dir = Path(args.team_dir).resolve()
    else:
        script_dir = Path(__file__).resolve().parent  # team/scripts/
        team_dir = script_dir.parent  # team/

    if not team_dir.is_dir():
        print(
            f"ERROR: team directory not found: {team_dir}\n" "Use --team-dir to specify an alternative path.",
            file=sys.stderr,
        )
        return 2

    print(f"lint_orphan_tests: scanning {team_dir}", flush=True)
    print(
        "lint_orphan_tests: collected roots — " + ", ".join(_COLLECTED_ROOTS),
        flush=True,
    )

    orphans = _find_orphans(team_dir, verbose=args.verbose)
    repo_root = team_dir.parent
    relint = f"python3 {Path(__file__).resolve()} --team-dir {team_dir}"
    findings = []
    for path in orphans:
        rel = display_path(path, repo_root)
        unit_dest = display_path(team_dir / "tests" / "unit" / path.name, repo_root)
        findings.append(
            quality_finding(
                _LINT_NAME,
                f"{rel} is not under any collected root ({', '.join(_COLLECTED_ROOTS)}), " "so no gated runner runs it",
                f"git -C {repo_root} mv {rel} {unit_dest} && {relint}",
                file=rel,
            )
        )
    write_findings_file(args.findings_file, findings, lint="lint_orphan_tests")

    if findings:
        print(
            f"\nlint_orphan_tests: {len(findings)} orphan test file(s) " "not reachable from any gated runner:\n",
            flush=True,
        )
        for finding in findings:
            print(f"  {finding['file']}", flush=True)
        print(
            "\nFix: either move the file into a collected root or extend a "
            "runner's PYTEST_ARGS and update _COLLECTED_ROOTS in "
            "team/scripts/lint_orphan_tests.py.",
            flush=True,
        )
        # CENSUS-ANCHOR: orphan-tests-01
        # CONVERT: an orphan test is a coverage gap with zero runtime-failure
        # risk -- narrated and recorded as findings; the lint returns 0 below.
        emit_error_contract(
            "lint.orphan-tests",
            f"{len(findings)} test file(s) under {team_dir} sit outside every "
            "collected root, so no gated runner runs them; each is listed above.",
            findings[0]["remedy"],
        )
        return 0

    total = len(_collect_test_files(team_dir, verbose=False))
    print(
        f"lint_orphan_tests: ok — {total} test file(s) covered, " "no orphan tests found.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
