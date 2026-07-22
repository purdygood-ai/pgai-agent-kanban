#!/usr/bin/env bash
# team/scripts/wake/harness.sh
# Integration-test wake harness provider.
#
# This script is a stub wake provider used exclusively by integration tests to
# exercise the real wake substrate (wake_common.sh functions, specifically
# close_intake_on_finalize_report and the completion census) without invoking a
# real LLM provider.
#
# It should NEVER be used in production or set as the active provider in a live
# install.  It is placed under wake/ so that wake-batch.sh can dispatch to it
# via kanban.cfg [providers] active = harness (only when set in a temp fixture).
#
# Root-cause evidence this harness is designed to surface :
#   [2026-07-14T14:04:10+00:00] wake(tester): task
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
#   TESTER-20260714-008-verify-pvg-closure-check:
#   close_intake_on_finalize_report: finalize_mode:report not found
#   in README constraints — skipping intake closure (not a
#   finalize=report terminal ticket)
#
# The harness exercises the REAL close_intake_on_finalize_report via the real
# wake substrate so that any regression in the census-based closure logic is
# caught end-to-end in subprocess, not in a Python simulation.
#
# Usage (always via wake-batch.sh with kanban.cfg [providers] active = harness):
#   PGAI_AGENT_KANBAN_ROOT_PATH=/path/to/temp/root \
#     bash team/scripts/wake-batch.sh --agent=tester --max-tasks=N
#
# Behaviour:
#   For each BACKLOG TESTER task the harness finds:
#     1. Sets status to WORKING (marking the task active)
#     2. Simulates agent completion: writes State=DONE to status.md and plants
#        a synthetic report artifact under the task's artifacts/ directory.
#     3. Calls close_intake_on_finalize_report (the real shell function from
#        wake_common.sh) which runs the census and closes the intake item when
#        all siblings are terminal.
#     4. Marks the task's backlog entry as [x] (done).
#
# The synthetic report artifact simulates what a real TESTER agent produces
# (artifacts/report.md) so that assertions about report presence at the
# finalize location are satisfied by the harness run.

set -euo pipefail

VALID_AGENTS="pm coder writer tester cm bug po overwatch"

# ---------------------------------------------------------------------------
# Argument parsing (mirrors claude.sh / codex.sh for wake-batch.sh compat)
# ---------------------------------------------------------------------------

_print_usage() {
  cat >&2 <<USAGE
Usage: $(basename "$0") --agent=AGENT [--sleep=N] [--max-tasks=N] [--help]

Integration test wake harness.  Not for production use.
Valid agents: $VALID_AGENTS
USAGE
}

AGENT=""
SLEEP=0
_CLI_MAX_TASKS=""

if [[ $# -gt 0 && "${1:-}" != -* ]]; then
  AGENT="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      _print_usage
      exit 0
      ;;
    --agent=*)
      AGENT="${1#--agent=}"
      shift
      ;;
    --agent|-a)
      AGENT="${2:-}"
      shift 2
      ;;
    --sleep=*)
      SLEEP="${1#--sleep=}"
      shift
      ;;
    --sleep|-s)
      SLEEP="${2:-0}"
      shift 2
      ;;
    --max-tasks=*)
      _CLI_MAX_TASKS="${1#--max-tasks=}"
      shift
      ;;
    --max-tasks)
      _CLI_MAX_TASKS="${2:-}"
      shift 2
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      _print_usage
      exit 1
      ;;
  esac
done

if [[ -z "$AGENT" ]]; then
  echo "ERROR: --agent is required" >&2
  _print_usage
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$TEAM_ROOT" ]]; then
  echo "ERROR: kanban root not found: $TEAM_ROOT" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# ---------------------------------------------------------------------------
# Provider preflight stub (no-op — no real CLI to check)
# ---------------------------------------------------------------------------
provider_preflight() {
  : # no-op
}

# ---------------------------------------------------------------------------
# provider_invoke_agent stub
#
# Arguments mirror the real provider_invoke_agent signature:
#   <prompt> <selected_model> <model_source>
#   <task_id> <task_artifact_dir> <log_file> <subagent>
#
# Simulates agent completion by:
#   1. Writing State=DONE to the task status.md.
#   2. Planting a synthetic report artifact at <task_artifact_dir>/report.md.
#
# Sets PROVIDER_AGENT_EXIT_CODE=0 on success.
# ---------------------------------------------------------------------------
provider_invoke_agent() {
  local _prompt="${1:-}"
  local _model="${2:-}"
  local _model_source="${3:-}"
  local _task_id="${4:-}"
  local _artifact_dir="${5:-}"
  local _log_file="${6:-}"
  local _subagent="${7:-}"

  PROVIDER_AGENT_EXIT_CODE=0

  # Find the task status file from the context (TASKS_ROOT is set by run_project_chain).
  local _task_status="${TASKS_ROOT:-}/${_task_id}/status.md"

  echo "[harness] provider_invoke_agent: simulating DONE for task ${_task_id} (subagent=${_subagent})" >&2

  # Write State=DONE to status.md
  if [[ -f "$_task_status" ]]; then
    python3 - "$_task_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text_new, n = re.subn(
    r'(^## State\s*\n)(.*?)(\n+##|\Z)',
    lambda m: m.group(1) + 'DONE' + '\n' + (m.group(3) if m.group(3) else ''),
    text,
    flags=re.S | re.M,
)
if n == 0:
    # Try to add State if absent
    text_new = text + '\n## State\nDONE\n'
path.write_text(text_new)
PY
    echo "[harness] provider_invoke_agent: wrote State=DONE to ${_task_status}" >&2
  else
    echo "[harness] provider_invoke_agent: WARNING: status.md not found at ${_task_status}" >&2
  fi

  # Write summary
  if [[ -f "$_task_status" ]]; then
    python3 - "$_task_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text_new, n = re.subn(
    r'(^## Summary\s*\n)(.*?)(\n+##|\Z)',
    lambda m: m.group(1) + 'Harness stub: task completed by integration test harness.\n' + (m.group(3) if m.group(3) else ''),
    text,
    flags=re.S | re.M,
)
if n > 0:
    path.write_text(text_new)
PY
  fi

  # Plant a synthetic report artifact for TESTER tasks.
  if [[ "$_subagent" == "tester" && -n "$_artifact_dir" ]]; then
    mkdir -p "$_artifact_dir"
    cat > "${_artifact_dir}/report.md" <<'REPORT'
# Verification Report

## Recommendation
SHIP

## Summary
Integration test harness stub: all acceptance criteria pass in the sandbox fixture.
Report planted by wake/harness.sh to satisfy finalize=report closure detection.

## Evidence
- Harness-simulated TESTER task completed.
- Intake item should be closed by close_intake_on_finalize_report census.

## Findings
none
REPORT
    echo "[harness] provider_invoke_agent: planted report artifact at ${_artifact_dir}/report.md" >&2
  fi

  PROVIDER_AGENT_EXIT_CODE=0
  return 0
}

# ---------------------------------------------------------------------------
# setup_project_directories stub (no-op for harness)
# ---------------------------------------------------------------------------
setup_project_directories() {
  local _log_dir="${1:-}"
  local _artifact_dir="${2:-}"
  mkdir -p "${_log_dir}" "${_artifact_dir}" 2>/dev/null || true
  return 0
}

# ---------------------------------------------------------------------------
# Utility readers: README field extractors.
# (These are defined in claude.sh; duplicated here so harness.sh is
# self-contained without sourcing the Claude-specific provider script.)
# ---------------------------------------------------------------------------

get_workflow_type_from_readme() {
  local readme="${1:-}"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text()
except Exception:
    raise SystemExit(1)
m = re.search(r'^##\s+Workflow Type\s*\n\s*(\S+)', text, re.M)
if m:
    print(m.group(1).strip())
    raise SystemExit(0)
raise SystemExit(1)
PY
}

get_source_branch_from_readme() {
  local readme="${1:-}"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text()
except Exception:
    raise SystemExit(1)
m = re.search(r'^##\s+Source Branch\s*\n\s*(\S+)', text, re.M)
if m:
    print(m.group(1).strip())
    raise SystemExit(0)
raise SystemExit(1)
PY
}

# ---------------------------------------------------------------------------
# Source temp helper (provides pgai_temp_dir used by the worktree stubs
# and wake_bracket_* helpers).  This is normally sourced by the provider
# lib (wake_claude_provider.sh); the harness sources it directly.
# ---------------------------------------------------------------------------
# shellcheck source=scripts/lib/temp.sh
source "${SCRIPT_DIR}/../lib/temp.sh"

# ---------------------------------------------------------------------------
# Source common substrate lib (provides close_intake_on_finalize_report,
# wf_load_plugin, project registry helpers, task-selection helpers, etc.)
# ---------------------------------------------------------------------------
# shellcheck source=scripts/lib/wake_common.sh
source "${SCRIPT_DIR}/../lib/wake_common.sh"

# ---------------------------------------------------------------------------
# Source worktree helpers (so worktree-related functions are available even
# when stubs override their behaviour).
# ---------------------------------------------------------------------------
# shellcheck source=scripts/lib/worktree.sh
source "${SCRIPT_DIR}/../lib/worktree.sh"

# ---------------------------------------------------------------------------
# Source wake-bracket lib (provides wake_bracket_* helpers expected by the
# common substrate).
# ---------------------------------------------------------------------------
# shellcheck source=scripts/lib/wake_bracket.sh
source "${SCRIPT_DIR}/../lib/wake_bracket.sh"

# ---------------------------------------------------------------------------
# create_detached_worktree stub
#
# Testing-only workflow declares git_mode=ro, so process_one_task in the real
# wake provider would call create_detached_worktree.  For integration test
# fixtures that use dev_tree_path=/dev/null, the real helper would fail.
# This stub returns a temp directory path so the task can proceed normally
# through provider_invoke_agent and on to close_intake_on_finalize_report.
# ---------------------------------------------------------------------------
create_detached_worktree() {
  local _task_id="${1:-}"
  local _ref="${2:-}"
  local _dev_tree="${3:-${PGAI_DEV_TREE_PATH:-}}"

  # Resolve (or create) a temp directory for the stub worktree.
  local _wt_base
  _wt_base="$(pgai_temp_dir)/harness_worktrees"
  mkdir -p "$_wt_base"
  local _wt_path="${_wt_base}/${_task_id}"
  mkdir -p "$_wt_path"
  echo "[harness] create_detached_worktree stub: using temp path ${_wt_path} for task ${_task_id} (ref=${_ref})" >&2
  printf '%s\n' "$_wt_path"
}

# teardown_task_worktree stub — no-op since the stub worktree is just a tmpdir.
teardown_task_worktree() {
  local _task_id="${1:-}"
  local _dev_tree="${2:-}"
  local _wt_base
  _wt_base="$(pgai_temp_dir)/harness_worktrees"
  local _wt_path="${_wt_base}/${_task_id}"
  if [[ -d "$_wt_path" ]]; then
    rm -rf "$_wt_path" 2>/dev/null || true
  fi
  echo "[harness] teardown_task_worktree stub: removed ${_wt_path}" >&2
  return 0
}

# ---------------------------------------------------------------------------
# Active provider check: harness must be configured as active provider
# (prevents accidental execution in production).
#
# We read kanban.cfg directly rather than using read_active_provider() because
# read_active_provider() only recognises claude|codex|gemini and defaults to
# "claude" for any other value, which would cause this guard to always exit.
# ---------------------------------------------------------------------------
_raw_active_provider="$(python3 - "${TEAM_ROOT}/kanban.cfg" <<'PY'
import sys, configparser
cfg = configparser.ConfigParser()
cfg.read(sys.argv[1])
print(cfg.get("providers", "active", fallback="").strip().lower())
PY
2>/dev/null || true)"

if [[ "$_raw_active_provider" != "harness" ]]; then
  echo "[$(date -Iseconds)] wake-harness(${AGENT}): active-provider is '${_raw_active_provider:-unset}', not 'harness' — this harness is not the active provider; exiting" >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# wake_common_preflight (python3 check)
# ---------------------------------------------------------------------------
wake_common_preflight

# ---------------------------------------------------------------------------
# run_project_chain override
#
# Simplified chain: finds TESTER BACKLOG tasks for this project, runs the
# harness simulation, and calls the real close_intake_on_finalize_report.
# ---------------------------------------------------------------------------
run_project_chain() {
  local project_name="$1"

  _CURRENT_PROJECT="$project_name"
  TASKS_ROOT="$(pp_tasks_dir "$project_name")"
  QUEUE_DIR="${TASKS_ROOT}/queues"
  BACKLOG="$(pp_queue_path "$project_name" "$AGENT")"
  LOG_DIR="${KANBAN_ROOT}/logs/agents"
  mkdir -p "$LOG_DIR" 2>/dev/null || true
  LOG_FILE="${LOG_DIR}/harness-${AGENT}-$(date +%Y%m%dT%H%M%S).log"
  touch "$LOG_FILE" 2>/dev/null || LOG_FILE="/dev/null"

  if [[ ! -f "$BACKLOG" ]]; then
    log "project ${project_name}: ${AGENT} backlog not found at ${BACKLOG}, skipping"
    return 0
  fi

  export PGAI_PROJECT_ROOT="$(pp_project_root "$project_name")"
  export PGAI_PROJECT_NAME="$project_name"
  export PGAI_TASKS_DIR="$TASKS_ROOT"

  # Load project config (sets PP_* vars including PP_workflow_type).
  pp_load_config "$project_name" 2>/dev/null || true

  # Load workflow plugin so WF_MANIFEST_FINALIZE is set.
  local _wf_type="${PP_workflow_type:-testing-only}"
  local _wf_load_exit=0
  set +e
  wf_load_plugin "$_wf_type"
  _wf_load_exit=$?
  set -e
  if [[ $_wf_load_exit -ne 0 ]]; then
    log "project ${project_name}: WARNING: could not load workflow plugin '${_wf_type}'; WF_MANIFEST_FINALIZE will be empty"
  fi

  # Per-project repo lock.
  local _lock_id
  _lock_id="$(printf '%s' "$project_name" | tr '/' '_' | tr -cd '[:alnum:]_-')"
  local _lock_file="${LOCK_DIR}/repo-wake-${_lock_id}.lock"
  local _lock_fd
  exec {_lock_fd}>"$_lock_file"
  if ! flock -n "$_lock_fd"; then
    log "project ${project_name}: another agent holds repo lock, skipping"
    exec {_lock_fd}>&-
    return 0
  fi

  local tasks_processed=0
  local max_tasks="${_CLI_MAX_TASKS:-${MAX_TASKS_PER_WAKE:-5}}"

  while [[ $tasks_processed -lt $max_tasks ]]; do
    # Get the next BACKLOG task.
    local task_id
    task_id="$(get_first_pending_task || true)"
    if [[ -z "${task_id:-}" ]]; then
      log "project ${project_name}: no pending ${AGENT} task found"
      break
    fi

    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"
    local task_log_dir="${task_dir}/logs"
    local task_artifact_dir="${task_dir}/artifacts"

    if [[ ! -f "$task_readme" ]]; then
      log "task ${task_id}: missing README.md; marking BLOCKED"
      ensure_status_file "$task_status"
      set_state "$task_status" "BLOCKED"
      mark_backlog "$task_id" "B"
      tasks_processed=$((tasks_processed + 1))
      continue
    fi

    ensure_status_file "$task_status"
    setup_project_directories "$task_log_dir" "$task_artifact_dir"

    # Check state — only process BACKLOG tasks.
    local current_state
    current_state="$(get_state "$task_status" 2>/dev/null || echo "UNKNOWN")"
    if [[ "$current_state" != "BACKLOG" ]]; then
      log "task ${task_id}: not BACKLOG (${current_state}); skipping"
      break
    fi

    # Check prerequisites.
    local unsatisfied_prereqs=""
    local prereq_exit=0
    set +e
    unsatisfied_prereqs=$(check_prerequisites "$task_readme")
    prereq_exit=$?
    set -e
    if [[ $prereq_exit -ne 0 ]]; then
      log "task ${task_id}: unsatisfied prerequisites; transitioning to WAITING"
      set_state "$task_status" "WAITING"
      mark_backlog "$task_id" "W"
      tasks_processed=$((tasks_processed + 1))
      continue
    fi

    # Determine subagent role.
    local role SUBAGENT
    role="$(get_role_from_readme "$task_readme" 2>/dev/null || echo "TESTER")"
    case "$role" in
      TESTER) SUBAGENT="tester" ;;
      PM)     SUBAGENT="pm" ;;
      CODER)  SUBAGENT="coder" ;;
      WRITER) SUBAGENT="writer" ;;
      *)      SUBAGENT="tester" ;;
    esac

    # Handle worktree for testing-only (git_mode=ro) TESTER tasks.
    # create_detached_worktree is stubbed above to return a temp dir.
    local _task_worktree_path=""
    if [[ "$SUBAGENT" == "tester" && "${WF_MANIFEST_GIT_MODE:-none}" != "none" ]]; then
      local _source_branch=""
      _source_branch="$(get_source_branch_from_readme "$task_readme" 2>/dev/null || echo "none")"
      local _wt_exit=0
      set +e
      _task_worktree_path="$(create_detached_worktree "$task_id" "${_source_branch:-none}" "${PP_dev_tree_path:-/dev/null}")"
      _wt_exit=$?
      set -e
      if [[ $_wt_exit -ne 0 || -z "$_task_worktree_path" ]]; then
        log "task ${task_id}: WARNING: create_detached_worktree failed (exit ${_wt_exit}); task will run without worktree"
        _task_worktree_path=""
      fi
    fi

    log "harness: starting task ${task_id} (subagent=${SUBAGENT} role=${role})"

    # Mark task WORKING.
    set_state "$task_status" "WORKING"
    mark_backlog "$task_id" "A"

    # Invoke stub provider (writes DONE + report artifact).
    PROVIDER_AGENT_EXIT_CODE=0
    set +e
    provider_invoke_agent \
      "harness-prompt" \
      "" \
      "harness" \
      "$task_id" \
      "$task_artifact_dir" \
      "$LOG_FILE" \
      "$SUBAGENT"
    set -e

    normalize_status_file "$task_status"
    local final_state
    final_state="$(get_state "$task_status" 2>/dev/null || echo "BLOCKED")"

    log "harness: task ${task_id} final_state=${final_state}"

    # Teardown stub worktree.
    if [[ -n "$_task_worktree_path" ]]; then
      teardown_task_worktree "$task_id" "${PP_dev_tree_path:-/dev/null}" || true
    fi

    # Post-TESTER finalize=report intake closure — same guard as claude.sh process_one_task.
    # This is the real close_intake_on_finalize_report call (not a Python simulation).
    if [[ "$SUBAGENT" == "tester" && "$final_state" == "DONE" ]]; then
      close_intake_on_finalize_report \
        "$task_id" \
        "$task_readme" \
        "${PGAI_PROJECT_ROOT:-}" \
        "${TASKS_ROOT:-}" \
        "${WF_MANIFEST_FINALIZE:-}"
    fi

    # Update backlog marker.
    case "$final_state" in
      DONE|WONT-DO) mark_backlog "$task_id" "x" ;;
      BLOCKED)      mark_backlog "$task_id" "B" ;;
      WAITING)      mark_backlog "$task_id" "W" ;;
      *)            mark_backlog "$task_id" " " ;;
    esac

    LAST_TASK_FINAL_STATE="$final_state"
    tasks_processed=$((tasks_processed + 1))

    # Promote WAITING tasks whose prerequisites are now satisfied.
    # This mirrors the recheck loop in the real wake substrate so that
    # dependent tasks become BACKLOG when their upstream completes.
    local _recheck_exit=0
    set +e
    recheck_waiting_tasks
    _recheck_exit=$?
    set -e
    if [[ $_recheck_exit -ne 0 ]]; then
      log "project ${project_name}: WARNING: recheck_waiting_tasks exited ${_recheck_exit}"
    fi
  done

  exec {_lock_fd}>&-
  log "project ${project_name}: harness run complete (${tasks_processed} task(s) processed)"
}

# ---------------------------------------------------------------------------
# Main: run the common wake loop
# ---------------------------------------------------------------------------
wake_common_run
