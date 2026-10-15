#!/usr/bin/env bash
# team/scripts/lib/cm/release_squash.sh
#
# CM release squash stage: checks out the main branch (or its prefixed
# equivalent), fast-forward pulls it, squash-merges the RC branch into it,
# commits the squash, and runs the post-squash fidelity gate. Sourced by
# team/scripts/cm/release.sh; extracted from that script's former inline
# Steps 5 through 7a so release.sh's own body stays a thin driver over
# sourced lib stages. This is a behavior-preserving move: no stdout/stderr
# line, exit code, HALT-file write, or git-history shape changes as a result
# of this file existing — it is one stage split out of a larger driver so
# each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   cm_release_squash_to_main
#       Runs the original Steps 5 through 7a in their original order:
#         5.  git checkout $MAIN_BRANCH && git pull --ff-only from the
#             project's remote-tracking ref <remote>/$MAIN_BRANCH, where
#             <remote> is pp_git_remote_name's answer (project.cfg [project]
#             git_remote_name, origin when absent or blank; an invalid value
#             refuses dual-channel and exits 1 before the checkout)
#             (idempotency-guarded: skipped when local main is already at or
#             ahead of <remote>/main)
#         6.  git merge --squash $RC_BRANCH into $MAIN_BRANCH (UD/DU conflicts
#             are auto-resolved via _cm_autoresolve_ud_conflicts before
#             HALTing; any other conflict shape HALTs — Trigger 5)
#         7.  git commit -m "Release $ACTIVE_RC" (idempotency-guarded: skipped
#             when the index is empty after the squash)
#         7a. Fidelity gate: git diff --quiet $RC_BRANCH $MAIN_BRANCH, HALTing
#             on divergence (Trigger 11); skipped on resume when
#             release-notes/$ACTIVE_RC.md is already committed to HEAD
#       Takes no arguments; reads REPO_ROOT, MAIN_BRANCH, RC_BRANCH, ACTIVE_RC,
#       PROJECT_NAME (all set by cm_release_preflight / release.sh) and the
#       ambient environment. Must be
#       called directly (never via command substitution) — its conflict and
#       fidelity-gate paths call `cm_halt`, which calls `exit` directly, and
#       its squash-commit failure path calls `exit 1` directly.
#
#       On return, MAIN_SHA and RC_SHA are set as globals — MAIN_SHA in
#       particular is read by cm_release_cleanup_on_exit (the script's EXIT
#       trap, defined in release_preflight.sh) to decide whether a rollback
#       is needed on later gate failure, so it is left as a plain (non-local)
#       assignment exactly as it was as inline script code.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's entry point is called)
# -----------------------------------------------------------------------
#   team/scripts/lib/cm/release_preflight.sh — cm_halt,
#     _cm_autoresolve_ud_conflicts (both called below), and
#     cm_release_preflight must already have run so REPO_ROOT, MAIN_BRANCH,
#     RC_BRANCH, ACTIVE_RC are set.
#   team/scripts/cm/release.sh itself — git_step (the git-command-with-
#     step-name-on-failure helper defined inline in release.sh after strict
#     mode is enabled). git_step only needs to exist by the time
#     cm_release_squash_to_main is CALLED, not at source time, since bash
#     resolves function names at call time.
#
# The caller must have `set -euo pipefail` enabled — this file relies on
# strict-mode error propagation exactly as the inline code it replaces did.

# Guard against double-sourcing.
if declare -F cm_release_squash_to_main >/dev/null 2>&1; then
    return 0
fi

# -----------------------------------------------------------------------
# cm_release_squash_to_main — entry point (see header doc above).
# -----------------------------------------------------------------------
cm_release_squash_to_main() {
  # The remote whose tracking ref Step 5 pulls from: project.cfg [project]
  # git_remote_name, origin when absent or blank (pp_git_remote_name).
  local _squash_remote
  if ! _squash_remote="$(pp_git_remote_name "$PROJECT_NAME")"; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): a git_remote_name that
    # cannot name a remote would fast-forward main from a ref nobody
    # configured; pp_git_remote_name has printed the dual-channel block.
    # CENSUS-ANCHOR: release-squash-git-remote-name-01
    exit 1
  fi

  # --- Step 5: git checkout main (or prefixed equivalent) && git pull --ff-only ---
  # Idempotency: on a re-run, local main may already be AHEAD of <remote>/main (push_to_remote=false,
  # or a partial re-run where push succeeded on the prior run).  git merge --ff-only fails in that
  # case ("not possible to fast-forward").  Guard: skip the ff-only pull when local main is already
  # ahead of or equal to <remote>/main, logging the idempotent step.
  echo "[Step 5] Checking out $MAIN_BRANCH and pulling..."
  git_step "git checkout $MAIN_BRANCH" git -C "$REPO_ROOT" checkout "$MAIN_BRANCH"

  _main_local_sha="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH" 2>/dev/null)" || _main_local_sha=""
  _main_origin_sha="$(git -C "$REPO_ROOT" rev-parse "${_squash_remote}/$MAIN_BRANCH" 2>/dev/null)" || _main_origin_sha=""
  if [[ -z "$_main_origin_sha" ]]; then
    # <remote>/main doesn't exist yet (local-only install) — skip the ff pull.
    echo "[Step 5] ${_squash_remote}/$MAIN_BRANCH not found — skipping ff-only pull (local-only or push_to_remote=false install)."
  elif [[ "$_main_local_sha" == "$_main_origin_sha" ]]; then
    echo "[Step 5] $MAIN_BRANCH already up-to-date with ${_squash_remote}/$MAIN_BRANCH — skipping ff-only pull."
  else
    # Check whether local is already ahead of the remote (merge-base == remote SHA).
    _main_merge_base="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "${_squash_remote}/$MAIN_BRANCH" 2>/dev/null)" || _main_merge_base=""
    if [[ -n "$_main_merge_base" && "$_main_merge_base" == "$_main_origin_sha" ]]; then
      echo "[Step 5] step 5: already complete, continuing — $MAIN_BRANCH is already ahead of ${_squash_remote}/$MAIN_BRANCH (re-run after partial release)."
    else
      git_step "git pull --ff-only $MAIN_BRANCH" git -C "$REPO_ROOT" merge --ff-only "${_squash_remote}/$MAIN_BRANCH"
    fi
  fi

  # --- Step 6: git merge --squash rc/<ACTIVE_RC> ---
  # Idempotency: if main already contains all of the RC's content (SHA-equality or
  # an empty index after squash), treat the squash as already complete.
  echo "[Step 6] Squash-merging $RC_BRANCH into $MAIN_BRANCH..."
  MAIN_SHA="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH")"
  RC_SHA="$(git -C "$REPO_ROOT" rev-parse "$RC_BRANCH")"

  if [[ "$MAIN_SHA" == "$RC_SHA" ]]; then
    echo "[Step 6] step 6: already complete, continuing — $MAIN_BRANCH is already at $RC_BRANCH tip ($(echo "$MAIN_SHA" | cut -c1-7)). Skipping squash."
    echo "[Step 7] step 7: already complete, continuing — no commit needed ($MAIN_BRANCH already contains RC content)."
  else
    _squash_main_rc=0
    git -C "$REPO_ROOT" merge --squash "$RC_BRANCH" || _squash_main_rc=$?
    if [[ $_squash_main_rc -ne 0 ]]; then
      # Attempt UD/DU auto-resolve before halting.
      _autoresolve_main_rc=0
      _cm_autoresolve_ud_conflicts "$REPO_ROOT" "${RC_BRANCH} into ${MAIN_BRANCH}" "main" || _autoresolve_main_rc=$?
      if [[ $_autoresolve_main_rc -ne 0 ]]; then
        cm_halt \
          "cm-release.trigger-5.squash-conflict" \
          "Trigger 5: git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} produced conflicts (rc=${_squash_main_rc}) that the UD/DU auto-resolver could not clear — git state may be damaged." \
          "Inspect the conflicted paths with 'git status' in ${REPO_ROOT}, resolve them by hand (or run 'git merge --abort' to back out), then re-run scripts/cm/release.sh."
      fi
    fi

    # --- Step 7: git commit ---
    # Idempotency: an empty index after the squash merge means the RC's content was
    # already present on main (e.g., from a prior run that committed the squash before
    # dying, or an operator manual commit). Detect via exit code, not git prose.
    echo "[Step 7] Committing squash on $MAIN_BRANCH..."
    if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
      echo "[Step 7] step 7: already complete, continuing — index is empty after squash (main already contained release content from prior run or manual operator commit)."
    else
      _commit_main_out=""
      _commit_main_rc=0
      _commit_main_out="$(git -C "$REPO_ROOT" commit -m "Release $ACTIVE_RC" 2>&1)" || _commit_main_rc=$?
      if [[ $_commit_main_rc -ne 0 ]]; then
        echo "" >&2
        echo "ERROR: git commit (main squash) failed (rc=${_commit_main_rc})" >&2
        echo "$_commit_main_out" >&2
        echo "The release-state.md has NOT been modified." >&2
        echo "Recover manually by checking the git state and re-running or reverting as needed." >&2
        # BLOCK-WORTHY: a failed git commit mid-squash is stopped rather
        # than papered over -- release-state.md staying unmodified here
        # keeps it in agreement with the git state.
        # CENSUS-ANCHOR: release-squash-commit-failure-01
        exit 1
      else
        echo "$_commit_main_out"
      fi
    fi
  fi

  # --- Step 7a: Fidelity gate ---
  # After the squash commit on main and BEFORE release-notes and tag:
  # assert that rc/<ACTIVE_RC> and $MAIN_BRANCH are byte-identical.
  # At this moment no release-notes have landed, so no diff exclusions are needed.
  # On divergence: print git diff --stat, write HALT, exit 1.
  # A clean gate prints a single OK line and continues.
  #
  # When divergence is detected it means an unexpected commit landed on main
  # after the RC branched from it (e.g. an operator commit mid-RC).  The squash
  # absorbed the RC's changes but the foreign commit is now present on main and
  # not in the RC — a difference the tag would silently lock in.
  #
  # Idempotency (resume): if release-notes for this RC are already committed to
  # main HEAD, the fidelity gate already passed on the prior run; skip it here.
  # Main legitimately contains more commits than the RC branch at this point
  # (the release artifacts from the prior run), so a diff would falsely fire.
  _fidelity_resume=0
  _fidelity_rn_ls="$(git -C "$REPO_ROOT" ls-tree --name-only HEAD "release-notes/${ACTIVE_RC}.md" 2>/dev/null)" || true
  [[ -n "$_fidelity_rn_ls" ]] && _fidelity_resume=1
  if [[ $_fidelity_resume -eq 1 ]]; then
    echo "[Step 7a] Fidelity gate: skip — release-notes/${ACTIVE_RC}.md already committed to $MAIN_BRANCH HEAD (resume path; gate passed on prior run)."
  else
    echo "[Step 7a] Fidelity gate: verifying rc/$ACTIVE_RC tree matches $MAIN_BRANCH after squash..."
    if git -C "$REPO_ROOT" diff --quiet "$RC_BRANCH" "$MAIN_BRANCH" 2>/dev/null; then
      echo "[Step 7a] Fidelity gate: OK — rc/$ACTIVE_RC and $MAIN_BRANCH are identical."
    else
      echo "" >&2
      echo "[Step 7a] Fidelity gate: DIVERGENCE DETECTED — rc/$ACTIVE_RC and $MAIN_BRANCH differ:" >&2
      git -C "$REPO_ROOT" diff --stat "$RC_BRANCH" "$MAIN_BRANCH" >&2 || true
      cm_halt \
        "cm-release.trigger-11.fidelity-gate-divergence" \
        "Trigger 11: the post-squash fidelity gate found ${MAIN_BRANCH} diverging from rc/${ACTIVE_RC} after the squash — a commit landed on ${MAIN_BRANCH} mid-RC." \
        "Inspect the divergent paths in the 'git diff --stat' output printed above, decide whether to fold the foreign commit into the next RC or revert it, then re-run scripts/cm/release.sh."
    fi
  fi
}
