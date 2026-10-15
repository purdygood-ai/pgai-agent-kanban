#!/usr/bin/env bash
# team/scripts/close.sh
# CLI operator tool: close/resolve an item (bug/priority/requirement/task).
#
# Usage:
#   close.sh --project <name> --key <key> [--state <done|wont-do>]
#            [--note <text>] [--dry-run] [--help]
#
# DESIGN: resolves the key via close_item (pgai_agent_kanban.ops.write) and
# performs the close operation.  Refuses in exactly two cases: a --state that
# is not a closed state (exit 1), and a key that cannot be identified as a
# single target (ambiguous, exit 2; not found, exit 3).
#
# KEY RESOLUTION (via resolve_item, called inside close_item):
#   1. tasks/<KEY>/          — agent task (closed as DONE; --state is intake-only)
#   2. bugs/<KEY>.md         — bug intake item
#   3. priority/<KEY>.md     — priority intake item
#   4. requirements/<KEY>.md — requirement intake item
#
# State values (intake terminal states, default: done):
#   done        — item resolved/addressed
#   wont-do     — item intentionally not addressed (an item another item
#                 subsumed closes as wont-do with a --note saying why)
# The accepted set is lib.terminal_states.CLOSED_STATES, checked by
# close_item; any other value (including the retired "superseded") is
# refused dual-channel with exit 1 and a remedy naming a valid --state.
#
# Optional flags:
#   --note TEXT   Free-form text recorded in ## Close Note section of item.
#   --dry-run     Report what would change without writing anything.
#   --help, -h    Show help and exit 0.
#
# Exit codes:
#   0   Item closed (or dry-run reported, or --help shown).
#   1   Usage error, missing argument, or invalid --state.
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
# shellcheck source=lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=lib/script_common.sh
source "${_LIB_DIR}/script_common.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# close accepts: --project --key --state --note --dry-run --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key state note dry-run help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
# Usage text as data (per docs/coding-standards.md and lib/script_common.sh's
# print_usage_and_exit contract) — rendered once via the shared per-script
# help renderer, then emitted by print_usage_and_exit at each call site.
# --state here takes the closed states, not the task states the renderer's
# built-in --state line lists, so its line is overridden; --note is outside
# the canonical vocabulary, so its line is given here too.
declare -A OPERATOR_FLAG_HELP=(
    [state]="  --state STATE      Closed state: done|wont-do (default: done)"
    [note]="  --note TEXT        Free-form text recorded in the item's ## Close Note section (intake items only)"
)
_USAGE_TEXT="$(operator_args_render_help_for_flags \
    "$(basename "$0")" \
    "Close/resolve an item (bug/priority/requirement/task) by setting its terminal status." \
    OPERATOR_VALID_FLAGS \
    "" \
    "         value: terminal state for intake items (default: done):" \
    "                 done        — item resolved/addressed" \
    "                 wont-do     — item intentionally not addressed" \
    "                               (an item another item subsumed closes" \
    "                               as wont-do with a --note saying why)" \
    "                 (For agent tasks, close always sets state to DONE.)" \
    "" \
    "  close.sh performs the close operation and refuses in two cases only:" \
    "    - --state is not a closed state (done|wont-do): close.invalid_state," \
    "      exit 1, nothing written;" \
    "    - the key cannot be resolved to a single target: ambiguous (exit 2)" \
    "      or not found (exit 3)." \
    "" \
    "Example:" \
    "  close.sh --project myproject --key BUG-NNNN --state wont-do \\" \
    "           --note 'subsumed by PRIORITY-NNNN'" \
    "" \
    "Exit codes:" \
    "  0  Item closed (or --help shown, or --dry-run reported)" \
    "  1  Usage error, missing argument, or --state not done|wont-do" \
    "  2  Ambiguous key — key matches zero or multiple items" \
    "  3  Key not found" \
    "  4  State mutation failed")"

# ---------------------------------------------------------------------------
# Manual argument parsing.
# We parse manually because --state takes intake close states (validated by
# close_item against lib.terminal_states.CLOSED_STATES), not the task states
# operator_args_validate_state checks (BACKLOG/WAITING/etc.).
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
            [[ -z "${2:-}" ]] && { printf 'close.sh: --project requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --key requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _key_value="$2"
            shift 2
            ;;
        --state)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --state requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
            _state_value="$2"
            shift 2
            ;;
        --note)
            [[ -z "${2:-}" ]] && { printf 'close.sh: --note requires a value\n' >&2; printf '\n' >&2; print_usage_and_exit 1 "$_USAGE_TEXT"; }
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
            print_usage_and_exit 1 "$_USAGE_TEXT"
            ;;
    esac
done

# Show help early (after parsing so --help can appear anywhere).
if [[ "${_show_help}" == "true" ]]; then
    print_usage_and_exit 0 "$_USAGE_TEXT"
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
    print_usage_and_exit 1 "$_USAGE_TEXT"
fi

# ---------------------------------------------------------------------------
# Resolve kanban root and project root.
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project name by exact match then unambiguous prefix ---
_project_value="$(pp_resolve_project_name "${_project_value}")" || exit 1

_project_root="$(pp_project_root "${_project_value}")" || exit 1

# ---------------------------------------------------------------------------
# Execute the close operation via the Python library.
# close_item handles:
#   - --state validation against the shared closed-state list (a refusal
#     prints the dual-channel block and exits 1)
#   - resolve_item (same resolution as delete/show/reset)
#   - dry-run mode
#   - ## Status/State mutation + optional ## Close Note recording
#   - queue/backlog marker flip
#
# CLI: pp_run_ops pgai_agent_kanban.ops close_item
#      PROJECT_ROOT KEY [STATE] [NOTE] [DRY_RUN]
# ---------------------------------------------------------------------------
_close_rc=0
pp_run_ops pgai_agent_kanban.ops close_item \
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
