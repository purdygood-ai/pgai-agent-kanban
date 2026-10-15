#!/usr/bin/env bash
# team/scripts/lib/wake_driver_finalize.sh
# Task-finalization stage of the shared wake pipeline: the provider
# process-group drain, per-task timeout kill detection, PGAI_DEV_TREE_PATH restoration after TESTER, the post-task
# pollution sweep, final-state resolution (transient-error retry, the
# CODER/WRITER completion gate), the backlog-marker case (and the record
# that this chain blocked a task, for the on-BLOCK nudge run_project_chain
# fires after releasing its repo lock), the post-session litter check, the training-corpus copy, the
# TESTER finalize=report intake-closure hook, PM auto-materialization, and
# worktree teardown. Extracted from team/scripts/wake/claude.sh's
# process_one_task so the shared pipeline in wake_driver.sh stays under the
# 800-line-per-file ceiling.
# Behavior-preserving move: no log line, task-state write, backlog marker,
# or teardown-timing change as a result of this file existing.
#
# Public entry point
# -------------------
#   resolve_dominant_output_token_model <tokens_json_path>
#       Prints the model name from <tokens_json_path>'s per-model usage
#       breakdown that produced the greatest output_tokens total, or prints
#       nothing and returns 1 when that cannot be determined (file missing,
#       empty, unparseable, or its model_usage object absent/null/empty).
#       See its own header below for the full contract.
#
#   narrate_model_mismatch <task_status> <tokens_json_path> [<task_id>]
#       Compares the model stamped into <task_status>'s ## Model section
#       against resolve_dominant_output_token_model's answer for
#       <tokens_json_path>, and logs one warning naming both on
#       disagreement. Narration only — never mutates a file, never affects
#       the wake's exit status. See its own header below for the full
#       contract.
#
#   requeue_after_dispatch_refusal <task_id> <task_status> <log_file>
#       Final-state resolution for a task the provider CLI refused to run
#       before any work began: returns it to BACKLOG with its durable
#       ## Zero Progress record untouched and echoes "BACKLOG". See its own
#       header below.
#
#   wd_pollution_new_lines <pre_snapshot> <post_snapshot>
#       The porcelain lines <post_snapshot> adds over <pre_snapshot> (whole lines).
#
#   wd_drain_provider_process_group
#       Holds until the provider's process group has no live member,
#       signalling survivors itself when the watchdog never did.
#
#   wd_finalize_task
#       Runs every post-invocation step of process_one_task, computing and
#       recording the task's final_state, and sets LAST_TASK_FINAL_STATE
#       (and LAST_TASK_DISPATCH_REFUSED — true when the provider CLI
#       refused this task's dispatch before any work began, so
#       run_project_chain stops dispatching for the wake) before returning. Always returns 0 — every original exit path past
#       this point in the pre-decomposition monolith fell through to a
#       final `return 0`, so this function has no early-return signal for
#       the caller to check; process_one_task simply calls it and returns 0
#       itself. One exception: if the
#       queue-write mutating-phase-boundary reverification (immediately
#       before the mark_backlog case statement) finds this chain no longer
#       holds the project repo lock, this function returns early (still 0)
#       having performed no further mutation and leaving LAST_TASK_FINAL_STATE
#       unset ("") — see that reverification's own inline comment.
#
# Contract: this function is called from within process_one_task's own
# stack frame (never in a subshell) and relies on bash's dynamic scoping to
# read process_one_task's own `local` variables directly, exactly as the
# pre-decomposition monolith did when this code was inline. It reads:
#   task_id task_dir task_readme task_status task_artifact_dir SUBAGENT
#   agent_exit _task_killed _provider_pgid _provider_deadline_epoch
#   _provider_group_sigkilled _task_worktree_path _task_feature_branch
#   _task_source_branch _canonical_dev_tree_path _park_resting_branch
#   _sweep_canonical_tree _pollution_pre_snapshot _pollution_snapshot_ok
#   _debug_gate task_debug_log task_start_ts _litter_snapshot_file
#   _CURRENT_PROJECT TASKS_ROOT KANBAN_ROOT AGENT SCRIPT_DIR
#   _project_lock_file (run_project_chain's own local — the queue-write
#   reverification's lock path)
# and writes (in addition to LAST_TASK_FINAL_STATE, below; it also clears CLEANUP_ON_EXIT_PROCESS_GROUP):
#   _project_lock_lost (run_project_chain's own local — set true on a
#   queue-write reverification loss, read by that function's own
#   end-of-chain cleanup to abandon rather than release the lock)
# LAST_TASK_FINAL_STATE is the shared cross-task-result global this function
# sets before returning.
#
# Dependencies: handle_working_after_exit / coder_writer_completion_gate
# (wake_lifecycle.sh), normalize_status_file / mark_backlog / set_block / block_task_with_record /
# block_task_with_unreadable_state (wake_task_state.sh), transition_task_state (wake_task_transition.sh),
# provider_dispatch_refused (the provider interface — provider_claude.sh /
# provider_codex.sh), zero_progress_reset (overwatch_protocol.sh — resets
# the durable zero-progress count on any cycle where the agent moved the
# task's own state), wake_bracket_post_session (wake_bracket.sh),
# _resolve_training_gate (wake_bootstrap.sh), pp_project_root
# (project_paths.sh), close_intake_on_finalize_report (wake_lifecycle.sh),
# teardown_task_worktree (worktree.sh), pgai_temp_dir (temp.sh), task_finalize_record_write
# (wake_reconcile_sweeps.sh), pgai_lock_reverify_or_narrate (lock_provenance.sh),
# metrics_rollup_refresh_for_task (metrics_rollup_refresh.sh — recomputes
# the per-RC rollup when this task's tokens.json lands after that rollup
# was written), log() (wake_bootstrap.sh) — all sourced before this file's
# function is called. pp_run_ops.sh, atomic_write.sh, block_record.sh and
# wake_task_budget.sh (the budget-stop requeue) are self-sourced: the ## Model
# read and ## Summary append are pgai_agent_kanban.task_status calls (read --raw,
# render --append), not inline Python.

_WD_FINALIZE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
declare -F pp_run_ops >/dev/null || source "${_WD_FINALIZE_LIB_DIR}/pp_run_ops.sh"
source "${_WD_FINALIZE_LIB_DIR}/atomic_write.sh"
declare -F block_record_write >/dev/null || source "${_WD_FINALIZE_LIB_DIR}/block_record.sh"
declare -F wd_task_limit_was_budget_cap >/dev/null || source "${_WD_FINALIZE_LIB_DIR}/wake_task_budget.sh"

# _wd_append_summary_finding <task_status> <finding> — append <finding> to
# the ## Summary body after one blank line (task_status render --append);
# a status.md without ## Summary is left unchanged. Committed through
# pgai_atomic_write; non-zero, target untouched, when the rewrite fails.
_wd_append_summary_finding() {
  pgai_atomic_write "$1" pp_run_ops pgai_agent_kanban.task_status render "$1" \
    --section=Summary --body="$2" --append --skip-missing
}

# ---------------------------------------------------------------------------
# resolve_dominant_output_token_model <tokens_json_path>
#
# Reads <tokens_json_path> (a task's tokens.json, written by
# token_capture.sh's token_capture_merge_into_task_artifacts) and prints the
# name of the model whose model_usage entry recorded the greatest
# output_tokens total. Ranking is on output tokens alone, not
# token_capture.sh's own canonical_model_id: that field's selection prefers
# the requested/selected model as a tiebreak whenever it appears as a
# model_usage key at all, which is exactly the case a model substitution
# needs to be caught rather than papered over — the requested model can be
# the dispatcher-turn model producing a few thousand tokens of scaffolding
# while a different model does the actual work. Ties are broken by sorting
# model_usage keys before comparing, so the result is deterministic.
#
# Prints nothing and returns 1 (nothing observed / cannot determine) when:
#   - tokens_json_path is empty, missing, or an empty file
#   - the JSON fails to parse
#   - model_usage is absent, null, or an empty object — the legacy schema
#     (no per-model breakdown was ever captured) and the captured:false
#     failure record (no usage was captured at all) both write model_usage
#     this way, and both cases are "cannot determine", not "zero usage"
#   - every model_usage entry's output_tokens is zero or unparseable
#
# Returns 0 with the model name on stdout otherwise.
# ---------------------------------------------------------------------------
resolve_dominant_output_token_model() {
  local tokens_json_path="$1"

  if [[ -z "$tokens_json_path" || ! -f "$tokens_json_path" || ! -s "$tokens_json_path" ]]; then
    return 1
  fi

  local dominant_model
  dominant_model="$(python3 - "$tokens_json_path" <<'PY'
import json, sys

path = sys.argv[1]
try:
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
except (OSError, json.JSONDecodeError):
    sys.exit(1)

model_usage = data.get('model_usage')
if not isinstance(model_usage, dict) or not model_usage:
    sys.exit(1)

best_model = None
best_output = 0
for model_name in sorted(model_usage):
    entry = model_usage[model_name]
    if not isinstance(entry, dict):
        continue
    try:
        output_tokens = int(entry.get('output_tokens', 0))
    except (TypeError, ValueError):
        continue
    if output_tokens > best_output:
        best_output = output_tokens
        best_model = model_name

if best_model is None:
    sys.exit(1)

print(best_model)
PY
)" || return 1

  if [[ -z "$dominant_model" ]]; then
    return 1
  fi

  printf '%s\n' "$dominant_model"
  return 0
}

# ---------------------------------------------------------------------------
# narrate_model_mismatch <task_status> <tokens_json_path> [<task_id>]
#
# Self-detects a model substitution: compares the model
# stamp_model_field (wake_task_state.sh) wrote into <task_status>'s
# ## Model section — the model the wake resolved and asked the provider to
# run — against resolve_dominant_output_token_model's answer for
# <tokens_json_path> — the model that actually produced the dominant share
# of this task's output tokens. Logs exactly one warning naming both values
# on disagreement, via the shared log() channel (wake_bootstrap.sh) so the
# line lands in the same wake log an operator already reads.
#
# This is narration, never a gate: it does not write to <task_status>, does
# not change task state, and does not affect the caller's exit status —
# always returns 0. <task_id> is optional context for the log line only;
# omit it and the line names the task as "<unknown>".
#
# Silent no-op (no warning, no error) when:
#   - <task_status> is empty, missing, or has no non-empty ## Model value
#   - resolve_dominant_output_token_model cannot determine an observed
#     model (tokens.json missing, empty, unparseable, or model_usage
#     absent/null/empty — including a provider that reported no usage at
#     all, which is expected behavior, not a defect to flag)
#   - the stamped and observed models agree
# ---------------------------------------------------------------------------
narrate_model_mismatch() {
  local task_status="$1"
  local tokens_json_path="$2"
  local narrate_task_id="${3:-<unknown>}"

  if [[ -z "$task_status" || ! -f "$task_status" ]]; then
    return 0
  fi

  # task_status read --raw: the ## Model body, stripped; an absent heading
  # or unreadable file reads empty (silent no-op below).
  local stamped_model
  stamped_model="$(pp_run_ops pgai_agent_kanban.task_status read "$task_status" \
    --section=Model --raw 2>/dev/null)" || stamped_model=""

  if [[ -z "$stamped_model" ]]; then
    return 0
  fi

  local observed_model
  observed_model="$(resolve_dominant_output_token_model "$tokens_json_path")" || return 0

  if [[ -z "$observed_model" || "$observed_model" == "$stamped_model" ]]; then
    return 0
  fi

  log "WARNING: model mismatch for task ${narrate_task_id}: status.md ## Model stamped '${stamped_model}' but tokens.json shows '${observed_model}' produced the dominant share of output tokens"
  return 0
}

# ---------------------------------------------------------------------------
# requeue_after_dispatch_refusal <task_id> <task_status> <log_file>
#
# Final-state resolution for a task still WORKING after a dispatch the
# provider CLI refused before any work began (provider_dispatch_refused —
# provider_claude.sh's CLI-too-old-for-model classification today). The
# agent never ran, so the cycle is neither progress nor zero progress: the
# task returns to BACKLOG, the durable ## Zero Progress record is left
# exactly as it was (never incremented, never reset), and ## Needs Human
# and ## Blockers stay untouched — the condition is infrastructure, and a
# task is never marked stuck for it. The dual-channel refusal itself was
# already narrated into <log_file> at the dispatch site; this records the
# outcome on the task's own ## Summary and echoes the resulting state to
# stdout, mirroring handle_working_after_exit's contract so the caller
# treats both alike. A failed atomic transition is narrated and still
# echoes BACKLOG, exactly as zero_progress_advance_or_escalate does, so the
# marker write below the call keeps the queue consistent with the wake's
# intent and the next reconciliation pass heals any remainder.
# ---------------------------------------------------------------------------
requeue_after_dispatch_refusal() {
  local task_id="${1:-}"
  local task_status="${2:-}"
  local log_file="${3:-}"

  if transition_task_state "$task_id" "$task_status" "BACKLOG"; then
    set_block "$task_status" "Summary" "Returned to BACKLOG by the wake: the provider CLI refused this dispatch before any work began (infrastructure condition, not task progress — zero-progress count unchanged). See ${log_file}."
    log "task ${task_id}: provider CLI refused the dispatch before any work began — returned to BACKLOG without counting a zero-progress cycle"
    normalize_status_file "$task_status"
    printf 'BACKLOG\n'
    return 0
  fi
  log "task ${task_id}: WARNING atomic transition to BACKLOG failed after a refused dispatch; status.md/marker left as they were (no partial update)"
  printf 'BACKLOG\n'
  return 1
}

# wd_pollution_new_lines <pre> <post>: each non-empty porcelain line of <post> not a WHOLE
# line of <pre> (grep -xF: a substring match hid '?? a/b.md' behind '?? a/b.md.bak').
wd_pollution_new_lines() {
  local _line
  while IFS= read -r _line; do
    [[ -n "$_line" ]] && ! grep -qxF -- "$_line" <<< "$1" 2>/dev/null && printf '%s\n' "$_line"
  done <<< "$2"
  return 0
}

# _wd_provider_group_members <pgid>: prints the pids still in the group,
# comma-separated (empty when none) -- narration only, never a decision.
_wd_provider_group_members() {
  ps -eo pid=,pgid= 2>/dev/null | awk -v g="$1" '$2 == g { printf "%s%s", sep, $1; sep="," }'
}

# _wd_provider_group_wait <pgid> <seconds>: polls once a second; 0 once no
# process in the group remains, 1 when one still exists after <seconds>.
_wd_provider_group_wait() {
  local _pg="$1" _limit="$2" _waited=0
  while kill -0 -- "-${_pg}" 2>/dev/null; do
    [[ $_waited -lt $_limit ]] || return 1
    sleep 1
    (( _waited++ )) || true
  done
  return 0
}

# ---------------------------------------------------------------------------
# wd_drain_provider_process_group
#
# Holds finalization until the provider's process group (_provider_pgid: the
# wrapper subshell wd_invoke_task launched under job control, and with it the
# provider CLI and every tool it spawned) has no live member. Everything after
# this call writes task state, tears down the worktree, or precedes the
# repo-lock release; a provider still running past any of it can edit a
# torn-down worktree or write State: DONE under a [B] marker.
#
# Three shapes, each narrated once: the group is already empty (the ordinary
# case, and a timeout the provider answered by exiting on SIGTERM) -- one line,
# with the measured seconds from deadline to empty group when the watchdog
# fired; the watchdog already sent SIGKILL -- wait up to KILL_GRACE_SECONDS for
# the kernel to finish the teardown, sending nothing more; survivors in a group
# the watchdog never signalled (the provider exited and left a descendant) --
# SIGTERM the group, wait the grace, SIGKILL the group, wait once more. A member
# that outlives SIGKILL by a full grace window is in kernel teardown (D state,
# or a zombie its parent has not reaped) and runs no further user code, so
# finalization proceeds past it with an ERROR line naming the pids.
#
# Reads (dynamic scope): _provider_pgid _provider_deadline_epoch
# _provider_group_sigkilled _task_killed task_id KILL_GRACE_SECONDS and
# _provider_timeout_seconds (the armed limit; falls back to MAX_TASK_SECONDS).
# No-op when _provider_pgid is empty (armed limit 0: foreground, no group). Returns 0.
# ---------------------------------------------------------------------------
wd_drain_provider_process_group() {
  local _pg="${_provider_pgid:-}"
  [[ -n "$_pg" ]] || return 0
  local _grace="${KILL_GRACE_SECONDS:-30}"

  _wd_provider_group_gone_line() {
    if [[ "${_task_killed:-false}" == "true" && -n "${_provider_deadline_epoch:-}" ]]; then
      log "task ${task_id}: provider process group ${_pg} gone $(( $(date +%s) - _provider_deadline_epoch ))s after the ${_provider_timeout_seconds:-${MAX_TASK_SECONDS}}s deadline; finalizing"
    else
      log "task ${task_id}: provider process group ${_pg} gone; finalizing"
    fi
  }

  if [[ "${_provider_group_sigkilled:-false}" == "true" ]]; then
    if _wd_provider_group_wait "$_pg" "$_grace"; then _wd_provider_group_gone_line; return 0; fi
  elif ! kill -0 -- "-${_pg}" 2>/dev/null; then
    _wd_provider_group_gone_line
    return 0
  else
    log "task ${task_id}: provider process group ${_pg} still alive after the invocation returned (pids $(_wd_provider_group_members "$_pg")); sending SIGTERM to the group and waiting up to ${_grace}s before finalizing"
    kill -TERM -- "-${_pg}" 2>/dev/null || true
    if _wd_provider_group_wait "$_pg" "$_grace"; then _wd_provider_group_gone_line; return 0; fi
    log "task ${task_id}: grace period (${_grace}s) expired; provider process group ${_pg} still alive (pids $(_wd_provider_group_members "$_pg")); sending SIGKILL to the group"
    kill -KILL -- "-${_pg}" 2>/dev/null || true
    if _wd_provider_group_wait "$_pg" "$_grace"; then _wd_provider_group_gone_line; return 0; fi
  fi
  log "ERROR: task ${task_id}: provider process group ${_pg} still has members ${_grace}s after SIGKILL (pids $(_wd_provider_group_members "$_pg")); they are in kernel teardown and can run no further code -- finalizing past them"
  return 0
}

wd_finalize_task() {
  local final_state

  # --- Provider process-group drain: nothing below runs while the provider's
  # group has a live member (its header above); then the exit trap forgets it ---
  wd_drain_provider_process_group; CLEANUP_ON_EXIT_PROCESS_GROUP=""

  # --- Per-task timeout kill detection (the watchdog fired) --- A kill at a
  # limit the remaining project/batch budget armed is requeued (wake_task_budget.sh);
  # one at max_task_seconds gets a BLOCKED reason an operator tells from a crash.
  if [[ "${_task_killed}" == "true" ]] \
      && ! requeue_after_budget_stop "$task_id" "$task_status" "${LOG_FILE:-}" "${_provider_timeout_seconds:-}" "${MAX_TASK_SECONDS:-0}"; then
    log "task ${task_id}: per-task timeout expired (${_provider_timeout_seconds:-${MAX_TASK_SECONDS}}s); process killed — marking BLOCKED"
    block_task_with_record "$task_id" "$task_status" per_task_timeout "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
      "exceeded max_task_seconds (${_provider_timeout_seconds:-${MAX_TASK_SECONDS}}s effective per-task limit; configured max_task_seconds=${MAX_TASK_SECONDS}s). Agent was still running after the hard timeout. See ${LOG_FILE} for execution log." \
      "$(block_record_evidence --worktree "${_task_worktree_path:-}" --repo "${_canonical_dev_tree_path:-}" --branch "${_task_feature_branch:-}" \
          --source "${_task_source_branch:-}" --task-dir "$task_dir" --diag-log "${LOG_FILE:-}")" "timeout_s=${_provider_timeout_seconds:-${MAX_TASK_SECONDS}}" || true
  fi  # Either way, fall through to the post-task path (sweep, teardown, marker): nothing leaks.

  # --- Restore canonical PGAI_DEV_TREE_PATH after TESTER task ---
  # TESTER tasks override PGAI_DEV_TREE_PATH to the worktree path for the
  # duration of the agent run.  Restore the canonical value now so that
  # teardown and any subsequent tasks use the correct dev tree.
  if [[ "$SUBAGENT" == "tester" && -n "${_task_worktree_path:-}" ]]; then
    export PGAI_DEV_TREE_PATH="${_canonical_dev_tree_path}"
    log "task ${task_id}: PGAI_DEV_TREE_PATH restored to canonical value (${_canonical_dev_tree_path:-<unset>})"
    # Restore the canonical dev tree's git HEAD to the resting branch.
    # The worktree teardown below removes the detached worktree; this restores
    # the canonical HEAD so the operator finds the dev tree on its resting
    # branch, not parked or detached.
    if [[ -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
      set +e
      git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
      set -e
      log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (post-tester)"
    fi
  fi

  # --- Post-task pollution sweep ---
  # Compare the canonical dev tree's porcelain state against the pre-task
  # snapshot.  On new untracked files: quarantine (move) to the pollution dir.
  # On tracked modifications during git-read-only tasks: log ERROR, record
  # in the task's status.md Summary.  CM tasks are exempt from the tracked
  # check.  The sweep is best-effort: any failure logs a WARNING and never
  # blocks or fails the task.
  if [[ "${_pollution_snapshot_ok}" == "true" ]]; then
    {
      local _pollution_post_snapshot=""
      local _pollution_sweep_err=0
      _pollution_post_snapshot="$(git -C "${_sweep_canonical_tree}" status --porcelain=v1 --untracked-files=all 2>/dev/null)" || _pollution_sweep_err=1
      if [[ $_pollution_sweep_err -ne 0 ]]; then
        log "WARNING: pollution sweep: post-task snapshot failed for ${task_id}; sweep skipped"
      else
        # Build sorted lists of pre/post lines to diff.
        local _new_untracked=()
        local _modified_tracked=()
        local _pol_line _pol_xy _pol_path
        # Only the lines the task added (wd_pollution_new_lines: whole-line compare).
        while IFS= read -r _pol_line; do
          [[ -z "$_pol_line" ]] && continue
          _pol_xy="${_pol_line:0:2}"
          _pol_path="${_pol_line:3}"
          case "$_pol_xy" in
            "??") _new_untracked+=( "$_pol_path" ) ;;     # new untracked file/dir: quarantine candidate
            *)    _modified_tracked+=( "$_pol_path" ) ;;  # tracked file with a new/changed status
          esac
        done < <(wd_pollution_new_lines "$_pollution_pre_snapshot" "$_pollution_post_snapshot")

        # --- Handle new untracked files: quarantine ---
        if [[ ${#_new_untracked[@]} -gt 0 ]]; then
          local _pol_quarantine_root
          _pol_quarantine_root="$(pgai_temp_dir)/pollution/${task_id}"
          mkdir -p "$_pol_quarantine_root" 2>/dev/null || true
          local _pol_src _pol_dst _pol_dst_dir _pol_moved=()
          for _pol_src in "${_new_untracked[@]}"; do
            # Strip trailing slash (directories show as "path/" in porcelain).
            _pol_src="${_pol_src%/}"
            local _pol_abs_src="${_sweep_canonical_tree}/${_pol_src}"
            _pol_dst="${_pol_quarantine_root}/${_pol_src}"
            _pol_dst_dir="$(dirname "$_pol_dst")"
            if [[ -e "$_pol_abs_src" ]]; then
              mkdir -p "$_pol_dst_dir" 2>/dev/null && \
                mv "$_pol_abs_src" "$_pol_dst" 2>/dev/null && \
                _pol_moved+=( "$_pol_src" ) || \
                log "WARNING: pollution sweep: could not quarantine '${_pol_src}' for task ${task_id}"
            fi
          done
          if [[ ${#_pol_moved[@]} -gt 0 ]]; then
            local _pol_names
            _pol_names="$(printf ' %s' "${_pol_moved[@]}")"
            log "WARNING: pollution sweep: task ${task_id} (agent ${SUBAGENT}) left untracked file(s) in dev tree; quarantined to ${_pol_quarantine_root}:${_pol_names}"
          fi
        fi

        # --- Handle tracked modifications during git-read-only tasks ---
        # Git-read-only agents: tester, pm, po.  CM is exempt.
        if [[ ${#_modified_tracked[@]} -gt 0 ]]; then
          case "$SUBAGENT" in
            tester|pm|po)
              local _mod_names
              _mod_names="$(printf ' %s' "${_modified_tracked[@]}")"
              log "ERROR: pollution sweep: task ${task_id} (agent ${SUBAGENT}) modified tracked file(s) in dev tree:${_mod_names}"
              # Record the finding in the task's status.md Summary (append, best-effort).
              _wd_append_summary_finding "${task_status}" \
                "POLLUTION SWEEP WARNING: agent ${SUBAGENT} modified tracked file(s) during this task:${_mod_names}" \
                || log "WARNING: pollution sweep: could not append finding to ${task_status}"
              ;;
            *)
              # CM (and other agents): exempt from tracked-file check.
              ;;
          esac
        fi
      fi
    } 2>/dev/null || log "WARNING: pollution sweep: sweep error for task ${task_id}; task result is unaffected"
  fi

  # --- Model-mismatch narration ---
  # Runs once per finalize, independent of final_state, so even a
  # timeout-killed task's partial token usage gets checked against the
  # stamp. See narrate_model_mismatch's own header above for the full
  # contract; it is narration only and never touches final_state or
  # task_status.
  narrate_model_mismatch "$task_status" "${task_artifact_dir}/tokens.json" "$task_id"

  # --- Per-RC rollup refresh ---
  # Runs once per finalize, independent of final_state, now that this
  # task's tokens.json is final. If the task's README attributes it to an
  # RC whose rollup already exists, that rollup is recomputed so it counts
  # this task -- the CM release task that wrote the rollup from inside
  # finalize, and whose own usage is captured only after its session ends,
  # is the standing case. Narration only, appended to the task log; it
  # never touches final_state or task_status, and a failed refresh is a
  # warning there rather than a result for this task.
  metrics_rollup_refresh_for_task "$task_dir" "$KANBAN_ROOT" "${_CURRENT_PROJECT:-}" \
    2>>"$LOG_FILE" || true
  # A ## State that is absent, empty or not one of the six states blocks the task: State and Block Record now, the [B] marker below.
  normalize_status_file "$task_status"
  final_state="$(get_state "$task_status" 2>/dev/null)" || final_state=""
  [[ "$final_state" =~ ^(BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO)$ ]] || { block_task_with_unreadable_state "$task_id" "$task_status" "$final_state" "${role:-}" "$SUBAGENT" || true; final_state="BLOCKED"; }

  LAST_TASK_DISPATCH_REFUSED=false
  if [[ "$final_state" == "WORKING" ]] && provider_dispatch_refused "$task_id"; then
    # --- Provider CLI refused the dispatch before any work began ---
    # Checked ahead of the transient/zero-progress classification below:
    # the agent never ran, so neither counter applies. See
    # requeue_after_dispatch_refusal's own header.
    LAST_TASK_DISPATCH_REFUSED=true
    final_state="$(requeue_after_dispatch_refusal "$task_id" "$task_status" "$LOG_FILE")"
  elif [[ "$final_state" == "WORKING" ]] \
      && _usage_limit_state="$(wake_record_usage_limit_pause "$task_id" "$task_status" "$LOG_FILE")"; then
    # --- Provider usage limit: a timed fleet-wide pause; past a bound on
    # uncounted requeues, a zero-progress cycle (wake_provider_pause.sh). ---
    final_state="$_usage_limit_state"
  elif [[ "$final_state" == "WORKING" ]]; then
    # --- Transient API error detection (shared helper) ---
    # Delegates to handle_working_after_exit in wake_common.sh, which greps
    # the log tail for Anthropic and OpenAI transient error signatures, manages
    # the retry counter, and writes BLOCKED + appropriate Needs Human value to
    # the status file.  Detection logic lives in exactly one place.
    final_state="$(handle_working_after_exit "$task_id" "$task_status" "$LOG_FILE" "$agent_exit")"
  elif [[ "${_task_killed:-false}" != "true" ]]; then
    # --- Zero-progress counter reset (real progress this cycle) ---
    # The task's own final state differs from WORKING WITHOUT going through
    # handle_working_after_exit's fallback above — the agent itself moved
    # the state (DONE, its own BLOCKED, WONT-DO, WAITING, or a fresh
    # BACKLOG). "Any real progress resets the count to zero" (see
    # overwatch_protocol.sh's zero_progress_* header). Excluded when
    # _task_killed is true: that BLOCKED was forced by the per-task timeout
    # above, not written by the agent, so it is not real progress. The same
    # progress clears the usage-limit requeue count (wake_provider_pause.sh).
    zero_progress_reset "$task_status"
    wake_usage_limit_requeue_reset "$task_status"
  fi

  # --- CODER/WRITER completion gate ---
  # Runs BEFORE the DONE transition is accepted anywhere below (mark_backlog,
  # the on-BLOCK overwatch trigger, training-corpus copy, worktree teardown).
  # Delegates to coder_writer_completion_gate in wake_common.sh, which verifies
  # the feature branch is merged into the task's source (RC) branch and the
  # task worktree is clean, mechanically rather than trusting the agent's own
  # done-claim. No-op for non-CODER/WRITER subagents and for any final_state
  # other than DONE. _completion_gate_refused distinguishes "this gate just
  # downgraded DONE to BLOCKED" from any other BLOCKED path, so the worktree
  # teardown step below can retain evidence for operator inspection without
  # changing retention behavior for pre-existing BLOCKED reasons.
  local _completion_gate_refused=false
  if [[ "$final_state" == "DONE" ]]; then
    local _gate_state
    _gate_state="$(coder_writer_completion_gate \
      "$task_id" "$task_status" "$SUBAGENT" \
      "${_task_feature_branch:-}" "${_task_source_branch:-}" "${_task_worktree_path:-}")"
    if [[ "$_gate_state" != "$final_state" ]]; then
      _completion_gate_refused=true
    fi
    final_state="$_gate_state"
  fi

  # --- Mutating phase boundary: queue write ---
  # Re-verify this chain still holds the project repo lock immediately before
  # the case statement below performs this task's queue-marker write
  # (mark_backlog) — the last of the three mutating phase boundaries (see
  # lock_provenance.sh's "Chain-level ownership reverification" header and
  # wake_driver.sh's process_one_task, which reverifies before dispatch and
  # before this stage begins). Reads _project_lock_file and writes
  # _project_lock_lost via bash dynamic scoping, exactly as process_one_task
  # does — this function declares no local of either name. A loss here skips
  # the queue write and every mutation below it (on-BLOCK overwatch trigger,
  # training-corpus copy, worktree teardown, dev-tree HEAD restore) and
  # leaves final_state unrecorded (LAST_TASK_FINAL_STATE stays "") rather
  # than risk a marker write racing the chain that now legitimately owns
  # this project's queue files; process_one_task's own dispatch-boundary
  # check catches the same loss again before the next task and stops the
  # whole chain cleanly.
  if ! pgai_lock_reverify_or_narrate "${_project_lock_file:-}" "$$" "queue write" "wake-chain:${_CURRENT_PROJECT:-}" "$task_id"; then
    _project_lock_lost=true
    log "task ${task_id}: chain stopping before queue write — repo lock ownership lost (see narration above)"
    LAST_TASK_FINAL_STATE=""
    return 0
  fi

  case "$final_state" in
    BLOCKED)
      mark_backlog "$task_id" "B"
      # --- on-BLOCK overwatch trigger ---
      # Only records that this chain blocked a task (_chain_blocked_a_task,
      # run_project_chain's local, via dynamic scoping). The nudge itself
      # fires after the chain releases the repo lock — fire_block_triage_nudge
      # in wake_project.sh — so the woken triage can take that lock.
      _chain_blocked_a_task=true
      ;;
    WAITING)      mark_backlog "$task_id" "W" ;;
    DONE|WONT-DO) mark_backlog "$task_id" "x" ;;
    BACKLOG|WORKING) mark_backlog "$task_id" " " ;;
    *)
      echo "ERROR: unexpected final state '${final_state}' for task ${task_id}" >&2
      echo "       This state should not occur in current kanban versions. Investigate task: ${task_id}" >&2
      exit 1
      ;;
  esac
  # The finalize record: this dispatch's DONE already went through the gate above.
  task_finalize_record_write "$task_dir" "$final_state" || log "task ${task_id}: WARNING: finalize record not written; the sweeps re-run the completion gate on a DONE"

  # --- Post-session /tmp litter check ---
  wake_bracket_post_session "$final_state" "${_litter_snapshot_file:-}" "$task_status" "$task_id" "${_CURRENT_PROJECT:-}" "${KANBAN_ROOT:-}"

  # --- Post-task training corpus copy ---
  # Gate: per-project [training] reasoning_trace=true AND current SUBAGENT in
  # [training] training_agents list.  Empty training_agents means NO agents
  # (narrow-start default).  The global PGAI_REASONING_TRACE=1 env var is a
  # deprecated shim that force-enables capture for all projects/agents; it emits
  # a single deprecation notice per wake (mirroring the PGAI_VERBOSE_MODE shim).
  # The destination is always per-project regardless of how the gate was opened.
  # Counterpart of the documentation-feedback prompt-injection gate in
  # wake_driver_invoke.sh's _wd_build_task_prompt — not a block in
  # wake_common.sh, which carries no prompt-injection or copy logic of its
  # own (it is a thin sourcing shell over the staged lib files).
  if [[ "$final_state" == "DONE" ]]; then
    local _training_gate
    _training_gate="$(_resolve_training_gate "${_CURRENT_PROJECT:-}" "${SUBAGENT}")"
    # One-shot guard propagation for the deprecation notice (mirrors verbose-mode
    # pattern: subshell cannot write parent-shell vars, so we persist flag here).
    if [[ "${PGAI_REASONING_TRACE:-0}" == "1" ]]; then
        _REASONING_TRACE_DEPRECATION_WARNED=true
    fi
    if [[ "${_training_gate}" == "true" ]]; then
      local notes_source="${task_artifact_dir}/task-notes.md"
      if [[ -f "$notes_source" ]]; then
        # Destination is per-project: $PGAI_PROJECT_ROOT/logs/training/<role>/<ts>-<task_id>.md
        # Resolve the project root via pp_project_root to avoid rebuilding the path manually.
        local _training_proj_root
        _training_proj_root="$(pp_project_root "${_CURRENT_PROJECT:-}" 2>/dev/null || echo "")"
        if [[ -n "$_training_proj_root" ]]; then
          local training_dest_dir="${_training_proj_root}/logs/training/${SUBAGENT}"
          local training_dest_file="${training_dest_dir}/${task_start_ts}-${task_id}.md"
          mkdir -p "$training_dest_dir"
          cp "$notes_source" "$training_dest_file"
          log "training corpus: copied task-notes to ${training_dest_file}"
        else
          log "training corpus: skipped — could not resolve project root for ${_CURRENT_PROJECT:-<unknown>}"
        fi
      fi
    fi
  fi

  # --- Post-TESTER event-hook: finalize=report intake closure ---
  # When the completing TESTER task reaches DONE, run the completion census to
  # determine whether all sibling tasks sharing the same requirements path are
  # terminal.  If so, flip the originating intake item from running to done.
  # Guards: SUBAGENT==tester, final_state==DONE, WF_MANIFEST_FINALIZE==report.
  # This is the low-latency path: closure lands in the same wake tick that
  # completes the final task.  The per-wake sweep (sweep_running_intake_census)
  # is the eventual-consistency guarantee for items whose closing tick is missed.
  if [[ "$SUBAGENT" == "tester" && "$final_state" == "DONE" ]]; then
    close_intake_on_finalize_report \
      "$task_id" \
      "$task_readme" \
      "${PGAI_PROJECT_ROOT:-}" \
      "${TASKS_ROOT:-}" \
      "${WF_MANIFEST_FINALIZE:-}"
  fi

  # --- Post-PM auto-materialization ---
  if [[ "$SUBAGENT" == "pm" && "$final_state" == "DONE" ]]; then
    local pm_materialize_script
    # Use KANBAN_ROOT (set by wake_common.sh from PGAI_AGENT_KANBAN_ROOT_PATH) to locate
    # pm_materialize.py. In the live install pm-agent/ lives directly under KANBAN_ROOT;
    # in the dev tree the script is installed alongside the rest of the kanban tree, so
    # the same relative path applies after install.sh runs.
    pm_materialize_script="${KANBAN_ROOT}/pm-agent/pm_materialize.py"
    # Fallback: dev-tree invocation where script lives under team/scripts/wake/ and
    # pm-agent is two levels up at team/pm-agent/.
    if [[ ! -f "$pm_materialize_script" ]]; then
      pm_materialize_script="${SCRIPT_DIR}/../../pm-agent/pm_materialize.py"
    fi
    local plan_json=""
    while IFS= read -r -d '' _f; do
      plan_json="$_f"
      break
    done < <(find "$task_artifact_dir" -maxdepth 1 -name '*plan*.json' -print0 2>/dev/null)

    if [[ -n "$plan_json" ]]; then
      log "PM task ${task_id} done: found plan JSON at ${plan_json}; invoking pm_materialize.py"
      # Extract the requirements file path from the PM task's
      # README so we can pass --requirements-path to the materializer.  The PM
      # task README lists the requirements file as the first bullet under
      # ## Inputs (written by pm-agent.sh).  Extract the path from that line
      # so the materializer receives the authoritative path independent of
      # whether the PM plan JSON carried requirements_path.
      local _pm_task_readme="${task_dir}/README.md"
      local _req_path_arg=""
      if [[ -f "$_pm_task_readme" ]]; then
        # Read lines under ## Inputs until the next ## heading; take the first
        # non-blank "- " bullet as the requirements file path.
        local _in_inputs=false
        while IFS= read -r _line; do
          if [[ "$_line" == "## Inputs" ]]; then
            _in_inputs=true
            continue
          fi
          if [[ "$_in_inputs" == "true" ]]; then
            if [[ "$_line" == "##"* ]]; then
              break
            fi
            # Match lines like "- /absolute/path/to/file.md"
            if [[ "$_line" =~ ^-[[:space:]]+(/[^[:space:]]+\.md) ]]; then
              _req_path_arg="${BASH_REMATCH[1]}"
              break
            fi
          fi
        done < "$_pm_task_readme"
      fi
      local _req_path_flag=""
      if [[ -n "$_req_path_arg" && -f "$_req_path_arg" ]]; then
        _req_path_flag="--requirements-path ${_req_path_arg}"
        log "pm_materialize.py: passing --requirements-path ${_req_path_arg}"
      else
        log "pm_materialize.py: requirements path not extracted from PM README; materializer will use plan JSON field"
      fi
      local materialize_exit=0
      set +e
      # shellcheck disable=SC2086
      python3 "$pm_materialize_script" "$plan_json" --team-root "$PGAI_PROJECT_ROOT" ${_req_path_flag} 2>&1 | tee -a "$LOG_FILE"
      materialize_exit=${PIPESTATUS[0]}
      set -e
      # Exit-code contract for pm_materialize.py:
      #   0 — materialization succeeded (tasks created).
      #   2 — idempotent no-op: plan was already materialized (hash-marker present).
      #       This is the expected result when the PM agent materialized inside the task
      #       and the wake-script safety re-run hits the marker. Log INFO, not WARNING.
      #   non-zero (not 2) — genuine materialization failure; surface as WARNING.
      if [[ $materialize_exit -eq 0 ]]; then
        log "pm_materialize.py completed successfully for ${plan_json}"
      elif [[ $materialize_exit -eq 2 ]]; then
        log "pm_materialize.py: plan already materialized (idempotent no-op) for ${plan_json}"
      else
        log "WARNING: pm_materialize.py exited ${materialize_exit} for ${plan_json} — PM task state unchanged"
      fi
    else
      log "PM task ${task_id} done: no *plan*.json found in ${task_artifact_dir}; skipping auto-materialization"
    fi
  fi

  # --- Shell-level debug log: task completion ---
  if [[ "${_debug_gate}" == "true" ]] && [ -n "${task_debug_log:-}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${task_id}] wake: task finished (final_state=${final_state} agent_exit=${agent_exit})" >> "$task_debug_log"
  fi

  # --- Worktree teardown ---
  # teardown_task_worktree is idempotent; safe to call when no worktree was
  # created (variable empty) or when it was already removed.
  #
  # CODER/WRITER: teardown on all exit paths (DONE, BLOCKED, WONT-DO), except
  # when the completion gate just refused this DONE-claim — that
  # BLOCKED is retained like TESTER's, below, so an operator can inspect the
  # dirty paths or the merge state before the worktree (a --force removal)
  # would otherwise discard them.
  #
  # TESTER: teardown only on DONE/WONT-DO.  On BLOCKED, retain the worktree
  # per the worktree.sh retention policy so the operator can inspect partial
  # verification state.  _canonical_dev_tree_path holds the per-project value
  # captured at function start; use it directly to avoid the env-variable
  # round-trip; use _canonical_dev_tree_path directly.
  if [[ -n "${_task_worktree_path:-}" ]]; then
    if [[ "$SUBAGENT" == "tester" && "$final_state" == "BLOCKED" ]]; then
      log "task ${task_id}: tester worktree retained for operator inspection (BLOCKED path: ${_task_worktree_path})"
    elif [[ "${_completion_gate_refused:-false}" == "true" ]]; then
      log "task ${task_id}: completion-gate worktree retained for operator inspection (BLOCKED path: ${_task_worktree_path})"
    else
      teardown_task_worktree "$task_id" "${_canonical_dev_tree_path}" || true
      log "task ${task_id}: worktree torn down (final_state=${final_state})"
    fi
  fi

  # Restore canonical dev tree HEAD to the resting branch (CODER/WRITER
  # post-teardown). For CODER/WRITER tasks the env-var restore above is not
  # needed (TESTER is the only path that overrides PGAI_DEV_TREE_PATH), but
  # the git HEAD MUST be restored here so the dev tree is never left parked
  # after the worktree is torn down. The TESTER restore already ran above;
  # this block is for CODER/WRITER tasks only (guard: SUBAGENT != tester).
  # Restore is also skipped when the worktree was retained (tester BLOCKED path)
  # because in that case the dev tree may still be needed parked.
  if [[ "$SUBAGENT" != "tester" && -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
    set +e
    git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
    set -e
    log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (post-teardown)"
  fi

  log "finished task ${task_id} with state ${final_state}"
  LAST_TASK_FINAL_STATE="$final_state"
  return 0
}
