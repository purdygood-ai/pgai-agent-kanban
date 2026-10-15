#!/usr/bin/env bash
# costs.sh
# Renders two cost-breakdown blocks for the pgai kanban dashboard:
#   Block 1: Today's per-LLM cost breakdown, grouped by project then provider/model.
#   Block 2: Current RC's per-LLM cost breakdown for projects with an active RC.
#
# Reads per-project token rollups from:
#   projects/<name>/usage/daily/$(date +%Y-%m-%d).json  (today's block)
#   projects/<name>/usage/rc/<active-rc>-tokens.json    (RC block)
#
# Converts tokens to dollar costs using team/scripts/lib/token_pricing.json.
#
# Designed to run under `watch -t -c -n $REFRESH_INTERVAL costs.sh`
# in a tmux Costs window created by dashboard/create.sh.
#
# Usage:
#   costs.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Override the kanban root directory
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Environment variables:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors
#
# Read-only: this script never modifies any kanban state.
# Exits 0 always; renders friendly placeholders on missing data.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:29}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--no-color]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source project libraries
# ---------------------------------------------------------------------------
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# The cost blocks, the RC header detail and the day total come from
# pgai_agent_kanban.lib.metrics (costs_pane), reached through pp_run_ops.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=../lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"
GREEN="$(ansi_code green)"
YELLOW="$(ansi_code yellow)"

# ---------------------------------------------------------------------------
# Locate token_pricing.json
# ---------------------------------------------------------------------------
# Resolved relative to the script's lib/ directory (canonical location in dev tree).
PRICING_FILE=""

# Check script-relative path (works when running from dev tree)
if [[ -f "${SCRIPT_DIR}/../lib/token_pricing.json" ]]; then
  PRICING_FILE="${SCRIPT_DIR}/../lib/token_pricing.json"
fi

# ---------------------------------------------------------------------------
# Shared helpers: section_header (lib/dashboard_common.sh) and _active_rc,
# a project's Active RC or "" when none (lib/cm/common.sh).
# ---------------------------------------------------------------------------
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"

# ---------------------------------------------------------------------------
# Helper: find the daily rollup file for a project.
# Reads from projects/<name>/usage/daily/<date>.json (canonical path).
# ---------------------------------------------------------------------------
_day_rollup_file() {
  local project_name="$1"
  local date_str="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }

  local candidate="${proj_root}/usage/daily/${date_str}.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Helper: find the RC rollup file for a project and RC version.
# ---------------------------------------------------------------------------
_rc_rollup_file() {
  local project_name="$1"
  local rc_version="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }

  local candidate="${proj_root}/usage/rc/${rc_version}-tokens.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# _cost_block <rollup_json_path> <pricing_json_path> <indent_string>
# Prints the roll-up's per-model cost block with each line prefixed by the
# indent, preceded by one line carrying the block's total (the sum of its
# printed subtotals, %.2f): pgai_agent_kanban.lib.metrics.cost_block().
# Callers split the first line off with ${out%%$'\n'*} / ${out#*$'\n'}.
# ---------------------------------------------------------------------------
_cost_block() {
  local rollup_file="$1"
  local pricing_file="$2"
  local indent="$3"
  pp_run_ops pgai_agent_kanban.lib.metrics cost_block \
    "$rollup_file" "${pricing_file:-}" "${#indent}"
}

# ---------------------------------------------------------------------------
# Render today's cost block (Block 1)
# ---------------------------------------------------------------------------
TODAY="$(date +%Y-%m-%d)"

section_header "Costs — today (${TODAY})"
printf '\n'

# Read registered projects
REGISTERED_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  REGISTERED_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  printf '%s(no projects registered)%s\n' "$DIM" "$RESET"
else
  DAY_TOTALS=()

  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    printf '  %s%s%s\n' "${BOLD}" "$_proj" "$RESET"

    _rollup_file="$(_day_rollup_file "$_proj" "$TODAY")"

    if [[ -z "$_rollup_file" ]]; then
      # No rollup file at all — check if project has any task activity
      _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
      _tasks_dir="${_proj_root}/tasks"
      if [[ -n "$_proj_root" && -d "$_tasks_dir" ]]; then
        printf '    %s(no activity yet today)%s\n' "$DIM" "$RESET"
      else
        printf '    %s(no data yet)%s\n' "$DIM" "$RESET"
      fi
    else
      # Render the cost breakdown; keep its total for the day total.
      _block_output="$(_cost_block "$_rollup_file" "${PRICING_FILE:-}" "    ")"
      DAY_TOTALS+=("${_block_output%%$'\n'*}")
      printf '%s\n' "${_block_output#*$'\n'}"
    fi
    printf '\n'
  done

  # Each running sum is rounded to cents; "0" when no project had a roll-up.
  DAY_GRAND_TOTAL="$(pp_run_ops pgai_agent_kanban.lib.metrics sum_amounts "${DAY_TOTALS[@]}" 2>/dev/null || echo "0")"
  printf '  %sDay total: $%s%s\n' "${BOLD}" "$DAY_GRAND_TOTAL" "$RESET"
fi

printf '\n'
printf '\n'

# ---------------------------------------------------------------------------
# Render current RC cost block (Block 2)
# ---------------------------------------------------------------------------
section_header "Costs — current RC"
printf '\n'

if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  printf '%s(no projects registered)%s\n' "$DIM" "$RESET"
else
  # Check if any project has an active RC
  ANY_RC_ACTIVE=false
  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    _rc="$(_active_rc "$_proj")"
    if [[ -n "$_rc" ]]; then
      ANY_RC_ACTIVE=true
      break
    fi
  done

  if [[ "$ANY_RC_ACTIVE" == "false" ]]; then
    printf '%s(no RC active in any project)%s\n' "$DIM" "$RESET"
  else
    for _proj in "${REGISTERED_PROJECTS[@]}"; do
      _rc="$(_active_rc "$_proj")"

      if [[ -z "$_rc" ]]; then
        printf '  %s(no active RC)%s\n' "$DIM" "$RESET"
        continue
      fi

      # Get RC metadata for display
      _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
      _rc_file="$(_rc_rollup_file "$_proj" "$_rc")"

      # Build the project header line with RC info
      _rc_label="${_proj} ${_rc}"
      if [[ -n "$_rc_file" && -f "$_rc_file" ]]; then
        # "started <shipped_at>, <N> tasks" or "<N> tasks"; empty when unreadable.
        _started_at="$(pp_run_ops pgai_agent_kanban.lib.metrics rc_label "$_rc_file" 2>/dev/null || true)"
        if [[ -n "$_started_at" ]]; then
          _rc_label="${_proj} ${_rc} (${_started_at})"
        fi
      fi

      printf '  %s%s%s\n' "${BOLD}" "$_rc_label" "$RESET"

      if [[ -z "$_rc_file" ]]; then
        printf '    %s(no data yet)%s\n' "$DIM" "$RESET"
      else
        # Render cost breakdown; its first line is the RC total.
        _rc_block_output="$(_cost_block "$_rc_file" "${PRICING_FILE:-}" "    ")"
        printf '%s\n' "${_rc_block_output#*$'\n'}"
        _rc_total="${_rc_block_output%%$'\n'*}"
        printf '  %sRC total: $%s%s\n' "${BOLD}" "$_rc_total" "$RESET"
      fi
      printf '\n'
    done
  fi
fi

printf '\n'

# ---------------------------------------------------------------------------
# Footer: last-updated timestamp
# ---------------------------------------------------------------------------
printf '%supdated %s%s\n' "$DIM" "$(date '+%Y-%m-%d %H:%M:%S')" "$RESET"
