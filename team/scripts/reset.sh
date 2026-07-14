#!/usr/bin/env bash
# team/scripts/reset.sh
# Single dispatcher for all operator reset operations.
#
# DESIGN PHILOSOPHY: operator power tool — assumes intent, refuses only
# filesystem races (WORKING state, ambiguous key), warns-and-proceeds on
# everything else. No confirmation prompts.
#
# Usage:
#   reset.sh --project <name> --key <CODER-YYYYMMDD-NNN[-slug]>  (agent task, role in key prefix)
#   reset.sh --project <name> --key <BUG-NNNN>       (intake type inferred from key prefix)
#   reset.sh --project <name> --key <PRIORITY-NNNN>  (intake type inferred from key prefix)
#   reset.sh --project <name> --key <version>         (intake type inferred from key prefix)
#
# KEY is self-identifying:
#   AGENT-YYYYMMDD-NNN (task)  — resolved via resolve_item; agent extracted from key prefix.
#   BUG-*                      — bug intake reset.
#   PRIORITY-*                 — priority intake reset.
#   version/other              — requirement intake reset.
#
# Required flags (all modes):
#   --project <name>   Project name (no env fallback; key collision across
#                      projects is why this flag is mandatory)
#   --key <key>        Full item key (task ID, BUG-NNNN, PRIORITY-NNNN, or version)
#
# Optional flags (agent-task resets only):
#   --keep-artifacts   Preserve artifacts/ contents (default: clear them)
#
# Optional flags (all modes):
#   --help, -h         Show this help and exit 0
#
# Agent-task actions on accepted reset (delegated to Python reset_item):
#   1. Regenerate status.md from template (State=BACKLOG, clean slate)
#   2. Clear artifacts/ and task's logs/ (skip artifacts with --keep-artifacts)
#   3. Flip queue marker to [ ] in the <agent>_backlog.md queue file
#   4. Delete feature/<AGENT>-<task-id> branch from the project dev tree (local only)
#   5. Run git worktree prune on the project dev tree
#   6. (TESTER only) Tear down any retained TESTER worktree
#   7. Append one line to the operator reset log
#
# Intake actions on accepted reset (delegated to Python reset_item):
#   bug:         Rewrite ## Status to 'open'; flip [x] to [ ] in bug_backlog.md
#   priority:    Rewrite ## Status to 'open'; flip [x] to [ ] in priority_backlog.md
#   requirement: Rewrite ## Status to 'open'; clear ## PM Task to 'none';
#                pm_backlog UNTOUCHED; delete PM materializer's .materialized.<sha256>
#                hash-marker; discovery mints a fresh decompose ticket next tick.
#
# Refusals (non-zero exit, no changes):
#   agent-task resets:  WORKING state (an agent may currently hold the task)
#   all modes:          Ambiguous key, zero key matches, missing required flags,
#                       unknown flags (e.g. old --agent selector is now gone)
#
# Exit codes:
#   0  reset completed
#   1  usage error, missing argument, or ambiguous/missing key
#   2  WORKING state refusal (agent-task resets only)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source shared libraries.
# shellcheck source=lib/operator_args.sh
source "${_SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/worktree.sh
source "${_SCRIPT_DIR}/lib/worktree.sh"

# ---------------------------------------------------------------------------
# Declared flag vocabulary for this script.
# reset accepts: --project --key --keep-artifacts --help
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(project key keep-artifacts help)

# ---------------------------------------------------------------------------
# Usage / --help
# ---------------------------------------------------------------------------
_usage() {
    operator_args_render_help_for_flags \
        "$(basename "$0")" \
        "Reset agent tasks or intake items to re-pickable state (operator power tool)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "         value: full item key (see formats):" \
        "                 Agent task:   ROLE-YYYYMMDD-NNN[-slug] (role in prefix)" \
        "                 Bug intake:   BUG-NNNN" \
        "                 Priority:     PRIORITY-NNNN" \
        "                 Requirement:  version string (e.g. v0.1.2)" \
        "" \
        "Examples:" \
        "  $(basename "$0") --project pgai-agent-kanban --key CODER-20260607-001-some-slug" \
        "  $(basename "$0") --project pgai-agent-kanban --key TESTER-20260607-001" \
        "  $(basename "$0") --project pgai-agent-kanban --key BUG-0042" \
        "  $(basename "$0") --project pgai-agent-kanban --key PRIORITY-0007" \
        "  $(basename "$0") --project pgai-agent-kanban --key v0.1.2" \
        "" \
        "Exit codes:" \
        "  0  reset completed" \
        "  1  usage error or key not found / ambiguous" \
        "  2  WORKING state refusal (agent-task resets only)"
}

# ---------------------------------------------------------------------------
# Parse arguments manually.
# We extend the canonical flag set with --keep-artifacts.
# --agent is REMOVED — passing it is now an unknown-flag error.
# --bug, --priority, --requirement are REMOVED — they now exit as unknown flags.
# ---------------------------------------------------------------------------
_project_value="" # --project VALUE
_key_value=""     # --key VALUE
_keep_artifacts=false
_show_help=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h)
            _show_help=true
            shift
            ;;
        --project)
            [[ -z "${2:-}" ]] && { echo "ERROR: --project requires a value" >&2; echo "" >&2; _usage >&2; exit 1; }
            _project_value="$2"
            shift 2
            ;;
        --key)
            [[ -z "${2:-}" ]] && { echo "ERROR: --key requires a value" >&2; echo "" >&2; _usage >&2; exit 1; }
            _key_value="$2"
            shift 2
            ;;
        --keep-artifacts)
            _keep_artifacts=true
            shift
            ;;
        *)
            printf 'reset.sh: unknown argument: %s\n' "$1" >&2
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
# Validate: required flags --project and --key for all modes.
# ---------------------------------------------------------------------------
_missing_flags=()
[[ -z "${_project_value}" ]] && _missing_flags+=(--project)
[[ -z "${_key_value}" ]]     && _missing_flags+=(--key)

if [[ ${#_missing_flags[@]} -gt 0 ]]; then
    echo "ERROR: missing required flag(s): ${_missing_flags[*]}" >&2
    echo "" >&2
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
# Delegate to Python reset_item.
# The Python implementation handles all item types (task/bug/priority/requirement)
# and the refuse-on-WORKING invariant.
# Exit code mapping from Python:
#   0  success
#   1  error / argument error
#   2  WORKING state refusal OR ambiguous key
#   3  not found
# ---------------------------------------------------------------------------
_keep_artifacts_flag="0"
if [[ "${_keep_artifacts}" == "true" ]]; then
    _keep_artifacts_flag="1"
fi

_py_rc=0
python3 -m pgai_agent_kanban.ops reset_item \
    "${_project_root}" \
    "${_key_value}" \
    "${_keep_artifacts_flag}" || _py_rc=$?

if [[ "${_py_rc}" -ne 0 ]]; then
    exit "${_py_rc}"
fi

# ---------------------------------------------------------------------------
# TESTER-specific: tear down any retained TESTER worktree.
# This is done after the Python reset because the worktree teardown uses
# the bash worktree.sh library (teardown_task_worktree / pgai_worktree_path).
#
# Only runs when the key resolves to a TESTER task (key prefix is TESTER-).
# ---------------------------------------------------------------------------
if [[ "${_key_value}" == TESTER-* ]]; then
    _task_id="${_key_value}"

    # Load dev_tree from project config (pp_load_config exports PP_dev_tree_path).
    _dev_tree=""
    if pp_load_config "${_project_value}" 2>/dev/null; then
        _dev_tree="${PP_dev_tree_path:-}"
    fi

    # Resolve the PGAI_WORKTREE_BASE-relative worktree path.
    _wt_path=""
    if _wt_path="$(pgai_worktree_path "${_task_id}" 2>/dev/null)" && [[ -n "${_wt_path}" && -d "${_wt_path}" ]]; then
        echo "TESTER worktree: tearing down retained worktree at ${_wt_path}"
        if teardown_task_worktree "${_task_id}" "${_dev_tree:-}"; then
            echo "TESTER worktree: teardown complete"
        else
            echo "WARNING: TESTER worktree teardown failed for '${_task_id}'" >&2
        fi
    else
        echo "TESTER worktree: no retained worktree found for '${_task_id}'"
    fi
fi

exit 0
