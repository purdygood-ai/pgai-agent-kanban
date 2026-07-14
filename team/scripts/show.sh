#!/usr/bin/env bash
# team/scripts/show.sh
# CLI read dispatcher: emit a task or intake item's content to stdout.
#
# Usage:
#   show.sh --project <name> --key <key> [--file <status|readme>] [--help]
#
# DESIGN: strictly read-only. Resolves the key via the shared resolve_item
# function (same resolution as delete/reset/close) and emits the relevant
# file to stdout.  Never mutates any file.
#
# KEY RESOLUTION (via resolve_item):
#   1. tasks/<KEY>/         — task directory (status.md or README.md, via --file)
#   2. bugs/<KEY>.md        — bug intake item (emits the .md directly)
#   3. priority/<KEY>.md    — priority intake item (emits the .md directly)
#   4. requirements/<KEY>.md — requirement intake item (emits the .md directly)
#
# Task key behavior:
#   --file status (default) — emit tasks/<KEY>/status.md
#   --file readme           — emit tasks/<KEY>/README.md
#
# Intake key behavior:
#   Emits the intake .md file directly (no --file selection needed).
#   If --file is supplied for an intake key it is silently ignored.
#
# Optional flags:
#   --help, -h      Show help and exit 0.
#
# Exit codes:
#   0   Content emitted to stdout successfully.
#   1   Usage error, missing argument, or configuration error.
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
# show accepts: --project --key --file --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key file help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Emit a task or intake item's content to stdout (read-only)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "         value: status (default) or readme" \
        "                status -> tasks/<KEY>/status.md" \
        "                readme -> tasks/<KEY>/README.md" \
        "                Ignored for intake keys (single-file items)." \
        "" \
        "Behavior:" \
        "  Task key:   emits status.md (default) or README.md via --file" \
        "  Intake key: emits the intake .md file directly" \
        "  Unknown key: non-zero exit with a clear error message" \
        "" \
        "Exit codes:" \
        "  0  Content emitted to stdout" \
        "  1  Usage error or missing argument" \
        "  3  Key not found"
}

# ---------------------------------------------------------------------------
# Manual argument parsing.
# Extends the canonical operator vocabulary with --file (value flag).
# We parse manually because operator_args.sh's canonical value-flag set
# does not include --file; we need to handle it explicitly.
#
# Pre-populate ARGPARSE_FLAGS for operator_args_validate_known, which requires
# argparse state to be set before it can check for unknown flags.
# ---------------------------------------------------------------------------
argparse_reset
argparse_parse --value-flags "project key file" -- "$@"

_project_value=""
_key_value=""
_file_value="status"   # default: emit status.md
_show_help=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _show_help=true
            shift
            ;;
        --project)
            [[ -z "${2:-}" ]] && { printf 'show.sh: --project requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show.sh: --key requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _key_value="$2"
            shift 2
            ;;
        --file)
            [[ -z "${2:-}" ]] && { printf 'show.sh: --file requires a value (status|readme)\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _file_value="$2"
            shift 2
            ;;
        *)
            printf 'show.sh: unknown argument: %s\n' "$1" >&2
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
    printf 'show.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
    printf '\n' >&2
    _usage >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate --file value (only status or readme are accepted).
# ---------------------------------------------------------------------------
case "${_file_value}" in
    status|readme)
        ;;
    *)
        printf 'show.sh: --file value must be "status" or "readme"; got: %s\n' "${_file_value}" >&2
        exit 1
        ;;
esac

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
#   the first match (alphabetically) is still written to stdout; show may proceed.
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

# rc=2 means "ambiguous match, resolved to first" — show treats this as success
# (warning was already emitted to stderr by the shim).
# All other non-zero codes are genuine failures.
if [[ "${_resolve_rc}" -ne 0 && "${_resolve_rc}" -ne 2 ]]; then
    if [[ "${_resolve_rc}" -eq 3 ]]; then
        printf 'show.sh: key not found: %s\n' "${_key_value}" >&2
    fi
    exit "${_resolve_rc}"
fi

# Parse the three-line result.
_item_type="$(printf '%s\n' "${_resolve_out}" | sed -n '1p')"
_item_path="$(printf '%s\n' "${_resolve_out}" | sed -n '2p')"
# _item_state is line 3 (not needed for show — we just emit the file).

# ---------------------------------------------------------------------------
# Emit the content based on item type.
# ---------------------------------------------------------------------------
if [[ "${_item_type}" == "task" ]]; then
    # Task: emit the selected file.
    case "${_file_value}" in
        status)
            _target_file="${_item_path}/status.md"
            ;;
        readme)
            _target_file="${_item_path}/README.md"
            ;;
    esac

    if [[ ! -f "${_target_file}" ]]; then
        printf 'show.sh: %s not found for task %s: %s\n' \
            "${_file_value}.md" "${_key_value}" "${_target_file}" >&2
        exit 1
    fi

    cat "${_target_file}"

else
    # Intake item (bug, priority, requirement): emit the .md file directly.
    # _item_path is already the absolute path to the .md file.
    if [[ ! -f "${_item_path}" ]]; then
        printf 'show.sh: intake file not found: %s\n' "${_item_path}" >&2
        exit 1
    fi

    cat "${_item_path}"
fi

exit 0
