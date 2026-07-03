#!/usr/bin/env bash
# scripts/add-project.sh
#
# Register an existing on-disk project directory in projects.cfg. The project
# directory must already exist under $KANBAN_ROOT/projects/<name>/ (e.g. it
# was migrated from another machine, or recreated by hand).
#
# This is the read-only counterpart of create-project.sh: it does not touch
# the project directory contents — only updates the registry.
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

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

NAME=""
PRIORITY=""
COLOR=""
DRY_RUN="false"
NO_MIGRATE="false"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project dry-run help priority color no-migrate h)

# Value-taking flags: script-specific (project, priority, color).
# Boolean flags: dry-run, no-migrate, help.
argparse_parse --value-flags "project priority color" -- "$@"

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

# Extract boolean flags.
if argparse_has "dry-run";    then DRY_RUN="true"; fi
if argparse_has "no-migrate"; then NO_MIGRATE="true"; fi

# Extract value flags.
NAME="$(operator_args_project)"
if argparse_has "priority"; then PRIORITY="${ARGPARSE_FLAGS[priority]}"; fi
if argparse_has "color"; then
    _validate_color_flag "--color" "${ARGPARSE_FLAGS[color]}"
    COLOR="${ARGPARSE_FLAGS[color]}"
fi

if [[ -z "$NAME" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    echo "Usage: add-project.sh --project <name> [--priority <int>]" >&2
    exit 1
fi

PROJECT_DIR="${KANBAN_ROOT}/projects/${NAME}"

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project directory does not exist: $PROJECT_DIR" >&2
    echo "       To create a new project, use: create-project.sh ${NAME}" >&2
    exit 2
fi

# --- Auto-assign color if not operator-supplied and this is a new registration ---
# Priority is auto-assigned by projects_cfg_add when omitted; color uses the
# next unused palette entry as a default.
if [[ -z "$COLOR" ]] && ! projects_cfg_has "$NAME"; then
    COLOR="$(projects_cfg_next_color)"
fi

# --- Detect projects.cfg format ---
projects_cfg_ensure
_cfg_file="$(projects_cfg_path)"
_cfg_fmt="$(projects_cfg_format "$_cfg_file")"

# --- Print plan ---
echo "Registering project: $NAME"
echo "  Project root: $PROJECT_DIR"

if projects_cfg_has "$NAME"; then
    current="$(projects_cfg_priority "$NAME")"
    if [[ -n "$PRIORITY" && "$current" != "$PRIORITY" ]]; then
        echo "  Already registered at priority=${current}; will update to ${PRIORITY}"
    else
        echo "  Already registered at priority=${current}; nothing to do."
        exit 0
    fi
else
    echo "  Will register at priority=${PRIORITY:-<next available>}, color=${COLOR:-<next available>}"
    if [[ "$_cfg_fmt" == "colon-legacy" ]]; then
        if [[ "$NO_MIGRATE" == "true" ]]; then
            echo "  projects.cfg:   colon-legacy (--no-migrate: will append colon line; WARNING: mixed format)"
        else
            echo "  projects.cfg:   colon-legacy (will auto-migrate to INI before registering)"
        fi
    else
        echo "  projects.cfg:   INI format"
    fi
fi
echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[DRY-RUN] No changes will be made."
    exit 0
fi

# --- Handle projects.cfg format: auto-migrate or warn ---
if [[ "$_cfg_fmt" == "colon-legacy" ]]; then
    if [[ "$NO_MIGRATE" == "true" ]]; then
        echo "WARNING: projects.cfg is in colon-legacy format and --no-migrate was given." >&2
        echo "WARNING: The new project will be appended as a colon-format line." >&2
        echo "WARNING: This produces a mixed-format file that is not a supported stable state." >&2
        echo "WARNING: Run 'scripts/migrate/projects-cfg.sh' (or re-run without --no-migrate)" >&2
        echo "WARNING: to convert projects.cfg to INI format." >&2
    else
        # Auto-migrate: convert colon-legacy to INI before registering.
        projects_cfg_colon_to_ini "$_cfg_file"
    fi
fi
unset _cfg_file _cfg_fmt

# --- Execute ---
projects_cfg_add "$NAME" "${PRIORITY}" "${COLOR}"
final_priority="$(projects_cfg_priority "$NAME")"
final_color="$(projects_cfg_color "$NAME")"
echo "  + registered (priority=${final_priority}, color=${final_color})"

echo ""
echo "Done."
