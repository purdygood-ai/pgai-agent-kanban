"""
test_report_template_freshness_agreement.py
=============================================
Regression coverage for the mandatory Deep-Verify Freshness reporting
contract between the two documents that jointly define it:

  - team/templates/agent/REPORT-TEMPLATE.md -- the structural template a
    TESTER report is written from.
  - team/roles/TESTER.md -- Step 5d (the freshness lane's own account) and
    Step 9's report-fields list (the other side of the agreement).

The '## Deep-Verify Freshness' section previously went missing from the
template without a single test failing -- nothing in the suite noticed the
drift. These tests close that gap: they fail when the section disappears or
is renamed in either document, and they fail when the two documents stop
agreeing on the verdict vocabulary (`built`, `failed-to-build`,
`could-not-attempt`) the section is built on.

Assertions run directly against the shipped documents, never against a copied
fixture -- a fixture that duplicates their prose would only drift in its own
right and stop testing anything real.

Test names describe behavior under test, never bug/task IDs (roles/TESTER.md,
Test Authoring Guidelines, anti-pattern 6).
"""

from __future__ import annotations

import pathlib
import re

import pytest

# ---------------------------------------------------------------------------
# Paths (relative to team/, which is the CWD when run via run-unit-tests.sh;
# team/tests/unit/<this file> -> parent.parent.parent = team/)
# ---------------------------------------------------------------------------

_TEAM_DIR = pathlib.Path(__file__).parent.parent.parent
_REPORT_TEMPLATE = _TEAM_DIR / "templates" / "agent" / "REPORT-TEMPLATE.md"
_TESTER_ROLE = _TEAM_DIR / "roles" / "TESTER.md"

_HEADING = "## Deep-Verify Freshness"
_VERDICT_WORDS = ("built", "failed-to-build", "could-not-attempt")


def _read(path: pathlib.Path) -> str:
    assert path.is_file(), f"expected file not found: {path}"
    return path.read_text(encoding="utf-8")


def _heading_lines(text: str) -> list[str]:
    """Return every markdown heading line (a line starting with '#') in *text*,
    stripped of trailing whitespace. Used to distinguish an actual section
    heading from a mention of the same words inside ordinary prose."""
    return [line.rstrip() for line in text.splitlines() if line.lstrip().startswith("#")]


def _extract_step9_section(text: str) -> str:
    """Return the body of TESTER.md's '## Step 9' section, up to the next
    top-level '## Step N' heading. Step 9 is where the report-fields bullet
    list lives -- the passage this task's requirement targets, distinct from
    Step 5d's freshness-lane narrative earlier in the same file."""
    match = re.search(
        r"^## Step 9 [^\n]*\n(.*?)(?=^## Step \d|\Z)",
        text,
        re.MULTILINE | re.DOTALL,
    )
    assert match, f"{_TESTER_ROLE}: could not locate a '## Step 9' section."
    return match.group(1)


# ---------------------------------------------------------------------------
# (a) The template defines the section as an actual heading.
# ---------------------------------------------------------------------------


def test_report_template_carries_the_deep_verify_freshness_heading() -> None:
    """REPORT-TEMPLATE.md defines '## Deep-Verify Freshness' as a real section
    heading (not merely a mention inside surrounding prose)."""
    text = _read(_REPORT_TEMPLATE)
    headings = _heading_lines(text)
    assert _HEADING in headings, (
        f"{_REPORT_TEMPLATE}: missing the '{_HEADING}' section heading. "
        "The mandatory Deep-Verify Freshness section appears to have been "
        "removed or renamed."
    )


# ---------------------------------------------------------------------------
# (b) TESTER.md's report-fields bullet list names the identical heading.
# ---------------------------------------------------------------------------


def test_tester_role_names_the_same_heading_in_its_report_fields_list() -> None:
    """TESTER.md Step 9's report-fields bullet list names the section with the
    exact same '## Deep-Verify Freshness' string the template uses as its
    heading -- not a paraphrase, not a different heading level, not a
    reworded mention."""
    text = _read(_TESTER_ROLE)
    step9_body = _extract_step9_section(text)
    quoted_heading = f"`{_HEADING}`"
    assert quoted_heading in step9_body, (
        f"{_TESTER_ROLE}: Step 9's report-fields list does not name the "
        f"section as {quoted_heading}. It has drifted from "
        f"{_REPORT_TEMPLATE}'s '{_HEADING}' heading."
    )


# ---------------------------------------------------------------------------
# (c) The three verdict words are spelled identically in both documents.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", _VERDICT_WORDS)
def test_verdict_word_is_spelled_identically_in_both_documents(word: str) -> None:
    """Each of the three deep-verify verdict words -- built, failed-to-build,
    could-not-attempt -- appears verbatim, in backtick-quoted form, in both
    the report template and TESTER.md. A hyphenation change, a spelling
    change, or a dropped word in either document fails this check."""
    template_text = _read(_REPORT_TEMPLATE)
    tester_text = _read(_TESTER_ROLE)
    quoted_word = f"`{word}`"

    assert quoted_word in template_text, (
        f"{_REPORT_TEMPLATE}: expected verdict word {quoted_word} not found "
        "verbatim. The verdict vocabulary must match TESTER.md exactly."
    )
    assert quoted_word in tester_text, (
        f"{_TESTER_ROLE}: expected verdict word {quoted_word} not found "
        f"verbatim. The verdict vocabulary must match {_REPORT_TEMPLATE} exactly."
    )
