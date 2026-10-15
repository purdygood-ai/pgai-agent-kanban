#!/usr/bin/env bash
# team/scripts/list-rejected.sh
#
# Inventory of quarantined files across all projects (or a single project).
#
# Discovery quarantines a file it keeps rejecting into the project's
# rejected/ directory (<project>/rejected/, beside a <filename>.reason
# sidecar); with no project name it falls back to the intake directory's own
# .rejected/ (priority/.rejected/, bugs/.rejected/, requirements/.rejected/).
# Both are read. For each project, the quarantined files are grouped by the
# intake directory they came from (the sidecar's original_dir/original_type,
# else the BUG-/PRIORITY- name prefix; dir=rejected when that cannot be told),
# each with its quarantine timestamp (mtime) and the rejection reason
# (derived from the filename contract check; "unknown" for unexpected
# filename shapes).
#
# Output format (one section per intake directory with quarantined files;
# an origin with none prints no section):
#
#   project=<name> dir=priority
#     PRIORITY-12-example.md  (quarantined 2026-05-17T12:31)
#     Reason: filename does not match expected pattern (^PRIORITY-[0-9]{4}(?:-[0-9]{8})?-[a-z0-9][a-z0-9-]*\.md$)
#
#   To recover: scripts/recover-rejected.sh --project <name> --file <filename> [--rename NEW]
#
# Usage:
#   list-rejected.sh [--project <name>]
#
# Options:
#   --project NAME   Limit the listing to one project (default: every project;
#                    PGAI_PROJECT_NAME is not read)
#   --help, -h       Print this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH — kanban root (default: $HOME/pgai_agent_kanban)
#   NO_COLOR=1 — suppress ANSI codes

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Declared flag vocabulary: all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: project.
# Boolean: help.
operator_args_argparse \
    --value-flags "project" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "project"; then
    echo "list-rejected.sh: error: --project requires a value" >&2
    exit 1
fi

# Handle --help / -h.
# The built-in --project line advertises a $PGAI_PROJECT_NAME default, but this
# script reads only an explicit --project and otherwise lists every project, so
# its line is overridden.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Limit the listing to one project (default: every project; PGAI_PROJECT_NAME is not read)"
)
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "list-rejected.sh" \
        "Inventory of quarantined files across all projects (or a single project)." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments.
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "list-rejected.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: list-rejected.sh [--project <name>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "list-rejected.sh" OPERATOR_VALID_FLAGS || exit 1

# Extract project filter.
# Use argparse_has to check explicit flag, not the env-var fallback from
# operator_args_project — list-rejected iterates all projects when no --project is given.
PROJECT_NAME=""
if argparse_has "project"; then
    PROJECT_NAME="${ARGPARSE_FLAGS[project]}"
fi

# ---------------------------------------------------------------------------
# Print the inventory through pgai_agent_kanban.ops.read.list_rejected -- the
# same function GET /rejected calls. It reads rejected/ and the legacy
# <intake>/.rejected/ directories of every project directory (or only
# --project), derives each file's reason from its name, and prints the
# sections above; colour is
# off when TERM is dumb or NO_COLOR=1. A missing projects/ directory or an
# unknown --project exits 1 with the reason on stderr.
# ---------------------------------------------------------------------------
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"
export KANBAN_ROOT

_list_rc=0
pp_run_ops pgai_agent_kanban.ops list_rejected "${KANBAN_ROOT}" "${PROJECT_NAME}" || _list_rc=$?
exit "${_list_rc}"
