"""
skipped_bug_pairs.py -- the bugs a release ships unfixed, and why.

A release's notes carry a "## Bugs Skipped" table: one row per bug the
verifier found and the release shipped without fixing, each with a
"Rationale for Skipping" cell. This module resolves the rows. It takes the
TESTER report's parsed findings, keeps every ``bug``, ``gap``,
``pre-existing-bug`` and ``stale-assertion`` finding, resolves the
``BUG-NNNN`` id each one's own Filed As field names to the bug file on
disk, and pairs that file with one readable sentence saying why the bug
shipped unfixed. A ``pre-existing-bug`` finding is a defect the release
did not introduce but did ship unfixed, so it gets a row like any other
filed bug, and its sentence says it predates the release.

The sentence is built only from the finding's own rating fields --
Category, Regression, Fix Effort and Systemic Risk -- for example
"Introduced by this release and found during its verification, rated fix
effort small and systemic risk medium; deferred to a follow-up release."
A rating the parser could not read (absent, or outside the report
template's vocabulary) is left out rather than echoed, so the sentence is
never empty. The finding's free-text detail bullets ("- Traces to: ...",
"- Location: ...") describe the defect, not a reason to skip it, and never
reach the sentence.

How the report is read:

- This module never reads the report text itself. Every finding and every
  field comes from the one TESTER-report parser,
  pgai_agent_kanban.reports.tester_report (parse_report), whose module
  docstring states the whole reading contract: what starts a finding and
  where it ends, how a field line is recognised (bold or plain label,
  colon inside or outside the bold, backticks and quotes ignored) and how
  each rating is normalised to one lowercase token.
- The findings are pgai_agent_kanban.cm.stop_bug_files.report_findings:
  each parsed finding followed by its nested sub-findings, in report
  order, the same findings the Known Issues stop list and the
  SHIP-KNOWN-ISSUE bug resolver read. A nested sub-finding is a finding of
  its own, and its fields never replace the outer finding's own.
- A finding's bug is stop_bug_files.filed_bug_id: the ``BUG-NNNN`` id the
  parser reads from its own Filed As value. A finding filed as a priority,
  or naming no id, or whose bug was already listed by an earlier finding
  or by an excluded path, adds no row. An id that resolves to no file
  under the bugs directory (stop_bug_files.bug_file_for_id) is narrated on
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

# Run by file path (``python3 .../skipped_bug_pairs.py``) or loaded from a
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

from pgai_agent_kanban.cm.stop_bug_files import (  # noqa: E402
    bug_file_for_id,
    filed_bug_id,
    report_findings,
    warn_unresolved,
)
from pgai_agent_kanban.reports.tester_report import Finding  # noqa: E402

__all__ = [
    "SKIPPED_CATEGORIES",
    "resolve_skipped_bug_pairs",
    "skip_rationale",
]

# The finding categories that name a bug the release shipped unfixed. A
# "pre-existing-bug" finding qualifies: the defect predates the release, but
# the release still ships it unfixed. A "pass" or "pass-with-caveat" finding
# skipped nothing and adds no row.
SKIPPED_CATEGORIES = ("bug", "gap", "pre-existing-bug", "stale-assertion")

# The rating words the sentence names for each field. The parser already
# returns only its own vocabulary or None; a rating outside these words is
# left out of the sentence, so the rendered cell only ever carries a word
# the reader can interpret.
_FIX_EFFORT_WORDS = ("small", "medium", "large")
_RISK_WORDS = ("low", "medium", "high")

# The BUG-NNNN id leading an excluded bug file's name.
_LEADING_BUG_ID_RE = re.compile(r"(BUG-\d{4,})", re.IGNORECASE)


def _normalized_bug_id(raw_id: str) -> str:
    """Return ``BUG-NNNN`` (at least four digits) for a matched BUG id."""
    return f"BUG-{int(raw_id.split('-')[1]):04d}"


def skip_rationale(finding: Finding) -> str:
    """Return one readable sentence saying why this finding shipped unfixed.

    Built only from the finding's own parsed rating fields -- ``category``,
    ``regression``, ``fix_effort`` and ``systemic_risk`` -- and never from
    its free-text detail. TESTER's detail bullets ("- Traces to: ...",
    "- Location: ...", "- Actual: ...") describe the defect, not a reason
    to skip it, and they routinely quote kanban task and bug identifiers
    that the downstream scrub can only replace with a neutral stand-in. A
    sentence assembled from rating words carries no list marker and no
    identifier, so it reads the same before and after the scrub. Always
    returns non-empty text.

    The sentence's lead is chosen in this order: a ``stale-assertion``
    finding is a stale test; an explicit Regression of ``yes`` was
    introduced by this release; a Regression of ``no``, or a
    ``pre-existing-bug`` finding with no ``yes`` Regression, predates it;
    anything else was found during verification. A ``pre-existing-bug``
    finding usually carries no Regression line at all (the field is only
    required when Systemic Risk is high), so its category alone says it
    predates the release.

    Args:
        finding: One parsed finding (a stop_bug_files.report_findings entry).
    """
    if finding.category == "stale-assertion":
        lead = "A test assertion left stale by changes in this release, found during its verification"
    elif finding.regression == "yes":
        lead = "Introduced by this release and found during its verification"
    elif finding.regression == "no" or finding.category == "pre-existing-bug":
        lead = "Predates this release and was found during its verification"
    else:
        lead = "Found during verification of this release"

    ratings = []
    if finding.fix_effort in _FIX_EFFORT_WORDS:
        ratings.append(f"fix effort {finding.fix_effort}")
    if finding.systemic_risk in _RISK_WORDS:
        ratings.append(f"systemic risk {finding.systemic_risk}")
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

    bugs = pathlib.Path(bugs_dir)
    seen_ids: set = set()
    pairs: List[Tuple[str, str]] = []
    for finding in report_findings(report_path):
        if finding.category not in SKIPPED_CATEGORIES:
            continue
        bug_id = filed_bug_id(finding)
        if not bug_id or bug_id in seen_ids or bug_id in excluded_ids:
            continue
        seen_ids.add(bug_id)
        bug_file = bug_file_for_id(bugs, bug_id)
        if bug_file is None:
            warn_unresolved(finding, bugs)
            continue
        pairs.append((str(bug_file), skip_rationale(finding)))
    return pairs


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.skipped_bug_pairs",
        description=(
            "Print the bugs a release ships unfixed, one "
            "'<bug_file>\\t<rationale>' line per bug/gap/pre-existing-bug/"
            "stale-assertion finding in a TESTER report, for the release notes' "
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
