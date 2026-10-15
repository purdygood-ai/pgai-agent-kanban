#!/usr/bin/env bash
# cron-tail.sh
# Tails the cron log for a single cron-driven agent.
#
# Designed to run in a tmux pane inside the cron-logs window created by
# dashboard/create.sh.  Handles the missing-log-file case gracefully:
# prints a "no log yet" notice and polls until the file appears, then
# exec-replaces itself with tail -F.
#
# Usage:
#   cron-tail.sh <agent> [--kanban-root <path>]
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
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh). This pane
# has always colored unconditionally (no NO_COLOR/TERM=dumb gate) -- that
# behavior is preserved by setting USE_COLOR=true unconditionally below,
# matching ansi_code()'s own USE_COLOR-gated contract.
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
USE_COLOR=true

# ---------------------------------------------------------------------------
# Valid agent names
# ---------------------------------------------------------------------------
VALID_AGENTS="pm cm coder writer tester"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
AGENT_NAME=""
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
# cron-suggested.txt template redirects each wake/claude.sh firing's stdout.
LOG_FILE="${KANBAN_ROOT}/logs/cron-${AGENT_NAME}.log"

# ---------------------------------------------------------------------------
# Print agent header banner so pane identity is visible even before the log
# ---------------------------------------------------------------------------
printf '%s=== cron log: %s ===%s\n' "$(ansi_code bold_cyan)" "$AGENT_NAME" "$(ansi_code reset)"
printf '%s%s%s\n' "$(ansi_code dim)" "$LOG_FILE" "$(ansi_code reset)"

# ---------------------------------------------------------------------------
# Wait for the log file to appear, then exec tail -F
# ---------------------------------------------------------------------------
if [[ ! -f "$LOG_FILE" ]]; then
  printf '%sno log yet — waiting for %s to appear...%s\n' "$(ansi_code yellow_bright)" "$LOG_FILE" "$(ansi_code reset)"
  while [[ ! -f "$LOG_FILE" ]]; do
    sleep 5
  done
  printf '%slog file found — starting tail%s\n' "$(ansi_code green_bright)" "$(ansi_code reset)"
fi

exec tail -F "$LOG_FILE"
