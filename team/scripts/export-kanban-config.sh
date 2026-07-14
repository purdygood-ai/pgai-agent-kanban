#!/bin/bash
# team/scripts/export-kanban-config.sh
#
# Bundle the kanban root configuration into a portable .tar.gz archive so an
# install's config can be replicated on a new VPS without hand-copying files.
#
# This script is strictly READ-ONLY with respect to root config files:
# it reads kanban.cfg, projects.cfg, shell-env, active-provider, and (with
# --include-secrets) secrets, but NEVER modifies, renames, or recreates any of
# them. The only output is the archive written to --file (default path below).
#
# Always-bundled files (when present):
#   kanban.cfg
#   projects.cfg
#   shell-env
#   active-provider
#   MANIFEST          (embedded; not written into the live kanban root)
#
# Secrets handling:
#   secrets is EXCLUDED by default.
#   Pass --include-secrets to include it. A loud stderr warning is emitted
#   when --include-secrets is used. The warning uses the words "credential"
#   and "secret" and instructs secure transfer / no-commit.
#
# MANIFEST schema (forward-contract for v0.52.0 import-kanban-config):
#   kanban_version: <string from VERSION file or "unknown">
#   export_timestamp: <UTC ISO-8601>
#   include_secrets: <true|false>
#   INCLUDED_FILES:
#   <one filename per line, relative to kanban root>
#   MISSING_FILES:
#   <one filename per line of expected files that were absent>
#
# Usage:
#   export-kanban-config.sh [--file <path>] [--include-secrets] [--help]
#
# Options:
#   --file <path>      Path for the output archive.
#                      Default: ./kanban-config-export-<UTCstamp>.tar.gz
#                      where UTCstamp = YYYYMMDDTHHMMSSZ (script start time)
#   --include-secrets  Include the secrets file in the archive (see warning above)
#   --help / -h        Print this help and exit 0
#
# Exit codes:
#   0 — archive written successfully (or --help)
#   1 — usage error, kanban root not found, or archive creation failure

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Capture script start time for default archive name
# ---------------------------------------------------------------------------
START_TIMESTAMP="$(date -u '+%Y%m%dT%H%M%SZ')"

# ---------------------------------------------------------------------------
# Resolve kanban root and source operator_args.sh
# ---------------------------------------------------------------------------
# Prefer the env var; fall back to the parent directory of this script's
# own scripts/ directory (i.e., the kanban root that contains scripts/).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Final guard: if the resolved root does not look like a kanban root, bail.
if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run from the kanban scripts/ directory." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Declared flag vocabulary for all flags this command accepts.
# ---------------------------------------------------------------------------
OPERATOR_VALID_FLAGS=(file help include-secrets h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: script-specific (file)
# Boolean: help, include-secrets.
argparse_parse \
    --value-flags "file" \
    -- "$@"

# Emit error for value-taking flags given with no value.
if argparse_missing "file"; then
    echo "export-kanban-config.sh: error: --file requires a path argument" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "export-kanban-config.sh" \
        "Bundle the kanban root configuration into a portable .tar.gz archive." \
        OPERATOR_VALID_FLAGS \
        "  (--file default: ./kanban-config-export-<UTCstamp>.tar.gz)" \
        "  --include-secrets  Include the secrets file in the archive (emits loud warning)"
    exit 0
fi

# Reject unexpected positional arguments.
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "export-kanban-config.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: export-kanban-config.sh [--file <path>] [--include-secrets] [--help]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "export-kanban-config.sh" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract flag values
# ---------------------------------------------------------------------------
OUT_FILE=""
INCLUDE_SECRETS=false

if argparse_has "file";             then OUT_FILE="${ARGPARSE_FLAGS[file]}"; fi
if argparse_has "include-secrets";  then INCLUDE_SECRETS=true; fi

# ---------------------------------------------------------------------------
# Resolve output file path (default uses script start timestamp)
# ---------------------------------------------------------------------------
if [[ -z "$OUT_FILE" ]]; then
    OUT_FILE="./kanban-config-export-${START_TIMESTAMP}.tar.gz"
fi

# Resolve to absolute path before any directory changes
if [[ "$OUT_FILE" != /* ]]; then
    OUT_FILE="$(pwd)/${OUT_FILE}"
fi

# ---------------------------------------------------------------------------
# Emit credential warning when --include-secrets is used
# ---------------------------------------------------------------------------
if [[ "$INCLUDE_SECRETS" == true ]]; then
    echo "WARNING: --include-secrets is set." >&2
    echo "The archive will contain the secrets file, which holds credentials" >&2
    echo "and sensitive access material." >&2
    echo "" >&2
    echo "  * Transfer this archive only over encrypted channels (e.g., scp, sftp)." >&2
    echo "  * Store it only in locations with appropriate access controls." >&2
    echo "  * NEVER commit this archive to a git repository or any version-control" >&2
    echo "    system — doing so exposes your credentials to all repository readers." >&2
    echo "  * Delete the archive after the import is complete." >&2
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
# Build the temp staging directory
# ---------------------------------------------------------------------------
TEMP_ROOT="${PGAI_AGENT_KANBAN_TEMP_DIR:-${TMPDIR:-/tmp}/pgai_kanban_tmp}/export-kanban-config"
mkdir -p "$TEMP_ROOT"

WORK_DIR="$(mktemp -d "${TEMP_ROOT}/export-XXXXXX")"

# Clean up staging dir on exit (covers success, failure, and signals)
trap 'rm -rf "$WORK_DIR"' EXIT

STAGE_DIR="${WORK_DIR}/stage"
mkdir -p "$STAGE_DIR"

# ---------------------------------------------------------------------------
# Collect files: always-included set, then conditionally secrets
# ---------------------------------------------------------------------------
ALWAYS_FILES=(kanban.cfg projects.cfg shell-env active-provider)
INCLUDED_FILES=()
MISSING_FILES=()

for fname in "${ALWAYS_FILES[@]}"; do
    src="${KANBAN_ROOT}/${fname}"
    if [[ -f "$src" ]]; then
        cp "$src" "${STAGE_DIR}/${fname}"
        INCLUDED_FILES+=("$fname")
    else
        MISSING_FILES+=("$fname")
    fi
done

if [[ "$INCLUDE_SECRETS" == true ]]; then
    secrets_src="${KANBAN_ROOT}/secrets"
    if [[ -f "$secrets_src" ]]; then
        cp "$secrets_src" "${STAGE_DIR}/secrets"
        INCLUDED_FILES+=("secrets")
    else
        MISSING_FILES+=("secrets")
    fi
fi

# ---------------------------------------------------------------------------
# Build MANIFEST content
# MANIFEST is always included; add it to the list before writing so the
# MANIFEST file itself appears in the INCLUDED_FILES section.
# ---------------------------------------------------------------------------
INCLUDED_FILES+=("MANIFEST")
EXPORT_TIMESTAMP="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

{
    echo "# MANIFEST"
    echo "# Generated by export-kanban-config.sh"
    echo "# This file is embedded in the archive only — not written into the live kanban root."
    echo ""
    echo "kanban_version: ${KANBAN_VERSION}"
    echo "export_timestamp: ${EXPORT_TIMESTAMP}"
    echo "include_secrets: ${INCLUDE_SECRETS}"
    echo ""
    echo "INCLUDED_FILES:"
    for f in "${INCLUDED_FILES[@]}"; do
        echo "  ${f}"
    done
    if [[ ${#MISSING_FILES[@]} -gt 0 ]]; then
        echo ""
        echo "MISSING_FILES:"
        for f in "${MISSING_FILES[@]}"; do
            echo "  ${f}"
        done
    fi
} > "${STAGE_DIR}/MANIFEST"

# ---------------------------------------------------------------------------
# Create the archive
# ---------------------------------------------------------------------------
# Build an explicit file list from the stage directory so tar packs only
# what we staged (no glob expansion surprises).
STAGED_NAMES=()
for fname in "${INCLUDED_FILES[@]}"; do
    if [[ -f "${STAGE_DIR}/${fname}" ]]; then
        STAGED_NAMES+=("$fname")
    fi
done

tar czf "$OUT_FILE" -C "$STAGE_DIR" "${STAGED_NAMES[@]}"

# ---------------------------------------------------------------------------
# Verify the archive was written
# ---------------------------------------------------------------------------
if [[ ! -f "$OUT_FILE" ]]; then
    echo "ERROR: archive was not created at: $OUT_FILE" >&2
    exit 1
fi

ARCHIVE_SIZE="$(du -sh "$OUT_FILE" 2>/dev/null | cut -f1)"
echo "Exported kanban config to: ${OUT_FILE} (${ARCHIVE_SIZE})"
echo "  kanban_version:   ${KANBAN_VERSION}"
echo "  export_timestamp: ${EXPORT_TIMESTAMP}"
echo "  include_secrets:  ${INCLUDE_SECRETS}"
echo "  included files:   ${INCLUDED_FILES[*]}"
if [[ ${#MISSING_FILES[@]} -gt 0 ]]; then
    echo "  missing (skipped): ${MISSING_FILES[*]}"
fi
