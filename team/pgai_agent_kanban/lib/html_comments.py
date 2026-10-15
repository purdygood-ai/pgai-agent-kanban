"""
html_comments.py -- find the HTML comments in markdown text the way markdown
renders them.

Every reader that must tell comment text from visible text -- a field value
under a heading, the ``## Status`` section of a release-notes file -- asks
this module rather than carrying its own scanner, so two readers never
disagree about what a reader of the rendered page would see.

The rule
--------
- An opener (``<!--``) that begins a line, after optional indentation,
  starts a comment block. It runs to the next ``-->`` wherever that is, so
  a heading-like line inside it is comment text, not a heading. Left open,
  it runs to the end of the text.
- An opener in the middle of a line is a comment only when ``-->`` closes
  it within the same paragraph, which ends at a blank line or at a line
  that starts with ``#``. Otherwise it is literal text, so a ``<!--``
  quoted in prose never swallows a later section and never cuts its own
  line short.
- Inside an inline code span -- text between matching backtick runs in the
  same paragraph -- ``<!--`` is literal text. A backtick run with no
  matching run in its paragraph is literal text too.
- A comment's delimiters belong to the comment.

One option narrows what a paragraph is. With
``html_block_interrupts_paragraph=True`` a line that begins with ``<!--``
also ends the paragraph before it, as a markdown HTML block does, so
neither an inline code span nor a mid-line opener ever reaches across a
line-start comment: every line-start opener outside a comment is a
comment. A reader that must treat every line-start comment exactly as a
comment -- the release-notes Status reader -- asks for that. The default
leaves paragraphs ending only at blank and ``#`` lines.

Text with no comment has no spans and strips to itself, unchanged.

Public API
----------
comment_spans(text, *, html_block_interrupts_paragraph=False) -> list[tuple[int, int]]
    Half-open ``(start, end)`` character offsets of every comment in
    *text*, in order and non-overlapping.
strip_html_comments(text, *, html_block_interrupts_paragraph=False) -> str
    *text* with every comment span removed.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

__all__ = ["comment_spans", "strip_html_comments"]

_COMMENT_OPEN = "<!--"
_COMMENT_CLOSE = "-->"
# The tokens the comment scan stops at: a backtick run (a possible inline
# code span) or a comment opener.
_COMMENT_SCAN_RE = re.compile(r"`+|<!--")
# Where a paragraph ends: a blank line, or a line that starts a heading.
_PARAGRAPH_END_RE = re.compile(r"\n[ \t]*(?:\n|#)")
# The same, plus a line that starts a comment (an HTML block).
_PARAGRAPH_OR_HTML_BLOCK_END_RE = re.compile(r"\n[ \t]*(?:\n|#|<!--)")


def _closing_backticks_end(text: str, start: int, stop: int, run: int) -> Optional[int]:
    """End offset of the first run of exactly *run* backticks in
    ``text[start:stop]``, or None when the code span is never closed."""
    closing = re.compile(r"(?<!`)`{%d}(?!`)" % run)
    m = closing.search(text, start, stop)
    return m.end() if m else None


def comment_spans(text: str, *, html_block_interrupts_paragraph: bool = False) -> List[Tuple[int, int]]:
    """Return the ``(start, end)`` offsets of every HTML comment in *text*.

    Comments are found as the module docstring describes: a line-start
    opener runs to the next ``-->`` (or the end of *text*), a mid-line
    opener counts only when closed within its own paragraph, and an opener
    inside an inline code span is text. With
    *html_block_interrupts_paragraph* a line-start opener also ends the
    paragraph before it, so it is always a comment. Each span covers the
    comment's delimiters, so ``text[start:end]`` is the whole comment.
    Spans are in order and never overlap; text with no comment yields an
    empty list.
    """
    paragraph_end_re = _PARAGRAPH_OR_HTML_BLOCK_END_RE if html_block_interrupts_paragraph else _PARAGRAPH_END_RE
    spans: List[Tuple[int, int]] = []
    pos = 0
    while True:
        m = _COMMENT_SCAN_RE.search(text, pos)
        if m is None:
            return spans
        start = m.start()
        para_end_m = paragraph_end_re.search(text, start)
        para_end = para_end_m.start() if para_end_m else len(text)
        if m.group().startswith("`"):
            # A code span is skipped whole; an unmatched run is literal.
            span_end = _closing_backticks_end(text, m.end(), para_end, len(m.group()))
            pos = span_end if span_end is not None else m.end()
            continue
        line_start = text.rfind("\n", 0, start) + 1
        begins_line = not text[line_start:start].strip()
        close = text.find(_COMMENT_CLOSE, m.end(), len(text) if begins_line else para_end)
        if close != -1:
            pos = close + len(_COMMENT_CLOSE)
            spans.append((start, pos))
        elif begins_line:
            spans.append((start, len(text)))
            return spans
        else:
            pos = m.end()


def strip_html_comments(text: str, *, html_block_interrupts_paragraph: bool = False) -> str:
    """Return *text* with its HTML comments (see :func:`comment_spans`) removed."""
    out: List[str] = []
    pos = 0
    for start, end in comment_spans(text, html_block_interrupts_paragraph=html_block_interrupts_paragraph):
        out.append(text[pos:start])
        pos = end
    out.append(text[pos:])
    return "".join(out)
