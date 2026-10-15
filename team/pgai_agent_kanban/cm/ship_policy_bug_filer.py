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
``## Ship-Policy Input`` -- one line, ``<kind>|<field>|<source_path>``. A bug
already open or running for the same key suppresses a fresh filing (the
same "cites an OPEN bug" rule team/roles/TESTER.md Step 6 states and
pgai_agent_kanban.deep_verify.bug_filer / pgai_agent_kanban.lint.bug_filer
already follow for their own keys). The claim of a BUG-NNNN slot and the
scan of bugs/ for a live dedup candidate come from
pgai_agent_kanban.lib.bug_filing, never re-implemented here
(docs/coding-standards.md directive 1).

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
    Prints "key=value" lines: outcome=filed|duplicate, bug_id=BUG-NNNN,
    bug_path=<abs path, or empty for outcome=duplicate>. Exits 0 on either
    outcome; exits 1 with the reason on stderr when nothing could be filed.
"""

from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

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

from pgai_agent_kanban.lib.bug_filing import import_bug_scanner, iter_open_or_running_bugs  # noqa: E402

# The two kinds of input defect Step 4d files, and the one release-history
# pattern Step 4c files under the ship-always posture. Each maps to the slug
# the filed file is named with and the sentence its header carries.
KIND_REPORT_VALUE = "report-value"
KIND_LOOKUP_FIELD = "lookup-field"
KIND_RELEASE_PATTERN = "release-pattern"
_KINDS = (KIND_REPORT_VALUE, KIND_LOOKUP_FIELD, KIND_RELEASE_PATTERN)

_INPUT_SECTION = "## Ship-Policy Input"
_INPUT_RE = re.compile(r"^##\s+Ship-Policy Input\s*\n\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)


def input_key(kind: str, field: str, source_path: str) -> str:
    """The dedup key one filing records and a later run matches against."""
    return f"{kind}|{field}|{source_path}"


def _read_input_key(text: str) -> "str | None":
    match = _INPUT_RE.search(text)
    return match.group(1).strip() if match else None


def find_existing_bug_for_input(bugs_dir: str, key: str) -> "str | None":
    """Return the BUG-NNNN id of an open/running bug in *bugs_dir* whose
    recorded '## Ship-Policy Input' equals *key*, or None. Scans in
    filename-sorted order (via iter_open_or_running_bugs) for a
    deterministic pick."""
    for bug_id, text in iter_open_or_running_bugs(bugs_dir):
        if _read_input_key(text) == key:
            return bug_id
    return None


def _slug_for(kind: str, field: str) -> str:
    if kind == KIND_RELEASE_PATTERN:
        return "last-three-releases-non-functional"
    field_slug = re.sub(r"[^a-z0-9]+", "-", field.lower()).strip("-") or "field"
    if kind == KIND_LOOKUP_FIELD:
        return f"tester-lookup-{field_slug}"
    return f"unparseable-{field_slug}-value"


def _render_bug_content(
    *,
    bug_id: str,
    slug: str,
    project: str,
    version: str,
    kind: str,
    field: str,
    raw_value: str,
    source_path: str,
    filed_at: str,
) -> str:
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

    return (
        f"# {bug_id}-{slug}\n\n"
        f"**Bug ID:** {bug_id}-{slug}\n"
        f"**Filed By:** cm-release ship-policy (autonomous) -- {headline}\n"
        f"**Date:** {filed_at}\n"
        "**Severity:** medium\n\n"
        "## Status\nopen\n\n"
        "## Category\ncm\n\n"
        "---\n\n"
        "## Symptom\n"
        f"{symptom}\n\n"
        "## Expected\n"
        f"{expected}\n\n"
        "## Actual\n"
        f"{actual}\n\n"
        "## Reproduction\n"
        f"{remedy}\n\n"
        "## Files Involved\n"
        f"{files_involved}\n\n"
        "## Hypothesis (optional)\n"
        f"{hypothesis}\n\n"
        "---\n\n"
        f"{_INPUT_SECTION}\n"
        f"{input_key(kind, field, source_path)}\n\n"
        "## Ship-Policy Run\n"
        f"- Project: {project}\n"
        f"- Version: {version}\n"
        f"- Field: {field}\n"
        f"- Raw value: {quoted}\n"
    )


@dataclass(frozen=True)
class FilingResult:
    """Outcome of one file_or_cite_input_bug() call.

    outcome   -- "filed" (a new bug file was written) or "duplicate" (an
                 existing open/running bug already records the same input
                 key; nothing was written).
    bug_id    -- "BUG-NNNN" for either outcome.
    bug_path  -- absolute path to the filed file ("filed" only); None for
                 "duplicate".
    key       -- the dedup key this result covers.
    """

    outcome: str
    bug_id: str
    bug_path: "str | None"
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
    filed_at: "str | None" = None,
) -> FilingResult:
    """File a bug for one unreadable ship-policy input (or, for the
    release-pattern kind, for the last-three-releases-NON-FUNCTIONAL
    pattern), or cite the existing open/running bug that already records
    the same (kind, field, source path).

    Creates *bugs_dir* if it does not yet exist (a fresh project's first
    filing has no bugs/ directory yet). Never touches anything outside
    *bugs_dir* -- see the module docstring.

    Raises ValueError when *kind* is not one of the two known kinds; the CLI
    turns that into an exit 1 with the accepted set on stderr.
    """
    if kind not in _KINDS:
        raise ValueError(
            f"unknown ship-policy input kind {kind!r}; expected one of: "
            + ", ".join(_KINDS)
        )
    os.makedirs(bugs_dir, exist_ok=True)

    key = input_key(kind, field, source_path)
    existing = find_existing_bug_for_input(bugs_dir, key)
    if existing:
        return FilingResult(outcome="duplicate", bug_id=existing, bug_path=None, key=key)

    claim_next_bug_id, release_bug_id_claim = import_bug_scanner()
    slug = _slug_for(kind, field)
    bug_id, bug_path, lock_path = claim_next_bug_id(
        bugs_dir, slug, claimed_by="cm-release-ship-policy-filer"
    )
    filed_at = filed_at or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    content = _render_bug_content(
        bug_id=bug_id,
        slug=slug,
        project=project,
        version=version,
        kind=kind,
        field=field,
        raw_value=raw_value,
        source_path=source_path,
        filed_at=filed_at,
    )
    try:
        Path(bug_path).write_text(content, encoding="utf-8")
    finally:
        release_bug_id_claim(lock_path)

    return FilingResult(outcome="filed", bug_id=bug_id, bug_path=str(bug_path), key=key)


def main(argv: "list | None" = None) -> int:
    """CLI entry point -- see the module docstring's "Usage (CLI)"."""
    parser = argparse.ArgumentParser(
        prog="python3 -m pgai_agent_kanban.cm.ship_policy_bug_filer",
        description=(
            "File a bug for one ship-policy input the release step could not "
            "read (an unparseable TESTER report value, or a TESTER-task lookup "
            "field that matched only after normalization), or for the "
            "last-three-releases-NON-FUNCTIONAL pattern, or cite the "
            "open/running bug that already records it."
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
