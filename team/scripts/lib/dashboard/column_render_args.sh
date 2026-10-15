#!/usr/bin/env bash
# team/scripts/lib/dashboard/column_render_args.sh
#
# column-render.sh's argument parser, as a function that only records what
# it read, so the driver and its board fast path (column_render_board_serve.sh)
# share one reading of the command line. Sourcing only defines functions.
# The accepted arguments are documented in column-render.sh's own header.
#
# Public entry point
# -------------------
#   column_render_parse_args "$@"
#       Sets, for the caller:
#         KANBAN_ROOT     --kanban-root's value (default:
#                         $PGAI_AGENT_KANBAN_ROOT_PATH; a trailing
#                         --kanban-root with no value keeps the default)
#         PANE_LABEL      --label's value ("" when absent)
#         ALL_PROJECTS    "true" with --all-projects, else "false"
#         COLUMN_TYPE     --column-type's value; when absent under
#                         --all-projects, the board column named by the
#                         label (case-insensitive), else ""
#         BOARD_WIDTHS    --board-widths's value ("" when absent)
#         POSARGS         the positional arguments, in order
#         SUBCOMMAND      POSARGS[0] ("" when there is none)
#         COLUMN_RENDER_SHOW_HELP   "true" when -h/--help came before any
#                                   unknown option, else "false"
#         COLUMN_RENDER_ARG_ERROR   the error message column-render.sh
#                                   prints for an unknown option, a missing
#                                   subcommand or an unknown subcommand
#                                   ("" when the arguments are valid)
#       Parsing stops at the first -h/--help or unknown option, as the
#       driver's inline parser did. Never exits and prints nothing.

column_render_parse_args() {
    KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
    PANE_LABEL=""
    ALL_PROJECTS=false
    COLUMN_TYPE=""
    BOARD_WIDTHS=""
    POSARGS=()
    SUBCOMMAND=""
    COLUMN_RENDER_SHOW_HELP=false
    COLUMN_RENDER_ARG_ERROR=""

    local -a _args=("$@")
    local _i=0 _next arg
    while [[ $_i -lt ${#_args[@]} ]]; do
        arg="${_args[$_i]}"
        case "$arg" in
            -h|--help)
                COLUMN_RENDER_SHOW_HELP=true
                return 0
                ;;
            --kanban-root)
                _next=$(( _i + 1 ))
                KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
                _i=$_next
                ;;
            --label)
                _next=$(( _i + 1 ))
                PANE_LABEL="${_args[$_next]:-}"
                _i=$_next
                ;;
            --all-projects)
                ALL_PROJECTS=true
                ;;
            --column-type)
                _next=$(( _i + 1 ))
                COLUMN_TYPE="${_args[$_next]:-}"
                _i=$_next
                ;;
            --board-widths)
                _next=$(( _i + 1 ))
                BOARD_WIDTHS="${_args[$_next]:-}"
                _i=$_next
                ;;
            -*)
                COLUMN_RENDER_ARG_ERROR="ERROR: Unknown option: $arg"
                return 0
                ;;
            *)
                POSARGS+=("$arg")
                ;;
        esac
        _i=$(( _i + 1 ))
    done

    if [[ ${#POSARGS[@]} -lt 1 ]]; then
        COLUMN_RENDER_ARG_ERROR="ERROR: subcommand required: 'input' or 'queue'"
        return 0
    fi
    SUBCOMMAND="${POSARGS[0]}"
    if [[ "$SUBCOMMAND" != "input" && "$SUBCOMMAND" != "queue" ]]; then
        COLUMN_RENDER_ARG_ERROR="ERROR: Unknown subcommand '${SUBCOMMAND}'. Use 'input' or 'queue'."
        return 0
    fi

    # Auto-detect COLUMN_TYPE from PANE_LABEL when unset and --all-projects is
    # active, so callers need not supply both --label and --column-type.
    if [[ "$ALL_PROJECTS" == "true" && -z "$COLUMN_TYPE" && -n "$PANE_LABEL" ]]; then
        case "${PANE_LABEL,,}" in
            bugs)         COLUMN_TYPE="bugs" ;;
            priorities)   COLUMN_TYPE="priorities" ;;
            requirements) COLUMN_TYPE="requirements" ;;
            pm)           COLUMN_TYPE="pm" ;;
            coder)        COLUMN_TYPE="coder" ;;
            writer)       COLUMN_TYPE="writer" ;;
            tester)       COLUMN_TYPE="tester" ;;
            cm)           COLUMN_TYPE="cm" ;;
            *)            COLUMN_TYPE="" ;;
        esac
    fi
    return 0
}
