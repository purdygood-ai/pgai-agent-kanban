#!/usr/bin/env bash
# dashboard-detach.sh
# Detach all clients from the pgai-kanban tmux dashboard session.
#
# Idempotent: exits 0 if the session does not exist (nothing to detach).
#
# Usage:
#   dashboard-detach.sh [--kanban-root <path>] [--session <name>] [-h|--help]
#
# Flags:
#   --kanban-root     Override the kanban root path (used to read kanban.cfg)
#   --session         Override the tmux session name (default: pgai-kanban-dashboard)
#   -h, --help        Show this help and exit

set -euo pipefail

# Source project_paths lib for pp_* helpers
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/../lib/dev_tree.sh"
unset _SCRIPT_DIR

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
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
    # Source: kanban.cfg [chain] key values
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# Allow session name override from env or kanban.cfg
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
# Detach all clients from the session (idempotent)
# ---------------------------------------------------------------------------
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "Session '$SESSION_NAME' does not exist — nothing to detach."
  exit 0
fi

# detach-client -s detaches ALL clients connected to the named session.
# It does not error if no clients are currently attached.
tmux detach-client -s "$SESSION_NAME" 2>/dev/null || true
echo "Detached all clients from session '${SESSION_NAME}'."
