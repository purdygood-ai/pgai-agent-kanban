"""
release_notes_date.py — the date a release-notes file carries.

The one date rule for a CHANGELOG entry, imported by both renderers of
CHANGELOG.md: the release-time changelog writer
(pgai_agent_kanban.cm.changelog_writer) and the operator's full rebuild
(pgai_agent_kanban.cm.regenerate_changelog, behind
scripts/regenerate-changelog.sh).

The rule: a notes file's date is the value after its **Release Date:** line;
with none, the value after its **Date:** line; with neither, there is no
date, one warning naming the file goes to stderr, and the caller renders the
entry's date as "unknown". **Release Date:** wins when both are present.
Field names match case-sensitively; spacing inside and after the bold
markers is free.

Usage (import):
    from pgai_agent_kanban.cm.release_notes_date import read_release_date
    date = read_release_date(text, notes_path)   # "" when the file has none
"""

from __future__ import annotations

import pathlib
import re
import sys

# A notes file's date line. **Release Date:** wins when both are present.
RELEASE_DATE_RE = re.compile(r"\*\*\s*Release\s+Date\s*:\s*\*\*\s*(\S+)")
DATE_RE = re.compile(r"\*\*\s*Date\s*:\s*\*\*\s*(\S+)")


def find_release_date(text: str) -> str:
    """Return the date in notes *text*, or "" when it has no date line.

    Reads the **Release Date:** line first and the **Date:** line only when
    there is none. Pure: prints nothing.
    """
    date_match = RELEASE_DATE_RE.search(text) or DATE_RE.search(text)
    return date_match.group(1) if date_match else ""


def warn_missing_date(notes_path: pathlib.Path) -> None:
    """Warn on stderr that *notes_path* carries no date line.

    Never raises: a missing date must not stop the CHANGELOG step; the entry
    still renders, with "unknown" as its date.
    """
    print(
        f"Warning: {notes_path}: no **Release Date:** or **Date:** line — "
        f"its CHANGELOG entry date renders as 'unknown'",
        file=sys.stderr,
    )


def read_release_date(text: str, notes_path: pathlib.Path) -> str:
    """Return the date in *text*, the content of *notes_path*.

    Returns "" when the file has neither date line, after writing exactly
    one warning naming *notes_path* to stderr; the caller renders that entry's
    date as "unknown".
    """
    date = find_release_date(text)
    if not date:
        warn_missing_date(notes_path)
    return date
