"""
notes_status_section.py -- the ``## Status`` section of a release-notes file.

A WRITER-drafted release-notes file carries the placeholder
``PENDING-RELEASE`` as the value of its ``## Status`` section until the
release stamps the ship decision over it (Step 4e of cm/release.sh, in
team/scripts/lib/cm/release_ship_policy.sh, through
release_known_issue.sh's ``cm_stamp_release_notes_status``). This module
is the one reading of that section both sides of the stamp share: the stamp
uses it to find the line it replaces, and Step 4e uses it to decide whether
there is anything to stamp and whether the placeholder survived the stamp.

What counts as the section:

- A ``## Status`` heading (any run of spaces or tabs after ``##``, trailing
  whitespace allowed) that is not inside an HTML comment. A heading quoted
  inside a ``<!-- ... -->`` block is guidance text, not a heading.
- Its body runs from the line after the heading to the next level-1 or
  level-2 heading outside a comment, or to the end of the file.
- A file may carry more than one ``## Status`` heading; every one of them is
  a Status section.

What counts as a comment -- the way markdown renders one, read through the
shared scanner ``pgai_agent_kanban.lib.html_comments``:

- A ``<!--`` that begins a line (after optional indentation) opens a
  comment that runs to the next ``-->`` wherever it is, or to the end of
  the file when never closed. It is always a comment: such a line starts
  an HTML block and ends the paragraph before it, so no code span or
  mid-line opener above reaches across it.
- A ``<!--`` in the middle of a line is a comment only when ``-->`` closes
  it within the same paragraph (up to a blank line, a ``#`` line, or a
  line that begins with ``<!--``).
  Otherwise it is literal text: a ``<!--`` quoted in prose above the
  heading never hides ``## Status``, and never pairs with a ``-->`` in a
  later section.
- A ``<!--`` inside an inline code span (`` `<!--` ``) is literal text.

What counts as the value:

- HTML comments are invisible. A comment may sit on its own line, share a
  line with other text, or span several lines; the text inside it is never
  read as the value and never matches the placeholder. The release-notes
  template keeps a one-line guidance comment between the heading and the
  placeholder on purpose.
- The value of a Status section is its first non-blank line once comments
  are removed. Blank and comment-only lines before it are passed over.

What counts as the placeholder -- one rule for the check and the stamp:

- ``PENDING-RELEASE`` is present when it appears in the visible text of any
  Status section, bare or decorated (``**PENDING-RELEASE**``,
  ``PENDING-RELEASE (CM stamps at ship)``).
- The stamp replaces the first such occurrence, so it succeeds exactly when
  the check reports the placeholder present; a second occurrence survives
  the stamp and the check still reports it.

The placeholder word elsewhere in the file -- in the template's top-of-file
guidance comment, in a Bugs Skipped rationale that quotes an error message,
in any authored prose -- is outside every Status section and is never
counted.

Usage (import):
    from pgai_agent_kanban.cm.notes_status_section import (
        has_status_section, stamp_status_placeholder, status_section_has_placeholder,
    )
    new_text = stamp_status_placeholder(text, "KNOWN-BUGS")   # None when no placeholder is visible
    present = status_section_has_placeholder(text)
    found = has_status_section(text)   # False when no ## Status heading outside a comment

Usage (CLI, the trigger-8 pattern read by release_ship_policy_report.sh's
check_last_three_non_functional):
    python3 -m pgai_agent_kanban.cm.notes_status_section non-functional-pattern \
        <notes_dir> [--active-rc vX.Y.Z]
    Reads the Status of the three newest shipped releases (the active RC's
    own notes and any file still at PENDING-RELEASE are not shipped) and
    exits 0 when all three read NON-FUNCTIONAL, 1 otherwise or when the
    directory is missing, 2 on a usage error. Prints a "last 3 releases:"
    line, a "DETECTED" line when the pattern holds, and a
    "NON-FUNCTIONAL pattern window: a.md,b.md,c.md" line naming the three.

Usage (CLI, consumed by release_known_issue.sh's
cm_release_notes_status_has_placeholder):
    python3 -m pgai_agent_kanban.cm.notes_status_section has-placeholder <notes_file>
    Prints ``present`` when PENDING-RELEASE appears in the visible text of
    any Status section, ``absent`` when the file has a Status section and
    none of them holds it, and ``no-section`` when the reader finds no
    ``## Status`` heading outside a comment. Exits 0 for all three. Exits
    1 when the file cannot be read (the reason on stderr), 2 on a usage
    error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import Iterator, List, Optional, Sequence, Tuple

# Run by file path (``python3 .../notes_status_section.py``) or loaded from a
# file outside its package, this module has no parent package and the
# interpreter puts only its own directory on sys.path, so the package import
# below would not resolve. Put this file's own package root -- the directory
# holding the pgai_agent_kanban/ this file sits in -- at the front of sys.path
# so the import resolves from the same tree, never another. Imported as a
# package member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.html_comments import comment_spans  # noqa: E402
from pgai_agent_kanban.lib import semver  # noqa: E402

__all__ = [
    "NON_FUNCTIONAL",
    "PLACEHOLDER",
    "PatternResult",
    "has_status_section",
    "non_functional_pattern",
    "status_value",
    "status_value_token",
    "status_sections",
    "stamp_status_placeholder",
    "status_section_has_placeholder",
    "main",
]

PLACEHOLDER = "PENDING-RELEASE"
NON_FUNCTIONAL = "NON-FUNCTIONAL"

_STATUS_HEADING_RE = re.compile(r"^##[ \t]+Status[ \t]*$")
_SECTION_END_RE = re.compile(r"^#{1,2}[ \t]+\S")
# The placeholder word with any Markdown emphasis or code markers wrapped
# directly around it (``**PENDING-RELEASE**``, ``_PENDING-RELEASE_``,
# `` `PENDING-RELEASE` ``): the stamp replaces the markers with the word.
_DECORATED_PLACEHOLDER_RE = re.compile(r"[*_`]*" + re.escape(PLACEHOLDER) + r"[*_`]*")
# The leading status token of a Status value once Markdown decoration is
# stripped: "**NON-FUNCTIONAL**", "`KNOWN-BUGS` (see below)" and
# "Status: FUNCTIONAL" read as NON-FUNCTIONAL, KNOWN-BUGS and FUNCTIONAL.
_VALUE_TOKEN_RE = re.compile(r"^(?:status\s*:\s*)?[*_`\s]*([A-Za-z][A-Za-z0-9_-]*)", re.IGNORECASE)
# A release-notes file named for a version: v1.2.3.md, 1.2.3.md, v1.2.3-notes.md.
_NOTES_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)")

Segment = Tuple[str, bool]


def _line_segments(lines: Sequence[str]) -> List[List[Segment]]:
    """Split each line of *lines* into ``(text, is_comment)`` segments.

    *lines* are a file's lines as ``str.splitlines(keepends=True)`` gives
    them; a line with no line ending (other than the last) is read as if it
    ended in a newline. Comments are found over the whole text by
    ``pgai_agent_kanban.lib.html_comments.comment_spans`` -- so a quoted or
    inline-code ``<!--`` is text, a line-start ``<!--`` is always a comment,
    and a comment block spanning several lines marks each of them -- then
    cut at line boundaries. Joining a line's
    segment texts gives the line back unchanged.
    """
    pieces = []
    for index, line in enumerate(lines):
        pieces.append(line)
        if index < len(lines) - 1 and not line.endswith(("\n", "\r")):
            pieces.append("\n")
    text = "".join(pieces)
    spans = comment_spans(text, html_block_interrupts_paragraph=True)

    result: List[List[Segment]] = []
    span_index = 0
    offset = 0
    for index, line in enumerate(lines):
        line_start, line_end = offset, offset + len(line)
        while span_index < len(spans) and spans[span_index][1] <= line_start:
            span_index += 1
        segments: List[Segment] = []
        pos = line_start
        probe = span_index
        while pos < line_end:
            if probe < len(spans) and spans[probe][0] < line_end:
                span_start, span_end = spans[probe]
                if span_start > pos:
                    segments.append((text[pos:span_start], False))
                    pos = span_start
                stop = min(span_end, line_end)
                segments.append((text[pos:stop], True))
                pos = stop
                probe += 1
            else:
                segments.append((text[pos:line_end], False))
                pos = line_end
        result.append(segments)
        offset = line_end
        if index < len(lines) - 1 and not line.endswith(("\n", "\r")):
            offset += 1
    return result


def _visible_lines(lines: Sequence[str]) -> List[str]:
    """Return each line of *lines* with its HTML comment text removed."""
    return [
        "".join(text for text, is_comment in segments if not is_comment)
        for segments in _line_segments(lines)
    ]


def status_sections(lines: Sequence[str]) -> Iterator[Tuple[int, int]]:
    """Yield ``(start, end)`` line-index ranges of every Status section body.

    *lines* are as ``str.splitlines(keepends=True)`` gives them. *start* is
    the line after the ``## Status`` heading and *end* is the index of the
    next level-1 or level-2 heading (or ``len(lines)``), both judged on
    comment-free text, so the body is ``lines[start:end]``. A ``<!--``
    quoted in prose or inside an inline code span is text, not a comment
    opener, so it never hides a heading (see the module docstring).
    """
    visible = _visible_lines(lines)
    index = 0
    while index < len(visible):
        if _STATUS_HEADING_RE.match(visible[index].rstrip("\r\n")):
            start = index + 1
            end = start
            while end < len(visible) and not _SECTION_END_RE.match(visible[end]):
                end += 1
            yield start, end
            index = end
        else:
            index += 1


def _replace_visible_placeholder(line: str, segments: Sequence[Segment], target_status: str) -> str:
    """Rewrite *line* with its first visible placeholder replaced by *target_status*.

    *segments* are the line's ``(text, is_comment)`` segments from
    ``_line_segments``. A line that is the bare placeholder becomes the
    bare status value. Otherwise only the first placeholder outside the
    line's comments is replaced, together with any Markdown emphasis or
    code markers wrapped directly around it (``**PENDING-RELEASE**`` becomes
    the bare status value); comments and the rest of the text are kept.
    """
    if line.strip() == PLACEHOLDER:
        return f"{target_status}\n"
    rebuilt = []
    replaced = False
    for text, is_comment in segments:
        if not is_comment and not replaced and PLACEHOLDER in text:
            text = _DECORATED_PLACEHOLDER_RE.sub(target_status, text, count=1)
            replaced = True
        rebuilt.append(text)
    return "".join(rebuilt).rstrip() + "\n"


def stamp_status_placeholder(text: str, target_status: str) -> Optional[str]:
    """Return *text* with the Status placeholder replaced by *target_status*.

    The stamp agrees with ``status_section_has_placeholder``: whenever that
    check reports the placeholder present, this returns stamped text, and
    whenever it reports it absent, this returns None. The first line of a
    Status section whose visible text (HTML comments removed) holds
    ``PENDING-RELEASE`` is stamped -- a bare placeholder line becomes the
    bare status value, and a decorated one (``**PENDING-RELEASE**``,
    ``PENDING-RELEASE (CM stamps at ship)``) has the placeholder word and
    any emphasis wrapped around it replaced, keeping the rest of the line
    and its comments. Blank and comment-only lines are kept as they are.
    Only that first occurrence is stamped, so a second visible placeholder
    in a Status section survives for the post-stamp check to report.

    Returns None, leaving the caller's text unchanged, when no Status
    section shows the placeholder -- an already-stamped file, a concrete
    value, or no ``## Status`` heading at all. A ``<!--`` quoted in prose or
    inline code anywhere in *text* is text, so it never hides the section
    or its placeholder from the stamp.
    """
    lines = text.splitlines(keepends=True)
    line_segments = _line_segments(lines)
    visible = _visible_lines(lines)
    for start, end in status_sections(lines):
        for index in range(start, end):
            if PLACEHOLDER not in visible[index]:
                continue
            lines[index] = _replace_visible_placeholder(lines[index], line_segments[index], target_status)
            return "".join(lines)
    return None


def has_status_section(text: str) -> bool:
    """Whether *text* has at least one ``## Status`` heading outside a comment."""
    return any(True for _ in status_sections(text.splitlines(keepends=True)))


def status_section_has_placeholder(text: str) -> bool:
    """Whether ``PENDING-RELEASE`` appears in the visible text of any Status section.

    Comments are read as markdown renders them (see the module docstring):
    a ``<!--`` quoted in prose or inline code is text, so the Status section
    below it is still read.
    """
    lines = text.splitlines(keepends=True)
    visible = _visible_lines(lines)
    return any(
        PLACEHOLDER in visible[index]
        for start, end in status_sections(lines)
        for index in range(start, end)
    )


def status_value(text: str) -> Optional[str]:
    """Return the value of the first Status section in *text*, or None.

    The value is the section's first non-blank line once HTML comments are
    removed (see the module docstring), stripped of surrounding whitespace.
    None when there is no ``## Status`` heading outside a comment or the
    section holds no visible text.
    """
    lines = text.splitlines(keepends=True)
    visible = _visible_lines(lines)
    for start, end in status_sections(lines):
        for index in range(start, end):
            value = visible[index].strip()
            if value:
                return value
        return None
    return None


def status_value_token(text: str) -> Optional[str]:
    """Return the leading status token of *text*'s Status value, upper-cased.

    Markdown emphasis and code markers and a ``Status:`` label before the
    token are ignored, and so is anything after it, so ``**NON-FUNCTIONAL**``
    and ``NON-FUNCTIONAL (pattern noted)`` both read ``NON-FUNCTIONAL``.
    None when there is no Status value or it starts with no word.
    """
    value = status_value(text)
    if value is None:
        return None
    match = _VALUE_TOKEN_RE.match(value)
    return match.group(1).upper() if match else None


class PatternResult:
    """The outcome of ``non_functional_pattern``.

    *detected* is True when the three newest shipped releases all read
    NON-FUNCTIONAL. *window* names those three notes files, oldest first
    (empty when fewer than three shipped releases exist). *lines* are the
    narration lines the CLI prints.
    """

    def __init__(self, detected: bool, window: List[str], lines: List[str]) -> None:
        self.detected = detected
        self.window = window
        self.lines = lines


def _notes_version(path: pathlib.Path) -> Optional[Tuple[int, int, int]]:
    match = _NOTES_VERSION_RE.match(path.name)
    if not match:
        return None
    return semver.parse(".".join(match.groups()))


def non_functional_pattern(notes_dir: pathlib.Path, active_rc: Optional[str] = None) -> PatternResult:
    """Decide whether the last three shipped releases were all NON-FUNCTIONAL.

    Every ``<version>.md`` file in *notes_dir* is a candidate, ordered by
    semantic version. Two kinds of file are not a shipped release and are
    passed over: the notes of *active_rc* (the RC being released now), and
    any file whose Status section still shows the ``PENDING-RELEASE``
    placeholder. Each remaining file's Status is read as its leading status
    token (``status_value_token``), so the template's guidance comment kept
    above the value, and Markdown emphasis around it, never hide it.
    """
    active = semver.parse(active_rc) if active_rc else None
    shipped: List[Tuple[Tuple[int, int, int], pathlib.Path, str]] = []
    for path in notes_dir.glob("*.md"):
        version = _notes_version(path)
        if version is None or (active is not None and version == active):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            shipped.append((version, path, "UNKNOWN"))
            continue
        if status_section_has_placeholder(text):
            continue
        shipped.append((version, path, status_value_token(text) or "UNKNOWN"))
    shipped.sort(key=lambda item: item[0])

    if len(shipped) < 3:
        return PatternResult(False, [], [
            f"NON-FUNCTIONAL pattern check: only {len(shipped)} shipped release(s) found — need at least 3.",
        ])
    last_three = shipped[-3:]
    pairs = ", ".join(f"{path.name}={status}" for _, path, status in last_three)
    lines = [f"NON-FUNCTIONAL pattern check: last 3 releases: {pairs}"]
    window = [path.name for _, path, _ in last_three]
    detected = all(status == NON_FUNCTIONAL for _, _, status in last_three)
    if detected:
        lines.append("NON-FUNCTIONAL pattern DETECTED: all 3 consecutive releases marked NON-FUNCTIONAL.")
    lines.append(f"NON-FUNCTIONAL pattern window: {','.join(window)}")
    return PatternResult(detected, window, lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point; see the module docstring for the contract."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.notes_status_section",
        description=(
            "Read the '## Status' section of a release-notes file. "
            "has-placeholder prints 'present' when PENDING-RELEASE appears in "
            "the visible (non-comment) text of a '## Status' section, "
            "'absent' when no Status section holds it, and 'no-section' when "
            "there is no '## Status' heading outside an HTML comment; the "
            "word anywhere else in the file is not counted. A '<!--' quoted "
            "in prose or inline code is text, not a comment. Exits 0 for all "
            "three, 1 when the file cannot be read. non-functional-pattern "
            "exits 0 when the last three shipped releases in a release-notes "
            "directory all read NON-FUNCTIONAL, 1 otherwise."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser(
        "has-placeholder",
        help="print 'present', 'absent' or 'no-section' for the '## Status' placeholder",
    )
    check.add_argument("notes_file", help="path to a release-notes markdown file")
    pattern = subparsers.add_parser(
        "non-functional-pattern",
        help=(
            "exit 0 when the last three shipped releases in NOTES_DIR all read "
            "NON-FUNCTIONAL, 1 otherwise; prints the releases read and a "
            "'NON-FUNCTIONAL pattern window: a.md,b.md,c.md' line"
        ),
    )
    pattern.add_argument("notes_dir", help="the release-notes/ directory of the dev tree")
    pattern.add_argument(
        "--active-rc",
        default=None,
        help="version of the RC being released (vX.Y.Z); its own notes are not a shipped release",
    )
    args = parser.parse_args(argv)

    if args.command == "non-functional-pattern":
        notes_dir = pathlib.Path(args.notes_dir)
        if not notes_dir.is_dir():
            print("NON-FUNCTIONAL pattern check: release-notes/ directory not found — skipping check.")
            return 1
        try:
            result = non_functional_pattern(notes_dir, args.active_rc)
        except ValueError as exc:
            print(f"ERROR: --active-rc must be a version like v1.2.3: {exc}", file=sys.stderr)
            return 2
        for line in result.lines:
            print(line)
        return 0 if result.detected else 1

    notes_path = pathlib.Path(args.notes_file)
    try:
        text = notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"ERROR: cannot read release notes {notes_path}: {exc}", file=sys.stderr)
        return 1
    if status_section_has_placeholder(text):
        print("present")
    elif has_status_section(text):
        print("absent")
    else:
        print("no-section")
    return 0


if __name__ == "__main__":
    sys.exit(main())
