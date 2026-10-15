#!/usr/bin/env bash
# team/scripts/lib/wake_driver.sh
# Shared, provider-neutral wake pipeline driver.
#
# Source this file AFTER sourcing the provider-specific lib (which must
# define the five-function provider interface: provider_env_preflight,
# provider_model_preflight, provider_dispatch, provider_parse_output and
# provider_dispatch_refused — see lib/provider_claude.sh) and wake_common.sh,
# worktree.sh, wake_bracket.sh, and active_provider.sh, and after argument
# parsing is complete in the calling wake script (e.g. wake/claude.sh).
#
# This file holds process_one_task and run_project_chain — the two
# provider-independent pipeline entry points every wake script calls — split
# across four sibling stage files so no file (including this one) exceeds
# the shared 800-line-per-file ceiling. Before this decomposition,
# each of wake/claude.sh and wake/codex.sh carried its own ~1350-line copy of
# process_one_task, identical apart from the four provider-specific
# concerns (dispatch, env, model preflight, output parse) documented in
# lib/provider_claude.sh. This file (and its sibling stages) is that shared
# body, reached through the provider interface instead of duplicated.
#
# Sourced in this order (the order is load-bearing — see each file's own
# header for its own dependencies):
#   1. wake_driver_prep.sh      — task-preparation stage (role/effort/model
#                                 resolution, state + prerequisite checks,
#                                 role reminder). Entry: wd_prepare_task.
#   2. wake_driver_worktree.sh  — worktree/branch lifecycle stage (feature
#                                 branch, CODER/WRITER worktree, TESTER
#                                 detached worktree). Entry:
#                                 wd_setup_task_worktree.
#   3. wake_driver_invoke.sh    — task-invocation stage (prompt assembly —
#                                 including the governance read list this
#                                 file's own read_order.sh source line
#                                 resolves per role, see below — WORKING
#                                 transition, provider CLI invocation
#                                 through the five-function interface,
#                                 timeout watchdog). Entry: wd_invoke_task.
#   4. wake_driver_finalize.sh  — task-finalization stage (pollution sweep,
#                                 final-state resolution, completion gate,
#                                 backlog marker, training-corpus copy,
#                                 worktree teardown). Entry: wd_finalize_task.
#
# This file's own remaining code is process_one_task and run_project_chain
# themselves. The three provider-neutral task-setup utilities the stages
# above share (setup_project_directories, get_workflow_type_from_readme,
# get_source_branch_from_readme) live in wake_driver_task_setup.sh, sourced
# below ahead of the worktree stage that reads the two README fields.
#
# Exported functions (public API for wake scripts):
#   process_one_task  — process exactly one task from the agent's queue.
#                        Return-code contract: 0 = task found and processed,
#                        1 = queue is empty OR the FATAL _CURRENT_PROJECT-lost
#                        invariant fired (see wake_driver_invoke.sh's
#                        wd_invoke_task contract for why these share a code —
#                        this reuse is pre-existing, behavior-preserving),
#                        2 = HALT flag detected, 3 = this chain lost
#                        ownership of the project's repo lock at a mutating
#                        phase boundary (agent dispatch or the post-agent
#                        merge check — see the reverification calls inside
#                        this function's own body, and lock_provenance.sh's
#                        pgai_lock_reverify_or_narrate) and stopped cleanly
#                        without performing that phase; the queue and this
#                        task's own status.md are left untouched by this
#                        call. Unrelated to run_project_chain's own use of 3
#                        below — the two functions' return-code namespaces
#                        are independent, per this comment's own note further
#                        down, and run_project_chain's inner loop is the one
#                        call site that reads this code, so it is never
#                        mistaken for the other function's contract.
#   run_project_chain <project_name> — process the agent queue for one
#                        project (multi-project iteration, model preflight,
#                        blocked-task guard -- held only for systemic
#                        blocks, never for overwatch -- the process_one_task
#                        loop, the WAITING/BLOCKED re-checks, and PM's
#                        discovery branch with its post-discovery
#                        iteration -- wake_driver_discovery.sh).
#                        Return-code contract: 0 = the project's chain ran to
#                        completion for this pass (this covers every outcome
#                        that is not repo-lock contention, including a HALT
#                        skip, a missing/absent dev tree, a model-preflight
#                        failure, a normal task-processing pass, or a
#                        mid-chain lock-ownership loss — none of those are
#                        distinguished from each other by the return code,
#                        only narrated in the log), 3 = this pass declined
#                        the project outright because another agent still
#                        held its repo lock once wake_lock_wait.sh's
#                        wlw_acquire_chain_lock gave up -- after waiting up
#                        to [wake] lock_wait_seconds for a wake-chain
#                        holder, at once for any other holder or a refused
#                        acquisition; the chain was never entered. 3
#                        is the one signal wake_common.sh's end-of-iteration
#                        retry pass acts on (see lib/wake_common.sh,
#                        wake_common_run) — every other outcome is 0 and is
#                        not retried. This function never returns 1 or 2;
#                        those values are process_one_task's own contract
#                        above and are left unused here so the two
#                        functions' codes are never confused for each other
#                        by a caller reading only one of them.
#
# Per-project repo lock hold discipline: run_project_chain takes the
# project's repo lock once (pgai_lock_acquire, lock_provenance.sh), on one
# open fd, and holds it for the WHOLE chain -- through every dispatched
# subagent invocation (the provider CLI and its descendants only inherit
# the fd; an inherited fd holds the flock, it cannot drop it), the
# post-agent merge check, and wd_finalize_task's queue-marker write -- up
# to the end-of-chain release, so a sibling chain that wakes meanwhile
# waits for it (up to lock_wait_seconds, wake_lock_wait.sh) and then
# defers on flock contention. The one exception is the PM wake's discovery
# branch (wake_driver_discovery.sh, wd_run_pm_discovery): it releases the
# lock for discovery's read-only guards and scans, and re-takes it -- same
# fd variable, same op -- only when discovery decides to write, holding it
# again through the post-discovery dispatch to the end-of-chain release. A chain that finds at a phase boundary that
# it no longer holds the lock stops and must still reach that cleanup,
# which ABANDONS its fd rather than releasing (the path now belongs to the
# chain that took over); process_one_task's merge-boundary return clears
# errexit for exactly that reason, and pgai_lock_release is itself
# ownership-checked as the second line of defence.

# --- Source the four pipeline stages, in dependency order ---
# shellcheck source=scripts/lib/wake_driver_prep.sh
source "${SCRIPT_DIR}/../lib/wake_driver_prep.sh"
# wake_driver_task_setup.sh defines setup_project_directories (called by
# process_one_task below) and the two task-README readers
# get_workflow_type_from_readme / get_source_branch_from_readme that
# wake_driver_worktree.sh's wd_setup_task_worktree calls. Sourced here,
# ahead of the stage that needs them, the same way the other cross-stage
# dependencies below are.
# shellcheck source=scripts/lib/wake_driver_task_setup.sh
source "${SCRIPT_DIR}/../lib/wake_driver_task_setup.sh"
# shellcheck source=scripts/lib/wake_driver_worktree.sh
source "${SCRIPT_DIR}/../lib/wake_driver_worktree.sh"
# read_order.sh defines read_order_resolve_role, which the governance
# composer (_wd_build_governance_line in wake_governance_composer.sh, sourced
# by wake_driver_invoke.sh itself) calls to assemble the per-role governance
# read list from read-order/<role>.txt. Sourced once here, ahead of the
# stage that needs it, the same way the other cross-stage dependencies above
# are.
# shellcheck source=scripts/lib/read_order.sh
source "${SCRIPT_DIR}/../lib/read_order.sh"
# wake_guard_wiring.sh defines wd_install_git_boundary_guard, which
# wake_driver_invoke.sh's wd_invoke_task calls to activate the git
# remote-boundary guard for this dispatch. Sourced once here, ahead of the
# stage that needs it, the same way read_order.sh is above.
# shellcheck source=scripts/lib/wake_guard_wiring.sh
source "${SCRIPT_DIR}/../lib/wake_guard_wiring.sh"
# wake_task_budget.sh: run_project_chain's ceiling-nesting warning and the
# per-dispatch watchdog limit, plus the clock the deadline watchdog reads.
# shellcheck source=scripts/lib/wake_task_budget.sh
source "${SCRIPT_DIR}/../lib/wake_task_budget.sh"
# shellcheck source=scripts/lib/wake_driver_invoke.sh
source "${SCRIPT_DIR}/../lib/wake_driver_invoke.sh"
# metrics_rollup_refresh.sh defines metrics_rollup_refresh_for_task, which
# wake_driver_finalize.sh's wd_finalize_task calls once per task after its
# tokens.json is final. Sourced once here, ahead of the stage that needs it.
# shellcheck source=scripts/lib/metrics_rollup_refresh.sh
source "${SCRIPT_DIR}/../lib/metrics_rollup_refresh.sh"
# shellcheck source=scripts/lib/wake_driver_finalize.sh
source "${SCRIPT_DIR}/../lib/wake_driver_finalize.sh"
# lock_provenance.sh defines pgai_lock_acquire/pgai_lock_release, used below
# by run_project_chain to take the per-project repo flock with a recorded
# holder/pid/op/task instead of a bare empty-file flock.
# shellcheck source=scripts/lib/lock_provenance.sh
source "${SCRIPT_DIR}/../lib/lock_provenance.sh"
# wake_lock_wait.sh defines wlw_acquire_chain_lock (the repo-lock take that
# waits for a wake-chain holder) and the lock-starvation guard
# run_project_chain and wake_common.sh's retry pass call.
# shellcheck source=scripts/lib/wake_lock_wait.sh
source "${SCRIPT_DIR}/../lib/wake_lock_wait.sh"
# wake_driver_discovery.sh defines wd_run_pm_discovery, the PM wake's
# discovery branch run_project_chain calls after its task loop (discovery
# without the repo lock, the lock re-taken to write and dispatch).
# shellcheck source=scripts/lib/wake_driver_discovery.sh
source "${SCRIPT_DIR}/../lib/wake_driver_discovery.sh"

# ---------------------------------------------------------------------------
# Process exactly one task.
#
# All process_one_task-level state that crosses stage-function boundaries is
# declared `local` here and threaded to the stage functions via bash's
# dynamic scoping (each stage function is called from within this function's
# own stack frame and reads/writes these locals directly without
# redeclaring them) — see each stage file's own header comment for its
# read/write contract. This mirrors exactly how the pre-decomposition
# monolith (wake/claude.sh's former ~1350-line process_one_task) threaded
# the same state through its own nested blocks.
# ---------------------------------------------------------------------------
LAST_TASK_FINAL_STATE=""
process_one_task() {
  local task_id task_dir task_readme task_status task_log_dir task_artifact_dir
  local role SUBAGENT
  local selected_model model_source selected_effort effort_source
  local task_debug_log
  local _task_feature_branch="" _task_worktree_path="" _task_workflow_type=""
  local _task_source_branch=""
  local _canonical_dev_tree_path="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
  local _park_resting_branch=""
  local task_start_ts agent_exit _task_killed _provider_pgid _provider_deadline_epoch _provider_group_sigkilled _provider_timeout_seconds
  local _litter_snapshot_file
  local _pollution_pre_snapshot="" _pollution_snapshot_ok=false

  LAST_TASK_FINAL_STATE=""; LAST_TASK_DISPATCH_REFUSED=false

  # --- D4 pollution sweep: snapshot anchoring ---
  # Capture canonical dev tree path at function entry, BEFORE any per-task
  # PGAI_DEV_TREE_PATH override (TESTER tasks override it to the worktree).
  # Both the pre-task and post-task snapshots use this variable so the sweep
  # always watches the canonical tree regardless of in-flight overrides.
  # Resolve per-project dev tree first; fall back to global for
  # single-project / legacy installs where PP_dev_tree_path is unset.
  local _sweep_canonical_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

  local _debug_gate
  _debug_gate="$(_resolve_debug_gate "${_CURRENT_PROJECT:-}" "${AGENT}")"

  # Return-code contract:
  #   0  — task found and processed
  #   1  — queue is empty
  #   2  — HALT flag detected
  if [[ "$AGENT" == "overwatch" ]]; then
    if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
      log "HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH — stopping batch loop cleanly"
      return 2
    fi
  else
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — stopping batch loop cleanly"
      return 2
    fi
  fi

  task_id="$(get_first_pending_task || true)"
  if [[ -z "${task_id:-}" ]]; then
    log "no pending Claude task found in ${AGENT} queue"
    return 1
  fi

  task_dir="${TASKS_ROOT}/${task_id}"
  task_readme="${task_dir}/README.md"
  task_status="${task_dir}/status.md"
  task_log_dir="${task_dir}/logs"
  task_artifact_dir="${task_dir}/artifacts"

  if [[ ! -f "$task_readme" ]]; then
    log "task ${task_id} missing README.md; marking BLOCKED"
    ensure_status_file "$task_status"
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Blockers" "Task README.md is missing at ${task_readme}"
    set_block "$task_status" "Needs Human" "yes"
    block_record_write "$task_status" missing_readme "$(block_record_owner_role "$task_status" "$AGENT")" \
      "$(block_record_evidence --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
    normalize_status_file "$task_status"
    mark_backlog "$task_id" "B"
    LAST_TASK_FINAL_STATE="BLOCKED"
    _chain_blocked_a_task=true
    return 0
  fi

  ensure_status_file "$task_status"
  setup_project_directories "$task_log_dir" "$task_artifact_dir" || return 1

  # --- Stage 1: task preparation (role/effort/model, state, prerequisites) ---
  # wd_prepare_task always returns 0 (see its own header comment); an
  # already-terminal outcome (BLOCKED/WAITING/non-BACKLOG skip) is signaled
  # by LAST_TASK_FINAL_STATE going non-empty, not by the return code.
  wd_prepare_task
  if [[ -n "$LAST_TASK_FINAL_STATE" ]]; then
    return 0
  fi

  # --- Stage 2: worktree/branch lifecycle ---
  # (the agent-dispatch mutating-phase-boundary reverification runs one
  # level up, at the top of run_project_chain's own per-iteration loop,
  # BEFORE reconcile_stale_active/reconcile_terminal_worktrees/recheck_
  # waiting_tasks/recheck_blocked_tasks/sweep_running_intake_census — see
  # that loop's own comment. Those five housekeeping calls themselves write
  # queue-adjacent state (or remove a worktree) for whichever task they
  # touch next, so gating them at the loop's own top is what actually stops
  # a reaped chain before ANY further shared-state mutation, not merely
  # before this one task's own worktree setup.)
  # wd_setup_task_worktree always returns 0 (see its own header comment);
  # same LAST_TASK_FINAL_STATE signal as Stage 1.
  wd_setup_task_worktree
  if [[ -n "$LAST_TASK_FINAL_STATE" ]]; then
    return 0
  fi

  # --- Stage 3: prompt assembly, WORKING transition, provider invocation ---
  # Name the task being dispatched in the repo lock's provenance (task= was
  # "none" at acquisition), so a reader of the lock sees what this chain is
  # doing. A lock this chain no longer holds is left untouched.
  if [[ -n "${_project_lock_fd:-}" ]]; then
    pgai_lock_set_task "${_project_lock_file:-}" "$_project_lock_fd" "$task_id" \
      || log "task ${task_id}: repo lock provenance not updated with this task -- this chain does not hold ${_project_lock_file:-the repo lock} at its path"
  fi
  local _invoke_exit=0
  set +e
  wd_invoke_task
  _invoke_exit=$?
  set -e
  if [[ $_invoke_exit -eq 2 ]]; then
    # FATAL: _CURRENT_PROJECT lost. wd_invoke_task already logged, restored
    # PGAI_DEV_TREE_PATH, tore down any worktree, and restored the canonical
    # dev tree HEAD. Matches the pre-decomposition monolith's own `return 1`
    # for this one FATAL branch (a pre-existing, behavior-preserving reuse
    # of the "queue is empty" exit code — see this function's own
    # return-code contract comment above).
    return 1
  fi

  # --- Mutating phase boundary: merge ---
  # Re-verify lock ownership again now that the dispatched agent has
  # returned control — the agent's own session is exactly where a CODER/
  # WRITER task's `git merge --no-ff` into the source branch happens (see
  # team/roles/CODER.md PHASE 2), and Stage 4 below is where this chain
  # itself resumes mutating the shared dev tree and task state on the
  # strength of that agent run (pollution-sweep quarantine, the completion
  # gate's merge verification, worktree teardown, dev-tree HEAD restore). A
  # chain that lost the lock during dispatch must not perform any of that.
  if ! pgai_lock_reverify_or_narrate "${_project_lock_file:-}" "$$" "merge" "wake-chain:${_CURRENT_PROJECT:-}" "$task_id"; then
    _project_lock_lost=true
    log "task ${task_id}: chain stopping before finalize/merge-verification — repo lock ownership lost (see narration above)"
    # errexit is global, not function-scoped: Stage 3 re-enabled it after
    # wd_invoke_task, so a nonzero return here would terminate the whole
    # wake process before run_project_chain reads the 3 -- and before its
    # end-of-chain cleanup abandons this fd, leaving the exit sweep to act
    # on a path that now belongs to the chain that took the lock over.
    # The caller's own set +e / set -e bracket restores errexit after $?.
    set +e
    return 3
  fi

  # --- Stage 4: pollution sweep, final-state resolution, teardown ---
  wd_finalize_task

  return 0
}

# ---------------------------------------------------------------------------
# run_project_chain <project_name>
# Process the agent queue for a single project.
#
# Return-code contract (see the public-API summary at the top of this file
# for the full rationale): 0 on every outcome except one — 3 when this pass
# declines the project outright because another agent still held its repo
# lock when the acquisition step below gave up (wlw_acquire_chain_lock waits
# up to lock_wait_seconds for a wake-chain holder, never for another holder
# or a refusal), before the chain does any work.
# wake_common.sh's wake_common_run reads exactly this signal to decide which
# projects get the end-of-iteration retry pass; every other early-return
# path in this function (missing log dir, missing backlog file, halted
# project, failed model preflight, absent dev tree) stays 0 because none of
# those are lock contention and none of them are retried.
# ---------------------------------------------------------------------------
run_project_chain() {
  local project_name="$1"

  _CURRENT_PROJECT="$project_name"
  TASKS_ROOT="$(pp_tasks_dir "$project_name")"
  QUEUE_DIR="${TASKS_ROOT}/queues"
  BACKLOG="$(pp_queue_path "$project_name" "$AGENT")"
  # Per-firing batch logs at kanban-root scope.
  LOG_DIR="${KANBAN_ROOT}/logs/agents"
  if ! mkdir -p "$LOG_DIR"; then
    log "FATAL: cannot create wake batch log directory at ${LOG_DIR}"
    log "       Check filesystem permissions, disk space, and read-only mounts."
    return 0
  fi

  # --- OVERWATCH block triage (overwatch only), after the HALT test ---
  # Before the repo lock below: the check takes that lock per task itself
  # (overwatch_halt_first_fix). A present HALT skips it, narrated "fix
  # deferred: HALT present"; a no-op for every other agent (wake_project.sh).
  run_overwatch_block_triage "$project_name"

  # --- Per-project repo flock ---
  # pp_repo_lock_path is the single source of truth for this path — OVERWATCH
  # resolves the same lock file via the same helper. wlw_acquire_chain_lock
  # (wake_lock_wait.sh) takes it through pgai_lock_acquire
  # (lock_provenance.sh), waiting up to lock_wait_seconds when a wake chain
  # holds it; pgai_lock_acquire records holder/pid/op/task as part of taking
  # the lock, so a contended caller can name who holds it instead of just
  # "somebody holds it" — see lock_provenance.sh for the read side
  # (pgai_lock_read_provenance) that a contention-message caller can use.
  # No single task is chosen yet at this point in the chain (this lock
  # spans the whole per-project chain, not one task), so task is the
  # explicit literal "none" rather than left blank.
  local _project_lock_file
  _project_lock_file="$(pp_repo_lock_path "$project_name")" || return 0

  local _project_lock_fd
  local _project_lock_rc
  # Set true by process_one_task / wd_finalize_task (bash dynamic scoping —
  # neither declares a local of this name) when a mutating-phase-boundary
  # reverification finds this chain no longer holds _project_lock_file (see
  # lock_provenance.sh's pgai_lock_reverify_or_narrate). Read only at this
  # function's own end-of-chain cleanup below, to decide pgai_lock_abandon
  # (drop this process's own fd/bookkeeping without touching the on-disk
  # file, which by then belongs to the new legitimate holder) vs the
  # ordinary pgai_lock_release.
  local _project_lock_lost=false
  # Set true (dynamic scoping) by every site that marks a task BLOCKED:
  # wd_finalize_task after the agent ran, and the pre-dispatch block sites
  # in process_one_task, wd_prepare_task and wd_setup_task_worktree. Read
  # only after the end-of-chain lock release below, to fire the on-BLOCK
  # nudge (fire_block_triage_nudge, wake_project.sh).
  local _chain_blocked_a_task=false
  # set +e/-e: wlw_acquire_chain_lock's return code (pgai_lock_acquire's:
  # 1 contended, 2 refused) is read on the next line
  # (a bare call followed by `$?` does NOT survive `set -e` -- contention
  # (its documented return 1) would otherwise kill the whole wake process
  # right here instead of the graceful per-project skip this branch exists
  # to perform.
  # NOTE: `set -e` is deliberately NOT restored until after the contention
  # branch below. A `return N` (N != 0) executed while `-e` is active
  # triggers errexit immediately, inside THIS function's own body, before
  # control ever reaches the caller -- it does not matter that the caller
  # (wake_common.sh) wraps its own call to run_project_chain in a matching
  # set +e/set -e bracket, because `set` is a global shell option, not
  # function-scoped: re-enabling it here before `return 3` would let the
  # nonzero return kill the whole wake process from inside this function,
  # exactly the failure mode this bracket exists to prevent. `-e` is
  # restored on the line immediately after the `if`, so every later line in
  # this function still runs under strict mode as before.
  set +e
  wlw_acquire_chain_lock "$project_name" "$_project_lock_file" _project_lock_fd
  _project_lock_rc=$?

  if [[ $_project_lock_rc -ne 0 ]]; then
    local _lock_contention_provenance _lock_contention_probe
    _lock_contention_provenance="$(pgai_lock_read_provenance "$_project_lock_file")"
    # Probe count from lock_provenance.sh's own per-process memoization --
    # pgai_lock_acquire narrated the dual-channel lock.provenance.contended
    # block at most once for this attempt; this count is how this line
    # still names how many probes it took without re-emitting that block
    # itself.
    _lock_contention_probe="$(pgai_lock_contention_probe_count "$_project_lock_file")"
    log "project ${project_name}: repo lock held by $(pgai_lock_describe_provenance "$_lock_contention_provenance") -- probe ${_lock_contention_probe}, deferring for an end-of-iteration retry"
    return 3
  fi
  set -e
  # This role holds the lock: its lock-starvation count starts over.
  wlw_starvation_note_acquired "$project_name"

  if [[ ! -f "$BACKLOG" ]]; then
    if [[ "$AGENT" == "overwatch" ]]; then
      # Self-heal: the resolution stage's trigger is the HALT
      # file itself, never this queue file's presence -- create-project.sh
      # and the upgrade migration both seed overwatch_backlog.md going
      # forward (team/scripts/lib/overwatch_backlog_seed.sh is the single
      # source of both paths' content), so reaching this branch means an
      # existing project predates that scaffolding, or the file was
      # deleted by hand. Either way, returning 0 here -- as every other
      # agent's missing-backlog path still does, unchanged, below -- would
      # silently swallow a HALT the overwatch carve-out in wake_common.sh
      # just let through the per-project HALT fast-exit specifically so
      # run_overwatch_resolution_stage below could classify it. Recreate
      # the file (empty; no BACKLOG-marked entries) and keep going instead
      # of skipping past the resolution stage call.
      mkdir -p "$(dirname "$BACKLOG")"
      overwatch_backlog_seed_content > "$BACKLOG"
      log "project ${project_name}: overwatch backlog not found at ${BACKLOG}, self-healed (seeded empty queue file) -- continuing so the resolution stage can run"
    else
      log "project ${project_name}: ${AGENT} backlog not found at ${BACKLOG}, skipping"
      pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
      return 0
    fi
  fi

  # --- Per-project clock ---
  # Each project gets its own elapsed measurement so a slow project 1 cannot
  # consume the batch budget of projects 2..N.
  local _project_start_epoch
  _project_start_epoch="$(date +%s)"

  # Warn (non-fatal) when the ceilings do not nest task < project <= batch.
  # _task_timeout_budget_seconds: per-dispatch watchdog limit, set by
  # wd_chain_task_timeout_budget before each dispatch (wake_task_budget.sh).
  local _eff_project="${MAX_PROJECT_SECONDS:-${MAX_RUNTIME_SECONDS}}"
  local _eff_task="${MAX_TASK_SECONDS:-0}" _task_timeout_budget_seconds=""
  wd_warn_runtime_bound_nesting "$project_name" "$_eff_task" "$_eff_project" "${MAX_RUNTIME_SECONDS:-0}"

  log "project ${project_name}: starting (agent=${AGENT} max_tasks=${MAX_TASKS_PER_WAKE} max_runtime=${MAX_RUNTIME_SECONDS}s max_project=${_eff_project}s max_task=${_eff_task}s stop_on_blocked=${STOP_ON_BLOCKED})"

  # --- Per-project config load ---
  # Load per-project config so PP_dev_tree_path is exported before any task
  # processing for this project.  process_one_task reads PP_dev_tree_path for
  # worktree creation and pollution-sweep operations; without this call it
  # falls back to the global PGAI_DEV_TREE_PATH (the global dev tree),
  # which is wrong for projects that have their own dev_tree_path.
  # Non-fatal: if pp_load_config fails (missing project.cfg) log a warning and
  # continue — the global fallback keeps single-project / legacy installs working.
  if ! pp_load_config "$project_name" 2>/dev/null; then
    log "project ${project_name}: WARNING: pp_load_config failed; PP_dev_tree_path unset — falling back to global PGAI_DEV_TREE_PATH for worktree operations"
  else
    log "project ${project_name}: pp_load_config loaded; PP_dev_tree_path=${PP_dev_tree_path:-<unset>}"
  fi

  # --- Per-project dev-tree existence gate (D3) ---
  # Fires AFTER pp_load_config so PP_dev_tree_path and PP_workflow_type are
  # populated.  Release-workflow projects with a missing dev tree are skipped
  # with a per-project log line; document-workflow projects are exempt.
  # The gate lives in wake_common.sh (_check_project_dev_tree) so both wake
  # scripts use identical logic (sibling discipline).
  if ! _check_project_dev_tree "$project_name"; then
    pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
    return 0
  fi

  # --- Autonomous block-resolution stage (overwatch only) ---
  # run_overwatch_resolution_stage is a no-op for every AGENT other than
  # overwatch; see team/scripts/lib/wake_common.sh for the full contract.
  run_overwatch_resolution_stage "$project_name"

  # --- Per-project HALT gate, deferred for overwatch ---
  # wake_common_run's iteration loop lets an overwatch firing through its
  # per-project HALT fast-exit specifically so the call above could classify
  # the HALT file and narrate its own decline. That classification has now
  # happened (or the stage no-op'd for a non-ship-policy, non-operator
  # absence — see resolution_stage.py). A halted project still gets no
  # further task processing on this firing, exactly as the fast-exit already
  # guarantees for every other agent.
  if [[ "$AGENT" == "overwatch" ]] && pp_project_halted "$project_name" 2>/dev/null; then
    log "project ${project_name}: per-project HALT present, resolution stage evaluated, skipping task processing"
    pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
    return 0
  fi

  reconcile_stale_active
  reconcile_terminal_worktrees

  # --- Model preflight ---
  # Validate the configured provider model once per project chain, before any
  # task is transitioned to WORKING. A bad model fails fast with a clear
  # operator-facing message rather than producing a cryptic mid-task BLOCKED.
  # Tasks are left unmodified — the queue is not disturbed by a config error.
  # Claude provider: model support is verified on first dispatch (no cheap
  # validation endpoint); the preflight refuses only a CLI an earlier dispatch
  # reported too old for the model. See provider_claude.sh for the memo.
  local _agent_upper _agent_model_var _preflight_model
  _agent_upper="${AGENT^^}"
  _agent_model_var="PGAI_${_agent_upper}_MODEL"
  _preflight_model="${!_agent_model_var:-}"   # env-var model; empty = provider CLI default

  local _preflight_exit=0
  if [[ -z "$(get_first_pending_task || true)" ]]; then  # nothing to dispatch: the codex preflight is a real model request
    log "project ${project_name}: no dispatchable task in the ${AGENT} queue; model-preflight skipped"
  else  # `||`, not set +e: the provider may re-enable errexit and end the wake before the release below
    provider_model_preflight "$_preflight_model" "$LOG_FILE" || _preflight_exit=$?
  fi

  if [[ $_preflight_exit -ne 0 ]]; then
    log "project ${project_name}: model-preflight FAILED — skipping task processing for this wake (tasks remain queued; fix model config and retry)"
    pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
    return 0
  fi

  # --- Blocked-task project guard ---
  # Holds task dispatch for this project only while its human-gated blocks
  # (BLOCKED + "## Needs Human: yes", across ALL agent queues) are systemic:
  # two or more whose Block Records share one reason_code (other than the
  # agent catch-all agent_unclassified) within [wake] block_storm_window
  # seconds back from now, or one whose reason_code is on
  # [wake] systemic_block_reasons. Dispatching into a systemic fault would
  # grind the remaining backlog into BLOCKED one task per firing. A lone
  # task-local block holds only the tasks that name it as a prerequisite
  # (WAITING already does), so unrelated work keeps flowing. The decision is
  # pgai_agent_kanban.overwatch.block_storm's; this block narrates it.
  # OVERWATCH is never held: its wake is what triages the blocks. While
  # held, the WAITING/BLOCKED re-checks after the loop still run, and
  # discovery / PM intake still runs (the AGENT==pm block below). The hold
  # clears once the operator resolves the blocks, and in any case lifts by
  # itself at lifts_at -- never more than [wake] block_hold_cap seconds
  # after the block that began it; a decision that cannot be read holds
  # nothing.
  local _dispatch_gated=false _gate_line _gate_verdict="" _gate_count=0
  local _gate_reason="" _gate_why="" _gate_remedy="" _gate_lifts_at=""
  while IFS= read -r _gate_line; do
    case "${_gate_line%%=*}" in
      verdict) _gate_verdict="${_gate_line#*=}" ;;
      count) _gate_count="${_gate_line#*=}" ;;
      reason_code) _gate_reason="${_gate_line#*=}" ;;
      why) _gate_why="${_gate_line#*=}" ;;
      remedy) _gate_remedy="${_gate_line#*=}" ;;
      lifts_at) _gate_lifts_at="${_gate_line#*=}" ;;
    esac
  done < <(pp_run_ops pgai_agent_kanban.overwatch.block_storm decide "$QUEUE_DIR" "$TASKS_ROOT" \
             --id-pattern "$(kanban_task_id_pattern)" --window "${BLOCK_STORM_WINDOW:-}" \
             --hold-cap "${BLOCK_HOLD_CAP:-}" \
             --systemic-codes "${SYSTEMIC_BLOCK_REASONS:-}" || true)

  if [[ "$_gate_verdict" == "systemic" && "$AGENT" == "overwatch" ]]; then
    log "project ${project_name}: ${_gate_count} BLOCKED task(s) need human attention; systemic reason_code=${_gate_reason}: ${_gate_why} -- overwatch is never held, dispatching"
  elif [[ "$_gate_verdict" == "systemic" ]]; then
    log "project ${project_name}: ${_gate_count} BLOCKED task(s) need human attention; systemic reason_code=${_gate_reason}: ${_gate_why} -- not dispatching new tasks until ${_gate_lifts_at:-the blocks are resolved}"
    # BLOCK-WORTHY: a systemic block is an operator stop by design -- every
    # task dispatched into it blocks the same way. Holds dispatch only; no
    # task state changes, and the hold lifts when the blocks are resolved
    # or at lifts_at, whichever comes first.
    # CENSUS-ANCHOR: wake-driver-systemic-block-01
    pgai_emit_error_contract "wake.dispatch_held_systemic_block" \
      "project ${project_name}: task dispatch held for every role except overwatch -- ${_gate_why}" \
      "${_gate_remedy}" "$project_name" || true
    _dispatch_gated=true
  elif [[ "$_gate_verdict" == "task_local" ]]; then
    log "project ${project_name}: ${_gate_count} BLOCKED task(s) need human attention; ${_gate_why} -- dispatching (a task-local block holds only its dependents)"
  elif [[ "$_gate_verdict" != "none" ]]; then
    log "project ${project_name}: WARNING: blocked-task guard could not decide (no verdict from pgai_agent_kanban.overwatch.block_storm) -- dispatching"
  fi

  local TASKS_COMPLETED=0
  local STOP_REASON=""
  if [[ "$_dispatch_gated" == "true" ]]; then
    STOP_REASON="blocked tasks need human attention (not dispatching)"
  fi

  if [[ "$_dispatch_gated" == "false" ]]; then

  while true; do
    if (( TASKS_COMPLETED >= MAX_TASKS_PER_WAKE )); then
      STOP_REASON="reached MAX_TASKS_PER_WAKE (${MAX_TASKS_PER_WAKE})"
      break
    fi

    ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
    if (( ELAPSED >= MAX_RUNTIME_SECONDS )); then
      STOP_REASON="reached MAX_RUNTIME_SECONDS (batch) (${MAX_RUNTIME_SECONDS}s, batch_elapsed=${ELAPSED}s)"
      break
    fi

    # Per-project clock check (D3): each project gets its own elapsed budget.
    local _project_elapsed=$(( $(date +%s) - _project_start_epoch ))
    if [[ "${_eff_project:-0}" -gt 0 && "${_project_elapsed}" -ge "${_eff_project}" ]]; then
      STOP_REASON="reached MAX_PROJECT_SECONDS (project) (${_eff_project}s, project_elapsed=${_project_elapsed}s)"
      break
    fi

    # --- Operator stop file: the between-tasks boundary ---
    # The file is operator-owned and shared by every wake batch on the
    # host (wake_stop_file_present, lib/wake_common.sh): every agent's
    # batch reads the same path and stops at its own next task boundary
    # while it exists, so the first chain to see it must leave it in place
    # rather than consume it. wake_common_run performs the matching check
    # between projects and before the contention retry pass; the operator
    # removes the file to let dispatch resume.
    if wake_stop_file_present; then
      STOP_REASON="stop file present at ${STOP_FILE} (left in place; remove it to resume dispatch)"
      break
    fi

    # --- Provider usage-limit pause: the between-tasks boundary ---
    # A task whose provider reported its usage limit recorded a fleet-wide
    # pause (handle_working_after_exit); every chain, this one included,
    # stops dispatching at its next task boundary until the stated reset
    # time. wake_provider_pause_gate narrates the hold, or the pause's end.
    if ! wake_provider_pause_gate "$project_name"; then
      STOP_REASON="provider ${ACTIVE_PROVIDER:-claude} paused at its usage limit until ${WAKE_PROVIDER_PAUSED_UNTIL} -- not dispatching"
      break
    fi

    # --- Mutating phase boundary: agent dispatch ---
    # Re-verify this chain still holds the project repo lock BEFORE the five
    # per-iteration housekeeping calls below (reconcile_stale_active,
    # reconcile_terminal_worktrees, recheck_waiting_tasks,
    # recheck_blocked_tasks, sweep_running_intake_census) — every one of them
    # writes queue-adjacent state (a stale [A] marker reset, a terminal
    # task's leftover worktree removed, a WAITING promotion, a BLOCKED
    # re-check, an intake-census closure) for whatever task it finds next,
    # so checking only right before
    # process_one_task's own dispatch would already be too late: a reaped
    # chain could still stomp a task another, legitimately-lock-holding
    # chain is actively working (e.g. resetting that chain's own in-flight
    # [A] marker back to BACKLOG as "orphaned", since these calls have no
    # notion of "another live chain" — only one chain is ever supposed to
    # hold this project's lock at a time). See lock_provenance.sh's
    # "Chain-level ownership reverification" header. No task is selected
    # yet at this point, so "none" is the explicit task argument, mirroring
    # this same lock's own acquisition call above.
    if ! pgai_lock_reverify_or_narrate "$_project_lock_file" "$$" "agent dispatch" "wake-chain:${project_name}" "none"; then
      _project_lock_lost=true
      STOP_REASON="chain lost repo lock ownership mid-run at a mutating phase boundary -- stopping cleanly (see narration above)"
      break
    fi

    reconcile_stale_active
    reconcile_terminal_worktrees
    recheck_waiting_tasks
    recheck_blocked_tasks
    sweep_running_intake_census

    wd_chain_task_timeout_budget
    set +e
    process_one_task
    _pot_exit=$?
    set -e
    case $_pot_exit in
      0)
        TASKS_COMPLETED=$(( TASKS_COMPLETED + 1 ))
        ;;
      1)
        STOP_REASON="no more BACKLOG tasks in ${AGENT} queue"
        break
        ;;
      2)
        if [[ "$AGENT" == "overwatch" ]]; then
          STOP_REASON="HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH"
        else
          STOP_REASON="HALT flag detected at ${TEAM_ROOT}/HALT"
        fi
        break
        ;;
      3)
        # The task's own post-dispatch "merge" phase-boundary reverification
        # (inside process_one_task, after wd_invoke_task returns) found this
        # chain no longer holds the repo lock (process_one_task's own
        # contract, above — unrelated to this function's own use of 3 for
        # first-acquisition contention, and to the same-shaped "agent
        # dispatch" boundary check this loop already performed above
        # process_one_task's own call). process_one_task already narrated
        # the loss and performed no further mutation for that task;
        # _project_lock_lost is already true by the time control reaches
        # here, so the end-of-chain cleanup below abandons rather than
        # releases the lock.
        STOP_REASON="chain lost repo lock ownership mid-run at a mutating phase boundary -- stopping cleanly (see narration above)"
        break
        ;;
      *)
        STOP_REASON="unexpected exit code ${_pot_exit} from process_one_task"
        break
        ;;
    esac

    if [[ "${LAST_TASK_DISPATCH_REFUSED:-false}" == "true" ]]; then
      STOP_REASON="provider CLI refused the dispatch before any work began (infrastructure condition; see the refusal narrated above) -- not dispatching further tasks this wake"
      break
    elif [[ "$LAST_TASK_FINAL_STATE" == "BLOCKED" && "$STOP_ON_BLOCKED" == "true" ]]; then
      STOP_REASON="task ended in BLOCKED state (STOP_ON_BLOCKED=true)"
      break
    fi

    log "pausing ${PAUSE_BETWEEN_TASKS}s before next task"
    sleep "$PAUSE_BETWEEN_TASKS"
  done

  fi  # end: if [[ "$_dispatch_gated" == "false" ]]

  # The WAITING promotion and BLOCKED auto-retry re-checks run on every
  # wake, held or not: a held project is the one whose blocks most need
  # re-checking, and a block cleared here must not wait for the hold to lift.
  recheck_waiting_tasks
  recheck_blocked_tasks
  if [[ "$_dispatch_gated" == "false" ]]; then
    sweep_running_intake_census
  fi

  # Already excluded when this chain lost its repo lock above: that break's
  # STOP_REASON text ("chain lost repo lock ownership mid-run...") matches
  # neither disjunct below, so a lock-loss stop never reaches PM's
  # discovery branch. That branch (wake_driver_discovery.sh) releases the
  # repo lock for the read-only discovery scan and re-takes it only to
  # write and to run the post-discovery dispatch.
  # A role the starvation guard flagged (wake_lock_wait.sh) goes first:
  # while its task is ready, PM runs no discovery and so stops taking the
  # repo lock for it.
  if [[ "$AGENT" == "pm" && ( "$STOP_REASON" == *"no more BACKLOG tasks"* || "$_dispatch_gated" == "true" ) && "${PGAI_KANBAN_PM_MODE:-automatic}" != "manual" ]]; then
    if wlw_starved_role_ready "$project_name"; then
      log "project ${project_name}: autonomous scan: skipping discovery -- ${WLW_STARVED_ROLE} task ${WLW_STARVED_TASK} is ready and ${WLW_STARVED_ROLE} has deferred on repo-lock contention for ${WLW_STARVED_DEFERRALS} consecutive firing(s)"
    else
      if [[ "$_dispatch_gated" == "true" ]]; then
          log "project ${project_name}: autonomous scan: dispatch gated (blocked tasks), running discovery pipeline (intake unaffected)"
      else
          log "project ${project_name}: autonomous scan: PM backlog empty, running discovery pipeline"
      fi
      wd_run_pm_discovery "$project_name"
    fi
  fi

  log "project ${project_name}: done: completed=${TASKS_COMPLETED} project_elapsed=$(( $(date +%s) - _project_start_epoch ))s batch_elapsed=$(( $(date +%s) - WAKE_START_EPOCH ))s reason=\"${STOP_REASON}\""

  # End-of-chain lock release. A mid-chain reverification loss
  # (_project_lock_lost, set by process_one_task/wd_finalize_task above) means
  # the on-disk lock file no longer belongs to this process — some other
  # chain reaped and reacquired it while this one kept running. Releasing it
  # normally here would truncate/remove that new holder's own record
  # (pgai_lock_release's documented contract assumes the caller still
  # legitimately owns the path); pgai_lock_abandon is the release-shaped
  # counterpart that only closes this process's own fd and drops its
  # bookkeeping entry, never touching the file. The ordinary path (lock never
  # lost) is unchanged.
  if [[ "$_project_lock_lost" == "true" ]]; then
    log "project ${project_name}: repo lock ownership was lost mid-chain; abandoning this process's own fd without touching the lock file (another chain owns it now)"
    pgai_lock_abandon "$_project_lock_file" "$_project_lock_fd"
  else
    pgai_lock_release "$_project_lock_file" "$_project_lock_fd"
  fi

  # On-BLOCK nudge, only once this chain no longer holds the repo lock.
  if [[ "$_chain_blocked_a_task" == "true" ]]; then
    fire_block_triage_nudge "$project_name"
  fi
}
