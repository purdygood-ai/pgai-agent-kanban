#!/usr/bin/env bash
# dashboard-visibility.sh
# Visibility window renderer implementing the middle-position-active algorithm.
#
# Renders a single agent queue column using the middle-position-active layout:
#   - Up to 10 items per queue, reverse-sorted by label/number (newest first).
#   - Active items (WORKING / BACKLOG / WAITING) appear in the center block.
#   - Open items (non-terminal, non-active) pad the slots above the active block.
#   - Closed items (DONE / WONT-DO / BLOCKED) fill the slots below.
#
# This script is a thin dispatch layer. The rendering algorithm lives in
# dashboard-column-render.sh (queue subcommand) so that both the tmux watch
# panes and --no-tmux mode share one implementation.
#
# Usage:
#   dashboard-visibility.sh queue <backlog_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#   dashboard-visibility.sh input <dir> <cache_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#
# Arguments (queue mode):
#   <backlog_file>  Absolute path to an agent backlog file (e.g. coder_backlog.md).
#   rows            Max rows to display (capped at 10 by the algorithm; default: 10).
#   width           Column width in visible characters (default: 22).
#
# Arguments (input mode):
#   <dir>           Absolute path to bugs/, priority/, or requirements/.
#   <cache_file>    Absolute path to the corresponding cache/backlog file.
#   rows            Max rows to display (default: 10).
#   width           Column width in visible characters (default: 38).
#
# Options:
#   --label NAME    Header label emitted as '=== NAME ==='.
#   --kanban-root   Override the kanban root path.
#   -h, --help      Show this help and exit.
#
# Middle-position-active window layout (queue mode, 10 slots):
#
#   ┌──────────────────────────────────────────┐
#   │  open items (above — newest first)        │  ← floor((10 - n_active) / 2) slots
#   │  active items (center — newest first)     │  ← min(n_active, 10) slots
#   │  closed items (below — newest first)      │  ← remainder slots
#   └──────────────────────────────────────────┘
#
# Edge cases:
#   all-active   All 10 slots used by active items; no open/closed padding.
#   all-done     All 10 slots used by closed items; active block is empty.
#   fewer-than-10  Only existing items are shown; no blank-line padding.
#   empty        Emits "(empty)"; exits 0.
#   exactly-10-mixed  Active block centered; open and closed fill remaining slots.
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   DASHBOARD_ROWS_PER_COLUMN           — global rows cap (read from config.cfg)
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors
#
# Legend:
#   The single-line color-convention legend rendered beneath the visibility
#   grid lives in team/scripts/lib/dashboard_legend.sh as the
#   DASHBOARD_LEGEND_TEMPLATE constant. See "Dashboard color conventions" in
#   team/SOP.md for the operator-facing explanation of project colors,
#   status colors, and the projects.cfg override flow.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Delegate entirely to dashboard-column-render.sh which holds the algorithm.
exec "${SCRIPT_DIR}/column-render.sh" "$@"
