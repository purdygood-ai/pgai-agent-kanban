"""
doc_markers.py — read and replace a generated block between marker lines.

The docs generators (operator verbs, dashboard windows, measured pace) each
own a block of a document fenced by a BEGIN and an END marker. These are the
one reader and the one writer of such a block.

Public API:
    MarkerNotFoundError
    extract_between_markers(content, begin_marker, end_marker) -> str
    replace_between_markers(content, begin_marker, end_marker, new_inner) -> str
"""

from __future__ import annotations

__all__ = ["MarkerNotFoundError", "extract_between_markers", "replace_between_markers"]


class MarkerNotFoundError(ValueError):
    """A required BEGIN/END marker is missing from the content."""


def _marker_span(content: str, begin_marker: str, end_marker: str) -> tuple[int, int]:
    """Start of *begin_marker* and end of the first *end_marker* after it."""
    begin_idx = content.find(begin_marker)
    if begin_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {begin_marker!r}")
    end_idx = content.find(end_marker, begin_idx)
    if end_idx == -1:
        raise MarkerNotFoundError(f"marker not found: {end_marker!r}")
    return begin_idx, end_idx + len(end_marker)


def extract_between_markers(content: str, begin_marker: str, end_marker: str) -> str:
    """Return the text from *begin_marker* through *end_marker*, both inclusive.

    Raises:
        MarkerNotFoundError: either marker is not present.
    """
    begin_idx, end_idx = _marker_span(content, begin_marker, end_marker)
    return content[begin_idx:end_idx]


def replace_between_markers(content: str, begin_marker: str, end_marker: str, new_inner: str) -> str:
    """Return *content* with the text from *begin_marker* through *end_marker*
    (both inclusive) replaced by *new_inner*, which carries its own marker lines.

    Raises:
        MarkerNotFoundError: either marker is not present.
    """
    begin_idx, end_idx = _marker_span(content, begin_marker, end_marker)
    return content[:begin_idx] + new_inner + content[end_idx:]
