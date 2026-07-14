#!/usr/bin/env bash
# team/scripts/intake.sh
# CLI operator tool: deposit a staged intake file into a project's intake directory.
#
# Usage: intake.sh --project <name> [--file] <source_file> [--help]
#
#   --project NAME    Project name (default: $PGAI_PROJECT_NAME)
#   --file PATH       Path to the staged intake file (may also be a positional arg)
#   --help, -h        Show help and exit
#
# Routes by filename prefix (case-sensitive):
#   BUG-*       -> projects/<name>/bugs/
#   PRIORITY-*  -> projects/<name>/priority/
#   v[0-9]*.md  -> projects/<name>/requirements/
#   (any other) -> REFUSED with a clear message; nothing is copied
#
# Thin delegator: all mutation logic lives in deposit_intake() in
# pgai_agent_kanban.ops.write (Python).  This script does only argument
# parsing, project-root resolution, invocation via the Python module,
# and output/exit-code propagation.  No mutation logic here.
#
# Exit codes:
#   0   File deposited successfully; prints the deposited path.
#   1   Usage error, source file problem, or destination directory missing.
#   2   Routing refused: filename does not match a known intake prefix.
#   3   Target already exists (no clobber).
#   4   Filesystem error during copy.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_INTAKE_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_INTAKE_LIB_DIR="${_INTAKE_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_INTAKE_LIB_DIR}/operator_args.sh"

# --- Source the project path helpers ---
# shellcheck source=lib/project_paths.sh
source "${_INTAKE_LIB_DIR}/project_paths.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# intake accepts: --project --file --help
# (source_file may also be supplied as a positional argument)
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project file help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_intake_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Deposit a staged intake file into the correct project intake directory." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  <source_file> may also be supplied as a positional argument instead of --file." \
        "" \
        "Routing by filename prefix (case-sensitive):" \
        "  BUG-*        -> projects/<name>/bugs/" \
        "  PRIORITY-*   -> projects/<name>/priority/" \
        "  v[0-9]*.md   -> projects/<name>/requirements/" \
        "  (any other)  -> REFUSED; nothing is copied" \
        "" \
        "Copy semantics:" \
        "  - Source file is NOT moved or deleted (copy only)." \
        "  - Deposited copy is always mode 644." \
        "  - Copy is atomic (temp + rename); discovery never sees a partial file." \
        "  - REFUSED if the target already exists (no clobber)." \
        "" \
        "NOTE: intake.sh is a DUMB router. It does NOT validate file contents," \
        "assign numbers, or check headings. Malformed files are still routed" \
        "to .rejected/ by the discovery pipeline." \
        "" \
        "Examples:" \
        "  intake.sh --project myproject /tmp/BUG-0400-some-slug.md" \
        "  intake.sh --project myproject --file /tmp/PRIORITY-0010-urgent.md" \
        "" \
        "Exit codes:" \
        "  0  File deposited (prints the deposited path)" \
        "  1  Usage error, source file problem, or destination directory missing" \
        "  2  Routing refused: filename does not match a known intake prefix" \
        "  3  Target already exists (no clobber)" \
        "  4  Filesystem error during copy"
}

# ---------------------------------------------------------------------------
# Argument parsing.
# We parse manually because --file is not in the canonical operator_args
# value flags (project/key/agent/state).  The canonical --project flag and
# --help flag are handled inline.
#
# Pre-populate ARGPARSE_FLAGS for operator_args_validate_known, which requires
# argparse state to be set before it can check for unknown flags.
# ---------------------------------------------------------------------------
argparse_reset
argparse_parse --value-flags "project file" -- "$@"

_project_value=""
_file_value=""
_show_help=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _show_help=true
            shift
            ;;
        --project)
            [[ -z "${2:-}" ]] && {
                printf 'intake.sh: --project requires a value\n' >&2
                printf '\n' >&2
                _intake_usage >&2
                exit 1
            }
            _project_value="$2"
            shift 2
            ;;
        --file)
            [[ -z "${2:-}" ]] && {
                printf 'intake.sh: --file requires a value\n' >&2
                printf '\n' >&2
                _intake_usage >&2
                exit 1
            }
            _file_value="$2"
            shift 2
            ;;
        -*)
            printf 'intake.sh: unknown argument: %s\n' "$1" >&2
            printf '\n' >&2
            _intake_usage >&2
            exit 1
            ;;
        *)
            # Positional argument: treat as the source file path.
            if [[ -z "${_file_value}" ]]; then
                _file_value="$1"
            else
                printf 'intake.sh: unexpected positional argument: %s\n' "$1" >&2
                printf '\n' >&2
                _intake_usage >&2
                exit 1
            fi
            shift
            ;;
    esac
done

# --- Show help early ---
if [[ "${_show_help}" == "true" ]]; then
    _intake_usage
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "intake.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Apply environment fallback for --project ---
if [[ -z "${_project_value}" ]]; then
    _project_value="${PGAI_PROJECT_NAME:-}"
fi

# --- Validate required args ---
_missing_args=()
[[ -z "${_project_value}" ]] && _missing_args+=("--project")
[[ -z "${_file_value}" ]]    && _missing_args+=("<source_file>")

if [[ ${#_missing_args[@]} -gt 0 ]]; then
    printf 'intake.sh: missing required argument(s): %s\n' "${_missing_args[*]}" >&2
    printf '\n' >&2
    _intake_usage >&2
    exit 1
fi

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project root ---
_project_root="$(pp_project_root "${_project_value}")" || exit 1

# --- Execute the intake operation via the Python library ---
_intake_rc=0
_deposited_path="$(python3 -m pgai_agent_kanban.ops deposit_intake \
    "${_project_root}" "${_file_value}")" \
    || _intake_rc=$?

case "${_intake_rc}" in
    0)
        printf 'intake: deposited %s\n' "${_deposited_path}"
        exit 0
        ;;
    1)
        # Argument/source error — stderr already printed by the Python module.
        exit 1
        ;;
    2)
        # Routing refused — stderr already printed by the Python module.
        exit 2
        ;;
    3)
        # Target exists (no clobber) — stderr already printed by the Python module.
        exit 3
        ;;
    4)
        # Filesystem error — stderr already printed by the Python module.
        exit 4
        ;;
    *)
        printf 'intake.sh: unexpected return code from deposit_intake: %d\n' \
            "${_intake_rc}" >&2
        exit 1
        ;;
esac
