#!/usr/bin/env bash
# show-queues.sh
# Renders the queue state pane (left ~50%).
#
# Example output (no flags):
#   pm:     1/1 done
#   coder:  4/7 done, 1 working
#   writer: 0/0
#   tester: 0/1 (waiting)
#   cm:     0/2 (waiting)
#
#   — currently working —
#   CODER-...-bug1-cleanup
#     started: 18:35:14
#     elapsed: 7m 17s
#
# Example output (--details):
#   === coder ===
#   [WORKING]
#     CODER-...-task1
#   [BLOCKED]
#     CODER-...-task2
#   [BACKLOG]
#     CODER-...-task3
#
# Usage:
#   show-queues.sh [--project <name>] [--details] [--kanban-root <path>] [--help]
#
# Read-only operator verb: prints the same queue summary the dashboard's
# queues pane renders, so it can be run outside tmux. The dashboard pane
# (show-multi.sh --mode queues, and each drill-N pane) and why.sh --details
# both call this exact script rather than carrying their own copy of the
# rendering.
#
# This script is a thin wrapper: it parses flags, resolves --project, and
# hands off to pgai_agent_kanban.ops.read.get_queues through pp_run_ops
# ("pp_run_ops pgai_agent_kanban.ops get_queues ..."), which reads the
# project's queue state and prints the pane through
# pgai_agent_kanban.dashboard.queue_pane. GET /show-queues calls the same
# get_queues function and the same renderer, so the REST envelope's stdout
# is this pane.
#
# Flags:
#   --project <name>    Project to render. Compact mode: the per-agent
#                        counters and the currently-working task come from
#                        this project -- or, when omitted, from the project
#                        named by PGAI_PROJECT_NAME; with neither, the
#                        script refuses (show_queues.no_project) rather than
#                        print counts it did not read. --details mode:
#                        iterates every registered project when omitted.
#   --details            Per-agent, per-state task listing instead of the
#                        compact one-line-per-agent summary.
#   --kanban-root <path> Override the kanban root path (default:
#                        $PGAI_AGENT_KANBAN_ROOT_PATH).
#   --help, -h           Show this help and exit 0.
#
# Exit codes:
#   0   Queue summary emitted to stdout.
#   1   Usage error (an undeclared flag was supplied); --project names
#       no registered project / no project directory; or, in compact mode,
#       neither --project nor PGAI_PROJECT_NAME names a project -- including
#       on a single-project root with no projects.cfg, whose
#       <root>/tasks/queues/ the board does not read (dual-channel refusal
#       on stderr, nothing on stdout).
#
# Configuration (kanban.cfg [dashboard], overridden by the environment):
#   color_done    / PGAI_DASHBOARD_COLOR_DONE     — done count color (default: green)
#   color_working / PGAI_DASHBOARD_COLOR_WORKING  — working indicator color (default: yellow)
#   color_blocked / PGAI_DASHBOARD_COLOR_BLOCKED  — blocked indicator color (default: red)
#   color_waiting / PGAI_DASHBOARD_COLOR_WAITING  — waiting indicator color (default: yellow)
#   color_label   / PGAI_DASHBOARD_COLOR_LABEL    — agent label color (default: cyan)
#   PGAI_DASHBOARD_COLOR_HALT                     — --details header color when halted (default: red)
#   PGAI_AGENT_PANE_WIDTH                         — compact row width (default: 36)
#
# Environment:
#   TERM=dumb, NO_COLOR=1  — disable all ANSI codes

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_resolve_project_name (and, through it, the
# shared dual-channel error emitter).
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source the shared operator argument layer: operator_args_parse,
# argparse_has, operator_args_validate_known, and
# operator_args_render_help_for_flags. Brings show-queues.sh into the same
# declared-flag-vocabulary / uniform-unknown-flag-refusal contract every
# other operator verb uses (team/scripts/show.sh is the model read verb).
# shellcheck source=../lib/operator_args.sh
source "${SCRIPT_DIR}/../lib/operator_args.sh"
# Source pp_run_ops: the cwd-independent bridge to the
# pgai_agent_kanban.ops package CLI (verb get_queues).
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# --- Declared flag vocabulary for this script ---
OPERATOR_VALID_FLAGS=(details project kanban-root help)

# --kanban-root is outside the canonical vocabulary, so its help line is given
# here (--details is described by the mode line in the help text below).
declare -A OPERATOR_FLAG_HELP=(
    [kanban-root]="  --kanban-root PATH Override the kanban root path (default: \$PGAI_AGENT_KANBAN_ROOT_PATH)"
)

# --- Parse args ---
# --kanban-root is dashboard-internal plumbing, not part of the canonical
# operator_args.sh value-flag set, so it is added for this parse only via
# --extra-value-flags (see operator_args_parse's own header for the
# contract).
operator_args_parse --extra-value-flags "kanban-root" "$@"

# --- Handle --help (before any unknown-flag rejection, matching show.sh /
# why.sh: --help always succeeds even alongside other flags) ---
if argparse_has help; then
    operator_args_render_help_for_flags "$(basename "$0")" \
        "Render the queue-state pane for one or every registered project (read-only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "No flags:   compact one-line-per-agent queue summary (pm/coder/writer/" \
        "            tester/cm) plus the currently-working task, if any." \
        "--details:  per-agent, per-state task listing for --project, or every" \
        "            registered project in turn when --project is omitted." \
        "" \
        "This is the same renderer the dashboard's queues pane and why.sh" \
        "--details call -- there is exactly one implementation." \
        "" \
        "Exit codes:" \
        "  0  Queue summary emitted to stdout" \
        "  1  Usage error (an undeclared flag was supplied), --project names" \
        "     no registered project, or (compact mode) neither --project nor" \
        "     PGAI_PROJECT_NAME names one -- refusal on stderr, nothing on stdout"
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

SHOW_DETAILS=0
argparse_has details && SHOW_DETAILS=1
PROJECT_NAME="$(operator_args_get project)"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_kanban_root_flag="$(operator_args_get kanban-root)"
[[ -n "${_kanban_root_flag}" ]] && KANBAN_ROOT="${_kanban_root_flag}"
export KANBAN_ROOT

# --- Resolve a supplied --project by exact match then unambiguous prefix.
# An ABSENT --project is a legitimate mode in both compact rendering (the
# counters come from PGAI_PROJECT_NAME's project; with that unset too,
# get_queues refuses show_queues.no_project) and details rendering (every
# registered project), so only a non-empty value is routed through the
# resolver. A name that does not resolve refuses here,
# dual-channel, with no board rendered (BLOCK-WORTHY per
# pp_resolve_project_name's own header in lib/project_paths.sh -- this call
# site adds no local refusal of its own). On a root with no projects.cfg
# the resolver passes the name through unchecked; get_queues then refuses a
# project directory that does not exist (show_queues.project_directory_missing),
# again before anything reaches stdout. ---
if [[ -n "$PROJECT_NAME" ]]; then
    PROJECT_NAME="$(pp_resolve_project_name "$PROJECT_NAME")" || exit 1
fi

set -euo pipefail

# --- Render: pgai_agent_kanban.ops.read.get_queues + dashboard.queue_pane ---
# Arguments: KANBAN_ROOT, the resolved --project (or ""), the project the
# compact counters and the --details header halt color read from (--project,
# else PGAI_PROJECT_NAME, else "" -- which compact mode refuses), and 1 for
# --details. Any refusal from get_queues exits 1 here, nothing on stdout.
pp_run_ops pgai_agent_kanban.ops get_queues \
    "$KANBAN_ROOT" "$PROJECT_NAME" "${PROJECT_NAME:-${PGAI_PROJECT_NAME:-}}" "$SHOW_DETAILS" || exit 1
