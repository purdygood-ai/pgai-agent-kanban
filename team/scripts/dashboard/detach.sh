#!/usr/bin/env bash
# detach.sh
# Detach all clients from the pgai-kanban tmux dashboard session.
#
# Idempotent: exits 0 if the session does not exist (nothing to detach).
#
# Usage:
#   detach.sh [--kanban-root <path>] [--session <name>] [-h|--help]
#
# Flags:
#   --kanban-root     Override the kanban root path (used to read kanban.cfg)
#   --session         Override the tmux session name. Resolved as --session,
#                     then PGAI_DASHBOARD_SESSION_NAME, then kanban.cfg
#                     [dashboard] session_name, then pgai-kanban-dashboard
#   -h, --help        Show this help and exit

set -euo pipefail
# -h/--help prints the header comment above and exits 0 before anything
# else runs: no kanban root is resolved and no argument is read as data.
# shellcheck source=../lib/header_help.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/header_help.sh"
header_help_exit_if_requested "${BASH_SOURCE[0]}" "$@"
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/env_bootstrap.sh"

# Source project_paths lib for pp_* helpers
_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/../lib/dev_tree.sh"
# The one session-name resolution create, attach, detach and kill share.
# shellcheck source=lib/session_name.sh
source "${_SCRIPT_DIR}/lib/session_name.sh"
unset _SCRIPT_DIR

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
# The --session value; the session name is resolved after argument parsing
# (dashboard_resolve_session_name, below).
SESSION_FLAG=""

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
      SESSION_FLAG="${2:-$SESSION_FLAG}"
      shift 2
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--session <name>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Operator user config, then [chain] pm_mode (config_loader.sh).
# ---------------------------------------------------------------------------
# shellcheck source=../lib/config_loader.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/config_loader.sh"
config_startup_prelude "$KANBAN_ROOT"
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# Session name: --session, then PGAI_DASHBOARD_SESSION_NAME, then kanban.cfg
# [dashboard] session_name, then the default (lib/session_name.sh).
SESSION_NAME="$(dashboard_resolve_session_name "$SESSION_FLAG" "$KANBAN_ROOT")"
unset SESSION_FLAG

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
