#!/usr/bin/env bash
# team/scripts/lib/wake_batch_guard.sh
#
# The single-instance guard wake-batch.sh takes for each (project, agent)
# pair, so batches of one agent never overlap within a project: cron starts
# every agent every minute, and a batch that is still working or waiting on
# a project's lock when the next firing starts would otherwise contend with
# it for the same project. Batches of one agent for different projects
# never wait on each other, and neither do different agents.
#
# SOURCE THIS FILE; do not execute it directly. Sourcing defines functions
# and nothing else.
#
#   wake_batch_guard_agent <args...>
#       Echo the agent a wake-batch.sh argument list names, read the way
#       the provider wake scripts (wake/<provider>.sh) read it: --agent=X,
#       --agent X or -a X anywhere, the last one winning, or else the
#       legacy positional word. The value-taking options --sleep / -s and
#       --max-tasks consume their value in the space-separated form, so
#       `-s 21 --agent=coder` names coder, not 21. Echoes nothing when the
#       list names no agent, or one that is not a plain role name.
#
#   wake_batch_guard_take <kanban_root> <agent> <project> <pid>
#       Claim the (<project>, <agent>) guard for <pid> -- the pid of the
#       process that execs the provider wake script for that one project,
#       so the record names the running batch for its whole life. Returns 0
#       when <pid> holds the guard now (a record naming <pid> is written), 1
#       when an earlier batch of <agent> for <project> is still running
#       (sets WAKE_BATCH_GUARD_HOLDER_PID and WAKE_BATCH_GUARD_HOLDER_AGE,
#       and writes nothing), 2 when the guard could not be read or written,
#       or <project> is not a plain project name (the caller runs unguarded
#       rather than not at all).
#
# THE RECORD
# ----------
# <kanban_root>/locks/wake-batch-<agent>-<project>.record, "key=value"
# lines: holder (wake-batch), pid, op (wake-batch:<agent>:<project>), task
# (none), acquired_at, pid_start and boot_id. The record is replaced, never
# edited, by a rename over the path. It excludes only while its pid is the
# live process it names: pgai_lock_pid_liveness_verdict
# (lock_provenance.sh) reads a dead pid, or a pid since reused (another
# start time or boot), as stale, and a stale record is taken over at once.
# Nothing deletes it; the batch never has to release it.
#
# The read-decide-write step runs under a short provenance-recorded flock
# on <kanban_root>/locks/wake-batch-<agent>-<project>.lock
# (pgai_lock_acquire), so two firings starting together cannot both claim
# the same pair. That flock is released before the provider script is
# execed, so no child of the batch -- an OVERWATCH sweep it backgrounds,
# say -- inherits it.
#
# This guard is not the per-project repo lock: that lock, held by every
# chain for its whole run, is what keeps two agents of one project apart,
# and nothing here changes it.

# Waits this long for another firing's read-decide-write step.
_WAKE_BATCH_GUARD_STEP_WAIT_SECONDS=5

# This file's directory, as an absolute path resolved once at load: no
# function depends on the caller's current directory or on whether this
# file was named by a relative path.
_WAKE_BATCH_GUARD_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _WAKE_BATCH_GUARD_LIB_DIR=""

wake_batch_guard_agent() {
    # The flag set mirrors the provider wake scripts' argument loops: keep
    # the value-taking options here in step with theirs.
    local agent="" positional=""
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --agent=*) agent="${1#--agent=}"; shift ;;
            --agent|-a) agent="${2:-}"; shift; [[ $# -gt 0 ]] && shift ;;
            --sleep|-s|--max-tasks) shift; [[ $# -gt 0 ]] && shift ;;
            -*) shift ;;
            *) [[ -n "$positional" ]] || positional="$1"; shift ;;
        esac
    done
    agent="${agent:-$positional}"
    [[ "$agent" =~ ^[a-z][a-z0-9_-]*$ ]] && printf '%s\n' "$agent"
    return 0
}

_wake_batch_guard_lib_dir() {
    [[ -n "${_WAKE_BATCH_GUARD_LIB_DIR}" ]] && printf '%s\n' "${_WAKE_BATCH_GUARD_LIB_DIR}"
}

wake_batch_guard_take() {
    local kanban_root="$1" agent="$2" project="$3" pid="$4"
    WAKE_BATCH_GUARD_HOLDER_PID=""
    WAKE_BATCH_GUARD_HOLDER_AGE=""
    [[ -n "$kanban_root" && -n "$agent" && "$pid" =~ ^[0-9]+$ ]] || return 2
    local lib_dir=""
    # The project name becomes part of a path under locks/: only a name the
    # project-name rule accepts (PROJECTS_CFG_NAME_RE, projects_cfg.sh -- the
    # rule every registry reader lists by) is.
    if [[ -z "${PROJECTS_CFG_NAME_RE:-}" ]]; then
        lib_dir="$(_wake_batch_guard_lib_dir)" || return 2
        # shellcheck source=projects_cfg.sh
        source "${lib_dir}/projects_cfg.sh" || return 2
    fi
    [[ -n "${PROJECTS_CFG_NAME_RE:-}" && "$project" =~ $PROJECTS_CFG_NAME_RE ]] || return 2

    if ! declare -F pgai_lock_acquire_waiting >/dev/null 2>&1; then
        [[ -n "$lib_dir" ]] || lib_dir="$(_wake_batch_guard_lib_dir)" || return 2
        # shellcheck source=lock_provenance.sh
        source "${lib_dir}/lock_provenance.sh" || return 2
    fi

    local locks="${kanban_root}/locks"
    local record="${locks}/wake-batch-${agent}-${project}.record"
    local step_lock="${locks}/wake-batch-${agent}-${project}.lock"
    mkdir -p "$locks" 2>/dev/null || return 2

    local step_fd="" rc=0
    pgai_lock_acquire_waiting "$_WAKE_BATCH_GUARD_STEP_WAIT_SECONDS" "$step_lock" \
        "wake-batch" "$pid" "wake-batch-guard:${agent}:${project}" "none" step_fd 2>/dev/null || rc=$?
    if [[ $rc -ne 0 ]]; then
        # Another firing has held this pair's step lock for the whole wait:
        # it is starting, so this one is the overlap the guard exists to stop.
        WAKE_BATCH_GUARD_HOLDER_PID="$(pgai_lock_field "$(pgai_lock_read_provenance "$step_lock" 2>/dev/null)" pid 2>/dev/null)"
        WAKE_BATCH_GUARD_HOLDER_AGE="0s"
        [[ $rc -eq 1 ]] && return 1
        return 2
    fi

    local content verdict holder_pid
    content="$(pgai_lock_read_provenance "$record" 2>/dev/null)" || content=""
    if [[ -n "$content" ]]; then
        verdict="$(pgai_lock_pid_liveness_verdict "$content")"
        holder_pid="$(pgai_lock_field "$content" pid)"
        if [[ "$verdict" == "held" && "$holder_pid" != "$pid" ]]; then
            local held
            held="$(pgai_lock_held_seconds "$content" 2>/dev/null)" || held=""
            WAKE_BATCH_GUARD_HOLDER_PID="$holder_pid"
            WAKE_BATCH_GUARD_HOLDER_AGE="${held:+${held}s}"
            WAKE_BATCH_GUARD_HOLDER_AGE="${WAKE_BATCH_GUARD_HOLDER_AGE:-unknown}"
            pgai_lock_release "$step_lock" "$step_fd" 2>/dev/null || true
            return 1
        fi
    fi

    local staged="${record}.tmp.${pid}"
    if printf 'holder=%s\npid=%s\nop=%s\ntask=%s\nacquired_at=%s\npid_start=%s\nboot_id=%s\n' \
        "wake-batch" "$pid" "wake-batch:${agent}:${project}" "none" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "$(pgai_proc_start_time "$pid")" "$(pgai_boot_id)" > "$staged" 2>/dev/null \
        && mv -f "$staged" "$record" 2>/dev/null; then
        rc=0
    else
        rm -f "$staged" 2>/dev/null || true
        rc=2
    fi
    pgai_lock_release "$step_lock" "$step_fd" 2>/dev/null || true
    return "$rc"
}
