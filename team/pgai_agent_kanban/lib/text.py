"""
text.py -- the one home for flattening a text onto a single line.

A value printed into a ``key=value`` line, a tab-separated record, a log
line or an inbox field must not carry a line break, or the reader sees two
records where one was written. Four flattenings are in use, and each
lives here once:

  one_line(value)          every whitespace run becomes one space and the
                           ends are stripped: ``"a\\n  b\\t"`` -> ``"a b"``.
                           The default; use it unless spacing is data.
  join_lines(text)         each line break, with the whitespace around it,
                           becomes one space and the ends are stripped;
                           spacing inside a line is kept:
                           ``"a  b\\n  c"`` -> ``"a  b c"``.
  breaks_to_spaces(text)   each CR and each LF becomes one space and
                           nothing else changes, for a value whose spacing
                           is data (a path, a status line read back
                           verbatim): ``" a\\r\\nb "`` -> ``" a  b "``.
  lf_to_spaces(value)      each LF becomes one space and nothing else
                           changes, CR included, for a field of a
                           one-value-per-line record:
                           ``"a\\r\\nb"`` -> ``"a\\r b"``.

``one_line`` and ``lf_to_spaces`` read any value through ``str()``, so
``None`` reads as ``"None"``; a caller for whom a missing value means an
empty line passes ``value or ""``. The other two take a string.

Usage (import)::

    from pgai_agent_kanban.lib.text import one_line
    print(f"reason={one_line(exc)}")
"""

from __future__ import annotations

import re

__all__ = ["breaks_to_spaces", "join_lines", "lf_to_spaces", "one_line"]

#: A run of line breaks and the whitespace on either side of it.
_BREAK_RUN_RE = re.compile(r"\s*[\r\n]+\s*")


def one_line(value: object) -> str:
    """*value* as one line: every run of whitespace becomes one space and
    the ends are stripped. Any value is read through ``str()``."""
    return " ".join(str(value).split())


def join_lines(text: str) -> str:
    """*text* with each line break, and the whitespace around it, replaced
    by one space, then stripped; spacing within a line is kept."""
    return _BREAK_RUN_RE.sub(" ", text).strip()


def breaks_to_spaces(text: str) -> str:
    """*text* with each CR and each LF replaced by one space; every other
    character, leading and trailing whitespace included, is kept."""
    return text.replace("\r", " ").replace("\n", " ")


def lf_to_spaces(value: object) -> str:
    """*value* read through ``str()``, with each LF replaced by one space;
    every other character, CR and leading or trailing whitespace included,
    is kept."""
    return str(value).replace("\n", " ")
