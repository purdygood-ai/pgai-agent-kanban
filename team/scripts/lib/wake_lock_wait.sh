#!/usr/bin/env bash
# team/scripts/lib/wake_lock_wait.sh
#
# Wake lock-wait stage: how a wake chain takes its project's repo lock when
# another wake chain already holds it, and the starvation guard that keeps
# one role from losing that lock firing after firing. One of the
# single-concern lib stages wake_driver.sh sources, kept separate so the
# driver stays under the engine-decomposition line ceiling
# (team/scripts/lint_engine_size_dupe.py).
#
# Waiting for a wake-chain holder
# -------------------------------
# A wake chain holds the repo lock for its whole run, and an idle chain
# (an empty queue, or a PM wake that finds nothing to discover) lets go of
# it again within a minute or two. Roles fire on a staggered cron, so a
# role that gave up at the first contended probe would find that same
# short holder in place at every firing and never run. run_project_chain
# therefore takes the lock through wlw_acquire_chain_lock, which:
#   - makes an ordinary pgai_lock_acquire attempt first, so an uncontended
#     lock is taken exactly as before;
#   - on contention, keeps retrying with backoff (pgai_lock_acquire_waiting,
#     lock_provenance.sh) for up to [wake] lock_wait_seconds (exported as
#     LOCK_WAIT_SECONDS, default 120) -- but ONLY while the holder's
#     provenance op is a wake chain's ("wake-chain:<project>"). Any other
#     holder (an OVERWATCH repair, an operator tool, a lock with no
#     provenance) is not waited on: the attempt defers at once, as before;
#   - never waits past the batch's remaining runtime budget
#     (MAX_RUNTIME_SECONDS from WAKE_START_EPOCH), and stops waiting the
#     moment the operator stop file (STOP_FILE, wake_stop_file_present)
#     appears;
#   - counts the seconds waited per project in this firing
#     (_WLW_WAITED_SECONDS), so the first pass and the end-of-iteration
#     retry pass (wake_common.sh) share ONE lock_wait_seconds budget per
#     project per firing rather than waiting it out twice.
# A refusal (pgai_lock_acquire's return 2: incomplete provenance, an
# unusable lock directory) is never retried -- pgai_lock_acquire_waiting
# returns it at once. The lock is taken with the same four provenance
# fields the chain always recorded (holder=$AGENT, pid=$$,
# op=wake-chain:<project>, task=none), and every attempt is a non-blocking
# flock, so two roles never hold the lock at once.
#
# Starvation guard
# ----------------
# A role that ends a firing deferred on repo-lock contention while it has a
# BACKLOG task in its own queue adds one to a per-project, per-role count
# persisted across firings at
#     $KANBAN_ROOT/locks/lock-starvation/<project>/<role>.state
# (plain "key=value" lines: deferrals, task, updated_at). The count resets
# -- the file is removed -- when the role takes the lock, and when it
# defers with no BACKLOG task. At [wake] lock_starvation_firings (exported
# as LOCK_STARVATION_FIRINGS, default 3; 0 turns the guard off) and on
# every deferral after it, the deferring wake logs the starvation WARNING.
# From then on, until the starved role takes the lock or runs out of
# BACKLOG work, every PM wake skips its discovery pipeline
# (wlw_starved_role_ready, called by run_project_chain), so PM stops
# taking the lock for discovery while that role's task is ready. The guard
# changes no task state, writes no HALT, and refuses nothing; it only
# narrates and steps PM aside.
#
# Public entry points
# -------------------
#   wlw_acquire_chain_lock <project> <lock_file> <out_fd_var>
#       Take the repo lock as above. <out_fd_var> names the caller's
#       `local` fd variable, exactly as for pgai_lock_acquire. Returns 0
#       held, 1 still contended (or not waited on), 2 refused.
#   wlw_lock_wait_budget <project>
#       Set WLW_WAIT_BUDGET to the seconds this project may still wait in
#       this firing: lock_wait_seconds less what it already waited, capped
#       at the batch's remaining runtime budget (WLW_BATCH_LEFT), never
#       below 0. Call directly, not through $(...).
#   wlw_role_ready_task <project> <role>
#       Echo the first task in <role>'s queue whose own status.md reads
#       BACKLOG; return 1 when there is none.
#   wlw_starvation_note_acquired <project>
#       Reset $AGENT's starvation count for <project> (it took the lock).
#   wlw_starvation_note_deferral <project>
#       Record that $AGENT ends this firing deferred on <project>'s repo
#       lock: count it while a BACKLOG task waits, reset it otherwise, and
#       log the starvation warning at and past the threshold. Always 0.
#   wlw_starved_role_ready <project>
#       Return 0 when some role's count for <project> has reached the
#       threshold and that role still has a BACKLOG task, setting
#       WLW_STARVED_ROLE, WLW_STARVED_TASK and WLW_STARVED_DEFERRALS;
#       return 1 otherwise. Call directly, not through $(...).
#
# Time is read through lock_provenance.sh's pgai_lock_wait_now and paused
# through its pgai_lock_wait_pause, the same pair pgai_lock_acquire_waiting
# uses, so a test that redefines those two drives the whole wait on a
# controlled clock.
#
# Dependencies (must already be defined by the caller)
# ----------------------------------------------------
#   lock_provenance.sh  — pgai_lock_acquire_waiting, pgai_lock_read_provenance,
#                         pgai_lock_describe_provenance, pgai_lock_field,
#                         pgai_lock_wait_now
#   discovery_guards.sh — _disc_backlog_task_states (via wake_bootstrap.sh)
#   project_paths.sh    — pp_queue_path
#   wake_common.sh      — wake_stop_file_present
#   wake_bootstrap.sh   — log(), KANBAN_ROOT, AGENT, WAKE_START_EPOCH

# Seconds each project has spent waiting for its repo lock in this firing.
declare -gA _WLW_WAITED_SECONDS 2>/dev/null || true

# Per-attempt state the keep-waiting hook reads and writes.
_WLW_WAIT_PROJECT=""
_WLW_WAIT_STARTED=false
_WLW_WAIT_STOPPED=""

WLW_WAIT_BUDGET=0
WLW_BATCH_LEFT=""
WLW_STARVED_ROLE=""
WLW_STARVED_TASK=""
WLW_STARVED_DEFERRALS=0

# Liberal whole-number read: the value when it is decimal digits, else the
# fallback (config_loader.sh has already refused a malformed configured
# value; this only covers a caller that never loaded the config).
_wlw_int() {
  local _v="${1:-}"
  _v="${_v//[[:space:]]/}"
  if [[ "$_v" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$(( 10#$_v ))"
  else
    printf '%s\n' "${2:-0}"
  fi
}

_wlw_lock_wait_seconds() {
  _wlw_int "${LOCK_WAIT_SECONDS:-}" 120
}

wlw_starvation_threshold() {
  _wlw_int "${LOCK_STARVATION_FIRINGS:-}" 3
}

wlw_lock_wait_budget() {
  local _project="$1"
  local _cfg _spent _left _now
  _cfg="$(_wlw_lock_wait_seconds)"
  _spent="${_WLW_WAITED_SECONDS[$_project]:-0}"
  _left=$(( _cfg - _spent ))
  WLW_BATCH_LEFT=""
  if [[ "${MAX_RUNTIME_SECONDS:-}" =~ ^[0-9]+$ && "${WAKE_START_EPOCH:-}" =~ ^[0-9]+$ ]]; then
    _now="$(pgai_lock_wait_now)"
    WLW_BATCH_LEFT=$(( MAX_RUNTIME_SECONDS - (_now - WAKE_START_EPOCH) ))
    (( WLW_BATCH_LEFT >= 0 )) || WLW_BATCH_LEFT=0
    (( WLW_BATCH_LEFT < _left )) && _left="$WLW_BATCH_LEFT"
  fi
  (( _left >= 0 )) || _left=0
  WLW_WAIT_BUDGET="$_left"
}

# The remaining batch budget for a log line: "<N>s", or "unbounded" when
# the wake has no batch clock (a caller that never loaded the config).
_wlw_batch_left_text() {
  if [[ -n "$WLW_BATCH_LEFT" ]]; then printf '%ss\n' "$WLW_BATCH_LEFT"; else printf 'unbounded\n'; fi
}

# True when the lock's current provenance names a wake chain as its holder.
_wlw_holder_is_wake_chain() {
  local _op
  _op="$(pgai_lock_field "$(pgai_lock_read_provenance "$1")" op)"
  [[ "$_op" == wake-chain:* ]]
}

# PGAI_LOCK_WAIT_WHILE hook for pgai_lock_acquire_waiting: called after a
# contended attempt that still has budget, before the pause.
_wlw_keep_waiting() {
  local _lock_file="$1"
  local _desc
  _desc="$(pgai_lock_describe_provenance "$(pgai_lock_read_provenance "$_lock_file")")"
  if wake_stop_file_present; then
    _WLW_WAIT_STOPPED="stop file"
    log "project ${_WLW_WAIT_PROJECT}: not waiting for the repo lock: stop file present at ${STOP_FILE}"
    return 1
  fi
  if ! _wlw_holder_is_wake_chain "$_lock_file"; then
    _WLW_WAIT_STOPPED="holder"
    log "project ${_WLW_WAIT_PROJECT}: not waiting for the repo lock: its holder is not a wake chain (${_desc})"
    return 1
  fi
  if [[ "$_WLW_WAIT_STARTED" != "true" ]]; then
    _WLW_WAIT_STARTED=true
    log "project ${_WLW_WAIT_PROJECT}: waiting up to ${WLW_WAIT_BUDGET}s for the repo lock held by wake chain ${_desc} (lock_wait_seconds=$(_wlw_lock_wait_seconds), batch budget left=$(_wlw_batch_left_text))"
  fi
  return 0
}

wlw_acquire_chain_lock() {
  local _wlw_project="$1" _wlw_lock_file="$2" _wlw_fd_var="$3"
  local _wlw_rc=0 _wlw_start _wlw_waited

  wlw_lock_wait_budget "$_wlw_project"
  _WLW_WAIT_PROJECT="$_wlw_project"
  _WLW_WAIT_STARTED=false
  _WLW_WAIT_STOPPED=""
  _wlw_start="$(pgai_lock_wait_now)"

  local PGAI_LOCK_WAIT_WHILE="_wlw_keep_waiting"
  pgai_lock_acquire_waiting "$WLW_WAIT_BUDGET" "$_wlw_lock_file" "$AGENT" "$$" \
    "wake-chain:${_wlw_project}" "none" "$_wlw_fd_var" || _wlw_rc=$?

  _wlw_waited=$(( $(pgai_lock_wait_now) - _wlw_start ))
  (( _wlw_waited >= 0 )) || _wlw_waited=0
  _WLW_WAITED_SECONDS[$_wlw_project]=$(( ${_WLW_WAITED_SECONDS[$_wlw_project]:-0} + _wlw_waited ))

  if [[ "$_WLW_WAIT_STARTED" == "true" ]]; then
    if [[ $_wlw_rc -eq 0 ]]; then
      log "project ${_wlw_project}: repo lock acquired after waiting ${_wlw_waited}s"
    elif [[ $_wlw_rc -eq 1 && -z "$_WLW_WAIT_STOPPED" ]]; then
      log "project ${_wlw_project}: repo lock still held after waiting ${_wlw_waited}s (lock_wait_seconds=$(_wlw_lock_wait_seconds))"
    fi
  elif [[ $_wlw_rc -eq 1 && "$WLW_WAIT_BUDGET" -eq 0 && "$(_wlw_lock_wait_seconds)" -gt 0 ]]; then
    log "project ${_wlw_project}: no repo-lock wait left this firing (waited ${_WLW_WAITED_SECONDS[$_wlw_project]}s of lock_wait_seconds=$(_wlw_lock_wait_seconds), batch budget left=$(_wlw_batch_left_text))"
  fi
  return "$_wlw_rc"
}

wlw_role_ready_task() {
  local _project="$1" _role="$2"
  local _queue _id _state
  _queue="$(pp_queue_path "$_project" "$_role" 2>/dev/null)" || return 1
  [[ -n "$_queue" && -f "$_queue" ]] || return 1
  while IFS=$'\t' read -r _id _state; do
    if [[ -n "$_id" && "$_state" == "BACKLOG" ]]; then
      printf '%s\n' "$_id"
      return 0
    fi
  done < <(_disc_backlog_task_states "$_project" "$_queue" 2>/dev/null || true)
  return 1
}

_wlw_starvation_file() {
  printf '%s/locks/lock-starvation/%s/%s.state\n' "${KANBAN_ROOT}" "$1" "$2"
}

# Echo the recorded deferral count in a state file (0 when absent or unreadable).
_wlw_starvation_count() {
  local _n=""
  [[ -f "$1" ]] && _n="$(sed -n 's/^deferrals=//p' "$1" 2>/dev/null | head -n1)"
  _wlw_int "$_n" 0
}

wlw_starvation_note_acquired() {
  local _file
  _file="$(_wlw_starvation_file "$1" "$AGENT")"
  [[ -f "$_file" ]] || return 0
  rm -f -- "$_file" 2>/dev/null || true
  log "project ${1}: lock starvation count for ${AGENT} reset: repo lock acquired"
  return 0
}

wlw_starvation_note_deferral() {
  local _project="$1"
  local _threshold _file _task _count _tmp
  _threshold="$(wlw_starvation_threshold)"
  _file="$(_wlw_starvation_file "$_project" "$AGENT")"

  if ! _task="$(wlw_role_ready_task "$_project" "$AGENT")"; then
    if [[ -f "$_file" ]]; then
      rm -f -- "$_file" 2>/dev/null || true
      log "project ${_project}: lock starvation count for ${AGENT} reset: no BACKLOG task"
    fi
    return 0
  fi
  (( _threshold > 0 )) || return 0

  _count=$(( $(_wlw_starvation_count "$_file") + 1 ))
  if ! mkdir -p "$(dirname "$_file")" 2>/dev/null; then
    log "project ${_project}: WARNING: lock starvation count for ${AGENT} not recorded -- cannot create $(dirname "$_file")"
    return 0
  fi
  _tmp="${_file}.tmp.$$"
  if printf 'deferrals=%s\ntask=%s\nupdated_at=%s\n' "$_count" "$_task" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$_tmp" 2>/dev/null \
      && mv -f -- "$_tmp" "$_file" 2>/dev/null; then
    :
  else
    rm -f -- "$_tmp" 2>/dev/null || true
    log "project ${_project}: WARNING: lock starvation count for ${AGENT} not recorded -- cannot write ${_file}"
    return 0
  fi

  if (( _count >= _threshold )); then
    log "project ${_project}: WARNING: lock starvation: ${AGENT} has deferred on repo-lock contention for ${_count} consecutive firing(s) while its task ${_task} is BACKLOG -- PM wakes skip discovery while that task is ready"
  else
    log "project ${_project}: lock starvation count for ${AGENT}: ${_count} of ${_threshold} consecutive contention deferral(s) with task ${_task} BACKLOG"
  fi
  return 0
}

wlw_starved_role_ready() {
  local _project="$1"
  local _threshold _dir _file _role _count _task
  WLW_STARVED_ROLE=""
  WLW_STARVED_TASK=""
  WLW_STARVED_DEFERRALS=0
  _threshold="$(wlw_starvation_threshold)"
  (( _threshold > 0 )) || return 1
  _dir="$(dirname "$(_wlw_starvation_file "$_project" x)")"
  [[ -d "$_dir" ]] || return 1
  while IFS= read -r _file; do
    [[ -n "$_file" && -f "$_file" ]] || continue
    _role="$(basename "$_file" .state)"
    _count="$(_wlw_starvation_count "$_file")"
    (( _count >= _threshold )) || continue
    _task="$(wlw_role_ready_task "$_project" "$_role")" || continue
    WLW_STARVED_ROLE="$_role"
    WLW_STARVED_TASK="$_task"
    WLW_STARVED_DEFERRALS="$_count"
    return 0
  done < <(compgen -G "${_dir}/*.state" 2>/dev/null | sort || true)
  return 1
}
