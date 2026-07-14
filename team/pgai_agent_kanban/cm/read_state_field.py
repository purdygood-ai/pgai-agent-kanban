#!/usr/bin/env python3
"""
read_state_field.py — Read a named heading field from a markdown state file.

This module is an as-is extraction of behavior previously embedded in the three
cm/*.sh scripts (team/scripts/cm/release.sh, team/scripts/cm/open-rc.sh,
team/scripts/cm/cancel-rc.sh) as inline Python heredocs. The scanning logic is
preserved byte-for-byte — do NOT change regex or whitespace-stripping behavior
without filing a separate bug. Any greedy-regex quirks in the original are
intentionally kept.

Horizontal-rule handling: treat a horizontal rule ('---', three or more dashes on
its own line) as a section boundary, identical to a '## ' heading line.  This
prevents a markdown '---' rule from being returned as the field value when the
real value is absent or the rule immediately follows the value.

Usage (CLI):
    python3 read_state_field.py <filepath> <heading>

Usage (import):
    from pgai_agent_kanban.cm.read_state_field import read_state_field
    value = read_state_field("team/release-state.md", "Active RC")

Both forms return/print the first non-blank, non-comment line after the
"## <heading>" marker in the file, or the literal string "none" when:
  - the file does not exist, or
  - the heading is not found, or
  - no qualifying value follows the heading before the next heading or EOF.
"""

import argparse
import pathlib
import re
import sys


def _is_horizontal_rule(line: str) -> bool:
    """Return True when *line* is a markdown horizontal rule (three or more dashes).

    Matches a stripped line composed entirely of three or more dash characters,
    optionally interspersed with spaces (CommonMark HR syntax).  Used by
    read_state_field to treat '---' as a section boundary.
    """
    return bool(re.fullmatch(r"-{3,}", line))


def read_state_field(filepath: str, heading: str) -> str:
    """Read a named field from a markdown state file.

    Finds the line "## <heading>" in the file at *filepath*, then returns the
    first non-blank, non-comment line that follows it.  Returns the literal
    string ``"none"`` when the file does not exist, the heading is absent, or
    no qualifying value line exists after the heading.

    Section boundaries: scanning stops (without returning a value) when a line
    starting with ``#`` or a horizontal rule (``---``, three or more dashes) is
    encountered before a qualifying value.  The ``---`` boundary was added by
    so a markdown horizontal rule is never returned as the
    field value.

    This is an as-is extraction of the ``read_md_field`` heredoc previously
    embedded in team/scripts/cm/release.sh, with the horizontal-rule
    boundary added.

    Args:
        filepath: Path to the markdown file (string or path-like).
        heading:  The heading name to search for (without the leading "## ").

    Returns:
        The field value as a stripped string, or ``"none"``.
    """
    p = pathlib.Path(filepath)
    if not p.exists():
        return "none"
    text = p.read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            for follow in lines[i + 1:]:
                v = follow.strip()
                # A horizontal rule ('---', three or more dashes) is treated as
                # a section boundary.  Stop here without returning a
                # value rather than returning '---' as the field value.
                if v and _is_horizontal_rule(v):
                    break
                if v and not v.startswith("#"):
                    return v
            break
    return "none"


def main() -> None:
    """CLI entry point: print the field value for the given file and heading."""
    parser = argparse.ArgumentParser(
        description=(
            "Read a named heading field from a markdown state file. "
            "Prints the value of the first non-blank, non-comment line after "
            "the '## <heading>' marker, or 'none' when the file is missing or "
            "the heading is not found."
        ),
    )
    parser.add_argument(
        "filepath",
        help="Path to the markdown file to read.",
    )
    parser.add_argument(
        "heading",
        help=(
            "The heading name to look up (without the leading '## '). "
            "Example: 'Active RC' matches the line '## Active RC'."
        ),
    )
    args = parser.parse_args()
    print(read_state_field(args.filepath, args.heading))


if __name__ == "__main__":
    main()
