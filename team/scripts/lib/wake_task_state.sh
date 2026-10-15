#!/usr/bin/env bash
# team/scripts/lib/wake_task_state.sh
#
# Wake task-state stage: status-file readers and writers, prerequisite
# checks, state transitions, WAITING/BLOCKED promotion and recheck, and
# the task-liveness primitives the reconciliation sweeps read. One of the
# single-concern lib stages wake_common.sh sources in order.
#
# Public entry point
# -------------------
#   set_state <status_file> <new_state>
#       Rewrites a task's ## State block to <new_state>, preserving every
#       other section byte-for-byte; an empty block is filled in place (every
#       section splice here stops its body at the next "##" line, never past
#       it). Called by both wake provider scripts
#       (claude.sh, codex.sh) at every state transition, and by the
#       reconciliation sweeps in the sibling stage file
#       wake_reconcile_sweeps.sh.
#
# transition_task_state (the single ## State + queue-marker write path)
# lives in the sibling stage file wake_task_transition.sh, and the two
# once-per-wake, once-per-project reconciliation sweeps
# (reconcile_stale_active, reconcile_terminal_worktrees) live in
# wake_reconcile_sweeps.sh — both kept separate so each stage file stays
# under the engine-decomposition line ceiling
# (team/scripts/lint_engine_size_dupe.py); see their own headers for the
# full contracts. wake_reconcile_sweeps.sh calls several primitives this
# file defines — mark_backlog, get_state, task_liveness_record_path,
# task_liveness_record, task_liveness_verdict, task_liveness_is_alive — so
# it is sourced immediately after this file.
#
# Other functions here — ensure_status_file, _status_section_re,
# _read_status_section, get_first_pending_task, get_state,
# normalize_status_file, set_block, set_state_or_insert, set_block_or_append,
# block_task_with_record, block_task_with_unreadable_state,
# stamp_model_field, mark_backlog, _promote_mark_pending,
# promote_waiting_to_backlog, promote_blocked_to_backlog,
# _blocked_check_active_rc, recheck_waiting_tasks,
# recheck_blocked_tasks, task_liveness_record_path, task_liveness_record,
# task_liveness_verdict, task_liveness_is_alive — are shared primitives, not
# private helpers of set_state alone; claude.sh/codex.sh call most directly.
# The task_liveness_* family answers "is the agent that owns this task still
# running?" from the pid recorded at dispatch, for both sweeps in
# wake_reconcile_sweeps.sh — see task_liveness_is_alive's own header below
# for why a `pgrep -f "$task_id"` substring search would answer a strictly
# larger, false-positive-prone question.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh     — BACKLOG, TASKS_ROOT globals, and log().
#   wake_common.sh's own remaining code — get_release_state_field (used by
#     _blocked_check_active_rc).
#   overwatch_protocol.sh — transient_retry_field, transient_retry_limit
#     (recheck_blocked_tasks' transient-API-error auto-retry branch).
#   lock_provenance.sh    — pgai_lock_read_provenance,
#     pgai_lock_pid_liveness_verdict (task_liveness_verdict's dispatch-pid
#     read); sourced by wake_driver.sh before run_project_chain invokes
#     either sweep in wake_reconcile_sweeps.sh.
#
# Python: this file carries no inline Python. Every status.md and queue
# read or rewrite is a call into the shared modules through pp_run_ops
# (pp_run_ops.sh, self-sourced below unless the caller already defines it):
#   pgai_agent_kanban.task_status  read --raw (section bodies), render
#                                  (section rewrites, blank-line collapse,
#                                  ## Model upsert)
#   pgai_agent_kanban.ops          render_marker / get_markers (queue
#                                  markers), scan_queue (the three scans)
#   pgai_agent_kanban.overwatch.block_record / .block_evidence
#                                  (block_task_with_record's
#                                  ## Block Record, through block_record.sh)
# python3 must be on PATH.
# Sourced by wake_common.sh immediately after wake_bootstrap.sh.
#
# Task-id gate: every site that reads a task id out of a queue line or a
# ## Prerequisites token validates it against the one canonical shape
# task_ids.sh's kanban_task_id_pattern prints before it can become a path;
# an id outside that shape is refused dual-channel and skipped. The three
# queue scans here (get_first_pending_task, recheck_waiting_tasks,
# recheck_blocked_tasks) call the sibling stage file wake_task_id_gate.sh
# for that refusal, and check_prerequisites lives there outright — it is a
# task-id reader end to end (sourced below, so callers see no difference).
#
# Atomic writes: every status.md and queue-file writer in this file
# (ensure_status_file, normalize_status_file, set_state, set_block,
# stamp_model_field, mark_backlog, both promote_*) replaces its target
# through atomic_write.sh's pgai_atomic_write — same-directory scratch file
# plus rename — so a concurrent reader (API, dashboard) sees the complete
# old bytes or the complete new bytes, never an empty or half-written file,
# and the file keeps its own mode. The module call (task_status render,
# ops render_marker) prints the whole new file to stdout and writes
# nothing itself; a call that exits non-zero leaves the target untouched
# and no scratch file behind.
# The promote_* writers validate the queue entry, commit status.md, then
# rewrite the marker through mark_backlog (see _promote_mark_pending).
# atomic_write.sh and pp_run_ops.sh are self-sourced below so these writers
# work whether this file is loaded through wake_common.sh or on its own.

_WAKE_TASK_STATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=atomic_write.sh
source "${_WAKE_TASK_STATE_LIB_DIR}/atomic_write.sh"
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_WAKE_TASK_STATE_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=wake_task_id_gate.sh
source "${_WAKE_TASK_STATE_LIB_DIR}/wake_task_id_gate.sh"
# block_task_with_record writes the ## Block Record; a caller's own
# block_record_write (wake_bootstrap.sh's sourcing, or a test stub) wins.
# shellcheck source=block_record.sh
declare -F block_record_write >/dev/null || source "${_WAKE_TASK_STATE_LIB_DIR}/block_record.sh"

# --- Helpers ---

ensure_status_file() {
  local status_file="$1"
  if [[ ! -f "$status_file" ]] || [[ ! -s "$status_file" ]]; then
    if ! pgai_atomic_write "$status_file" cat <<'EOF'
## State
BACKLOG

## Summary
TBD

## Artifacts
none

## Blockers
none

## Needs Human
no

## Next Recommended Step
TBD

## Instruction Conflicts
none
EOF
    then
      return 1
    fi
    log "created status.md template for task"
  fi
}

# _status_section_re <heading_re> — print the section regex (re.M only)
# whose body boundary the bash libs' remaining inline status.md readers
# share — heading line, then body lines none starting with "##" — so an
# empty body reads empty, never as the next section. Group 2 is the body.
# <heading_re> is a regex ('## State', or '##[^\S\n]+State' for any inner
# spacing); trailing "\r" is accepted so CRLF files read. The readers and
# writers in this file go through pgai_agent_kanban.task_status instead;
# this stays as the reference boundary those other readers are checked
# against.
_status_section_re() {
  printf '%s' "(^${1}"'[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)'
}

# _read_status_section <status_file> <heading> — print the ## <heading>
# section body, stripped (empty for an empty body); return non-zero, printing
# nothing, when the heading is absent. <heading> is the bare name ('State',
# 'Needs Human'); the shared reader matches it with any spacing or case.
_read_status_section() {
  pp_run_ops pgai_agent_kanban.task_status read "$1" --section="$2" --raw
}

# Serves the first [ ]/[B] entry whose ## State reads BACKLOG. A task with no
# status.md, no ## State, or an empty ## State is served too, so the prep
# stage's own state check blocks it (block_task_with_unreadable_state)
# instead of the scan skipping it. Returns 1 when nothing is pending, and
# also -- logging the scan's exit code -- when the scan itself fails.
get_first_pending_task() {
  _narrate_malformed_queue_task_ids "$BACKLOG" ' B' "pending-task scan"
  local task_id scan_exit=0
  task_id="$(pp_run_ops pgai_agent_kanban.ops scan_queue \
    "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" 1 ' B=,BACKLOG')" || scan_exit=$?
  if [[ $scan_exit -ne 0 ]]; then
    log "WARNING: pending-task scan of ${BACKLOG} failed (exit ${scan_exit}; its error is above); no task dispatched from this queue this wake"
    return 1
  fi
  [[ -n "$task_id" ]] || return 1
  printf '%s\n' "$task_id"
}


# Prints the ## State value uppercased (empty for an empty body); returns 1
# with a message on stderr when status.md has no ## State heading.
get_state() {
  local state
  state="$(_read_status_section "$1" 'State')" \
    || { echo "Could not parse ## State block in $1" >&2; return 1; }
  printf '%s\n' "${state^^}"
}

# Cuts every run of two or more blank lines in status.md to one.
normalize_status_file() {
  local status_file="$1"
  pgai_atomic_write "$status_file" pp_run_ops pgai_agent_kanban.task_status render \
    "$status_file" --collapse-blank-lines
}

# Rewrites the ## State body to <new_state> (stripped), keeping the heading
# line and the separator before the next section byte-for-byte; fails with
# "Could not update ## State block", writing nothing, when there is none.
set_state() {
  local status_file="$1"
  local new_state="$2"
  pgai_atomic_write "$status_file" pp_run_ops pgai_agent_kanban.task_status render \
    "$status_file" --section=State --body="$new_state"
}

# set_block <status_file> <heading> <body> — set_state for any ## <heading>.
set_block() {
  local status_file="$1"
  local heading="$2"
  local body="$3"
  pgai_atomic_write "$status_file" pp_run_ops pgai_agent_kanban.task_status render \
    "$status_file" --section="$heading" --body="$body"
}

# set_state_or_insert <status_file> <new_state> — set_state for a status.md
# that may have lost its ## State heading: a present heading is rewritten
# exactly as set_state does; an absent one is inserted after ## Model,
# ## Role, ## Participant or ## Task (the first present), or at the end when
# none is, and blank-line runs are then cut to one.
set_state_or_insert() {
  pgai_atomic_write "$1" pp_run_ops pgai_agent_kanban.task_status render "$1" \
    --section=State --body="$2" \
    --insert-after=Model --insert-after=Role --insert-after=Participant --insert-after=Task
}

# set_block_or_append <status_file> <heading> <body> — set_block that appends
# ## <heading> at the end of the file when the section is absent.
set_block_or_append() {
  pgai_atomic_write "$1" pp_run_ops pgai_agent_kanban.task_status render "$1" \
    --section="$2" --body="$3" --append-missing="$2"
}

# block_task_with_record <task_id> <status_file> <reason_code> <owner_role> <blockers> <evidence> [extra_evidence...]
#   The wake's BLOCKED write for a task whose status.md may have lost
#   sections: writes ## State BLOCKED (set_state_or_insert -- a missing
#   heading is inserted), ## Blockers <blockers> and ## Needs Human yes
#   (set_block_or_append), the ## Block Record (<reason_code>, <owner_role>,
#   <evidence> and any extra key=value entries), and cuts blank-line runs to
#   one. The queue marker is the caller's to write, as [B], after this
#   returns, so ## State and the marker agree. Returns 1, logging the file,
#   only when ## State itself could not be written; every later write is
#   best-effort and logged. Never exits the caller, errexit on or off, when
#   called as `block_task_with_record ... || log ...`.
block_task_with_record() {
  local task_id="$1" status_file="$2" reason_code="$3" owner_role="$4" blockers="$5"
  shift 5
  if ! set_state_or_insert "$status_file" "BLOCKED"; then
    log "task ${task_id}: ERROR: could not write ## State BLOCKED to ${status_file} (see the error above); repair that file by hand so it is readable UTF-8 with a '## State' section reading BLOCKED"
    return 1
  fi
  set_block_or_append "$status_file" "Blockers" "$blockers" \
    || log "task ${task_id}: WARNING: ## Blockers not written to ${status_file}"
  set_block_or_append "$status_file" "Needs Human" "yes" \
    || log "task ${task_id}: WARNING: ## Needs Human not written to ${status_file}"
  block_record_write "$status_file" "$reason_code" "$owner_role" "$@" \
    || log "task ${task_id}: WARNING: Block Record not written"
  normalize_status_file "$status_file" \
    || log "task ${task_id}: WARNING: blank-line normalization of ${status_file} failed"
  return 0
}

# block_task_with_unreadable_state <task_id> <status_file> <read_state> [owner_candidate...]
#   The BLOCKED write for a task whose ## State does not read as one of the
#   six task states: the heading is absent, its body is empty, or it holds
#   an unrecognised word. <read_state> is what get_state printed (empty when
#   it failed). Blocks through block_task_with_record with ## Blockers
#   naming the fault and the Block Record reason invalid_state (owner role:
#   the first candidate that is an owner role, else the README's ## Role).
#   Same marker contract and return codes as block_task_with_record.
block_task_with_unreadable_state() {
  local task_id="$1" status_file="$2" read_state="$3"
  shift 3
  local fault
  if [[ -n "$read_state" ]]; then
    fault="Invalid task state detected: ${read_state}"
  elif _read_status_section "$status_file" 'State' >/dev/null 2>&1; then
    fault="Invalid task state detected: ## State is empty in ${status_file}"
  else
    fault="Invalid task state detected: ${status_file} has no ## State heading"
  fi
  log "task ${task_id}: ${fault}; marking BLOCKED"
  block_task_with_record "$task_id" "$status_file" invalid_state \
    "$(block_record_owner_role "$status_file" "$@")" "$fault" \
    "$(block_record_evidence --diag-log "${LOG_FILE:-}")"
}

# --- stamp_model_field <status_file> <model_string> ---
# Write the resolved model string into the ## Model section of a task status file.
# If the section is absent it is inserted after ## Role (or after ## Participant
# when ## Role is absent, or at the end when neither anchor is found).
# Only ## Model is touched; all other sections are preserved byte-identically.
# Best-effort: on failure a warning is logged to stderr and the task continues.
stamp_model_field() {
  local status_file="$1"
  local model_value="$2"
  pgai_atomic_write "$status_file" pp_run_ops pgai_agent_kanban.task_status render \
    "$status_file" --section=Model --body="$model_value" \
    --insert-after=Role --insert-after=Participant
  local _stamp_exit=$?
  if [[ $_stamp_exit -ne 0 ]]; then
    echo "WARNING: stamp_model_field failed (exit ${_stamp_exit}) for ${status_file}; continuing" >&2
  fi
}

# mark_backlog <task_id> <marker> — set every $BACKLOG line for <task_id> to
# [<marker>] (one of " " W A B x), touching nothing else on the line; fails
# with "Could not update backlog entry for <task_id>", writing nothing, when
# the queue has no line for it. The rewrite is ops.set_marker's own
# (render_marker prints it); the commit is pgai_atomic_write's.
mark_backlog() {
  local task_id="$1"
  local marker="$2"
  pgai_atomic_write "$BACKLOG" pp_run_ops pgai_agent_kanban.ops render_marker \
    "$BACKLOG" "$task_id" "$marker" || return 1
}

# --- _promote_mark_pending <task_id> <status_file> ---
# Second half of both promote_* writers: status.md already reads BACKLOG, so
# rewrite the queue marker to [ ]. status.md is the single truth and the
# marker is derived from it; a marker write that fails here is reported
# naming both files (the next wake re-derives the marker), never rolled back.
_promote_mark_pending() {
  local task_id="$1"
  local task_status="$2"
  if ! mark_backlog "$task_id" " "; then
    echo "promote: ${task_status} now reads ## State BACKLOG but the queue marker for ${task_id} in ${BACKLOG} could not be rewritten to [ ]; the next wake re-derives the marker from ## State" >&2
    return 1
  fi
  return 0
}

# promote_waiting_to_backlog <task_id> <status_file> — requires a [W] or
# [ ] $BACKLOG line for <task_id> (refusing "Could not find [W] or [ ] queue
# entry for <task_id>", writing nothing, otherwise); sets ## State BACKLOG,
# ## Blockers none and the promotion ## Summary (each required), cuts
# blank-line runs to one, then derives the [ ] marker.
promote_waiting_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  local queue_markers markers_exit=0
  queue_markers="$(pp_run_ops pgai_agent_kanban.ops get_markers "$BACKLOG" "$task_id")" || markers_exit=$?
  [[ "$markers_exit" == 0 || "$markers_exit" == 3 ]] || return 1
  if ! grep -qxF -e '[W]' -e '[ ]' <<< "$queue_markers"; then
    echo "Could not find [W] or [ ] queue entry for ${task_id}" >&2
    return 1
  fi
  if ! pgai_atomic_write "$task_status" pp_run_ops pgai_agent_kanban.task_status render "$task_status" \
      --section=State --body=BACKLOG \
      --section=Blockers --body=none \
      --section=Summary --body="Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied." \
      --collapse-blank-lines
  then
    return 1
  fi
  _promote_mark_pending "$task_id" "$task_status"
}

# promote_blocked_to_backlog <task_id> <status_file> <cleared_reason> —
# requires a [B] $BACKLOG line for <task_id> (refusing "Could not find [B]
# queue entry for <task_id>" otherwise); resets ## State, ## Blockers,
# ## Blocked By Agent, ## Blocked Reason, ## Needs Human and ## Summary
# (each only where present), cuts blank-line runs to one, then derives the
# [ ] marker. Every other section is left as written — the durable
# ## Transient Retry, ## Zero Progress and ## Block Record records included,
# so a retry count, a zero-progress count and a block's triage history all
# survive the return to BACKLOG.
promote_blocked_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  local cleared_reason="$3"
  local queue_markers markers_exit=0
  queue_markers="$(pp_run_ops pgai_agent_kanban.ops get_markers "$BACKLOG" "$task_id")" || markers_exit=$?
  [[ "$markers_exit" == 0 || "$markers_exit" == 3 ]] || return 1
  if ! grep -qxF -e '[B]' <<< "$queue_markers"; then
    echo "Could not find [B] queue entry for ${task_id}" >&2
    return 1
  fi
  if ! pgai_atomic_write "$task_status" pp_run_ops pgai_agent_kanban.task_status render "$task_status" \
      --section=State --body=BACKLOG \
      --section=Blockers --body=none \
      --section="Blocked By Agent" --body=none \
      --section="Blocked Reason" --body=none \
      --section="Needs Human" --body=no \
      --section=Summary --body="Promoted from BLOCKED to BACKLOG by wake script: blocker cleared (${cleared_reason})." \
      --skip-missing --collapse-blank-lines
  then
    return 1
  fi
  _promote_mark_pending "$task_id" "$task_status"
}

# --- BLOCKED-task blocker-check dispatch table ---

_blocked_check_active_rc() {
  local active_rc
  active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
  if [[ -z "$active_rc" || "$active_rc" == "none" ]]; then
    return 0  # cleared
  fi
  return 1  # still blocked
}

recheck_waiting_tasks() {
  log "re-checking WAITING tasks for satisfied prerequisites..."

  _narrate_malformed_queue_task_ids "$BACKLOG" 'W ' "WAITING scan"
  # Every [W] line, and every [ ] or [] line whose ## State reads WAITING.
  # A failing scan is logged and ends this re-check only: the wake goes on.
  local waiting_ids scan_exit=0
  waiting_ids=$(pp_run_ops pgai_agent_kanban.ops scan_queue \
    "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" 0 'W=*' ' []=WAITING') || scan_exit=$?
  if [[ $scan_exit -ne 0 ]]; then
    log "WARNING: WAITING scan of ${BACKLOG} failed (exit ${scan_exit}; its error is above); no WAITING task re-checked this pass"
    return 0
  fi

  if [[ -z "$waiting_ids" ]]; then
    log "no WAITING tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "WAITING task ${task_id} missing files; skipping re-check"
      continue
    fi

    local check_exit=0
    set +e
    check_prerequisites "$task_readme" >/dev/null
    check_exit=$?
    set -e

    if [[ $check_exit -eq 0 ]]; then
      log "WAITING task ${task_id}: prerequisites satisfied, promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_waiting_to_backlog "$task_id" "$task_status"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$waiting_ids"

  log "re-check complete: promoted ${promoted} task(s) from WAITING to BACKLOG"
}

recheck_blocked_tasks() {
  log "re-checking BLOCKED tasks for cleared blockers..."

  _narrate_malformed_queue_task_ids "$BACKLOG" 'B' "BLOCKED scan"
  # Every [B] line whose ## State reads BLOCKED.
  # A failing scan is logged and ends this re-check only: the wake goes on.
  local blocked_ids scan_exit=0
  blocked_ids=$(pp_run_ops pgai_agent_kanban.ops scan_queue \
    "$BACKLOG" "$TASKS_ROOT" "$(kanban_task_id_pattern)" 0 'B=BLOCKED') || scan_exit=$?
  if [[ $scan_exit -ne 0 ]]; then
    log "WARNING: BLOCKED scan of ${BACKLOG} failed (exit ${scan_exit}; its error is above); no BLOCKED task re-checked this pass"
    return 0
  fi

  if [[ -z "$blocked_ids" ]]; then
    log "no BLOCKED tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "BLOCKED task ${task_id} missing files; skipping re-check"
      continue
    fi

    local needs_human
    # An absent or empty ## Needs Human reads as "yes": never auto-resolve a
    # task whose human flag is unrecorded.
    needs_human="$(_read_status_section "$task_status" 'Needs Human')" || needs_human=""
    needs_human="${needs_human,,}"
    [[ -n "$needs_human" ]] || needs_human="yes"
    if [[ "$needs_human" == "yes" ]]; then
      log "BLOCKED task ${task_id}: Needs Human=yes, skipping auto-resolve"
      continue
    fi

    local blocked_reason
    blocked_reason="$(_read_status_section "$task_status" 'Blocked Reason')" || blocked_reason=""

    local cleared=false
    local cleared_desc=""

    if grep -qi "active rc" <<< "$blocked_reason"; then
      local check_exit=0
      set +e
      _blocked_check_active_rc "$task_id" "$blocked_reason"
      check_exit=$?
      set -e
      if [[ $check_exit -eq 0 ]]; then
        cleared=true
        cleared_desc="Active RC=none"
      fi
    fi

    # --- Transient API error auto-retry ---
    # A task whose durable ## Transient Retry record has reason_code
    # transient_api_error and Needs Human=no (confirmed above) is eligible
    # for auto-promotion to BACKLOG. Reads the SAME durable record
    # handle_working_after_exit writes — never ## Blocked Reason prose
    # (reset to "none" on every promotion, so untrustworthy as a count
    # source). The record's own retry_limit is preferred (judges a
    # promoted-then-reclassified task against the ceiling in effect when
    # recorded); transient_retry_limit() falls back for older/malformed
    # records. The "< ceiling" comparison mirrors
    # handle_working_after_exit's exactly, so the two paths can never
    # disagree; this branch is a safety double-check for TRANSIENT_RETRY_CAP
    # having been lowered between the record's write and this recheck.
    if [[ "$cleared" == "false" ]]; then
      local _retry_reason_code
      _retry_reason_code="$(transient_retry_field "$task_status" "reason_code" "")"
      if [[ "$_retry_reason_code" == "transient_api_error" ]]; then
        local _transient_cap
        _transient_cap="$(transient_retry_field "$task_status" "retry_limit" "")"
        [[ "$_transient_cap" =~ ^[0-9]+$ ]] || _transient_cap="$(transient_retry_limit)"
        local _retry_count
        _retry_count="$(transient_retry_field "$task_status" "retry_count" "0")"
        [[ "$_retry_count" =~ ^[0-9]+$ ]] || _retry_count=0

        # Below the ceiling the retry also waits out its backoff window
        # (transient_backoff_due, overwatch_protocol.sh: 5, 10, then 20
        # minutes after the block as shipped); absent the gate, it is due.
        local _not_before=""
        if [[ "$_retry_count" -lt "$_transient_cap" ]] && declare -F transient_backoff_due >/dev/null 2>&1 \
            && ! _not_before="$(transient_backoff_due "$task_status")"; then
          log "BLOCKED task ${task_id}: transient API error — auto-retry (attempt ${_retry_count}/${_transient_cap}) waits for its backoff window, not before ${_not_before}"
        elif [[ "$_retry_count" -lt "$_transient_cap" ]]; then
          cleared=true
          cleared_desc="transient API error auto-retry (attempt ${_retry_count}/${_transient_cap})"
          log "BLOCKED task ${task_id}: transient API error — scheduling auto-retry (attempt ${_retry_count}/${_transient_cap})"
        else
          log "BLOCKED task ${task_id}: transient API error — retry cap reached (${_retry_count}/${_transient_cap}); leaving for human"
        fi
      fi
    fi

    if [[ "$cleared" == "true" ]]; then
      log "BLOCKED task ${task_id}: blocker cleared (${cleared_desc}), promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_blocked_to_backlog "$task_id" "$task_status" "$cleared_desc"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$blocked_ids"

  log "re-check complete: promoted ${promoted} BLOCKED task(s) to BACKLOG"
}

# ---------------------------------------------------------------------------
# task_liveness_record_path <task_id>
#
# Echoes the path to <task_id>'s dispatch-liveness record:
#   $KANBAN_ROOT/locks/task-liveness/<task_id>.record
#
# Same root, and the same holder/pid/op/task provenance shape, as every
# per-project repo lock file (lock_provenance.sh, pp_repo_lock_path) — an
# operator inspecting locks/ sees every record in one place. A plain record
# file, not a mutex: no flock is taken, because "is the pid this task's own
# dispatch started still alive" needs no exclusion, only a durable answer.
#
# Returns 1 (empty stdout) when KANBAN_ROOT is unset or task_id is blank —
# task_liveness_verdict reports that as "unparseable" (cannot determine),
# never as "absent" (no record exists yet).
# ---------------------------------------------------------------------------
task_liveness_record_path() {
  local task_id="$1"
  if [[ -z "${KANBAN_ROOT:-}" || -z "$task_id" ]]; then
    return 1
  fi
  printf '%s/locks/task-liveness/%s.record\n' "${KANBAN_ROOT}" "$task_id"
}

# ---------------------------------------------------------------------------
# task_liveness_record <task_id> <holder> <pid> <op>
#
# Write (overwrite) <task_id>'s dispatch-liveness record naming the pid the
# caller just launched to work it. Called once by each provider's
# provider_dispatch (provider_claude.sh, provider_codex.sh) immediately
# after backgrounding the CLI invocation and capturing its pid — this is
# the write side of the liveness answer: recording the ONE process that is
# this task's own dispatch, so the read side (task_liveness_verdict /
# task_liveness_is_alive) never has to guess from a command-line search.
#
# Beside holder/pid/op/task the record carries pid_start (the pid's start
# time, pgai_proc_start_time) and boot_id (pgai_boot_id), so a later
# process that reuses the pid -- or any pid after a reboot or container
# restart -- reads as stale rather than as this dispatch still running.
# Either is written blank where /proc cannot answer, and a blank field is
# not compared.
#
# Best-effort: a failure to resolve the path, create the directory, or
# write the file logs a WARNING to stderr and returns 1, but is never
# fatal to the caller's dispatch — a missing record reads as "absent" at
# read time, which task_liveness_is_alive treats as alive (never the
# dangerous direction of a false "safe to reclaim").
# ---------------------------------------------------------------------------
task_liveness_record() {
  local task_id="$1" holder="$2" pid="$3" op="${4:-dispatch}"
  local record_path
  record_path="$(task_liveness_record_path "$task_id" 2>/dev/null)" || {
    echo "wake_task_state.sh: task_liveness_record: KANBAN_ROOT unset or task_id blank; liveness for '${task_id:-<blank>}' will read as undetermined" >&2
    return 1
  }
  mkdir -p "$(dirname "$record_path")" 2>/dev/null || {
    echo "wake_task_state.sh: task_liveness_record: cannot create $(dirname "$record_path")" >&2
    return 1
  }
  local pid_start="" boot_id=""
  if declare -F pgai_proc_start_time >/dev/null 2>&1; then
    pid_start="$(pgai_proc_start_time "$pid")"
    boot_id="$(pgai_boot_id)"
  fi
  printf 'holder=%s\npid=%s\nop=%s\ntask=%s\npid_start=%s\nboot_id=%s\n' \
    "$holder" "$pid" "$op" "$task_id" "$pid_start" "$boot_id" > "$record_path" 2>/dev/null || {
    echo "wake_task_state.sh: task_liveness_record: cannot write ${record_path}" >&2
    return 1
  }
  return 0
}

# ---------------------------------------------------------------------------
# task_liveness_verdict <task_id>
#
# Echoes one word classifying <task_id>'s dispatch-liveness record, so a
# caller can tell a task that was never dispatched (or whose record was
# already reaped) apart from a record that exists but cannot be read:
#   absent      -- no record file exists for this task_id.
#   held        -- a record exists and its pid still answers kill -0, and
#                  is the recorded process (same boot, same start time).
#   stale       -- a record exists and its pid does NOT answer kill -0, or
#                  answers but was started under another boot or at
#                  another time than the record says (a reused pid).
#   unparseable -- a record exists but carries no usable pid, OR the record
#                  path itself cannot be resolved (KANBAN_ROOT unset, blank
#                  task_id): liveness cannot be determined either way.
# held/stale/unparseable come from the shared pgai_lock_pid_liveness_verdict
# classifier (lock_provenance.sh); absent is decided here, before that
# classifier ever sees an empty string. Always exits 0 with one word on stdout.
# ---------------------------------------------------------------------------
task_liveness_verdict() {
  local task_id="$1"
  local record_path content verdict
  if ! record_path="$(task_liveness_record_path "$task_id" 2>/dev/null)"; then
    echo "unparseable"
    return 0
  fi
  if [[ ! -f "$record_path" ]]; then
    echo "absent"
    return 0
  fi
  content="$(pgai_lock_read_provenance "$record_path" 2>/dev/null)"
  verdict="$(pgai_lock_pid_liveness_verdict "$content" 2>/dev/null)" || verdict="unparseable"
  echo "${verdict:-unparseable}"
}

# ---------------------------------------------------------------------------
# task_liveness_is_alive <task_id>
#
# Boolean form of task_liveness_verdict: "is the agent process that owns
# task_id still running?", answered from the pid task_liveness_record wrote
# at dispatch — never from a `pgrep -f "$task_id"` command-line substring
# search, which answers a strictly larger question and false-positives on
# any unrelated process that merely mentions the task id (a sibling agent's
# dispatch prompt quoting an old task id, an operator's diagnostic grep).
#
#   stale                        -> returns 1 ("dead"): the sweep may proceed.
#   held / absent / unparseable  -> returns 0 ("alive"): the sweep skips.
#
# Only a recorded pid that provably no longer runs counts as dead; an absent
# or unreadable record reads as alive because reclaiming a live agent's
# worktree is the damaging direction and leftovers are recoverable by hand.
# A caller that must act differently on an absent record (reconcile_stale_active
# does, for a task still WORKING) calls task_liveness_verdict directly; one
# whose decision does not depend on liveness (a task already DONE or
# WONT-DO) reads the state first and never asks.
# ---------------------------------------------------------------------------
task_liveness_is_alive() {
  local verdict
  verdict="$(task_liveness_verdict "$1")"
  [[ "$verdict" == "stale" ]] && return 1
  return 0
}
