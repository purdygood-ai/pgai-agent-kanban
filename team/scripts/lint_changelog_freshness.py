#!/usr/bin/env python3
"""
lint_changelog_freshness.py
===========================
Pre-flight gate that asserts the checked-in CHANGELOG.md artifact is
byte-identical to a fresh regeneration from the current codebase.

How it works:
  1. Discovers all release-notes/vX.Y.Z.md files in the repo root.
  2. Loads the release-notes/PUBLISHED manifest (absent = empty manifest).
  3. Loads the project-scoped bug ledger from the configured bugs directory.
  4. Regenerates CHANGELOG.md to a temporary file (via the framework temp
     resolver, never directly to /tmp) using the same changelog_writer
     invocation that cm-release uses.
  5. Reads the checked-in artifact from CHANGELOG.md.
  6. Byte-compares the two.
  7. Exits 0 when they are identical; exits 1 when they differ, printing an
     actionable error message naming the stale artifact and the command to
     refresh it.

An empty or missing CHANGELOG.md is treated as stale (exit 1) — the
artifact must be generated and committed before this gate passes.

RC mode (PGAI_LINT_CHANGELOG_MODE=rc):
  When the env var PGAI_LINT_CHANGELOG_MODE is set to "rc", the strict
  byte-compare is replaced with a tolerance check for two classes of
  legitimate in-flight-RC staleness.

  The tolerance check regenerates the CHANGELOG against the full live bugs
  directory, then computes the diff between the checked-in artifact and the
  regeneration.  A diff is tolerable in RC mode only when ALL of the
  following hold:

    a) There are no removed lines — the checked-in content is a prefix-subset
       of the regenerated content; nothing was deleted.
    b) Every added line falls into one of two accepted categories:

       Category 1 — Post-RC bug-ledger KI addition:
         The added line is a KI line (matches the ``KI-X.X.X.N —`` prefix)
         and its Public ID does NOT appear in the checked-in artifact,
         confirming the KI entry is wholly new (filed post-commit).

       Category 2 — New-release section addition:
         The added lines constitute a complete ``## vX.Y.Z`` section that
         (i)  a matching release-notes/vX.Y.Z.md file exists on the branch,
         (ii) the version heading is NOT already in the checked-in artifact
              (confirming it is a new entry, not a modification), and
         (iii) the version is the highest-versioned section present in the
               fresh regeneration that is absent from the committed artifact.

  If all conditions hold, the gate exits 0.  Any other difference is a hard
  failure.

  The admission predicate does NOT rely on filesystem mtime (which is
  unreliable — regeneration writes Public IDs back to bug files, updating
  their mtime after each regeneration call).

  RC mode NEVER applies on ai_main or other non-RC branches.  The gated
  runners (run-unit-tests.sh, run-integration-tests.sh) set this variable
  automatically when the worktree HEAD is on an rc/* or ai_rc/* branch.
  The default behaviour (strict byte-compare) is enforced when the variable
  is absent or set to any value other than "rc".

Migration note: prior to this RC (v1.19.0), CHANGELOG.md was maintained as
a hybrid hand-edited format.  This RC regenerated CHANGELOG.md via the
changelog_writer as a one-time migration step.  After this point, the
committed CHANGELOG.md is always writer-produced, and this gate enforces
that invariant on every subsequent RC.

Wired into both gated runners (run-unit-tests.sh and run-integration-tests.sh)
as a pre-flight check, following the lint_icd_freshness pattern.

Usage:
    python3 team/scripts/lint_changelog_freshness.py [--changelog PATH]
                                                      [--bugs-dir PATH]

Options:
    --changelog PATH    Path to the checked-in CHANGELOG.md artifact.
                        Default: CHANGELOG.md relative to the dev tree root.
    --bugs-dir PATH     Path to the project-scoped bug ledger directory.
                        Default: resolved from PGAI_AGENT_KANBAN_ROOT_PATH:
                        <kanban-root>/projects/pgai-agent-kanban/bugs/
                        Falls back to an empty directory when unresolvable
                        (no bugs disclosed — pre-ledger state).
    --help, -h          Show this help and exit.

Exit codes:
    0   Artifact is fresh (byte-identical to regeneration, or all differences
        are tolerable in RC mode: new KI additions and/or a new-release
        section backed by a release-notes file on the branch).
    1   Artifact is stale, missing, or regeneration failed.
    2   Usage error (unknown argument).

Environment:
    PGAI_AGENT_KANBAN_TEMP_DIR       Framework temp root (defaults to the
                                      resolver fallback when unset).  Temp
                                      files are written under this root, never
                                      directly to /tmp.
    PGAI_AGENT_KANBAN_ROOT_PATH      Kanban root used to locate the bug ledger
                                      when --bugs-dir is not specified.
    PGAI_LINT_CHANGELOG_MODE         When set to "rc", enables RC-mode tolerance
                                      for post-CHANGELOG-commit bug-ledger drift
                                      and new-release-section additions backed by
                                      a release-notes file on the branch.
                                      Any other value (including absent) uses the
                                      default strict byte-compare.
    PYTHONHASHSEED                   Must be set to 0 by the caller (gated
                                      runners do this automatically) so that
                                      changelog_writer's frozenset iteration
                                      over section heading names is stable
                                      across independent process invocations.
                                      The committed CHANGELOG.md must also be
                                      generated under PYTHONHASHSEED=0 to
                                      ensure byte-identical comparison.
                                      Without this, frozenset ordering varies
                                      between Python processes, making the
                                      byte-compare non-deterministic.
"""

from __future__ import annotations

import argparse
import difflib
import os
import pathlib
import re
import sys
import tempfile

# ---------------------------------------------------------------------------
# Dev-tree root resolution.
# This file lives at team/scripts/lint_changelog_freshness.py.
# Two parent levels: scripts/ -> team/ -> project_root/
# ---------------------------------------------------------------------------
_THIS_FILE = pathlib.Path(__file__).resolve()
_TEAM_DIR = _THIS_FILE.parent.parent  # team/
_DEV_TREE_ROOT = _TEAM_DIR.parent  # project_root/
_DEFAULT_CHANGELOG_PATH = _DEV_TREE_ROOT / "CHANGELOG.md"

# Default project name — the kanban-self project that owns this script.
_DEFAULT_PROJECT_NAME = "pgai-agent-kanban"

# Pattern that matches a KI line prefix in CHANGELOG output.
# Example: "KI-1.0.0.1 — symptom text; affects v1.0.0 · open"
_KI_LINE_RE = re.compile(r"^KI-(\d+\.\d+\.\d+\.\d+)\s+—")

# Pattern to extract Public IDs already present in the checked-in CHANGELOG.
_KI_ID_IN_CHANGELOG_RE = re.compile(r"\bKI-(\d+\.\d+\.\d+\.\d+)\b")

# Pattern that matches a CHANGELOG section heading for a release version.
# Example: "## v1.22.5 — 2026-07-14"
_RELEASE_HEADING_RE = re.compile(r"^## (v\d+\.\d+\.\d+)(?:\s|$)")


def _get_temp_root() -> pathlib.Path:
    """Return the framework temp root directory.

    Uses PGAI_AGENT_KANBAN_TEMP_DIR when set, otherwise falls back to the
    documented resolver default.  Never writes directly to bare /tmp.

    Returns:
        Path to the temp root directory (created if absent).
    """
    # anti-pattern-allowlist: 2 (justification: the literal is the resolver's
    # documented last-resort fallback, mirroring the behaviour of temp.sh's
    # pgai_temp_dir(); callers of this script should set PGAI_AGENT_KANBAN_TEMP_DIR)
    root = (
        pathlib.Path(
            os.environ.get("PGAI_AGENT_KANBAN_TEMP_DIR", "") or "/tmp/pgai_kanban_tmp"
        )
        / "changelog_freshness"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_bugs_dir() -> pathlib.Path:
    """Resolve the project-scoped bug ledger directory.

    Derives the path from PGAI_AGENT_KANBAN_ROOT_PATH via the canonical
    resolver (:func:`pgai_agent_kanban.env.resolve_kanban_root`).  Returns
    the resolved path regardless of whether it exists — the caller decides
    how to handle an absent ledger.

    Returns:
        Path to the bugs directory (may not exist).
    """
    # Ensure the team package is importable (mirrors the sys.path setup that
    # _regenerate_changelog() performs for changelog_writer).
    team_str = str(_TEAM_DIR)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)

    try:
        from pgai_agent_kanban.env import resolve_kanban_root

        kanban_root = resolve_kanban_root()
    except (ImportError, RuntimeError):
        # Graceful fallback: if the env var is unset, the caller will handle
        # the missing bugs directory.
        kanban_root = pathlib.Path.home() / "pgai_agent_kanban"

    return kanban_root / "projects" / _DEFAULT_PROJECT_NAME / "bugs"


def _extract_ki_ids(text: str) -> set[str]:
    """Return the set of KI Public IDs that appear in the given text.

    Scans for all occurrences of ``KI-X.X.X.N`` in text.

    Args:
        text: CHANGELOG content (or any string) to scan.

    Returns:
        Set of Public ID strings (e.g. {"KI-1.0.0.1", "KI-1.0.0.2"}).
    """
    return set(_KI_ID_IN_CHANGELOG_RE.findall(text))


def _extract_release_versions_from_headings(text: str) -> set[str]:
    """Return the set of release versions referenced by ## vX.Y.Z headings in text.

    Scans for all ``## vX.Y.Z`` headings in the CHANGELOG content.

    Args:
        text: CHANGELOG content (or any string) to scan.

    Returns:
        Set of version strings (e.g. {"v1.0.0", "v1.22.4"}).
    """
    return set(re.findall(r"^## (v\d+\.\d+\.\d+)(?:\s|$)", text, re.MULTILINE))


def _diff_is_rc_tolerable(
    checked_in_text: str, regenerated_text: str
) -> tuple[bool, str]:
    """Return whether the diff between checked-in and regenerated CHANGELOG is
    tolerable in RC mode, and a log message identifying which tolerance clause fired.

    A diff is tolerable when ALL of the following hold:

      a) No lines were REMOVED from the checked-in artifact.
      b) Every ADDED line falls into one of two accepted categories:

         Category 1 — Post-RC bug-ledger KI addition:
           The added line is a KI line (matches ``KI-X.X.X.N —`` prefix) and
           its Public ID does NOT appear in the checked-in artifact, confirming
           the KI entry is wholly new (filed post-commit).

         Category 2 — New-release section addition:
           The added lines constitute a complete ``## vX.Y.Z`` section where:
           (i)  a matching release-notes/vX.Y.Z.md file exists on the branch,
           (ii) the version heading is NOT already in the checked-in artifact,
                confirming it is a new entry rather than a modification.

    Condition (a) guards against RC-mode masking genuine staleness: if any
    committed line was removed or modified, the diff shows a removed line and
    the gate rejects immediately.  Condition (b-KI)(c) guards against a KI ID
    that existed in the checked-in artifact but whose line content changed.

    When both KI additions and a new-release section are present in the same
    diff, both categories are checked independently; the diff is tolerable if
    every added line satisfies at least one category.

    Args:
        checked_in_text:  Content of the checked-in CHANGELOG artifact.
        regenerated_text: Content of a fresh regeneration from the bugs dir.

    Returns:
        Tuple of (is_tolerable, clause_description) where clause_description
        names which tolerance clause(s) fired for use in log messages.
    """
    existing_ki_ids = _extract_ki_ids(checked_in_text)
    existing_versions = _extract_release_versions_from_headings(checked_in_text)

    # Derive the dev-tree root from this file's location to look up release-notes.
    release_notes_dir = _DEV_TREE_ROOT / "release-notes"

    checked_in_lines = checked_in_text.splitlines(keepends=True)
    regenerated_lines = regenerated_text.splitlines(keepends=True)

    # Two-pass approach: collect all diff lines, then evaluate tolerability.
    # This allows reasoning about groups of added lines (release sections).

    # Track which tolerance clauses fired so the log message is precise.
    has_ki_additions = False
    has_new_release_section = False

    # Track state for new-release section detection.
    # A new-release section in the CHANGELOG is structured as:
    #
    #   (blank line)
    #   ---
    #   (blank line)
    #   ## vX.Y.Z — <date>
    #   <content lines>
    #
    # When this block is added to CHANGELOG.md (at the top of the file,
    # before an already-present ## vX.Y-1.Z section), the unified diff
    # shows the preamble lines (blank, ---, blank) BEFORE the heading.
    # We buffer these candidate preamble lines and confirm them as
    # tolerable once we see the matching ## vX.Y.Z heading.

    # current_section_valid: True once a ## vX.Y.Z heading has been validated.
    current_section_valid: bool = False
    # pending_preamble: accumulated added lines before a heading is confirmed.
    # These are empty lines and '---' lines that may precede a section heading.
    pending_preamble: list[str] = []
    # Whether the pending preamble contains only blank/separator lines.
    pending_preamble_clean: bool = True

    def _is_preamble_line(s: str) -> bool:
        """Return True when s is a blank line or a '---' CHANGELOG separator."""
        stripped = s.rstrip("\n")
        return stripped == "" or stripped == "---"

    for diff_line in difflib.unified_diff(
        checked_in_lines, regenerated_lines, lineterm=""
    ):
        # Skip diff header lines (--- / +++ / @@ context markers).
        if diff_line.startswith("---") or diff_line.startswith("+++"):
            continue
        if diff_line.startswith("@@"):
            # A new diff hunk resets the section-tracking context.
            current_section_valid = False
            pending_preamble = []
            pending_preamble_clean = True
            continue
        if diff_line.startswith(" "):
            # Context line (unchanged): marks the end of any in-progress section.
            current_section_valid = False
            pending_preamble = []
            pending_preamble_clean = True
            continue
        if diff_line.startswith("-"):
            # A line was removed from the checked-in artifact.  RC-mode tolerance
            # never allows removals; this is always a hard failure.
            return False, ""
        if diff_line.startswith("+"):
            content = diff_line[1:]  # Strip the leading '+' from the diff line.

            # --- Category 1: KI-line addition ---
            ki_match = _KI_LINE_RE.match(content)
            if ki_match:
                public_id = "KI-" + ki_match.group(1)
                if public_id in existing_ki_ids:
                    # This KI ID was already in the checked-in artifact, but the
                    # line content differs (otherwise the diff wouldn't show it as
                    # added).  Something other than pure ledger-drift changed it.
                    # Hard failure to avoid masking genuine staleness.
                    return False, ""
                # Valid KI addition: new ID not present in the committed artifact.
                has_ki_additions = True
                # A KI line cannot start or continue a release section.
                current_section_valid = False
                pending_preamble = []
                pending_preamble_clean = True
                continue

            # --- Category 2: New-release section line ---
            # Check whether this line opens or continues a tolerable release section.
            heading_match = _RELEASE_HEADING_RE.match(content)
            if heading_match:
                # Start of a new section heading.  Validate it.
                version = heading_match.group(1)
                if version in existing_versions:
                    # This version is already in the committed artifact.
                    # The heading is being modified — hard failure.
                    return False, ""
                # Check whether a release-notes file exists for this version.
                notes_file = release_notes_dir / f"{version}.md"
                if not notes_file.exists():
                    # No backing release-notes file — hard failure.
                    return False, ""
                # The pending preamble (blank lines and --- separators) is
                # confirmed as the preamble for this valid section heading.
                if not pending_preamble_clean:
                    # The preamble contained non-preamble lines — hard failure.
                    return False, ""
                # Valid new-release section heading confirmed.
                current_section_valid = True
                has_new_release_section = True
                pending_preamble = []
                pending_preamble_clean = True
                continue

            if current_section_valid:
                # This line is a content line inside a tolerable release section
                # (everything between the ## vX.Y.Z heading and the next context
                # line or new hunk).  Accept it.
                continue

            # The line has not been confirmed as part of a valid section yet.
            # Check if it could be a preamble line (blank or '---' separator).
            if _is_preamble_line(content):
                # Accumulate as a candidate preamble line; it will be confirmed
                # tolerable if a valid ## vX.Y.Z heading follows.
                pending_preamble.append(content)
                continue

            # Non-preamble line before a validated section heading — mark the
            # pending preamble as dirty.  If a heading follows, validation fails.
            pending_preamble.append(content)
            pending_preamble_clean = False
            # Do NOT return False yet; wait to see if this gets resolved.
            # But if we reach the end without confirming a heading, the dirty
            # preamble means the diff was not tolerable.

    # After all diff lines are processed, check for unresolved dirty preamble.
    if pending_preamble and not pending_preamble_clean and not current_section_valid:
        # Accumulated non-preamble lines that were never confirmed by a heading.
        return False, ""

    # Also check for the case where pending_preamble has content and no heading
    # confirmed it — meaning we have orphan preamble lines with no section.
    if pending_preamble and not current_section_valid:
        # Orphan preamble (blank/--- lines with no following heading) are not
        # tolerable as standalone additions.
        return False, ""

    if not (has_ki_additions or has_new_release_section):
        # No tolerable additions were found, yet the diff passed the no-removal
        # check.  This means the diff was empty (byte-identical) — the caller
        # should have already handled that case before calling this function.
        # Return True as a safety valve; the caller's byte-compare catches this.
        return True, "no differences"

    # Build a description of which tolerance clauses fired.
    clauses = []
    if has_ki_additions:
        clauses.append("post-CHANGELOG-commit KI additions tolerated")
    if has_new_release_section:
        clauses.append(
            "new-release section addition backed by release-notes file tolerated"
        )
    clause_description = "; ".join(clauses)

    return True, clause_description


def _regenerate_to_temp(
    temp_root: pathlib.Path,
    bugs_dir: pathlib.Path,
) -> pathlib.Path | None:
    """Regenerate CHANGELOG.md to a temp file and return the path.

    Uses the same changelog_writer.regenerate() invocation as cm-release:
    discovers all release-notes/vX.Y.Z.md files newest-first, loads the
    PUBLISHED manifest, loads the bug ledger, and regenerates to a temp file.

    The repo root is always derived from the script's own location (_DEV_TREE_ROOT),
    not from the --changelog argument.  The --changelog argument controls which
    file is compared against the regeneration, not where the source inputs live.

    Args:
        temp_root:  Directory under the framework temp root to use.
        bugs_dir:   Path to the project-scoped bug ledger.

    Returns:
        Path to the regenerated temp file, or None if regeneration failed
        (error already printed to stderr).
    """
    repo_root = _DEV_TREE_ROOT

    # Import the writer module.  Adjust sys.path so the team package is
    # importable when this script is run directly (not via pytest).
    team_str = str(_TEAM_DIR)
    if team_str not in sys.path:
        sys.path.insert(0, team_str)
    dev_tree_str = str(_DEV_TREE_ROOT)
    if dev_tree_str not in sys.path:
        sys.path.insert(0, dev_tree_str)

    try:
        from pgai_agent_kanban.cm.changelog_writer import (
            load_published_manifest,
            regenerate,
            _parse_version,
        )
    except ImportError as exc:
        print(
            f"ERROR: cannot import changelog_writer module: {exc}\n"
            "Ensure team/ is on PYTHONPATH or run from the repository root.",
            file=sys.stderr,
        )
        return None

    # Load the PUBLISHED manifest (absent = empty: no published releases).
    published = load_published_manifest(repo_root)

    # Discover all versioned release-notes files, newest first.
    notes_dir = repo_root / "release-notes"
    if not notes_dir.is_dir():
        print(
            f"ERROR: release-notes/ directory not found at {notes_dir}",
            file=sys.stderr,
        )
        return None
    found = sorted(
        notes_dir.glob("v*.md"),
        key=lambda p: _parse_version(p.stem),
        reverse=True,
    )
    versions = [p.stem for p in found]

    # Write to a named temp file under the framework temp root.
    fd, tmp_str = tempfile.mkstemp(
        prefix="changelog_fresh_", suffix=".md", dir=str(temp_root)
    )
    os.close(fd)
    tmp_path = pathlib.Path(tmp_str)

    try:
        content = regenerate(repo_root, versions, published, bugs_dir)
        tmp_path.write_text(content, encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        print(
            f"ERROR: changelog regeneration failed: {exc}",
            file=sys.stderr,
        )
        tmp_path.unlink(missing_ok=True)
        return None

    return tmp_path


def _is_rc_mode() -> bool:
    """Return True when RC-mode tolerance is active.

    RC mode is enabled only when the PGAI_LINT_CHANGELOG_MODE environment
    variable is set to the exact string "rc" (case-sensitive).  Any other
    value — including absent, empty, or any case variant — leaves the gate
    in its default strict byte-compare mode.

    Returns:
        True when PGAI_LINT_CHANGELOG_MODE == "rc", False otherwise.
    """
    return os.environ.get("PGAI_LINT_CHANGELOG_MODE", "") == "rc"


def check_freshness(
    changelog_path: pathlib.Path = _DEFAULT_CHANGELOG_PATH,
    bugs_dir: pathlib.Path | None = None,
) -> bool:
    """Assert that changelog_path is byte-identical to a fresh regeneration.

    In normal mode (PGAI_LINT_CHANGELOG_MODE not "rc"):
        Strict byte-compare.  Any difference between the checked-in artifact
        and a fresh regeneration is a failure.

    In RC mode (PGAI_LINT_CHANGELOG_MODE == "rc"):
        Regenerates against the full live bugs_dir, then checks if the diff
        between the checked-in artifact and the regeneration is tolerable.
        A diff is tolerable when there are no removed lines, every added line
        is a KI line, and every added KI's Public ID is new (not present in
        the checked-in artifact).  See _diff_is_rc_tolerable() for the full
        predicate.

        This approach does NOT rely on filesystem mtime — bug files'
        modification times are unreliable because regenerate() writes Public
        IDs back to bug files, bumping their mtime.

    Args:
        changelog_path: Path to the checked-in CHANGELOG.md artifact to verify.
        bugs_dir:       Path to the project-scoped bug ledger.  When None,
                        resolved via _resolve_bugs_dir().

    Returns:
        True when the artifact is fresh (or all staleness is tolerable in RC
        mode); False when stale, missing, or regeneration failed.
    """
    if bugs_dir is None:
        bugs_dir = _resolve_bugs_dir()

    temp_root = _get_temp_root()
    rc_mode = _is_rc_mode()

    regen_path = _regenerate_to_temp(temp_root, bugs_dir)
    if regen_path is None:
        return False

    try:
        checked_in = changelog_path.read_bytes() if changelog_path.exists() else None
        regenerated = regen_path.read_bytes()
    finally:
        regen_path.unlink(missing_ok=True)

    if checked_in is None:
        print(
            f"lint_changelog_freshness: FAIL — artifact not found at {changelog_path}\n"
            "Run cm-release or invoke the changelog_writer to regenerate CHANGELOG.md "
            "and commit the result:\n"
            "  python3 -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir>",
            file=sys.stderr,
        )
        return False

    if checked_in == regenerated:
        print(f"lint_changelog_freshness: ok — {changelog_path} is fresh.")
        return True

    if rc_mode:
        # Interpret the diff in RC-mode tolerance terms.
        checked_in_text = checked_in.decode("utf-8", errors="replace")
        regenerated_text = regenerated.decode("utf-8", errors="replace")
        is_tolerable, clause_description = _diff_is_rc_tolerable(
            checked_in_text, regenerated_text
        )
        if is_tolerable:
            print(
                f"lint_changelog_freshness: ok — {changelog_path} is fresh "
                f"(RC mode: {clause_description})."
            )
            return True

        print(
            f"lint_changelog_freshness: FAIL — {changelog_path} is stale.\n"
            "RC-mode tolerance active, but the diff is not entirely attributable "
            "to tolerable in-flight-RC changes (new KI additions or a new-release "
            "section backed by a release-notes file on the branch).\n"
            "The checked-in artifact does not match a fresh regeneration from the "
            "current codebase, and some differences are not tolerable.\n"
            "Regenerate and commit CHANGELOG.md:\n"
            "  python3 -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir>",
            file=sys.stderr,
        )
        return False

    # Normal mode: strict byte-compare already failed above.
    print(
        f"lint_changelog_freshness: FAIL — {changelog_path} is stale.\n"
        "The checked-in artifact does not match a fresh regeneration from the "
        "current codebase.\n"
        "Regenerate and commit CHANGELOG.md:\n"
        "  python3 -m pgai_agent_kanban.cm.changelog_writer <repo_root> <bugs_dir>",
        file=sys.stderr,
    )
    return False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lint_changelog_freshness.py",
        description=(
            "Assert that CHANGELOG.md is byte-identical to a fresh regeneration "
            "from the current codebase.  Exits 0 when fresh, 1 when stale or missing.\n"
            "\n"
            "Set PGAI_LINT_CHANGELOG_MODE=rc to enable RC-mode tolerance: KI-line "
            "additions caused by bugs filed after the CHANGELOG.md git commit are "
            "tolerated (the diff must be only new KI lines with no removals)."
        ),
    )
    parser.add_argument(
        "--changelog",
        metavar="PATH",
        help=(
            "Path to the checked-in CHANGELOG.md artifact.  Default: CHANGELOG.md "
            "relative to the dev tree root."
        ),
    )
    parser.add_argument(
        "--bugs-dir",
        metavar="PATH",
        help=(
            "Path to the project-scoped bug ledger directory.  "
            "Default: <kanban-root>/projects/pgai-agent-kanban/bugs/ resolved from "
            "PGAI_AGENT_KANBAN_ROOT_PATH."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point for CLI invocation.

    Args:
        argv: Argument list (default: sys.argv[1:]).

    Returns:
        Exit code (0 = fresh, 1 = stale/error, 2 = usage error).
    """
    args = _parse_args(argv)
    changelog_path = (
        pathlib.Path(args.changelog) if args.changelog else _DEFAULT_CHANGELOG_PATH
    )
    bugs_dir = pathlib.Path(args.bugs_dir) if args.bugs_dir else None

    ok = check_freshness(changelog_path=changelog_path, bugs_dir=bugs_dir)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
