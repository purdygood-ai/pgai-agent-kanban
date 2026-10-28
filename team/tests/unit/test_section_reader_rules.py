"""
test_section_reader_rules.py
============================
The rules of ``task_status.read_section``, the one ``## Heading`` section
reader, each pinned by behaviour:

  * fenced code (``` and ~~~): with ``fences`` a ``## `` line inside a block
    neither starts nor ends a section; by default it does, as in
    ``replace_section``;
  * blank lines between heading and value, CRLF input, a missing heading
    (``None``) versus an empty value (``""``), a heading on the last line
    with and without a trailing newline, an inline ``## Heading: value``;
  * where a section ends: the next ``##``, not a ``###`` subheading unless
    ``end_at_subheading``, never a ``---`` rule;
  * ``exact_heading``, ``comments`` and ``last``, the readings
    ``cm/read_state_field`` and the repeated-event readers need;
  * ``heading_prefix``, the task-state reading ``ops/verdict`` needs;
  * the readers built on it: ``md_fields.read_body`` and the release-state
    field readers return what the reader returns under their documented
    readings, and ``replace_section`` agrees with the default reading on
    where a section starts and ends.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pgai_agent_kanban.cm.read_state_field import read_state_field
from pgai_agent_kanban.lib.md_fields import read_body
from pgai_agent_kanban.task_status import read_section, replace_section
from tests.fixtures.release_state_section import release_state_section_value

# ---------------------------------------------------------------------------
# Fenced code blocks
# ---------------------------------------------------------------------------


def _fenced(fence: str, closer: str = "") -> str:
    """A Summary whose fenced example carries a heading-shaped line."""
    return (
        "# Status\n\n"
        "## Summary\n"
        "Example below.\n"
        f"{fence}\n"
        "## State\n"
        "WORKING\n"
        "# a comment line\n"
        f"{closer or fence}\n"
        "After the example.\n\n"
        "## State\n"
        "DONE\n"
    )


@pytest.mark.parametrize(
    "fence, closer",
    [("```", "```"), ("~~~", "~~~"), ("```markdown", "```"), ("~~~~", "~~~~")],
    ids=["backticks", "tildes", "tagged", "long_tildes"],
)
def test_a_heading_line_inside_a_fence_neither_starts_nor_ends_a_section(fence: str, closer: str) -> None:
    text = _fenced(fence, closer)
    assert read_section(text, "State", fences=True) == "DONE"
    summary = read_section(text, "Summary", fences=True)
    assert summary is not None
    assert summary.startswith("Example below.")
    assert summary.endswith("After the example.")
    # The fenced lines come back exactly as written, '#' characters included.
    assert "## State\nWORKING\n# a comment line" in summary


@pytest.mark.parametrize("fence", ["```", "~~~"], ids=["backticks", "tildes"])
def test_by_default_a_fenced_heading_line_is_read_like_replace_section_reads_it(fence: str) -> None:
    text = _fenced(fence)
    assert read_section(text, "State") == f"WORKING\n# a comment line\n{fence}\nAfter the example."
    assert read_section(text, "Summary") == f"Example below.\n{fence}"


def test_an_unclosed_fence_runs_to_the_end_of_the_text() -> None:
    text = "## Summary\n```\n## State\nDONE\n"
    assert read_section(text, "State", fences=True) is None
    assert read_section(text, "Summary", fences=True) == "```\n## State\nDONE"


def test_a_fence_closer_must_match_its_opener() -> None:
    text = "## Summary\n````\n```\n## State\nWORKING\n````\n\n## State\nDONE\n"
    assert read_section(text, "State", fences=True) == "DONE"


# ---------------------------------------------------------------------------
# Value shapes: blank lines, CRLF, missing, empty, end of file, inline
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, heading, expected",
    [
        pytest.param("## State\n\n\nDONE\n\n## Role\nCODER\n", "State", "DONE", id="blank_lines_before_value"),
        pytest.param("## State\r\nDONE\r\n\r\n## Role\r\nCODER\r\n", "State", "DONE", id="crlf"),
        pytest.param("## Summary\r\none\r\ntwo\r\n", "Summary", "one\ntwo", id="crlf_body_returns_lf"),
        pytest.param("## Role\nCODER\n", "State", None, id="missing_heading_is_none"),
        pytest.param("", "State", None, id="empty_text_is_none"),
        pytest.param("## State\n\n## Role\nCODER\n", "State", "", id="empty_value_is_empty_not_next_section"),
        pytest.param("## State\n## Role\nCODER\n", "State", "", id="next_heading_directly_after"),
        pytest.param("## Role\nCODER\n\n## State", "State", "", id="heading_on_last_line_no_newline"),
        pytest.param("## Role\nCODER\n\n## State\n", "State", "", id="heading_on_last_line_with_newline"),
        pytest.param("## Role\nCODER\n\n## State  \t", "State", "", id="heading_on_last_line_trailing_space"),
        pytest.param("## State: DONE\n\n## Role\nCODER\n", "State", "DONE", id="inline_value"),
        pytest.param("## Notes: first\nsecond\n", "Notes", "first\nsecond", id="inline_value_then_body"),
        pytest.param("##\tstate  \nDONE\n", "State", "DONE", id="tab_and_case_in_heading"),
        pytest.param("## State Notes\nx\n## Status\ny\n", "State", None, id="heading_matched_whole"),
    ],
)
def test_value_shapes(text: str, heading: str, expected) -> None:
    assert read_section(text, heading) == expected


# ---------------------------------------------------------------------------
# Where a section ends
# ---------------------------------------------------------------------------

_WITH_SUBHEADING = "## Summary\nfirst\n\n### Detail\nsecond\n\n## Blockers\nnone\n"


def test_a_subheading_stays_inside_the_body_by_default() -> None:
    assert read_section(_WITH_SUBHEADING, "Summary") == "first\n\n### Detail\nsecond"


def test_end_at_subheading_ends_the_body_at_a_subheading() -> None:
    assert read_section(_WITH_SUBHEADING, "Summary", end_at_subheading=True) == "first"
    assert read_section("## Field\n### Sub\nvalue\n", "Field", end_at_subheading=True) == ""
    assert read_section(_WITH_SUBHEADING, "Blockers", end_at_subheading=True) == "none"


def test_a_horizontal_rule_does_not_end_a_section() -> None:
    assert read_section("## Active RC\n---\nv1.2.3\n", "Active RC") == "---\nv1.2.3"
    assert read_section("## Active RC\n---\nv1.2.3\n", "Active RC", exact_heading=True) == "---\nv1.2.3"


# ---------------------------------------------------------------------------
# exact_heading — the release-state reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param("## Active RC\nv1.2.3\n", "v1.2.3", id="exact"),
        pytest.param("## Active RC  \nv1.2.3\n", "v1.2.3", id="trailing_space"),
        pytest.param("  ## Active RC\nv1.2.3\n", "v1.2.3", id="indented_heading"),
        pytest.param("## active rc\nv1.2.3\n", None, id="case_counts"),
        pytest.param("##  Active RC\nv1.2.3\n", None, id="one_space_only"),
        pytest.param("##\tActive RC\nv1.2.3\n", None, id="no_tab"),
        pytest.param("## Active RC: v1.2.3\n", None, id="no_inline_value"),
        pytest.param("## Active RC\n### Sub\nv1.2.3\n", "", id="subheading_ends"),
        pytest.param("## Active RC\n  ## Next\nv1.2.3\n", "", id="indented_heading_ends"),
        pytest.param("## active rc\nx\n## Active RC\nv1.2.3\n", "v1.2.3", id="first_exact_match_wins"),
    ],
)
def test_exact_heading(text: str, expected) -> None:
    assert read_section(text, "Active RC", exact_heading=True) == expected


# ---------------------------------------------------------------------------
# heading_prefix — the verdict engine's task-state reading
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        pytest.param("## State\nDONE\n", "DONE", id="whole_name"),
        pytest.param("## States\nWORKING\n", "WORKING", id="prefix_of_a_longer_name"),
        pytest.param("## state notes\nBLOCKED\n", "BLOCKED", id="any_letter_case"),
        pytest.param("##\tState\nDONE\n", "DONE", id="tab_after_hashes"),
        pytest.param("## State: DONE\nWORKING\n", "WORKING", id="no_inline_value"),
        pytest.param("## State\n### Sub\nDONE\n", "### Sub\nDONE", id="subheading_stays"),
        pytest.param("## State\n## Summary\nDONE\n", "", id="empty_body"),
        pytest.param("## Stat\nDONE\n", None, id="shorter_name_is_absent"),
        pytest.param("## Summary\n## State\r\nDONE\r\n", "DONE", id="crlf"),
    ],
)
def test_heading_prefix(text: str, expected) -> None:
    assert read_section(text, "State", heading_prefix=True) == expected


def test_heading_prefix_is_a_heading_reading_of_its_own() -> None:
    with pytest.raises(ValueError):
        read_section("## State\nDONE\n", "State", heading_prefix=True, exact_heading=True)
    with pytest.raises(ValueError):
        read_section("## State\nDONE\n", "State", heading_prefix=True, free_spacing=True)


# ---------------------------------------------------------------------------
# comments — HTML comment blocks
# ---------------------------------------------------------------------------


def test_a_heading_line_inside_an_html_comment_neither_starts_nor_ends_a_section() -> None:
    text = "## Target\n<!-- a comment\n## Next\n-->\nactual\n\n## Next\nreal\n"
    assert read_section(text, "Target", comments=True) == "<!-- a comment\n## Next\n-->\nactual"
    assert read_section(text, "Next", comments=True) == "real"
    # Without comments the commented line is an ordinary heading.
    assert read_section(text, "Target") == "<!-- a comment"


def test_an_unclosed_html_comment_runs_to_the_end_of_the_text() -> None:
    text = "## Field\n<!-- never closed\n## Other\nx\n"
    assert read_section(text, "Field", comments=True) == "<!-- never closed\n## Other\nx"
    assert read_section(text, "Other", comments=True) is None


def test_a_one_line_html_comment_does_not_open_a_block() -> None:
    text = "## Field\n<!-- a | b -->\nvalue\n## Other\nx\n"
    assert read_section(text, "Field", comments=True) == "<!-- a | b -->\nvalue"
    assert read_section(text, "Other", comments=True) == "x"


# ---------------------------------------------------------------------------
# last — repeated headings
# ---------------------------------------------------------------------------

_DECISIONS = (
    "## Active RC\nv1.2.0\n\n"
    "## OVERWATCH Decision\nLane: a\nOutcome: first\n\n"
    "## Last Released\nv1.1.0\n\n"
    "## OVERWATCH Decision\nLane: b\nOutcome: second\n"
    "## OVERWATCH Decision\nLane: c\nOutcome: third\n"
)


def test_a_repeated_heading_reads_its_first_section_by_default() -> None:
    assert read_section(_DECISIONS, "OVERWATCH Decision") == "Lane: a\nOutcome: first"


def test_last_reads_the_last_section_of_a_repeated_heading() -> None:
    assert read_section(_DECISIONS, "OVERWATCH Decision", last=True) == "Lane: c\nOutcome: third"
    assert read_section(_DECISIONS, "Active RC", last=True) == "v1.2.0"
    assert read_section(_DECISIONS, "Absent", last=True) is None


# ---------------------------------------------------------------------------
# The readers built on read_section
# ---------------------------------------------------------------------------

_READER_FIXTURES = [
    "## PM Task\n\nPM-20260101-001-x\n\n## Status\nopen\n",
    "## Status: running\n",
    "## Goal\nFirst line.\n\n### Detail\nSecond.\n## Next\nx\n",
    "## PM Task\n## Status\nopen\n",
    "## Role\r\nCODER\r\n",
    "# Title\n",
]


@pytest.mark.parametrize("text", _READER_FIXTURES)
@pytest.mark.parametrize("heading", ["PM Task", "Status", "Goal", "Role"])
def test_md_fields_read_body_is_the_subheading_reading(tmp_path: Path, text: str, heading: str) -> None:
    md = tmp_path / "README.md"
    md.write_bytes(text.encode("utf-8"))
    assert read_body(md, heading) == read_section(text, heading, end_at_subheading=True)


@pytest.mark.parametrize(
    "text, expected_section, expected_field",
    [
        pytest.param("## Active RC\nv1.2.3\n", "v1.2.3", "v1.2.3", id="value"),
        pytest.param("## Active RC\n<!-- a\n## x\n-->\nv1.2.3\n", "v1.2.3", "v1.2.3", id="comment_block"),
        pytest.param("## Active RC\n---\nv1.2.3\n", "", "none", id="rule_ends_the_field"),
        pytest.param("## Active RC\r\n\r\nv1.2.3\r\n", "v1.2.3", "v1.2.3", id="crlf"),
        pytest.param("## Active RC", "", "none", id="heading_at_eof"),
        pytest.param("## active rc\nv1.2.3\n", None, "none", id="heading_case_counts"),
    ],
)
def test_read_state_field_readers_take_the_section_from_the_reader(
    tmp_path: Path, text: str, expected_section, expected_field: str
) -> None:
    md = tmp_path / "release-state.md"
    md.write_bytes(text.encode("utf-8"))
    assert release_state_section_value(str(md), "Active RC") == expected_section
    assert read_state_field(str(md), "Active RC") == expected_field


_WRITER_FIXTURES = [
    "# Status\n\n## State\nWORKING\n\n## Summary\nold\n\n### Detail\nmore\n\n## Blockers\nnone\n",
    "## State: WORKING\n## Summary\n\n## Blockers\nnone",
    "## Summary\n---\nold\n## State\n\n\nWORKING\n## Blockers\nnone\n",
]


@pytest.mark.parametrize("text", _WRITER_FIXTURES)
@pytest.mark.parametrize("heading", ["State", "Summary", "Blockers"])
def test_replace_section_and_read_section_agree_on_the_section(text: str, heading: str) -> None:
    assert read_section(text, heading) is not None, f"fixture carries no ## {heading}"
    others = {h: read_section(text, h) for h in ("State", "Summary", "Blockers") if h != heading}
    rewritten = replace_section(text, heading, "new body\nsecond line")
    assert read_section(rewritten, heading) == "new body\nsecond line"
    for other, body in others.items():
        assert read_section(rewritten, other) == body, other
