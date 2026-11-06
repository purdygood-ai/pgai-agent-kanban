#!/usr/bin/env bash
# show-status-window.sh
# Renders the full Window 0 "Status" pane in one screen.
#
# Layout:
#   ┌─────────────────────────────────────────────────────────────────────────┐
#   │ pgai-kanban v0.15.5    RC: v0.15.5 (15m active)    Last Released: v0.15.4│
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │ HALT: ✓ off                                          Next cron firings:  │
#   │ Project: pgai_agent_kanban   pm:     in 6 min                    │
#   │ Foundation patch — dashboard rework coder:  in 1 min                    │
#   │ Progress: ▓▓▓▓▓▓▓░░░░░░░░ 45%      writer: in 14 min                   │
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │ QUEUE STATUS                                                             │
#   │ pm:     1/1 done                                                         │
#   │ coder:  3/5 done, 1 working   ▓▓▓▓▓▓░░░░ 60%                           │
#   │ writer: 0/0                                                              │
#   │ tester: 0/1 waiting                                                      │
#   │ cm:     1/2 done, waiting                                                │
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │ ⚠ ATTENTION  (shown only when BLOCKED tasks exist)                       │
#   │   CLAUDE-CM-YYYYMMDD-NNN-release  Blocked 2m ago                         │
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │ — currently working —                                                    │
#   │   CLAUDE-CODER-...-005-task    started: 14:23:14    elapsed: 3m 47s     │
#   └─────────────────────────────────────────────────────────────────────────┘
#
# Usage:
#   show-status-window.sh [--kanban-root <path>] [-h|--help]
#
# Prints one snapshot and exits; refreshing it is the caller's job.
#
# Environment:
#   NO_COLOR=1 — disables all ANSI codes
#   TERM=dumb  — disables all ANSI codes
#   PGAI_CRONTAB_CMD — command run as "<cmd> -l" to read the crontab;
#                      unset or empty means 'crontab'

# -h/--help prints the header comment above and exits 0 before anything
# else runs: no kanban root is resolved and no argument is read as data.
# shellcheck source=../lib/header_help.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/header_help.sh"
header_help_exit_if_requested "${BASH_SOURCE[0]}" "$@"

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

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
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# pp_run_ops: the cron-firing flattener and the ATTENTION renderer run from
# this tree's own pgai_agent_kanban package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# --- Pass through args ---
DATA_ARGS=("$@")

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  if [[ "${_args[$_i]}" == "--kanban-root" ]]; then
    _next=$(( _i + 1 ))
    KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
    break
  fi
  _i=$(( _i + 1 ))
done
# Operator user config, then [chain] pm_mode (config_loader.sh).
# shellcheck source=../lib/config_loader.sh
source "${SCRIPT_DIR}/../lib/config_loader.sh"
config_startup_prelude "$KANBAN_ROOT"
# Source: kanban.cfg [paths/dashboard] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    DASHBOARD_ROWS_PER_COLUMN="$(config_dashboard_rows_per_column "${KANBAN_ROOT}/kanban.cfg")"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

# ansi_code() is defined in lib/script_common.sh (sourced above).
RESET="$(ansi_code reset)"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"
C_CYAN="$(ansi_code cyan)"
C_GREEN="$(ansi_code green)"
C_YELLOW="$(ansi_code yellow)"
C_RED="$(ansi_code red)"
C_WHITE="$(ansi_code white)"

# --- Collect data ---
DATA="$("$SCRIPT_DIR/data.sh" "${DATA_ARGS[@]}")"

# get_val reads one KEY=VALUE pair from DATA (lib/dashboard_common.sh).
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"

KANBAN_ROOT_VAL="$(get_val KANBAN_ROOT "${PGAI_AGENT_KANBAN_ROOT_PATH}")"
ACTIVE_RC="$(get_val ACTIVE_RC "none")"
LAST_RELEASED="$(get_val LAST_RELEASED "none")"
HALT_FLAG="$(get_val HALT_FLAG "no")"
TOTAL="$(get_val TOTAL_TICKETS 0)"
DONE_COUNT="$(get_val DONE_COUNT 0)"
WORKING_COUNT="$(get_val WORKING_COUNT 0)"
BLOCKED_COUNT="$(get_val BLOCKED_COUNT 0)"
WORKING_ID="$(get_val WORKING_TASK_ID "")"
WORKING_MTIME="$(get_val WORKING_TASK_MTIME "")"

# --- Version resolution via shared helper ---
# Tier order: KANBAN_ROOT/VERSION > REPO_ROOT/VERSION > git tag --merged > Last Released > 'unknown'
# Single decision point: get_kanban_version in lib/version.sh (do not add tier-order logic here).
# The checkout this script runs from (resolve_script_repo_root, lib/dev_tree.sh).
REPO_ROOT="$(resolve_script_repo_root "$SCRIPT_DIR" "$SCRIPT_DIR/../../..")"
KANBAN_VERSION="$(get_kanban_version "$KANBAN_ROOT_VAL" "$REPO_ROOT" "${LAST_RELEASED:-}")"

# --- RC opened-time ---
# RC Opened At comes from data.sh (RC_OPENED_AT), which reads it with Active
# RC through the one release-state reader; empty when it is not recorded.
RC_AGE_STR=""
RC_OPENED_AT="$(get_val RC_OPENED_AT "")"
if [[ -n "$RC_OPENED_AT" ]]; then
  RC_EPOCH="$(date -d "$RC_OPENED_AT" +%s 2>/dev/null || echo "")"
  if [[ -n "$RC_EPOCH" ]]; then
    NOW_EPOCH="$(date +%s)"
    RC_ELAPSED=$(( NOW_EPOCH - RC_EPOCH ))
    if [[ "$RC_ELAPSED" -lt 3600 ]]; then
      RC_AGE_STR="$(( RC_ELAPSED / 60 ))m active"
    elif [[ "$RC_ELAPSED" -lt 86400 ]]; then
      RC_AGE_STR="$(( RC_ELAPSED / 3600 ))h $(( (RC_ELAPSED % 3600) / 60 ))m active"
    else
      RC_AGE_STR="$(( RC_ELAPSED / 86400 ))d active"
    fi
  fi
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
declare -A CRON_FIRINGS
CRON_FIRINGS_AVAILABLE=false
CRONTAB_TEXT="$("${PGAI_CRONTAB_CMD:-crontab}" -l 2>/dev/null || true)"
if [[ -n "$CRONTAB_TEXT" ]]; then
  # cron_parser.py is resolved from the live-install anchor only.
  # The dev tree is DATA to the live runtime; executing python code under
  # PGAI_DEV_TREE_PATH is prohibited for live-runtime scripts.
  CRON_PARSER="${KANBAN_ROOT}/pm-agent/lib/cron_parser.py"
  if [[ -f "$CRON_PARSER" ]]; then
    # Resolve cron_firings.py via shared helper resolver (live-install anchor first — D3 fix).
    CRON_FIRINGS_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/cron_firings.py")"
    CRON_JSON="$(python3 "$CRON_FIRINGS_PY" "$CRONTAB_TEXT" "$CRON_PARSER" 2>/dev/null || true)"
    if [[ -n "$CRON_JSON" ]]; then
      CRON_FIRINGS_AVAILABLE=true
      # Flatten the JSON object into the associative array: cron_firings
      # --key-value prints one agent=label line per entry.
      while IFS='=' read -r key val; do
        [[ -n "$key" ]] && CRON_FIRINGS["$key"]="$val"
      done < <(pp_run_ops pgai_agent_kanban.dashboard.cron_firings --key-value "$CRON_JSON" 2>/dev/null || true)
    fi
  fi
fi

# --- HALT indicator ---
if [[ "$HALT_FLAG" == "yes" ]]; then
  if [[ "$USE_COLOR" == "true" ]]; then
    HALT_STR="${C_RED}${C_BOLD}⚠ HALT: ON${RESET}"
  else
    HALT_STR="HALT: ON"
  fi
else
  if [[ "$USE_COLOR" == "true" ]]; then
    HALT_STR="${C_GREEN}✓ off${RESET}"
    HALT_LABEL="${C_CYAN}HALT:${RESET} "
  else
    HALT_STR="off"
    HALT_LABEL="HALT: "
  fi
  HALT_STR="${HALT_LABEL:-}${HALT_STR}"
fi

# --- Project name ---
RESOLVED_PROJECT="$(get_val PROJECT_NAME "")"
PROJECT_NAME="${RESOLVED_PROJECT:-$(basename "$KANBAN_ROOT_VAL")}"

# -------------------------------------------------------------------
# RENDER
# -------------------------------------------------------------------

# ── Row 1: version | RC | last released ──
if [[ -n "$RC_AGE_STR" ]] && [[ "$ACTIVE_RC" != "none" ]]; then
  RC_DISPLAY="${ACTIVE_RC} (${RC_AGE_STR})"
else
  RC_DISPLAY="$ACTIVE_RC"
fi

printf '%s%s%s    RC: %s    Last Released: %s\n' \
  "$C_BOLD" "${PROJECT_NAME} ${KANBAN_VERSION}" "$RESET" \
  "$RC_DISPLAY" \
  "$LAST_RELEASED"

echo ""

# ── Section: HALT + project info + progress (left) | cron firings (right) ──
# We'll render left column lines and right column lines, then merge

LEFT_LINES=()
LEFT_LINES+=("${HALT_STR}")
LEFT_LINES+=("Project: ${PROJECT_NAME}")

# Progress line
PROG_DETAIL="${DONE_COUNT}/${TOTAL} done"
[[ "$WORKING_COUNT" -gt 0 ]] && PROG_DETAIL="${PROG_DETAIL}, ${WORKING_COUNT} working"
[[ "$BLOCKED_COUNT" -gt 0 ]] && PROG_DETAIL="${PROG_DETAIL}, ${BLOCKED_COUNT} blocked"
LEFT_LINES+=("Progress: ${C_GREEN}${BAR}${RESET} ${PCT}%  (${PROG_DETAIL})")

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
  [[ ${#RIGHT_LINES[@]} -eq 1 ]] && RIGHT_LINES+=("  (no kanban cron entries found)")
else
  RIGHT_LINES+=("${C_DIM}Next cron firings:${RESET}")
  RIGHT_LINES+=("  (crontab not parseable)")
fi

# Print left/right side by side (left col ~45 chars, right fills remainder)
LEFT_WIDTH=48
MAX_ROWS=$(( ${#LEFT_LINES[@]} > ${#RIGHT_LINES[@]} ? ${#LEFT_LINES[@]} : ${#RIGHT_LINES[@]} ))
for (( row=0; row<MAX_ROWS; row++ )); do
  left_text="${LEFT_LINES[$row]:-}"
  right_text="${RIGHT_LINES[$row]:-}"
  # Print left padded (strip ANSI for width calculation is complex; use printf -v)
  printf '%-*s  %s\n' "$LEFT_WIDTH" "$left_text" "$right_text"
done

echo ""

# ── Section: QUEUE STATUS ──
printf '%sQUEUE STATUS%s\n' "$C_CYAN" "$RESET"
echo ""

render_queue_row() {
  local label="$1"
  local role="$2"
  local total done_q working blocked waiting
  total="$(get_val "QUEUE_${role}_TOTAL" 0)"
  done_q="$(get_val "QUEUE_${role}_DONE" 0)"
  working="$(get_val "QUEUE_${role}_WORKING" 0)"
  blocked="$(get_val "QUEUE_${role}_BLOCKED" 0)"
  waiting="$(get_val "QUEUE_${role}_WAITING" 0)"

  local annot=""
  [[ "$working" -gt 0 ]] && annot="${C_YELLOW}${working} working${RESET}"
  if [[ "$blocked" -gt 0 ]]; then
    [[ -n "$annot" ]] && annot="${annot}, "
    annot="${annot}${C_RED}${blocked} blocked${RESET}"
  fi
  if [[ "$waiting" -gt 0 ]] && [[ "$done_q" -lt "$total" ]]; then
    [[ -n "$annot" ]] && annot="${annot}, "
    annot="${annot}${C_YELLOW}waiting${RESET}"
  fi

  # Mini progress bar (10 wide)
  local mini_bar=""
  if [[ "$total" -gt 0 ]]; then
    local bw=10
    local bf=$(( done_q * bw / total ))
    local be=$(( bw - bf ))
    local pct_q=$(( done_q * 100 / total ))
    local fc ec bar_str=""
    if [[ "$USE_COLOR" == "true" ]]; then
      fc=$'\xe2\x96\x93'; ec=$'\xe2\x96\x91'
    else
      fc="#"; ec="."
    fi
    local j
    for (( j=0; j<bf; j++ )); do bar_str="${bar_str}${fc}"; done
    for (( j=0; j<be; j++ )); do bar_str="${bar_str}${ec}"; done
    mini_bar=" ${C_GREEN}${bar_str}${RESET} ${pct_q}%"
  fi

  local padded_label
  padded_label="$(printf '  %-8s' "${label}:")"

  if [[ "$total" -eq 0 ]]; then
    printf '%s%s%s%s0/0%s\n' "$C_CYAN" "$padded_label" "$RESET" "$C_DIM" "$RESET"
  elif [[ -n "$annot" ]]; then
    printf '%s%s%s%s%s/%s done%s, %s%s\n' \
      "$C_CYAN" "$padded_label" "$RESET" \
      "$C_GREEN" "$done_q" "$total" "$RESET" \
      "$annot" "$mini_bar"
  else
    printf '%s%s%s%s%s/%s done%s%s\n' \
      "$C_CYAN" "$padded_label" "$RESET" \
      "$C_GREEN" "$done_q" "$total" "$RESET" \
      "$mini_bar"
  fi
}

render_queue_row "pm"     "PM"
render_queue_row "coder"  "CODER"
render_queue_row "writer" "WRITER"
render_queue_row "tester" "TESTER"
render_queue_row "cm"     "CM"

echo ""

# ── Section: ATTENTION (only when BLOCKED tasks exist) ──
if [[ "$BLOCKED_COUNT" -gt 0 ]]; then
  if [[ "$USE_COLOR" == "true" ]]; then
    printf '%s%s⚠ ATTENTION%s\n' "$C_RED" "$C_BOLD" "$RESET"
  else
    printf '! ATTENTION\n'
  fi
  echo ""

  # List blocked tasks with brief reason.
  # Use RESOLVED_PROJECT (from data.sh) for the tasks dir; no default substitution.
  _ssw_tasks_dir=""
  if [[ -n "$RESOLVED_PROJECT" ]]; then
      _ssw_tasks_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$RESOLVED_PROJECT" 2>/dev/null || true)"
  fi
  # task_panes reads every ## section through pgai_agent_kanban.task_status
  # in one interpreter for the whole tasks directory.
  pp_run_ops pgai_agent_kanban.dashboard.task_panes status-window-blocked "${_ssw_tasks_dir}" \
    --reset "$RESET" --red "$C_RED" --dim "$C_DIM" --bold "$C_BOLD" 2>/dev/null || true

  echo ""
fi

# ── Section: Currently working ──
if [[ "$USE_COLOR" == "true" ]]; then
  printf '%s\342\200\224 currently working \342\200\224%s\n' "$C_DIM" "$RESET"
else
  echo "-- currently working --"
fi

if [[ -z "$WORKING_ID" ]]; then
  printf '  %s(none)%s\n' "$C_DIM" "$RESET"
else
  printf '  %s%s%s\n' "$C_YELLOW" "$WORKING_ID" "$RESET"

  if [[ -n "$WORKING_MTIME" ]] && [[ "$WORKING_MTIME" -gt 0 ]]; then
    STARTED_TIME="$(date -d "@${WORKING_MTIME}" '+%H:%M:%S' 2>/dev/null || \
                   date -r "$WORKING_MTIME" '+%H:%M:%S' 2>/dev/null || echo "unknown")"
    NOW_EPOCH="$(date +%s)"
    ELAPSED_SECS=$(( NOW_EPOCH - WORKING_MTIME ))

    if [[ "$ELAPSED_SECS" -lt 60 ]]; then
      ELAPSED_STR="${ELAPSED_SECS}s"
    elif [[ "$ELAPSED_SECS" -lt 3600 ]]; then
      E_MINS=$(( ELAPSED_SECS / 60 ))
      E_SECS=$(( ELAPSED_SECS % 60 ))
      ELAPSED_STR="${E_MINS}m ${E_SECS}s"
    else
      E_HRS=$(( ELAPSED_SECS / 3600 ))
      E_MINS=$(( (ELAPSED_SECS % 3600) / 60 ))
      ELAPSED_STR="${E_HRS}h ${E_MINS}m"
    fi

    printf '  started: %s    elapsed: %s\n' "$STARTED_TIME" "$ELAPSED_STR"
  fi
fi
