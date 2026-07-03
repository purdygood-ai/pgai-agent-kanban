#!/usr/bin/env bash
# dashboard-costs.sh
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
# Designed to run under `watch -t -c -n $REFRESH_INTERVAL dashboard-costs.sh`
# in a tmux Costs window created by dashboard-create.sh.
#
# Usage:
#   dashboard-costs.sh [--kanban-root <path>] [--no-color]
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

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
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
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -35
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

# Source shared Python-helper resolver (live-install anchor first — D3 fix)
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

_c() {
  [[ "$USE_COLOR" != "true" ]] && { printf ''; return; }
  case "$1" in
    bold)    printf '\033[1m' ;;
    dim)     printf '\033[2m' ;;
    reset)   printf '\033[0m' ;;
    cyan)    printf '\033[0;36m' ;;
    green)   printf '\033[0;32m' ;;
    yellow)  printf '\033[0;33m' ;;
    white)   printf '\033[0;37m' ;;
    *)       printf '' ;;
  esac
}

RESET="$(_c reset)"
BOLD="$(_c bold)"
DIM="$(_c dim)"
CYAN="$(_c cyan)"
GREEN="$(_c green)"
YELLOW="$(_c yellow)"

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
# Section header helper (matches dashboard-metadata.sh style)
# ---------------------------------------------------------------------------
section_header() {
  local title="$1"
  printf '%s%s%s\n' "${CYAN}${BOLD}" "=== ${title} ===" "$RESET"
}

# ---------------------------------------------------------------------------
# Helper: read Active RC from release-state.md
# Returns empty string if none.
# Liberal parse — read first ## Active RC header only, trim whitespace,
# validate: accept only vX.Y.Z semver; anything else (empty, malformed, 'none') → "".
# ---------------------------------------------------------------------------
_active_rc() {
  local project_name="$1"
  local release_state
  release_state="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)/release-state.md" || { echo ""; return; }

  if [[ ! -f "$release_state" ]]; then
    echo ""
    return
  fi

  local rc
  rc="$(awk '/^##[[:space:]]+Active RC/{found=1;next} found && /^[[:space:]]*$/{next} found{print; exit}' \
        "$release_state" 2>/dev/null | tr -d '[:space:]')"

  if [[ "$rc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "$rc"
  else
    echo ""
  fi
}

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
# Python cost computation engine.
# Delegates to team/pgai_agent_kanban/dashboard/render_costs.py.
# Arguments: <rollup_json_path> <pricing_json_path> <indent_spaces>
# ---------------------------------------------------------------------------
_render_cost_block_python() {
  local rollup_file="$1"
  local pricing_file="$2"
  local indent="$3"
  # Convert indent string to a space count for --indent flag
  local indent_count="${#indent}"

  # Resolve render_costs.py via shared helper resolver (live-install anchor first — D3 fix).
  local _rcp_py
  _rcp_py="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/render_costs.py")"
  python3 "$_rcp_py" \
    "$rollup_file" "${pricing_file:-}" --indent "$indent_count"
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
  DAY_GRAND_TOTAL=0

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
      # Render cost breakdown via Python helper
      _block_output="$(_render_cost_block_python "$_rollup_file" "${PRICING_FILE:-}" "    ")"
      printf '%s\n' "$_block_output"

      # Extract subtotal from the rendered block to compute day total
      # (Sum all subtotal lines from the Python output)
      _proj_total="$(echo "$_block_output" | python3 -c "
import sys, re
total = 0.0
for line in sys.stdin:
    m = re.search(r'subtotal:\s+\\\$([\d,]+\.?\d*)', line)
    if m:
        total += float(m.group(1).replace(',',''))
print(f'{total:.2f}')
" 2>/dev/null || echo "0.00")"
      DAY_GRAND_TOTAL="$(python3 -c "print(f'{float(\"${DAY_GRAND_TOTAL}\") + float(\"${_proj_total}\"):.2f}')" 2>/dev/null || echo "$DAY_GRAND_TOTAL")"
    fi
    printf '\n'
  done

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
        # Extract started_at and task count from JSON if available
        _started_at="$(python3 -c "
import json, sys
try:
    d = json.load(open('${_rc_file}'))
    s = d.get('shipped_at', '') or d.get('started_at', '')
    tasks = len(d.get('tasks', []))
    if s:
        print(f'started {s}, {tasks} tasks')
    else:
        print(f'{tasks} tasks')
except:
    pass
" 2>/dev/null || true)"
        if [[ -n "$_started_at" ]]; then
          _rc_label="${_proj} ${_rc} (${_started_at})"
        fi
      fi

      printf '  %s%s%s\n' "${BOLD}" "$_rc_label" "$RESET"

      if [[ -z "$_rc_file" ]]; then
        printf '    %s(no data yet)%s\n' "$DIM" "$RESET"
      else
        # Render cost breakdown
        _rc_block_output="$(_render_cost_block_python "$_rc_file" "${PRICING_FILE:-}" "    ")"
        printf '%s\n' "$_rc_block_output"

        # RC total
        _rc_total="$(echo "$_rc_block_output" | python3 -c "
import sys, re
total = 0.0
for line in sys.stdin:
    m = re.search(r'subtotal:\s+\\\$([\d,]+\.?\d*)', line)
    if m:
        total += float(m.group(1).replace(',',''))
print(f'{total:.2f}')
" 2>/dev/null || echo "0.00")"
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
