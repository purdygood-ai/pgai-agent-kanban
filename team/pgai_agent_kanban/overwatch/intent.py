"""
intent.py — Reader for a project's INTENT.md, the operator-authored
declared-intent ledger OVERWATCH's Lane B matches a HALT's evidence
against before it will ship past that HALT on its own judgment.

This module is read-only: it never creates, writes, or repairs
INTENT.md. It exposes one entry record type (``IntentEntry``) and a
handful of read functions — a path-based parser (``parse_entries``) and
a project-scoped convenience wrapper (``read_project_intent``) that
resolves the standard ``<kanban_root>/projects/<project>/INTENT.md``
path and parses it.

Entry grammar (one bullet per line, under a "## Entries" heading):

    - YYYY-MM-DD: <declaration text>

- The date is the day the operator made the decision, not necessarily
  the day the file was edited. It must be a real calendar date.
- The declaration is free text explaining the deliberate state in
  enough detail that a matcher can judge whether it fully explains a
  given piece of evidence.
- One entry per line. No continuation lines, no nested bullets, no
  entries inside fenced code blocks (a fenced block is how the
  template's own worked example stays inert).

Parsing is fail-closed throughout: an absent file, an unreadable file,
a file with no "## Entries" heading, or a file whose only content
under that heading is commentary (the shipped zero-entry template) all
parse to an empty list. A line that only partially matches the grammar
— no date, an uncalendared date, an empty declaration, or a body that
spills onto a following line instead of terminating on its own — is
dropped whole rather than parsed as a truncated fragment; a malformed
entry must never surface as a partial match for anything downstream.
Well-formed entries elsewhere in the same file are unaffected by a
malformed neighbor.

Command line (``python3 -m pgai_agent_kanban.overwatch.intent``):

  declares INTENT_FILE REASON
      Exit 0 when some entry's declaration text is exactly REASON, 1
      otherwise (including an absent or unreadable file). Prints nothing.
      cm-release reads this to admit OVERWATCH's Lane B ship path.

A wrong argument count prints the usage on stderr and exits 2; ``--help``
prints this text and exits 0.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List

# ---------------------------------------------------------------------------
# Entry model
# ---------------------------------------------------------------------------


@dataclass
class IntentEntry:
    """One operator-declared entry from INTENT.md.

    Attributes:
        date: The entry's ``YYYY-MM-DD`` date, exactly as written. Only
            ever set to a string that parsed as a real calendar date.
        declaration: The free-text declaration following the date, with
            surrounding whitespace stripped.
    """

    date: str
    declaration: str


# ---------------------------------------------------------------------------
# Grammar
# ---------------------------------------------------------------------------

_ENTRIES_HEADING_RE = re.compile(r"^##\s+Entries\s*$")
_ANY_HEADING_RE = re.compile(r"^##\s+\S")
_FENCE_RE = re.compile(r"^\s*```")
_BLANK_RE = re.compile(r"^\s*$")
_BULLET_START_RE = re.compile(r"^\s*-")
_ENTRY_LINE_RE = re.compile(
    r"^-\s+(?P<date>\d{4}-\d{2}-\d{2}):\s+(?P<declaration>\S.*)$"
)


def _is_real_date(value: str) -> bool:
    """Return True when *value* parses as a real YYYY-MM-DD calendar date
    (rejects shapes like 2026-13-40 that match the digit pattern but name
    no actual day).
    """
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _entries_section_lines(lines: List[str]) -> List[str]:
    """Return the lines strictly between a "## Entries" heading and the
    next "##" heading (or end of file). Returns [] when no "## Entries"
    heading is present — a file that never declares an Entries section
    has no entries to find, by construction rather than by accident.
    """
    start = None
    for index, line in enumerate(lines):
        if _ENTRIES_HEADING_RE.match(line):
            start = index + 1
            break
    if start is None:
        return []

    end = len(lines)
    for index in range(start, len(lines)):
        if _ANY_HEADING_RE.match(lines[index]):
            end = index
            break
    return lines[start:end]


def _parse_section(section: List[str]) -> List[IntentEntry]:
    """Parse the pre-sliced "## Entries" section body into entries."""
    entries: List[IntentEntry] = []
    in_fence = False
    index = 0
    while index < len(section):
        line = section[index]

        if _FENCE_RE.match(line):
            in_fence = not in_fence
            index += 1
            continue
        if in_fence:
            index += 1
            continue

        match = _ENTRY_LINE_RE.match(line)
        if not match:
            index += 1
            continue

        # The grammar forbids continuation lines. If the line right after
        # a bullet is neither blank, a fence marker, nor another bullet,
        # it is a continuation attempt — an operator wrapping a long
        # line, most likely. The whole entry is dropped: taking only the
        # bullet line as the declaration would silently truncate text the
        # operator wrote as one statement, which is exactly the
        # partially-parsed-entry failure mode this reader must avoid.
        next_index = index + 1
        has_continuation = False
        scan = next_index
        while scan < len(section):
            candidate = section[scan]
            if _BLANK_RE.match(candidate) or _FENCE_RE.match(candidate) or _BULLET_START_RE.match(candidate):
                break
            has_continuation = True
            scan += 1

        if has_continuation:
            index = scan
            continue

        date_str = match.group("date")
        declaration = match.group("declaration").strip()
        if _is_real_date(date_str) and declaration:
            entries.append(IntentEntry(date=date_str, declaration=declaration))

        index = next_index

    return entries


# ---------------------------------------------------------------------------
# Public read path
# ---------------------------------------------------------------------------


def parse_entries(intent_path: str) -> List[IntentEntry]:
    """Parse the INTENT.md at *intent_path* into a list of IntentEntry, in
    file (top-to-bottom) order.

    Returns [] — never raises — for a missing file, a file that cannot be
    opened (permission error, not a regular file, decoding failure), a
    file with no "## Entries" heading, or a file whose Entries section
    has no lines matching the entry grammar (the shipped zero-entry
    template's shape). This function performs no writes of any kind.
    """
    try:
        with open(intent_path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except (OSError, UnicodeDecodeError):
        return []

    section = _entries_section_lines(text.splitlines())
    return _parse_section(section)


def intent_path_for_project(kanban_root: str, project_name: str) -> str:
    """Return the standard path: <kanban_root>/projects/<project_name>/INTENT.md."""
    return os.path.join(kanban_root, "projects", project_name, "INTENT.md")


def read_project_intent(kanban_root: str, project_name: str) -> List[IntentEntry]:
    """Resolve *project_name*'s standard INTENT.md path under *kanban_root*
    and parse it. Same fail-closed behavior as parse_entries(): an absent
    file, an unreadable file, or a file with zero live entries all return
    [] rather than raising.
    """
    return parse_entries(intent_path_for_project(kanban_root, project_name))


def declares(intent_path: str, declaration: str) -> bool:
    """True when some entry of the INTENT.md at *intent_path* declares exactly
    *declaration* (fail-closed, as parse_entries())."""
    return any(entry.declaration == declaration for entry in parse_entries(intent_path))


def main(argv: "List[str] | None" = None) -> int:
    """Command-line entry point; see the module docstring."""
    args = sys.argv[1:] if argv is None else argv
    if args and args[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if len(args) != 3 or args[0] != "declares":
        print("usage: python3 -m pgai_agent_kanban.overwatch.intent declares INTENT_FILE REASON", file=sys.stderr)
        return 2
    return 0 if declares(args[1], args[2]) else 1


if __name__ == "__main__":
    sys.exit(main())
