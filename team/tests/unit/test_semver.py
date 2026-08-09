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


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("v1.2.3", (1, 2, 3)),  # lowercase 'v' prefix stripped
        ("V0.10.5", (0, 10, 5)),  # uppercase 'V' prefix accepted
        ("0.9.7", (0, 9, 7)),  # bare X.Y.Z without prefix
        ("v0.0.0", (0, 0, 0)),  # all-zero components
    ],
)
def test_parse_version(raw: str, expected: tuple) -> None:
    """parse() returns the numeric (major, minor, patch) triple, with or without a v/V prefix."""
    assert semver.parse(raw) == expected


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


@pytest.mark.parametrize(
    "func_name, a, b, expected",
    [
        ("lt", "v0.9.7", "v0.17.1", True),  # first strictly less
        ("lt", "v1.0.0", "v1.0.0", False),  # equal
        ("lt", "v0.17.1", "v0.9.7", False),  # first greater
        ("le", "v1.2.3", "v1.2.3", True),  # equal
        ("le", "v0.1.0", "v0.2.0", True),  # first strictly less
        ("le", "v2.0.0", "v1.99.0", False),  # first greater
        ("gt", "v0.17.1", "v0.9.7", True),  # first strictly greater
        ("gt", "v1.0.0", "v1.0.0", False),  # equal
        ("ge", "v1.5.0", "v1.5.0", True),  # equal
        ("ge", "v1.6.0", "v1.5.9", True),  # first strictly greater
        ("ge", "v0.0.1", "v0.1.0", False),  # first smaller
        ("eq", "v1.2.3", "v1.2.3", True),  # identical
        ("eq", "v1.2.3", "v1.2.4", False),  # different
    ],
)
def test_relational_helper(func_name: str, a: str, b: str, expected: bool) -> None:
    """lt/le/gt/ge/eq compare two version strings per their relational semantics."""
    func = getattr(semver, func_name)
    assert func(a, b) is expected


# ---------------------------------------------------------------------------
# from_filename() — extract version token from filename
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, expected",
    [
        ("changelog-v0.17.1.md", "v0.17.1"),  # simple filename
        ("/releases/v1.0.0/notes-v0.17.1.txt", "v0.17.1"),  # basename only, ignores dir components
        ("README.md", ""),  # no version token present
        ("migrate-v0.9.7-to-v0.17.1.sql", "v0.9.7"),  # first token when multiple present
    ],
)
def test_from_filename(filename: str, expected: str) -> None:
    """from_filename() extracts the first v-prefixed version token from the basename."""
    assert semver.from_filename(filename) == expected


def test_from_filename_reconstructs_v_prefix() -> None:
    """from_filename() always returns a 'v'-prefixed token, even from matched groups."""
    result = semver.from_filename("release-v2.0.0-notes.txt")
    assert result.startswith("v")


def test_from_filename_handles_full_path_with_version_in_dir() -> None:
    """from_filename() ignores version strings in directory parts of a path."""
    # The path has v1.0.0 in a directory name but v2.3.4 in the basename.
    result = semver.from_filename("/srv/v1.0.0/artifacts/release-v2.3.4.tar.gz")
    assert result == "v2.3.4"
