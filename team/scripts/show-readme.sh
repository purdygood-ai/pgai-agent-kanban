#!/usr/bin/env bash
# team/scripts/show-readme.sh
# CLI read dispatcher: emit a task's README.md to stdout.
#
# Usage:
#   show-readme.sh --project <name> --key <task-key> [--help]
#
# DESIGN: strictly read-only. Resolves the key via the shared resolve_item
# function (same resolution as show.sh, delete, reset, close) and emits the
# task's README.md to stdout. Task keys only — a ledger key (bug, priority,
# or requirement) is refused with a prescriptive line directing the operator
# to show.sh, which already prints that key's document.
#
# KEY RESOLUTION (via resolve_item):
#   1. tasks/<KEY>/         — task directory (README.md is emitted)
#   2. bugs/<KEY>.md        — refused: not a task
#   3. priority/<KEY>.md    — refused: not a task
#   4. requirements/<KEY>.md — refused: not a task
#
# Optional flags:
#   --help, -h      Show help and exit 0.
#
# Exit codes:
#   0   README.md emitted to stdout successfully.
#   1   Usage error, missing argument, configuration error, or the key
#       resolved to a ledger item (not a task).
#   3   Key not found in tasks/, bugs/, priority/, requirements/.

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
# show-readme accepts: --project --key --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Emit a task's README.md to stdout (read-only, task keys only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Behavior:" \
        "  Task key:   emits tasks/<KEY>/README.md" \
        "  Ledger key (bug/priority/requirement): refused — READMEs are task" \
        "              artifacts; run show.sh for that key's document" \
        "  Unknown key: non-zero exit with a clear error message" \
        "" \
        "Exit codes:" \
        "  0  README.md emitted to stdout" \
        "  1  Usage error, missing argument, or key resolved to a ledger item" \
        "  3  Key not found"
}

# ---------------------------------------------------------------------------
# Manual argument parsing.
# Mirrors show.sh's parsing shape (this script's flag set is a strict subset
# of show.sh's: no --file, since README.md is the only file this script
# ever emits).
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
            [[ -z "${2:-}" ]] && { printf 'show-readme.sh: --project requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show-readme.sh: --key requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _key_value="$2"
            shift 2
            ;;
        *)
            printf 'show-readme.sh: unknown argument: %s\n' "$1" >&2
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
    printf 'show-readme.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
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
# Resolve the item via the Python resolver.
# Delegates to pgai_agent_kanban.ops.resolve via resolve_item_shim.py.
# Emits three lines on success: type, path, state.
# Returns 3 for not-found; 1 for argument/filesystem errors.
# Returns 2 for ambiguous match (multiple items matched the key prefix);
#   the first match (alphabetically) is still written to stdout; show-readme
#   may proceed.
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

# rc=2 means "ambiguous match, resolved to first" — show-readme treats this
# as success (warning was already emitted to stderr by the shim).
# All other non-zero codes are genuine failures.
if [[ "${_resolve_rc}" -ne 0 && "${_resolve_rc}" -ne 2 ]]; then
    if [[ "${_resolve_rc}" -eq 3 ]]; then
        printf 'show-readme.sh: key not found: %s\n' "${_key_value}" >&2
    fi
    exit "${_resolve_rc}"
fi

# Parse the three-line result.
_item_type="$(printf '%s\n' "${_resolve_out}" | sed -n '1p')"
_item_path="$(printf '%s\n' "${_resolve_out}" | sed -n '2p')"
# _item_state is line 3 (not needed for show-readme — we just emit the file).

# ---------------------------------------------------------------------------
# Task keys only: refuse ledger items (bug, priority, requirement) with a
# prescriptive line pointing the operator back to show.sh for that key's
# document.
# ---------------------------------------------------------------------------
if [[ "${_item_type}" != "task" ]]; then
    printf 'show-readme.sh: %s is a %s item, not a task — READMEs are task artifacts.\n' \
        "${_key_value}" "${_item_type}" >&2
    printf 'show.sh already prints %s'"'"'s document: show.sh --project %s --key %s\n' \
        "${_key_value}" "${_project_value}" "${_key_value}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Emit the task's README.md.
# ---------------------------------------------------------------------------
_target_file="${_item_path}/README.md"

if [[ ! -f "${_target_file}" ]]; then
    printf 'show-readme.sh: README.md not found for task %s: %s\n' \
        "${_key_value}" "${_target_file}" >&2
    exit 1
fi

cat "${_target_file}"

exit 0
