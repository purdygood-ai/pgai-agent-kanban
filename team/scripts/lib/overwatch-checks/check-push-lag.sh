#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-push-lag.sh
#
# OVERWATCH detection module: detect and correct push lag on the project's
# own main branch and release tags that have not yet been propagated to its
# remote: project.cfg [project] git_remote_name, origin when absent or blank
# (pp_git_remote_name). Every ref, ls-remote and push below names that
# remote; "origin" in this header means it. A git_remote_name that is not a
# valid remote name skips the check this time, with one line naming the
# key, rather than guessing a destination.
#
# The project's own refs are the ones the release cuts, read through the
# shared release_refs.sh (the same rule CM's push-watchdog.sh applies): its
# main branch is <branch_prefix>main and its release tags are
# <branch_prefix>vX.Y.Z (ai_main and ai_v1.2.3 with branch_prefix = ai_; main
# and v1.2.3 with none). In a repository shared with another lane, that
# lane's main and tags are never compared and never pushed. A branch_prefix
# that cannot be read skips the check this time rather than guessing.
#
# Push lag is defined as:
#   - The local <prefix>main is ahead of origin/<prefix>main by at least one
#     commit, OR
#   - One or more local <prefix>vX.Y.Z release tags do not exist on origin.
#
# Before any git call the check asks the shared push-decision resolver
# (pp_push_decision, team/scripts/lib/push_decision.sh) about the project.
# The resolver answers from the project's own registration and config only:
#
#   unresolved — the project is not registered in projects.cfg, has no
#                project.cfg (or legacy PROJECT.cfg), or that file names no
#                dev_tree_path. The check prints one line with the status
#                'skipped-unresolved-project' naming the missing piece, runs
#                no git command at all (no ls-remote, no push), and exits 0.
#   no-push    — push_to_remote = false: when lag is found, local-ahead state
#                is by design, the action log receives a 'staged-by-design'
#                entry, and no push is attempted.
#   push       — the resolver's answer when push_to_remote is true or absent.
#                This check pushes only on an explicit push_to_remote = true
#                in the project's config: an absent (or any other) value is
#                treated like false -- 'staged-by-design' is logged naming the
#                setting, and nothing is pushed. With push_to_remote = true,
#                when lag is found and no CM agent flock is contended,
#                OVERWATCH pushes via overwatch_halt_first_fix to keep origin
#                in sync, each ref by its explicit name
#                (refs/heads/<prefix>main, refs/tags/<tag>).
#
# The per-repo flock ($KANBAN_ROOT/locks/repo-wake-<project-repo-id>.lock,
# resolved via pp_repo_lock_path in project_paths.sh) is the proxy for "a CM
# or other agent is actively running." If that lock is held, OVERWATCH backs
# off and lets the live agent finish before touching origin.
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
# The dev tree is the resolver's: dev_tree_path from the project's own
# config and nowhere else:
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/project.cfg (preferred)
#   $KANBAN_ROOT/projects/$OVERWATCH_PROJECT/PROJECT.cfg (legacy fallback)
# PGAI_DEV_TREE_PATH is never consulted -- it names one global checkout, and
# pushing that checkout's tags under another project's push_to_remote
# setting would publish the wrong repository's refs. A sandbox root built by
# a test, a no-git project, or a half-built config therefore resolves to
# 'unresolved' and the check declines without contacting any remote.
#
# When invoked directly, KANBAN_ROOT defaults to:
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}
# OVERWATCH_PROJECT defaults to "pgai-agent-kanban".
#
# Usage:
#   bash check-push-lag.sh [--dry-run]
#
# Exit codes:
#   0 — completed successfully (no lag, lag corrected without error, or the
#       project did not resolve: skipped-unresolved-project)
#   1 — internal error (missing dependencies, unreadable state dir, etc.)
#
# --dry-run: scans and logs findings but does NOT push to origin.

# ---------------------------------------------------------------------------
# _cpl_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and the push decision.
# Sets in the calling scope:
#   _CPL_DECISION     push | no-push | unresolved (from pp_push_decision)
#   _CPL_DEV_TREE     the project config's dev_tree_path; empty unless resolved
#   _CPL_CFG_FILE     the project config file read ("" when none)
#   _CPL_SKIP_LINE    the skipped-unresolved-project line when unresolved
#   _CPL_PUSH_SETTING "true" only for an explicit push_to_remote = true;
#                     otherwise the value read ("false", "unset", ...), and
#                     _CPL_DECISION is then no-push
#   _CPL_REMOTE       the project's git remote (pp_git_remote_name --quiet);
#                     empty when its git_remote_name is invalid
# Returns 0 on success (an unresolved project included -- the caller
# declines), 1 on failure.
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

    # Ask the shared resolver. It reads the project's registration and its
    # own config only; an inherited PGAI_DEV_TREE_PATH plays no part.
    if ! declare -F pp_push_decision >/dev/null 2>&1; then
        local _cpl_lib_dir
        _cpl_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)" || {
            echo "check-push-lag: cannot resolve lib dir" >&2
            return 1
        }
        if [[ ! -f "${_cpl_lib_dir}/push_decision.sh" ]]; then
            echo "check-push-lag: push_decision.sh not found in ${_cpl_lib_dir}" >&2
            return 1
        fi
        # shellcheck source=../push_decision.sh
        source "${_cpl_lib_dir}/push_decision.sh"
    fi
    if ! declare -F release_refs_local_release_tags >/dev/null 2>&1; then
        local _cpl_refs_dir
        _cpl_refs_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)" || return 1
        if [[ ! -f "${_cpl_refs_dir}/release_refs.sh" ]]; then
            echo "check-push-lag: release_refs.sh not found in ${_cpl_refs_dir}" >&2
            return 1
        fi
        # shellcheck source=../release_refs.sh
        source "${_cpl_refs_dir}/release_refs.sh"
    fi
    pp_push_decision "${OVERWATCH_PROJECT}"
    _CPL_DECISION="${PP_PUSH_DECISION}"
    _CPL_CFG_FILE="${PP_PUSH_CFG_FILE}"
    _CPL_SKIP_LINE="${PP_PUSH_STATUS_LINE}"
    _CPL_DEV_TREE=""
    if [[ "${_CPL_DECISION}" != "push" && "${_CPL_DECISION}" != "no-push" ]]; then
        return 0
    fi
    _CPL_DEV_TREE="${PP_PUSH_DEV_TREE}"
    _CPL_REMOTE="$(pp_git_remote_name --quiet "${OVERWATCH_PROJECT}" 2>/dev/null)" || _CPL_REMOTE=""
    if [[ ! -d "${_CPL_DEV_TREE}" ]]; then
        echo "check-push-lag: dev tree path does not exist: ${_CPL_DEV_TREE}" >&2
        return 1
    fi

    # This check pushes only on an explicit push_to_remote = true (see the
    # header); the resolver also answers "push" for an absent key.
    _CPL_PUSH_SETTING="false"
    if [[ "${_CPL_DECISION}" == "push" ]]; then
        local _cpl_raw
        _cpl_raw="$(_pp_read_cfg_key "${_CPL_CFG_FILE}" project push_to_remote "")"
        _cpl_raw="${_cpl_raw//\"/}"
        _cpl_raw="$(tr '[:upper:]' '[:lower:]' <<< "${_cpl_raw}" | tr -d '[:space:]')"
        if [[ "${_cpl_raw}" == "true" ]]; then
            _CPL_PUSH_SETTING="true"
        else
            _CPL_PUSH_SETTING="${_cpl_raw:-unset}"
            _CPL_DECISION="no-push"
        fi
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _cpl_main_commits_ahead <dev_tree> <main_branch> <remote>
# Echo the number of commits that local <main_branch> is ahead of
# <remote>/<main_branch>. Echoes 0 if either ref does not exist or local is
# not ahead.
# ---------------------------------------------------------------------------
_cpl_main_commits_ahead() {
    local dev_tree="$1" main_branch="$2" remote="$3"

    # Verify the local branch exists
    if ! git -C "${dev_tree}" rev-parse --verify --quiet "refs/heads/${main_branch}" >/dev/null 2>&1; then
        echo "0"
        return 0
    fi

    # Verify <remote>/<main_branch> exists (it may not in a fresh clone without fetch)
    if ! git -C "${dev_tree}" rev-parse --verify --quiet "refs/remotes/${remote}/${main_branch}" >/dev/null 2>&1; then
        echo "0"
        return 0
    fi

    local ahead
    ahead="$(git -C "${dev_tree}" rev-list --count "refs/remotes/${remote}/${main_branch}..refs/heads/${main_branch}" 2>/dev/null)" || {
        echo "0"
        return 0
    }
    echo "${ahead}"
}

# ---------------------------------------------------------------------------
# _cpl_unpushed_tags <dev_tree> <tag_prefix> <remote>
# Echo one tag name per line for each of the project's local release tags
# (<tag_prefix>vX.Y.Z, release_refs_local_release_tags) that does not exist
# on <remote>. Echoes nothing if all of them exist there.
# ---------------------------------------------------------------------------
_cpl_unpushed_tags() {
    local dev_tree="$1" tag_prefix="$2" remote="$3"

    # The project's own local release tags only
    local local_tags
    local_tags="$(release_refs_local_release_tags "${dev_tree}" "${tag_prefix}")"

    if [[ -z "${local_tags}" ]]; then
        return 0
    fi

    # Get all remote tags (refs/tags/*)
    local remote_tags
    remote_tags="$(git -C "${dev_tree}" ls-remote --tags "${remote}" 2>/dev/null \
        | awk '{print $2}' \
        | grep -v '\^{}' \
        | sed 's|refs/tags/||')" || remote_tags=""

    # Emit local tags not present on the remote
    local tag
    while IFS= read -r tag; do
        [[ -z "${tag}" ]] && continue
        if ! grep -qxF "${tag}" <<< "${remote_tags}" 2>/dev/null; then
            echo "${tag}"
        fi
    done <<< "${local_tags}"
}

# ---------------------------------------------------------------------------
# _cpl_do_push
# Inner function invoked via overwatch_halt_first_fix.
# Reads from environment:
#   _CPL_DEV_TREE       — dev tree path
#   _CPL_REMOTE         — the project's git remote (pp_git_remote_name)
#   _CPL_MAIN_BRANCH    — the project's main branch (<branch_prefix>main)
#   _CPL_PUSH_MAIN      — "1" if that branch needs pushing, "0" otherwise
#   _CPL_UNPUSHED_TAGS  — newline-separated list of release tags to push (may be empty)
#
# Actions:
#   1. Push refs/heads/<main_branch> to the remote if _CPL_PUSH_MAIN == 1
#   2. Push each tag in _CPL_UNPUSHED_TAGS to the remote by refs/tags/<tag>
#   3. Log each successful push via overwatch_log_action
# ---------------------------------------------------------------------------
_cpl_do_push() {
    local dev_tree="${_CPL_DEV_TREE}"
    local remote="${_CPL_REMOTE:?_CPL_REMOTE must name the project remote}"
    local main_branch="${_CPL_MAIN_BRANCH:-main}"
    local push_main="${_CPL_PUSH_MAIN:-0}"
    local unpushed_tags="${_CPL_UNPUSHED_TAGS:-}"
    local push_errors=0

    # Push the project's main branch, by its explicit ref
    if [[ "${push_main}" == "1" ]]; then
        echo "check-push-lag: pushing ${main_branch} to ${remote}" >&2
        local push_output
        if push_output="$(git -C "${dev_tree}" push "${remote}" "refs/heads/${main_branch}:refs/heads/${main_branch}" 2>&1)"; then
            echo "check-push-lag: pushed ${main_branch} to ${remote}: ${push_output}" >&2
            overwatch_log_action \
                "check-push-lag" \
                "${main_branch}" \
                "pushed-main-to-origin" \
                "none" \
                "Local ${main_branch} was ahead of ${remote}/${main_branch}; pushed to ${remote}" \
            || true
        else
            echo "check-push-lag: push ${main_branch} failed: ${push_output}" >&2
            overwatch_log_action \
                "check-push-lag" \
                "${main_branch}" \
                "push-main-failed" \
                "none" \
                "Failed to push ${main_branch} to ${remote}: ${push_output}" \
            || true
            push_errors=$(( push_errors + 1 ))
        fi
    fi

    # Push unpushed tags
    if [[ -n "${unpushed_tags}" ]]; then
        local tag
        while IFS= read -r tag; do
            [[ -z "${tag}" ]] && continue
            echo "check-push-lag: pushing tag ${tag} to ${remote}" >&2
            local tag_push_output
            if tag_push_output="$(git -C "${dev_tree}" push "${remote}" "refs/tags/${tag}" 2>&1)"; then
                echo "check-push-lag: pushed tag ${tag} to ${remote}: ${tag_push_output}" >&2
                overwatch_log_action \
                    "check-push-lag" \
                    "${tag}" \
                    "pushed-tag-to-origin" \
                    "none" \
                    "Local tag ${tag} was absent from ${remote}; pushed" \
                || true
            else
                echo "check-push-lag: push tag ${tag} failed: ${tag_push_output}" >&2
                overwatch_log_action \
                    "check-push-lag" \
                    "${tag}" \
                    "push-tag-failed" \
                    "none" \
                    "Failed to push tag ${tag} to ${remote}: ${tag_push_output}" \
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

    # The project did not resolve (unregistered, no project.cfg, or no
    # dev_tree_path in it): decline before any git call, so no remote is
    # contacted. One line, naming the missing piece.
    if [[ "${_CPL_DECISION}" != "push" && "${_CPL_DECISION}" != "no-push" ]]; then
        echo "check-push-lag: ${_CPL_SKIP_LINE}" >&2
        return 0
    fi

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

    # The project's own refs (release_refs.sh): <prefix>main and <prefix>vX.Y.Z.
    local tag_prefix main_branch
    if ! tag_prefix="$(release_refs_tag_prefix "${project_name}")"; then
        echo "check-push-lag: branch_prefix for ${project_name} could not be read from ${_CPL_CFG_FILE}; its main branch and release tags are not checked this time (fix [project] branch_prefix)" >&2
        return 0
    fi
    main_branch="$(release_refs_main_branch "${tag_prefix}")"

    # The project's remote (pp_git_remote_name); an invalid git_remote_name
    # skips the check this time rather than guessing a destination.
    local remote="${_CPL_REMOTE:-}"
    if [[ -z "${remote}" ]]; then
        echo "check-push-lag: git_remote_name for ${project_name} in ${_CPL_CFG_FILE} is not a valid git remote name; its main branch and release tags are not checked this time (fix [project] git_remote_name)" >&2
        return 0
    fi

    # Detect main branch lag
    local ahead
    ahead="$(_cpl_main_commits_ahead "${dev_tree}" "${main_branch}" "${remote}")"

    echo "check-push-lag: local ${main_branch} is ${ahead} commit(s) ahead of ${remote}/${main_branch}" >&2

    # Detect unpushed release tags (ls-remote reads the remote; nothing is fetched)
    local unpushed_tags=""
    local tag_count=0
    if (( dry_run == 0 )); then
        unpushed_tags="$(_cpl_unpushed_tags "${dev_tree}" "${tag_prefix}" "${remote}")"
    else
        unpushed_tags="$(_cpl_unpushed_tags "${dev_tree}" "${tag_prefix}" "${remote}")" 2>/dev/null || unpushed_tags=""
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
        echo "check-push-lag: local matches ${remote}; no push needed" >&2
        return 0
    fi

    echo "check-push-lag: push lag detected (${main_branch} ahead=${ahead}, unpushed tags=${tag_count})" >&2

    # The resolver's no-push answer is push_to_remote = false: the
    # local-ahead state is by design (local-only mode), so log
    # 'staged-by-design' and exit without pushing.  This is the gate that
    # prevents OVERWATCH from pushing deliberately staged local work to the
    # remote repository on installs configured for operator-controlled pushes.
    if [[ "${_CPL_DECISION}" != "push" ]]; then
        echo "check-push-lag: push_to_remote=${_CPL_PUSH_SETTING:-false} (not true); local-ahead state is by design; skipping push" >&2
        overwatch_log_action \
            "check-push-lag" \
            "${dev_tree}" \
            "staged-by-design" \
            "none" \
            "push_to_remote=${_CPL_PUSH_SETTING:-false} for project ${project_name}; OVERWATCH pushes only on push_to_remote = true, so the local-ahead state is by design (${main_branch} ahead=${ahead}, unpushed_tags=${tag_count}); no push attempted" \
        2>/dev/null || true
        return 0
    fi

    if (( dry_run == 1 )); then
        echo "check-push-lag: [dry-run] would push ${main_branch}=${push_main} tag_count=${tag_count} to ${remote}" >&2
        overwatch_log_action \
            "check-push-lag" \
            "${dev_tree}" \
            "dry-run-push-lag-detected" \
            "none" \
            "Push lag: ${main_branch} ahead=${ahead}, unpushed tags=${tag_count}; dry-run, no action taken" \
        2>/dev/null || true
        return 0
    fi

    # Live mode: push via overwatch_halt_first_fix
    export _CPL_DEV_TREE="${dev_tree}"
    export _CPL_REMOTE="${remote}"
    export _CPL_MAIN_BRANCH="${main_branch}"
    export _CPL_PUSH_MAIN="${push_main}"
    export _CPL_UNPUSHED_TAGS="${unpushed_tags}"

    local fix_exit=0
    overwatch_halt_first_fix _cpl_do_push || fix_exit=$?

    unset _CPL_MAIN_BRANCH _CPL_PUSH_MAIN _CPL_UNPUSHED_TAGS
    # Note: _CPL_DEV_TREE is kept (set by _cpl_resolve_env) — do not unset here

    if (( fix_exit == 3 )); then
        echo "check-push-lag: HALT_OVERWATCH guard tripped; aborting push" >&2
        return 0
    elif (( fix_exit == 4 )); then
        echo "check-push-lag: per-repo flock contended; aborting push (CM agent likely running)" >&2
        return 0
    elif (( fix_exit == 5 )); then
        echo "check-push-lag: fix deferred: HALT present; aborting push" >&2
        return 0
    elif (( fix_exit != 0 )); then
        echo "check-push-lag: push operation failed (exit ${fix_exit})" >&2
        return 1
    fi

    echo "check-push-lag: push complete (${main_branch} ahead=${ahead} fixed, tags=${tag_count} pushed)" >&2
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
