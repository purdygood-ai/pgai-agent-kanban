"""
test_semver.py
==============
Behavioral unit tests for team/pm-agent/lib/semver.py.

Each test feeds a real input and asserts the specific output — no
did-not-crash checks.  The key behavioral requirement driving these tests
is that version comparison uses numeric tuple ordering, not lexicographic
string ordering, so v0.9.7 < v0.17.1 (not the reverse).
"""

from __future__ import annotations

import pytest

# Import via installed package path; fall back to direct import when running
# from within the pm-agent directory.
try:
    from pm_agent.lib import semver
except ImportError:
    import sys, pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent"))
    from lib import semver  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# parse() — string -> tuple
# ---------------------------------------------------------------------------


def test_parse_version_with_lowercase_v_prefix() -> None:
    """parse() strips a leading 'v' and returns the numeric triple."""
    assert semver.parse("v1.2.3") == (1, 2, 3)


def test_parse_version_with_uppercase_v_prefix() -> None:
    """parse() accepts uppercase 'V' prefix in addition to lowercase 'v'."""
    assert semver.parse("V0.10.5") == (0, 10, 5)


def test_parse_version_without_prefix() -> None:
    """parse() accepts a bare X.Y.Z string without any prefix."""
    assert semver.parse("0.9.7") == (0, 9, 7)


def test_parse_version_zero_components() -> None:
    """parse() correctly handles versions whose components are 0."""
    assert semver.parse("v0.0.0") == (0, 0, 0)


def test_parse_version_large_minor() -> None:
    """parse() produces correct integer values for large minor versions."""
    result = semver.parse("v0.102.0")
    assert result == (0, 102, 0)


def test_parse_invalid_string_raises_value_error() -> None:
    """parse() raises ValueError for strings that do not match X.Y.Z."""
    with pytest.raises(ValueError, match="Cannot parse version"):
        semver.parse("not-a-version")


def test_parse_missing_patch_raises_value_error() -> None:
    """parse() raises ValueError when the patch component is absent."""
    with pytest.raises(ValueError):
        semver.parse("v1.2")


def test_parse_empty_string_raises_value_error() -> None:
    """parse() raises ValueError for an empty string."""
    with pytest.raises(ValueError):
        semver.parse("")


# ---------------------------------------------------------------------------
# compare() — numeric ordering (the critical requirement)
# ---------------------------------------------------------------------------


def test_compare_numeric_minor_not_string_ordering() -> None:
    """compare() uses numeric tuple ordering: v0.9.7 < v0.17.1.

    With lexicographic string ordering '0.9.7' > '0.17.1' (because '9' > '1').
    Numeric ordering must return -1, confirming the tuple comparison is used.
    """
    assert semver.compare("v0.9.7", "v0.17.1") == -1


def test_compare_equal_versions_returns_zero() -> None:
    """compare() returns 0 when both versions are identical."""
    assert semver.compare("v1.2.3", "v1.2.3") == 0


def test_compare_greater_version_returns_positive_one() -> None:
    """compare() returns 1 when the first argument is strictly greater."""
    assert semver.compare("v0.17.1", "v0.9.7") == 1


def test_compare_major_component_dominates() -> None:
    """compare() respects major version ordering over minor/patch."""
    assert semver.compare("v2.0.0", "v1.99.99") == 1
    assert semver.compare("v1.99.99", "v2.0.0") == -1


def test_compare_patch_component_breaks_tie() -> None:
    """compare() breaks ties at the patch level when major and minor match."""
    assert semver.compare("v0.17.0", "v0.17.1") == -1
    assert semver.compare("v0.17.1", "v0.17.0") == 1


def test_compare_accepts_pre_parsed_tuples() -> None:
    """compare() accepts pre-parsed (major, minor, patch) tuples directly."""
    assert semver.compare((0, 9, 7), (0, 17, 1)) == -1
    assert semver.compare((1, 0, 0), (1, 0, 0)) == 0


def test_compare_mixed_string_and_tuple_arguments() -> None:
    """compare() handles one string argument and one tuple argument."""
    assert semver.compare("v0.9.7", (0, 17, 1)) == -1


# ---------------------------------------------------------------------------
# lt / le / gt / ge / eq — relational helpers
# ---------------------------------------------------------------------------


def test_lt_returns_true_when_first_is_smaller() -> None:
    """lt() returns True when the first version is strictly less than the second."""
    assert semver.lt("v0.9.7", "v0.17.1") is True


def test_lt_returns_false_when_equal() -> None:
    """lt() returns False when both versions are equal."""
    assert semver.lt("v1.0.0", "v1.0.0") is False


def test_lt_returns_false_when_first_is_greater() -> None:
    """lt() returns False when the first version is greater."""
    assert semver.lt("v0.17.1", "v0.9.7") is False


def test_le_returns_true_for_equal_versions() -> None:
    """le() returns True when both versions are equal."""
    assert semver.le("v1.2.3", "v1.2.3") is True


def test_le_returns_true_when_first_is_smaller() -> None:
    """le() returns True when the first version is strictly less."""
    assert semver.le("v0.1.0", "v0.2.0") is True


def test_le_returns_false_when_first_is_greater() -> None:
    """le() returns False when the first version exceeds the second."""
    assert semver.le("v2.0.0", "v1.99.0") is False


def test_gt_returns_true_when_first_is_greater() -> None:
    """gt() returns True when the first version exceeds the second."""
    assert semver.gt("v0.17.1", "v0.9.7") is True


def test_gt_returns_false_when_equal() -> None:
    """gt() returns False when both versions are equal."""
    assert semver.gt("v1.0.0", "v1.0.0") is False


def test_ge_returns_true_for_equal_versions() -> None:
    """ge() returns True when both versions are equal."""
    assert semver.ge("v1.5.0", "v1.5.0") is True


def test_ge_returns_true_when_first_is_greater() -> None:
    """ge() returns True when the first version is strictly greater."""
    assert semver.ge("v1.6.0", "v1.5.9") is True


def test_ge_returns_false_when_first_is_smaller() -> None:
    """ge() returns False when the first version is smaller."""
    assert semver.ge("v0.0.1", "v0.1.0") is False


def test_eq_returns_true_for_identical_versions() -> None:
    """eq() returns True when both versions are the same."""
    assert semver.eq("v1.2.3", "v1.2.3") is True


def test_eq_returns_false_for_different_versions() -> None:
    """eq() returns False when versions differ."""
    assert semver.eq("v1.2.3", "v1.2.4") is False


# ---------------------------------------------------------------------------
# from_filename() — extract version token from filename
# ---------------------------------------------------------------------------


def test_from_filename_extracts_version_token() -> None:
    """from_filename() extracts the first v-prefixed version token from a filename."""
    result = semver.from_filename("changelog-v0.17.1.md")
    assert result == "v0.17.1"


def test_from_filename_uses_basename_only() -> None:
    """from_filename() ignores directory components and extracts from the basename."""
    result = semver.from_filename("/releases/v1.0.0/notes-v0.17.1.txt")
    assert result == "v0.17.1"


def test_from_filename_returns_empty_when_no_version_present() -> None:
    """from_filename() returns an empty string when no version token is found."""
    result = semver.from_filename("README.md")
    assert result == ""


def test_from_filename_returns_first_version_token() -> None:
    """from_filename() returns the first version token when multiple are present."""
    result = semver.from_filename("migrate-v0.9.7-to-v0.17.1.sql")
    assert result == "v0.9.7"


def test_from_filename_reconstructs_v_prefix() -> None:
    """from_filename() always returns a 'v'-prefixed token, even from matched groups."""
    result = semver.from_filename("release-v2.0.0-notes.txt")
    assert result.startswith("v")


def test_from_filename_handles_full_path_with_version_in_dir() -> None:
    """from_filename() ignores version strings in directory parts of a path."""
    # The path has v1.0.0 in a directory name but v2.3.4 in the basename.
    result = semver.from_filename("/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz")
    assert result == "v2.3.4"
