#!/usr/bin/env bash
# dashboard-cron-tail.sh
# Tails the cron log for a single cron-driven agent.
#
# Designed to run in a tmux pane inside the cron-logs window created by
# dashboard-create.sh.  Handles the missing-log-file case gracefully:
# prints a "no log yet" notice and polls until the file appears, then
# exec-replaces itself with tail -F.
#
# Usage:
#   dashboard-cron-tail.sh <agent> [--kanban-root <path>]
#
# Arguments:
#   agent        One of: pm, cm, coder, writer, tester
#
# Options:
#   --kanban-root <path>   Override the kanban root (default:
#                          $PGAI_AGENT_KANBAN_ROOT_PATH or
#                          ~/pgai_agent_kanban)
#   -h, --help             Show this help and exit
#
# Log path pattern:
#   $KANBAN_ROOT/logs/cron-<agent>.log
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root directory

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# ---------------------------------------------------------------------------
# Valid agent names
# ---------------------------------------------------------------------------
VALID_AGENTS="pm cm coder writer tester"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
AGENT_NAME=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  case "$arg" in
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -35
      exit 0
      ;;
    --kanban-root)
      _next=$(( _i + 1 ))
      KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
      _i=$_next
      ;;
    -*)
      echo "ERROR: Unknown option: $arg" >&2
      echo "Usage: $0 <agent> [--kanban-root <path>]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$AGENT_NAME" ]]; then
        AGENT_NAME="$arg"
      else
        echo "ERROR: Unexpected extra argument: $arg" >&2
        echo "Usage: $0 <agent> [--kanban-root <path>]" >&2
        exit 1
      fi
      ;;
  esac
  _i=$(( _i + 1 ))
done

# Agent name is required
if [[ -z "$AGENT_NAME" ]]; then
  echo "ERROR: agent argument is required." >&2
  echo "Usage: $0 <agent> [--kanban-root <path>]" >&2
  echo "Valid agents: ${VALID_AGENTS}" >&2
  exit 1
fi

# Reject unknown agent names
case "$AGENT_NAME" in
  pm|cm|coder|writer|tester)
    ;;
  *)
    echo "ERROR: Unknown agent: '${AGENT_NAME}'" >&2
    echo "Valid agents: ${VALID_AGENTS}" >&2
    exit 1
    ;;
esac

# ---------------------------------------------------------------------------
# Resolve log file path
# ---------------------------------------------------------------------------
# cron-<agent>.log files live at $KANBAN_ROOT/logs/ — that's where the
# cron-suggested.txt template redirects each wake-claude.sh firing's stdout.
LOG_FILE="${KANBAN_ROOT}/logs/cron-${AGENT_NAME}.log"

# ---------------------------------------------------------------------------
# Print agent header banner so pane identity is visible even before the log
# ---------------------------------------------------------------------------
printf '\033[1;36m=== cron log: %s ===\033[0m\n' "$AGENT_NAME"
printf '\033[2m%s\033[0m\n' "$LOG_FILE"

# ---------------------------------------------------------------------------
# Wait for the log file to appear, then exec tail -F
# ---------------------------------------------------------------------------
if [[ ! -f "$LOG_FILE" ]]; then
  printf '\033[33mno log yet — waiting for %s to appear...\033[0m\n' "$LOG_FILE"
  while [[ ! -f "$LOG_FILE" ]]; do
    sleep 5
  done
  printf '\033[32mlog file found — starting tail\033[0m\n'
fi

exec tail -F "$LOG_FILE"
