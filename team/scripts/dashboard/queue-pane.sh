#!/usr/bin/env bash
# queue-pane.sh
# Pretty-prints the content and recent task statuses for a single agent queue.
#
# Designed to run under `watch -n 5 -- queue-pane.sh <queue_name> --project <name>`.
#
# Output includes:
#   - Queue name header with counts (done / working / waiting / blocked / pending)
#   - Per-entry listing with status badges from status.md
#   - WAITING-eligibility hints (lists prerequisite tasks not yet done)
#   - Missing-folder warnings for queue entries without a task directory
#
# Entries are queue lines "- [M] TASK-ID ..." whose ID has the canonical
# task-ID shape (kanban_task_id_pattern, lib/task_ids.sh): current
# role-prefixed IDs and legacy CLAUDE- prefixed IDs. The renderer is
# pgai_agent_kanban.dashboard.named_queue_pane.
#
# Usage:
#   queue-pane.sh <queue_name> [--project <name>] [--kanban-root <path>]
#
# Arguments:
#   queue_name   One of: pm, coder, writer, tester, cm
#                (also accepts: bug, priority — for the auxiliary backlogs)
#
# Options:
#   --project <name>       The project whose queue is rendered (default:
#                          $PGAI_PROJECT_NAME), resolved against projects.cfg
#                          by exact name or unambiguous prefix. With no project,
#                          or one that does not resolve, the pane prints
#                          "<queue_name>: empty (queue file not found)" and
#                          exits 0; it never picks a registered project itself.
#   --kanban-root <path>   Override the kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH
#                          or ~/pgai_agent_kanban)
#   -h, --help             Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root directory
#   PGAI_PROJECT_NAME            — project used when --project is not given
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# pp_run_ops: runs the renderer module from this tree's own pgai_agent_kanban
# package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"
# kanban_task_id_pattern: the task-ID shape the renderer matches entries with.
# shellcheck source=../lib/task_ids.sh
source "${SCRIPT_DIR}/../lib/task_ids.sh"

# ---------------------------------------------------------------------------
# Valid queue names — map to their backlog filename stem
# ---------------------------------------------------------------------------
declare -A QUEUE_FILE_MAP
QUEUE_FILE_MAP["pm"]="pm_backlog"
QUEUE_FILE_MAP["coder"]="coder_backlog"
QUEUE_FILE_MAP["writer"]="writer_backlog"
QUEUE_FILE_MAP["tester"]="tester_backlog"
QUEUE_FILE_MAP["cm"]="cm_backlog"
QUEUE_FILE_MAP["bug"]="bug_backlog"
QUEUE_FILE_MAP["priority"]="priority_backlog"

VALID_QUEUES="pm coder writer tester cm bug priority"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
QUEUE_NAME=""
PROJECT_ARG=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  case "$arg" in
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    --kanban-root)
      _next=$(( _i + 1 ))
      KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
      _i=$_next
      ;;
    --project)
      _next=$(( _i + 1 ))
      PROJECT_ARG="${_args[$_next]:-}"
      _i=$_next
      ;;
    -*)
      echo "ERROR: Unknown option: $arg" >&2
      echo "Usage: $0 <queue_name> [--project <name>] [--kanban-root <path>]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$QUEUE_NAME" ]]; then
        QUEUE_NAME="$arg"
      else
        echo "ERROR: Unexpected extra argument: $arg" >&2
        echo "Usage: $0 <queue_name> [--project <name>] [--kanban-root <path>]" >&2
        exit 1
      fi
      ;;
  esac
  _i=$(( _i + 1 ))
done

# Queue name is required
if [[ -z "$QUEUE_NAME" ]]; then
  echo "ERROR: queue_name is required." >&2
  echo "Usage: $0 <queue_name> [--project <name>] [--kanban-root <path>]" >&2
  echo "Valid queues: ${VALID_QUEUES}" >&2
  exit 1
fi

# Reject unknown queue names
if [[ -z "${QUEUE_FILE_MAP[$QUEUE_NAME]:-}" ]]; then
  echo "ERROR: Unknown queue name: '${QUEUE_NAME}'" >&2
  echo "Valid queues: ${VALID_QUEUES}" >&2
  exit 1
fi

# Operator user config, then [chain] pm_mode (config_loader.sh).
# shellcheck source=../lib/config_loader.sh
source "${SCRIPT_DIR}/../lib/config_loader.sh"
config_startup_prelude "$KANBAN_ROOT"
# ---------------------------------------------------------------------------
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_done green)}"
    export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_working yellow)}"
    export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_blocked red)}"
    export PGAI_DASHBOARD_COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_waiting yellow)}"
    export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_label cyan)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]]; then
  USE_COLOR=false
fi

COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"
COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-red}"
COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-yellow}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"

# ansi_code() is defined in lib/script_common.sh (sourced above).
RESET="$(ansi_code reset)"
C_DONE="$(ansi_code "$COLOR_DONE")"
C_WORK="$(ansi_code "$COLOR_WORKING")"
C_BLCK="$(ansi_code "$COLOR_BLOCKED")"
C_WAIT="$(ansi_code "$COLOR_WAITING")"
C_LABL="$(ansi_code "$COLOR_LABEL")"
C_DIM="$(ansi_code dim)"
C_BOLD="$(ansi_code bold)"
C_WARN="$(ansi_code yellow)"

# ---------------------------------------------------------------------------
# Resolve paths
# The pane renders one project's queue: the --project value, else
# PGAI_PROJECT_NAME, resolved against projects.cfg (pp_resolve_project_name).
# With neither, or a name that does not resolve, TASKS_DIR/QUEUE_FILE stay
# empty and the missing-queue-file handler below renders "(queue file not
# found)" rather than substituting the first-registered project.
# ---------------------------------------------------------------------------
TASKS_DIR=""
QUEUE_FILE=""
if PROJECT_NAME="$(pp_require_project_context "$PROJECT_ARG" 2>/dev/null)" \
   && PROJECT_NAME="$(pp_resolve_project_name "$PROJECT_NAME")" \
   && _qp_tasks_dir="$(pp_tasks_dir "$PROJECT_NAME")" \
   && _qp_queue_file="$(pp_queue_path "$PROJECT_NAME" "$QUEUE_NAME")"; then
  TASKS_DIR="$_qp_tasks_dir"
  QUEUE_FILE="$_qp_queue_file"
fi

# ---------------------------------------------------------------------------
# Handle missing queue file
# ---------------------------------------------------------------------------
if [[ ! -f "$QUEUE_FILE" ]]; then
  printf '%s%s%s%s%s\n' \
    "$C_LABL" "$C_BOLD" "=== ${QUEUE_NAME} ===" "$RESET" ""
  printf '%s%s: empty (queue file not found)%s\n' "$C_DIM" "$QUEUE_NAME" "$RESET"
  exit 0
fi

# ---------------------------------------------------------------------------
# Render the queue (pgai_agent_kanban.dashboard.named_queue_pane)
# ---------------------------------------------------------------------------
pp_run_ops pgai_agent_kanban.dashboard.named_queue_pane \
  "$QUEUE_FILE" "$TASKS_DIR" "$QUEUE_NAME" \
  --task-id-pattern "$(kanban_task_id_pattern)" \
  --colors "$C_LABL" "$C_BOLD" "$C_DONE" "$C_WORK" "$C_BLCK" \
           "$C_WAIT" "$C_DIM" "$C_WARN" "$RESET"
