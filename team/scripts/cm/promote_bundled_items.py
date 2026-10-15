#!/usr/bin/env python3
"""
promote_bundled_items.py — Step 16b helper for scripts/cm/release.sh
                           and document-workflow finalize helper.

Promotes each BUG-*.md (or PRIORITY-*.md) file referenced in the shipping
bundle's ## Bundled Items section from status 'running' to 'done' -- but only
when some DONE task's README claims that item's fix (see "What a README
claims" below). Closing an item is a claim its fix shipped, so an enumerated
item no DONE README claims is left at 'running' and reported as carried
rather than closed. Once item promotion finishes (whatever the carried
count), the bundle requirements file's own ## Status is promoted from
'running' to 'done' unconditionally.

What happens to a carried item: it stays at 'running', and once this bundle
is 'done' no open bundle lists it, so the next discovery pass offers it as a
bundling candidate again (pgai_agent_kanban.lib.bundle_items decides which
'running' items were carried; discovery's item scan and the bug-backlog
cache both read it). Nothing here rewrites the item's status.

What a README claims: an identifier counts when at least one clause of the
README names it without saying it is outside the task. A clause that names
an item only to say it is not ticketed, out of scope, deferred, left unfixed,
excluded or skipped does not claim it. A bug-file path listed under
## Inputs -- the line the task materializer writes for each item a fix
ticket owns -- always claims its item.

Which DONE tasks count: only a DONE task whose README ## Release Version names
the version being shipped is evidence for this release. The field is read
through pgai_agent_kanban.cm.release_version, the normaliser the CM TESTER-task
lookup also uses, so "v1.2.3", "1.2.3" and a branch-prefixed "ai_v1.2.3" all
name v1.2.3. A DONE task from another release -- an older one never archived,
or one whose RC was cancelled -- and a DONE task with no Release Version claim
nothing here: closing an item on their word would record a fix this release
does not carry.

Derivation path: In addition to the ## Bundled Items enumeration path, a
second derivation path scans the project's tasks directory for DONE tasks of
the shipped version whose README files reference BUG-NNNN identifiers, and
cross-references those against bug files in the bugs/ directory that are still
at ## Status: running. This catches bugs that shipped in a release but were not
explicitly enumerated in the bundle's ## Bundled Items section.

--doc-mode: For the document workflow, scans a requirements/ directory for the
file whose ## Target Version exactly matches a given semver string (e.g. v0.0.1)
and promotes its ## Status from 'running' to 'done'. Uses the semver verbatim —
no derivation or transformation.

Items that were carried (enumerated but claimed by no DONE task) and bugs
that cannot be auto-promoted (still at 'running' but not derivable from
either path) are listed in a clearly-labeled post-ship report section of
stdout.

Usage (called by scripts/cm/release.sh Step 16b):
    python3 promote_bundled_items.py <bundle_file> <version> [<tasks_dir> <bugs_dir>]

    version    — the release being shipped (vX.Y.Z; a bare X.Y.Z or a
                 branch-prefixed tag name is read the same way). Only DONE
                 tasks whose ## Release Version names it count as evidence.
                 A value naming no release version counts no task, so every
                 enumerated item at 'running' is carried.

    tasks_dir  — path to the project tasks/ directory (optional). When provided,
                 gates ## Bundled Items promotion on a DONE task README
                 reference (see Invariants) and enables the derivation path.
                 When omitted, no ## Bundled Items entry can be verified as
                 fixed, so every enumerated item at 'running' is carried.
    bugs_dir   — path to the project bugs/ directory (optional, paired with
                 tasks_dir). When provided, stale-running bugs in this directory
                 are scanned for the post-ship report.

Usage (called by scripts/cm/finalize.sh --doc-mode step — document workflow):
    python3 promote_bundled_items.py --doc-mode <req_dir> <semver>

    req_dir    — path to the project's requirements/ directory.
    semver     — the exact semver from the document run (e.g. v0.0.1). Must be
                 the requirement's actual ## Target Version; NOT derived.

Exit codes:
    0  All promoted items and the bundle file itself were processed (or already
       at 'done' — idempotent).
    1  An unexpected error prevented processing (exception, unreadable file,
       etc.).  Promotion of any remaining items is NOT attempted after an error.
       In --doc-mode: also exits 1 when no matching requirement file is found.
    --help (or -h) prints this text on stdout and exits 0.

Invariants:
    - Only files whose ## Status is currently "running" are promoted. Files
      already at "done" (or "open" or any other value) are skipped silently.
    - An enumerated ## Bundled Items entry at 'running' is promoted ONLY when
      tasks_dir is supplied AND some DONE task of the shipped version claims
      that item's BUG-NNNN / PRIORITY-NNNN identifier in its README. Absent that claim -- including
      when tasks_dir itself is omitted -- the item is left at 'running' and
      named in the post-ship report as carried; discovery re-offers it once
      this bundle is 'done'. The gate fails toward "leave it," never toward
      "close it."
    - If a referenced item path does not exist on disk, a WARN is emitted and
      that item is skipped; the remaining items and the bundle file itself are
      still processed.
    - The bundle file's own ## Status is promoted AFTER item promotion, so a
      partial-failure in item promotion is still visible in the bundle file's
      status (it remains 'running' only if the script exits non-zero).
    - The task-scanning derivation path ONLY promotes a bug if a DONE task
      of the shipped version claims it in its README — it never promotes bugs that are merely at 'running'
      without a task-level confirmation of shipment, and a negative mention
      is no confirmation. This is the same claim rule the enumeration gate
      above applies to ## Bundled Items entries.
    - --doc-mode is idempotent: re-running against an already-done requirement
      emits a skip message and exits 0.

This module is imported directly by the unit test suite so the core logic
lives in importable functions rather than only in the if-__main__ block.
"""

import re
import sys
import pathlib

# Each tree finds itself. Run by file path (``python3 .../promote_bundled_items.py``,
# as lib/cm/release_finalize.sh and an operator's --help both do) this script
# has no parent package and only its own directory is on sys.path. Put this
# file's own package root -- the directory holding the pgai_agent_kanban/ this
# tree ships, two levels above cm/: team/ in the repository, the kanban root
# in an install -- at the front of sys.path so the package imports below
# resolve from the same tree, never another, with or without PYTHONPATH.
if not __package__:
    _OWN_PACKAGE_ROOT = str(pathlib.Path(__file__).resolve().parents[2])
    if _OWN_PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _OWN_PACKAGE_ROOT)

from pgai_agent_kanban.cm.release_version import release_version_of, task_release_version  # noqa: E402
from pgai_agent_kanban.lib.bundle_items import parse_bundled_item_paths  # noqa: E402
from pgai_agent_kanban.task_status import read_state  # noqa: E402


# ---------------------------------------------------------------------------
# Core promotion helper
# ---------------------------------------------------------------------------

def promote_status_running_to_done(p: pathlib.Path) -> bool:
    """Promote ## Status: running -> done in file p.

    Returns True if the file was promoted (was at 'running'), False if the
    file was skipped (not at 'running' or lacks a ## Status field).

    Raises OSError / PermissionError if the file cannot be read or written.
    """
    content = p.read_text(encoding="utf-8")
    status_m = re.search(
        r'^(##\s+Status\s*\n\s*)running(\s*)$',
        content,
        re.MULTILINE | re.IGNORECASE,
    )
    if not status_m:
        has_status = re.search(r'^##\s+Status\s*$', content, re.MULTILINE | re.IGNORECASE)
        if has_status:
            cur_m = re.search(r'^##\s+Status\s*\n\s*(\S+)', content, re.MULTILINE | re.IGNORECASE)
            cur_val = cur_m.group(1) if cur_m else "unknown"
            print(f"  skip (status={cur_val}): {p.name}", flush=True)
        else:
            print(f"  skip (no ## Status field): {p.name}", flush=True)
        return False
    new_content = (
        content[: status_m.start()]
        + status_m.group(1)
        + "done"
        + status_m.group(2)
        + content[status_m.end() :]
    )
    p.write_text(new_content, encoding="utf-8")
    print(f"  promoted (running -> done): {p.name}", flush=True)
    return True


def read_status_value(p: pathlib.Path) -> str | None:
    """Return the raw ## Status field value for file p, or None when the
    file is unreadable or carries no ## Status field.

    Used by the ## Bundled Items enumeration path to decide, before writing,
    whether an item is even eligible for promotion -- distinct from
    promote_status_running_to_done()'s own check, which performs the write.
    """
    try:
        content = p.read_text(encoding="utf-8")
    except OSError:
        return None
    m = re.search(r'^##\s+Status\s*\n\s*(\S+)', content, re.MULTILINE | re.IGNORECASE)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Item-identifier derivation helpers
#
# Shared by both the ## Bundled Items enumeration path (BUG- and PRIORITY-
# items alike) and the task-scanning derivation path (BUG- items only, via
# the derive_shipped_bug_ids_from_tasks() view below). Both paths apply the
# same safe bound: an identifier counts as shipped only when some DONE
# task's README claims it (extract_claimed_item_ids_from_text()).
# ---------------------------------------------------------------------------

_ITEM_ID_PATTERN = re.compile(r'\b(BUG|PRIORITY)-(\d{4})\b', re.IGNORECASE)
_ITEM_ID_FILENAME_PATTERN = re.compile(r'^(BUG|PRIORITY)-(\d{4})\b', re.IGNORECASE)


def extract_item_ids_from_text(text: str) -> set[str]:
    """Return the set of BUG-NNNN / PRIORITY-NNNN identifiers found in text,
    normalised to an upper-case prefix (e.g. 'BUG-0001', 'PRIORITY-0004')."""
    return {f"{m.group(1).upper()}-{m.group(2)}" for m in _ITEM_ID_PATTERN.finditer(text)}


def extract_bug_ids_from_text(text: str) -> set[str]:
    """BUG-only view of extract_item_ids_from_text(), kept for
    promote_derived_bugs(), which cross-references BUG-*.md files only."""
    return {item_id for item_id in extract_item_ids_from_text(text) if item_id.startswith("BUG-")}


# A whitespace-delimited token that carries an item identifier: a bare ID, a
# filename stem or filename (its slug may itself hold words such as
# "deferred"), or a whole path ending in one. Removed from a clause before
# the clause is read for negative wording, so a slug never reads as a
# statement about the item.
_ITEM_TOKEN_PATTERN = re.compile(r'\S*\b(?:BUG|PRIORITY)-\d{4}\b\S*', re.IGNORECASE)

# Wording that says a clause names an item only to put it outside the task:
# not ticketed, out of scope, deferred, left unfixed, excluded, skipped.
# "Carries" is deliberately absent: a ride-along ticket "carries" the items
# it fixes. Matched case-insensitively with loose whitespace and hyphens.
_NEGATIVE_MENTION_PATTERN = re.compile(
    r"""
    \b(?:not|never|isn'?t|aren'?t|wasn'?t|no\s+longer)\s+
        (?:(?:been|being|yet|be|going\s+to\s+be)\s+)?
        (?:ticketed|fixed|addressed|covered|included|handled|planned|scheduled|
           tackled|implemented|in[\s-]+scope|part\s+of)\b
    | \bout[\s-]+of[\s-]+scope\b
    | \bun(?:ticketed|fixed|addressed|planned)\b
    | \bdefer(?:s|red|ring)?\b
    | \bpostpone[sd]?\b
    | \bwon'?t[\s-]+(?:be[\s-]+)?(?:fix(?:ed)?|do(?:ne)?|ticketed)\b
    | \bwont[\s-]do\b
    | \bleft\s+(?:open|running|unfixed|unticketed|alone|for\s+(?:a\s+)?(?:later|future|next))\b
    | \bexclude[sd]?\b
    | \bskip(?:s|ped)?\b
    | \b(?:no|without\s+a?)\s*(?:ticket|fix)\s+(?:for|in)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Clause boundaries inside one line: a sentence end (. ! ? ;) followed by
# whitespace, or a contrasting conjunction. A parenthetical is read as its
# own clause, apart from the sentence around it.
_CLAUSE_SPLIT_PATTERN = re.compile(
    r'(?<=[.!?;])\s+|,?\s+\b(?:but|whereas|while|however|although)\b\s+',
    re.IGNORECASE,
)
_PARENTHETICAL_PATTERN = re.compile(r'\(([^()]*)\)')


def _readme_clauses(text: str) -> list[str]:
    """Split README text into the clauses a mention is judged within: one
    per sentence of each line, with every parenthetical split out as a
    clause of its own."""
    clauses: list[str] = []
    for line in text.splitlines():
        for sentence in _CLAUSE_SPLIT_PATTERN.split(line):
            clauses.extend(_PARENTHETICAL_PATTERN.findall(sentence))
            remainder = _PARENTHETICAL_PATTERN.sub(" ", sentence)
            clauses.append(remainder)
    return [clause for clause in clauses if clause.strip()]


def is_negative_mention(clause: str) -> bool:
    """True when *clause* names its items only to put them outside the task
    (not ticketed, out of scope, deferred, left unfixed, skipped ...).

    The item tokens themselves -- IDs, filenames, paths -- are removed first,
    so wording inside a slug never counts.
    """
    return bool(_NEGATIVE_MENTION_PATTERN.search(_ITEM_TOKEN_PATTERN.sub(" ", clause)))


def extract_claimed_item_ids_from_text(text: str) -> set[str]:
    """Return the BUG-NNNN / PRIORITY-NNNN identifiers *text* claims to fix.

    An identifier counts when at least one clause naming it is not a
    negative mention (see is_negative_mention). A README that names an item
    only to say it is not ticketed, out of scope or deferred does not claim
    it. A bug-file path under ## Inputs -- the line the task
    materializer writes for every item a fix ticket owns -- is a clause with
    no other wording, so it always counts.
    """
    claimed: set[str] = set()
    for clause in _readme_clauses(text):
        ids = extract_item_ids_from_text(clause)
        if ids and not is_negative_mention(clause):
            claimed |= ids
    return claimed


def item_id_from_filename(name: str) -> str | None:
    """Return the normalised BUG-NNNN / PRIORITY-NNNN identifier a bundled
    item's filename starts with, or None when the name carries neither
    recognised prefix.

    An item whose filename cannot be resolved to an identifier is an item the
    DONE-reference gate cannot verify -- the caller carries it rather than
    assuming it is safe to promote.
    """
    m = _ITEM_ID_FILENAME_PATTERN.match(name)
    if not m:
        return None
    return f"{m.group(1).upper()}-{m.group(2)}"


def derive_shipped_item_ids_from_tasks(tasks_dir: pathlib.Path, version: str) -> set[str]:
    """Scan tasks_dir for DONE tasks of *version* and extract the BUG-NNNN /
    PRIORITY-NNNN identifiers their READMEs claim
    (extract_claimed_item_ids_from_text()).

    A task is considered DONE when its status.md contains a ## State field whose
    value is 'DONE' (case-insensitive). Only DONE tasks whose README
    ## Release Version names *version* contribute identifiers; both sides are
    read through release_version_of(), so "v1.2.3", "1.2.3" and "ai_v1.2.3"
    are the same release. A task with no Release Version, or one naming
    another release, contributes nothing, and a *version* that names no
    release version matches no task at all.

    Returns a set of normalised identifier strings (e.g. {'BUG-0001',
    'PRIORITY-0004'}). Returns an empty set if tasks_dir does not exist or
    is empty.

    This is the safe derivation bound shared by the ## Bundled Items
    enumeration gate and the task-scanning derivation path below: an item is
    only included if a DONE task README claims it — never inferred from the
    item file alone, and never from a README that names it only to say it
    is out of the task.
    """
    shipped: set[str] = set()
    shipped_version = release_version_of(version)
    if not shipped_version or not tasks_dir.is_dir():
        return shipped

    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        # Skip the queues/ subdirectory — it is not a task folder.
        if task_dir.name == "queues":
            continue

        status_file = task_dir / "status.md"
        readme_file = task_dir / "README.md"

        if not status_file.is_file() or not readme_file.is_file():
            continue

        # Only a DONE task counts; read_state is the one status.md reader.
        state_tokens = read_state(status_file).split()
        if not state_tokens or state_tokens[0] != "DONE":
            continue

        # A DONE task from another release is no evidence for this one.
        if task_release_version(readme_file) != shipped_version:
            continue

        # DONE task of this release — extract identifiers from README
        try:
            readme_text = readme_file.read_text(encoding="utf-8")
        except OSError:
            continue

        shipped |= extract_claimed_item_ids_from_text(readme_text)

    return shipped


def derive_shipped_bug_ids_from_tasks(tasks_dir: pathlib.Path, version: str) -> set[str]:
    """BUG-only view of derive_shipped_item_ids_from_tasks(), kept for
    promote_derived_bugs(), which cross-references BUG-*.md files in
    bugs_dir only."""
    return {
        item_id
        for item_id in derive_shipped_item_ids_from_tasks(tasks_dir, version)
        if item_id.startswith("BUG-")
    }


def find_running_bugs_in_dir(bugs_dir: pathlib.Path) -> list[pathlib.Path]:
    """Return bug files in bugs_dir whose ## Status is currently 'running'.

    Only files matching BUG-*.md are considered. Files are returned sorted
    by name for deterministic output.
    """
    running: list[pathlib.Path] = []
    if not bugs_dir.is_dir():
        return running

    for bug_file in sorted(bugs_dir.glob("BUG-*.md")):
        try:
            text = bug_file.read_text(encoding="utf-8")
        except OSError:
            continue
        status_m = re.search(
            r'^##\s+Status\s*\n\s*(\S+)',
            text,
            re.MULTILINE | re.IGNORECASE,
        )
        if status_m and status_m.group(1).strip().lower() == "running":
            running.append(bug_file)

    return running


def promote_derived_bugs(
    tasks_dir: pathlib.Path,
    bugs_dir: pathlib.Path,
    already_promoted_paths: set[pathlib.Path],
    version: str,
) -> dict:
    """Derive shipped bugs from DONE task READMEs and promote them.

    Cross-references:
      - The set of BUG-NNNN IDs the READMEs of DONE tasks of *version* claim
        (shipped set).
      - The set of bug files in bugs_dir currently at ## Status: running.

    Bugs that appear in both sets AND are not already promoted via the Bundled
    Items path are promoted to 'done'. Bugs at 'running' not in the shipped set
    are listed in a post-ship report (cannot-auto-promote).

    Args:
        tasks_dir:               Path to the project tasks/ directory.
        bugs_dir:                Path to the project bugs/ directory.
        already_promoted_paths:  Set of pathlib.Path objects already promoted
                                 by the Bundled Items path (to avoid double-count).
        version:                 The release being shipped; only DONE tasks
                                 whose ## Release Version names it count.

    Returns a dict with keys:
        derived_promoted:         int  — bugs promoted via derivation path
        derived_skipped_not_running: int  — bugs in shipped set already at 'done'
        derived_errors:           int  — bugs in shipped set that errored on write
        cannot_auto_promote:      list[str]  — filenames of 'running' bugs not derivable
    """
    shipped_bug_ids = derive_shipped_bug_ids_from_tasks(tasks_dir, version)
    running_bugs = find_running_bugs_in_dir(bugs_dir)

    derived_promoted = 0
    derived_skipped_not_running = 0
    derived_errors = 0
    cannot_auto_promote: list[str] = []

    for bug_path in running_bugs:
        if bug_path in already_promoted_paths:
            # Already handled by the Bundled Items path — skip silently.
            continue

        # Extract the bug ID stem from the filename (e.g. "BUG-NNNN" from
        # "BUG-NNNN-example-bug.md").
        stem = bug_path.stem  # e.g. "BUG-NNNN-example-bug-slug"
        id_m = re.match(r'^(BUG-\d{4})\b', stem, re.IGNORECASE)
        bug_id = id_m.group(1).upper() if id_m else None

        if bug_id and bug_id in shipped_bug_ids:
            # This bug shipped — promote it.
            try:
                result = promote_status_running_to_done(bug_path)
                if result:
                    print(
                        f"  derived promotion (task-README path, {bug_id} referenced"
                        f" in a DONE task of {version}): {bug_path.name}",
                        flush=True,
                    )
                    derived_promoted += 1
                else:
                    # File is no longer at 'running' (idempotent or raced).
                    derived_skipped_not_running += 1
            except OSError as exc:
                print(
                    f"  ERROR: could not promote derived bug {bug_path}: {exc}",
                    flush=True,
                    file=sys.stderr,
                )
                derived_errors += 1
        else:
            # Bug is at 'running' but not derivable from DONE tasks — report it.
            cannot_auto_promote.append(bug_path.name)

    return {
        "derived_promoted": derived_promoted,
        "derived_skipped_not_running": derived_skipped_not_running,
        "derived_errors": derived_errors,
        "cannot_auto_promote": cannot_auto_promote,
    }


def emit_post_ship_report(
    carried_items: list[str],
    cannot_auto_promote: list[str],
    version: str,
) -> None:
    """Emit a labeled post-ship report for {version}, always emitted (even
    when both lists are empty) so Step 16b output is consistent.

    Two sections:
      1. carried_items       — enumerated ## Bundled Items entries left at
                                'running' because no DONE task README claims
                                a fix for them this release. Not closed:
                                once the bundle is 'done', the next
                                discovery pass offers each as a candidate
                                for the next bundle.
      2. cannot_auto_promote — bugs at ## Status: running the task-scanning
                                derivation path could not confirm shipped.
    """
    print(
        f"\n--- Step 16b post-ship report: bugs still at 'running' for {version} ---",
        flush=True,
    )
    print(f"  Carried (not closed): {len(carried_items)} item(s)", flush=True)
    if carried_items:
        print(
            "  The following ## Bundled Items entries stay at ## Status: running"
            " — no DONE task README claims a fix for them this release, so"
            " they are not closed. Once this bundle is done, the next"
            " discovery pass offers each as a candidate for the next bundle:",
            flush=True,
        )
        for name in sorted(carried_items):
            print(f"    - {name}", flush=True)
    if cannot_auto_promote:
        print(
            "  The following bug files remain at ## Status: running but could not"
            " be auto-promoted (not referenced in any DONE task README for this"
            " release). Operator should review and promote manually if the fix"
            " shipped:",
            flush=True,
        )
        for name in sorted(cannot_auto_promote):
            print(f"    - {name}", flush=True)
    if not carried_items and not cannot_auto_promote:
        print(
            "  No unresolved stale-running bugs detected — all known shipped bugs promoted.",
            flush=True,
        )
    print("--- end post-ship report ---\n", flush=True)


# ---------------------------------------------------------------------------
# High-level promotion driver (used by main and tests)
# ---------------------------------------------------------------------------

def promote_bundle(
    bundle_file: pathlib.Path,
    version: str,
    tasks_dir: pathlib.Path | None = None,
    bugs_dir: pathlib.Path | None = None,
) -> dict:
    """Promote all bundled items and the bundle file itself.

    An enumerated ## Bundled Items entry at 'running' is promoted only when
    tasks_dir is supplied AND the README of some DONE task of *version*
    claims that item's BUG-NNNN / PRIORITY-NNNN identifier -- closing an item is a claim its fix
    shipped, and enumeration alone is not that evidence. An entry that fails
    the check is left at 'running' and counted as carried; once the bundle
    is 'done', discovery offers it for the next bundle. When tasks_dir is
    omitted, the check has nothing to consult, so every enumerated entry at
    'running' is carried rather than promoted.

    When tasks_dir and bugs_dir are both provided, also derives the
    shipped-bug set from DONE task READMEs and promotes any bug files in
    bugs_dir that are still at 'running' and referenced by a DONE task
    (unchanged from prior behaviour). Bugs at 'running' not derivable from
    any source are listed in the post-ship report alongside carried items.

    Returns a summary dict with keys:
        has_bundled_items_section:      bool
        item_paths:                     list[str]
        promoted:                       int
        skipped_not_running:            int
        skipped_missing:                int
        carried:                        int         (enumerated, left running)
        carried_items:                  list[str]
        bundle_file_promoted:           bool
        derived_promoted:               int   (0 when derivation path not used)
        derived_skipped_not_running:    int
        derived_errors:                 int
        cannot_auto_promote:            list[str]

    Does NOT raise — callers can inspect the return value.  Individual item
    errors (OSError on a specific path) are caught and counted as missing.
    """
    text = bundle_file.read_text(encoding="utf-8")

    has_bundled_items_section = bool(
        re.search(r'^\s*##\s+Bundled Items\s*$', text, re.MULTILINE | re.IGNORECASE)
    )

    item_paths = parse_bundled_item_paths(text)

    promoted = 0
    skipped_not_running = 0
    skipped_missing = 0
    carried = 0
    carried_items: list[str] = []
    # Track which bug paths this path already disposed of -- promoted OR
    # carried -- so the derivation path below skips them rather than
    # re-listing a carried item a second time as merely "cannot auto-promote".
    bundled_handled_paths: set[pathlib.Path] = set()

    # The DONE-reference set the enumeration gate below checks against.
    # None when tasks_dir is absent, in which case the gate cannot run and
    # every 'running' entry is carried (see promote_bundle's own docstring).
    shipped_item_ids: set[str] | None = None
    if tasks_dir is not None:
        if not release_version_of(version):
            print(
                f"  WARN: version {version!r} names no release version (expected vX.Y.Z);"
                " no DONE task can be tied to it, so every 'running' item is carried."
                " Re-run with the shipped version, e.g.:"
                f" python3 promote_bundled_items.py {bundle_file} v1.2.3 {tasks_dir}"
                f" {bugs_dir if bugs_dir is not None else tasks_dir.parent / 'bugs'}",
                flush=True,
            )
        shipped_item_ids = derive_shipped_item_ids_from_tasks(tasks_dir, version)

    if has_bundled_items_section:
        for path_str in item_paths:
            p = pathlib.Path(path_str)
            if not p.exists():
                print(f"  WARN: item not found (skipped): {path_str}", flush=True)
                skipped_missing += 1
                continue

            current_status = read_status_value(p)
            if current_status is None or current_status.strip().lower() != "running":
                # Not eligible for promotion regardless of the DONE-reference
                # gate below -- delegate to promote_status_running_to_done()
                # so the existing idempotent skip path (already 'done', or a
                # malformed file) prints its own message and stays quiet on
                # re-runs, exactly as before this change.
                try:
                    if promote_status_running_to_done(p):
                        promoted += 1
                        bundled_handled_paths.add(p.resolve())
                    else:
                        skipped_not_running += 1
                except OSError as exc:
                    print(f"  ERROR: could not read/write {path_str}: {exc}", flush=True, file=sys.stderr)
                    skipped_missing += 1
                continue

            item_id = item_id_from_filename(p.name)
            if shipped_item_ids is None or not item_id or item_id not in shipped_item_ids:
                # No DONE task of this release claims a fix for this item --
                # closing it would claim a fix that never shipped. Leave it
                # at 'running': once this bundle is 'done' below, no open
                # bundle lists it, and discovery offers it for the next one.
                print(f"  carried (no DONE task claims it): {p.name}", flush=True)
                carried_items.append(p.name)
                carried += 1
                bundled_handled_paths.add(p.resolve())
                continue

            try:
                if promote_status_running_to_done(p):
                    promoted += 1
                    bundled_handled_paths.add(p.resolve())
                else:
                    skipped_not_running += 1
            except OSError as exc:
                print(f"  ERROR: could not read/write {path_str}: {exc}", flush=True, file=sys.stderr)
                skipped_missing += 1

        if not item_paths:
            print(
                f"  ## Bundled Items section present but contains no item paths"
                f" in {bundle_file.name}",
                flush=True,
            )
        else:
            print(
                f"  Bundled items: {promoted} promoted,"
                f" {skipped_not_running} skipped (not running),"
                f" {skipped_missing} missing,"
                f" {carried} carried (unfixed)",
                flush=True,
            )
    else:
        print(
            f"  No ## Bundled Items section in {bundle_file.name}"
            f" — operator-authored file; skipping item promotion.",
            flush=True,
        )

    # --- Derivation path ---
    # When tasks_dir and bugs_dir are available, derive the shipped-bug set
    # from DONE task READMEs and promote any remaining 'running' bugs.
    derived_promoted = 0
    derived_skipped_not_running = 0
    derived_errors = 0
    cannot_auto_promote: list[str] = []

    if tasks_dir is not None and bugs_dir is not None:
        print(
            f"\n  [derivation] Running derivation path:"
            f" tasks_dir={tasks_dir}, bugs_dir={bugs_dir}",
            flush=True,
        )
        derive_result = promote_derived_bugs(
            tasks_dir=tasks_dir,
            bugs_dir=bugs_dir,
            already_promoted_paths=bundled_handled_paths,
            version=version,
        )
        derived_promoted = derive_result["derived_promoted"]
        derived_skipped_not_running = derive_result["derived_skipped_not_running"]
        derived_errors = derive_result["derived_errors"]
        cannot_auto_promote = derive_result["cannot_auto_promote"]

        print(
            f"  Derivation path: {derived_promoted} promoted,"
            f" {derived_skipped_not_running} skipped (not running),"
            f" {derived_errors} errors",
            flush=True,
        )

    emit_post_ship_report(carried_items, cannot_auto_promote, version)

    # Step (b): promote the requirements file's own ## Status from running to done.
    # This is the idempotency gate used by discovery_step_requirements.
    # Applies to ALL matched files (auto-generated bundles and operator-authored files),
    # and always happens regardless of how many items above were carried --
    # the release still ships; only the record of what it fixed changes.
    print(f"  Promoting requirements file itself: {bundle_file.name}", flush=True)
    bundle_file_promoted = promote_status_running_to_done(bundle_file)

    return {
        "has_bundled_items_section": has_bundled_items_section,
        "item_paths": item_paths,
        "promoted": promoted,
        "skipped_not_running": skipped_not_running,
        "skipped_missing": skipped_missing,
        "carried": carried,
        "carried_items": carried_items,
        "bundle_file_promoted": bundle_file_promoted,
        "derived_promoted": derived_promoted,
        "derived_skipped_not_running": derived_skipped_not_running,
        "derived_errors": derived_errors,
        "cannot_auto_promote": cannot_auto_promote,
    }


# ---------------------------------------------------------------------------
# Document-workflow requirement promotion helpers
# ---------------------------------------------------------------------------

# Pattern for ## Target Version section: group 2 is the body, the lines
# after the heading up to the next line starting with "##" (or end of file).
# Body lines never start with "##", so an empty section reads as empty rather
# than as the heading and text of the section after it.
_TARGET_VERSION_SECTION_RE = re.compile(
    r'(^##[^\S\n]*Target[^\S\n]*Version[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)',
    re.IGNORECASE | re.MULTILINE,
)


def find_requirement_by_version(
    req_dir: pathlib.Path,
    semver: str,
) -> pathlib.Path | None:
    """Scan req_dir for a requirements file whose ## Target Version matches semver.

    The match is an exact, case-insensitive, whitespace-tolerant comparison
    against each line in the ## Target Version section body.  The semver is
    normalised to always have a 'v' prefix before comparison.

    Returns the path of the first matching file (sorted lexicographically for
    determinism), or None when no match is found.

    Files named REQUIREMENTS-TEMPLATE*.md are skipped (template scaffolding).
    """
    # Normalise: ensure v-prefix.
    target = semver.strip()
    if not target.startswith("v"):
        target = "v" + target
    # Regex for an exact full-line match (liberal whitespace on both sides).
    version_pattern = re.compile(
        r'^\s*' + re.escape(target) + r'\s*$',
        re.MULTILINE,
    )

    if not req_dir.is_dir():
        return None

    for md_file in sorted(req_dir.glob("*.md")):
        if md_file.name.startswith("REQUIREMENTS-TEMPLATE"):
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        m = _TARGET_VERSION_SECTION_RE.search(text)
        if not m:
            continue
        body = m.group(2)
        if version_pattern.search(body):
            return md_file

    return None


def promote_document_requirement(
    req_dir: pathlib.Path,
    semver: str,
) -> int:
    """Promote a document-workflow requirement's ## Status from running to done.

    Scans req_dir for the requirements file whose ## Target Version exactly
    matches semver.  If found, calls promote_status_running_to_done() on it
    (idempotent — already-done files are skipped silently).

    Args:
        req_dir: Path to the project's requirements/ directory.
        semver:  The exact semver from the document run (e.g. 'v0.0.1').
                 Must be the requirement's actual ## Target Version — NOT derived
                 by incrementing or transforming another field.

    Returns:
        0  Requirement found and promoted (or was already at 'done' — idempotent).
        1  req_dir does not exist, req_dir is not a directory, no matching
           requirement file found, or an OSError occurred during the write.

    This function is the Python entry point used by scripts/cm/finalize.sh (--doc-mode)
    so that the semver-to-file lookup and promotion are handled in one place,
    testable independently of finalize.sh.
    """
    if not req_dir.is_dir():
        print(
            f"ERROR: requirements directory not found or not a directory: {req_dir}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    # Normalise semver for display.
    display_ver = semver.strip()
    if not display_ver.startswith("v"):
        display_ver = "v" + display_ver

    req_file = find_requirement_by_version(req_dir, semver)
    if req_file is None:
        print(
            f"ERROR: no requirements file found in {req_dir} "
            f"with ## Target Version: {display_ver}",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"  The requirement file's ## Status will remain at 'running'.",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"  Operator may promote manually: edit ## Status to 'done' in the "
            f"matching requirements file under {req_dir}/.",
            file=sys.stderr,
            flush=True,
        )
        return 1

    print(
        f"  Requirements file for {display_ver}: {req_file.name}",
        flush=True,
    )

    try:
        promote_status_running_to_done(req_file)
    except OSError as exc:
        print(
            f"ERROR: could not read/write {req_file}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    return 0


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """Run the promotion CLI for a shipped bundle, or for a document requirement.

    ``BUNDLE_FILE VERSION [TASKS_DIR BUGS_DIR]`` promotes each bundled item a
    DONE task of VERSION claims from ``running`` to ``done``, then the bundle
    itself; ``--doc-mode REQ_DIR SEMVER`` promotes one document-workflow
    requirement. Exit codes: 0 everything processed (or already ``done``);
    1 a usage error, or an error stopped processing (in ``--doc-mode``, also no
    matching requirement file). ``--help`` or ``-h`` prints the module
    documentation, usage included, on stdout and exits 0.
    """
    if len(sys.argv) >= 2 and sys.argv[1] in ("--help", "-h"):
        print(__doc__.strip(), flush=True)
        return 0

    # -----------------------------------------------------------------------
    # --doc-mode <req_dir> <semver>
    # --doc-mode: document-workflow finalize promotion path.
    # Searches req_dir for the requirement file matching semver and promotes it.
    # -----------------------------------------------------------------------
    if len(sys.argv) >= 2 and sys.argv[1] == "--doc-mode":
        if len(sys.argv) < 4:
            print(
                f"Usage: {sys.argv[0]} --doc-mode <req_dir> <semver>",
                file=sys.stderr,
                flush=True,
            )
            print(
                "  req_dir: path to the project's requirements/ directory",
                file=sys.stderr,
                flush=True,
            )
            print(
                "  semver:  exact semver from the document run (e.g. v0.0.1)",
                file=sys.stderr,
                flush=True,
            )
            return 1

        req_dir = pathlib.Path(sys.argv[2])
        semver = sys.argv[3]
        return promote_document_requirement(req_dir, semver)

    # -----------------------------------------------------------------------
    # Standard mode: <bundle_file> <version> [<tasks_dir> <bugs_dir>]
    # Step 16b for cm/release.sh.
    # -----------------------------------------------------------------------
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} <bundle_file> <version> [<tasks_dir> <bugs_dir>]",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"       {sys.argv[0]} --doc-mode <req_dir> <semver>",
            file=sys.stderr,
            flush=True,
        )
        return 1

    bundle_file = pathlib.Path(sys.argv[1])
    version = sys.argv[2]  # the shipped release; scopes which DONE tasks count

    # Optional derivation path arguments.
    tasks_dir: pathlib.Path | None = None
    bugs_dir: pathlib.Path | None = None
    if len(sys.argv) >= 5:
        tasks_dir = pathlib.Path(sys.argv[3])
        bugs_dir = pathlib.Path(sys.argv[4])
        if not tasks_dir.is_dir():
            print(
                f"  WARN: tasks_dir not found or not a directory: {tasks_dir}"
                " — derivation path disabled.",
                flush=True,
            )
            tasks_dir = None
            bugs_dir = None
        elif not bugs_dir.is_dir():
            print(
                f"  WARN: bugs_dir not found or not a directory: {bugs_dir}"
                " — derivation path disabled.",
                flush=True,
            )
            tasks_dir = None
            bugs_dir = None

    if not bundle_file.exists():
        print(
            f"ERROR: bundle file not found: {bundle_file}",
            file=sys.stderr,
            flush=True,
        )
        return 1

    promote_bundle(bundle_file, version, tasks_dir=tasks_dir, bugs_dir=bugs_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
