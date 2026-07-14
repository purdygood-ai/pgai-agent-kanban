#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-stale-worktrees.sh
#
# OVERWATCH Tier-1 detection module: find git worktrees whose backing task
# is in a terminal state AND older than an age threshold.
#
# Detection:
#   - Run `git worktree list --porcelain` in the dev tree to enumerate all
#     worktrees.
#   - For each worktree whose path contains a known task-ID pattern
#     (or is under the framework's worktree temp directory), look up the
#     corresponding task status.md.
#   - A worktree is "stale" when BOTH conditions are true:
#     1. The task is in a terminal state: DONE, WONT-DO, or BLOCKED.
#     2. The worktree directory's mtime is older than OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS
#        (default: 7 days).
#
# Auto-fix path (no extra commits, prune-class only):
#   - Call `git worktree remove --force <path>` (or `git worktree prune` after
#     removing the directory) to clean up the worktree metadata.
#   - Log the action via overwatch_log_action.
#   - This is the ONLY auto-fix; no branch deletion.
#
# Bug-file path (branch-carrying worktree):
#   - If the worktree branch has commits not reachable from any other ref
#     (it "carries commits"), write a bug report and log it.
#   - Never auto-remove a branch-carrying worktree.
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_stale_worktrees
#   - Zero arguments. All context from environment variables.
#   - Returns 0 on success (regardless of whether anomalies were found or fixed).
#   - Returns 1 on internal error.
#
# Required environment variables:
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# Optional:
#   PGAI_DEV_TREE_PATH — fallback dev tree path
#   OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS — age threshold in days (default: 7)
#
# Usage (standalone):
#   bash check-stale-worktrees.sh [--dry-run]
#
# Exit codes:
#   0 — completed (anomalies found and handled, or none found)
#   1 — internal error

# ---------------------------------------------------------------------------
# _csw_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, dev tree, and task paths.
# Sets _CSW_DEV_TREE, _CSW_TASKS_ROOT, _CSW_THRESHOLD_SECONDS in calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_csw_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-stale-worktrees: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        local _all_projects=""
        if declare -f projects_cfg_list >/dev/null 2>&1; then
            _all_projects="$(projects_cfg_list 2>/dev/null)"
        else
            local _cfg="${KANBAN_ROOT}/projects.cfg"
            _all_projects="$(awk '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1]}' "${_cfg}" 2>/dev/null)"
        fi
        local _project_count
        _project_count="$(echo "${_all_projects}" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)"
        if [[ "${_project_count}" -gt 1 ]]; then
            echo "check-stale-worktrees: ERROR: OVERWATCH_PROJECT not set and multiple projects registered." >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-stale-worktrees: ERROR: no project specified and none resolvable from projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path from project.cfg.
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local _cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg_file="${_proj_root}/PROJECT.cfg"
    fi

    _CSW_DEV_TREE=""
    if [[ -n "${_cfg_file}" ]]; then
        _CSW_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CSW_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CSW_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi

    # Tasks root for this project.
    _CSW_TASKS_ROOT="${_proj_root}/tasks"

    # Age threshold in seconds (default 7 days = 604800 seconds).
    local _threshold_days="${OVERWATCH_STALE_WORKTREE_THRESHOLD_DAYS:-7}"
    _CSW_THRESHOLD_SECONDS=$(( _threshold_days * 86400 ))

    return 0
}

# ---------------------------------------------------------------------------
# _csw_load_protocol
# Ensure overwatch_log_action and overwatch_backup_file are available.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_csw_load_protocol() {
    if declare -f overwatch_log_action >/dev/null 2>&1; then
        return 0
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
        echo "check-stale-worktrees: cannot resolve lib dir" >&2
        return 1
    }
    local protocol_sh="${lib_dir}/../overwatch_protocol.sh"
    if [[ ! -f "${protocol_sh}" ]]; then
        echo "check-stale-worktrees: overwatch_protocol.sh not found at ${protocol_sh}" >&2
        return 1
    fi
    # shellcheck source=/dev/null
    source "${protocol_sh}"
}

# ---------------------------------------------------------------------------
# _csw_extract_task_id_from_path <worktree_path>
# Attempt to extract a task ID from a worktree path.
# Task IDs match the pattern: [A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+
# Echoes the first match found in the path components, or empty string.
# ---------------------------------------------------------------------------
_csw_extract_task_id_from_path() {
    local wt_path="$1"
    # Strip common prefix components and look for the task-ID pattern.
    local basename_part
    basename_part="$(basename "${wt_path}")"
    # Task ID pattern: ROLE-YYYYMMDD-NNN-slug
    if [[ "${basename_part}" =~ ^([A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+)$ ]]; then
        echo "${BASH_REMATCH[1]}"
        return 0
    fi
    # Also check within path components (worktree may be under a directory named with the task ID)
    local path_part
    path_part="${wt_path}"
    while [[ "${path_part}" != "/" && "${path_part}" != "." ]]; do
        local component
        component="$(basename "${path_part}")"
        if [[ "${component}" =~ ^([A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+)$ ]]; then
            echo "${BASH_REMATCH[1]}"
            return 0
        fi
        path_part="$(dirname "${path_part}")"
    done
    echo ""
}

# ---------------------------------------------------------------------------
# _csw_task_is_terminal <tasks_root> <task_id>
# Returns 0 (true) if the task's state is DONE, WONT-DO, or BLOCKED.
# Returns 1 (false) otherwise (WORKING, BACKLOG, WAITING, or no status file).
# ---------------------------------------------------------------------------
_csw_task_is_terminal() {
    local tasks_root="$1"
    local task_id="$2"

    local status_file="${tasks_root}/${task_id}/status.md"
    if [[ ! -f "${status_file}" ]]; then
        return 1
    fi

    # Extract the ## State field.
    local state
    state="$(awk '/^## State$/{found=1;next} found && /^## /{exit} found{print;exit}' \
        "${status_file}" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"

    case "${state}" in
        DONE|WONT-DO|BLOCKED)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# _csw_worktree_age_seconds <wt_path>
# Echo the age of the worktree directory in seconds (current time - mtime).
# Echoes 0 on failure (treats unknown age as "not stale").
# ---------------------------------------------------------------------------
_csw_worktree_age_seconds() {
    local wt_path="$1"

    if [[ ! -e "${wt_path}" ]]; then
        echo "0"
        return 0
    fi

    local mtime now age
    mtime="$(stat -c '%Y' "${wt_path}" 2>/dev/null || echo "0")"
    now="$(date +%s 2>/dev/null || echo "0")"

    if [[ "${mtime}" == "0" || "${now}" == "0" ]]; then
        echo "0"
        return 0
    fi

    age=$(( now - mtime ))
    if (( age < 0 )); then
        age=0
    fi
    echo "${age}"
}

# ---------------------------------------------------------------------------
# _csw_worktree_has_extra_commits <dev_tree> <wt_path> <branch_ref>
# Returns 0 (true) if the worktree branch has commits not reachable from
# any branch other than itself (i.e., it "carries commits").
# Returns 1 (false) if the worktree is safe to prune.
# ---------------------------------------------------------------------------
_csw_worktree_has_extra_commits() {
    local dev_tree="$1"
    local wt_path="$2"
    local branch_ref="$3"

    if [[ -z "${dev_tree}" || ! -d "${dev_tree}" ]]; then
        # No dev tree — cannot determine; treat as safe to prune.
        return 1
    fi

    if [[ -z "${branch_ref}" ]]; then
        # Detached HEAD or no branch info — check if HEAD is reachable from any named branch.
        local wt_head
        wt_head="$(git -C "${wt_path}" rev-parse HEAD 2>/dev/null || true)"
        if [[ -z "${wt_head}" ]]; then
            return 1
        fi
        local reachable
        reachable="$(git -C "${dev_tree}" branch --contains "${wt_head}" 2>/dev/null | head -n1 || true)"
        if [[ -z "${reachable}" ]]; then
            # HEAD not reachable from any branch — has unique commits
            return 0
        fi
        return 1
    fi

    # Extract just the branch name from refs/heads/<name>
    local branch_name="${branch_ref#refs/heads/}"

    # Find commits on this branch not yet merged into any integration branch.
    # Integration branches: main, ai_main, rc/*, ai_rc/*.
    # If any integration branch contains all commits from this branch, return 1 (safe to prune).
    local integration_branches=()
    local _b
    while IFS= read -r _b; do
        _b="${_b#  }"    # strip leading spaces
        _b="${_b#\* }"   # strip "* " current-branch marker
        _b="${_b//[[:space:]]/}"
        [[ -z "${_b}" ]] && continue
        case "${_b}" in
            main|ai_main) integration_branches+=("${_b}") ;;
            rc/*|ai_rc/*) integration_branches+=("${_b}") ;;
        esac
    done < <(git -C "${dev_tree}" branch 2>/dev/null)

    if (( ${#integration_branches[@]} == 0 )); then
        # No integration branches found — cannot safely determine; treat as carrying commits.
        return 0
    fi

    # For each integration branch, count commits ahead.
    # If ANY integration branch has 0 commits ahead, the branch is already merged.
    local _base_branch
    for _base_branch in "${integration_branches[@]}"; do
        local _extra_count
        _extra_count="$(git -C "${dev_tree}" rev-list --count \
            "${_base_branch}..${branch_name}" 2>/dev/null || echo "1")"
        if [[ "${_extra_count}" =~ ^[0-9]+$ ]] && (( _extra_count == 0 )); then
            # Branch has no commits ahead of this integration branch — safe to prune.
            return 1
        fi
    done

    # All integration branches show commits ahead — branch carries unique commits.
    return 0
}

# ---------------------------------------------------------------------------
# _csw_bug_file <task_id> <wt_path> <reason> <bugs_dir>
# Write a bug report for a branch-carrying stale worktree.
# Echoes the path to the created bug file.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_csw_bug_file() {
    local task_id="$1"
    local wt_path="$2"
    local reason="$3"
    local bugs_dir="$4"
    local timestamp
    timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
    local safe_id
    safe_id="$(echo "${task_id:-unknown}" | tr '/' '-')"
    local bug_file="${bugs_dir}/BUG-overwatch-stale-worktree-${safe_id}-${timestamp}.md"

    mkdir -p "${bugs_dir}" 2>/dev/null || {
        echo "check-stale-worktrees: cannot create bugs dir: ${bugs_dir}" >&2
        return 1
    }

    cat > "${bug_file}" <<EOF
# Bug: Stale Worktree With Commits — ${task_id:-unknown}

## Status
open

## Filed By
overwatch/check-stale-worktrees

## Filed At
${timestamp}

## Task
${task_id:-unknown}

## Worktree Path
${wt_path}

## Reason
${reason}

## Description
OVERWATCH detected a stale worktree whose backing task is in a terminal state
but which carries local commits not reachable from any other branch. The worktree
cannot be auto-pruned. Manual inspection is required to determine whether the
commits should be merged, abandoned, or preserved.
EOF

    echo "${bug_file}"
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_check_stale_worktrees [--dry-run]
# Main detection and action function.
# Returns 0 on success (including finding and handling anomalies), 1 on error.
# ---------------------------------------------------------------------------
overwatch_check_stale_worktrees() {
    local dry_run=0
    local arg
    for arg in "$@"; do
        if [[ "${arg}" == "--dry-run" ]]; then
            dry_run=1
        fi
    done

    _csw_resolve_env || return 1
    _csw_load_protocol || return 1

    local project_name="${OVERWATCH_PROJECT}"
    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local bugs_dir="${project_root}/bugs"
    local tasks_root="${_CSW_TASKS_ROOT}"
    local dev_tree="${_CSW_DEV_TREE:-}"
    local threshold_secs="${_CSW_THRESHOLD_SECONDS}"

    if [[ ! -d "${project_root}" ]]; then
        echo "check-stale-worktrees: project root does not exist: ${project_root}" >&2
        return 1
    fi

    if [[ -z "${dev_tree}" ]]; then
        echo "check-stale-worktrees: no dev tree configured; skipping worktree scan" >&2
        overwatch_log_action \
            "check-stale-worktrees" \
            "${project_name}" \
            "skipped-no-dev-tree" \
            "none" \
            "No dev_tree_path configured; cannot enumerate worktrees" \
        || true
        return 0
    fi

    if [[ ! -d "${dev_tree}" ]]; then
        echo "check-stale-worktrees: dev tree does not exist: ${dev_tree}; skipping" >&2
        return 0
    fi

    echo "check-stale-worktrees: enumerating worktrees in ${dev_tree}" >&2

    # Enumerate worktrees using --porcelain format.
    # Each worktree block:
    #   worktree <path>
    #   HEAD <sha>
    #   branch <refs/heads/name>   (or 'detached')
    #   (blank line)
    local worktree_entries
    worktree_entries="$(git -C "${dev_tree}" worktree list --porcelain 2>/dev/null || true)"

    if [[ -z "${worktree_entries}" ]]; then
        echo "check-stale-worktrees: no worktrees found" >&2
        return 0
    fi

    # Parse --porcelain output into arrays.
    local wt_paths=()
    local wt_branches=()
    local current_path="" current_branch=""

    while IFS= read -r line; do
        if [[ "${line}" =~ ^worktree[[:space:]]+(.+)$ ]]; then
            if [[ -n "${current_path}" ]]; then
                wt_paths+=("${current_path}")
                wt_branches+=("${current_branch}")
            fi
            current_path="${BASH_REMATCH[1]}"
            current_branch=""
        elif [[ "${line}" =~ ^branch[[:space:]]+(.+)$ ]]; then
            current_branch="${BASH_REMATCH[1]}"
        elif [[ "${line}" == "detached" ]]; then
            current_branch=""
        fi
    done <<< "${worktree_entries}"
    # Don't forget the last entry
    if [[ -n "${current_path}" ]]; then
        wt_paths+=("${current_path}")
        wt_branches+=("${current_branch}")
    fi

    echo "check-stale-worktrees: found ${#wt_paths[@]} worktree(s)" >&2

    local pruned_count=0
    local bug_count=0
    local i

    for (( i=0; i<${#wt_paths[@]}; i++ )); do
        local wt_path="${wt_paths[$i]}"
        local wt_branch="${wt_branches[$i]}"

        # Skip the main worktree (the dev tree itself).
        if [[ "${wt_path}" == "${dev_tree}" ]]; then
            continue
        fi

        # Extract task ID from the worktree path.
        local task_id
        task_id="$(_csw_extract_task_id_from_path "${wt_path}")"

        if [[ -z "${task_id}" ]]; then
            echo "check-stale-worktrees: worktree ${wt_path}: no task ID found in path; skipping" >&2
            continue
        fi

        # Check if the task is in a terminal state.
        if ! _csw_task_is_terminal "${tasks_root}" "${task_id}"; then
            echo "check-stale-worktrees: worktree ${wt_path}: task ${task_id} is not terminal; skipping" >&2
            continue
        fi

        # Check age threshold.
        local age_secs
        age_secs="$(_csw_worktree_age_seconds "${wt_path}")"
        if (( age_secs < threshold_secs )); then
            local threshold_days=$(( threshold_secs / 86400 ))
            echo "check-stale-worktrees: worktree ${wt_path}: age=${age_secs}s < threshold=${threshold_secs}s (${threshold_days}d); skipping" >&2
            continue
        fi

        echo "check-stale-worktrees: worktree ${wt_path}: task=${task_id} is terminal and age=${age_secs}s >= threshold; inspecting" >&2

        # Check for extra commits before deciding on auto-fix vs bug-file.
        if _csw_worktree_has_extra_commits "${dev_tree}" "${wt_path}" "${wt_branch}"; then
            # Carries commits — bug-file, never auto-prune.
            echo "check-stale-worktrees: worktree ${wt_path}: carries commits; bug-filing" >&2
            if (( dry_run == 0 )); then
                local bug_path
                bug_path="$(_csw_bug_file "${task_id}" "${wt_path}" \
                    "Stale worktree for terminal task ${task_id} carries local commits; manual inspection required" \
                    "${bugs_dir}")" || {
                    echo "check-stale-worktrees: bug-file write failed for ${wt_path}" >&2
                    continue
                }
                overwatch_log_action \
                    "check-stale-worktrees" \
                    "${wt_path}" \
                    "bug-filed:branch-carrying-worktree" \
                    "none" \
                    "Stale worktree for terminal task ${task_id} carries commits; bug filed at ${bug_path}" \
                || true
                bug_count=$(( bug_count + 1 ))
                echo "check-stale-worktrees: bug filed at ${bug_path}" >&2
            else
                echo "check-stale-worktrees: [dry-run] would bug-file worktree ${wt_path} (carries commits)" >&2
                overwatch_log_action \
                    "check-stale-worktrees" \
                    "${wt_path}" \
                    "dry-run-would-bug-file-branch-carrying-worktree" \
                    "none" \
                    "Stale worktree for terminal task ${task_id} carries commits; dry-run, no action" \
                2>/dev/null || true
            fi
        else
            # No extra commits — prune-class auto-fix.
            echo "check-stale-worktrees: worktree ${wt_path}: no extra commits; prune candidate" >&2
            if (( dry_run == 0 )); then
                # Remove the worktree directory if it still exists, then prune metadata.
                if [[ -d "${wt_path}" ]]; then
                    git -C "${dev_tree}" worktree remove --force "${wt_path}" 2>/dev/null || true
                fi
                git -C "${dev_tree}" worktree prune 2>/dev/null || true

                overwatch_log_action \
                    "check-stale-worktrees" \
                    "${wt_path}" \
                    "auto-fix:worktree-pruned" \
                    "none" \
                    "Stale worktree for terminal task ${task_id} had no extra commits; pruned (age=${age_secs}s)" \
                || true
                pruned_count=$(( pruned_count + 1 ))
                echo "check-stale-worktrees: pruned worktree ${wt_path}" >&2
            else
                echo "check-stale-worktrees: [dry-run] would prune worktree ${wt_path} (no extra commits)" >&2
                overwatch_log_action \
                    "check-stale-worktrees" \
                    "${wt_path}" \
                    "dry-run-would-prune-worktree" \
                    "none" \
                    "Stale worktree for terminal task ${task_id} at ${wt_path}; dry-run, would prune" \
                2>/dev/null || true
            fi
        fi
    done

    echo "check-stale-worktrees: complete; pruned=${pruned_count}, bug-filed=${bug_count}" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_stale_worktrees "$@"
    exit $?
fi
