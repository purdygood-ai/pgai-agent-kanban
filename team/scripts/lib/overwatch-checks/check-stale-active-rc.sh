#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-stale-active-rc.sh
#
# OVERWATCH detection module: detect a stale Active RC in release-state.md.
#
# A stale Active RC is defined as:
#   - release-state.md shows "Active RC: vX.Y.Z" (non-none), AND
#   - a git tag matching that version exists in the dev tree, AND
#   - no local rc/<version> branch exists in the dev tree.
#
# This condition indicates a release completed (tag exists) but the RC state
# was never reset (branch gone, tag present, state file stuck). OVERWATCH
# corrects it by:
#   1. Backing up release-state.md
#   2. Resetting "Active RC" to "none" (clearing RC Opened At / RC Opened By Task)
#   3. Logging the action
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_stale_active_rc [--dry-run]
#   - Directly invokable: bash check-stale-active-rc.sh [--dry-run]
#
# Required environment variables (when sourced by OVERWATCH driver):
#   KANBAN_ROOT      — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# For dev tree git plumbing, the script reads dev_tree_path from:
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/project.cfg (preferred)
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/PROJECT.cfg (legacy fallback)
# or falls back to $PGAI_DEV_TREE_PATH if set.
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to "pgai-agent-kanban".
#
# Usage:
#   bash check-stale-active-rc.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no stale RC, or stale RC reset without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT modify release-state.md.

# ---------------------------------------------------------------------------
# _csarc_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and the dev tree path from the env.
# Sets _CSARC_DEV_TREE in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_csarc_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-stale-active-rc: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        # Resolve from projects.cfg — never silently fall back to first project.
        # On a multi-project install the caller MUST set OVERWATCH_PROJECT; this
        # path is only safe for single-project installs.
        local _all_projects=""
        if declare -f projects_cfg_list >/dev/null 2>&1; then
            _all_projects="$(projects_cfg_list 2>/dev/null)"
        else
            local _cfg="${KANBAN_ROOT}/projects.cfg"
            _all_projects="$(awk '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1]}' "$_cfg" 2>/dev/null)"
        fi
        local _project_count
        _project_count="$(echo "${_all_projects}" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)"
        if [[ "${_project_count}" -gt 1 ]]; then
            echo "check-stale-active-rc: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-stale-active-rc: ERROR: no project specified and none resolvable from projects.cfg" >&2
            echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path: project.cfg takes precedence over env (falls back to PROJECT.cfg for legacy installs)
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        cfg_file="${_proj_root}/PROJECT.cfg"
    fi
    _CSARC_DEV_TREE=""
    if [[ -n "${cfg_file}" ]]; then
        _CSARC_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CSARC_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CSARC_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi
    if [[ -z "${_CSARC_DEV_TREE}" ]]; then
        echo "check-stale-active-rc: cannot resolve dev tree path (set PGAI_DEV_TREE_PATH or ensure project.cfg has dev_tree_path)" >&2
        return 1
    fi
    if [[ ! -d "${_CSARC_DEV_TREE}" ]]; then
        echo "check-stale-active-rc: dev tree path does not exist: ${_CSARC_DEV_TREE}" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _csarc_read_active_rc <release_state_file>
# Echo the current Active RC value from release-state.md, or "none" if absent.
# ---------------------------------------------------------------------------
_csarc_read_active_rc() {
    local rs_file="$1"
    if [[ ! -f "${rs_file}" ]]; then
        echo "none"
        return 0
    fi
    # Extract the line immediately following "## Active RC"
    local val
    val="$(awk '/^## Active RC/{found=1; next} found && /^[[:space:]]*$/{next} found{print; exit}' "${rs_file}" \
        | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
    if [[ -z "${val}" ]]; then
        echo "none"
    else
        echo "${val}"
    fi
}

# ---------------------------------------------------------------------------
# _csarc_tag_exists <dev_tree> <version>
# Returns 0 if a git tag exactly matching <version> exists in the dev tree.
# <version> is e.g. "v0.21.42".
# Uses git plumbing (git tag --list) per task constraints.
# ---------------------------------------------------------------------------
_csarc_tag_exists() {
    local dev_tree="$1"
    local version="$2"
    local result
    result="$(git -C "${dev_tree}" tag --list "${version}" 2>/dev/null)" || return 1
    [[ -n "${result}" ]]
}

# ---------------------------------------------------------------------------
# _csarc_rc_branch_exists <dev_tree> <version>
# Returns 0 if a local branch named rc/<version> exists in the dev tree.
# <version> is e.g. "v0.21.42".
# Uses git plumbing (git branch --list) per task constraints.
# ---------------------------------------------------------------------------
_csarc_rc_branch_exists() {
    local dev_tree="$1"
    local version="$2"
    local result
    result="$(git -C "${dev_tree}" branch --list "rc/${version}" 2>/dev/null)" || return 1
    [[ -n "${result}" ]]
}

# ---------------------------------------------------------------------------
# _csarc_do_reset
# Inner function invoked via overwatch_halt_first_fix.
# Reads _CSARC_RS_FILE and _CSARC_ACTIVE_RC from the environment.
#
# Actions:
#   1. Backup release-state.md via overwatch_backup_file
#   2. Rewrite the file: set Active RC to none, clear RC Opened At / RC Opened By Task
#   3. Log the action via overwatch_log_action
# ---------------------------------------------------------------------------
_csarc_do_reset() {
    local rs_file="${_CSARC_RS_FILE}"
    local stale_rc="${_CSARC_ACTIVE_RC}"
    local backup_path="none"

    # Backup before modify
    local bpath
    bpath="$(overwatch_backup_file "${rs_file}")" || {
        echo "check-stale-active-rc: backup failed for ${rs_file}" >&2
        return 1
    }
    backup_path="${bpath}"

    # Rewrite release-state.md: reset Active RC, clear RC Opened At / RC Opened By Task
    # Strategy: use awk to replace the values while preserving all other lines.
    local new_content
    new_content="$(awk '
        /^## Active RC$/ { in_active_rc=1; print; next }
        /^## RC Opened At$/ { in_active_rc=0; in_opened_at=1; print; next }
        /^## RC Opened By Task$/ { in_opened_at=0; in_opened_by=1; print; next }
        /^##/ { in_active_rc=0; in_opened_at=0; in_opened_by=0 }
        in_active_rc && /^[^#]/ { print "none"; in_active_rc=0; next }
        in_opened_at && /^[^#]/ { print "none"; in_opened_at=0; next }
        in_opened_by && /^[^#]/ { print "none"; in_opened_by=0; next }
        { print }
    ' "${rs_file}")"

    if [[ -z "${new_content}" ]]; then
        echo "check-stale-active-rc: awk produced empty output for ${rs_file}" >&2
        return 1
    fi

    if ! printf '%s\n' "${new_content}" > "${rs_file}" 2>/dev/null; then
        echo "check-stale-active-rc: failed to write reset release-state.md at ${rs_file}" >&2
        return 1
    fi

    # Log
    overwatch_log_action \
        "check-stale-active-rc" \
        "${rs_file}" \
        "active-rc-reset-to-none" \
        "${backup_path}" \
        "Stale Active RC ${stale_rc}: tag exists, rc/ branch absent; reset to none" \
    || true

    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_stale_active_rc [--dry-run]
# Main detection function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_stale_active_rc() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _csarc_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local rs_file="${project_root}/release-state.md"

    if [[ ! -f "${rs_file}" ]]; then
        echo "check-stale-active-rc: release-state.md not found at ${rs_file}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-stale-active-rc: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-stale-active-rc: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        # Verify overwatch state dir exists for live mode
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-stale-active-rc: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    # Read current Active RC
    local active_rc
    active_rc="$(_csarc_read_active_rc "${rs_file}")"

    if [[ -z "${active_rc}" || "${active_rc}" == "none" ]]; then
        echo "check-stale-active-rc: Active RC is none; nothing to check" >&2
        return 0
    fi

    echo "check-stale-active-rc: Active RC is ${active_rc}; checking for stale state" >&2

    # Condition 1: tag must exist
    if ! _csarc_tag_exists "${_CSARC_DEV_TREE}" "${active_rc}"; then
        echo "check-stale-active-rc: tag ${active_rc} does not exist; no stale state" >&2
        return 0
    fi
    echo "check-stale-active-rc: tag ${active_rc} exists" >&2

    # Condition 2: rc/ branch must NOT exist
    if _csarc_rc_branch_exists "${_CSARC_DEV_TREE}" "${active_rc}"; then
        echo "check-stale-active-rc: branch rc/${active_rc} still exists; Active RC is not stale" >&2
        return 0
    fi
    echo "check-stale-active-rc: branch rc/${active_rc} absent — stale Active RC confirmed" >&2

    if (( dry_run == 1 )); then
        echo "check-stale-active-rc: [dry-run] would reset Active RC from ${active_rc} to none in ${rs_file}" >&2
        overwatch_log_action \
            "check-stale-active-rc" \
            "${rs_file}" \
            "dry-run-stale-active-rc-detected" \
            "none" \
            "Stale Active RC ${active_rc} detected (tag exists, rc/ branch absent); dry-run, no action taken" \
        2>/dev/null || true
        return 0
    fi

    # Live mode: reset via overwatch_halt_first_fix
    export _CSARC_RS_FILE="${rs_file}"
    export _CSARC_ACTIVE_RC="${active_rc}"

    local fix_exit=0
    overwatch_halt_first_fix _csarc_do_reset || fix_exit=$?

    unset _CSARC_RS_FILE _CSARC_ACTIVE_RC

    if (( fix_exit == 3 )); then
        echo "check-stale-active-rc: HALT_OVERWATCH guard tripped; aborting reset" >&2
        return 0
    elif (( fix_exit == 4 )); then
        echo "check-stale-active-rc: per-repo flock contended; aborting reset" >&2
        return 0
    elif (( fix_exit != 0 )); then
        echo "check-stale-active-rc: reset failed (exit ${fix_exit})" >&2
        return 1
    fi

    echo "check-stale-active-rc: Active RC reset from ${active_rc} to none" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_stale_active_rc "$@"
    exit $?
fi
