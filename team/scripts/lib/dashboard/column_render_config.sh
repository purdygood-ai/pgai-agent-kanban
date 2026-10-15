#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_config.sh
#
# The render settings column-render.sh resolves from the environment and
# kanban.cfg [dashboard], one function per setting, so the driver and its
# board fast path (column_render_board_serve.sh) resolve each one the same
# way. Sourcing only defines functions.
#
# Functions
# ---------
#   column_render_config_color_mode
#       USE_COLOR: "false" under TERM=dumb or a non-empty NO_COLOR, else
#       "true".
#   column_render_config_max_rows
#       DASHBOARD_MAX_ROWS (exported): a value already in the environment
#       wins, else kanban.cfg [dashboard] max_rows (default 20). Applied
#       only when kanban.cfg exists; otherwise left as it was.
#   column_render_config_status_glyph
#       DASHBOARD_STATUS_GLYPH (exported): the environment's value when
#       non-empty, else kanban.cfg [dashboard] dashboard_status_glyph, else
#       U+25A0 "■".
#   column_render_config_layout
#       DASHBOARD_MIN_ROWS_PER_COLUMN, DASHBOARD_MAX_ROWS_PER_COLUMN,
#       DASHBOARD_MIN_ROWS_PER_PROJECT, DASHBOARD_MAX_ROWS_PER_PROJECT from
#       kanban.cfg [dashboard] (defaults 13, 34, 3, 8); a value that is not
#       a positive integer falls back to its default.
#   column_render_config_ansi
#       C_RED, C_YELLOW, C_GREEN, C_WHITE, C_DIM, RESET from ansi_code for
#       the current USE_COLOR (empty when colour is off).
#
# Every function reads KANBAN_ROOT. Dependencies: lib/ini_parser.sh
# (read_ini) and, for column_render_config_ansi, lib/script_common.sh
# (ansi_code) -- the caller sources both.

column_render_config_color_mode() {
    USE_COLOR=true
    if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]]; then
        USE_COLOR=false
    fi
}

column_render_config_max_rows() {
    # Precedence: per-project projects.cfg > env > kanban.cfg > 20 (the
    # per-project tier is applied by the gather). An operator-exported
    # DASHBOARD_MAX_ROWS still wins (env-over-cfg).
    if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
        DASHBOARD_MAX_ROWS="${DASHBOARD_MAX_ROWS:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard max_rows 20)}"
        export DASHBOARD_MAX_ROWS
    fi
}

column_render_config_status_glyph() {
    # A value already set in the environment (e.g. exported by load_config
    # upstream) wins; read_ini is only called when the env var is unset.
    if [[ -z "${DASHBOARD_STATUS_GLYPH:-}" ]]; then
        DASHBOARD_STATUS_GLYPH="$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard dashboard_status_glyph "■")"
        DASHBOARD_STATUS_GLYPH="${DASHBOARD_STATUS_GLYPH:-■}"
    fi
    export DASHBOARD_STATUS_GLYPH
}

column_render_config_layout() {
    local cfg="${KANBAN_ROOT}/kanban.cfg"
    DASHBOARD_MIN_ROWS_PER_COLUMN="$(read_ini "$cfg" dashboard min_rows_per_column 13)"
    DASHBOARD_MAX_ROWS_PER_COLUMN="$(read_ini "$cfg" dashboard max_rows_per_column 34)"
    DASHBOARD_MIN_ROWS_PER_PROJECT="$(read_ini "$cfg" dashboard min_rows_per_project 3)"
    DASHBOARD_MAX_ROWS_PER_PROJECT="$(read_ini "$cfg" dashboard max_rows_per_project 8)"
    [[ "${DASHBOARD_MIN_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MIN_ROWS_PER_COLUMN > 0 )) || DASHBOARD_MIN_ROWS_PER_COLUMN=13
    [[ "${DASHBOARD_MAX_ROWS_PER_COLUMN}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MAX_ROWS_PER_COLUMN > 0 )) || DASHBOARD_MAX_ROWS_PER_COLUMN=34
    [[ "${DASHBOARD_MIN_ROWS_PER_PROJECT}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MIN_ROWS_PER_PROJECT > 0 )) || DASHBOARD_MIN_ROWS_PER_PROJECT=3
    [[ "${DASHBOARD_MAX_ROWS_PER_PROJECT}" =~ ^[0-9]+$ ]] && (( DASHBOARD_MAX_ROWS_PER_PROJECT > 0 )) || DASHBOARD_MAX_ROWS_PER_PROJECT=8
    return 0
}

column_render_config_ansi() {
    C_RED="$(ansi_code red)"
    C_YELLOW="$(ansi_code yellow)"
    C_GREEN="$(ansi_code green)"
    C_WHITE="$(ansi_code white)"
    C_DIM="$(ansi_code dim)"
    RESET="$(ansi_code reset)"
}
