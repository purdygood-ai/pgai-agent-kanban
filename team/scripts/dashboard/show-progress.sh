#!/usr/bin/env bash
# show-progress.sh
# Renders the overall progress pane (right ~50%).
#
# Example output (default):
#   Total: 11 tickets
#   Done:    5  ████████░░░░░░░░░░  45%
#   Working: 1
#   Blocked: 0
#   Waiting: 5
#
#   Last 5 done:
#   ✓ open-rc-v0-14-0       (CM)
#   ✓ bug3-example-config   (CODER)
#   ...
#
# Example output (--compact):
#   Progress: ▓▓▓▓▓▓▓░░░░░░░░ 45%  (5/11 done, 1 working)
#
# Usage:
#   show-progress.sh [--kanban-root <path>] [--compact] [-h|--help]
#
# Flags:
#   --compact   One-line summary format (used by show-status-window.sh)
#   -h, --help  Print this usage and exit 0
#
# Configuration (kanban.cfg [dashboard], overridden by the environment):
#   color_done    / PGAI_DASHBOARD_COLOR_DONE     — done bar color (default: green)
#   color_working / PGAI_DASHBOARD_COLOR_WORKING  — working count color (default: yellow)
#   color_blocked / PGAI_DASHBOARD_COLOR_BLOCKED  — blocked count color (default: red)
#
# Environment:
#   TERM=dumb  — disables ANSI codes and uses ASCII progress bar (# and .)
#   NO_COLOR=1 — disables ANSI codes

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

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# Source the per-tick snapshot cache: show-progress.sh's data.sh scan is a
# twin of the same --project scan run by an overview pane (show-multi.sh
# --mode progress) and/or a drill-N pane for the same project -- see
# snapshot.sh's dashboard_data_snapshot_get for the sharing contract.
# shellcheck source=lib/snapshot.sh
source "${SCRIPT_DIR}/lib/snapshot.sh"

# --- Parse args ---
COMPACT_MODE=false
PROJECT_NAME=""
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  if [[ "$arg" == "--compact" ]]; then
    COMPACT_MODE=true
  elif [[ "$arg" == "--project" ]]; then
    _next=$(( _i + 1 ))
    PROJECT_NAME="${_args[$_next]:-}"
    _i=$_next
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
    export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_done green)}"
    export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_working yellow)}"
    export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_blocked red)}"
    export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_label cyan)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"
COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-red}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"

# ansi_code() is defined in lib/script_common.sh (sourced above).
RESET="$(ansi_code reset)"
C_DONE="$(ansi_code "$COLOR_DONE")"
C_WORK="$(ansi_code "$COLOR_WORKING")"
C_BLCK="$(ansi_code "$COLOR_BLOCKED")"
C_LABL="$(ansi_code "$COLOR_LABEL")"
C_DIM="$(ansi_code dim)"

# --- Collect data ---
# Snapshot-cached: shares one per-tick data.sh scan with any twin pane
# invoking data.sh for the same project (see lib/snapshot.sh). Built from
# the already-resolved KANBAN_ROOT/PROJECT_NAME in a FIXED flag order
# (--kanban-root then --project) rather than the caller's own raw CLI argv
# order -- two callers that agree on kanban root and project must resolve to
# the identical cache key regardless of what order their own flags arrived
# in (an overview pane's show-multi.sh-constructed argv orders these flags
# differently than a drill-N pane's own argv). Falls back to a direct
# data.sh call (with a stderr-only narration) when no fresh snapshot exists
# -- never blanks this pane.
_CANON_DATA_ARGS=(--kanban-root "$KANBAN_ROOT")
[[ -n "$PROJECT_NAME" ]] && _CANON_DATA_ARGS+=(--project "$PROJECT_NAME")
DATA="$(dashboard_data_snapshot_get "$SCRIPT_DIR/data.sh" "${_CANON_DATA_ARGS[@]}")"

# get_val reads one KEY=VALUE pair from DATA (lib/dashboard_common.sh).
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"

TOTAL="$(get_val TOTAL_TICKETS 0)"
DONE="$(get_val DONE_COUNT 0)"
WORKING="$(get_val WORKING_COUNT 0)"
BLOCKED="$(get_val BLOCKED_COUNT 0)"
WAITING="$(get_val WAITING_COUNT 0)"
BACKLOG="$(get_val BACKLOG_COUNT 0)"

# --- Progress bar ---
# Bar is 20 chars wide. Use block/empty chars or #/. for dumb terminals.
BAR_WIDTH=20
if [[ "$TOTAL" -gt 0 ]]; then
  FILLED=$(( DONE * BAR_WIDTH / TOTAL ))
  PCT=$(( DONE * 100 / TOTAL ))
else
  FILLED=0
  PCT=0
fi
EMPTY=$(( BAR_WIDTH - FILLED ))

if [[ "$USE_COLOR" == "true" ]]; then
  FILLED_CHAR=$'\xe2\x96\x88'   # UTF-8: U+2588 FULL BLOCK
  EMPTY_CHAR=$'\xe2\x96\x91'    # UTF-8: U+2591 LIGHT SHADE
else
  FILLED_CHAR="#"
  EMPTY_CHAR="."
fi

BAR=""
for (( i=0; i<FILLED; i++ )); do BAR="${BAR}${FILLED_CHAR}"; done
for (( i=0; i<EMPTY; i++ ));  do BAR="${BAR}${EMPTY_CHAR}"; done

# --- Compact mode: single-line format ---
if [[ "$COMPACT_MODE" == "true" ]]; then
  local_detail="${DONE}/${TOTAL} done"
  if [[ "$WORKING" -gt 0 ]]; then
    local_detail="${local_detail}, ${WORKING} working"
  fi
  if [[ "$BLOCKED" -gt 0 ]]; then
    local_detail="${local_detail}, ${BLOCKED} blocked"
  fi
  printf '%sProgress:%s %s%s%s %d%%  (%s)\n' \
    "$C_LABL" "$RESET" \
    "$C_DONE" "$BAR" "$RESET" \
    "$PCT" "$local_detail"
  exit 0
fi

# --- Render summary ---
printf '%sTotal:%s %d tickets\n' "$C_LABL" "$RESET" "$TOTAL"
printf '%sDone:%s    %-4d  %s%s%s  %d%%\n' \
  "$C_LABL" "$RESET" \
  "$DONE" \
  "$C_DONE" "$BAR" "$RESET" \
  "$PCT"
# Pair Working/Blocked on one line, Waiting/Backlog on one line
printf '%sWorking:%s %-4d  %sBlocked:%s %d\n' \
  "$C_WORK" "$RESET" "$WORKING" \
  "$C_BLCK" "$RESET" "$BLOCKED"
printf 'Waiting: %-4d  Backlog: %d\n' "$WAITING" "$BACKLOG"

# --- Last 5 done ---
echo ""
printf '%sLast 5 done:%s\n' "$C_LABL" "$RESET"

HAS_DONE=false
for i in 1 2 3 4 5; do
  task_id="$(get_val "LAST_DONE_${i}" "")"
  task_role="$(get_val "LAST_DONE_ROLE_${i}" "")"
  if [[ -z "$task_id" ]]; then
    break
  fi
  HAS_DONE=true

  # Abbreviate task ID: strip leading prefix fields, keep the slug.
  short_id="$task_id"
  # Try to extract slug: everything after the 3rd hyphen-group
  slug="$(echo "$task_id" | sed 's/^[^-]*-[^-]*-[^-]*-[^-]*-//')"
  [[ -n "$slug" ]] && short_id="$slug"

  if [[ "$USE_COLOR" == "true" ]]; then
    CHECKMARK=$'\xe2\x9c\x93'   # UTF-8: U+2713 CHECK MARK
  else
    CHECKMARK="+"
  fi

  printf '%s%s%s %-25s %s(%s)%s\n' \
    "$C_DONE" "$CHECKMARK" "$RESET" \
    "$short_id" \
    "$C_DIM" "$task_role" "$RESET"
done

if [[ "$HAS_DONE" == "false" ]]; then
  printf '%s(none yet)%s\n' "$C_DIM" "$RESET"
fi

# --- Last 5 shipped (tagged releases) ---
echo ""
printf '%sLast 5 shipped:%s\n' "$C_LABL" "$RESET"

HAS_SHIPPED=false
for i in 1 2 3 4 5; do
  shipped_tag="$(get_val "LAST_SHIPPED_${i}" "")"
  shipped_ago="$(get_val "LAST_SHIPPED_AGO_${i}" "")"
  if [[ -z "$shipped_tag" ]]; then
    break
  fi
  HAS_SHIPPED=true

  if [[ -n "$shipped_ago" ]]; then
    printf '  %s%-14s%s %s%s%s\n' \
      "$C_DONE" "$shipped_tag" "$RESET" \
      "$C_DIM" "$shipped_ago" "$RESET"
  else
    printf '  %s%s%s\n' "$C_DONE" "$shipped_tag" "$RESET"
  fi
done

if [[ "$HAS_SHIPPED" == "false" ]]; then
  printf '%s(no tags yet)%s\n' "$C_DIM" "$RESET"
fi
