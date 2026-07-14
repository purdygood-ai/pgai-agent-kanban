#!/usr/bin/env bash
# team/scripts/show-test-report.sh
# CLI read command: print a TESTER verification report to stdout.
#
# Usage:
#   show-test-report.sh --project <name> --key <RC-version|TESTER-task-key> [--help]
#
# DESIGN: strictly read-only.  Resolves the key via the shared resolve_item
# shim (for TESTER task keys) or via a direct tasks/ scan (for vX.Y.Z keys),
# then prints the task's artifacts/report.md to stdout.  Never mutates any file.
#
# KEY RESOLUTION:
#   TESTER task key (TESTER-YYYYMMDD-NNN or a unique prefix thereof):
#     Delegates to the shared resolve_item shim; requires exactly one match.
#     Prints projects/<project>/tasks/<task>/artifacts/report.md.
#
#   RC version key (vX.Y.Z):
#     Scans tasks/ for TESTER tasks whose name encodes that version (the
#     naming convention is TESTER-YYYYMMDD-NNN-verify-X-Y-Z where dots
#     are replaced with dashes).  Selects the latest such task (highest
#     task number / most recent date) and prints its artifacts/report.md.
#     When multiple TESTER tasks verify the same RC, the highest task
#     number wins (the most recent re-run).
#
# Exit codes:
#   0   Report emitted to stdout successfully.
#   1   Usage error, missing argument, or configuration error.
#   2   Ambiguous key (multiple tasks matched via prefix; first match printed).
#   3   Key not found, task is not a TESTER task, or artifacts/report.md absent.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_SCRIPT_DIR}/lib"

# Source shared libraries.
# shellcheck source=lib/operator_args.sh
source "${_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/operator_ops.sh
source "${_LIB_DIR}/operator_ops.sh"
# shellcheck source=lib/project_paths.sh
source "${_LIB_DIR}/project_paths.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# show-test-report accepts: --project --key --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Print a TESTER verification report to stdout (read-only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Key resolution:" \
        "  TESTER-YYYYMMDD-NNN (or unique prefix):" \
        "    Resolves to that TESTER task via the shared resolver and" \
        "    prints its artifacts/report.md." \
        "" \
        "  vX.Y.Z (RC version, e.g. v1.0.0):" \
        "    Finds TESTER tasks whose name encodes that RC version" \
        "    (naming convention: TESTER-...-verify-X-Y-Z)." \
        "    Selects the latest task (highest task number / most recent)" \
        "    and prints its artifacts/report.md." \
        "" \
        "Exit codes:" \
        "  0  Report emitted to stdout" \
        "  1  Usage error or missing argument" \
        "  2  Ambiguous key (multiple tasks matched via prefix; first match printed)" \
        "  3  Key not found, no matching TESTER task, or report.md absent"
}

# ---------------------------------------------------------------------------
# Manual argument parsing.
# Mirrors the canonical operator vocabulary from show.sh.
#
# Pre-populate ARGPARSE_FLAGS for operator_args_validate_known, which requires
# argparse state to be set before it can check for unknown flags.
# ---------------------------------------------------------------------------
argparse_reset
argparse_parse --value-flags "project key" -- "$@"

_project_value=""
_key_value=""
_show_help=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _show_help=true
            shift
            ;;
        --project)
            [[ -z "${2:-}" ]] && { printf 'show-test-report.sh: --project requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show-test-report.sh: --key requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _key_value="$2"
            shift 2
            ;;
        *)
            printf 'show-test-report.sh: unknown argument: %s\n' "$1" >&2
            printf '\n' >&2
            _usage >&2
            exit 1
            ;;
    esac
done

# Show help early (after parsing so --help can appear anywhere).
if [[ "${_show_help}" == "true" ]]; then
    _usage
    exit 0
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Validate required flags.
# ---------------------------------------------------------------------------
_missing_flags=()
[[ -z "${_project_value}" ]] && { _project_value="${PGAI_PROJECT_NAME:-}"; }
[[ -z "${_project_value}" ]] && _missing_flags+=(--project)
[[ -z "${_key_value}" ]]     && _missing_flags+=(--key)

if [[ ${#_missing_flags[@]} -gt 0 ]]; then
    printf 'show-test-report.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
    printf '\n' >&2
    _usage >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve kanban root and project root.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

_project_root="$(pp_project_root "${_project_value}")" || exit 1

# ---------------------------------------------------------------------------
# Determine key type: RC version (vX.Y.Z) or TESTER task key.
# ---------------------------------------------------------------------------
# _rc_version_key returns 0 and populates _version_suffix when the key is
# a vX.Y.Z pattern; returns 1 otherwise.
# ---------------------------------------------------------------------------
_is_rc_version_key=false
_version_suffix=""   # will hold X-Y-Z (dots replaced by dashes)

if [[ "${_key_value}" =~ ^v([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    _is_rc_version_key=true
    _version_suffix="${BASH_REMATCH[1]}-${BASH_REMATCH[2]}-${BASH_REMATCH[3]}"
fi

# ---------------------------------------------------------------------------
# RC-version resolution path.
# Scan tasks/ for TESTER-*-verify-<X-Y-Z> directories, select the latest
# (highest task number / most recent date when task numbers are the same).
# ---------------------------------------------------------------------------
if [[ "${_is_rc_version_key}" == "true" ]]; then
    _tasks_dir="${_project_root}/tasks"

    if [[ ! -d "${_tasks_dir}" ]]; then
        printf 'show-test-report.sh: tasks directory not found: %s\n' "${_tasks_dir}" >&2
        exit 3
    fi

    # Collect all TESTER tasks that encode this RC version.
    # Naming convention: TESTER-YYYYMMDD-NNN-verify-X-Y-Z[-extra...]
    # We match on the -verify-<suffix> segment anywhere in the tail.
    _matching_tasks=()
    while IFS= read -r -d '' _entry; do
        _bn="$(basename "${_entry}")"
        # Must start with TESTER- and contain -verify-<version-suffix>.
        if [[ "${_bn}" == TESTER-* ]] && [[ "${_bn}" == *"-verify-${_version_suffix}"* || "${_bn}" == *"-verify-${_version_suffix}" ]]; then
            _matching_tasks+=("${_bn}")
        fi
    done < <(find "${_tasks_dir}" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null)

    if [[ ${#_matching_tasks[@]} -eq 0 ]]; then
        printf 'show-test-report.sh: no TESTER task found for RC version: %s\n' "${_key_value}" >&2
        exit 3
    fi

    # Select the latest: sort descending by the full task name (which encodes
    # date YYYYMMDD then sequence NNN), then pick the first (highest).
    # Bash sort: use printf + sort -r to get descending order.
    _latest_task="$(printf '%s\n' "${_matching_tasks[@]}" | sort -r | head -1)"
    _item_path="${_tasks_dir}/${_latest_task}"

    # Locate and emit artifacts/report.md.
    _report_file="${_item_path}/artifacts/report.md"

    if [[ ! -f "${_report_file}" ]]; then
        printf 'show-test-report.sh: report not found for task %s: %s\n' \
            "${_latest_task}" "${_report_file}" >&2
        exit 3
    fi

    cat "${_report_file}"
    exit 0
fi

# ---------------------------------------------------------------------------
# TESTER task key path: delegate to the Python resolver.
# Delegates to pgai_agent_kanban.ops.resolve via resolve_item_shim.py.
# Emits three lines on success: type, path, state.
# Returns 3 for not-found; 1 for argument/filesystem errors.
# Returns 2 for ambiguous match (multiple items matched the key prefix);
#   the first match (alphabetically) is still written to stdout.
# Errors go to stderr (pass-through); stdout carries the structured result.
# Use if-form to safely capture exit code when set -e is active.
# ---------------------------------------------------------------------------
_resolve_out=""
_resolve_rc=0
if _resolve_out="$(python3 "${_OPERATOR_OPS_SHIM}" "${_project_root}" "${_key_value}")"; then
    _resolve_rc=0
else
    _resolve_rc=$?
fi

if [[ "${_resolve_rc}" -eq 3 ]]; then
    printf 'show-test-report.sh: key not found: %s\n' "${_key_value}" >&2
    exit 3
fi

if [[ "${_resolve_rc}" -ne 0 && "${_resolve_rc}" -ne 2 ]]; then
    exit "${_resolve_rc}"
fi

# Parse the three-line result.
_item_type="$(printf '%s\n' "${_resolve_out}" | sed -n '1p')"
_item_path="$(printf '%s\n' "${_resolve_out}" | sed -n '2p')"
# _item_state is line 3 (not needed — we emit the report file).

# ---------------------------------------------------------------------------
# Verify the resolved item is a task (not an intake item).
# ---------------------------------------------------------------------------
if [[ "${_item_type}" != "task" ]]; then
    printf 'show-test-report.sh: key does not resolve to a task: %s\n' "${_key_value}" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# Verify the task is a TESTER task (basename must start with TESTER-).
# ---------------------------------------------------------------------------
_task_basename="$(basename "${_item_path}")"
if [[ "${_task_basename}" != TESTER-* ]]; then
    printf 'show-test-report.sh: resolved task is not a TESTER task: %s\n' "${_task_basename}" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# Locate and emit artifacts/report.md.
# ---------------------------------------------------------------------------
_report_file="${_item_path}/artifacts/report.md"

if [[ ! -f "${_report_file}" ]]; then
    printf 'show-test-report.sh: report not found for task %s: %s\n' \
        "${_task_basename}" "${_report_file}" >&2
    exit 3
fi

cat "${_report_file}"

# Propagate the ambiguous-match exit code so callers can distinguish
# "found one" (0) from "found multiple, used first" (2).
exit "${_resolve_rc}"
