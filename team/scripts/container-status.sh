#!/usr/bin/env bash
# team/scripts/container-status.sh
# Show the kanban container's status in one block (or one JSON object).
#
# The host and the container are two ways to run the kanban, and exactly one
# runs at a time: the host runs on cron, the container on pseudocron. This
# script reports the container side:
#
#   container        the container named by kanban.cfg [docker]
#                    container_name (default pgai-kanban) or --name, and its
#                    state: running, stopped or absent
#   image            the image it runs
#   uptime           how long it has run (running only)
#   scheduler        whether the in-container pseudocron runs, with its pid
#   last heartbeat   the age of the last "fired (minute=NN)" line in
#                    <kanban root>/logs/pseudocron.log (read even when the
#                    container is down: it shows how long ago the board beat)
#   agent sessions   the agent dispatches running in the container, by role
#                    and task ID
#   host crontab     whether the invoking user's crontab carries kanban wake
#                    lines, with an ERROR line when it does while the
#                    container runs (two schedulers would drive one board)
#
# --json prints the same report as one JSON object for machine use. The
# crontab's wake lines are counted, never echoed: a crontab line may carry
# an operator's environment assignments.
#
# Read-only. It starts, stops, builds, pulls and removes nothing, so it is
# not gated by the container tier: it runs anywhere, inside an agent wake
# included. The engine calls it makes -- one "container inspect" and, while
# the container runs, one "exec NAME ps -eo pid=,args=" -- go through
# pgai_docker (lib/docker_use.sh) inside one docker-use session, so the
# docker-use log records who looked. Every field is parsed by
# pgai_agent_kanban.container.lifecycle (through pp_run_ops).
#
# Usage:
#   container-status.sh [--json] [--name NAME]
#   container-status.sh --help
#
# Exit codes:
#   0  the container runs with a live scheduler
#   3  it does not: absent, stopped, or running without a scheduler
#   1  error: the kanban root is missing; the engine is not on PATH; the
#      engine's state query or process listing failed; or the report could
#      not be assembled
#   2  usage error (unknown option, missing value)

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
# shellcheck source=lib/pp_run_ops.sh
source "${_SCRIPT_DIR}/lib/pp_run_ops.sh"
# shellcheck source=lib/ini_parser.sh
source "${_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/lib/temp.sh"

_MODULE="pgai_agent_kanban.container.lifecycle"
_DEFAULT_NAME="pgai-kanban"

_USAGE_TEXT="$(cat <<EOF
Usage: ${_SCRIPT_NAME} [OPTIONS]

Show the kanban container's status in one block: container state (running,
stopped or absent), image, uptime, whether the scheduler runs, the age of
its last heartbeat, the agent sessions running in it (role and task ID),
and whether the host crontab carries kanban wake lines -- an ERROR line
when it does while the container runs.

Options:
  --json        Print the report as one JSON object instead of the block.
  --name NAME   Container name. Default: kanban.cfg [docker]
                container_name, else ${_DEFAULT_NAME}.
  -h, --help    Print this usage and exit 0.

The heartbeat is read from \$PGAI_AGENT_KANBAN_ROOT_PATH/logs/pseudocron.log
and the agent sessions from \$PGAI_AGENT_KANBAN_ROOT_PATH/locks/task-liveness/,
so the kanban root must be the one the container mounts. The crontab is the
invoking user's ("crontab -l"); its wake lines are counted, never printed.

Read-only: starts, stops, builds and removes nothing, and runs anywhere,
inside an agent wake included. The engine is kanban.cfg [docker]
docker_exe (docker by default, podman when configured).

Exit codes: 0 running with a live scheduler; 3 absent, stopped, or running
without a scheduler; 1 error (kanban root missing, engine missing, an
engine query failed, or the report could not be assembled); 2 usage error.
EOF
)"

# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

# _refuse_usage MESSAGE
# A command line this script cannot read is refused before any engine call.
_refuse_usage() {
    local message="${_SCRIPT_NAME} refused: $1. No status was read."
    local remedy
    remedy="$(printf '%q --help' "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: invariant by design -- an unreadable command line is refused.
    pgai_emit_error_contract "container_status.usage" "${message}" "${remedy}"
    # CENSUS-ANCHOR: container-status-usage-01
    exit 2
}

# _refuse_engine WHAT DETAIL REMEDY
# An engine query the report depends on failed, so the status cannot be
# told: reporting "not running" here would be a guess.
_refuse_engine() {
    local message="${_SCRIPT_NAME}: ${1} failed: ${2:-no error text}. The container's status is unknown; nothing was changed."
    # BLOCK-WORTHY: verification unable to run -- the engine cannot be read.
    pgai_emit_error_contract "container_status.engine_failed" "${message}" "${3}"
    # CENSUS-ANCHOR: container-status-engine-failed-01
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
FORMAT="text"
NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            printf '%s\n' "${_USAGE_TEXT}"
            exit 0
            ;;
        --json)
            FORMAT="json"; shift
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
        *)
            _refuse_usage "unknown argument '$1'"
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Kanban root and configuration
# ---------------------------------------------------------------------------
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
if [[ -z "${_KANBAN_ROOT}" || ! -d "${_KANBAN_ROOT}" ]]; then
    _message="${_SCRIPT_NAME}: PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory ('${_KANBAN_ROOT}'). The heartbeat and the agent sessions are read from that root (logs/pseudocron.log, locks/task-liveness/) and the container name from its kanban.cfg, so no status can be reported without it. Point the variable at the kanban root the container mounts."
    _remedy="$(printf 'PGAI_AGENT_KANBAN_ROOT_PATH=%q %q' "${HOME:-/root}/pgai_agent_kanban" "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: verification unable to run -- heartbeat and sessions cannot be read.
    pgai_emit_error_contract "container_status.kanban_root_missing" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-status-kanban-root-missing-01
    exit 1
fi
_KANBAN_ROOT="$(realpath "${_KANBAN_ROOT}")"
_CFG="${_KANBAN_ROOT}/kanban.cfg"

if [[ -z "${NAME}" ]]; then
    NAME="$(read_ini "${_CFG}" docker container_name "${_DEFAULT_NAME}" 2>/dev/null || true)"
    NAME="${NAME:-${_DEFAULT_NAME}}"
fi

# ---------------------------------------------------------------------------
# Resolve the engine (kanban.cfg [docker] docker_exe, default docker).
# ---------------------------------------------------------------------------
_engine_rc=0
ENGINE="$(resolve_docker_exe "${_KANBAN_ROOT}" 2>/dev/null)" || _engine_rc=$?
if [[ "${_engine_rc}" -ne 0 ]]; then
    _configured="$(read_ini "${_CFG}" docker docker_exe "docker" 2>/dev/null || true)"
    _message="${_SCRIPT_NAME}: container engine '${_configured:-docker}' (kanban.cfg [docker] docker_exe in ${_CFG}, default docker) is not on PATH, so the container's status cannot be read. Install it, or set docker_exe to an engine that is."
    _remedy="command -v docker podman"
    # BLOCK-WORTHY: verification unable to run -- no engine to ask.
    pgai_emit_error_contract "container_status.engine_not_found" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-status-engine-not-found-01
    exit 1
fi

_WORK="$(pgai_mktemp_d container-status)"
trap 'rm -rf "${_WORK}"' EXIT
# One docker-use session covers both engine calls below (START now, FINISH
# with this script's exit code), so the log shows who looked.
docker_use_session_begin "${_SCRIPT_NAME}" "-"

# ---------------------------------------------------------------------------
# Engine queries
# ---------------------------------------------------------------------------

# _query_state: inspects the container into ${_WORK}/inspect.{out,err,rc}
# and sets _STATE (running|stopped|absent). A failed query that is not
# "no such container" is refused.
_query_state() {
    local rc=0 prc=0 parsed
    pgai_docker "${ENGINE}" container inspect "${NAME}" \
        >"${_WORK}/inspect.out" 2>"${_WORK}/inspect.err" || rc=$?
    printf '%s\n' "${rc}" > "${_WORK}/inspect.rc"
    parsed="$(pp_run_ops "${_MODULE}" container-state --name "${NAME}" --input "${_WORK}/inspect.out" \
        --exit-code "${rc}" --stderr-file "${_WORK}/inspect.err" 2>"${_WORK}/state.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "the state query for container '${NAME}'" \
            "$(_first_line "${_WORK}/state.err")" "$(printf '%q info' "${ENGINE}")"
    fi
    _STATE="$(_kv_get state "${parsed}")"
}

# _list_processes: writes the container's process table to ${_WORK}/ps.out.
# Returns the engine's exit status (stderr in ${_WORK}/ps.err).
_list_processes() {
    local rc=0
    pgai_docker "${ENGINE}" exec "${NAME}" ps -eo pid=,args= >"${_WORK}/ps.out" 2>"${_WORK}/ps.err" || rc=$?
    return "${rc}"
}

_query_state
_ps_args=()
if [[ "${_STATE}" == "running" ]]; then
    if _list_processes; then
        _ps_args=(--ps-file "${_WORK}/ps.out")
    else
        # The container may have stopped between the two calls; a listing
        # that fails on a container that still runs leaves the scheduler
        # and the sessions unknown.
        cp "${_WORK}/ps.err" "${_WORK}/ps-first.err"
        _query_state
        if [[ "${_STATE}" == "running" ]]; then
            _refuse_engine "listing the processes in container '${NAME}' (to find its scheduler and agent sessions)" \
                "$(_first_line "${_WORK}/ps-first.err")" \
                "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Host crontab: the invoking user's. A missing crontab binary, or "no
# crontab for USER", is a crontab with no wake lines.
# ---------------------------------------------------------------------------
: > "${_WORK}/crontab.txt"
if command -v crontab >/dev/null 2>&1; then
    crontab -l >"${_WORK}/crontab.txt" 2>/dev/null || : > "${_WORK}/crontab.txt"
fi

# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------
_report_rc=0
pp_run_ops "${_MODULE}" status --format "${FORMAT}" --name "${NAME}" \
    --kanban-root "${_KANBAN_ROOT}" \
    --input "${_WORK}/inspect.out" \
    --exit-code "$(<"${_WORK}/inspect.rc")" --stderr-file "${_WORK}/inspect.err" \
    ${_ps_args[@]+"${_ps_args[@]}"} \
    --crontab-file "${_WORK}/crontab.txt" \
    >"${_WORK}/report.out" 2>"${_WORK}/report.err" || _report_rc=$?

if [[ "${_report_rc}" -ne 0 && "${_report_rc}" -ne 3 ]]; then
    _message="${_SCRIPT_NAME}: the status report for container '${NAME}' could not be assembled (${_MODULE} status exited ${_report_rc}): $(_first_line "${_WORK}/report.err" || true). Nothing was changed."
    _remedy="$(printf 'PYTHONPATH=%q python3 -m %s status --help' "$(pp_pythonpath)" "${_MODULE}")"
    # BLOCK-WORTHY: verification unable to run -- the report cannot be built.
    pgai_emit_error_contract "container_status.report_failed" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-status-report-failed-01
    exit 1
fi

cat "${_WORK}/report.out"
exit "${_report_rc}"
