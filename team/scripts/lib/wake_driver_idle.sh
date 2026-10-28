#!/usr/bin/env bash
# team/scripts/lib/wake_driver_idle.sh
#
# Wake idle-pass stage: the repo-lock scope of a run_project_chain pass
# (wake_driver.sh) that has no dispatchable task. One of the single-concern
# lib stages wake_driver.sh sources, kept separate so the driver stays under
# the engine-decomposition line ceiling (team/scripts/lint_engine_size_dupe.py).
#
# Repo-lock scope
# ---------------
# With no dispatchable task, and no discovery scan to come (PM's discovery
# branch, wake_driver_discovery.sh, keeps the chain's hold through its scan
# and writes, so a PM pass in automatic mode keeps the lock throughout), the
# pass holds the repo lock only for the housekeeping that touches the dev
# tree and worktrees (reconcile_stale_active,
# reconcile_terminal_worktrees and reconcile_terminal_liveness_records, run
# before the model preflight), then releases it before the WAITING/BLOCKED
# re-checks and the intake census, which read and write task and intake
# state only. Their scans run unlocked,
# here and again after the chain's task loop, in the same order and with the
# same lines as a locked pass; each write they make -- a WAITING promotion,
# a BLOCKED promotion, an intake-item closure -- goes through
# wd_queue_write_under_lock, which retakes the repo lock for that one write
# and releases it straight after, so no two writers interleave on a queue
# even when the per-(project, agent) wake-batch guard is bypassed. When a
# write cannot get the lock it is not made, nor is any later write in this
# chain, and the pass ends; the next firing retries. A re-check that makes a
# task dispatchable sends the pass back for the lock, and the task runs
# under it exactly as on a busy pass.
#
# Public entry points
# -------------------
#   wd_run_idle_pass <project_name>
#       Called by run_project_chain, in its frame (bash dynamic scoping),
#       before its task loop; a no-op unless the pass is idle (no
#       dispatchable task, dispatch not held, no discovery to come). Sets
#       STOP_REASON and _idle_pass_done (true: the pass is over; false: it
#       retook the lock and the loop dispatches), and the chain's
#       _project_lock_* state.
#   _wd_release_chain_lock <project_name>
#       The chain's one release of its repo lock (abandon when it was lost
#       mid-chain); logs the measured hold. A second call is a no-op.
#   _wd_chain_lock_held_for <acquired_epochrealtime>
#       The hold as seconds with two decimals, for that line.
#   wd_queue_write_under_lock <task|none> <description> <command> [args...]
#       One re-check or census write (reached through wake_queue_write,
#       wake_task_state.sh): run as-is while the chain holds its repo lock,
#       under a retaken lock after the idle pass released it. 75 when the
#       write was not made for want of the lock.

# ---------------------------------------------------------------------------
# _wd_chain_lock_held_for <acquired_epochrealtime>
# Echo how long the chain has held its repo lock, as seconds with two
# decimals ("0.43"), from the $EPOCHREALTIME taken when it was acquired; ""
# when that is unknown. Read by the "repo lock released" line.
# ---------------------------------------------------------------------------
_wd_chain_lock_held_for() {
  local since="${1:-}" now="${EPOCHREALTIME:-}"
  [[ "$since" =~ ^[0-9]+[.,][0-9]{6}$ && "$now" =~ ^[0-9]+[.,][0-9]{6}$ ]] || return 0
  local us=$(( 10#${now//[.,]/} - 10#${since//[.,]/} ))
  (( us < 0 )) && us=0
  printf '%d.%02d\n' $(( us / 1000000 )) $(( (us % 1000000) / 10000 ))
}

# ---------------------------------------------------------------------------
# _wd_release_chain_lock <project_name>
# The chain's one release of its repo lock, in run_project_chain's frame
# (dynamic scoping: _project_lock_file, _project_lock_fd, _project_lock_lost,
# _project_lock_held, _project_lock_acquired_at). Logs the measured hold. A
# second call is a no-op.
#
# A mid-chain reverification loss (_project_lock_lost, set by
# process_one_task / wd_finalize_task) means the on-disk lock file no longer
# belongs to this process — some other chain reaped and reacquired it while
# this one kept running. Releasing it normally would truncate/remove that
# new holder's own record (pgai_lock_release's documented contract assumes
# the caller still legitimately owns the path); pgai_lock_abandon is the
# release-shaped counterpart that only closes this process's own fd and
# drops its bookkeeping entry, never touching the file.
# ---------------------------------------------------------------------------
_wd_release_chain_lock() {
  local project_name="$1"
  [[ "${_project_lock_held:-false}" == "true" ]] || return 0
  local held
  held="$(_wd_chain_lock_held_for "${_project_lock_acquired_at:-}")"
  if [[ "${_project_lock_lost:-false}" == "true" ]]; then
    log "project ${project_name}: repo lock ownership was lost mid-chain; abandoning this process's own fd without touching the lock file (another chain owns it now)"
    pgai_lock_abandon "$_project_lock_file" "$_project_lock_fd"
  else
    pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
    log "project ${project_name}: repo lock released${held:+ (held ${held}s)}"
  fi
  _project_lock_held=false
}

# Set true by wd_queue_write_under_lock when a write could not take the repo
# lock; every later write in the same chain is then skipped. Reset by
# wd_run_idle_pass, which every run_project_chain calls once, first.
_WD_QUEUE_WRITES_DEFERRED=false

# ---------------------------------------------------------------------------
# wd_queue_write_under_lock <task|none> <description> <command> [args...]
# Run one queue or status write from the WAITING/BLOCKED re-checks or the
# intake census. Outside run_project_chain, or while the chain still holds
# its repo lock (dynamic scoping: _project_lock_file, _project_lock_held),
# <command> runs as-is. After the idle pass released the lock, the write
# retakes it first through wlw_acquire_chain_lock -- holder $AGENT, pid $$,
# op wake-chain:<project>, task <task> (the literal none for a write not
# scoped to one task), waiting only as the chain's own retake would --
# runs <command>, releases the lock at once, and logs the hold. When the
# lock cannot be taken, <command> does not run, the chain is marked so
# every later write is skipped too, and 75 is returned: nothing is
# half-written, and the next firing retries. Otherwise returns <command>'s
# status.
# ---------------------------------------------------------------------------
wd_queue_write_under_lock() {
  local task="$1" what="$2"
  shift 2
  local _wq_rc=0
  if [[ -z "${_project_lock_file:-}" || "${_project_lock_held:-true}" == "true" ]]; then
    "$@" || _wq_rc=$?
    return "$_wq_rc"
  fi
  local project="${_CURRENT_PROJECT:-}"
  if [[ "$_WD_QUEUE_WRITES_DEFERRED" == "true" ]]; then
    log "project ${project}: idle pass: ${what} not written -- an earlier write this pass could not take the repo lock; the next firing retries"
    return 75
  fi
  local _wq_fd="" _wq_at
  wlw_acquire_chain_lock "$project" "$_project_lock_file" _wq_fd 0 "$task" || _wq_rc=$?
  if [[ $_wq_rc -ne 0 ]]; then
    _WD_QUEUE_WRITES_DEFERRED=true
    log "project ${project}: idle pass: ${what} not written -- repo lock held by $(pgai_lock_describe_provenance "$(pgai_lock_read_provenance "$_project_lock_file")"); the pass ends without its remaining writes and the next firing retries"
    return 75
  fi
  _wq_at="${EPOCHREALTIME:-}"
  "$@" || _wq_rc=$?
  pgai_lock_release "$_project_lock_file" "$_wq_fd"
  log "project ${project}: idle pass: ${what} made under the retaken repo lock (held $(_wd_chain_lock_held_for "$_wq_at")s, exit ${_wq_rc})"
  return "$_wq_rc"
}

# ---------------------------------------------------------------------------
# wd_run_idle_pass <project_name>
# See the file header. Always returns 0.
# ---------------------------------------------------------------------------
wd_run_idle_pass() {
  local project_name="$1"
  _WD_QUEUE_WRITES_DEFERRED=false
  [[ "$_dispatch_gated" == "false" && "$_no_dispatchable_task" == "true" ]] || return 0
  [[ "$AGENT" != "pm" || "${PGAI_KANBAN_PM_MODE:-automatic}" == "manual" ]] || return 0
  if ! pgai_lock_reverify_or_narrate "$_project_lock_file" "$$" "agent dispatch" "wake-chain:${project_name}" "none"; then
    _project_lock_lost=true
    STOP_REASON="chain lost repo lock ownership mid-run at a mutating phase boundary -- stopping cleanly (see narration above)"
    _idle_pass_done=true
  else
    _wd_release_chain_lock "$project_name"
    log "project ${project_name}: idle pass: no dispatchable task; the WAITING/BLOCKED re-checks and the intake census scan without the repo lock and retake it for each write"
    recheck_waiting_tasks
    recheck_blocked_tasks
    sweep_running_intake_census
    _idle_pass_done=true
    if [[ "$AGENT" == "overwatch" && -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
      log "HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH — stopping batch loop cleanly"
      STOP_REASON="HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH"
    elif [[ "$AGENT" != "overwatch" && -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — stopping batch loop cleanly"
      STOP_REASON="HALT flag detected at ${TEAM_ROOT}/HALT"
    elif [[ "$_WD_QUEUE_WRITES_DEFERRED" == "true" ]]; then
      STOP_REASON="repo lock held elsewhere when an idle-pass write retook it; the writes not made wait for the next firing"
    elif [[ -n "$(get_first_pending_task || true)" ]]; then
      log "project ${project_name}: idle pass: a re-check made a ${AGENT} task dispatchable; retaking the repo lock to dispatch it"
      # No <others_pending> count: this retake waits for a wake-chain holder
      # (within the project's remaining lock_wait_seconds budget) even while
      # other projects remain in the batch, because a failure here ends the
      # pass with 0 and the end-of-iteration retry pass never revisits it.
      set +e
      wlw_acquire_chain_lock "$project_name" "$_project_lock_file" _project_lock_fd
      _project_lock_rc=$?
      set -e
      if [[ $_project_lock_rc -eq 0 ]]; then
        _project_lock_held=true
        _project_lock_acquired_at="${EPOCHREALTIME:-}"
        _idle_pass_done=false
      else
        STOP_REASON="repo lock held by another chain after the idle re-checks; the dispatchable ${AGENT} task waits for the next firing"
      fi
    else
      log "no pending Claude task found in ${AGENT} queue"
      STOP_REASON="no more BACKLOG tasks in ${AGENT} queue"
    fi
  fi
  return 0
}
