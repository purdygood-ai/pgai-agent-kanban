#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-push-lag.sh
#
# OVERWATCH detection module: detect and correct push lag on the dev tree's
# main branch and tag refs that have not yet been propagated to origin.
#
# Push lag is defined as:
#   - The local main branch is ahead of origin/main by at least one commit, OR
#   - One or more local tags do not exist on origin.
#
# When push lag is detected, the check first reads push_to_remote from the
# project config (project.cfg [project] push_to_remote).  When push_to_remote
# is false the project is in local-only mode: local-ahead state is by design,
# the action log receives a 'staged-by-design' entry, and no push is attempted.
# When push_to_remote is true (the default) and no CM agent flock is contended,
# OVERWATCH pushes via overwatch_halt_first_fix to ensure origin stays in sync.
#
# The per-repo flock ($KANBAN_ROOT/locks/repo-wake-pgai-kanban.lock) is the
# proxy for "a CM or other agent is actively running." If that lock is held,
# OVERWATCH backs off and lets the live agent finish before touching origin.
#
# This script is both:
#   - Sourceable (for the OVERWATCH driver): source this file and call
#     overwatch_check_push_lag [--dry-run]
#   - Directly invokable: bash check-push-lag.sh [--dry-run]
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
#   bash check-push-lag.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no lag, or lag corrected without error)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT push to origin.

# ---------------------------------------------------------------------------
# _cpl_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and the dev tree path from the env.
# Sets _CPL_DEV_TREE in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cpl_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-push-lag: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
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
            echo "check-push-lag: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
            echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
            echo "  Registered projects: $(echo "${_all_projects}" | tr '\n' ' ')" >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-push-lag: ERROR: no project specified and none resolvable from projects.cfg" >&2
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
    _CPL_DEV_TREE=""
    if [[ -n "${cfg_file}" ]]; then
        _CPL_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CPL_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CPL_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi
    if [[ -z "${_CPL_DEV_TREE}" ]]; then
        echo "check-push-lag: cannot resolve dev tree path (set PGAI_DEV_TREE_PATH or ensure project.cfg has dev_tree_path)" >&2
        return 1
    fi
    if [[ ! -d "${_CPL_DEV_TREE}" ]]; then
        echo "check-push-lag: dev tree path does not exist: ${_CPL_DEV_TREE}" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _cpl_read_push_to_remote
# Read the push_to_remote value from the project config file.
# Echoes 'true' when CM should push to origin; echoes 'false' when the
# project is configured for local-only mode.
#
# Reads from:
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/project.cfg  (preferred)
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/PROJECT.cfg  (legacy fallback)
#
# Default when absent or empty: 'true' (preserves existing behavior).
# Only the exact string 'false' opts out of remote pushes.
# ---------------------------------------------------------------------------
_cpl_read_push_to_remote() {
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        cfg_file="${_proj_root}/PROJECT.cfg"
    fi

    if [[ -z "${cfg_file}" ]]; then
        echo "true"
        return 0
    fi

    local raw
    raw="$(grep -E '^[[:space:]]*push_to_remote[[:space:]]*=' "${cfg_file}" \
        | head -n1 \
        | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"

    # Only the exact string 'false' opts out; everything else defaults to 'true'.
    if [[ "${raw}" == "false" ]]; then
        echo "false"
    else
        echo "true"
    fi
}

# ---------------------------------------------------------------------------
# _cpl_main_commits_ahead <dev_tree>
# Echo the number of commits that local main is ahead of origin/main.
# Echoes 0 if origin/main does not exist or local is not ahead.
# Returns 1 on git error.
# ---------------------------------------------------------------------------
_cpl_main_commits_ahead() {
    local dev_tree="$1"

    # Verify local main branch exists
    local local_main
    local_main="$(git -C "${dev_tree}" branch --list 'main' 2>/dev/null | sed 's/^[[:space:]]*\*\?[[:space:]]*//')"
    if [[ -z "${local_main}" ]]; then
        echo "0"
        return 0
    fi

    # Verify origin/main exists (it may not in a fresh clone without fetch)
    if ! git -C "${dev_tree}" rev-parse --verify 'origin/main' >/dev/null 2>&1; then
        echo "0"
        return 0
    fi

    local ahead
    ahead="$(git -C "${dev_tree}" rev-list --count 'origin/main..main' 2>/dev/null)" || {
        echo "0"
        return 0
    }
    echo "${ahead}"
}

# ---------------------------------------------------------------------------
# _cpl_unpushed_tags <dev_tree>
# Echo one tag name per line for each local tag that does not exist on origin.
# Echoes nothing if all local tags exist on origin.
# ---------------------------------------------------------------------------
_cpl_unpushed_tags() {
    local dev_tree="$1"

    # Get all local tags
    local local_tags
    local_tags="$(git -C "${dev_tree}" tag --list 2>/dev/null)" || return 0

    if [[ -z "${local_tags}" ]]; then
        return 0
    fi

    # Get all remote tags (refs/tags/*)
    local remote_tags
    remote_tags="$(git -C "${dev_tree}" ls-remote --tags origin 2>/dev/null \
        | awk '{print $2}' \
        | grep -v '\^{}' \
        | sed 's|refs/tags/||')" || remote_tags=""

    # Emit local tags not present on origin
    local tag
    while IFS= read -r tag; do
        [[ -z "${tag}" ]] && continue
        if ! echo "${remote_tags}" | grep -qxF "${tag}" 2>/dev/null; then
            echo "${tag}"
        fi
    done <<< "${local_tags}"
}

# ---------------------------------------------------------------------------
# _cpl_do_push
# Inner function invoked via overwatch_halt_first_fix.
# Reads from environment:
#   _CPL_DEV_TREE       — dev tree path
#   _CPL_PUSH_MAIN      — "1" if main needs pushing, "0" otherwise
#   _CPL_UNPUSHED_TAGS  — newline-separated list of tags to push (may be empty)
#
# Actions:
#   1. Push main to origin if _CPL_PUSH_MAIN == 1
#   2. Push each tag in _CPL_UNPUSHED_TAGS to origin
#   3. Log each successful push via overwatch_log_action
# ---------------------------------------------------------------------------
_cpl_do_push() {
    local dev_tree="${_CPL_DEV_TREE}"
    local push_main="${_CPL_PUSH_MAIN:-0}"
    local unpushed_tags="${_CPL_UNPUSHED_TAGS:-}"
    local push_errors=0

    # Push main branch
    if [[ "${push_main}" == "1" ]]; then
        echo "check-push-lag: pushing main to origin" >&2
        local push_output
        if push_output="$(git -C "${dev_tree}" push origin main 2>&1)"; then
            echo "check-push-lag: pushed main to origin: ${push_output}" >&2
            overwatch_log_action \
                "check-push-lag" \
                "main" \
                "pushed-main-to-origin" \
                "none" \
                "Local main was ahead of origin/main; pushed to origin" \
            || true
        else
            echo "check-push-lag: push main failed: ${push_output}" >&2
            overwatch_log_action \
                "check-push-lag" \
                "main" \
                "push-main-failed" \
                "none" \
                "Failed to push main to origin: ${push_output}" \
            || true
            push_errors=$(( push_errors + 1 ))
        fi
    fi

    # Push unpushed tags
    if [[ -n "${unpushed_tags}" ]]; then
        local tag
        while IFS= read -r tag; do
            [[ -z "${tag}" ]] && continue
            echo "check-push-lag: pushing tag ${tag} to origin" >&2
            local tag_push_output
            if tag_push_output="$(git -C "${dev_tree}" push origin "refs/tags/${tag}" 2>&1)"; then
                echo "check-push-lag: pushed tag ${tag} to origin: ${tag_push_output}" >&2
                overwatch_log_action \
                    "check-push-lag" \
                    "${tag}" \
                    "pushed-tag-to-origin" \
                    "none" \
                    "Local tag ${tag} was absent from origin; pushed" \
                || true
            else
                echo "check-push-lag: push tag ${tag} failed: ${tag_push_output}" >&2
                overwatch_log_action \
                    "check-push-lag" \
                    "${tag}" \
                    "push-tag-failed" \
                    "none" \
                    "Failed to push tag ${tag} to origin: ${tag_push_output}" \
                || true
                push_errors=$(( push_errors + 1 ))
            fi
        done <<< "${unpushed_tags}"
    fi

    if (( push_errors > 0 )); then
        echo "check-push-lag: ${push_errors} push error(s) encountered" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_push_lag [--dry-run]
# Main detection function.
# Returns 0 on success, 1 on internal error.
# ---------------------------------------------------------------------------
overwatch_check_push_lag() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _cpl_resolve_env || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-push-lag: project root does not exist: ${project_root}" >&2
        return 1
    fi

    # Source overwatch_protocol.sh if not already loaded
    if ! declare -f overwatch_log_action >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
            echo "check-push-lag: cannot resolve lib dir" >&2
            return 1
        }
        if [[ -f "${lib_dir}/../overwatch_protocol.sh" ]]; then
            # shellcheck source=/dev/null
            source "${lib_dir}/../overwatch_protocol.sh"
        else
            echo "check-push-lag: overwatch_protocol.sh not found relative to ${lib_dir}" >&2
            return 1
        fi
    fi

    if (( dry_run == 0 )); then
        # Verify overwatch state dir exists for live mode
        local state_dir="${KANBAN_ROOT}/projects/${project_name}/overwatch"
        if [[ ! -d "${state_dir}" ]]; then
            echo "check-push-lag: overwatch state dir missing: ${state_dir}" >&2
            return 1
        fi
    fi

    local dev_tree="${_CPL_DEV_TREE}"

    # Detect main branch lag
    local ahead
    ahead="$(_cpl_main_commits_ahead "${dev_tree}")"

    echo "check-push-lag: local main is ${ahead} commit(s) ahead of origin/main" >&2

    # Detect unpushed tags
    local unpushed_tags=""
    local tag_count=0
    if (( dry_run == 0 )); then
        # Only fetch tag info from origin in live mode; dry-run skips network calls
        # that would imply state knowledge we don't act on anyway.
        unpushed_tags="$(_cpl_unpushed_tags "${dev_tree}")"
    else
        # In dry-run, still detect unpushed tags via ls-remote to report accurately.
        unpushed_tags="$(_cpl_unpushed_tags "${dev_tree}")" 2>/dev/null || unpushed_tags=""
    fi

    if [[ -n "${unpushed_tags}" ]]; then
        tag_count="$(echo "${unpushed_tags}" | grep -c '.' 2>/dev/null || echo 0)"
        echo "check-push-lag: ${tag_count} unpushed local tag(s) detected" >&2
        local tag
        while IFS= read -r tag; do
            [[ -z "${tag}" ]] && continue
            echo "check-push-lag:   unpushed tag: ${tag}" >&2
        done <<< "${unpushed_tags}"
    else
        echo "check-push-lag: no unpushed tags detected" >&2
    fi

    # Nothing to do?
    local push_main=0
    if (( ahead > 0 )); then
        push_main=1
    fi

    if (( push_main == 0 && tag_count == 0 )); then
        echo "check-push-lag: local matches origin; no push needed" >&2
        return 0
    fi

    echo "check-push-lag: push lag detected (main ahead=${ahead}, unpushed tags=${tag_count})" >&2

    # Read push_to_remote from project config.
    # When false, the local-ahead state is by design (local-only mode): log
    # 'staged-by-design' and exit without pushing.  This is the gate that
    # prevents OVERWATCH from force-pushing deliberately staged local work to
    # the remote repository on installs configured for operator-controlled pushes.
    local push_to_remote
    push_to_remote="$(_cpl_read_push_to_remote)"
    if [[ "${push_to_remote}" == "false" ]]; then
        echo "check-push-lag: push_to_remote=false; local-ahead state is by design; skipping push" >&2
        overwatch_log_action \
            "check-push-lag" \
            "${dev_tree}" \
            "staged-by-design" \
            "none" \
            "push_to_remote=false for project ${project_name}; local-ahead state is by design (ahead=${ahead}, unpushed_tags=${tag_count}); no push attempted" \
        2>/dev/null || true
        return 0
    fi

    if (( dry_run == 1 )); then
        echo "check-push-lag: [dry-run] would push main=${push_main} tag_count=${tag_count} to origin" >&2
        overwatch_log_action \
            "check-push-lag" \
            "${dev_tree}" \
            "dry-run-push-lag-detected" \
            "none" \
            "Push lag: main ahead=${ahead}, unpushed tags=${tag_count}; dry-run, no action taken" \
        2>/dev/null || true
        return 0
    fi

    # Live mode: push via overwatch_halt_first_fix
    export _CPL_DEV_TREE="${dev_tree}"
    export _CPL_PUSH_MAIN="${push_main}"
    export _CPL_UNPUSHED_TAGS="${unpushed_tags}"

    local fix_exit=0
    overwatch_halt_first_fix _cpl_do_push || fix_exit=$?

    unset _CPL_PUSH_MAIN _CPL_UNPUSHED_TAGS
    # Note: _CPL_DEV_TREE is kept (set by _cpl_resolve_env) — do not unset here

    if (( fix_exit == 3 )); then
        echo "check-push-lag: HALT_OVERWATCH guard tripped; aborting push" >&2
        return 0
    elif (( fix_exit == 4 )); then
        echo "check-push-lag: per-repo flock contended; aborting push (CM agent likely running)" >&2
        return 0
    elif (( fix_exit != 0 )); then
        echo "check-push-lag: push operation failed (exit ${fix_exit})" >&2
        return 1
    fi

    echo "check-push-lag: push complete (main ahead=${ahead} fixed, tags=${tag_count} pushed)" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# When this script is executed directly (not sourced), call the main function.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_push_lag "$@"
    exit $?
fi
