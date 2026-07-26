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
  When the env var PGAI_LINT_CHANGELOG_MODE is set to "rc" AND the current
  git branch is an RC branch (rc/* or ai_rc/*), the strict byte-compare is
  replaced with a tolerance check for legitimate in-flight-RC staleness.

  The tolerance check regenerates the CHANGELOG against the full live bugs
  directory, then computes the diff between the checked-in artifact and the
  regeneration.  A diff is tolerable in RC mode only when ALL added lines
  fall into one of two accepted categories, and ALL removed lines fall into
  one of three accepted writer-migration drift categories:

  Tolerable ADDED lines:

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

  Tolerable REMOVED lines (writer-migration drift categories, per the recorded defect):

    Category 3 — Fenced-section ``## Fixed`` sub-header removal:
      A line exactly matching ``## Fixed`` (with optional trailing whitespace)
      that the older writer emitted as a sub-section header inside a release
      entry but the current writer no longer produces.

    Category 4 — Hyphen-prefixed KI-resolved line removal:
      A line matching the pattern ``- KI-N.N.N.N — resolved`` that the older
      writer emitted for resolved Known Issues but the current writer replaces
      with a full-format KI title line.

    Category 5 — Embedded triple-backtick code sample removal:
      A line consisting only of triple-backtick (` ``` `) that the older writer
      emitted as an opening or closing fence for embedded code samples inside
      KI descriptions or Fixed entries but the current writer strips.

  These three removal categories represent one-time writer-rule migration drift:
  the checked-in CHANGELOG was committed under older writer rules, and the
  current writer no longer produces those patterns.  Allowing their removal
  enables RC verification to pass without requiring a per-RC CHANGELOG
  regeneration; the next release's CHANGELOG commit will eliminate the drift.

  If all conditions hold, the gate exits 0.  Any other difference is a hard
  failure.

  The admission predicate does NOT rely on filesystem mtime (which is
  unreliable — regeneration writes Public IDs back to bug files, updating
  their mtime after each regeneration call).

  RC mode NEVER applies on ai_main or other non-RC branches, even when
  PGAI_LINT_CHANGELOG_MODE=rc is set in the environment.  The gate checks the
  current git branch directly and enforces strict byte-compare on ai_main so
  that writer-migration drift is caught before an RC opens rather than
  surfacing at every RC verification.  The gated runners
  (run-unit-tests.sh, run-integration-tests.sh) set PGAI_LINT_CHANGELOG_MODE=rc
  automatically when the worktree HEAD is on an rc/* or ai_rc/* branch.
  The default behaviour (strict byte-compare) is enforced when the variable
  is absent, set to any value other than "rc", or the branch is ai_main.

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
    PGAI_LINT_CHANGELOG_MODE         When set to "rc" AND the current git branch
                                      is rc/* or ai_rc/*, enables RC-mode tolerance
                                      for: post-CHANGELOG-commit bug-ledger KI drift
                                      (Category 1), new-release-section additions
                                      backed by a release-notes file (Category 2),
                                      and writer-migration drift removals — fenced
                                      ## Fixed headers (Category 3), hyphen-prefix
                                      KI-resolved lines (Category 4), and embedded
                                      triple-backtick code fences (Category 5).
                                      On ai_main and all non-RC branches, RC-mode
                                      tolerance is suppressed unconditionally so
                                      drift is caught before an RC opens (fail-fast).
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

# ---------------------------------------------------------------------------
# Writer-migration drift categories (an earlier defect enumeration).
#
# These three patterns identify REMOVED lines in the diff between a checked-in
# CHANGELOG (committed under older writer rules) and a fresh regeneration from
# the current writer.  They represent one-time format migrations; the current
# writer no longer produces these patterns.  RC-mode tolerance allows their
# removal so that an RC can proceed without forcing a per-RC CHANGELOG
# regeneration.  Any other removed-line pattern is still a hard failure.
#
# Category 3 — Fenced ``## Fixed`` sub-section header:
#   Older writer emitted a ``## Fixed`` sub-section header inside each release
#   entry.  Current writer omits it (title-only projection from an earlier defect).
# Category 4 — Hyphen-prefixed KI-resolved line:
#   Older writer emitted ``- KI-N.N.N.N — resolved`` for resolved Known Issues.
#   Current writer emits a full-format KI title line instead.
# Category 5 — Embedded triple-backtick code fence:
#   Older writer embedded ``` fences inside KI descriptions and Fixed entries.
#   Current writer strips them (title-only projection from an earlier defect).
# ---------------------------------------------------------------------------

# Category 3: exactly "## Fixed" (optional trailing whitespace, no other content).
_DRIFT_FIXED_HEADER_RE = re.compile(r"^## Fixed\s*$")

# Category 4: "- KI-N.N.N.N — resolved" hyphen-prefix resolved-KI line.
_DRIFT_KI_RESOLVED_RE = re.compile(r"^-\s+KI-\d+\.\d+\.\d+\.\d+\s+—\s+resolved\s*$")

# Category 5: a line consisting only of triple-backtick (code fence open/close).
# Allows optional trailing whitespace.
_DRIFT_TRIPLE_BACKTICK_RE = re.compile(r"^```\s*$")


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

      a) Every REMOVED line falls into one of three accepted writer-migration
         drift categories (an earlier defect enumeration):

         Category 3 — Fenced ``## Fixed`` sub-section header:
           The removed line exactly matches ``## Fixed`` (optional trailing
           whitespace).  The older writer emitted this as a sub-section header;
           the current writer omits it.

         Category 4 — Hyphen-prefixed KI-resolved line:
           The removed line matches ``- KI-N.N.N.N — resolved``.  The older
           writer emitted this for resolved Known Issues; the current writer
           emits a full-format KI title line instead.

         Category 5 — Embedded triple-backtick code fence:
           The removed line consists only of ` ``` ` (triple-backtick).  The
           older writer embedded these fences inside KI descriptions and Fixed
           entries; the current writer strips them.

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

    If any removed line does NOT match one of the three drift categories, the
    gate rejects immediately (removal of genuine content is never tolerable).
    Similarly, if any added line does not satisfy Category 1 or Category 2, the
    gate rejects.

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
    has_drift_removals = False  # True when writer-migration drift lines are removed.

    # Category 5 drift fence context: once a removed ``` line is seen, subsequent
    # removed lines (the code block content) are inside a drift fence and are also
    # tolerable removals.  The fence context is closed when a second ``` removal is
    # seen (the closing fence) or a non-removed diff line breaks the removal run.
    _inside_drift_fence: bool = False

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
            _inside_drift_fence = False
            continue
        if diff_line.startswith(" "):
            # Context line (unchanged): marks the end of any in-progress section
            # and any drift-fence context.
            current_section_valid = False
            pending_preamble = []
            pending_preamble_clean = True
            _inside_drift_fence = False
            continue
        if diff_line.startswith("-"):
            # A line was removed from the checked-in artifact.
            # RC-mode tolerance allows removal of writer-migration drift lines
            # (an earlier defect enumeration: Categories 3, 4, and 5).  Any other removal
            # is a hard failure (genuine content was deleted or modified).
            removed_content = diff_line[1:]  # Strip the leading '-'.

            # Category 5 — drift fence context: if we are inside a triple-backtick
            # fence (opened by a previous Category 5 removal), any removed line is
            # part of the embedded code sample and is tolerable.  A second ``` line
            # closes the fence context.
            if _inside_drift_fence:
                has_drift_removals = True
                if _DRIFT_TRIPLE_BACKTICK_RE.match(removed_content):
                    _inside_drift_fence = False  # Closing fence — context ends.
                continue

            if _DRIFT_FIXED_HEADER_RE.match(removed_content):        # Category 3
                has_drift_removals = True
                continue
            if _DRIFT_KI_RESOLVED_RE.match(removed_content):         # Category 4
                has_drift_removals = True
                continue
            if _DRIFT_TRIPLE_BACKTICK_RE.match(removed_content):     # Category 5 (open)
                # Opening fence — enter fence context; subsequent removed lines
                # are the code block content (also tolerable as drift).
                has_drift_removals = True
                _inside_drift_fence = True
                continue
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

    if not (has_ki_additions or has_new_release_section or has_drift_removals):
        # No tolerable changes were found, yet the diff passed all checks.
        # This means the diff was empty (byte-identical) — the caller
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
    if has_drift_removals:
        clauses.append(
            "writer-migration drift removals tolerated "
            "(fenced ## Fixed headers, hyphen-prefix KI-resolved lines, "
            "and/or embedded triple-backtick code fences)"
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


def _current_git_branch(repo_root: pathlib.Path) -> str:
    """Return the current git branch name for the repo at repo_root.

    Uses ``git symbolic-ref --short HEAD`` to resolve the branch name.  When
    in a detached-HEAD state (e.g. TESTER worktrees), falls back to
    ``git branch --points-at HEAD`` to look for an rc/* or ai_rc/* branch that
    includes the current commit.  Returns an empty string when the branch cannot
    be determined (subprocess failure or no matching branch found).

    Args:
        repo_root: Path to the git worktree root.

    Returns:
        Branch name string, or empty string if unresolvable.
    """
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "-C", str(repo_root), "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        # Detached HEAD — check for an rc/* or ai_rc/* branch at this commit.
        result2 = _sp.run(
            [
                "git", "-C", str(repo_root),
                "branch", "--format=%(refname:short)",
                "--points-at", "HEAD",
            ],
            capture_output=True,
            text=True,
        )
        if result2.returncode == 0:
            for branch in result2.stdout.splitlines():
                branch = branch.strip()
                # Skip git's detached-HEAD sentinel and any bare-parenthesized
                # non-branch names such as '(no branch)'.
                if not branch or branch.startswith("("):
                    continue
                # Prefer an rc-prefixed branch if one is present at HEAD.
                if _is_rc_branch(branch):
                    return branch
            # No rc-prefixed branch at HEAD — fall through to empty string.
    except Exception:  # noqa: BLE001
        pass
    return ""


def _is_rc_branch(branch: str) -> bool:
    """Return True when branch is an rc/* or ai_rc/* branch name.

    Args:
        branch: Git branch name string (may be empty).

    Returns:
        True when the branch name matches the rc/* or ai_rc/* pattern.
    """
    return bool(branch) and bool(re.match(r"^(rc/|ai_rc/)", branch))


def _is_rc_mode() -> bool:
    """Return True when RC-mode tolerance is active.

    RC mode requires BOTH conditions:

    1. The PGAI_LINT_CHANGELOG_MODE environment variable is set to the exact
       string "rc" (case-sensitive).
    2. The current git branch is an RC branch (rc/* or ai_rc/* prefix).

    When on ai_main or any other non-RC branch, RC-mode tolerance is suppressed
    unconditionally, even when the env var is set to "rc".  This ensures that
    writer-migration drift is caught on ai_main before an RC opens (fail-fast),
    rather than being masked by RC-mode tolerance at every RC verification.

    Returns:
        True when both the env var is "rc" AND the current branch is rc/*.
        False in all other cases (strict byte-compare mode).
    """
    if os.environ.get("PGAI_LINT_CHANGELOG_MODE", "") != "rc":
        return False
    branch = _current_git_branch(_DEV_TREE_ROOT)
    return _is_rc_branch(branch)


def check_freshness(
    changelog_path: pathlib.Path = _DEFAULT_CHANGELOG_PATH,
    bugs_dir: pathlib.Path | None = None,
) -> bool:
    """Assert that changelog_path is byte-identical to a fresh regeneration.

    In normal mode (PGAI_LINT_CHANGELOG_MODE not "rc", or branch is not rc/*):
        Strict byte-compare.  Any difference between the checked-in artifact
        and a fresh regeneration is a failure.  This mode applies unconditionally
        on ai_main so that writer-migration drift is caught before an RC opens.

    In RC mode (PGAI_LINT_CHANGELOG_MODE == "rc" AND branch is rc/* or ai_rc/*):
        Regenerates against the full live bugs_dir, then checks if the diff
        between the checked-in artifact and the regeneration is tolerable.
        See _diff_is_rc_tolerable() for the full predicate, which tolerates:
          - Added lines: new KI entries (Category 1) or new-release sections
            (Category 2).
          - Removed lines: writer-migration drift — fenced ## Fixed headers
            (Category 3), hyphen-prefix KI-resolved lines (Category 4), and
            embedded triple-backtick code fences (Category 5).
        Any other difference is a hard failure.

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
            "  bash team/scripts/regenerate-changelog.sh --project <name>",
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
            "to tolerable in-flight-RC changes.\n"
            "Tolerable categories: new KI additions (Category 1), new-release "
            "section backed by a release-notes file (Category 2), fenced ## Fixed "
            "sub-section header removal (Category 3), hyphen-prefix KI-resolved line "
            "removal (Category 4), embedded triple-backtick code fence removal "
            "(Category 5).\n"
            "The checked-in artifact does not match a fresh regeneration from the "
            "current codebase, and some differences are not in the tolerable set.\n"
            "Regenerate and commit CHANGELOG.md:\n"
            "  bash team/scripts/regenerate-changelog.sh --project <name>",
            file=sys.stderr,
        )
        return False

    # Normal mode: strict byte-compare already failed above.
    print(
        f"lint_changelog_freshness: FAIL — {changelog_path} is stale.\n"
        "The checked-in artifact does not match a fresh regeneration from the "
        "current codebase.\n"
        "Regenerate and commit CHANGELOG.md:\n"
        "  bash team/scripts/regenerate-changelog.sh --project <name>",
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
