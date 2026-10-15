#!/usr/bin/env bash
# team/scripts/recover-rejected.sh
#
# Restore a file discovery quarantined back to the intake directory it came
# from, and clear its per-file counter entry in the state file.
#
# Discovery quarantines into the project's rejected/ directory
# (<project>/rejected/<filename>, beside a <filename>.reason sidecar); with no
# project name it falls back to the intake directory's own .rejected/
# (<project>/priority/.rejected/, bugs/.rejected/, requirements/.rejected/).
# Both are searched.
#
# Usage:
#   recover-rejected.sh --project <name> --file <filename> [--rename NEW]
#
# Options:
#   --project NAME       Registered project name (required unless
#                        PGAI_PROJECT_NAME is set, which is used when it is omitted)
#   --file FILENAME      Base name of the quarantined file, as list-rejected.sh prints it
#                        (e.g. PRIORITY-NNNN-bad.md) (required)
#   --rename NEW         Write the restored file as NEW instead of <filename>;
#                        NEW is a plain base name (no '/', not '.' or '..')
#   --help, -h           Print this help and exit
#
# Behavior:
#   - Searches <project>/rejected/, then priority/.rejected/, bugs/.rejected/
#     and requirements/.rejected/ for <filename>.
#   - Moves the file back to the intake directory it came from (renamed if
#     --rename supplied). For a file in rejected/ that is the directory its
#     <filename>.reason sidecar records -- original_dir when it names one of
#     the project's intake directories, else original_type -- or, with no
#     usable sidecar, bugs/ for a BUG- name and priority/ for a PRIORITY- name.
#     A file in <dir>/.rejected/ goes back to <dir>. The sidecar is left in
#     place: discovery reads its retry_count if the file is quarantined again.
#   - Removes the <filename> counter entry from .discovery-state/rejected-counts.tsv.
#     (The new name, when --rename is used, gets no entry — discovery counts from 0.)
#   - Does NOT auto-rename: omitting --rename restores under the original broken
#     name, which will be re-rejected on the next discovery cron tick. This is
#     intentional — the operator must provide a corrected name explicitly.
#
# Exit codes:
#   0 — success
#   1 — usage error, missing project, missing file, origin unknown,
#       a --rename that is not a plain base name, or rename collision

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

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
operator_args_argparse \
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
# The built-in --project and --file lines say "default: $PGAI_PROJECT_NAME" and
# "File path"; here --project is required unless PGAI_PROJECT_NAME supplies it,
# and --file takes the quarantined file's base name, so both are overridden.
declare -A OPERATOR_FLAG_HELP=(
    [project]="  --project NAME     Registered project name (required unless PGAI_PROJECT_NAME is set, which is used when it is omitted)"
    [file]="  --file FILENAME    Base name of the quarantined file as list-rejected.sh prints it, e.g. PRIORITY-NNNN-bad.md (required)"
)
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "recover-rejected.sh" \
        "Restore a quarantined file from the project's rejected/ directory (or a legacy <intake>/.rejected/) to the intake directory it came from." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --rename NEW       Write the restored file as NEW instead of <filename>; NEW is a plain base name (no '/', not '.' or '..')"
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
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

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
# shellcheck source=lib/pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${SCRIPT_DIR}/lib/pp_run_ops.sh"
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
# Locate the quarantined file and the intake directory it came from, through
# pgai_agent_kanban.dashboard._rejected.find_quarantined -- the same lookup
# list-rejected.sh, GET /rejected and GET /board read through. It prints
# "<path>\t<restore-dir>" (restore-dir empty when the origin cannot be told),
# or nothing when no quarantined file has that name.
# ---------------------------------------------------------------------------
# The project's rejected/ directory: the location pp_rejected_dir names.
REJECTED_DIR="${PROJ_ROOT}/rejected"

_located="$(pp_run_ops pgai_agent_kanban.discovery.items locate-quarantined \
    "$PROJ_ROOT" "$REJECTED_DIR" "$FILENAME")"
SOURCE_FILE="${_located%%$'\t'*}"
PARENT_DIR="${_located#*$'\t'}"
unset _located

if [[ -z "$SOURCE_FILE" ]]; then
    echo "recover-rejected.sh: error: '${FILENAME}' is not a quarantined file of project '${PROJECT}'" >&2
    echo "  Checked:" >&2
    echo "    ${REJECTED_DIR}/${FILENAME}" >&2
    echo "    ${PROJ_ROOT}/priority/.rejected/${FILENAME}" >&2
    echo "    ${PROJ_ROOT}/bugs/.rejected/${FILENAME}" >&2
    echo "    ${PROJ_ROOT}/requirements/.rejected/${FILENAME}" >&2
    echo "  List what is quarantined: ${SCRIPT_DIR}/list-rejected.sh --project ${PROJECT}" >&2
    exit 1
fi

if [[ -z "$PARENT_DIR" ]]; then
    # shellcheck source=lib/error_contract.sh
    source "${SCRIPT_DIR}/lib/error_contract.sh"
    # BLOCK-WORTHY (docs/blocking-site-census.md): with no usable sidecar and
    # no BUG-/PRIORITY- name prefix there is no intake directory to restore
    # into; moving the file into a guessed one would misroute intake, so
    # nothing is moved until the operator records its origin.
    pgai_emit_error_contract "recover_rejected.origin_unknown" \
        "recover-rejected.sh: cannot tell which intake directory '${FILENAME}' came from: ${SOURCE_FILE} has no ${FILENAME}.reason sidecar naming original_dir or original_type, and its name carries no BUG- or PRIORITY- prefix. Nothing was moved. Record its origin (bugs, priority or requirements) in the sidecar and run again." \
        "echo original_type=bugs >> '${SOURCE_FILE}.reason' && '${SCRIPT_DIR}/recover-rejected.sh' --project '${PROJECT}' --file '${FILENAME}'" \
        "${PROJECT}"
    # CENSUS-ANCHOR: recover-rejected-origin-unknown-01
    exit 1
fi

# ---------------------------------------------------------------------------
# --rename names a file inside the intake directory, so it must be a plain
# base name: a '/' or a bare '.' / '..' would place the restored file
# somewhere other than ${PARENT_DIR}.
# ---------------------------------------------------------------------------
if [[ -n "$RENAME_TO" ]] && [[ "$RENAME_TO" == */* || "$RENAME_TO" == "." || "$RENAME_TO" == ".." ]]; then
    # shellcheck source=lib/error_contract.sh
    source "${SCRIPT_DIR}/lib/error_contract.sh"
    # The remedy suggests the last path component of the rejected value, or
    # the quarantined file's own name when that component is empty, '.' or '..'.
    _suggested="${RENAME_TO%/}"
    _suggested="${_suggested##*/}"
    if [[ -z "$_suggested" || "$_suggested" == "." || "$_suggested" == ".." ]]; then
        _suggested="$FILENAME"
    fi
    # BLOCK-WORTHY (docs/blocking-site-census.md): a --rename that is not a
    # plain base name would move the file out of its intake directory, where
    # discovery never reads it, so nothing is moved until the operator gives a
    # plain file name.
    pgai_emit_error_contract "recover_rejected.rename_not_basename" \
        "recover-rejected.sh: --rename '${RENAME_TO}' is not a plain file name. --rename takes a base name only (no '/', not '.' or '..'), because the restored file is written into ${PARENT_DIR}. Nothing was moved." \
        "$(printf '%q --project %q --file %q --rename %q' "${SCRIPT_DIR}/recover-rejected.sh" "$PROJECT" "$FILENAME" "$_suggested")" \
        "${PROJECT}"
    # CENSUS-ANCHOR: recover-rejected-rename-not-basename-01
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
# Move the file out of quarantine back to its intake directory
# ---------------------------------------------------------------------------
if ! mv "$SOURCE_FILE" "$DEST_FILE"; then
    echo "recover-rejected.sh: error: mv failed: ${SOURCE_FILE} -> ${DEST_FILE}" >&2
    exit 1
fi

echo "recover-rejected.sh: restored '${FILENAME}' -> '${DEST_BASENAME}' in ${PARENT_DIR}"

# ---------------------------------------------------------------------------
# Clear the counter entry for <filename> from the state file, through the
# same removal _disc_maybe_quarantine uses (pgai_agent_kanban.discovery.items
# forget-rejection).
# ---------------------------------------------------------------------------
STATE_FILE="${PROJ_ROOT}/.discovery-state/rejected-counts.tsv"

if [[ -f "$STATE_FILE" ]]; then
    pp_run_ops pgai_agent_kanban.discovery.items forget-rejection "$STATE_FILE" "$FILENAME"
    echo "recover-rejected.sh: cleared counter entry for '${FILENAME}' in ${STATE_FILE}"
else
    echo "recover-rejected.sh: state file not found (${STATE_FILE}); no counter to clear"
fi

if [[ -n "$RENAME_TO" ]]; then
    echo "recover-rejected.sh: note: '${DEST_BASENAME}' will get a fresh counter on first discovery parse"
fi
