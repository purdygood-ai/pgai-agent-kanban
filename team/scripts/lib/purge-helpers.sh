#!/usr/bin/env bash
# team/scripts/lib/purge-helpers.sh
# Per-category purge functions for purge-old-files.sh.
#
# Source this file from purge-old-files.sh (the entry point) and from unit
# tests that exercise each function in isolation.
#
# All functions write output in greppable log-tagged form:
#   [WOULD PURGE]    <path>  (apply=false / dry-run mode)
#   [PURGED]         <path>  (apply=true, delete succeeded)
#   [SKIP]           <path>  (item preserved: too recent, wrong state, no status.md, etc.)
#   [SKIP active-RC] <path>  (task referenced by current active RC bundle — never purge)
#   [SKIP blocked]   <path>  (task in BLOCKED state; use --include-blocked to override)
#   [PURGED dir]     <path>  (empty directory removed after purge run)
#   [WOULD PURGE dir] <path> (dry-run: directory would be emptied and removed)
#
# Functions
# ---------
#   get_active_rc                <project_dir>                — public: returns active RC or ""
#   get_rc_referenced_tasks      <project_dir> <rc_version>  — public: returns task-ID list
#   purge_tasks                  <project_root> <days> <apply> <include_blocked>
#   purge_log_archives           <project_root> <days> <apply>
#   purge_shipped_bundles        <project_root> <days> <apply>
#   purge_closed_bugs            <project_root> <days> <apply>
#   purge_closed_priorities      <project_root> <days> <apply>
#   snapshot_nonempty_task_dirs  <project_root>              — returns newline list of non-empty task subdirs
#   purge_empty_task_dirs        <project_root> <snapshot> <apply>
#
# PROTECTED DIRECTORIES (never purged by any function in this file)
# -----------------------------------------------------------------------
# projects/<proj>/artifacts/  — the versioned document library (v0.44.0+).
#   This directory is written by cm-finalize.sh when PGAI_TARGET_VERSION is set.
#   Published artifacts (v<ver>-<name>.md) are permanent library entries and must
#   NOT be swept by any periodic cleanup.  None of the purge_* functions scan
#   artifacts/ — the protection is structural (they only scan tasks/, logs/,
#   requirements/, bugs/, priority/).  Future additions must maintain this invariant:
#   do not add logic that reads from or removes entries in projects/<proj>/artifacts/.
#   If WRITER task artifacts/ must be purged (they are under tasks/<id>/artifacts/
#   and handled by purge_tasks), that path is distinct from the project-level
#   artifacts/ library and does not fall under this constraint.
#
# Each purge_* category function prints per-item log lines to stdout and on
# exit echoes two values on stdout separated by a space as the final line:
#   <count> <bytes>
# where count is the number of items purged (or would-purge) and bytes is the
# total size in bytes.  Callers use these for summary accounting.
#
# Argument conventions
# --------------------
#   project_root   — absolute path to the project directory (e.g., .../projects/pgai-agent-kanban)
#   days           — integer retention threshold; items older than this many days are candidates
#   apply          — "true" to actually delete; "false" (or anything else) for dry-run
#   include_blocked— "true" to also purge BLOCKED tasks (purge_tasks only)
#
# Output conventions
# ------------------
#   Per-item lines are emitted to stdout so callers can tee them to the run log.
#   The final stdout line of every purge_* function is "<count> <bytes>" for
#   summary accounting.  All stderr output is for diagnostic messages only.

# ---------------------------------------------------------------------------
# Internal helper: _purge_read_field <file> <field_name>
# Parse the body of a "## FieldName" section from a markdown file.
# Prints the first non-blank line after the matching heading, or empty string.
# ---------------------------------------------------------------------------
_purge_read_field() {
    local file="$1"
    local field="$2"
    local val
    val="$(awk -v fld="## ${field}" '
        $0 == fld { found=1; next }
        found && /^## / { exit }
        found && /^[[:space:]]*$/ { next }
        found { print; exit }
    ' "${file}" | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    echo "${val}"
}

# ---------------------------------------------------------------------------
# Internal helper: _purge_read_active_rc <project_root>
# Echo the Active RC value from release-state.md, or "none" if absent.
# ---------------------------------------------------------------------------
_purge_read_active_rc() {
    local project_root="$1"
    local rs_file="${project_root}/release-state.md"
    if [[ ! -f "${rs_file}" ]]; then
        echo "none"
        return 0
    fi
    local val
    val="$(awk '/^## Active RC$/{found=1; next} found && /^[[:space:]]*$/{next} found{print; exit}' "${rs_file}" \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [[ -z "${val}" ]]; then
        echo "none"
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# Public helper: get_active_rc <project_dir>
# Returns the active RC version string from <project_dir>/release-state.md,
# or empty string when no active RC is set (value is "none" or file is absent).
#
# Usage:
#   active_rc=$(get_active_rc "$project_dir")
#   if [[ -n "$active_rc" ]]; then ... fi
# ---------------------------------------------------------------------------
get_active_rc() {
    local project_dir="$1"
    local val
    val="$(_purge_read_active_rc "${project_dir}")"
    if [[ "${val}" == "none" ]] || [[ -z "${val}" ]]; then
        echo ""
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# Internal helper: _purge_collect_active_rc_tasks <project_root> <active_rc>
# Build a newline-separated list of task IDs referenced in the active RC's
# bundle requirement files.  Returns empty string if no bundle files found.
# ---------------------------------------------------------------------------
_purge_collect_active_rc_tasks() {
    local project_root="$1"
    local active_rc="$2"
    local req_dir="${project_root}/requirements"
    if [[ -z "${active_rc}" ]] || [[ "${active_rc}" == "none" ]]; then
        echo ""
        return 0
    fi
    # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
    # Bundle files are named like: v0.24.8-bugfix-bundle-*.md or v0.24.8-priority-bundle-*.md
    local bundle_pattern="${req_dir}/${active_rc}-*bundle*.md"
    local referenced=""
    for bundle_file in ${bundle_pattern}; do
        [[ -f "${bundle_file}" ]] || continue
        local ids
        ids="$(grep -oE 'CLAUDE-[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+' "${bundle_file}" 2>/dev/null || true)"
        if [[ -n "${ids}" ]]; then
            if [[ -n "${referenced}" ]]; then
                referenced="${referenced}"$'\n'"${ids}"
            else
                referenced="${ids}"
            fi
        fi
    done
    echo "${referenced}"
}

# ---------------------------------------------------------------------------
# Public helper: get_rc_referenced_tasks <project_dir> <rc_version>
# Returns a newline-separated list of task IDs referenced in the bundle
# requirements files for the given RC version.  Returns empty string when no
# bundle files are found or rc_version is empty / "none".
#
# Task IDs match the pattern: CLAUDE-[A-Z]+-[0-9]+-[0-9]+-[a-z0-9-]+
#
# Usage:
#   task_list=$(get_rc_referenced_tasks "$project_dir" "$rc_version")
#   if echo "$task_list" | grep -qxF "$task_id"; then ... fi
# ---------------------------------------------------------------------------
get_rc_referenced_tasks() {
    local project_dir="$1"
    local rc_version="$2"
    _purge_collect_active_rc_tasks "${project_dir}" "${rc_version}"
}

# ---------------------------------------------------------------------------
# Internal helper: _purge_task_in_list <task_id> <newline_list>
# Returns 0 (true) if task_id appears in the newline-separated list.
# ---------------------------------------------------------------------------
_purge_task_in_list() {
    local task_id="$1"
    local list="$2"
    [[ -z "${list}" ]] && return 1
    echo "${list}" | grep -qxF "${task_id}"
}

# ---------------------------------------------------------------------------
# Internal helper: _purge_item_size_bytes <path>
# Return size in bytes of <path> (file or directory tree) using du -sb.
# Falls back to du -sk * 1024 if -b not supported (BSD du).
# Returns 0 on error.
# ---------------------------------------------------------------------------
_purge_item_size_bytes() {
    local path="$1"
    local size
    # Try GNU du -sb first, then BSD du -sk.
    size="$(du -sb "${path}" 2>/dev/null | awk '{print $1}')" && [[ -n "${size}" ]] && echo "${size}" && return 0
    size="$(du -sk "${path}" 2>/dev/null | awk '{print $1 * 1024}')" && [[ -n "${size}" ]] && echo "${size}" && return 0
    echo "0"
}

# ---------------------------------------------------------------------------
# Internal helper: _purge_do_delete <path> <apply>
# Delete <path> (file or directory) when apply=true.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_purge_do_delete() {
    local path="$1"
    local apply="$2"
    if [[ "${apply}" == "true" ]]; then
        if [[ -d "${path}" ]]; then
            rm -rf "${path}" 2>/dev/null
        else
            rm -f "${path}" 2>/dev/null
        fi
    fi
}

# ---------------------------------------------------------------------------
# purge_tasks
# Scan <project_root>/tasks/CLAUDE-*/ for terminal-state task folders
# (DONE or WONT-DO, and optionally BLOCKED) whose status.md mtime is older
# than <days> days.  Respects active-RC defense: tasks referenced by the
# current RC's bundle requirements are never purged.
#
# Args:
#   $1  project_root    — absolute path to the project root directory
#   $2  days            — retention threshold in days (integer)
#   $3  apply           — "true" to delete; "false" for dry-run
#   $4  include_blocked — "true" to also purge BLOCKED tasks; "false" to skip
#
# Stdout (final line): "<count> <bytes>"
# ---------------------------------------------------------------------------
purge_tasks() {
    local project_root="$1"
    local days="$2"
    local apply="$3"
    local include_blocked="${4:-false}"

    local tasks_dir="${project_root}/tasks"
    local count=0
    local total_bytes=0

    # Guard: no tasks directory.
    if [[ ! -d "${tasks_dir}" ]]; then
        echo "0 0"
        return 0
    fi

    # Collect active-RC task references for the skip guard.
    local active_rc
    active_rc="$(_purge_read_active_rc "${project_root}")"
    local rc_task_list=""
    if [[ "${active_rc}" != "none" ]]; then
        rc_task_list="$(_purge_collect_active_rc_tasks "${project_root}" "${active_rc}")"
    fi

    # Find candidate task folders: named CLAUDE-* and older than <days> days.
    # We use -maxdepth 1 on the tasks dir so we only look at immediate children.
    while IFS= read -r -d '' task_dir; do
        [[ -d "${task_dir}" ]] || continue
        local task_id
        task_id="$(basename "${task_dir}")"

        # Active-RC defense: never purge tasks referenced by the current bundle.
        if _purge_task_in_list "${task_id}" "${rc_task_list}"; then
            echo "[SKIP active-RC] ${task_dir} (referenced by active RC ${active_rc})"
            continue
        fi

        # Read task state from status.md.
        local status_file="${task_dir}/status.md"
        if [[ ! -f "${status_file}" ]]; then
            # No status.md — cannot determine state; skip safely.
            echo "[SKIP] ${task_dir} (no status.md found)"
            continue
        fi

        local task_state
        task_state="$(_purge_read_field "${status_file}" "State")"
        # Normalize to uppercase for comparison.
        task_state="${task_state^^}"

        case "${task_state}" in
            DONE|WONT-DO)
                # Terminal states eligible for purge — proceed.
                ;;
            BLOCKED)
                if [[ "${include_blocked}" == "true" ]]; then
                    : # Proceed — operator explicitly opted in.
                else
                    echo "[SKIP blocked] ${task_dir} (state=BLOCKED; use --include-blocked to purge)"
                    continue
                fi
                ;;
            *)
                # Non-terminal state (BACKLOG, WAITING, WORKING) or unknown — skip.
                echo "[SKIP] ${task_dir} (state=${task_state}; non-terminal)"
                continue
                ;;
        esac

        # Size before potential deletion.
        local item_bytes
        item_bytes="$(_purge_item_size_bytes "${task_dir}")"

        if [[ "${apply}" == "true" ]]; then
            _purge_do_delete "${task_dir}" "true"
            echo "[PURGED] ${task_dir} (state=${task_state})"
        else
            echo "[WOULD PURGE] ${task_dir} (state=${task_state})"
        fi
        count=$(( count + 1 ))
        total_bytes=$(( total_bytes + item_bytes ))

    done < <(find "${tasks_dir}" -maxdepth 1 -mindepth 1 -type d -name 'CLAUDE-*' -mtime "+${days}" -print0 2>/dev/null)

    echo "${count} ${total_bytes}"
}

# ---------------------------------------------------------------------------
# purge_log_archives
# Scan <project_root>/logs/*/archive/ for rotated debug log files whose
# mtime is older than <days> days.  Also scans $KANBAN_ROOT/logs/ for
# purge-*.log and cleanup-*.log files beyond the threshold.
#
# Args:
#   $1  project_root — absolute path to the project root directory
#   $2  days         — retention threshold in days (integer)
#   $3  apply        — "true" to delete; "false" for dry-run
#
# Stdout (final line): "<count> <bytes>"
# ---------------------------------------------------------------------------
purge_log_archives() {
    local project_root="$1"
    local days="$2"
    local apply="$3"

    local count=0
    local total_bytes=0

    # --- Project-level log archives: <project_root>/logs/*/archive/ ---
    local proj_logs_dir="${project_root}/logs"
    if [[ -d "${proj_logs_dir}" ]]; then
        # Find files inside any archive/ subdirectory under project logs.
        while IFS= read -r -d '' log_file; do
            [[ -f "${log_file}" ]] || continue
            local item_bytes
            item_bytes="$(_purge_item_size_bytes "${log_file}")"
            if [[ "${apply}" == "true" ]]; then
                _purge_do_delete "${log_file}" "true"
                echo "[PURGED] ${log_file}"
            else
                echo "[WOULD PURGE] ${log_file}"
            fi
            count=$(( count + 1 ))
            total_bytes=$(( total_bytes + item_bytes ))
        done < <(find "${proj_logs_dir}" -path "*/archive/*" -type f -mtime "+${days}" -print0 2>/dev/null)
    fi

    # --- Kanban-root log files: purge-*.log, cleanup-*.log ---
    # KANBAN_ROOT is an env var set by the caller (purge-old-files.sh).
    # Guard: only scan when the variable is set and the directory exists.
    local kanban_logs_dir="${KANBAN_ROOT:-}/logs"
    if [[ -n "${KANBAN_ROOT:-}" ]] && [[ -d "${kanban_logs_dir}" ]]; then
        # Only process purge and cleanup log files (not all logs).
        while IFS= read -r -d '' log_file; do
            [[ -f "${log_file}" ]] || continue
            local item_bytes
            item_bytes="$(_purge_item_size_bytes "${log_file}")"
            if [[ "${apply}" == "true" ]]; then
                _purge_do_delete "${log_file}" "true"
                echo "[PURGED] ${log_file}"
            else
                echo "[WOULD PURGE] ${log_file}"
            fi
            count=$(( count + 1 ))
            total_bytes=$(( total_bytes + item_bytes ))
        done < <(find "${kanban_logs_dir}" -maxdepth 1 -type f \
            \( -name 'purge-*.log' -o -name 'cleanup-*.log' \) \
            -mtime "+${days}" -print0 2>/dev/null)
    fi

    echo "${count} ${total_bytes}"
}

# ---------------------------------------------------------------------------
# purge_shipped_bundles
# Scan <project_root>/requirements/ for bundle files matching the patterns
#   *-bugfix-bundle-*.md
#   *-priority-bundle-*.md
# that are older than <days> days AND whose release version has a corresponding
# release-notes entry (confirming the bundle shipped).
#
# Detection logic: extract the version prefix (vX.Y.Z) from the filename and
# check whether <project_root>/release-notes/vX.Y.Z.md exists.
#
# Args:
#   $1  project_root — absolute path to the project root directory
#   $2  days         — retention threshold in days (integer)
#   $3  apply        — "true" to delete; "false" for dry-run
#
# Stdout (final line): "<count> <bytes>"
# ---------------------------------------------------------------------------
purge_shipped_bundles() {
    local project_root="$1"
    local days="$2"
    local apply="$3"

    local req_dir="${project_root}/requirements"
    local release_notes_dir="${project_root}/release-notes"
    local count=0
    local total_bytes=0

    # Guard: no requirements directory.
    if [[ ! -d "${req_dir}" ]]; then
        echo "0 0"
        return 0
    fi

    # Find bundle files older than <days>.
    while IFS= read -r -d '' bundle_file; do
        [[ -f "${bundle_file}" ]] || continue
        local filename
        filename="$(basename "${bundle_file}")"

        # Skip non-bundle files (double-check the name pattern).
        case "${filename}" in
            *-bugfix-bundle-*.md|*-priority-bundle-*.md) ;;
            *) continue ;;
        esac

        # Extract version: first token split by '-' that starts with 'v'.
        # Filename format: v0.24.8-bugfix-bundle-20260501.md
        local version=""
        version="$(echo "${filename}" | grep -oE '^v[0-9]+\.[0-9]+\.[0-9]+')"
        if [[ -z "${version}" ]]; then
            echo "[SKIP] ${bundle_file} (cannot extract version from filename)"
            continue
        fi

        # Safety gate: require a release-notes entry for this version.
        local release_note_file="${release_notes_dir}/${version}.md"
        if [[ ! -f "${release_note_file}" ]]; then
            echo "[SKIP] ${bundle_file} (no release-notes/${version}.md — not confirmed shipped)"
            continue
        fi

        local item_bytes
        item_bytes="$(_purge_item_size_bytes "${bundle_file}")"

        if [[ "${apply}" == "true" ]]; then
            _purge_do_delete "${bundle_file}" "true"
            echo "[PURGED] ${bundle_file} (shipped per release-notes/${version}.md)"
        else
            echo "[WOULD PURGE] ${bundle_file} (shipped per release-notes/${version}.md)"
        fi
        count=$(( count + 1 ))
        total_bytes=$(( total_bytes + item_bytes ))

    done < <(find "${req_dir}" -maxdepth 1 -type f \
        \( -name '*-bugfix-bundle-*.md' -o -name '*-priority-bundle-*.md' \) \
        -mtime "+${days}" -print0 2>/dev/null)

    echo "${count} ${total_bytes}"
}

# ---------------------------------------------------------------------------
# purge_closed_bugs
# Scan <project_root>/bugs/ for bug files whose "## Status" field is
# "done" or "wont-do" and whose mtime is older than <days> days.
#
# Skips README.md and any file whose Status is not done/wont-do.
#
# Args:
#   $1  project_root — absolute path to the project root directory
#   $2  days         — retention threshold in days (integer)
#   $3  apply        — "true" to delete; "false" for dry-run
#
# Stdout (final line): "<count> <bytes>"
# ---------------------------------------------------------------------------
purge_closed_bugs() {
    local project_root="$1"
    local days="$2"
    local apply="$3"

    local bugs_dir="${project_root}/bugs"
    local count=0
    local total_bytes=0

    # Guard: no bugs directory.
    if [[ ! -d "${bugs_dir}" ]]; then
        echo "0 0"
        return 0
    fi

    # Find .md files in bugs/ older than <days>.
    while IFS= read -r -d '' bug_file; do
        [[ -f "${bug_file}" ]] || continue
        local filename
        filename="$(basename "${bug_file}")"

        # Never purge README.md or template files.
        case "${filename}" in
            README.md|*.template.md) continue ;;
        esac

        # Read the ## Status field.
        local file_status
        file_status="$(_purge_read_field "${bug_file}" "Status")"
        # Normalize to lowercase for comparison.
        file_status="${file_status,,}"

        case "${file_status}" in
            done|wont-do)
                # Eligible for purge.
                ;;
            *)
                echo "[SKIP] ${bug_file} (status=${file_status}; not closed)"
                continue
                ;;
        esac

        local item_bytes
        item_bytes="$(_purge_item_size_bytes "${bug_file}")"

        if [[ "${apply}" == "true" ]]; then
            _purge_do_delete "${bug_file}" "true"
            echo "[PURGED] ${bug_file} (status=${file_status})"
        else
            echo "[WOULD PURGE] ${bug_file} (status=${file_status})"
        fi
        count=$(( count + 1 ))
        total_bytes=$(( total_bytes + item_bytes ))

    done < <(find "${bugs_dir}" -maxdepth 1 -type f -name '*.md' -mtime "+${days}" -print0 2>/dev/null)

    echo "${count} ${total_bytes}"
}

# ---------------------------------------------------------------------------
# purge_closed_priorities
# Scan <project_root>/priority/ for priority files whose "## Status" field
# is "done" or "wont-do" and whose mtime is older than <days> days.
# Uses the same detection logic as purge_closed_bugs.
#
# Args:
#   $1  project_root — absolute path to the project root directory
#   $2  days         — retention threshold in days (integer)
#   $3  apply        — "true" to delete; "false" for dry-run
#
# Stdout (final line): "<count> <bytes>"
# ---------------------------------------------------------------------------
purge_closed_priorities() {
    local project_root="$1"
    local days="$2"
    local apply="$3"

    local priority_dir="${project_root}/priority"
    local count=0
    local total_bytes=0

    # Guard: no priority directory.
    if [[ ! -d "${priority_dir}" ]]; then
        echo "0 0"
        return 0
    fi

    # Find .md files in priority/ older than <days>.
    while IFS= read -r -d '' pri_file; do
        [[ -f "${pri_file}" ]] || continue
        local filename
        filename="$(basename "${pri_file}")"

        # Never purge README.md or template files.
        case "${filename}" in
            README.md|*.template.md) continue ;;
        esac

        # Read the ## Status field.
        local file_status
        file_status="$(_purge_read_field "${pri_file}" "Status")"
        # Normalize to lowercase for comparison.
        file_status="${file_status,,}"

        case "${file_status}" in
            done|wont-do)
                # Eligible for purge.
                ;;
            *)
                echo "[SKIP] ${pri_file} (status=${file_status}; not closed)"
                continue
                ;;
        esac

        local item_bytes
        item_bytes="$(_purge_item_size_bytes "${pri_file}")"

        if [[ "${apply}" == "true" ]]; then
            _purge_do_delete "${pri_file}" "true"
            echo "[PURGED] ${pri_file} (status=${file_status})"
        else
            echo "[WOULD PURGE] ${pri_file} (status=${file_status})"
        fi
        count=$(( count + 1 ))
        total_bytes=$(( total_bytes + item_bytes ))

    done < <(find "${priority_dir}" -maxdepth 1 -type f -name '*.md' -mtime "+${days}" -print0 2>/dev/null)

    echo "${count} ${total_bytes}"
}

# ---------------------------------------------------------------------------
# snapshot_nonempty_task_dirs <project_root>
# Print a newline-separated list of absolute paths to tasks/ subdirectories
# that currently contain at least one file (are non-empty).
# Used before the purge run to detect which directories this run empties.
#
# Args:
#   $1  project_root — absolute path to the project root directory
#
# Stdout: one absolute path per line for each non-empty tasks/ subdir.
# ---------------------------------------------------------------------------
snapshot_nonempty_task_dirs() {
    local project_root="$1"
    local tasks_dir="${project_root}/tasks"

    if [[ ! -d "${tasks_dir}" ]]; then
        return 0
    fi

    # Iterate immediate subdirectories of tasks/.
    while IFS= read -r -d '' task_dir; do
        [[ -d "${task_dir}" ]] || continue
        # Non-empty: at least one file exists anywhere under the directory.
        if find "${task_dir}" -mindepth 1 -maxdepth 3 -type f -print -quit 2>/dev/null | grep -q .; then
            echo "${task_dir}"
        fi
    done < <(find "${tasks_dir}" -maxdepth 1 -mindepth 1 -type d -print0 2>/dev/null)
}

# ---------------------------------------------------------------------------
# purge_empty_task_dirs <project_root> <nonempty_snapshot> <apply>
# Remove tasks/ subdirectories that were non-empty before this purge run
# but are now empty (i.e., were emptied by this run).
# Pre-existing empty directories are NOT removed — this function only cleans
# up directories that this specific run rendered empty.
#
# Args:
#   $1  project_root      — absolute path to the project root directory
#   $2  nonempty_snapshot — newline-separated list of paths that were non-empty
#                           before the run (from snapshot_nonempty_task_dirs)
#   $3  apply             — "true" to actually remove dirs; "false" for dry-run
#
# Stdout: one log line per directory removed (or would-remove).
# ---------------------------------------------------------------------------
purge_empty_task_dirs() {
    local project_root="$1"
    local nonempty_snapshot="$2"
    local apply="$3"

    if [[ -z "${nonempty_snapshot}" ]]; then
        return 0
    fi

    # For each directory that was non-empty before the run, check if it is
    # now empty (no files remain at any depth).
    while IFS= read -r task_dir; do
        [[ -z "${task_dir}" ]] && continue

        # If the directory was removed wholesale by purge_tasks, skip — it is
        # already gone and does not need a separate rmdir pass.
        [[ -d "${task_dir}" ]] || continue

        # Check if any files remain inside the directory.
        if find "${task_dir}" -mindepth 1 -maxdepth 3 -type f -print -quit 2>/dev/null | grep -q .; then
            # Still has files — not emptied by this run; leave alone.
            continue
        fi

        # Directory exists but is now empty (or contains only empty subdirs).
        if [[ "${apply}" == "true" ]]; then
            rm -rf "${task_dir}" 2>/dev/null \
                && echo "[PURGED dir] ${task_dir} (emptied by this run)" \
                || echo "[WARN] failed to remove empty dir: ${task_dir}"
        else
            echo "[WOULD PURGE dir] ${task_dir} (would be emptied by this run)"
        fi
    done <<< "${nonempty_snapshot}"
}
