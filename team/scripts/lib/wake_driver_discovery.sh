#!/usr/bin/env bash
# team/scripts/lib/wake_driver_discovery.sh
#
# Wake discovery stage: the PM wake's discovery branch at the end of
# run_project_chain (wake_driver.sh) -- the discovery pipeline run when the
# PM queue is empty (or dispatch is held by the blocked-task guard), and the
# one post-discovery dispatch when discovery produced work. One of the
# single-concern lib stages wake_driver.sh sources, kept separate so the
# driver stays under the engine-decomposition line ceiling
# (team/scripts/lint_engine_size_dupe.py).
#
# Repo-lock scope
# ---------------
# run_project_chain holds the project's repo lock for its whole chain, and
# this stage keeps it: discovery's guards, its scans, its writes and the
# post-discovery dispatch all run under the lock the chain already holds,
# so a discovery that finds work writes it in the same firing whatever the
# other roles' wakes are doing. A busy chain idle-exits before any scan
# (discovery.sh), so the PM wake is over in seconds; an idle chain has no
# other role with real work for the project, so holding the lock through
# the scan delays only their idle passes. wd_run_pm_discovery therefore:
#   1. runs discovery_run_pipeline with wd_discovery_confirm_repo_lock as
#      its DISCOVERY_WRITE_LOCK_HOOK, so discovery's write phase uses the
#      chain's own hold and never acquires the lock a second time;
#   2. runs the post-discovery process_one_task only after
#      pgai_lock_reverify_or_narrate confirms the chain still holds the
#      lock at the "post-discovery dispatch" phase boundary.
#      process_one_task's own merge-boundary reverification applies inside
#      it, as for every other dispatch.
# Whatever discovery's outcome, run_project_chain's end-of-chain release
# then drops the lock exactly once.
#
# Public entry points
# -------------------
#   wd_run_pm_discovery <project_name>
#       The PM branch itself; called by run_project_chain after its task
#       loop (AGENT=pm, PM queue empty or dispatch held, pm_mode not manual)
#       once it has logged why discovery runs. Logs every later decision;
#       always returns 0.
#   wd_discovery_confirm_repo_lock <project_name>
#       The DISCOVERY_WRITE_LOCK_HOOK: returns 0 when the chain's
#       _project_lock_fd is set and the lock's provenance still names this
#       process, non-zero (narrated) otherwise. It never acquires.
#
# State shared with run_project_chain (bash dynamic scoping -- both
# functions run inside its stack frame and declare none of these):
#   reads   _project_lock_file, _project_lock_fd, _dispatch_gated,
#           STOP_REASON
#   writes  _project_lock_lost (set when a reverification finds the lock
#           gone, so the end-of-chain cleanup abandons the fd),
#           TASKS_COMPLETED
#
# Dependencies (must already be defined by the caller)
# ----------------------------------------------------
#   lock_provenance.sh  — pgai_lock_reverify_or_narrate
#   discovery.sh        — discovery_run_pipeline, DISCOVERY_LAST_STATUS,
#                         _disc_get_release_field (via wake_bootstrap.sh)
#   wake_driver.sh      — process_one_task; the reconcile/recheck/sweep
#                         helpers and wd_chain_task_timeout_budget it calls
#                         before a dispatch; log()

# ---------------------------------------------------------------------------
# wd_discovery_confirm_repo_lock <project_name>
# ---------------------------------------------------------------------------
wd_discovery_confirm_repo_lock() {
  local project_name="$1"
  if [[ -z "${_project_lock_fd:-}" ]]; then
    log "project ${project_name}: discovery write: this chain holds no repo lock; not writing"
    return 1
  fi
  if ! pgai_lock_reverify_or_narrate "$_project_lock_file" "$$" "discovery write" "wake-chain:${project_name}" "none"; then
    _project_lock_lost=true
    log "project ${project_name}: discovery write: repo lock ownership lost; not writing"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# wd_run_pm_discovery <project_name>
# ---------------------------------------------------------------------------
wd_run_pm_discovery() {
  local project_name="$1"

  # Step 1: discovery under the chain's own hold; the hook only confirms it.
  log "project ${project_name}: repo lock kept through discovery; the discovery write uses the chain's hold"
  local DISCOVERY_WRITE_LOCK_HOOK="wd_discovery_confirm_repo_lock"
  set +e
  discovery_run_pipeline "$project_name"
  set -e

  if [[ "${DISCOVERY_LAST_STATUS:-idle}" != "produced_work" ]]; then
    return 0
  fi

  # Step 2: the post-discovery dispatch, only while the chain still holds the lock.
  if [[ "$_dispatch_gated" == "true" ]]; then
    log "project ${project_name}: post-discovery iteration: blocked-task guard active, skipping task dispatch"
    return 0
  fi
  if [[ -f "${TEAM_ROOT}/HALT" ]]; then
    log "project ${project_name}: post-discovery iteration: HALT flag now present, skipping"
    return 0
  fi
  local _post_disc_active_rc
  _post_disc_active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || echo "none")"
  if [[ -n "$_post_disc_active_rc" && "$_post_disc_active_rc" != "none" ]]; then
    log "project ${project_name}: post-discovery iteration: Active RC=${_post_disc_active_rc}, skipping (safety gate)"
    return 0
  fi
  if [[ -z "${_project_lock_fd:-}" ]]; then
    log "project ${project_name}: post-discovery iteration: repo lock not held, skipping task dispatch (the next PM wake dispatches it)"
    return 0
  fi
  if ! pgai_lock_reverify_or_narrate "$_project_lock_file" "$$" "post-discovery dispatch" "wake-chain:${project_name}" "none"; then
    _project_lock_lost=true
    log "project ${project_name}: post-discovery iteration: repo lock ownership lost, skipping task dispatch"
    return 0
  fi

  log "project ${project_name}: post-discovery iteration: chain idle and discovery produced work, processing"
  reconcile_stale_active
  reconcile_terminal_worktrees
  reconcile_terminal_liveness_records
  recheck_waiting_tasks
  recheck_blocked_tasks
  sweep_running_intake_census
  wd_chain_task_timeout_budget
  local _post_disc_exit
  set +e
  process_one_task
  _post_disc_exit=$?
  set -e
  if [[ "$_post_disc_exit" -eq 0 ]]; then
    TASKS_COMPLETED=$(( TASKS_COMPLETED + 1 ))
    log "project ${project_name}: post-discovery iteration: task completed"
  else
    log "project ${project_name}: post-discovery iteration: no task to process (process_one_task returned ${_post_disc_exit})"
  fi
  return 0
}
