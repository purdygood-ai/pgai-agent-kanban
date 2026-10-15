#!/usr/bin/env bash
# team/scripts/show-readme.sh
# CLI read dispatcher: emit a task's README.md to stdout.
#
# Usage:
#   show-readme.sh --project <name> --key <task-key> [--help]
#
# DESIGN: strictly read-only. A thin wrapper over
# pgai_agent_kanban.ops.read.get_task_readme (reached through pp_run_ops) --
# the function GET /projects/{project}/tasks/{key}/readme calls too -- which
# resolves the key via the shared resolve_item function (same resolution as
# show.sh, delete, reset, close) and emits the task's README.md to stdout.
# Task keys only — a ledger key (bug, priority, or requirement) is refused
# with a prescriptive line directing the operator to show.sh, which already
# prints that key's document.
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
# shellcheck source=lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/script_common.sh
source "${_LIB_DIR}/script_common.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# show-readme accepts: --project --key --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract).
_USAGE_TEXT="$(operator_args_render_help_for_flags \
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
    "  3  Key not found")"

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
            [[ -z "${2:-}" ]] && { printf 'show-readme.sh: --project requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show-readme.sh: --key requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _key_value="$2"
            shift 2
            ;;
        *)
            printf 'show-readme.sh: unknown argument: %s\n' "$1" >&2
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
    printf 'show-readme.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
    printf '\n' >&2
    print_usage_and_exit 1 "$_USAGE_TEXT"
fi

# ---------------------------------------------------------------------------
# Emit the README through pgai_agent_kanban.ops.read.get_task_readme -- the
# same function GET /projects/{project}/tasks/{key}/readme calls. It resolves
# the key with the shared resolver (an ambiguous prefix prints the
# resolver's notice to stderr and still emits the first match), refuses a
# ledger key with a line pointing at show.sh, and prints the task's
# README.md. Exit 3 means nothing matched; exit 1 covers a missing project
# directory, a ledger key and a task without README.md. Errors go to stderr.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

_readme_rc=0
pp_run_ops pgai_agent_kanban.ops get_task_readme \
    "${KANBAN_ROOT}" "${_project_value}" "${_key_value}" || _readme_rc=$?
exit "${_readme_rc}"
