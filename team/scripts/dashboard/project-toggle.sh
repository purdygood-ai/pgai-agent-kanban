#!/usr/bin/env bash
# dashboard-project-toggle.sh
# Cycles the tmux session through overview and per-project drill windows.
#
# Called by the tmux key binding registered in dashboard-create.sh:
#   bind-key p run-shell "dashboard-project-toggle.sh [--session <name>]"
#
# Cycle order:
#   overview (main window)  →  drill-1  →  drill-2  →  ...  →  overview
#
# Overview window name: "main"
# Drill window name pattern: "drill-<N>: <project-name>" where N is the 1-based
# project index. The toggle also handles the bare "drill-<N>" format (sessions
# without a project name suffix).
#
# When the current window is main (overview), this script switches to
# drill-1.  When already in a drill window, it advances to the next drill
# window, wrapping back to "main" after the last project.
#
# Works correctly with 1 project:
#   main  →  drill-1  →  main  (drill == overview, no error)
#
# Works correctly with 2+ projects:
#   main  →  drill-1  →  drill-2  →  ...  →  main
#
# Usage:
#   dashboard-project-toggle.sh [--session <name>]
#
# Options:
#   --session <name>   tmux session to operate on (default: pgai-kanban-dashboard)
#   -h, --help         Show this help and exit
#
# Environment:
#   PGAI_DASHBOARD_SESSION_NAME   — default session name override
#   TMUX                          — must be set (script must run inside tmux or
#                                   be invoked via run-shell)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-pgai-kanban-dashboard}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --session)
      SESSION_NAME="${2:-$SESSION_NAME}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -45
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--session <name>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Verify we can reach the tmux session
# ---------------------------------------------------------------------------
if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  echo "dashboard-project-toggle: session '$SESSION_NAME' not found." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Discover drill windows (windows named "drill-<N>" or "drill-<N>: <project>")
# ---------------------------------------------------------------------------
# tmux list-windows output: INDEX<TAB>NAME
# We collect drill-N window names sorted by window index (numeric).
# The drill window name format is "drill-N: <project-name>" — the awk
# filter matches any name starting with "drill-<digits>" to support both
# the old bare "drill-N" format and the new "drill-N: <project>" format.
mapfile -t DRILL_WINDOWS < <(
  tmux list-windows -t "$SESSION_NAME" -F "#{window_index}	#{window_name}" 2>/dev/null \
    | awk -F'\t' '$2 ~ /^drill-[0-9]+/ { print $2 }' \
    | sort -t- -k2 -n
)

# If no drill windows exist yet, nothing to toggle — exit cleanly.
if [[ ${#DRILL_WINDOWS[@]} -eq 0 ]]; then
  echo "dashboard-project-toggle: no drill-N windows found in session '$SESSION_NAME'." >&2
  exit 0
fi

# ---------------------------------------------------------------------------
# Determine current window
# ---------------------------------------------------------------------------
CURRENT_WINDOW="$(tmux display-message -t "$SESSION_NAME" -p '#{window_name}' 2>/dev/null || true)"

# ---------------------------------------------------------------------------
# Decide the target window
# ---------------------------------------------------------------------------
TARGET_WINDOW=""

if [[ "$CURRENT_WINDOW" == "main" ]]; then
  # In overview: go to the first drill window
  TARGET_WINDOW="${DRILL_WINDOWS[0]}"
else
  # Determine if current window is a drill window, and which index it is
  FOUND_IDX=-1
  for _i in "${!DRILL_WINDOWS[@]}"; do
    if [[ "${DRILL_WINDOWS[$_i]}" == "$CURRENT_WINDOW" ]]; then
      FOUND_IDX=$_i
      break
    fi
  done

  if [[ "$FOUND_IDX" -ge 0 ]]; then
    # In a drill window: advance to the next, wrapping to main
    NEXT_IDX=$(( FOUND_IDX + 1 ))
    if [[ "$NEXT_IDX" -lt "${#DRILL_WINDOWS[@]}" ]]; then
      TARGET_WINDOW="${DRILL_WINDOWS[$NEXT_IDX]}"
    else
      TARGET_WINDOW="main"
    fi
  else
    # In some other window (logs, shell, etc.): go to main
    TARGET_WINDOW="main"
  fi
fi

# ---------------------------------------------------------------------------
# Switch to the target window
# ---------------------------------------------------------------------------
tmux select-window -t "${SESSION_NAME}:${TARGET_WINDOW}"
