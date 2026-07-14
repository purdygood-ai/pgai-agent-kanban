#!/usr/bin/env bash
# show-active.sh
# Renders the currently-working ticket detail pane.
#
# Shows: task ID, role, state, goal excerpt, summary excerpt, and how long
# the task has been in WORKING state.
#
# Usage:
#   show-active.sh [--kanban-root <path>] [--compact]
#
# Flags:
#   --compact   Compact two-line format (used by show-status-window.sh)
#
# Configuration (via config.cfg):
#   PGAI_DASHBOARD_COLOR_WORKING  — working label color (default: yellow)
#   PGAI_DASHBOARD_COLOR_LABEL    — field label color (default: cyan)
#
# Environment:
#   TERM=dumb  — disables all ANSI codes
#   NO_COLOR=1 — disables all ANSI codes

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# --- Parse args ---
COMPACT_MODE=false
DATA_ARGS=()
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  if [[ "$arg" == "--compact" ]]; then
    COMPACT_MODE=true
  else
    DATA_ARGS+=("$arg")
  fi
  _i=$(( _i + 1 ))
done

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  if [[ "${_args[$_i]}" == "--kanban-root" ]]; then
    _next=$(( _i + 1 ))
    KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
    break
  fi
  _i=$(( _i + 1 ))
done
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_working yellow)}"
    export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_label cyan)}"
    export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_done green)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"
COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"

ansi_code() {
  local color="$1"
  if [[ "$USE_COLOR" != "true" ]]; then echo ""; return; fi
  case "$color" in
    black)   echo $'\033[0;30m' ;;
    red)     echo $'\033[0;31m' ;;
    green)   echo $'\033[0;32m' ;;
    yellow)  echo $'\033[0;33m' ;;
    blue)    echo $'\033[0;34m' ;;
    magenta) echo $'\033[0;35m' ;;
    cyan)    echo $'\033[0;36m' ;;
    white)   echo $'\033[0;37m' ;;
    bold)    echo $'\033[1m' ;;
    dim)     echo $'\033[2m' ;;
    reset)   echo $'\033[0m' ;;
    *)       echo "" ;;
  esac
}

RESET="$(ansi_code reset)"
C_WORK="$(ansi_code "$COLOR_WORKING")"
C_LABL="$(ansi_code "$COLOR_LABEL")"
C_DONE="$(ansi_code "$COLOR_DONE")"
C_DIM="$(ansi_code dim)"

# --- Collect data ---
DATA="$("$SCRIPT_DIR/data.sh" "${DATA_ARGS[@]}")"

get_val() {
  local key="$1"
  local default="${2:-}"
  echo "$DATA" | awk -F= -v k="$key" 'NR==1{OFS="="} $1 == k { $1=""; sub(/^=/, ""); print; found=1 } END { if (!found) print "'"$default"'" }'
}

WORKING_ID="$(get_val WORKING_TASK_ID "")"
WORKING_MTIME="$(get_val WORKING_TASK_MTIME "")"
WORKING_ROLE="$(get_val WORKING_TASK_ROLE "")"

# --- Elapsed time helper ---
compute_elapsed() {
  local mtime="$1"
  local elapsed_str=""
  if [[ -n "$mtime" ]] && [[ "$mtime" -gt 0 ]]; then
    local now_epoch elapsed_secs
    now_epoch="$(date +%s)"
    elapsed_secs=$(( now_epoch - mtime ))
    if [[ "$elapsed_secs" -lt 60 ]]; then
      elapsed_str="${elapsed_secs}s"
    elif [[ "$elapsed_secs" -lt 3600 ]]; then
      local mins secs
      mins=$(( elapsed_secs / 60 ))
      secs=$(( elapsed_secs % 60 ))
      elapsed_str="${mins}m ${secs}s"
    else
      local hrs mins
      hrs=$(( elapsed_secs / 3600 ))
      mins=$(( (elapsed_secs % 3600) / 60 ))
      elapsed_str="${hrs}h ${mins}m"
    fi
  fi
  echo "$elapsed_str"
}

# --- Compact mode: concise format for status window ---
if [[ "$COMPACT_MODE" == "true" ]]; then
  printf '%s\342\200\224 currently working \342\200\224%s\n' "$C_DIM" "$RESET"
  if [[ -z "$WORKING_ID" ]]; then
    printf '  %s(none)%s\n' "$C_DIM" "$RESET"
  else
    printf '  %s%s%s\n' "$C_WORK" "$WORKING_ID" "$RESET"
    if [[ -n "$WORKING_MTIME" ]] && [[ "$WORKING_MTIME" -gt 0 ]]; then
      STARTED_COMPACT="$(date -d "@${WORKING_MTIME}" '+%H:%M:%S' 2>/dev/null || \
                 date -r "$WORKING_MTIME" '+%H:%M:%S' 2>/dev/null || echo "unknown")"
      ELAPSED_COMPACT="$(compute_elapsed "$WORKING_MTIME")"
      printf '  started: %s    elapsed: %s\n' "$STARTED_COMPACT" "$ELAPSED_COMPACT"
    fi
  fi
  exit 0
fi

# --- Print section header (full mode) ---
printf '%s[ Active Task ]%s\n' "$C_LABL" "$RESET"
echo ""

if [[ -z "$WORKING_ID" ]]; then
  printf '%s(no task currently in WORKING state)%s\n' "$C_DIM" "$RESET"
  exit 0
fi

# --- Task ID and role ---
printf '%sTask:%s  %s\n' "$C_LABL" "$RESET" "$WORKING_ID"
printf '%sRole:%s  %s\n' "$C_LABL" "$RESET" "$WORKING_ROLE"

# --- Elapsed time ---
if [[ -n "$WORKING_MTIME" ]] && [[ "$WORKING_MTIME" -gt 0 ]]; then
  STARTED="$(date -d "@${WORKING_MTIME}" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || \
             date -r "$WORKING_MTIME" '+%Y-%m-%d %H:%M:%S %Z' 2>/dev/null || echo "unknown")"
  ELAPSED_STR="$(compute_elapsed "$WORKING_MTIME")"

  printf '%sStarted:%s %s\n' "$C_LABL" "$RESET" "$STARTED"
  printf '%sElapsed:%s %s\n' "$C_LABL" "$RESET" "$ELAPSED_STR"
fi

# --- Read task details from README and status ---
# Resolve owning project from data.sh output (no default substitution).
# If no project is resolved, TASK_DIR will not exist and the README/status
# sections below are skipped gracefully.
_sa_project="$(get_val PROJECT_NAME "")"
_sa_tasks_dir=""
if [[ -n "$_sa_project" ]]; then
    _sa_tasks_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_sa_project" 2>/dev/null || true)"
fi
TASK_DIR="${_sa_tasks_dir:+${_sa_tasks_dir}/}${WORKING_ID}"
README_FILE="${TASK_DIR}/README.md"
STATUS_FILE="${TASK_DIR}/status.md"

echo ""

if [[ -f "$README_FILE" ]]; then
  # Extract ## Goal section (first 3 lines of content)
  GOAL="$(awk '
    /^## Goal/ { found=1; next }
    found && /^## / { exit }
    found && NF { lines++; print; if (lines >= 3) exit }
  ' "$README_FILE")"
  if [[ -n "$GOAL" ]]; then
    printf '%sGoal:%s\n' "$C_LABL" "$RESET"
    while IFS= read -r line; do
      printf '  %s\n' "$line"
    done <<< "$GOAL"
    echo ""
  fi
fi

if [[ -f "$STATUS_FILE" ]]; then
  # Extract ## Summary section (first 5 lines of content)
  SUMMARY="$(awk '
    /^## Summary/ { found=1; next }
    found && /^## / { exit }
    found && NF { lines++; print; if (lines >= 5) exit }
  ' "$STATUS_FILE")"
  if [[ -n "$SUMMARY" ]] && [[ "$SUMMARY" != "TBD" ]] && [[ "$SUMMARY" != "none" ]]; then
    printf '%sSummary:%s\n' "$C_LABL" "$RESET"
    while IFS= read -r line; do
      printf '  %s\n' "$line"
    done <<< "$SUMMARY"
    echo ""
  fi

  # Extract ## Blockers section
  BLOCKERS="$(awk '
    /^## Blockers/ { found=1; next }
    found && /^## / { exit }
    found && NF { lines++; print; if (lines >= 3) exit }
  ' "$STATUS_FILE")"
  if [[ -n "$BLOCKERS" ]] && [[ "$BLOCKERS" != "none" ]]; then
    printf '%sBlockers:%s\n' "$C_LABL" "$RESET"
    while IFS= read -r line; do
      printf '  %s\n' "$line"
    done <<< "$BLOCKERS"
    echo ""
  fi
fi
