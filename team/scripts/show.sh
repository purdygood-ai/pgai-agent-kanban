#!/usr/bin/env bash
# team/scripts/show.sh
# CLI read dispatcher: emit a task or intake item's content to stdout.
#
# Usage:
#   show.sh --project <name> --key <key> [--file <status|readme>] [--help]
#
# DESIGN: strictly read-only. A thin wrapper over
# pgai_agent_kanban.ops.read.get_task_status (reached through pp_run_ops) --
# the function GET /show calls too -- which resolves the key with the shared
# resolve_item function (same resolution as delete/reset/close) and emits the
# relevant file to stdout.  Never mutates any file.
#
# KEY RESOLUTION (via resolve_item):
#   1. tasks/<KEY>/         — task directory (status.md or README.md, via --file)
#   2. bugs/<KEY>.md        — bug intake item (emits the .md directly)
#   3. priority/<KEY>.md    — priority intake item (emits the .md directly)
#   4. requirements/<KEY>.md — requirement intake item (emits the .md directly)
#
# ARCHIVED ITEMS: a key matching nothing in the live project is looked up the
# same way (same four steps, exact name then unambiguous prefix) in the
# project's archive, $PGAI_AGENT_KANBAN_ROOT_PATH/archive/<project>/. An item
# found there is emitted unchanged on stdout, and stderr carries one line
# marking it: "archived: <KEY> is not in the live project; read from the
# archive: <path>" (exit 0). A live item always wins over an archived one.
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
#   3   Key not found in tasks/, bugs/, priority/, requirements/, live or archived.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_SCRIPT_DIR}/lib"

# Source shared libraries.
# shellcheck source=lib/operator_args.sh
source "${_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/project_paths.sh
source "${_LIB_DIR}/project_paths.sh"
# shellcheck source=lib/script_common.sh
source "${_LIB_DIR}/script_common.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# show accepts: --project --key --file --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key file help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract).
_USAGE_TEXT="$(operator_args_render_help_for_flags \
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
    "  3  Key not found")"

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
            [[ -z "${2:-}" ]] && { printf 'show.sh: --project requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show.sh: --key requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _key_value="$2"
            shift 2
            ;;
        --file)
            [[ -z "${2:-}" ]] && { printf 'show.sh: --file requires a value (status|readme)\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _file_value="$2"
            shift 2
            ;;
        *)
            printf 'show.sh: unknown argument: %s\n' "$1" >&2
            printf '\n' >&2
            print_usage_and_exit 1 "$_USAGE_TEXT"
            ;;
    esac
done

# Show help early (after parsing so --help can appear anywhere).
if [[ "${_show_help}" == "true" ]]; then
    print_usage_and_exit 0 "$_USAGE_TEXT"
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
    print_usage_and_exit 1 "$_USAGE_TEXT"
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
# Resolve the kanban root.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project name by exact match then unambiguous prefix ---
_project_value="$(pp_resolve_project_name "${_project_value}")" || exit 1

# ---------------------------------------------------------------------------
# Emit the item's document through pgai_agent_kanban.ops.read.get_task_status
# -- the same function GET /show calls. It resolves the key (a task folder,
# then bugs/, priority/, requirements/; each by exact name, then unambiguous
# prefix; a key matching nothing live is looked up the same way in the
# project's archive) and prints the selected document to stdout. An
# ambiguous prefix prints the resolver's notice to stderr and still emits the
# first match (exit 0); an archived item prints the "archived:" line to
# stderr and is emitted unchanged (exit 0). Exit 3 means nothing matched;
# exit 1 covers a missing project root, a task folder without status.md, and
# a task without the --file document. Errors go to stderr.
# ---------------------------------------------------------------------------
_show_rc=0
pp_run_ops pgai_agent_kanban.ops get_task_status \
    "${KANBAN_ROOT}" "${_project_value}" "${_key_value}" "${_file_value}" || _show_rc=$?

if [[ "${_show_rc}" -eq 3 ]]; then
    printf 'show.sh: key not found: %s\n' "${_key_value}" >&2
fi
exit "${_show_rc}"
