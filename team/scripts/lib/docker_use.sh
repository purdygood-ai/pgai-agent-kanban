#!/usr/bin/env bash
# team/scripts/lib/docker_use.sh
#
# The one path from kanban code to a container engine, and the log that
# records who used it.
#
# Every kanban-originated container-engine command (docker or podman)
# runs through pgai_docker, and every container-tier run is bracketed by a
# docker-use session. Both append to one append-only log,
# $PGAI_AGENT_KANBAN_ROOT_PATH/logs/docker-use.log, so the log shows who
# reached for a container engine -- operator or agent -- not only who ran
# one. team/scripts/lint_docker_helper_path.py checks that no script calls
# an engine directly outside this file.
#
# Log line format (one event per line, UTC timestamp first):
#   <ts> <EVENT> actor=<actor> task=<task-id|-> script=<name> images=<a,b|-> [key=value ...]
#
#   EVENT    START    a container-tier run (or a standalone engine
#                     command) began
#            FINISH   it ended; adds duration=<N>s exit=<code>
#            SKIPPED  a container-tier harness was called without operator
#                     authorization and did nothing
#            REFUSED  a harness refused before building (e.g. an image
#                     name the manifest does not declare); adds reason=
#   actor    "operator" for a call from outside any agent wake; the agent
#            role (PGAI_AGENT_ROLE, e.g. "coder") inside one, or "agent"
#            when the role is not known
#   task     PGAI_TASK_ID inside a wake, else "-"
#
# Enforcement class: non-blocking. A log directory that is missing is
# created; one that cannot be created or written produces one narrated
# warning on stderr per process and the caller carries on exactly as it
# would have -- a log failure never changes an exit code.
#
# Public API
# ----------
#   docker_use_log_path
#       Echoes the log's absolute path.
#
#   docker_use_record <event> <script-name> <images> [key=value ...]
#       Appends one event line. Always returns 0.
#
#   docker_use_session_begin <script-name> <images>
#       Records START and arranges for FINISH (duration, exit code) to be
#       recorded when the calling script exits, chaining any EXIT trap the
#       script already set. A nested run (a harness started by another
#       harness that already opened a session) records nothing of its
#       own: the outer session covers it. Call it once, after the run is
#       authorized and its images are selected, before the first engine
#       command.
#
#   pgai_docker <engine> <subcommand> [args...]
#       Runs "<engine> <subcommand> [args...]" and returns its exit status.
#       Inside a docker-use session it only runs the command. Outside one,
#       a state-changing command (build, run, pull, create, rm, ...) is
#       recorded as its own START/FINISH pair; read-only probes (info,
#       version, ps, images, inspect, "compose ps") are not recorded.
#
#   docker_use_last_line
#       Echoes the log's last line, or nothing when there is no log.

[[ -n "${_PGAI_DOCKER_USE_SH_LOADED:-}" ]] && return 0
_PGAI_DOCKER_USE_SH_LOADED=1

_DOCKER_USE_WARNED=0

docker_use_log_path() {
    printf '%s/logs/docker-use.log\n' "${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
}

_docker_use_actor() {
    if [[ "${PGAI_AGENT_WAKE_ACTIVE:-0}" == "1" || -n "${PGAI_AGENT_ROLE:-}" ]]; then
        printf '%s\n' "${PGAI_AGENT_ROLE:-agent}"
    else
        printf 'operator\n'
    fi
}

docker_use_record() {
    local event="${1:-}" script_name="${2:--}" images="${3:--}"
    shift 3 2>/dev/null || shift $#
    [[ -z "$images" ]] && images="-"
    local log_path log_dir line
    log_path="$(docker_use_log_path)"
    log_dir="$(dirname "$log_path")"
    line="$(date -u +%Y-%m-%dT%H:%M:%SZ) ${event} actor=$(_docker_use_actor) task=${PGAI_TASK_ID:--} script=${script_name} images=${images}"
    if [[ $# -gt 0 ]]; then
        line="${line} $*"
    fi
    if { mkdir -p "$log_dir" && printf '%s\n' "$line" >> "$log_path"; } 2>/dev/null; then
        return 0
    fi
    if [[ "${_DOCKER_USE_WARNED}" -eq 0 ]]; then
        _DOCKER_USE_WARNED=1
        printf 'docker-use log: WARNING: cannot append to %s (directory missing or not writable); this run continues without a docker-use record. Fix: mkdir -p %q && chmod u+w %q\n' \
            "$log_path" "$log_dir" "$log_dir" >&2
    fi
    return 0
}

_docker_use_on_exit() {
    local rc=$?
    local -
    set +e
    if [[ -n "${_DOCKER_USE_SESSION_SCRIPT:-}" ]]; then
        local duration=$(( $(date +%s) - ${_DOCKER_USE_SESSION_START:-$(date +%s)} ))
        docker_use_record FINISH "${_DOCKER_USE_SESSION_SCRIPT}" "${_DOCKER_USE_SESSION_IMAGES:--}" \
            "duration=${duration}s" "exit=${rc}"
        _DOCKER_USE_SESSION_SCRIPT=""
    fi
    if [[ -n "${_DOCKER_USE_PREV_EXIT_TRAP:-}" ]]; then
        # Hand the previous handler the same $? it would have seen.
        ( exit "$rc" )
        eval "${_DOCKER_USE_PREV_EXIT_TRAP}"
    fi
    return "$rc"
}

docker_use_session_begin() {
    local script_name="${1:?docker_use_session_begin: script name required}"
    local images="${2:--}"
    [[ -z "$images" ]] && images="-"
    if [[ -n "${PGAI_DOCKER_USE_SESSION:-}" ]]; then
        return 0
    fi
    export PGAI_DOCKER_USE_SESSION="${script_name}:$$"
    _DOCKER_USE_SESSION_SCRIPT="$script_name"
    _DOCKER_USE_SESSION_IMAGES="$images"
    _DOCKER_USE_SESSION_START="$(date +%s)"
    docker_use_record START "$script_name" "$images"

    local -a _prev=()
    eval "_prev=($(trap -p EXIT))"
    _DOCKER_USE_PREV_EXIT_TRAP="${_prev[2]:-}"
    trap _docker_use_on_exit EXIT
    return 0
}

_docker_use_read_only() {
    case "${1:-}" in
        info|version|ps|images|inspect|events|logs|stats|top) return 0 ;;
        compose) [[ "${2:-}" == "ps" || "${2:-}" == "ls" || "${2:-}" == "version" ]] && return 0 ;;
        image|container) [[ "${2:-}" == "ls" || "${2:-}" == "list" || "${2:-}" == "inspect" ]] && return 0 ;;
    esac
    return 1
}

pgai_docker() {
    local engine="${1:?pgai_docker: engine required (docker, podman, or a configured binary)}"
    shift
    if [[ -n "${PGAI_DOCKER_USE_SESSION:-}" ]] || _docker_use_read_only "$@"; then
        "$engine" "$@"
        return $?
    fi
    local caller start rc
    caller="$(basename "${BASH_SOURCE[1]:-${0}}")"
    start="$(date +%s)"
    docker_use_record START "$caller" "-" "command=$(basename "$engine")_${1:-}"
    # The && / || form keeps a caller's `set -e` from ending the script
    # before the FINISH record is written; the status is returned as-is.
    "$engine" "$@" && rc=0 || rc=$?
    docker_use_record FINISH "$caller" "-" "command=$(basename "$engine")_${1:-}" \
        "duration=$(( $(date +%s) - start ))s" "exit=${rc}"
    return "$rc"
}

docker_use_last_line() {
    local log_path
    log_path="$(docker_use_log_path)"
    [[ -r "$log_path" ]] || return 0
    tail -n 1 "$log_path" 2>/dev/null || true
}
