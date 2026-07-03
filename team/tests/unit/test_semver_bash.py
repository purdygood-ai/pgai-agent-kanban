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


def test_compare_equal_versions_echoes_zero(tmp_path: pathlib.Path) -> None:
    """semver_compare echoes 0 when both version strings are identical."""
    result = run_bash(tmp_path, _source("semver_compare v1.2.3 v1.2.3"))
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_compare_lesser_version_echoes_minus_one(tmp_path: pathlib.Path) -> None:
    """semver_compare echoes -1 when the first argument is strictly less."""
    result = run_bash(tmp_path, _source("semver_compare v0.9.7 v0.17.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1"


def test_compare_greater_version_echoes_one(tmp_path: pathlib.Path) -> None:
    """semver_compare echoes 1 when the first argument is strictly greater."""
    result = run_bash(tmp_path, _source("semver_compare v0.17.1 v0.9.7"))
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


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


def test_compare_major_version_dominates_minor(tmp_path: pathlib.Path) -> None:
    """semver_compare reports v2.0.0 > v1.99.99 because major dominates."""
    result = run_bash(tmp_path, _source("semver_compare v2.0.0 v1.99.99"))
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


def test_compare_patch_breaks_tie_at_same_major_minor(tmp_path: pathlib.Path) -> None:
    """semver_compare breaks ties at the patch component when major and minor match."""
    result = run_bash(tmp_path, _source("semver_compare v0.17.0 v0.17.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1"


def test_compare_strips_leading_v_prefix(tmp_path: pathlib.Path) -> None:
    """semver_compare handles versions with and without the leading 'v' prefix."""
    result = run_bash(tmp_path, _source("semver_compare 1.2.3 v1.2.3"))
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_compare_strips_leading_uppercase_v_prefix(tmp_path: pathlib.Path) -> None:
    """semver_compare handles versions with an uppercase 'V' prefix."""
    result = run_bash(tmp_path, _source("semver_compare V1.2.3 v1.2.3"))
    assert result.returncode == 0
    assert result.stdout.strip() == "0"


def test_compare_zero_components(tmp_path: pathlib.Path) -> None:
    """semver_compare correctly handles v0.0.0 as the minimum version."""
    result = run_bash(tmp_path, _source("semver_compare v0.0.0 v0.0.1"))
    assert result.returncode == 0
    assert result.stdout.strip() == "-1"


def test_compare_large_minor_version(tmp_path: pathlib.Path) -> None:
    """semver_compare produces correct results for large minor version numbers."""
    result = run_bash(tmp_path, _source("semver_compare v0.102.0 v0.101.9"))
    assert result.returncode == 0
    assert result.stdout.strip() == "1"


# ---------------------------------------------------------------------------
# semver_lt — returns 0 (true) when A < B
# ---------------------------------------------------------------------------


def test_lt_returns_true_exit_when_less(tmp_path: pathlib.Path) -> None:
    """semver_lt exits 0 (true) when the first version is strictly less."""
    result = run_bash(tmp_path, _source("semver_lt v0.9.7 v0.17.1 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_lt_returns_false_exit_when_equal(tmp_path: pathlib.Path) -> None:
    """semver_lt exits 1 (false) when both versions are equal."""
    result = run_bash(tmp_path, _source("semver_lt v1.0.0 v1.0.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


def test_lt_returns_false_exit_when_greater(tmp_path: pathlib.Path) -> None:
    """semver_lt exits 1 (false) when the first version is greater."""
    result = run_bash(tmp_path, _source("semver_lt v0.17.1 v0.9.7 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


# ---------------------------------------------------------------------------
# semver_lte — returns 0 (true) when A <= B
# ---------------------------------------------------------------------------


def test_lte_returns_true_when_equal(tmp_path: pathlib.Path) -> None:
    """semver_lte exits 0 (true) when both versions are equal."""
    result = run_bash(tmp_path, _source("semver_lte v1.2.3 v1.2.3 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_lte_returns_true_when_strictly_less(tmp_path: pathlib.Path) -> None:
    """semver_lte exits 0 (true) when the first version is strictly less."""
    result = run_bash(tmp_path, _source("semver_lte v0.1.0 v0.2.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_lte_returns_false_when_greater(tmp_path: pathlib.Path) -> None:
    """semver_lte exits 1 (false) when the first version exceeds the second."""
    result = run_bash(tmp_path, _source("semver_lte v2.0.0 v1.99.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


# ---------------------------------------------------------------------------
# semver_gt — returns 0 (true) when A > B
# ---------------------------------------------------------------------------


def test_gt_returns_true_when_greater(tmp_path: pathlib.Path) -> None:
    """semver_gt exits 0 (true) when the first version is strictly greater."""
    result = run_bash(tmp_path, _source("semver_gt v0.17.1 v0.9.7 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_gt_returns_false_when_equal(tmp_path: pathlib.Path) -> None:
    """semver_gt exits 1 (false) when both versions are equal."""
    result = run_bash(tmp_path, _source("semver_gt v1.0.0 v1.0.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


def test_gt_returns_false_when_less(tmp_path: pathlib.Path) -> None:
    """semver_gt exits 1 (false) when the first version is less than the second."""
    result = run_bash(tmp_path, _source("semver_gt v0.9.7 v0.17.1 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


# ---------------------------------------------------------------------------
# semver_gte — returns 0 (true) when A >= B
# ---------------------------------------------------------------------------


def test_gte_returns_true_when_equal(tmp_path: pathlib.Path) -> None:
    """semver_gte exits 0 (true) when both versions are equal."""
    result = run_bash(tmp_path, _source("semver_gte v1.5.0 v1.5.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_gte_returns_true_when_greater(tmp_path: pathlib.Path) -> None:
    """semver_gte exits 0 (true) when the first version is strictly greater."""
    result = run_bash(tmp_path, _source("semver_gte v1.6.0 v1.5.9 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_gte_returns_false_when_less(tmp_path: pathlib.Path) -> None:
    """semver_gte exits 1 (false) when the first version is smaller."""
    result = run_bash(tmp_path, _source("semver_gte v0.0.1 v0.1.0 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


# ---------------------------------------------------------------------------
# semver_eq — returns 0 (true) when A == B
# ---------------------------------------------------------------------------


def test_eq_returns_true_for_identical_versions(tmp_path: pathlib.Path) -> None:
    """semver_eq exits 0 (true) when both versions are the same."""
    result = run_bash(tmp_path, _source("semver_eq v1.2.3 v1.2.3 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


def test_eq_returns_false_for_different_versions(tmp_path: pathlib.Path) -> None:
    """semver_eq exits 1 (false) when versions differ."""
    result = run_bash(tmp_path, _source("semver_eq v1.2.3 v1.2.4 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


def test_eq_handles_prefix_mismatch_as_equal(tmp_path: pathlib.Path) -> None:
    """semver_eq treats '1.2.3' and 'v1.2.3' as equal (prefix is normalised)."""
    result = run_bash(tmp_path, _source("semver_eq 1.2.3 v1.2.3 && echo yes || echo no"))
    assert result.returncode == 0
    assert result.stdout.strip() == "yes"


# ---------------------------------------------------------------------------
# semver_from_filename: extract version token from a filename string
# ---------------------------------------------------------------------------


def test_from_filename_extracts_version_from_simple_name(tmp_path: pathlib.Path) -> None:
    """semver_from_filename echoes the vX.Y.Z token from a simple filename."""
    result = run_bash(tmp_path, _source("semver_from_filename 'changelog-v0.17.1.md'"))
    assert result.returncode == 0
    assert result.stdout.strip() == "v0.17.1"


def test_from_filename_uses_basename_only(tmp_path: pathlib.Path) -> None:
    """semver_from_filename ignores directory components and extracts from basename."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename '/releases/v1.0.0/notes-v0.17.1.txt'"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "v0.17.1"


def test_from_filename_returns_empty_when_no_version_present(tmp_path: pathlib.Path) -> None:
    """semver_from_filename echoes an empty string when no version token is found."""
    result = run_bash(tmp_path, _source("semver_from_filename 'README.md'"))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_from_filename_returns_first_version_when_multiple_present(
    tmp_path: pathlib.Path,
) -> None:
    """semver_from_filename returns only the first vX.Y.Z token."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename 'migrate-v0.9.7-to-v0.17.1.sql'"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "v0.9.7"


def test_from_filename_result_starts_with_v(tmp_path: pathlib.Path) -> None:
    """semver_from_filename always returns a 'v'-prefixed token."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename 'release-v2.0.0-notes.txt'"),
    )
    assert result.returncode == 0
    output = result.stdout.strip()
    assert output.startswith("v"), f"Expected output starting with 'v'; got {output!r}"


def test_from_filename_ignores_version_in_directory_part(tmp_path: pathlib.Path) -> None:
    """semver_from_filename skips version strings in directory path components."""
    result = run_bash(
        tmp_path,
        _source("semver_from_filename '/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz'"),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "v2.3.4"
