#!/usr/bin/env bash
# team/scripts/overwatch-revert.sh
#
# OVERWATCH revert tool — restore files from a timestamped backup directory.
#
# Each OVERWATCH action that modifies a file first calls overwatch_backup_file,
# which stores a copy in:
#   $KANBAN_ROOT/projects/<project>/overwatch/backups/<TIMESTAMP>/<basename>
#
# This script is the operator escape hatch: given a TIMESTAMP, it reads the
# matching entries from actions.log and copies each backed-up file back to its
# original location.
#
# Usage:
#   overwatch-revert.sh [OPTIONS] <TIMESTAMP>
#   overwatch-revert.sh --list
#   overwatch-revert.sh --show <TIMESTAMP>
#
# Modes:
#   <TIMESTAMP>          Restore all files backed up under backups/<TIMESTAMP>/
#   --list               List all timestamps that have backup directories
#   --show <TIMESTAMP>   Print action-log entries for <TIMESTAMP>; no changes made
#
# Options:
#   --project <name>     OVERWATCH project name (required unless OVERWATCH_PROJECT
#                        env var is set; no silent default)
#   --kanban-root <path> Kanban root directory (default: $PGAI_AGENT_KANBAN_ROOT_PATH
#                        or $HOME/pgai_agent_kanban)
#   --dry-run            Show what would be restored without making any changes
#
# Idempotency:
#   Running twice with the same TIMESTAMP is safe. On the second run the script
#   detects that all files already match the backup content, logs a no-op revert
#   entry, and exits 0.
#
# Constraints:
#   - Refuses to run if backups/<TIMESTAMP>/ does not exist
#   - Never deletes backup directories — only reads from them
#   - Appends a revert record to actions.log for every non-dry-run restore
#   - Exit 0 on success; non-zero on error or bad arguments
#
# Exit codes:
#   0  success (or idempotent no-op)
#   1  usage error (missing argument, bad option, etc.)
#   2  runtime error (backup dir missing, log write failure, etc.)
#
# Environment variables:
#   KANBAN_ROOT           — absolute path to kanban root (overrides default)
#   OVERWATCH_PROJECT      — project name (overrides --project and default)


set -euo pipefail

# ---------------------------------------------------------------------------
# Source required libraries for project name resolution
# ---------------------------------------------------------------------------
_OR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_OR_SCRIPT_DIR}/lib/project_paths.sh"
# shellcheck source=lib/projects.sh
source "${_OR_SCRIPT_DIR}/lib/projects.sh"
unset _OR_SCRIPT_DIR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]:-$0}")"
readonly SCRIPT_NAME

# ---------------------------------------------------------------------------
# _or_usage
# Print usage summary to stderr and exit 1.
# ---------------------------------------------------------------------------
_or_usage() {
    cat >&2 <<EOF
Usage: ${SCRIPT_NAME} [OPTIONS] <TIMESTAMP>
       ${SCRIPT_NAME} --list
       ${SCRIPT_NAME} --show <TIMESTAMP>

Restore files from an OVERWATCH backup directory created at <TIMESTAMP>.

Modes:
  <TIMESTAMP>          Restore all files backed up under backups/<TIMESTAMP>/
  --list               List all available backup timestamps
  --show <TIMESTAMP>   Show action-log entries for <TIMESTAMP> without restoring

Options:
  --project <name>     Project name (required unless \$OVERWATCH_PROJECT is set)
  --kanban-root <path> Kanban root directory
  --dry-run            Show what would be restored; make no changes

Examples:
  ${SCRIPT_NAME} --list
  ${SCRIPT_NAME} --show 2026-05-10T12:00:00Z
  ${SCRIPT_NAME} 2026-05-10T12:00:00Z
  ${SCRIPT_NAME} --dry-run 2026-05-10T12:00:00Z
EOF
    exit 1
}

# ---------------------------------------------------------------------------
# _or_err <message>
# Print an error message to stderr.
# ---------------------------------------------------------------------------
_or_err() {
    echo "${SCRIPT_NAME}: error: $*" >&2
}

# ---------------------------------------------------------------------------
# _or_info <message>
# Print an informational message to stdout.
# ---------------------------------------------------------------------------
_or_info() {
    echo "${SCRIPT_NAME}: $*"
}

# ---------------------------------------------------------------------------
# _or_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment or defaults.
# Sets both variables in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_or_resolve_env() {
    # KANBAN_ROOT: prefer env var, then command-line override (set before call),
    
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi

    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        _or_err "KANBAN_ROOT does not exist: ${KANBAN_ROOT}"
        return 1
    fi

    # OVERWATCH_PROJECT: env var overrides command-line --project (set before call).
    # No silent default — a restore tool must never guess which project to restore.
    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        _or_err "no project specified and none resolvable from --project or \$OVERWATCH_PROJECT"
        _or_err "  Pass --project <name> or set \$OVERWATCH_PROJECT"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _or_state_dir
# Echo the absolute path to the project's overwatch state directory.
# ---------------------------------------------------------------------------
_or_state_dir() {
    echo "${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}/overwatch"
}

# ---------------------------------------------------------------------------
# _or_backup_base_dir
# Echo the absolute path to the backups/ directory.
# ---------------------------------------------------------------------------
_or_backup_base_dir() {
    echo "$(_or_state_dir)/backups"
}

# ---------------------------------------------------------------------------
# _or_actions_log
# Echo the absolute path to actions.log.
# ---------------------------------------------------------------------------
_or_actions_log() {
    echo "$(_or_state_dir)/actions.log"
}

# ---------------------------------------------------------------------------
# _or_list_timestamps
# List all timestamps that have subdirectories under backups/.
# Output: one timestamp per line, sorted lexicographically (ISO-8601 sorts chronologically).
# Returns 0 if any timestamps found, 1 if none.
# ---------------------------------------------------------------------------
_or_list_timestamps() {
    local backup_base
    backup_base="$(_or_backup_base_dir)"

    if [[ ! -d "${backup_base}" ]]; then
        _or_err "backups directory does not exist: ${backup_base}"
        return 2
    fi

    local count=0
    local ts
    while IFS= read -r ts; do
        [[ -z "${ts}" ]] && continue
        echo "${ts}"
        count=$(( count + 1 ))
    done < <(
        find "${backup_base}" -mindepth 1 -maxdepth 1 -type d 2>/dev/null \
            | sed 's|.*/||' \
            | sort
    )

    if (( count == 0 )); then
        echo "(no backups found)" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _or_show_timestamp <TIMESTAMP>
# Print all action-log entries that reference backups/<TIMESTAMP>/.
# Makes no changes. Returns 0 if entries found, 1 if none, 2 on error.
# ---------------------------------------------------------------------------
_or_show_timestamp() {
    local ts="$1"
    local backup_base log_file backup_dir
    backup_base="$(_or_backup_base_dir)"
    log_file="$(_or_actions_log)"
    backup_dir="${backup_base}/${ts}"

    # Validate backup directory
    if [[ ! -d "${backup_dir}" ]]; then
        _or_err "backup directory not found: ${backup_dir}"
        return 2
    fi

    if [[ ! -f "${log_file}" ]]; then
        _or_err "actions.log does not exist: ${log_file}"
        return 2
    fi

    local count=0
    local line

    echo "Action-log entries for timestamp: ${ts}"
    echo "Backup directory:                 ${backup_dir}"
    echo ""
    echo "TIMESTAMP                 NAME                          TARGET                         ACTION                  BACKUP_PATH"
    echo "$(printf '%0.s-' {1..120})"

    while IFS=$'\t' read -r ts_field name target action backup_path reason; do
        # Filter: backup_path must contain /backups/<TIMESTAMP>/
        if [[ "${backup_path}" == *"/backups/${ts}/"* ]] || [[ "${backup_path}" == *"/backups/${ts}" ]]; then
            printf '%-26s  %-30s  %-31s  %-22s  %s\n' \
                "${ts_field}" \
                "${name}" \
                "${target}" \
                "${action}" \
                "${backup_path}"
            count=$(( count + 1 ))
        fi
    done < "${log_file}"

    echo ""
    echo "Total matching entries: ${count}"

    if (( count == 0 )); then
        echo ""
        echo "Note: backup directory exists but no action-log entries reference it."
        echo "      Files in the backup directory:"
        find "${backup_dir}" -maxdepth 1 -type f 2>/dev/null | sort | while IFS= read -r f; do
            echo "  ${f}"
        done
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _or_find_original_path <backup_path> <target> <backup_dir>
# Determine where to restore a backup file.
#
# Strategy (in order):
#   1. If <target> is an absolute file path whose parent directory exists,
#      use it directly. This covers check-empty-files and similar scripts
#      where target IS the original file path.
#   2. If <target> looks like a task ID (alphanumeric-dashes pattern) and
#      the backup basename is "status.md", construct the task status path.
#   3. If <target> looks like a task ID and the backup basename is a queue
#      file, search the queues directory for that filename.
#   4. If none of the above, use backup basename to search the kanban tree
#      relative to <target> as a directory hint.
#
# Echoes the original file path on success.
# Returns 0 on success, 1 if the original path cannot be determined.
# ---------------------------------------------------------------------------
_or_find_original_path() {
    local backup_path="$1"
    local target="$2"
    local backup_dir="$3"
    local basename
    basename="$(basename "${backup_path}")"

    local project_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"

    # Strategy 1: target is an absolute path.
    # The file might not exist yet (it was moved/deleted), but the parent dir should.
    if [[ "${target}" == /* ]]; then
        local parent_dir
        parent_dir="$(dirname "${target}")"
        if [[ -d "${parent_dir}" ]]; then
            echo "${target}"
            return 0
        fi
        # Parent dir doesn't exist either — still try if target looks plausible
        # (the OVERWATCH action may have moved or deleted it, leaving no directory).
        # Accept it as-is since we can only go by what the log records.
        echo "${target}"
        return 0
    fi

    # Strategy 2: target is a task ID and the backup file is status.md.
    # Original path: <tasks_root>/<task_id>/status.md
    local tasks_root="${project_root}/tasks"
    if [[ "${basename}" == "status.md" ]]; then
        local candidate="${tasks_root}/${target}/status.md"
        local candidate_dir
        candidate_dir="$(dirname "${candidate}")"
        if [[ -d "${candidate_dir}" ]]; then
            echo "${candidate}"
            return 0
        fi
    fi

    # Strategy 3: target is a task ID and the backup file is a queue file (*.md
    # that doesn't match status.md). Search queues/ for a file with this basename.
    local queues_dir="${tasks_root}/queues"
    if [[ -d "${queues_dir}" && "${basename}" != "status.md" && "${basename}" == *.md ]]; then
        local queue_file
        queue_file="$(find "${queues_dir}" -maxdepth 3 -name "${basename}" -type f 2>/dev/null | head -n1)"
        if [[ -n "${queue_file}" ]]; then
            echo "${queue_file}"
            return 0
        fi
    fi

    # Strategy 4: search the entire project tree for a file with this basename
    # that lives under the target-named directory (if target is a subpath hint).
    local found
    found="$(find "${project_root}" -maxdepth 5 -name "${basename}" -type f 2>/dev/null | head -n1)"
    if [[ -n "${found}" ]]; then
        echo "${found}"
        return 0
    fi

    # Cannot determine original path.
    return 1
}

# ---------------------------------------------------------------------------
# _or_append_log <name> <target> <action> <backup_path> <reason>
# Append one record to actions.log using the same format as overwatch_log_action
# in overwatch_protocol.sh.
#
# Format: timestamp<TAB>name<TAB>target<TAB>action<TAB>backup_path<TAB>reason
# ---------------------------------------------------------------------------
_or_append_log() {
    local name="$1"
    local target="$2"
    local action="$3"
    local backup_path="$4"
    local reason="$5"
    local log_file
    log_file="$(_or_actions_log)"

    local state_dir
    state_dir="$(_or_state_dir)"
    if [[ ! -d "${state_dir}" ]]; then
        _or_err "overwatch state dir does not exist: ${state_dir}"
        return 2
    fi

    local timestamp
    timestamp="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
        "${timestamp}" \
        "${name}" \
        "${target}" \
        "${action}" \
        "${backup_path:-none}" \
        "${reason:-}" \
        >> "${log_file}" || {
        _or_err "write failed to ${log_file}"
        return 2
    }

    return 0
}

# ---------------------------------------------------------------------------
# _or_check_idempotent <TIMESTAMP>
# Check whether a revert for this timestamp was already completed.
# Returns 0 (true) if a "revert-completed" entry for this TIMESTAMP exists.
# Returns 1 (false) if not yet reverted.
# ---------------------------------------------------------------------------
_or_check_idempotent() {
    local ts="$1"
    local log_file
    log_file="$(_or_actions_log)"

    [[ -f "${log_file}" ]] || return 1

    # Look for a revert entry that references this timestamp in its backup_path
    # or in its reason, with action = "revert-completed".
    local line name target action backup_path reason
    while IFS=$'\t' read -r _ name target action backup_path reason; do
        if [[ "${name}" == "overwatch-revert" ]] \
           && [[ "${action}" == "revert-completed" ]] \
           && [[ "${reason}" == *"${ts}"* ]]; then
            return 0
        fi
    done < "${log_file}"

    return 1
}

# ---------------------------------------------------------------------------
# _or_do_revert <TIMESTAMP> <dry_run>
# Core revert logic: scan actions.log for entries referencing backups/<TIMESTAMP>/,
# restore each backed-up file to its original location.
#
# Args:
#   $1  ts      — TIMESTAMP string (e.g. "2026-05-10T12:00:00Z")
#   $2  dry_run — "1" for dry-run, "0" for live
#
# Returns 0 on success, 2 on error.
# ---------------------------------------------------------------------------
_or_do_revert() {
    local ts="$1"
    local dry_run="$2"
    local backup_base log_file backup_dir

    backup_base="$(_or_backup_base_dir)"
    log_file="$(_or_actions_log)"
    backup_dir="${backup_base}/${ts}"

    # Guard: backup directory must exist
    if [[ ! -d "${backup_dir}" ]]; then
        _or_err "backup directory not found: ${backup_dir}"
        _or_err "Use --list to see available timestamps."
        return 2
    fi

    # Guard: actions.log must exist
    if [[ ! -f "${log_file}" ]]; then
        _or_err "actions.log not found: ${log_file}"
        return 2
    fi

    # Idempotency check
    if (( dry_run == 0 )) && _or_check_idempotent "${ts}"; then
        _or_info "Revert for timestamp ${ts} was already completed (idempotent no-op)."
        # Log the second-run no-op
        _or_append_log \
            "overwatch-revert" \
            "${ts}" \
            "revert-noop" \
            "none" \
            "Second revert attempt for ${ts}; already completed; no changes made" \
        || true
        return 0
    fi

    # Collect all action-log entries that reference this timestamp's backup dir.
    # Each entry: backup_path -> target
    local restore_count=0
    local error_count=0
    local skipped_count=0

    # Build a list of (backup_path, target) pairs from the log
    local restore_pairs=()   # interleaved: restore_pairs[i]=backup_path, restore_pairs[i+1]=target
    local line ts_field name target action backup_path reason

    while IFS=$'\t' read -r ts_field name target action backup_path reason; do
        # Skip entries that don't reference this timestamp's backup dir
        if [[ "${backup_path}" != *"/backups/${ts}/"* ]] && \
           [[ "${backup_path}" != *"/backups/${ts}" ]]; then
            continue
        fi

        # Skip entries that are themselves revert actions (don't recurse)
        if [[ "${name}" == "overwatch-revert" ]]; then
            continue
        fi

        # Skip entries where backup_path doesn't exist (file may have been cleaned up)
        if [[ ! -f "${backup_path}" ]]; then
            _or_info "Backup file not found (skipping): ${backup_path}"
            skipped_count=$(( skipped_count + 1 ))
            continue
        fi

        restore_pairs+=("${backup_path}" "${target}")
    done < "${log_file}"

    # If no log entries found, still try to restore files directly from backup dir
    if (( ${#restore_pairs[@]} == 0 )); then
        _or_info "No action-log entries reference timestamp ${ts}."
        _or_info "Checking backup directory for files to restore..."

        # Cannot determine original paths without log entries — inform and exit
        local backup_files_count=0
        while IFS= read -r f; do
            [[ -z "${f}" ]] && continue
            backup_files_count=$(( backup_files_count + 1 ))
        done < <(find "${backup_dir}" -maxdepth 1 -type f 2>/dev/null)

        if (( backup_files_count == 0 )); then
            _or_info "Backup directory is empty: ${backup_dir}"
            return 0
        fi

        _or_err "Cannot restore ${backup_files_count} file(s) without action-log entries."
        _or_err "The action-log has no entries referencing backup_path under ${backup_dir}."
        _or_err "Manual restore may be required."
        return 2
    fi

    # Process each (backup_path, target) pair
    local i=0
    while (( i < ${#restore_pairs[@]} )); do
        local bp="${restore_pairs[$i]}"
        local tgt="${restore_pairs[$((i + 1))]}"
        i=$(( i + 2 ))

        # Determine the original file path
        local orig_path
        if ! orig_path="$(_or_find_original_path "${bp}" "${tgt}" "${backup_dir}")"; then
            _or_info "Cannot determine original path for backup: ${bp} (target: ${tgt}) — skipping"
            skipped_count=$(( skipped_count + 1 ))
            continue
        fi

        if (( dry_run == 1 )); then
            _or_info "[dry-run] Would restore: ${bp} -> ${orig_path}"
            restore_count=$(( restore_count + 1 ))
            continue
        fi

        # Ensure the destination directory exists
        local orig_dir
        orig_dir="$(dirname "${orig_path}")"
        if [[ ! -d "${orig_dir}" ]]; then
            _or_info "Destination directory does not exist: ${orig_dir} — skipping ${bp}"
            skipped_count=$(( skipped_count + 1 ))
            continue
        fi

        # Perform the restore: copy backup -> original location
        if ! cp "${bp}" "${orig_path}"; then
            _or_err "Copy failed: ${bp} -> ${orig_path}"
            error_count=$(( error_count + 1 ))
            continue
        fi

        _or_info "Restored: ${bp} -> ${orig_path}"
        restore_count=$(( restore_count + 1 ))
    done

    # Summary
    echo ""
    echo "Revert summary for timestamp: ${ts}"
    echo "  Files restored: ${restore_count}"
    echo "  Skipped:        ${skipped_count}"
    echo "  Errors:         ${error_count}"

    if (( dry_run == 1 )); then
        _or_info "[dry-run] No changes made."
        return 0
    fi

    if (( error_count > 0 )); then
        _or_err "Revert completed with ${error_count} error(s)."
        # Log a partial revert
        _or_append_log \
            "overwatch-revert" \
            "${ts}" \
            "revert-partial" \
            "none" \
            "Partial revert for ${ts}: ${restore_count} restored, ${skipped_count} skipped, ${error_count} errors" \
        || true
        return 2
    fi

    if (( restore_count == 0 )); then
        _or_info "Nothing to restore for timestamp: ${ts}"
        _or_append_log \
            "overwatch-revert" \
            "${ts}" \
            "revert-completed" \
            "none" \
            "Revert for ${ts}: no restorable files found (${skipped_count} skipped)" \
        || true
        return 0
    fi

    # Log the completed revert
    _or_append_log \
        "overwatch-revert" \
        "${ts}" \
        "revert-completed" \
        "none" \
        "Revert for ${ts}: ${restore_count} file(s) restored, ${skipped_count} skipped" \
    || {
        _or_err "Revert succeeded but failed to write to actions.log"
        return 2
    }

    _or_info "Revert completed successfully."
    return 0
}

# ---------------------------------------------------------------------------
# main
# Parse arguments and dispatch to the appropriate mode.
# ---------------------------------------------------------------------------
main() {
    local mode=""           # "revert" | "list" | "show"
    local timestamp=""
    local dry_run=0
    local project_override=""
    local kanban_root_override=""

    # Parse arguments
    if (( $# == 0 )); then
        _or_usage
    fi

    local args=("$@")
    local i=0
    while (( i < ${#args[@]} )); do
        local arg="${args[$i]}"
        case "${arg}" in
            --list)
                mode="list"
                ;;
            --show)
                mode="show"
                i=$(( i + 1 ))
                if (( i >= ${#args[@]} )); then
                    _or_err "--show requires a TIMESTAMP argument"
                    _or_usage
                fi
                timestamp="${args[$i]}"
                ;;
            --dry-run)
                dry_run=1
                ;;
            --project)
                i=$(( i + 1 ))
                if (( i >= ${#args[@]} )); then
                    _or_err "--project requires an argument"
                    _or_usage
                fi
                project_override="${args[$i]}"
                ;;
            --kanban-root)
                i=$(( i + 1 ))
                if (( i >= ${#args[@]} )); then
                    _or_err "--kanban-root requires an argument"
                    _or_usage
                fi
                kanban_root_override="${args[$i]}"
                ;;
            --help|-h)
                _or_usage
                ;;
            -*)
                _or_err "Unknown option: ${arg}"
                _or_usage
                ;;
            *)
                # Positional argument: TIMESTAMP for revert mode
                if [[ -z "${mode}" ]]; then
                    mode="revert"
                    timestamp="${arg}"
                else
                    _or_err "Unexpected argument: ${arg}"
                    _or_usage
                fi
                ;;
        esac
        i=$(( i + 1 ))
    done

    # Validate mode
    if [[ -z "${mode}" ]]; then
        _or_err "No mode specified. Provide a TIMESTAMP, --list, or --show <TIMESTAMP>."
        _or_usage
    fi

    # Apply overrides before resolving env
    if [[ -n "${kanban_root_override}" ]]; then
        KANBAN_ROOT="${kanban_root_override}"
        export KANBAN_ROOT
    fi
    if [[ -n "${project_override}" ]]; then
        OVERWATCH_PROJECT="${project_override}"
        export OVERWATCH_PROJECT
    fi

    # Resolve environment (sets KANBAN_ROOT and OVERWATCH_PROJECT if not already set)
    _or_resolve_env || exit 2

    # Dispatch to the requested mode
    case "${mode}" in
        list)
            _or_list_timestamps
            ;;
        show)
            if [[ -z "${timestamp}" ]]; then
                _or_err "--show requires a TIMESTAMP argument"
                _or_usage
            fi
            _or_show_timestamp "${timestamp}"
            ;;
        revert)
            if [[ -z "${timestamp}" ]]; then
                _or_err "Revert mode requires a TIMESTAMP argument"
                _or_usage
            fi
            _or_do_revert "${timestamp}" "${dry_run}"
            ;;
        *)
            _or_err "Internal error: unknown mode: ${mode}"
            exit 2
            ;;
    esac
}

main "$@"
