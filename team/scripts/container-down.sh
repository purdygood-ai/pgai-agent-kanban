#!/usr/bin/env bash
# team/scripts/container-down.sh
# Soft-stop the kanban container: scheduler first, agents finish, then stop.
#
# The host and the container are two ways to run the kanban, and exactly one
# runs at a time. This script ends the container side without cutting an
# agent off mid-task:
#
#   1. Stops the in-container scheduler first so no new wake starts: every
#      pseudocron process listed is sent SIGTERM, and once it has exited the
#      tmux session "pseudocron" that container-up.sh starts is closed if it
#      is still open. The order matters: closing the session while the
#      scheduler runs hangs up its pane and everything in the pane's process
#      group.
#   2. Waits until the container is idle -- no agent session, no wake
#      driver (wake-batch.sh, wake.sh, wake-now.sh) and no OVERWATCH sweep
#      (overwatch-sweep.sh) still running, since a driver finalizes its task
#      after the agent CLI exits and may start the next one, and a sweep
#      stopped mid-repair leaves its project HALT behind -- up to kanban.cfg
#      [docker] container_stop_timeout_seconds
#      (default 1800), printing a progress line when the wait starts and
#      once every --progress-interval seconds (default 60) after that. When
#      the timeout elapses with either still running, it says so, naming
#      them, and carries on.
#   3. Stops and removes the container named by kanban.cfg [docker]
#      container_name (default pgai-kanban) or --name.
#
# --now skips the wait: the scheduler is still stopped first, the sessions
# and wake drivers still running are named, and the container is stopped
# and removed at once. A container that is already stopped is removed; one
# that does not exist is reported and the script exits 0 -- stopping what
# is not running is not an error.
#
# A running agent session is a liveness record under
# <kanban root>/locks/task-liveness/ whose pid is in the container's own
# process table, and a running wake driver is a wake script's process in
# that table (pgai_agent_kanban.container.lifecycle "sessions" reads both),
# so the kanban root this script reads must be the one the container mounts.
#
# Container state, the scheduler's processes and the agent sessions are read
# by pgai_agent_kanban.container.lifecycle (through pp_run_ops); every engine
# call goes through pgai_docker (lib/docker_use.sh) inside one docker-use
# session, so the docker-use log records the run. Stopping a container is
# container-tier work and operator-only (lib/container_tier.sh): inside an
# agent wake, or without PGAI_CONTAINER_TIER=1 / --operator-approved, it
# prints one SKIPPED line and exits 0 having touched nothing. A HALT file is
# never read, written or removed here.
#
# Usage:
#   container-down.sh [--now] [--name NAME] [--progress-interval SECONDS]
#                     [--operator-approved]
#   container-down.sh --help
#
# Exit codes:
#   0  the container was stopped and removed (after the wait, after the
#      timeout, or at once with --now); it did not exist; or the run was
#      SKIPPED (not authorized)
#   1  the kanban root is missing; the engine is not on PATH or one of its
#      commands failed (the container is left as it was at that point)
#   2  usage error (unknown option, missing or malformed value) or a
#      kanban.cfg [docker] container_stop_timeout_seconds that is not whole
#      seconds

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SCRIPT_PATH="${_SCRIPT_DIR}/${_SCRIPT_NAME}"

# shellcheck source=lib/error_contract.sh
source "${_SCRIPT_DIR}/lib/error_contract.sh"
# shellcheck source=lib/docker_engine.sh
source "${_SCRIPT_DIR}/lib/docker_engine.sh"
# shellcheck source=lib/docker_use.sh
source "${_SCRIPT_DIR}/lib/docker_use.sh"
# shellcheck source=lib/container_tier.sh
source "${_SCRIPT_DIR}/lib/container_tier.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_SCRIPT_DIR}/lib/pp_run_ops.sh"
# shellcheck source=lib/ini_parser.sh
source "${_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/lib/temp.sh"

_MODULE="pgai_agent_kanban.container.lifecycle"
_DEFAULT_NAME="pgai-kanban"
_DEFAULT_STOP_TIMEOUT=1800
_DEFAULT_PROGRESS_INTERVAL=60
# How often the wait re-reads the process table (never longer than the
# progress interval or the time left).
_POLL_SECONDS=5
# How long a scheduler sent SIGTERM gets to leave the process table.
_SCHEDULER_EXIT_CHECKS=5
_SCHEDULER_SESSION="pseudocron"

_USAGE_TEXT="$(cat <<EOF
Usage: ${_SCRIPT_NAME} [OPTIONS]

Soft-stop the kanban container: stop its scheduler so no new wake starts
(SIGTERM to the pseudocron process, then close its tmux session
"pseudocron" once it has exited), wait for the agent sessions and wake
drivers (wake-batch.sh, wake.sh, wake-now.sh) already running in it to
finish, then stop and remove the container.

The wait lasts at most kanban.cfg [docker] container_stop_timeout_seconds
(default ${_DEFAULT_STOP_TIMEOUT}) and prints a progress line when it starts
and once per progress interval. When the timeout elapses with sessions or
drivers still running, they are named and the container is stopped anyway.
A stopped container is removed; an absent one is reported (exit 0).

Options:
  --now                       Skip the wait: stop the scheduler, then stop
                              and remove the container at once, ending any
                              agent session or wake driver still running.
  --name NAME                 Container name. Default: kanban.cfg [docker]
                              container_name, else ${_DEFAULT_NAME}.
  --progress-interval SECONDS Seconds between progress lines while waiting
                              (whole seconds, at least 1). Default:
                              ${_DEFAULT_PROGRESS_INTERVAL}.
  --operator-approved         Authorize this run without PGAI_CONTAINER_TIER=1.
  -h, --help                  Print this usage and exit 0.

Agent sessions are the liveness records under
\$PGAI_AGENT_KANBAN_ROOT_PATH/locks/task-liveness/ whose process runs in the
container; wake drivers are the wake scripts' processes in the container's
process table. A HALT file is never written or removed.

The engine is kanban.cfg [docker] docker_exe (docker by default, podman
when configured). Operator-only: inside an agent wake, or without
PGAI_CONTAINER_TIER=1 or --operator-approved, it prints one SKIPPED line
and exits 0.

Exit codes: 0 stopped and removed, absent, or skipped; 1 refused (kanban
root missing, engine missing or an engine command failed); 2 usage error
or a malformed container_stop_timeout_seconds.
EOF
)"

# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

# _refuse_usage MESSAGE
# A command line this script cannot read is refused before anything runs.
_refuse_usage() {
    local message="${_SCRIPT_NAME} refused: $1. Nothing was stopped."
    local remedy
    remedy="$(printf '%q --help' "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: invariant by design -- an unreadable command line is refused.
    pgai_emit_error_contract "container_down.usage" "${message}" "${remedy}"
    # CENSUS-ANCHOR: container-down-usage-01
    exit 2
}

# _refuse_engine WHAT DETAIL REMEDY
# An engine command the stop depends on failed. Nothing after it runs, so
# agent sessions are never ended by a stop that could not see them.
_refuse_engine() {
    local message="${_SCRIPT_NAME} refused: ${1} failed: ${2:-no error text}. The container is left as it is now."
    # BLOCK-WORTHY: the container cannot be stopped safely; the engine's own error is named.
    pgai_emit_error_contract "container_down.engine_failed" "${message}" "${3}"
    # CENSUS-ANCHOR: container-down-engine-failed-01
    exit 1
}

# _first_line FILE -- the first non-blank line of FILE (empty when none).
_first_line() {
    [[ -r "$1" ]] || return 0
    local line
    while IFS= read -r line || [[ -n "${line}" ]]; do
        [[ -n "${line//[[:space:]]/}" ]] && { printf '%s\n' "${line}"; return 0; }
    done < "$1"
    return 0
}

# _kv_get KEY TEXT -- the value of the first "KEY=value" line in TEXT.
_kv_get() {
    local key="$1" line
    while IFS= read -r line; do
        if [[ "${line}" == "${key}="* ]]; then
            printf '%s\n' "${line#"${key}="}"
            return 0
        fi
    done <<< "$2"
    return 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
_ORIGINAL_ARGS=("$@")
container_tier_take_args "$@"
set -- ${CONTAINER_TIER_REST_ARGS[@]+"${CONTAINER_TIER_REST_ARGS[@]}"}

NOW=0
NAME=""
PROGRESS_INTERVAL="${_DEFAULT_PROGRESS_INTERVAL}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            printf '%s\n' "${_USAGE_TEXT}"
            exit 0
            ;;
        --now)
            NOW=1; shift
            ;;
        --name)
            [[ $# -ge 2 && -n "$2" ]] || _refuse_usage "--name requires a value (e.g. --name pgai-kanban)"
            NAME="$2"; shift 2
            ;;
        --name=*)
            NAME="${1#--name=}"
            [[ -n "${NAME}" ]] || _refuse_usage "--name requires a value (e.g. --name pgai-kanban)"
            shift
            ;;
        --progress-interval)
            [[ $# -ge 2 ]] || _refuse_usage "--progress-interval requires a value in whole seconds (e.g. --progress-interval 60)"
            PROGRESS_INTERVAL="$2"; shift 2
            ;;
        --progress-interval=*)
            PROGRESS_INTERVAL="${1#--progress-interval=}"; shift
            ;;
        *)
            _refuse_usage "unknown argument '$1'"
            ;;
    esac
done

if [[ ${#CONTAINER_TIER_IMAGE_REQUEST[@]} -gt 0 ]]; then
    _refuse_usage "--image selects images for the container tier's builds; this script stops a container and takes no image (name the container with --name, e.g. --name pgai-kanban)"
fi
if [[ ! "${PROGRESS_INTERVAL}" =~ ^[0-9]+$ ]] || (( 10#${PROGRESS_INTERVAL} < 1 )); then
    _refuse_usage "--progress-interval '${PROGRESS_INTERVAL}' is not a whole number of seconds of at least 1 (e.g. --progress-interval 60)"
fi
PROGRESS_INTERVAL=$((10#${PROGRESS_INTERVAL}))

# ---------------------------------------------------------------------------
# Operator-only: prints SKIPPED and exits 0 when not authorized (agent wake).
# ---------------------------------------------------------------------------
container_tier_require_operator "${_SCRIPT_PATH}" ${_ORIGINAL_ARGS[@]+"${_ORIGINAL_ARGS[@]}"}

# ---------------------------------------------------------------------------
# Kanban root and configuration
# ---------------------------------------------------------------------------
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
if [[ -z "${_KANBAN_ROOT}" || ! -d "${_KANBAN_ROOT}" ]]; then
    _message="${_SCRIPT_NAME} refused: PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory ('${_KANBAN_ROOT}'). The agent sessions the soft stop waits for are read from that root's locks/task-liveness/, so without it every session would be ended unseen. Nothing was stopped; point the variable at the kanban root the container mounts."
    _remedy="$(printf 'PGAI_AGENT_KANBAN_ROOT_PATH=%q %q' "${HOME:-/root}/pgai_agent_kanban" "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: verification unable to run -- running agent sessions cannot be listed.
    pgai_emit_error_contract "container_down.kanban_root_missing" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-down-kanban-root-missing-01
    exit 1
fi
_KANBAN_ROOT="$(realpath "${_KANBAN_ROOT}")"
_CFG="${_KANBAN_ROOT}/kanban.cfg"

if [[ -z "${NAME}" ]]; then
    NAME="$(read_ini "${_CFG}" docker container_name "${_DEFAULT_NAME}" 2>/dev/null || true)"
    NAME="${NAME:-${_DEFAULT_NAME}}"
fi

STOP_TIMEOUT="$(read_ini "${_CFG}" docker container_stop_timeout_seconds "${_DEFAULT_STOP_TIMEOUT}" 2>/dev/null || true)"
STOP_TIMEOUT="${STOP_TIMEOUT:-${_DEFAULT_STOP_TIMEOUT}}"
if [[ ! "${STOP_TIMEOUT}" =~ ^[0-9]+$ ]]; then
    _message="${_SCRIPT_NAME} refused: kanban.cfg [docker] container_stop_timeout_seconds in ${_CFG} is '${STOP_TIMEOUT}', which is not a whole number of seconds (decimal digits only, no unit suffix). Nothing was stopped. The remedy sets it to the default, ${_DEFAULT_STOP_TIMEOUT}; --now skips the wait instead."
    _remedy="$(printf "sed -i -E 's/^[[:space:]]*container_stop_timeout_seconds[[:space:]]*=.*/container_stop_timeout_seconds = %s/' %q" \
        "${_DEFAULT_STOP_TIMEOUT}" "${_CFG}")"
    # BLOCK-WORTHY: invariant by design -- a soft-stop bound that cannot be read is not guessed.
    pgai_emit_error_contract "container_down.stop_timeout_invalid" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-down-stop-timeout-invalid-01
    exit 2
fi
STOP_TIMEOUT=$((10#${STOP_TIMEOUT}))

# ---------------------------------------------------------------------------
# Resolve the engine (kanban.cfg [docker] docker_exe, default docker).
# ---------------------------------------------------------------------------
_engine_rc=0
ENGINE="$(resolve_docker_exe "${_KANBAN_ROOT}")" || _engine_rc=$?
if [[ "${_engine_rc}" -ne 0 ]]; then
    _configured="$(read_ini "${_CFG}" docker docker_exe "docker" 2>/dev/null || true)"
    _message="${_SCRIPT_NAME} refused: container engine '${_configured:-docker}' (kanban.cfg [docker] docker_exe in ${_CFG}, default docker) is not on PATH. Install it, or set docker_exe to an engine that is. Nothing was stopped."
    _remedy="command -v docker podman"
    # BLOCK-WORTHY: no engine, so the container cannot be stopped at all.
    pgai_emit_error_contract "container_down.engine_not_found" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-down-engine-not-found-01
    exit 1
fi

_WORK="$(pgai_mktemp_d container-down)"
trap 'rm -rf "${_WORK}"' EXIT
# One docker-use session covers every engine call below (START now, FINISH
# with this script's exit code).
docker_use_session_begin "${_SCRIPT_NAME}" "-"

# ---------------------------------------------------------------------------
# Engine queries
# ---------------------------------------------------------------------------

# _query_state: sets _STATE (running|stopped|absent) and _STATE_IMAGE.
_query_state() {
    local out="${_WORK}/inspect.out" err="${_WORK}/inspect.err" rc=0 prc=0 parsed
    pgai_docker "${ENGINE}" container inspect "${NAME}" >"${out}" 2>"${err}" || rc=$?
    parsed="$(pp_run_ops "${_MODULE}" container-state --name "${NAME}" --input "${out}" \
        --exit-code "${rc}" --stderr-file "${err}" 2>"${_WORK}/state.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "the state query for container '${NAME}'" \
            "$(_first_line "${_WORK}/state.err")" "$(printf '%q info' "${ENGINE}")"
    fi
    _STATE="$(_kv_get state "${parsed}")"
    _STATE_IMAGE="$(_kv_get image "${parsed}")"
}

# _list_processes: writes the container's process table to ${_WORK}/ps.out.
# Returns the engine's exit status (stderr in ${_WORK}/ps.err).
_list_processes() {
    local rc=0
    pgai_docker "${ENGINE}" exec "${NAME}" ps -eo pid=,args= >"${_WORK}/ps.out" 2>"${_WORK}/ps.err" || rc=$?
    return "${rc}"
}

# _parse_scheduler: sets _SCHEDULER_PIDS (space-separated; empty when none)
# from the last process table.
_parse_scheduler() {
    local prc=0 parsed
    parsed="$(pp_run_ops "${_MODULE}" scheduler --input "${_WORK}/ps.out" 2>"${_WORK}/sched.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "reading the process table of container '${NAME}' (to find its scheduler)" \
            "$(_first_line "${_WORK}/sched.err")" "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
    fi
    _SCHEDULER_PIDS="$(_kv_get pids "${parsed}")"
    _SCHEDULER_PIDS="${_SCHEDULER_PIDS//,/ }"
}

# _parse_sessions: from the last process table and the kanban root's
# liveness records, sets _SESSION_COUNT and _SESSION_TASKS (comma-separated
# task IDs), _DRIVER_COUNT and _DRIVER_NAMES (comma-separated "script pid
# N"), _SWEEP_COUNT and _SWEEP_PIDS (comma-separated "pid N" of running
# OVERWATCH sweeps), and two phrases naming whatever still runs -- _BUSY_TEXT for the
# progress lines, _RUNNING_TEXT for the timeout line -- both empty when the
# container is idle.
_parse_sessions() {
    local prc=0 parsed line task driver pid
    parsed="$(pp_run_ops "${_MODULE}" sessions --kanban-root "${_KANBAN_ROOT}" \
        --input "${_WORK}/ps.out" 2>"${_WORK}/sessions.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "listing the agent sessions in container '${NAME}'" \
            "$(_first_line "${_WORK}/sessions.err")" \
            "$(printf 'PYTHONPATH=%q python3 -m %s sessions --help' "$(pp_pythonpath)" "${_MODULE}")"
    fi
    _SESSION_COUNT=0
    _SESSION_TASKS=""
    _DRIVER_COUNT=0
    _DRIVER_NAMES=""
    _SWEEP_COUNT=0
    _SWEEP_PIDS=""
    while IFS= read -r line; do
        if [[ "${line}" == *task=* ]]; then
            task="${line#*task=}"
            task="${task%% *}"
            _SESSION_COUNT=$((_SESSION_COUNT + 1))
            _SESSION_TASKS="${_SESSION_TASKS:+${_SESSION_TASKS}, }${task}"
        elif [[ "${line}" == driver=* ]]; then
            driver="${line#driver=}"
            driver="${driver%% *}"
            pid="${line##*pid=}"
            pid="${pid%% *}"
            _DRIVER_COUNT=$((_DRIVER_COUNT + 1))
            _DRIVER_NAMES="${_DRIVER_NAMES:+${_DRIVER_NAMES}, }${driver} pid ${pid}"
        elif [[ "${line}" == sweep=* ]]; then
            pid="${line##*pid=}"
            pid="${pid%% *}"
            _SWEEP_COUNT=$((_SWEEP_COUNT + 1))
            _SWEEP_PIDS="${_SWEEP_PIDS:+${_SWEEP_PIDS}, }pid ${pid}"
        fi
    done <<< "${parsed}"
    _BUSY_TEXT=""
    _RUNNING_TEXT=""
    if (( _SESSION_COUNT > 0 )); then
        _BUSY_TEXT="${_SESSION_COUNT} agent session(s) to exit: ${_SESSION_TASKS}"
        _RUNNING_TEXT="${_SESSION_COUNT} agent session(s) (${_SESSION_TASKS})"
    fi
    if (( _DRIVER_COUNT > 0 )); then
        _BUSY_TEXT="${_BUSY_TEXT:+${_BUSY_TEXT}; }${_DRIVER_COUNT} wake driver process(es) to finish: ${_DRIVER_NAMES}"
        _RUNNING_TEXT="${_RUNNING_TEXT:+${_RUNNING_TEXT} and }${_DRIVER_COUNT} wake driver process(es) (${_DRIVER_NAMES})"
    fi
    if (( _SWEEP_COUNT > 0 )); then
        _BUSY_TEXT="${_BUSY_TEXT:+${_BUSY_TEXT}; }${_SWEEP_COUNT} OVERWATCH sweep(s) to finish: overwatch-sweep.sh ${_SWEEP_PIDS}"
        _RUNNING_TEXT="${_RUNNING_TEXT:+${_RUNNING_TEXT} and }${_SWEEP_COUNT} OVERWATCH sweep(s) (overwatch-sweep.sh ${_SWEEP_PIDS})"
    fi
}

# _refresh_sessions: re-reads the process table and the sessions. Returns 1
# (with _STATE updated) when the container stopped running meanwhile; an
# engine failure on a container that still runs is refused, because the
# sessions it would end cannot be seen.
_refresh_sessions() {
    if ! _list_processes; then
        _query_state
        [[ "${_STATE}" != "running" ]] && return 1
        _refuse_engine "listing the processes in container '${NAME}' (to find its agent sessions)" \
            "$(_first_line "${_WORK}/ps.err")" "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
    fi
    _parse_sessions
    return 0
}

# _stop_scheduler: ends the scheduler so no new wake starts. Every
# scheduler process listed (in the tmux session or started by hand) is sent
# SIGTERM first and given _SCHEDULER_EXIT_CHECKS seconds to leave the
# process table; only then is the tmux session closed, when it is still
# open. Closing the session first would hang up its pane, and a wake driver
# the scheduler started in that pane's process group would die with it.
_stop_scheduler() {
    local rc=0 check had_session=0
    if pgai_docker "${ENGINE}" exec "${NAME}" tmux has-session -t "${_SCHEDULER_SESSION}" >/dev/null 2>&1; then
        had_session=1
    fi

    for (( check = 0; check <= _SCHEDULER_EXIT_CHECKS; check++ )); do
        if ! _list_processes; then
            _refuse_engine "listing the processes in container '${NAME}' (to find its scheduler)" \
                "$(_first_line "${_WORK}/ps.err")" "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
        fi
        _parse_scheduler
        [[ -z "${_SCHEDULER_PIDS}" ]] && break
        if (( check == 0 )); then
            # shellcheck disable=SC2086 # one argument per pid
            pgai_docker "${ENGINE}" exec "${NAME}" kill ${_SCHEDULER_PIDS} >/dev/null 2>"${_WORK}/kill.err" || true
            echo "${_SCRIPT_NAME}: sent SIGTERM to scheduler process(es) ${_SCHEDULER_PIDS}"
        elif (( check < _SCHEDULER_EXIT_CHECKS )); then
            sleep 1
        fi
    done
    if [[ -n "${_SCHEDULER_PIDS}" ]]; then
        # shellcheck disable=SC2086 # one argument per pid
        _refuse_engine "stopping the scheduler in container '${NAME}' (pid ${_SCHEDULER_PIDS} still running ${_SCHEDULER_EXIT_CHECKS}s after SIGTERM)" \
            "$(_first_line "${_WORK}/kill.err")" \
            "$(printf '%q exec %q kill -KILL %s' "${ENGINE}" "${NAME}" "${_SCHEDULER_PIDS}")"
    fi

    (( had_session )) || return 0
    # The scheduler has exited; its session usually closed with it. A
    # kill-session that fails is a fault only while the session still runs.
    pgai_docker "${ENGINE}" exec "${NAME}" tmux kill-session -t "${_SCHEDULER_SESSION}" \
        >/dev/null 2>"${_WORK}/tmux.err" || rc=$?
    if [[ "${rc}" -ne 0 ]] \
        && pgai_docker "${ENGINE}" exec "${NAME}" tmux has-session -t "${_SCHEDULER_SESSION}" >/dev/null 2>&1; then
        _refuse_engine "closing the scheduler's tmux session '${_SCHEDULER_SESSION}' in container '${NAME}'" \
            "$(_first_line "${_WORK}/tmux.err")" \
            "$(printf '%q exec %q tmux kill-session -t %q' "${ENGINE}" "${NAME}" "${_SCHEDULER_SESSION}")"
    fi
    echo "${_SCRIPT_NAME}: scheduler stopped (tmux session '${_SCHEDULER_SESSION}' closed after the scheduler exited); no new wake starts"
}

# _wait_for_sessions: polls until neither an agent session nor a wake driver
# runs, the container stops on its own, or STOP_TIMEOUT elapses; prints
# progress at the start and once per PROGRESS_INTERVAL.
_wait_for_sessions() {
    local start now deadline next_progress wait
    start="$(date +%s)"
    deadline=$(( start + STOP_TIMEOUT ))
    next_progress="${start}"
    while :; do
        if ! _refresh_sessions; then
            echo "${_SCRIPT_NAME}: container '${NAME}' is no longer running (${_STATE:-unknown}); ending the wait"
            return 0
        fi
        now="$(date +%s)"
        if (( _SESSION_COUNT == 0 && _DRIVER_COUNT == 0 && _SWEEP_COUNT == 0 )); then
            echo "${_SCRIPT_NAME}: no agent session is running and no wake driver or OVERWATCH sweep remains in '${NAME}' ($(( now - start ))s waited)"
            return 0
        fi
        if (( now >= deadline )); then
            echo "${_SCRIPT_NAME}: timeout: ${_RUNNING_TEXT} still running after ${STOP_TIMEOUT}s ([docker] container_stop_timeout_seconds); stopping the container anyway"
            return 0
        fi
        if (( now >= next_progress )); then
            echo "${_SCRIPT_NAME}: waiting for ${_BUSY_TEXT} ($(( now - start ))s waited of at most ${STOP_TIMEOUT}s)"
            next_progress=$(( now + PROGRESS_INTERVAL ))
        fi
        wait=$(( deadline - now ))
        (( next_progress - now < wait )) && wait=$(( next_progress - now ))
        (( wait > _POLL_SECONDS )) && wait="${_POLL_SECONDS}"
        (( wait < 1 )) && wait=1
        sleep "${wait}"
    done
}

# _stop_and_remove: stops the container when it runs, then removes it.
_stop_and_remove() {
    local rc=0
    if [[ "${_STATE}" == "running" ]]; then
        pgai_docker "${ENGINE}" stop "${NAME}" >/dev/null 2>"${_WORK}/stop.err" || rc=$?
        if [[ "${rc}" -ne 0 ]]; then
            _refuse_engine "stopping container '${NAME}'" \
                "$(_first_line "${_WORK}/stop.err")" "$(printf '%q rm -f %q' "${ENGINE}" "${NAME}")"
        fi
        echo "${_SCRIPT_NAME}: container '${NAME}' stopped"
    fi
    rc=0
    pgai_docker "${ENGINE}" rm "${NAME}" >/dev/null 2>"${_WORK}/rm.err" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        _refuse_engine "removing container '${NAME}'" \
            "$(_first_line "${_WORK}/rm.err")" "$(printf '%q rm -f %q' "${ENGINE}" "${NAME}")"
    fi
    echo "${_SCRIPT_NAME}: container '${NAME}' removed"
}

# ---------------------------------------------------------------------------
# Bring the container down
# ---------------------------------------------------------------------------
if (( NOW )); then
    _mode="now"
else
    _mode="soft, waiting up to ${STOP_TIMEOUT}s"
fi
echo "${_SCRIPT_NAME}: engine=${ENGINE} container=${NAME} kanban_root=${_KANBAN_ROOT} mode=${_mode}"

_query_state
case "${_STATE}" in
    absent)
        echo "${_SCRIPT_NAME}: container '${NAME}' does not exist; nothing to stop"
        exit 0
        ;;
    running)
        _stop_scheduler
        if (( NOW )); then
            # Naming the sessions and drivers --now ends is information, not a check:
            # a listing that fails does not hold up the stop.
            if _list_processes; then
                _parse_sessions
                if (( _SESSION_COUNT > 0 )); then
                    echo "${_SCRIPT_NAME}: --now: stopping with ${_SESSION_COUNT} agent session(s) still running: ${_SESSION_TASKS}"
                fi
                if (( _DRIVER_COUNT > 0 )); then
                    echo "${_SCRIPT_NAME}: --now: stopping with ${_DRIVER_COUNT} wake driver process(es) still running: ${_DRIVER_NAMES}"
                fi
                if (( _SWEEP_COUNT > 0 )); then
                    echo "${_SCRIPT_NAME}: --now: stopping with ${_SWEEP_COUNT} OVERWATCH sweep(s) still running: overwatch-sweep.sh ${_SWEEP_PIDS}"
                fi
            fi
        else
            _wait_for_sessions
        fi
        ;;
    *)
        echo "${_SCRIPT_NAME}: container '${NAME}' is already stopped (image ${_STATE_IMAGE:-unknown}); removing it"
        ;;
esac

_stop_and_remove
echo "${_SCRIPT_NAME}: container '${NAME}' is down"
exit 0
