#!/usr/bin/env bash
# team/scripts/lib/wake_task_transition.sh
#
# Wake task-state stage: the single write path that moves a task's
# status.md ## State block and its queue marker together, as one operation.
# Split out of wake_task_state.sh into its own file so that file stays
# under the shared per-file line ceiling.
#
# Public entry point
# -------------------
#   transition_task_state <task_id> <status_file> <new_state>
#       Writes a task's ## State block AND its queue marker together, as one
#       operation, deriving the marker from <new_state> through the single
#       state->marker table this function owns (no caller chooses a marker
#       independently). status.md is snapshotted before either surface is
#       touched; both writes go through the same-directory scratch-plus-
#       rename path (atomic_write.sh), so a reader never sees a half-written
#       file. If the marker write fails after the ## State write landed,
#       ## State is restored from the snapshot through that same atomic
#       path and the function returns non-zero with a dual-channel refusal
#       naming the failed surface. If the restore itself cannot land, that
#       is its own hard refusal (wake.transition.rollback-failed) naming
#       both files and the retained snapshot — the log never claims a
#       rollback that did not happen. A SIGTERM/SIGINT/SIGHUP arriving
#       between the two writes is handled the same way before the signal is
#       re-raised. See the function's own header comment below for the full
#       derivation table and the reasoning behind it.
#       A transition to DONE or WONT-DO that lands both writes then removes
#       the task's ## Block Record (block_record.sh, block_record_remove):
#       the record lives from a task's first block until it ends. The
#       removal is best-effort — a failure is narrated on stderr and never
#       changes the transition's own outcome.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's function is called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh   — BACKLOG global.
#   wake_task_state.sh  — set_state, mark_backlog. Sourced by wake_common.sh
#     immediately before this file.
#   error_contract.sh, temp.sh, atomic_write.sh — pgai_emit_error_contract,
#     pgai_mktemp and pgai_atomic_write. All three are self-sourced below
#     (self-locating from this file's own directory) so transition_task_state
#     works whether this file is sourced through the full wake pipeline
#     (which already sources them) or on its own, as several unit tests do.
#   block_record.sh — block_record_remove, for the terminal-state removal.
#     Self-sourced below the same way unless the caller already defined it
#     (wake_bootstrap.sh sources it for the wake path).
#
# python3 must be on PATH (used transitively via set_state / mark_backlog).
# Sourced by wake_common.sh immediately after wake_task_state.sh.

# ---------------------------------------------------------------------------
# Self-source this file's own dependencies. Sourcing error_contract.sh again
# is a cheap, side-effect-free function redefinition, so it is unconditional;
# temp.sh's and atomic_write.sh's own include guards make the pgai_mktemp
# check here a pure optimization rather than a correctness requirement.
# Mirrors the guarded self-source idiom project_paths.sh uses for temp.sh —
# each file finds its own siblings rather than assuming a caller sourced
# them first.
# ---------------------------------------------------------------------------
_WAKE_TASK_TRANSITION_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=error_contract.sh
source "${_WAKE_TASK_TRANSITION_LIB_DIR}/error_contract.sh"
if ! declare -F pgai_mktemp >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "${_WAKE_TASK_TRANSITION_LIB_DIR}/temp.sh"
fi
# shellcheck source=atomic_write.sh
source "${_WAKE_TASK_TRANSITION_LIB_DIR}/atomic_write.sh"
if ! declare -F block_record_remove >/dev/null 2>&1; then
    # shellcheck source=block_record.sh
    source "${_WAKE_TASK_TRANSITION_LIB_DIR}/block_record.sh"
fi

# ---------------------------------------------------------------------------
# In-flight transition record. Set by _wake_transition_arm for exactly the
# span between the ## State write and the marker write, cleared by
# _wake_transition_disarm, and read by _wake_transition_on_signal when a
# catchable signal lands inside that span. Every reader tolerates the
# variables being unset (`${VAR:-}`) so the functions below also work when
# extracted on their own, without this file's top level.
# ---------------------------------------------------------------------------
_WAKE_TRANSITION_TASK_ID=""
_WAKE_TRANSITION_STATUS_FILE=""
_WAKE_TRANSITION_STATUS_BACKUP=""
_WAKE_TRANSITION_MARKER=""
_WAKE_TRANSITION_SAVED_TRAPS=""

# ---------------------------------------------------------------------------
# _wake_transition_marker_landed <task_id> <marker>
# True when the queue file already carries <marker> for <task_id>: the marker
# write completed, the pair is consistent, and rolling ## State back would
# itself create the disagreement the rollback exists to prevent.
# ---------------------------------------------------------------------------
_wake_transition_marker_landed() {
  local task_id="$1"
  local marker="$2"
  [[ -f "${BACKLOG:-}" ]] || return 1
  grep -qE "^[[:space:]]*-[[:space:]]*\[${marker}\][[:space:]]+${task_id}([[:space:]]|$)" "$BACKLOG"
}

# ---------------------------------------------------------------------------
# _wake_transition_restore_state <task_id> <status_file> <status_backup>
# Roll ## State back to the pre-call snapshot through the same
# scratch-plus-rename path every status.md write uses, so a reader racing
# the rollback never sees a half-restored file. Returns 0 only when the
# restored bytes are on disk (the snapshot is then removed). On failure the
# snapshot is kept for the operator, a dual-channel refusal names both
# files and the snapshot, and the function returns 1 — the caller must not
# claim "rolled back" on that path.
# ---------------------------------------------------------------------------
_wake_transition_restore_state() {
  local task_id="$1"
  local status_file="$2"
  local status_backup="$3"
  local _restore_err
  if _restore_err="$(pgai_atomic_write "$status_file" cat "$status_backup" 2>&1 >/dev/null)"; then
    rm -f -- "$status_backup" 2>/dev/null || true
    return 0
  fi
  pgai_emit_error_contract \
    "wake.transition.rollback-failed" \
    "transition_task_state could not restore ${status_file} for ${task_id} after its queue marker in ${BACKLOG:-the agent queue file} was not written (${_restore_err:-unknown error}): ## State still reads the new value while the marker reads the old one, and the pre-call snapshot is kept at ${status_backup}." \
    "Restore the snapshot by hand: cp -- '${status_backup}' '${status_file}' ; then rerun the wake so the marker in ${BACKLOG:-the agent queue file} is derived from ## State again."
  # BLOCK-WORTHY: status.md and the queue marker disagree and this code
  # could not repair it — a state-corrupting failure under The Loop
  # doctrine (team/SOP-core.md, "Gates and Refusals"). Refusing loud with
  # the snapshot retained is what keeps a false "rolled back" out of the log.
  # CENSUS-ANCHOR: atomic-transition-02
  return 1
}

# ---------------------------------------------------------------------------
# _wake_transition_arm <task_id> <status_file> <status_backup> <marker>
# _wake_transition_disarm
# Record the in-flight transition and route SIGTERM/SIGINT/SIGHUP through
# _wake_transition_on_signal for as long as the two writes are in progress;
# disarm restores whatever traps the caller had on those signals (the wake
# bootstrap's own cleanup trap, for one) and clears the record. SIGKILL
# cannot be caught by anything: a kill -9 between the two writes leaves
# ## State ahead of the marker, which the next wake's reconciliation
# repairs by re-deriving the marker from ## State.
# ---------------------------------------------------------------------------
_wake_transition_arm() {
  _WAKE_TRANSITION_TASK_ID="$1"
  _WAKE_TRANSITION_STATUS_FILE="$2"
  _WAKE_TRANSITION_STATUS_BACKUP="$3"
  _WAKE_TRANSITION_MARKER="$4"
  _WAKE_TRANSITION_SAVED_TRAPS="$(trap -p TERM INT HUP)"
  trap '_wake_transition_on_signal TERM' TERM
  trap '_wake_transition_on_signal INT' INT
  trap '_wake_transition_on_signal HUP' HUP
}

_wake_transition_disarm() {
  trap - TERM INT HUP
  if [[ -n "${_WAKE_TRANSITION_SAVED_TRAPS:-}" ]]; then
    eval "${_WAKE_TRANSITION_SAVED_TRAPS}"
  fi
  _WAKE_TRANSITION_TASK_ID=""
  _WAKE_TRANSITION_STATUS_FILE=""
  _WAKE_TRANSITION_STATUS_BACKUP=""
  _WAKE_TRANSITION_MARKER=""
  _WAKE_TRANSITION_SAVED_TRAPS=""
}

# ---------------------------------------------------------------------------
# _wake_transition_on_signal <SIGNAME>
# Runs when a catchable signal lands between the ## State write and the
# marker write. If the marker already landed the pair is consistent and
# nothing is rolled back; otherwise ## State is restored from the snapshot
# (or the rollback-failed refusal fires) and the interruption is logged
# dual-channel naming both files. The signal is then re-raised against this
# shell with the caller's own trap back in place, so the shell terminates —
# or runs its own cleanup — exactly as it would have without this handler.
# ---------------------------------------------------------------------------
_wake_transition_on_signal() {
  local sig="$1"
  local task_id="${_WAKE_TRANSITION_TASK_ID:-}"
  local status_file="${_WAKE_TRANSITION_STATUS_FILE:-}"
  local status_backup="${_WAKE_TRANSITION_STATUS_BACKUP:-}"
  local marker="${_WAKE_TRANSITION_MARKER:-}"
  _wake_transition_disarm
  if [[ -n "$task_id" ]]; then
    if _wake_transition_marker_landed "$task_id" "$marker"; then
      rm -f -- "$status_backup" 2>/dev/null || true
    elif _wake_transition_restore_state "$task_id" "$status_file" "$status_backup"; then
      pgai_emit_error_contract \
        "wake.transition.interrupted" \
        "transition_task_state for ${task_id} was interrupted by SIG${sig} after ## State was written to ${status_file} and before the queue marker was written to ${BACKLOG:-the agent queue file}; ## State was rolled back to its pre-call value so the two files agree." \
        "Rerun the wake; the interrupted transition is retried from the pre-call state."
    fi
  fi
  kill -s "$sig" "$BASHPID"
}

# ---------------------------------------------------------------------------
# transition_task_state <task_id> <status_file> <new_state>
#
# The single write path for moving a task to one of the six canonical
# states: it rewrites status.md's ## State block AND the task's queue
# marker together, as one operation, instead of leaving callers to pair
# set_state and mark_backlog by hand. Before this function existed, every
# call site chose its own marker for a given state; a caller that named the
# wrong bracket value — or crashed, timed out, or returned early between the
# two calls — left status.md and the queue marker disagreeing until the next
# reconciliation pass happened to notice, which is exactly the drift class
# "queue markers, status.md, and release-state drifted into three truths"
# describes. Centralizing the state->marker table here removes both failure
# modes: there is exactly one place a future state can be given a marker,
# and there is exactly one write sequence that can leave the pair
# half-applied.
#
# Derivation table (owned here; no caller passes a marker):
#   BACKLOG          -> " "   (pending, ready to pull)
#   WAITING          -> "W"   (prerequisites not yet satisfied)
#   WORKING          -> "A"   (active; the shape a task carries for as long
#                              as the agent that started it is running)
#   BLOCKED          -> "B"   (hard blocked, requires human intervention)
#   DONE | WONT-DO   -> "x"   (terminal)
#
# This table is the ONE used when a caller actually MOVES a task to
# <new_state>. It is deliberately not used for the separate "normalize a
# marker to match a state that already holds, without changing that state"
# operation the wake driver's prep stage performs on a task rediscovered
# mid-run: a task found still in WORKING state at that point is marked "B"
# (not "A") while its own ## State is left at WORKING, by design — see
# team/SOP-core.md, "The two WORKING situations" and "Re-check cascade on
# task selection" step 3. That normalization does not name a target state
# for the task to reach, so it is out of this function's contract and stays
# a direct marker write at its own call site.
#
# Atomicity: status.md is copied to a scratch file under the configured
# temp root BEFORE either surface is written. Each write is itself a
# same-directory scratch-plus-rename (atomic_write.sh), so a surface is
# never observed half-written and a failed write never changes it — the
# queue file therefore needs no snapshot of its own: it is written last,
# and its writer either replaces it whole or leaves it exactly as it was.
# If the ## State write fails the marker is never touched. If the marker
# write fails, ## State is restored from its snapshot through the same
# atomic path and the function returns 1 — status.md and the queue marker
# read exactly as they did before the call. A missing or unparseable queue
# entry (mark_backlog finding no line to rewrite) is one of the refused
# outcomes this guarantees against half-applying, not a caller-visible
# crash: the caller gets a non-zero return and a dual-channel message
# naming the failed surface, never an uncaught SystemExit propagating out
# of a python3 heredoc. A restore that cannot land is refused on its own
# (wake.transition.rollback-failed, above) with the snapshot retained; the
# marker-write refusal below is only reached once the restore is on disk,
# so its "rolled back" wording is always true when it is printed.
#
# Every refusal below shares one classification (see the marker-write
# refusal's own comment, the representative site this function's census
# entry names): BLOCK-WORTHY under team/SOP-core.md, "Gates and Refusals" —
# refusing to write one surface without the other prevents the split-truth
# corruption between status.md and the queue marker that this function
# exists to end. Each refusal also carries its own dual-channel emitter
# call, naming the failed surface and a runnable remedy.
# ---------------------------------------------------------------------------
transition_task_state() {
  local task_id="$1"
  local status_file="$2"
  local new_state="$3"

  local marker
  case "$new_state" in
    BACKLOG)      marker=" " ;;
    WAITING)      marker="W" ;;
    WORKING)      marker="A" ;;
    BLOCKED)      marker="B" ;;
    DONE|WONT-DO) marker="x" ;;
    *)
      pgai_emit_error_contract \
        "wake.transition.invalid-state" \
        "transition_task_state was asked to move ${task_id} to '${new_state}', which is not one of the six canonical states (BACKLOG, WAITING, WORKING, BLOCKED, DONE, WONT-DO) — neither status.md nor the queue marker was touched." \
        "Call transition_task_state with one of the six canonical state names; if the value came from a status.md read, that file's ## State block is corrupt and needs manual repair."
      return 1
      ;;
  esac

  if [[ ! -f "$status_file" ]]; then
    pgai_emit_error_contract \
      "wake.transition.status-missing" \
      "transition_task_state could not find ${status_file} for ${task_id} — neither status.md nor the queue marker was touched." \
      "Confirm the task folder exists and status.md was created (ensure_status_file), then retry the transition."
    return 1
  fi

  local status_backup
  status_backup="$(pgai_mktemp wake_transition_status)" || {
    pgai_emit_error_contract \
      "wake.transition.backup-failed" \
      "transition_task_state could not create a scratch copy of ${status_file} for ${task_id} before writing — refusing to write either surface." \
      "Confirm the configured temp root (PGAI_AGENT_KANBAN_TEMP_DIR) exists and is writable, then retry the transition."
    return 1
  }

  if ! cp "$status_file" "$status_backup" 2>/dev/null; then
    rm -f -- "$status_backup" 2>/dev/null || true
    pgai_emit_error_contract \
      "wake.transition.backup-failed" \
      "transition_task_state could not snapshot ${status_file} for ${task_id} before writing — refusing to write either surface." \
      "Confirm the file exists and is readable, then retry the transition."
    return 1
  fi

  _wake_transition_arm "$task_id" "$status_file" "$status_backup" "$marker"

  local _state_err
  if ! _state_err="$(set_state "$status_file" "$new_state" 2>&1 >/dev/null)"; then
    _wake_transition_disarm
    rm -f -- "$status_backup" 2>/dev/null || true
    pgai_emit_error_contract \
      "wake.transition.state-write-failed" \
      "transition_task_state refused: writing ## State=${new_state} to ${status_file} for ${task_id} failed (${_state_err:-unknown error}); the queue marker was never touched." \
      "Inspect ${status_file} for a malformed ## State block or a permissions problem, fix it, and retry the transition."
    return 1
  fi

  local _marker_err
  if ! _marker_err="$(mark_backlog "$task_id" "$marker" 2>&1 >/dev/null)"; then
    _wake_transition_disarm
    # A missing or unparseable queue entry lands here: mark_backlog found no
    # line to rewrite. Roll ## State back to its pre-call bytes first; a
    # restore that does not land has already refused on its own and must
    # not be reported as rolled back.
    _wake_transition_restore_state "$task_id" "$status_file" "$status_backup" || return 1
    pgai_emit_error_contract \
      "wake.transition.marker-write-failed" \
      "transition_task_state refused: writing queue marker [${marker}] for ${task_id} failed (${_marker_err:-unknown error}); ## State was rolled back to its prior value so status.md and the queue marker stay in agreement." \
      "Check that ${task_id} has a well-formed queue entry in ${BACKLOG}; add or repair the line, then retry the transition."
    # BLOCK-WORTHY: refusing here, with the ## State write already rolled
    # back above, is what keeps status.md and the queue marker from ever
    # disagreeing — a destructive/state-corrupting failure under The Loop
    # doctrine (team/SOP-core.md, "Gates and Refusals").
    # CENSUS-ANCHOR: atomic-transition-01
    return 1
  fi

  _wake_transition_disarm
  rm -f -- "$status_backup" 2>/dev/null || true

  # A task that has just reached DONE or WONT-DO loses its ## Block Record
  # (every other section byte-for-byte). Only once both surfaces landed, and
  # only when status.md carries the heading, so the common terminal
  # transition spawns nothing extra. A removal that fails leaves a stale
  # record on a finished task (block triage never acts on a terminal task),
  # never a failed transition: it is narrated on stderr and the transition
  # still returns 0. Skipped when block_record_remove is not defined (a
  # caller that extracted this function on its own).
  if [[ "$new_state" == "DONE" || "$new_state" == "WONT-DO" ]] \
      && declare -F block_record_remove >/dev/null 2>&1 \
      && grep -qiE '^##[[:space:]]+Block[[:space:]]+Record[[:space:]]*$' "$status_file" 2>/dev/null; then
    block_record_remove "$status_file" >/dev/null 2>&1 \
      || echo "transition_task_state: ${task_id}: WARNING: ## Block Record not removed from ${status_file}" >&2
  fi
  return 0
}
