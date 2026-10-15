"""
test_html_comment_spans.py
==========================
pgai_agent_kanban.lib.html_comments finds HTML comments the way markdown
renders them, for every reader that must tell comment text from visible
text.

Coverage:
  - A line-start opener is a comment block to the next ``-->``, or to the
    end of the text when never closed; spans cover the delimiters.
  - A mid-line opener is a comment only when closed within its paragraph;
    one quoted in prose or inside an inline code span is text.
  - strip_html_comments removes exactly the spans comment_spans reports,
    and text with no comment comes back unchanged.
  - With html_block_interrupts_paragraph, a line-start opener ends the
    paragraph above it, so a stray backtick there never turns the comment
    into code-span text; without it, paragraphs end only at blank and
    ``#`` lines.
  - project_summary's field reader strips comments through this module.

Pure string tests; nothing touches the filesystem.
"""

from __future__ import annotations

import pytest

from pgai_agent_kanban.lib.html_comments import comment_spans, strip_html_comments
from pgai_agent_kanban.reports import project_summary


def _comments(text: str, **kwargs) -> list[str]:
    return [text[start:end] for start, end in comment_spans(text, **kwargs)]


def test_line_start_comment_block_runs_to_its_closer_across_lines():
    text = "Intro.\n<!--\n## Status\nguidance\n-->\nValue\n"
    assert _comments(text) == ["<!--\n## Status\nguidance\n-->"]
    assert strip_html_comments(text) == "Intro.\n\nValue\n"


def test_unclosed_line_start_comment_runs_to_the_end_of_the_text():
    text = "Value\n  <!-- never closed\n## Later\n"
    assert _comments(text) == ["<!-- never closed\n## Later\n"]
    assert strip_html_comments(text) == "Value\n  "


def test_mid_line_comment_closed_in_its_paragraph_is_a_comment():
    text = "keep <!-- one --> this <!-- two\nlines --> too\n"
    assert _comments(text) == ["<!-- one -->", "<!-- two\nlines -->"]
    assert strip_html_comments(text) == "keep  this  too\n"


@pytest.mark.parametrize(
    "text",
    [
        "A quoted `<!--` opener.\n\n## Status\nPENDING-RELEASE\n",
        "A quoted `<!--` opener.\n\n## Status\nPENDING-RELEASE\n\n## Notes\nends -->\n",
        "Open one with <!-- in prose.\n\n## Status\nPENDING-RELEASE\n\n## Notes\nends -->\n",
        "Use ``<!-- x -->`` literally.\n",
    ],
    ids=["inline-code-unclosed", "inline-code-later-close", "prose-later-close", "double-backtick-span"],
)
def test_quoted_or_inline_code_opener_is_text(text):
    assert comment_spans(text) == []
    assert strip_html_comments(text) == text


def test_text_without_comments_is_unchanged():
    text = "# Notes\n\n## Status\nFUNCTIONAL\n\nSome `code` and ``more``.\n"
    assert comment_spans(text) == []
    assert strip_html_comments(text) == text


def test_html_block_option_keeps_a_line_start_comment_after_a_stray_backtick():
    text = "A stray ` backtick.\n<!-- hidden ` -->\nshown\n"
    assert comment_spans(text) == [], "by default the backticks pair into one code span"
    assert _comments(text, html_block_interrupts_paragraph=True) == ["<!-- hidden ` -->"]
    assert strip_html_comments(text, html_block_interrupts_paragraph=True) == "A stray ` backtick.\n\nshown\n"


def test_html_block_option_ends_a_mid_line_opener_paragraph_at_a_line_start_comment():
    text = "prose <!-- quoted\n<!-- real -->\nafter -->\n"
    assert _comments(text) == ["<!-- quoted\n<!-- real -->"]
    assert _comments(text, html_block_interrupts_paragraph=True) == ["<!-- real -->"]


def test_project_summary_field_reader_uses_the_shared_scanner():
    text = "## Symptom\nA quoted `<!--` opener in prose.\n\n## Expected\nWorks. -->\n"
    assert project_summary._strip_html_comments(text) == strip_html_comments(text) == text
    assert project_summary._extract_field(text, "Symptom") == "A quoted `<!--` opener in prose."
