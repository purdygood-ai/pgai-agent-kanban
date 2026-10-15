#!/usr/bin/env bash
# scripts/add-project.sh
#
# Register an existing on-disk project directory in projects.cfg. The project
# directory must already exist under $KANBAN_ROOT/projects/<name>/ (e.g. it
# was migrated from another machine, or recreated by hand).
#
# This is the registry-only counterpart of create-project.sh: it updates the
# registry, and the only things it may write under the project directory are
# two files, each only when absent:
#   - projects/<name>/README.md — seeded from
#     templates/project/README-TEMPLATE.md beside this script's scripts/
#     directory, with a line telling the operator to fill it in;
#   - projects/<name>/project.cfg — rendered from the project.cfg template
#     (config/project.cfg_example, every key documented) with this project's
#     name and the template's defaults, narrated in one line; set its
#     dev_tree_path and git_repo_url afterwards. Not written when a legacy
#     PROJECT.cfg is present.
# An existing README.md, project.cfg or PROJECT.cfg is never touched.
#
# Thin adapter: parses the flags via operator_args.sh, then hands the values
# to the Python add_project function in pgai_agent_kanban.ops.projects (the
# same function POST /operations/add-project calls) through pp_run_ops,
# which validates them, prints the plan and updates projects.cfg.
#
# projects.cfg format handling:
#   When projects.cfg is in colon-legacy format, this script automatically
#   converts it to INI format before registering the project (auto-migrate).
#   To suppress migration and append in legacy colon format instead, pass
#   --no-migrate. A mixed-format warning is emitted in that case because the
#   resulting file will have an INI section and colon lines, which is not
#   a supported stable state.
#
# Usage:
#   add-project.sh --project <name>                       # register at next-available priority and auto-assigned color
#   add-project.sh --project <name> --priority <int>      # register at specific priority
#   add-project.sh --project <name> --color '#RRGGBB'     # register with specific display color
#                                                         # (quote hex colors: unquoted '#' is treated as a shell comment)
#   add-project.sh --project <name> --no-migrate          # skip auto-migration; append colon line (not recommended)
#   add-project.sh --project <name> --dry-run             # preview, no writes
#
# Exit codes:
#   0 — registered (or already present)
#   1 — usage error
#   2 — project directory does not exist
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

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project dry-run help priority color no-migrate h)

# Value-taking flags: script-specific (project, priority, color).
# Boolean flags: dry-run, no-migrate, help.
operator_args_argparse --value-flags "project priority color" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "priority"; then
    echo "ERROR: --priority requires a value." >&2
    exit 1
fi
if argparse_missing "color"; then
    echo "ERROR: --color requires a value." >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "add-project.sh" \
        "Register an existing on-disk project directory in projects.cfg." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --priority INT       registry priority (default: next available)" \
        "  --color '#RRGGBB'    registry display color (default: next unused)" \
        "  --no-migrate         skip auto-migration of projects.cfg (not recommended)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: add-project.sh --project <name> [--priority <int>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "add-project.sh" OPERATOR_VALID_FLAGS || exit 1

# --- Hand the values to add_project ---
# Only flags the operator gave are passed (as --name=value). This script's own
# scripts/ directory is passed so the project README template is read from
# ../templates/project/ beside it.
_ops_args=(--kanban-root="$KANBAN_ROOT" --scripts-dir="$SCRIPT_DIR" --project="$(operator_args_project)")
if argparse_has "priority";   then _ops_args+=(--priority="${ARGPARSE_FLAGS[priority]}"); fi
if argparse_has "color";      then _ops_args+=(--color="${ARGPARSE_FLAGS[color]}"); fi
if argparse_has "no-migrate"; then _ops_args+=(--no-migrate); fi
if argparse_has "dry-run";    then _ops_args+=(--dry-run); fi

# Exit codes pass through: 1 for a usage or value error, 2 when the project
# directory does not exist.
_rc=0
pp_run_ops pgai_agent_kanban.ops add_project "${_ops_args[@]}" || _rc=$?
exit "$_rc"
