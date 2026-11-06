#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-cache-marker-drift.sh
#
# OVERWATCH detection module: detect and correct cache marker drift between
# bug and priority files and their respective backlog cache files.
#
# Drift condition detected:
#   A bug or priority file has "## Status: open" but its entry in the
#   corresponding backlog cache (bug_backlog.md or priority_backlog.md)
#   is marked "[x]" (processed/bundled).
#
# This can happen when an operator edits a bug or priority file after it was
# bundled (e.g., to add missing content or revive a previously-empty file) and
# resets ## Status back to "open", but the cache entry is still "[x]".
#
# Per SOP-core.md ("Per-Agent Queues and Marker Correctness"): derived
# caches are not gates -- status is authoritative, backlog markers are
# derived. The discovery pipeline already handles drift at bundle time, but stale "[x]"
# entries with Status=open can confuse humans reading the backlog cache.
# OVERWATCH proactively resets these entries to "[ ]" (pending) so the
# cache reflects reality.
#
# Correction applied:
#   When drift detected (Status=open, cache=[x]):
#   1. Backup the cache file via overwatch_backup_file
#   2. Replace "[x] <item_id>" with "[ ] <item_id>" in the cache file
#   3. Log the action via overwatch_log_action
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_cache_marker_drift [--dry-run]
#   - Directly invokable: bash check-cache-marker-drift.sh [--dry-run]
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
#   bash check-cache-marker-drift.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no drift found, or drift corrected without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT modify cache files.

# This file's directory, and the lib directory above it, as absolute paths
# resolved once at load: no function depends on the caller's current
# directory or on whether this file was named by a relative path.
_CCMD_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || _CCMD_SELF_DIR=""
_CCMD_LIB_DIR="${_CCMD_SELF_DIR%/*}"

# overwatch_registered_projects: the shared projects.cfg reader.
# shellcheck source=../overwatch_config.sh
source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"

# ---------------------------------------------------------------------------
# _ccmd_resolve_env
# Resolve KANBAN_ROOT and OVERWATCH_PROJECT from the environment.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_ccmd_resolve_env() {
    overwatch_resolve_check_project check-cache-marker-drift || return 1
    return 0
}

# ---------------------------------------------------------------------------
# _ccmd_load_md_field
# Make read_md_field (../md_field.sh, the one bash section reader) available,
# sourcing it from this file's own lib directory on first use unless the
# caller already defines it. A pp_run_ops the caller defined before the
# load (its own copy, or a test wrapper) stays the active one: md_field.sh
# leaves it in place, and it is also saved and put back here. Returns 0
# when read_md_field is defined.
# ---------------------------------------------------------------------------
_ccmd_load_md_field() {
    declare -F read_md_field >/dev/null 2>&1 && return 0
    local runner=""
    runner="$(declare -f pp_run_ops 2>/dev/null)" || runner=""
    # shellcheck source=../md_field.sh
    source "${_CCMD_LIB_DIR}/md_field.sh" || return 1
    [[ -n "${runner}" ]] && eval "${runner}"
    declare -F read_md_field >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# _ccmd_read_file_status <file>
# Echo the ## Status field value from a bug or priority file.
# Echoes "open", "running", "done", or the raw value found.
# Echoes "none" if the field is absent or the file does not exist.
# The section is read by read_md_field with --end-at-subheading: any letter
# case on the heading, and the next "##" line ("###" included) ends it. The
# value is its first non-blank line, lowercased, whitespace removed.
# ---------------------------------------------------------------------------
_ccmd_read_file_status() {
    local file="$1"

    if [[ ! -f "${file}" ]]; then
        echo "none"
        return 0
    fi

    local val=""
    if _ccmd_load_md_field; then
        val="$(read_md_field --end-at-subheading "${file}" Status 2>/dev/null)" || val=""
    fi
    val="${val%%$'\n'*}"
    val="$(printf '%s' "${val}" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"

    if [[ -z "${val}" ]]; then
        echo "none"
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# _ccmd_is_marked_processed <cache_file> <item_id>
# Returns 0 if the cache file contains "- [x] <item_id>" for the given item.
# Returns 1 if the entry is absent or not marked [x].
# ---------------------------------------------------------------------------
_ccmd_is_marked_processed() {
    local cache_file="$1"
    local item_id="$2"

    if [[ ! -f "${cache_file}" ]]; then
        return 1
    fi

    grep -qE "^\s*-\s+\[x\]\s+${item_id}(\s|$)" "${cache_file}" 2>/dev/null
}

# ---------------------------------------------------------------------------
# _ccmd_reset_marker <cache_file> <item_id>
# Reset every "- [x] <item_id>" line of <cache_file> to "- [ ]" in place
# (pgai_agent_kanban.overwatch.cache_marker_reset; its docstring has the
# exact line shape). Prints one line on stderr either way and exits 0 when
# at least one line was reset; exits 1, the file unchanged, when the file
# cannot be read or written or holds no [x] line for <item_id>. Runs in a
# subshell that loads this file's own lib directory's pp_run_ops, so a
# caller's definition never answers for the cache file.
# ---------------------------------------------------------------------------
_ccmd_reset_marker() (
    # shellcheck source=../pp_run_ops.sh
    source "${_CCMD_LIB_DIR}/pp_run_ops.sh" || exit 1
    pp_run_ops pgai_agent_kanban.overwatch.cache_marker_reset -- "$1" "$2"
)

# ---------------------------------------------------------------------------
# _ccmd_do_reset_cache_entry
# Inner function invoked via overwatch_halt_first_fix for one drifted item.
# Reads from environment:
#   _CCMD_CACHE_FILE  — path to the cache file to modify
#   _CCMD_ITEM_ID     — the item ID whose marker needs resetting
#   _CCMD_SRC_FILE    — path to the source bug/priority file (for logging)
#
# Actions:
#   1. Backup the cache file via overwatch_backup_file
#   2. Replace "[x] <item_id>" with "[ ] <item_id>" in the cache
#   3. Log the action via overwatch_log_action
# ---------------------------------------------------------------------------
_ccmd_do_reset_cache_entry() {
    local cache_file="${_CCMD_CACHE_FILE}"
    local item_id="${_CCMD_ITEM_ID}"
    local src_file="${_CCMD_SRC_FILE}"

    # Backup the cache file before modification
    local bpath
    bpath="$(overwatch_backup_file "${cache_file}")" || {
        echo "check-cache-marker-drift: backup failed for ${cache_file}" >&2
        return 1
    }

    # Reset [x] -> [ ] for this specific item_id
    local reset_exit=0
    _ccmd_reset_marker "${cache_file}" "${item_id}" || reset_exit=$?

    if (( reset_exit != 0 )); then
        echo "check-cache-marker-drift: reset failed for ${item_id} in ${cache_file}" >&2
        return 1
    fi

    # Log the corrective action
    overwatch_log_action \
        "check-cache-marker-drift" \
        "${item_id}" \
        "cache-marker-reset-to-pending" \
        "${bpath}" \
        "Drift: ${src_file} has Status=open but cache had [x]; reset to [ ] in ${cache_file}" \
    || true

    echo "check-cache-marker-drift: reset cache entry [x] -> [ ] for ${item_id} (backup: ${bpath})" >&2
    return 0
}

# ---------------------------------------------------------------------------
# _ccmd_scan_dir_for_drift <dir> <cache_file> <id_prefix>
# Scan <dir> for files matching <id_prefix>*.md where the file has
# Status=open but the cache marks it [x].
#
# Emits lines: "<item_id> <file_path>" for each drifted item found.
# ---------------------------------------------------------------------------
_ccmd_scan_dir_for_drift() {
    local dir="$1"
    local cache_file="$2"
    local id_prefix="$3"

    if [[ ! -d "${dir}" ]]; then
        return 0
    fi

    local f item_id file_status

    while IFS= read -r f; do
        [[ -f "${f}" ]] || continue

        local fname
        fname="$(basename "${f}")"

        # Derive item_id from filename (strip .md)
        item_id="${fname%.md}"

        # Check if the cache marks this item as processed [x]
        if ! _ccmd_is_marked_processed "${cache_file}" "${item_id}"; then
            continue
        fi

        # Check the file's ## Status field
        file_status="$(_ccmd_read_file_status "${f}")"

        # Drift condition: Status=open but cache=[x]
        if [[ "${file_status}" == "open" ]]; then
            echo "${item_id} ${f}"
        fi

    done < <(find "${dir}" -maxdepth 1 -type f -name "${id_prefix}*.md" 2>/dev/null | sort)
}

# ---------------------------------------------------------------------------
# overwatch_check_cache_marker_drift [--dry-run]
# Main detection function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_cache_marker_drift() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _ccmd_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local tasks_root="${project_root}/tasks"
    local queues_dir="${tasks_root}/queues/claude"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-cache-marker-drift: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="${_CCMD_SELF_DIR}"
        [[ -n "${lib_dir}" ]] || {
            echo "check-cache-marker-drift: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-cache-marker-drift: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-cache-marker-drift: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    local bugs_dir="${project_root}/bugs"
    local priority_dir="${project_root}/priority"
    local bug_cache="${queues_dir}/bug_backlog.md"
    local priority_cache="${queues_dir}/priority_backlog.md"

    # Build list of drifted items from both bugs/ and priority/
    local drifted_items=()
    local cache_for_item=()
    local item item_id src_file

    # Scan bugs/
    if [[ -d "${bugs_dir}" ]] && [[ -f "${bug_cache}" ]]; then
        while IFS= read -r item; do
            [[ -z "${item}" ]] && continue
            item_id="${item%% *}"
            src_file="${item#* }"
            drifted_items+=("${item_id} ${src_file} ${bug_cache}")
        done < <(_ccmd_scan_dir_for_drift "${bugs_dir}" "${bug_cache}" "BUG-")
    fi

    # Scan priority/
    if [[ -d "${priority_dir}" ]] && [[ -f "${priority_cache}" ]]; then
        while IFS= read -r item; do
            [[ -z "${item}" ]] && continue
            item_id="${item%% *}"
            src_file="${item#* }"
            drifted_items+=("${item_id} ${src_file} ${priority_cache}")
        done < <(_ccmd_scan_dir_for_drift "${priority_dir}" "${priority_cache}" "PRIORITY-")
    fi

    if (( ${#drifted_items[@]} == 0 )); then
        echo "check-cache-marker-drift: no cache marker drift detected" >&2
        return 0
    fi

    echo "check-cache-marker-drift: found ${#drifted_items[@]} drifted entry(ies)" >&2

    local corrected=0
    local entry

    for entry in "${drifted_items[@]}"; do
        # Parse: "<item_id> <src_file> <cache_file>"
        read -r item_id src_file cache_file <<< "${entry}"

        echo "check-cache-marker-drift: drift detected: ${item_id} has Status=open but cache=[x] in ${cache_file}" >&2

        if (( dry_run == 1 )); then
            echo "check-cache-marker-drift: [dry-run] would reset [x] -> [ ] for ${item_id} in ${cache_file}" >&2
            overwatch_log_action \
                "check-cache-marker-drift" \
                "${item_id}" \
                "dry-run-cache-drift-detected" \
                "none" \
                "Drift: ${src_file} has Status=open but cache has [x] in ${cache_file}; dry-run, no action taken" \
            2>/dev/null || true
            continue
        fi

        export _CCMD_CACHE_FILE="${cache_file}"
        export _CCMD_ITEM_ID="${item_id}"
        export _CCMD_SRC_FILE="${src_file}"

        local fix_exit=0
        overwatch_halt_first_fix _ccmd_do_reset_cache_entry || fix_exit=$?

        unset _CCMD_CACHE_FILE _CCMD_ITEM_ID _CCMD_SRC_FILE

        if (( fix_exit == 3 )); then
            echo "check-cache-marker-drift: HALT_OVERWATCH guard tripped; aborting" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-cache-marker-drift: per-repo flock contended; aborting" >&2
            return 0
        elif (( fix_exit == 5 )); then
            echo "check-cache-marker-drift: fix deferred: HALT present; aborting" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-cache-marker-drift: reset failed for ${item_id} (exit ${fix_exit})" >&2
            continue
        fi

        corrected=$(( corrected + 1 ))
    done

    echo "check-cache-marker-drift: complete; corrected ${corrected} drifted entry(ies)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_cache_marker_drift "$@"
    exit $?
fi
