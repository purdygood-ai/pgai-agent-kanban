#!/bin/bash
# team/scripts/import-kanban-config.sh
#
# Restore the kanban root configuration from an archive produced by
# team/scripts/export-kanban-config.sh.
#
# The script is the strict counterpart to export-kanban-config.sh:
#   - it reads the MANIFEST embedded in the archive to know which files
#     to restore (and at what kanban_version the archive was created)
#   - it never silently clobbers existing config (use --force to allow
#     overwriting; with --force, each existing destination file is backed
#     up first to <file>.bak-<UTCstamp>)
#   - it restores secrets ONLY when the archive's MANIFEST lists the
#     secrets file in INCLUDED_FILES
#   - it prints a re-source notice when shell-env is written
#   - it never writes MANIFEST itself into KANBAN_ROOT (MANIFEST lives
#     inside the archive only)
#
# Usage:
#   import-kanban-config.sh --file <archive> [--force] [--help | -h]
#
# Options:
#   --file PATH     Path to the .tar.gz export archive (required)
#   --force         Allow overwriting existing config files.  Each existing
#                   destination file is backed up to <file>.bak-<UTCstamp>
#                   before being overwritten.  A single timestamp is used for
#                   all backups produced in one run.
#   --help / -h     Print this help and exit 0
#
# Exit codes:
#   0 — restore completed successfully (or --help)
#   1 — usage error, missing archive, malformed MANIFEST, clobber without
#       --force, or extraction failure

set -euo pipefail

# ---------------------------------------------------------------------------
# Capture script start time for backup stamp
# ---------------------------------------------------------------------------
START_TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"

# ---------------------------------------------------------------------------
# Resolve kanban root and source operator_args.sh
# ---------------------------------------------------------------------------
# Prefer the env var; fall back to the parent directory of this script's
# own scripts/ directory (i.e., the kanban root that contains scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${SCRIPT_DIR%/scripts}}"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Final guard: if the resolved root does not look like a kanban root, bail.
if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run from the kanban scripts/ directory." >&2
    exit 1
fi

# Declared flag vocabulary: all flags this command accepts.
OPERATOR_VALID_FLAGS=(file force help h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: file.
# Boolean: force, help.
argparse_parse \
    --value-flags "file" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "file"; then
    echo "import-kanban-config.sh: error: --file requires a path argument" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "import-kanban-config.sh" \
        "Restore the kanban root configuration from an export archive." \
        OPERATOR_VALID_FLAGS
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional archive path).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "import-kanban-config.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: import-kanban-config.sh --file <archive> [--force] [--help]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "import-kanban-config.sh" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract flag values
# ---------------------------------------------------------------------------
ARCHIVE_PATH=""
FORCE=false

if argparse_has "file";  then ARCHIVE_PATH="${ARGPARSE_FLAGS[file]}"; fi
if argparse_has "force"; then FORCE=true; fi

# ---------------------------------------------------------------------------
# Validate required --file argument
# ---------------------------------------------------------------------------
if [[ -z "$ARCHIVE_PATH" ]]; then
    echo "import-kanban-config.sh: error: --file is required" >&2
    echo "Usage: import-kanban-config.sh --file <archive> [--force] [--help]" >&2
    exit 1
fi

# Resolve to absolute path
if [[ "$ARCHIVE_PATH" != /* ]]; then
    ARCHIVE_PATH="$(pwd)/${ARCHIVE_PATH}"
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
    echo "ERROR: archive not found: $ARCHIVE_PATH" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Build the temp staging directory and register trap for cleanup
# ---------------------------------------------------------------------------
TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-${TMPDIR:-/tmp}/pgai_kanban_tmp}/import-kanban-config"
mkdir -p "$TEMP_ROOT"

WORK_DIR="$(mktemp -d "${TEMP_ROOT}/import-XXXXXX")"

# Clean up staging dir on exit (covers success, failure, and signals)
trap 'rm -rf "$WORK_DIR"' EXIT

STAGE_DIR="${WORK_DIR}/stage"
mkdir -p "$STAGE_DIR"

# ---------------------------------------------------------------------------
# Extract the archive into the staging directory
# ---------------------------------------------------------------------------
if ! tar xzf "$ARCHIVE_PATH" -C "$STAGE_DIR" 2>/dev/null; then
    echo "ERROR: failed to extract archive: $ARCHIVE_PATH" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Read and parse the MANIFEST
# ---------------------------------------------------------------------------
MANIFEST_FILE="${STAGE_DIR}/MANIFEST"
if [[ ! -f "$MANIFEST_FILE" ]]; then
    echo "ERROR: archive does not contain a MANIFEST file: $ARCHIVE_PATH" >&2
    exit 1
fi

# Extract kanban_version
ARCHIVE_KANBAN_VERSION=""
ARCHIVE_KANBAN_VERSION="$(grep '^kanban_version:' "$MANIFEST_FILE" | sed 's/^kanban_version:[[:space:]]*//' | tr -d '[:space:]')" || true

if [[ -z "$ARCHIVE_KANBAN_VERSION" ]]; then
    echo "ERROR: MANIFEST missing required field 'kanban_version'" >&2
    exit 1
fi

# Extract INCLUDED_FILES list (lines between INCLUDED_FILES: and the next section / end of file)
# The section is indented with two spaces per the MANIFEST schema.
INCLUDED_FILES=()
in_included=false
while IFS= read -r line; do
    if [[ "$line" == "INCLUDED_FILES:" ]]; then
        in_included=true
        continue
    fi
    if [[ "$in_included" == true ]]; then
        # A blank line or a new section header ends the list
        if [[ -z "$line" || "$line" =~ ^[A-Z_]+: ]]; then
            in_included=false
            continue
        fi
        # Strip leading whitespace and collect the filename
        fname="${line#  }"
        fname="${fname## }"
        fname="${fname%%[[:space:]]}"
        [[ -n "$fname" ]] && INCLUDED_FILES+=("$fname")
    fi
done < "$MANIFEST_FILE"

if [[ ${#INCLUDED_FILES[@]} -eq 0 ]]; then
    echo "ERROR: MANIFEST INCLUDED_FILES section is empty or missing" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Version mismatch warning (non-fatal)
# ---------------------------------------------------------------------------
INSTALLED_VERSION="unknown"
VERSION_FILE="${KANBAN_ROOT}/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
    INSTALLED_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null)" || INSTALLED_VERSION="unknown"
    [[ -z "$INSTALLED_VERSION" ]] && INSTALLED_VERSION="unknown"
fi

if [[ "$ARCHIVE_KANBAN_VERSION" != "$INSTALLED_VERSION" ]]; then
    echo "WARN: version mismatch — archive was created with kanban ${ARCHIVE_KANBAN_VERSION}, installed version is ${INSTALLED_VERSION}" >&2
fi

# ---------------------------------------------------------------------------
# Build the list of files to restore (MANIFEST entries except MANIFEST itself)
# ---------------------------------------------------------------------------
RESTORE_FILES=()
for fname in "${INCLUDED_FILES[@]}"; do
    [[ "$fname" == "MANIFEST" ]] && continue
    # Only restore files that are actually present in the staged archive
    if [[ -f "${STAGE_DIR}/${fname}" ]]; then
        RESTORE_FILES+=("$fname")
    fi
done

if [[ ${#RESTORE_FILES[@]} -eq 0 ]]; then
    echo "INFO: archive contains no restorable config files (only MANIFEST)" >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Per-file clobber guard
# Without --force: collect ALL conflicts first, then exit non-zero without
# writing anything.
# ---------------------------------------------------------------------------
CLOBBER_FILES=()
for fname in "${RESTORE_FILES[@]}"; do
    dest="${KANBAN_ROOT}/${fname}"
    if [[ -f "$dest" ]]; then
        CLOBBER_FILES+=("$fname")
    fi
done

if [[ ${#CLOBBER_FILES[@]} -gt 0 && "$FORCE" == false ]]; then
    echo "ERROR: the following files already exist in ${KANBAN_ROOT} and would be overwritten:" >&2
    for fname in "${CLOBBER_FILES[@]}"; do
        echo "  ${fname}" >&2
    done
    echo "" >&2
    echo "Run with --force to overwrite (existing files will be backed up first)." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Restore files
# With --force: backup existing files before overwriting.
# ---------------------------------------------------------------------------
SHELL_ENV_WRITTEN=false

for fname in "${RESTORE_FILES[@]}"; do
    src="${STAGE_DIR}/${fname}"
    dest="${KANBAN_ROOT}/${fname}"

    # Backup existing file when --force is active
    if [[ -f "$dest" && "$FORCE" == true ]]; then
        backup="${dest}.bak-${START_TIMESTAMP}"
        cp "$dest" "$backup"
        echo "  backed up: ${fname} -> ${fname}.bak-${START_TIMESTAMP}"
    fi

    # Ensure parent directory exists (handles nested paths if any ever appear)
    mkdir -p "$(dirname "$dest")"

    cp "$src" "$dest"
    echo "  restored: ${fname}"

    if [[ "$fname" == "shell-env" ]]; then
        SHELL_ENV_WRITTEN=true
    fi
done

# ---------------------------------------------------------------------------
# Shell-env re-source notice
# ---------------------------------------------------------------------------
if [[ "$SHELL_ENV_WRITTEN" == true ]]; then
    echo ""
    echo "NOTICE: shell-env was written to ${KANBAN_ROOT}/shell-env"
    echo "  To apply the new settings in your current shell, run:"
    echo "    source ${KANBAN_ROOT}/shell-env"
    echo "  Or open a new shell for the changes to take effect automatically."
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "Import complete."
echo "  archive:          ${ARCHIVE_PATH}"
echo "  kanban_version:   ${ARCHIVE_KANBAN_VERSION}"
echo "  restored files:   ${RESTORE_FILES[*]}"
