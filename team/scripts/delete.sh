#!/usr/bin/env bash
# team/scripts/delete.sh
# CLI wrapper: delete a task or intake item by key.
#
# Usage: delete.sh [OPTIONS]
#   --key KEY         Item key to delete (required): task folder name or
#                     intake file base name (e.g. BUG-NNNN, CODER-20260622-001-foo)
#   --project NAME    Project name (default: $PGAI_PROJECT_NAME)
#   --force           Override terminal-state guard and delete regardless of state
#   --dry-run         Print what would be deleted without removing anything
#   --help, -h        Show help and exit
#
# GUARD (the safety-critical property):
#   delete_item refuses to delete an item unless its state is DONE or WONT-DO.
#   Use --force to override. Use --dry-run to preview without side effects.
#
# KEY RESOLUTION:
#   The key is resolved against the project's tasks/ directory first, then
#   bugs/, priority/, and requirements/ (intake items).
#
# Thin adapter: parses arguments via operator_args.sh, resolves the project
# root via project_paths.sh, and delegates to delete_item in
# pgai_agent_kanban.ops.write (Python).  No mutation logic lives in this file.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_DELETE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DELETE_LIB_DIR="${_DELETE_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_DELETE_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_DELETE_LIB_DIR}/project_paths.sh"

# --- Source operator_ops.sh for the resolve_item shim path (dry-run preview) ---
# shellcheck source=lib/operator_ops.sh
source "${_DELETE_LIB_DIR}/operator_ops.sh"

# --- Source the shared Python invocation helper ---
# shellcheck source=lib/pp_run_ops.sh
source "${_DELETE_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project key force dry-run help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "delete.sh" \
        "Delete a task or intake item by key, with a terminal-state safety guard." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Guard: deletion is refused unless the item is in state DONE or WONT-DO." \
        "Use --force to bypass the guard (data-loss risk: no undo available)." \
        "Use --dry-run to preview the target before committing to deletion."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "delete.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project ---
_project_raw="$(operator_args_project)"
if [[ -z "${_project_raw}" ]]; then
    printf 'delete.sh: --project is required (or set $PGAI_PROJECT_NAME)\n' >&2
    exit 1
fi

# --- Resolve project name by exact match then unambiguous prefix ---
_project="$(pp_resolve_project_name "${_project_raw}")" || exit 1

# --- Validate --key (required) ---
_key="$(operator_args_get key)"
if [[ -z "${_key}" ]]; then
    printf 'delete.sh: --key is required\n' >&2
    exit 1
fi

# --- Resolve --force ---
_force="0"
if argparse_has force; then
    _force="1"
fi

# --- Resolve --dry-run ---
_dry_run=0
if argparse_has dry-run; then
    _dry_run=1
fi

# --- Resolve project root ---
_project_root="$(pp_project_root "${_project}")" || exit 1

# --- Dry-run mode: report what would be deleted without deleting ---
if [[ "${_dry_run}" -eq 1 ]]; then
    # Locate the item via the shared resolve_item shim -- the same
    # exact-then-prefix resolution delete_item itself uses -- rather than a
    # local ad-hoc exact-only lookup, so a dry-run preview and the real
    # delete always agree on which item a prefix key resolves to.
    _resolve_out=""
    _resolve_rc=0
    if _resolve_out="$(python3 "${_OPERATOR_OPS_SHIM}" "${_project_root}" "${_key}")"; then
        _resolve_rc=0
    else
        _resolve_rc=$?
    fi

    # rc=2 means "ambiguous match, resolved to first" -- the shim already
    # printed the warning and candidate list to stderr; dry-run previews the
    # first match the same way the real delete would proceed if re-run
    # without --dry-run. rc=3 (not found) and rc=1 (argument/filesystem
    # error) are genuine failures.
    if [[ "${_resolve_rc}" -ne 0 && "${_resolve_rc}" -ne 2 ]]; then
        if [[ "${_resolve_rc}" -eq 3 ]]; then
            printf 'delete.sh (dry-run): item not found for key: %s\n' "${_key}" >&2
        fi
        exit "${_resolve_rc}"
    fi

    _found_type="$(printf '%s\n' "${_resolve_out}" | sed -n '1p')"
    _found_path="$(printf '%s\n' "${_resolve_out}" | sed -n '2p')"
    _found_state="$(printf '%s\n' "${_resolve_out}" | sed -n '3p')"

    printf 'delete.sh (dry-run): would delete %s: %s (state: %s)\n' \
        "${_found_type}" "${_found_path}" "${_found_state}"
    if [[ "${_force}" == "1" ]]; then
        printf 'delete.sh (dry-run): --force is set — guard would be bypassed\n'
    fi
    exit 0
fi

# --- Execute the operation via the Python library ---
# pp_run_ops pgai_agent_kanban.ops delete_item PROJECT_ROOT KEY [FORCE]
# Exit codes: 0 success, 1 error, 2 guard refused, 3 not found, 4 filesystem error.
_rc=0
pp_run_ops pgai_agent_kanban.ops delete_item "${_project_root}" "${_key}" "${_force}" \
    || _rc=$?

case "${_rc}" in
    0)
        printf 'delete: item %s deleted\n' "${_key}"
        exit 0
        ;;
    1)
        # Argument/configuration error — stderr already printed by delete_item.
        exit 1
        ;;
    2)
        # Guard refused — stderr already printed by delete_item.
        exit 2
        ;;
    3)
        # Item not found — stderr already printed by delete_item.
        exit 3
        ;;
    4)
        # Filesystem error — stderr already printed by delete_item.
        exit 4
        ;;
    *)
        printf 'delete.sh: unexpected return code from delete_item: %d\n' "${_rc}" >&2
        exit 1
        ;;
esac
