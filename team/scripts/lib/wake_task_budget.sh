#!/usr/bin/env bash
# team/scripts/lib/wake_task_budget.sh
#
# Wake task-budget stage: the arithmetic that decides how long one task may
# run, and the startup check that the three runtime ceilings nest. One of
# the single-concern lib stages wake_driver.sh sources ahead of
# run_project_chain, kept separate so the driver and the invoke stage each
# stay under the engine-decomposition line ceiling
# (team/scripts/lint_engine_size_dupe.py).
#
# The three ceilings, all from kanban.cfg [wake] via config_loader.sh:
#
#   MAX_TASK_SECONDS     — per-task watchdog limit (0 = no per-task bound;
#                          unset or blank = the registry default,
#                          config_wake_max_task_seconds_default)
#   MAX_PROJECT_SECONDS  — per-project budget, clock reset per project
#                          (0 = no per-project bound)
#   MAX_RUNTIME_SECONDS  — whole-batch budget, clock started at wake
#                          (0 = no batch bound; unset or blank = the
#                          registry default,
#                          config_wake_max_runtime_seconds_default)
#
# They must nest task < project <= batch. run_project_chain checks the
# project and batch budgets only BETWEEN tasks, so without this stage a
# task dispatched late in the batch could run its whole per-task limit
# past the batch cap — with three equal ceilings, a batch could run to
# roughly twice its configured budget. wd_effective_task_timeout closes
# that: the watchdog is armed with the smallest of the per-task limit and
# the two REMAINING budgets, computed at dispatch, so no task outlives the
# project or the batch that dispatched it.
#
# Public entry points
# -------------------
#   _wd_epoch_now
#       Prints the current wall-clock time as whole seconds since the
#       epoch. The one clock the deadline watchdog in wake_driver_invoke.sh
#       reads, so a unit test can substitute a controlled clock by
#       redefining this single function.
#
#   wd_effective_task_timeout <max_task> <project_cap> <project_elapsed>
#                             <batch_cap> <batch_elapsed>
#       Prints the seconds the watchdog should arm for the task about to
#       be dispatched: the minimum of <max_task>, the remaining project
#       budget (<project_cap> - <project_elapsed>) and the remaining batch
#       budget (<batch_cap> - <batch_elapsed>). A ceiling of 0 (or a
#       non-numeric value) means "no bound" and does not participate. A
#       remaining budget that has already reached 0 clamps to 1 so the
#       watchdog still arms rather than silently disabling itself. Prints
#       0 only when no ceiling is in force at all.
#
#   wd_task_limit_was_budget_cap <armed_limit> <max_task>
#       Returns 0 when a watchdog armed with <armed_limit> seconds was armed
#       by the remaining project or batch budget rather than by the per-task
#       limit: <armed_limit> is positive and either <max_task> is 0 (no
#       per-task bound) or <armed_limit> is below it. A kill at such a
#       limit means the wake ran out of time, not that the task overran
#       max_task_seconds; wd_finalize_task requeues it instead of blocking
#       it. Returns 1 otherwise (a kill at the full max_task_seconds, or no
#       limit armed at all).
#
#   wd_configured_max_task_seconds
#       Prints the per-task limit the wake is configured with:
#       MAX_TASK_SECONDS when it is set and not blank (0 included -- an
#       explicit 0 still disables the per-task bound), otherwise the config
#       registry default from config_loader.sh's
#       config_wake_max_task_seconds_default. An unset variable never reads
#       as 0 (no bound). run_project_chain seeds its _eff_task from this.
#
#   wd_configured_max_runtime_seconds
#       Prints the whole-batch budget the wake is configured with, by the
#       same rule: MAX_RUNTIME_SECONDS when it is set and not blank (an
#       explicit 0 still disables the batch bound), otherwise
#       config_loader.sh's config_wake_max_runtime_seconds_default. An unset
#       variable never reads as 0 (no bound).
#
#   wd_batch_budget_left <batch_elapsed>
#       Prints the seconds left in the whole-batch budget
#       (wd_configured_max_runtime_seconds) after <batch_elapsed> seconds,
#       clamped at 0. Prints nothing when the budget is 0 (no batch bound).
#       A non-numeric budget or elapsed value reads as 0, as in
#       wd_effective_task_timeout. The one place a batch-budget check
#       applies the 0 rule: the lock wait (wake_lock_wait.sh) reads it here.
#
#   wd_batch_budget_reached <batch_elapsed>
#       Returns 0 when a batch budget is in force and <batch_elapsed> has
#       reached it, 1 otherwise -- a budget of 0 is never reached. The
#       between-tasks check in run_project_chain and the contention retry
#       pass in wake_common_run stop on it.
#
#   wd_chain_task_timeout_budget
#       run_project_chain's per-dispatch call: sets the caller's
#       _task_timeout_budget_seconds from the chain's own ceilings and
#       clocks, read through dynamic scope exactly as the lock-refresh
#       wrapper in wake_driver_invoke.sh reads _project_lock_file:
#       _eff_task, _eff_project, _project_start_epoch (run_project_chain
#       locals) and MAX_RUNTIME_SECONDS, WAKE_START_EPOCH (wake globals).
#       An unset clock reads as "started now", i.e. a full budget; an unset
#       or blank _eff_task reads as wd_configured_max_task_seconds, and an
#       unset or blank MAX_RUNTIME_SECONDS as wd_configured_max_runtime_seconds.
#
#   wd_warn_runtime_bound_nesting <project_name> <max_task> <project_cap>
#                                 <batch_cap>
#       Logs a WARNING line for each violated nesting relation: project
#       cap above the batch cap, per-task limit at or above the project
#       cap, per-task limit at or above the batch cap. Equality is a
#       violation for the task limit: a task limit equal to its enclosing
#       budget lets one task consume that whole budget, which is exactly
#       the shape the effective-timeout cap exists to prevent. Never
#       fatal; returns 0.
#
#   requeue_after_budget_stop <task_id> <task_status> <log_file> <armed_limit> <max_task>
#       wd_finalize_task's resolution of a watchdog kill: requeues a budget
#       stop, recording that the next dispatch resumes in the branch mode,
#       and returns 0, or returns 1 for a per-task timeout; see its own
#       header below.
#
#   requeue_after_timeout_with_commits <task_id> <task_status> <log_file>
#                                      <killed_at> <owner_role> <evidence>
#       wd_finalize_task's resolution of a kill at max_task_seconds itself:
#       when the feature branch holds commits ahead of the source branch and
#       the requeue budget allows, records the timeout and one requeue on the
#       Block Record, sets its resume to branch and returns the task to
#       BACKLOG (returns 0); otherwise returns 1 and the caller blocks the
#       task as a per-task timeout. See its own header below.
#
#   wd_record_branch_resume <task_id> <task_status>
#       Records that the task's next dispatch attaches its existing feature
#       branch (resume mode branch) instead of re-creating it: the Block
#       Record's resume field when the task carries a record, otherwise the
#       wake-owned ## Resume Mode section (body: branch), which the worktree
#       stage reads for a task with no Block Record. Never fatal; returns 0.
#
# Dependencies (must already be defined by the caller)
# -----------------------------------------------------
#   log()  — wake_bootstrap.sh's logger; used by wd_warn_runtime_bound_nesting
#            and requeue_after_budget_stop.
#   config_wake_max_task_seconds_default,
#   config_wake_max_runtime_seconds_default (config_loader.sh) — sourced
#            below from this file's own directory when the caller has not
#            already sourced config_loader.sh; used by
#            wd_configured_max_task_seconds and
#            wd_configured_max_runtime_seconds.
#   transition_task_state, set_block, set_block_or_append,
#   normalize_status_file (wake_task_state.sh / wake_task_transition.sh) and
#   block_record_field, block_record_update, block_record_write
#   (block_record.sh) and pp_run_ops (pp_run_ops.sh) — used by
#   requeue_after_budget_stop and requeue_after_timeout_with_commits, which
#   wd_finalize_task calls.
#   operator_inbox_notice (operator_inbox_notice.sh, sourced below) — the
#   inbox entry an unreadable requeue budget leaves; it reads KANBAN_ROOT
#   (else PGAI_AGENT_KANBAN_ROOT_PATH) and _CURRENT_PROJECT.

# Guarded so a caller that already loaded the config keeps the cfg path its
# load_config recorded: sourcing config_loader.sh again would reset it.
declare -F config_wake_max_task_seconds_default >/dev/null \
  && declare -F config_wake_max_runtime_seconds_default >/dev/null \
  || source "$(dirname "${BASH_SOURCE[0]}")/config_loader.sh"
# shellcheck source=operator_inbox_notice.sh
source "$(dirname "${BASH_SOURCE[0]}")/operator_inbox_notice.sh"

# Wall-clock read, isolated so tests can redefine it.
_wd_epoch_now() {
  date +%s
}

# Liberal integer read: prints the value when it is a non-negative integer,
# 0 otherwise, so a blank or malformed ceiling reads as "no bound" instead
# of tripping bash arithmetic.
_wd_budget_int() {
  local _v="${1:-0}"
  _v="${_v//[[:space:]]/}"
  if [[ "$_v" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$_v"
  else
    printf '0\n'
  fi
}

wd_effective_task_timeout() {
  local _max_task _project_cap _project_elapsed _batch_cap _batch_elapsed
  _max_task="$(_wd_budget_int "${1:-0}")"
  _project_cap="$(_wd_budget_int "${2:-0}")"
  _project_elapsed="$(_wd_budget_int "${3:-0}")"
  _batch_cap="$(_wd_budget_int "${4:-0}")"
  _batch_elapsed="$(_wd_budget_int "${5:-0}")"

  local _effective=0 _remaining
  # A bound participates only when enabled (> 0); the smallest enabled
  # bound wins. A remaining budget at or below 0 clamps to 1: the chain
  # only dispatches while budget remains, so 0 here is a clock tick that
  # landed between that check and this call, and it must still arm.
  if (( _max_task > 0 )); then
    _effective="$_max_task"
  fi
  if (( _project_cap > 0 )); then
    _remaining=$(( _project_cap - _project_elapsed ))
    (( _remaining >= 1 )) || _remaining=1
    if (( _effective == 0 || _remaining < _effective )); then _effective="$_remaining"; fi
  fi
  if (( _batch_cap > 0 )); then
    _remaining=$(( _batch_cap - _batch_elapsed ))
    (( _remaining >= 1 )) || _remaining=1
    if (( _effective == 0 || _remaining < _effective )); then _effective="$_remaining"; fi
  fi
  printf '%s\n' "$_effective"
}

wd_task_limit_was_budget_cap() {
  local _armed _max_task
  _armed="$(_wd_budget_int "${1:-0}")"
  _max_task="$(_wd_budget_int "${2:-0}")"
  (( _armed > 0 )) || return 1
  (( _max_task == 0 || _armed < _max_task ))
}

wd_configured_max_task_seconds() {
  if [[ -n "${MAX_TASK_SECONDS:-}" ]]; then
    printf '%s\n' "$MAX_TASK_SECONDS"
  else
    config_wake_max_task_seconds_default
  fi
}

wd_configured_max_runtime_seconds() {
  if [[ -n "${MAX_RUNTIME_SECONDS:-}" ]]; then
    printf '%s\n' "$MAX_RUNTIME_SECONDS"
  else
    config_wake_max_runtime_seconds_default
  fi
}

wd_batch_budget_left() {
  local _cap _elapsed
  _cap="$(_wd_budget_int "$(wd_configured_max_runtime_seconds)")"
  _elapsed="$(_wd_budget_int "${1:-0}")"
  (( _cap > 0 )) || return 0
  (( _elapsed < _cap )) || _elapsed="$_cap"
  printf '%s\n' "$(( _cap - _elapsed ))"
}

wd_batch_budget_reached() {
  local _left
  _left="$(wd_batch_budget_left "${1:-0}")"
  [[ "$_left" == "0" ]]
}

wd_chain_task_timeout_budget() {
  local _now
  _now="$(_wd_epoch_now)"
  _task_timeout_budget_seconds="$(wd_effective_task_timeout "${_eff_task:-$(wd_configured_max_task_seconds)}" "${_eff_project:-0}" \
    "$(( _now - ${_project_start_epoch:-_now} ))" "$(wd_configured_max_runtime_seconds)" "$(( _now - ${WAKE_START_EPOCH:-_now} ))")"
}

wd_warn_runtime_bound_nesting() {
  local _project_name="${1:-}" _max_task _project_cap _batch_cap
  _max_task="$(_wd_budget_int "${2:-0}")"
  _project_cap="$(_wd_budget_int "${3:-0}")"
  _batch_cap="$(_wd_budget_int "${4:-0}")"
  local _rule="the runtime ceilings must nest task < project <= batch"

  if (( _project_cap > 0 && _batch_cap > 0 && _project_cap > _batch_cap )); then
    log "WARNING: project ${_project_name}: MAX_PROJECT_SECONDS (${_project_cap}s) > MAX_RUNTIME_SECONDS (${_batch_cap}s) — per-project cap exceeds batch cap; project cap has no effect; ${_rule}"
  fi
  if (( _max_task > 0 && _project_cap > 0 && _max_task >= _project_cap )); then
    log "WARNING: project ${_project_name}: MAX_TASK_SECONDS (${_max_task}s) >= MAX_PROJECT_SECONDS (${_project_cap}s) — one task may consume the whole project budget; the per-task timeout is capped to the remaining project budget at dispatch; ${_rule}"
  fi
  if (( _max_task > 0 && _batch_cap > 0 && _max_task >= _batch_cap )); then
    log "WARNING: project ${_project_name}: MAX_TASK_SECONDS (${_max_task}s) >= MAX_RUNTIME_SECONDS (${_batch_cap}s) — one task may consume the whole batch budget; the per-task timeout is capped to the remaining batch budget at dispatch; ${_rule}"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# requeue_after_budget_stop <task_id> <task_status> <log_file> <armed_limit> <max_task>
#
# wd_finalize_task's first step after a watchdog kill. When the limit the
# watchdog was armed with came from the wake's remaining project or batch
# budget (wd_task_limit_was_budget_cap <armed_limit> <max_task>), the wake
# ran out of time; the task did not overrun its own limit, so this is not
# a task failure: the task returns to BACKLOG with ## Needs Human no and no
# per_task_timeout record is written -- so OVERWATCH's per_task_timeout
# requeue allowance is not spent. A ## Block Record the task already
# carries is left in place, exactly as a triage requeue leaves it: its
# requeues / requeue_reasons are the requeue budget, which only DONE or
# WONT-DO resets, so the task's next block keeps counting from it.
# Block-storm detection never sees the stop, because it counts tasks whose
# ## State is BLOCKED with Needs Human yes, never a record on a BACKLOG
# task. The zero-progress record is left exactly as it was, and the
# outcome is recorded on the task's own ## Summary.
#
# The commits the stopped agent already made are kept: the stop records
# resume mode branch (wd_record_branch_resume), so the next dispatch's
# worktree stage (wd_setup_task_worktree, wake_driver_worktree.sh) attaches
# the existing feature branch at its tip instead of force-deleting and
# re-creating it from the source branch. A task with a Block Record has that
# record's resume set to branch -- the field a triage requeue sets -- and a
# task without one gets the ## Resume Mode section instead, so no Block
# Record is ever created for a stop that is not a block.
#
# Returns 0 when the kill was a budget stop (handled here; a failed atomic
# transition is narrated and the next reconciliation pass heals it, as
# requeue_after_dispatch_refusal does), 1 when the limit was max_task_seconds
# itself -- the caller then blocks the task as a per-task timeout.
# ---------------------------------------------------------------------------
requeue_after_budget_stop() {
  local task_id="${1:-}" task_status="${2:-}" log_file="${3:-}" armed_limit="${4:-}" max_task="${5:-0}"
  wd_task_limit_was_budget_cap "$armed_limit" "$max_task" || return 1

  log "task ${task_id}: stopped at the wake's remaining time budget (${armed_limit}s armed, below max_task_seconds=${max_task}s) -- returning it to BACKLOG; this is not a per-task timeout"
  if transition_task_state "$task_id" "$task_status" "BACKLOG"; then
    set_block "$task_status" "Needs Human" "no"
    set_block "$task_status" "Summary" "Returned to BACKLOG by the wake: the batch or project time budget ran out ${armed_limit}s into this dispatch (configured max_task_seconds=${max_task}s was not reached), so the agent was stopped and the task requeued to run again with a full budget. Not a task failure. The next dispatch resumes on the existing feature branch, keeping its commits. See ${log_file}."
    wd_record_branch_resume "$task_id" "$task_status"
    normalize_status_file "$task_status"
  else
    log "task ${task_id}: WARNING atomic transition to BACKLOG failed after a budget stop; status.md/marker left as they were (no partial update)"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# requeue_after_timeout_with_commits <task_id> <task_status> <log_file>
#                                    <killed_at> <owner_role> <evidence>
#
# wd_finalize_task's step after requeue_after_budget_stop declined a kill
# (the watchdog fired at max_task_seconds itself). <evidence> is the
# block_record_evidence line the per-task timeout block would record; its
# ahead=<n> token is the count of commits the feature branch holds ahead of
# the source branch. <killed_at> is the armed limit in seconds.
#
# Returns 1 -- the caller blocks the task as a per-task timeout, exactly as
# it would without this step -- when:
#   * ahead is 0 or absent: the agent committed nothing, so there is no
#     work for a requeue to resume;
#   * [overwatch] requeue_budget cannot be read (block_triage requeue-budget
#     refused) or the Block Record's requeues cannot be read: the bound
#     cannot be checked, so the task is not requeued past it. An unreadable
#     requeue_budget also leaves one operator inbox entry naming the read
#     error (_wd_requeue_budget_unreadable_notice), since the block it
#     causes is a config fault the task's own record does not name;
#   * the Block Record's requeues has reached the budget: the same bound
#     OVERWATCH's block triage applies, so a task that keeps timing out ends
#     BLOCKED with Needs Human yes, and triage then escalates it instead of
#     requeueing it again;
#   * the Block Record write or update is refused: an uncounted requeue
#     would escape the bound.
#
# Otherwise the timeout is recorded on the Block Record (reason_code
# per_task_timeout, <owner_role>, <evidence> and timeout_s=<killed_at>; a
# record the task already carries keeps its requeues, as any re-block does),
# one requeue is counted against it (requeues plus one, per_task_timeout
# appended to requeue_reasons) and its resume is set to branch -- the
# fields a block-triage requeue writes -- and the task returns to BACKLOG
# with ## Needs Human no and the outcome on its ## Summary. The next
# dispatch's worktree stage (wd_setup_task_worktree, wake_driver_worktree.sh)
# reads resume branch and attaches the existing feature branch at its tip.
# Block-storm detection and block triage never see the task: both read only
# tasks whose ## State is BLOCKED. Returns 0; a failed atomic transition to
# BACKLOG is narrated and left for the next reconciliation pass, as
# requeue_after_budget_stop does.
# ---------------------------------------------------------------------------
requeue_after_timeout_with_commits() {
  local task_id="${1:-}" task_status="${2:-}" log_file="${3:-}" killed_at="${4:-}" owner_role="${5:-}" evidence="${6:-}"
  local _ahead=0 _budget="" _requeues=""
  [[ "$evidence" =~ (^|[;[:space:]])ahead=([0-9]+) ]] && _ahead="${BASH_REMATCH[2]}"
  (( 10#$_ahead > 0 )) || return 1

  if ! _budget="$(pp_run_ops pgai_agent_kanban.overwatch.block_triage requeue-budget "$task_status")" \
      || [[ ! "$_budget" =~ ^[0-9]+$ ]]; then
    log "task ${task_id}: WARNING: [overwatch] requeue_budget could not be read (see the error above); the timed-out task is blocked rather than requeued past an unchecked bound"
    _wd_requeue_budget_unreadable_notice "$task_id" "$task_status" "$_ahead" "$_budget"
    return 1
  fi
  if ! _requeues="$(block_record_field "$task_status" requeues 0)" || [[ ! "$_requeues" =~ ^[0-9]+$ ]]; then
    log "task ${task_id}: WARNING: its Block Record requeue count could not be read; the timed-out task is blocked rather than requeued past an unchecked bound"
    return 1
  fi
  if (( _requeues >= _budget )); then
    log "task ${task_id}: per-task timeout with ${_ahead} commit(s) on its feature branch, but the requeue budget is spent (${_requeues} of ${_budget}); blocking it"
    return 1
  fi
  if ! block_record_write "$task_status" per_task_timeout "$owner_role" "$evidence" "timeout_s=${killed_at}" \
      || ! block_record_update "$task_status" --record-requeue per_task_timeout --resume branch; then
    log "task ${task_id}: WARNING: the Block Record could not count this requeue; blocking the timed-out task instead"
    return 1
  fi

  local _spent=$(( _requeues + 1 ))
  log "task ${task_id}: per-task timeout (${killed_at}s) after ${_ahead} commit(s) on its feature branch -- returning it to BACKLOG to resume on that branch (requeue ${_spent} of ${_budget})"
  if transition_task_state "$task_id" "$task_status" "BACKLOG"; then
    set_block "$task_status" "Needs Human" "no"
    set_block "$task_status" "Summary" "Returned to BACKLOG by the wake: the per-task timeout (max_task_seconds, ${killed_at}s) stopped the agent after it had committed ${_ahead} commit(s) ahead of the source branch. The next dispatch resumes on the existing feature branch, keeping those commits. Requeue ${_spent} of ${_budget} ([overwatch] requeue_budget); once it is spent, a further timeout blocks the task. See ${log_file}."
    normalize_status_file "$task_status"
  else
    log "task ${task_id}: WARNING atomic transition to BACKLOG failed after a per-task timeout; status.md/marker left as they were (no partial update)"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# _wd_requeue_budget_unreadable_notice <task_id> <task_status> <ahead> <value>
#
# The operator inbox entry for a timed-out task blocked because
# [overwatch] requeue_budget could not be read. The read error is captured
# by reading the budget once more with its stderr kept (the first read's
# stderr is already in the wake log); <value> is what the first read
# printed, named when the second read prints no error. Written with
# write --once, Source the task ID, and the question names only the task
# and the read error, so a task that times out again on the same error
# leaves one entry. Never fails; a write it cannot make is a WARNING.
# ---------------------------------------------------------------------------
_wd_requeue_budget_unreadable_notice() {
  local task_id="${1:-}" task_status="${2:-}" ahead="${3:-0}" value="${4:-}"
  local root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}" project="${_CURRENT_PROJECT:-}"
  local read_error="" entry="" scripts_dir question meanwhile how_to_answer
  read_error="$(pp_run_ops pgai_agent_kanban.overwatch.block_triage requeue-budget "$task_status" 2>&1 >/dev/null)" || true
  read_error="${read_error//$'\n'/ }"
  [[ -n "$read_error" ]] || read_error="the read printed '${value}' where a whole number belongs"
  scripts_dir="${root}/scripts"
  question="Task ${task_id} hit its per-task timeout with committed work, but the wake could not read [overwatch] requeue_budget (${read_error}), so it blocked the task instead of requeueing it past an unchecked bound. Fix requeue_budget and return the task to BACKLOG?"
  meanwhile="The wake blocked the task as a per-task timeout (## State BLOCKED, Needs Human yes) and counted no requeue on its Block Record. Its feature branch held ${ahead} commit(s) ahead of the source branch."
  how_to_answer="Set [overwatch] requeue_budget to a whole number (project.cfg, then kanban.cfg; default 3) and confirm it reads: $(printf '%q' "${scripts_dir}/lib/pp_run_ops.sh") pgai_agent_kanban.overwatch.block_triage requeue-budget $(printf '%q' "$task_status"). Then return the task: $(printf '%q' "${scripts_dir}/task-status.sh") --project ${project} --key ${task_id} --state BACKLOG --needs-human no. Close this entry: $(printf '%q' "${scripts_dir}/inbox.sh") --project ${project} close --id followed by this entry's INBOX ID."
  if entry="$(operator_inbox_notice "$root" "$project" "$task_id" "$question" "$meanwhile" "$how_to_answer")"; then
    log "task ${task_id}: operator inbox entry ${entry} names the requeue_budget read error"
  else
    log "task ${task_id}: WARNING: the operator inbox entry for the unreadable requeue_budget was not written: ${entry}"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# wd_record_branch_resume <task_id> <task_status>
#
# Records resume mode branch for the task's next dispatch, so the worktree
# stage attaches the existing feature branch and keeps every commit on it.
#
#   * The task carries a ## Block Record: its resume field is set to branch
#     (block_record_update --resume branch), exactly the field a triage
#     requeue sets; every other field, the requeue budget included, is
#     unchanged.
#   * The task carries no Block Record: the wake-owned ## Resume Mode section
#     is written (appended when absent) with the body 'branch'. No Block
#     Record is created -- a budget stop is not a block, so block triage and
#     block-storm detection have nothing to see. wd_setup_task_worktree reads
#     the section only when the task has no Block Record; once a block writes
#     a record, that record's resume field decides instead.
#   * The record cannot be read: nothing is written. The worktree stage reads
#     an unreadable record as resume mode branch already.
#
# Every failure is logged and never stops the requeue; returns 0.
# ---------------------------------------------------------------------------
wd_record_branch_resume() {
  local task_id="${1:-}" task_status="${2:-}" _reason="" _read_exit=0
  _reason="$(block_record_field "$task_status" reason_code "")" || _read_exit=$?
  if (( _read_exit != 0 )); then
    log "task ${task_id}: WARNING: its Block Record could not be read (exit ${_read_exit}); nothing recorded -- the next dispatch reads an unreadable record as resume mode branch and keeps the feature branch"
  elif [[ -n "$_reason" ]]; then
    if block_record_update "$task_status" --resume branch; then
      log "task ${task_id}: Block Record resume set to branch; the next dispatch keeps the feature branch and its commits"
    else
      log "task ${task_id}: WARNING: could not set the Block Record resume to branch; the next dispatch follows the record's existing resume mode"
    fi
  elif set_block_or_append "$task_status" "Resume Mode" "branch"; then
    log "task ${task_id}: ## Resume Mode branch recorded; the next dispatch keeps the feature branch and its commits"
  else
    log "task ${task_id}: WARNING: could not write ## Resume Mode branch to ${task_status}; the next dispatch re-creates the feature branch from the source branch"
  fi
  return 0
}
