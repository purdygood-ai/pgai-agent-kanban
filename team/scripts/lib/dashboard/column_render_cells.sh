#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_cells.sh
#
# Single-project per-column cell rendering stage: renders one project's own
# bugs/, priority/, requirements/, or queue column — per-entry colour,
# version suffix, deferral glyph, ANSI-aware truncation, and the
# active-window / middle-position-active row selection — for the two
# single-project subcommands (input and queue). The rendering lives in
# pgai_agent_kanban.dashboard.column_render.single (reached through
# pp_run_ops, one Python process per render); item statuses come from
# pgai_agent_kanban.board, the classifier GET /board uses, so this pane and
# the board agree about every item.
#
# Public entry point
# -------------------
#   column_render_render_single_project
#       Dispatches on $SUBCOMMAND ("input" or "queue") and renders that
#       subcommand's pane to stdout.
#
# Contract: the caller must call column_render_gather_data (mode "input" or
# "queue", matching $SUBCOMMAND — see column_render_data.sh) first, and must
# have already run its own argument parsing, config load, and color setup.
#   input mode reads: INPUT_DIR, ROWS, WIDTH, IS_REQUIREMENTS, ACTIVE_RC,
#     LAST_RELEASED, PROJ_MAX_ROWS, DEFER_MODE, DEFER_IDS,
#     GLYPH_DEFERRED_BUG, GLYPH_DEFER_WARNING, and (requirements, when
#     CEILINGS_READ is true) MAX_MAJOR, MAX_MINOR, MAX_PATCH,
#     CEILING_UNREADABLE.
#   queue mode reads: BACKLOG_FILE, ROWS, WIDTH, TASKS_DIR, QUEUE_PROJ_MAX_ROWS.
#   Both modes read: USE_COLOR, C_RED/C_YELLOW/C_GREEN/C_WHITE/C_DIM/RESET.
#
# Dependencies: lib/pp_run_ops.sh (sourced below unless the caller already
# has pp_run_ops) and the pgai_agent_kanban package beside this scripts/
# tree. This stage does no projects.cfg lookups of its own.

_COLUMN_RENDER_CELLS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_COLUMN_RENDER_CELLS_LIB_DIR}/../pp_run_ops.sh"

# column_render_palette_args
#     Prints nothing; fills the caller-visible array _COLUMN_RENDER_PALETTE_ARGS
#     with the --use-color/--red/... options every column_render mode takes,
#     built from the driver's USE_COLOR and C_* escapes.
column_render_palette_args() {
    _COLUMN_RENDER_PALETTE_ARGS=(
        "--use-color=${USE_COLOR}"
        "--red=${C_RED}" "--yellow=${C_YELLOW}" "--green=${C_GREEN}"
        "--white=${C_WHITE}" "--dim=${C_DIM}" "--reset=${RESET}"
    )
}

column_render_render_single_project() {
    column_render_palette_args
    if [[ "$SUBCOMMAND" == "input" ]]; then
        # Version ceilings reach the renderer only when the gather stage
        # read them for a requirements column; omitted, none is checked.
        local -a _ceiling_args=()
        if [[ "${IS_REQUIREMENTS:-false}" == "true" && "${CEILINGS_READ:-false}" == "true" ]]; then
            _ceiling_args=(
                "--max-major=${MAX_MAJOR:-}" "--max-minor=${MAX_MINOR:-}" "--max-patch=${MAX_PATCH:-}"
                "--ceiling-unreadable=${CEILING_UNREADABLE:-false}"
            )
        fi
        pp_run_ops pgai_agent_kanban.dashboard.column_render input \
            "--input-dir=${INPUT_DIR}" \
            "--rows=${ROWS}" \
            "--width=${WIDTH}" \
            "--requirements=${IS_REQUIREMENTS}" \
            "--active-rc=${ACTIVE_RC}" \
            "--last-released=${LAST_RELEASED}" \
            "--max-rows=${PROJ_MAX_ROWS}" \
            "--defer-mode=${DEFER_MODE:-off}" \
            "--defer-ids=${DEFER_IDS:-}" \
            "--glyph-deferred-bug=${GLYPH_DEFERRED_BUG:-}" \
            "--glyph-defer-warning=${GLYPH_DEFER_WARNING:-}" \
            ${_ceiling_args[@]+"${_ceiling_args[@]}"} \
            "${_COLUMN_RENDER_PALETTE_ARGS[@]}"
    elif [[ "$SUBCOMMAND" == "queue" ]]; then
        pp_run_ops pgai_agent_kanban.dashboard.column_render queue \
            "--backlog-file=${BACKLOG_FILE}" \
            "--rows=${ROWS}" \
            "--width=${WIDTH}" \
            "--tasks-dir=${TASKS_DIR}" \
            "--max-rows=${QUEUE_PROJ_MAX_ROWS}" \
            "${_COLUMN_RENDER_PALETTE_ARGS[@]}"
    fi
}
