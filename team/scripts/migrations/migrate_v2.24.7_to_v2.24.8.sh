#!/usr/bin/env bash
# team/scripts/migrations/migrate_v2.24.7_to_v2.24.8.sh
#
# Seeds tasks/queues/overwatch_backlog.md into every already-registered
# project, so an existing install carries the same queue file
# create-project.sh now writes into a brand-new project (see
# team/scripts/lib/overwatch_backlog_seed.sh, the single source of truth
# for the content both paths emit). Before this migration, an existing
# project's HALT carve-out could enter the wake chain (per
# team/roles/OVERWATCH.md's on-BLOCK trigger) only to be skipped outright
# because run_project_chain's missing-backlog check ran before the
# resolution stage ever got a look at the HALT.
#
# What this migration does:
#   - For each targeted project, checks whether
#     tasks/queues/overwatch_backlog.md already exists (any content,
#     including empty).
#   - If absent: creates it with the seeded header/legend content.
#   - If present: makes NO change to that file. This covers both an
#     operator who created the file by hand and a project that was already
#     migrated or created post-v2.24.8 — the second run (or the tenth) is a
#     byte-identical no-op.
#
# Contract (team/scripts/migrations/README.md):
#   - Additive:      only ever creates the file when it is missing; never
#                     removes or rewrites an existing one.
#   - Non-destructive: an existing overwatch_backlog.md — empty or carrying
#                     real entries — is left untouched, byte-for-byte.
#   - Idempotent:     running this script N times against the same project
#                     produces the same on-disk state N=1 produced.
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
# shellcheck source=../lib/overwatch_backlog_seed.sh
source "${_MIG_SCRIPT_DIR}/../lib/overwatch_backlog_seed.sh"
unset _MIG_SCRIPT_DIR

OPERATOR_VALID_FLAGS=(project dry-run help)

_USAGE_TEXT="$(operator_args_render_help_for_flags "migrate_v2.24.7_to_v2.24.8.sh" \
    "Seed tasks/queues/overwatch_backlog.md (one project or every registered project)." \
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

DRY_RUN="false"
argparse_has "dry-run" && DRY_RUN="true"
SINGLE_PROJECT="$(operator_args_project)"

# ---------------------------------------------------------------------------
# _migrate_one <project_name>
# Returns 0 whether the project was seeded or already up to date. Returns 2
# only when the project's directory is missing on disk.
# ---------------------------------------------------------------------------
_migrate_one() {
    local name="$1"
    local proj_dir="${KANBAN_ROOT}/projects/${name}"
    local queues_dir="${proj_dir}/tasks/queues"
    local backlog="${queues_dir}/overwatch_backlog.md"

    if [[ ! -d "$proj_dir" ]]; then
        echo "ERROR: project '${name}' not found -- no project directory at: ${proj_dir}" >&2
        return 2
    fi

    if [[ -f "$backlog" ]]; then
        echo "  [skip]  ${name}: overwatch_backlog.md already present -- no change"
        return 0
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        echo "  [would-seed]  ${name}: would create ${backlog}"
        return 0
    fi

    mkdir -p "$queues_dir"
    overwatch_backlog_seed_content > "$backlog"
    echo "  [seeded]  ${name}: created ${backlog}"
    return 0
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if [[ -n "$SINGLE_PROJECT" ]]; then
    SINGLE_PROJECT="$(pp_resolve_project_name "$SINGLE_PROJECT")" || exit 1
    echo "migrate_v2.24.7_to_v2.24.8.sh: seeding overwatch_backlog.md for project '${SINGLE_PROJECT}'"
    _migrate_one "$SINGLE_PROJECT"
    exit $?
fi

echo "migrate_v2.24.7_to_v2.24.8.sh: seeding overwatch_backlog.md for every registered project"
_MIG_SAW_PROJECT=0
_MIG_EXIT=0
while IFS= read -r _mig_project; do
    [[ -z "$_mig_project" ]] && continue
    _MIG_SAW_PROJECT=1
    _migrate_one "$_mig_project" || _MIG_EXIT=$?
done < <(projects_cfg_list 2>/dev/null || true)

if [[ "$_MIG_SAW_PROJECT" -eq 0 ]]; then
    echo "migrate_v2.24.7_to_v2.24.8.sh: no registered projects found (see $(projects_cfg_path)) -- nothing to seed"
fi

exit "$_MIG_EXIT"
