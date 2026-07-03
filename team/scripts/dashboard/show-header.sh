#!/usr/bin/env bash
# show-header.sh
# Renders the dashboard header pane.
#
# Example output:
#   pgai-agent-kanban v0.15.5
#   Project: pgai_agent_kanban  |  18:42:31 EDT
#
#   chain=RUNNING                                             Next cron firings:
#   Project: pgai_agent_kanban                        pm:     in 6 min
#                                                            coder:  in 1 min
#   Progress: ▓▓▓▓▓▓░░░░░░░░ 45%                            writer: in 14 min
#
# Note: per-project RC / Last Released are rendered in the right column (column-render.sh /
# show-multi.sh), not in this header. The header carries framework-level identity only.
#
# Also used as sub-component by show-status-window.sh.
#
# Usage:
#   show-header.sh [--kanban-root <path>]
#
# Configuration (via config.cfg):
#   PGAI_DASHBOARD_COLOR_HEADER   — header text color (default: cyan)
#   PGAI_DASHBOARD_COLOR_HALT     — halt warning color (default: red)
#
# Environment:
#   TERM=dumb  — disables all ANSI codes
#   NO_COLOR=1 — disables all ANSI codes

# --- Resolve script dir and source dashboard-data ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source shared Python-helper resolver (live-install anchor first — D3 fix)
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared version helper (single tier-order decision point)
# shellcheck source=lib/version.sh
source "${SCRIPT_DIR}/lib/version.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# --- Pass through args ---
DATA_ARGS=("$@")

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
for cfg_arg in "$@"; do
  shift
  if [[ "$cfg_arg" == "--kanban-root" ]]; then
    KANBAN_ROOT="${1:-$KANBAN_ROOT}"
    break
  fi
done
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# Ignore SIGPIPE so that piping show-header.sh output to an early-closing reader
# (e.g. "show-header.sh | head -1") does not terminate the script with exit 141.
# With SIGPIPE ignored, writes to a closed pipe return EPIPE rather than killing
# the process; the shell's set -e will still catch genuine failures.
trap '' PIPE

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

# Color defaults (configurable via config.cfg)
COLOR_HEADER="${PGAI_DASHBOARD_COLOR_HEADER:-cyan}"
COLOR_HALT="${PGAI_DASHBOARD_COLOR_HALT:-red}"
COLOR_OK="${PGAI_DASHBOARD_COLOR_OK:-green}"

ansi_code() {
  # Returns ANSI escape code for a named color, or empty string if color disabled.
  local color="$1"
  if [[ "$USE_COLOR" != "true" ]]; then
    echo ""
    return
  fi
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
C_HEADER="$(ansi_code "$COLOR_HEADER")"
C_HALT="$(ansi_code "$COLOR_HALT")"
C_OK="$(ansi_code "$COLOR_OK")"
C_BOLD="$(ansi_code bold)"
C_CYAN="$(ansi_code cyan)"
C_DIM="$(ansi_code dim)"
C_GREEN="$(ansi_code green)"
C_YELLOW="$(ansi_code yellow)"

# --- Collect data ---
DATA="$("$SCRIPT_DIR/data.sh" "${DATA_ARGS[@]}")"

get_val() {
  local key="$1"
  local default="${2:-}"
  echo "$DATA" | awk -F= -v k="$key" '$1 == k { sub(/^[^=]+=/, ""); print; found=1 } END { if (!found) print "'"$default"'" }'
}

KANBAN_ROOT_VAL="$(get_val KANBAN_ROOT "${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}")"
LAST_RELEASED="$(get_val LAST_RELEASED "none")"
HALT_FLAG="$(get_val HALT_FLAG "no")"
HALT_OVERWATCH_FLAG="$(get_val HALT_OVERWATCH_FLAG "no")"
HALT_TEXT="$(get_val HALT_TEXT "")"
HALT_SUMMARY="$(get_val HALT_SUMMARY "none")"
TOTAL="$(get_val TOTAL_TICKETS 0)"
DONE_COUNT="$(get_val DONE_COUNT 0)"

# --- Version resolution via shared helper ---
# Tier order: KANBAN_ROOT/VERSION > REPO_ROOT/VERSION > git tag --merged > Last Released > 'unknown'
# Single decision point: get_kanban_version in lib/version.sh (do not add tier-order logic here).
# Git tier uses tag --merged origin/main (reachability-independent), not git describe.
# Walk up from SCRIPT_DIR to find the dev-tree repo root (depth-independent .git-marker walk).
REPO_ROOT="$SCRIPT_DIR"
while [[ "$REPO_ROOT" != "/" ]] && [[ ! -d "$REPO_ROOT/.git" ]]; do
  REPO_ROOT="$(dirname "$REPO_ROOT")"
done
if [[ "$REPO_ROOT" == "/" ]]; then
  # Fallback if .git not found (e.g. shallow export or unusual layout)
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi
KANBAN_VERSION="$(get_kanban_version "$KANBAN_ROOT_VAL" "$REPO_ROOT" "${LAST_RELEASED:-}")"

# --- Project name resolution (from data layer) ---
RESOLVED_PROJECT="$(get_val PROJECT_NAME "")"

# --- Current time ---
TIMESTAMP="$(date '+%H:%M:%S %Z')"

# --- Derive project name (prefer dashboard-data emit, fallback to KANBAN_ROOT basename) ---
if [[ -n "$RESOLVED_PROJECT" ]]; then
  PROJECT_NAME="$RESOLVED_PROJECT"
else
  PROJECT_NAME="$(basename "$KANBAN_ROOT_VAL")"
fi

# --- Per-project version resolution ---
# When --project <P> is supplied, the header is a drill-window project row: resolve
# P's own last-released version via pp_last_released_version so all render paths
# (show-header, show-multi, status-bottom) agree on a project's version.
# When no --project is given (legacy single-project or framework-identity mode),
# fall back to KANBAN_VERSION (the deployed framework version).
if [[ -n "$RESOLVED_PROJECT" ]]; then
  DISPLAY_VERSION="$(KANBAN_ROOT="$KANBAN_ROOT_VAL" pp_last_released_version "$RESOLVED_PROJECT" 2>/dev/null || echo "v0.0.0")"
  [[ -z "$DISPLAY_VERSION" ]] && DISPLAY_VERSION="v0.0.0"
else
  DISPLAY_VERSION="$KANBAN_VERSION"
fi

# --- Progress bar (15 wide) ---
BAR_WIDTH=15
if [[ "$TOTAL" -gt 0 ]]; then
  FILLED=$(( DONE_COUNT * BAR_WIDTH / TOTAL ))
  PCT=$(( DONE_COUNT * 100 / TOTAL ))
else
  FILLED=0
  PCT=0
fi
EMPTY=$(( BAR_WIDTH - FILLED ))

if [[ "$USE_COLOR" == "true" ]]; then
  FILLED_CHAR=$'\xe2\x96\x93'   # U+2593 DARK SHADE
  EMPTY_CHAR=$'\xe2\x96\x91'    # U+2591 LIGHT SHADE
else
  FILLED_CHAR="#"
  EMPTY_CHAR="."
fi

BAR=""
for (( i=0; i<FILLED; i++ )); do BAR="${BAR}${FILLED_CHAR}"; done
for (( i=0; i<EMPTY; i++ ));  do BAR="${BAR}${EMPTY_CHAR}"; done

# --- Cron firings (via cron_parser.py) ---
#
# SHOW_HEADER_NOW: optional Unix timestamp (seconds since epoch) that fixes the
# reference "now" for all cron-firing computations in this render cycle.  When
# set, every agent's next-firing time is computed relative to that fixed point
# rather than the live wall clock.  Intended for testing — inject a timestamp
# that is not on a */5 boundary to guarantee deterministic "in N min" output.
#
# If SHOW_HEADER_NOW is unset or empty, we capture the current epoch once here
# so that all per-agent computations within the same render cycle share a single
# reference time (prevents sub-second drift between agents).
NOW_TS="${SHOW_HEADER_NOW:-$(date +%s)}"

declare -A CRON_FIRINGS
CRON_FIRINGS_AVAILABLE=false
CRON_ERROR=false
CRONTAB_TEXT="$(crontab -l 2>/dev/null || true)"
if [[ -n "$CRONTAB_TEXT" ]]; then
  CRON_PARSER="${PGAI_DEV_TREE_PATH}/team/pm-agent/lib/cron_parser.py"
  if [[ -f "$CRON_PARSER" ]]; then
    # Resolve cron_firings.py via shared helper resolver (live-install anchor first — D3 fix).
    CRON_FIRINGS_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/cron_firings.py")"
    CRON_JSON="$(python3 "$CRON_FIRINGS_PY" "$CRONTAB_TEXT" "$CRON_PARSER" "$NOW_TS" 2>/dev/null || true)"
    if [[ -n "$CRON_JSON" ]]; then
      CRON_FIRINGS_AVAILABLE=true
      # Parse JSON key=value pairs into associative array
      while IFS='=' read -r key val; do
        [[ -n "$key" ]] && CRON_FIRINGS["$key"]="$val"
      done < <(python3 -c "
import json, sys
d = json.loads(sys.argv[1])
for k,v in d.items():
    print(f'{k}={v}')
" "$CRON_JSON" 2>/dev/null || true)
    else
      CRON_ERROR=true
    fi
  else
    CRON_ERROR=true
  fi
fi

# --- HALT indicator ---
# CHAIN_STR is driven by HALT_SUMMARY (GLOBAL/PROJECT/none) from the data layer.
# GLOBAL wins when both global and per-project halts are active (global is the master switch).
# Project names are NOT rendered here — taskbar is width-constrained (see Tier-2 attention window).
if [[ "$HALT_SUMMARY" == "GLOBAL" ]]; then
  CHAIN_STR="${C_HALT}chain=HALT GLOBAL${RESET}"
elif [[ "$HALT_SUMMARY" == "PROJECT" ]]; then
  CHAIN_STR="${C_HALT}chain=HALT PROJECT${RESET}"
else
  CHAIN_STR="${C_OK}chain=RUNNING${RESET}"
fi
HALT_STR="${CHAIN_STR}"

# Append per-project halt text when non-empty.
# HALT_TEXT is one of: "" (normal), "HALT-AFTER <event>" (draining), "HALT" (halted).
if [[ -n "$HALT_TEXT" ]]; then
  if [[ "$HALT_TEXT" == "HALT" ]]; then
    _ht_color="$C_HALT"
  else
    # draining: HALT-AFTER <event> → yellow
    _ht_color="$C_YELLOW"
  fi
  HALT_STR="${HALT_STR}  ${_ht_color}[${HALT_TEXT}]${RESET}"
  unset _ht_color
fi

# --- Render row 1: project name + version ---
# When invoked with --project <P> (drill window): PROJECT_NAME is the drilled project,
# DISPLAY_VERSION is that project's own last-released version (all render paths agree).
# When invoked without --project (legacy single-project / framework-identity mode):
# DISPLAY_VERSION falls back to KANBAN_VERSION (deployed framework version).
printf '%s%s %s%s\n' \
  "$C_BOLD" \
  "$PROJECT_NAME" \
  "$DISPLAY_VERSION" \
  "$RESET"

echo ""

# --- Render header info block (left) side by side with cron firings (right) ---
#
# Left column lines:
LEFT_LINES=()
LEFT_LINES+=("${HALT_STR}")
LEFT_LINES+=("${C_HEADER}Project:${RESET} ${PROJECT_NAME}")
LEFT_LINES+=("Progress: ${C_GREEN}${BAR}${RESET} ${PCT}%")
LEFT_LINES+=("${TIMESTAMP}")

# Right column: cron firings
RIGHT_LINES=()
CRON_AGENTS=("pm" "coder" "writer" "tester" "cm" "cleanup")
if [[ "$CRON_FIRINGS_AVAILABLE" == "true" ]]; then
  RIGHT_LINES+=("${C_CYAN}Next cron firings:${RESET}")
  for agent in "${CRON_AGENTS[@]}"; do
    firing="${CRON_FIRINGS[$agent]:-}"
    if [[ -n "$firing" ]]; then
      RIGHT_LINES+=("$(printf '  %-9s %s' "${agent}:" "$firing")")
    fi
  done
  if [[ ${#RIGHT_LINES[@]} -eq 1 ]]; then
    RIGHT_LINES+=("  (no kanban cron entries found)")
  fi
elif [[ "$CRON_ERROR" == "true" ]]; then
  RIGHT_LINES+=("${C_DIM}Next cron firings:${RESET}")
  RIGHT_LINES+=("  (crontab not parseable)")
else
  # No crontab at all — show placeholder
  RIGHT_LINES+=("${C_DIM}Next cron firings:${RESET}")
  RIGHT_LINES+=("  (no crontab entries)")
fi

# Print left and right columns side by side
LEFT_WIDTH=48
MAX_ROWS=$(( ${#LEFT_LINES[@]} > ${#RIGHT_LINES[@]} ? ${#LEFT_LINES[@]} : ${#RIGHT_LINES[@]} ))
for (( row=0; row<MAX_ROWS; row++ )); do
  left_text="${LEFT_LINES[$row]:-}"
  right_text="${RIGHT_LINES[$row]:-}"
  printf '%-*s  %s\n' "$LEFT_WIDTH" "$left_text" "$right_text"
done
