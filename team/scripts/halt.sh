#!/usr/bin/env bash
# team/scripts/halt.sh
# CLI wrapper: create the HALT signal for a project.
#
# Usage: halt.sh [OPTIONS]
#   --project NAME    Project to halt (default: $PGAI_PROJECT_NAME)
#   --help, -h        Show help and exit
#
# Thin adapter: parses --project via operator_args.sh, resolves the project
# root via project_paths.sh, and delegates the on-disk change to the Python
# halt function in pgai_agent_kanban.ops.write.  No mutation logic lives here.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_HALT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HALT_LIB_DIR="${_HALT_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_HALT_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_HALT_LIB_DIR}/project_paths.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_HALT_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "halt.sh" \
        "Create the HALT signal for a project, stopping the wake loop cleanly." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "halt.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project ---
_project="$(operator_args_project)"
if [[ -z "${_project}" ]]; then
    printf 'halt.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Resolve project root path ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Execute the operation via Python library ---
if pp_run_ops pgai_agent_kanban.ops halt "${_project_root}"; then
    printf 'halt: HALT signal set for project %s (%s/HALT)\n' \
        "${_project}" "${_project_root}"
    exit 0
else
    printf 'halt: failed to set HALT signal for project %s\n' "${_project}" >&2
    exit 1
fi
