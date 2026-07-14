#!/usr/bin/env bash
# team/scripts/close.sh
# CLI operator tool: close/resolve an item (bug/priority/requirement/task).
#
# Usage:
#   close.sh --project <name> --key <key> [--state <done|wont-do|superseded>]
#            [--note <text>] [--dry-run] [--help]
#
# DESIGN: resolves the key via close_item (pgai_agent_kanban.ops.write) and
# performs the close operation.  Refuses only when the key cannot be identified
# as a single target (not found or ambiguous).
#
# KEY RESOLUTION (via resolve_item, called inside close_item):
#   1. tasks/<KEY>/          — agent task (closed as DONE; --state is intake-only)
#   2. bugs/<KEY>.md         — bug intake item
#   3. priority/<KEY>.md     — priority intake item
#   4. requirements/<KEY>.md — requirement intake item
#
# State values (intake terminal states, default: done):
#   done        — item resolved/addressed
#   wont-do     — item intentionally not addressed
#   superseded  — item subsumed by another item
#
# Optional flags:
#   --note TEXT   Free-form text recorded in ## Close Note section of item.
#   --dry-run     Report what would change without writing anything.
#   --help, -h    Show help and exit 0.
#
# Exit codes:
#   0   Item closed (or dry-run reported, or --help shown).
#   1   Usage error or missing argument.
#   2   Ambiguous key — key matches zero or multiple items.
#   3   Key not found.
#   4   State mutation failed.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LIB_DIR="${_SCRIPT_DIR}/lib"

# Source shared libraries.
# shellcheck source=lib/operator_args.sh
source "${_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_LIB_DIR}/project_paths.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# close accepts: --project --key --state --note --dry-run --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key state note dry-run help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Close/resolve an item (bug/priority/requirement/task) by setting its terminal status." \
        OPERATOR_VALID_FLAGS \
        "" \
        "         value: terminal state for intake items (default: done):" \
        "                 done        — item resolved/addressed" \
        "                 wont-do     — item intentionally not addressed" \
        "                 superseded  — item subsumed by another item" \
        "                 (For agent tasks, close always sets state to DONE.)" \
        "" \
        "  close.sh performs the close operation and refuses only when the key" \
        "  cannot be resolved to a single target (not found or ambiguous)." \
        "" \
        "Example:" \
        "  close.sh --project myproject --key BUG-0362 --state superseded \\" \
        "           --note 'subsumed by PRIORITY-0099'" \
        "" \
        "Exit codes:" \
        "  0  Item closed (or --help shown, or --dry-run reported)" \
        "  1  Usage error or missing argument" \
        "  2  Ambiguous key — key matches zero or multiple items" \
        "  3  Key not found" \
        "  4  State mutation failed"
}

# ---------------------------------------------------------------------------
# Manual argument parsing.
# We parse manually because --state requires intake-specific values
# (done|wont-do|superseded) not covered by operator_args_validate_state
# (which validates task states: BACKLOG/WAITING/etc.).
# We also need --note, which is not in the canonical operator vocabulary.
# ---------------------------------------------------------------------------
_project_value=""
_key_value=""
_state_value="done"    # default: done
_note_value=""
_dry_run=0
_show_help=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _show_help=true
            shift
            ;;
        --project)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --project requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --key requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _key_value="$2"
            shift 2
            ;;
        --state)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --state requires a value (done|wont-do|superseded)\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _state_value="$2"
            shift 2
            ;;
        --note)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --note requires a value\n' >&2; printf '\n' >&2; _usage >&2; exit 1; }
            _note_value="$2"
            shift 2
            ;;
        --dry-run)
            _dry_run=1
            shift
            ;;
        *)
            printf 'close.sh: unknown argument: %s\n' "$1" >&2
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

# Reject any flag not in the declared vocabulary (belt-and-suspenders; the
# while-case parser above already catches unknowns via the *)  handler).
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Validate required flags.
# ---------------------------------------------------------------------------
_missing_flags=()
[[ -z "${_project_value}" ]] && { _project_value="${PGAI_PROJECT_NAME:-}"; }
[[ -z "${_project_value}" ]] && _missing_flags+=(--project)
[[ -z "${_key_value}" ]]     && _missing_flags+=(--key)

if [[ ${#_missing_flags[@]} -gt 0 ]]; then
    printf 'close.sh: missing required flag(s): %s\n' "${_missing_flags[*]}" >&2
    printf '\n' >&2
    _usage >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate --state value (intake terminal states only).
# ---------------------------------------------------------------------------
case "${_state_value}" in
    done|wont-do|superseded)
        ;;
    *)
        printf 'close.sh: invalid --state value: %s (valid: done | wont-do | superseded)\n' \
            "${_state_value}" >&2
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
# Execute the close operation via the Python library.
# close_item handles:
#   - resolve_item (same resolution as delete/show/reset)
#   - dry-run mode
#   - ## Status/State mutation + optional ## Close Note recording
#   - queue/backlog marker flip
#
# CLI: python3 -m pgai_agent_kanban.ops close_item
#      PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
# ---------------------------------------------------------------------------
_close_rc=0
python3 -m pgai_agent_kanban.ops close_item \
    "${_project_root}" "${_key_value}" "${_state_value}" "${_note_value}" "${_dry_run}" \
    || _close_rc=$?

case "${_close_rc}" in
    0)
        if [[ "${_dry_run}" -eq 0 ]]; then
            printf 'close: %s closed (status: %s)\n' "${_key_value}" "${_state_value}"
        fi
        exit 0
        ;;
    1)
        # Argument/configuration error — stderr already printed by close_item.
        exit 1
        ;;
    2)
        # Ambiguous key — stderr already printed by close_item/resolve_item.
        exit 2
        ;;
    3)
        # Item not found — stderr already printed by close_item/resolve_item.
        exit 3
        ;;
    4)
        # State mutation failed — stderr already printed by close_item.
        exit 4
        ;;
    *)
        printf 'close.sh: unexpected return code from close_item: %d\n' "${_close_rc}" >&2
        exit 1
        ;;
esac
