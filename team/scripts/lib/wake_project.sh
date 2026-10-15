#!/usr/bin/env bash
# team/scripts/lib/wake_project.sh
#
# Wake per-project stage: feature-branch naming/README helpers, the
# HALT-AFTER sentinel check, the per-project dev-tree existence gate, and
# the OVERWATCH resolution-stage call site. Extracted from team/scripts/lib/
# wake_common.sh's former inline body so wake_common.sh's own remaining code
# stays a thinner shell over sourced lib stages. Behavior-preserving: no
# stdout/stderr line, exit code, HALT/HALT-AFTER evaluation semantics, or
# on-disk file content changes as a result of this file existing — it is
# one stage split out of a larger driver so each stays a focused,
# independently testable unit.
#
# Public entry point
# -------------------
#   _check_halt_after_for_project <project_name>
#       Evaluates the HALT-AFTER sentinel for a single project on each wake,
#       delegating token parsing, drain evaluation, and atomic promotion to
#       the halt_after Python module. Called once after run_project_chain
#       returns in wake_common_run, and once from the per-project loop's
#       post-discovery block, for every project iterated each wake.
#
# Other functions in this file — _build_feature_branch,
# _update_readme_feature_branch, _check_project_dev_tree,
# run_overwatch_resolution_stage, run_overwatch_block_triage,
# fire_block_triage_nudge — are shared primitives, not private
# helpers of _check_halt_after_for_project alone. Both wake provider scripts
# call all of them directly at their own points in run_project_chain:
#   _build_feature_branch           — produces the prefixed feature-branch
#                                      name for a new CODER/WRITER task.
#   _update_readme_feature_branch   — rewrites a task README's
#                                      ## Feature Branch field in place.
#   _check_project_dev_tree         — per-project dev-tree existence gate,
#                                      called after pp_load_config so
#                                      PP_dev_tree_path/PP_workflow_type are
#                                      resolved; exempts git_mode=none
#                                      workflows.
#   run_overwatch_resolution_stage  — invokes the OVERWATCH resolution stage
#                                      for a project; a no-op for every
#                                      other agent.
#   run_overwatch_block_triage      — runs OVERWATCH's block-triage check
#                                      for a project before the chain takes
#                                      the repo lock, unless a HALT is
#                                      present; a no-op for every other
#                                      agent.
#   fire_block_triage_nudge         — the on-BLOCK nudge: backgrounds
#                                      overwatch-sweep.sh --only block-triage
#                                      once a chain that blocked a task has
#                                      released the repo lock; a no-op for
#                                      the overwatch agent.
# Defined at source time, available as soon as this file is sourced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh   — log(), KANBAN_ROOT, AGENT.
#   project_paths.sh, projects.sh (sourced transitively by
#                       wake_bootstrap.sh) — pp_project_root, pp_prefix_branch.
#   workflow.sh (sourced transitively by wake_bootstrap.sh) — wf_load_plugin,
#                       wf_git_mode.
#   pp_run_ops.sh (sourced transitively by wake_bootstrap.sh) — pp_run_ops
#                       (run_overwatch_resolution_stage's overwatch call).
#   SCRIPT_DIR (set by the wake script) — run_overwatch_block_triage finds
#                       ../lib/overwatch-checks/check-block-triage.sh from it,
#                       fire_block_triage_nudge ../overwatch-sweep.sh.
#   overwatch_lib.sh, overwatch_protocol.sh (sourced from ${SCRIPT_DIR}/../lib
#                       by this file itself, inside the overwatch steps'
#                       subshells) — overwatch_run_with_firing_lock and its
#                       action-log row.
#   halt_after (Python module, resolved via PYTHONPATH=$KANBAN_ROOT) —
#                       token parsing, drain evaluation, and promotion.
#
# python3 must be on PATH. Sourced by wake_common.sh after wake_lifecycle.sh.

# ---------------------------------------------------------------------------
# _build_feature_branch <task_id> <project_name>
# Calls pp_prefix_branch() to produce the prefixed feature branch name.
# Returns "<prefix>feature/<task_id>" when branch_prefix is set in project.cfg.
# Returns "feature/<task_id>" when branch_prefix is empty (pure-AI install).
# Returns non-zero when pp_prefix_branch fails (invalid config).
# ---------------------------------------------------------------------------
_build_feature_branch() {
    local task_id="${1:-}"
    local project="${2:-}"
    local base="feature/${task_id}"
    [[ -z "$project" ]] && { printf '%s' "$base"; return 0; }
    local result
    result="$(pp_prefix_branch "$project" "$base")"
    printf '%s' "${result:-$base}"
}

# ---------------------------------------------------------------------------
# _update_readme_feature_branch <readme_path> <feature_branch>
# Overwrites the ## Feature Branch field in a task README with <feature_branch>.
# An empty field is filled in place; every other section is left untouched.
# No-op (non-fatal) when the field or the README is absent. Needs pp_run_ops
# and pgai_atomic_write, both loaded with the wake libraries (wake_bootstrap.sh
# sources pp_run_ops.sh, wake_task_state.sh sources atomic_write.sh).
# ---------------------------------------------------------------------------
_update_readme_feature_branch() {
    local readme="$1"
    local branch="$2"
    [[ -f "$readme" ]] || return 0
    # The section rewrite is task_status's (the same boundary every status.md
    # writer uses); pgai_atomic_write commits it. Any failure leaves the
    # README as it was.
    pgai_atomic_write "$readme" pp_run_ops pgai_agent_kanban.task_status render \
        "$readme" --section="Feature Branch" --body="$branch" --skip-missing 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _check_halt_after_for_project <project_name>
# Evaluates the HALT-AFTER sentinel for a single project on each wake.
#
# Scope resolution (project scope takes precedence):
#   1. $project_root/HALT-AFTER  — project-scoped sentinel
#   2. $KANBAN_ROOT/HALT-AFTER   — root-scoped sentinel (applies to all projects)
#
# Delegates to the Python halt_after module (python3 -m halt_after <scope_root>)
# which parses the sentinel token, evaluates the drain condition, and when
# drain is satisfied performs the atomic promotion (rm HALT-AFTER, touch HALT,
# append audit entry to release-state.md).
#
# Return values (mirroring halt_after exit codes):
#   0 — HALT-AFTER was promoted to HALT; caller MUST skip this project
#   1 — drain not yet satisfied; chain continues normally
#   2 — invalid token; warning already logged by Python; chain continues
#   3 — no HALT-AFTER present; chain continues
#   4 — HALT-AFTER check could not execute; warning logged; chain continues
#   5 — drain satisfied but promotion deferred: the project's Active RC is
#       not "none" (an RC is open). The satisfied drain is latched by the
#       Python module (rc_gate.py) so it is not lost -- it converts on a
#       later evaluation once the RC closes, without re-deriving the
#       condition. Chain continues normally; see the RC-close gate
#       invariant in team/pgai_agent_kanban/halt_after/rc_gate.py.
#
# Called once AFTER run_project_chain returns in wake_common_run (so promotion
# is decided after the agent drains its queue) and once inside the
# post-discovery block so a fresh promotion is honored mid-wake.
# ---------------------------------------------------------------------------
_check_halt_after_for_project() {
  local project_name="$1"
  local project_root
  project_root="$(pp_project_root "$project_name" 2>/dev/null)" || return 3

  # Determine the scope root: project scope takes precedence over root scope.
  # Delegates to the shared helper (team/scripts/lib/halt_after_invoke.sh) so
  # this resolution has exactly one implementation, shared with CM's
  # ship-time spend of the version-pinned sentinel
  # (team/scripts/lib/cm/release_finalize.sh, Step 12).
  local scope_root
  scope_root="$(pgai_halt_after_resolve_scope_root "$project_root" "$KANBAN_ROOT")" || return 3
  if [[ "$scope_root" == "$project_root" ]]; then
    log "project ${project_name}: HALT-AFTER sentinel found at project scope (${project_root}/HALT-AFTER)"
  else
    log "project ${project_name}: HALT-AFTER sentinel found at root scope (${KANBAN_ROOT}/HALT-AFTER)"
  fi

  # Delegate to the Python module for token parsing, drain evaluation, and the
  # RC-close gate (rc_gate.py) that decides whether a satisfied drain may
  # promote right now or must be latched and deferred until the project's
  # Active RC reads "none".
  #
  # When the sentinel is at root scope ($KANBAN_ROOT/HALT-AFTER), scope_root is
  # $KANBAN_ROOT but release-state.md lives under the per-project directory.
  # Pass project_root as the second argument so the Python module uses the correct
  # release-state.md for the rc-token bind and drain checks.  When sentinel is at
  # project scope, scope_root == project_root, so the second argument is redundant
  # but harmless (backward-compatible).
  local halt_after_exit
  local halt_after_output
  pgai_halt_after_run "$scope_root" "$project_root"
  halt_after_exit="$PGAI_HALT_AFTER_EXIT"
  halt_after_output="$PGAI_HALT_AFTER_OUTPUT"

  if [[ -n "$halt_after_output" ]]; then
    while IFS= read -r _ha_line; do
      log "project ${project_name}: halt_after: ${_ha_line}"
    done <<< "$halt_after_output"
  fi

  case "$halt_after_exit" in
    0)
      log "project ${project_name}: HALT-AFTER promoted to HALT (scope: ${scope_root}); project will be skipped this wake"
      ;;
    1)
      if grep -q "drain not yet satisfied" <<< "$halt_after_output"; then
        log "project ${project_name}: HALT-AFTER drain in progress — chain continues normally"
      else
        log "project ${project_name}: WARNING: HALT-AFTER check failed to run (exit ${halt_after_exit}) — chain continues"
        halt_after_exit=4
      fi
      ;;
    2)
      log "project ${project_name}: HALT-AFTER invalid token — warning logged; chain continues normally"
      ;;
    3)
      log "project ${project_name}: HALT-AFTER absent — chain continues normally"
      ;;
    5)
      log "project ${project_name}: HALT-AFTER drain satisfied but promotion deferred (Active RC open) — chain continues normally"
      ;;
    *)
      log "project ${project_name}: WARNING: HALT-AFTER check failed to run (exit ${halt_after_exit}) — chain continues"
      halt_after_exit=4
      ;;
  esac

  return "$halt_after_exit"
}

# ---------------------------------------------------------------------------
# _check_project_dev_tree <project_name>
# Per-project dev-tree existence gate for run_project_chain.
#
# Called AFTER pp_load_config so PP_dev_tree_path and PP_workflow_type are set.
#
# For git-workflow projects (git_mode != none): if PP_dev_tree_path is empty
# or not a directory, log the canonical skip line and return 1 (caller must
# skip this project and continue to the next).
#
# Projects whose workflow plugin declares git_mode=none are exempt — they have
# no dev tree by design (e.g. document workflow).  The plugin is loaded
# best-effort; a failed load is treated conservatively (dev tree required).
#
# Return values:
#   0 — dev tree present (or exempt); caller continues normally
#   1 — dev tree missing; caller must skip this project
# ---------------------------------------------------------------------------
_check_project_dev_tree() {
    local project_name="${1:-}"

    # Determine the workflow's git mode by loading its plugin.
    # Projects with git_mode=none have no dev tree by design — exempt.
    local _wf="${PP_workflow_type:-release}"
    local _wf_git_mode="rw"   # conservative default: require dev tree
    local _wf_load_exit=0
    set +e
    wf_load_plugin "$_wf" 2>/dev/null
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -eq 0 ]]; then
        _wf_git_mode="$(wf_git_mode 2>/dev/null || echo "rw")"
    fi

    if [[ "$_wf_git_mode" == "none" ]]; then
        return 0
    fi

    local _tree="${PP_dev_tree_path:-}"

    # Empty path or non-directory — skip with the canonical log line.
    if [[ -z "$_tree" || ! -d "$_tree" ]]; then
        log "project ${project_name}: dev tree '${_tree}' missing — skipping this project"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# run_overwatch_resolution_stage <project_name>
# Invokes pgai_agent_kanban.overwatch.resolution_stage for the named
# project when AGENT is overwatch. No-op for every other agent.
#
# This is the ONE call site both wake provider siblings (claude.sh, codex.sh)
# invoke from run_project_chain, once per project, on every OVERWATCH firing
# — including the firing the on-BLOCK trigger produces within seconds of a
# fresh BLOCKED task (see "on-BLOCK overwatch trigger" below and
# team/roles/OVERWATCH.md "Autonomous Block Resolution"). The stage
# itself decides whether there is anything to resolve; calling it on every
# firing (including ordinary firings with no ship-policy HALT present) is
# safe and cheap — it declines silently when there is nothing to evaluate.
#
# This call site is also where a project-scope HALT reaches the resolution
# stage's own classification (see the wake-loop carve-out comment at the
# per-project HALT fast-exit above): the outer loop lets an overwatch firing
# through despite a projects/<name>/HALT file so this call can classify it
# as ship-policy or operator-authored and narrate the matching decline
# itself, rather than the project being skipped unnarrated by the coarser
# gate before this ever runs.
#
# Runs with the per-project repo flock already held by the caller (see the
# "Per-project repo flock" block above this function's call site), so the
# drill's git-touching step (cm/cancel-rc.sh) never races another agent's
# git operations on the same dev tree.
#
# The stage runs in a subshell under the project's OVERWATCH firing lock
# (overwatch_run_with_firing_lock, overwatch_lib.sh), so two resolution
# stages never overwrite each other's in-progress marker; a firing already
# in progress for the project skips it, logged "firing-skipped:lock-held".
#
# Failure is logged and swallowed — a broken resolution stage must never
# abort the wake loop or prevent OVERWATCH's own task processing from
# running; the on-BLOCK trigger's degrade-to-next-hour-coverage guarantee
# (see OVERWATCH.md "Fire-and-forget") extends to this stage as well.
# ---------------------------------------------------------------------------
run_overwatch_resolution_stage() {
  local project_name="$1"
  [[ "$AGENT" == "overwatch" ]] || return 0
  mkdir -p "${KANBAN_ROOT}/logs" 2>/dev/null || true
  # Under the project's OVERWATCH firing lock (see the header above): a
  # firing already in progress for this project skips the stage, logged.
  (
    _overwatch_wake_load_firing_lock || exit 0
    overwatch_run_with_firing_lock "$project_name" "overwatch-wake-resolution-stage" \
      pp_run_ops pgai_agent_kanban.overwatch.resolution_stage \
        --project "$project_name" --kanban-root "$KANBAN_ROOT"
  ) >>"${KANBAN_ROOT}/logs/overwatch-resolution-stage.log" 2>&1 \
    || log "project ${project_name}: overwatch resolution stage exited non-zero (see logs/overwatch-resolution-stage.log)"
}

# ---------------------------------------------------------------------------
# _overwatch_wake_load_firing_lock
# Loads overwatch_lib.sh (overwatch_run_with_firing_lock) and
# overwatch_protocol.sh (its action-log row) from ${SCRIPT_DIR}/../lib, for
# the two overwatch wake steps; meant to run inside their subshells. A
# missing library is narrated and the step skipped (status 1), never run
# unlocked.
# ---------------------------------------------------------------------------
_overwatch_wake_load_firing_lock() {
  local lib_dir="${SCRIPT_DIR}/../lib"
  if ! declare -F overwatch_run_with_firing_lock >/dev/null 2>&1; then
    # shellcheck source=overwatch_lib.sh
    source "${lib_dir}/overwatch_lib.sh" 2>/dev/null || {
      echo "wake: overwatch_lib.sh not found in ${lib_dir}; overwatch step skipped" >&2
      return 1
    }
  fi
  if ! declare -F overwatch_log_action >/dev/null 2>&1; then
    # shellcheck source=overwatch_protocol.sh
    source "${lib_dir}/overwatch_protocol.sh" 2>/dev/null || true
  fi
  return 0
}

# ---------------------------------------------------------------------------
# run_overwatch_block_triage <project_name>
# Runs OVERWATCH's block-triage check (lib/overwatch-checks/
# check-block-triage.sh, overwatch_check_block_triage) for the named project
# when AGENT is overwatch: every BLOCKED task carrying a ## Block Record is
# classified and its decision carried out through the block-triage executor.
# No-op for every other agent.
#
# Called from run_project_chain BEFORE the chain takes the project's repo
# lock. The check takes that same lock itself, per task, through
# overwatch_halt_first_fix, so running it while this chain held the lock
# would refuse every task as contended.
#
# HALT test first: triage changes task state, so a present HALT -- the
# fleet-wide $KANBAN_ROOT/HALT, or the project's own HALT (the wake loop's
# overwatch carve-out lets a halted project through so the resolution
# stage can classify it) -- skips triage for this project with one "fix
# deferred: HALT present" log line naming the file. A ship-policy HALT is
# still resolved by run_overwatch_resolution_stage, which run_project_chain
# calls after this.
#
# Runs in a subshell, under the project's OVERWATCH firing lock
# (overwatch_run_with_firing_lock, overwatch_lib.sh): a sweep or nudge
# already firing for this project makes the wake skip triage, logged
# "firing-skipped:lock-held". The check sources its own libraries, and
# overwatch_halt_first_fix sets and clears an EXIT trap; neither may leak
# into the wake's own shell. Output goes to logs/overwatch-block-triage.log
# under the kanban root. A non-zero exit is logged and swallowed — block
# triage is quality class and never stops a wake.
# ---------------------------------------------------------------------------
run_overwatch_block_triage() {
  local project_name="$1"
  [[ "$AGENT" == "overwatch" ]] || return 0
  local halt_path
  for halt_path in "${KANBAN_ROOT}/HALT" "$(pp_project_root "$project_name")/HALT"; do
    [[ -f "$halt_path" ]] || continue
    log "project ${project_name}: block triage skipped -- HALT present at ${halt_path}"
    # BLOCK-WORTHY: an invariant by design -- a HALT is an operator's (or
    # CM's) stop, so triage, which changes task state, waits for it.
    # CENSUS-ANCHOR: wake-project-block-triage-halt-01
    pgai_emit_error_contract "wake.overwatch_block_triage_deferred_halt_present" \
      "fix deferred: HALT present at ${halt_path}; OVERWATCH block triage changed no task of project ${project_name}." "cat ${halt_path}" "$project_name"
    return 0
  done
  local check="${SCRIPT_DIR}/../lib/overwatch-checks/check-block-triage.sh"
  mkdir -p "${KANBAN_ROOT}/logs" 2>/dev/null || true
  (
    set +e
    export OVERWATCH_PROJECT="$project_name"
    _overwatch_wake_load_firing_lock || exit 0
    # shellcheck source=overwatch-checks/check-block-triage.sh
    source "$check" \
      && overwatch_run_with_firing_lock "$project_name" "overwatch-wake-block-triage" overwatch_check_block_triage
  ) >>"${KANBAN_ROOT}/logs/overwatch-block-triage.log" 2>&1 \
    || log "project ${project_name}: overwatch block triage exited non-zero (see logs/overwatch-block-triage.log)"
}

# ---------------------------------------------------------------------------
# BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS
# How long the sweep fire_block_triage_nudge starts keeps retrying a held
# repo lock (overwatch-sweep.sh --lock-wait) before it gives up with one
# line naming the holder. The nudge fires the moment its own chain releases
# that lock, so the wait only matters when another chain took the lock in
# between; a minute covers a chain's setup without delaying the nudged
# triage past the next scheduled sweep.
# ---------------------------------------------------------------------------
BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS=60

# ---------------------------------------------------------------------------
# fire_block_triage_nudge <project_name>
# The on-BLOCK nudge: starts OVERWATCH's block triage for every registered
# project (overwatch-sweep.sh --only block-triage) so a fresh block is
# triaged within seconds rather than at the next scheduled sweep.
#
# Called by run_project_chain AFTER it has released the project's repo lock,
# and only when that chain blocked a task (wd_finalize_task marked one [B]);
# a chain that blocked nothing never calls it. Firing after the release is
# what lets the woken triage take the lock: fired earlier, every task it
# tried would be refused as contended by the chain that blocked it.
#
# Guards: (a) a no-op when AGENT is overwatch, so OVERWATCH never nudges
# itself; (b) fully backgrounded (nohup, stdin closed) with its exit status
# ignored, so it adds no failure mode to the chain, and started from a
# subshell so it is not one of the wake's own jobs -- the wake's EXIT trap
# (cleanup_on_exit) kills every job still running, and the sweep usually
# outlives the wake that nudged it; (c) HALT and
# HALT_OVERWATCH gate the woken sweep at its own pre-flight, not here; (d)
# the woken sweep retries a held lock with backoff for up to
# BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS, then declines, leaving every task
# unchanged. Output goes to logs/overwatch-trigger.log under the kanban root.
# Always returns 0.
# ---------------------------------------------------------------------------
fire_block_triage_nudge() {
  local project_name="$1"
  [[ "$AGENT" != "overwatch" ]] || return 0
  local sweep="${SCRIPT_DIR}/../overwatch-sweep.sh"
  mkdir -p "${KANBAN_ROOT}/logs" 2>/dev/null || true
  log "project ${project_name}: this chain blocked a task and has released the repo lock -- nudging OVERWATCH: overwatch-sweep.sh --only block-triage --lock-wait ${BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS}"
  ( KANBAN_ROOT="$KANBAN_ROOT" nohup "$sweep" --only block-triage --lock-wait "$BLOCK_TRIAGE_NUDGE_LOCK_WAIT_SECONDS" \
      </dev/null >>"${KANBAN_ROOT}/logs/overwatch-trigger.log" 2>&1 & ) || true
  return 0
}
