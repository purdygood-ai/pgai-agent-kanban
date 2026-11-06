"""
ship_policy_bug_filer.py -- the CM ship-policy stage's bug filer.

The ship-policy decision matrix (team/scripts/lib/cm/release_ship_policy.sh,
Step 4d) reads three inputs it does not author: the TESTER report's
Recommendation and Systemic Risk values (read by
pgai_agent_kanban.cm.ship_policy_report_inputs through the one report
parser, pgai_agent_kanban.reports.tester_report), and the TESTER task's
own README ``## Role`` / ``## Release Version`` fields the task lookup keys
on. Each is normalized before the matrix reads it, and the matrix has a row
for every normalized token -- but a value that still cannot be parsed, or a
lookup field that matched only after normalization, is a defect in the input
document, not in the release. Under the ship-always posture such a defect is
never a HALT and never a silent default: the matrix floors the release to
SHIP-WITH-CONCERNS and this module files the bug that carries the defect
into the next iteration's bundle, quoting the raw field text and the path of
the document that holds it so the next reader can open the exact line.

The same filer records one release-history pattern: when the last three
releases' notes all read ``## Status`` NON-FUNCTIONAL and the project's ship
posture lets the release ship anyway (release_ship_posture.sh, Step 4c), the
``release-pattern`` kind files the bug that names the pattern, quoting the
three releases and their statuses and the release-notes directory they were
read from, so the next iteration looks for why the chain is not
self-correcting.

This filer never blocks anything and never touches an RC: it writes at most
one new file into the project's bugs/ directory per distinct
(kind, field, source path) input this run, and nothing else -- no
release-state.md, no task status.md, no queue file, no HALT-family file, no
branch. A failure inside this module is the caller's to swallow: Step 4d
narrates it and carries on with the release, because a bug filer that could
stop a release would be a gate no requirement authorized.

Dedup key
---------
``## Ship-Policy Input`` -- one line, ``<kind>|<field>|<source_path>``. A live
bug (status open, running or needs-operator) recording the same key
suppresses a fresh filing (the same "cites an OPEN bug" rule
team/roles/TESTER.md Step 6 states and pgai_agent_kanban.deep_verify.bug_filer
/ pgai_agent_kanban.lint.bug_filer already follow for their own keys). When
the key matches nothing, the shared same-defect rule
(lib.bug_filing.find_same_defect_for_draft) is consulted for the drafted
report: a live bug whose slug differs from the draft's only by re-file
markers (a re-file word, an ordinal or a trailing counter), or one the
draft's body cites as its defect, is cited the same way. The record shape,
the existing-bug lookup, the claim of a BUG-NNNN slot and the same-defect
rule come from pgai_agent_kanban.lib.bug_filing (file_or_cite_record),
never re-implemented here (docs/coding-standards.md directive 1); this
module supplies only the ship-policy fields.

Filed-bug shape
---------------
Follows team/templates/project/release/BUG-TEMPLATE.md's header/section
order, with the one additive section above. ``**Filed By:**`` follows the
self-documenting label convention: the token ``cm-release ship-policy
(autonomous)``, then `` -- `` and one plain sentence naming what could not
be read.

Severity is ``medium`` against team/SOP-core.md's "Rating a Finding" rubric.
For an input defect, the release decision read a default row rather than the
row the document meant to name -- incorrect, but the release still ships (the
floor is a ship outcome) and the fix is one edit to the document, so a
workaround exists. For the release pattern, each NON-FUNCTIONAL release's own
findings already carry their own bugs and ratings; this bug records that the
chain has not healed them across three releases -- incorrect process
behaviour, with each underlying bug its own route to the fix.

Usage (import):
    from pgai_agent_kanban.cm.ship_policy_bug_filer import file_or_cite_input_bug
    result = file_or_cite_input_bug(
        bugs_dir, project="demo", version="v1.2.3", kind="report-value",
        field="Recommendation", raw_value="SHIPP-WITH-CONCERNZ",
        source_path="/path/to/report.md")
    # result.outcome is "filed" or "duplicate"; result.bug_id is BUG-NNNN.
    result = file_or_cite_input_bug(
        bugs_dir, project="demo", version="v1.2.4", kind="release-pattern",
        field="Status",
        raw_value="v1.2.1.md=NON-FUNCTIONAL, v1.2.2.md=NON-FUNCTIONAL, v1.2.3.md=NON-FUNCTIONAL",
        source_path="/path/to/release-notes")

Usage (CLI, consumed by release_ship_policy.sh and release_ship_posture.sh):
    python3 -m pgai_agent_kanban.cm.ship_policy_bug_filer \\
        <bugs_dir> <project> <version> <kind> <field> <raw_value> <source_path>
    Prints "key=value" lines: outcome=filed|duplicate, bug_id=BUG-NNNN (the
    filed bug, or the live bug cited), bug_path=<abs path, or empty for
    outcome=duplicate>. Exits 0 on either
    outcome; exits 1 with the reason on stderr when nothing could be filed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, UTC

# Run by file path (``python3 .../ship_policy_bug_filer.py``) or loaded from a file
# outside its package, this module has no parent package and the interpreter
# puts only its own directory on sys.path, so the package import below would
# not resolve. Put this file's own package root -- the directory holding the
# pgai_agent_kanban/ this file sits in -- at the front of sys.path so the
# import resolves from the same tree, never another. Imported as a package
# member, sys.path is left untouched.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.lib.bug_filing import BugRecord, file_or_cite_record  # noqa: E402

# The three kinds of input defect Step 4d files (a report value, a lookup
# field, a TESTER state the matrix has no row for), and the one
# release-history pattern Step 4c files under the ship-always posture. Each
# maps to the slug the filed file is named with and the sentence its header
# carries.
KIND_REPORT_VALUE = "report-value"
KIND_LOOKUP_FIELD = "lookup-field"
KIND_RELEASE_PATTERN = "release-pattern"
KIND_TESTER_STATE = "tester-state"
_KINDS = (KIND_REPORT_VALUE, KIND_LOOKUP_FIELD, KIND_RELEASE_PATTERN, KIND_TESTER_STATE)

_INPUT_HEADING = "Ship-Policy Input"


def input_key(kind: str, field: str, source_path: str) -> str:
    """The dedup key one filing records and a later run matches against."""
    return f"{kind}|{field}|{source_path}"


def _slug_for(kind: str, field: str) -> str:
    if kind == KIND_RELEASE_PATTERN:
        return "last-three-releases-non-functional"
    field_slug = re.sub(r"[^a-z0-9]+", "-", field.lower()).strip("-") or "field"
    if kind == KIND_LOOKUP_FIELD:
        return f"tester-lookup-{field_slug}"
    if kind == KIND_TESTER_STATE:
        return f"unrecognized-tester-{field_slug}"
    return f"unparseable-{field_slug}-value"


def _input_record(
    *,
    slug: str,
    project: str,
    version: str,
    kind: str,
    field: str,
    raw_value: str,
    source_path: str,
    filed_at: str,
) -> BugRecord:
    """The bug record for one ship-policy input defect (or the release
    pattern), keyed on input_key() in "## Ship-Policy Input"."""
    quoted = raw_value if raw_value.strip() else "(empty)"
    files_involved = (
        f"- {source_path}\n"
        "- team/scripts/lib/cm/release_ship_policy_report.sh (the reader that "
        "normalizes the value)"
    )
    hypothesis = (
        "Speculative -- the value was authored in a shape the report template does "
        "not name, or the writer that produced it drifted from the template."
    )
    if kind == KIND_RELEASE_PATTERN:
        headline = (
            f"the last three releases of {project} before {version} were all marked "
            f"NON-FUNCTIONAL -- the autonomous chain is shipping degraded work "
            f"repeatedly without self-correcting."
        )
        symptom = (
            f"While releasing {version}, the last-three-releases check read "
            f"`## {field}` from the release notes under `{source_path}` as:\n\n"
            f"    {quoted}\n\n"
            "Under the ship-always posture this pattern does not stop the release; "
            "this report carries it into the next bundle instead."
        )
        expected = (
            "A NON-FUNCTIONAL release is followed by a release that fixes what made "
            "it non-functional, so three NON-FUNCTIONAL releases never run in a row."
        )
        actual = f"The last three releases read: {quoted}."
        remedy = (
            f"1. Open the three release notes named above under `{source_path}` and "
            "list the findings behind each NON-FUNCTIONAL status.\n"
            "2. Find the defect the releases share, or the reason each bundle left "
            "the previous release's findings unfixed, and fix that.\n"
            "3. Confirm the next release ships with a status other than NON-FUNCTIONAL."
        )
        files_involved = (
            f"- {source_path} (the three release notes)\n"
            "- team/scripts/lib/cm/release_ship_policy_report.sh "
            "(check_last_three_non_functional, the check that read them)"
        )
        hypothesis = (
            "Speculative -- the findings behind the NON-FUNCTIONAL statuses were "
            "filed but not bundled ahead of new work, or one defect recurs across "
            "the releases."
        )
    elif kind == KIND_TESTER_STATE:
        headline = (
            f"the ship-policy matrix has no row for the TESTER task's `{field}` for {version} "
            f"-- it reads `{quoted}` in {source_path} -- and floored the release to SHIP-WITH-CONCERNS."
        )
        symptom = (
            f"While applying the ship-policy decision matrix for {project} at {version}, the "
            f"TESTER task's `{field}` read from `{source_path}` as:\n\n"
            f"    {quoted}\n\n"
            "The matrix has rows only for a TESTER task that reads DONE or BLOCKED. The release did "
            "not halt and was not treated as untested: it shipped as SHIP-WITH-CONCERNS (release "
            "notes Status KNOWN-BUGS) and filed this report."
        )
        expected = (
            "The TESTER task for the RC reads DONE (with artifacts/report.md) or BLOCKED when the "
            "release runs, so the matrix reads the row its outcome names."
        )
        actual = f"`{field}` reads `{quoted}`."
        remedy = (
            f"1. Open `{source_path}` and find why the TESTER task ended in this state (a session "
            "that did not finish, a hand edit, a typo).\n"
            "2. Fix the writer or the procedure that left it there, so the next RC's TESTER task "
            "ends DONE or BLOCKED.\n"
            "3. Confirm the next release's ship-policy step reads a recognised state."
        )
        files_involved = (
            f"- {source_path}\n"
            "- team/scripts/lib/cm/release_verification_retry.sh (the step that floored the release)"
        )
        hypothesis = (
            "Speculative -- the TESTER session ended without writing a final state, or the state was " "edited by hand."
        )
    elif kind == KIND_LOOKUP_FIELD:
        headline = (
            f"the TESTER-task lookup for {version} matched the `## {field}` field of "
            f"{source_path} only after normalization -- it reads `{quoted}`, not the "
            f"canonical form the lookup keys on."
        )
        symptom = (
            f"While applying the ship-policy decision matrix for {project} at "
            f"{version}, the TESTER-task lookup read `## {field}` from "
            f"`{source_path}` as:\n\n"
            f"    {quoted}\n\n"
            "The exact-match lookup would have skipped this task and treated the "
            "RC as untested (the test-not-required row). The value was accepted "
            "after normalization and the task was used, so the release decision "
            "read the task the report belongs to -- but the field as written is "
            "not the canonical form."
        )
        expected = (
            f"`## {field}` in a TESTER task README holds the bare canonical value "
            "(`TESTER` for Role, `vX.Y.Z` for Release Version) with no Markdown "
            "decoration or annotation."
        )
        actual = f"`## {field}` reads `{quoted}`."
        remedy = (
            f"1. Open `{source_path}` and rewrite `## {field}` as its bare "
            "canonical value.\n"
            f"2. Re-run the release for {version} (or the next release) and confirm "
            "the ship-policy step narrates no normalization for this task."
        )
    else:
        files_involved = (
            f"- {source_path}\n"
            "- team/pgai_agent_kanban/cm/ship_policy_report_inputs.py (the reader that "
            "normalizes the value, through team/pgai_agent_kanban/reports/tester_report.py)"
        )
        headline = (
            f"the ship-policy matrix could not parse `## {field}` in the TESTER "
            f"report at {source_path} -- it reads `{quoted}` -- and floored the "
            f"release of {version} to SHIP-WITH-CONCERNS."
        )
        symptom = (
            f"While applying the ship-policy decision matrix for {project} at "
            f"{version}, `## {field}` in the TESTER report `{source_path}` "
            "read as:\n\n"
            f"    {quoted}\n\n"
            "which is not one of the values the matrix has a row for, even "
            "after stripping Markdown decoration, a label prefix and any "
            "annotation after the first word. Under the ship-always posture the "
            "release did not halt and did not take the permissive "
            "SHIP-FUNCTIONAL row: it shipped as SHIP-WITH-CONCERNS (release "
            "notes Status KNOWN-BUGS) and filed this report."
        )
        if field.lower() == "recommendation":
            allowed = "PASS, SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS"
        else:
            allowed = "low, medium or high"
        expected = (
            f"`## {field}` holds one of {allowed} (team/templates/agent/"
            "REPORT-TEMPLATE.md), optionally followed by an annotation, so the "
            "matrix reads the row the report names."
        )
        actual = f"`## {field}` reads `{quoted}`; the matrix read it as unparseable."
        remedy = (
            f"1. Open `{source_path}` and set `## {field}` to one of {allowed}.\n"
            "2. If the value was produced by the report writer rather than by "
            "hand, fix the writer so the next report carries a readable value.\n"
            "3. Re-run the release step and confirm the ship-policy output names "
            "the row the report intends."
        )

    return BugRecord(
        slug=slug,
        filed_by=f"cm-release ship-policy (autonomous) -- {headline}",
        filed_at=filed_at,
        severity="medium",
        category="cm",
        symptom=symptom,
        expected=expected,
        actual=actual,
        reproduction=remedy,
        files_involved=files_involved,
        hypothesis=hypothesis,
        key_heading=_INPUT_HEADING,
        key=input_key(kind, field, source_path),
        extra_sections=(
            (
                "Ship-Policy Run",
                f"- Project: {project}\n- Version: {version}\n- Field: {field}\n- Raw value: {quoted}",
            ),
        ),
    )


@dataclass(frozen=True)
class FilingResult:
    """Outcome of one file_or_cite_input_bug() call.

    outcome   -- "filed" (a new bug file was written) or "duplicate" (a
                 live bug -- open, running or needs-operator -- already
                 records the same input key, or the shared same-defect rule
                 matches it; nothing was written).
    bug_id    -- "BUG-NNNN" for either outcome: the filed bug, or the live
                 bug cited.
    bug_path  -- absolute path to the filed file ("filed" only); None for
                 "duplicate".
    key       -- the dedup key this result covers.
    """

    outcome: str
    bug_id: str
    bug_path: str | None
    key: str


def file_or_cite_input_bug(
    bugs_dir: str,
    *,
    project: str,
    version: str,
    kind: str,
    field: str,
    raw_value: str,
    source_path: str,
    filed_at: str | None = None,
) -> FilingResult:
    """File a bug for one unreadable ship-policy input (or, for the
    release-pattern kind, for the last-three-releases-NON-FUNCTIONAL
    pattern), or cite the live bug (open, running or needs-operator) that
    already records the same (kind, field, source path) -- or, failing
    that, the live bug the shared same-defect rule matches the drafted
    report to (lib.bug_filing.find_same_defect_for_draft). Either citation
    claims no slot and writes nothing.

    Creates *bugs_dir* if it does not yet exist (a fresh project's first
    filing has no bugs/ directory yet). Never touches anything outside
    *bugs_dir* -- see the module docstring.

    Raises ValueError when *kind* is not one of the two known kinds; the CLI
    turns that into an exit 1 with the accepted set on stderr.
    """
    if kind not in _KINDS:
        raise ValueError(f"unknown ship-policy input kind {kind!r}; expected one of: " + ", ".join(_KINDS))
    os.makedirs(bugs_dir, exist_ok=True)

    filed_at = filed_at or datetime.now(UTC).strftime("%Y-%m-%d")
    record = _input_record(
        slug=_slug_for(kind, field),
        project=project,
        version=version,
        kind=kind,
        field=field,
        raw_value=raw_value,
        source_path=source_path,
        filed_at=filed_at,
    )
    filed = file_or_cite_record(bugs_dir, record, claimed_by="cm-release-ship-policy-filer")
    return FilingResult(outcome=filed.outcome, bug_id=filed.bug_id, bug_path=filed.bug_path, key=record.key)


def main(argv: list | None = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.ship_policy_bug_filer",
        description=(
            "File a bug for one ship-policy input the release step could not "
            "read (an unparseable TESTER report value, or a TESTER-task lookup "
            "field that matched only after normalization), or for the "
            "last-three-releases-NON-FUNCTIONAL pattern, or cite the "
            "open, running or needs-operator bug that already records it "
            "or reports the same defect (a slug differing only by a re-file "
            "word, ordinal or trailing counter, or a body citing it as its "
            "defect)."
        ),
    )
    parser.add_argument("bugs_dir", help="The project's bugs/ directory (created if absent).")
    parser.add_argument("project", help="Project name, recorded in the filed bug.")
    parser.add_argument("version", help="The release version being decided, e.g. v1.2.3.")
    parser.add_argument(
        "kind",
        choices=_KINDS,
        help=(
            f"{KIND_REPORT_VALUE}: a TESTER report value the matrix could not parse; "
            f"{KIND_LOOKUP_FIELD}: a TESTER task README field matched only after "
            f"normalization; {KIND_RELEASE_PATTERN}: the last three releases' notes "
            "all read NON-FUNCTIONAL (field Status, raw_value the releases and "
            "their statuses, source_path the release-notes directory)."
        ),
    )
    parser.add_argument("field", help="The field name, e.g. 'Recommendation' or 'Release Version'.")
    parser.add_argument("raw_value", help="The field's raw text, quoted verbatim in the bug.")
    parser.add_argument("source_path", help="Path of the report or README the value was read from.")
    args = parser.parse_args(argv)

    try:
        result = file_or_cite_input_bug(
            args.bugs_dir,
            project=args.project,
            version=args.version,
            kind=args.kind,
            field=args.field,
            raw_value=args.raw_value,
            source_path=args.source_path,
        )
    except Exception as exc:  # any filing failure is the caller's to narrate
        print(f"ship-policy bug filer: nothing filed -- {exc}", file=sys.stderr)
        return 1
    print(f"outcome={result.outcome}")
    print(f"bug_id={result.bug_id}")
    print(f"bug_path={result.bug_path or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
