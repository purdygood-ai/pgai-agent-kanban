#!/usr/bin/env bash
# regenerate-changelog.sh
# Operator-invoked script to rebuild CHANGELOG.md from all release-notes/*.md
# files in a project root.
#
# Usage:
#   regenerate-changelog.sh --project <name> [--output <path>]
#
# Options:
#   --project NAME         Registered project name. The project root is resolved
#                          as $KANBAN_ROOT/projects/<name>/. Required.
#   --output PATH          Output path for CHANGELOG.md.
#                          Default: <project-root>/CHANGELOG.md
#   --help, -h             Print this help and exit
#
# The script:
#   1. Resolves the project root from --project <name>.
#   2. Reads all *.md files in <project-root>/release-notes/ whose names match
#      the pattern v<major>.<minor>.<patch>*.md (semver prefix).
#   3. Sorts them by semantic version (newest first).
#   4. For each file, strips the top-level H1 title line.
#   5. Assembles CHANGELOG.md with a standard header, each release as a
#      ## vX.Y.Z — <date> section. The date is the file's **Release Date:**
#      value, else its **Date:** value, else 'unknown' with one stderr
#      warning naming the file -- the same rule the release-time changelog
#      writer applies (pgai_agent_kanban.cm.release_notes_date).
#   6. Writes the result to the output path and exits 0.
#
# Steps 2-6 run in pgai_agent_kanban.cm.regenerate_changelog.
#
# Exits non-zero on error (missing --project, project not found, missing release-notes/ dir).

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/lib/pp_run_ops.sh"

# Resolve kanban root.
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help output h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: project, output.
# Boolean: help.
operator_args_argparse \
    --value-flags "project output" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "project"; then
    echo "regenerate-changelog.sh: error: --project requires a value" >&2
    exit 1
fi
if argparse_missing "output"; then
    echo "regenerate-changelog.sh: error: --output requires a value" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "regenerate-changelog.sh" \
        "Rebuild CHANGELOG.md from all release-notes/*.md files in a project root." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --output PATH      Output path (default: <project-root>/CHANGELOG.md)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "regenerate-changelog.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: regenerate-changelog.sh --project <name> [--output <path>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "regenerate-changelog.sh" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract flag values
# ---------------------------------------------------------------------------
PROJECT_NAME=""
OUTPUT_PATH=""

if argparse_has "project"; then PROJECT_NAME="${ARGPARSE_FLAGS[project]}"; fi
if argparse_has "output";  then OUTPUT_PATH="${ARGPARSE_FLAGS[output]}"; fi

# ---------------------------------------------------------------------------
# Validate required --project argument
# ---------------------------------------------------------------------------
PROJECT_NAME="$(operator_args_project)"
if [[ -z "$PROJECT_NAME" ]]; then
    echo "regenerate-changelog.sh: error: --project is required (or set PGAI_PROJECT_NAME)" >&2
    echo "Usage: regenerate-changelog.sh --project <name> [--output <path>]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Resolve project root from project name
# ---------------------------------------------------------------------------
PROJECT_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}"

if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "regenerate-changelog.sh: error: project '${PROJECT_NAME}' not found (expected: ${PROJECT_DIR})" >&2
    exit 1
fi

RELEASE_NOTES_DIR="${PROJECT_DIR}/release-notes"
if [[ ! -d "$RELEASE_NOTES_DIR" ]]; then
    echo "regenerate-changelog.sh: error: release-notes/ directory not found under: $PROJECT_DIR" >&2
    exit 1
fi

if [[ -z "$OUTPUT_PATH" ]]; then
    OUTPUT_PATH="${PROJECT_DIR}/CHANGELOG.md"
fi

# ---------------------------------------------------------------------------
# Render: semver sort, date rule and assembly live in the Python module
# ---------------------------------------------------------------------------
pp_run_ops pgai_agent_kanban.cm.regenerate_changelog "$RELEASE_NOTES_DIR" "$OUTPUT_PATH"
