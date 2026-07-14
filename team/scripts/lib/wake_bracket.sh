#!/usr/bin/env bash
# team/scripts/lib/wake_bracket.sh
# Shared wake-bracket functions for wake provider siblings (claude.sh, codex.sh).
#
# Source this file to get the three wake_bracket_* functions:
#   source "$(dirname "${BASH_SOURCE[0]}")/wake_bracket.sh"
#
# Requires:
#   - temp.sh sourced beforehand (provides pgai_temp_dir, pgai_project_temp_dir,
#     wake_tmp_litter_take_snapshot, wake_tmp_litter_check_and_report)
#   - The calling scope must expose a log() function for pre-dispatch warnings.
#
# Functions exposed:
#   wake_bracket_compute_temp_subtree <project_name>
#       Resolve the per-project temp subtree path at dispatch time and echo it.
#       Use the output to set _project_temp_subtree before building the prompt
#       heredoc so the emitted string contains the absolute path, not an
#       unexpanded variable reference.
#
#   wake_bracket_pre_dispatch <task_id>
#       Pre-dispatch /tmp litter snapshot. Captures bare /tmp top-level entries
#       before the provider CLI runs. Prints the snapshot file path to stdout
#       on success; prints an empty string on failure (fire-and-forget).
#       The caller stores the output in _litter_snapshot_file.
#
#   wake_bracket_post_session <final_state> <snapshot_file> <task_status> \
#                             <task_id> <current_project> <kanban_root>
#       Post-session /tmp litter check. Diffs bare /tmp against the pre-dispatch
#       snapshot and reports any fresh top-level entries to status.md, the wake
#       log, and the project's OVERWATCH actions.log.
#       Guard: runs only when final_state is DONE or BLOCKED (WONT-DO excluded).
#       Fire-and-forget: any internal failure returns 0 to preserve the wake
#       tail's exit-code handling.

# Include guard: safe to source multiple times.
[[ -n "${_WAKE_BRACKET_SH_LOADED:-}" ]] && return 0
_WAKE_BRACKET_SH_LOADED=1

# ---------------------------------------------------------------------------
# wake_bracket_compute_temp_subtree <project_name>
# Resolve the per-project temp subtree at dispatch time and echo the absolute
# path.  The caller assigns the result to _project_temp_subtree before the
# prompt heredoc is evaluated so the heredoc expansion embeds the resolved
# path instead of an unexpanded variable reference.
#
# Arguments:
#   $1  project_name  — current project identifier (may be empty; falls back
#                       to the install-wide temp root via pgai_project_temp_dir)
# ---------------------------------------------------------------------------
wake_bracket_compute_temp_subtree() {
  local _project_name="${1:-}"
  pgai_project_temp_dir "${_project_name}"
}

# ---------------------------------------------------------------------------
# wake_bracket_pre_dispatch <task_id>
# Pre-dispatch /tmp litter snapshot.
#
# Captures the current bare /tmp top-level entries before the provider CLI
# runs.  Prints the absolute snapshot file path to stdout on success, or an
# empty string on failure.  The caller stores the printed value in
# _litter_snapshot_file so it can be passed to wake_bracket_post_session.
#
# Fire-and-forget: any failure here logs a WARNING but must not block the
# task or alter its state.
#
# Arguments:
#   $1  task_id  — task identifier (used to scope the snapshot file path)
#
# Environment:
#   PP_TEMP_DIR   — per-project temp dir (set by pp_load_config); falls back
#                   to pgai_temp_dir when unset.
# ---------------------------------------------------------------------------
wake_bracket_pre_dispatch() {
  local _task_id="${1:-}"
  # --- Pre-dispatch /tmp litter snapshot ---
  # Capture bare /tmp top-level entries before the agent runs so the
  # post-session check can diff and report any litter the session leaves.
  # Fire-and-forget: failure here must not block the task or change its state.
  local _litter_snapshot_file=""
  local _litter_session_epoch
  _litter_session_epoch="$(date +%s)"
  {
    local _litter_snap_dir
    _litter_snap_dir="${PP_TEMP_DIR:-$(pgai_temp_dir)}/tasks/${_task_id}/litter"
    _litter_snapshot_file="${_litter_snap_dir}/pre_dispatch_tmp_snapshot"
    wake_tmp_litter_take_snapshot "$_litter_snapshot_file" "$_litter_session_epoch"
  } 2>/dev/null || { log "WARNING: litter check: pre-dispatch snapshot failed for ${_task_id}; litter check disabled for this task"; _litter_snapshot_file=""; }
  echo "${_litter_snapshot_file}"
}

# ---------------------------------------------------------------------------
# wake_bracket_post_session <final_state> <snapshot_file> <task_status> \
#                           <task_id> <current_project> <kanban_root>
# Post-session /tmp litter check.
#
# Diffs bare /tmp against the pre-dispatch snapshot and reports any fresh
# top-level entries to status.md, the wake log, and the project's OVERWATCH
# actions.log.
#
# Guard: runs only when final_state is DONE or BLOCKED (WONT-DO excluded).
# Fire-and-forget: any internal failure inside wake_tmp_litter_check_and_report
# returns 0, preserving the wake tail's exit-code handling.  The ( set +e; ... )
# isolation inside wake_tmp_litter_check_and_report guarantees this.
#
# Arguments:
#   $1  final_state      — task terminal state (DONE, BLOCKED, or WONT-DO)
#   $2  snapshot_file    — path written by wake_bracket_pre_dispatch
#   $3  task_status      — absolute path to the task's status.md
#   $4  task_id          — task identifier string
#   $5  current_project  — project name (for OVERWATCH actions.log path)
#   $6  kanban_root      — kanban root directory (for OVERWATCH actions.log)
# ---------------------------------------------------------------------------
wake_bracket_post_session() {
  local _final_state="${1:-}"
  local _snapshot_file="${2:-}"
  local _task_status="${3:-}"
  local _task_id="${4:-}"
  local _current_project="${5:-}"
  local _kanban_root="${6:-}"
  # --- Post-session /tmp litter check ---
  # Diff bare /tmp against the pre-dispatch snapshot; report any fresh top-level
  # entries to status.md, the wake log, and the project's OVERWATCH actions.log.
  # Fire-and-forget: failure inside the check must not alter the task's exit path.
  # Runs on both DONE and BLOCKED terminal paths (guard: WONT-DO is excluded).
  if [[ "$_final_state" == "DONE" || "$_final_state" == "BLOCKED" ]]; then
    {
      wake_tmp_litter_check_and_report \
        "${_snapshot_file:-}" \
        "$_task_status" \
        "$_task_id" \
        "log" \
        "${_current_project:-}" \
        "${_kanban_root:-}"
    } 2>/dev/null || true
  fi
}
