#!/usr/bin/env bash
# scripts/remove-project.sh
#
# Unregister a project from projects.cfg. With --force, also delete the
# project directory under projects/<name>/.
#
# Refuses to remove a project that has an Active RC in flight, with or
# without --force: the refusal is the dual-channel error/message/remedy
# block (remove_project.active_rc), and nothing is changed. Ship or cancel
# the RC first. The Active RC is read through cm/read_state_field.py; a
# field that does not validate is refused the same way
# (remove_project.release_state_invalid).
#
# Thin adapter: parses the flags via operator_args.sh, then hands them to
# the Python remove_project function in pgai_agent_kanban.ops.projects (the
# same function POST /operations/remove-project calls) through pp_run_ops,
# which applies the guard, prints the plan and makes the change.
#
# projects.cfg format handling:
#   For INI-format files, the entire [project:NAME] section block (header and
#   every line up to the next section header) is removed. For colon-legacy
#   files, the matching colon-format line is removed. Format is detected
#   automatically.
#
#   When projects.cfg is in colon-legacy format, a warning is emitted
#   encouraging migration to INI format. The remove still succeeds.
#
# Usage:
#   remove-project.sh --project <name>                # unregister only (safe default)
#   remove-project.sh --project <name> --force        # also rm -rf the directory
#   remove-project.sh --project <name> --dry-run      # preview, no changes
#
# Exit codes:
#   0 — removed successfully (or already absent)
#   1 — usage error
#   2 — project has an Active RC (with or without --force)
#   3 — kanban root not found

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

# Declared flag vocabulary: drives --help output; flags the command accepts.
OPERATOR_VALID_FLAGS=(project force dry-run help)

# Parse arguments: operator_args_parse normalizes -h -> --help.
# Value-taking: project (inherited from canonical set).
# Boolean: force, dry-run, help.
operator_args_parse "$@"

# Handle --help / -h.
if argparse_has "help"; then
    operator_args_render_help_for_flags "remove-project.sh" \
        "Unregister a project from projects.cfg; with --force also delete the directory." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: remove-project.sh --project <name> [--force] [--dry-run]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS || exit 1

# NOTE: remove-project.sh intentionally does NOT route the name through
# pp_resolve_project_name. Unlike a lookup verb, remove-project.sh's own
# contract already handles "not currently registered" gracefully (see
# "already absent from projects.cfg") — that is exactly the state its own
# second, idempotent invocation produces. Requiring registry membership up
# front (what pp_resolve_project_name does) would turn that already-correct
# idempotent no-op into a hard refusal. This is the same reasoning that
# excludes create-project.sh / add-project.sh: those name a
# NOT-YET-registered project; remove-project.sh's re-run names a
# NO-LONGER-registered one. Prefix convenience is traded for that invariant
# on this one verb.
_ops_args=(--kanban-root="$KANBAN_ROOT" --project="$(operator_args_project)")
if argparse_has "force";   then _ops_args+=(--force); fi
if argparse_has "dry-run"; then _ops_args+=(--dry-run); fi

# Exit codes pass through: 1 for a usage error, 2 for an Active RC.
_rc=0
pp_run_ops pgai_agent_kanban.ops remove_project "${_ops_args[@]}" || _rc=$?
exit "$_rc"
