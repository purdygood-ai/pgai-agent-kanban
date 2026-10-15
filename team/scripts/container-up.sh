#!/usr/bin/env bash
# team/scripts/container-up.sh
# Run the kanban detached in one named container, with its scheduler.
#
# The host and the container are two ways to run the kanban, and exactly one
# runs at a time: the host runs on cron, the container on pseudocron. This
# script starts the container side:
#
#   1. Refuses (exit 1, before any engine call) when the invoking user's
#      crontab carries a kanban wake line -- cron already drives this board,
#      and a container scheduler would be a second heartbeat on it. There is
#      no override flag; the refusal's remedy removes the wake lines.
#   2. Runs an EXISTING image -- kanban.cfg [docker] image (default
#      pgai-kanban:latest) or --image-tag -- detached as the container named
#      by kanban.cfg [docker] container_name (default pgai-kanban) or
#      --name, with the same mounts and environment as container-shell.sh
#      (lib/container_mounts.sh). It never builds or pulls: a missing image
#      is an error naming docker/build.sh, which creates images.
#   3. Starts exactly one scheduler inside it -- the detached tmux session
#      "pseudocron" running scripts/pseudocron.py, logging to
#      logs/pseudocron.log (docs/docker.md, "Start exactly one scheduler") --
#      and waits, bounded by --heartbeat-timeout, for its first heartbeat
#      (a "fired (minute=NN)" line written after the start).
#
# Idempotent: a container of that name already running with a live
# scheduler is reported and left alone (exit 0, no run, no second scheduler);
# one running without a scheduler gets a scheduler; a stopped one is removed
# and recreated.
#
# The image's ENTRYPOINT (docker/entrypoint.sh) ignores arguments and ends
# in "exec bash -l", which exits at once on a closed stdin. The detached run
# therefore keeps stdin open with a terminal (-d -i -t), so that login shell
# -- the container's main process -- waits instead of exiting; the
# scheduler runs beside it through "<engine> exec".
#
# Container state, the scheduler's process, the heartbeat and the crontab
# wake lines are read by pgai_agent_kanban.container.lifecycle (through
# pp_run_ops); every engine call goes through pgai_docker
# (lib/docker_use.sh) inside one docker-use session, so the docker-use log
# records the run. Starting a container is container-tier work and
# operator-only (lib/container_tier.sh): inside an agent wake, or without
# PGAI_CONTAINER_TIER=1 / --operator-approved, it prints one SKIPPED line
# and exits 0 having touched nothing. A HALT file is never read, written or
# removed here.
#
# Usage:
#   container-up.sh [--image-tag TAG] [--name NAME] [--mount HOST:CONTAINER]...
#                   [--heartbeat-timeout SECONDS] [--operator-approved]
#   container-up.sh --help
#
# Exit codes:
#   0  the container runs with a confirmed scheduler heartbeat; it was
#      already running with a live scheduler; or the run was SKIPPED (not
#      authorized)
#   1  the host crontab carries kanban wake lines; the image is missing;
#      the engine is not on PATH or one of its commands failed; or no
#      heartbeat was seen within --heartbeat-timeout (the container and its
#      scheduler are left running for inspection)
#   2  usage error (unknown option, missing or malformed value, malformed
#      --mount)

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
# shellcheck source=lib/container_mounts.sh
source "${_SCRIPT_DIR}/lib/container_mounts.sh"
# shellcheck source=lib/pp_run_ops.sh
source "${_SCRIPT_DIR}/lib/pp_run_ops.sh"
# shellcheck source=lib/ini_parser.sh
source "${_SCRIPT_DIR}/lib/ini_parser.sh"
# shellcheck source=lib/temp.sh
source "${_SCRIPT_DIR}/lib/temp.sh"

_MODULE="pgai_agent_kanban.container.lifecycle"
_DEFAULT_NAME="pgai-kanban"
_DEFAULT_IMAGE="pgai-kanban:latest"
# pseudocron fires on the minute (every shipped tier has a job each minute),
# so a healthy scheduler beats within about 61 seconds of starting.
_DEFAULT_HEARTBEAT_TIMEOUT=120
_HEARTBEAT_POLL_SECONDS=2
_SCHEDULER_SESSION="pseudocron"
# What the crontab-removal remedy filters out: every kanban wake entry point
# under scripts/ (a superset of the lines the lifecycle module detects).
_WAKE_LINE_ERE='scripts/wake(-batch|-now)?\.sh'

_USAGE_TEXT="$(cat <<EOF
Usage: ${_SCRIPT_NAME} [OPTIONS]

Run the kanban detached in one named container and start its scheduler
(the tmux session "pseudocron") inside it, then confirm the scheduler's
first heartbeat. Already running with a live scheduler: says so, exits 0.
A stopped container of that name is removed and recreated.

Refuses (status 1) while the host crontab carries kanban wake lines: the
host runs on cron or the container runs on pseudocron, never both.

Options:
  -t, --image-tag TAG         Image to run. It must already exist; create it
                              with docker/build.sh FLAVOR. Default: kanban.cfg
                              [docker] image, else ${_DEFAULT_IMAGE}.
  --name NAME                 Container name. Default: kanban.cfg [docker]
                              container_name, else ${_DEFAULT_NAME}.
  --mount HOST:CONTAINER      Extra bind mount. Repeatable.
  --heartbeat-timeout SECONDS How long to wait for the scheduler's first
                              heartbeat. Default: ${_DEFAULT_HEARTBEAT_TIMEOUT}.
  --operator-approved         Authorize this run without PGAI_CONTAINER_TIER=1.
  -h, --help                  Print this usage and exit 0.

Standard mounts and environment (lib/container_mounts.sh, the same as
container-shell.sh):
  \$HOME and \$PGAI_AGENT_KANBAN_ROOT_PATH, bind-mounted at the same paths,
  and exported as HOME and PGAI_AGENT_KANBAN_ROOT_PATH.

The engine is kanban.cfg [docker] docker_exe (docker by default, podman
when configured). Operator-only: inside an agent wake, or without
PGAI_CONTAINER_TIER=1 or --operator-approved, it prints one SKIPPED line
and exits 0.

Exit codes: 0 running with a confirmed heartbeat, already running, or
skipped; 1 refused (host cron wake lines, missing image, engine failure)
or no heartbeat within the timeout; 2 usage error.
EOF
)"

# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

# _refuse_usage MESSAGE
# A command line this script cannot read is refused before anything runs.
_refuse_usage() {
    local message="${_SCRIPT_NAME} refused: $1. Nothing was started."
    local remedy
    remedy="$(printf '%q --help' "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: invariant by design -- an unreadable command line is refused.
    pgai_emit_error_contract "container_up.usage" "${message}" "${remedy}"
    # CENSUS-ANCHOR: container-up-usage-01
    exit 2
}

# _refuse_engine WHAT DETAIL REMEDY
# An engine command the start depends on failed. Nothing after it runs, so a
# half-made start is never reported as up.
_refuse_engine() {
    local message="${_SCRIPT_NAME} refused: ${1} failed: ${2:-no error text}."
    # BLOCK-WORTHY: the container cannot be brought up; the engine's own error is named.
    pgai_emit_error_contract "container_up.engine_failed" "${message}" "${3}"
    # CENSUS-ANCHOR: container-up-engine-failed-01
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

IMAGE=""
NAME=""
EXTRA_MOUNTS=()
HEARTBEAT_TIMEOUT="${_DEFAULT_HEARTBEAT_TIMEOUT}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            printf '%s\n' "${_USAGE_TEXT}"
            exit 0
            ;;
        -t|--image-tag)
            [[ $# -ge 2 && -n "$2" ]] || _refuse_usage "$1 requires a value (e.g. $1 pgai-kanban:latest)"
            IMAGE="$2"; shift 2
            ;;
        --image-tag=*)
            IMAGE="${1#--image-tag=}"
            [[ -n "${IMAGE}" ]] || _refuse_usage "--image-tag requires a value (e.g. --image-tag pgai-kanban:latest)"
            shift
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
        --mount)
            [[ $# -ge 2 ]] || _refuse_usage "--mount requires a value (e.g. --mount /srv/data:/srv/data)"
            EXTRA_MOUNTS+=("$2"); shift 2
            ;;
        --mount=*)
            EXTRA_MOUNTS+=("${1#--mount=}"); shift
            ;;
        --heartbeat-timeout)
            [[ $# -ge 2 ]] || _refuse_usage "--heartbeat-timeout requires a value in whole seconds (e.g. --heartbeat-timeout 120)"
            HEARTBEAT_TIMEOUT="$2"; shift 2
            ;;
        --heartbeat-timeout=*)
            HEARTBEAT_TIMEOUT="${1#--heartbeat-timeout=}"; shift
            ;;
        *)
            _refuse_usage "unknown argument '$1'"
            ;;
    esac
done

if [[ ${#CONTAINER_TIER_IMAGE_REQUEST[@]} -gt 0 ]]; then
    _refuse_usage "--image selects images for the container tier's builds; name the image to run with --image-tag (e.g. --image-tag pgai-kanban:latest)"
fi
if [[ ! "${HEARTBEAT_TIMEOUT}" =~ ^[0-9]+$ ]]; then
    _refuse_usage "--heartbeat-timeout '${HEARTBEAT_TIMEOUT}' is not a whole number of seconds (e.g. --heartbeat-timeout 120)"
fi
HEARTBEAT_TIMEOUT=$((10#${HEARTBEAT_TIMEOUT}))

# ---------------------------------------------------------------------------
# Operator-only: prints SKIPPED and exits 0 when not authorized (agent wake).
# ---------------------------------------------------------------------------
container_tier_require_operator "${_SCRIPT_PATH}" ${_ORIGINAL_ARGS[@]+"${_ORIGINAL_ARGS[@]}"}

# ---------------------------------------------------------------------------
# Cron XOR pseudocron: refuse, before any engine call, while the invoking
# user's crontab carries a kanban wake line. A missing crontab binary, or
# "no crontab for USER", is a crontab with no wake lines.
# ---------------------------------------------------------------------------
_crontab_text=""
if command -v crontab >/dev/null 2>&1; then
    _crontab_text="$(crontab -l 2>/dev/null)" || _crontab_text=""
fi
_wake_rc=0
_wake_lines="$(printf '%s\n' "${_crontab_text}" | pp_run_ops "${_MODULE}" crontab-wake-lines 2>/dev/null)" || _wake_rc=$?
if [[ "${_wake_rc}" -ne 0 && "${_wake_rc}" -ne 3 ]]; then
    _message="${_SCRIPT_NAME} refused: the host crontab could not be checked for kanban wake lines (${_MODULE} crontab-wake-lines exited ${_wake_rc}), so starting a second scheduler cannot be ruled out. Nothing was started."
    _remedy="$(printf 'crontab -l | PYTHONPATH=%q python3 -m %s crontab-wake-lines' "$(pp_pythonpath)" "${_MODULE}")"
    # BLOCK-WORTHY: invariant by design -- cron XOR pseudocron cannot be verified, so nothing starts.
    pgai_emit_error_contract "container_up.crontab_check_failed" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-up-crontab-check-01
    exit 1
fi
if [[ "${_wake_rc}" -eq 0 ]]; then
    _wake_count=0
    while IFS= read -r _line; do
        [[ -n "${_line}" ]] && _wake_count=$((_wake_count + 1))
    done <<< "${_wake_lines}"
    _backup="${HOME}/crontab-before-container-up.bak"
    # The wake lines are counted, never echoed: a crontab line may carry an
    # operator's environment assignments.
    _message="${_SCRIPT_NAME} refused: the crontab of user ${USER:-$(id -un 2>/dev/null || echo unknown)} carries ${_wake_count} kanban wake line(s) (scripts/wake-batch.sh), so cron already drives this kanban and a container scheduler would be a second heartbeat on the same board (cron XOR pseudocron). Nothing was started. The remedy saves the crontab to ${_backup}, then reinstalls it without the wake lines; run container-up.sh again afterwards, and pass --wake-tier none to later upgrade.sh runs so an upgrade does not reinstall them."
    _remedy="$(printf 'crontab -l > %q && crontab -l | grep -Ev %s | crontab -' "${_backup}" "'${_WAKE_LINE_ERE}'")"
    # BLOCK-WORTHY: invariant by design -- one board, one scheduler; no override flag.
    pgai_emit_error_contract "container_up.host_cron_wake_lines" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-up-host-cron-01
    exit 1
fi

# ---------------------------------------------------------------------------
# Mounts and environment (lib/container_mounts.sh): a missing kanban root or
# HOME exits 1, a malformed --mount exits 2, each naming the value.
# ---------------------------------------------------------------------------
container_mounts_assemble "${_SCRIPT_NAME}" \
    "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" "${HOME:-}" ${EXTRA_MOUNTS[@]+"${EXTRA_MOUNTS[@]}"} || exit $?
_KANBAN_ROOT="${CONTAINER_MOUNTS_KANBAN_ROOT}"
_CFG="${_KANBAN_ROOT}/kanban.cfg"

if [[ -z "${NAME}" ]]; then
    NAME="$(read_ini "${_CFG}" docker container_name "${_DEFAULT_NAME}" 2>/dev/null || true)"
    NAME="${NAME:-${_DEFAULT_NAME}}"
fi
if [[ -z "${IMAGE}" ]]; then
    IMAGE="$(read_ini "${_CFG}" docker image "${_DEFAULT_IMAGE}" 2>/dev/null || true)"
    IMAGE="${IMAGE:-${_DEFAULT_IMAGE}}"
fi

# ---------------------------------------------------------------------------
# Resolve the engine (kanban.cfg [docker] docker_exe, default docker).
# ---------------------------------------------------------------------------
_engine_rc=0
ENGINE="$(resolve_docker_exe "${_KANBAN_ROOT}")" || _engine_rc=$?
if [[ "${_engine_rc}" -ne 0 ]]; then
    _configured="$(read_ini "${_CFG}" docker docker_exe "docker" 2>/dev/null || true)"
    _message="${_SCRIPT_NAME} refused: container engine '${_configured:-docker}' (kanban.cfg [docker] docker_exe in ${_CFG}, default docker) is not on PATH. Install it, or set docker_exe to an engine that is. Nothing was started."
    _remedy="command -v docker podman"
    # BLOCK-WORTHY: no engine, so the container cannot be started at all.
    pgai_emit_error_contract "container_up.engine_not_found" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: container-up-engine-not-found-01
    exit 1
fi

_WORK="$(pgai_mktemp_d container-up)"
trap 'rm -rf "${_WORK}"' EXIT
# One docker-use session covers every engine call below (START now, FINISH
# with this script's exit code).
docker_use_session_begin "${_SCRIPT_NAME}" "${IMAGE}"

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

# _query_scheduler: sets _SCHEDULER_PIDS (comma-separated; empty when none).
_query_scheduler() {
    local out="${_WORK}/ps.out" rc=0 prc=0 parsed
    pgai_docker "${ENGINE}" exec "${NAME}" ps -eo pid=,args= >"${out}" 2>"${_WORK}/ps.err" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        _refuse_engine "listing the processes in container '${NAME}' (to find its scheduler)" \
            "$(_first_line "${_WORK}/ps.err")" "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
    fi
    parsed="$(pp_run_ops "${_MODULE}" scheduler --input "${out}" 2>"${_WORK}/sched.err")" || prc=$?
    if [[ "${prc}" -ne 0 && "${prc}" -ne 3 ]]; then
        _refuse_engine "reading the process table of container '${NAME}'" \
            "$(_first_line "${_WORK}/sched.err")" "$(printf '%q exec %q ps -eo pid=,args=' "${ENGINE}" "${NAME}")"
    fi
    _SCHEDULER_PIDS="$(_kv_get pids "${parsed}")"
}

# _require_image: refuses, naming docker/build.sh, when IMAGE does not exist.
_require_image() {
    local rc=0
    pgai_docker "${ENGINE}" image inspect "${IMAGE}" >/dev/null 2>"${_WORK}/image.err" || rc=$?
    [[ "${rc}" -eq 0 ]] && return 0
    local repo="${IMAGE%:*}" tag="${IMAGE##*:}" build
    [[ "${IMAGE}" == *:* ]] || { repo="${IMAGE}"; tag="latest"; }
    if [[ "${repo}" == "pgai-kanban" && "${tag}" != "latest" ]]; then
        build="$(printf 'PGAI_CONTAINER_TIER=1 docker/build.sh debian --tag %q' "${tag}")"
    else
        build="PGAI_CONTAINER_TIER=1 docker/build.sh debian"
    fi
    local message="${_SCRIPT_NAME} refused: image '${IMAGE}' is not in the ${ENGINE} image store ($(_first_line "${_WORK}/image.err")). This script never builds or pulls; create the image with docker/build.sh FLAVOR from a pgai-agent-kanban repository checkout (debian, rocky9 or claude-abliterated), which tags pgai-kanban:FLAVOR and pgai-kanban:latest. Nothing was started."
    # BLOCK-WORTHY: there is no image to run, and creating one is docker/build.sh's job alone.
    pgai_emit_error_contract "container_up.image_missing" "${message}" "${build}"
    # CENSUS-ANCHOR: container-up-image-missing-01
    exit 1
}

# _start_scheduler: starts the one pseudocron tmux session; sets _SINCE to
# the start time, before which no heartbeat counts.
_start_scheduler() {
    mkdir -p "${_KANBAN_ROOT}/logs"
    _SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local command rc=0
    command="$(printf 'python3 %q >> %q 2>&1' \
        "${_KANBAN_ROOT}/scripts/pseudocron.py" "${_KANBAN_ROOT}/logs/pseudocron.log")"
    pgai_docker "${ENGINE}" exec -w "${_KANBAN_ROOT}" "${NAME}" \
        tmux new -d -s "${_SCHEDULER_SESSION}" "${command}" 2>"${_WORK}/tmux.err" || rc=$?
    if [[ "${rc}" -ne 0 ]]; then
        _refuse_engine "starting the scheduler (tmux session '${_SCHEDULER_SESSION}') in container '${NAME}'" \
            "$(_first_line "${_WORK}/tmux.err")" "$(printf '%q logs %q' "${ENGINE}" "${NAME}")"
    fi
    echo "${_SCRIPT_NAME}: scheduler started in tmux session '${_SCHEDULER_SESSION}' (log ${_KANBAN_ROOT}/logs/pseudocron.log)"
}

# _confirm_heartbeat: polls the pseudocron log (on the shared kanban root)
# for a heartbeat at or after _SINCE, for at most HEARTBEAT_TIMEOUT seconds.
_confirm_heartbeat() {
    local log="${_KANBAN_ROOT}/logs/pseudocron.log"
    local deadline=$(( $(date +%s) + HEARTBEAT_TIMEOUT )) out rc now wait
    echo "${_SCRIPT_NAME}: waiting up to ${HEARTBEAT_TIMEOUT}s for the scheduler's first heartbeat (it fires on the minute)"
    while :; do
        rc=0
        out="$(pp_run_ops "${_MODULE}" heartbeat --kanban-root "${_KANBAN_ROOT}" --since "${_SINCE}" 2>/dev/null)" || rc=$?
        if [[ "${rc}" -eq 0 ]]; then
            echo "${_SCRIPT_NAME}: heartbeat confirmed at $(_kv_get last_fired_at "${out}"); container '${NAME}' is up"
            return 0
        fi
        now="$(date +%s)"
        (( now >= deadline )) && break
        wait=$(( deadline - now ))
        (( wait > _HEARTBEAT_POLL_SECONDS )) && wait="${_HEARTBEAT_POLL_SECONDS}"
        sleep "${wait}"
    done
    local message="${_SCRIPT_NAME}: no scheduler heartbeat (a 'fired (minute=NN)' line in ${log}) appeared within ${HEARTBEAT_TIMEOUT}s of starting the scheduler in container '${NAME}'. The container and its tmux session '${_SCHEDULER_SESSION}' are left running: read the log for a startup error or 'vars loaded from' 0, then fix it and stop the container, or wait for a later minute."
    local remedy
    remedy="$(printf 'tail -n 20 %q' "${log}")"
    # BLOCK-WORTHY: verification unable to confirm the scheduler runs, so "up" is not claimed.
    pgai_emit_error_contract "container_up.heartbeat_unconfirmed" "${message}" "${remedy}"
    # CENSUS-ANCHOR: container-up-heartbeat-unconfirmed-01
    exit 1
}

# ---------------------------------------------------------------------------
# Bring the container up
# ---------------------------------------------------------------------------
echo "${_SCRIPT_NAME}: engine=${ENGINE} container=${NAME} image=${IMAGE} kanban_root=${_KANBAN_ROOT}"

_query_state
case "${_STATE}" in
    running)
        _query_scheduler
        if [[ -n "${_SCHEDULER_PIDS}" ]]; then
            echo "${_SCRIPT_NAME}: container '${NAME}' is already running (image ${_STATE_IMAGE:-unknown}) with a live scheduler (pid ${_SCHEDULER_PIDS}); nothing started"
            if [[ "${_SCHEDULER_PIDS}" == *,* ]]; then
                echo "${_SCRIPT_NAME}: WARNING: more than one scheduler runs in '${NAME}' (pids ${_SCHEDULER_PIDS}); exactly one may run -- stop the extra ones" >&2
            fi
            if [[ -n "${_STATE_IMAGE}" && "${_STATE_IMAGE}" != "${IMAGE}" ]]; then
                echo "${_SCRIPT_NAME}: note: it runs ${_STATE_IMAGE}, not ${IMAGE}; stop and remove it first to switch images"
            fi
            exit 0
        fi
        echo "${_SCRIPT_NAME}: container '${NAME}' is running (image ${_STATE_IMAGE:-unknown}) with no scheduler; starting one"
        ;;
    *)
        _require_image
        if [[ "${_STATE}" == "stopped" ]]; then
            echo "${_SCRIPT_NAME}: removing stopped container '${NAME}' to recreate it"
            _rm_rc=0
            pgai_docker "${ENGINE}" rm "${NAME}" >/dev/null 2>"${_WORK}/rm.err" || _rm_rc=$?
            if [[ "${_rm_rc}" -ne 0 ]]; then
                _refuse_engine "removing stopped container '${NAME}'" \
                    "$(_first_line "${_WORK}/rm.err")" "$(printf '%q container inspect %q' "${ENGINE}" "${NAME}")"
            fi
        fi
        _run_rc=0
        pgai_docker "${ENGINE}" run -d -i -t --name "${NAME}" \
            "${CONTAINER_MOUNTS_ARGS[@]}" "${IMAGE}" >/dev/null 2>"${_WORK}/run.err" || _run_rc=$?
        if [[ "${_run_rc}" -ne 0 ]]; then
            _refuse_engine "running image '${IMAGE}' as container '${NAME}'" \
                "$(_first_line "${_WORK}/run.err")" "$(printf '%q logs %q' "${ENGINE}" "${NAME}")"
        fi
        _query_state
        if [[ "${_STATE}" != "running" ]]; then
            _refuse_engine "keeping container '${NAME}' running (it is ${_STATE:-unknown} right after the run; the entrypoint refuses a kanban root that is not an install)" \
                "see the container log" "$(printf '%q logs %q' "${ENGINE}" "${NAME}")"
        fi
        echo "${_SCRIPT_NAME}: container '${NAME}' started from ${IMAGE}"
        ;;
esac

_start_scheduler
_confirm_heartbeat
exit 0
