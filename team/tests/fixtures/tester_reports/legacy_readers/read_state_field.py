# TEST-ONLY LEGACY COPY -- imported by no production code.
#
# A frozen excerpt of team/pgai_agent_kanban/cm/read_state_field.py: the
# one "## <heading>" field reader the CM ship-policy report reader takes a
# report's ## Recommendation and ## Systemic Risk values through, as it read
# before the report reading moved onto pgai_agent_kanban.reports.
# tester_report. It is kept for this release only, so that
# team/tests/unit/test_tester_report_differential.py can run the old reader
# over the TESTER report corpus; it is deleted together with that test once
# the readers are migrated. The code below is the behaviour being recorded:
# do not edit it. Only read_state_field and the walk it calls are copied,
# verbatim; release-state validation is not a report reader.
"""Legacy markdown "## <heading>" field reader (test-only copy)."""

from __future__ import annotations

import pathlib
import re


# Sentinels distinguishing "heading not found at all" from "heading found but
# no qualifying value follows it before the next boundary". Neither is the
# string "none" — that string is reserved for a legitimate field value.
_ABSENT = object()
_EMPTY = object()

# HTML comment delimiters. A block opening on one line and closing on a later
# one is skipped as a unit; a complete "<!-- ... -->" inside a single line is
# removed from that line before its remainder is considered as a value.
_HTML_COMMENT_OPEN = "<!--"
_HTML_COMMENT_CLOSE = "-->"
_HTML_COMMENT_INLINE_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def _is_horizontal_rule(line: str) -> bool:
    """Return True when *line* is a markdown horizontal rule (three or more dashes).

    Matches a stripped line composed entirely of three or more dash characters,
    optionally interspersed with spaces (CommonMark HR syntax).  Used by
    read_state_field to treat '---' as a section boundary.
    """
    return bool(re.fullmatch(r"-{3,}", line))


def _scan_field_raw(text: str, heading: str):
    """Scan *text* for "## <heading>" and return its raw result.

    Returns one of:
      - the extracted value string (stripped), which may legitimately be the
        literal text "none";
      - the module sentinel ``_EMPTY`` when the heading is found but no
        qualifying non-blank, non-comment line follows it before the next
        heading (a line whose stripped text starts with ``##``), a
        horizontal rule, or EOF — a "comment" here is a single-``#`` line or
        any part of an HTML ``<!-- ... -->`` block;
      - the module sentinel ``_ABSENT`` when "## <heading>" is never found.

    Every reader in this module calls this one walk, so the validating API
    can tell "no heading" apart from "heading with no value" apart from
    "heading holding the literal value 'none'" — the distinction
    ``read_state_field()``'s own collapsed "none" sentinel cannot express —
    and no reader ever takes a value from the section after the field's own.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == f"## {heading}":
            in_html_comment = False
            for follow in lines[i + 1:]:
                v = follow.strip()
                # An HTML comment block is skipped whole, however many lines
                # it spans; an inline "<!-- ... -->" is dropped from a value
                # line so the text beside it is still read as the value.
                if in_html_comment:
                    if _HTML_COMMENT_CLOSE in v:
                        in_html_comment = False
                        v = v.split(_HTML_COMMENT_CLOSE, 1)[1].strip()
                    else:
                        continue
                v = _HTML_COMMENT_INLINE_RE.sub("", v).strip()
                if v.startswith(_HTML_COMMENT_OPEN):
                    in_html_comment = True
                    continue
                # A horizontal rule ('---', three or more dashes) is treated as
                # a section boundary.  Stop here without returning a
                # value rather than returning '---' as the field value.
                if v and _is_horizontal_rule(v):
                    break
                # The next '##' heading (any depth) ends the section, so an
                # empty field never reads the value of the field after it.
                if v.startswith("##"):
                    break
                if v and not v.startswith("#"):
                    return v
            return _EMPTY
    return _ABSENT


def read_state_field(filepath: str, heading: str) -> str:
    """Read a named field from a markdown state file.

    Finds the line "## <heading>" in the file at *filepath*, then returns the
    first non-blank, non-comment line that follows it.  Returns the literal
    string ``"none"`` when the file does not exist, the heading is absent, or
    no qualifying value line exists after the heading.

    Section boundaries: scanning stops (without returning a value) at the
    next ``##`` heading or a horizontal rule (``---``, three or more dashes)
    met before a qualifying value, so a heading with no value reads as
    ``"none"`` — never as the next field's value — and a markdown horizontal
    rule is never returned as the field value.  Single-``#`` comment lines
    and HTML comment blocks (``<!-- ... -->``, single- or multi-line) are
    skipped, not treated as boundaries, so a template's "allowed values"
    comment under a heading never becomes the value.

    The walk is shared with ``read_state_section``; the two differ only in
    that this one folds the absent and empty cases into ``"none"``.

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
    result = _scan_field_raw(text, heading)
    if result is _ABSENT or result is _EMPTY:
        return "none"
    return result

