#!/usr/bin/env bash
# team/scripts/lib/cm/release_finalize.sh
#
# CM release finalize stage: the token-usage aggregator, git tag creation,
# RC branch/worktree teardown (origin and local), the project-scoped
# release-state.md field update, bundled-item promotion, per-RC shipped-state
# JSON, best-effort auto-push of main and the release tag, metrics aggregation, RC
# temp cleanup, the ## Fixed In bug stamp, and the post-release archive of
# the project's finished items. Sourced by
# team/scripts/cm/release.sh; extracted from that script's former inline
# Steps 12b through 16b (plus the closing summary block) so release.sh's
# own body stays a thin driver over sourced lib stages. Behavior-preserving:
# no stdout/stderr line, exit code, HALT-file write, release-state.md
# content, or metrics CSV row changes as a result of this file existing —
# it is one stage of a driver split for focused, testable units.
#
# Public entry point
# -------------------
#   cm_release_finalize <pre_tag_hook_step_fn> <post_tag_hook_step_fn>
#       Runs Steps 12b through 16c, plus the closing "Local
#       release preparation complete" summary, in this order:
#       12b token aggregator; the pre-tag hook (via callback #1); 13/13a tag
#       creation (remote tag check push_to_remote-gated, pre-tag VERSION
#       gate just before the cut) and its "not pushed yet" log line; the post-tag hook (via
#       callback #2, logged-warning-only); the "verification complete"
#       narration (sets _CM_VERIFICATION_COMPLETE=1); 9 "main not pushed"
#       log line; 10 delete RC branch on the remote (push_to_remote-gated);
#       10.5 force-remove any worktree still pinned to RC_BRANCH; 11 delete
#       the local RC branch; 12 update release-state.md fields (a failed
#       write HALTs with the write as its remedy) plus its auto-resolve
#       audit trail; 13c promote bundled items to done (a failure warns); 13d
#       per-RC shipped-state JSON; 14 best-effort auto-push; 15 metrics
#       aggregation + history.csv append; 16 RC temp cleanup (worktree
#       prune only); 16b stamp ## Fixed In on the bugs this release
#       closed (those 'running' in a snapshot taken just before 13c and
#       'done' after it; earlier done bugs are never stamped), then name
#       each done bug still lacking ## Fixed In on its own [Step 16b]
#       line (read-only, nothing when there are none; counts, exit code
#       and the summary line are unaffected); 16c archive this project's
#       finished items (quality class: never blocks or delays the release;
#       runs only once the RC branch is gone and release-state.md reads
#       Active RC none and the new Last Released, and a skipped or failed
#       run is narrated and retried after the next release); then the
#       closing summary, whose Fixed In line reports Step 16b's actual outcome and whose
#       tag, RC-branch and auto-push lines report the outcomes Steps 10,
#       11 and 14 recorded (a push_to_remote=false release says it is
#       local only and claims no push to or deletion from origin), and
#       `exit 0`. Every remote step and hint names pp_git_remote_name's
#       answer, refused first when invalid. Must be called directly (never via
#       command substitution) — the pre-tag VERSION gate, cm_halt, and the
#       final line all call `exit` directly.
#
# Why two callbacks (pre_tag_hook_step_fn, post_tag_hook_step_fn)
# ------------------------------------------------------------------
# The pre-tag and post-tag hook blocks fall inside this stage's step range,
# but each is one of the three `cm_resolve_and_enforce_hook` call sites
# team/tests/unit/test_ship_rc_hooks.py counts directly in release.sh's own
# file text (exactly three, non-comment: pre-squash, pre-tag, post-tag).
# Moving either call site's text here would drop that count below three and
# fail the pre-existing structural test — the same reasoning that keeps the
# pre-squash hook step inline in release.sh (see release_ship_policy.sh's
# "Why a callback" for the precedent). Both hook steps stay defined in
# release.sh as small named functions (`_cm_run_pre_tag_hook_step`,
# `_cm_run_post_tag_hook_step`) and are threaded through here by name so
# cm_release_finalize invokes each at exactly the right point, preserving
# the original step order and stdout without duplicating either hook body.
#
# Other functions in this file (_run_release_hook,
# _cm_remove_stale_worktrees_for_branch) are internal helpers used by
# cm_release_finalize and by the hook-step callbacks defined in release.sh.
# Step 16b's two helpers (_cm_release_snapshot_running_bugs,
# _cm_release_stamp_fixed_in) are in the sibling release_fixed_in.sh, and
# Step 16c's (_cm_release_archive_finished_items) in release_archive.sh;
# this file sources both itself.
# The closing summary's origin/branch lines come from
# _cm_release_print_origin_summary_lines in release_origin_summary.sh.
# _run_release_hook lives here (rather than staying inline in release.sh)
# because every one of its call sites is a hook-step callback; like
# git_step (see release_squash.sh's header), it only needs to exist by call
# time, not by release.sh's own source time.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's entry point is called)
# -----------------------------------------------------------------------
#   team/scripts/lib/cm/release_preflight.sh — cm_halt, and
#     cm_release_preflight must already have run so PROJECT_NAME,
#     REPO_ROOT, MAIN_BRANCH, RC_BRANCH, ACTIVE_RC, RELEASE_STATE,
#     PROJECT_HOOKS_DIR, _CM_PROJECT_ROOT, _CM_PUSH_TO_REMOTE,
#     _CM_AUTORESOLVE_LOG are set.
#   team/scripts/lib/cm/release_notes.sh — _CLEAN_RELEASE_VERSION (read by
#     the pre-tag VERSION gate below) must already be set.
#   team/scripts/lib/cm_release_hooks.sh — cm_resolve_and_enforce_hook
#     (called by the caller-supplied hook-step callbacks, not directly here).
#   team/scripts/lib/pp_run_ops.sh / lib/project_paths.sh — pp_run_ops
#     (Step 16b's writeback, Step 16c's archive), pp_pythonpath (Step 13c's
#     bundled-item promotion), pp_prefix_tag, pp_strip_prefix_from_tag,
#     pp_project_root.
#   team/scripts/lib/error_contract.sh — pgai_emit_error_contract (the
#     blocking-hook failure refusal in _run_release_hook).
#   team/scripts/lib/cm/release_ship_posture.sh —
#     cm_release_append_auto_ship_events (Step 12 post-write: appends each
#     Auto-Ship Event the posture recorded; prints nothing when none was).
#   team/scripts/lib/cm/release_campaign_spend.sh —
#     cm_release_spend_campaign_file (Step 12).
#   team/scripts/lib/cm/release_halt_after_spend.sh —
#     cm_release_spend_halt_after_sentinel (Step 12c).
#   team/scripts/lib/cm/release_origin_summary.sh —
#     _cm_release_print_origin_summary_lines (the closing summary).
#   team/scripts/cm/release.sh itself — git_step and the two hook-step
#     callback functions named above; all three only need to exist by call
#     time, since bash resolves function names at call time, not source time.
#
# The caller must also have `set -euo pipefail` enabled — this file relies
# on strict-mode error propagation exactly as the inline code it replaces did.

# Guard against double-sourcing.
if declare -F cm_release_finalize >/dev/null 2>&1; then
    return 0
fi

# Step 16b's helpers live in their own sibling stage, sourced here so a
# caller that sources only this file still resolves them. It carries its own
# guard.
# shellcheck source=release_fixed_in.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_fixed_in.sh"
# Step 16c's helper, likewise (release_archive.sh).
# shellcheck source=release_archive.sh
source "$(dirname "${BASH_SOURCE[0]}")/release_archive.sh"

# Helper: run a lifecycle hook for this release.
# Usage: _run_release_hook <hook-name> <hook-path> [--no-block]
#
# hook-name  : identifier used in log prefixes (e.g. "cm-release-pre-squash")
# hook-path  : absolute path to the hook script
# --no-block : if set, hook failure is a logged warning; release continues
#
# Hook runs with cwd=$REPO_ROOT and six required PGAI_* env vars plus the
# ambient environment. stdout/stderr are captured and prefixed with
# "[hook <name>]". Return: 0 = success or benign skip; non-zero = blocking
# failure (when --no-block is absent).
_run_release_hook() {
  local hook_name="$1"
  local hook_path="$2"
  local no_block="${3:-}"

  if [[ ! -f "$hook_path" ]]; then
    echo "[cm-release] hook ${hook_name}: not present, skipping"
    return 0
  fi
  if [[ ! -x "$hook_path" ]]; then
    echo "[cm-release] WARNING: hook ${hook_name} exists but is not executable, skipping" >&2
    return 0
  fi

  echo "[cm-release] running hook ${hook_name}..."
  local _hook_rc=0
  (
    cd "$REPO_ROOT"
    PGAI_TARGET_VERSION="$ACTIVE_RC" \
    PGAI_PROJECT_NAME="$PROJECT_NAME" \
    PGAI_PROJECT_ROOT="$_CM_PROJECT_ROOT" \
    PGAI_DEV_TREE_PATH="$REPO_ROOT" \
    PGAI_RC_BRANCH="$RC_BRANCH" \
    PGAI_KANBAN_ROOT="$KANBAN_ROOT" \
    "$hook_path" 2>&1 | sed "s/^/[hook ${hook_name}] /"
    exit "${PIPESTATUS[0]}"
  ) || _hook_rc=$?

  if [[ $_hook_rc -ne 0 ]]; then
    if [[ "$no_block" == "--no-block" ]]; then
      echo "[cm-release] WARNING: hook ${hook_name} failed (rc=$_hook_rc), continuing per --no-block" >&2
      return 0
    fi
    # Non-deterministic (exempt): the hook is project-supplied, so its failure
    # cause lives inside the hook's own logic, not in anything release.sh can
    # inspect. Per docs/coding-standards.md "The non-deterministic exemption",
    # this failure is exempt from a numbered fix-command block but is NEVER
    # exempt from naming the next diagnostic step — the "[hook <name>]"
    # lines already printed above are that step.
    echo "[cm-release] ERROR: ${hook_name}: FAIL — hook exited non-zero (rc=$_hook_rc)" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Cause(s):" >&2
    echo "[cm-release] ERROR:   - ${hook_path} — project-supplied hook returned rc=$_hook_rc" >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Next diagnostic step: read the \"[hook ${hook_name}]\"-prefixed lines printed above for this hook's own stdout/stderr — that output names the specific failure inside the hook. This gate cannot prescribe a fix command because the hook's internal logic is project-supplied; its failure cause is not knowable from release.sh." >&2
    echo "[cm-release] ERROR:" >&2
    echo "[cm-release] ERROR: Rule: a blocking hook (${hook_name}) must exit 0 for the release to proceed past this phase." >&2
    # Dual-channel error contract (team/scripts/lib/error_contract.sh), in
    # addition to the Cause(s)/Next-diagnostic-step/Rule block above: the
    # error key below is the hook's own phase name, the documented id an
    # operator or tool would grep for.
    pgai_emit_error_contract \
      "cm-release.hook-failure.${hook_name}" \
      "The ${hook_name} release hook exited non-zero (rc=${_hook_rc}); a blocking release hook must exit 0 for the release to proceed past this phase." \
      "Read the \"[hook ${hook_name}]\"-prefixed lines printed above for the hook's own stdout/stderr, fix the underlying cause inside ${hook_path}, then re-run scripts/cm/release.sh."
    # CENSUS-ANCHOR: release-finalize-hook-failure-01
    exit 1
  fi
  echo "[cm-release] hook ${hook_name}: completed successfully"
  return 0
}

# Helper: force-remove any linked worktree still checked out on the given
# branch, so a subsequent `git branch -D <branch>` does not fail with
# "cannot delete branch ... used by worktree at ...".
# Usage: _cm_remove_stale_worktrees_for_branch <repo_root> <branch_name>
#
# Per-task worktree teardown is supposed to remove a CODER task's linked
# worktree when the task completes, but is not guaranteed to run to
# completion for every task; a leftover worktree pinned to the RC branch
# would otherwise fail Step 11's `git branch -D` AFTER all the real release
# work has already succeeded. `git worktree prune` (Step 16a) does not help
# here — it only clears refs for worktrees already deleted from disk; a
# live worktree directory needs an actual `git worktree remove`.
#
# Defensive and idempotent: a silent no-op when no worktree is pinned to
# <branch_name> (the common case). Returns 0 on success (including the
# no-op case); returns 1 if a pinning worktree was found but could not be
# force-removed, so the caller can HALT with an actionable message before
# attempting the branch deletion.
_cm_remove_stale_worktrees_for_branch() {
  local repo_root="$1"
  local branch_name="$2"
  local target_ref="refs/heads/${branch_name}"
  local wt_path=""
  local wt_branch=""
  local removed_count=0

  while IFS= read -r _line; do
    case "$_line" in
      "worktree "*)
        wt_path="${_line#worktree }"
        wt_branch=""
        ;;
      "branch "*)
        wt_branch="${_line#branch }"
        ;;
      "")
        # Blank line marks the end of one worktree's porcelain record.
        if [[ -n "$wt_path" && "$wt_branch" == "$target_ref" ]]; then
          if [[ "$wt_path" == "$repo_root" ]]; then
            # The main worktree itself (REPO_ROOT) is never removable via
            # `git worktree remove` and should already be on $MAIN_BRANCH by
            # this point in the script (Step 5 checked it out) — skip rather
            # than attempt a removal that would fail on the main worktree.
            echo "[cm-release] NOTE: main worktree ($wt_path) reports branch $branch_name — skipping (git worktree remove does not apply to the main worktree)." >&2
          else
            echo "[cm-release] Stale worktree detected: $wt_path is still checked out on $branch_name — force-removing before branch deletion so the release can finish."
            local _wt_rm_out _wt_rm_rc=0
            _wt_rm_out="$(git -C "$repo_root" worktree remove --force "$wt_path" 2>&1)" || _wt_rm_rc=$?
            if [[ -n "$_wt_rm_out" ]]; then
              echo "$_wt_rm_out" | sed 's/^/[worktree remove] /'
            fi
            if [[ $_wt_rm_rc -eq 0 ]]; then
              echo "[cm-release] Removed stale worktree: $wt_path"
              removed_count=$((removed_count + 1))
            else
              echo "ERROR: failed to force-remove stale worktree at $wt_path (still pinning $branch_name, rc=$_wt_rm_rc)." >&2
              return 1
            fi
          fi
        fi
        wt_path=""
        wt_branch=""
        ;;
    esac
  done < <(git -C "$repo_root" worktree list --porcelain; echo "")

  if [[ $removed_count -gt 0 ]]; then
    echo "[cm-release] Removed ${removed_count} stale worktree(s) pinned to $branch_name."
  fi
  return 0
}

# -----------------------------------------------------------------------
# cm_release_finalize <pre_tag_hook_step_fn> <post_tag_hook_step_fn>
# entry point (see header doc above).
# -----------------------------------------------------------------------
cm_release_finalize() {
  local pre_tag_hook_step_fn="$1"
  local post_tag_hook_step_fn="$2"

  # Origin/branch outcomes, recorded by the step that performs each action
  # and read by the closing summary so it reports what actually ran:
  #   _CM_RC_ORIGIN_DELETE_OUTCOME  Step 10: deleted | already-absent | skipped
  #   _CM_RC_LOCAL_DELETE_OUTCOME   Step 11: deleted | already-absent
  #   _CM_PUSH_MAIN_OUTCOME         Step 14: PUSHED | UP-TO-DATE | FAILED | skipped
  #   _CM_PUSH_TAGS_OUTCOME         Step 14: PUSHED | UP-TO-DATE | FAILED | skipped
  # "not-run" is the value until the owning step records its outcome.
  _CM_RC_ORIGIN_DELETE_OUTCOME="not-run"
  _CM_RC_LOCAL_DELETE_OUTCOME="not-run"
  _CM_PUSH_MAIN_OUTCOME="not-run"
  _CM_PUSH_TAGS_OUTCOME="not-run"

  # BLOCK-WORTHY (docs/blocking-site-census.md): an invalid git_remote_name would misdirect every remote step.
  # CENSUS-ANCHOR: release-finalize-git-remote-name-01
  local _fin_remote; _fin_remote="$(pp_git_remote_name "$PROJECT_NAME")" || exit 1

  # --- Step 12b: Run token usage aggregator (per-RC roll-up) ---
  # Runs AFTER the squash-to-main commits (Steps 7-8) and BEFORE the tag (Step 12).
  # Produces projects/<name>/usage/rc/<ACTIVE_RC>-tokens.json from every task that
  # contributed to this RC (identified by ## Release Version in task README.md).
  #
  # The aggregator is NON-BLOCKING: if it exits non-zero, the failure is logged
  # and the release continues to the tag step.  Per the autonomy principle:
  # ship the release, do not stop on a metrics-collection failure.
  echo "[Step 12b] Running token usage aggregator for $ACTIVE_RC..."
  _agg_script="$KANBAN_ROOT/pm-agent/aggregate_tokens.py"
  if [[ -f "$_agg_script" ]]; then
    (
      set +e
      python3 "$_agg_script" \
        --project "$PROJECT_NAME" \
        --rc "$ACTIVE_RC" \
        --kanban-root "$KANBAN_ROOT" \
        2>&1 | sed 's/^/[aggregate_tokens] /'
      _agg_rc=$?
      if [[ $_agg_rc -ne 0 ]]; then
        echo "[aggregate_tokens] WARNING: aggregator exited with code $_agg_rc — token roll-up may be incomplete; release continues." >&2
      fi
    ) || true
  else
    echo "[Step 12b] WARNING: aggregate_tokens.py not found at $_agg_script — skipping token roll-up." >&2
  fi

  "$pre_tag_hook_step_fn"

  # --- Step 13: git tag <RELEASE_TAG> ---
  # Tag is created HERE, after the squash commit and release-notes commit on main, so that
  # `git describe --tags` on main returns the clean tag (e.g. v0.17.1) with
  # no trailing commit offset.
  # RELEASE_TAG is the prefixed tag name (e.g. ai_v0.31.0 in hybrid mode,
  # or v0.31.0 in pure-AI mode where prefix is empty).
  RELEASE_TAG="$(pp_prefix_tag "$PROJECT_NAME" "$ACTIVE_RC")"
  echo "[Step 13] Tagging $RELEASE_TAG on the final commit on main..."

  # Idempotency: check local tag first.
  #   - Local tag exists AND points at HEAD: already done, continue (log audit line).
  #   - Local tag exists AND points at a DIFFERENT commit: HALT (genuine divergence, Trigger 5b).
  #   - Local tag absent: proceed to the remote check, then create.
  _local_tag_sha="$(git -C "$REPO_ROOT" rev-parse "${RELEASE_TAG}^{}" 2>/dev/null)" || _local_tag_sha=""
  if [[ -n "$_local_tag_sha" ]]; then
    _current_head_sha="$(git -C "$REPO_ROOT" rev-parse "$MAIN_BRANCH")"
    if [[ "$_local_tag_sha" == "$_current_head_sha" ]]; then
      echo "[Step 13] step 13: already complete, continuing — local tag $RELEASE_TAG already exists and points at the expected commit ($(echo "$_local_tag_sha" | cut -c1-7))."
    else
      cm_halt \
        "cm-release.trigger-5b.tag-points-at-wrong-commit" \
        "Trigger 5b: local tag ${RELEASE_TAG} exists but points at $(echo "$_local_tag_sha" | cut -c1-7) instead of the expected $(echo "$_current_head_sha" | cut -c1-7) (${MAIN_BRANCH} HEAD), indicating a diverged re-run." \
        "Inspect the local tag with 'git show ${RELEASE_TAG}' in ${REPO_ROOT}; delete it with 'git tag -d ${RELEASE_TAG}' if it is stale, then re-run scripts/cm/release.sh."
    fi
  else
    # Local tag absent: check the remote (Trigger 7) before creating; local-only runs never ask it.
    if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]] && git -C "$REPO_ROOT" ls-remote --exit-code --tags "$_fin_remote" "refs/tags/${RELEASE_TAG}" >/dev/null 2>&1; then
      cm_halt \
        "cm-release.trigger-7.tag-exists-on-origin" \
        "Trigger 7: tag ${RELEASE_TAG} already exists on ${_fin_remote} — creating it again would be a duplicate, indicating a race condition or a repeated invocation against an already-shipped version." \
        "Run 'git ls-remote --tags ${_fin_remote} ${RELEASE_TAG}' to confirm, verify whether ${ACTIVE_RC} already shipped, then either cancel this RC (scripts/cm/cancel-rc.sh) or investigate the race."
    fi

    # --- Pre-tag VERSION gate ---
    # Assert that VERSION on disk equals the tag about to be cut.  This is the
    # single gate that prevents a committed VERSION from lying about the release.
    # Fires AFTER VERSION is written (Step 11d) and BEFORE git tag — on mismatch
    # the release exits non-zero without creating the tag and without pushing anything.
    _pretag_version_on_disk=""
    if [[ -f "$REPO_ROOT/VERSION" ]]; then
      _pretag_version_on_disk="$(cat "$REPO_ROOT/VERSION" 2>/dev/null || true)"
    fi
    # Strip trailing newline from on-disk read for comparison (printf '%s\n' wrote one).
    _pretag_version_trimmed="${_pretag_version_on_disk%$'\n'}"
    # _CLEAN_RELEASE_VERSION was set in Step 11d; fall back to computing it here for
    # re-entry paths where Step 11d was skipped (VERSION already correct).
    _pretag_expected="${_CLEAN_RELEASE_VERSION:-$(pp_strip_prefix_from_tag "$PROJECT_NAME" "$ACTIVE_RC" 2>/dev/null || echo "$ACTIVE_RC")}"
    if [[ "$_pretag_version_trimmed" != "$_pretag_expected" ]]; then
      echo "" >&2
      echo "[Pre-tag VERSION gate] FAIL — on-disk VERSION does not match the tag about to be created. No tag has been created." >&2
      echo "" >&2
      echo "Cause(s):" >&2
      echo "  - ${REPO_ROOT}/VERSION contains '${_pretag_version_trimmed}', expected '${_pretag_expected}' (release tag ${RELEASE_TAG})" >&2
      echo "" >&2
      echo "To fix:" >&2
      echo "  1. printf '%s\n' '${_pretag_expected}' > '${REPO_ROOT}/VERSION'" >&2
      echo "  2. git -C '${REPO_ROOT}' add VERSION && git -C '${REPO_ROOT}' commit -m 'Fix VERSION for ${_pretag_expected}'" >&2
      echo "  3. Re-run scripts/cm/release.sh." >&2
      echo "" >&2
      echo "Rule: the committed VERSION file must equal the release tag being cut, or the shipped tag would misreport the version." >&2
      # CENSUS-ANCHOR: release-finalize-pretag-version-mismatch-01
      exit 1
    fi
    echo "[Step 13] Pre-tag VERSION gate: OK — VERSION='${_pretag_version_trimmed}' matches tag '${_pretag_expected}'."

    _tag_rc=0
    git -C "$REPO_ROOT" tag "$RELEASE_TAG" || _tag_rc=$?
    if [[ $_tag_rc -ne 0 ]]; then
      echo "" >&2
      echo "ERROR: git tag ${RELEASE_TAG} failed (rc=${_tag_rc})" >&2
      echo "The release-state.md has NOT been modified." >&2
      # CENSUS-ANCHOR: release-finalize-git-tag-failure-01
      exit 1
    fi
  fi

  # --- Step 13a: tag is NOT pushed here ---
  # The tag was created locally in Step 13. The operator pushes it via
  # cm/finalize-release.sh (or manually: git push <remote> <VERSION>).
  echo "[Step 13a] Tag $RELEASE_TAG created locally — NOT pushing to ${_fin_remote} (operator will push via scripts/cm/finalize-release.sh)."

  "$post_tag_hook_step_fn"

  # --- Verification complete: proceeding to finalization ---
  # Every veto-capable gate has now run; nothing below is a gate to rerun.
  # The steps below are the only destructive ones (RC branch deletion,
  # release-state.md rewrite) — see cm_release_cleanup_on_exit's rollback
  # guard for what happens when an earlier step fails instead.
  echo ""
  echo "[cm-release] verification complete — proceeding to finalization"
  echo ""
  _CM_VERIFICATION_COMPLETE=1

  # --- Step 9: main is NOT pushed here ---
  # Release commits on main stay local; operator pushes via cm/finalize-release.sh.
  echo "[Step 9] main is staged locally — NOT pushing to ${_fin_remote} (operator will push via scripts/cm/finalize-release.sh)."

  # --- Step 10: git push <remote> --delete rc/<ACTIVE_RC> ---
  # Gated by push_to_remote; idempotent when the RC branch is already absent on the remote.
  echo "[Step 10] Deleting $RC_BRANCH on ${_fin_remote}..."
  if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
    if ! git -C "$REPO_ROOT" ls-remote --exit-code --heads "$_fin_remote" "$RC_BRANCH" >/dev/null 2>&1; then
      echo "[Step 10] step 10: already complete, continuing — $RC_BRANCH is already absent on ${_fin_remote} (deleted in prior run or never pushed)."
      _CM_RC_ORIGIN_DELETE_OUTCOME="already-absent"
    else
      git_step "git push ${_fin_remote} --delete $RC_BRANCH" git -C "$REPO_ROOT" push "$_fin_remote" --delete "$RC_BRANCH"
      _CM_RC_ORIGIN_DELETE_OUTCOME="deleted"
    fi
  else
    echo "[push_to_remote=false] skipping ${_fin_remote} push for ${PROJECT_NAME}: git push ${_fin_remote} --delete $RC_BRANCH (Step 10)"
    _CM_RC_ORIGIN_DELETE_OUTCOME="skipped"
  fi

  # --- Step 10.5: defensively remove any worktree still pinned to $RC_BRANCH ---
  # Runs before Step 11's branch deletion (via _cm_remove_stale_worktrees_for_branch,
  # defined above); idempotent no-op in the common case where nothing is pinned.
  echo "[Step 10.5] Checking for stale worktrees still pinned to $RC_BRANCH..."
  _cm_remove_stale_worktrees_for_branch "$REPO_ROOT" "$RC_BRANCH" || cm_halt \
    "cm-release.stale-worktree-blocks-branch-delete" \
    "A worktree is still checked out on ${RC_BRANCH} and could not be force-removed; Step 11 (git branch -D ${RC_BRANCH}) would fail with 'used by worktree at ...'. The worktree path is named in the [cm-release] log line above." \
    "Inspect the worktree path named above, remove it manually with 'git -C ${REPO_ROOT} worktree remove --force <path>' (or delete the directory on disk and run 'git -C ${REPO_ROOT} worktree prune'), then re-run scripts/cm/release.sh."

  # --- Step 11: git branch -D rc/<ACTIVE_RC> ---
  # Idempotent: an already-absent local RC branch (prior-run deletion) is treated as success.
  echo "[Step 11] Deleting local branch $RC_BRANCH..."
  if ! git -C "$REPO_ROOT" rev-parse --verify "refs/heads/$RC_BRANCH" >/dev/null 2>&1; then
    echo "[Step 11] step 11: already complete, continuing — local branch $RC_BRANCH is already absent (deleted in prior run)."
    _CM_RC_LOCAL_DELETE_OUTCOME="already-absent"
  else
    git_step "git branch -D $RC_BRANCH" git -C "$REPO_ROOT" branch -D "$RC_BRANCH"
    _CM_RC_LOCAL_DELETE_OUTCOME="deleted"
  fi

  # --- Step 12: Update project-scoped release-state.md fields in place ---
  # The shared field writer (write_rc_state.py release-state) changes only the
  # four schema fields and keeps every appended event block byte-for-byte,
  # writing atomically. Last Released is set to ACTIVE_RC so drain.py's
  # rc:vX.Y.Z branch can read it with semver >=.
  # The tag is cut and the RC branch is gone, so a failed write cannot be
  # retried by re-running release.sh: it stops the project with a HALT
  # whose remedy is the exact write, never a silent set -e exit.
  echo "[Step 12] Updating project-scoped release-state.md..."
  _cm_release_state_cmd="python3 $KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py release-state $RELEASE_STATE --active-rc none --rc-opened-at none --rc-opened-by-task none --last-released $ACTIVE_RC"
  _cm_release_state_rc=0
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" release-state "$RELEASE_STATE" \
    --active-rc none --rc-opened-at none --rc-opened-by-task none \
    --last-released "$ACTIVE_RC" || _cm_release_state_rc=$?
  if [[ $_cm_release_state_rc -ne 0 ]]; then
    # BLOCK-WORTHY: release-state.md still names the shipped RC as active, which wedges the lane.
    # CENSUS-ANCHOR: release-finalize-release-state-write-failure-01
    cm_halt "cm-release.release-state-write-failed" \
      "Step 12: ${ACTIVE_RC} is tagged (${RELEASE_TAG}) and its RC branch deleted, but writing release-state.md failed (rc=${_cm_release_state_rc}); it still names ${ACTIVE_RC} as the active RC. Steps 13c-16c (bundle promotion, shipped-state JSON, Fixed In stamp, archive) did not run." \
      "${_cm_release_state_cmd} && team/scripts/unhalt.sh --project ${PROJECT_NAME}"
  fi

  echo "  release-state.md updated: Active RC -> none, Last Released -> ${ACTIVE_RC}"
  echo "  Path: $RELEASE_STATE"

  # --- Step 12 post-write: Append auto-resolve audit trail (if any) ---
  # Appended after Step 12's field update, only when Step 6 auto-resolved UD/DU conflicts.
  if [[ -n "${_CM_AUTORESOLVE_LOG:-}" ]]; then
    _ar_timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    cat >> "$RELEASE_STATE" <<EOF

## Auto-Resolve Event
Timestamp: ${_ar_timestamp}
CM Task: scripts/cm/release.sh (automated)
Action: Auto-resolved modify/delete (UD/DU) conflicts by taking main's side.
Resolved Paths:
${_CM_AUTORESOLVE_LOG}
EOF
    echo "[cm-release] Auto-resolve audit trail appended to release-state.md."
  fi

  # Step 12 post-write: append the Operator Override event (--force only),
  # then any Auto-Ship Event (ship_posture=always; release_ship_posture.sh).
  [[ -n "${_CM_OPERATOR_OVERRIDE_LOG:-}" ]] && printf '%s\n' "$_CM_OPERATOR_OVERRIDE_LOG" >> "$RELEASE_STATE"
  cm_release_append_auto_ship_events
  cm_release_spend_campaign_file "$PROJECT_NAME" "$ACTIVE_RC" "$_CM_PROJECT_ROOT" "$RELEASE_STATE"
  cm_release_spend_halt_after_sentinel "$_CM_PROJECT_ROOT" "$KANBAN_ROOT"  # Step 12c; rationale in release_halt_after_spend.sh's header
  # --- Step 13c: Promote bundled items from running -> done ---
  # Success-path only (runs after tag creation). Locates the requirements
  # bundle file that produced this RC, promotes its own ## Status to done,
  # and — when a ## Bundled Items section exists — promotes each referenced
  # item too. This is the discovery pipeline's idempotency complement: once
  # the bundle file is 'done', discovery_step_requirements skips it forever.
  echo "[Step 13c] Promoting bundled items and bundle file from 'running' to 'done'..."

  # Locate the project root for the active project (where requirements/ lives).
  _cm_project_name="$PROJECT_NAME"
  _cm_project_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_cm_project_name" 2>/dev/null)" || _cm_project_root=""
  _cm_requirements_dir="${_cm_project_root}/requirements"
  # Bugs at 'running' now are this RC's; Step 16b stamps those 13c closes.
  _cm_release_bugs_dir="${_cm_project_root:-${_CM_PROJECT_ROOT}}/bugs"
  _cm_release_snapshot_running_bugs "$_cm_release_bugs_dir"

  _cm_bundle_file=""
  if [[ -n "$_cm_project_root" && -d "$_cm_requirements_dir" ]]; then
    # Matches auto-generated bundle files and operator-authored requirements
    # files alike (both share the "${ACTIVE_RC}-*.md" naming convention).
    while IFS= read -r -d '' _f; do
      _cm_bundle_file="$_f"
      break
    done < <(find "$_cm_requirements_dir" -maxdepth 1 -type f \
              -name "${ACTIVE_RC}-*.md" -print0 2>/dev/null | sort -z)
  fi

  if [[ -z "$_cm_bundle_file" ]]; then
    echo "  WARNING: no requirements file found for $ACTIVE_RC in $_cm_requirements_dir" >&2
    echo "  The source requirements file's ## Status will remain at 'running'." >&2
    echo "  Run scripts/migrate/bug-status-done.sh to recover, or promote ## Status manually." >&2
  else
    echo "  Requirements file: $_cm_bundle_file"
    # Promotes the file's own ## Status (always) and, only when a
    # ## Bundled Items section exists, each referenced item's ## Status too.
    # Lives in promote_bundled_items.py so the regression suite can import it.
    #
    # This stage file sits two levels below the scripts root in both the dev
    # tree (team/scripts/lib/cm/) and the installed layout (scripts/lib/cm/);
    # promote_bundled_items.py sits in cm/ at that same root. Anchoring to
    # this file's OWN location (never whatever sourced it) keeps the lookup
    # correct in both layouts.
    _cm_scripts_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
    _cm_promote_script="${_cm_scripts_root}/cm/promote_bundled_items.py"
    # Both failure branches below are warnings (exit 0), deliberately: the tag
    # is cut, the RC branch torn down and release-state.md rewritten, all
    # irreversible, and Steps 13d-16c still need to run. Step 13c only updates
    # bookkeeping metadata; a missing script or a failed run is surfaced here
    # with the command that re-runs it, never by aborting the shipped release.
    if [[ ! -f "$_cm_promote_script" ]]; then
      echo "  ERROR: promote_bundled_items.py not found at $_cm_promote_script" >&2
      echo "  Step 13c cannot promote bundled items. Check your installation." >&2
      echo "  WARNING: bundled item ## Status fields NOT promoted. Run promote_bundled_items.py manually." >&2
    else
      # tasks_dir/bugs_dir let the derivation path promote shipped bugs not
      # enumerated in ## Bundled Items; absent dirs disable that path (WARN only).
      # pp_pythonpath puts this tree's package root on PYTHONPATH (task_status import).
      _cm_tasks_dir="${_cm_project_root}/tasks"
      _cm_bugs_dir="${_cm_project_root}/bugs"
      _cm_promote_rc=0
      PYTHONPATH="$(pp_pythonpath)" python3 "$_cm_promote_script" \
        "$_cm_bundle_file" "$ACTIVE_RC" "$_cm_tasks_dir" "$_cm_bugs_dir" || _cm_promote_rc=$?
      if [[ $_cm_promote_rc -ne 0 ]]; then
        echo "  WARNING: promote_bundled_items.py exited ${_cm_promote_rc}; bundled item ## Status fields may NOT be promoted. The release continues. Re-run: PYTHONPATH=$(pp_pythonpath) python3 ${_cm_promote_script} ${_cm_bundle_file} ${ACTIVE_RC} ${_cm_tasks_dir} ${_cm_bugs_dir}" >&2
      fi
    fi
  fi

  # --- Step 13d: Record shipped state in per-RC release-state JSON ---
  # Fires unconditionally once the local tag exists (the canonical shipped
  # signal), so closed_at is populated regardless of push_to_remote mode.
  # write_ship() is idempotent, so Step 14's second write (after a successful
  # origin push) is harmless. Non-blocking: failures are logged and continue.
  echo "[Step 13d] Recording shipped state in per-RC release-state JSON (closed_at + outcome=shipped)..."
  _rc_state_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
  _rc_state_json="${_rc_state_dir}/${ACTIVE_RC}.json"
  _closed_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  mkdir -p "$_rc_state_dir" 2>/dev/null || true
  python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship \
      "$_rc_state_json" "$ACTIVE_RC" "$_closed_at_utc" 2>&1 || \
    echo "[cm-release] WARNING: could not update per-RC release-state JSON at $_rc_state_json" >&2

  # --- Step 14: Best-effort auto-push of main and the release tag to the remote ---
  # All critical release work is already complete above this line. The tag
  # push names the release tag's own ref (refs/tags/$RELEASE_TAG), never
  # every local tag: in a repository shared with another lane, that lane's
  # unpublished tags stay local. Both pushes are best-effort — the script always exits 0 after a successful
  # local release regardless of push outcome. Gated by push_to_remote; the
  # whole block is skipped when it is false. The block runs as a brace group
  # with errexit suspended (not a subshell) so the two push outcomes it
  # records are still set when the closing summary reads them.
  echo "[Step 14] Attempting best-effort auto-push of main and tags to ${_fin_remote}..."
  if [[ "$_CM_PUSH_TO_REMOTE" == "true" ]]; then
  _cm_errexit_was_on=0
  [[ $- == *e* ]] && _cm_errexit_was_on=1
  {
    set +e
    # Capture output so the push-result marker can detect UP-TO-DATE.
    _push_main_out=$(git -C "$REPO_ROOT" push "$_fin_remote" "$MAIN_BRANCH" 2>&1); _push_main_rc=$?
    printf '%s\n' "$_push_main_out" | sed 's/^/[cm-release auto-push] /'
    if [[ $_push_main_rc -ne 0 ]]; then
      echo "[cm-release auto-push] Push to ${_fin_remote} $MAIN_BRANCH failed (rc=$_push_main_rc). Retrying in 3 seconds..."
      sleep 3
      _push_main_out=$(git -C "$REPO_ROOT" push "$_fin_remote" "$MAIN_BRANCH" 2>&1); _push_main_rc=$?
      printf '%s\n' "$_push_main_out" | sed 's/^/[cm-release auto-push retry] /'
    fi
    # Emit single grep-able push-result marker for Step 14 main push.
    if [[ $_push_main_rc -ne 0 ]]; then
      _push_main_outcome=FAILED
    elif grep -q "Everything up-to-date" <<< "$_push_main_out"; then
      _push_main_outcome=UP-TO-DATE
    else
      _push_main_outcome=PUSHED
    fi
    echo "[cm-release push-result] target=$MAIN_BRANCH rc=${ACTIVE_RC} outcome=${_push_main_outcome} outcome_code=${_push_main_rc}"
    _CM_PUSH_MAIN_OUTCOME="$_push_main_outcome"

    # Push the release tag by its explicit ref — same output-capture pattern
    # and one-time retry.
    _push_tags_out=$(git -C "$REPO_ROOT" push "$_fin_remote" "refs/tags/${RELEASE_TAG}" 2>&1); _push_tags_rc=$?
    printf '%s\n' "$_push_tags_out" | sed 's/^/[cm-release auto-push] /'
    if [[ $_push_tags_rc -ne 0 ]]; then
      echo "[cm-release auto-push] Push of tag ${RELEASE_TAG} failed (rc=$_push_tags_rc). Retrying in 3 seconds..."
      sleep 3
      _push_tags_out=$(git -C "$REPO_ROOT" push "$_fin_remote" "refs/tags/${RELEASE_TAG}" 2>&1); _push_tags_rc=$?
      printf '%s\n' "$_push_tags_out" | sed 's/^/[cm-release auto-push retry] /'
    fi
    # Emit single grep-able push-result marker for Step 14 tags push.
    if [[ $_push_tags_rc -ne 0 ]]; then
      _push_tags_outcome=FAILED
    elif grep -q "Everything up-to-date" <<< "$_push_tags_out"; then
      _push_tags_outcome=UP-TO-DATE
    else
      _push_tags_outcome=PUSHED
    fi
    echo "[cm-release push-result] target=tags rc=${ACTIVE_RC} outcome=${_push_tags_outcome} outcome_code=${_push_tags_rc}"
    _CM_PUSH_TAGS_OUTCOME="$_push_tags_outcome"

    if [[ $_push_main_rc -ne 0 || $_push_tags_rc -ne 0 ]]; then
      echo "[cm-release auto-push] WARNING: one or more push attempts failed after retry (main rc=$_push_main_rc, tags rc=$_push_tags_rc)."
      echo "[cm-release auto-push] The release is complete locally. The watchdog should catch the remaining work."
    else
      echo "[cm-release auto-push] main and tags pushed successfully."

      # Write/update per-RC release-state JSON with the shipped outcome —
      # only after the release tag push succeeds. Non-blocking on failure.
      _rc_state_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}/release-state"
      _rc_state_json="${_rc_state_dir}/${ACTIVE_RC}.json"
      _closed_at_utc="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      mkdir -p "$_rc_state_dir" 2>/dev/null || true
      python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" ship \
          "$_rc_state_json" "$ACTIVE_RC" "$_closed_at_utc" 2>&1 || \
        echo "[cm-release] WARNING: could not update per-RC release-state JSON at $_rc_state_json" >&2
    fi
  }
  if [[ $_cm_errexit_was_on -eq 1 ]]; then set -e; fi
  else
    echo "[push_to_remote=false] skipping ${_fin_remote} push for ${PROJECT_NAME}: auto-push of $MAIN_BRANCH and tags (Step 14)"
    _CM_PUSH_MAIN_OUTCOME="skipped"
    _CM_PUSH_TAGS_OUTCOME="skipped"
  fi

  # --- Step 15: Metrics aggregation and CSV append ---
  # 15a writes the per-RC JSON rollup (metrics/rc/<ACTIVE_RC>.json); 15b
  # appends one row to the cumulative metrics/history.csv. Both are
  # NON-BLOCKING (failures log a [metrics] WARNING and the script still
  # exits 0). Idempotent: aggregate_rc() recomputes deterministically;
  # append_rc_row() dedupes under an exclusive flock. --project/--rc are
  # always passed explicitly — neither script infers the project from cwd.
  echo "[Step 15] Running metrics aggregation for $ACTIVE_RC (non-blocking)..."

  _metrics_agg_script="$KANBAN_ROOT/scripts/lib/metrics_aggregator.py"
  _metrics_csv_script="$KANBAN_ROOT/scripts/lib/metrics_csv_writer.py"
  _metrics_rc_json=""  # set if 15a succeeds; used by 15b

  # 15a: per-RC JSON rollup
  if [[ -f "$_metrics_agg_script" ]]; then
    _metrics_agg_rc=0
    (
      set +e
      python3 "$_metrics_agg_script" \
        --project "$PROJECT_NAME" \
        --rc      "$ACTIVE_RC" \
        --kanban-root "$KANBAN_ROOT" \
        2>&1 | sed 's/^/[metrics] /'
      exit "${PIPESTATUS[0]}"
    ) || _metrics_agg_rc=$?
    if [[ $_metrics_agg_rc -ne 0 ]]; then
      echo "[metrics] WARNING: metrics_aggregator.py exited with code $_metrics_agg_rc — per-RC JSON rollup may be incomplete; release continues." >&2
    else
      # Derive the rollup path from the known output convention so 15b can read it.
      _cm_proj_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_proj_dir=""
      if [[ -n "$_cm_proj_dir" ]]; then
        _metrics_rc_json="${_cm_proj_dir}/metrics/rc/${ACTIVE_RC}.json"
        if [[ ! -f "$_metrics_rc_json" ]]; then
          echo "[metrics] WARNING: expected rollup file not found after aggregation: $_metrics_rc_json" >&2
          _metrics_rc_json=""
        fi
      fi
    fi
  else
    echo "[Step 15a] WARNING: metrics_aggregator.py not found at $_metrics_agg_script — skipping per-RC JSON rollup." >&2
  fi

  # 15b: append row to cumulative history.csv
  if [[ -n "$_metrics_rc_json" && -f "$_metrics_csv_script" ]]; then
    _cm_proj_dir_csv="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)" || _cm_proj_dir_csv=""
    if [[ -n "$_cm_proj_dir_csv" ]]; then
      _history_csv="${_cm_proj_dir_csv}/metrics/history.csv"
      _metrics_csv_rc=0
      (
        set +e
        python3 "$_metrics_csv_script" \
          --csv-path    "$_history_csv" \
          --rollup-json "$_metrics_rc_json" \
          2>&1 | sed 's/^/[metrics] /'
        exit "${PIPESTATUS[0]}"
      ) || _metrics_csv_rc=$?
      if [[ $_metrics_csv_rc -ne 0 ]]; then
        echo "[metrics] WARNING: metrics_csv_writer.py exited with code $_metrics_csv_rc — history.csv row may not have been appended; release continues." >&2
      fi
    else
      echo "[Step 15b] WARNING: could not resolve project directory for '$PROJECT_NAME' — skipping history.csv append." >&2
    fi
  elif [[ -z "$_metrics_rc_json" ]]; then
    echo "[Step 15b] WARNING: per-RC JSON rollup was not written — skipping history.csv append." >&2
  else
    echo "[Step 15b] WARNING: metrics_csv_writer.py not found at $_metrics_csv_script — skipping history.csv append." >&2
  fi

  # --- Step 16: Success-gated RC temp cleanup (non-blocking) ---
  # Runs only on the explicit success path (must never live in the EXIT
  # trap). 16a is a repo-scoped 'git worktree prune' only — the old
  # filesystem straggler-sweep was removed because a shared temp worktrees/
  # namespace let it delete another project's in-flight worktree; stale
  # on-disk dirs from abnormally-terminated tasks are now an operator
  # cleanup via pgai_temp_cleanup, scoped to this project's subtree only.
  echo "[Step 16] Success-gated RC temp cleanup (non-blocking)..."
  (
    set +e
    # 16a: prune stale worktree refs from the git registry (repo-scoped; safe).
    echo "[Step 16a] Running git worktree prune on $REPO_ROOT..."
    git -C "$REPO_ROOT" worktree prune 2>&1 | sed 's/^/[worktree prune] /' || \
      echo "[Step 16a] WARNING: git worktree prune exited non-zero — continuing." >&2
    echo "[Step 16a] git worktree prune complete (no FS sweep — safe by construction)."
  ) || true

  # --- Step 16b: Stamp ## Fixed In: <ACTIVE_RC> on the bugs this release closed ---
  # Only bugs that were 'running' before Step 13c and are 'done' now; an
  # already-set ## Fixed In is never overwritten. Then names (read-only)
  # every done bug still lacking ## Fixed In. Non-blocking.
  echo "[Step 16b] Stamping ## Fixed In: ${ACTIVE_RC} on bugs closed in this release..."
  _cm_release_stamp_fixed_in "$_cm_release_bugs_dir" "$ACTIVE_RC"

  # --- Step 16c: Archive this project's finished items (quality class) ---
  # Last, after the RC is closed (Steps 11-12). Never fails or delays the
  # release; a skipped or failed run is narrated and retried after the next
  # release. See release_archive.sh.
  echo "[Step 16c] Archiving finished items for ${PROJECT_NAME} (quality class; never blocks the release)..."
  _cm_release_archive_finished_items

  echo ""
  echo "Local release preparation complete."
  echo "  Version:     $ACTIVE_RC"
  echo "  State file:  $RELEASE_STATE"
  echo ""
  echo "Summary of what was done:"
  echo "  - $RC_BRANCH squashed to $MAIN_BRANCH (single-lane squash commit)"
  echo "  - Post-squash fidelity gate passed: $RC_BRANCH and $MAIN_BRANCH trees are identical"
  _cm_release_print_origin_summary_lines tag-and-branch "$_fin_remote"
  echo "  - release-state.md updated: Active RC -> none"
  echo "  - release-notes/${ACTIVE_RC}.md generated and committed on $MAIN_BRANCH"
  echo "  - bundled items promoted from 'running' to 'done' (Step 13c)"
  echo "  - ${_CM_FIXED_IN_OUTCOME}"
  echo "  - metrics aggregation + history.csv append attempted (Step 15, non-blocking)"
  _cm_release_print_origin_summary_lines auto-push "$_fin_remote"
  echo "  - RC temp cleanup attempted (Step 16, non-blocking — git worktree prune; no FS sweep by design)"
  echo ""
  echo "## Next Recommended Step"
  if [[ "$_CM_PUSH_TO_REMOTE" != "true" ]]; then
    echo "This release is local only (push_to_remote=false): no push or branch deletion was run against ${_fin_remote}."
  elif [[ "$_CM_PUSH_MAIN_OUTCOME" != "FAILED" && "$_CM_PUSH_TAGS_OUTCOME" != "FAILED" ]]; then
    echo "The auto-push succeeded; the release is complete on ${_fin_remote}."
  else
    echo "The auto-push failed; push manually from a shell where the PreToolUse hook does not apply:"
    echo "    git push ${_fin_remote} $MAIN_BRANCH"
    echo "    git push ${_fin_remote} ${RELEASE_TAG}"
    echo "Or run the convenience script:"
    echo "    $([[ "$_fin_remote" == origin ]] || echo "GIT_REMOTE_NAME=${_fin_remote} ")bash ${KANBAN_ROOT}/scripts/cm/finalize-release.sh"
  fi
  echo "The release is complete locally."
  exit 0
}
