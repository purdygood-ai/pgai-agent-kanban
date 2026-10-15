#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_defer.sh
#
# Deferral-posture gather stage for the bugs column: the bash-side glue
# between team/scripts/dashboard/lib/defer_scope.sh's dashboard_defer_card_ids
# (the single resolver/decision owner — see that file) and the two card
# renderers (column_render_cells.sh single-project, column_render_data_multi.sh's
# multi-project data-gather). Extracted out of column_render_data.sh so that
# file's own per-project loop and "input" mode branch stay thin calls
# instead of carrying this stage's plumbing inline — the same
# engine-decomposition principle team/scripts/lint_engine_size_dupe.py
# enforces (800-line ceiling per engine file) that already produced
# column_render_data.sh / column_render_frame.sh / column_render_cells.sh
# as siblings of one former column-render.sh.
#
# Public entry points
# --------------------
#   column_render_defer_gather_input
#       For the single-project "input" mode. Reads $KANBAN_ROOT,
#       $_RENDER_PROJECT, and $INPUT_DIR from the caller's own shell (same
#       side-effect-on-caller-globals convention column_render_gather_data
#       itself uses for ACTIVE_RC/LAST_RELEASED/etc.) and sets three globals:
#         DEFER_MODE        "off" | "on" | "malformed"
#         DEFER_ERROR_FILE  the offending file's path (malformed only)
#         DEFER_IDS         newline-separated deferred bug IDs (on only)
#       All three are left at their off-mode defaults ("off", "", "") when
#       INPUT_DIR is not the bugs column (basename != "bugs") — dashboard_
#       defer_card_ids is not even called in that case, matching the "no
#       glyph outside the bugs column" contract every renderer relies on.
#
#   column_render_defer_multi_line <kanban_root> <project_name> <bugs_dir>
#       For the multi-project "multi" per-project gather loop. Echoes ONE
#       line: "mode<DFS_SEP>errfile<DFS_SEP>ids-joined-by-pipe" — the exact
#       line shape pgai_agent_kanban.dashboard.column_render.multi's
#       sources_from_env parses via parse_proj_defer_info() (team/
#       pgai_agent_kanban/dashboard/defer_card_glyph.py). Callers append this function's
#       output directly to their own PROJ_DEFER_INFO array entry.
#
# Dependencies: the caller must already have sourced
# team/scripts/dashboard/lib/defer_scope.sh (for dashboard_defer_card_ids
# and DFS_SEP) — column-render.sh, the shared driver both card renderers run
# under, sources it once at the top; this file does not re-source it itself
# to avoid a second opinion on load order.

# Guard against double-sourcing.
if declare -F column_render_defer_gather_input >/dev/null 2>&1; then
    return 0
fi

# column_render_defer_first_line <text>
#     Prints the first line of <text> (without its newline). Reads it with
#     mapfile from a here-string rather than piping printf into head -n1:
#     head exits as soon as it has its line, and when the text is larger
#     than the pipe buffer the still-writing printf takes SIGPIPE, which
#     pipefail turns into exit 141 and set -e into a dead renderer. A
#     here-string has no writer process for an early-closing reader to
#     kill, so the result is the same whatever the size of the card list.
column_render_defer_first_line() {
    local -a _crd_lines=()
    mapfile -t -n 1 _crd_lines <<< "$1"
    printf '%s' "${_crd_lines[0]-}"
}

column_render_defer_gather_input() {
    DEFER_MODE="off"
    DEFER_ERROR_FILE=""
    DEFER_IDS=""
    if [[ "$(basename "$INPUT_DIR")" == "bugs" ]]; then
        local _crd_out _crd_first
        _crd_out="$(dashboard_defer_card_ids "$KANBAN_ROOT" "$_RENDER_PROJECT" "$INPUT_DIR")"
        _crd_first="$(column_render_defer_first_line "$_crd_out")"
        DEFER_MODE="$(cut -d "$DFS_SEP" -f1 <<< "$_crd_first")"
        DEFER_ERROR_FILE="$(cut -d "$DFS_SEP" -f2 <<< "$_crd_first")"
        DEFER_IDS="$(printf '%s\n' "$_crd_out" | tail -n +2)"
    fi
}

column_render_defer_multi_line() {
    local kanban_root="$1" project_name="$2" bugs_dir="$3"
    local out first mode errfile ids_joined
    out="$(dashboard_defer_card_ids "$kanban_root" "$project_name" "$bugs_dir")"
    first="$(column_render_defer_first_line "$out")"
    mode="$(cut -d "$DFS_SEP" -f1 <<< "$first")"
    errfile="$(cut -d "$DFS_SEP" -f2 <<< "$first")"
    ids_joined="$(printf '%s\n' "$out" | tail -n +2 | tr '\n' '|')"
    printf '%s%s%s%s%s\n' "$mode" "$DFS_SEP" "$errfile" "$DFS_SEP" "$ids_joined"
}
