#!/usr/bin/env bash
# dashboard-training-logs.sh
# Merged, color-coded training trace viewer for the dashboard training-logs window.
#
# When at least one project has [training] reasoning_trace=true in project.cfg,
# or has an existing per-project training corpus directory, the pane enumerates
# every projects/*/logs/training/ tree and renders the newest trace per agent
# per project, sorted by modification time (most recent first / newest at top).
#
# When PGAI_REASONING_TRACE=1 (global override), all projects are treated as
# active regardless of per-project config.
#
# When neither flag is on and no project has a corpus, shows a placeholder and
# exits.
#
# Designed to be run under watch -t -n 30 so the view refreshes automatically.
#
# Color scheme (same as dashboard-logs.sh — reused, not reinvented):
#   pm      = cyan
#   coder   = green
#   writer  = yellow
#   tester  = blue
#   cm      = magenta
#   overwatch = dim
#
# Per-project training traces are expected at:
#   $KANBAN_ROOT/projects/<project>/logs/training/<agent>/<timestamp>-<task-id>.md
#
# Missing training directories are handled gracefully (skipped silently).
#
# Usage:
#   dashboard-training-logs.sh [--kanban-root <path>]
#
# Flags:
#   --kanban-root <path>   Override the kanban root path
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   PGAI_REASONING_TRACE         — set to 1 to activate trace display globally
#   NO_COLOR                     — set to 1 to disable color

set -uo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

MAX_LINES_PER_AGENT=80

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT_OVERRIDE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_OVERRIDE="${2:-}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    --*)
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ -n "$KANBAN_ROOT_OVERRIDE" ]]; then
  KANBAN_ROOT="$KANBAN_ROOT_OVERRIDE"
fi
export KANBAN_ROOT

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${_SCRIPT_DIR}/../lib/projects.sh"
unset _SCRIPT_DIR

# ---------------------------------------------------------------------------
# Global PGAI_REASONING_TRACE gate
# ---------------------------------------------------------------------------
REASONING_TRACE="${PGAI_REASONING_TRACE:-}"

# ---------------------------------------------------------------------------
# Enumerate projects and build the list of active training roots
# ---------------------------------------------------------------------------
# A project is "active" for training display when:
#   1. PGAI_REASONING_TRACE=1 (global override), OR
#   2. pp_reasoning_trace <project> returns 'true', OR
#   3. The project's per-project training corpus directory already exists
#      (i.e., traces were written before the flag was configured).
# Projects with no flag and no corpus are silently skipped.

declare -a ACTIVE_PROJECTS=()
declare -A PROJECT_TRAINING_ROOT=()

while IFS= read -r _proj; do
  [[ -z "$_proj" ]] && continue
  _proj_root="$(pp_project_root "$_proj" 2>/dev/null)" || continue
  _training_dir="${_proj_root}/logs/training"
  _trace_flag="$(pp_reasoning_trace "$_proj" 2>/dev/null || echo 'false')"

  if [[ "$REASONING_TRACE" == "1" ]] || [[ "$_trace_flag" == "true" ]] || [[ -d "$_training_dir" ]]; then
    ACTIVE_PROJECTS+=("$_proj")
    PROJECT_TRAINING_ROOT["$_proj"]="$_training_dir"
  fi
done < <(projects_cfg_list 2>/dev/null)

# ---------------------------------------------------------------------------
# If nothing is active anywhere, show the disabled placeholder and exit
# ---------------------------------------------------------------------------
if [[ ${#ACTIVE_PROJECTS[@]} -eq 0 ]]; then
  printf '\n  [training-logs] (reasoning trace disabled — set PGAI_REASONING_TRACE=1 or enable [training] reasoning_trace = true in a project.cfg to activate)\n\n'
  exit 0
fi

# ---------------------------------------------------------------------------
# Color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

if [[ "$USE_COLOR" == "true" ]]; then
  C_CYAN=$'\033[0;36m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_MAGENTA=$'\033[0;35m'
  C_DIM=$'\033[2m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_CYAN=""
  C_GREEN=""
  C_YELLOW=""
  C_BLUE=""
  C_MAGENTA=""
  C_DIM=""
  C_BOLD=""
  C_RESET=""
fi

# Agent order and color mapping — same as dashboard-logs.sh
AGENT_ORDER=(pm coder writer tester cm overwatch)

declare -A AGENT_COLOR
AGENT_COLOR[pm]="$C_CYAN"
AGENT_COLOR[coder]="$C_GREEN"
AGENT_COLOR[writer]="$C_YELLOW"
AGENT_COLOR[tester]="$C_BLUE"
AGENT_COLOR[cm]="$C_MAGENTA"
AGENT_COLOR[overwatch]="$C_DIM"

# ---------------------------------------------------------------------------
# Collect (mtime, project, agent, filepath) for the newest trace file per
# agent per project.  Build a list sorted by mtime descending so most recent
# appears first (top).
# ---------------------------------------------------------------------------
ENTRIES=()   # each element: "<epoch> <project> <agent> <filepath>"

for _proj in "${ACTIVE_PROJECTS[@]}"; do
  _training_root="${PROJECT_TRAINING_ROOT[$_proj]}"
  if [[ ! -d "$_training_root" ]]; then
    continue
  fi

  for agent in "${AGENT_ORDER[@]}"; do
    agent_dir="${_training_root}/${agent}"
    if [[ ! -d "$agent_dir" ]]; then
      continue
    fi

    # Find the newest .md file (ls -t sorts newest first)
    newest_file="$(ls -t "${agent_dir}"/*.md 2>/dev/null | head -n1 || true)"
    if [[ -z "$newest_file" ]]; then
      continue
    fi

    # Get mtime as epoch seconds for sorting
    mtime="$(stat -c '%Y' "$newest_file" 2>/dev/null || echo "0")"
    ENTRIES+=("${mtime} ${_proj} ${agent} ${newest_file}")
  done
done

# ---------------------------------------------------------------------------
# If no trace files found at all, show a summary status
# ---------------------------------------------------------------------------
if [[ ${#ENTRIES[@]} -eq 0 ]]; then
  printf '\n  [training-logs] no trace files found yet\n\n'
  printf '  Active projects checked: %s\n' "${ACTIVE_PROJECTS[*]}"
  exit 0
fi

# Sort entries by mtime descending (newest first, oldest at bottom)
IFS=$'\n' SORTED_ENTRIES=($(printf '%s\n' "${ENTRIES[@]}" | sort -rn))
unset IFS

# ---------------------------------------------------------------------------
# Render each trace block in sorted order
# ---------------------------------------------------------------------------
SEPARATOR="$(printf '%.0s-' {1..60})"

for entry in "${SORTED_ENTRIES[@]}"; do
  # Parse: "<epoch> <project> <agent> <filepath>"
  mtime_val="${entry%% *}"
  rest="${entry#* }"
  proj_name="${rest%% *}"
  rest="${rest#* }"
  agent="${rest%% *}"
  filepath="${rest#* }"

  color="${AGENT_COLOR[$agent]:-}"
  basename_file="$(basename "$filepath")"

  # Colored agent banner — includes project name
  if [[ "$USE_COLOR" == "true" ]]; then
    printf '\n%s%s[%s/%s] %s%s\n' "$color" "$C_BOLD" "$proj_name" "$agent" "$basename_file" "$C_RESET"
  else
    printf '\n[%s/%s] %s\n' "$proj_name" "$agent" "$basename_file"
  fi
  printf '%s\n' "$SEPARATOR"

  # Display up to MAX_LINES_PER_AGENT lines of the trace file
  if [[ "$USE_COLOR" == "true" ]]; then
    head -n "$MAX_LINES_PER_AGENT" "$filepath" 2>/dev/null | \
      awk -v color="$color" -v reset="$C_RESET" '{printf "%s%s%s\n", color, $0, reset}' || true
  else
    head -n "$MAX_LINES_PER_AGENT" "$filepath" 2>/dev/null || true
  fi

  # Count additional files in this project/agent directory
  _training_root="${PROJECT_TRAINING_ROOT[$proj_name]}"
  all_files="$(ls -t "${_training_root}/${agent}"/*.md 2>/dev/null | tail -n +2 || true)"
  if [[ -n "$all_files" ]]; then
    file_count="$(printf '%s\n' "$all_files" | wc -l)"
    if [[ "$USE_COLOR" == "true" ]]; then
      printf '%s  ... and %d older trace file(s)%s\n' "$C_DIM" "$file_count" "$C_RESET"
    else
      printf '  ... and %d older trace file(s)\n' "$file_count"
    fi
  fi
done
