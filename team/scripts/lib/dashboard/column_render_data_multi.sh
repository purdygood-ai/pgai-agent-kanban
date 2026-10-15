#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_data_multi.sh
#
# Multi-project data-gather stage: the "multi" mode of column_render_gather_data()
# (team/scripts/lib/dashboard/column_render_data.sh), split into its own file.
# It resolves, in bash, everything the --all-projects pane needs from
# projects.cfg and the project path helpers — the registered projects in
# priority order, each one's colour, max_rows, intake directory or backlog
# file, tasks directory, active RC and last release (requirements column),
# and deferral posture (bugs column) — and exports them for the frame stage
# (column_render_frame.sh), whose single Python process reads the files,
# classifies the items and renders the pane. This stage starts no Python
# process of its own.
#
# Public entry point
# -------------------
#   column_render_gather_data_multi
#       Resolves ROWS/WIDTH from POSARGS, builds the parallel PROJ_* arrays
#       from projects.cfg, and exports them as newline-separated lists:
#         _PROJ_NAMES_STR, _PROJ_COLORS_STR, _PROJ_MAX_ROWS_STR,
#         _PROJ_INPUT_DIRS_STR, _PROJ_BACKLOG_FILES_STR, _PROJ_TASKS_DIRS_STR,
#         _PROJ_DEFER_INFO_STR, _ALL_ACTIVE_RCS, _ALL_LAST_RELEASED,
#         plus DFS_SEP, GLYPH_DEFERRED_BUG and GLYPH_DEFER_WARNING.
#       One line per project, in PROJ_NAMES order; "none" marks a path or
#       version the project does not have. The reader is
#       pgai_agent_kanban.dashboard.column_render.multi.sources_from_env.
#       Prints "(empty)" and exits 0 when no project is registered.
#       Called by column_render_gather_data() (column_render_data.sh) when
#       its <mode> argument is "multi".
#
#   column_render_gather_board_multi
#       The same gather for EVERY board column at once, for the shared
#       board snapshot (column_render_board.sh): resolves ROWS/WIDTH and the
#       shared per-project lists once, then exports each column's own lists
#       (_PROJ_INPUT_DIRS_STR, _PROJ_BACKLOG_FILES_STR, _PROJ_DEFER_INFO_STR,
#       _ALL_ACTIVE_RCS, _ALL_LAST_RELEASED) with the suffix "__<column>"
#       appended, e.g. _PROJ_INPUT_DIRS_STR__bugs -- the names
#       sources_from_env reads with that column's suffix. The shared lists
#       (_PROJ_NAMES_STR, _PROJ_COLORS_STR, _PROJ_MAX_ROWS_STR,
#       _PROJ_TASKS_DIRS_STR) keep their plain names. Prints "(empty)" and
#       exits 0 when no project is registered, exactly as above.
#
# Contract: identical to column_render_gather_data's own — the caller must
# have already run argument parsing, config load, and color setup; this
# stage reads KANBAN_ROOT, SUBCOMMAND, COLUMN_TYPE, POSARGS and the
# DASHBOARD_* tunables, and sets ROWS/WIDTH as a side effect.
# Dependencies: lib/project_paths.sh, lib/projects.sh, lib/ini_parser.sh,
# lib/cm/common.sh, team/scripts/lib/dashboard/column_render_defer.sh (all
# sourced by column-render.sh before this file).

# Guard against double-sourcing.
if declare -F column_render_gather_data_multi >/dev/null 2>&1; then
    return 0
fi

# The eight board column types, in board order.
COLUMN_RENDER_BOARD_COLUMN_TYPES=(bugs priorities requirements pm coder writer tester cm)

# _column_render_multi_width
#     Resolves WIDTH from POSARGS (same slot as single-project): the pane
#     width argument, 38 (input) or 22 (queue) when absent, 38 when not a
#     positive integer. Reads only SUBCOMMAND and POSARGS, so the board
#     fast path (column_render_board_serve.sh) resolves the width the same
#     way before any config is loaded.
_column_render_multi_width() {
        # input: <subcommand> <dir> <cache> [rows] [width]
        # queue: <subcommand> <backlog> [rows] [width]
        if [[ "$SUBCOMMAND" == "input" ]]; then
            WIDTH="${POSARGS[4]:-38}"
        else
            WIDTH="${POSARGS[3]:-22}"
        fi
        if ! [[ "$WIDTH" =~ ^[0-9]+$ ]] || (( WIDTH <= 0 )); then
            WIDTH=38
        fi
}

# _column_render_multi_rows_width
#     Resolves ROWS/WIDTH from POSARGS (same slots as single-project).
_column_render_multi_rows_width() {
        if [[ "$SUBCOMMAND" == "input" ]]; then
            ROWS="${POSARGS[3]:-$DASHBOARD_ROWS_PER_COLUMN}"
        else
            ROWS="${POSARGS[2]:-$DASHBOARD_ROWS_PER_COLUMN}"
        fi
        if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || (( ROWS <= 0 )); then
            ROWS="$DASHBOARD_ROWS_PER_COLUMN"
        fi
        _column_render_multi_width
}

# _column_render_multi_projects
#     Builds the per-project lists every column shares -- PROJ_NAMES,
#     PROJ_COLORS, PROJ_MAX_ROWS, PROJ_TASKS_DIRS -- from projects.cfg, and
#     exports them (plus DFS_SEP and the deferral glyphs). Prints "(empty)"
#     and exits 0 when no project is registered.
_column_render_multi_projects() {
        # Collect project list and their colors from projects.cfg.
        # Build two parallel arrays: PROJ_NAMES and PROJ_COLORS.
        # Projects are read in priority order (as returned by projects_cfg_list).
        PROJ_NAMES=()
        PROJ_COLORS=()
        PROJ_MAX_ROWS=()
        _cfg_path="$(projects_cfg_path)"
        while IFS= read -r _proj; do
            [[ -z "$_proj" ]] && continue
            PROJ_NAMES+=("$_proj")
            # projects_cfg_color requires KANBAN_ROOT to be set (it is).
            _color="$(projects_cfg_color "$_proj" 2>/dev/null || echo "")"
            # Canonical gray sentinel: PGAI_DEFAULT_PALETTE (built by projects_cfg.sh,
            # sourced above, from team/config/default_palette.txt) -- its last entry
            # is the documented "last resort" gray. Do not hand-transcribe the hex.
            [[ -z "$_color" ]] && _color="${PGAI_DEFAULT_PALETTE[-1]}"
            PROJ_COLORS+=("$_color")
            # per-project max_rows: precedence is per-project cfg > DASHBOARD_MAX_ROWS > 20
            _max_rows="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_max_rows "$_proj" 2>/dev/null || echo "${DASHBOARD_MAX_ROWS:-20}")"
            [[ -z "$_max_rows" ]] && _max_rows="${DASHBOARD_MAX_ROWS:-20}"
            PROJ_MAX_ROWS+=("$_max_rows")
        done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
    
        if [[ ${#PROJ_NAMES[@]} -eq 0 ]]; then
            echo "(empty)"
            exit 0
        fi

        PROJ_TASKS_DIRS=()
        for _p in "${PROJ_NAMES[@]}"; do
            _p_tasks="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_p" 2>/dev/null || true)"
            PROJ_TASKS_DIRS+=("${_p_tasks}")
        done

        # Serialize the shared arrays to newline-delimited strings, exported
        # for the frame stage's Python process (sources_from_env reads them).
        export _PROJ_NAMES_STR
        export _PROJ_COLORS_STR
        export _PROJ_MAX_ROWS_STR
        export _PROJ_TASKS_DIRS_STR
        export DFS_SEP
        export GLYPH_DEFERRED_BUG
        export GLYPH_DEFER_WARNING
        _PROJ_NAMES_STR="$(printf '%s\n' "${PROJ_NAMES[@]}")"
        _PROJ_COLORS_STR="$(printf '%s\n' "${PROJ_COLORS[@]}")"
        _PROJ_MAX_ROWS_STR="$(printf '%s\n' "${PROJ_MAX_ROWS[@]}")"
        _PROJ_TASKS_DIRS_STR="$(printf '%s\n' "${PROJ_TASKS_DIRS[@]}")"
}

# _column_render_multi_column_lists <column_type> <suffix>
#     Builds one column's per-project lists (intake directory or backlog
#     file, deferral info, active RC and last release) for the projects
#     _column_render_multi_projects resolved, and exports them under their
#     plain names with <suffix> appended ("" for a single-column gather).
_column_render_multi_column_lists() {
        local _ctype="$1" _sfx="$2"
        # Build per-project directory/file lists for the chosen column type.
        PROJ_INPUT_DIRS=()
        PROJ_BACKLOG_FILES=()
        # Deferral posture, one entry per project (bugs column only) — see
        # column_render_defer_multi_line's own header comment.
        PROJ_DEFER_INFO=()

        for _p in "${PROJ_NAMES[@]}"; do
            # Queue file paths are resolved through pp_queue_path (canonical flat
            # layout: queues/<agent>_backlog.md).  Do NOT construct these paths
            # inline; pp_queue_path is the single source of truth for queue file
            # locations.
            case "${_ctype}" in
                bugs)
                    _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_bugs_dir "$_p" 2>/dev/null || true)"
                    PROJ_INPUT_DIRS+=("${_dir}")
                    PROJ_BACKLOG_FILES+=("none")
                    PROJ_DEFER_INFO+=("$(column_render_defer_multi_line "$KANBAN_ROOT" "$_p" "${_dir}")")
                    ;;
                priorities)
                    _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_priority_dir "$_p" 2>/dev/null || true)"
                    PROJ_INPUT_DIRS+=("${_dir}")
                    PROJ_BACKLOG_FILES+=("none")
                    ;;
                requirements)
                    _dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_requirements_dir "$_p" 2>/dev/null || true)"
                    PROJ_INPUT_DIRS+=("${_dir}")
                    PROJ_BACKLOG_FILES+=("none")
                    ;;
                pm|coder|writer|tester|cm)
                    PROJ_INPUT_DIRS+=("none")
                    PROJ_BACKLOG_FILES+=("$(KANBAN_ROOT="$KANBAN_ROOT" pp_queue_path "$_p" "${_ctype}" 2>/dev/null || true)")
                    ;;
                *)
                    # Unknown column type — fall back to empty for this project
                    PROJ_INPUT_DIRS+=("none")
                    PROJ_BACKLOG_FILES+=("none")
                    ;;
            esac
        done

        # Resolve per-project RC info for requirements columns.
        # For non-requirements columns every entry is "none".
        local _active_rcs="" _last_released=""
        for _p in "${PROJ_NAMES[@]}"; do
            if [[ "${_ctype}" == "requirements" ]]; then
                _p_rs="$(KANBAN_ROOT="$KANBAN_ROOT" pp_release_state "$_p" 2>/dev/null || true)"
                # A vX.Y.Z version; anything else (missing, empty, malformed)
                # reads "none" (no [active] marker).
                _p_arc="$(release_state_active_rc "$_p_rs" none)"
                _active_rcs="${_active_rcs}${_p_arc}"$'\n'
                _p_lr="$(KANBAN_ROOT="$KANBAN_ROOT" pp_last_released_version "$_p" 2>/dev/null || echo "none")"
                [[ "$_p_lr" == "v0.0.0" ]] && _p_lr="none"
                _last_released="${_last_released}${_p_lr}"$'\n'
            else
                _active_rcs="${_active_rcs}none"$'\n'
                _last_released="${_last_released}none"$'\n'
            fi
        done

        export "_PROJ_INPUT_DIRS_STR${_sfx}=$(printf '%s\n' "${PROJ_INPUT_DIRS[@]}")"
        export "_PROJ_BACKLOG_FILES_STR${_sfx}=$(printf '%s\n' "${PROJ_BACKLOG_FILES[@]}")"
        export "_PROJ_DEFER_INFO_STR${_sfx}=$(printf '%s\n' "${PROJ_DEFER_INFO[@]}")"
        export "_ALL_ACTIVE_RCS${_sfx}=${_active_rcs}"
        export "_ALL_LAST_RELEASED${_sfx}=${_last_released}"
}

column_render_gather_data_multi() {
        _column_render_multi_rows_width
        _column_render_multi_projects
        _column_render_multi_column_lists "${COLUMN_TYPE}" ""
}

column_render_gather_board_multi() {
        _column_render_multi_rows_width
        _column_render_multi_projects
        local _ctype
        for _ctype in "${COLUMN_RENDER_BOARD_COLUMN_TYPES[@]}"; do
            _column_render_multi_column_lists "$_ctype" "__${_ctype}"
        done
}
