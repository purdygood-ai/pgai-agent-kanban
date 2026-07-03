#!/usr/bin/env bash
# team/scripts/halt-after.sh
# CLI wrapper: arm the HALT-AFTER soft-drain signal for a project.
#
# Usage: halt-after.sh [OPTIONS]
#   --project NAME    Project to arm (default: $PGAI_PROJECT_NAME)
#   --key TOKEN       Drain event token (default: rc)
#   --help, -h        Show help and exit
#
# Thin adapter: parses --project and --key via operator_args.sh, resolves
# the project root via project_paths.sh, and delegates the on-disk change
# to the Python halt_after function in pgai_agent_kanban.ops.write.
# No mutation logic lives in this file.
#
# HALT-AFTER semantics:
#   The HALT-AFTER file is evaluated by the Python halt_after module each
#   wake cycle.  When the drain condition for TOKEN is satisfied, the module
#   promotes the sentinel to a hard HALT.  The wake loop does NOT check
#   HALT-AFTER directly.
#
#   Supported tokens:
#     rc      — drain after current RC ships (default)
#     pm      — drain after current PM task completes
#     coder   — drain after current CODER task completes
#     writer  — drain after current WRITER task completes
#     tester  — drain after current TESTER task completes
#     cm      — drain after current CM task completes

set -euo pipefail

# --- Locate the lib directory relative to this script ---
_HALT_AFTER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HALT_AFTER_LIB_DIR="${_HALT_AFTER_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_HALT_AFTER_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_HALT_AFTER_LIB_DIR}/project_paths.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project key help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "halt-after.sh" \
        "Arm the HALT-AFTER soft-drain signal for a project." \
        OPERATOR_VALID_FLAGS \
        "" \
        "The --key flag sets the drain event token (default: rc)." \
        "Supported tokens: rc  pm  coder  writer  tester  cm"
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "halt-after.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
export KANBAN_ROOT

# --- Resolve project ---
_project="$(operator_args_project)"
if [[ -z "${_project}" ]]; then
    printf 'halt-after.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Resolve drain token (--key flag, default: rc) ---
_token="$(operator_args_get key)"
_token="${_token:-rc}"

# --- Resolve project root path ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Execute the operation via Python library ---
if python3 -m pgai_agent_kanban.ops halt_after "${_project_root}" "${_token}"; then
    printf 'halt-after: HALT-AFTER signal armed for project %s (token: %s, path: %s/HALT-AFTER)\n' \
        "${_project}" "${_token}" "${_project_root}"
    exit 0
else
    printf 'halt-after: failed to arm HALT-AFTER signal for project %s\n' "${_project}" >&2
    exit 1
fi
