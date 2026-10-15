#!/usr/bin/env bash
# team/scripts/lib/wake_reconcile_sweeps.sh
#
# The two once-per-wake, once-per-project reconciliation sweeps —
# reconcile_stale_active and reconcile_terminal_worktrees — kept in their
# own stage file so wake_task_state.sh stays under the 800-line-per-file
# ceiling (team/scripts/lint_engine_size_dupe.py). Both are called from
# run_project_chain's three sweep points (wake_driver.sh) and share one
# dependency set (worktree.sh, project_paths.sh, the task-liveness
# primitives in wake_task_state.sh, and pp_run_ops for the queue scan).
#
# Public entry points
# --------------------
#   reconcile_stale_active
#       Walks every [A] marker in this agent's own queue file ($BACKLOG)
#       and brings each into line with its task's own ## State — status.md
#       is the single truth and the marker is derived from it. Decision
#       order, per [A] entry:
#
#         1. Read ## State first. DONE or WONT-DO is the whole answer: the
#            marker is corrected to [x] (through transition_task_state, so
#            it is derived by the one shared write path) and liveness is
#            never consulted — a finished task cannot be "actively being
#            worked" whatever any process or record says.
#         2. Only for a non-terminal state (or a missing status.md) ask
#            task_liveness_verdict (wake_task_state.sh) about the pid its
#            dispatch recorded:
#              held        -> keep [A]; the agent is still running.
#              unparseable -> keep [A]; a record exists (or its path cannot
#                             be resolved) and this sweep cannot prove the
#                             process dead, so it never tears down a task
#                             whose own state says it is still working.
#              stale       -> orphaned: reset to BACKLOG through the
#                             zero-progress path (below).
#              absent      -> orphaned, same path as stale. A WORKING task
#                             under [A] with no liveness record at all is
#                             NOT kept: the record is written by
#                             provider_dispatch the moment the dispatch pid
#                             is known, inside the same lock-holding chain
#                             that wrote the [A], so by the time any sweep
#                             can observe the marker the record is on disk
#                             unless its write failed (a WARNING at
#                             dispatch time) or it was removed out of band.
#                             In both cases no process can be located, and
#                             one bounded reset — the zero-progress cap
#                             escalates to BLOCKED + Needs Human after
#                             repeated resets, and the next dispatch writes
#                             a fresh record — is preferred over a marker
#                             kept forever. The log line names the absent
#                             record so the decision is never silent.
#         3. An orphaned task has any worktree/branch the interrupted
#            attempt left behind torn down first, so the next
#            create_task_worktree call gets a clean slate, then goes through
#            zero_progress_advance_or_escalate: BACKLOG below the cap,
#            BLOCKED + Needs Human at it.
#
#   reconcile_terminal_worktrees
#       Companion sweep, keyed off status.md's own ## State (the SOT) rather
#       than the queue marker: tears down a registered worktree whose own
#       task already reached DONE or WONT-DO but whose finalize-stage
#       teardown was skipped by a lock-loss reverification, and in the same
#       pass corrects that task's [A] marker to [x] when the entry is in
#       this agent's own queue file. See its own header below for the full
#       contract (scope, safety, and why BLOCKED is deliberately left
#       alone). Called from the same three sweep points in wake_driver.sh,
#       immediately after reconcile_stale_active.
#
# Both sweeps answer "is the agent that owns this task still running?"
# through the shared task_liveness_verdict / task_liveness_is_alive
# predicates (wake_task_state.sh) rather than each carrying its own
# `pgrep -f "$task_id"` guard — see those functions' headers for why a
# command-line substring search answers a strictly larger,
# false-positive-prone question. Defined at source time, available as soon
# as this file is sourced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh    — BACKLOG, TASKS_ROOT globals, and log().
#   wake_task_state.sh   — mark_backlog, get_state, normalize_status_file,
#     task_liveness_record_path, task_liveness_record, task_liveness_verdict,
#     task_liveness_is_alive; sourced immediately before this file. It
#     also self-sources wake_task_id_gate.sh, which supplies
#     _narrate_malformed_queue_task_ids and (through task_ids.sh)
#     kanban_task_id_pattern — the refusal and the shape
#     reconcile_stale_active checks every [A] id against.
#   overwatch_protocol.sh — zero_progress_advance_or_escalate
#     (reconcile_stale_active's cap-aware reset-or-escalate — a separate
#     counter from ## Transient Retry, see that file's header; the
#     function owns its own transition_task_state / set_block /
#     pgai_format_error_contract calls).
#   wake_task_transition.sh — transition_task_state, called directly by
#     _sweep_correct_terminal_marker (both sweeps' [A] -> [x] correction)
#     and via zero_progress_advance_or_escalate above as part of
#     reconcile_stale_active's reset/escalation path; sourced by
#     wake_common.sh immediately after wake_task_state.sh.
#   worktree.sh, project_paths.sh — pgai_worktree_path, pgai_worktree_base,
#     teardown_task_worktree, pp_prefix_branch (reconcile_stale_active's git
#     teardown and reconcile_terminal_worktrees' own sweep); sourced by the
#     calling wake script before run_project_chain invokes either function —
#     pre-existing, unchanged by this move.
#
# python3 must be on PATH (used by reconcile_stale_active's own heredoc).
# Sourced by wake_common.sh immediately after wake_task_state.sh.

# ---------------------------------------------------------------------------
# The finalize record: <task_dir>/logs/finalize.record
#
# Evidence that the wake's own finalize (wd_finalize_task,
# wake_driver_finalize.sh) handled the task's current dispatch. Written there
# once the queue marker has been derived from the final state -- so a DONE
# has already been through the CODER/WRITER completion gate -- and removed by
# wd_invoke_task (wake_driver_invoke.sh) before a new dispatch writes [A].
# Plain key=value lines: final_state, pid (the wake process that finalized)
# and finalized_at (UTC ISO-8601). Only its presence is read.
#
# Both sweeps below read it for one decision. A task whose ## State reads
# DONE but that has no record reached DONE without the wake's finalize: the
# wake was stopped while the agent ran, or lost its repo lock before the
# queue write. Its done-claim never met the completion gate, so
# _sweep_gate_unfinalized_done runs that same gate before the marker can
# become [x] or the worktree is torn down.
#
#   task_finalize_record_path <task_dir>     echo the record's path
#   task_finalize_record_write <task_dir> <final_state>
#   task_finalize_record_clear <task_dir>    0 once no record remains
#   task_finalize_record_present <task_dir>  0 when a record exists
# Each returns 1 on a blank <task_dir>; write/clear also on an I/O failure.
# ---------------------------------------------------------------------------
task_finalize_record_path() {
  [[ -n "${1:-}" ]] || return 1
  printf '%s/logs/finalize.record\n' "${1%/}"
}

task_finalize_record_write() {
  local path
  path="$(task_finalize_record_path "${1:-}")" || return 1
  mkdir -p "$(dirname "$path")" 2>/dev/null || return 1
  printf 'final_state=%s\npid=%s\nfinalized_at=%s\n' \
    "${2:-}" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$path" 2>/dev/null
}

task_finalize_record_clear() {
  local path
  path="$(task_finalize_record_path "${1:-}")" || return 1
  rm -f -- "$path" 2>/dev/null || true
  [[ ! -e "$path" ]]
}

task_finalize_record_present() {
  local path
  path="$(task_finalize_record_path "${1:-}")" || return 1
  [[ -f "$path" ]]
}

# ---------------------------------------------------------------------------
# _sweep_gate_unfinalized_done <task_id> <task_status> <prefix>
#
# Called by both sweeps for a task whose ## State reads DONE, before either
# one renders [x] or tears the worktree down. Echoes the state the sweep must
# act on:
#   DONE     -- a finalize record exists (the wake's finalize already ran the
#               gate), or the completion gate passed now.
#   BLOCKED  -- the completion gate refused the done-claim now. The gate has
#               already written ## State BLOCKED, ## Blockers, Needs Human
#               and the Block Record; the caller derives the [B] marker.
#
# The gate is coder_writer_completion_gate (wake_lifecycle.sh), called with
# the same six arguments wd_finalize_task gives it, resolved the way
# wd_setup_task_worktree resolves them: the role from the task README's
# ## Role, the source branch from its ## Source Branch, the feature branch
# from pp_prefix_branch, the worktree from pgai_worktree_path. The gate's own
# scope rules decide what passes trivially (a role other than CODER/WRITER,
# no branch to verify, a worktree that is already gone). A task folder with
# no README.md names neither a role nor a branch, so there is nothing to
# verify and it passes the same way. Each outcome is one log line.
# ---------------------------------------------------------------------------
_sweep_gate_unfinalized_done() {
  local task_id="$1" task_status="$2" prefix="$3"
  local task_dir readme
  task_dir="$(dirname "$task_status")"
  readme="${task_dir}/README.md"

  if task_finalize_record_present "$task_dir"; then
    printf 'DONE\n'
    return 0
  fi
  if [[ ! -f "$readme" ]]; then
    log "${prefix}: ${task_id} is DONE with no finalize record and no README.md naming a role or branch; nothing for the completion gate to verify" >/dev/null
    printf 'DONE\n'
    return 0
  fi

  local role source_branch="" feature_branch="" worktree_path=""
  role="$(get_role_from_readme "$readme" 2>/dev/null || true)"
  source_branch="$(get_source_branch_from_readme "$readme" 2>/dev/null || true)"
  feature_branch="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "feature/${task_id}" 2>/dev/null || true)"
  worktree_path="$(pgai_worktree_path "$task_id" 2>/dev/null || true)"

  log "${prefix}: ${task_id} is DONE with no finalize record (its wake did not finalize this dispatch); running the completion gate before trusting the done-claim" >/dev/null
  local gated
  gated="$(coder_writer_completion_gate "$task_id" "$task_status" "${role,,}" \
    "$feature_branch" "$source_branch" "$worktree_path")"
  if [[ "$gated" == "BLOCKED" ]]; then
    log "${prefix}: ${task_id} completion gate refused the done-claim; ## State is now BLOCKED (see its ## Blockers)" >/dev/null
    printf 'BLOCKED\n'
    return 0
  fi
  printf 'DONE\n'
}

# ---------------------------------------------------------------------------
# _sweep_mark_gate_refused <task_id> <prefix>
#
# Derives the [B] marker for a task _sweep_gate_unfinalized_done just moved to
# BLOCKED. Only this agent's own queue file ($BACKLOG) is written; a task
# whose entry lives in another agent's queue keeps its status.md BLOCKED and
# has its marker corrected by that agent's own wake. Always returns 0.
# ---------------------------------------------------------------------------
_sweep_mark_gate_refused() {
  local task_id="$1" prefix="$2"
  if [[ -n "${BACKLOG:-}" && -f "${BACKLOG:-}" ]] && mark_backlog "$task_id" "B" 2>/dev/null; then
    log "${prefix}: ${task_id} marker set to [B] after the completion gate refused its done-claim"
  else
    log "${prefix}: ${task_id} has no entry in ${BACKLOG:-the queue file of this agent}; its BLOCKED marker is left to the owning agent's wake"
  fi
  return 0
}

# ---------------------------------------------------------------------------
# _sweep_correct_terminal_marker <task_id> <task_status> <task_state> <prefix>
#
# Shared by both sweeps: rewrites the [A] queue marker of a task whose own
# ## State already reads DONE or WONT-DO to the [x] that state renders as.
# Goes through transition_task_state — the same call the agent's own final
# transition and wake prep's marker normalization make — so the marker is
# derived from ## State by the one shared write path and never chosen here;
# ## State is re-written with its current value (a no-op on the state) and
# normalize_status_file tidies the spacing that re-write leaves behind.
#
# Only this agent's own queue file ($BACKLOG) is examined: a task whose [A]
# entry lives in another agent's queue is left for that agent's own sweep.
# <prefix> is the log-line prefix of the calling sweep.
#
# Returns 0 when a marker was corrected; 1 when there was nothing to correct
# (no $BACKLOG, or no [A] entry for this task in it); 2 when the transition
# refused — both surfaces left exactly as they were, per
# transition_task_state's own atomicity contract, with a WARNING log line.
# ---------------------------------------------------------------------------
_sweep_correct_terminal_marker() {
  local task_id="$1" task_status="$2" task_state="$3" prefix="$4"
  [[ -n "${BACKLOG:-}" && -f "${BACKLOG:-}" ]] || return 1
  # Liberal match: optional leading dash, any whitespace around the A, the
  # task id as a whole word.
  grep -Eq "^[[:space:]]*-?[[:space:]]*\[[[:space:]]*A[[:space:]]*\][[:space:]]+${task_id}([[:space:]]|\$)" "$BACKLOG" \
    || return 1
  if transition_task_state "$task_id" "$task_status" "$task_state"; then
    normalize_status_file "$task_status"
    log "${prefix}: ${task_id} is ${task_state} in status.md; corrected marker [A] -> [x]"
    return 0
  fi
  log "${prefix}: ${task_id} WARNING: marker correction [A] -> [x] failed; status.md/marker left as they were (no partial update)"
  return 2
}

reconcile_stale_active() {
  # Every [A] id is checked against the canonical task-id shape before it
  # can become a worktree path, a branch name or a status path: a line
  # outside it is refused dual-channel here (wake_task_id_gate.sh) and the
  # scan below skips it, so it is never torn down, reset or re-marked.
  _narrate_malformed_queue_task_ids "$BACKLOG" 'A' "stale-active scan"
  local stale_ids
  # Every [A] key in file order that has the canonical task-id shape (the
  # shared queue scan; the rule "A=*" reads no status.md).
  stale_ids=$(pp_run_ops pgai_agent_kanban.ops scan_queue "$BACKLOG" "${TASKS_ROOT:-}" "$(kanban_task_id_pattern)" 0 "A=*")

  if [[ -z "$stale_ids" ]]; then
    return 0
  fi

  log "stale-active reconciliation: checking [A] markers for orphaned tasks..."

  local reconciled=0
  local corrected=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue

    local task_status="${TASKS_ROOT}/${task_id}/status.md"
    local task_state=""
    if [[ -f "$task_status" ]]; then
      task_state="$(get_state "$task_status" 2>/dev/null || echo "")"
    fi

    # Decision 1 — the task's own ## State. A terminal state is the whole
    # answer: the marker renders ## State, so a DONE/WONT-DO task under [A]
    # is corrected to [x] without ever asking whether a process is alive.
    # A DONE its wake never finalized meets the completion gate first; a
    # refusal leaves the task BLOCKED under [B] instead.
    if [[ "$task_state" == "DONE" ]]; then
      task_state="$(_sweep_gate_unfinalized_done "$task_id" "$task_status" "stale-active")"
      if [[ "$task_state" == "BLOCKED" ]]; then
        _sweep_mark_gate_refused "$task_id" "stale-active"
        continue
      fi
    fi
    case "$task_state" in
      DONE|WONT-DO)
        if _sweep_correct_terminal_marker "$task_id" "$task_status" "$task_state" "stale-active"; then
          corrected=$((corrected + 1))
        fi
        continue
        ;;
    esac

    # Decision 2 — liveness, only now that the state is known to be
    # non-terminal (or status.md is missing). See this file's header for
    # the four verdicts and why "absent" is treated as orphaned.
    local verdict
    verdict="$(task_liveness_verdict "$task_id")"
    case "$verdict" in
      held)
        log "stale-active: ${task_id} has active process, keeping [A]"
        continue
        ;;
      unparseable)
        log "stale-active: ${task_id} liveness cannot be determined (record present but unreadable, or record path unresolvable); keeping [A]"
        continue
        ;;
      absent)
        log "stale-active: ${task_id} has no liveness record at $(task_liveness_record_path "$task_id" 2>/dev/null || echo '<unresolved>') and its own state is ${task_state:-unknown}; treating as orphaned"
        ;;
    esac

    if [[ ! -f "$task_status" ]]; then
      log "stale-active: ${task_id} missing status.md; resetting marker to [ ]"
      mark_backlog "$task_id" " "
      reconciled=$((reconciled + 1))
      continue
    fi

    log "stale-active: ${task_id} has no active process, resetting to [ ]/BACKLOG"

    # --- Teardown prior worktree/branch before BACKLOG reset ---
    # The interrupted attempt may have left a worktree/branch on disk; tear
    # them down (teardown_task_worktree from worktree.sh, pp_prefix_branch
    # from project_paths.sh — both sourced by the caller before
    # reconcile_stale_active runs) so the next create_task_worktree call gets
    # a clean slate instead of hard-blocking on "branch already exists".
    # PP_dev_tree_path (falls back to PGAI_DEV_TREE_PATH) locates the dev
    # tree. Best-effort: failures WARN but never block the state reset.
    local _sa_dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
    if [[ -n "$_sa_dev_tree" && -d "$_sa_dev_tree/.git" ]]; then
      # Compute the prefix-aware feature branch name (mirrors reset.sh Step 4).
      local _sa_feature_branch=""
      local _sa_fb_exit=0
      set +e
      _sa_feature_branch="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "feature/${task_id}" 2>/dev/null)"
      _sa_fb_exit=$?
      set -e
      if [[ $_sa_fb_exit -ne 0 || -z "$_sa_feature_branch" ]]; then
        _sa_feature_branch="feature/${task_id}"   # safe fallback when helper fails
      fi

      # Step 1: remove the worktree directory (branch may be checked out there).
      local _sa_wt_path=""
      if _sa_wt_path="$(pgai_worktree_path "$task_id" 2>/dev/null)" && [[ -d "$_sa_wt_path" ]]; then
        log "stale-active: ${task_id} tearing down prior worktree at ${_sa_wt_path}"
        if ! teardown_task_worktree "$task_id" "$_sa_dev_tree" 2>/dev/null; then
          log "stale-active: ${task_id} WARNING: worktree teardown failed (non-fatal; create_task_worktree will retry idempotently)"
        else
          log "stale-active: ${task_id} prior worktree removed"
        fi
      fi

      # Step 2: delete the feature branch if it still exists locally.
      if git -C "$_sa_dev_tree" rev-parse --verify "refs/heads/${_sa_feature_branch}" >/dev/null 2>&1; then
        log "stale-active: ${task_id} deleting prior feature branch ${_sa_feature_branch}"
        # Suppress stdout as well as stderr — git branch -D emits "Deleted branch ..."
        # to stdout, which would appear as stray output in cron logs.
        if git -C "$_sa_dev_tree" branch -D "$_sa_feature_branch" >/dev/null 2>&1; then
          log "stale-active: ${task_id} feature branch ${_sa_feature_branch} deleted"
        else
          log "stale-active: ${task_id} WARNING: could not delete feature branch ${_sa_feature_branch} (non-fatal)"
        fi
      fi
    else
      log "stale-active: ${task_id} no dev tree configured or accessible — skipping git teardown"
    fi

    # Zero-progress ceiling (separate from ## Transient Retry — see
    # overwatch_protocol.sh's zero_progress_* header): below cap, keep
    # resetting to BACKLOG; at cap, escalate instead. Mechanics shared with
    # handle_working_after_exit's fallback via zero_progress_advance_or_escalate.
    if zero_progress_advance_or_escalate "$task_id" "$task_status" \
        "stale_active_orphan" "orphaned WORKING task, no live process" \
        "wake.reconcile.zero-progress-cap" >/dev/null; then
      reconciled=$((reconciled + 1))
    fi
  done <<< "$stale_ids"

  if [[ $reconciled -gt 0 ]]; then
    log "stale-active reconciliation: reset ${reconciled} orphaned [A] task(s)"
  fi
  if [[ $corrected -gt 0 ]]; then
    log "stale-active reconciliation: corrected ${corrected} terminal-task [A] marker(s) to [x]"
  fi
}

# ---------------------------------------------------------------------------
# reconcile_terminal_worktrees
#
# Companion sweep to reconcile_stale_active above, for the complementary
# leak: a task whose own status.md already reached a terminal state (DONE or
# WONT-DO) but whose worktree was never torn down. This happens when
# wd_finalize_task's own teardown step (wake_driver_finalize.sh, "Worktree
# teardown") never ran — the "merge" mutating-phase-boundary reverification
# in process_one_task (wake_driver.sh) deliberately returns before finalize
# whenever this chain lost the project repo lock while the agent was
# running, so a DIFFERENT chain that now legitimately holds the lock never
# stomps a race. That reverification is correct and stays as-is; its
# necessary side effect is that the losing chain's own finalize — and with
# it, worktree teardown — never executes for that one task. The task's own
# ## State can still read DONE (the agent wrote it before the chain lost the
# lock), so the leftover-worktree symptom recurs on every lock race,
# independent of whether the terminal-state teardown in wd_finalize_task
# itself is correct.
#
# Unlike reconcile_stale_active (keyed off a stale "[A]" queue marker),
# this function is keyed off status.md's own ## State — the SOT per
# SOP-core's "the marker is derived" — so it heals the leak regardless of
# what the queue marker shows (self-corrected by the agent or not). Having
# established that the task is terminal and that finalize never ran for
# it, the same pass also corrects the task's [A] marker to [x] when the
# entry is in this agent's own queue file (_sweep_correct_terminal_marker,
# best-effort), and then removes the task's liveness record as
# housekeeping: a task id is never dispatched twice, so the record can
# never usefully answer a future query, and reconcile_stale_active reads
# ## State before it ever consults a record, so its absence changes no
# later decision for a terminal task.
#
# Scope and safety:
#   - Only worktrees actually registered under this project's own
#     pgai_worktree_base() are considered (git worktree list --porcelain
#     against the project dev tree, filtered to that base path) — never a
#     sibling project's worktrees, and never the canonical dev tree itself.
#   - A worktree whose task_id's own dispatch pid still answers kill -0 is
#     skipped, via the shared task_liveness_is_alive predicate that also
#     backs reconcile_stale_active's own live-process guard — never race a
#     task that is still actually running.
#   - Torn down only when the task's own ## State is DONE or WONT-DO.
#   - BLOCKED is deliberately left alone: wd_finalize_task itself retains
#     a BLOCKED worktree for TESTER and for a completion-gate refusal so an
#     operator can inspect it, and this sweep cannot tell those apart from
#     an ordinary BLOCKED after the fact — retaining every BLOCKED worktree
#     here is the conservative choice that never discards evidence.
#   - WORKING, BACKLOG, WAITING, and a missing/malformed status.md are all
#     left untouched (never remove the worktree of a task still
#     WORKING).
#   - Idempotent and best-effort: teardown_task_worktree's own idempotency
#     covers an already-removed or already-pruned worktree, and a failed
#     teardown here WARNs and is retried on the next call rather than
#     failing this sweep or the chain that calls it.
#
# Dependencies: pgai_worktree_base, teardown_task_worktree (worktree.sh);
# get_state, task_liveness_is_alive, task_liveness_record_path
# (wake_task_state.sh); _sweep_correct_terminal_marker (this file);
# TASKS_ROOT, BACKLOG, log() (wake_bootstrap.sh); PP_dev_tree_path /
# PGAI_DEV_TREE_PATH (project_paths.sh, exported by pp_load_config before
# run_project_chain calls this function).
# ---------------------------------------------------------------------------
reconcile_terminal_worktrees() {
  local dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
  if [[ -z "$dev_tree" || ! -d "${dev_tree}/.git" ]]; then
    return 0
  fi

  local wt_base
  wt_base="$(pgai_worktree_base 2>/dev/null)" || return 0
  [[ -n "$wt_base" && -d "$wt_base" ]] || return 0
  wt_base="${wt_base%/}"

  local registered
  registered="$(git -C "$dev_tree" worktree list --porcelain 2>/dev/null | awk '/^worktree /{print substr($0, 10)}')"
  [[ -z "$registered" ]] && return 0

  local reaped=0
  local wt_path task_id task_status task_state
  while IFS= read -r wt_path; do
    [[ -z "$wt_path" ]] && continue
    # Only worktrees living directly under THIS project's own worktree base
    # are in scope — never the canonical dev tree, never a sibling project.
    case "$wt_path" in
      "${wt_base}"/*) : ;;
      *) continue ;;
    esac
    task_id="$(basename "$wt_path")"
    [[ -z "$task_id" ]] && continue

    # Never touch a task whose own dispatch pid is still alive — shared
    # predicate with reconcile_stale_active (task_liveness_is_alive, above).
    if task_liveness_is_alive "$task_id"; then
      continue
    fi

    task_status="${TASKS_ROOT}/${task_id}/status.md"
    [[ -f "$task_status" ]] || continue

    task_state="$(get_state "$task_status" 2>/dev/null || echo "")"
    # A DONE its wake never finalized meets the completion gate before its
    # worktree -- the evidence the gate reads -- is torn down; a refusal keeps
    # the worktree for inspection, exactly as wd_finalize_task does.
    if [[ "$task_state" == "DONE" ]]; then
      task_state="$(_sweep_gate_unfinalized_done "$task_id" "$task_status" "reconcile-terminal-worktrees")"
      if [[ "$task_state" == "BLOCKED" ]]; then
        _sweep_mark_gate_refused "$task_id" "reconcile-terminal-worktrees"
        continue
      fi
    fi
    case "$task_state" in
      DONE|WONT-DO)
        log "reconcile-terminal-worktrees: ${task_id} is ${task_state} with a registered worktree at ${wt_path} (finalize teardown did not run for it); tearing down"
        if teardown_task_worktree "$task_id" "$dev_tree" 2>/dev/null; then
          log "reconcile-terminal-worktrees: ${task_id} worktree removed"
          reaped=$((reaped + 1))
          # Best-effort housekeeping: this task_id will never be dispatched
          # again (task ids are unique forever), so its liveness record can
          # never usefully answer a future query — remove it rather than
          # leaving a permanently-dead record behind. Only after a
          # successful teardown: a record that still names a dead pid is
          # what lets the next cycle retry a failed teardown, since an
          # absent record reads as alive above. Never fatal to the sweep.
          rm -f "$(task_liveness_record_path "$task_id" 2>/dev/null)" 2>/dev/null || true
        else
          log "reconcile-terminal-worktrees: WARNING: worktree teardown failed for ${task_id} (non-fatal; will retry next cycle)"
        fi
        # Same pass, same knowledge: the task is terminal and finalize never
        # ran for it, so the [A] its wake wrote is still there whenever the
        # entry is in this agent's own queue. Best-effort; a task whose
        # entry lives in another agent's queue is left for that agent's own
        # sweep, and a refused transition leaves both surfaces untouched.
        _sweep_correct_terminal_marker "$task_id" "$task_status" "$task_state" "reconcile-terminal-worktrees" || true
        ;;
      *)
        ;;
    esac
  done <<< "$registered"

  if [[ $reaped -gt 0 ]]; then
    log "reconcile-terminal-worktrees: reaped ${reaped} terminal-task worktree(s)"
  fi
}
