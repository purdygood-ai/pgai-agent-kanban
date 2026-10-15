#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-orphan-rc-branches.sh
#
# OVERWATCH detection module: find and delete orphan RC branches — local and
# remote rc/* branches for which a corresponding release tag already exists.
#
# Branch names and tag names use the project's branch_prefix from project.cfg.
# With branch_prefix=ai_, orphan detection looks for ai_rc/vX.Y.Z branches and
# ai_vX.Y.Z tags.  With an empty prefix the bare names (rc/vX.Y.Z, vX.Y.Z) are
# used (existing behavior preserved).
#
# A <prefix>rc/vX.Y.Z branch is "orphan" if:
#   1. The local branch <prefix>rc/vX.Y.Z exists, AND
#   2. A git tag named <prefix>vX.Y.Z exists in the dev tree.
#
# An orphan RC branch indicates that the release completed (tag cut) but the
# RC branch was not cleaned up. CM is responsible for branch cleanup as part
# of the release flow, but when that step is missed, this check catches it.
#
# Proof before deletion: releases squash the RC onto main, so the RC tip is
# never an ancestor of main and `git branch -d` always refuses. Instead the
# branch's work is proven released through the tag: the RC tip's tree equals
# the tagged commit's tree, or the RC tip is an ancestor of the tag. Only a
# proven branch is deleted, with `git branch -D`.
#
# Corrective action (per proven branch):
#   1. Touch HALT via overwatch_halt_first_fix before any mutation.
#   2. Delete the local branch (git branch -D).
#   3. Delete the branch on the project's remote (pp_git_remote_name) only
#      when the shared push decision (pp_push_decision) answers "push"; with
#      push_to_remote = false, or an unresolved project, no remote is
#      contacted and the skip is logged.
#   4. Log each step via overwatch_log_action.
#
# A branch that cannot be proven released, or whose proven delete fails,
# files one bug report in the project's bugs/ and is recorded under
# overwatch/orphan-rc-reported/; later sweeps log it as already reported and
# make no further attempt while the branch tip is unchanged.
#
# Notes:
#   - The ACTIVE RC branch (the one currently being worked) is NOT deleted.
#     OVERWATCH resolves the active RC from release-state.md and skips it.
#   - Branch deletion goes through overwatch_halt_first_fix to honor HALT.
#   - In --dry-run, no branch is deleted, no remote is contacted and no bug
#     is filed.
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

    # Read branch_prefix from project config; default to empty (no prefix).
    # Strips surrounding double-quotes so that `branch_prefix = ""` is treated as empty.
    _CORB_BRANCH_PREFIX=""
    if [[ -n "${cfg_file}" ]]; then
        local _raw_prefix
        _raw_prefix="$(grep -E '^[[:space:]]*branch_prefix[[:space:]]*=' "${cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
        _CORB_BRANCH_PREFIX="${_raw_prefix:-}"
    fi

    return 0
}

# release_state_value reads Active RC through the one release-state reader.
# shellcheck source=../cm/common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../cm/common.sh"

# ---------------------------------------------------------------------------
# _corb_read_active_rc <release_state_file>
# Echo the current Active RC value from release-state.md as written, or
# "none" when the file, the heading or a value is missing.
# ---------------------------------------------------------------------------
_corb_read_active_rc() {
    release_state_value "$1" "Active RC"
}

# ---------------------------------------------------------------------------
# _corb_tag_exists <dev_tree> <version> [prefix]
# Returns 0 if a git tag matching <prefix><version> exists in the dev tree.
# <version> is the bare version e.g. "v0.21.42".
# <prefix> is the optional branch_prefix (e.g. "ai_"); default is empty.
# ---------------------------------------------------------------------------
_corb_tag_exists() {
    local dev_tree="$1"
    local version="$2"
    local prefix="${3:-}"
    local result
    result="$(git -C "${dev_tree}" tag --list "${prefix}${version}" 2>/dev/null)" || return 1
    [[ -n "${result}" ]]
}

# ---------------------------------------------------------------------------
# _corb_list_local_rc_branches <dev_tree> [prefix]
# Echo one local <prefix>rc/* branch name per line.
# Branch names are in full form: <prefix>rc/vX.Y.Z
# When prefix is empty, lists bare rc/* branches.
# ---------------------------------------------------------------------------
_corb_list_local_rc_branches() {
    local dev_tree="$1"
    local prefix="${2:-}"
    git -C "${dev_tree}" branch --list "${prefix}rc/*" 2>/dev/null \
        | sed 's/^[[:space:]]*\*\?[[:space:]]*//'
}

# ---------------------------------------------------------------------------
# _corb_release_proven <dev_tree> <branch> <tag>
# Returns 0 when <branch>'s work is on the release <tag>: the branch tip's
# tree is the tagged commit's tree (a squash release), or the tip is an
# ancestor of the tag (a merge release). Returns 1 otherwise.
# ---------------------------------------------------------------------------
_corb_release_proven() {
    local dev_tree="$1" branch="$2" tag="$3"
    local branch_tree tag_tree
    branch_tree="$(git -C "${dev_tree}" rev-parse --verify --quiet "refs/heads/${branch}^{tree}" 2>/dev/null)" || return 1
    tag_tree="$(git -C "${dev_tree}" rev-parse --verify --quiet "refs/tags/${tag}^{tree}" 2>/dev/null)" || return 1
    [[ -n "${branch_tree}" && "${branch_tree}" == "${tag_tree}" ]] && return 0
    git -C "${dev_tree}" merge-base --is-ancestor "refs/heads/${branch}" "refs/tags/${tag}" 2>/dev/null
}

# ---------------------------------------------------------------------------
# _corb_report_marker <project_root> <branch>
# Echo the path recording that <branch> was reported (one file per branch,
# '/' in the name written as '__').
# ---------------------------------------------------------------------------
_corb_report_marker() {
    local project_root="$1" branch="$2"
    printf '%s/overwatch/orphan-rc-reported/%s\n' "${project_root}" "${branch//\//__}"
}

# ---------------------------------------------------------------------------
# _corb_already_reported <project_root> <dev_tree> <branch>
# Returns 0 when <branch> was reported at its current tip (the marker's
# first line is that commit), 1 otherwise.
# ---------------------------------------------------------------------------
_corb_already_reported() {
    local project_root="$1" dev_tree="$2" branch="$3"
    local marker tip recorded=""
    marker="$(_corb_report_marker "${project_root}" "${branch}")"
    [[ -f "${marker}" ]] || return 1
    tip="$(git -C "${dev_tree}" rev-parse --verify --quiet "refs/heads/${branch}" 2>/dev/null)" || return 1
    IFS= read -r recorded < "${marker}" || true
    [[ "${recorded}" == "${tip}" ]]
}

# ---------------------------------------------------------------------------
# _corb_next_bug_number <bugs_dir>
# The next BUG-NNNN number after the highest in <bugs_dir>, zero-padded.
# ---------------------------------------------------------------------------
_corb_next_bug_number() {
    local bugs_dir="$1" highest=0 num f
    while IFS= read -r f; do
        num="$(basename "${f}" | grep -oE '^BUG-[0-9]+' | grep -oE '[0-9]+' | head -n1)"
        if [[ -n "${num}" ]] && (( 10#${num} > highest )); then
            highest=$(( 10#${num} ))
        fi
    done < <(find "${bugs_dir}" -maxdepth 1 -name 'BUG-[0-9]*' -type f 2>/dev/null)
    printf '%04d' $(( highest + 1 ))
}

# ---------------------------------------------------------------------------
# _corb_bug_text <seq> <branch> <tag> <reason>
# Print the bug report _corb_report_unresolved files.
# ---------------------------------------------------------------------------
_corb_bug_text() {
    local seq="$1" branch="$2" tag="$3" reason="$4"
    printf '%s\n' \
        "# BUG-${seq}: Orphan RC branch ${branch} not deleted" \
        "" \
        "**Severity:** low" \
        "" \
        "## Status" \
        "open" \
        "" \
        "## Category" \
        "bugfix" \
        "" \
        "## Symptom" \
        "The RC branch ${branch} still exists although its release tag ${tag} does; OVERWATCH did not delete it: ${reason}." \
        "" \
        "## Expected" \
        "An RC branch is removed once its release is tagged, and only when the tag provably holds its work." \
        "" \
        "## Detected At" \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "" \
        "## Detected By" \
        "check-orphan-rc-branches (OVERWATCH)"
}

# ---------------------------------------------------------------------------
# _corb_report_unresolved <project_root> <dev_tree> <branch> <tag> <reason>
# File one bug report for an orphan RC branch OVERWATCH will not delete, and
# record the branch tip so later sweeps do not retry it. A bug file is never
# overwritten: a taken number moves on to the next one.
# ---------------------------------------------------------------------------
_corb_report_unresolved() {
    local project_root="$1" dev_tree="$2" branch="$3" tag="$4" reason="$5"
    local bugs_dir="${project_root}/bugs"
    local marker tip bug_file="" seq attempt
    marker="$(_corb_report_marker "${project_root}" "${branch}")"
    tip="$(git -C "${dev_tree}" rev-parse --verify --quiet "refs/heads/${branch}" 2>/dev/null)" || tip=""
    if [[ -d "${bugs_dir}" ]]; then
        local slug="orphan-rc-branch-${branch//[^A-Za-z0-9]/-}"
        slug="${slug,,}"
        for attempt in 1 2 3 4 5; do
            seq="$(_corb_next_bug_number "${bugs_dir}")"
            bug_file="${bugs_dir}/BUG-${seq}-${slug}.md"
            if ( set -o noclobber; _corb_bug_text "${seq}" "${branch}" "${tag}" "${reason}" > "${bug_file}" ) 2>/dev/null; then
                break
            fi
            bug_file=""
        done
    fi
    mkdir -p "$(dirname "${marker}")" 2>/dev/null || true
    printf '%s\n%s\n' "${tip}" "${bug_file:-no bug filed (bugs/ missing or unwritable)}" > "${marker}" 2>/dev/null || true
    echo "check-orphan-rc-branches: ${branch} left in place (${reason}); reported once${bug_file:+ in ${bug_file}}" >&2
    overwatch_log_action \
        "check-orphan-rc-branches" \
        "${branch}" \
        "orphan-rc-branch-reported" \
        "none" \
        "Orphan RC branch ${branch} not deleted: ${reason}; ${bug_file:+bug filed: ${bug_file}; }no further attempts while its tip is unchanged" \
    2>/dev/null || true
}

# ---------------------------------------------------------------------------
# _corb_do_delete_branch
# Inner function invoked via overwatch_halt_first_fix for one proven orphan
# branch. Reads from environment:
#   _CORB_BRANCH_NAME — the <prefix>rc/vX.Y.Z branch name
#   _CORB_VERSION     — the version string vX.Y.Z
#   _CORB_PUSH        — "push" when the shared push decision allows the
#                       remote delete; anything else skips it
#   _CORB_REMOTE      — the project's remote name
#
# Actions:
#   1. Delete the local branch: git branch -D <branch> (its work is proven
#      on the release tag; a squash release never makes it an ancestor of
#      main, so -d would always refuse)
#   2. Delete the branch on the remote, only when _CORB_PUSH is "push" and
#      the local delete succeeded
#   3. Log each action via overwatch_log_action
# Sets _CORB_DELETE_ERROR to git's message when the local delete fails.
# ---------------------------------------------------------------------------
_corb_do_delete_branch() {
    local branch="${_CORB_BRANCH_NAME}"
    local version="${_CORB_VERSION}"
    local dev_tree="${_CORB_DEV_TREE}"
    local remote="${_CORB_REMOTE:-}"
    _CORB_DELETE_ERROR=""

    # Step 1: Delete local branch
    echo "check-orphan-rc-branches: deleting local branch ${branch}" >&2
    local local_del_output
    if local_del_output="$(git -C "${dev_tree}" branch -D "${branch}" 2>&1)"; then
        echo "check-orphan-rc-branches: local branch deleted: ${branch}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "local-branch-deleted" \
            "none" \
            "Orphan RC branch deleted locally: release tag for ${version} exists; branch ${branch} removed" \
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
        _CORB_DELETE_ERROR="${local_del_output}"
        # Do not attempt remote delete if local failed
        return 1
    fi

    # Step 2: Delete the remote branch (only if local delete succeeded, and
    # only for a project whose push decision is "push").
    if [[ "${_CORB_PUSH:-}" != "push" || -z "${remote}" ]]; then
        echo "check-orphan-rc-branches: push_to_remote is not in effect for this project; ${remote:-the remote} not contacted" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "remote-branch-delete-skipped" \
            "none" \
            "Remote delete of ${branch} skipped: push decision '${_CORB_PUSH:-unresolved}' (OVERWATCH contacts a remote only when the project pushes)" \
        || true
        return 0
    fi
    echo "check-orphan-rc-branches: deleting remote branch ${remote}/${branch}" >&2
    local remote_del_output
    if remote_del_output="$(git -C "${dev_tree}" push "${remote}" --delete "${branch}" 2>&1)"; then
        echo "check-orphan-rc-branches: remote branch deleted: ${remote}/${branch}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "remote-branch-deleted" \
            "none" \
            "Orphan RC branch deleted from ${remote}: git push ${remote} --delete ${branch}" \
        || true
    else
        echo "check-orphan-rc-branches: remote branch delete failed for ${remote}/${branch}: ${remote_del_output}" >&2
        overwatch_log_action \
            "check-orphan-rc-branches" \
            "${branch}" \
            "remote-branch-delete-failed" \
            "none" \
            "Failed to delete remote branch ${remote}/${branch}: ${remote_del_output}" \
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

    local branch_prefix="${_CORB_BRANCH_PREFIX:-}"
    echo "check-orphan-rc-branches: project=${project_name} branch_prefix='${branch_prefix}'" >&2

    # Whether the remote delete may run: the shared push decision, and the
    # project's own remote name. Neither is consulted for a dry run.
    local push_decision="unresolved" remote=""
    if (( dry_run == 0 )); then
        local _corb_lib
        _corb_lib="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)"
        if ! declare -F pp_git_remote_name >/dev/null 2>&1 && [[ -f "${_corb_lib}/project_paths.sh" ]]; then
            # shellcheck source=../project_paths.sh
            source "${_corb_lib}/project_paths.sh"
        fi
        if ! declare -F pp_push_decision >/dev/null 2>&1 && [[ -f "${_corb_lib}/push_decision.sh" ]]; then
            # shellcheck source=../push_decision.sh
            source "${_corb_lib}/push_decision.sh"
        fi
        if declare -F pp_push_decision >/dev/null 2>&1; then
            pp_push_decision "${project_name}"
            push_decision="${PP_PUSH_DECISION}"
            [[ "${push_decision}" == "unresolved" ]] && echo "check-orphan-rc-branches: ${PP_PUSH_STATUS_LINE}" >&2
        fi
        if [[ "${push_decision}" == "push" ]] && declare -F pp_git_remote_name >/dev/null 2>&1; then
            remote="$(pp_git_remote_name --quiet "${project_name}" 2>/dev/null)" || remote=""
        fi
        echo "check-orphan-rc-branches: push decision: ${push_decision}${remote:+ (remote ${remote})}" >&2
    fi

    # Read current Active RC — never delete the currently active RC branch
    local active_rc
    active_rc="$(_corb_read_active_rc "${rs_file}")"
    echo "check-orphan-rc-branches: Active RC from release-state.md: ${active_rc}" >&2

    # List all local <prefix>rc/* branches
    local rc_branches
    rc_branches="$(_corb_list_local_rc_branches "${_CORB_DEV_TREE}" "${branch_prefix}")"

    if [[ -z "${rc_branches}" ]]; then
        echo "check-orphan-rc-branches: no local ${branch_prefix}rc/* branches found" >&2
        return 0
    fi

    echo "check-orphan-rc-branches: scanning local ${branch_prefix}rc/* branches for orphans" >&2

    # rc_prefix is the full prefix up to the version: e.g. "ai_rc/" or "rc/"
    local rc_prefix="${branch_prefix}rc/"

    local orphan_count=0
    local deleted_count=0
    local branch

    while IFS= read -r branch; do
        [[ -z "${branch}" ]] && continue

        # Extract version from branch name (<prefix>rc/vX.Y.Z -> vX.Y.Z)
        local version="${branch#"${rc_prefix}"}"
        if [[ "${version}" == "${branch}" ]]; then
            echo "check-orphan-rc-branches: skipping non-rc branch: ${branch}" >&2
            continue
        fi

        # Skip the currently active RC branch
        if [[ "${active_rc}" != "none" ]] && [[ "${version}" == "${active_rc}" ]]; then
            echo "check-orphan-rc-branches: skipping active RC branch: ${branch}" >&2
            continue
        fi

        # Check if a release tag exists for this version (with prefix applied)
        if ! _corb_tag_exists "${_CORB_DEV_TREE}" "${version}" "${branch_prefix}"; then
            local prefixed_tag="${branch_prefix}${version}"
            echo "check-orphan-rc-branches: branch ${branch}: no tag ${prefixed_tag}; not an orphan" >&2
            continue
        fi

        orphan_count=$(( orphan_count + 1 ))
        local prefixed_tag_for_log="${branch_prefix}${version}"
        echo "check-orphan-rc-branches: orphan confirmed: branch ${branch} has matching tag ${prefixed_tag_for_log}" >&2

        if _corb_already_reported "${project_root}" "${_CORB_DEV_TREE}" "${branch}"; then
            echo "check-orphan-rc-branches: ${branch} already reported at its current tip; not retried" >&2
            continue
        fi

        if ! _corb_release_proven "${_CORB_DEV_TREE}" "${branch}" "${prefixed_tag_for_log}"; then
            local unproven_reason="its tip's tree differs from ${prefixed_tag_for_log} and it is not an ancestor of the tag, so its work cannot be proven released"
            if (( dry_run == 1 )); then
                echo "check-orphan-rc-branches: [dry-run] would report ${branch}: ${unproven_reason}" >&2
                continue
            fi
            _corb_report_unresolved "${project_root}" "${_CORB_DEV_TREE}" "${branch}" "${prefixed_tag_for_log}" "${unproven_reason}"
            continue
        fi

        if (( dry_run == 1 )); then
            echo "check-orphan-rc-branches: [dry-run] would delete local branch ${branch} (and the remote one when the project pushes)" >&2
            overwatch_log_action \
                "check-orphan-rc-branches" \
                "${branch}" \
                "dry-run-orphan-rc-branch-detected" \
                "none" \
                "Orphan RC branch detected: tag ${prefixed_tag_for_log} exists; dry-run, no deletion attempted" \
            2>/dev/null || true
            continue
        fi

        # Live mode: delete branch via overwatch_halt_first_fix
        export _CORB_BRANCH_NAME="${branch}"
        export _CORB_VERSION="${version}"
        export _CORB_DEV_TREE="${_CORB_DEV_TREE}"
        export _CORB_PUSH="${push_decision}"
        export _CORB_REMOTE="${remote}"
        _CORB_DELETE_ERROR=""

        local fix_exit=0
        overwatch_halt_first_fix _corb_do_delete_branch || fix_exit=$?

        unset _CORB_BRANCH_NAME _CORB_VERSION _CORB_PUSH _CORB_REMOTE

        if (( fix_exit == 3 )); then
            echo "check-orphan-rc-branches: HALT_OVERWATCH guard tripped; aborting" >&2
            return 0
        elif (( fix_exit == 4 )); then
            echo "check-orphan-rc-branches: per-repo flock contended; aborting" >&2
            return 0
        elif (( fix_exit == 5 )); then
            echo "check-orphan-rc-branches: fix deferred: HALT present; aborting" >&2
            return 0
        elif (( fix_exit != 0 )); then
            echo "check-orphan-rc-branches: delete failed for ${branch} (exit ${fix_exit})" >&2
            _corb_report_unresolved "${project_root}" "${_CORB_DEV_TREE}" "${branch}" "${prefixed_tag_for_log}" \
                "deleting it failed: ${_CORB_DELETE_ERROR:-exit ${fix_exit}}"
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
