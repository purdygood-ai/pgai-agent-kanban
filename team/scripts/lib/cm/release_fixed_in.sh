#!/usr/bin/env bash
# team/scripts/lib/cm/release_fixed_in.sh
#
# CM release Fixed-In stage: the two helpers behind Step 16b, which stamps
# "## Fixed In: <version>" on the bugs this release closed. Called from
# cm_release_finalize (release_finalize.sh), which sources this file itself;
# split into its own file so release_finalize.sh stays under the shared
# per-file size ceiling (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py).
#
# Functions
# ---------
#   _cm_release_snapshot_running_bugs <bugs_dir>
#       Called just before Step 13c promotes bundled items: records the bugs
#       at 'running' so Step 16b stamps only those this release closes.
#   _cm_release_stamp_fixed_in <bugs_dir> <version>
#       Step 16b's body: stamps the snapshotted bugs now 'done' and sets
#       _CM_FIXED_IN_OUTCOME for the closing summary.
#   Neither ever fails the release; see each function's own comment.
#
# Dependencies (must already be sourced/defined by the time they are called)
# ------------------------------------------------------------------------
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops (runs
#     pgai_agent_kanban.cm.fixed_in_writeback with a cwd-independent
#     PYTHONPATH).
# The caller must have `set -euo pipefail` enabled.

# Guard against double-sourcing.
if declare -F _cm_release_stamp_fixed_in >/dev/null 2>&1; then
    return 0
fi

# Helper: record which bugs are 'running' before Step 13c promotes them.
# Usage: _cm_release_snapshot_running_bugs <bugs_dir>
# Sets _CM_RUNNING_BUGS (array of bug file names) and _CM_RUNNING_BUGS_OK
# (1 on success, 0 when the listing failed). Never fails the release.
_cm_release_snapshot_running_bugs() {
  local _out="" _rc=0
  _CM_RUNNING_BUGS=()
  _out="$(pp_run_ops pgai_agent_kanban.cm.fixed_in_writeback running "$1")" || _rc=$?
  _CM_RUNNING_BUGS_OK=$(( _rc == 0 ? 1 : 0 ))
  [[ $_rc -eq 0 && -n "$_out" ]] && mapfile -t _CM_RUNNING_BUGS <<<"$_out"
  return 0
}

# Helper: Step 16b body — stamp ## Fixed In on the snapshotted bugs now 'done'.
# Usage: _cm_release_stamp_fixed_in <bugs_dir> <version>
# Runs through pp_run_ops so the package resolves from this tree whatever
# the working directory holds, prefixes its output with "[Step 16b] ", and
# sets _CM_FIXED_IN_OUTCOME to the closing-summary line: the stamp counts
# on success, or a FAILED line naming the exit code. Never fails the release.
_cm_release_stamp_fixed_in() {
  local _out="" _rc=0 _result=""
  if [[ "${_CM_RUNNING_BUGS_OK:-0}" != 1 ]]; then
    _rc=3; _out="WARNING: the running-bug snapshot taken before Step 13c failed; nothing can be stamped."
  else
    _out="$(pp_run_ops pgai_agent_kanban.cm.fixed_in_writeback stamp "$1" "$2" ${_CM_RUNNING_BUGS[@]+"${_CM_RUNNING_BUGS[@]}"})" || _rc=$?
  fi
  [[ -n "$_out" ]] && printf '%s\n' "$_out" | sed 's/^/[Step 16b] /'
  _result="$(printf '%s\n' "$_out" | grep -E '^Fixed-In writeback:' | tail -n 1 || true)"
  if [[ $_rc -eq 0 && -n "$_result" ]]; then
    _CM_FIXED_IN_OUTCOME="## Fixed In: $2 — ${_result#Fixed-In writeback: } (Step 16b)"
  else
    _CM_FIXED_IN_OUTCOME="## Fixed In: $2 writeback FAILED (exit ${_rc}) — see the [Step 16b] lines above${_result:+; ${_result}} (Step 16b, non-blocking)"
    echo "[Step 16b] WARNING: Fixed-In writeback encountered an error — release continues." >&2
  fi
  return 0
}
