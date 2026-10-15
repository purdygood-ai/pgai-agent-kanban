#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_data.sh
#
# Column-render data-gather stage: resolves which projects/files/config
# values feed a render call, before any cell is formatted or any frame is
# assembled. Extracted from team/scripts/dashboard/column-render.sh so the
# driver stays a thin dispatcher over sourced stage libs. Behavior-preserving
# move: no stdout/stderr line, exit code, or on-disk output changes as a
# result of this file existing — it is one stage split out of a larger
# driver so each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   column_render_gather_data <mode>
#       <mode> is "multi", "input", or "queue" — matching the render call
#       the driver is about to make. Sets the global variables the matching
#       render-stage function reads (column_render_render_multi_project in
#       column_render_frame.sh, or column_render_render_single_project in
#       column_render_cells.sh):
#         multi   Delegates to column_render_gather_data_multi()
#                 (column_render_data_multi.sh — split out of this file so
#                 this driver stays a thin mode dispatcher; see that file's
#                 own header for the full "multi" contract, unchanged by
#                 the split).
#         input   Resolves the owning project for INPUT_DIR, plus IS_REQUIREMENTS,
#                 ACTIVE_RC, LAST_RELEASED, PROJ_MAX_ROWS, the version
#                 ceilings (MAX_MAJOR, MAX_MINOR, MAX_PATCH, CEILINGS_READ,
#                 CEILING_UNREADABLE), and (bugs column only)
#                 DEFER_MODE/DEFER_ERROR_FILE/DEFER_IDS via
#                 column_render_defer_gather_input (column_render_defer.sh).
#         queue   Resolves TASKS_DIR (the backlog's own <project>/tasks, or
#                 empty) and QUEUE_PROJ_MAX_ROWS.
# Contract: the caller must have already run argument parsing, config load, and
# color setup — this stage reads KANBAN_ROOT, SUBCOMMAND, COLUMN_TYPE, POSARGS,
# INPUT_DIR, the DASHBOARD_* tunables, USE_COLOR, and the ANSI C_* variables the
# driver sets; it sets ROWS/WIDTH as a side effect in "multi" mode.
# Dependencies: lib/project_paths.sh, lib/projects.sh, lib/ini_parser.sh,
# lib/cm/common.sh, column_render_data_multi.sh, dashboard/lib/defer_scope.sh,
# dashboard/lib/column_render_defer.sh (all sourced by column-render.sh before
# this file).

column_render_gather_data() {
    local _mode="$1"

    case "$_mode" in
    multi)
        column_render_gather_data_multi
        ;;

    input)
        # Determine if this is a requirements/ column (suffix indicators apply)
        IS_REQUIREMENTS=false
        if [[ "$INPUT_DIR" == *requirements* ]]; then
            IS_REQUIREMENTS=true
        fi
    
        # Resolve the project that owns INPUT_DIR so all per-project lookups
        # (last_released, active RC, version ceilings, max_rows) use that project's
        # own values rather than a substituted default.
        #
        # Root-cause note: falling back to a default project here is exactly the
        # when the match failed, so a managed project's requirements were colored
        # against the self-build's version instead of their own.
        # Fix: match INPUT_DIR against each registered project's requirements/,
        # bugs/, and priority/ paths; use the matched project.  If no project
        # matches, _RENDER_PROJECT is left empty and all per-project lookups
        # produce empty strings — the renderer receives an empty last release
        # and treats requirements as unshipped (white/open), which is the correct
        # safe default.
        #
        # The shipped / active-RC decision itself is
        # pgai_agent_kanban.board.classify_requirement, shared with the
        # multi-project pane and GET /board; this stage only resolves the
        # owning project's active RC and last release for it.
    
        # Normalize INPUT_DIR for path comparison (strip trailing slash; resolve symlinks
        # conservatively with realpath -m so non-existent dirs still match).
        _INPUT_DIR_NORM="$(realpath -m "$INPUT_DIR" 2>/dev/null || echo "$INPUT_DIR")"
        _INPUT_PROJECT=""
        while IFS= read -r _p; do
            [[ -z "$_p" ]] && continue
            # Check requirements/, bugs/, and priority/ for this project.
            for _dir_fn in pp_requirements_dir pp_bugs_dir pp_priority_dir; do
                _p_dir="$(KANBAN_ROOT="$KANBAN_ROOT" "$_dir_fn" "$_p" 2>/dev/null || true)"
                [[ -z "$_p_dir" ]] && continue
                _p_dir_norm="$(realpath -m "$_p_dir" 2>/dev/null || echo "$_p_dir")"
                if [[ "$_INPUT_DIR_NORM" == "$_p_dir_norm" ]]; then
                    _INPUT_PROJECT="$_p"
                    break 2
                fi
            done
        done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
        # Use the matched project; when no match, leave empty so per-project
        # lookups below produce empty values (safe open/unshipped rendering).
        _RENDER_PROJECT="$_INPUT_PROJECT"
    
        ACTIVE_RC=""
        MAX_MINOR=""
        MAX_MAJOR=""
        MAX_PATCH=""
        CEILINGS_READ=false
        CEILING_UNREADABLE=false
        LAST_RELEASED=""
    
        # Resolve per-project max_rows (precedence: per-project cfg > DASHBOARD_MAX_ROWS > 20)
        PROJ_MAX_ROWS="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_max_rows "$_RENDER_PROJECT" 2>/dev/null || echo "${DASHBOARD_MAX_ROWS:-20}")"
        [[ -z "$PROJ_MAX_ROWS" ]] && PROJ_MAX_ROWS="${DASHBOARD_MAX_ROWS:-20}"
    
        if [[ "$IS_REQUIREMENTS" == "true" ]]; then
            # Read Active RC from release-state.md for the rendered project.
            RELEASE_STATE="$(pp_release_state "$_RENDER_PROJECT" 2>/dev/null)" || RELEASE_STATE=""
            # A vX.Y.Z version; "none", empty, malformed or missing reads as
            # "" (no [active] marker).
            ACTIVE_RC="$(release_state_active_rc "$RELEASE_STATE" "")"
            # Version ceilings in effect, for the owning project only: an
            # absent key is its documented default, an explicit `none` is
            # empty (no ceiling). A malformed value makes its accessor print
            # one line naming the file, key and value -- left on stderr, the
            # render's diagnostics -- and fail; that is CEILING_UNREADABLE,
            # never "no ceiling", and the renderer marks every open
            # requirement the way discovery blocks them. With no owning
            # project no ceiling is read and none is marked.
            if [[ -n "$_RENDER_PROJECT" ]]; then
                CEILINGS_READ=true
                MAX_MAJOR="$(pp_max_major "$_RENDER_PROJECT")" || CEILING_UNREADABLE=true
                MAX_MINOR="$(pp_max_minor "$_RENDER_PROJECT")" || CEILING_UNREADABLE=true
                MAX_PATCH="$(pp_max_patch "$_RENDER_PROJECT")" || CEILING_UNREADABLE=true
            fi
            # Resolve last_released for this entry's own project (never a default).
            # The sentinel guard ("none" / "v0.0.0") is preserved:
            # classify_requirement treats an empty last release as nothing
            # shipped, so a fresh project never renders open requirements green.
            LAST_RELEASED="$(pp_last_released_version "$_RENDER_PROJECT" 2>/dev/null)" || LAST_RELEASED=""
            # Strip sentinel values so the renderer receives "" (nothing shipped).
            [[ "$LAST_RELEASED" == "v0.0.0" || "${LAST_RELEASED,,}" == "none" ]] && LAST_RELEASED=""
        fi

        # Deferral posture surfacing (bugs column only) — sets DEFER_MODE/
        # DEFER_ERROR_FILE/DEFER_IDS (see column_render_defer.sh).
        column_render_defer_gather_input

        # Explicit success: a "cond && action" whose cond is false (the common
        # path here) must not leak as this function's return status under -e.
        true
        ;;

    queue)
        # Resolve the tasks directory (for reading each task's status.md)
        # from the backlog file's own location: a project's queues live at
        # <project>/tasks/queues/<agent>_backlog.md, so its tasks are the
        # queues/ directory's parent — the same tasks/ GET /board reads.
        # A backlog anywhere else leaves TASKS_DIR empty and every entry's
        # state falls back to its queue marker; no other project's tasks are
        # ever substituted.
        TASKS_DIR=""
        _queue_dir="$(dirname "$BACKLOG_FILE")"
        if [[ "$(basename "$_queue_dir")" == "queues" && -d "$(dirname "$_queue_dir")" ]]; then
            TASKS_DIR="$(dirname "$_queue_dir")"
        fi
        # Resolve per-project max_rows (precedence: per-project cfg > DASHBOARD_MAX_ROWS > 20)
        QUEUE_PROJ_MAX_ROWS="${DASHBOARD_MAX_ROWS:-20}"
        ;;

    *)
        echo "ERROR: column_render_gather_data: unknown mode '${_mode}'" >&2
        exit 1
        ;;
    esac
}
