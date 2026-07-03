#!/usr/bin/env bash
# scripts/migrate/projects-cfg.sh
#
# Convert a colon-format projects.cfg to INI format with an automatic backup.
#
# This script is IDEMPOTENT — running it more than once causes no harm.
# Running it against an already-INI projects.cfg prints a friendly message
# and exits 0 without touching the file or creating a backup.
#
# What this script does (colon-legacy → INI migration):
#   1. Reads the format of projects.cfg via projects_cfg_format.
#   2. If already INI: prints a status message and exits 0. Nothing is modified.
#   3. If colon-legacy:
#      a. Writes a backup to <cfg_dir>/projects.cfg.colon-format-backup.
#         Refuses to overwrite an existing backup unless --force is given.
#      b. Converts projects.cfg to INI format in-place using
#         projects_cfg_colon_to_ini (from lib/projects.sh). Priority and
#         dashboard_color fields from the colon-format are preserved.
#
# Round-trip property: the converted INI file must yield the same project list,
# priorities, and colors as the original colon-format file when parsed by the
# same library helpers. The conversion logic in projects_cfg_colon_to_ini is
# designed to uphold this property.
#
# Usage:
#   migrate-projects-cfg.sh              # migrate $KANBAN_ROOT/projects.cfg
#   migrate-projects-cfg.sh --force      # overwrite existing backup and proceed
#   migrate-projects-cfg.sh --dry-run    # preview, no writes
#
# Exit codes:
#   0 — success (migrated, or already INI — nothing to do)
#   1 — usage error or internal error
#   2 — backup already exists and --force not given
#   3 — kanban root not found

set -euo pipefail

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root not found: $KANBAN_ROOT" >&2
    echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh first." >&2
    exit 3
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

FORCE="false"
DRY_RUN="false"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --force)
            FORCE="true"; shift ;;
        --dry-run)
            DRY_RUN="true"; shift ;;
        --help|-h)
            sed -n '2,34p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
            exit 0 ;;
        -*)
            echo "ERROR: unknown option: $1" >&2
            exit 1 ;;
        *)
            echo "ERROR: unexpected argument: $1" >&2
            exit 1 ;;
    esac
done

CFG_FILE="$(projects_cfg_path)"
BACKUP_FILE="${CFG_FILE}.colon-format-backup"

echo "=== migrate-projects-cfg.sh ==="
echo "Kanban root: $KANBAN_ROOT"
echo "projects.cfg: $CFG_FILE"
echo ""

# --- Detect format ---
if [[ ! -f "$CFG_FILE" ]]; then
    echo "projects.cfg not found: $CFG_FILE"
    echo "Nothing to migrate. Create projects.cfg first (e.g. via create-project.sh)."
    exit 0
fi

FMT="$(projects_cfg_format "$CFG_FILE")"

if [[ "$FMT" == "ini" ]]; then
    echo "projects.cfg is already in INI format. Nothing to do."
    exit 0
fi

# --- Colon-legacy: announce migration plan ---
echo "Current format: colon-legacy"
echo "Target format:  INI"
echo "Backup path:    $BACKUP_FILE"

if [[ -f "$BACKUP_FILE" ]]; then
    if [[ "$FORCE" == "true" ]]; then
        echo "WARNING: Backup already exists; --force given — will overwrite backup."
    else
        echo "" >&2
        echo "ERROR: Backup file already exists: $BACKUP_FILE" >&2
        echo "       A previous migration may have run already, or this file was" >&2
        echo "       created manually. To overwrite the existing backup and proceed," >&2
        echo "       run with --force:" >&2
        echo "" >&2
        echo "         scripts/migrate/projects-cfg.sh --force" >&2
        echo "" >&2
        exit 2
    fi
fi

echo ""

if [[ "$DRY_RUN" == "true" ]]; then
    echo "[dry-run] Would back up: $CFG_FILE → $BACKUP_FILE"
    echo "[dry-run] Would convert: $CFG_FILE (colon-legacy → INI)"
    echo "[dry-run] No changes will be made."
    exit 0
fi

# --- Write backup ---
cp "$CFG_FILE" "$BACKUP_FILE"
echo "Backup written: $BACKUP_FILE"

# --- Convert to INI ---
projects_cfg_colon_to_ini "$CFG_FILE"

echo ""
echo "Migration complete. projects.cfg is now in INI format."
echo "Backup of the original colon-format file: $BACKUP_FILE"
echo ""
echo "Verify the result by running:"
echo "  scripts/dashboard/show-multi.sh"
echo ""
echo "If the result is unexpected, restore the backup:"
echo "  cp ${BACKUP_FILE} ${CFG_FILE}"
