#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-blocked-tasks.sh
#
# OVERWATCH detection module, REPORT-ONLY: narrate the BLOCKED tasks that
# block triage cannot see.
#
# Block triage (check-block-triage.sh) acts only on a BLOCKED task that
# carries a ## Block Record. A BLOCKED task without one — blocked before
# block sites wrote records, or by a hand edit — is invisible to it. This
# module lists each such task (queue line marked [B], status.md ## State
# BLOCKED, no ## Block Record) on stderr, with its ## Needs Human value, so
# the sweep log shows what is left for a human.
#
# It never modifies anything: no status.md, queue, README or action-log
# write, and no backup. It always returns 0 once its environment resolves.
#
# This script is both:
#   - Sourceable (for the OVERWATCH sweep runner): source this file and call
#     overwatch_check_blocked_tasks
#   - Directly invokable: bash check-blocked-tasks.sh [--dry-run]
#     (--dry-run is accepted for symmetry with the other checks; the module
#     never writes, so it changes nothing.)
#
# Required environment variables (when sourced):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
# Invoked directly, KANBAN_ROOT defaults to
# ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}; OVERWATCH_PROJECT
# must be set.
#
# Exit codes:
#   0 — completed (whatever was found)
#   1 — internal error (KANBAN_ROOT, OVERWATCH_PROJECT or the project root
#       missing, or the shared module runner unavailable)

# ---------------------------------------------------------------------------
# overwatch_check_blocked_tasks [--dry-run]
# ---------------------------------------------------------------------------
overwatch_check_blocked_tasks() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        echo "check-blocked-tasks: OVERWATCH_PROJECT is not set; set it to the project to report on" >&2
        return 1
    fi
    local project_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local tasks_root="${project_root}/tasks"
    if [[ ! -d "${project_root}" ]]; then
        echo "check-blocked-tasks: project root does not exist: ${project_root}" >&2
        return 1
    fi
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)" || lib_dir=""
    if ! declare -F pp_run_ops >/dev/null 2>&1; then
        # shellcheck source=../pp_run_ops.sh
        source "${lib_dir}/pp_run_ops.sh" 2>/dev/null || {
            echo "check-blocked-tasks: cannot load pp_run_ops.sh from ${lib_dir:-the lib directory}" >&2
            return 1
        }
    fi
    if ! declare -F kanban_task_id_pattern >/dev/null 2>&1; then
        # shellcheck source=../task_ids.sh
        source "${lib_dir}/task_ids.sh" 2>/dev/null || {
            echo "check-blocked-tasks: cannot load task_ids.sh from ${lib_dir:-the lib directory}" >&2
            return 1
        }
    fi

    local queue task_id status_file record needs_human unseen=0
    local -A seen=()
    while IFS= read -r queue; do
        [[ -f "${queue}" ]] || continue
        while IFS= read -r task_id; do
            [[ -n "${task_id}" && -z "${seen[${task_id}]:-}" ]] || continue
            seen["${task_id}"]=1
            status_file="${tasks_root}/${task_id}/status.md"
            record="$(pp_run_ops pgai_agent_kanban.task_status read "${status_file}" \
                --section "Block Record" 2>/dev/null)" || record=""
            [[ -z "${record//[[:space:]]/}" || "${record,,}" == "none" ]] || continue
            needs_human="$(pp_run_ops pgai_agent_kanban.task_status read "${status_file}" \
                --section "Needs Human" 2>/dev/null)" || needs_human="unreadable"
            echo "check-blocked-tasks: ${task_id}: BLOCKED with no ## Block Record (Needs Human: ${needs_human:-unrecorded}); block triage cannot act on it" >&2
            unseen=$(( unseen + 1 ))
        done < <(pp_run_ops pgai_agent_kanban.ops scan_queue "${queue}" "${tasks_root}" \
            "$(kanban_task_id_pattern)" 0 'B=BLOCKED' 2>/dev/null)
    done < <(find "${tasks_root}/queues" -maxdepth 2 -name '*_backlog.md' -type f 2>/dev/null | sort)

    echo "check-blocked-tasks: ${OVERWATCH_PROJECT}: ${unseen} BLOCKED task(s) without a Block Record" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_blocked_tasks "$@"
    exit $?
fi
