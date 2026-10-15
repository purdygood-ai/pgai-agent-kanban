#!/usr/bin/env bash
# kill.sh
# Kill (destroy) the pgai-kanban tmux dashboard session.
#
# Idempotent: exits 0 if the session does not exist (nothing to kill).
#
# Removes only this session's own dashboard state -- its armed-pane folder
# and its panes' cadence-loop registry entries -- so another dashboard
# session on the same tmux server keeps working. The shared dashboard temp
# folder is removed, and tmux's own prefix+p (previous-window) restored,
# only once no dashboard session remains.
#
# Usage:
#   kill.sh [--kanban-root <path>] [--session <name>] [-h|--help]
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
# Source the activation-hook lifecycle for dashboard_remove_activation_hook
# (mirrors create.sh's install call — see lib/activation_hook.sh).
# shellcheck source=lib/activation_hook.sh
source "${_SCRIPT_DIR}/lib/activation_hook.sh"
# And the schedule window's layout hooks (lib/schedule_layout.sh).
# shellcheck source=lib/schedule_layout.sh
source "${_SCRIPT_DIR}/lib/schedule_layout.sh"
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
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:18}"
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

# ---------------------------------------------------------------------------
# Remove the session-scoped activation hook (mirrors create.sh's install —
# see lib/activation_hook.sh). `tmux kill-session` below would also destroy
# any session-scoped hook along with the session object it belongs to, but
# this explicit removal makes it an action this script owns and verifies
# rather than an incidental side effect, and keeps the step correct even if
# a future teardown path is added that does not go through kill-session.
# dashboard_remove_activation_hook already swallows a missing-hook/missing-
# session failure internally, so this call is idempotent on every exit path
# kill.sh owns.
# ---------------------------------------------------------------------------
dashboard_remove_schedule_layout_hook "$SESSION_NAME"
dashboard_remove_activation_hook "$SESSION_NAME"

# Pane ids (%N) are unique across the server, so the cadence-loop registry
# entries keyed by this session's pane ids are this session's alone. Read
# them before the session (and its panes) are gone.
_KILLED_PANE_IDS=()
mapfile -t _KILLED_PANE_IDS < <(tmux list-panes -s -t "$SESSION_NAME" -F '#{pane_id}' 2>/dev/null || true)

tmux kill-session -t "$SESSION_NAME"
echo "Killed session '${SESSION_NAME}'."

# ---------------------------------------------------------------------------
# _kill_remaining_dashboards
# Print each live tmux session that is still a dashboard: marked
# @pgai_dashboard by create.sh, or holding an armed-pane folder under the
# dashboard temp folder (a dashboard created before the marker existed).
# ---------------------------------------------------------------------------
_kill_remaining_dashboards() {
  local armed_root="$1" name marker
  while IFS=$'\t' read -r name marker; do
    [[ -n "$name" ]] || continue
    if [[ -n "$marker" && "$marker" != "0" ]] || [[ -n "$armed_root" && -d "${armed_root}/${name}" ]]; then
      printf '%s\n' "$name"
    fi
  done < <(tmux list-sessions -F '#{session_name}	#{@pgai_dashboard}' 2>/dev/null || true)
}

# ---------------------------------------------------------------------------
# Remove this session's dashboard temp scratch (deliberate-exit path only):
# its armed-pane folder and its panes' loop-registry entries. The whole
# dashboard folder -- snapshots, logs, every session's state -- goes only
# when no dashboard session remains, and then tmux's own prefix+p
# (previous-window) is restored in place of create.sh's conditional
# binding. Uses the scoped pgai_temp_cleanup helper — never removes the
# temp root or other projects' subdirs. If temp.sh was not sourced above,
# skip silently.
# ---------------------------------------------------------------------------
if declare -f pgai_temp_cleanup >/dev/null 2>&1 && \
   declare -f pgai_temp_subdir  >/dev/null 2>&1; then
    # The same root create.sh's armed folder was resolved under
    # (--kanban-root), for the folder and for pgai_temp_cleanup's guard.
    export PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT"
    _DASH_DIR="$(pgai_temp_subdir dashboard 2>/dev/null || true)"
    if [[ -n "$_DASH_DIR" ]]; then
        pgai_temp_cleanup "${_DASH_DIR}/armed/${SESSION_NAME}" 2>/dev/null || true
        for _pane_id in "${_KILLED_PANE_IDS[@]}"; do
            [[ "$_pane_id" =~ ^%[0-9]+$ ]] && rm -f "${_DASH_DIR}/loops/${_pane_id}" 2>/dev/null
        done
        if [[ -z "$(_kill_remaining_dashboards "${_DASH_DIR}/armed")" ]]; then
            pgai_temp_cleanup "$_DASH_DIR" 2>/dev/null || true
            tmux bind-key -T prefix p previous-window 2>/dev/null || true
        fi
    fi
fi
