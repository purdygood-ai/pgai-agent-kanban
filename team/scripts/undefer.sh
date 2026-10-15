#!/usr/bin/env bash
# team/scripts/undefer.sh
# CLI wrapper: clear the DEFER-LOW-BUGS deferral campaign for a project.
#
# Usage: undefer.sh --project NAME [OPTIONS]
#   --project NAME    Project to clear the campaign for (required)
#   --help, -h        Show help and exit
#
# Thin adapter: parses the flags via operator_args.sh, refuses a call with
# no project named, and hands the rest to the Python undefer function in
# pgai_agent_kanban.ops.write (the same function POST /operations/undefer
# calls). That function resolves the project by exact match then
# unambiguous prefix and removes PROJECT_ROOT/DEFER-LOW-BUGS. Idempotent: a
# project with no campaign file in force succeeds and reports there was
# nothing to clear, rather than refusing -- clearing an already-clear
# posture is not an error.
#
# After clearing, the deferral posture falls back to whatever the
# project.cfg / kanban.cfg / default tiers report; this verb decides no
# posture itself, it only removes the top precedence tier.
#
# Exit codes: 0 cleared or nothing to clear; 1 any refusal (no project,
# unknown or ambiguous project, no project directory, a removal failure).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# --- Locate the lib directory relative to this script ---
_UNDEFER_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_UNDEFER_LIB_DIR="${_UNDEFER_SCRIPT_DIR}/lib"

# --- Source the standard argument layer ---
# shellcheck source=lib/operator_args.sh
source "${_UNDEFER_LIB_DIR}/operator_args.sh"
# shellcheck source=lib/error_contract.sh
source "${_UNDEFER_LIB_DIR}/error_contract.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_UNDEFER_LIB_DIR}/pp_run_ops.sh"

# --- Declare accepted flags for this script ---
OPERATOR_VALID_FLAGS=(project help)

# --- Parse arguments ---
operator_args_parse "$@"

# --- Handle --help ---
if argparse_has help; then
    operator_args_render_help_for_flags "undefer.sh" \
        "Clear the DEFER-LOW-BUGS deferral campaign for a project (idempotent)." \
        OPERATOR_VALID_FLAGS \
        "" \
        "Running this against a project with no campaign file in force succeeds" \
        "and reports there was nothing to clear."
    exit 0
fi

# --- Reject any flag not in the declared vocabulary ---
operator_args_validate_known "undefer.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Resolve kanban root ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
export KANBAN_ROOT

# --- Resolve project (flag, then $PGAI_PROJECT_NAME) ---
_project_raw="$(operator_args_project)"
if [[ -z "${_project_raw}" ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): a version boundary names a
    # different release in every project's version space, so a campaign
    # without a named project has no target to resolve against -- an
    # invariant-by-design stop, not a judgement call.
    pgai_emit_error_contract \
        "undefer.project_required" \
        "undefer.sh: no project named -- a deferral campaign is always per-project." \
        "Set \$PGAI_PROJECT_NAME to a project registered in projects.cfg, or pass --project directly, e.g. team/scripts/undefer.sh --project pgai-agent-kanban."
    # CENSUS-ANCHOR: undefer-project-required-01
    exit 1
fi

# --- Resolve and clear via the Python library ---
pp_run_ops pgai_agent_kanban.ops undefer "${KANBAN_ROOT}" "${_project_raw}" || exit 1
exit 0
