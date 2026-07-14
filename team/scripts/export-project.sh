#!/usr/bin/env bash
# team/scripts/export-project.sh
#
# Package a registered project's entire projects/<name>/ tree into a portable
# .tar.gz archive containing an embedded MANIFEST file.
#
# This script is strictly read-only: it never creates, modifies, or deletes
# anything under projects/<name>/. The only output is the archive written to
# --out (or the default output path).
#
# A MANIFEST file is embedded inside the archive (not written into the live
# project tree). The MANIFEST captures:
#   - kanban_version: the version string from $KANBAN_ROOT/VERSION
#   - export_timestamp: UTC ISO-8601 timestamp at time of export
#   - project_name: the project's registered name
#   - registration: the verbatim [project:NAME] section from projects.cfg
#
# Archive layout:
#   <name>/                   (top-level entry — extract directly into projects/)
#   <name>/MANIFEST           (embedded; not present in the live tree)
#   <name>/project.cfg        (and all other files from projects/<name>/)
#   ...
#
# Usage:
#   export-project.sh --project <name>
#   export-project.sh --project <name> --out <file>
#   export-project.sh --help
#
# Arguments:
#   --project NAME  Required. A registered project name.
#   --out FILE      Optional. Path for the output archive.
#                   Default: ./<name>-export-<UTCstamp>.tar.gz
#                   where UTCstamp = YYYYMMDDTHHMMSSZ
#   --help / -h     Print this help and exit 0.
#
# Exit codes:
#   0 — archive written successfully
#   1 — usage error or unknown option
#   2 — project name is not registered (not found in projects.cfg)
#   3 — kanban root not found (PGAI_AGENT_KANBAN_ROOT_PATH or $HOME/pgai_agent_kanban)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
    exit 3
fi

# ---------------------------------------------------------------------------
# Source helpers
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project help out h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
NAME=""
OUT_FILE=""

# Value-taking flags: project, out.
# Boolean flags: help.
argparse_parse --value-flags "project out" -- "$@"

# Emit clear error for value-taking flags given with no value.
if argparse_missing "out"; then
    echo "ERROR: --out requires a file path argument" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "export-project.sh" \
        "Package a registered project's directory tree into a portable .tar.gz archive." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --out FILE           Output archive path (default: ./<name>-export-<UTCstamp>.tar.gz)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional project name).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: export-project.sh --project <name> [--out <file>]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "export-project.sh" OPERATOR_VALID_FLAGS || exit 1

# Extract value flags.
NAME="$(operator_args_project)"
if argparse_has "out"; then OUT_FILE="${ARGPARSE_FLAGS[out]}"; fi

# ---------------------------------------------------------------------------
# Validate required argument
# ---------------------------------------------------------------------------
if [[ -z "$NAME" ]]; then
    echo "ERROR: project name is required (--project <name>)" >&2
    echo "Usage: export-project.sh --project <name> [--out <file>]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Verify the project is registered in projects.cfg
# ---------------------------------------------------------------------------
PROJECTS_CFG="${KANBAN_ROOT}/projects.cfg"
PROJECT_DIR="${KANBAN_ROOT}/projects/${NAME}"

if [[ ! -f "$PROJECTS_CFG" ]]; then
    echo "ERROR: projects.cfg not found at: $PROJECTS_CFG" >&2
    exit 2
fi

# Check registration: look for [project:NAME] section header in INI format,
# or a colon-legacy line starting with NAME:.
if ! grep -qE "^\[project:${NAME}\]$|^${NAME}:" "$PROJECTS_CFG" 2>/dev/null; then
    echo "ERROR: project '${NAME}' is not registered in projects.cfg" >&2
    echo "  Registered projects:" >&2
    # Extract project names from INI [project:NAME] sections
    grep -oE '^\[project:[a-zA-Z0-9_-]+\]' "$PROJECTS_CFG" 2>/dev/null \
        | sed 's/^\[project://; s/\]$//' \
        | sed 's/^/    /' >&2 || true
    # Also handle colon-legacy format
    grep -E '^[a-zA-Z0-9_-]+:' "$PROJECTS_CFG" 2>/dev/null \
        | cut -d: -f1 \
        | sed 's/^/    /' >&2 || true
    exit 2
fi

# Verify the project directory exists on disk
if [[ ! -d "$PROJECT_DIR" ]]; then
    echo "ERROR: project '${NAME}' is registered but its directory does not exist: $PROJECT_DIR" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Resolve output file path
# ---------------------------------------------------------------------------
if [[ -z "$OUT_FILE" ]]; then
    TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
    OUT_FILE="./${NAME}-export-${TIMESTAMP}.tar.gz"
fi

# ---------------------------------------------------------------------------
# Read kanban version from VERSION file
# ---------------------------------------------------------------------------
VERSION_FILE="${KANBAN_ROOT}/VERSION"
KANBAN_VERSION="unknown"
if [[ -f "$VERSION_FILE" ]]; then
    KANBAN_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null)" || KANBAN_VERSION="unknown"
    [[ -z "$KANBAN_VERSION" ]] && KANBAN_VERSION="unknown"
fi

# ---------------------------------------------------------------------------
# Extract the registration section from projects.cfg
# ---------------------------------------------------------------------------
# Extract the full [project:NAME] INI section (header + all lines until the
# next section or end of file). For colon-legacy format, extract the single
# matching line. The result is stored verbatim in the MANIFEST.
REGISTRATION=""

# Try INI format first (canonical)
if grep -qE "^\[project:${NAME}\]$" "$PROJECTS_CFG" 2>/dev/null; then
    # Extract from the section header until the next [section] header or EOF
    REGISTRATION="$(awk -v name="${NAME}" '
        /^\[project:/ && $0 == "[project:" name "]" { found=1; print; next }
        found && /^\[/ { exit }
        found { print }
    ' "$PROJECTS_CFG")"
else
    # Colon-legacy format: single matching line.
    # Avoid "grep ... | head -n1" under pipefail: if grep emits multiple lines
    # head closes the pipe after the first, grep receives SIGPIPE (exit 141),
    # and pipefail promotes that to a pipeline failure.  Use awk to stop after
    # the first match so no pipe is involved and SIGPIPE cannot occur.
    REGISTRATION="$(awk -v name="${NAME}" '$0 ~ ("^" name ":") { print; exit }' "$PROJECTS_CFG" 2>/dev/null || true)"
fi

# ---------------------------------------------------------------------------
# Build the MANIFEST content
# ---------------------------------------------------------------------------
EXPORT_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

MANIFEST_CONTENT="# MANIFEST
# Generated by export-project.sh
# This file is embedded in the archive only — not written into the live project tree.

kanban_version: ${KANBAN_VERSION}
export_timestamp: ${EXPORT_TIMESTAMP}
project_name: ${NAME}

## registration
${REGISTRATION}
"

# ---------------------------------------------------------------------------
# Build the archive using a temporary directory
# ---------------------------------------------------------------------------
# Use framework temp resolver for temp work (per CODER.md temp hygiene rules)
TEMP_ROOT="$(pgai_temp_dir)/export-project"
mkdir -p "$TEMP_ROOT"

# Create a unique temp directory for this export run
WORK_DIR="$(mktemp -d "${TEMP_ROOT}/export-XXXXXX")"

# Ensure temp dir is cleaned up on exit
trap 'rm -rf "$WORK_DIR"' EXIT

# Write the MANIFEST to a temp file
MANIFEST_FILE="${WORK_DIR}/MANIFEST"
printf '%s' "$MANIFEST_CONTENT" > "$MANIFEST_FILE"

# ---------------------------------------------------------------------------
# Create the archive
# ---------------------------------------------------------------------------
# Strategy: tar the projects/<name>/ tree directly with -C $KANBAN_ROOT/projects
# so the archive top-level entry is <name>/. Then append the MANIFEST by
# transforming its path.
#
# GNU tar supports --transform / --add-file for appending, but for portability
# we use a two-step approach:
#   1. Create a staging directory with <name>/ as the top level
#   2. Copy the project tree into it
#   3. Add MANIFEST at <name>/MANIFEST
#   4. tar czf from the staging directory
#
# This avoids any dependency on GNU tar extensions and keeps the archive
# top-level entry as <name>/ regardless of tar version.

STAGE_DIR="${WORK_DIR}/stage"
mkdir -p "${STAGE_DIR}/${NAME}"

# Copy the project tree into the staging directory
cp -a "${PROJECT_DIR}/." "${STAGE_DIR}/${NAME}/"

# Place the MANIFEST at the top of <name>/
cp "$MANIFEST_FILE" "${STAGE_DIR}/${NAME}/MANIFEST"

# Resolve output file to absolute path before changing directories
if [[ "$OUT_FILE" != /* ]]; then
    OUT_FILE="$(pwd)/${OUT_FILE}"
fi

# Create the archive from the staging directory
# -C ensures the top-level entry is <name>/ (not <stage-dir>/<name>/)
tar czf "$OUT_FILE" -C "$STAGE_DIR" "$NAME"

# ---------------------------------------------------------------------------
# Verify the archive was written
# ---------------------------------------------------------------------------
if [[ ! -f "$OUT_FILE" ]]; then
    echo "ERROR: archive was not created at: $OUT_FILE" >&2
    exit 1
fi

ARCHIVE_SIZE="$(du -sh "$OUT_FILE" 2>/dev/null | cut -f1)"
echo "Exported project '${NAME}' to: ${OUT_FILE} (${ARCHIVE_SIZE})"
echo "  kanban_version:    ${KANBAN_VERSION}"
echo "  export_timestamp:  ${EXPORT_TIMESTAMP}"
echo "  archive contains:  ${NAME}/ (including MANIFEST)"
