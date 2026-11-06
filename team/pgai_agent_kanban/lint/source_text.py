"""
source_text.py — small source-text helpers the structural lints share.

Public API:
    mask_comment_lines(text) -> str
    violation_causes(violations) -> list[str]
"""

from __future__ import annotations

from typing import Protocol
from collections.abc import Iterable

__all__ = ["mask_comment_lines", "violation_causes"]


class _Violation(Protocol):
    path: object
    line: int
    kind: str
    snippet: str


def mask_comment_lines(text: str) -> str:
    """Return *text* with every comment-only line blanked out.

    A line is a comment line when its first non-whitespace character is
    ``#`` (the line-comment convention bash and Python share). The line is
    emptied, not removed, so line numbers derived from match offsets stay
    right.
    """
    return "\n".join("" if line.lstrip().startswith("#") else line for line in text.splitlines())


def violation_causes(violations: Iterable[_Violation]) -> list[str]:
    """One ``<path>:<line>: <kind> — <snippet>`` cause line per violation."""
    return [f"{v.path}:{v.line}: {v.kind} — {v.snippet!r}" for v in violations]
