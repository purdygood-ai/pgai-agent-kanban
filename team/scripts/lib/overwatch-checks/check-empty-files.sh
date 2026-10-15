#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-empty-files.sh
#
# OVERWATCH detection module: scan bugs/ and priority/ under the kanban project
# for 0-byte REPORT files, rename each to <name>.empty.orphan, log the action,
# and file a brief bug entry. The scan is scoped to report files only — any
# dotfile (a filename starting with ".") is protocol machinery (claim-lock
# sentinels, claim ledgers, and similar in-flight state) rather than a report,
# and is left untouched regardless of size. The module also runs a bounded
# prune of claim-lock-shaped residue (".claim-NNNN.lock.empty.orphan") that
# earlier, unscoped firings of this check left behind — each was a permanently
# consumed claim slot with no report content to preserve as evidence, so the
# prune removes it. Every other ".empty.orphan" file is left in place as
# evidence, pruned or not.
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_empty_files <project_name> [--dry-run]
#   - Directly invokable: bash check-empty-files.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT      — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT must be set explicitly when invoking directly; the script
# exits non-zero with an error naming OVERWATCH_PROJECT if it is unset.
#
# Usage:
#   bash check-empty-files.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no empty report files, or renamed/pruned
#       without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT rename, prune, or create
# bug entries.

# ---------------------------------------------------------------------------
# _cef_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment when not set.
# Sets both variables in the calling scope.
# Returns 0 on success, 1 if resolution fails.
# ---------------------------------------------------------------------------
_cef_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-empty-files: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        echo "check-empty-files: ERROR: OVERWATCH_PROJECT is not set" >&2
        echo "  Set OVERWATCH_PROJECT to the target project name before invoking directly." >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _cef_ensure_state_dir <project_name>
# Verify the overwatch state dir and backups subdir exist.
# Returns 0 on success, 1 if missing.
# ---------------------------------------------------------------------------
_cef_ensure_state_dir() {
    local project_name="$1"
    local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
    local backup_dir="${state_dir}/backups"

    if [[ ! -d "${state_dir}" ]]; then
        echo "check-empty-files: overwatch state dir missing: ${state_dir}" >&2
        return 1
    fi
    if [[ ! -d "${backup_dir}" ]]; then
        echo "check-empty-files: overwatch backups dir missing: ${backup_dir}" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _cef_is_report_candidate <path>
# True (0) when <path>'s basename is eligible to be treated as a report file
# by this check; false (1) for anything else.
#
# The only exclusion is dotfiles: a filename starting with "." is protocol
# machinery, never a bug or priority report. This covers the claim-lock
# sentinel shape owned by team/pm-agent/lib/bug_scanner.py
# (".claim-NNNN.lock", matched there by _CLAIM_LOCK_RE) and its durable
# claim ledger (".bug-claims.log", _CLAIM_LOG_FILENAME) without restating
# either pattern here — both are dotfiles, so the general exclusion covers
# them, and covers any future dotfile-shaped protocol state the same way.
# ---------------------------------------------------------------------------
_cef_is_report_candidate() {
    local bn
    bn="$(basename "$1")"
    [[ "${bn}" == .* ]] && return 1
    return 0
}

# ---------------------------------------------------------------------------
# _cef_is_claim_lock_residue <path>
# True (0) when <path>'s basename is a claim-lock sentinel already quarantined
# by a prior, unscoped firing of this check — i.e. matches the claim-lock
# shape from team/pm-agent/lib/bug_scanner.py's _CLAIM_LOCK_RE
# (".claim-NNNN.lock", 4+ digits) suffixed with the ".empty.orphan" this
# check appends. False (1) for every other filename, including every other
# ".empty.orphan" file — those remain in place as evidence.
# ---------------------------------------------------------------------------
_cef_is_claim_lock_residue() {
    local bn
    bn="$(basename "$1")"
    [[ "${bn}" =~ ^\.claim-[0-9]{4,}\.lock\.empty\.orphan$ ]]
}

# ---------------------------------------------------------------------------
# _cef_next_bug_number <bugs_dir>
# Determine the next bug report sequence number by scanning existing BUG-NNNN
# files in <bugs_dir>. Echoes a zero-padded 4-digit number.
# ---------------------------------------------------------------------------
_cef_next_bug_number() {
    local bugs_dir="$1"
    local highest=0
    local num

    if [[ -d "${bugs_dir}" ]]; then
        while IFS= read -r f; do
            # Extract the 4-digit sequence from BUG-NNNN-... filenames
            num="$(basename "${f}" | grep -oE '^BUG-[0-9]+' | grep -oE '[0-9]+' | head -n1)"
            if [[ -n "${num}" ]] && (( 10#${num} > highest )); then
                highest=$(( 10#${num} ))
            fi
        done < <(find "${bugs_dir}" -maxdepth 1 -name 'BUG-[0-9]*' -type f 2>/dev/null)
    fi

    printf '%04d' $(( highest + 1 ))
}

# ---------------------------------------------------------------------------
# _cef_do_rename
# Inner function invoked via overwatch_halt_first_fix for one file.
# Reads _CEF_TARGET_FILE and _CEF_DRY_RUN from the environment.
#
# Actions:
#   1. Backup the source file via overwatch_backup_file
#   2. Rename to <name>.empty.orphan
#   3. Log the action via overwatch_log_action
# ---------------------------------------------------------------------------
_cef_do_rename() {
    local src="${_CEF_TARGET_FILE}"
    local dst="${src}.empty.orphan"
    local backup_path="none"

    # Backup (even though the file is empty; satisfies the backup-before-modify contract)
    local bpath
    bpath="$(overwatch_backup_file "${src}")" || {
        echo "check-empty-files: backup failed for ${src}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Rename
    if ! mv "${src}" "${dst}" 2>/dev/null; then
        echo "check-empty-files: rename failed: ${src} -> ${dst}" >&2
        return 1
    fi

    # Log
    overwatch_log_action \
        "check-empty-files" \
        "${src}" \
        "renamed-to-empty-orphan" \
        "${backup_path}" \
        "0-byte file detected; renamed to ${dst}" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _cef_do_prune
# Inner function invoked via overwatch_halt_first_fix for one claim-lock
# residue file (a ".claim-NNNN.lock.empty.orphan" left by a prior, unscoped
# firing of this check). Reads _CEF_TARGET_FILE from the environment.
#
# Actions:
#   1. Backup the source file via overwatch_backup_file (same
#      backup-before-modify contract as _cef_do_rename)
#   2. Delete the file
#   3. Log the action via overwatch_log_action
# ---------------------------------------------------------------------------
_cef_do_prune() {
    local src="${_CEF_TARGET_FILE}"
    local backup_path="none"

    local bpath
    bpath="$(overwatch_backup_file "${src}")" || {
        echo "check-empty-files: backup failed for ${src}" >&2
        return 1
    }
    backup_path="${bpath}"

    if ! rm -f "${src}" 2>/dev/null; then
        echo "check-empty-files: prune failed: ${src}" >&2
        return 1
    fi

    overwatch_log_action \
        "check-empty-files" \
        "${src}" \
        "pruned-claim-lock-residue" \
        "${backup_path}" \
        "stale claim-lock sentinel quarantined by a prior firing; permanently consumed claim slot, no report content to preserve" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# _cef_file_bug <bugs_dir> <orphan_list>
# Create a brief bug entry listing the orphaned files.
# <orphan_list> is a newline-separated list of original file paths.
# ---------------------------------------------------------------------------
_cef_file_bug() {
    local bugs_dir="$1"
    local orphan_list="$2"

    if [[ -z "${orphan_list}" ]] || [[ ! -d "${bugs_dir}" ]]; then
        return 0
    fi

    local seq
    seq="$(_cef_next_bug_number "${bugs_dir}")"
    local slug="auto-empty-files-$(date +%Y%m%d)"
    local bug_file="${bugs_dir}/BUG-${seq}-${slug}.md"

    # Build the file list for the report
    local file_list=""
    while IFS= read -r f; do
        [[ -z "${f}" ]] && continue
        file_list="${file_list}- ${f}
"
    done <<< "${orphan_list}"

    cat > "${bug_file}" <<EOF
# BUG-${seq}: Empty files detected and quarantined

## Status
open

## Summary
OVERWATCH detected 0-byte files in bugs/ or priority/ and renamed them with
the .empty.orphan suffix. These files were likely created incomplete by a
failed write. They should be reviewed and either deleted or completed.

## Detected Files
${file_list}
## Detected At
$(date -u +%Y-%m-%dT%H:%M:%SZ)

## Detected By
check-empty-files (OVERWATCH)
EOF

    echo "check-empty-files: filed bug report at ${bug_file}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_empty_files [--dry-run]
# Main detection function. Scans bugs/ and priority/ for 0-byte report files
# (skipping dotfiles) and for claim-lock residue left by a prior, unscoped
# firing. When --dry-run is passed (or _CEF_DRY_RUN=1), skips all mutations.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_empty_files() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _cef_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-empty-files: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-empty-files: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-empty-files: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        _cef_ensure_state_dir "${project_name}" || return 1
    fi

    # Directories to scan
    local bugs_dir="${project_root}/bugs"
    local priority_dir="${project_root}/priority"

    local found_empty=()
    local residue=()
    local scan_dir

    for scan_dir in "${bugs_dir}" "${priority_dir}"; do
        [[ -d "${scan_dir}" ]] || continue

        local f
        while IFS= read -r f; do
            [[ -z "${f}" ]] && continue
            # Skip files already quarantined
            if [[ "${f}" == *.empty.orphan ]]; then
                continue
            fi
            # Scope to report files only: a dotfile is protocol machinery
            # (claim-lock sentinel, claim ledger, or similar in-flight
            # state), never a bug or priority report.
            _cef_is_report_candidate "${f}" || continue
            found_empty+=("${f}")
        done < <(find "${scan_dir}" -maxdepth 1 -type f -empty 2>/dev/null)

        # Bounded prune scope: claim-lock residue left by a prior, unscoped
        # firing of this check. Every other ".empty.orphan" file is left in
        # place as evidence — only the claim-lock shape is pruned.
        while IFS= read -r f; do
            [[ -z "${f}" ]] && continue
            _cef_is_claim_lock_residue "${f}" && residue+=("${f}")
        done < <(find "${scan_dir}" -maxdepth 1 -type f -name '.claim-*.lock.empty.orphan' 2>/dev/null)
    done

    if (( ${#found_empty[@]} == 0 && ${#residue[@]} == 0 )); then
        echo "check-empty-files: no 0-byte report files or claim-lock residue found in bugs/ or priority/" >&2
        return 0
    fi

    echo "check-empty-files: found ${#found_empty[@]} empty report file(s), ${#residue[@]} claim-lock residue file(s)" >&2

    if (( dry_run == 1 )); then
        local f
        for f in "${found_empty[@]}"; do
            echo "check-empty-files: [dry-run] would rename: ${f} -> ${f}.empty.orphan" >&2
            overwatch_log_action \
                "check-empty-files" \
                "${f}" \
                "dry-run-empty-file-detected" \
                "none" \
                "0-byte report file detected (dry-run; no action taken)" \
            2>/dev/null || true
        done
        for f in "${residue[@]}"; do
            echo "check-empty-files: [dry-run] would prune claim-lock residue: ${f}" >&2
            overwatch_log_action \
                "check-empty-files" \
                "${f}" \
                "dry-run-claim-lock-residue-detected" \
                "none" \
                "claim-lock residue from a prior firing (dry-run; no action taken)" \
            2>/dev/null || true
        done
        return 0
    fi

    # Live mode: rename each empty file via overwatch_halt_first_fix
    local orphan_list=""
    local f
    for f in "${found_empty[@]}"; do
        export _CEF_TARGET_FILE="${f}"
        export _CEF_DRY_RUN="${dry_run}"

        local fix_exit=0
        overwatch_halt_first_fix _cef_do_rename || fix_exit=$?

        if (( fix_exit == 3 )); then
            echo "check-empty-files: HALT_OVERWATCH guard tripped; aborting rename of ${f}" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-empty-files: per-repo flock contended; aborting rename of ${f}" >&2
            return 0
        elif (( fix_exit == 5 )); then
            echo "check-empty-files: fix deferred: HALT present; aborting rename of ${f}" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-empty-files: rename failed for ${f} (exit ${fix_exit})" >&2
            # Continue with remaining files
        else
            orphan_list="${orphan_list}${f}
"
        fi

        unset _CEF_TARGET_FILE _CEF_DRY_RUN
    done

    # File one consolidated bug entry for all orphaned files
    if [[ -n "${orphan_list}" ]]; then
        _cef_file_bug "${bugs_dir}" "${orphan_list}" || true
    fi

    # Live mode: prune claim-lock residue via overwatch_halt_first_fix.
    # Independent of found_empty/orphan_list above — this is cleanup of a
    # prior firing's over-eager renames, not a new detection, so it is never
    # folded into the bug report.
    for f in "${residue[@]}"; do
        export _CEF_TARGET_FILE="${f}"

        local prune_exit=0
        overwatch_halt_first_fix _cef_do_prune || prune_exit=$?

        if (( prune_exit == 3 )); then
            echo "check-empty-files: HALT_OVERWATCH guard tripped; aborting prune of ${f}" >&2
            return 0
        elif (( prune_exit == 4 )); then
            echo "check-empty-files: per-repo flock contended; aborting prune of ${f}" >&2
            return 0
        elif (( prune_exit == 5 )); then
            echo "check-empty-files: fix deferred: HALT present; aborting prune of ${f}" >&2
            return 0
        elif (( prune_exit != 0 )); then
            echo "check-empty-files: prune failed for ${f} (exit ${prune_exit})" >&2
            # Continue with remaining files
        fi

        unset _CEF_TARGET_FILE
    done

    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_empty_files "$@"
    exit $?
fi
