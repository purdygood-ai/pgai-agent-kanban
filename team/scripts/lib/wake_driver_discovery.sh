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
# run_project_chain holds the project's repo lock for its whole chain. This
# stage is the one window where the chain lets go of it: discovery's guards
# and scans are read-only, and a scan of a large project can take a minute,
# so holding the lock through them keeps every other role's wake out for
# that long. wd_run_pm_discovery therefore:
#   1. releases the chain's repo lock and runs discovery_run_pipeline
#      without it. A busy chain idle-exits there before any scan
#      (discovery.sh), so the PM wake is over in seconds;
#   2. hands discovery wd_discovery_take_repo_lock as its
#      DISCOVERY_WRITE_LOCK_HOOK, so the lock is taken again -- with the
#      chain's own provenance (op wake-chain:<project>), on the chain's own
#      _project_lock_fd -- only once discovery has decided to write
#      (bundle, flip intake items, queue PM). The acquisition does not
#      wait: a lock another role took meanwhile defers discovery's write to
#      the next wake;
#   3. runs the post-discovery process_one_task only while the chain holds
#      the lock again, after pgai_lock_reverify_or_narrate confirms it at
#      the "post-discovery dispatch" phase boundary. process_one_task's own
#      merge-boundary reverification applies inside it, as for every other
#      dispatch.
# Whatever discovery's outcome, run_project_chain's end-of-chain release
# then drops the lock if this stage re-took it (pgai_lock_release is a
# no-op on the empty fd this stage leaves when it did not).
#
# Public entry points
# -------------------
#   wd_run_pm_discovery <project_name>
#       The PM branch itself; called by run_project_chain after its task
#       loop (AGENT=pm, PM queue empty or dispatch held, pm_mode not manual)
#       once it has logged why discovery runs. Logs every later decision;
#       always returns 0.
#   wd_discovery_take_repo_lock <project_name>
#       The DISCOVERY_WRITE_LOCK_HOOK: one non-blocking pgai_lock_acquire of
#       the chain's repo lock into the chain's own _project_lock_fd.
#       Returns 0 when held, non-zero when another holder has it
#       (pgai_lock_acquire narrates that holder).
#
# State shared with run_project_chain (bash dynamic scoping -- both
# functions run inside its stack frame and declare none of these):
#   reads   _project_lock_file, _dispatch_gated, STOP_REASON
#   writes  _project_lock_fd (released, then re-taken by the hook),
#           _project_lock_lost (set on a failed post-discovery
#           reverification, so the end-of-chain cleanup abandons the fd),
#           TASKS_COMPLETED
#
# Dependencies (must already be defined by the caller)
# ----------------------------------------------------
#   lock_provenance.sh  — pgai_lock_acquire, pgai_lock_release,
#                         pgai_lock_reverify_or_narrate
#   discovery.sh        — discovery_run_pipeline, DISCOVERY_LAST_STATUS,
#                         _disc_get_release_field (via wake_bootstrap.sh)
#   wake_driver.sh      — process_one_task; the reconcile/recheck/sweep
#                         helpers and wd_chain_task_timeout_budget it calls
#                         before a dispatch; log()

# ---------------------------------------------------------------------------
# wd_discovery_take_repo_lock <project_name>
# ---------------------------------------------------------------------------
wd_discovery_take_repo_lock() {
  local project_name="$1"
  [[ -n "${_project_lock_fd:-}" ]] && return 0
  pgai_lock_acquire "$_project_lock_file" "$AGENT" "$$" "wake-chain:${project_name}" "none" _project_lock_fd
}

# ---------------------------------------------------------------------------
# wd_run_pm_discovery <project_name>
# ---------------------------------------------------------------------------
wd_run_pm_discovery() {
  local project_name="$1"

  # Step 1: let go of the chain's repo lock for the read-only discovery.
  pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
  _project_lock_fd=""
  log "project ${project_name}: repo lock released for read-only discovery; discovery re-takes it only to write"

  # Step 2: discovery re-takes the lock through the hook before writing.
  local DISCOVERY_WRITE_LOCK_HOOK="wd_discovery_take_repo_lock"
  set +e
  discovery_run_pipeline "$project_name"
  set -e

  if [[ "${DISCOVERY_LAST_STATUS:-idle}" != "produced_work" ]]; then
    return 0
  fi

  # Step 3: the post-discovery dispatch, only under the re-taken lock.
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
