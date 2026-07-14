#!/usr/bin/env bash
# dashboard-kill.sh
# Kill (destroy) the pgai-kanban tmux dashboard session.
#
# Idempotent: exits 0 if the session does not exist (nothing to kill).
#
# Usage:
#   dashboard-kill.sh [--kanban-root <path>] [--session <name>] [-h|--help]
#
# Flags:
#   --kanban-root     Override the kanban root path (used to source config.cfg)
#   --session         Override the tmux session name (default: pgai-kanban-dashboard)
#   -h, --help        Show this help and exit

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# Source project_paths lib for pp_* helpers
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# Source temp.sh for pgai_temp_cleanup / pgai_temp_subdir.
# Sourced silently — kill.sh continues normally if the lib is unavailable.
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/../lib/temp.sh" 2>/dev/null || true
unset _SCRIPT_DIR

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
SESSION_NAME="pgai-kanban-dashboard"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --session)
      SESSION_NAME="${2:-$SESSION_NAME}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -20
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--session <name>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard session_name pgai-kanban-dashboard)}"
fi

# Allow session name override from kanban.cfg
SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-$SESSION_NAME}"

# ---------------------------------------------------------------------------
# Verify tmux is available
# ---------------------------------------------------------------------------
if ! command -v tmux &>/dev/null; then
  echo "ERROR: tmux is not installed or not in PATH." >&2
  echo "  On RHEL/Rocky Linux: sudo dnf install -y tmux" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Kill the session (idempotent)
# ---------------------------------------------------------------------------
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Session '$SESSION_NAME' does not exist — nothing to kill."
  exit 0
fi

tmux kill-session -t "$SESSION_NAME"
echo "Killed session '${SESSION_NAME}'."

# ---------------------------------------------------------------------------
# Remove dashboard temp scratch (deliberate-exit path only).
# Uses the scoped pgai_temp_cleanup helper — never removes the temp root or
# other projects' subdirs.  If temp.sh was not sourced above, skip silently.
# ---------------------------------------------------------------------------
if declare -f pgai_temp_cleanup >/dev/null 2>&1 && \
   declare -f pgai_temp_subdir  >/dev/null 2>&1; then
    pgai_temp_cleanup "$(pgai_temp_subdir dashboard)" 2>/dev/null || true
fi
