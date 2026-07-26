#!/usr/bin/env bash
# kanban-status.sh
# One-shot terminal status view (no tmux required).
#
# Example output:
#   pgai-kanban v0.15.5    RC: v0.15.5 (15m)    Last: v0.15.4
#   HALT: off    Working: CODER-...-005-model-resolution (3m 47s)
#
#   Queue:
#     pm:     1/1 done
#     coder:  3/5 done, 1 working
#     writer: 0/0
#     tester: 0/1 waiting
#     cm:     1/2 done, waiting
#
#   Progress: [▓▓▓▓▓▓░░░░░░░░] 45%
#
#   (no blocked tasks)
#
# Usage:
#   kanban-status.sh --project <name> [--kanban-root <path>] [--no-color]
#
# Flags:
#   --project <name>      Project name (required when PGAI_PROJECT_NAME is not set)
#   --no-color            Disable ANSI color codes
#   --kanban-root <path>  Override kanban root path
#
# Environment:
#   PGAI_PROJECT_NAME    Project name (alternative to --project)
#   TERM=dumb            Disables all ANSI codes
#   NO_COLOR=1           Disables all ANSI codes

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/lib/dev_tree.sh"

# --- Parse args ---
NO_COLOR_FLAG=false
DATA_ARGS=()
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  if [[ "$arg" == "--no-color" ]]; then
    NO_COLOR=1
    export NO_COLOR
    NO_COLOR_FLAG=true
  elif [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    sed -n '2,33p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0
  else
    DATA_ARGS+=("$arg")
  fi
  _i=$(( _i + 1 ))
done

# --- Source config (non-strict) ---
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
# --kanban-root overrides the default for this run.
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
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): kanban-status reads task state from $KANBAN_ROOT only;
# no dev tree access required. Global require_dev_tree removed (D5).

set -euo pipefail

# --- ANSI color support ---
# Disable color if: --no-color flag, NO_COLOR env var (any non-empty value),
# or TERM=dumb
USE_COLOR=true
if [[ "$NO_COLOR_FLAG" == "true" ]]; then
  USE_COLOR=false
elif [[ -n "${NO_COLOR:-}" ]]; then
  USE_COLOR=false
elif [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"
COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-red}"
COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-yellow}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"
COLOR_HEADER="${PGAI_DASHBOARD_COLOR_HEADER:-cyan}"
COLOR_HALT_ON="${PGAI_DASHBOARD_COLOR_HALT:-red}"
COLOR_HALT_OFF="${PGAI_DASHBOARD_COLOR_OK:-green}"

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
C_DONE="$(ansi_code "$COLOR_DONE")"
C_WORK="$(ansi_code "$COLOR_WORKING")"
C_BLCK="$(ansi_code "$COLOR_BLOCKED")"
C_WAIT="$(ansi_code "$COLOR_WAITING")"
C_LABL="$(ansi_code "$COLOR_LABEL")"
C_HEAD="$(ansi_code "$COLOR_HEADER")"
C_HALT_ON="$(ansi_code "$COLOR_HALT_ON")"
C_HALT_OFF="$(ansi_code "$COLOR_HALT_OFF")"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"

# --- Collect data ---
DATA="$("$SCRIPT_DIR/dashboard/data.sh" "${DATA_ARGS[@]}")"

get_val() {
  local key="$1"
  local default="${2:-}"
  echo "$DATA" | awk -F= -v k="$key" 'NR==1{OFS="="} $1 == k { $1=""; sub(/^=/, ""); print; found=1 } END { if (!found) print "'"$default"'" }'
}

# --- Read values ---
KANBAN_VERSION="$(get_val KANBAN_VERSION "unknown")"
ACTIVE_RC="$(get_val ACTIVE_RC "none")"
LAST_RELEASED="$(get_val LAST_RELEASED "none")"
HALT_FLAG="$(get_val HALT_FLAG "no")"
TOTAL="$(get_val TOTAL_TICKETS 0)"
DONE_COUNT="$(get_val DONE_COUNT 0)"
WORKING_COUNT="$(get_val WORKING_COUNT 0)"
BLOCKED_COUNT="$(get_val BLOCKED_COUNT 0)"
WORKING_ID="$(get_val WORKING_TASK_ID "")"
WORKING_MTIME="$(get_val WORKING_TASK_MTIME "")"
# Resolved project name from data.sh (explicit --project arg or first in projects.cfg);
# data.sh already fails loudly when no project is resolvable, so by the time we reach
# here the project name is always set.  Use it directly for all project-scoped lookups.
_KS_PROJECT="$(get_val PROJECT_NAME "")"

# --- RC age ---
RC_AGE_STR=""
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Prefer project-scoped release-state.md (canonical location);
# fall back to legacy team/release-state.md only if project-scoped does not exist.
RELEASE_STATE_FILE=""
if [[ -n "$_KS_PROJECT" ]] && command -v pp_project_root >/dev/null 2>&1; then
  if [[ -f "$(pp_project_root "$_KS_PROJECT" 2>/dev/null)/release-state.md" ]]; then
    RELEASE_STATE_FILE="$(pp_project_root "$_KS_PROJECT")/release-state.md"
  fi
fi
[[ -z "$RELEASE_STATE_FILE" ]] && RELEASE_STATE_FILE="${REPO_ROOT}/team/release-state.md"
if [[ -f "$RELEASE_STATE_FILE" ]]; then
  RC_OPENED_AT="$(awk '/^## RC Opened At/{found=1;next} found && /^## /{exit} found && NF{print;exit}' "$RELEASE_STATE_FILE" | xargs 2>/dev/null || true)"
  if [[ -n "$RC_OPENED_AT" ]]; then
    RC_EPOCH="$(date -d "$RC_OPENED_AT" +%s 2>/dev/null || echo "")"
    if [[ -n "$RC_EPOCH" ]]; then
      NOW_EPOCH="$(date +%s)"
      RC_ELAPSED=$(( NOW_EPOCH - RC_EPOCH ))
      if [[ "$RC_ELAPSED" -lt 3600 ]]; then
        RC_AGE_STR="$(( RC_ELAPSED / 60 ))m"
      elif [[ "$RC_ELAPSED" -lt 86400 ]]; then
        RC_AGE_STR="$(( RC_ELAPSED / 3600 ))h $(( (RC_ELAPSED % 3600) / 60 ))m"
      else
        RC_AGE_STR="$(( RC_ELAPSED / 86400 ))d"
      fi
    fi
  fi
fi

# --- Row 1: Version header ---
if [[ -n "$RC_AGE_STR" ]] && [[ "$ACTIVE_RC" != "none" ]]; then
  RC_DISPLAY="${ACTIVE_RC} (${RC_AGE_STR})"
else
  RC_DISPLAY="$ACTIVE_RC"
fi

printf '%s%spgai-kanban %s%s    RC: %s    Last: %s\n' \
  "$C_BOLD" "$C_HEAD" \
  "$KANBAN_VERSION" \
  "$RESET" \
  "$RC_DISPLAY" \
  "$LAST_RELEASED"

# --- Row 2: HALT status + Working task ---
if [[ "$HALT_FLAG" == "yes" ]]; then
  HALT_DISP="${C_HALT_ON}HALT: on${RESET}"
else
  HALT_DISP="${C_HALT_OFF}HALT: off${RESET}"
fi

# Compute elapsed time for working task
WORKING_DISP=""
if [[ -n "$WORKING_ID" ]]; then
  ELAPSED_STR=""
  if [[ -n "$WORKING_MTIME" ]] && [[ "$WORKING_MTIME" -gt 0 ]]; then
    NOW_EPOCH="$(date +%s)"
    ELAPSED_SECS=$(( NOW_EPOCH - WORKING_MTIME ))
    if [[ "$ELAPSED_SECS" -lt 60 ]]; then
      ELAPSED_STR="${ELAPSED_SECS}s"
    elif [[ "$ELAPSED_SECS" -lt 3600 ]]; then
      MINS=$(( ELAPSED_SECS / 60 ))
      SECS=$(( ELAPSED_SECS % 60 ))
      ELAPSED_STR="${MINS}m ${SECS}s"
    else
      HRS=$(( ELAPSED_SECS / 3600 ))
      MINS=$(( (ELAPSED_SECS % 3600) / 60 ))
      ELAPSED_STR="${HRS}h ${MINS}m"
    fi
    WORKING_DISP="${C_WORK}${WORKING_ID}${RESET} (${ELAPSED_STR})"
  else
    WORKING_DISP="${C_WORK}${WORKING_ID}${RESET}"
  fi
else
  WORKING_DISP="${C_DIM}(none)${RESET}"
fi

printf '%s    Working: %s\n' "$HALT_DISP" "$WORKING_DISP"

# --- Queue section ---
echo ""
printf '%sQueue:%s\n' "$C_LABL" "$RESET"

render_queue_row() {
  local label="$1"
  local role="$2"

  local total done working blocked waiting
  total="$(get_val "QUEUE_${role}_TOTAL" 0)"
  done="$(get_val "QUEUE_${role}_DONE" 0)"
  working="$(get_val "QUEUE_${role}_WORKING" 0)"
  blocked="$(get_val "QUEUE_${role}_BLOCKED" 0)"
  waiting="$(get_val "QUEUE_${role}_WAITING" 0)"

  # Build annotation string
  local annot=""
  if [[ "$working" -gt 0 ]]; then
    annot="${C_WORK}${working} working${RESET}"
  fi
  if [[ "$blocked" -gt 0 ]]; then
    [[ -n "$annot" ]] && annot="${annot}, "
    annot="${annot}${C_BLCK}${blocked} blocked${RESET}"
  fi
  if [[ "$waiting" -gt 0 ]] && [[ "$done" -lt "$total" ]]; then
    [[ -n "$annot" ]] && annot="${annot}, "
    annot="${annot}${C_WAIT}waiting${RESET}"
  fi

  # Pad label to 8 chars
  local padded_label
  padded_label="$(printf '%-8s' "${label}:")"

  if [[ "$total" -eq 0 ]]; then
    printf '  %s%s%s%s0/0%s\n' "$C_LABL" "$padded_label" "$RESET" "$C_DIM" "$RESET"
  elif [[ -n "$annot" ]]; then
    printf '  %s%s%s%s%s/%s done%s, %s\n' \
      "$C_LABL" "$padded_label" "$RESET" \
      "$C_DONE" "$done" "$total" "$RESET" \
      "$annot"
  else
    printf '  %s%s%s%s%s/%s done%s\n' \
      "$C_LABL" "$padded_label" "$RESET" \
      "$C_DONE" "$done" "$total" "$RESET"
  fi
}

render_queue_row "pm"     "PM"
render_queue_row "coder"  "CODER"
render_queue_row "writer" "WRITER"
render_queue_row "tester" "TESTER"
render_queue_row "cm"     "CM"

# --- Progress bar ---
echo ""
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
  FILLED_CHAR=$'\xe2\x96\x93'   # UTF-8: U+2593 DARK SHADE
  EMPTY_CHAR=$'\xe2\x96\x91'    # UTF-8: U+2591 LIGHT SHADE
else
  FILLED_CHAR="#"
  EMPTY_CHAR="."
fi

BAR=""
for (( i=0; i<FILLED; i++ )); do BAR="${BAR}${FILLED_CHAR}"; done
for (( i=0; i<EMPTY; i++ ));  do BAR="${BAR}${EMPTY_CHAR}"; done

printf '%sProgress:%s [%s%s%s] %d%%\n' \
  "$C_LABL" "$RESET" \
  "$C_DONE" "$BAR" "$RESET" \
  "$PCT"

# --- Blocked tasks section ---
echo ""
if [[ "$BLOCKED_COUNT" -gt 0 ]]; then
  printf '%sBlocked (%d):%s\n' "$C_BLCK" "$BLOCKED_COUNT" "$RESET"
  # Scan task dirs for BLOCKED state — use project resolved by data.sh above.
  # When _KS_PROJECT is empty (should not happen; data.sh fails loudly first),
  # skip the blocked listing rather than guessing a project.
  TASKS_ROOT=""
  [[ -n "$_KS_PROJECT" ]] && TASKS_ROOT="$(pp_tasks_dir "$_KS_PROJECT" 2>/dev/null || true)"
  if [[ -d "$TASKS_ROOT" ]]; then
    while IFS= read -r -d '' status_file; do
      task_dir="$(dirname "$status_file")"
      task_id="$(basename "$task_dir")"
      # Skip archive/queues/plans
      case "$task_id" in
        archive|queues|plans) continue ;;
      esac
      state="$(awk '/^## State/{found=1;next} found && /^## /{exit} found && NF{print;exit}' "$status_file" 2>/dev/null | xargs 2>/dev/null || echo "")"
      if [[ "$state" == "BLOCKED" ]]; then
        printf '  %s%s%s\n' "$C_BLCK" "$task_id" "$RESET"
      fi
    done < <(find "$TASKS_ROOT" -maxdepth 2 -name "status.md" -print0 2>/dev/null | sort -z)
  fi
else
  printf '%s(no blocked tasks)%s\n' "$C_DIM" "$RESET"
fi

# --- Quarantined count ---
# Count non-sidecar files in the active project's rejected/ directory.
# Sidecar files end in .reason and are excluded from the count.
# Rule: Quarantined is always shown (even when 0) for a consistent, predictable
# summary line that operators can rely on regardless of current state.
_QUARANTINED_COUNT=0
# Use project resolved by data.sh; skip quarantine count when no project is available.
_REJECTED_DIR=""
[[ -n "$_KS_PROJECT" ]] && _REJECTED_DIR="$(pp_rejected_dir "$_KS_PROJECT" 2>/dev/null || true)"
if [[ -n "$_REJECTED_DIR" ]] && [[ -d "$_REJECTED_DIR" ]]; then
  _QUARANTINED_COUNT="$(find "$_REJECTED_DIR" -maxdepth 1 -type f ! -name '*.reason' | wc -l)"
  _QUARANTINED_COUNT="${_QUARANTINED_COUNT// /}"  # strip whitespace from wc -l output
fi
echo ""
printf '%sQuarantined:%s %s%d%s\n' "$C_LABL" "$RESET" "$C_DIM" "$_QUARANTINED_COUNT" "$RESET"

exit 0
