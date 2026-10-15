"""
stop_bug_files.py -- resolve the bug files behind a ship-policy stop.

When the project's ship posture lets a release ship through a ship-policy
trigger that would otherwise HALT it (team/scripts/lib/cm/
release_ship_posture.sh), the release notes' ``## Known Issues`` section
names the bugs behind that stop. This module finds them: it keeps the
TESTER findings that caused the stop and resolves the ``BUG-NNNN`` id each
one's own Filed As field names to the bug file on disk.

Which findings caused the stop depends on the trigger:

``systemic-risk-high``
    Trigger 2 -- every finding whose own Systemic Risk reads ``high``.
``fix-effort-large``
    Trigger 3 -- every finding whose own Fix Effort reads ``large``.

How the report is read
----------------------
This module never reads the report text itself. Every finding and every
field comes from the one TESTER-report parser,
``pgai_agent_kanban.reports.tester_report`` (``parse_report``), whose
module docstring states the whole reading contract: what starts a finding
and where it ends, how a field line is recognised, and how a value is
normalised. report_findings flattens the parsed findings the way the
ship-policy matrix inputs do
(``pgai_agent_kanban.cm.ship_policy_report_inputs.all_findings``): each
finding followed by its nested sub-findings, in report order. A nested
sub-finding is consulted as a finding in its own right, and its fields
never replace or fill in the outer finding's own.

The two release-notes resolvers read through this module: the Known Issues
stop list (stop_bug_paths / resolve_stop_bug_files, which the
SHIP-KNOWN-ISSUE resolver ``read_ship_policy_known_issue_bug_files`` in
release_known_issue.sh also runs) and the Bugs Skipped table
(pgai_agent_kanban.cm.skipped_bug_pairs, through report_findings,
filed_bug_id and bug_file_for_id). So both resolve every finding to the
same bug file.

A finding names a bug through the parser's ``filed_as``: the first
``BUG-NNNN`` or ``PRIORITY-NNNN`` id anywhere in its Filed As value, in
canonical form. Only a ``BUG-`` id names a bug file; a finding filed as a
priority, or naming no id at all, contributes nothing.

This module reads only; it never writes anything. A Filed As id that does
not resolve to a file under the bugs directory is narrated on stderr and
skipped -- a path is never invented -- and a missing or unreadable report
resolves to no bug files at all. Resolving bug files never stops a
release.

Usage (import):
    from pgai_agent_kanban.cm.stop_bug_files import resolve_stop_bug_files
    paths = resolve_stop_bug_files("/path/to/report.md", "/path/to/bugs",
                                   "systemic-risk-high")
    # stop_bug_paths is the same list, each path in the form bugs_dir was
    # given in rather than resolved to an absolute path.

Usage (CLI, consumed by release_ship_posture.sh and release_known_issue.sh):
    python3 -m pgai_agent_kanban.cm.stop_bug_files [--keep-path-form] <report_path> <bugs_dir> <cause>
    Prints one bug-file path per line, in report order, each bug once:
    absolute paths by default, or with --keep-path-form each path in the
    form <bugs_dir> was given in (stop_bug_paths). Exits 0 whatever it
    finds (including nothing); exits 2 on a usage error such as an unknown
    cause.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

# Run by file path (``python3 .../stop_bug_files.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package imports below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# imports resolve from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.ship_policy_report_inputs import all_findings  # noqa: E402
from pgai_agent_kanban.reports.tester_report import (  # noqa: E402
    Finding,
    parse_report_text,
    read_report_text,
)

CAUSE_SYSTEMIC_RISK_HIGH = "systemic-risk-high"
CAUSE_FIX_EFFORT_LARGE = "fix-effort-large"

# cause -> (the parsed Finding field, the token that marks a causing finding)
_CAUSE_FIELDS = {
    CAUSE_SYSTEMIC_RISK_HIGH: ("systemic_risk", "high"),
    CAUSE_FIX_EFFORT_LARGE: ("fix_effort", "large"),
}
CAUSES = tuple(_CAUSE_FIELDS)


def report_findings(report_path: str) -> list:
    """Return every finding of the TESTER report at *report_path*.

    The report is parsed by ``parse_report``'s rules and its findings come
    back flattened by ``all_findings``: each finding followed by its nested
    sub-findings, in report order, each a
    ``pgai_agent_kanban.reports.tester_report.Finding``. A report that
    cannot be read returns [], so a missing report resolves to no bug
    files rather than stopping a release.
    """
    try:
        text = read_report_text(report_path)
    except OSError:
        return []
    return all_findings(parse_report_text(text))


def filed_bug_id(finding: Finding) -> str:
    """Return the ``BUG-NNNN`` id *finding*'s own Filed As names, or "".

    The id is the parser's ``filed_as`` (canonical, upper-case, the number
    zero-padded to four digits). A ``PRIORITY-NNNN`` id names a priority
    file, not a bug file, so it reads "" here, as does a value naming no id
    (``none``, ``not filed``).
    """
    filed_as = finding.filed_as or ""
    return filed_as if filed_as.startswith("BUG-") else ""


def warn_unresolved(finding: Finding, bugs_dir: pathlib.Path) -> None:
    """Narrate on stderr that *finding*'s Filed As names no file under
    *bugs_dir*, quoting the value as the report wrote it (the parser's
    ``raw`` value) with bold markers and backticks dropped, so the quote
    reads as plain text."""
    quoted = finding.raw.get("filed_as", "").replace("*", "").replace("`", "")
    print(
        f"WARNING: Filed As '{quoted}' does not resolve to a bug file under {bugs_dir}",
        file=sys.stderr,
    )


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
    prints this list for ``systemic-risk-high`` through the CLI's
    --keep-path-form.

    Args, return order, de-duplication and the ValueError are as for
    resolve_stop_bug_files; the paths are pathlib.Path objects.
    """
    if cause not in _CAUSE_FIELDS:
        raise ValueError(f"unknown stop cause {cause!r}; expected one of: {', '.join(CAUSES)}")
    field, wanted = _CAUSE_FIELDS[cause]
    bugs = pathlib.Path(bugs_dir)
    seen: set = set()
    paths: list = []
    for finding in report_findings(report_path):
        if getattr(finding, field) != wanted:
            continue
        bug_id = filed_bug_id(finding)
        if not bug_id or bug_id in seen:
            continue
        seen.add(bug_id)
        bug_file = bug_file_for_id(bugs, bug_id)
        if bug_file is None:
            warn_unresolved(finding, bugs)
            continue
        paths.append(bug_file)
    return paths


def resolve_stop_bug_files(report_path: str, bugs_dir: str, cause: str) -> list:
    """Return the bug files behind the findings that caused a stop.

    A causing finding is one whose own parsed rating (``systemic_risk``
    for trigger 2, ``fix_effort`` for trigger 3) is ``high`` / ``large``;
    its bug is the ``BUG-NNNN`` id filed_bug_id reads from its own
    ``filed_as``. Findings come from report_findings, nested sub-findings
    included.

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
            "ship-policy stop, one path per line. Reads only; exits 0 "
            "whatever it finds."
        ),
    )
    parser.add_argument(
        "--keep-path-form",
        action="store_true",
        help=(
            "Print each path in the form bugs_dir was given in (a relative "
            "bugs_dir gives relative paths) instead of as an absolute path."
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
    if args.keep_path_form:
        paths = [str(path) for path in stop_bug_paths(args.report_path, args.bugs_dir, args.cause)]
    else:
        paths = resolve_stop_bug_files(args.report_path, args.bugs_dir, args.cause)
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
