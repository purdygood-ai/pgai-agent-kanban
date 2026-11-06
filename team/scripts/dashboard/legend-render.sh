#!/usr/bin/env bash
# legend-render.sh
# Renders the persistent one-line color legend for the unified 8-column
# visibility window (unified 8-column layout).
#
# Output shape:
#   PROJECT: <tag> <name>  ...   |   STATUS: open  running  done  wont-do  blocked  needs-operator  ↻ carried
#
# Each project tag is rendered in its display_color (truecolor ANSI when the
# terminal supports it; "[abbr]" fallback in NO_COLOR / TERM=dumb mode). The
# status keywords are rendered in their mapped status colors so the operator can
# read the convention directly from the legend without needing external reference.
# "carried" is shown as the marker carried rows carry ("↻" in colour,
# "[carried]" in NO_COLOR / TERM=dumb mode).
#
# Run in a board window's legend pane while that window's columns are too
# narrow to keep their floor width, it prints the window's recorded NOTICE
# line (@pgai_board_notice) instead, cut to the pane's width, until a
# reflow at a wide-enough size clears it.
#
# Usage:
#   legend-render.sh [--kanban-root <path>]
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root override
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors
#   TMUX_PANE                           — set by tmux in a pane; names the
#                                         window whose board notice is shown
#
# Exit 0 always.

set -euo pipefail
# -h/--help prints the header comment above and exits 0 before anything
# else runs: no kanban root is resolved and no argument is read as data.
# shellcheck source=../lib/header_help.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/header_help.sh"
header_help_exit_if_requested "${BASH_SOURCE[0]}" "$@"
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source projects lib for projects_cfg_* helpers
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source legend template constants
# shellcheck source=lib/dashboard_legend.sh
source "${SCRIPT_DIR}/../lib/dashboard_legend.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# pp_run_ops: the legend renderer runs from this tree's own pgai_agent_kanban
# package.
# shellcheck source=../lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --kanban-root)
            KANBAN_ROOT="${2:-$KANBAN_ROOT}"
            shift 2
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# Operator user config, then [chain] pm_mode (config_loader.sh).
# shellcheck source=../lib/config_loader.sh
source "${SCRIPT_DIR}/../lib/config_loader.sh"
config_startup_prelude "$KANBAN_ROOT"
# ---------------------------------------------------------------------------
# Source config (INI format via kanban.cfg; replaces legacy config.cfg)
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    DASHBOARD_ROWS_PER_COLUMN="$(config_dashboard_rows_per_column "${KANBAN_ROOT}/kanban.cfg")"
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

# ---------------------------------------------------------------------------
# Board notice: a board window whose columns cannot keep their floor width
# records one NOTICE line in its @pgai_board_notice option, and every build
# and reflow resets it (lib/board_window.sh). While this pane's window holds
# one, the bar shows it in place of the legend, cut to the pane's width so
# the one-row bar shows its start rather than wrapping to its end.
# ---------------------------------------------------------------------------
if [[ -n "${TMUX_PANE:-}" ]]; then
    _board_state="$(tmux display-message -p -t "$TMUX_PANE" '#{pane_width} #{@pgai_board_notice}' 2>/dev/null || true)"
    _pane_width="${_board_state%% *}"
    _board_notice="${_board_state#* }"
    if [[ "$_board_state" == *" "* && -n "$_board_notice" ]]; then
        if [[ "$_pane_width" =~ ^[0-9]+$ ]] && (( _pane_width > 0 && ${#_board_notice} > _pane_width )); then
            _board_notice="${_board_notice:0:_pane_width}"
        fi
        printf '%s\n' "$_board_notice"
        exit 0
    fi
fi

# ---------------------------------------------------------------------------
# Build the legend line: pgai_agent_kanban.dashboard.legend handles the
# truecolor hex-to-rgb conversion and the text assembly.
# ---------------------------------------------------------------------------

# Collect project names and their display colors via projects_cfg helpers.
PROJ_NAMES=()
PROJ_COLORS=()

while IFS= read -r _proj; do
    [[ -z "$_proj" ]] && continue
    PROJ_NAMES+=("$_proj")
    _color="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_color "$_proj" 2>/dev/null || echo "")"
    # Canonical gray sentinel: PGAI_DEFAULT_PALETTE (built by projects_cfg.sh,
    # sourced above, from team/config/default_palette.txt) -- its last entry
    # is the documented "last resort" gray. Do not hand-transcribe the hex.
    [[ -z "$_color" ]] && _color="${PGAI_DEFAULT_PALETTE[-1]}"
    PROJ_COLORS+=("$_color")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

# Names and colors go to the renderer newline-delimited; the template and
# the status keywords are the DASHBOARD_LEGEND_TEMPLATE and
# DASHBOARD_LEGEND_STATUS_KEYWORDS constants (sourced from
# lib/dashboard_legend.sh above), so the assembled legend always matches the
# canonical template.
#
# pp_run_ops resolves pgai_agent_kanban from its own tree's root, which in
# the live install is the install root: the dev tree is DATA to the live
# runtime, never CODE, and no dev-tree path is added. A missing package
# fails the import loudly.
pp_run_ops pgai_agent_kanban.dashboard.legend \
    --use-color="$USE_COLOR" \
    --template="${DASHBOARD_LEGEND_TEMPLATE}" \
    --status-keywords="${DASHBOARD_LEGEND_STATUS_KEYWORDS[*]}" \
    --names="$(printf '%s\n' "${PROJ_NAMES[@]+"${PROJ_NAMES[@]}"}")" \
    --colors="$(printf '%s\n' "${PROJ_COLORS[@]+"${PROJ_COLORS[@]}"}")"
