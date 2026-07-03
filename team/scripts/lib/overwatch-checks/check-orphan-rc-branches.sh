#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-orphan-rc-branches.sh
#
# OVERWATCH detection module: find and delete orphan RC branches — local and
# remote rc/* branches for which a corresponding release tag already exists.
#
# A rc/vX.Y.Z branch is "orphan" if:
#   1. The local branch rc/vX.Y.Z exists, AND
#   2. A git tag named vX.Y.Z exists in the dev tree.
#
# An orphan RC branch indicates that the release completed (tag pushed) but the
# RC branch was not cleaned up. CM is responsible for branch cleanup as part
# of the release flow, but when that step is missed, this check catches it.
#
# Corrective action (per branch):
#   1. Touch HALT via overwatch_halt_first_fix before any mutation.
#   2. Delete the local rc/vX.Y.Z branch (git branch -d).
#   3. Delete the remote rc/vX.Y.Z branch (git push origin --delete rc/vX.Y.Z).
#      In --dry-run mode, the push is never invoked.
#   4. Log both deletions via overwatch_log_action.
#
# Notes:
#   - The ACTIVE RC branch (the one currently being worked) is NOT deleted.
#     OVERWATCH resolves the active RC from release-state.md and skips it.
#   - Branch deletion goes through overwatch_halt_first_fix to honor HALT.
#   - In --dry-run, git branch -d and git push are never invoked.
#   - git push is the ONLY origin operation in CODER-authored code that is
#     explicitly authorized by the task brief (Notes: "Branch deletion of
#     origin requires push permissions; in dry-run never invoke git push").
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_orphan_rc_branches [--dry-run]
#   - Directly invokable: bash check-orphan-rc-branches.sh [--dry-run]
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
#   bash check-orphan-rc-branches.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no orphan branches, or branches deleted without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT delete branches or push to origin.

# ---------------------------------------------------------------------------
# _corb_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and the dev tree path.
# Sets _CORB_DEV_TREE in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_corb_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-orphan-rc-branches: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-orphan-rc-branches: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-orphan-rc-branches: ERROR: no project specified and none resolvable from projects.cfg" >&2
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
    _CORB_DEV_TREE=""
    if [[ -n "${cfg_file}" ]]; then
        _CORB_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CORB_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CORB_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi
    if [[ -z "${_CORB_DEV_TREE}" ]]; then
        echo "check-orphan-rc-branches: cannot resolve dev tree path (set PGAI_DEV_TREE_PATH or ensure project.cfg has dev_tree_path)" >&2
        return 1
    fi
    if [[ ! -d "${_CORB_DEV_TREE}" ]]; then
        echo "check-orphan-rc-branches: dev tree path does not exist: ${_CORB_DEV_TREE}" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _corb_read_active_rc <release_state_file>
# Echo the current Active RC value from release-state.md, or "none" if absent.
# ---------------------------------------------------------------------------
_corb_read_active_rc() {
    local rs_file="$1"
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
# _corb_tag_exists <dev_tree> <version>
# Returns 0 if a git tag exactly matching <version> exists in the dev tree.
# <version> is e.g. "v0.21.42".
# ---------------------------------------------------------------------------
_corb_tag_exists() {
    local dev_tree="$1"
    local version="$2"
    local result
    result="$(git -C "${dev_tree}" tag --list "${version}" 2>/dev/null)" || return 1
    [[ -n "${result}" ]]
}

# ---------------------------------------------------------------------------
# _corb_list_local_rc_branches <dev_tree>
# Echo one local rc/* branch name per line.
# Branch names are in full form: rc/vX.Y.Z
# ---------------------------------------------------------------------------
_corb_list_local_rc_branches() {
    local dev_tree="$1"
    git -C "${dev_tree}" branch --list 'rc/*' 2>/dev/null \
        | sed 's/^[[:space:]]*\*\?[[:space:]]*//'
}

# ---------------------------------------------------------------------------
# _corb_do_delete_branch
# Inner function invoked via overwatch_halt_first_fix for one orphan branch.
# Reads from environment:
#   _CORB_BRANCH_NAME — the rc/vX.Y.Z branch name
#   _CORB_VERSION     — the version string vX.Y.Z
#   _CORB_DRY_RUN     — "1" for dry-run, "0" for live (always 0 here since
#                       overwatch_halt_first_fix is not called in dry-run)
#
# Actions:
#   1. Delete the local branch: git branch -d <branch>
#   2. Delete the remote branch: git push origin --delete <branch>
#      (remote push only attempted if local delete succeeded)
#   3. Log each action via overwatch_log_action
# ---------------------------------------------------------------------------
_corb_do_delete_branch() {
    local branch="${_CORB_BRANCH_NAME}"
    local version="${_CORB_VERSION}"
    local dev_tree="${_CORB_DEV_TREE}"
    local local_del_ok=0
    local remote_del_ok=0

    # Step 1: Delete local branch
    echo "check-orphan-rc-branches: deleting local branch ${branch}" >&2
    local local_del_output
    if local_del_output="$(git -C "${dev_tree}" branch -d "${branch}" 2>&1)"; then
        local_del_ok=1
        echo "check-orphan-rc-branches: local branch deleted: ${branch}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "local-branch-deleted" \
            "none" \
            "Orphan RC branch deleted locally: tag ${version} exists, branch ${branch} removed" \
        || true
    else
        echo "check-orphan-rc-branches: local branch delete failed for ${branch}: ${local_del_output}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "local-branch-delete-failed" \
            "none" \
            "Failed to delete local branch ${branch}: ${local_del_output}" \
        || true
        # Do not attempt remote delete if local failed
        return 1
    fi

    # Step 2: Delete remote branch (only if local delete succeeded)
    echo "check-orphan-rc-branches: deleting remote branch origin/${branch}" >&2
    local remote_del_output
    if remote_del_output="$(git -C "${dev_tree}" push origin --delete "${branch}" 2>&1)"; then
        remote_del_ok=1
        echo "check-orphan-rc-branches: remote branch deleted: origin/${branch}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "remote-branch-deleted" \
            "none" \
            "Orphan RC branch deleted from origin: git push origin --delete ${branch}" \
        || true
    else
        echo "check-orphan-rc-branches: remote branch delete failed for origin/${branch}: ${remote_del_output}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "remote-branch-delete-failed" \
            "none" \
            "Failed to delete remote branch origin/${branch}: ${remote_del_output}" \
        || true
        # Local delete already succeeded; log warning but return 0 so the local
        # deletion is counted as a success — partial success is better than none.
        echo "check-orphan-rc-branches: WARNING: local branch ${branch} deleted but remote delete failed" >&2
    fi

    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_orphan_rc_branches [--dry-run]
# Main detection function. Scans local rc/* branches for orphans.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_orphan_rc_branches() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _corb_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local rs_file="${project_root}/release-state.md"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-orphan-rc-branches: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-orphan-rc-branches: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-orphan-rc-branches: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-orphan-rc-branches: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    # Read current Active RC — never delete the currently active RC branch
    local active_rc
    active_rc="$(_corb_read_active_rc "${rs_file}")"
    echo "check-orphan-rc-branches: Active RC from release-state.md: ${active_rc}" >&2

    # List all local rc/* branches
    local rc_branches
    rc_branches="$(_corb_list_local_rc_branches "${_CORB_DEV_TREE}")"

    if [[ -z "${rc_branches}" ]]; then
        echo "check-orphan-rc-branches: no local rc/* branches found" >&2
        return 0
    fi

    echo "check-orphan-rc-branches: scanning local rc/* branches for orphans" >&2

    local orphan_count=0
    local deleted_count=0
    local branch

    while IFS= read -r branch; do
        [[ -z "${branch}" ]] && continue

        # Extract version from branch name (rc/vX.Y.Z -> vX.Y.Z)
        local version="${branch#rc/}"
        if [[ "${version}" == "${branch}" ]]; then
            echo "check-orphan-rc-branches: skipping non-rc branch: ${branch}" >&2
            continue
        fi

        # Skip the currently active RC branch
        if [[ "${active_rc}" != "none" ]] && [[ "${version}" == "${active_rc}" ]]; then
            echo "check-orphan-rc-branches: skipping active RC branch: ${branch}" >&2
            continue
        fi

        # Check if a release tag exists for this version
        if ! _corb_tag_exists "${_CORB_DEV_TREE}" "${version}"; then
            echo "check-orphan-rc-branches: branch ${branch}: no tag ${version}; not an orphan" >&2
            continue
        fi

        orphan_count=$(( orphan_count + 1 ))
        echo "check-orphan-rc-branches: orphan confirmed: branch ${branch} has matching tag ${version}" >&2

        if (( dry_run == 1 )); then
            echo "check-orphan-rc-branches: [dry-run] would delete local branch ${branch} and remote origin/${branch}" >&2
            overwatch_log_action \
                "check-orphan-rc-branches" \
                "${branch}" \
                "dry-run-orphan-rc-branch-detected" \
                "none" \
                "Orphan RC branch detected: tag ${version} exists; dry-run, no deletion attempted" \
            2>/dev/null || true
            continue
        fi

        # Live mode: delete branch via overwatch_halt_first_fix
        export _CORB_BRANCH_NAME="${branch}"
        export _CORB_VERSION="${version}"
        export _CORB_DEV_TREE="${_CORB_DEV_TREE}"

        local fix_exit=0
        overwatch_halt_first_fix _corb_do_delete_branch || fix_exit=$?

        unset _CORB_BRANCH_NAME _CORB_VERSION

        if (( fix_exit == 3 )); then
            echo "check-orphan-rc-branches: HALT_OVERWATCH guard tripped; aborting" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-orphan-rc-branches: per-repo flock contended; aborting" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-orphan-rc-branches: delete failed for ${branch} (exit ${fix_exit})" >&2
            continue
        fi

        deleted_count=$(( deleted_count + 1 ))

    done <<< "${rc_branches}"

    echo "check-orphan-rc-branches: complete; found ${orphan_count} orphan(s), deleted ${deleted_count}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_orphan_rc_branches "$@"
    exit $?
fi
