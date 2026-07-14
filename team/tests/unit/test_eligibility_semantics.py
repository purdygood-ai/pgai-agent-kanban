"""
test_eligibility_semantics.py
==============================
Unit tests for discovery eligibility semantics — structural checks (an earlier defect).

These tests are structural in nature: they inspect the source code of the
eligibility engine to assert that no workflow-type string literals appear in
the eligibility code region.

an earlier defect fix invariant: the engine consults the plugin's capability flag
(version_semantics) rather than comparing workflow type strings directly.
Any regression that reintroduces a workflow-type comparison (e.g. checking
for 'testing-only', 'release', or 'document' as string literals inside the
eligibility code) would be caught by this test.

This is the B39 grep-gate pattern applied to the eligibility code region.
The grep is scoped to _disc_list_all_eligible_requirements in discovery.sh
(not the whole repo) to avoid false positives from doc comments, examples,
or import statements that legitimately reference type names as strings.
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Path to the eligibility source file
# ---------------------------------------------------------------------------
_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent  # team/
_DISCOVERY_SH = _TEAM_DIR / "scripts" / "lib" / "discovery.sh"

# ---------------------------------------------------------------------------
# Known workflow-type string literals that must NOT appear in the eligibility
# code region.  Each entry is a string that could be used in a type-switch
# comparison rather than a plugin-capability lookup.
# ---------------------------------------------------------------------------
_WORKFLOW_TYPE_LITERALS = [
    "testing-only",
    "release",
    "document",
]

# ---------------------------------------------------------------------------
# Start and end markers for the eligibility code region.
#
# The region starts at the function definition line of
# _disc_list_all_eligible_requirements and ends at the closing line of that
# function (the "}" that closes the outer shell function).
#
# We grep this region, not the whole file, to avoid false positives in:
#   - Doc comments at the top of the file that explain the fix
#   - The _disc_write_bundle function that references workflow_type for bundle
#     writing (a separate concern from eligibility)
#   - Any test-only comments or examples
# ---------------------------------------------------------------------------
_REGION_START_PATTERN = re.compile(r"^_disc_list_all_eligible_requirements\s*\(")
_REGION_END_PATTERN = re.compile(r"^\}")


def _extract_eligibility_region(source: str) -> str:
    """Extract the _disc_list_all_eligible_requirements function body from source.

    Scans line by line for the function definition, then captures all lines
    until the closing "}" that ends the shell function.  Returns the extracted
    region as a single string.

    If the function definition cannot be found, raises AssertionError so the
    test fails loudly instead of silently passing on a missing function.

    Args:
        source: Complete text of discovery.sh.

    Returns:
        The function body including the opening and closing braces.
    """
    lines = source.splitlines()
    in_region = False
    region_lines: list[str] = []

    for line in lines:
        if not in_region:
            if _REGION_START_PATTERN.match(line):
                in_region = True
                region_lines.append(line)
        else:
            region_lines.append(line)
            # The closing brace on its own line ends the function.
            # We also stop at the next top-level function definition to be safe.
            if _REGION_END_PATTERN.match(line) and len(region_lines) > 2:
                break

    if not region_lines:
        raise AssertionError(
            f"Could not locate _disc_list_all_eligible_requirements in {_DISCOVERY_SH}. "
            "The structural grep test cannot run because the function is absent. "
            "Verify the function name has not been renamed."
        )

    return "\n".join(region_lines)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_discovery_sh_exists() -> None:
    """The eligibility source file exists at the expected path.

    This test fails loudly if the file has been moved, preventing the structural
    grep test from silently passing due to a missing file.
    """
    assert _DISCOVERY_SH.exists(), (
        f"Expected discovery.sh at {_DISCOVERY_SH}. "
        "If the file was moved, update _DISCOVERY_SH in this test file."
    )


def test_eligibility_region_locatable_in_discovery_sh() -> None:
    """The _disc_list_all_eligible_requirements function can be located in discovery.sh.

    Confirms the structural grep test's region extraction can find its target.
    If this test fails, the function may have been renamed; update the test
    to match the new name.
    """
    source = _DISCOVERY_SH.read_text(encoding="utf-8")
    region = _extract_eligibility_region(source)
    assert region, (
        "Expected to extract a non-empty region for _disc_list_all_eligible_requirements."
    )
    assert "_disc_list_all_eligible_requirements" in region, (
        "Extracted region must contain the function name."
    )


@pytest.mark.parametrize("literal", _WORKFLOW_TYPE_LITERALS)
def test_eligibility_code_contains_no_workflow_type_string_literal(
    literal: str,
) -> None:
    """Eligibility code region contains no workflow-type string literal '{literal}'.

    an earlier defect fix invariant: the eligibility engine branches on the plugin's
    declared version_semantics capability, not on workflow-type string comparisons.
    Any reintroduction of a type-literal comparison (checking for 'testing-only',
    'release', or 'document' as strings inside the eligibility function) would
    violate this invariant and indicate a regression.

    The grep is scoped to _disc_list_all_eligible_requirements, not the whole
    repo, to avoid false positives from comments that legitimately reference
    type names for documentation purposes.
    """
    source = _DISCOVERY_SH.read_text(encoding="utf-8")
    region = _extract_eligibility_region(source)

    # Search for the literal as a quoted string (single or double quotes).
    # We allow the literal to appear as a comment (# line) since comments are
    # documentation and cannot cause a type-switch regression.
    non_comment_lines = [
        line for line in region.splitlines()
        if not line.strip().startswith("#")
    ]
    non_comment_text = "\n".join(non_comment_lines)

    # Pattern: the literal appears as a quoted string value in non-comment code.
    # Match: 'testing-only', "testing-only", 'release', "release", etc.
    quoted_pattern = re.compile(
        r"""(['"])""" + re.escape(literal) + r"""(['"])"""
    )

    matches = quoted_pattern.findall(non_comment_text)
    assert not matches, (
        f"Eligibility code region contains a workflow-type string literal "
        f"{literal!r} in non-comment code. "
        f"This indicates a type-switch regression: the eligibility engine must "
        f"branch on the plugin's version_semantics capability, not on type-name "
        f"string comparisons. "
        f"Found {len(matches)} occurrence(s) in "
        f"_disc_list_all_eligible_requirements.\n"
        f"Region excerpt (non-comment lines):\n{non_comment_text[:2000]}"
    )


def test_eligibility_code_uses_version_semantics_variable() -> None:
    """Eligibility code region references the version_semantics variable.

    Confirms the fix is present: the function must read from a `version_semantics`
    variable (the capability value from the plugin manifest) rather than hardcoding
    type comparisons.

    If this test fails, the eligibility branching may have been removed entirely,
    which would be a regression back to semver-only behavior.
    """
    source = _DISCOVERY_SH.read_text(encoding="utf-8")
    region = _extract_eligibility_region(source)

    assert "version_semantics" in region, (
        "Expected 'version_semantics' to appear in the eligibility code region. "
        "The fix branches on this variable (from the plugin's capabilities manifest); "
        "its absence indicates the branching logic may have been removed."
    )
