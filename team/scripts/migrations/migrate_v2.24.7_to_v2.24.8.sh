#!/usr/bin/env bash
# team/scripts/migrations/migrate_v2.24.7_to_v2.24.8.sh
#
# A narrated no-op: it writes nothing. OVERWATCH keeps no queue file
# (tasks/queues/overwatch_backlog.md) -- its firings are the hourly sweep and
# the on-BLOCK nudge, neither of which reads one -- so there is nothing to
# seed. The step stays in the chain so an upgrade that crosses this version
# runs every step in order, and it validates its arguments as every
# migration does.
#
# What this migration does:
#   - For each targeted project, confirms the project directory exists and
#     prints one "[no-op]" line naming it.
#   - Creates, removes and rewrites nothing. A project's existing
#     overwatch_backlog.md, if any, is left as it is.
#
# Contract (team/scripts/migrations/README.md):
#   - Additive, non-destructive and idempotent trivially: it writes nothing.
#
# Usage:
#   migrate_v2.24.7_to_v2.24.8.sh                    # every registered project
#   migrate_v2.24.7_to_v2.24.8.sh --project <name>   # one project only
#   migrate_v2.24.7_to_v2.24.8.sh --dry-run          # preview only, no writes
#   migrate_v2.24.7_to_v2.24.8.sh --project <name> --dry-run
#
# Exit codes:
#   0 — success (including "nothing to do" no-ops, and an empty registry
#       for the all-projects mode)
#   1 — usage error
#   2 — a named project (--project) has no project directory on disk
#   3 — KANBAN_ROOT not set or not found

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

_MIG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/operator_args.sh
source "${_MIG_SCRIPT_DIR}/../lib/operator_args.sh"
# shellcheck source=../lib/projects.sh
source "${_MIG_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=../lib/project_paths.sh
source "${_MIG_SCRIPT_DIR}/../lib/project_paths.sh"
unset _MIG_SCRIPT_DIR

OPERATOR_VALID_FLAGS=(project dry-run help)

_USAGE_TEXT="$(operator_args_render_help_for_flags "migrate_v2.24.7_to_v2.24.8.sh" \
    "No-op: OVERWATCH keeps no queue file, so there is nothing to seed (one project or every registered project)." \
    OPERATOR_VALID_FLAGS)"

operator_args_argparse --value-flags "project" -- "$@"

if argparse_missing "project"; then
    echo "ERROR: --project requires a value" >&2
    exit 1
fi

if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 1
fi

operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# --dry-run is still accepted; with nothing to write, a dry run and a real
# run print the same lines.
SINGLE_PROJECT="$(operator_args_project)"

# ---------------------------------------------------------------------------
# _migrate_one <project_name>
# Narrates the no-op for one project and returns 0. Returns 2 only when the
# project's directory is missing on disk.
# ---------------------------------------------------------------------------
_migrate_one() {
    local name="$1"
    local proj_dir="${KANBAN_ROOT}/projects/${name}"

    if [[ ! -d "$proj_dir" ]]; then
        echo "ERROR: project '${name}' not found -- no project directory at: ${proj_dir}" >&2
        return 2
    fi

    echo "  [no-op]  ${name}: OVERWATCH keeps no queue file -- nothing to seed, no change"
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ -n "$SINGLE_PROJECT" ]]; then
    SINGLE_PROJECT="$(pp_resolve_project_name "$SINGLE_PROJECT")" || exit 1
    echo "migrate_v2.24.7_to_v2.24.8.sh: no-op for project '${SINGLE_PROJECT}' (OVERWATCH keeps no queue file)"
    _migrate_one "$SINGLE_PROJECT"
    exit $?
fi

echo "migrate_v2.24.7_to_v2.24.8.sh: no-op for every registered project (OVERWATCH keeps no queue file)"
_MIG_SAW_PROJECT=0
_MIG_EXIT=0
while IFS= read -r _mig_project; do
    [[ -z "$_mig_project" ]] && continue
    _MIG_SAW_PROJECT=1
    _migrate_one "$_mig_project" || _MIG_EXIT=$?
done < <(projects_cfg_list 2>/dev/null || true)

if [[ "$_MIG_SAW_PROJECT" -eq 0 ]]; then
    echo "migrate_v2.24.7_to_v2.24.8.sh: no registered projects found (see $(projects_cfg_path)) -- nothing to do"
fi

exit "$_MIG_EXIT"
