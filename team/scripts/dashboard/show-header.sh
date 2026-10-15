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
#   show-header.sh [--kanban-root <path>] [-h|--help]
#
# Configuration (environment; show-multi.sh exports each from kanban.cfg
# [dashboard] before it renders this pane):
#   color_header / PGAI_DASHBOARD_COLOR_HEADER   — header text color (default: cyan)
#   color_halt   / PGAI_DASHBOARD_COLOR_HALT     — halt warning color (default: red)
#
# Environment:
#   TERM=dumb  — disables all ANSI codes
#   NO_COLOR=1 — disables all ANSI codes

# shellcheck source=../lib/header_help.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"

# -h/--help prints the header comment above and exits 0. It is handled before
# env_bootstrap.sh so help never resolves a kanban root, reads an argument as
# data, or starts the script's own work.
for __help_arg in "$@"; do
  case "$__help_arg" in
    -h|--help)
      # The header block up to its first non-comment line, however long it
      # grows; awk writes it straight to stdout, so no pipe stage can race.
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir and source dashboard-data ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Local-only render guard: this is a dashboard render path, so
# pp_last_released_version below (DISPLAY_VERSION resolution) must never
# perform a network git fetch.  Also exported into the data.sh subprocess
# invoked below, which independently sets the same guard for its own
# pp_last_released_version call.
export PGAI_GIT_LOCAL_ONLY=1

# Source shared Python-helper resolver (live-install anchor first — D3 fix)
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared halt scope helper (single decision point for halt scope —
# same function status-bottom.sh and status-right.sh call, so this header
# can never disagree with the bottom bar about which scopes are active).
# shellcheck source=lib/halt_scope.sh
source "${SCRIPT_DIR}/lib/halt_scope.sh"
# Source shared version helper (single tier-order decision point)
# shellcheck source=lib/version.sh
source "${SCRIPT_DIR}/lib/version.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# Source shared pane-geometry query + row-fit-with-marker helpers — the
# same functions next-cron-firings.sh uses, so both next-fire homes
# truncate identically rather than drifting apart.
# shellcheck source=lib/next_fire_viewport.sh
source "${SCRIPT_DIR}/lib/next_fire_viewport.sh"
# Source the shared roster band renderer — the single "one renderer, two
# call sites" home for packing the roster into a columnar band or falling
# back to the vertical list. next-cron-firings.sh's schedule-window band
# renders through the same functions, so window 0's tile can never
# disagree with it entry-for-entry.
# shellcheck source=lib/roster_band.sh
source "${SCRIPT_DIR}/lib/roster_band.sh"
# Source shared dashboard layout constants (PGAI_ROSTER_BAND_MAX_ROWS,
# PGAI_ROSTER_BAND_SEPARATOR) — single source of truth, never hardcoded
# independently here.
# shellcheck source=../lib/dashboard_constants.sh
source "${SCRIPT_DIR}/../lib/dashboard_constants.sh"

# --- Pass through args ---
DATA_ARGS=("$@")

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
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

# Color defaults (each overridden by its PGAI_DASHBOARD_COLOR_* variable; see Configuration above)
COLOR_HEADER="${PGAI_DASHBOARD_COLOR_HEADER:-cyan}"
COLOR_HALT="${PGAI_DASHBOARD_COLOR_HALT:-red}"
COLOR_OK="${PGAI_DASHBOARD_COLOR_OK:-green}"

# ansi_code() is defined in lib/script_common.sh (sourced above).
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

# get_val reads one KEY=VALUE pair from DATA (lib/dashboard_common.sh).
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"

KANBAN_ROOT_VAL="$(get_val KANBAN_ROOT "${PGAI_AGENT_KANBAN_ROOT_PATH}")"
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

# --- Cron firings (via the shared roster renderer's entry fetcher) ---
#
# SHOW_HEADER_NOW: optional Unix timestamp (seconds since epoch) that fixes the
# reference "now" for all cron-firing computations in this render cycle.  When
# set, every agent's next-firing time is computed relative to that fixed point
# rather than the live wall clock.  Intended for testing — inject a timestamp
# that is not on a */5 boundary to guarantee deterministic countdown output.
#
# If SHOW_HEADER_NOW is unset or empty, we capture the current epoch once here
# so that all per-agent computations within the same render cycle share a single
# reference time (prevents sub-second drift between agents).
NOW_TS="${SHOW_HEADER_NOW:-$(date +%s)}"

# ROSTER_BAND_DISPLAY (populated below via roster_band_fetch_entries) is the
# same entry-text array next-cron-firings.sh's schedule-window band renders
# from — the "one renderer, two call sites" requirement: window 0's tile
# and the schedule window agree entry-for-entry because both read the SAME
# roster_entries.py output through the SAME lib/roster_band.sh functions,
# never a second copy of this parsing.
CRON_FIRINGS_AVAILABLE=false
CRON_ERROR=false
CRONTAB_TEXT="$(crontab -l 2>/dev/null || true)"
if [[ -n "$CRONTAB_TEXT" ]]; then
  # cron_parser.py is resolved from the live-install anchor only.
  # The dev tree is DATA to the live runtime; executing python code under
  # PGAI_DEV_TREE_PATH is prohibited for live-runtime scripts.
  CRON_PARSER="${KANBAN_ROOT}/pm-agent/lib/cron_parser.py"
  if [[ -f "$CRON_PARSER" ]]; then
    # Resolve roster_entries.py via shared helper resolver (live-install anchor first — D3 fix).
    ROSTER_ENTRIES_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/roster_entries.py")"
    if [[ -n "$ROSTER_ENTRIES_PY" ]]; then
      CRON_FIRINGS_AVAILABLE=true
      roster_band_fetch_entries "$ROSTER_ENTRIES_PY" "$CRON_PARSER" "$CRONTAB_TEXT" "$NOW_TS"
    else
      CRON_ERROR=true
    fi
  else
    CRON_ERROR=true
  fi
fi

# --- HALT / HALT-AFTER indicator — full enumeration ---
# Delegates to lib/halt_scope.sh (dashboard_halt_scope), the single decision
# point for halt scope — the SAME function status-bottom.sh and
# status-right.sh call, so this header can never show a picture that
# contradicts the bottom bar. Renders every ACTIVE scope simultaneously:
# a global HALT and a project's own HALT-AFTER are not mutually exclusive
# here, and two separately-halted projects are both named — neither
# suppresses the other. Scope words are the SOP vocabulary exactly
# ("GLOBAL" or the literal project name).
#
# CHAIN_STR stays a coarse fleet-health headline (chain=HALT / chain=RUNNING)
# — the per-scope detail lives entirely in the bracketed segments that
# follow, so the two never say the same thing twice.
#
# A draining segment's event token is rendered through
# dashboard_halt_describe_event (lib/halt_scope.sh) — the one shared
# "until vX.Y.Z" describer for a version-pinned token.
_SH_HALT_SEGMENTS=()
_sh_any_halted=false
_sh_any_active=false

while IFS=$'\t' read -r _sh_hs_state _sh_hs_scope _sh_hs_event; do
  [[ "$_sh_hs_state" == "normal" ]] && continue
  [[ -z "$_sh_hs_scope" ]] && continue
  _sh_any_active=true

  if [[ "$_sh_hs_state" == "halted" ]]; then
    _sh_any_halted=true
    _sh_hs_text="HALT ${_sh_hs_scope}"
    _sh_hs_color="$C_HALT"
  else
    _sh_hs_described=""
    if [[ -n "$_sh_hs_event" ]]; then
      _sh_hs_described="$(dashboard_halt_describe_event "$KANBAN_ROOT_VAL" "${PGAI_DEV_TREE_PATH:-}" "$_sh_hs_event")"
    fi
    if [[ -n "$_sh_hs_described" ]]; then
      _sh_hs_text="HALT-AFTER ${_sh_hs_scope} ${_sh_hs_described}"
    else
      _sh_hs_text="HALT-AFTER ${_sh_hs_scope}"
    fi
    _sh_hs_color="$C_YELLOW"
  fi
  _SH_HALT_SEGMENTS+=("${_sh_hs_color}[${_sh_hs_text}]${RESET}")
done < <(dashboard_halt_scope "$KANBAN_ROOT_VAL" "${PGAI_DEV_TREE_PATH:-}" "${RESOLVED_PROJECT:-}")
unset _sh_hs_state _sh_hs_scope _sh_hs_event _sh_hs_text _sh_hs_color _sh_hs_described

if [[ "$_sh_any_halted" == "true" ]]; then
  CHAIN_STR="${C_HALT}chain=HALT${RESET}"
elif [[ "$_sh_any_active" == "true" ]]; then
  CHAIN_STR="${C_YELLOW}chain=DRAINING${RESET}"
else
  CHAIN_STR="${C_OK}chain=RUNNING${RESET}"
fi

HALT_STR="$CHAIN_STR"
for _sh_seg in "${_SH_HALT_SEGMENTS[@]}"; do
  HALT_STR="${HALT_STR}  ${_sh_seg}"
done
unset _sh_seg _sh_any_halted _sh_any_active _SH_HALT_SEGMENTS

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

# Right column: cron firings.
# ROSTER_BAND_DISPLAY (populated above via roster_band_fetch_entries) drives
# the roster body, in roster order (pm first, never a literal agent tuple).
# ROSTER_LINES holds just the per-entry rows so they can be rendered
# separately from the fixed "Next cron firings:" title line above them.
RIGHT_LINES=()
ROSTER_LINES=()
if [[ "$CRON_FIRINGS_AVAILABLE" == "true" ]]; then
  RIGHT_LINES+=("${C_CYAN}Next cron firings:${RESET}")
  ROSTER_LINES=("${ROSTER_BAND_DISPLAY[@]}")
  if [[ ${#ROSTER_LINES[@]} -eq 0 ]]; then
    ROSTER_LINES+=("  (no kanban cron entries found)")
  fi

  # Render through the shared roster band renderer: pack the full roster
  # into a compact columnar band (at most PGAI_ROSTER_BAND_MAX_ROWS rows)
  # when the pane's queryable width allows it, or fall back to one entry
  # per line, fitted to the pane's queryable height — reserve the lines
  # that print unconditionally around it (the project/version line, the
  # blank line, and this column's own "Next cron firings:" title) and drop
  # from the BOTTOM with a "+N more" marker when even the vertical list
  # cannot fit. A pane whose geometry cannot be queried (non-tmux, piped,
  # --no-tmux one-shot mode) renders every entry untruncated.
  RESERVED_ROWS=3
  PANE_HEIGHT="$(next_fire_pane_height)"
  if [[ -n "$PANE_HEIGHT" ]]; then
    ROSTER_BUDGET=$(( PANE_HEIGHT - RESERVED_ROWS ))
    (( ROSTER_BUDGET < 0 )) && ROSTER_BUDGET=0
  else
    ROSTER_BUDGET=""
  fi
  PANE_WIDTH="$(next_fire_pane_width)"
  while IFS= read -r roster_line; do
    RIGHT_LINES+=("$roster_line")
  done < <(roster_band_render "$PANE_WIDTH" "$PGAI_ROSTER_BAND_MAX_ROWS" \
    "$PGAI_ROSTER_BAND_SEPARATOR" "$ROSTER_BUDGET" "${ROSTER_LINES[@]}")
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
