"""
test_semver_bash.py
===================
Behavioral unit tests for team/scripts/lib/semver.sh.

Tests source the shell script and invoke each function via the bash harness,
asserting on stdout and exit codes.  No full wake cycle is invoked.

All semver comparisons use sort -V (GNU version sort), which handles numeric
ordering across decade boundaries (e.g. v0.9.x < v0.10.x).

Key behavioral requirements:
  - semver_compare: echoes -1, 0, or 1
  - semver_lt/lte/gt/gte/eq: returns 0 (true) or 1 (false) via exit code
  - semver_from_filename: extracts first vX.Y.Z token from filename basename
  - All functions accept versions with or without a leading 'v' prefix
"""

from __future__ import annotations

import pathlib

import pytest

from tests.unit.shell_harness import run_bash

# Path to the script under test, relative to the team/ directory where pytest runs.
# pytest's working directory is the team/ subdirectory (set by run-unit-tests.sh).
_LIB = "scripts/lib/semver.sh"


def _source(func_call: str) -> str:
    """Return a bash snippet that sources semver.sh then calls func_call."""
    return f"source {_LIB} && {func_call}"


# ---------------------------------------------------------------------------
# semver_compare: numeric ordering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command, expected",
    [
        # semver_compare: numeric ordering, echoes -1/0/1
        ("semver_compare v1.2.3 v1.2.3", "0"),  # identical versions
        ("semver_compare v0.9.7 v0.17.1", "-1"),  # first strictly less
        ("semver_compare v0.17.1 v0.9.7", "1"),  # first strictly greater
        ("semver_compare v2.0.0 v1.99.99", "1"),  # major dominates minor
        ("semver_compare v0.17.0 v0.17.1", "-1"),  # patch breaks tie at same major.minor
        ("semver_compare 1.2.3 v1.2.3", "0"),  # leading 'v' prefix stripped
        ("semver_compare V1.2.3 v1.2.3", "0"),  # uppercase 'V' prefix stripped
        ("semver_compare v0.0.0 v0.0.1", "-1"),  # v0.0.0 is the minimum version
        ("semver_compare v0.102.0 v0.101.9", "1"),  # large minor version numbers
        # semver_lt/lte/gt/gte/eq: exit 0 (true) -> echoes 'yes', else 'no'
        ("semver_lt v0.9.7 v0.17.1 && echo yes || echo no", "yes"),
        ("semver_lt v1.0.0 v1.0.0 && echo yes || echo no", "no"),
        ("semver_lt v0.17.1 v0.9.7 && echo yes || echo no", "no"),
        ("semver_lte v1.2.3 v1.2.3 && echo yes || echo no", "yes"),
        ("semver_lte v0.1.0 v0.2.0 && echo yes || echo no", "yes"),
        ("semver_lte v2.0.0 v1.99.0 && echo yes || echo no", "no"),
        ("semver_gt v0.17.1 v0.9.7 && echo yes || echo no", "yes"),
        ("semver_gt v1.0.0 v1.0.0 && echo yes || echo no", "no"),
        ("semver_gt v0.9.7 v0.17.1 && echo yes || echo no", "no"),
        ("semver_gte v1.5.0 v1.5.0 && echo yes || echo no", "yes"),
        ("semver_gte v1.6.0 v1.5.9 && echo yes || echo no", "yes"),
        ("semver_gte v0.0.1 v0.1.0 && echo yes || echo no", "no"),
        ("semver_eq v1.2.3 v1.2.3 && echo yes || echo no", "yes"),
        ("semver_eq v1.2.3 v1.2.4 && echo yes || echo no", "no"),
        ("semver_eq 1.2.3 v1.2.3 && echo yes || echo no", "yes"),  # prefix mismatch treated as equal
        # semver_from_filename: extract first vX.Y.Z token from basename
        ("semver_from_filename 'changelog-v0.17.1.md'", "v0.17.1"),
        ("semver_from_filename '/releases/v1.0.0/notes-v0.17.1.txt'", "v0.17.1"),  # basename only
        ("semver_from_filename 'README.md'", ""),  # no version token present
        ("semver_from_filename 'migrate-v0.9.7-to-v0.17.1.sql'", "v0.9.7"),  # first token only
        (
            "semver_from_filename '/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz'",
            "v2.3.4",
        ),  # skips version in directory path components
    ],
)
def test_semver_bash_behaviors(tmp_path: pathlib.Path, command: str, expected: str) -> None:
    """semver_compare/lt/lte/gt/gte/eq/from_filename behaviors across ordering, prefix, and filename shapes."""
    result = run_bash(tmp_path, _source(command))
    assert result.returncode == 0
    assert result.stdout.strip() == expected


def test_compare_numeric_ordering_not_lexicographic(tmp_path: pathlib.Path) -> None:
    """semver_compare uses numeric tuple ordering, not string ordering.

    Lexicographic ordering would incorrectly report v0.9.7 > v0.17.1 because
    '9' > '1'.  Numeric ordering correctly reports v0.9.7 < v0.17.1.
    """
    result = run_bash(tmp_path, _source("semver_compare v0.9.7 v0.17.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1", (
        "v0.9.7 must be less than v0.17.1 under numeric ordering"
    )


def test_from_filename_result_starts_with_v(tmp_path: pathlib.Path) -> None:
    """semver_from_filename always returns a 'v'-prefixed token."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename 'release-v2.0.0-notes.txt'"),
    )
    assert result.returncode == 0
    output = result.stdout.strip()
    assert output.startswith("v"), f"Expected output starting with 'v'; got {output!r}"
