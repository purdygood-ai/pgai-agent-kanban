#!/usr/bin/env bash
# scripts/remove-project.sh
#
# Unregister a project from projects.cfg. With --force, also delete the
# project directory under projects/<name>/.
#
# Refuses to delete a project that has an Active RC in flight unless --force
# is given (and even then prints a warning).
#
# projects.cfg format handling:
#   For INI-format files, the entire [project:NAME] section block (header and
#   all contiguous field lines) is removed. For colon-legacy files, the
#   matching colon-format line is removed. Format is detected automatically
#   via projects_cfg_format — no inline grep.
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
#   2 — project has Active RC ≠ none and --force not given
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
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary: drives --help output; flags the command accepts.
OPERATOR_VALID_FLAGS=(project force dry-run help)

NAME=""
FORCE="false"
DRY_RUN="false"

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

# Extract boolean flags.
if argparse_has "force";   then FORCE="true"; fi
if argparse_has "dry-run"; then DRY_RUN="true"; fi

# Extract value flags.
NAME="$(operator_args_project)"

if [[ -z "$NAME" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    echo "Usage: remove-project.sh --project <name> [--force] [--dry-run]" >&2
    exit 1
fi

PROJECT_DIR="${KANBAN_ROOT}/projects/${NAME}"

# --- Active RC guard ---
if [[ -f "${PROJECT_DIR}/release-state.md" ]]; then
    active_rc="$(awk '/^## Active RC/{flag=1;next} /^##/{flag=0} flag && NF{print;exit}' "${PROJECT_DIR}/release-state.md" | tr -d '[:space:]')"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        if [[ "$FORCE" != "true" ]]; then
            echo "ERROR: project '${NAME}' has Active RC = ${active_rc}" >&2
            echo "       Refusing to remove a project with an active release in flight." >&2
            echo "       Pass --force if you really want to discard the in-flight work." >&2
            exit 2
        fi
        echo "WARNING: removing project '${NAME}' with Active RC = ${active_rc} (--force given)" >&2
    fi
fi

# --- Detect projects.cfg format ---
_cfg_file="$(projects_cfg_path)"
_cfg_fmt="$(projects_cfg_format "$_cfg_file")"

# --- Print plan ---
echo "Removing project: $NAME"
if [[ "$FORCE" == "true" ]]; then
    if [[ -d "$PROJECT_DIR" ]]; then
        echo "  - will DELETE: $PROJECT_DIR"
    else
        echo "  - directory absent: $PROJECT_DIR (skipping delete)"
    fi
else
    echo "  - directory will be left in place: $PROJECT_DIR"
    echo "  - to fully delete, re-run with --force"
fi
if projects_cfg_has "$NAME"; then
    if [[ "$_cfg_fmt" == "colon-legacy" ]]; then
        echo "  - will unregister from projects.cfg (colon-legacy: removes matching line)"
    else
        echo "  - will unregister from projects.cfg (INI: removes [project:${NAME}] section)"
    fi
else
    echo "  - already absent from projects.cfg"
fi
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] No changes will be made."
    exit 0
fi

# --- Emit colon-legacy format warning ---
# Format detection uses projects_cfg_format (no inline grep).
if [[ "$_cfg_fmt" == "colon-legacy" ]]; then
    echo "WARNING: projects.cfg is in colon-legacy format." >&2
    echo "WARNING: Removal will delete the matching colon-format line for '${NAME}'." >&2
    echo "WARNING: Consider migrating to INI format: scripts/migrate/projects-cfg.sh" >&2
fi
unset _cfg_file _cfg_fmt

# --- Execute ---
projects_cfg_remove "$NAME"
echo "  + projects.cfg updated"

if [[ "$FORCE" == "true" && -d "$PROJECT_DIR" ]]; then
    rm -rf "$PROJECT_DIR"
    echo "  + deleted: $PROJECT_DIR"
fi

echo ""
echo "Done."
