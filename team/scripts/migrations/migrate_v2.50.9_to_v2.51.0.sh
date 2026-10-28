#!/usr/bin/env bash
# team/scripts/migrations/migrate_v2.50.9_to_v2.51.0.sh
#
# Moves an existing install's kanban.cfg onto two changed defaults:
#
#   [release] ship_posture     matrix -> always
#       Ship-policy triggers 2, 3 and 8 ship the release marked
#       NON-FUNCTIONAL and record why, instead of halting.
#   [wake]    stop_on_blocked  true   -> false
#       A BLOCKED task parks only itself and its dependants; the rest of
#       the batch carries on.
#
# kanban.cfg survives upgrade.sh untouched, and an install whose kanban.cfg
# was copied from the older example pins both previous values as explicit
# lines, so the new built-in defaults never reach it. This migration
# rewrites those lines.
#
# What this migration does:
#   - In kanban.cfg, rewrites [release] ship_posture = matrix to always and
#     [wake] stop_on_blocked = true to false, and prints each change with
#     the one cfg line that reverts it.
#   - Leaves a key alone when it is absent (the built-in default already
#     applies), already holds the new value, or holds any other value (an
#     operator's own choice). Every other line keeps its bytes.
#   - Never writes a project.cfg. For each targeted project it reports
#     whether that project's project.cfg sets [release] ship_posture itself
#     (it keeps its own value, whichever it is) or follows kanban.cfg.
#   - kanban.cfg is kanban-wide, so --project narrows only the project
#     report; the kanban.cfg step runs either way.
#
# The logic lives in pgai_agent_kanban.lib.cfg_default_upgrade; this script
# parses arguments and calls it.
#
# Contract (team/scripts/migrations/README.md):
#   - Additive:        adds and removes no line; the two lines it rewrites
#                      keep their key and section.
#   - Non-destructive: any value other than the previous default is left
#                      exactly as set, and project.cfg is never written.
#   - Idempotent:      a second run finds both keys at their new values,
#                      says it is already applied, and writes nothing.
#
# Usage:
#   migrate_v2.50.9_to_v2.51.0.sh                    # every registered project
#   migrate_v2.50.9_to_v2.51.0.sh --project <name>   # report one project only
#   migrate_v2.50.9_to_v2.51.0.sh --dry-run          # preview only, no writes
#   migrate_v2.50.9_to_v2.51.0.sh --project <name> --dry-run
#
# Exit codes:
#   0 — success (including "already applied" and an empty registry)
#   1 — usage error
#   3 — KANBAN_ROOT not set or not found
#   4 — kanban.cfg could not be rewritten (left unchanged)

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

_MIG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_MIG_SCRIPT_PATH="${_MIG_SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
# shellcheck source=../lib/error_contract.sh
source "${_MIG_SCRIPT_DIR}/../lib/error_contract.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
if [[ ! -d "$KANBAN_ROOT" ]]; then
    # BLOCK-WORTHY: with no kanban root there is no kanban.cfg to migrate,
    # and reporting success would let an upgrade claim the new defaults
    # reached an install this script never saw.
    pgai_emit_error_contract "migration.kanban_root_missing" \
        "kanban root not found: ${KANBAN_ROOT} -- set PGAI_AGENT_KANBAN_ROOT_PATH to the install root or run install.sh." \
        "PGAI_AGENT_KANBAN_ROOT_PATH=\"\$HOME/pgai_agent_kanban\" bash $(printf '%q' "$_MIG_SCRIPT_PATH")"
    # CENSUS-ANCHOR: migrate-defaults-root-missing-01
    exit 3
fi

# shellcheck source=../lib/operator_args.sh
source "${_MIG_SCRIPT_DIR}/../lib/operator_args.sh"
# shellcheck source=../lib/projects.sh
source "${_MIG_SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=../lib/project_paths.sh
source "${_MIG_SCRIPT_DIR}/../lib/project_paths.sh"
# shellcheck source=../lib/pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_MIG_SCRIPT_DIR}/../lib/pp_run_ops.sh"
unset _MIG_SCRIPT_DIR

OPERATOR_VALID_FLAGS=(project dry-run help)

_USAGE_TEXT="$(operator_args_render_help_for_flags "migrate_v2.50.9_to_v2.51.0.sh" \
    "Rewrite kanban.cfg [release] ship_posture = matrix to always and [wake] stop_on_blocked = true to false, stating each change and its revert line; report which projects keep their own project.cfg ship_posture (never written). --project narrows only that report." \
    OPERATOR_VALID_FLAGS)"

operator_args_argparse --value-flags "project" -- "$@"

if argparse_missing "project"; then
    # BLOCK-WORTHY: a usage error; nothing has been read or written yet.
    pgai_emit_error_contract "migration.project_value_missing" \
        "--project requires a project name." \
        "bash $(printf '%q' "$_MIG_SCRIPT_PATH") --dry-run"
    # CENSUS-ANCHOR: migrate-defaults-project-value-01
    exit 1
fi

if argparse_has "help" || argparse_has "h"; then
    printf '%s\n' "$_USAGE_TEXT" >&2
    exit 0
fi

if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    # BLOCK-WORTHY: a usage error; nothing has been read or written yet.
    pgai_emit_error_contract "migration.unexpected_positional" \
        "unexpected positional argument: ${ARGPARSE_POSITIONAL[0]} -- this script takes only --project, --dry-run and --help." \
        "bash $(printf '%q' "$_MIG_SCRIPT_PATH") --help"
    # CENSUS-ANCHOR: migrate-defaults-positional-01
    exit 1
fi

operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

DRY_RUN="false"
argparse_has "dry-run" && DRY_RUN="true"
SINGLE_PROJECT="$(operator_args_project)"

# ---------------------------------------------------------------------------
# Projects to report on: the one named, or every registered project.
# ---------------------------------------------------------------------------
_MIG_PROJECTS=()
if [[ -n "$SINGLE_PROJECT" ]]; then
    if ! SINGLE_PROJECT="$(pp_resolve_project_name "$SINGLE_PROJECT")"; then
        # BLOCK-WORTHY: --project names no registered project, so there is
        # nothing to report on; nothing has been written yet.
        # pp_resolve_project_name has already printed the dual-channel
        # refusal (project_resolve.not_found) naming the registered projects.
        # CENSUS-ANCHOR: migrate-defaults-project-unknown-01
        exit 1
    fi
    _MIG_PROJECTS=("$SINGLE_PROJECT")
    echo "migrate_v2.50.9_to_v2.51.0.sh: ship_posture and stop_on_blocked defaults (kanban.cfg; project report for '${SINGLE_PROJECT}')"
else
    while IFS= read -r _mig_project; do
        [[ -z "$_mig_project" ]] && continue
        _MIG_PROJECTS+=("$_mig_project")
    done < <(projects_cfg_list 2>/dev/null || true)
    unset _mig_project
    echo "migrate_v2.50.9_to_v2.51.0.sh: ship_posture and stop_on_blocked defaults (kanban.cfg; project report for every registered project)"
fi

_MIG_ARGS=(
    --kanban-root "$KANBAN_ROOT"
    --change release ship_posture matrix always
        "ship-policy triggers 2, 3 and 8 ship the release marked NON-FUNCTIONAL instead of halting (a push_to_remote = true project also needs ship_posture_confirm_remote = true)"
    --change wake stop_on_blocked true false
        "a BLOCKED task parks only itself and its dependants; the rest of the batch carries on"
    --project-key release ship_posture
    --rerun-command "bash $(printf '%q' "$_MIG_SCRIPT_PATH")"
)
for _mig_project in "${_MIG_PROJECTS[@]+"${_MIG_PROJECTS[@]}"}"; do
    _MIG_ARGS+=(--project "$_mig_project")
done
unset _mig_project
[[ "$DRY_RUN" == "true" ]] && _MIG_ARGS+=(--dry-run)

_MIG_EXIT=0
pp_run_ops pgai_agent_kanban.lib.cfg_default_upgrade "${_MIG_ARGS[@]}" || _MIG_EXIT=$?

if [[ "$_MIG_EXIT" -eq 0 && "${#_MIG_PROJECTS[@]}" -eq 0 ]]; then
    echo "migrate_v2.50.9_to_v2.51.0.sh: no registered projects found (see $(projects_cfg_path)) -- no project.cfg to report on"
fi

exit "$_MIG_EXIT"
