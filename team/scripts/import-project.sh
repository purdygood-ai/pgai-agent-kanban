#!/usr/bin/env bash
# team/scripts/import-project.sh
#
# Import a project from a portable .tar.gz archive produced by export-project.sh
# and optionally register it in projects.cfg.
#
# Usage:
#   import-project.sh --archive <path>
#   import-project.sh --archive <path> [--name <newname>] [--register] [--force]
#   import-project.sh --help
#
# Arguments:
#   --archive PATH    Required. Path to a .tar.gz archive created by export-project.sh.
#   --name NEWNAME    Optional. Import the project under a different name.
#                     Default: project name embedded in the archive.
#   --register        Optional. Add the project to projects.cfg using the
#                     registration data from the archive's MANIFEST file.
#                     Without this flag a notice is printed with the line the
#                     operator must add manually.
#   --force           Optional. Allow overwriting an existing projects/<name>/.
#                     Without this flag the script exits non-zero if the target
#                     directory already exists.  With this flag the existing
#                     directory is moved to projects/<name>.bak-<UTCstamp>
#                     before extraction.
#   --help / -h       Print this help and exit 0.
#
# Exit codes:
#   0 — import completed successfully (or --help requested)
#   1 — usage error, unknown option, or missing required argument
#   2 — archive not found or not readable
#   3 — kanban root not found (PGAI_AGENT_KANBAN_ROOT_PATH or $HOME/pgai_agent_kanban)
#   4 — target project directory already exists and --force not given
#   5 — extraction or registration error

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve kanban root
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

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

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
# Declared flag vocabulary for all flags this command accepts.
OPERATOR_VALID_FLAGS=(force help archive name register h)

ARCHIVE=""
NEW_NAME=""
REGISTER=false
FORCE=false

# Value-taking flags: script-specific (archive, name).
# Boolean flags: register, force, help.
argparse_parse --value-flags "archive name" -- "$@"

# Emit clear errors for value-taking flags given with no value.
if argparse_missing "archive"; then
    echo "ERROR: --archive requires a path argument" >&2
    exit 1
fi
if argparse_missing "name"; then
    echo "ERROR: --name requires a project name argument" >&2
    exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
    operator_args_render_help_for_flags "import-project.sh" \
        "Import a project from a .tar.gz archive produced by export-project.sh." \
        OPERATOR_VALID_FLAGS \
        "" \
        "  --archive PATH       Archive file to import (required)" \
        "  --name NEWNAME       Import under a different project name (optional)" \
        "  --register           Register project in projects.cfg from archive MANIFEST" \
        "  (--force: backs up existing projects/<name>/ before overwriting)"
    exit 0
fi

# Reject unexpected positional arguments (hard-cut: no positional archive path).
if [[ "${#ARGPARSE_POSITIONAL[@]}" -gt 0 ]]; then
    echo "ERROR: unexpected positional argument: ${ARGPARSE_POSITIONAL[0]}" >&2
    echo "Usage: import-project.sh --archive <path> [--name <newname>] [--register] [--force]" >&2
    exit 1
fi

# Reject any flag not in the declared vocabulary.
operator_args_validate_known "import-project.sh" OPERATOR_VALID_FLAGS || exit 1

# Extract flags.
if argparse_has "archive";  then ARCHIVE="${ARGPARSE_FLAGS[archive]}"; fi
if argparse_has "name";     then NEW_NAME="${ARGPARSE_FLAGS[name]}"; fi
if argparse_has "register"; then REGISTER=true; fi
if argparse_has "force";    then FORCE=true; fi

# ---------------------------------------------------------------------------
# Validate required argument
# ---------------------------------------------------------------------------
if [[ -z "$ARCHIVE" ]]; then
    echo "ERROR: --archive is required" >&2
    echo "Usage: import-project.sh --archive <path> [--name <newname>] [--register] [--force]" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Validate archive path
# ---------------------------------------------------------------------------
if [[ ! -f "$ARCHIVE" ]]; then
    echo "ERROR: archive not found: $ARCHIVE" >&2
    exit 2
fi

if [[ ! -r "$ARCHIVE" ]]; then
    echo "ERROR: archive is not readable: $ARCHIVE" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Detect project name from the archive's top-level directory entry
#
# export-project.sh always writes a single top-level directory <name>/ into
# the archive.  We use "tar tzf" to peek at the entries and extract the
# leading path component of the first entry.  This is safer than assuming
# the archive basename matches the project name.
# ---------------------------------------------------------------------------
# Capture the full archive listing first (no pipe-into-head), then take the
# first entry via parameter expansion.  Piping tar into "head -n1" under
# "set -o pipefail" causes a false-positive SIGPIPE: head closes the read end
# after the first line, tar receives SIGPIPE writing the second entry, and exits
# 141 (128+13).  pipefail promotes that to a pipeline failure even though the
# archive is perfectly valid.  The capture-then-expand form below runs tar to
# completion so its real exit code reflects genuine corruption, not SIGPIPE.
if ! ARCHIVE_LISTING="$(tar tzf "$ARCHIVE" 2>/dev/null)"; then
    echo "ERROR: cannot read archive contents (corrupt or not a valid tar.gz): $ARCHIVE" >&2
    exit 2
fi
ARCHIVE_TOP_ENTRY="${ARCHIVE_LISTING%%$'\n'*}"

if [[ -z "$ARCHIVE_TOP_ENTRY" ]]; then
    echo "ERROR: archive is empty: $ARCHIVE" >&2
    exit 2
fi

# Strip trailing slash and extract the first path component (the project name)
# e.g. "myproject/"  ->  "myproject"
# e.g. "myproject/project.cfg"  ->  "myproject"
ARCHIVE_PROJECT_NAME="${ARCHIVE_TOP_ENTRY%%/*}"
ARCHIVE_PROJECT_NAME="${ARCHIVE_PROJECT_NAME%/}"

if [[ -z "$ARCHIVE_PROJECT_NAME" ]]; then
    echo "ERROR: could not determine project name from archive top-level entry: $ARCHIVE_TOP_ENTRY" >&2
    exit 2
fi

# Determine the destination project name.
# If --name was supplied, the final resting place is projects/<newname>/.
# We still extract to the archive-native name first, then rename below.
DEST_NAME="$ARCHIVE_PROJECT_NAME"
DEST_DIR="${KANBAN_ROOT}/projects/${DEST_NAME}"
FINAL_NAME="${NEW_NAME:-${DEST_NAME}}"
FINAL_DIR="${KANBAN_ROOT}/projects/${FINAL_NAME}"

# ---------------------------------------------------------------------------
# Clobber guard for the archive-native destination (projects/<archive-name>/)
# ---------------------------------------------------------------------------
BACKUP_DIR=""

if [[ -d "$DEST_DIR" ]]; then
    if [[ "$FORCE" == false ]]; then
        echo "ERROR: destination already exists: ${DEST_DIR}" >&2
        echo "Use --force to overwrite (the existing directory will be backed up)." >&2
        exit 4
    fi

    # --force: move existing directory to a backup before extracting
    UTC_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
    BACKUP_DIR="${DEST_DIR}.bak-${UTC_STAMP}"
    mv "$DEST_DIR" "$BACKUP_DIR" || {
        echo "ERROR: failed to move existing directory to backup: $BACKUP_DIR" >&2
        exit 5
    }
fi

# ---------------------------------------------------------------------------
# Clobber guard for the rename target (projects/<newname>/) when --name is
# supplied and the rename target is different from the extraction target.
# Checked before extraction so that a collision is detected early with no
# side-effects on disk.
# ---------------------------------------------------------------------------
RENAME_BACKUP_DIR=""

if [[ -n "$NEW_NAME" && "$FINAL_NAME" != "$DEST_NAME" && -d "$FINAL_DIR" ]]; then
    if [[ "$FORCE" == false ]]; then
        echo "ERROR: rename target already exists: ${FINAL_DIR}" >&2
        echo "Use --force to overwrite (the existing directory will be backed up)." >&2
        exit 4
    fi

    # --force: move existing rename-target directory to a backup before we
    # rename into it after extraction.
    UTC_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
    RENAME_BACKUP_DIR="${FINAL_DIR}.bak-${UTC_STAMP}"
    mv "$FINAL_DIR" "$RENAME_BACKUP_DIR" || {
        echo "ERROR: failed to move existing rename-target to backup: $RENAME_BACKUP_DIR" >&2
        exit 5
    }
fi

# ---------------------------------------------------------------------------
# Extract the archive into $KANBAN_ROOT/projects/
#
# The archive top-level entry is <name>/ so tar will create
# $KANBAN_ROOT/projects/<name>/ directly.
# ---------------------------------------------------------------------------
tar xzf "$ARCHIVE" -C "${KANBAN_ROOT}/projects" || {
    echo "ERROR: extraction failed for archive: $ARCHIVE" >&2
    # If we moved an existing dir to backup, restore it so the destination
    # is left byte-identical to what it was before the attempt.
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" && ! -d "$DEST_DIR" ]]; then
        mv "$BACKUP_DIR" "$DEST_DIR" || true
    fi
    exit 5
}

# ---------------------------------------------------------------------------
# Verify the expected destination directory was created by extraction
# ---------------------------------------------------------------------------
if [[ ! -d "$DEST_DIR" ]]; then
    echo "ERROR: extraction appeared to succeed but destination directory is missing: $DEST_DIR" >&2
    exit 5
fi

# ---------------------------------------------------------------------------
# Rename: if --name <newname> was supplied, move the extracted directory to
# projects/<newname>/ and update project_name in project.cfg.
# ---------------------------------------------------------------------------
if [[ -n "$NEW_NAME" && "$FINAL_NAME" != "$DEST_NAME" ]]; then
    mv "$DEST_DIR" "$FINAL_DIR" || {
        echo "ERROR: failed to rename extracted directory from '${DEST_DIR}' to '${FINAL_DIR}'" >&2
        exit 5
    }

    # Rewrite project_name in the renamed project.cfg.
    PROJECT_CFG="${FINAL_DIR}/project.cfg"
    if [[ -f "$PROJECT_CFG" ]]; then
        if grep -qE '^[[:space:]]*project_name[[:space:]]*=' "$PROJECT_CFG"; then
            sed -i "s|^[[:space:]]*project_name[[:space:]]*=.*\$|project_name = ${FINAL_NAME}|" "$PROJECT_CFG" || {
                echo "ERROR: failed to rewrite project_name in ${PROJECT_CFG}" >&2
                exit 5
            }
        else
            echo "WARNING: project_name key not found in ${PROJECT_CFG}; skipping rename of project_name." >&2
        fi
    else
        echo "WARNING: project.cfg not found at ${PROJECT_CFG}; skipping project_name rename." >&2
    fi
fi

# ---------------------------------------------------------------------------
# Blank env-specific fields: dev_tree_path and git_repo_url in project.cfg.
# These fields are machine-specific and must not carry over from the archive's
# source environment.  Blank the values in-place, preserving all INI structure
# and surrounding comments (matching create-project.sh fresh-project contract).
# ---------------------------------------------------------------------------
PROJECT_CFG="${FINAL_DIR}/project.cfg"
BLANKED_FIELDS=()

if [[ -f "$PROJECT_CFG" ]]; then
    for _field in dev_tree_path git_repo_url; do
        if grep -qE "^[[:space:]]*${_field}[[:space:]]*=" "$PROJECT_CFG"; then
            sed -i "s|^[[:space:]]*${_field}[[:space:]]*=.*\$|${_field} =|" "$PROJECT_CFG" || {
                echo "ERROR: failed to blank ${_field} in ${PROJECT_CFG}" >&2
                exit 5
            }
            BLANKED_FIELDS+=("$_field")
        else
            echo "WARNING: field '${_field}' not found in ${PROJECT_CFG}; skipping blank." >&2
        fi
    done
else
    echo "WARNING: project.cfg not found at ${PROJECT_CFG}; skipping env-field blanking." >&2
fi

# ---------------------------------------------------------------------------
# MANIFEST validation — read from the extracted project directory.
#
# export-project.sh writes <name>/MANIFEST into the archive.  After extraction
# (and optional rename) the file lives at projects/<final-name>/MANIFEST.
# We validate the kanban_version field and extract the registration block.
#
# Validation rules (per task constraints):
#   - Missing MANIFEST: warn on stderr; do NOT exit non-zero.
#   - Missing kanban_version field in MANIFEST: warn; continue.
#   - kanban_version mismatch: warn on stderr; exit code stays 0.
#   - Missing registration block when --register given: exit 5.
# ---------------------------------------------------------------------------
MANIFEST_FILE="${FINAL_DIR}/MANIFEST"
MANIFEST_KANBAN_VERSION=""
MANIFEST_REGISTRATION_BLOCK=""

if [[ -f "$MANIFEST_FILE" ]]; then
    # Parse kanban_version: look for a line matching "kanban_version: <value>"
    MANIFEST_KANBAN_VERSION="$(awk -F': ' '/^kanban_version[[:space:]]*:/ { $1=""; sub(/^[[:space:]]+/, ""); print; exit }' "$MANIFEST_FILE" 2>/dev/null || true)"
    # Trim whitespace
    MANIFEST_KANBAN_VERSION="${MANIFEST_KANBAN_VERSION#"${MANIFEST_KANBAN_VERSION%%[![:space:]]*}"}"
    MANIFEST_KANBAN_VERSION="${MANIFEST_KANBAN_VERSION%"${MANIFEST_KANBAN_VERSION##*[![:space:]]}"}"

    # Compare against the live kanban VERSION
    VERSION_FILE="${KANBAN_ROOT}/VERSION"
    LIVE_KANBAN_VERSION="unknown"
    if [[ -f "$VERSION_FILE" ]]; then
        LIVE_KANBAN_VERSION="$(tr -d '[:space:]' < "$VERSION_FILE" 2>/dev/null)" || LIVE_KANBAN_VERSION="unknown"
        [[ -z "$LIVE_KANBAN_VERSION" ]] && LIVE_KANBAN_VERSION="unknown"
    fi

    if [[ -z "$MANIFEST_KANBAN_VERSION" ]]; then
        echo "WARNING: MANIFEST is present but 'kanban_version' field is missing or unreadable." >&2
    elif [[ "$MANIFEST_KANBAN_VERSION" != "$LIVE_KANBAN_VERSION" ]]; then
        echo "WARNING: kanban version mismatch — archive was exported from ${MANIFEST_KANBAN_VERSION}; this install is ${LIVE_KANBAN_VERSION}." >&2
        echo "  This is usually recoverable. Review project.cfg for schema differences before enabling releases." >&2
    fi

    # Extract the registration block: lines from "## registration" until the
    # next "##" section header or end of file.  The block includes the
    # section header line itself (e.g. [project:NAME]) and all key=value lines.
    MANIFEST_REGISTRATION_BLOCK="$(awk '
        /^## registration[[:space:]]*$/ { in_reg=1; next }
        in_reg && /^##/ { exit }
        in_reg { print }
    ' "$MANIFEST_FILE" 2>/dev/null || true)"

    # Trim leading/trailing blank lines from the block
    MANIFEST_REGISTRATION_BLOCK="$(printf '%s' "$MANIFEST_REGISTRATION_BLOCK" | sed '/./,$!d' | sed -e :a -e '/^[[:space:]]*$/{$d;N;ba}')"
else
    echo "WARNING: MANIFEST file not found in imported project at: ${MANIFEST_FILE}" >&2
    echo "  Import continues; registration block is unavailable." >&2
fi

# ---------------------------------------------------------------------------
# Registration: --register merges the [project:<final-name>] INI section into
# projects.cfg.  Without --register, print the exact block for copy/paste.
#
# When --name was supplied, rewrite the section header from [project:<orig>]
# to [project:<final-name>] before appending.
#
# Guard: if the section header already exists in projects.cfg, warn and skip
# (do NOT duplicate it).
# ---------------------------------------------------------------------------
PROJECTS_CFG="${KANBAN_ROOT}/projects.cfg"

if [[ "$REGISTER" == true ]]; then
    # Registration block is required when --register is given.
    if [[ -z "$MANIFEST_REGISTRATION_BLOCK" ]]; then
        echo "ERROR: --register was supplied but the MANIFEST contains no registration block." >&2
        echo "  Cannot auto-register project '${FINAL_NAME}' without a [project:NAME] INI block." >&2
        echo "  Inspect '${MANIFEST_FILE}' manually and add the section to ${PROJECTS_CFG} by hand." >&2
        exit 5
    fi

    # Rewrite the section header to the final name (handles --name rename case).
    # The block from MANIFEST carries the archive's original project name; replace
    # the first [project:*] header line with the canonical final name.
    REWRITTEN_BLOCK="$(printf '%s\n' "$MANIFEST_REGISTRATION_BLOCK" | \
        awk -v final="${FINAL_NAME}" '
            /^\[project:[a-zA-Z0-9_-]+\]$/ && !replaced {
                print "[project:" final "]"
                replaced = 1
                next
            }
            { print }
        ')"

    # Verify the rewritten block has a valid header.
    if ! printf '%s\n' "$REWRITTEN_BLOCK" | grep -qE '^\[project:[a-zA-Z0-9_-]+\]$'; then
        echo "ERROR: registration block from MANIFEST does not contain a valid [project:NAME] header." >&2
        echo "  Block content:" >&2
        printf '%s\n' "$REWRITTEN_BLOCK" | sed 's/^/    /' >&2
        exit 5
    fi

    # Check for duplicate — never append a section that already exists.
    if grep -qE "^\[project:${FINAL_NAME}\]$" "$PROJECTS_CFG" 2>/dev/null; then
        echo "WARNING: project '${FINAL_NAME}' is already registered in ${PROJECTS_CFG}." >&2
        echo "  Skipping registration to avoid duplication. Edit ${PROJECTS_CFG} manually if an update is needed." >&2
    else
        # projects.cfg may or may not exist; create it if needed.
        if [[ ! -f "$PROJECTS_CFG" ]]; then
            # Create a minimal header-only projects.cfg
            printf '# projects.cfg — auto-created by import-project.sh\n' > "$PROJECTS_CFG" || {
                echo "ERROR: failed to create ${PROJECTS_CFG}" >&2
                exit 5
            }
        fi

        # Append the registration block.  Ensure a trailing newline separates it
        # from any existing content by adding a blank line before the block.
        {
            printf '\n'
            printf '%s\n' "$REWRITTEN_BLOCK"
        } >> "$PROJECTS_CFG" || {
            echo "ERROR: failed to append registration block to ${PROJECTS_CFG}" >&2
            exit 5
        }
        echo "Registered project '${FINAL_NAME}' in: ${PROJECTS_CFG}"
    fi
else
    # --register not supplied: print the block the operator must paste manually.
    # Rewrite the header to the final name even in the print path.
    if [[ -n "$MANIFEST_REGISTRATION_BLOCK" ]]; then
        REWRITTEN_BLOCK="$(printf '%s\n' "$MANIFEST_REGISTRATION_BLOCK" | \
            awk -v final="${FINAL_NAME}" '
                /^\[project:[a-zA-Z0-9_-]+\]$/ && !replaced {
                    print "[project:" final "]"
                    replaced = 1
                    next
                }
                { print }
            ')"
        echo ""
        echo "NOTICE: To register this project, add the following section to: ${PROJECTS_CFG}"
        echo ""
        printf '%s\n' "$REWRITTEN_BLOCK"
        echo ""
        echo "  Or re-run with --register to have it added automatically."
    fi
fi

# ---------------------------------------------------------------------------
# Success output
# ---------------------------------------------------------------------------
echo "Imported project '${DEST_NAME}' to: ${DEST_DIR}"
if [[ -n "$BACKUP_DIR" ]]; then
    echo "  previous directory backed up to: ${BACKUP_DIR}"
fi
if [[ -n "$NEW_NAME" && "$FINAL_NAME" != "$DEST_NAME" ]]; then
    echo "  renamed to: ${FINAL_DIR}"
    if [[ -n "$RENAME_BACKUP_DIR" ]]; then
        echo "  rename target backed up to: ${RENAME_BACKUP_DIR}"
    fi
fi
echo "  archive: ${ARCHIVE}"
echo "  final location: ${FINAL_DIR}"

# ---------------------------------------------------------------------------
# Operator notice: blanked env-specific fields and next-step guidance.
# Mirrors the post-create notice from create-project.sh.
# ---------------------------------------------------------------------------
if [[ "${#BLANKED_FIELDS[@]}" -gt 0 ]]; then
    echo ""
    echo "NOTICE: The following env-specific fields in project.cfg have been blanked:"
    for _f in "${BLANKED_FIELDS[@]}"; do
        echo "  ${_f} ="
    done
    echo ""
    echo "  These fields are machine-specific and were intentionally cleared."
    echo "  Edit ${PROJECT_CFG} to supply the correct paths for this environment."
    echo ""
    echo "  Suggested next steps:"
    echo "    1. Edit project.cfg to set dev_tree_path and git_repo_url:"
    echo "       \$EDITOR ${PROJECT_CFG}"
    echo "    2. Push the chain's base branches to origin (run once, before first release):"
    echo "       init-project-git-repo.sh ${FINAL_NAME}"
    echo "    3. When ready to authorize releases, raise the ceiling:"
    echo "       set-version-ceiling.sh ${FINAL_NAME} --minor <N>"
fi
