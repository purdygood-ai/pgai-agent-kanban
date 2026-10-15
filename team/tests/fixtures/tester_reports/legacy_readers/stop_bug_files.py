# TEST-ONLY LEGACY COPY -- imported by no production code.
#
# A frozen copy of team/pgai_agent_kanban/cm/stop_bug_files.py as it read
# before its report reading moved onto pgai_agent_kanban.reports.tester_report.
# It is kept for this release only, so that
# team/tests/unit/test_tester_report_differential.py can run the old reader
# over the TESTER report corpus; it is deleted together with that test once
# the readers are migrated. The code below is the behaviour being recorded:
# do not edit it. The only change from the original is this comment.

"""
stop_bug_files.py -- resolve the bug files behind a ship-policy stop.

When the project's ship posture lets a release ship through a ship-policy
trigger that would otherwise HALT it (team/scripts/lib/cm/
release_ship_posture.sh), the release notes' ``## Known Issues`` section
names the bugs behind that stop. This module finds them: it reads the
TESTER report's finding blocks, keeps the findings that caused the stop,
and resolves the ``BUG-NNNN`` id each one's own Filed As field names to
the bug file on disk.

Which findings caused the stop depends on the trigger:

``systemic-risk-high``
    Trigger 2 -- every finding whose own ``**Systemic Risk:**`` value is
    ``high``.
``fix-effort-large``
    Trigger 3 -- every finding whose own ``**Fix Effort:**`` value is
    ``large``.

A finding block starts at a finding heading and ends at the next heading
no deeper than its own, or at the next finding heading of any depth,
whichever comes first. What counts as a finding heading, and where a
block ends, is one rule, stated beside finding_heading_level below, and
one splitter, finding_sections, applies it; finding_blocks is the same
split with each block's heading dropped. Every reader of a TESTER
report's findings splits through that one splitter: this module (the
Known Issues stop list), pgai_agent_kanban.cm.skipped_bug_pairs (the
release notes' Bugs Skipped table), the SHIP-KNOWN-ISSUE bug resolver
(read_ship_policy_known_issue_bug_files, release_known_issue.sh), and the
ship-policy matrix's finding reader (read_ship_policy_finding_dimensions
and read_ship_policy_report_values, release_ship_policy_report.sh)
through finding_blocks, and OVERWATCH's resolution-stage finding parser
(parse_tester_report_findings in
pgai_agent_kanban.overwatch.resolution_matrix) through finding_sections.
So all five split any report into the same blocks, for every heading
shape and every nesting.

Inside a block, a finding's fields are read by one shared field reader,
also in this module: field_value (a field's value), field_first_word (a
rating: Category, Fix Effort, Systemic Risk, Regression, Integrity) and
filed_as_bug_id (the bug a finding's Filed As names). Its rule, stated in
full in field_value's docstring, is liberal: bold markers and backticks
are ignored, the label is case-insensitive and needs its colon, a rating
is the first word of the value (``high (an annotation)`` reads as
``high``), and the bug id is the first ``BUG-NNNN`` anywhere in the Filed
As value (``see BUG-NNNN`` reads as ``BUG-NNNN``). Four readers read their
fields through it: this module (the Known Issues stop list),
pgai_agent_kanban.cm.skipped_bug_pairs (the Bugs Skipped table), the
SHIP-KNOWN-ISSUE bug resolver (read_ship_policy_known_issue_bug_files,
which calls stop_bug_paths) and the ship-policy matrix's finding reader
(read_ship_policy_finding_dimensions and read_ship_policy_report_values'
large-fix-effort flag). So the matrix never decides from a field line the
resolver or the release-notes readers cannot read. OVERWATCH's finding
parser shares the block split only; it reads its own two fields with its
own pattern.

This module reads only; it never writes anything. A Filed As value that
does not resolve to a file under the bugs directory is narrated on stderr
and skipped -- a path is never invented -- and a missing or unreadable
report resolves to no bug files at all. Resolving bug files never stops a
release.

Usage (import):
    from pgai_agent_kanban.cm.stop_bug_files import resolve_stop_bug_files
    paths = resolve_stop_bug_files("/path/to/report.md", "/path/to/bugs",
                                   "systemic-risk-high")
    # stop_bug_paths is the same list, each path in the form bugs_dir was
    # given in rather than resolved to an absolute path.

Usage (CLI, consumed by release_ship_posture.sh):
    python3 -m pgai_agent_kanban.cm.stop_bug_files <report_path> <bugs_dir> <cause>
    Prints one absolute bug-file path per line, in report order, each bug
    once. Exits 0 whatever it finds (including nothing); exits 2 on a usage
    error such as an unknown cause.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

CAUSE_SYSTEMIC_RISK_HIGH = "systemic-risk-high"
CAUSE_FIX_EFFORT_LARGE = "fix-effort-large"

# cause -> (field label, the first-word value that marks a causing finding)
_CAUSE_FIELDS = {
    CAUSE_SYSTEMIC_RISK_HIGH: ("Systemic Risk", "high"),
    CAUSE_FIX_EFFORT_LARGE: ("Fix Effort", "large"),
}
CAUSES = tuple(_CAUSE_FIELDS)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
# The one finding-heading rule; finding_heading_level's docstring explains it.
_FINDING_HEADING_RE = re.compile(r"^(#{3,6})\s+finding\b", re.IGNORECASE)
_BUG_ID_RE = re.compile(r"BUG-(\d{4,})", re.IGNORECASE)
# A rating word: a letter, then letters, underscores and dashes.
_RATING_WORD_RE = re.compile(r"[A-Za-z][A-Za-z_-]*")


def finding_heading_level(line: str) -> int:
    """Return the heading level when *line* opens a finding block, else 0.

    This is the single rule for "a finding block starts here" that every
    reader of a TESTER report's findings applies (see the module
    docstring). A line opens a finding block when it is a Markdown heading
    of level 3 to 6 (three to six "#" at the very start of the line, then
    at least one space or tab) whose text starts with the word "Finding",
    in any case. "Finding" must be a whole word, so "Finding 1: ...",
    "Finding: ..." and "finding 2" qualify. The hashes must start the
    line, and nothing after the word "Finding" is read.

    Shapes that deliberately open no block:

    - "## Findings" and any level-1 or level-2 heading: a report section,
      not a finding.
    - "### Findings summary" (or any text starting "Findings..."): the
      plural names a section about findings, such as a summary or a list
      of pass results, not one finding. Its prose must never be read as
      one finding's own fields.
    - "###Finding 1: ..." (no space or tab after the hashes): not a
      heading at all. CommonMark renders it as paragraph text, and this
      module's block-ending rule does not treat it as a heading either.
      A report written entirely in this shape reads as having no
      findings, which the ship-policy reader reports on stderr and
      answers with its fail-loud "unknown unknown" default.
    - Seven or more "#": not a Markdown heading.

    Where a block ends (finding_sections applies this):

    - At the next Markdown heading no deeper than the finding's own, so a
      following report section ("## Lint Findings") or a sibling grouping
      heading ("### Step 6.5" after "#### Finding 1") never lends the
      finding a field. A deeper non-finding heading ("##### detail")
      stays inside the block.
    - At the next finding heading of any depth. A finding heading nested
      inside an open finding ("#### Finding 2" under "### Finding 1")
      ends the outer block: the outer finding keeps every line up to the
      nested heading and is returned, and the nested heading opens a
      block of its own. Every finding heading therefore opens exactly one
      block, no line belongs to two blocks, and no block carries another
      finding's fields. Lines after the nested finding's own block ends,
      but before the next heading no deeper than the outer finding's,
      belong to no block: the outer block is already closed.
    """
    match = _FINDING_HEADING_RE.match(line)
    return len(match.group(1)) if match else 0


def finding_sections(report_text: str) -> list:
    """Return ``(heading, body)`` for each finding block in *report_text*.

    This is the one finding-block splitter every TESTER-report reader uses
    (see the module docstring). A block opens at a line
    finding_heading_level accepts and runs up to, not including, the next
    heading no deeper than its own or the next finding heading of any
    depth -- the rule finding_heading_level's docstring states in full. A
    block is always returned once it closes, so no line of an open block
    is ever discarded.

    *heading* is the opening heading's text with its hashes and the
    surrounding whitespace removed (``"Finding 1: a title"`` for
    ``### Finding 1: a title``). *body* is the block's lines after the
    heading, joined with newlines and otherwise unchanged; the heading
    line itself is not part of it. Blocks come back in report order.
    """
    sections: list = []
    level = 0
    heading_text = ""
    body: list = []
    for line in report_text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            heading_level = len(heading.group(1))
            opens_finding = finding_heading_level(line)
            if level and (heading_level <= level or opens_finding):
                sections.append((heading_text, "\n".join(body)))
                level = 0
            if opens_finding:
                level = heading_level
                heading_text = heading.group(2)
                body = []
                continue
        if level:
            body.append(line)
    if level:
        sections.append((heading_text, "\n".join(body)))
    return sections


def finding_blocks(report_text: str) -> list:
    """Return the body text of each finding block in *report_text*, in order.

    The same split as finding_sections, with each block's heading dropped:
    one body string per finding block, the heading line not part of it.
    """
    return [body for _heading, body in finding_sections(report_text)]


def _field_line_re(label: str) -> "re.Pattern":
    """The field-line pattern for *label*; field_value's docstring states it."""
    words = r"\s+".join(re.escape(word) for word in label.split())
    return re.compile(r"^\s*" + words + r"\s*:\s*(\S.*?)\s*$", re.IGNORECASE)


def field_value(block: str, label: str) -> str:
    """Return the value of the ``label:`` field line in *block*, or "".

    This is the one field-line rule every reader of a TESTER report's
    finding fields shares (see the module docstring). Each line of the
    block is read with every bold marker (``*``) and backtick removed; a
    field line is then optional leading whitespace, the label (matched
    case-insensitively, any run of whitespace between its words), a colon
    with optional whitespace on either side, and a non-blank value. So
    ``**Filed As:** BUG-NNNN``, ``Filed As: BUG-NNNN``,
    ``**Filed As**: BUG-NNNN`` with the id in backticks, and
    ``filed as : BUG-NNNN`` are all the same field line. A label with no
    colon is prose, not a field line, and a label line with nothing after
    its colon is passed over.

    Returns the first field line's value with the markers removed and the
    surrounding whitespace trimmed (``"BUG-NNNN (cited)"``), or "" when the
    block has no such line. The value is otherwise as written; use
    field_first_word for a rating and filed_as_bug_id for a bug id.
    """
    pattern = _field_line_re(label)
    for line in block.splitlines():
        match = pattern.match(line.replace("*", "").replace("`", ""))
        if match:
            return match.group(1)
    return ""


def field_first_word(block: str, label: str) -> str:
    """Return the rating word of the ``label:`` field line in *block*, or "".

    The line is found by field_value's rule. The rating is the value's
    first word -- a letter, then letters, underscores and dashes --
    lowercased, with each underscore read as a dash and trailing dashes
    dropped, so ``**Fix Effort:** large (notes)`` and
    ``Fix Effort: Large.`` both read as ``large``, a backticked ``High``
    reads as ``high``, and ``stale_assertion`` reads as
    ``stale-assertion``. A value that does not start with a letter has no
    rating and reads as "".
    """
    match = _RATING_WORD_RE.match(field_value(block, label))
    return match.group(0).lower().replace("_", "-").rstrip("-") if match else ""


def filed_as_bug_id(block: str) -> str:
    """Return the ``BUG-NNNN`` id the block's Filed As field names, or "".

    The Filed As line is found by field_value's rule, and the id is the
    first ``BUG-`` followed by four or more digits anywhere in its value,
    in any case, so ``BUG-NNNN``, ``bug-NNNN (a note)``, ``see BUG-NNNN``
    and ``cites the existing BUG-NNNN-slug`` all name ``BUG-NNNN``. The id
    is returned normalized: upper-case ``BUG-`` and the number zero-padded
    to four digits, with any further leading zeros dropped.
    A value naming no id (``none``, ``not filed``) returns "".
    """
    match = _BUG_ID_RE.search(field_value(block, "Filed As"))
    return f"BUG-{int(match.group(1)):04d}" if match else ""


def bug_file_for_id(bugs_dir: pathlib.Path, bug_id: str) -> "pathlib.Path | None":
    """Return the bug file for *bug_id* under *bugs_dir*, or None.

    *bug_id* is a normalized ``BUG-NNNN`` id. The lexically first
    ``BUG-NNNN-<slug>.md`` wins; a bare ``BUG-NNNN.md`` is the fallback.
    The path keeps the form *bugs_dir* was given in. Shared by
    stop_bug_paths (the Known Issues stop list and the SHIP-KNOWN-ISSUE bug
    resolver) and pgai_agent_kanban.cm.skipped_bug_pairs, so every
    release-notes reader resolves a Filed As id to the same file.
    """
    candidates = sorted(bugs_dir.glob(f"{bug_id}-*.md"))
    if candidates:
        return candidates[0]
    exact = bugs_dir / f"{bug_id}.md"
    return exact if exact.is_file() else None


def stop_bug_paths(report_path: str, bugs_dir: str, cause: str) -> list:
    """Return the bug files behind the findings that caused a stop.

    The same list resolve_stop_bug_files returns, with each path kept in
    the form *bugs_dir* was given in (a relative *bugs_dir* gives relative
    paths) instead of resolved. The SHIP-KNOWN-ISSUE bug resolver
    (read_ship_policy_known_issue_bug_files, release_known_issue.sh)
    prints this list for ``systemic-risk-high``.

    Args, return order, de-duplication and the ValueError are as for
    resolve_stop_bug_files; the paths are pathlib.Path objects.
    """
    if cause not in _CAUSE_FIELDS:
        raise ValueError(f"unknown stop cause {cause!r}; expected one of: {', '.join(CAUSES)}")
    label, wanted = _CAUSE_FIELDS[cause]
    try:
        text = pathlib.Path(report_path).read_text(encoding="utf-8")
    except OSError:
        return []
    bugs = pathlib.Path(bugs_dir)
    seen: set = set()
    paths: list = []
    for block in finding_blocks(text):
        if field_first_word(block, label) != wanted:
            continue
        bug_id = filed_as_bug_id(block)
        if not bug_id or bug_id in seen:
            continue
        seen.add(bug_id)
        bug_file = bug_file_for_id(bugs, bug_id)
        if bug_file is None:
            print(
                f"WARNING: Filed As '{field_value(block, 'Filed As')}' does not "
                f"resolve to a bug file under {bugs}",
                file=sys.stderr,
            )
            continue
        paths.append(bug_file)
    return paths


def resolve_stop_bug_files(report_path: str, bugs_dir: str, cause: str) -> list:
    """Return the bug files behind the findings that caused a stop.

    A causing finding is one whose own rating field (Systemic Risk for
    trigger 2, Fix Effort for trigger 3) reads ``high`` / ``large`` by
    field_first_word; its bug is the id filed_as_bug_id reads from its own
    Filed As field.

    Args:
        report_path: The TESTER report the ship decision read.
        bugs_dir:    The project's bugs/ directory.
        cause:       ``systemic-risk-high`` (trigger 2) or
                     ``fix-effort-large`` (trigger 3).

    Returns:
        Absolute bug-file paths as strings, in report order, each bug once.
        Empty for a missing or unreadable report, or when no causing
        finding names a bug that exists on disk.

    Raises:
        ValueError: when *cause* is not one of CAUSES.
    """
    return [str(path.resolve()) for path in stop_bug_paths(report_path, bugs_dir, cause)]


def main(argv: "list | None" = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.stop_bug_files",
        description=(
            "Print the bug files behind the TESTER findings that caused a "
            "ship-policy stop, one absolute path per line. Reads only; "
            "exits 0 whatever it finds."
        ),
    )
    parser.add_argument("report_path", help="The TESTER report the ship decision read.")
    parser.add_argument("bugs_dir", help="The project's bugs/ directory.")
    parser.add_argument(
        "cause",
        choices=CAUSES,
        help=(
            f"{CAUSE_SYSTEMIC_RISK_HIGH}: findings whose Systemic Risk is high "
            f"(trigger 2); {CAUSE_FIX_EFFORT_LARGE}: findings whose Fix Effort "
            "is large (trigger 3)."
        ),
    )
    args = parser.parse_args(argv)
    for path in resolve_stop_bug_files(args.report_path, args.bugs_dir, args.cause):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
