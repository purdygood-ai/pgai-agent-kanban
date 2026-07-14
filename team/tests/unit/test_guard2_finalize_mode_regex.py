"""
test_guard2_finalize_mode_regex.py
===================================
Regression pin for the Guard 2 regex in close_intake_on_finalize_report
(team/scripts/lib/wake_common.sh).

Guard 2 detects whether a TESTER task README's ## Constraints section
contains a ``finalize_mode: report`` entry.  The pm_materialize.format_list()
function writes every constraint entry with a leading "- " bullet prefix, so
the Guard 2 regex must accept that prefix.

These tests drive pm_materialize.format_list() directly — not a hand-written
literal string — so a future change to the bullet prefix format invalidates
the test rather than silently letting the mismatch regress again.

The tests also confirm that:
  - the corrected regex does NOT match the bullet-prefixed line when
    ``finalize_mode: report-x`` is used (trailing anchor preserved).
  - the old narrow regex (no bullet allowance) fails against the actual
    format_list() output, making the regression class visible.
"""

from __future__ import annotations

import pathlib
import re
import sys

# ---------------------------------------------------------------------------
# Import with installed-package / direct-checkout fallback
# ---------------------------------------------------------------------------
try:
    import pm_agent.pm_materialize as pm  # installed via pm_agent package
except ImportError:
    sys.path.insert(
        0, str(pathlib.Path(__file__).parent.parent.parent / "pm-agent")
    )
    import pm_materialize as pm  # type: ignore[no-redef]


# ---------------------------------------------------------------------------
# Regexes under test
# ---------------------------------------------------------------------------

# The corrected Guard 2 regex (as shipped in wake_common.sh after this fix).
_GUARD2_REGEX = r"^\s*[-*]?\s*finalize_mode\s*:\s*report\s*$"

# The old, broken regex (no bullet allowance) — kept here so the test
# explicitly documents the regression class it prevents.
_OLD_GUARD2_REGEX = r"^\s*finalize_mode\s*:\s*report\s*$"

# The constraint list that pm_materialize writes for testing-only TESTER tasks.
_CONSTRAINT_LIST = ["tester_operation: verify-and-report", "finalize_mode: report"]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_format_list_output_matches_corrected_guard2_regex() -> None:
    """Corrected Guard 2 regex matches format_list() output for the finalize=report constraint list.

    This is the primary regression pin: pm_materialize.format_list() is called
    with the real constraint list, and the corrected regex must match the
    resulting text.  If format_list() changes its bullet prefix and the regex
    is not updated, this test fails — catching the mismatch before it reaches
    production.
    """
    body = pm.format_list(_CONSTRAINT_LIST)
    assert re.search(_GUARD2_REGEX, body, re.M), (
        f"Corrected Guard 2 regex {_GUARD2_REGEX!r} did not match "
        f"format_list() output:\n{body!r}\n"
        "The regex must accept the bullet prefix format_list() uses."
    )


def test_old_guard2_regex_does_not_match_format_list_output() -> None:
    """Old Guard 2 regex (no bullet allowance) fails against format_list() output.

    Documents the regression class: the old regex silently skipped intake
    closure on every real testing-only run because it required no leading
    bullet marker, but format_list() always produces one.
    """
    body = pm.format_list(_CONSTRAINT_LIST)
    assert not re.search(_OLD_GUARD2_REGEX, body, re.M), (
        f"Old Guard 2 regex {_OLD_GUARD2_REGEX!r} unexpectedly matched "
        f"format_list() output:\n{body!r}\n"
        "This means format_list() no longer produces a bullet prefix — "
        "update the new regex accordingly."
    )


def test_corrected_guard2_regex_matches_bullet_dash_format() -> None:
    """Corrected regex matches '- finalize_mode: report' (dash bullet, space-separated)."""
    line = "- finalize_mode: report"
    assert re.search(_GUARD2_REGEX, line, re.M), (
        f"Regex {_GUARD2_REGEX!r} did not match dash-bullet line {line!r}"
    )


def test_corrected_guard2_regex_matches_bullet_star_format() -> None:
    """Corrected regex matches '* finalize_mode: report' (star bullet, space-separated)."""
    line = "* finalize_mode: report"
    assert re.search(_GUARD2_REGEX, line, re.M), (
        f"Regex {_GUARD2_REGEX!r} did not match star-bullet line {line!r}"
    )


def test_corrected_guard2_regex_matches_no_bullet_format() -> None:
    """Corrected regex still matches 'finalize_mode: report' (no bullet prefix).

    The bullet marker is optional ([-*]?) so bare entries remain valid — this
    preserves backward compatibility with any hand-authored README that lacks
    the bullet.
    """
    line = "finalize_mode: report"
    assert re.search(_GUARD2_REGEX, line, re.M), (
        f"Regex {_GUARD2_REGEX!r} did not match bare (no-bullet) line {line!r}"
    )


def test_corrected_guard2_regex_does_not_match_partial_suffix() -> None:
    """Corrected regex does not match 'finalize_mode: report-x' (trailing anchor preserved).

    The fix must not weaken the trailing anchor so that a line like
    'finalize_mode: report-extended' starts matching.  This test pins that
    the '-x' suffix is rejected.
    """
    line = "- finalize_mode: report-x"
    assert not re.search(_GUARD2_REGEX, line, re.M), (
        f"Regex {_GUARD2_REGEX!r} incorrectly matched line with suffix: {line!r}"
    )


def test_corrected_guard2_regex_does_not_match_report_only_partial() -> None:
    """Corrected regex does not match a line containing only 'report' without the key."""
    line = "- report"
    assert not re.search(_GUARD2_REGEX, line, re.M), (
        f"Regex {_GUARD2_REGEX!r} incorrectly matched line {line!r}"
    )


def test_format_list_produces_bullet_prefix() -> None:
    """format_list() produces a '- ' bullet prefix for each entry in the list.

    This is a direct behavioral assertion on pm_materialize.format_list()
    confirming that the bug source (the bullet prefix) exists and has not
    been removed.  If this assertion fails, the Guard 2 regex and this test
    file need to be re-evaluated together.
    """
    body = pm.format_list(_CONSTRAINT_LIST)
    lines = body.splitlines()
    for line in lines:
        assert line.startswith("- "), (
            f"format_list() produced a line without '- ' bullet prefix: {line!r}\n"
            "If the bullet prefix changed, update Guard 2 regex and this test."
        )
