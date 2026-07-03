#!/usr/bin/env bash
# team/scripts/unhalt.sh
# CLI wrapper: remove the HALT signal for a project.
#
# Usage: unhalt.sh [OPTIONS]
#   --project NAME    Project to unhalt (default: $PGAI_PROJECT_NAME)
#   --help, -h        Show help and exit
#
# Thin adapter: parses --project via operator_args.sh, resolves the project
# root via project_paths.sh, and delegates the on-disk change to the Python
# unhalt function in pgai_agent_kanban.ops.write.  No mutation logic lives here.

set -euo pipefail

# --- Locate the lib directory relative to this script ---
_UNHALT_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_UNHALT_LIB_DIR="${_UNHALT_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_UNHALT_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_UNHALT_LIB_DIR}/project_paths.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "unhalt.sh" \
        "Remove the HALT signal for a project, allowing the wake loop to resume." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "unhalt.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
export KANBAN_ROOT

# --- Resolve project ---
_project="$(operator_args_project)"
if [[ -z "${_project}" ]]; then
    printf 'unhalt.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Resolve project root path ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Execute the operation via Python library ---
if python3 -m pgai_agent_kanban.ops unhalt "${_project_root}"; then
    printf 'unhalt: HALT signal cleared for project %s (%s/HALT removed)\n' \
        "${_project}" "${_project_root}"
    exit 0
else
    printf 'unhalt: failed to clear HALT signal for project %s\n' "${_project}" >&2
    exit 1
fi
