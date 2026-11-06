#!/usr/bin/env bash
# board-reflow.sh
# Re-lays out a dashboard board window for the size it has now. create.sh
# sets the operator and visibility windows' window-layout-changed hooks to
# run this script whenever the window changes size — a client attaching at
# another size, a terminal resize, a resize-window — so every board column
# gets the builder's width for the new size instead of one column taking
# the whole difference. On an operator window built with a workbench, the
# workbench column also leaves the window while the board beside it would
# be too narrow for its columns, and comes back when there is room again.
# The decision, the pane moves and the sizes are lib/board_window.sh's
# (dashboard_reflow_board_window; "RESIZE" in its header).
#
# Usage:
#   board-reflow.sh --window <window> [-h|--help]
#
# Flags:
#   --window      The tmux window to re-lay out: a window id such as @3, or
#                 <session>:<window>, e.g. pgai-kanban-dashboard:operator.
#                 Required.
#   -h, --help    Show this help and exit
#
# Prints nothing and exits 0 whenever --window is given, including when the
# window is gone, is not a board window, or has been rearranged by hand
# (each is left as it is): tmux shows a hook command's output in the
# attached client's pane. A board too narrow for its columns to keep 15
# columns each gets the degraded layout, and its NOTICE line is recorded in
# the window's @pgai_board_notice option rather than printed; the window's
# legend bar shows it (legend-render.sh) until a reflow clears it. An unknown
# argument or a missing --window exits 2 with an error on stderr, having
# moved and resized nothing.

# -h/--help prints the header comment above and exits 0 before anything
# else runs: no kanban root is resolved and no argument is read as data.
# shellcheck source=../lib/header_help.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/header_help.sh"
header_help_exit_if_requested "${BASH_SOURCE[0]}" "$@"

set -uo pipefail

_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/board_window.sh
source "${_SCRIPT_DIR}/lib/board_window.sh"

WINDOW=""
_usage_problem=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --window)
      WINDOW="${2:-}"
      shift
      [[ $# -gt 0 ]] && shift
      ;;
    *)
      _usage_problem="unknown argument '$1'"
      break
      ;;
  esac
done
if [[ -z "$_usage_problem" && -z "$WINDOW" ]]; then
  _usage_problem="no --window value given"
fi

if [[ -n "$_usage_problem" ]]; then
  # shellcheck source=../lib/error_contract.sh
  source "${_SCRIPT_DIR}/../lib/error_contract.sh"
  pgai_emit_error_contract "dashboard-board-reflow.usage" \
    "board-reflow.sh: ${_usage_problem}. It re-lays out exactly the one board window --window names (see --help), so nothing was moved or resized." \
    "${_SCRIPT_DIR}/board-reflow.sh --window pgai-kanban-dashboard:operator"
  # BLOCK-WORTHY: guessing a window would move the workbench pane in and
  # out of, and resize every pane of, a window nobody named; refuse before
  # any tmux call (team/SOP-core.md, "Gates and Refusals").
  # CENSUS-ANCHOR: dashboard-board-reflow-usage-01
  exit 2
fi

dashboard_reflow_board_window "$WINDOW"
exit 0
