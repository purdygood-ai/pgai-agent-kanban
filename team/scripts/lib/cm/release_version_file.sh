#!/usr/bin/env bash
# team/scripts/lib/cm/release_version_file.sh
#
# CM release VERSION-file stage: Step 11d, which writes the bare release
# version to $REPO_ROOT/VERSION and commits it into the release, so every
# checkout, clone, archive and tag carries the committed version without a
# git-describe or stamping step. Called from the end of
# cm_release_generate_notes (release_notes.sh), which sources this file
# itself; split into its own file so release_notes.sh stays under the
# shared per-file size ceiling (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py).
#
# Public entry point
# -------------------
#   cm_release_write_version_file
#       Writes VERSION when its bytes differ from the expected
#       "<bare version>\n" and commits it on the checked-out branch.
#       Byte-compare idempotency: a re-run with VERSION already correct and
#       committed is a no-op that says so. Takes no arguments; reads
#       REPO_ROOT, ACTIVE_RC and PROJECT_NAME. Sets _CLEAN_RELEASE_VERSION
#       (the bare, prefix-stripped version) as a global, which release.sh's
#       own Step 13 pre-tag VERSION gate reads again. Must be called
#       directly (never via command substitution) — its commit failure
#       paths call `exit 1` directly.
#
# Dependencies (must already be sourced/defined by the time it is called)
# ------------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_strip_prefix_from_tag
#   team/scripts/cm/release.sh — git_step
# The caller must have `set -euo pipefail` enabled.

# Guard against double-sourcing.
if declare -F cm_release_write_version_file >/dev/null 2>&1; then
    return 0
fi

# -----------------------------------------------------------------------
# cm_release_write_version_file — entry point (see header doc above).
# -----------------------------------------------------------------------
cm_release_write_version_file() {
  # --- Step 11d: Write VERSION file into the release commit ---
  # Writes the clean bare release version (e.g. v1.23.9) to $REPO_ROOT/VERSION and
  # stages it into a dedicated release commit alongside CHANGELOG.md.  This makes
  # every checkout, clone, zip download, and tag carry the committed version without
  # requiring a git-describe or any stamping step.
  #
  # Content: the bare version string (prefix stripped), single line, one trailing newline.
  # Idempotency: byte-compare the on-disk file against the expected content; skip
  # write and commit when already byte-identical (re-runs are clean no-ops).
  #
  # VERSION_DETAIL remains tool-written at deploy time by stamp_version_files — do not
  # touch it here.
  echo "[Step 11d] Writing VERSION for ${ACTIVE_RC}..."
  _CLEAN_RELEASE_VERSION="$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null)" || _CLEAN_RELEASE_VERSION="$ACTIVE_RC"
  _version_expected_content="$(printf '%s\n' "$_CLEAN_RELEASE_VERSION")"

  # Byte-compare: skip write when the on-disk file already matches.
  _version_needs_write=1
  if [[ -f "$REPO_ROOT/VERSION" ]]; then
    _version_on_disk="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || true)"
    if [[ "$_version_on_disk" == "$_version_expected_content" ]]; then
      _version_needs_write=0
    fi
  fi

  if [[ $_version_needs_write -eq 0 ]]; then
    # File already matches; check whether it is already staged/committed.
    git_step "git add VERSION (idempotency)" git -C "$REPO_ROOT" add "VERSION"
    if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
      echo "[Step 11d] step 11d: already complete, continuing — VERSION is byte-identical and already committed (no staged changes)."
    else
      _commit_ver_out=""
      _commit_ver_rc=0
      _commit_ver_out="$(git -C "$REPO_ROOT" commit -m "Add committed VERSION for ${ACTIVE_RC}" 2>&1)" || _commit_ver_rc=$?
      if [[ $_commit_ver_rc -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: git commit VERSION failed (rc=${_commit_ver_rc})" >&2
        echo "$_commit_ver_out" >&2
        # CENSUS-ANCHOR: release-notes-version-commit-failure-reentry-01
        exit 1
      else
        echo "$_commit_ver_out"
        echo "  [Step 11d] VERSION committed (was already correct on disk; staged and committed)."
      fi
    fi
  else
    # Write the version string to disk.
    printf '%s\n' "$_CLEAN_RELEASE_VERSION" > "$REPO_ROOT/VERSION"
    echo "  VERSION written: $_CLEAN_RELEASE_VERSION"

    git_step "git add VERSION" git -C "$REPO_ROOT" add "VERSION"
    if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
      echo "[Step 11d] step 11d: already complete, continuing — VERSION already committed from prior run (no staged changes)."
    else
      _commit_ver_out=""
      _commit_ver_rc=0
      _commit_ver_out="$(git -C "$REPO_ROOT" commit -m "Add committed VERSION for ${ACTIVE_RC}" 2>&1)" || _commit_ver_rc=$?
      if [[ $_commit_ver_rc -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: git commit VERSION failed (rc=${_commit_ver_rc})" >&2
        echo "$_commit_ver_out" >&2
        # CENSUS-ANCHOR: release-notes-version-commit-failure-fresh-01
        exit 1
      else
        echo "$_commit_ver_out"
        echo "  [Step 11d] VERSION committed for ${ACTIVE_RC}."
      fi
    fi
  fi
}
