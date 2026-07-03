#!/usr/bin/env bash
# team/scripts/recover-rejected.sh
#
# Restore a quarantined file from <project>/priority/.rejected/ or
# <project>/bugs/.rejected/ back to its parent directory and clear the
# per-file counter entry in the state file.
#
# Usage:
#   recover-rejected.sh --project <name> --file <filename> [--rename NEW]
#
# Options:
#   --project NAME       Registered project name (required)
#   --file FILENAME      Base filename inside .rejected/ (e.g. PRIORITY-NNNN-bad.md) (required)
#   --rename NEW         Write the restored file as NEW instead of <filename>
#   --help, -h           Print this help and exit
#
# Behavior:
#   - Searches priority/.rejected/ then bugs/.rejected/ for <filename>.
#   - Moves the file back to the parent dir (renamed if --rename supplied).
#   - Removes the <filename> counter entry from .discovery-state/rejected-counts.tsv.
#     (The new name, when --rename is used, gets no entry — discovery counts from 0.)
#   - Does NOT auto-rename: omitting --rename restores under the original broken
#     name, which will be re-rejected on the next discovery cron tick. This is
#     intentional — the operator must provide a corrected name explicitly.
#
# Exit codes:
#   0 — success
#   1 — usage error, missing project, missing file, or rename collision

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source operator_args.sh for parsing and uniform --help.
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"

# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(project file help rename h)

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Value-taking: project, file, rename.
# Boolean: help.
argparse_parse \
    --value-flags "project file rename" \
    -- "$@"

# Emit error for value-taking flags given with no value.
for _vf in project file rename; do
    if argparse_missing "$_vf"; then
        echo "recover-rejected.sh: error: --${_vf} requires a value" >&2
        exit 1
    fi
done
unset _vf

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "recover-rejected.sh" \
        "Restore a quarantined file from .rejected/ back to its parent directory." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  (--file: base filename inside .rejected/, e.g. PRIORITY-NNNN-bad.md)" \
        "  --rename NEW       Write the restored file as NEW instead of <filename>"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional args).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "recover-rejected.sh: error: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: recover-rejected.sh --project <name> --file <filename> [--rename NEW]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "recover-rejected.sh" OPERATOR_VALID_FLAGS || exit 1

# ---------------------------------------------------------------------------
# Extract flag values
# ---------------------------------------------------------------------------
PROJECT=""
FILENAME=""
RENAME_TO=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

PROJECT="$(operator_args_project)"
if argparse_has "file";   then FILENAME="${ARGPARSE_FLAGS[file]}"; fi
if argparse_has "rename"; then RENAME_TO="${ARGPARSE_FLAGS[rename]}"; fi

# ---------------------------------------------------------------------------
# Validate required flags
# ---------------------------------------------------------------------------
if [[ -z "$PROJECT" ]]; then
    echo "recover-rejected.sh: error: --project is required (or set PGAI_PROJECT_NAME)" >&2
    echo "Usage: recover-rejected.sh --project <name> --file <filename> [--rename NEW]" >&2
    exit 1
fi

if [[ -z "$FILENAME" ]]; then
    echo "recover-rejected.sh: error: --file is required" >&2
    echo "Usage: recover-rejected.sh --project <name> --file <filename> [--rename NEW]" >&2
    exit 1
fi

# Source ini_parser.sh for read_ini; dev_tree.sh for resolve/require helpers.
# shellcheck source=lib/ini_parser.sh
[[ -f "${SCRIPT_DIR}/lib/ini_parser.sh" ]] && source "${SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/lib/dev_tree.sh"
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): recover-rejected moves files between subdirectories of
# $KANBAN_ROOT/projects/<name>; no dev tree access required.
# Global require_dev_tree removed (D5).

# ---------------------------------------------------------------------------
# Validate project
# ---------------------------------------------------------------------------
if [[ -d "${KANBAN_ROOT}/projects" ]]; then
    PROJ_ROOT="${KANBAN_ROOT}/projects/${PROJECT}"
else
    # Legacy single-project layout
    PROJ_ROOT="${KANBAN_ROOT}"
fi

if [[ ! -d "$PROJ_ROOT" ]]; then
    echo "recover-rejected.sh: error: project '${PROJECT}' not found (expected directory: ${PROJ_ROOT})" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Locate the quarantined file (priority/.rejected/ or bugs/.rejected/)
# ---------------------------------------------------------------------------
SOURCE_FILE=""
PARENT_DIR=""

for dir_name in priority bugs; do
    candidate="${PROJ_ROOT}/${dir_name}/.rejected/${FILENAME}"
    if [[ -f "$candidate" ]]; then
        SOURCE_FILE="$candidate"
        PARENT_DIR="${PROJ_ROOT}/${dir_name}"
        break
    fi
done

if [[ -z "$SOURCE_FILE" ]]; then
    echo "recover-rejected.sh: error: '${FILENAME}' not found in any .rejected/ directory for project '${PROJECT}'" >&2
    echo "  Checked:" >&2
    echo "    ${PROJ_ROOT}/priority/.rejected/${FILENAME}" >&2
    echo "    ${PROJ_ROOT}/bugs/.rejected/${FILENAME}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Determine destination filename and path
# ---------------------------------------------------------------------------
DEST_BASENAME="${RENAME_TO:-$FILENAME}"
DEST_FILE="${PARENT_DIR}/${DEST_BASENAME}"

# Guard: refuse to overwrite an existing file at the destination.
if [[ -f "$DEST_FILE" ]]; then
    echo "recover-rejected.sh: error: destination file already exists: ${DEST_FILE}" >&2
    echo "  Choose a different --rename target or remove the existing file first." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Move file from .rejected/ back to parent dir
# ---------------------------------------------------------------------------
if ! mv "$SOURCE_FILE" "$DEST_FILE"; then
    echo "recover-rejected.sh: error: mv failed: ${SOURCE_FILE} -> ${DEST_FILE}" >&2
    exit 1
fi

echo "recover-rejected.sh: restored '${FILENAME}' -> '${DEST_BASENAME}' in ${PARENT_DIR}"

# ---------------------------------------------------------------------------
# Clear the counter entry for <filename> from the state file.
# Uses the same Python removal logic as _disc_maybe_quarantine.
# ---------------------------------------------------------------------------
STATE_FILE="${PROJ_ROOT}/.discovery-state/rejected-counts.tsv"

if [[ -f "$STATE_FILE" ]]; then
    python3 - "$STATE_FILE" "$FILENAME" <<'PY'
import pathlib, sys
state_file = pathlib.Path(sys.argv[1])
filename = sys.argv[2]
if state_file.is_file():
    lines = [l for l in state_file.read_text(encoding="utf-8").splitlines()
             if l.split("\t")[0] != filename]
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    echo "recover-rejected.sh: cleared counter entry for '${FILENAME}' in ${STATE_FILE}"
else
    echo "recover-rejected.sh: state file not found (${STATE_FILE}); no counter to clear"
fi

if [[ -n "$RENAME_TO" ]]; then
    echo "recover-rejected.sh: note: '${DEST_BASENAME}' will get a fresh counter on first discovery parse"
fi
