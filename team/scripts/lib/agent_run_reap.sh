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
# Unreadable /proc: a reap whose scan reports `process_reap: unreadable=N`
# logs it as a WARNING. One such reap is a log line only: a process that
# exits mid-scan can cause it. Reaps that report it back to back are a host
# problem (a hidepid or restricted /proc mount, say) that leaves every reap
# partial, so the second consecutive one writes one operator inbox entry
# (operator_inbox_notice, write --once) naming the first unreadable path of
# the streak. The streak is counted kanban-wide, shared by every wake, in
# <kanban root>/logs/process-reap-unreadable.streak (KANBAN_ROOT, else
# PGAI_AGENT_KANBAN_ROOT_PATH): one tab-separated line -- count, first
# path, and 1 once the entry is written. A reap that reads all of /proc
# removes the file; a reap that could not run leaves it as it was. With no
# kanban root the streak is not tracked and the WARNING is the only record.
# The entry goes to the inbox of _CURRENT_PROJECT (else PGAI_PROJECT_NAME).
#
# The streak update is atomic across concurrent wakes: each read, increment
# and write (and each removal) happens while holding
# <kanban root>/locks/process-reap-unreadable-streak.lock, taken through
# pgai_lock_acquire_waiting (lock_provenance.sh) so it records holder, pid,
# op and task. A wake that finds the lock held waits up to
# _AGENT_RUN_REAP_STREAK_LOCK_WAIT_SECONDS for it, so two reaps finishing
# together count 2, never 1. A reap that cannot take the lock in that time
# leaves the streak as it was and logs a WARNING.
#
# Exit trap: between agent_run_begin and agent_run_end,
# CLEANUP_ON_EXIT_AGENT_RUN_ID and CLEANUP_ON_EXIT_AGENT_RUN_LOG name the run
# and its task log, so script_common.sh's cleanup_on_exit reaps the run when
# the wake itself is stopped mid-run.
#
# Dependencies: pp_run_ops (pp_run_ops.sh), operator_inbox_notice
# (operator_inbox_notice.sh) and pgai_lock_acquire_waiting /
# pgai_lock_release (lock_provenance.sh), each self-sourced when the caller
# has not loaded it; log() is optional.

_AGENT_RUN_REAP_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_AGENT_RUN_REAP_LIB_DIR}/pp_run_ops.sh"
fi
# shellcheck source=operator_inbox_notice.sh
source "${_AGENT_RUN_REAP_LIB_DIR}/operator_inbox_notice.sh"
if ! declare -F pgai_lock_acquire_waiting >/dev/null 2>&1; then
    # shellcheck source=lock_provenance.sh
    source "${_AGENT_RUN_REAP_LIB_DIR}/lock_provenance.sh"
fi

# How long, in whole seconds, a reap waits for another wake's streak update
# to finish. The update is a file read and write plus at most one inbox
# write, so a holder normally lets go well inside this.
_AGENT_RUN_REAP_STREAK_LOCK_WAIT_SECONDS=15

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
    local out rc=0 line found=0 unreadable=""
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
        elif [[ "$line" == "process_reap: unreadable="* ]]; then
            unreadable="$line"
            _agent_run_reap_say "WARNING: task ${label}: process reap could not read part of /proc: ${line}"
        else
            _agent_run_reap_say "task ${label}: process reap: ${line}"
        fi
    done <<< "$out"
    # Exit 0 and 3 are a completed scan; exit 4 (/proc unlistable) always
    # carries the unreadable summary. Anything else means the scan did not
    # run, which says nothing about /proc, so the streak is left alone.
    if [[ -n "$unreadable" ]] || (( rc == 0 || rc == 3 )); then
        _agent_run_reap_track_unreadable "$label" "$unreadable"
    fi
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

# _agent_run_reap_track_unreadable <label> <summary>
# Counts consecutive reaps that reported an unreadable /proc (see this
# file's header). An empty <summary> ends the streak. The whole update runs
# under the streak lock, so concurrent wakes never lose an increment.
# Never fails.
_agent_run_reap_track_unreadable() {
    local label="${1:-unknown}" summary="${2:-}"
    local root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}"
    [[ -n "$root" && -d "$root" ]] || return 0
    local streak_file="${root}/logs/process-reap-unreadable.streak"
    local lock_file="${root}/locks/process-reap-unreadable-streak.lock"
    local lock_task="$label" lock_fd="" lock_rc=0
    [[ -n "$lock_task" && "$lock_task" != "unknown" ]] || lock_task="none"
    mkdir -p "${root}/locks" 2>/dev/null || true
    pgai_lock_acquire_waiting "$_AGENT_RUN_REAP_STREAK_LOCK_WAIT_SECONDS" "$lock_file" \
        "${AGENT:-wake}" "$$" "process-reap-streak" "$lock_task" lock_fd 2>/dev/null || lock_rc=$?
    if (( lock_rc != 0 )); then
        local holder="(lock refused)"
        if (( lock_rc == 1 )); then
            holder="$(pgai_lock_describe_provenance "$(pgai_lock_read_provenance "$lock_file" 2>/dev/null)" 2>/dev/null)"
        fi
        _agent_run_reap_say "WARNING: task ${label}: could not take ${lock_file} within ${_AGENT_RUN_REAP_STREAK_LOCK_WAIT_SECONDS}s (holder: ${holder:-unknown}); the unreadable-reap streak is left as it was"
        return 0
    fi
    _agent_run_reap_update_streak "$label" "$summary" "$root" "$streak_file" || true
    pgai_lock_release "$lock_file" "$lock_fd" 2>/dev/null || true
    return 0
}

# _agent_run_reap_streak_read <streak_file>
# Echoes the streak record (count, first path, raised -- tab-separated), or
# nothing when there is none. Always returns 0.
_agent_run_reap_streak_read() {
    local line=""
    if [[ -f "$1" ]]; then
        IFS= read -r line < "$1" 2>/dev/null || true
    fi
    printf '%s\n' "$line"
}

# _agent_run_reap_update_streak <label> <summary> <root> <streak_file>
# The read-increment-write of the streak record. Called only while holding
# the streak lock (_agent_run_reap_track_unreadable). Always returns 0.
_agent_run_reap_update_streak() {
    local label="$1" summary="$2" root="$3" streak_file="$4"
    if [[ -z "$summary" ]]; then
        rm -f "$streak_file" 2>/dev/null || true
        return 0
    fi
    local count="" first="" raised=""
    IFS=$'\t' read -r count first raised <<< "$(_agent_run_reap_streak_read "$streak_file")" || true
    [[ "$count" =~ ^[0-9]+$ ]] || { count=0; first=""; raised=""; }
    count=$(( count + 1 ))
    if [[ -z "$first" ]]; then
        first="unknown"
        [[ "$summary" =~ first_path=([^[:space:]]+) ]] && first="${BASH_REMATCH[1]}"
    fi
    if (( count >= 2 )) && [[ "$raised" != "1" ]]; then
        local project="${_CURRENT_PROJECT:-${PGAI_PROJECT_NAME:-}}" entry=""
        local question meanwhile how_to_answer
        question="The wake's process reap could not read part of /proc on consecutive agent runs (first unreadable path: ${first}), so processes those runs left behind may still be running. Restore /proc read access for the wake's account?"
        meanwhile="Each reap stopped every process it could read, logged a WARNING and the wake carried on. Latest reap, task ${label}: ${summary}"
        how_to_answer="Check how /proc is mounted and what the wake's account may read: \`findmnt /proc\` and \`ls -ld $(printf '%q' "$first")\` (a hidepid or restricted mount causes this). The streak ends on the next reap that reads all of /proc. Then close this entry: $(printf '%q' "${root}/scripts/inbox.sh") --project ${project} close --id followed by this entry's INBOX ID."
        if entry="$(operator_inbox_notice "$root" "$project" "wake-process-reap" "$question" "$meanwhile" "$how_to_answer")"; then
            raised=1
            _agent_run_reap_say "WARNING: task ${label}: ${count} consecutive process reaps could not read part of /proc; operator inbox entry ${entry} names ${first}"
        else
            _agent_run_reap_say "WARNING: task ${label}: ${count} consecutive process reaps could not read part of /proc, and the operator inbox entry was not written: ${entry}"
        fi
    elif (( count == 1 )); then
        _agent_run_reap_say "task ${label}: first process reap in a row to find /proc partly unreadable; the operator inbox hears of it if the next reap does too"
    fi
    mkdir -p "${root}/logs" 2>/dev/null || true
    printf '%s\t%s\t%s\n' "$count" "$first" "${raised:-0}" > "$streak_file" 2>/dev/null \
        || _agent_run_reap_say "WARNING: task ${label}: could not record the unreadable-reap streak in ${streak_file}"
    return 0
}
