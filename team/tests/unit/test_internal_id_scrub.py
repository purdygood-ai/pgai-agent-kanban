"""
test_internal_id_scrub.py
==========================
Direct unit tests for team/scripts/lib/cm/internal_id_scrub.py's
_strip_internal_ids(), exercising the scrub itself rather than one of its
three importing call sites (the release-notes generator module
pgai_agent_kanban.cm.release_notes_generator,
release_known_issue.sh's build_known_issue_notes_block, and
release_known_issue.sh's build_bugs_skipped_notes_block -- all covered
separately, from the caller side, by test_cm_known_issue_carry_forward_bash.py
and test_release_notes_content_described.py).

_strip_internal_ids replaces a removed identifier with the module's neutral
stand-in text, _ID_REPLACEMENT_TEXT ("another item"), instead of deleting it
outright. Deletion left the surrounding sentence broken -- "Deferred because
CODER-YYYYMMDD-NNN-some-slug already covers the area." collapsed to
"Deferred because already covers the area." -- clean of the identifier, but
unparseable prose. This module tests that every sentence position an
identifier can occupy still reads afterward, and that the identifier itself
(including its kebab-case slug) never survives regardless of position.

Covers:
  1. An identifier alone, as the whole input.
  2. An identifier mid-clause, after a conjunction.
  3. An identifier inside a parenthetical, alone and alongside label text.
  4. An identifier immediately before a sentence-ending period.
  5. Text with no identifier at all -- byte-identical passthrough.
  6. The exact worked sentence from this scrub's own defect report, asserted
     against its exact expected output string.
  7. Both BUG-/PRIORITY-NNNN and the six agent-prefixed task-ID shapes.
  8. Multiple identifiers in one input.
  9. The abbreviated <ROLE>-NNN task reference, including a possessive, and
     a longer multi-segment identifier the short form must not claim.
 10. An internal requirements-document path, with and without backticks.

Test naming describes behavior, not bug or ticket IDs (roles/TESTER.md
Anti-pattern 6). No filesystem or subprocess interaction -- this module
calls _strip_internal_ids() as a plain Python function.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

# internal_id_scrub.py is a standalone module (no package __init__ around
# it) that ships beside the three .sh files that import it via
# PGAI_CM_INTERNAL_ID_SCRUB_DIR -- see that module's own docstring, "HOW A
# HEREDOC READS THIS FILE". A plain Python unit test reaches it the same
# way any of those heredocs do: put its directory on sys.path and import it
# by name.
_CM_LIB_DIR = pathlib.Path(__file__).parent.parent.parent / "scripts" / "lib" / "cm"
sys.path.insert(0, str(_CM_LIB_DIR))
from internal_id_scrub import _strip_internal_ids  # noqa: E402


# ---------------------------------------------------------------------------
# Sentence positions an identifier can occupy
# ---------------------------------------------------------------------------


def test_identifier_alone_in_a_sentence():
    result = _strip_internal_ids("CODER-20260101-001-some-slug")
    assert result == "another item"
    assert "CODER-20260101-001" not in result
    assert "some-slug" not in result


def test_identifier_mid_clause_after_a_conjunction():
    result = _strip_internal_ids(
        "Deferred because CODER-20260101-001-some-slug already covers the area."
    )
    assert result == "Deferred because another item already covers the area."


def test_identifier_inside_a_parenthetical_alone():
    result = _strip_internal_ids("(CODER-20260101-001-some-slug)")
    assert result == "(another item)"


def test_identifier_inside_a_parenthetical_with_label_text():
    result = _strip_internal_ids("(Source Task: CODER-20260101-001-some-slug)")
    assert result == "(Source Task: another item)"


def test_identifier_at_the_end_of_a_sentence_before_the_period():
    result = _strip_internal_ids(
        "This bug was already reported as CODER-20260101-001-some-slug."
    )
    assert result == "This bug was already reported as another item."


def test_text_with_no_identifier_at_all_is_returned_unchanged():
    text = "A perfectly ordinary sentence with no identifiers of any kind."
    assert _strip_internal_ids(text) == text


# ---------------------------------------------------------------------------
# The scrub's own worked example: the exact sentence a rationale collapses
# into unparseable prose when the task ID is cut out without a replacement
# noun. Asserted on the exact expected
# string, not on absence of the identifier alone.
# ---------------------------------------------------------------------------


def test_defect_report_worked_example_reads_correctly_in_english():
    result = _strip_internal_ids(
        "Deferred because CODER-20260101-001-some-slug already covers the area."
    )
    assert result == "Deferred because another item already covers the area."
    assert "CODER-20260101-001" not in result
    assert "some-slug" not in result


# ---------------------------------------------------------------------------
# Token shapes: BUG-/PRIORITY-NNNN and every agent-prefixed task-ID shape
# ---------------------------------------------------------------------------


def test_bug_id_mid_sentence_is_replaced():
    result = _strip_internal_ids("Restored the guard removed during BUG-0011.")
    assert result == "Restored the guard removed during another item."


def test_priority_id_mid_sentence_is_replaced():
    result = _strip_internal_ids("Tracked by PRIORITY-0042 for now.")
    assert result == "Tracked by another item for now."


@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_every_agent_prefixed_task_id_is_replaced_and_its_slug_removed(prefix):
    task_id = f"{prefix}-20260910-011-verify-2-14-2"
    result = _strip_internal_ids(f"Deferred; see {task_id} for the root cause.")
    assert task_id not in result
    assert prefix + "-20260910" not in result
    assert "-verify-2-14-2" not in result
    assert result == "Deferred; see another item for the root cause."


# ---------------------------------------------------------------------------
# Multiple identifiers and cleanup behavior
# ---------------------------------------------------------------------------


def test_multiple_identifiers_in_one_sentence_are_each_replaced():
    result = _strip_internal_ids(
        "See BUG-0001 and BUG-0002 for the two earlier attempts."
    )
    assert result == "See another item and another item for the two earlier attempts."


def test_no_double_spaces_or_stray_punctuation_survive_removal():
    result = _strip_internal_ids("Deferred (see CODER-20260101-001-some-slug) for now.")
    assert "  " not in result
    assert result == "Deferred (see another item) for now."


# ---------------------------------------------------------------------------
# Abbreviated task references and requirements-document paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["PM", "CODER", "WRITER", "CM", "TESTER", "PO"])
def test_abbreviated_task_reference_is_replaced(prefix):
    result = _strip_internal_ids(f"Raised by {prefix}-016's status Summary; confirmed by probe.")
    assert f"{prefix}-016" not in result
    assert result == "Raised by another item's status Summary; confirmed by probe."


def test_abbreviated_task_reference_is_matched_case_insensitively():
    result = _strip_internal_ids("Seen in tester-012 and CODER-7.")
    assert result == "Seen in another item and another item."


def test_short_form_does_not_claim_a_longer_multi_segment_identifier():
    text = "The fixture CODER-001-002 is a prefix-matching sample."
    assert _strip_internal_ids(text) == text


@pytest.mark.parametrize(
    "citation",
    ["`requirements/v2.26.0-agent-metrics-v2.md`", "requirements/v2.26.0-agent-metrics-v2.md"],
    ids=["backticked", "bare"],
)
def test_requirements_document_path_is_replaced(citation):
    result = _strip_internal_ids(f"The v2.26.0 requirement ({citation}, Intent) exists because x.")
    assert "requirements/" not in result
    assert result == (
        "The v2.26.0 requirement (the release's requirements document, Intent) exists because x."
    )
