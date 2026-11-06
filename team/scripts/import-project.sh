#!/usr/bin/env bash
# team/scripts/import-project.sh
#
# Import a project from a portable .tar.gz archive produced by export-project.sh
# and optionally register it in projects.cfg.
#
# The archive is extracted into a private staging directory under projects/
# (projects/.import-staging-XXXXXX) and the extracted tree is moved straight
# to projects/<final-name>/. Only the final name is ever checked for a
# collision or backed up, so importing under --name never moves, renames or
# overwrites the project that carries the archive's original name. The
# staging directory is removed on every exit path, success or failure.
#
# When the export carries the project's archive (the embedded <name>/ARCHIVE/
# directory export-project.sh writes from $KANBAN_ROOT/archive/<name>/), it
# is moved to $KANBAN_ROOT/archive/<final-name>/ after extraction, byte for
# byte, ARCHIVE-LEDGER.tsv included. The ledger's rows are left exactly as
# exported: their paths are relative to the kanban root and keep naming the
# project the items were archived from.
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
#                     Exactly one entry is appended: the block's first
#                     [project:NAME] header whose NAME the registry accepts,
#                     renamed to the final name, and the lines under it up
#                     to the next [project:...] header.  A section header of
#                     another kind inside it (such as [release]) is removed,
#                     with a warning naming it, so the keys after it stay in
#                     the project's section.  A block holding only a legacy
#                     NAME:PRIORITY[:COLOR] line is appended as that line
#                     (renamed) when projects.cfg is in the legacy format,
#                     and converted to a [project:<final-name>] section when
#                     it is INI.  A block with neither is refused (exit 5)
#                     and nothing is appended.  Everything else in the
#                     block -- non-comment lines before its first header,
#                     sections whose NAME the registry rejects, and any
#                     further accepted section -- is left out with a warning
#                     naming it; comment lines are dropped silently.
#                     Without this flag a notice is printed with the entry
#                     the operator must add manually, built the same way;
#                     for a block with no usable entry a warning is printed
#                     instead and nothing is offered.
#   --force           Optional. Allow overwriting an existing
#                     projects/<final-name>/ (and, when the export carries an
#                     archive, an existing archive/<final-name>/).
#                     <final-name> is --name when given, else the archive's
#                     own name; no other project directory is ever touched.
#                     Without this flag the script exits non-zero if either
#                     target directory already exists.  With this flag the
#                     existing project directory is moved to
#                     projects/<final-name>.bak-<UTCstamp> once extraction
#                     has succeeded, just before the imported tree takes its
#                     place, and an existing archive directory to
#                     archive/<final-name>.bak-<UTCstamp> before the imported
#                     archive takes its place.  Nothing is deleted.
#   --help / -h       Print this help and exit 0.
#
# Exit codes:
#   0 — import completed successfully (or --help requested)
#   1 — usage error, unknown option, or missing required argument
#   2 — archive not found or not readable
#   3 — kanban root not found (PGAI_AGENT_KANBAN_ROOT_PATH or $HOME/pgai_agent_kanban)
#   1 — also: the final project name (--name, else the archive's own) is not
#       a project name the registry accepts (PROJECTS_CFG_NAME_RE in
#       lib/projects_cfg.sh: a lowercase letter or digit, then lowercase
#       letters, digits and '-'); an export that carries an archive reports
#       a name of letters, digits, '_' and '-' outside that rule as an
#       invalid archive project name
#   4 — projects/<final-name>/ (or archive/<final-name>/, when the export
#       carries an archive) already exists and --force not given
#   5 — staging, extraction, archive placement, or registration error

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

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
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"
# shellcheck source=lib/operator_args.sh
source "${SCRIPT_DIR}/lib/operator_args.sh"
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/lib/project_paths.sh"
# projects_cfg_has (registry membership)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/lib/projects.sh"
# shellcheck source=lib/error_contract.sh
source "${SCRIPT_DIR}/lib/error_contract.sh"
# shellcheck source=lib/md_field.sh
source "${SCRIPT_DIR}/lib/md_field.sh"

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
operator_args_argparse --value-flags "archive name" -- "$@"

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
        "  (--force: backs up an existing projects/<final-name>/ before replacing it;" \
        "   <final-name> is --name when given, and no other project is touched)" \
        "  (an export carrying the project's archive is placed in archive/<name>/;" \
        "   --force backs up an existing archive/<name>/ first)"
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
# The archive-native name (DEST_NAME) only names the extracted tree inside
# the staging directory; projects/<archive-name>/ itself is never read,
# checked, moved or written unless it is also the final name.
DEST_NAME="$ARCHIVE_PROJECT_NAME"
FINAL_NAME="${NEW_NAME:-${DEST_NAME}}"
FINAL_DIR="${KANBAN_ROOT}/projects/${FINAL_NAME}"

# ---------------------------------------------------------------------------
# The project's archive: export-project.sh embeds archive/<name>/ (ledger
# included) as <name>/ARCHIVE/. When the export carries one, it is placed at
# $KANBAN_ROOT/archive/<final-name>/ after extraction. Both checks below run
# before anything on disk changes, so a refusal leaves the kanban root as it
# was.
# ---------------------------------------------------------------------------
CARRIES_ARCHIVE=false
if [[ $'\n'"${ARCHIVE_LISTING}" == *$'\n'"${ARCHIVE_PROJECT_NAME}/ARCHIVE/"* ]]; then
    CARRIES_ARCHIVE=true
fi
ARCHIVE_DEST="${KANBAN_ROOT}/archive/${FINAL_NAME}"
ARCHIVE_BACKUP_DIR=""

# Re-run line for the remedies below: this invocation's own arguments.
_rerun="$(printf '%q ' "$0" --archive "$ARCHIVE")"
[[ "$REGISTER" == true ]] && _rerun+="--register "
# The same, carrying --name and --force as given, for remedies that re-run
# this exact invocation unchanged.
_rerun_as_given="$_rerun"
if [[ -n "$NEW_NAME" ]]; then
    _rerun_as_given+="$(printf -- '--name %q ' "$NEW_NAME")"
fi
if [[ "$FORCE" == true ]]; then
    _rerun_as_given+="--force"
fi
_rerun_as_given="${_rerun_as_given% }"

# A sanitised final name for the two name refusals' remedies: lower-cased,
# every other character turned into '-', leading separators dropped.
_suggested="${FINAL_NAME,,}"
_suggested="${_suggested//[^a-z0-9-]/-}"
_suggested="${_suggested#"${_suggested%%[a-z0-9]*}"}"
[[ -n "$_suggested" ]] || _suggested="imported-project"

# Every import -- with or without an embedded archive, under --name or the
# archive's own name -- lands at projects/<final-name>/, so the final name
# must be a project name the registry accepts: the project-name rule
# PROJECTS_CFG_NAME_RE (lib/projects_cfg.sh), which create-project.sh and
# add-project.sh apply too. When the export carries an archive, a final name
# of letters, digits, '_' and '-' that the rule refuses (My_App) is reported
# by the archive check below, under its own error key.
_final_name_path_shaped=true
[[ "$FINAL_NAME" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] && _final_name_path_shaped=false
if [[ ! "$FINAL_NAME" =~ $PROJECTS_CFG_NAME_RE ]] \
        && [[ "$_final_name_path_shaped" == true || "$CARRIES_ARCHIVE" == false ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): the final name is joined
    # into projects/<final-name>/, which --force moves aside and the import
    # replaces; a separator, '..' or a leading '-' would put the project --
    # or the backup -- outside projects/, and any other name outside the
    # rule is one no registry reader lists or wake starts. Nothing has been
    # extracted or moved yet; the import stops here.
    # CENSUS-ANCHOR: import-project-name-01
    pgai_emit_error_contract "import.invalid_project_name" \
        "import-project.sh: '${FINAL_NAME}' is not a valid project name (one matching ${PROJECTS_CFG_NAME_RE}), so it cannot name projects/${FINAL_NAME}/; nothing was imported." \
        "${_rerun}$(printf -- '--name %q' "$_suggested")$([[ "$FORCE" == true ]] && printf ' --force')"
    exit 1
fi

unset _final_name_path_shaped

if [[ "$CARRIES_ARCHIVE" == true && ! "$FINAL_NAME" =~ $PROJECTS_CFG_NAME_RE ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): the archive is placed at
    # archive/<final-name>/, and a name carrying a separator, '..' or other
    # characters outside the project-name shape would put archived items
    # outside archive/ or under a name no archive reader resolves. Nothing
    # has been extracted or moved yet; the import stops here.
    # CENSUS-ANCHOR: import-project-archive-name-01
    pgai_emit_error_contract "import.invalid_archive_project_name" \
        "import-project.sh: the export carries the project's archive, and '${FINAL_NAME}' is not a valid project name (one matching ${PROJECTS_CFG_NAME_RE}), so it cannot name archive/${FINAL_NAME}/; nothing was imported." \
        "${_rerun}$(printf -- '--name %q' "$_suggested")$([[ "$FORCE" == true ]] && printf ' --force')"
    exit 1
fi

if [[ "$CARRIES_ARCHIVE" == true && -e "$ARCHIVE_DEST" && "$FORCE" == false ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): archive/<final-name>/
    # already holds archived items and their ledger; placing the imported
    # archive there would overwrite or mix two ledgers. Nothing has been
    # extracted or moved yet; --force backs the existing directory up first.
    # CENSUS-ANCHOR: import-project-archive-exists-01
    pgai_emit_error_contract "import.archive_destination_exists" \
        "import-project.sh: the export carries the project's archive, and ${ARCHIVE_DEST} already exists; nothing was imported. Re-running with --force moves the existing directory to ${ARCHIVE_DEST}.bak-<UTCstamp> first and deletes nothing." \
        "${_rerun}$([[ -n "$NEW_NAME" ]] && printf -- '--name %q ' "$NEW_NAME")--force"
    exit 4
fi

# ---------------------------------------------------------------------------
# Clobber guard for the final destination (projects/<final-name>/).
# This is the only project directory the import ever checks, backs up or
# replaces: with --name, projects/<archive-name>/ is not touched at all.
# Checked before anything on disk changes.
# ---------------------------------------------------------------------------
if [[ -e "$FINAL_DIR" && "$FORCE" == false ]]; then
    # BLOCK-WORTHY (docs/blocking-site-census.md): projects/<final-name>/
    # already holds a project, and replacing it unasked would discard it.
    # Nothing has been extracted or moved yet; --force backs it up first.
    # CENSUS-ANCHOR: import-project-destination-exists-01
    pgai_emit_error_contract "import.destination_exists" \
        "import-project.sh: ${FINAL_DIR} already exists; nothing was imported. Re-running with --force moves the existing directory to ${FINAL_DIR}.bak-<UTCstamp> once extraction has succeeded and deletes nothing; re-running with a different --name leaves it alone." \
        "${_rerun}$([[ -n "$NEW_NAME" ]] && printf -- '--name %q ' "$NEW_NAME")--force"
    exit 4
fi

# ---------------------------------------------------------------------------
# Staging directory: the archive is extracted into a fresh
# projects/.import-staging-XXXXXX/ (same filesystem as the final name, so the
# final move is a rename) and never straight into projects/. The EXIT trap
# removes it on every exit path, so a failed or interrupted import leaves no
# staging directory and no half-extracted tree behind.
# ---------------------------------------------------------------------------
STAGING_DIR=""

_remove_staging_dir() {
    if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
        rm -rf -- "$STAGING_DIR" \
            || echo "WARNING: could not remove the import staging directory; remove it by hand: rm -rf -- ${STAGING_DIR}" >&2
    fi
}
trap _remove_staging_dir EXIT
# Route INT/TERM through exit so the EXIT trap still runs.
trap 'exit 130' INT
trap 'exit 143' TERM

if ! mkdir -p "${KANBAN_ROOT}/projects" \
    || ! STAGING_DIR="$(mktemp -d "${KANBAN_ROOT}/projects/.import-staging-XXXXXX")"; then
    STAGING_DIR=""
    # BLOCK-WORTHY (docs/blocking-site-census.md): with no staging directory
    # there is nowhere to extract that cannot clobber an existing project.
    # CENSUS-ANCHOR: import-project-staging-01
    pgai_emit_error_contract "import.staging_unavailable" \
        "import-project.sh: could not create a staging directory under ${KANBAN_ROOT}/projects (missing or not writable); nothing was extracted or moved." \
        "$(printf 'mkdir -p %q && test -w %q && ' "${KANBAN_ROOT}/projects" "${KANBAN_ROOT}/projects")${_rerun_as_given}"
    exit 5
fi

# ---------------------------------------------------------------------------
# Extract the archive into the staging directory.
#
# The archive top-level entry is <archive-name>/ so tar creates
# <staging>/<archive-name>/. A failure here changes nothing under projects/:
# no existing project has been moved yet, and the EXIT trap removes the
# partial extraction.
# ---------------------------------------------------------------------------
if ! tar xzf "$ARCHIVE" -C "$STAGING_DIR"; then
    echo "ERROR: extraction failed for archive: $ARCHIVE" >&2
    echo "  Nothing under ${KANBAN_ROOT}/projects was changed; the partial extraction is removed." >&2
    exit 5
fi

STAGED_DIR="${STAGING_DIR}/${DEST_NAME}"
if [[ ! -d "$STAGED_DIR" ]]; then
    echo "ERROR: extraction appeared to succeed but the archive's project directory '${DEST_NAME}/' is missing from it: $ARCHIVE" >&2
    exit 5
fi

# ---------------------------------------------------------------------------
# Move the extracted tree to projects/<final-name>/. With --force an
# existing projects/<final-name>/ is first moved aside to
# projects/<final-name>.bak-<UTCstamp>; if the move then fails, it is moved
# back. mv -T never moves the tree *into* a directory that appeared since the
# guard above ran.
# ---------------------------------------------------------------------------
BACKUP_DIR=""

if [[ -e "$FINAL_DIR" && "$FORCE" == true ]]; then
    UTC_STAMP="$(date -u '+%Y%m%dT%H%M%SZ')"
    BACKUP_DIR="${FINAL_DIR}.bak-${UTC_STAMP}"
    if ! mv -T "$FINAL_DIR" "$BACKUP_DIR"; then
        echo "ERROR: failed to move existing directory to backup: $BACKUP_DIR" >&2
        echo "  Nothing was imported; ${FINAL_DIR} is unchanged." >&2
        exit 5
    fi
fi

if ! mv -T "$STAGED_DIR" "$FINAL_DIR"; then
    echo "ERROR: failed to move the extracted project from '${STAGED_DIR}' to '${FINAL_DIR}'" >&2
    if [[ -n "$BACKUP_DIR" && -e "$BACKUP_DIR" && ! -e "$FINAL_DIR" ]]; then
        if mv -T "$BACKUP_DIR" "$FINAL_DIR"; then
            echo "  The previous ${FINAL_DIR} was restored from its backup." >&2
        else
            echo "  The previous ${FINAL_DIR} is still at ${BACKUP_DIR}." >&2
        fi
    fi
    exit 5
fi

# ---------------------------------------------------------------------------
# Rename: if --name <newname> was supplied, update project_name in the
# imported project.cfg to the final name.
# ---------------------------------------------------------------------------
if [[ -n "$NEW_NAME" && "$FINAL_NAME" != "$DEST_NAME" ]]; then

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
# Place the project's archive: move the extracted <final-name>/ARCHIVE/ to
# $KANBAN_ROOT/archive/<final-name>/. With --force an existing archive
# directory is first moved aside to archive/<final-name>.bak-<UTCstamp>;
# if the placement then fails, it is moved back.
# ---------------------------------------------------------------------------
EXTRACTED_ARCHIVE="${FINAL_DIR}/ARCHIVE"
if [[ -d "$EXTRACTED_ARCHIVE" ]]; then
    _placement_error=""
    if ! mkdir -p "${KANBAN_ROOT}/archive"; then
        _placement_error="could not create ${KANBAN_ROOT}/archive"
    elif [[ -e "$ARCHIVE_DEST" ]]; then
        ARCHIVE_BACKUP_DIR="${ARCHIVE_DEST}.bak-$(date -u '+%Y%m%dT%H%M%SZ')"
        if ! mv "$ARCHIVE_DEST" "$ARCHIVE_BACKUP_DIR"; then
            _placement_error="could not move the existing ${ARCHIVE_DEST} aside to ${ARCHIVE_BACKUP_DIR}"
            ARCHIVE_BACKUP_DIR=""
        fi
    fi
    if [[ -z "$_placement_error" ]] && ! mv "$EXTRACTED_ARCHIVE" "$ARCHIVE_DEST"; then
        _placement_error="could not move ${EXTRACTED_ARCHIVE} to ${ARCHIVE_DEST}"
        if [[ -n "$ARCHIVE_BACKUP_DIR" && -d "$ARCHIVE_BACKUP_DIR" && ! -e "$ARCHIVE_DEST" ]]; then
            mv "$ARCHIVE_BACKUP_DIR" "$ARCHIVE_DEST" && ARCHIVE_BACKUP_DIR=""
        fi
    fi
    if [[ -n "$_placement_error" ]]; then
        # BLOCK-WORTHY (docs/blocking-site-census.md): the project tree is
        # extracted but its archive could not be put in place (a filesystem
        # failure). Continuing would report an import whose archived items
        # and ledger are not where every archive reader looks. Nothing is
        # deleted: the imported archive stays inside the extracted project.
        # CENSUS-ANCHOR: import-project-archive-placement-01
        pgai_emit_error_contract "import.archive_placement_failed" \
            "import-project.sh: the project was extracted to ${FINAL_DIR}, but its archive could not be placed: ${_placement_error}. The imported archive is still at ${EXTRACTED_ARCHIVE}${ARCHIVE_BACKUP_DIR:+, and the previous archive is at ${ARCHIVE_BACKUP_DIR}}; nothing was deleted." \
            "$(printf 'mkdir -p %q && mv -T %q %q' "${KANBAN_ROOT}/archive" "$EXTRACTED_ARCHIVE" "$ARCHIVE_DEST")"
        exit 5
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

    # Extract the registration block: the body of "## registration" (exact
    # heading, letter case counts) up to the next "##" line or end of file,
    # read by read_md_field.  The block includes the INI section header line
    # itself (e.g. [project:NAME]) and all key=value lines.
    MANIFEST_REGISTRATION_BLOCK="$(read_md_field --exact-heading "$MANIFEST_FILE" registration 2>/dev/null || true)"

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
# Both paths build the one entry through _registration_entry, so they treat
# the same MANIFEST block the same way:
#   - a block with neither an accepted header nor a legacy NAME:PRIORITY line
#     is refused under --register (exit 5) and, without it, reported with a
#     warning and nothing offered to paste (exit 0);
#   - a legacy line is offered or appended as a legacy line when projects.cfg
#     is legacy, and as a converted [project:<final-name>] section when it is
#     INI;
#   - otherwise only one section is appended or offered: the first accepted
#     header, as [project:<final-name>], and the lines under it up to the
#     next [project:...] header, any other kind of section header inside it
#     removed.  Non-comment lines before the first header, sections whose
#     header the registry rejects, and every further accepted section are
#     left out, with a warning naming them (exit 0 on both paths).  No
#     section the registry would ignore, no line it would attribute to
#     another project and no second project is ever appended or offered.
#
# Guard: if projects.cfg already registers the final name (projects_cfg_has),
# warn and skip (do NOT duplicate it).
# ---------------------------------------------------------------------------
PROJECTS_CFG="${KANBAN_ROOT}/projects.cfg"

# _registration_entry_target — "colon-legacy" when projects.cfg exists in
# the legacy NAME:PRIORITY[:COLOR] format (projects_cfg_format) and already
# holds a data line; "ini" otherwise, a missing or empty registry included.
_registration_entry_target() {
    if [[ -f "$PROJECTS_CFG" ]] \
        && [[ "$(projects_cfg_format "$PROJECTS_CFG")" == "colon-legacy" ]] \
        && grep -qvE '^[[:space:]]*([#;]|$)' "$PROJECTS_CFG"; then
        echo "colon-legacy"
    else
        echo "ini"
    fi
}

# _registration_entry BLOCK TARGET — print the one entry to register for
# BLOCK and return its kind; this is the one place a usable header is found.
#
#   0  An INI section: BLOCK's first [project:NAME] header whose NAME the
#      registry accepts (project_header in PROJECTS_CFG_AWK_HEADER,
#      lib/projects_cfg.sh), printed as [project:<final-name>], and the lines
#      under it up to the next [project:...] header.  A section header of
#      any other kind inside it is dropped (the registry would end the
#      project's section there and lose the keys after it);
#      _warn_foreign_section_headers names it.  Also returned when BLOCK
#      has no such header but a legacy NAME:PRIORITY[:COLOR] line whose NAME
#      the registry accepts and TARGET is "ini": the line converted to a
#      [project:<final-name>] section with priority= and dashboard_color=.
#   3  That legacy line with TARGET "colon-legacy": printed as
#      <final-name>:PRIORITY[:COLOR], the form a legacy registry reads.
#   1  Nothing usable; nothing printed.
#
# Lines before the header would be read as part of whichever section
# precedes them in projects.cfg, and any later section would register or
# overwrite a project other than <final-name>, so neither is printed;
# _warn_left_out_registration_content names them.
_registration_entry() {
    printf '%s\n' "$1" | \
        awk -v final="${FINAL_NAME}" -v target="${2:-ini}" "${PROJECTS_CFG_AWK_HEADER}"'
            project_section($0) {
                if (kept) { done = 1 }
                else if (project_header($0) != "") {
                    print "[project:" final "]"
                    kept = 1
                    next
                }
            }
            kept && !done {
                if ($0 ~ /^[[:space:]]*\[.*\][[:space:]]*$/) next
                print
                next
            }
            !kept && legacy == "" && $0 ~ /^[[:space:]]*[^#;[[:space:]][^=]*$/ && index($0, ":") > 0 {
                legacy = $0
            }
            END {
                if (kept) exit 0
                if (legacy == "") exit 1
                n = split(legacy, field, ":")
                for (i = 1; i <= n; i++) gsub(/^[[:space:]]+|[[:space:]]+$/, "", field[i])
                if (project_header("[project:" field[1] "]") == "" || field[2] == "") exit 1
                if (target == "colon-legacy") {
                    print final ":" field[2] (n >= 3 && field[3] != "" ? ":" field[3] : "")
                    exit 3
                }
                print "[project:" final "]"
                print "priority=" field[2]
                if (n >= 3 && field[3] != "") print "dashboard_color=" field[3]
                exit 0
            }
        '
}

# _warn_foreign_section_headers BLOCK — on stderr, name each section header
# of a kind other than [project:...] that sits inside the section
# _registration_entry keeps (and drops).  Prints nothing when there is none.
_warn_foreign_section_headers() {
    local foreign
    foreign="$(awk "${PROJECTS_CFG_AWK_HEADER}"'
            project_section($0) {
                if (kept) exit
                if (project_header($0) != "") kept = 1
                next
            }
            kept && $0 ~ /^[[:space:]]*\[.*\][[:space:]]*$/ { print "    " $0 }
        ' <<< "$1")"
    [[ -n "$foreign" ]] || return 0
    echo "WARNING: the MANIFEST registration block holds a section header that is not [project:NAME] inside the section registered for '${FINAL_NAME}';" >&2
    echo "  projects.cfg readers would end the project's section there and drop the keys after it, so the header is removed and those keys stay in [project:${FINAL_NAME}]:" >&2
    printf '%s\n' "$foreign" >&2
}

# _warn_rejected_registration_sections BLOCK — on stderr, name each
# [project:NAME] section of BLOCK whose NAME the registry rejects (sections
# _registration_entry leaves out), and say where their keys still
# are.  Prints nothing when every section is accepted.
_warn_rejected_registration_sections() {
    local rejected
    rejected="$(awk "${PROJECTS_CFG_AWK_HEADER}"'
            project_section($0) && project_header($0) == "" {
                print "    [project:" project_section_name($0) "]"
            }
        ' <<< "$1")"
    [[ -n "$rejected" ]] || return 0
    echo "WARNING: the MANIFEST registration block holds [project:NAME] sections the registry does not accept" >&2
    echo "  (NAME must match ${PROJECTS_CFG_NAME_RE}); they are left out of the section registered for '${FINAL_NAME}':" >&2
    printf '%s\n' "$rejected" >&2
    echo "  Their keys remain in '${MANIFEST_FILE}'; copy any you need into the [project:${FINAL_NAME}] section by hand." >&2
}

# _warn_left_out_registration_content BLOCK — on stderr, name the rest of
# what _registration_entry leaves out of an INI BLOCK, beside the
# rejected-name sections _warn_rejected_registration_sections names: every
# non-blank, non-comment line before BLOCK's first [project:...] header (a
# comment line is read by no registry reader, so it is dropped without a
# warning), and the header
# of every section after the first accepted one whose NAME the registry also
# accepts.  Prints nothing when BLOCK holds neither.
_warn_left_out_registration_content() {
    local left_out stray extra
    left_out="$(awk "${PROJECTS_CFG_AWK_HEADER}"'
            project_section($0) {
                in_section = 1
                if (project_header($0) != "") {
                    if (accepted) print "extra\t    [project:" project_header($0) "]"
                    accepted = 1
                }
                next
            }
            !in_section && /[^[:space:]]/ && !/^[[:space:]]*[#;]/ { print "stray\t    " $0 }
        ' <<< "$1")"
    stray="$(sed -n 's/^stray\t//p' <<< "$left_out")"
    extra="$(sed -n 's/^extra\t//p' <<< "$left_out")"
    if [[ -n "$stray" ]]; then
        echo "WARNING: the MANIFEST registration block holds lines before its first [project:NAME] header;" >&2
        echo "  in projects.cfg they would be read as part of whichever section precedes them, so they are" >&2
        echo "  left out of the section registered for '${FINAL_NAME}':" >&2
        printf '%s\n' "$stray" >&2
        echo "  Copy any you need into the [project:${FINAL_NAME}] section by hand." >&2
    fi
    if [[ -n "$extra" ]]; then
        echo "WARNING: the MANIFEST registration block holds more than one [project:NAME] section the registry accepts;" >&2
        echo "  only the first is registered, as [project:${FINAL_NAME}], and these are left out:" >&2
        printf '%s\n' "$extra" >&2
        echo "  Their keys remain in '${MANIFEST_FILE}'; copy any you need into the [project:${FINAL_NAME}] section by hand." >&2
    fi
    return 0
}

if [[ "$REGISTER" == true ]]; then
    # Registration block is required when --register is given.
    if [[ -z "$MANIFEST_REGISTRATION_BLOCK" ]]; then
        echo "ERROR: --register was supplied but the MANIFEST contains no registration block." >&2
        echo "  Cannot auto-register project '${FINAL_NAME}' without a [project:NAME] INI block." >&2
        echo "  Inspect '${MANIFEST_FILE}' manually and add the section to ${PROJECTS_CFG} by hand." >&2
        exit 5
    fi

    # Build the one entry to register: the first section whose header the
    # registry accepts, renamed to the final name (handles the --name rename
    # case; the block from MANIFEST carries the archive's original project
    # name), or a legacy line in the registry's own format.  A block with
    # neither is refused rather than appended, and printed as it stands.
    ENTRY_TARGET="$(_registration_entry_target)"
    ENTRY_STATUS=0
    REWRITTEN_BLOCK="$(_registration_entry "$MANIFEST_REGISTRATION_BLOCK" "$ENTRY_TARGET")" || ENTRY_STATUS=$?
    if [[ "$ENTRY_STATUS" -eq 1 ]]; then
        echo "ERROR: registration block from MANIFEST does not contain a valid [project:NAME] header or NAME:PRIORITY line." >&2
        echo "  Block content:" >&2
        printf '%s\n' "$MANIFEST_REGISTRATION_BLOCK" | sed 's/^/    /' >&2
        exit 5
    fi

    # Check for duplicate — never append a project the registry already has.
    if projects_cfg_has "$FINAL_NAME"; then
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

        _warn_rejected_registration_sections "$MANIFEST_REGISTRATION_BLOCK"
        _warn_left_out_registration_content "$MANIFEST_REGISTRATION_BLOCK"
        _warn_foreign_section_headers "$MANIFEST_REGISTRATION_BLOCK"

        # Append the registration entry.  Ensure a trailing newline separates it
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
    # --register not supplied: print the block the operator must paste manually,
    # rewritten exactly as --register would append it -- the one section to
    # register, header renamed to the final name, everything else in the
    # block left out with a warning.  A block with no header the registry
    # accepts is never offered for pasting: a warning says so instead.
    # Either way the import still exits 0 -- the project is already in
    # place, only its registration is left to do.
    if [[ -n "$MANIFEST_REGISTRATION_BLOCK" ]]; then
        ENTRY_STATUS=0
        REWRITTEN_BLOCK="$(_registration_entry "$MANIFEST_REGISTRATION_BLOCK" "$(_registration_entry_target)")" || ENTRY_STATUS=$?
        if [[ "$ENTRY_STATUS" -ne 1 ]]; then
            _warn_rejected_registration_sections "$MANIFEST_REGISTRATION_BLOCK"
            _warn_left_out_registration_content "$MANIFEST_REGISTRATION_BLOCK"
            _warn_foreign_section_headers "$MANIFEST_REGISTRATION_BLOCK"
            echo ""
            if [[ "$ENTRY_STATUS" -eq 3 ]]; then
                echo "NOTICE: To register this project, add the following line to: ${PROJECTS_CFG}"
            else
                echo "NOTICE: To register this project, add the following section to: ${PROJECTS_CFG}"
            fi
            echo ""
            printf '%s\n' "$REWRITTEN_BLOCK"
            echo ""
            echo "  Or re-run with --register to have it added automatically."
        else
            echo "" >&2
            echo "WARNING: the MANIFEST registration block has no [project:NAME] header or NAME:PRIORITY line the registry accepts" >&2
            echo "  (NAME must match ${PROJECTS_CFG_NAME_RE}), so nothing is offered to paste and --register would refuse it." >&2
            echo "  To register project '${FINAL_NAME}', add a [project:${FINAL_NAME}] section to ${PROJECTS_CFG} by hand," >&2
            echo "  taking its keys from the registration block in '${MANIFEST_FILE}'." >&2
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Success output
# ---------------------------------------------------------------------------
echo "Imported project '${DEST_NAME}' to: ${FINAL_DIR}"
if [[ -n "$BACKUP_DIR" ]]; then
    echo "  previous directory backed up to: ${BACKUP_DIR}"
fi
if [[ -n "$NEW_NAME" && "$FINAL_NAME" != "$DEST_NAME" ]]; then
    echo "  renamed to: ${FINAL_DIR}"
fi
echo "  archive: ${ARCHIVE}"
echo "  final location: ${FINAL_DIR}"
if [[ -d "$ARCHIVE_DEST" && "$CARRIES_ARCHIVE" == true ]]; then
    echo "  archived items placed in: ${ARCHIVE_DEST}"
    if [[ -n "$ARCHIVE_BACKUP_DIR" ]]; then
        echo "  previous archive backed up to: ${ARCHIVE_BACKUP_DIR}"
    fi
fi

# ---------------------------------------------------------------------------
# Operator notice: blanked env-specific fields and next-step guidance.
# Mirrors the post-create notice from create-project.sh. Step 2 names the
# remote init-project-git-repo.sh pushes to: the imported project.cfg's
# git_remote_name (kept from the archive), or origin when none is set.
# ---------------------------------------------------------------------------
if [[ "${#BLANKED_FIELDS[@]}" -gt 0 ]]; then
    # An invalid git_remote_name is narrated, not refused: the import has
    # already succeeded, and init-project-git-repo.sh refuses that value
    # with its own remedy when the operator runs step 2.
    _push_remote="$(pp_git_remote_name --quiet "$FINAL_NAME")" \
        || _push_remote="the configured remote once git_remote_name in project.cfg holds a valid remote name"
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
    echo "    2. Push the chain's base branches to ${_push_remote} (run once, before first release):"
    echo "       init-project-git-repo.sh --project ${FINAL_NAME}"
    echo "    3. When ready to authorize releases, raise the ceiling:"
    echo "       set-version-ceiling.sh --project ${FINAL_NAME} --minor <N>"
fi
