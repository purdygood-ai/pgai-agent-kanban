#!/usr/bin/env bash
# team/scripts/lib/cm/release_resolve.sh
#
# CM release stage: what cm/release.sh does instead of halting when a
# mechanical step fails without damage. Sourced by release_squash.sh and
# release_finalize.sh (below their own double-sourcing guards), so a caller
# that sources either one resolves the function it needs.
#
# Squash conflict (trigger 5) -- cm_release_resolve_squash_conflict
#   The squash into main left unmerged paths the UD/DU auto-resolver could
#   not clear. The squash is backed out (main returns to MAIN_SHA; nothing
#   was committed), then pgai_agent_kanban.cm.release_resolve squash-conflict
#   asks the resolution matrix whether the RC may be cancelled and rebundled
#   -- the resolution stage's CANCEL-AND-REBUNDLE drill -- and writes one
#   operator inbox entry naming every conflicting path. Cancelled: no HALT,
#   the release exits 5. Not cancelled (an approval gate, the retry ceiling,
#   a failed cancel, a module that would not run): the trigger 5 HALT, whose
#   remedy is the cancel. A squash that failed with no unmerged path is not
#   a conflict this stage can name; it HALTs on trigger 5 as before.
#
# Push failure (trigger 6) -- cm_release_record_push_lag
#   Called once after Step 14 with the remote. When any push the release
#   made to the remote failed (Step 10's RC-branch delete, Step 14's main or
#   tag push), it narrates one dual-channel notice and appends one
#   "## Push Lag Event" to release-state.md naming what did not reach the
#   remote. The release still exits 0: the commits and the tag stay ahead of
#   the remote in git, which is where the push-lag check reads them
#   (scripts/cm/push-watchdog.sh, check-push-lag.sh), and that check pushes
#   main and the tag once the remote accepts. An RC branch left on the
#   remote is named in the event ("<branch> (still on <remote>; deletion
#   failed)"), and push-watchdog.sh deletes it from there once the remote
#   accepts and main is on it.
#
# Tag already on the remote (trigger 7) -- cm_release_resolve_tag_on_remote
#   Step 13 found the release tag on the remote before cutting it. One
#   operator inbox entry names the tag (pgai_agent_kanban.cm.release_resolve
#   tag-on-remote), and the release exits 6 without a HALT and without
#   cutting the tag; the EXIT trap (cm_release_cleanup_on_exit) returns main
#   to its pre-release commit, so no branch, tag or release-state.md changes.
#
# Globals read: PROJECT_NAME, ACTIVE_RC, KANBAN_ROOT, REPO_ROOT,
# MAIN_BRANCH, RC_BRANCH, MAIN_SHA, RC_SHA, RELEASE_TAG, RELEASE_STATE,
# _CM_RC_ORIGIN_DELETE_OUTCOME, _CM_PUSH_MAIN_OUTCOME, _CM_PUSH_TAGS_OUTCOME.
# Written: MAIN_SHA is cleared after a cancelled RC (main is where the cancel
# left it; the EXIT trap has nothing to roll back).
# Dependencies (sourced before these run): cm_halt (release_preflight.sh),
# pp_run_ops (pp_run_ops.sh), pgai_emit_error_contract (error_contract.sh).
# No inline Python: every Python call is a module.

# Guard against double-sourcing.
if declare -F cm_release_resolve_squash_conflict >/dev/null 2>&1; then
    return 0
fi

# Helper: the value of one key=value line in a module's output.
# Usage: _cm_release_resolve_field <key> <output>
_cm_release_resolve_field() {
  local key="$1" line
  while IFS= read -r line; do
    if [[ "$line" == "${key}="* ]]; then
      printf '%s\n' "${line#"${key}"=}"
      return 0
    fi
  done <<< "$2"
  return 0
}

# cm_release_resolve_squash_conflict <squash_rc>
# Never returns: exits 5 after a cancelled RC, otherwise HALTs (cm_halt).
cm_release_resolve_squash_conflict() {
  local squash_rc="$1"
  local -a paths=()
  mapfile -t paths < <(git -C "$REPO_ROOT" diff --name-only --diff-filter=U 2>/dev/null)
  if [[ ${#paths[@]} -eq 0 ]]; then
    cm_halt \
      "cm-release.trigger-5.squash-conflict" \
      "Trigger 5: git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} failed (rc=${squash_rc}) without leaving an unmerged path the UD/DU auto-resolver could name — git state may be damaged." \
      "git -C $(printf '%q' "$REPO_ROOT") status"
  fi
  echo "[Step 6] Squash conflict on ${#paths[@]} path(s): ${paths[*]}"
  echo "[Step 6] Backing the squash out: ${MAIN_BRANCH} returns to $(echo "$MAIN_SHA" | cut -c1-7); nothing was committed."
  if ! git -C "$REPO_ROOT" reset -q --hard "$MAIN_SHA"; then
    cm_halt \
      "cm-release.trigger-5.squash-conflict" \
      "Trigger 5: git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} conflicted on ${paths[*]}, and backing the squash out (git reset --hard ${MAIN_SHA}) failed — git state may be damaged." \
      "git -C $(printf '%q' "$REPO_ROOT") status"
  fi

  local -a args=(squash-conflict --kanban-root "$KANBAN_ROOT" --project "$PROJECT_NAME" --version "$ACTIVE_RC"
                 --repo-root "$REPO_ROOT" --rc-branch "$RC_BRANCH" --main-branch "$MAIN_BRANCH"
                 --main-sha "$MAIN_SHA" --rc-tip "$RC_SHA")
  local p
  for p in "${paths[@]}"; do
    args+=(--path "$p")
  done
  local out rc=0
  out="$(pp_run_ops pgai_agent_kanban.cm.release_resolve "${args[@]}")" || rc=$?
  local outcome reason entry_id ledger
  outcome="$(_cm_release_resolve_field outcome "$out")"
  reason="$(_cm_release_resolve_field reason "$out")"
  entry_id="$(_cm_release_resolve_field entry_id "$out")"
  ledger="$(_cm_release_resolve_field ledger_entry "$out")"
  while IFS= read -r p; do
    [[ "$p" == action_error=* ]] && echo "[cm-release] WARNING: cancel-and-rebundle step reported: ${p#action_error=}" >&2
  done <<< "$out"

  if [[ $rc -eq 0 && "$outcome" == "cancelled" ]]; then
    MAIN_SHA=""
    # AGENT-RESOLVABLE: nothing shipped, and no stop was written -- the RC
    # was cancelled and its cargo returned for rebundling; the next RC
    # carries it from the current main.
    # CENSUS-ANCHOR: release-resolve-squash-conflict-01
    pgai_emit_error_contract "cm-release.squash-conflict.rebundled" \
      "${ACTIVE_RC} was not shipped: the squash of ${RC_BRANCH} into ${MAIN_BRANCH} conflicted on ${#paths[@]} path(s) (${paths[*]}), so the RC was cancelled and its cargo returned for rebundling (ledger entry ${ledger:-none}; operator inbox entry ${entry_id:-none}). No HALT was written; exit 5 means the RC is gone and its release task closed WONT-DO with it." \
      "$(printf '%q' "${KANBAN_ROOT}/scripts/inbox.sh") --project $(printf '%q' "$PROJECT_NAME") list" \
      "$PROJECT_NAME"
    exit 5
  fi

  [[ $rc -ne 0 ]] && reason="the cancel-and-rebundle module exited ${rc}; see the error block above"
  cm_halt \
    "cm-release.trigger-5.squash-conflict" \
    "Trigger 5: git merge --squash ${RC_BRANCH} into ${MAIN_BRANCH} conflicted on ${#paths[@]} path(s) (${paths[*]}); the squash was backed out (${MAIN_BRANCH} is at its pre-release commit) and the RC was not cancelled automatically: ${reason:-no reason reported}. Operator inbox entry: ${entry_id:-none}." \
    "$(printf '%q' "${KANBAN_ROOT}/scripts/cm/cancel-rc.sh") --project $(printf '%q' "$PROJECT_NAME") --key $(printf '%q' "$ACTIVE_RC") --yes"
}

# cm_release_resolve_tag_on_remote <remote>
# Never returns: exits 6 after writing the inbox entry.
cm_release_resolve_tag_on_remote() {
  local remote="$1"
  local out rc=0 entry_id
  out="$(pp_run_ops pgai_agent_kanban.cm.release_resolve tag-on-remote --kanban-root "$KANBAN_ROOT" \
           --project "$PROJECT_NAME" --version "$ACTIVE_RC" --repo-root "$REPO_ROOT" \
           --tag "$RELEASE_TAG" --remote "$remote")" || rc=$?
  entry_id="$(_cm_release_resolve_field entry_id "$out")"
  [[ $rc -ne 0 || -z "$entry_id" ]] && entry_id="not written (see the error block above)"
  # BLOCK-WORTHY: cutting a tag the remote already publishes would put a
  # second release under one version; the release stops before the tag, but
  # writes no HALT -- the question goes to the operator inbox and nothing
  # changes locally.
  # CENSUS-ANCHOR: release-resolve-tag-on-remote-01
  pgai_emit_error_contract "cm-release.trigger-7.tag-exists-on-remote" \
    "Trigger 7: tag ${RELEASE_TAG} already exists on ${remote}, so ${ACTIVE_RC} was not tagged or shipped again. No HALT was written and nothing changes locally (${MAIN_BRANCH} returns to its pre-release commit; the RC branch and release-state.md are untouched). Operator inbox entry: ${entry_id}." \
    "git -C $(printf '%q' "$REPO_ROOT") ls-remote --tags $(printf '%q' "$remote") $(printf '%q' "refs/tags/${RELEASE_TAG}")" \
    "$PROJECT_NAME"
  exit 6
}

# cm_release_record_push_lag <remote>
# Returns 0 always: a push the remote refused never fails the release.
cm_release_record_push_lag() {
  local remote="$1"
  local -a lagging=()
  [[ "${_CM_PUSH_MAIN_OUTCOME:-}" == "FAILED" ]] && lagging+=("${MAIN_BRANCH} (push failed)")
  [[ "${_CM_PUSH_TAGS_OUTCOME:-}" == "FAILED" ]] && lagging+=("refs/tags/${RELEASE_TAG} (push failed)")
  [[ "${_CM_RC_ORIGIN_DELETE_OUTCOME:-}" == "FAILED" ]] && lagging+=("${RC_BRANCH} (still on ${remote}; deletion failed)")
  [[ ${#lagging[@]} -eq 0 ]] && return 0
  local watchdog
  watchdog="$(printf '%q' "${KANBAN_ROOT}/scripts/cm/push-watchdog.sh") --project $(printf '%q' "$PROJECT_NAME")"
  # CONVERT: a push the remote refused leaves push lag for the push-lag
  # check to retry; the release is complete locally and never stops on it.
  # CENSUS-ANCHOR: release-resolve-push-lag-01
  pgai_emit_error_contract "cm-release.trigger-6.push-lag" \
    "${ACTIVE_RC} shipped locally (tag ${RELEASE_TAG}), but ${remote} refused: $(IFS=';'; echo "${lagging[*]}"). No HALT was written: the push-lag check pushes ${MAIN_BRANCH} and the tag once ${remote} accepts." \
    "$watchdog" \
    "$PROJECT_NAME"
  if [[ -f "$RELEASE_STATE" ]]; then
    {
      printf '\n## Push Lag Event\n'
      printf 'Timestamp: %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
      printf 'Version: %s\n' "$ACTIVE_RC"
      printf 'Remote: %s\n' "$remote"
      printf 'Not On Remote:\n'
      printf '  - %s\n' "${lagging[@]}"
      printf 'Retry: %s\n' "$watchdog"
    } >> "$RELEASE_STATE"
    echo "[cm-release] Push Lag Event appended to release-state.md."
  fi
  return 0
}
