#!/usr/bin/env bash
# team/scripts/lib/wake_provider_pause.sh
#
# Wake provider-pause stage: a provider's account usage limit is a timed,
# fleet-wide pause, not a task failure. Extracted as its own stage so the
# post-exit classifier (wake_lifecycle.sh) and the dispatch loop
# (wake_driver.sh) share one owner for it.
#
# Public entry points
# -------------------
#   wake_record_usage_limit_pause <task_id> <task_status> <log_file>
#       Called by wake_driver_finalize.sh for a task still WORKING after
#       its agent exited, ahead of handle_working_after_exit. When the last
#       50 lines of <log_file> carry a provider usage-limit signature
#       (pgai_agent_kanban.lib.provider_pause: a line that opens with the
#       provider CLI's own usage-limit message, never agent prose that
#       merely mentions the usage limit), records a fleet-wide pause
#       for ${ACTIVE_PROVIDER} -- the marker
#       $KANBAN_ROOT/provider-pause/<provider>.pause -- until the reset time
#       the output states, or [wake] usage_limit_pause_seconds
#       (USAGE_LIMIT_PAUSE_SECONDS) from now when it states none, and
#       counts the attempt in the task's durable ## Usage Limit Requeue
#       record (count, limit, signature, resumes_at).
#       Up to wake_usage_limit_requeue_cap (3) usage-limit requeues since
#       the task last moved under its own power, the task returns to
#       BACKLOG with a ## Summary naming the pause and neither
#       ## Transient Retry nor ## Zero Progress is touched: the account,
#       not the task, is out of capacity. Every usage-limit attempt past
#       that cap still records the pause but counts as a zero-progress
#       cycle (zero_progress_advance_or_escalate, reason_code
#       usage_limit_requeue): BACKLOG below [wake] zero_progress_cap,
#       BLOCKED with Needs Human=yes at it, so a task cannot cycle
#       BACKLOG -> WORKING -> BACKLOG on usage limits forever. Prints the
#       resulting state (BACKLOG or BLOCKED) and returns 0. Returns 1,
#       printing nothing and changing nothing, when there is no usage-limit
#       signature (or the module cannot run), so the transient/zero-progress
#       classification proceeds exactly as before.
#
#   wake_usage_limit_requeue_reset <task_status>
#       Clears ## Usage Limit Requeue back to none. wake_driver_finalize.sh
#       calls it beside zero_progress_reset whenever the task's own state
#       moved this cycle (real progress). A status.md without a non-zero
#       record is left untouched, so the section never appears on a task
#       that never hit a usage limit.
#
#   wake_provider_pause_gate <project_name>
#       The dispatch-time check run_project_chain makes at every task
#       boundary. Returns 0 when dispatch may proceed and 1 while the
#       provider is paused, setting WAKE_PROVIDER_PAUSED_UNTIL for the
#       caller's stop reason. Every role is held, OVERWATCH included: its
#       wake dispatches to the same provider. The first wake to find the
#       pause over narrates its end (the module removes the marker as it
#       reports it). A check that cannot run holds nothing.
#
# Dependencies (defined by the caller before these functions run)
# ---------------------------------------------------------------
#   wake_bootstrap.sh       — log(), KANBAN_ROOT, ACTIVE_PROVIDER.
#   wake_task_state.sh      — set_block, normalize_status_file.
#   wake_task_transition.sh — transition_task_state.
#   overwatch_protocol.sh (sourced by wake_bootstrap.sh) —
#                             _overwatch_write_record,
#                             zero_progress_advance_or_escalate.
#   pp_run_ops.sh / error_contract.sh (sourced by wake_bootstrap.sh) —
#                             pp_run_ops, pgai_emit_error_contract.
# Sourced by wake_common.sh immediately after wake_lifecycle.sh.

# ---------------------------------------------------------------------------
# wake_usage_limit_requeue_cap
# Prints 3: the usage-limit requeues of one task, since it last moved under
# its own power, that return it to BACKLOG without counting a retry. Every
# usage-limit attempt past this many counts as a zero-progress cycle
# instead. A fixed bound rather than a [wake] key: [wake] zero_progress_cap
# already tunes how many counted cycles a task gets before it escalates.
# ---------------------------------------------------------------------------
wake_usage_limit_requeue_cap() {
  printf '3\n'
}

# ---------------------------------------------------------------------------
# wake_record_usage_limit_pause <task_id> <task_status> <log_file>
# ---------------------------------------------------------------------------
wake_record_usage_limit_pause() {
  local task_id="${1:-}" task_status="${2:-}" log_file="${3:-}"
  [[ -f "$log_file" && -n "${KANBAN_ROOT:-}" ]] && declare -f pp_run_ops >/dev/null 2>&1 || return 1

  local _pause_out="" _pause_line _pause_until="" _pause_source="" _pause_sig=""
  local _provider="${ACTIVE_PROVIDER:-claude}"
  _pause_out="$(pp_run_ops pgai_agent_kanban.lib.provider_pause record \
      "$KANBAN_ROOT" "$_provider" "$log_file" \
      --default-seconds "${USAGE_LIMIT_PAUSE_SECONDS:-}" \
      --task "$task_id" --project "${_CURRENT_PROJECT:-none}" 2>/dev/null)" || return 1
  while IFS= read -r _pause_line; do
    case "${_pause_line%%=*}" in
      resumes_at) _pause_until="${_pause_line#*=}" ;;
      source) _pause_source="${_pause_line#*=}" ;;
      signature) _pause_sig="${_pause_line#*=}" ;;
    esac
  done <<< "$_pause_out"

  # Count this usage-limit requeue in the durable record. An unreadable
  # record reads as 0: the bound then starts over, never blocks wrongly.
  local _requeue_cap _requeue_count _requeue_body
  _requeue_cap="$(wake_usage_limit_requeue_cap)"
  _requeue_count="$(pp_run_ops pgai_agent_kanban.task_status read "$task_status" \
      --section="Usage Limit Requeue" --field=count --default=0 2>/dev/null)" || _requeue_count=0
  [[ "$_requeue_count" =~ ^[0-9]+$ ]] || _requeue_count=0
  _requeue_count=$(( _requeue_count + 1 ))
  _requeue_body="count: ${_requeue_count}"$'\n'"limit: ${_requeue_cap}"$'\n'
  _requeue_body+="signature: ${_pause_sig}"$'\n'"resumes_at: ${_pause_until}"

  if (( _requeue_count > _requeue_cap )); then
    log "provider ${_provider} usage limit reached (${_pause_sig}); fleet-wide dispatch pause recorded until ${_pause_until} (${_pause_source} reset time) -- task ${task_id} already requeued for a usage limit ${_requeue_cap} times since it last moved; counting this attempt as a zero-progress cycle"
    _overwatch_write_record wake_record_usage_limit_pause "$task_status" "Usage Limit Requeue" "$_requeue_body" \
      || log "task ${task_id} WARNING: ## Usage Limit Requeue record not written"
    zero_progress_advance_or_escalate "$task_id" "$task_status" "usage_limit_requeue" \
      "provider ${_provider} reported its usage limit again (${_pause_sig}) after ${_requeue_cap} uncounted usage-limit requeues; dispatch to it is paused fleet-wide until ${_pause_until}; see ${log_file}" \
      "wake.usage-limit-requeue.zero-progress-cap" || true
    return 0
  fi

  log "provider ${_provider} usage limit reached (${_pause_sig}); fleet-wide dispatch pause recorded until ${_pause_until} (${_pause_source} reset time) -- task ${task_id} returned to BACKLOG, no retry counted (usage-limit requeue ${_requeue_count}/${_requeue_cap})"
  if transition_task_state "$task_id" "$task_status" "BACKLOG"; then
    _overwatch_write_record wake_record_usage_limit_pause "$task_status" "Usage Limit Requeue" "$_requeue_body" \
      || log "task ${task_id} WARNING: ## Usage Limit Requeue record not written"
    set_block "$task_status" "Summary" "Returned to BACKLOG by the wake: provider ${_provider} reported its usage limit (${_pause_sig}). Dispatch to that provider is paused fleet-wide until ${_pause_until}; this attempt counts against neither ## Transient Retry nor ## Zero Progress (usage-limit requeue ${_requeue_count}/${_requeue_cap}; past ${_requeue_cap}, each further usage-limit attempt counts as a zero-progress cycle). See ${log_file}."
  else
    log "task ${task_id} WARNING: atomic transition to BACKLOG failed after a usage limit; status.md/marker left as they were (no partial update)"
  fi
  normalize_status_file "$task_status"
  printf 'BACKLOG\n'
  return 0
}

# ---------------------------------------------------------------------------
# wake_usage_limit_requeue_reset <task_status>
# ---------------------------------------------------------------------------
wake_usage_limit_requeue_reset() {
  local task_status="${1:-}" _count
  [[ -f "$task_status" ]] && declare -f pp_run_ops >/dev/null 2>&1 || return 0
  _count="$(pp_run_ops pgai_agent_kanban.task_status read "$task_status" \
      --section="Usage Limit Requeue" --field=count --default=0 2>/dev/null)" || return 0
  [[ "$_count" =~ ^[0-9]+$ && "$_count" -gt 0 ]] || return 0
  _overwatch_write_record wake_usage_limit_requeue_reset "$task_status" "Usage Limit Requeue" "none" || true
}

# ---------------------------------------------------------------------------
# wake_provider_pause_gate <project_name>
# ---------------------------------------------------------------------------
wake_provider_pause_gate() {
  local project_name="${1:-}"
  local _ppg_provider="${ACTIVE_PROVIDER:-claude}"
  WAKE_PROVIDER_PAUSED_UNTIL=""
  [[ -n "${KANBAN_ROOT:-}" ]] && declare -f pp_run_ops >/dev/null 2>&1 || return 0

  local _ppg_out="" _ppg_rc=0 _ppg_line _ppg_state="" _ppg_until="" _ppg_source="" _ppg_sig="" _ppg_marker=""
  _ppg_out="$(pp_run_ops pgai_agent_kanban.lib.provider_pause check "$KANBAN_ROOT" "$_ppg_provider" 2>/dev/null)" \
    || _ppg_rc=$?
  while IFS= read -r _ppg_line; do
    case "${_ppg_line%%=*}" in
      state) _ppg_state="${_ppg_line#*=}" ;;
      resumes_at) _ppg_until="${_ppg_line#*=}" ;;
      source) _ppg_source="${_ppg_line#*=}" ;;
      signature) _ppg_sig="${_ppg_line#*=}" ;;
      marker) _ppg_marker="${_ppg_line#*=}" ;;
    esac
  done <<< "$_ppg_out"

  if [[ "$_ppg_state" == "ended" ]]; then
    log "provider ${_ppg_provider} usage-limit pause ended (was until ${_ppg_until}) -- dispatch resumes"
    return 0
  fi
  if [[ "$_ppg_rc" -ne 3 || "$_ppg_state" != "paused" ]]; then
    return 0
  fi

  WAKE_PROVIDER_PAUSED_UNTIL="$_ppg_until"
  log "project ${project_name}: provider ${_ppg_provider} is paused after reaching its usage limit (${_ppg_sig}) -- not dispatching any task until ${_ppg_until} (${_ppg_source} reset time)"
  # BLOCK-WORTHY: every dispatch to a provider at its usage limit fails
  # the same way and would burn each task's retries; the pause is timed
  # and lifts by itself at resumes_at. Holds dispatch only; no task state
  # changes.
  # CENSUS-ANCHOR: wake-provider-pause-01
  pgai_emit_error_contract "wake.dispatch_paused_usage_limit" \
    "provider ${_ppg_provider} reported its usage limit; task dispatch to it is paused for every role and project until ${_ppg_until}" \
    "rm -f '${_ppg_marker}'" "$project_name" || true
  return 1
}
