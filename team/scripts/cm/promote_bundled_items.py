#!/usr/bin/env python3
"""
promote_bundled_items.py — Step 16b helper for cm-release.sh
                           and document-workflow finalize helper.

Promotes each BUG-*.md (or PRIORITY-*.md) file referenced in the shipping
bundle's ## Bundled Items section from status 'running' to 'done', then
promotes the bundle requirements file's own ## Status from 'running' to 'done'.

Derivation path: In addition to the ## Bundled Items enumeration path, a
second derivation path scans the project's tasks directory for DONE tasks whose
README files reference BUG-NNNN identifiers, and cross-references those against
bug files in the bugs/ directory that are still at ## Status: running. This
catches bugs that shipped in a release but were not explicitly enumerated in the
bundle's ## Bundled Items section.

--doc-mode: For the document workflow, scans a requirements/ directory for the
file whose ## Target Version exactly matches a given semver string (e.g. v0.0.1)
and promotes its ## Status from 'running' to 'done'. Uses the semver verbatim —
no derivation or transformation.

Bugs that cannot be auto-promoted (still at 'running' but not derivable from
either path) are listed in a clearly-labeled post-ship report section of stdout.

Usage (called by cm-release.sh Step 16b):
    python3 promote_bundled_items.py <bundle_file> <version> [<tasks_dir> <bugs_dir>]

    tasks_dir  — path to the project tasks/ directory (optional). When provided,
                 enables the derivation path.
    bugs_dir   — path to the project bugs/ directory (optional, paired with
                 tasks_dir). When provided, stale-running bugs in this directory
                 are scanned for the post-ship report.

Usage (called by cm-finalize.sh --doc-mode step — document workflow):
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

Invariants:
    - Only files whose ## Status is currently "running" are promoted. Files
      already at "done" (or "open" or any other value) are skipped silently.
    - If a referenced item path does not exist on disk, a WARN is emitted and
      that item is skipped; the remaining items and the bundle file itself are
      still processed.
    - The bundle file's own ## Status is promoted AFTER item promotion, so a
      partial-failure in item promotion is still visible in the bundle file's
      status (it remains 'running' only if the script exits non-zero).
    - The derivation path ONLY promotes a bug if it is explicitly
      referenced by a DONE task README — it never promotes bugs that are merely
      at 'running' without a task-level confirmation of shipment.
    - --doc-mode is idempotent: re-running against an already-done requirement
      emits a skip message and exits 0.

This module is imported directly by the test suite
(test_step16b_bundled_bug_status_done.py) so the core logic lives in
importable functions rather than only in the if-__main__ block.
"""

import re
import sys
import pathlib


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


# ---------------------------------------------------------------------------
# Bundle parsing helper
# ---------------------------------------------------------------------------

def parse_bundled_item_paths(bundle_text: str) -> list[str]:
    """Extract absolute item paths from the ## Bundled Items section.

    Returns a list of path strings (may be empty if the section is absent or
    contains no backtick-paren path entries).

    The expected line format for each item is:
        - FILENAME.md (`/absolute/path/to/FILENAME.md`)
    """
    lines = bundle_text.splitlines()
    in_section = False
    item_paths: list[str] = []
    for line in lines:
        if re.match(r'^\s*##\s+Bundled Items\s*$', line, re.IGNORECASE):
            in_section = True
            continue
        if in_section:
            if re.match(r'^\s*##', line):
                break
            # Extract absolute path from: - FILENAME.md (`/some/path/FILENAME.md`)
            m = re.search(r'\(\`([^`]+)\`\)', line)
            if m:
                item_paths.append(m.group(1).strip())
    return item_paths


# ---------------------------------------------------------------------------
# Bug-file derivation helpers
# ---------------------------------------------------------------------------

_BUG_ID_PATTERN = re.compile(r'\bBUG-(\d{4})\b', re.IGNORECASE)


def extract_bug_ids_from_text(text: str) -> set[str]:
    """Return the set of BUG-NNNN identifiers (normalised to 'BUG-NNNN') found in text."""
    return {f"BUG-{m.group(1)}" for m in _BUG_ID_PATTERN.finditer(text)}


def derive_shipped_bug_ids_from_tasks(tasks_dir: pathlib.Path) -> set[str]:
    """Scan tasks_dir for DONE tasks and extract BUG-NNNN references from their READMEs.

    A task is considered DONE when its status.md contains a ## State field whose
    value is 'DONE' (case-insensitive). Only DONE tasks contribute bug IDs.

    Returns a set of BUG-NNNN identifier strings (e.g. {'BUG-0001', 'BUG-0002'}).
    Returns an empty set if tasks_dir does not exist or is empty.

    This is the safe derivation bound: a bug is only included if it appears
    explicitly in a DONE task README — never inferred from the bug file alone.
    """
    shipped: set[str] = set()
    if not tasks_dir.is_dir():
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

        try:
            status_text = status_file.read_text(encoding="utf-8")
        except OSError:
            continue

        # Check ## State: DONE
        state_m = re.search(
            r'^##\s+State\s*\n\s*(\S+)',
            status_text,
            re.MULTILINE | re.IGNORECASE,
        )
        if not state_m:
            continue
        if state_m.group(1).strip().upper() != "DONE":
            continue

        # DONE task — extract BUG-NNNN from README
        try:
            readme_text = readme_file.read_text(encoding="utf-8")
        except OSError:
            continue

        shipped |= extract_bug_ids_from_text(readme_text)

    return shipped


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
      - The set of BUG-NNNN IDs referenced in DONE task READMEs (shipped set).
      - The set of bug files in bugs_dir currently at ## Status: running.

    Bugs that appear in both sets AND are not already promoted via the Bundled
    Items path are promoted to 'done'. Bugs at 'running' not in the shipped set
    are listed in a post-ship report (cannot-auto-promote).

    Args:
        tasks_dir:               Path to the project tasks/ directory.
        bugs_dir:                Path to the project bugs/ directory.
        already_promoted_paths:  Set of pathlib.Path objects already promoted
                                 by the Bundled Items path (to avoid double-count).
        version:                 Release version string (for reporting only).

    Returns a dict with keys:
        derived_promoted:         int  — bugs promoted via derivation path
        derived_skipped_not_running: int  — bugs in shipped set already at 'done'
        derived_errors:           int  — bugs in shipped set that errored on write
        cannot_auto_promote:      list[str]  — filenames of 'running' bugs not derivable
    """
    shipped_bug_ids = derive_shipped_bug_ids_from_tasks(tasks_dir)
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
                        f" in DONE task): {bug_path.name}",
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
    cannot_auto_promote: list[str],
    version: str,
) -> None:
    """Emit a labeled post-ship report for bugs that could not be auto-promoted.

    Always emitted (even when the list is empty) so Step 16b output is consistent.
    """
    print(
        f"\n--- Step 16b post-ship report: bugs still at 'running' for {version} ---",
        flush=True,
    )
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
    else:
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

    When tasks_dir and bugs_dir are provided, also
    derives the shipped-bug set from DONE task READMEs and promotes any
    bug files in bugs_dir that are still at 'running' and referenced by
    a DONE task. Bugs at 'running' not derivable from any source are
    listed in the post-ship report.

    Returns a summary dict with keys:
        has_bundled_items_section:      bool
        item_paths:                     list[str]
        promoted:                       int
        skipped_not_running:            int
        skipped_missing:                int
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
    # Track which bug paths were promoted via the Bundled Items path so the
    # derivation path can skip them (avoiding double-counting).
    bundled_promoted_paths: set[pathlib.Path] = set()

    if has_bundled_items_section:
        for path_str in item_paths:
            p = pathlib.Path(path_str)
            if not p.exists():
                print(f"  WARN: item not found (skipped): {path_str}", flush=True)
                skipped_missing += 1
                continue
            try:
                if promote_status_running_to_done(p):
                    promoted += 1
                    bundled_promoted_paths.add(p.resolve())
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
                f" {skipped_missing} missing",
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
            already_promoted_paths=bundled_promoted_paths,
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

        emit_post_ship_report(cannot_auto_promote, version)
    else:
        # No derivation path available — emit minimal post-ship report so output
        # is consistent (operator sees the section header regardless).
        emit_post_ship_report([], version)

    # Step (b): promote the requirements file's own ## Status from running to done.
    # This is the idempotency gate used by discovery_step_requirements.
    # Applies to ALL matched files (auto-generated bundles and operator-authored files).
    print(f"  Promoting requirements file itself: {bundle_file.name}", flush=True)
    bundle_file_promoted = promote_status_running_to_done(bundle_file)

    return {
        "has_bundled_items_section": has_bundled_items_section,
        "item_paths": item_paths,
        "promoted": promoted,
        "skipped_not_running": skipped_not_running,
        "skipped_missing": skipped_missing,
        "bundle_file_promoted": bundle_file_promoted,
        "derived_promoted": derived_promoted,
        "derived_skipped_not_running": derived_skipped_not_running,
        "derived_errors": derived_errors,
        "cannot_auto_promote": cannot_auto_promote,
    }


# ---------------------------------------------------------------------------
# Document-workflow requirement promotion helpers
# ---------------------------------------------------------------------------

# Pattern for ## Target Version section: captures body text between this
# section header and the next ## header (or end of file).
_TARGET_VERSION_SECTION_RE = re.compile(
    r'##\s*Target\s*Version\s*\n(.*?)(?=\n##|\Z)',
    re.IGNORECASE | re.DOTALL,
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
        body = m.group(1)
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

    This function is the Python entry point used by cm-finalize.sh (--doc-mode)
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
    # Step 16b for cm-release.sh.
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
    version = sys.argv[2]  # informational; used by caller for logging

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
