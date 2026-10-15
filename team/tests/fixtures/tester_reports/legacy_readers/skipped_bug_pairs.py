# TEST-ONLY LEGACY COPY -- imported by no production code.
#
# A frozen copy of team/pgai_agent_kanban/cm/skipped_bug_pairs.py as it read
# before its report reading moved onto pgai_agent_kanban.reports.tester_report.
# It is kept for this release only, so that
# team/tests/unit/test_tester_report_differential.py can run the old reader
# over the TESTER report corpus; it is deleted together with that test once
# the readers are migrated. The code below is the behaviour being recorded:
# do not edit it. The changes from the original are this comment, the shared
# reader import pointing at the legacy copy of stop_bug_files beside this
# file, and the run-by-path sys.path guard dropped (a fixture is only ever
# imported).

"""
skipped_bug_pairs.py -- the bugs a release ships unfixed, and why.

A release's notes carry a "## Bugs Skipped" table: one row per bug the
verifier found and the release shipped without fixing, each with a
"Rationale for Skipping" cell. This module resolves the rows. It reads the
TESTER report's finding blocks, keeps every ``bug``, ``gap`` and
``stale-assertion`` finding, resolves the ``BUG-NNNN`` id each one's own
Filed As field names to the bug file on disk, and pairs that file with one
readable sentence saying why the bug shipped unfixed.

The sentence is built only from the finding's own rating fields --
Category, Regression, Fix Effort and Systemic Risk -- for example
"Introduced by this release and found during its verification, rated fix
effort small and systemic risk medium; deferred to a follow-up release."
A rating word outside the report template's vocabulary is left out rather
than echoed, so the sentence is never empty. The finding's free-text
detail bullets ("- Traces to: ...", "- Location: ...") describe the defect,
not a reason to skip it, and never reach the sentence.

How the report is read:

- A finding block opens at a heading of level 3 to 6 whose text starts
  with the word "Finding" (any case) and runs up to the next heading no
  deeper than its own, or the end of the report. A "#### Finding" grouped
  under a "### Step" heading is therefore read exactly as a "### Finding"
  is, and the next "### Step" heading ends it. A report section that
  follows the last finding (for example "## Lint Findings") is shallower
  than the finding and so never lends it a field. A finding heading nested
  inside an open finding ends the outer block, which keeps its lines up to
  there, and opens its own. "## Findings", "### Findings summary" and
  "###Finding" (no space after the hashes) open no block. The splitting is
  pgai_agent_kanban.cm.stop_bug_files.finding_blocks, which this table
  shares with the Known Issues stop list, the SHIP-KNOWN-ISSUE bug resolver
  (read_ship_policy_known_issue_bug_files, release_known_issue.sh) and the
  ship-policy matrix (read_ship_policy_finding_dimensions); OVERWATCH's
  finding parser (parse_tester_report_findings) uses the same split
  through finding_sections. So all five readers split every report into
  the same finding blocks.
- A finding's fields are read by the shared field reader in
  pgai_agent_kanban.cm.stop_bug_files (field_value, field_first_word,
  filed_as_bug_id), the same reader the Known Issues stop list, the
  SHIP-KNOWN-ISSUE bug resolver and the ship-policy matrix read their
  fields through; field_value's docstring states the rule. In short: bold
  markers and backticks are ignored, so ``**Category:** bug`` and
  ``Category: bug`` are the same field line; the label is matched
  case-insensitively and needs its colon; a rating is the lowercased first
  word of its value (``small (1-2 CODER tasks)`` reads as ``small``); and
  the bug is the first ``BUG-NNNN`` id anywhere in the Filed As value, so
  ``BUG-NNNN``, the same id in backticks and ``see BUG-NNNN`` all name
  the same bug.
- A finding whose Filed As value names no ``BUG-NNNN`` id, or whose bug was
  already listed by an earlier finding or by an excluded path, adds no row.
  An id that resolves to no file under the bugs directory is narrated on
  stderr and skipped -- a path is never invented.

Excluded paths (typically the bug files the "## Known Issues" block already
names) are matched by the BUG-NNNN id at the start of their file name, so
a bug is never listed under both sections whatever path form the caller
passed in.

This module reads only; it never writes anything. A missing or unreadable
report resolves to no rows, and resolving rows never stops a release.

Usage (import):
    from pgai_agent_kanban.cm.skipped_bug_pairs import resolve_skipped_bug_pairs
    pairs = resolve_skipped_bug_pairs("/path/to/report.md", "/path/to/bugs",
                                      ["/path/to/bugs/BUG-NNNN-known-issue.md"])
    for bug_file, rationale in pairs: ...

Usage (CLI, consumed by release_known_issue.sh's
read_ship_policy_skipped_bug_pairs):
    python3 -m pgai_agent_kanban.cm.skipped_bug_pairs <report_path> <bugs_dir> [<exclude_path> ...]
    Prints one "<bug_file>\\t<rationale>" line per row, in report order.
    The bug_file path keeps the form bugs_dir was given in (a relative
    bugs_dir gives relative paths). Exits 0 whatever it finds (including
    nothing); exits 2 on a usage error.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
from typing import List, Optional, Sequence, Tuple

from tests.fixtures.tester_reports.legacy_readers.stop_bug_files import (
    bug_file_for_id,
    field_first_word,
    field_value,
    filed_as_bug_id,
    finding_blocks,
)

__all__ = [
    "SKIPPED_CATEGORIES",
    "resolve_skipped_bug_pairs",
    "skip_rationale",
]

# The finding categories that name a bug the release shipped unfixed. A
# "pass" or "pass-with-caveat" finding skipped nothing and adds no row.
SKIPPED_CATEGORIES = ("bug", "gap", "stale-assertion")

# The rating words TESTER's report template defines for each field. A value
# outside its own vocabulary is left out of the sentence rather than echoed,
# so the rendered cell only ever carries a word the reader can interpret.
_FIX_EFFORT_WORDS = ("small", "medium", "large")
_RISK_WORDS = ("low", "medium", "high")

# The BUG-NNNN id leading an excluded bug file's name.
_LEADING_BUG_ID_RE = re.compile(r"(BUG-\d{4,})", re.IGNORECASE)


def _normalized_bug_id(raw_id: str) -> str:
    """Return ``BUG-NNNN`` (at least four digits) for a matched BUG id."""
    return f"BUG-{int(raw_id.split('-')[1]):04d}"


def skip_rationale(section: str, category: str) -> str:
    """Return one readable sentence saying why this finding shipped unfixed.

    Built only from the finding's own rating fields -- Category, Regression,
    Fix Effort and Systemic Risk -- and never from its free-text detail.
    TESTER's detail bullets ("- Traces to: ...", "- Location: ...",
    "- Actual: ...") describe the defect, not a reason to skip it, and they
    routinely quote kanban task and bug identifiers that the downstream
    scrub can only replace with a neutral stand-in. A sentence assembled
    from rating words carries no list marker and no identifier, so it reads
    the same before and after the scrub. Always returns non-empty text.

    Args:
        section:  The finding's field-bearing text (one finding_blocks body).
        category: The finding's Category rating, already lowercased.
    """
    regression = field_first_word(section, "Regression")
    if category == "stale-assertion":
        lead = "A test assertion left stale by changes in this release, found during its verification"
    elif regression == "yes":
        lead = "Introduced by this release and found during its verification"
    elif regression == "no":
        lead = "Predates this release and was found during its verification"
    else:
        lead = "Found during verification of this release"

    ratings = []
    fix_effort = field_first_word(section, "Fix Effort")
    if fix_effort in _FIX_EFFORT_WORDS:
        ratings.append(f"fix effort {fix_effort}")
    risk = field_first_word(section, "Systemic Risk")
    if risk in _RISK_WORDS:
        ratings.append(f"systemic risk {risk}")
    rated = f", rated {' and '.join(ratings)}" if ratings else ""

    return f"{lead}{rated}; deferred to a follow-up release."


def resolve_skipped_bug_pairs(
    report_path: str, bugs_dir: str, exclude_paths: Sequence[str] = ()
) -> List[Tuple[str, str]]:
    """Return the (bug_file, rationale) rows for a release's Bugs Skipped table.

    Args:
        report_path:   The TESTER report the release was verified by.
        bugs_dir:      The project's bugs/ directory. Returned paths keep the
                       form this was given in.
        exclude_paths: Bug files already listed elsewhere (the Known Issues
                       block's own), matched by the BUG-NNNN id leading
                       their file name.

    Returns:
        One (bug_file, rationale) tuple per qualifying finding, in report
        order, each bug once. Empty for a missing or unreadable report, or
        when no qualifying finding names a bug that exists on disk.
    """
    excluded_ids = set()
    for path in exclude_paths:
        match = _LEADING_BUG_ID_RE.match(pathlib.Path(path).name)
        if match:
            excluded_ids.add(_normalized_bug_id(match.group(1)))

    try:
        text = pathlib.Path(report_path).read_text(encoding="utf-8")
    except OSError:
        return []

    bugs = pathlib.Path(bugs_dir)
    seen_ids: set = set()
    pairs: List[Tuple[str, str]] = []
    for section in finding_blocks(text):
        category = field_first_word(section, "Category")
        if category not in SKIPPED_CATEGORIES:
            continue
        bug_id = filed_as_bug_id(section)
        if not bug_id or bug_id in seen_ids or bug_id in excluded_ids:
            continue
        seen_ids.add(bug_id)
        bug_file = bug_file_for_id(bugs, bug_id)
        if bug_file is None:
            print(
                f"WARNING: Filed As '{field_value(section, 'Filed As')}' does not "
                f"resolve to a bug file under {bugs}",
                file=sys.stderr,
            )
            continue
        pairs.append((str(bug_file), skip_rationale(section, category)))
    return pairs


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.skipped_bug_pairs",
        description=(
            "Print the bugs a release ships unfixed, one "
            "'<bug_file>\\t<rationale>' line per bug/gap/stale-assertion "
            "finding in a TESTER report, for the release notes' "
            "'## Bugs Skipped' table. Reads only; exits 0 whatever it finds."
        ),
    )
    parser.add_argument("report_path", help="The TESTER report the release was verified by.")
    parser.add_argument("bugs_dir", help="The project's bugs/ directory.")
    parser.add_argument(
        "exclude_paths",
        nargs="*",
        metavar="exclude_path",
        help=(
            "A bug file already listed under '## Known Issues'; its BUG-NNNN "
            "id is left out of the output. Repeatable."
        ),
    )
    args = parser.parse_args(argv)
    for bug_file, rationale in resolve_skipped_bug_pairs(
        args.report_path, args.bugs_dir, args.exclude_paths
    ):
        print(f"{bug_file}\t{rationale}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
