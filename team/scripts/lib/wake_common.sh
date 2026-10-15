#!/usr/bin/env bash
# team/scripts/lib/wake_common.sh
# Shared substrate driver for all wake scripts (wake/claude.sh, wake/codex.sh).
#
# Source this file AFTER sourcing the provider-specific lib (which must
# implement the five-function provider interface — env preflight, model
# preflight, dispatch, output parse, dispatch-refused; see provider_claude.sh's
# documented contract) and AFTER argument parsing and provider checking are
# complete in the calling wake script.
#
# Contract: the following variables MUST be set by the caller before sourcing:
#   AGENT       — the agent name (pm, coder, writer, etc.)
#   SLEEP       — seconds to sleep before work (0 or more)
#   _CLI_MAX_TASKS — optional CLI override for MAX_TASKS_PER_WAKE (may be "")
#   TEAM_ROOT   — resolved kanban root path
#   SCRIPT_DIR  — absolute path to the directory containing the wake script
#
# process_one_task and run_project_chain (the shared pipeline that reaches
# the provider interface) live in lib/wake_driver.sh, sourced separately by
# the calling wake script after this file and the provider lib.
#
# This file is now a thin driver over six sourced lib stages, each split
# out so it stays a self-contained file under 800 lines with a documented
# public entry point; see each stage's own header for its full function
# list and dependencies.
# Sourced in this order (the order is load-bearing — see each file's own
# "Dependencies" section):
#   1. wake_bootstrap.sh      — env/config/secrets sourcing, tunables,
#                               path/lock resolution, debug/training gates,
#                               and the log() function every later stage uses.
#   2. semver.sh              — semver_from_filename / semver_gt, needed by
#                               wake_autonomous_scan.sh.
#   3. wake_autonomous_scan.sh — the discovery pipeline's autonomous
#                               requirements-queue scan and bug-bundling scan
#                               (get_release_state_field, scan_requirements_queue,
#                               scan_and_bundle_bugs, drop_self_pm_ticket).
#   4. wake_readme_meta.sh    — task-README field readers used during wake
#                               dispatch (get_role_from_readme,
#                               get_assigned_agent_from_readme,
#                               role_is_valid_agent, get_model_override_from_readme).
#   5. wake_task_state.sh     — status-file readers/writers, prerequisite
#                               checks, state transitions, WAITING/BLOCKED
#                               promotion and recheck, and the
#                               task-liveness primitives the reconciliation
#                               sweeps read.
#   6. wake_reconcile_sweeps.sh — reconcile_stale_active and
#                               reconcile_terminal_worktrees, the two
#                               once-per-wake, once-per-project
#                               reconciliation sweeps. Depends on
#                               wake_task_state.sh's mark_backlog/get_state/
#                               task_liveness_* primitives, so sourced
#                               immediately after it.
#   7. wake_task_transition.sh — transition_task_state, the single write
#                               path that moves a task's ## State and its
#                               queue marker together. Depends on
#                               wake_task_state.sh's set_state/mark_backlog,
#                               so sourced immediately after it.
#   8. wake_lifecycle.sh      — post-exit WORKING handling
#                               (handle_working_after_exit), the CODER/WRITER
#                               completion gate (coder_writer_completion_gate),
#                               and finalize=report intake closure
#                               (close_intake_on_finalize_report,
#                               sweep_running_intake_census). Depends on
#                               wake_task_state.sh's set_state/set_block/
#                               normalize_status_file and
#                               wake_task_transition.sh's
#                               transition_task_state, so sourced after both.
#   9. wake_project.sh        — feature-branch naming/README helpers, the
#                               HALT-AFTER sentinel check
#                               (_check_halt_after_for_project), the
#                               per-project dev-tree gate
#                               (_check_project_dev_tree), and the OVERWATCH
#                               resolution-stage call site
#                               (run_overwatch_resolution_stage).
#   10. wake_blocked_ledger.sh — the blocked-interval ledger adapter
#                               (pgai_stamp_halt_boundary,
#                               pgai_stamp_fleet_halt_boundary) called from
#                               the HALT fast-exit and per-project HALT
#                               checks below. Depends on
#                               _disc_get_release_field, already sourced
#                               transitively via wake_bootstrap.sh's own
#                               discovery.sh source line.
#
# This file's own remaining code is the two functions every wake script
# calls directly -- wake_common_preflight (script-level preflight) and
# wake_common_run (the HALT check, stagger sleep, and multi-project
# iteration loop) -- plus wake_stop_file_present, the operator stop-file
# check both wake_common_run and run_project_chain read. wake_common_run's
# end-of-iteration retry pass also reports each project that ends the
# firing still deferred on repo-lock contention to the lock-starvation
# guard (wlw_starvation_note_deferral, lib/wake_lock_wait.sh -- sourced by
# wake_driver.sh, which every wake script sources before calling
# wake_common_run).
#
# Exported functions (public API for wake scripts):
#   wake_common_preflight — script-level preflight checks.
#   wake_common_run       — main entry point; sets up env, runs
#                           multi-project loop.
#   wake_stop_file_present — true while the operator's stop file exists;
#                           the batch-level stop condition wake_common_run
#                           and run_project_chain both read.
#
# Provider-neutral substrate shared by provider_claude.sh,
# provider_codex.sh, and future provider scripts.

# --- Source bootstrap stage (env/config/secrets sourcing, tunables,
# path/lock resolution, debug/training gates) ---
# shellcheck source=scripts/lib/wake_bootstrap.sh
source "${SCRIPT_DIR}/../lib/wake_bootstrap.sh"

# --- Source semver helpers (needed by wake_autonomous_scan.sh) ---
source "${SCRIPT_DIR}/../lib/semver.sh"

# --- Source autonomous-scan stage (requirements-queue scan, bug-bundling scan) ---
# shellcheck source=scripts/lib/wake_autonomous_scan.sh
source "${SCRIPT_DIR}/../lib/wake_autonomous_scan.sh"

# --- Source README-metadata stage (dispatch field readers) ---
# shellcheck source=scripts/lib/wake_readme_meta.sh
source "${SCRIPT_DIR}/../lib/wake_readme_meta.sh"

# --- Source task-state stage (status-file readers/writers, prerequisite
# checks, state transitions, WAITING/BLOCKED promotion and recheck) ---
# shellcheck source=scripts/lib/wake_task_state.sh
source "${SCRIPT_DIR}/../lib/wake_task_state.sh"

# --- Source the reconciliation-sweeps stage (reconcile_stale_active,
# reconcile_terminal_worktrees) -- split out of wake_task_state.sh to keep
# both files under the engine-decomposition line ceiling. Depends on
# wake_task_state.sh's mark_backlog/get_state/task_liveness_* primitives,
# so sourced immediately after it. ---
# shellcheck source=scripts/lib/wake_reconcile_sweeps.sh
source "${SCRIPT_DIR}/../lib/wake_reconcile_sweeps.sh"

# --- Source task-transition stage (the single write path that moves a
# task's ## State and queue marker together). Depends on wake_task_state.sh's
# set_state/mark_backlog, so sourced immediately after it. ---
# shellcheck source=scripts/lib/wake_task_transition.sh
source "${SCRIPT_DIR}/../lib/wake_task_transition.sh"

# --- Source lifecycle stage (post-exit WORKING handling, CODER/WRITER
# completion gate, finalize=report intake closure). Depends on
# wake_task_state.sh's set_state/set_block/normalize_status_file and
# wake_task_transition.sh's transition_task_state, so sourced after both. ---
# shellcheck source=scripts/lib/wake_lifecycle.sh
source "${SCRIPT_DIR}/../lib/wake_lifecycle.sh"
# --- Provider usage-limit pause: recorded post-exit, checked at dispatch. ---
# shellcheck source=scripts/lib/wake_provider_pause.sh
source "${SCRIPT_DIR}/../lib/wake_provider_pause.sh"

# --- Source the shared halt_after CLI invocation helper (scope-root
# resolution + `python3 -m halt_after` call) before wake_project.sh, which
# depends on it for _check_halt_after_for_project. ---
# shellcheck source=scripts/lib/halt_after_invoke.sh
source "${SCRIPT_DIR}/../lib/halt_after_invoke.sh"

# --- Source per-project stage (feature-branch/README helpers, HALT-AFTER
# check, per-project dev-tree gate, OVERWATCH resolution-stage call site) ---
# shellcheck source=scripts/lib/wake_project.sh
source "${SCRIPT_DIR}/../lib/wake_project.sh"

# --- Source the blocked-interval ledger adapter (pgai_stamp_halt_boundary,
# pgai_stamp_fleet_halt_boundary), called from the HALT checks below.
# Depends on _disc_get_release_field (discovery_guards.sh, sourced
# transitively by wake_bootstrap.sh's own discovery.sh source line), so
# sourced after wake_bootstrap.sh -- which every earlier source line here
# already is. ---
# shellcheck source=scripts/lib/wake_blocked_ledger.sh
source "${SCRIPT_DIR}/../lib/wake_blocked_ledger.sh"

# ---------------------------------------------------------------------------
# wake_common_preflight
# Script-level preflight checks. Called by the wake script after sourcing
# this file and the provider lib, before wake_common_run.
# ---------------------------------------------------------------------------
wake_common_preflight() {
  # Provider CLI is checked by the provider lib (it knows which binary to check).
  # Python3 is a substrate requirement.
  # BLOCK-WORTHY: every wake-substrate script below this point assumes a
  # working python3 on PATH; a missing interpreter is caught here before any
  # of that substrate runs, rather than failing opaquely partway through it.
  # CENSUS-ANCHOR: wake-common-01
  command -v python3 >/dev/null 2>&1 || { log "python3 not found in PATH"; exit 1; }
}

# ---------------------------------------------------------------------------
# wake_stop_file_present
# True while the operator's stop file (STOP_FILE; kanban.cfg [wake]
# stop_file) exists. The file is operator-owned and shared by every wake
# batch on the host: each batch, for every agent and every registered
# project, stops at its own next task boundary while the file exists, and
# nothing in the chain ever removes it -- the operator deletes it to let
# dispatch resume. The boundaries are: before each task within a project
# (run_project_chain, lib/wake_driver.sh), before each project, and before
# the end-of-iteration contention retry pass (both in wake_common_run
# below); a wake waiting for a wake chain's repo lock also stops waiting at
# its next attempt (lib/wake_lock_wait.sh). Call it only inside an `if` condition: this file runs under
# `set -e`, and a bare false call would trip errexit.
# ---------------------------------------------------------------------------
wake_stop_file_present() {
  [[ -n "${STOP_FILE:-}" && -f "$STOP_FILE" ]]
}

# ---------------------------------------------------------------------------
# wake_common_run
# Main entry point. Runs the agent-aware HALT check, stagger sleep, and
# multi-project iteration loop.
# ---------------------------------------------------------------------------
wake_common_run() {
  # --- Agent-aware HALT flag check at startup ---
  if [[ "$AGENT" == "overwatch" ]]; then
    if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
      log "HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH — exiting cleanly without processing tasks"
      exit 0
    fi
    # The fleet-wide HALT stops OVERWATCH's wake too: block triage, the
    # resolution stage and task processing all change state, and an
    # operator stopping every project has asked for none of that. CM never
    # writes this file (its ship-policy HALT is project-scoped), so there
    # is nothing here for the resolution stage to resolve. The fleet
    # blocked-interval ledger is stamped by the other agents' wakes, not
    # here, so the overwatch schedule adds no boundaries of its own.
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — exiting cleanly without block triage, resolution stage or task processing for any project"
      # BLOCK-WORTHY: an invariant by design -- an operator's fleet-wide
      # stop; only the operator removes it, and the wake exits 0.
      # CENSUS-ANCHOR: wake-common-overwatch-halt-01
      pgai_emit_error_contract "wake.overwatch_fix_deferred_halt_present" \
        "fix deferred: HALT present at ${TEAM_ROOT}/HALT; the OVERWATCH wake changed nothing for any project. Fixes resume once the operator removes the HALT." \
        "cat ${TEAM_ROOT}/HALT"
      exit 0
    fi
  else
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — exiting cleanly without processing tasks"
      # Fleet-scoped HALT blocks every project's own active RC at once —
      # stamp a block-start for each one (best-effort; a no-op once a
      # prior cycle already opened it). CENSUS CLASS CONVERT: this must
      # never affect the HALT skip/exit it rides beside.
      pgai_stamp_fleet_halt_boundary "halt:global" start
      exit 0
    fi
    # HALT absent: close any block-start a prior cycle opened for this
    # same fleet-scoped source, for every project. A no-op when nothing
    # was ever opened. See wake_blocked_ledger.sh's own header for why
    # this attempt-every-cycle shape needs no separate "was it open last
    # cycle" state of its own.
    pgai_stamp_fleet_halt_boundary "halt:global" end
  fi

  # --- Sub-minute stagger sleep ---
  if [[ "$SLEEP" -gt 0 ]]; then
    log "sleeping ${SLEEP}s before work (--sleep stagger)"
    sleep "$SLEEP"
  fi

  # Capture post-stagger work begin epoch; excludes the stagger from work_delta.
  WAKE_WORK_BEGIN_EPOCH=$(date +%s)

  # --- Multi-project iteration ---
  log "starting multi-project iteration (agent=${AGENT})"

  _ITER_PROJECT_COUNT=0
  # Projects run_project_chain declined this firing because another agent
  # held the repo lock (return code 3 — see the contract comment above
  # run_project_chain in lib/wake_driver.sh). Collected here so the
  # end-of-iteration retry pass below knows which projects to give a second
  # look, after every project in projects_cfg_list has had its ordinary turn.
  declare -a _CONTENDED_PROJECTS=()
  # The subset that ends the firing still deferred on that contention --
  # not retried for lack of batch budget, or still contended on the retry.
  # Each is reported to the lock-starvation guard once, after the retry
  # pass. A project the stop file or a HALT kept from its retry is not:
  # that deferral is the operator's, not the lock's.
  declare -a _LOCK_DEFERRED_PROJECTS=()

  while IFS= read -r _iter_project; do
    [[ -z "$_iter_project" ]] && continue

    # --- Operator stop file: the between-projects boundary ---
    # Checked before every project, the first included, so a file touched
    # while an earlier project's chain was mid-task stops the whole batch
    # here instead of only that project's own task loop, and a batch that
    # starts while the file already exists dispatches nothing at all. The
    # file is left in place -- see wake_stop_file_present.
    if wake_stop_file_present; then
      log "stop file present at ${STOP_FILE} -- stopping the batch before project ${_iter_project} (every agent's batch stops at its next task boundary while the file exists; remove it to resume dispatch)"
      break
    fi

    # --- Disabled project: projects.cfg enabled=false ---
    # Skipped for every agent, OVERWATCH's daily wake included, before any
    # HALT stamp, lock, discovery, agent or provider call -- one line per
    # firing. projects_cfg_enabled (lib/projects.sh) is the one rule;
    # `why` reads the same one, so it reports this project IDLE.
    if ! projects_cfg_enabled "$_iter_project"; then
      log "$(projects_cfg_disabled_skip_line "$_iter_project")"
      continue
    fi

    # --- HALT fast-exit: skip project if an existing HALT sentinel is present ---
    # This honors an ALREADY-promoted HALT file so the wake exits fast without
    # spinning up the agent.  It does NOT evaluate HALT-AFTER drain/promotion —
    # that decision is made AFTER the agent's work loop (see below).
    #
    # Overwatch carve-out: every other agent is fully skipped here, unchanged.
    # Overwatch alone is let through to run_project_chain so
    # run_overwatch_resolution_stage can classify the file itself (a
    # ship-policy HALT vs. an operator-authored one) and narrate its own
    # decline reason instead of being shadowed by this coarser gate.
    # run_project_chain still refuses to do any further task processing for a
    # halted project once the resolution stage has had its look — see the
    # per-project HALT check placed immediately after run_overwatch_resolution_stage
    # in run_project_chain (claude.sh / codex.sh) — so this carve-out narrows
    # only "does the resolution stage get to classify the HALT", never "does
    # a halted project still get worked".
    if pp_project_halted "$_iter_project" 2>/dev/null; then
      # Best-effort; never affects the skip/carve-out decision below.
      pgai_stamp_halt_boundary "$_iter_project" "halt:project" start
      if [[ "$AGENT" != "overwatch" ]]; then
        log "project ${_iter_project}: per-project HALT present, skipping"
        continue
      fi
      log "project ${_iter_project}: per-project HALT present; overwatch carve-out — entering chain so the resolution stage can classify it"
    else
      # HALT absent: close any block-start a prior cycle opened for this
      # project's own "halt:project" source. No-op when nothing was ever
      # opened -- see wake_blocked_ledger.sh's own header.
      pgai_stamp_halt_boundary "$_iter_project" "halt:project" end
    fi

    export PGAI_PROJECT_ROOT="$(pp_project_root "$_iter_project")"
    export PGAI_PROJECT_NAME="$_iter_project"
    export PGAI_TASKS_DIR="$(pp_tasks_dir "$_iter_project")"

    _ITER_PROJECT_COUNT=$(( _ITER_PROJECT_COUNT + 1 ))
    log "project ${_iter_project}: beginning chain (project ${_ITER_PROJECT_COUNT})"

    # set +e/-e: run_project_chain returns 3 on repo-lock contention (see
    # its contract comment in lib/wake_driver.sh), and this script runs
    # under `set -e` — a bare call whose exit status is read on the next
    # line does not survive errexit, so the non-zero return must be taken
    # inside a set +e/set -e bracket, the same idiom process_one_task's own
    # callers already use in wake_driver.sh.
    set +e
    run_project_chain "$_iter_project"
    _rpc_exit=$?
    set -e

    if [[ $_rpc_exit -eq 3 ]]; then
      _CONTENDED_PROJECTS+=("$_iter_project")
    fi

    # --- HALT-AFTER post-work check ---
    # Evaluate whether the HALT-AFTER drain condition is satisfied NOW that the
    # agent has drained its actionable queue for this project this wake.  Placing
    # the check here (after run_project_chain) prevents premature promotion where
    # the drain was assessed before the agent picked up its pending task, causing
    # HALT-AFTER to fire just before the agent did its work.
    #
    # The call MUST live in the `if` condition: _check_halt_after_for_project
    # returns non-zero (1/2/3) in the common cases, and wake_common.sh runs under
    # `set -e`.  A bare call on its own line would trip errexit and kill the wake
    # on every project that has no HALT-AFTER sentinel.  A function in an `if`
    # condition is exempt from errexit — this is the safe idiom.
    if _check_halt_after_for_project "$_iter_project"; then
      log "project ${_iter_project}: HALT-AFTER promoted after work loop — halted for next wake"
    fi

  done < <(projects_cfg_list 2>/dev/null)

  # --- End-of-iteration contention retry pass ---
  # Once means once: exactly one retry attempt per project that
  # run_project_chain declined this firing for repo-lock contention (return
  # code 3 — see the contract comment above run_project_chain in
  # lib/wake_driver.sh), after every project in projects_cfg_list has had
  # its ordinary turn and the contending holder has had the rest of the
  # iteration to finish. This pass itself never loops, polls, or takes a
  # blocking flock — it calls run_project_chain exactly once per contended
  # project. That call takes the lock the way the first pass did
  # (wlw_acquire_chain_lock, lib/wake_lock_wait.sh): non-blocking attempts,
  # with a backoff wait for a wake-chain holder bounded by what is left of
  # the project's ONE lock_wait_seconds budget for this firing -- usually
  # nothing once the first pass waited it out, so the retry is then a
  # single attempt -- and by the remaining batch runtime budget.
  if (( ${#_CONTENDED_PROJECTS[@]} > 0 )); then
    _RETRY_ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
    # The operator stop file is a task boundary for the retry pass too: a
    # retry dispatches tasks exactly as the first pass does, so a file that
    # appeared during the first pass (or during an earlier retry, below)
    # performs no retry. Checked ahead of the runtime budget so the log
    # names the operator's stop, not an incidental budget reading.
    if wake_stop_file_present; then
      log "retry pass: stop file present at ${STOP_FILE} -- performing no retry for ${#_CONTENDED_PROJECTS[@]} contended project(s): ${_CONTENDED_PROJECTS[*]}"
    elif (( _RETRY_ELAPSED >= MAX_RUNTIME_SECONDS )); then
      log "retry pass: batch runtime budget already exhausted (elapsed=${_RETRY_ELAPSED}s, cap=${MAX_RUNTIME_SECONDS}s) -- performing no retry for ${#_CONTENDED_PROJECTS[@]} contended project(s): ${_CONTENDED_PROJECTS[*]}"
      _LOCK_DEFERRED_PROJECTS=("${_CONTENDED_PROJECTS[@]}")
    else
      log "retry pass: ${#_CONTENDED_PROJECTS[@]} project(s) were contended on the first pass, retrying: ${_CONTENDED_PROJECTS[*]}"

      for _retry_index in "${!_CONTENDED_PROJECTS[@]}"; do
        _retry_project="${_CONTENDED_PROJECTS[$_retry_index]}"
        if wake_stop_file_present; then
          log "retry pass: stop file present at ${STOP_FILE} -- skipping remaining retries (next would have been project ${_retry_project})"
          break
        fi
        _RETRY_ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
        if (( _RETRY_ELAPSED >= MAX_RUNTIME_SECONDS )); then
          log "retry pass: batch runtime budget reached mid-retry (elapsed=${_RETRY_ELAPSED}s, cap=${MAX_RUNTIME_SECONDS}s) -- skipping remaining retries"
          _LOCK_DEFERRED_PROJECTS+=("${_CONTENDED_PROJECTS[@]:$_retry_index}")
          break
        fi

        # Re-evaluate the per-project HALT fast-exit fresh, exactly as the
        # first pass does at the top of this loop -- a project that
        # acquired a HALT sentinel while the first pass ran is not retried
        # on the strength of the first pass's (necessarily earlier) answer.
        # Same overwatch carve-out as the first pass: overwatch alone is let
        # through so run_overwatch_resolution_stage gets a look, and
        # run_project_chain's own per-project HALT check still refuses
        # further task processing for a halted project either way.
        if pp_project_halted "$_retry_project" 2>/dev/null; then
          # Best-effort; same call as the first pass's own HALT check —
          # a project reaching the retry pass never got this stamp on the
          # first pass (it wasn't halted then, or it would not be here).
          pgai_stamp_halt_boundary "$_retry_project" "halt:project" start
          if [[ "$AGENT" != "overwatch" ]]; then
            log "project ${_retry_project}: retry pass: per-project HALT now present, not retried"
            continue
          fi
          log "project ${_retry_project}: retry pass: per-project HALT present; overwatch carve-out -- retrying so the resolution stage can classify it"
        fi

        # Preserve the per-project environment discipline: export fresh for
        # the project being retried rather than inheriting whatever the
        # last first-pass project (or a prior retry in this same pass) left
        # exported.
        export PGAI_PROJECT_ROOT="$(pp_project_root "$_retry_project")"
        export PGAI_PROJECT_NAME="$_retry_project"
        export PGAI_TASKS_DIR="$(pp_tasks_dir "$_retry_project")"

        log "project ${_retry_project}: retry pass: retrying after end-of-iteration deferral"

        # set +e/-e: see the matching comment on the first pass's call —
        # run_project_chain can return 3 (still contended) and this script
        # runs under `set -e`.
        set +e
        run_project_chain "$_retry_project"
        _retry_rpc_exit=$?
        set -e

        if [[ $_retry_rpc_exit -eq 3 ]]; then
          # Closing summary: the underlying
          # lock.provenance.contended narration fired at most once per
          # lock this process (lock_provenance.sh's own memoization), so
          # this line -- not a repeat of that dual-channel block -- is
          # what names the total probe count once this project gives up
          # for the firing.
          _retry_lock_file="$(pp_repo_lock_path "$_retry_project" 2>/dev/null)" || _retry_lock_file=""
          _retry_probe_count=0
          if [[ -n "$_retry_lock_file" ]]; then
            _retry_probe_count="$(pgai_lock_contention_probe_count "$_retry_lock_file")"
          fi
          log "project ${_retry_project}: retry pass: still contended after retry (${_retry_probe_count} probe(s) this firing), deferring to the next wake firing"
          _LOCK_DEFERRED_PROJECTS+=("$_retry_project")
        else
          log "project ${_retry_project}: retry pass: chain completed"
        fi

        # Same HALT-AFTER post-work check as the first pass, evaluated now
        # that the retried project's queue has actually had a chance to
        # drain on this firing.
        if _check_halt_after_for_project "$_retry_project"; then
          log "project ${_retry_project}: HALT-AFTER promoted after retry work loop -- halted for next wake"
        fi
      done
    fi
  fi

  # --- Lock-starvation guard: one report per project still deferred ---
  # Counts the deferral while this role has a BACKLOG task in that project
  # (resets the count when it has none) and logs the starvation warning at
  # and past [wake] lock_starvation_firings -- see lib/wake_lock_wait.sh.
  for _deferred_project in "${_LOCK_DEFERRED_PROJECTS[@]}"; do
    wlw_starvation_note_deferral "$_deferred_project"
  done

  ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
  log "done: iterated ${_ITER_PROJECT_COUNT} project(s), total elapsed=${ELAPSED}s"

  # Capture post-stagger work end epoch before the tail pause so the fixed ~1s
  # sleep is excluded from work_delta; delta equals pure work time.
  WAKE_WORK_END_EPOCH=$(date +%s)
  WAKE_WORK_DELTA_SECS=$(( WAKE_WORK_END_EPOCH - WAKE_WORK_BEGIN_EPOCH ))
  log "timing: work_begin=${WAKE_WORK_BEGIN_EPOCH} work_end=${WAKE_WORK_END_EPOCH} work_delta=${WAKE_WORK_DELTA_SECS}s"

  sleep 1
  exit 0
}
