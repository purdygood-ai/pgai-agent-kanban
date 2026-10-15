"""pgai_agent_kanban/lib/markdown_fence.py
CommonMark fenced-code-block recognition, shared by every reader that pairs
a Markdown fence opener with its closer.

The rule
--------
ANY run of three or more backticks or tildes opens a fenced block, whatever
info string follows it (```` ```text ````, ```` ```ini ````,
```` ~~~python ````, ```` ```bash title=x ````, a bare fence). The block
closes at the next line made only of the same character, at least as long as
the opener, optionally followed by whitespace. A block with no such line runs
to the end of the document.

A reader that recognised only bare and bash/sh openers would read a tagged
opener as an ordinary line and take that block's bare closer for an opener,
so the "block" it then collects swallows whatever real fence follows. Pairing
every opener with its own closer, and only afterwards asking whether the
block's info string is one the reader cares about, keeps every block's
boundaries right whatever mix of languages a document uses.

Lines are matched after ``str.strip()``, so an indented fence (one inside a
list item, say) is recognised the same as a flush-left one.

Public API
----------
match_fence_opener(stripped) -> Optional[tuple[str, str]]
    ``(fence_token, lang)`` when *stripped* opens a fenced block, else None.
    *lang* is the info string's first word, lower-cased, or ``""`` for a
    bare fence.

is_fence_closer(stripped, fence_token) -> bool
    True when *stripped* closes the block *fence_token* opened.

find_fence_close(lines, body_start, fence_token) -> int
    Index of the closing fence line at or after *body_start*, or
    ``len(lines)`` when the block is unterminated.

SHELL_FENCE_LANGS
    The info-string first words that mark a block as shell commands
    (``bash``, ``sh``). Whether a bare block also counts is each reader's
    own documented decision, not this module's.

Pure Python, no external dependencies.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

FENCE_OPENER_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")

SHELL_FENCE_LANGS = frozenset({"bash", "sh"})


def match_fence_opener(stripped: str) -> Optional[tuple[str, str]]:
    """Return ``(fence_token, lang)`` when *stripped* opens a fenced block.

    *fence_token* is the run of backticks or tildes (its first character and
    length decide which line closes the block); *lang* is the info string's
    first word, lower-cased, or ``""`` for a bare fence. Returns None for a
    line that is not a fence opener — including a backtick run whose info
    string itself contains a backtick (```` ```x``` ```` is an inline code
    span in CommonMark, not a fence).
    """
    m = FENCE_OPENER_RE.match(stripped)
    if m is None:
        return None
    fence_token, info = m.group(1), m.group(2).strip()
    if fence_token[0] == "`" and "`" in info:
        return None
    lang = info.split(None, 1)[0].lower() if info else ""
    return fence_token, lang


def is_fence_closer(stripped: str, fence_token: str) -> bool:
    """True when *stripped* closes the block that *fence_token* opened.

    A closer is made only of the opener's character, at least as many of
    them as the opener had, with nothing after it but whitespace. A longer
    run of the other fence character, or a same-character run carrying an
    info string, does not close the block.
    """
    fence_char = fence_token[0]
    return re.match(rf"^[{re.escape(fence_char)}]{{{len(fence_token)},}}\s*$", stripped) is not None


def find_fence_close(lines: Sequence[str], body_start: int, fence_token: str) -> int:
    """Return the index of the line closing *fence_token*'s block.

    The search starts at *body_start* (the line after the opener) and
    matches each line after ``str.strip()``. Returns ``len(lines)`` when no
    line closes the block, so ``lines[body_start:result]`` is always the
    block's body.
    """
    idx = body_start
    n = len(lines)
    while idx < n and not is_fence_closer(lines[idx].strip(), fence_token):
        idx += 1
    return idx
