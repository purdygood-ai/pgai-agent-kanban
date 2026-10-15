#!/usr/bin/env bash
# column-render.sh
# Per-column rendering logic for the unified visibility window.
#
# Two calling forms:
#
#   INPUT COLUMN (discovery pipeline — bugs/, priority/, requirements/):
#     column-render.sh input <dir> <cache_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#
#   AGENT QUEUE COLUMN:
#     column-render.sh queue <backlog_file> [rows] [width] [--label LABEL] [--kanban-root <path>]
#
# Multi-project mode (--all-projects flag):
#   When --all-projects is provided, the <dir>/<backlog_file> positional
#   argument is IGNORED (pass "none") — the script reads projects.cfg instead,
#   discovers every registered project, and renders a mixed-project view. Each
#   row is prefixed with a colored project tag derived from the project's
#   display_color in projects.cfg; entry text is colored by ## Status.
#     column-render.sh input none none [rows] [width] \
#       --label LABEL --kanban-root <path> --all-projects --column-type T
#     column-render.sh queue none [rows] [width] \
#       --label LABEL --kanban-root <path> --all-projects --column-type T
#
# Arguments:
#   <dir>           Absolute path to bugs/, priority/, or requirements/
#   <cache_file>    Absolute path to bug_backlog.md, priority_backlog.md, or pm_backlog.md
#   <backlog_file>  Absolute path to coder_backlog.md (or pm_, writer_, etc.)
#   rows            Max rows to display (default: $DASHBOARD_ROWS_PER_COLUMN or 7)
#   width           Column width in characters (default: 38)
#
# Options:
#   --label NAME      Pane label for the header line (e.g. BUGS, CODER, REQUIREMENTS).
#                     Emits '=== NAME ===' as the first output line.
#   --kanban-root     Override the kanban root path.
#   --all-projects    Render mixed view from all registered projects (see above).
#   --column-type T   "bugs"|"priorities"|"requirements"|"pm"|"coder"|"writer"|"tester"|"cm"
#                     Required when --all-projects is set; identifies which data to gather
#                     per project.  Falls back to auto-detection from PANE_LABEL when absent.
#   --board-widths S  (--all-projects board columns) The width every board column of
#                     this pane set is drawn at, as comma-separated KEY=WIDTH entries;
#                     KEY is a column type, or input / queue for every column of that
#                     kind (a column type wins), e.g. "input=29,queue=26". The column
#                     that builds a tick pre-renders all of them, so the other columns
#                     of the tick print without starting Python. Optional: output is
#                     identical with or without it; only the cost changes.
#
# Board columns (--all-projects with one of the eight column types): one
# column per tick builds the shared board snapshot and pre-renders the
# declared columns; the others print their pre-rendered slice (see
# lib/dashboard/column_render_board_serve.sh and column_render_board.sh).
#
# Output:
#   First line: '=== LABEL ===' (when --label is provided). Then one item per
#   line, ANSI-colored where applicable, truncated to <width> visible
#   characters (ANSI codes do not count against width). '(empty)' when there
#   are no items. In multi-project mode each row is prefixed with a project
#   color tag: "<glyph> " (glyph from dashboard_status_glyph in kanban.cfg,
#   default "■") in the project's display_color, or "[<short>] " under
#   NO_COLOR/TERM=dumb.
#   A single-project requirements column marks an open requirement the
#   project's [versioning] ceilings stop, after its coloured label:
#   " [ceiling]" when its target version exceeds a ceiling in effect,
#   " [ceiling?]" on every open requirement while a ceiling value is
#   malformed (the accessor's one line naming the file, key and value is
#   printed on stderr; the project is ceiling-blocked until it is fixed).
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (for config.cfg and project paths)
#   PGAI_AGENT_KANBAN_TEMP_DIR   — temp root; board snapshots and pre-rendered
#                                  columns live under <it>/dashboard/snapshots
#   DASHBOARD_ROWS_PER_COLUMN           — global rows override (read from config.cfg)
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# This script's directory. Built without dirname (one process fewer on every
# board column render -- see the fast path below).
_column_render_self="${BASH_SOURCE[0]}"
[[ "$_column_render_self" == */* ]] || _column_render_self="./${_column_render_self}"
SCRIPT_DIR="$(cd "${_column_render_self%/*}" && pwd)"

# Parse arguments (lib/dashboard/column_render_args.sh): records KANBAN_ROOT,
# PANE_LABEL, ALL_PROJECTS, COLUMN_TYPE, BOARD_WIDTHS, POSARGS, SUBCOMMAND and
# the help/error outcome, which are acted on below.
# shellcheck source=lib/dashboard/column_render_args.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_args.sh"
column_render_parse_args "$@"

# Board column fast path: serve this tick's pre-rendered column, or build the
# tick in one Python process, before loading the libraries below. Returns 1
# (having printed nothing) whenever this column needs the full path.
# shellcheck source=lib/dashboard/column_render_board_serve.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_board_serve.sh"
if column_render_board_serve; then
    exit 0
fi

# TEAM_DIR: the 'team/' directory two levels up (.../dashboard -> scripts -> team).
TEAM_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Local-only render guard: pp_last_released_version calls below (requirements-
# column last-released resolution) must never perform a network git fetch.
export PGAI_GIT_LOCAL_ONLY=1

# lib/projects.sh: projects_cfg_* helpers, required for --all-projects mode.
# Sourced unconditionally; its helpers are no-ops when projects.cfg is absent.
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/ini_parser.sh
source "${SCRIPT_DIR}/../lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# Source glyph map (single home for all status-bar/card glyph literals)
# shellcheck source=../lib/status_glyphs.sh
source "${SCRIPT_DIR}/../lib/status_glyphs.sh"
# Source shared deferral-posture scope helper (single decision point for the
# per-bug deferred-card glyph — resolves through team/scripts/lib/defer_posture.sh
# and team/scripts/lib/discovery_defer.sh, never re-derives either here)
# shellcheck source=lib/defer_scope.sh
source "${SCRIPT_DIR}/lib/defer_scope.sh"
# Source the deferral-posture gather stage (bash-side glue between
# defer_scope.sh and the two card renderers — see its own header comment)
# shellcheck source=lib/dashboard/column_render_defer.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_defer.sh"

# Column-render stage libs (data-gather, multi-project frame assembly,
# single-project cell rendering) — this driver is a thin dispatcher over them.
# column_render_data_multi.sh is the "multi" mode split out of
# column_render_data.sh (see both files' own header comments); it must be
# sourced first since column_render_data.sh's "multi" case calls the
# function it defines.
# The data-gather stages read each project's Active RC through
# release_state_active_rc (lib/cm/common.sh).
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"
# shellcheck source=lib/dashboard/column_render_data_multi.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_data_multi.sh"
# shellcheck source=lib/dashboard/column_render_data.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_data.sh"
# shellcheck source=lib/dashboard/column_render_frame.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_frame.sh"
# shellcheck source=lib/dashboard/column_render_cells.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_cells.sh"

# Shared per-tick board snapshot: in --all-projects mode the gather that
# resolves and classifies every registered project's items runs once per
# tick for all eight board columns, and each column (in the operator
# window's aggregated panes and the visibility window's per-column panes
# alike) renders its own slice of it -- see column_render_board.sh. The
# snapshot's location and staleness bound come from lib/snapshot.sh.
# shellcheck source=lib/snapshot.sh
source "${SCRIPT_DIR}/lib/snapshot.sh"
# shellcheck source=lib/dashboard/column_render_board.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_board.sh"
# shellcheck source=lib/dashboard/column_render_config.sh
source "${SCRIPT_DIR}/../lib/dashboard/column_render_config.sh"

# Act on the parse outcome recorded above.
if [[ "$COLUMN_RENDER_SHOW_HELP" == "true" ]]; then
    # shellcheck source=../lib/header_help.sh
    source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
    header_help_print "${BASH_SOURCE[0]}"
    exit 0
fi
if [[ -n "$COLUMN_RENDER_ARG_ERROR" ]]; then
    echo "$COLUMN_RENDER_ARG_ERROR" >&2
    exit 1
fi

# Source config (kanban.cfg; read_ini comes from project_paths.sh above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
    # [dashboard] max_rows → DASHBOARD_MAX_ROWS (column_render_config.sh).
    column_render_config_max_rows
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# DASHBOARD_ROWS_PER_COLUMN — validate and apply default.
_DEFAULT_ROWS=13
if [[ -z "${DASHBOARD_ROWS_PER_COLUMN:-}" ]]; then
    DASHBOARD_ROWS_PER_COLUMN="$_DEFAULT_ROWS"
elif ! [[ "${DASHBOARD_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] || (( DASHBOARD_ROWS_PER_COLUMN <= 0 )); then
    echo "WARNING: DASHBOARD_ROWS_PER_COLUMN='${DASHBOARD_ROWS_PER_COLUMN}' is invalid; falling back to ${_DEFAULT_ROWS}" >&2
    DASHBOARD_ROWS_PER_COLUMN="$_DEFAULT_ROWS"
fi

# Dashboard layout values (kanban.cfg [dashboard]): control the dynamic
# per-project row allocation algorithm. Defaults match kanban.cfg.example.
column_render_config_layout

# [dashboard] dashboard_status_glyph → DASHBOARD_STATUS_GLYPH
# OPTIONAL; default is U+25A0 "■" — byte-identical to pre-change output when
# the key is absent. Passed to the multi-project renderer as --status-glyph
# (column_render_frame.sh).
column_render_config_status_glyph

# ANSI color support: USE_COLOR, then the C_* escapes from ansi_code() in
# lib/script_common.sh (sourced above).
column_render_config_color_mode
column_render_config_ansi

# The per-column header is deliberately neutral: per-project halt scope is
# surfaced by show-header/show-multi/status-bottom/status-right/attention
# indicators, never by the column header — any future per-column halt tint
# must derive the owning project from the column's input directory, never
# from registry order.
HALT_HEADER_COLOR=""
HALT_HEADER_MARKER=""

# Emit pane label header if provided -- unless the board fast path already
# printed it before handing this column over (_COLUMN_RENDER_HEADER_PRINTED).
if [[ -n "$PANE_LABEL" && "${_COLUMN_RENDER_HEADER_PRINTED:-}" != true ]]; then
    if [[ -n "$HALT_HEADER_COLOR" ]]; then
        echo "${HALT_HEADER_COLOR}=== ${PANE_LABEL} ===${RESET}"
    else
        echo "=== ${PANE_LABEL} ===${HALT_HEADER_MARKER}"
    fi
fi

# Multi-project mode dispatch — see column_render_data.sh / column_render_frame.sh.
if [[ "$ALL_PROJECTS" == "true" ]]; then
    # A board column (one of the eight types, with the subcommand that type
    # takes) renders its slice of the shared per-tick board snapshot
    # (column_render_board.sh), which falls back to gathering this column
    # directly when the snapshot cannot be used. Narration is stderr-only,
    # so stdout is byte-identical either way. Anything else -- an unknown or
    # missing column type -- gathers and renders on its own, as before.
    case "${COLUMN_TYPE}:${SUBCOMMAND}" in
        bugs:input|priorities:input|requirements:input|pm:queue|coder:queue|writer:queue|tester:queue|cm:queue)
            _column_render_multi_rows_width
            column_render_board_render
            ;;
        *)
            _column_render_out="$(column_render_gather_data multi; column_render_render_multi_project)"
            printf '%s\n' "$_column_render_out"
            ;;
    esac
    exit 0
fi

# Subcommand dispatch (single-project input/queue).
if [[ "$SUBCOMMAND" == "input" ]]; then
    # input <dir> <cache_file> [rows] [width]
    if [[ ${#POSARGS[@]} -lt 3 ]]; then
        echo "ERROR: 'input' requires <dir> and <cache_file>" >&2
        echo "Usage: $0 input <dir> <cache_file> [rows] [width]" >&2
        exit 1
    fi
    INPUT_DIR="${POSARGS[1]}"
    CACHE_FILE="${POSARGS[2]}"
    ROWS="${POSARGS[3]:-$DASHBOARD_ROWS_PER_COLUMN}"
    WIDTH="${POSARGS[4]:-38}"

    # Validate rows/width are positive integers
    if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
        echo "WARNING: rows='${ROWS}' invalid; using ${DASHBOARD_ROWS_PER_COLUMN}" >&2
        ROWS="$DASHBOARD_ROWS_PER_COLUMN"
    fi
    if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
        echo "WARNING: width='${WIDTH}' invalid; using 38" >&2
        WIDTH=38
    fi

    column_render_gather_data input

elif [[ "$SUBCOMMAND" == "queue" ]]; then
    # queue <backlog_file> [rows] [width]
    if [[ ${#POSARGS[@]} -lt 2 ]]; then
        echo "ERROR: 'queue' requires <backlog_file>" >&2
        echo "Usage: $0 queue <backlog_file> [rows] [width]" >&2
        exit 1
    fi
    BACKLOG_FILE="${POSARGS[1]}"
    ROWS="${POSARGS[2]:-$DASHBOARD_ROWS_PER_COLUMN}"
    WIDTH="${POSARGS[3]:-22}"

    # Validate rows/width
    if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
        echo "WARNING: rows='${ROWS}' invalid; using ${DASHBOARD_ROWS_PER_COLUMN}" >&2
        ROWS="$DASHBOARD_ROWS_PER_COLUMN"
    fi
    if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
        echo "WARNING: width='${WIDTH}' invalid; using 22" >&2
        WIDTH=22
    fi

    column_render_gather_data queue
fi

column_render_render_single_project
