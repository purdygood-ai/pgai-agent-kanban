#!/usr/bin/env bash
# kanban-status.sh
# One-shot terminal status view (no tmux required).
#
# Example output:
#   pgai-kanban v0.15.5    RC: v0.15.5 (15m)    Last: v0.15.4
#   HALT: off    Working: CODER-...-005-model-resolution (3m 47s)
#
#   Queue:
#     pm:     1/1 done
#     coder:  3/5 done, 1 working
#     writer: 0/0
#     tester: 0/1 waiting
#     cm:     1/2 done, waiting
#
#   Progress: [▓▓▓▓▓▓░░░░░░░░] 45%
#
#   (no blocked tasks)
#
#   Quarantined: 0
#   Ship posture: matrix
#
# The last line is the project's resolved [release] ship_posture, matrix or
# always, after the remote-lane guard: a project that pushes to origin with
# always set but not confirmed by ship_posture_confirm_remote = true shows
# matrix, and the resolver's warning line goes to stderr (exit stays 0).
#
# Usage:
#   kanban-status.sh --project <name> [--kanban-root <path>] [--no-color]
#
# Flags:
#   --project <name>      Project name (required when PGAI_PROJECT_NAME is not set)
#   --no-color            Disable ANSI color codes
#   --kanban-root <path>  Override kanban root path
#   --help, -h            Print this usage and exit 0
#
# Environment:
#   PGAI_PROJECT_NAME    Project name (alternative to --project)
#   TERM=dumb            Disables all ANSI codes
#   NO_COLOR=1           Disables all ANSI codes

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# A thin wrapper over pgai_agent_kanban.ops.read.get_status (reached through
# pp_run_ops) -- the function GET /status calls too. It reads the version,
# release state, halt flag, task counts, blocked tasks, quarantined count and
# resolved ship posture for one project and prints the summary above.
# Nothing is written.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"
# shellcheck source=lib/header_help.sh
source "${SCRIPT_DIR}/lib/header_help.sh"

# --- Parse args ---
# First pass, over every argument: --no-color and --help/-h are this
# script's own. Second pass, over the rest: --project and --kanban-root take
# the next argument (the last occurrence wins); anything else is ignored.
NO_COLOR_FLAG=0
DATA_ARGS=()
for arg in "$@"; do
  if [[ "$arg" == "--no-color" ]]; then
    NO_COLOR_FLAG=1
  elif [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    header_help_print "${BASH_SOURCE[0]}"
    exit 0
  else
    DATA_ARGS+=("$arg")
  fi
done

PROJECT_ARG=""
KANBAN_ROOT_ARG=""
_i=0
while [[ $_i -lt ${#DATA_ARGS[@]} ]]; do
  case "${DATA_ARGS[$_i]}" in
    --project)     PROJECT_ARG="${DATA_ARGS[$((_i + 1))]:-}"; _i=$((_i + 2)) ;;
    --kanban-root) KANBAN_ROOT_ARG="${DATA_ARGS[$((_i + 1))]:-}"; _i=$((_i + 2)) ;;
    *)             _i=$((_i + 1)) ;;
  esac
done

KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"
export KANBAN_ROOT

# An empty project (no --project, no PGAI_PROJECT_NAME) is refused by the
# function itself, exit 1, naming both ways to supply one.
pp_run_ops pgai_agent_kanban.ops get_status \
  "${KANBAN_ROOT}" "${PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}" "${NO_COLOR_FLAG}"
