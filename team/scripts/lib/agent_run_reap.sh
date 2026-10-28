#!/usr/bin/env bash
# team/scripts/lib/agent_run_reap.sh
# Marks the processes of one agent run so they can be found after the run
# ends, and stops whatever the run left running.
#
# A process the agent starts in the background (`cmd &`, `nohup`, a tool
# that opens a session of its own) is not reliably in the provider's process
# group and re-parents to PID 1 when its parent exits, so no group signal or
# `wait` reaches it. Every process does inherit its parent's environment,
# though: the wake exports PGAI_AGENT_RUN_ID, a value unique to the run,
# before it launches the provider, and once the run ends it stops every
# process whose environment still carries that value
# (pgai_agent_kanban.lib.process_reap --env). Nothing else on the host
# carries it, so the operator's shells and other agents' processes are never
# signalled.
#
# Public entry points
# -------------------
#   agent_run_begin <task_id> [<task_log_file>]
#       Called just before the provider is launched: exports
#       PGAI_AGENT_RUN_ID with a new run id (agent_run_new_id), remembers it,
#       <task_id> and <task_log_file> for agent_run_end, and sets the exit
#       trap's CLEANUP_ON_EXIT_AGENT_RUN_ID / CLEANUP_ON_EXIT_AGENT_RUN_LOG.
#
#   agent_run_end [<grace_seconds>]
#       Called once the provider has returned, on normal exit and after a
#       timeout alike: unsets PGAI_AGENT_RUN_ID, runs agent_run_reap on the
#       remembered run (grace default KILL_GRACE_SECONDS, then 30) and
#       clears the exit trap's variables. Returns agent_run_reap's status;
#       a no-op returning 0 when no run was begun.
#
#   agent_run_new_id <task_id>
#       Echoes a run id unique to this run of <task_id>: the task id, the
#       wake's pid, the time in nanoseconds and a random number.
#
#   agent_run_reap <run_id> <grace_seconds> [<task_log_file>] [<task_id>]
#       Stops every process whose environment holds PGAI_AGENT_RUN_ID=<run_id>:
#       SIGTERM, then SIGKILL after <grace_seconds>. For each one it logs a
#       line naming the pid, the outcome and the command (log(), when
#       defined, else stderr) and appends the same line, timestamped, to
#       <task_log_file> when given (the task's logs/process-reap.log). One
#       line says when the run left nothing running. Returns 0, or 1 when a
#       process outlived SIGKILL by the grace window (in kernel teardown).
#       The caller unsets PGAI_AGENT_RUN_ID first, so neither this function
#       nor the scan it starts carries the value it looks for.
#
# Exit trap: between agent_run_begin and agent_run_end,
# CLEANUP_ON_EXIT_AGENT_RUN_ID and CLEANUP_ON_EXIT_AGENT_RUN_LOG name the run
# and its task log, so script_common.sh's cleanup_on_exit reaps the run when
# the wake itself is stopped mid-run.
#
# Dependencies: pp_run_ops (pp_run_ops.sh, self-sourced when the caller has
# not loaded it); log() is optional.

_AGENT_RUN_REAP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_AGENT_RUN_REAP_LIB_DIR}/pp_run_ops.sh"
fi

agent_run_begin() {
    _AGENT_RUN_TASK="${1:-unknown}"
    _AGENT_RUN_LOG="${2:-}"
    _AGENT_RUN_ID="$(agent_run_new_id "$_AGENT_RUN_TASK")"
    export PGAI_AGENT_RUN_ID="$_AGENT_RUN_ID"
    CLEANUP_ON_EXIT_AGENT_RUN_ID="$_AGENT_RUN_ID"
    CLEANUP_ON_EXIT_AGENT_RUN_LOG="$_AGENT_RUN_LOG"
}

agent_run_end() {
    local grace="${1:-${KILL_GRACE_SECONDS:-30}}" rc=0
    unset PGAI_AGENT_RUN_ID
    [[ -n "${_AGENT_RUN_ID:-}" ]] || return 0
    agent_run_reap "$_AGENT_RUN_ID" "$grace" "${_AGENT_RUN_LOG:-}" "${_AGENT_RUN_TASK:-}" || rc=$?
    _AGENT_RUN_ID=""; _AGENT_RUN_LOG=""; _AGENT_RUN_TASK=""
    CLEANUP_ON_EXIT_AGENT_RUN_ID=""; CLEANUP_ON_EXIT_AGENT_RUN_LOG=""
    return "$rc"
}

agent_run_new_id() {
    printf '%s.%s.%s.%s\n' "${1:-run}" "$$" "$(date +%s%N)" "${RANDOM}"
}

_agent_run_reap_say() {
    if declare -F log >/dev/null 2>&1; then
        log "$*"
    else
        echo "[$(date -Iseconds)] $*" >&2
    fi
}

agent_run_reap() {
    local run_id="${1:-}" grace="${2:-30}" task_log="${3:-}" label="${4:-${task_id:-unknown}}"
    [[ -n "$run_id" ]] || return 0
    [[ "$grace" =~ ^[0-9]+$ ]] || grace=30
    local out rc=0 line found=0
    out="$(pp_run_ops pgai_agent_kanban.lib.process_reap \
        --env "PGAI_AGENT_RUN_ID=${run_id}" --grace "$grace" 2>&1)" || rc=$?
    while IFS= read -r line; do
        [[ -n "$line" ]] || continue
        if [[ "$line" == pid=* ]]; then
            found=$(( found + 1 ))
            _agent_run_reap_say "task ${label}: stopped a process the agent run left running: ${line}"
            if [[ -n "$task_log" ]]; then
                printf '%s reaped %s\n' "$(date -Iseconds)" "$line" >> "$task_log" 2>/dev/null || true
            fi
        else
            _agent_run_reap_say "task ${label}: process reap: ${line}"
        fi
    done <<< "$out"
    if (( found == 0 && rc == 0 )); then
        _agent_run_reap_say "task ${label}: the agent run left no process running"
    fi
    if (( rc == 3 )); then
        _agent_run_reap_say "ERROR: task ${label}: a process the agent run started outlived SIGKILL by ${grace}s (outcome=survived above); it is in kernel teardown and runs no further code"
        return 1
    fi
    if (( rc != 0 )); then
        _agent_run_reap_say "WARNING: task ${label}: the process reap could not run (exit ${rc}); processes the agent run started may still be running"
    fi
    return 0
}
