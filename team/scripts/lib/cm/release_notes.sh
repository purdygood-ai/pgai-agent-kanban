#!/usr/bin/env bash
# team/scripts/lib/cm/release_notes.sh
#
# CM release-notes stage: generates release-notes/<ACTIVE_RC>.md from the RC
# branch's commits (or a bundled requirements file's categorized items),
# commits any WRITER polish left uncommitted on disk, regenerates
# CHANGELOG.md from the full release-notes history, adds a one-time
# CHANGELOG.md pointer to README.md, and writes the committed VERSION file
# into the release commit. Sourced by team/scripts/cm/release.sh; extracted
# from that script's former inline Steps 8, 8b, 11b, 11c, and 11d so
# release.sh's own body stays a thin driver over sourced lib stages. This is
# a behavior-preserving move: no stdout/stderr line, exit code, HALT-file
# write, or on-disk artifact (release-notes/*.md, CHANGELOG.md, README.md,
# VERSION) content changes as a result of this file existing — it is one
# stage split out of a larger driver so each stays a focused,
# independently testable unit.
#
# The release-notes generator itself (which sections exist, where each
# bundled item lands, how a bug's Symptom becomes its bullet) is the Python
# module team/pgai_agent_kanban/cm/release_notes_generator.py, reached
# through pp_run_ops like every other package module these stages call.
# This file carries only the shell around it: the idempotency guard, the
# bundle-file and merge-base lookups, the argument list, and the commits.
#
# Public entry point
# -------------------
#   cm_release_generate_notes
#       Runs the original Steps 8, 8b, 11b, 11c, and 11d in their original
#       order:
#         8.  Generate release-notes/$ACTIVE_RC.md from a bundled requirements
#             file's categorized items (preferred; every bug report goes under
#             Bug Fixes) or else RC commit subjects, add any ship-posture
#             auto-ship line under ## Known Issues, and commit on
#             $MAIN_BRANCH. Idempotency-guarded: skipped entirely when the
#             file is already committed to HEAD.
#         8b. Commit any uncommitted WRITER polish of
#             release-notes/$ACTIVE_RC.md so Step 11b's changelog_writer
#             reads the polished notes. Idempotency-guarded: no-op when the
#             file matches HEAD.
#         11b. Regenerate CHANGELOG.md via changelog_writer, byte-comparing
#             against a fresh render so idempotency is exact rather than
#             heading-presence-only; runs a post-regeneration BUG-[0-9]
#             safety-pass grep before committing.
#         11c. One-time README.md pointer to CHANGELOG.md, if not already
#             present.
#         11d. Write the bare release version to $REPO_ROOT/VERSION and
#             commit it (cm_release_write_version_file, in the sibling
#             release_version_file.sh this file sources). Byte-compare
#             idempotency: no-op when VERSION already matches.
#       Takes no arguments; reads REPO_ROOT, MAIN_BRANCH, RC_BRANCH,
#       ACTIVE_RC, PROJECT_NAME, KANBAN_ROOT, _CM_PROJECT_ROOT,
#       _RELEASE_NOTES_STATUS, _KNOWN_ISSUE_NOTES_BLOCK (both set by
#       cm_release_apply_ship_policy) and the ambient environment. Must be
#       called directly (never via command substitution) — its commit
#       failure paths call `exit 1` directly.
#
#       On return, _cl_buf and _CLEAN_RELEASE_VERSION are set as globals.
#       _cl_buf (the changelog_writer scratch-render temp file) is read by
#       cm_release_cleanup_on_exit (the script's EXIT trap, defined in
#       release_preflight.sh) to clean up the temp file on every exit path.
#       _CLEAN_RELEASE_VERSION (the bare, prefix-stripped version string) is
#       read again by release.sh's own Step 13 pre-tag VERSION gate. Both are
#       left as plain (non-local) assignments exactly as they were as inline
#       script code, so this leakage is preserved rather than newly
#       introduced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's entry point is called)
# -----------------------------------------------------------------------
#   team/scripts/lib/cm/internal_id_scrub.py — must ship beside this file
#     (same directory). The release-notes generator module loads its
#     _strip_internal_ids from there; Step 11a points it at this file's
#     directory via PGAI_CM_INTERNAL_ID_SCRUB_DIR — see that module's
#     docstring.
#   team/pgai_agent_kanban/cm/release_notes_generator.py — the Step 11a
#     generator, invoked through pp_run_ops.
#   team/scripts/lib/project_paths.sh — pp_strip_prefix_from_tag,
#     pp_project_root (both called below)
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops (invokes the release-notes
#     generator and changelog_writer)
#   team/scripts/lib/cm/release_ship_posture.sh — cm_ship_posture_stamp_notes
#     (Step 11a: the auto-ship line(s) under ## Known Issues)
#   team/scripts/cm/release.sh itself — git_step (defined inline in
#     release.sh after strict mode is enabled). git_step only needs to exist
#     by the time cm_release_generate_notes is CALLED, not at source time.
#   cm_release_preflight and cm_release_apply_ship_policy must already have
#     run so REPO_ROOT, MAIN_BRANCH, RC_BRANCH, ACTIVE_RC, PROJECT_NAME,
#     KANBAN_ROOT, _CM_PROJECT_ROOT, _RELEASE_NOTES_STATUS,
#     _KNOWN_ISSUE_NOTES_BLOCK are set. cm_release_squash_to_main must
#     already have run so the squash commit (and MAIN_SHA) exist before
#     release notes are generated.
#
# The caller must have `set -euo pipefail` enabled — this file relies on
# strict-mode error propagation exactly as the inline code it replaces did.

# Guard against double-sourcing.
if declare -F cm_release_generate_notes >/dev/null 2>&1; then
    return 0
fi

# Step 11d lives in its own sibling stage, sourced here so a caller that
# sources only this file still resolves it. It carries its own guard.
# shellcheck source=release_version_file.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_version_file.sh"

# -----------------------------------------------------------------------
# cm_release_generate_notes — entry point (see header doc above).
# -----------------------------------------------------------------------
cm_release_generate_notes() {
  # --- Step 8: Generate release notes from RC branch commits ---
  # Done here, after the fidelity gate (Step 7a) and squash commit (Step 7), but
  # before the RC branch is deleted (Steps 9-10).  The RC branch must still exist
  # so git log can enumerate the feature/bug commits it contributed.
  #
  # Strategy (in priority order):
  #   1. If a requirements bundle file exists for this RC, read each bundled item's
  #      ## Category field and map it to an industry-standard release notes section.
  #      A bug report (BUG-NNNN-*.md) always lands under Bug Fixes, whatever its Category.
  #   2. Fallback: collect git commit subjects from the RC branch and place all
  #      of them in the "Other Changes" section.
  #
  # Output structure (9 sections, all present even when empty):
  #   ## Breaking Changes
  #   ## Upgrade Notes
  #   ## Features
  #   ## Bug Fixes
  #   ## Deprecations
  #   ## Documentation
  #   ## Other Changes
  #   ## Bugs Skipped
  #   ## Known Issues
  echo "[Step 11a] Generating release notes for $ACTIVE_RC..."

  RELEASE_NOTES_DIR="$REPO_ROOT/release-notes"
  RELEASE_NOTES_FILE="$RELEASE_NOTES_DIR/${ACTIVE_RC}.md"
  RELEASE_DATE="$(date +%Y-%m-%d)"

  # Idempotency: if release-notes/${ACTIVE_RC}.md is already committed to HEAD on main,
  # the entire generation + add + commit block is already complete.  Re-running the Python
  # generator could produce different output (e.g., if RC_BRANCH now points at main tip
  # after a reset), creating a new commit that advances main HEAD past the existing tag.
  # Guard: skip the block entirely when the file is already present in HEAD.
  # NOTE: git ls-tree exits 0 even when the path is absent (output is empty in that case).
  # Detect presence by checking whether stdout is non-empty.
  _rn_in_head=0
  _rn_ls_out="$(git -C "$REPO_ROOT" ls-tree --name-only HEAD "release-notes/${ACTIVE_RC}.md" 2>/dev/null)" || true
  [[ -n "$_rn_ls_out" ]] && _rn_in_head=1
  if [[ $_rn_in_head -eq 1 ]]; then
    echo "[Step 11a] step 11a: already complete, continuing — release-notes/${ACTIVE_RC}.md is already committed to $MAIN_BRANCH HEAD."
  else

  # Ensure the release-notes/ directory exists
  mkdir -p "$RELEASE_NOTES_DIR"

  # Identify the point where the RC branch diverged from main.
  # merge-base gives us the last common ancestor commit.
  RC_MERGE_BASE="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "$RC_BRANCH" 2>/dev/null)" || {
    echo "WARNING: could not find merge-base for $RC_BRANCH and $MAIN_BRANCH; using empty commit list." >&2
    RC_MERGE_BASE=""
  }

  # Locate the requirements bundle file for this RC (same logic as Step 16b).
  # Used to read per-item ## Category fields for section classification.
  _cm_bundle_file_for_notes=""
  _cm_notes_project_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_notes_project_root=""
  _cm_notes_requirements_dir="${_cm_notes_project_root}/requirements"
  if [[ -n "$_cm_notes_project_root" && -d "$_cm_notes_requirements_dir" ]]; then
    while IFS= read -r -d '' _f; do
      _cm_bundle_file_for_notes="$_f"
      break
    done < <(find "$_cm_notes_requirements_dir" -maxdepth 1 -type f \
              -name "${ACTIVE_RC}-*.md" -print0 2>/dev/null | sort -z)
  fi

  # Directory this file itself lives in -- resolved via BASH_SOURCE, never
  # guessed, so the lookup stays correct in both the dev-tree layout
  # (team/scripts/lib/cm/) and the flattened installed layout
  # (scripts/lib/cm/). Passed to the generator via
  # PGAI_CM_INTERNAL_ID_SCRUB_DIR so it loads the shared internal-ID scrub
  # (internal_id_scrub.py, ships beside this file) from this same tree.
  _cm_release_notes_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

  # Generate the structured release notes file with the package module
  # pgai_agent_kanban.cm.release_notes_generator (its module docstring
  # carries the section rules and the full argument contract).
  # Arguments (positional):
  #   1: bundle file path (or "none" if not found)
  #   2: REPO_ROOT
  #   3: ACTIVE_RC (version string)
  #   4: _RELEASE_NOTES_STATUS
  #   5: RELEASE_DATE
  #   6: PROJECT_NAME
  #   7: RC_MERGE_BASE (or "none")
  #   8: RC_BRANCH
  #   9: _KNOWN_ISSUE_NOTES_BLOCK (set by cm_release_apply_ship_policy;
  #      non-empty only on a SHIP-KNOWN-ISSUE decision — see
  #      release_known_issue.sh's build_known_issue_notes_block — or on a
  #      NON-FUNCTIONAL ship through a trigger the ship posture covers, where
  #      it is the whole Known Issues body; see release_ship_posture.sh)
  #   10: _BUGS_SKIPPED_NOTES_BLOCK (set by cm_release_apply_ship_policy via
  #      cm_build_bugs_skipped_ship_block, unconditionally on every release —
  #      see release_known_issue.sh's cm_build_bugs_skipped_ship_block. Empty
  #      when no TESTER finding qualifies. Already carries the table's
  #      header/separator and every rendered cell already passed through the
  #      internal-ID scrub — the generator renders it as-is under
  #      "## Bugs Skipped", it does not rebuild it.)
  PGAI_CM_INTERNAL_ID_SCRUB_DIR="$_cm_release_notes_lib_dir" \
    pp_run_ops pgai_agent_kanban.cm.release_notes_generator \
    "${_cm_bundle_file_for_notes:-none}" \
    "$REPO_ROOT" \
    "$ACTIVE_RC" \
    "${_RELEASE_NOTES_STATUS:-FUNCTIONAL}" \
    "$RELEASE_DATE" \
    "$PROJECT_NAME" \
    "${RC_MERGE_BASE:-none}" \
    "$RC_BRANCH" \
    "${_KNOWN_ISSUE_NOTES_BLOCK:-}" \
    "${_BUGS_SKIPPED_NOTES_BLOCK:-}"
  # One auto-ship line per stop the ship posture shipped through, under
  # "## Known Issues"; nothing under matrix (release_ship_posture.sh).
  cm_ship_posture_stamp_notes "$RELEASE_NOTES_FILE"

  # Commit the release notes file on main (alongside the squash commit just made).
  # Idempotency: if release notes already exist unchanged from a prior run, the index
  # is empty after git add — detect via exit code, not git prose.
  git_step "git add release-notes" git -C "$REPO_ROOT" add "release-notes/${ACTIVE_RC}.md"
  if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
    echo "[Step 11a] step 11a: already complete, continuing — release notes already committed from prior run (no staged changes)."
  else
    _commit_rn_out=""
    _commit_rn_rc=0
    _commit_rn_out="$(git -C "$REPO_ROOT" commit -m "Add release notes for ${ACTIVE_RC}" 2>&1)" || _commit_rn_rc=$?
    if [[ $_commit_rn_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git commit release-notes failed (rc=${_commit_rn_rc})" >&2
      echo "$_commit_rn_out" >&2
      # CENSUS-ANCHOR: release-notes-commit-failure-01
      exit 1
    else
      echo "$_commit_rn_out"
    fi
  fi

  fi  # end: skip if release-notes/${ACTIVE_RC}.md already in HEAD

  # --- Step 8b: Commit WRITER polish of release-notes/<ACTIVE_RC>.md if present ---
  # When WRITER authored release notes on the RC branch, the squash merge already
  # brought those notes onto main.  If the WRITER also left uncommitted polish
  # changes on disk (working tree), commit them NOW — before Step 11b regenerates
  # CHANGELOG.md — so the changelog_writer reads the polished notes as input.
  # This ordering ensures that after the release: (1) the polished notes are committed,
  # (2) CHANGELOG.md reflects those polished notes, and (3) the tag lands on that
  # CHANGELOG commit, so the committed CHANGELOG.md on the tagged commit matches
  # the release notes the tag carries (tag == tip).
  #
  # Idempotency: git status --porcelain is empty when the file matches HEAD.
  # A re-run with no uncommitted polish produces no commit — this step is a no-op.
  #
  # Cases handled:
  #   - Working-tree changes to the file (uncommitted WRITER polish) → commit them
  #   - File already committed (no diff) or unchanged from generated stub → no-op
  #   - File missing (notes were never generated and WRITER never authored them)  → warning
  _BARE_VERSION_FOR_POLISH="$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _BARE_VERSION_FOR_POLISH="$ACTIVE_RC"
  _POLISH_NOTES_FILE="$REPO_ROOT/release-notes/${_BARE_VERSION_FOR_POLISH}.md"
  echo "[Step 8b] Checking for uncommitted WRITER polish of release-notes/${_BARE_VERSION_FOR_POLISH}.md..."
  if [[ ! -f "$_POLISH_NOTES_FILE" ]]; then
    echo "  [Step 8b] release-notes/${_BARE_VERSION_FOR_POLISH}.md not found; polish step skipped." >&2
  else
    _POLISH_PORCELAIN="$(git -C "$REPO_ROOT" status --porcelain "release-notes/${_BARE_VERSION_FOR_POLISH}.md" 2>/dev/null || true)"
    if [[ -n "$_POLISH_PORCELAIN" ]]; then
      echo "  [Step 8b] Uncommitted WRITER polish detected; committing before CHANGELOG regeneration..."
      git_step "git add release-notes (polish)" git -C "$REPO_ROOT" add "release-notes/${_BARE_VERSION_FOR_POLISH}.md"
      if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
        echo "  [Step 8b] step 8b: already complete, continuing — no staged polish changes to commit."
      else
        _commit_polish_out=""
        _commit_polish_rc=0
        _commit_polish_out="$(git -C "$REPO_ROOT" commit -m "Polish release notes for ${ACTIVE_RC}" 2>&1)" || _commit_polish_rc=$?
        if [[ $_commit_polish_rc -ne 0 ]]; then
          echo "" >&2
          echo "ERROR: git commit release-notes polish failed (rc=${_commit_polish_rc})" >&2
          echo "$_commit_polish_out" >&2
          # CENSUS-ANCHOR: release-notes-polish-commit-failure-01
          exit 1
        else
          echo "$_commit_polish_out"
          echo "  [Step 8b] Polish commit created before CHANGELOG regeneration."
        fi
      fi
    else
      echo "  [Step 8b] release-notes/${_BARE_VERSION_FOR_POLISH}.md is clean (no uncommitted polish)."
    fi
  fi

  # --- Step 11b: Regenerate CHANGELOG.md at project root ---
  # Regenerates the full CHANGELOG.md from all release-notes/vX.Y.Z.md files and
  # the bug ledger via changelog_writer.py (newest release first). The writer is
  # the single source of truth; using it here keeps the released artifact
  # byte-identical to a fresh render of the release notes the tag carries.
  echo "[Step 11b] Updating CHANGELOG.md for ${ACTIVE_RC}..."

  # Idempotency: always render a fresh changelog_writer buffer and compare it
  # byte-for-byte against the checked-in CHANGELOG.md.  Only skip regeneration when the two are byte-identical.
  #
  # A heading-presence-only check (the prior approach) was insufficient: a
  # WRITER-authored section satisfies the heading check but diverges from the
  # canonical writer output, and a stale section would silently merge to main.
  # The byte-compare catches any divergence — including hand-authored content,
  # reordered bullets, or retained internal BUG-NNNN identifiers that the writer
  # strips.
  _cl_bugs_dir="${_CM_PROJECT_ROOT}/bugs"
  _cl_temp_dir="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}"
  mkdir -p "$_cl_temp_dir"
  _cl_buf="$(mktemp "${_cl_temp_dir}/cl_buf_XXXXXXXX")"
  # Cleaned up by cm_release_cleanup_on_exit (the script's single EXIT trap) on every exit
  # path — do not add a second `trap ... EXIT` here; it would silently replace
  # the script-wide trap and disable the rollback guard for every step after
  # this point (see cm_release_cleanup_on_exit's own comment near the top of the script).

  # Render the canonical CHANGELOG.md to the temp buffer.
  # The render is hash-seed-independent: the writer sorts its heading sets, so
  # two runs on identical inputs are byte-identical under any seed.
  # PYTHONHASHSEED=0 is kept as defense in depth.
  # pp_run_ops sets PYTHONPATH to include the own-tree root and KANBAN_ROOT fallback.
  PYTHONHASHSEED=0 \
    pp_run_ops pgai_agent_kanban.cm.changelog_writer \
    "$REPO_ROOT" "$_cl_bugs_dir" \
    > "$_cl_buf"

  # Byte-compare: skip regeneration only when the checked-in file is byte-identical
  # to the fresh render.  A missing CHANGELOG.md on disk always diverges.
  _cl_needs_regen=1
  if [[ -f "$REPO_ROOT/CHANGELOG.md" ]] && cmp -s "$REPO_ROOT/CHANGELOG.md" "$_cl_buf"; then
    _cl_needs_regen=0
  fi

  if [[ $_cl_needs_regen -eq 0 ]]; then
    echo "[Step 11b] step 11b: already complete, continuing — CHANGELOG.md is byte-identical to a fresh changelog_writer render."
  else

  # Install the fresh render as CHANGELOG.md.
  cp "$_cl_buf" "$REPO_ROOT/CHANGELOG.md"
  echo "  CHANGELOG.md regenerated in full for ${ACTIVE_RC}."

  # Safety pass: no internal BUG-[0-9] token may survive in the regenerated
  # CHANGELOG.md.  The changelog_writer strips these via its documented safety
  # pass; if any survive (e.g. from a writer defect), fail loudly here so the
  # release is blocked rather than shipping an internal identifier.
  if grep -qE "BUG-[0-9]" "$REPO_ROOT/CHANGELOG.md"; then
    echo "" >&2
    echo "ERROR: [Step 11b] Post-regeneration safety check failed: internal BUG-[0-9] token(s) found in CHANGELOG.md." >&2
    echo "  Matching lines:" >&2
    grep -nE "BUG-[0-9]" "$REPO_ROOT/CHANGELOG.md" >&2
    echo "  The changelog_writer safety pass should strip these. Investigate changelog_writer.py." >&2
    # CENSUS-ANCHOR: release-notes-changelog-bug-token-leak-01
    exit 1
  fi
  echo "  [Step 11b] Safety pass: no internal BUG-[0-9] tokens in regenerated CHANGELOG.md."

  git_step "git add CHANGELOG.md" git -C "$REPO_ROOT" add "CHANGELOG.md"
  # Idempotency: CHANGELOG.md already committed from a prior run — detect via exit code,
  # not git prose. An empty index after git add means nothing changed.
  if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
    echo "[Step 11b] step 11b: already complete, continuing — CHANGELOG.md already committed from prior run (no staged changes)."
  else
    _commit_cl_out=""
    _commit_cl_rc=0
    _commit_cl_out="$(git -C "$REPO_ROOT" commit -m "Update CHANGELOG.md for ${ACTIVE_RC}" 2>&1)" || _commit_cl_rc=$?
    if [[ $_commit_cl_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git commit CHANGELOG.md failed (rc=${_commit_cl_rc})" >&2
      echo "$_commit_cl_out" >&2
      # CENSUS-ANCHOR: release-notes-changelog-commit-failure-01
      exit 1
    else
      echo "$_commit_cl_out"
    fi
  fi

  fi  # end: skip if CHANGELOG.md is byte-identical to fresh render

  # --- Step 11c: Add CHANGELOG.md reference to README.md (one-time) ---
  # If the project README does not already mention CHANGELOG.md, append a
  # pointer so readers know where to find release history.
  if [[ -f "$REPO_ROOT/README.md" ]]; then
    if ! grep -q "CHANGELOG.md" "$REPO_ROOT/README.md"; then
      printf '\nSee CHANGELOG.md for release history.\n' >> "$REPO_ROOT/README.md"
      git_step "git add README.md (changelog ref)" git -C "$REPO_ROOT" add "README.md"
      git_step "git commit README.md (changelog ref)" \
        git -C "$REPO_ROOT" commit -m "Add CHANGELOG.md reference to README.md"
      echo "  README.md updated with CHANGELOG.md reference."
    else
      echo "  README.md already references CHANGELOG.md — skipping."
    fi
  fi

  # --- Step 11d: Write VERSION file into the release commit ---
  # release_version_file.sh (sourced at the top of this file).
  cm_release_write_version_file
}
