#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-tester-orphan-files.sh
#
# OVERWATCH detection module: find TESTER-authored priority files that were
# placed in priority/ but should have been filed as bug reports.
#
# Pattern detected: priority/<project>/<name>.md files whose filename matches
# the vX.Y.Z-*.md pattern. TESTER autonomous Path C sometimes places
# version-prefixed bug descriptions in priority/ instead of bugs/.
#
# Detection logic:
#   1. Scan $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/priority/ for files
#      matching the regex ^v[0-9]+\.[0-9]+\.[0-9]+-.*\.md$ (case insensitive).
#   2. For each matched file:
#      a. If the file contains BOTH a "## Symptom" section AND a
#         "## Root Cause" section (case-insensitive), it is "bug-shaped":
#         - Derive a slug from the first non-empty H1 heading (# ...) or
#           from the filename stem (strip leading vX.Y.Z- prefix, strip .md)
#         - Rename the original priority file to <name>.orphan
#         - File the original file's content as a bug through intake
#           (overwatch_deposit_bug), which claims its BUG-NNNN number; when
#           the deposit fails the rename is undone
#         - Log the action
#      b. If the file is NOT bug-shaped (missing Symptom or Root Cause):
#         - Rename to <name>.orphan
#         - File a bug report noting the suspicious file, through intake
#         - Log the action
#      A report intake refuses as the same defect as a live bug is not filed
#      again; the file is still quarantined and the live bug is logged.
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_tester_orphan_files [--dry-run]
#   - Directly invokable: bash check-tester-orphan-files.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT      — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to "pgai-agent-kanban".
#
# Usage:
#   bash check-tester-orphan-files.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no orphans found, or orphans handled without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT rename files or create bug entries.

# This file's directory, as an absolute path
# resolved once at load: no function depends on the caller's current
# directory or on whether this file was named by a relative path.
_CTOF_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || _CTOF_SELF_DIR=""

# overwatch_registered_projects: the shared projects.cfg reader;
# overwatch_deposit_bug: filing through intake.
# shellcheck source=../overwatch_config.sh
source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"

# ---------------------------------------------------------------------------
# _ctof_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ctof_resolve_env() {
    overwatch_resolve_check_project check-tester-orphan-files || return 1
    return 0
}

# ---------------------------------------------------------------------------
# _ctof_is_bug_shaped <file>
# Returns 0 if <file> contains both "## Symptom" and "## Root Cause" sections.
# Returns 1 otherwise.
# Case-insensitive match.
# ---------------------------------------------------------------------------
_ctof_is_bug_shaped() {
    local file="$1"
    local has_symptom=0
    local has_root_cause=0

    if [[ ! -f "${file}" ]]; then
        return 1
    fi

    if grep -qiE '^##[[:space:]]+Symptom[[:space:]]*$' "${file}" 2>/dev/null; then
        has_symptom=1
    fi
    if grep -qiE '^##[[:space:]]+Root Cause[[:space:]]*$' "${file}" 2>/dev/null; then
        has_root_cause=1
    fi

    if (( has_symptom == 1 && has_root_cause == 1 )); then
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# _ctof_derive_slug <file>
# Derive a slug for a bug report from:
#   1. The first non-empty H1 heading (# ...) in the file, OR
#   2. The filename stem with the leading vX.Y.Z- prefix removed.
#
# Slug is lowercased, spaces and special chars replaced with hyphens,
# compressed and trimmed. Max 60 chars.
# Echoes the derived slug.
# ---------------------------------------------------------------------------
_ctof_derive_slug() {
    local file="$1"
    local slug=""

    # Try to extract first non-empty H1 heading
    local h1
    h1="$(grep -m1 -E '^#[[:space:]]+' "${file}" 2>/dev/null \
        | sed 's/^#[[:space:]]*//' \
        | tr '[:upper:]' '[:lower:]' \
        | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//' \
        | head -c 60)"

    if [[ -n "${h1}" ]]; then
        slug="${h1}"
    else
        # Fall back to filename stem: strip extension and leading vX.Y.Z- prefix
        local stem
        stem="$(basename "${file}" .md \
            | sed 's/^v[0-9]\+\.[0-9]\+\.[0-9]\+-//' \
            | tr '[:upper:]' '[:lower:]' \
            | sed 's/[^a-z0-9]\+/-/g; s/^-//; s/-$//' \
            | head -c 60)"
        slug="${stem}"
    fi

    # Sanitize: ensure no double hyphens, no leading/trailing hyphens
    slug="$(echo "${slug}" | sed 's/--*/-/g; s/^-//; s/-$//')"

    # Fallback if empty
    if [[ -z "${slug}" ]]; then
        slug="tester-orphan-$(date +%Y%m%d)"
    fi

    echo "${slug}"
}

# ---------------------------------------------------------------------------
# _ctof_do_handle_bug_shaped
# Inner function invoked via overwatch_halt_first_fix for one bug-shaped file.
# Reads from environment:
#   _CTOF_SRC_FILE   — path to the priority/ file to handle
#   _CTOF_PROJECT    — the project name the bug is filed for
#   _CTOF_SLUG       — pre-derived slug
#
# Actions:
#   1. Rename source to <src>.orphan
#   2. File the source content as a bug through intake
#      (overwatch_deposit_bug); on a failed deposit, undo the rename
#   3. Log action via overwatch_log_action
# ---------------------------------------------------------------------------
_ctof_do_handle_bug_shaped() {
    local src="${_CTOF_SRC_FILE}"
    local project="${_CTOF_PROJECT}"
    local slug="${_CTOF_SLUG}"

    local orphan_path="${src}.orphan"

    # Backup the source before modifying
    local bpath
    bpath="$(overwatch_backup_file "${src}")" || {
        echo "check-tester-orphan-files: backup failed for ${src}" >&2
        return 1
    }

    # Read the original content for the bug report
    local orig_content
    orig_content="$(cat "${src}" 2>/dev/null)" || {
        echo "check-tester-orphan-files: cannot read source file: ${src}" >&2
        return 1
    }

    # Rename source to .orphan
    if ! mv "${src}" "${orphan_path}" 2>/dev/null; then
        echo "check-tester-orphan-files: rename failed: ${src} -> ${orphan_path}" >&2
        return 1
    fi

    # File the original content as a bug through intake
    local bug_file="" deposit_rc=0
    bug_file="$({
        printf '<!-- Migrated from priority/ by OVERWATCH check-tester-orphan-files -->\n'
        printf '<!-- Original path: %s -->\n' "${src}"
        printf '<!-- Migration date: %s -->\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '%s\n' "${orig_content}"
    } | overwatch_deposit_bug check-tester-orphan-files "${project}" "${slug}" "${slug}")" || deposit_rc=$?

    if (( deposit_rc == 2 )); then
        overwatch_log_action \
            "check-tester-orphan-files" \
            "${src}" \
            "bug-shaped-orphan-already-filed" \
            "${bpath}" \
            "Bug-shaped priority file reports the same defect as live bug ${bug_file}; not filed again; original renamed to ${orphan_path}" \
        || true
        echo "check-tester-orphan-files: ${src} already filed as ${bug_file} (orphan: ${orphan_path})" >&2
        return 0
    elif (( deposit_rc != 0 )); then
        echo "check-tester-orphan-files: bug deposit failed for ${src}; restoring it from ${orphan_path}" >&2
        mv "${orphan_path}" "${src}" 2>/dev/null \
            || echo "check-tester-orphan-files: restore failed; the original is at ${orphan_path}" >&2
        return 1
    fi

    # Log the action
    overwatch_log_action \
        "check-tester-orphan-files" \
        "${src}" \
        "bug-shaped-orphan-migrated" \
        "${bpath}" \
        "Bug-shaped priority file migrated to ${bug_file}; original renamed to ${orphan_path}" \
    || true

    echo "check-tester-orphan-files: migrated ${src} -> ${bug_file} (orphan: ${orphan_path})" >&2
    return 0
}

# ---------------------------------------------------------------------------
# _ctof_do_handle_not_bug_shaped
# Inner function invoked via overwatch_halt_first_fix for a non-bug-shaped file.
# Reads from environment:
#   _CTOF_SRC_FILE   — path to the priority/ file to handle
#   _CTOF_PROJECT    — the project name the bug is filed for
#
# Actions:
#   1. Rename source to <src>.orphan
#   2. File a bug report noting the suspicious file, through intake
#      (overwatch_deposit_bug)
#   3. Log action via overwatch_log_action
# ---------------------------------------------------------------------------
_ctof_do_handle_not_bug_shaped() {
    local src="${_CTOF_SRC_FILE}"
    local project="${_CTOF_PROJECT}"

    local orphan_path="${src}.orphan"
    local bug_slug="suspicious-priority-file-$(date +%Y%m%d)"

    # Backup source before any modification
    local bpath
    bpath="$(overwatch_backup_file "${src}")" || {
        echo "check-tester-orphan-files: backup failed for ${src}" >&2
        return 1
    }

    # Rename source to .orphan
    if ! mv "${src}" "${orphan_path}" 2>/dev/null; then
        echo "check-tester-orphan-files: rename failed: ${src} -> ${orphan_path}" >&2
        return 1
    fi

    # File a bug noting the suspicious file
    local bug_file="" deposit_rc=0 filed_note
    bug_file="$(overwatch_deposit_bug check-tester-orphan-files "${project}" "${bug_slug}" \
        "Suspicious vX.Y.Z-prefixed file found in priority/" <<EOF
## Status
open

## Symptom

OVERWATCH found a file in priority/ whose filename matches the vX.Y.Z-*.md
pattern but which does not contain both "## Symptom" and "## Root Cause"
sections that would make it a valid bug report. This file may have been
placed there by a TESTER autonomous run that wrote to the wrong directory.

## Root Cause

Unknown. The file does not conform to the bug report template. It may be
an incomplete bug report, a misrouted priority document, or a file that was
written incorrectly by TESTER Path C.

## Suspicious File

Original path: ${src}
Renamed to:    ${orphan_path}
Backup at:     ${bpath}
Detected at:   $(date -u +%Y-%m-%dT%H:%M:%SZ)
Detected by:   check-tester-orphan-files (OVERWATCH)

## Next Steps

Inspect ${orphan_path} and determine whether it should be:
  1. Completed as a bug report (add Symptom + Root Cause) and re-filed in bugs/
  2. Converted to a priority document (remove the vX.Y.Z- prefix, add Status)
  3. Deleted if it is spurious
EOF
)" || deposit_rc=$?
    case "${deposit_rc}" in
        0) filed_note="bug filed at ${bug_file}" ;;
        2) filed_note="already filed as ${bug_file}; not filed again" ;;
        *) filed_note="bug not filed (intake refused or failed)" ;;
    esac

    # Log the action
    overwatch_log_action \
        "check-tester-orphan-files" \
        "${src}" \
        "non-bug-shaped-orphan-quarantined" \
        "${bpath}" \
        "Non-bug-shaped vX.Y.Z-prefixed priority file quarantined to ${orphan_path}; ${filed_note}" \
    || true

    echo "check-tester-orphan-files: quarantined ${src} -> ${orphan_path}; ${filed_note}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_tester_orphan_files [--dry-run]
# Main detection function. Scans priority/ for vX.Y.Z-*.md files.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_tester_orphan_files() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _ctof_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local priority_dir="${project_root}/priority"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-tester-orphan-files: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="${_CTOF_SELF_DIR}"
        [[ -n "${lib_dir}" ]] || {
            echo "check-tester-orphan-files: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-tester-orphan-files: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-tester-orphan-files: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    if [[ ! -d "${priority_dir}" ]]; then
        echo "check-tester-orphan-files: priority dir does not exist: ${priority_dir}; nothing to scan" >&2
        return 0
    fi

    # Find all vX.Y.Z-*.md files in priority/
    local orphan_candidates=()
    local f
    while IFS= read -r f; do
        local fname
        fname="$(basename "${f}")"
        # Must match vX.Y.Z-... pattern
        if [[ "${fname}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+-.*\.md$ ]]; then
            # Skip already-orphaned files
            if [[ "${fname}" == *.orphan ]]; then
                continue
            fi
            orphan_candidates+=("${f}")
        fi
    done < <(find "${priority_dir}" -maxdepth 1 -type f -name 'v*.md' 2>/dev/null)

    if (( ${#orphan_candidates[@]} == 0 )); then
        echo "check-tester-orphan-files: no vX.Y.Z-*.md orphan candidates found in priority/" >&2
        return 0
    fi

    echo "check-tester-orphan-files: found ${#orphan_candidates[@]} orphan candidate(s) in priority/" >&2

    local handled=0

    for f in "${orphan_candidates[@]}"; do
        echo "check-tester-orphan-files: examining ${f}" >&2

        local bug_shaped=0
        if _ctof_is_bug_shaped "${f}"; then
            bug_shaped=1
        fi

        if (( dry_run == 1 )); then
            if (( bug_shaped == 1 )); then
                echo "check-tester-orphan-files: [dry-run] ${f} is bug-shaped; would migrate to bugs/BUG-NNNN-<slug>.md" >&2
            else
                echo "check-tester-orphan-files: [dry-run] ${f} is NOT bug-shaped; would quarantine to ${f}.orphan and file bug" >&2
            fi
            overwatch_log_action \
                "check-tester-orphan-files" \
                "${f}" \
                "dry-run-orphan-detected" \
                "none" \
                "vX.Y.Z-prefixed priority file detected (bug-shaped=${bug_shaped}); dry-run, no action taken" \
            2>/dev/null || true
            continue
        fi

        if (( bug_shaped == 1 )); then
            local slug
            slug="$(_ctof_derive_slug "${f}")"

            export _CTOF_SRC_FILE="${f}"
            export _CTOF_PROJECT="${project_name}"
            export _CTOF_SLUG="${slug}"

            local fix_exit=0
            overwatch_halt_first_fix _ctof_do_handle_bug_shaped || fix_exit=$?

            unset _CTOF_SRC_FILE _CTOF_PROJECT _CTOF_SLUG

            if (( fix_exit == 3 )); then
                echo "check-tester-orphan-files: HALT_OVERWATCH guard tripped; aborting" >&2
                return 0
            elif (( fix_exit == 4 )); then
                echo "check-tester-orphan-files: per-repo flock contended; aborting" >&2
                return 0
            elif (( fix_exit == 5 )); then
                echo "check-tester-orphan-files: fix deferred: HALT present; aborting" >&2
                return 0
            elif (( fix_exit != 0 )); then
                echo "check-tester-orphan-files: handle-bug-shaped failed for ${f} (exit ${fix_exit})" >&2
                continue
            fi

        else
            export _CTOF_SRC_FILE="${f}"
            export _CTOF_PROJECT="${project_name}"

            local fix_exit=0
            overwatch_halt_first_fix _ctof_do_handle_not_bug_shaped || fix_exit=$?

            unset _CTOF_SRC_FILE _CTOF_PROJECT

            if (( fix_exit == 3 )); then
                echo "check-tester-orphan-files: HALT_OVERWATCH guard tripped; aborting" >&2
                return 0
            elif (( fix_exit == 4 )); then
                echo "check-tester-orphan-files: per-repo flock contended; aborting" >&2
                return 0
            elif (( fix_exit == 5 )); then
                echo "check-tester-orphan-files: fix deferred: HALT present; aborting" >&2
                return 0
            elif (( fix_exit != 0 )); then
                echo "check-tester-orphan-files: handle-not-bug-shaped failed for ${f} (exit ${fix_exit})" >&2
                continue
            fi
        fi

        handled=$(( handled + 1 ))
    done

    echo "check-tester-orphan-files: complete; handled ${handled} orphan file(s)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_tester_orphan_files "$@"
    exit $?
fi
