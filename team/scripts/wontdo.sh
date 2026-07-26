#!/usr/bin/env bash
# team/scripts/wontdo.sh
# CLI wrapper: mark a task as WONT-DO.
#
# Usage: wontdo.sh [OPTIONS]
#   --key TASK_ID     Task ID (the folder name under tasks/) — required
#   --project NAME    Project name (default: $PGAI_PROJECT_NAME)
#   --help, -h        Show help and exit
#
# Thin adapter: parses --key and --project via operator_args.sh,
# resolves the project's tasks directory via project_paths.sh, and delegates
# the state mutation to wontdo_item in pgai_agent_kanban.ops.write (Python).
# No mutation logic lives in this file.
#
# This wrapper produces WONT-DO only.  It cannot set DONE under any argument
# combination.  It is the operator's abandon verb: use it to retire a task
# that will not be worked rather than marking it complete.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_WONTDO_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_WONTDO_LIB_DIR="${_WONTDO_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_WONTDO_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_WONTDO_LIB_DIR}/project_paths.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_WONTDO_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project key help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "wontdo.sh" \
        "Mark a task as WONT-DO, retiring it cleanly without marking it DONE." \
        OPERATOR_VALID_FLAGS \
        "" \
        "This script produces WONT-DO only and cannot set DONE through any" \
        "argument combination.  Use close.sh to mark a task as DONE."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "wontdo.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project ---
_project="$(operator_args_project)"
if [[ -z "${_project}" ]]; then
    printf 'wontdo.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Validate --key ---
_key="$(operator_args_get key)"
if [[ -z "${_key}" ]]; then
    printf 'wontdo.sh: --key is required\n' >&2
    exit 1
fi

# --- Resolve project root ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Execute the operation via the Python library ---
# pp_run_ops pgai_agent_kanban.ops wontdo_item PROJECT_ROOT KEY
# Exit codes: 0 success, 1 error, 2 ambiguous, 3 not found, 4 mutation failed.
_wontdo_rc=0
pp_run_ops pgai_agent_kanban.ops wontdo_item "${_project_root}" "${_key}" \
    || _wontdo_rc=$?

case "${_wontdo_rc}" in
    0)
        printf 'wontdo: task %s marked WONT-DO\n' "${_key}"
        exit 0
        ;;
    2)
        printf 'wontdo: ambiguous task prefix %s — multiple matches; task NOT marked WONT-DO\n' \
            "${_key}" >&2
        exit 2
        ;;
    *)
        printf 'wontdo: failed to mark task %s as WONT-DO (rc=%d)\n' \
            "${_key}" "${_wontdo_rc}" >&2
        exit "${_wontdo_rc}"
        ;;
esac
