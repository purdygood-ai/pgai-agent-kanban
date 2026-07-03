#!/usr/bin/env bash
# purge-old-files.sh
# Periodic disk-hygiene script for the pgai-agent-kanban framework.
# Removes old task folders, log archives, shipped bundle requirements,
# closed bug files, and closed priority files that have exceeded their
# configured retention windows.
#
# SAFETY: dry-run is the default.  Nothing is deleted unless --apply is passed.
#
# Usage:
#   purge-old-files.sh [OPTIONS]
#
# OPTIONS:
#   --days N              Default retention in days for all categories (default: 30)
#   --tasks-days N        Override retention for task folders (default: 30)
#   --logs-days N         Override retention for log archives (default: 7)
#   --bundles-days N      Override retention for shipped bundles (default: 30)
#   --bugs-days N         Override retention for closed bug files (default: 30)
#   --priorities-days N   Override retention for closed priority files (default: 30)
#   --project NAME        Limit purge to one project (default: all projects)
#   --include-blocked     Also purge BLOCKED tasks (rare; explicit opt-in required)
#   --apply               Actually delete files (default: dry-run preview only)
#   --archive             Tar all purged files to $KANBAN_ROOT/archive/purge-YYYYMMDDTHHMMSSZ.tar.gz
#                         BEFORE any deletion.  If tar fails the purge is aborted.
#   --verbose             Show every file considered, not just purged items
#   --quiet               Suppress per-file output; print summary only
#   --help                Print this usage text
#
# EXAMPLES:
#   # Dry-run with all defaults (see what WOULD be purged)
#   purge-old-files.sh
#
#   # Actually delete files older than 30 days
#   purge-old-files.sh --apply
#
#   # Aggressive: purge anything older than 14 days, including BLOCKED tasks, archive first
#   purge-old-files.sh --days 14 --include-blocked --archive --apply
#
#   # Just one project
#   purge-old-files.sh --project pgai-video-generator --apply
#
#   # Long-term retention for bugs, short for tasks
#   purge-old-files.sh --tasks-days 14 --bugs-days 90 --apply
#
# OUTPUT TAGS (greppable):
#   [WOULD PURGE]     dry-run: item eligible for deletion
#   [PURGED]          apply: item deleted
#   [SKIP]            item kept (too recent, wrong state, active-RC guard, etc.)
#   [PURGED dir]      apply: empty tasks/ subdir removed after this run's deletions
#   [WOULD PURGE dir] dry-run: tasks/ subdir would be emptied and removed
#
# EXIT CODES:
#   0 — success (dry-run or apply completed normally)
#   1 — error (missing environment, unreadable root, bad arguments, archive failure)

# ---------------------------------------------------------------------------
# Bootstrap: resolve kanban root BEFORE strict mode.
# Config files may reference unset variables; source them outside set -euo.
# ---------------------------------------------------------------------------

KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"

# Validate required environment BEFORE strict mode so the error message is clean.
if [[ -z "$KANBAN_ROOT" ]]; then
    echo "ERROR: PGAI_AGENT_KANBAN_ROOT_PATH is not set." >&2
    echo "  Export one of these variables to point at your kanban install root before running." >&2
    exit 1
fi

if [[ ! -d "$KANBAN_ROOT" ]]; then
    echo "ERROR: kanban root directory does not exist: $KANBAN_ROOT" >&2
    echo "  Check that PGAI_AGENT_KANBAN_ROOT_PATH is correct." >&2
    exit 1
fi

# Resolve script directory for sourcing helpers.
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source per-category helpers.
# shellcheck source=lib/purge-helpers.sh
if [[ -f "${_SCRIPT_DIR}/../lib/purge-helpers.sh" ]]; then
    source "${_SCRIPT_DIR}/../lib/purge-helpers.sh"
else
    echo "ERROR: required helper not found: ${_SCRIPT_DIR}/../lib/purge-helpers.sh" >&2
    exit 1
fi
unset _SCRIPT_DIR

# ---------------------------------------------------------------------------
# Strict mode — enable AFTER validation and sourcing.
# ---------------------------------------------------------------------------
set -euo pipefail

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'EOF'
Usage: purge-old-files.sh [OPTIONS]

Disk hygiene for the kanban project. Removes old task folders, log
archives, shipped bundle requirements, closed bugs, and closed priorities that
have exceeded their configured retention windows.

SAFETY: dry-run is the DEFAULT. Nothing is deleted unless --apply is passed.

OPTIONS:
  --days N              Default retention in days for all categories (default: 30)
  --tasks-days N        Override retention for task folders (default: 30)
  --logs-days N         Override retention for log archives (default: 7)
  --bundles-days N      Override retention for shipped bundles (default: 30)
  --bugs-days N         Override retention for closed bug files (default: 30)
  --priorities-days N   Override retention for closed priority files (default: 30)
  --project NAME        Limit purge to one project (default: all projects)
  --include-blocked     Also purge BLOCKED tasks (rare; explicit opt-in required)
  --apply               Actually delete files (default: dry-run preview only)
  --archive             Tar purged files to $KANBAN_ROOT/archive/purge-TIMESTAMP.tar.gz
                        BEFORE deleting them.  If tar fails the entire purge is
                        aborted; no files are deleted.
  --verbose             Show every file considered, not just purged items
  --quiet               Suppress per-file output; print summary only
  --help                Print this usage text

OUTPUT TAGS (greppable):
  [WOULD PURGE]      dry-run: item eligible for deletion
  [PURGED]           apply: item deleted
  [SKIP]             item kept: too recent, wrong state, or active-RC guard
  [PURGED dir]       apply: empty tasks/ subdir removed after this run
  [WOULD PURGE dir]  dry-run: tasks/ subdir would be emptied and removed

EXAMPLES:
  # Preview what would be purged (safe; default)
  purge-old-files.sh

  # Delete files older than 30 days (standard apply)
  purge-old-files.sh --apply

  # Aggressive: 14-day cutoff, include BLOCKED tasks, archive before delete
  purge-old-files.sh --days 14 --include-blocked --archive --apply

  # Restrict to one project
  purge-old-files.sh --project pgai-video-generator --apply

  # Different retention per category
  purge-old-files.sh --tasks-days 14 --bugs-days 90 --apply

CRON EXAMPLE:
  # Weekly purge, Sunday 03:00 UTC, archive first
  0 3 * * 0 PGAI_AGENT_KANBAN_ROOT_PATH=/home/youruser/pgai_agent_kanban \
      /home/youruser/pgai_agent_kanban/scripts/cleanup/purge-old-files.sh --archive --apply \
      >> /home/youruser/pgai_agent_kanban/logs/cron-purge.log 2>&1

EXIT CODES:
  0  success
  1  error (missing environment, unreadable root, bad arguments, archive failure)
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DAYS_DEFAULT=30
TASKS_DAYS=""
LOGS_DAYS=""
BUNDLES_DAYS=""
BUGS_DAYS=""
PRIORITIES_DAYS=""
PROJECT_FILTER=""
INCLUDE_BLOCKED=false
APPLY=false
ARCHIVE=false
VERBOSE=false
QUIET=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --days requires a positive integer argument." >&2
                exit 1
            fi
            DAYS_DEFAULT="$2"
            shift 2
            ;;
        --tasks-days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --tasks-days requires a positive integer argument." >&2
                exit 1
            fi
            TASKS_DAYS="$2"
            shift 2
            ;;
        --logs-days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --logs-days requires a positive integer argument." >&2
                exit 1
            fi
            LOGS_DAYS="$2"
            shift 2
            ;;
        --bundles-days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --bundles-days requires a positive integer argument." >&2
                exit 1
            fi
            BUNDLES_DAYS="$2"
            shift 2
            ;;
        --bugs-days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --bugs-days requires a positive integer argument." >&2
                exit 1
            fi
            BUGS_DAYS="$2"
            shift 2
            ;;
        --priorities-days)
            if [[ -z "${2:-}" ]] || ! [[ "${2}" =~ ^[0-9]+$ ]]; then
                echo "ERROR: --priorities-days requires a positive integer argument." >&2
                exit 1
            fi
            PRIORITIES_DAYS="$2"
            shift 2
            ;;
        --project)
            if [[ -z "${2:-}" ]]; then
                echo "ERROR: --project requires a project name argument." >&2
                exit 1
            fi
            PROJECT_FILTER="$2"
            shift 2
            ;;
        --include-blocked)
            INCLUDE_BLOCKED=true
            shift
            ;;
        --apply)
            APPLY=true
            shift
            ;;
        --archive)
            ARCHIVE=true
            shift
            ;;
        --verbose)
            VERBOSE=true
            shift
            ;;
        --quiet)
            QUIET=true
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run '$(basename "$0") --help' for usage." >&2
            exit 1
            ;;
    esac
done

# Validate conflicting options.
if [[ "$VERBOSE" == "true" ]] && [[ "$QUIET" == "true" ]]; then
    echo "ERROR: --verbose and --quiet are mutually exclusive." >&2
    exit 1
fi

# Resolve per-category days: per-category flag overrides --days.
EFFECTIVE_TASKS_DAYS="${TASKS_DAYS:-$DAYS_DEFAULT}"
EFFECTIVE_LOGS_DAYS="${LOGS_DAYS:-7}"       # logs default is 7, not DAYS_DEFAULT
EFFECTIVE_BUNDLES_DAYS="${BUNDLES_DAYS:-$DAYS_DEFAULT}"
EFFECTIVE_BUGS_DAYS="${BUGS_DAYS:-$DAYS_DEFAULT}"
EFFECTIVE_PRIORITIES_DAYS="${PRIORITIES_DAYS:-$DAYS_DEFAULT}"

# Allow --logs-days to override the 7-day logs default when --days alone is
# passed: if the user set --days but not --logs-days, keep the 7-day logs
# default.  If the user set --logs-days explicitly, that value is already in
# EFFECTIVE_LOGS_DAYS.

# ---------------------------------------------------------------------------
# Run timestamp and log setup
# ---------------------------------------------------------------------------
RUN_TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
PURGE_LOG_DIR="${KANBAN_ROOT}/logs"
PURGE_LOG="${PURGE_LOG_DIR}/purge-${RUN_TIMESTAMP}.log"

# Ensure the log directory exists.
mkdir -p "$PURGE_LOG_DIR"

# Archive path (used only when --archive is set).
ARCHIVE_DIR="${KANBAN_ROOT}/archive"
ARCHIVE_TARBALL="${ARCHIVE_DIR}/purge-${RUN_TIMESTAMP}.tar.gz"

# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------
# plog: write a line to both stdout (unless --quiet) and the run log.
plog() {
    local line="$*"
    echo "$line" >> "$PURGE_LOG"
    if [[ "$QUIET" == "false" ]]; then
        echo "$line"
    fi
}

# plog_item: write a per-file tagged line; suppressed by --quiet; shown in
# verbose mode for [SKIP] items, always shown for [WOULD PURGE] and [PURGED].
plog_item() {
    local tag="$1"
    local path="$2"
    local detail="${3:-}"
    local line
    if [[ -n "$detail" ]]; then
        line="${tag} ${path} (${detail})"
    else
        line="${tag} ${path}"
    fi
    echo "$line" >> "$PURGE_LOG"
    if [[ "$QUIET" == "false" ]]; then
        case "$tag" in
            "[SKIP]")
                [[ "$VERBOSE" == "true" ]] && echo "$line" || true
                ;;
            *)
                echo "$line"
                ;;
        esac
    fi
}

# plog_summary: always printed (even in --quiet mode); always written to log.
plog_summary() {
    local line="$*"
    echo "$line" | tee -a "$PURGE_LOG"
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
if [[ "$APPLY" == "true" ]]; then
    plog "purge-old-files.sh (APPLYING)"
else
    plog "purge-old-files.sh (DRY-RUN — pass --apply to actually delete)"
fi

CUTOFF_DATE="$(date -u -d "-${DAYS_DEFAULT} days" +%Y-%m-%d 2>/dev/null \
    || date -u -v-"${DAYS_DEFAULT}d" +%Y-%m-%d 2>/dev/null \
    || echo "(cutoff date unavailable)")"

plog "Threshold: files older than ${DAYS_DEFAULT} days (default)"
plog "  tasks:      ${EFFECTIVE_TASKS_DAYS} days"
plog "  logs:       ${EFFECTIVE_LOGS_DAYS} days"
plog "  bundles:    ${EFFECTIVE_BUNDLES_DAYS} days"
plog "  bugs:       ${EFFECTIVE_BUGS_DAYS} days"
plog "  priorities: ${EFFECTIVE_PRIORITIES_DAYS} days"
plog "Cutoff date (default threshold): ${CUTOFF_DATE}"
plog "Include BLOCKED tasks: ${INCLUDE_BLOCKED}"
plog "Archive mode: ${ARCHIVE}"
[[ -n "$PROJECT_FILTER" ]] && plog "Project filter: ${PROJECT_FILTER}"
plog "Run log: ${PURGE_LOG}"
plog ""

# ---------------------------------------------------------------------------
# Project discovery
# ---------------------------------------------------------------------------
# Build the list of project root directories to process.
PROJECTS_DIR="${KANBAN_ROOT}/projects"

declare -a PROJECT_ROOTS=()

if [[ -n "$PROJECT_FILTER" ]]; then
    # Single-project mode.
    _proj_root="${PROJECTS_DIR}/${PROJECT_FILTER}"
    if [[ ! -d "$_proj_root" ]]; then
        echo "ERROR: project not found: ${_proj_root}" >&2
        echo "  Check that --project NAME matches a directory under ${PROJECTS_DIR}/" >&2
        exit 1
    fi
    PROJECT_ROOTS=("$_proj_root")
    unset _proj_root
elif [[ -d "$PROJECTS_DIR" ]]; then
    # All-projects mode.
    for _dir in "${PROJECTS_DIR}"/*/; do
        [[ -d "$_dir" ]] || continue
        # Never traverse .git/ directories.
        case "$_dir" in
            */.git/*|*/.git/) continue ;;
        esac
        PROJECT_ROOTS+=("${_dir%/}")
    done
    unset _dir
else
    echo "ERROR: projects directory not found: ${PROJECTS_DIR}" >&2
    echo "  Verify that PGAI_AGENT_KANBAN_ROOT_PATH points at a valid kanban install." >&2
    exit 1
fi

if [[ "${#PROJECT_ROOTS[@]}" -eq 0 ]]; then
    plog "No projects found under ${PROJECTS_DIR} — nothing to do."
    plog ""
    plog_summary "Summary:"
    plog_summary "  No projects processed."
    exit 0
fi

# ---------------------------------------------------------------------------
# Internal: _run_project_helpers <project_root> <apply_flag>
# Run all five per-category helpers for one project.
# Accumulates results in the global PROJECT_COUNTS, PROJECT_BYTES,
# TOTAL_COUNT, and TOTAL_BYTES associative arrays.
# ---------------------------------------------------------------------------
TOTAL_COUNT=0
TOTAL_BYTES=0
declare -A PROJECT_COUNTS=()
declare -A PROJECT_BYTES=()
_HELPER_COUNT=0
_HELPER_BYTES=0

# parse_helper_result <final_stdout_line>
# Parses the "<count> <bytes>" final line that every helper emits.
parse_helper_result() {
    local result="$1"
    _HELPER_COUNT="${result%% *}"
    _HELPER_BYTES="${result##* }"
    [[ "$_HELPER_COUNT" =~ ^[0-9]+$ ]] || _HELPER_COUNT=0
    [[ "$_HELPER_BYTES" =~ ^[0-9]+$ ]] || _HELPER_BYTES=0
}

# _tasks_label: return the section label for task folders based on INCLUDE_BLOCKED.
_tasks_label() {
    if [[ "$INCLUDE_BLOCKED" == "true" ]]; then
        echo "  Task folders (DONE/WONT-DO/BLOCKED):"
    else
        echo "  Task folders (DONE/WONT-DO):"
    fi
}

_run_project_helpers() {
    local project_root="$1"
    local apply_flag="$2"
    local project_name
    project_name="$(basename "$project_root")"
    local proj_count=0
    local proj_bytes=0
    local _result

    plog "$(_tasks_label)"
    _result="$(purge_tasks \
        "$project_root" \
        "$EFFECTIVE_TASKS_DAYS" \
        "$apply_flag" \
        "$INCLUDE_BLOCKED" \
        2>&1 | tee -a "$PURGE_LOG" | tail -1)"
    parse_helper_result "$_result"
    proj_count=$(( proj_count + _HELPER_COUNT ))
    proj_bytes=$(( proj_bytes + _HELPER_BYTES ))

    plog "  Log archives:"
    _result="$(purge_log_archives \
        "$project_root" \
        "$EFFECTIVE_LOGS_DAYS" \
        "$apply_flag" \
        2>&1 | tee -a "$PURGE_LOG" | tail -1)"
    parse_helper_result "$_result"
    proj_count=$(( proj_count + _HELPER_COUNT ))
    proj_bytes=$(( proj_bytes + _HELPER_BYTES ))

    plog "  Shipped bundles:"
    _result="$(purge_shipped_bundles \
        "$project_root" \
        "$EFFECTIVE_BUNDLES_DAYS" \
        "$apply_flag" \
        2>&1 | tee -a "$PURGE_LOG" | tail -1)"
    parse_helper_result "$_result"
    proj_count=$(( proj_count + _HELPER_COUNT ))
    proj_bytes=$(( proj_bytes + _HELPER_BYTES ))

    plog "  Closed bugs:"
    _result="$(purge_closed_bugs \
        "$project_root" \
        "$EFFECTIVE_BUGS_DAYS" \
        "$apply_flag" \
        2>&1 | tee -a "$PURGE_LOG" | tail -1)"
    parse_helper_result "$_result"
    proj_count=$(( proj_count + _HELPER_COUNT ))
    proj_bytes=$(( proj_bytes + _HELPER_BYTES ))

    plog "  Closed priorities:"
    _result="$(purge_closed_priorities \
        "$project_root" \
        "$EFFECTIVE_PRIORITIES_DAYS" \
        "$apply_flag" \
        2>&1 | tee -a "$PURGE_LOG" | tail -1)"
    parse_helper_result "$_result"
    proj_count=$(( proj_count + _HELPER_COUNT ))
    proj_bytes=$(( proj_bytes + _HELPER_BYTES ))

    PROJECT_COUNTS["$project_name"]="${proj_count}"
    PROJECT_BYTES["$project_name"]="${proj_bytes}"
    TOTAL_COUNT=$(( TOTAL_COUNT + proj_count ))
    TOTAL_BYTES=$(( TOTAL_BYTES + proj_bytes ))

    plog ""
}

# _run_empty_dir_cleanup <project_root> <apply_flag> <nonempty_snapshot>
# Run the empty-directory cleanup step for one project after a purge pass.
# Prints log lines for each dir removed (or would-remove).
_run_empty_dir_cleanup() {
    local project_root="$1"
    local apply_flag="$2"
    local nonempty_snapshot="$3"

    [[ -z "${nonempty_snapshot}" ]] && return 0

    if [[ "${apply_flag}" == "true" ]]; then
        plog "  Empty task dirs (cleaned):"
    else
        plog "  Empty task dirs (would be cleaned):"
    fi

    # Run purge_empty_task_dirs and print each non-empty output line.
    while IFS= read -r dline; do
        [[ -z "${dline}" ]] && continue
        plog "    ${dline}"
    done < <(purge_empty_task_dirs "$project_root" "${nonempty_snapshot}" "${apply_flag}" 2>&1)
}

# ---------------------------------------------------------------------------
# _archive_stage_candidates <dry_run_log_file> <stage_dir>
# Parse [WOULD PURGE] lines from the dry-run log, copy each item to the
# staging dir preserving its path relative to KANBAN_ROOT.
# Returns 0 on success, 1 if any copy fails.
# ---------------------------------------------------------------------------
_archive_stage_candidates() {
    local dry_run_log="$1"
    local stage_dir="$2"
    local failed=0

    while IFS= read -r line; do
        # Lines: [WOULD PURGE] /abs/path/to/item (optional detail)
        local item_path
        item_path="${line#\[WOULD PURGE\] }"
        item_path="${item_path%% (*}"
        item_path="${item_path%% }"
        [[ -z "${item_path}" ]] && continue

        # Skip if the item no longer exists (race between passes).
        if [[ ! -e "${item_path}" ]]; then
            echo "  [WARN] archive: item missing before copy: ${item_path}" >&2
            continue
        fi

        # Compute relative path from KANBAN_ROOT.
        local rel_path="${item_path#${KANBAN_ROOT}/}"
        if [[ "${rel_path}" == "${item_path}" ]]; then
            rel_path="$(basename "${item_path}")"
        fi

        local dest_parent="${stage_dir}/$(dirname "${rel_path}")"
        if ! mkdir -p "${dest_parent}"; then
            echo "  [ERROR] archive: mkdir failed: ${dest_parent}" >&2
            failed=1
            continue
        fi

        if [[ -d "${item_path}" ]]; then
            cp -a "${item_path}" "${dest_parent}/" 2>/dev/null \
                || { echo "  [ERROR] archive: cp failed for dir: ${item_path}" >&2; failed=1; }
        else
            cp -a "${item_path}" "${dest_parent}/$(basename "${item_path}")" 2>/dev/null \
                || { echo "  [ERROR] archive: cp failed for file: ${item_path}" >&2; failed=1; }
        fi
    done < <(grep '^\[WOULD PURGE\]' "${dry_run_log}" 2>/dev/null || true)

    return "${failed}"
}

# ---------------------------------------------------------------------------
# Pre-run snapshots: record which tasks/ subdirs are currently non-empty.
# Taken BEFORE any deletion so purge_empty_task_dirs knows what this run
# emptied (vs. directories that were already empty before we started).
# ---------------------------------------------------------------------------
declare -A NONEMPTY_SNAPSHOTS=()
for project_root in "${PROJECT_ROOTS[@]}"; do
    project_name="$(basename "$project_root")"
    NONEMPTY_SNAPSHOTS["$project_name"]="$(snapshot_nonempty_task_dirs "$project_root")"
done
unset project_root project_name

# ---------------------------------------------------------------------------
# Main execution: three branches — dry-run, archive+apply, plain apply.
# ---------------------------------------------------------------------------

if [[ "$APPLY" == "false" ]]; then
    # ---- Pure dry-run: show what would be purged, no deletions ----
    for project_root in "${PROJECT_ROOTS[@]}"; do
        project_name="$(basename "$project_root")"
        plog "Project: ${project_name}"
        _run_project_helpers "$project_root" "false"
        _run_empty_dir_cleanup "$project_root" "false" "${NONEMPTY_SNAPSHOTS["$project_name"]}"
    done

elif [[ "$ARCHIVE" == "true" ]]; then
    # ---- Archive + apply: two-pass approach ----
    #
    # The tarball MUST exist before any file is deleted (constraint from spec).
    # Strategy:
    #   Pass 1  — dry-run; collect [WOULD PURGE] paths into DRYRUN_LOG.
    #   Stage   — copy each candidate into a temp dir preserving relative paths.
    #   Tar     — create $KANBAN_ROOT/archive/purge-TIMESTAMP.tar.gz from stage.
    #   Abort   — if tar fails, exit 1; no deletions have happened yet.
    #   Pass 2  — apply; delete originals and run empty-dir cleanup.

    mkdir -p "$ARCHIVE_DIR"
    DRYRUN_LOG="$(mktemp "${ARCHIVE_DIR}/purge-dryrun-XXXXXX.log")"

    plog "Archive mode: collecting candidates (pass 1 — dry-run)..."
    plog ""

    # Pass 1: collect [WOULD PURGE] lines from all projects.
    for project_root in "${PROJECT_ROOTS[@]}"; do
        project_name="$(basename "$project_root")"
        plog "Project: ${project_name} (scanning)"
        {
            purge_tasks "$project_root" "$EFFECTIVE_TASKS_DAYS" "false" "$INCLUDE_BLOCKED"
            purge_log_archives "$project_root" "$EFFECTIVE_LOGS_DAYS" "false"
            purge_shipped_bundles "$project_root" "$EFFECTIVE_BUNDLES_DAYS" "false"
            purge_closed_bugs "$project_root" "$EFFECTIVE_BUGS_DAYS" "false"
            purge_closed_priorities "$project_root" "$EFFECTIVE_PRIORITIES_DAYS" "false"
        } >> "$DRYRUN_LOG" 2>&1
    done
    unset project_root project_name

    CANDIDATE_COUNT="$(grep -c '^\[WOULD PURGE\]' "$DRYRUN_LOG" 2>/dev/null || echo 0)"
    plog ""
    plog "Candidates found: ${CANDIDATE_COUNT}"

    if [[ "$CANDIDATE_COUNT" -eq 0 ]]; then
        plog "No candidates to archive — nothing to do."
        rm -f "$DRYRUN_LOG"
        plog_summary "Summary:"
        plog_summary "  TOTAL: 0 items, estimated 0 MB"
        plog_summary "  Archive: (none; no candidates found)"
        plog_summary "  Log: ${PURGE_LOG}"
        exit 0
    fi

    # Stage: copy candidates into temp dir.
    ARCHIVE_STAGE_DIR="$(mktemp -d "${ARCHIVE_DIR}/purge-stage-XXXXXX")"
    plog "Staging ${CANDIDATE_COUNT} candidate(s) to: ${ARCHIVE_STAGE_DIR}"

    if ! _archive_stage_candidates "$DRYRUN_LOG" "$ARCHIVE_STAGE_DIR"; then
        plog "ERROR: staging failed for one or more items. Aborting purge." >&2
        rm -f "$DRYRUN_LOG"
        rm -rf "$ARCHIVE_STAGE_DIR"
        exit 1
    fi
    rm -f "$DRYRUN_LOG"

    # Tar: create the archive from the staging dir.
    # Paths inside the tarball are relative to the staging root, which mirrors
    # the relative structure from KANBAN_ROOT.  On extract, paths restore as:
    #   projects/<name>/tasks/<task-id>/...
    #   projects/<name>/bugs/BUG-*.md
    #   etc.
    plog "Creating archive: ${ARCHIVE_TARBALL}"
    if ! tar -czf "$ARCHIVE_TARBALL" -C "$ARCHIVE_STAGE_DIR" . 2>>"$PURGE_LOG"; then
        plog "ERROR: tar failed — aborting purge. No files have been deleted." >&2
        plog "  Staging dir left for inspection: ${ARCHIVE_STAGE_DIR}" >&2
        exit 1
    fi
    plog "Archive created: ${ARCHIVE_TARBALL}"
    rm -rf "$ARCHIVE_STAGE_DIR"
    plog ""

    # Pass 2: actual deletion (archive is guaranteed to exist at this point).
    plog "Applying deletions (pass 2)..."
    plog ""

    for project_root in "${PROJECT_ROOTS[@]}"; do
        project_name="$(basename "$project_root")"
        plog "Project: ${project_name}"
        _run_project_helpers "$project_root" "true"
        _run_empty_dir_cleanup "$project_root" "true" "${NONEMPTY_SNAPSHOTS["$project_name"]}"
    done
    unset project_root project_name

else
    # ---- Plain apply: delete without archive ----
    for project_root in "${PROJECT_ROOTS[@]}"; do
        project_name="$(basename "$project_root")"
        plog "Project: ${project_name}"
        _run_project_helpers "$project_root" "true"
        _run_empty_dir_cleanup "$project_root" "true" "${NONEMPTY_SNAPSHOTS["$project_name"]}"
    done
    unset project_root project_name
fi

unset _HELPER_COUNT _HELPER_BYTES

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
plog_summary "Summary:"
for project_name in "${!PROJECT_COUNTS[@]}"; do
    _count="${PROJECT_COUNTS[$project_name]}"
    _bytes="${PROJECT_BYTES[$project_name]}"
    _mb=$(( _bytes / 1048576 ))
    plog_summary "  Project ${project_name}: ${_count} items, estimated ${_mb} MB"
done
unset _count _bytes _mb project_name

_total_mb=$(( TOTAL_BYTES / 1048576 ))
plog_summary "  TOTAL: ${TOTAL_COUNT} items, estimated ${_total_mb} MB"
unset _total_mb

if [[ "$APPLY" == "false" ]]; then
    plog_summary ""
    if [[ "$ARCHIVE" == "true" ]]; then
        # Print the planned archive path so the operator knows where the tarball
        # would be created (acceptance criterion: dry-run + archive shows planned path).
        plog_summary "  Planned archive: ${ARCHIVE_TARBALL} (not created; dry-run only)"
        plog_summary ""
    fi
    plog_summary "To delete: rerun with --apply"
    plog_summary "To archive first: rerun with --archive --apply"
fi

# Archive status line: only shown in apply mode.
if [[ "$APPLY" == "true" ]]; then
    if [[ "$ARCHIVE" == "true" ]] && [[ -f "$ARCHIVE_TARBALL" ]]; then
        plog_summary "  Archive: ${ARCHIVE_TARBALL}"
    else
        plog_summary "  Archive: (none; pass --archive to preserve)"
    fi
fi

plog_summary "  Log: ${PURGE_LOG}"

exit 0
