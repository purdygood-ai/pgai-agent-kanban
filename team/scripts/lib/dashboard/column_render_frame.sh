#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_frame.sh
#
# Multi-project frame stage: renders the --all-projects pane from the
# per-project lists column_render_data_multi.sh's gather stage exported —
# reading each project's bugs/, priority/, requirements/, or queue file,
# classifying every item through pgai_agent_kanban.board (the classifier
# GET /board uses), the status-priority-aware per-project cap, the
# per-project active-window / middle-position-active math, project tags,
# truncation, and the final print. All of it lives in
# pgai_agent_kanban.dashboard.column_render.multi, reached through
# pp_run_ops in ONE Python process per render.
#
# Public entry point
# -------------------
#   column_render_render_multi_project
#       Renders the --all-projects pane and prints it to stdout.
#
# Contract: the caller must call column_render_gather_data multi first (see
# column_render_data.sh) and must have already run its own argument parsing,
# config load, and color setup — this stage reads WIDTH, SUBCOMMAND,
# COLUMN_TYPE, USE_COLOR, the C_RED/C_YELLOW/C_GREEN/C_WHITE/C_DIM/RESET
# ANSI variables, DASHBOARD_STATUS_GLYPH, and the DASHBOARD_MIN/MAX_ROWS_PER_
# COLUMN/PROJECT layout values; the per-project lists reach the Python
# process through the environment the gather stage exported.
#
# Dependencies: lib/pp_run_ops.sh and column_render_cells.sh's
# column_render_palette_args (both sourced below unless already defined).

_COLUMN_RENDER_FRAME_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_COLUMN_RENDER_FRAME_LIB_DIR}/../pp_run_ops.sh"
# shellcheck source=column_render_cells.sh
declare -F column_render_palette_args >/dev/null || source "${_COLUMN_RENDER_FRAME_LIB_DIR}/column_render_cells.sh"

column_render_render_multi_project() {
    column_render_palette_args
    pp_run_ops pgai_agent_kanban.dashboard.column_render multi \
        "--subcommand=${SUBCOMMAND}" \
        "--column-type=${COLUMN_TYPE}" \
        "--width=${WIDTH}" \
        "--status-glyph=${DASHBOARD_STATUS_GLYPH:-}" \
        "--min-rows-per-column=${DASHBOARD_MIN_ROWS_PER_COLUMN}" \
        "--max-rows-per-column=${DASHBOARD_MAX_ROWS_PER_COLUMN}" \
        "--min-rows-per-project=${DASHBOARD_MIN_ROWS_PER_PROJECT}" \
        "--max-rows-per-project=${DASHBOARD_MAX_ROWS_PER_PROJECT}" \
        "${_COLUMN_RENDER_PALETTE_ARGS[@]}"
}
