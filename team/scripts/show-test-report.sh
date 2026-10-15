#!/usr/bin/env bash
# team/scripts/show-test-report.sh
# CLI read command: print a TESTER verification report to stdout.
#
# Usage:
#   show-test-report.sh --project <name> --key <RC-version|TESTER-task-key> [--help]
#
# DESIGN: strictly read-only.  A thin wrapper over
# pgai_agent_kanban.ops.read.get_test_report (reached through pp_run_ops) --
# the function GET /test-report calls too -- which resolves the key via the
# shared resolve_item function (for TESTER task keys) or a direct tasks/ scan
# (for vX.Y.Z keys), then prints the task's artifacts/report.md to stdout.
# Never mutates any file.
#
# KEY RESOLUTION:
#   TESTER task key (TESTER-YYYYMMDD-NNN or a unique prefix thereof):
#     Resolves through the shared resolve_item function; an ambiguous
#     prefix reads the first match and exits 2.
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
# shellcheck source=lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/script_common.sh
source "${_LIB_DIR}/script_common.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# show-test-report accepts: --project --key --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract).
_USAGE_TEXT="$(operator_args_render_help_for_flags \
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
    "  3  Key not found, no matching TESTER task, or report.md absent")"

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
            [[ -z "${2:-}" ]] && { printf 'show-test-report.sh: --project requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'show-test-report.sh: --key requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _key_value="$2"
            shift 2
            ;;
        *)
            printf 'show-test-report.sh: unknown argument: %s\n' "$1" >&2
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
    printf 'show-test-report.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
    printf '\n' >&2
    print_usage_and_exit 1 "$_USAGE_TEXT"
fi

# ---------------------------------------------------------------------------
# Emit the report through pgai_agent_kanban.ops.read.get_test_report -- the
# same function GET /test-report calls. A vX.Y.Z key selects the latest
# TESTER-...-verify-X-Y-Z task folder; any other key resolves through the
# shared resolver and must name a TESTER task. The function prints the
# task's artifacts/report.md and exits with the codes listed in --help
# (an ambiguous prefix prints the resolver's notice to stderr, then the
# first match's report, and exits 2). Errors go to stderr.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

_report_rc=0
pp_run_ops pgai_agent_kanban.ops get_test_report \
    "${KANBAN_ROOT}" "${_project_value}" "${_key_value}" || _report_rc=$?
exit "${_report_rc}"
