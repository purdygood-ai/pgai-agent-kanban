#!/usr/bin/env bash
# docker/build.sh
# Build one pgai-agent-kanban image from docker/<flavor>/Dockerfile.
#
# Repository only: the build context is the repository root, so this script
# runs from a checkout and is never installed. It may be called from any
# working directory; the Dockerfile and the context are resolved from this
# script's own location.
#
# Usage:
#   docker/build.sh FLAVOR [--tag TAG] [--no-cache] [--engine docker|podman] [--dry-run]
#   docker/build.sh --help
#
# FLAVOR is the name of a docker/<flavor>/ directory holding a Dockerfile
# (debian, rocky9, claude-abliterated today). The pinned provider-CLI
# versions in docker/cli-versions.env are passed as --build-arg
# CLAUDE_CODE_VERSION / CODEX_VERSION, and the image is tagged
# pgai-kanban:<flavor> and pgai-kanban:latest (--tag replaces latest).
#
# The build, the flavor list and the argv come from
# pgai_agent_kanban.container.image_build (called through pp_run_ops); the
# engine command runs through pgai_docker (team/scripts/lib/docker_use.sh),
# so the docker-use log records it. A real build is container-tier work and
# operator-only (team/scripts/lib/container_tier.sh): inside an agent wake,
# or without PGAI_CONTAINER_TIER=1, it prints one SKIPPED line and exits 0.
# --help, flavor checks and --dry-run run before that gate and touch no
# engine.
#
# Exit codes:
#   0  built; --dry-run or --help printed; or the build was SKIPPED
#      (not authorized)
#   1  docker/cli-versions.env cannot supply the pinned versions, or the
#      engine binary is not on PATH
#   2  usage error, or FLAVOR has no docker/<flavor>/Dockerfile
#   N  the engine's own exit status when the build itself fails

set -euo pipefail

_SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SCRIPT_PATH="${_SCRIPT_DIR}/${_SCRIPT_NAME}"
REPO_ROOT="$(cd "${_SCRIPT_DIR}/.." && pwd)"
_LIB_DIR="${REPO_ROOT}/team/scripts/lib"

# shellcheck source=../team/scripts/lib/error_contract.sh
source "${_LIB_DIR}/error_contract.sh"
# shellcheck source=../team/scripts/lib/docker_use.sh
source "${_LIB_DIR}/docker_use.sh"
# shellcheck source=../team/scripts/lib/container_tier.sh
source "${_LIB_DIR}/container_tier.sh"
# shellcheck source=../team/scripts/lib/pp_run_ops.sh
source "${_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=../team/scripts/lib/ini_parser.sh
source "${_LIB_DIR}/ini_parser.sh"

_MODULE="pgai_agent_kanban.container.image_build"
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"

_USAGE_TEXT="$(cat <<EOF
Usage: ${_SCRIPT_NAME} FLAVOR [OPTIONS]

Build docker/FLAVOR/Dockerfile with the repository root as build context,
passing the pinned CLI versions from docker/cli-versions.env as
--build-arg CLAUDE_CODE_VERSION=... and --build-arg CODEX_VERSION=...,
and tag the image pgai-kanban:FLAVOR and pgai-kanban:latest.

Arguments:
  FLAVOR                  A docker/<flavor>/ directory holding a Dockerfile
                          (e.g. debian, rocky9, claude-abliterated).

Options:
  --tag TAG               Tag the image pgai-kanban:TAG instead of
                          pgai-kanban:latest (pgai-kanban:FLAVOR is always
                          added).
  --no-cache              Build without the engine's layer cache.
  --engine docker|podman  Container engine to build with. Default:
                          [docker] docker_exe in the kanban's kanban.cfg
                          (\$PGAI_AGENT_KANBAN_ROOT_PATH/kanban.cfg, else
                          \$HOME/pgai_agent_kanban/kanban.cfg) when set,
                          else docker.
  --dry-run               Print the build command and run nothing.
  -h, --help              Print this usage and exit 0.

A real build is operator-only: run it from your own shell with
PGAI_CONTAINER_TIER=1 set. Inside an agent wake, or without that
variable, the build prints one SKIPPED line and exits 0. --dry-run and
--help never need it.

Exit codes: 0 built / printed / skipped; 1 pinned versions unreadable or
engine not on PATH; 2 usage error or unknown flavor; otherwise the
engine's own exit status when the build fails.
EOF
)"

# _refuse_usage MESSAGE
# Prints the usage refusal (dual-channel, remedy: this script's --help) on
# stderr, then exits 2. A command line this script cannot read is never
# guessed at; it refuses before any build is attempted.
_refuse_usage() {
    local message="${_SCRIPT_NAME} refused: $1. Nothing was built."
    local remedy
    remedy="$(printf '%q --help' "${_SCRIPT_PATH}")"
    # BLOCK-WORTHY: invariant by design -- an unreadable command line is refused.
    pgai_emit_error_contract "docker_build.usage" "${message}" "${remedy}"
    # CENSUS-ANCHOR: docker-build-usage-01
    exit 2
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
_ORIGINAL_ARGS=("$@")
FLAVOR=""
TAG="latest"
NO_CACHE=0
ENGINE=""
DRY_RUN=0
_REST_ARGS=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            printf '%s\n' "${_USAGE_TEXT}"
            exit 0
            ;;
        --tag)
            [[ $# -ge 2 ]] || _refuse_usage "--tag requires a value (e.g. --tag dev)"
            TAG="$2"; _REST_ARGS+=("$1" "$2"); shift 2
            ;;
        --tag=*)
            TAG="${1#--tag=}"; _REST_ARGS+=("$1"); shift
            ;;
        --engine)
            [[ $# -ge 2 ]] || _refuse_usage "--engine requires a value: docker or podman"
            ENGINE="$2"; _REST_ARGS+=("$1" "$2"); shift 2
            ;;
        --engine=*)
            ENGINE="${1#--engine=}"; _REST_ARGS+=("$1"); shift
            ;;
        --no-cache)
            NO_CACHE=1; _REST_ARGS+=("$1"); shift
            ;;
        --dry-run)
            DRY_RUN=1; _REST_ARGS+=("$1"); shift
            ;;
        -*)
            _refuse_usage "unknown option '$1'"
            ;;
        *)
            [[ -z "${FLAVOR}" ]] || _refuse_usage "more than one FLAVOR given ('${FLAVOR}' and '$1')"
            FLAVOR="$1"; shift
            ;;
    esac
done

[[ -n "${FLAVOR}" ]] || _refuse_usage "no FLAVOR given"
if [[ -n "${ENGINE}" && "${ENGINE}" != "docker" && "${ENGINE}" != "podman" ]]; then
    _refuse_usage "--engine '${ENGINE}' is not docker or podman"
fi

# ---------------------------------------------------------------------------
# Validate the flavor against the docker/<flavor>/Dockerfile directories
# ---------------------------------------------------------------------------
mapfile -t _FLAVORS < <(pp_run_ops "${_MODULE}" flavors --repo-root "${REPO_ROOT}")
_known=0
for _f in ${_FLAVORS[@]+"${_FLAVORS[@]}"}; do
    [[ "${_f}" == "${FLAVOR}" ]] && { _known=1; break; }
done
if [[ "${_known}" -ne 1 ]]; then
    # The remedy previews a valid flavor with the same options; --dry-run is
    # added so pasting it prints the command instead of starting a build.
    _remedy_args=(${_REST_ARGS[@]+"${_REST_ARGS[@]}"})
    [[ "${DRY_RUN}" -eq 1 ]] || _remedy_args+=(--dry-run)
    _remedy="$(printf '%q ' "${_SCRIPT_PATH}" "${_FLAVORS[0]:-debian}" "${_remedy_args[@]}")"
    _message="${_SCRIPT_NAME} refused: '${FLAVOR}' is not a buildable flavor. Valid flavors (docker/<flavor>/Dockerfile): ${_FLAVORS[*]:-(none found)}. Nothing was built."
    # BLOCK-WORTHY: invariant by design -- only a docker/<flavor>/ directory
    # holding a Dockerfile is buildable; any other name is refused, listed.
    pgai_emit_error_contract "docker_build.unknown_flavor" "${_message}" "${_remedy% }"
    # CENSUS-ANCHOR: docker-build-unknown-flavor-01
    exit 2
fi

# ---------------------------------------------------------------------------
# Resolve the engine: --engine, else kanban.cfg [docker] docker_exe, else docker
# ---------------------------------------------------------------------------
if [[ -z "${ENGINE}" ]]; then
    ENGINE="$(read_ini "${_KANBAN_ROOT}/kanban.cfg" docker docker_exe "docker" 2>/dev/null || true)"
    ENGINE="${ENGINE:-docker}"
fi

# ---------------------------------------------------------------------------
# Assemble the build argv (Dockerfile, pinned build args, tags, context)
# ---------------------------------------------------------------------------
_build_args_cmd=("${_MODULE}" build-args "${FLAVOR}" --repo-root "${REPO_ROOT}" --tag "${TAG}")
[[ "${NO_CACHE}" -eq 1 ]] && _build_args_cmd+=(--no-cache)
# The module prints the argv on stdout; on failure it prints nothing there
# and names the reason (bad --tag, missing pin file or key) on stderr, which
# reaches the operator directly, above this script's refusal.
_args_rc=0
_args_out="$(pp_run_ops "${_build_args_cmd[@]}")" || _args_rc=$?
if [[ "${_args_rc}" -eq 2 ]]; then
    _refuse_usage "--tag '${TAG}' is not a valid image tag (reason above)"
fi
if [[ "${_args_rc}" -ne 0 ]]; then
    # A build without both pinned CLI versions would not reproduce, so it is
    # refused before any engine is touched; no default version is substituted.
    _message="${_SCRIPT_NAME} refused: the pinned CLI versions could not be read from ${REPO_ROOT}/docker/cli-versions.env (exit ${_args_rc}; reason above). Nothing was built."
    _remedy="$(printf 'git -C %q checkout -- docker/cli-versions.env' "${REPO_ROOT}")"
    # BLOCK-WORTHY: invariant by design -- an unpinned build is never attempted.
    pgai_emit_error_contract "docker_build.cli_pins_unreadable" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: docker-build-cli-pins-unreadable-01
    exit 1
fi
mapfile -t BUILD_ARGV <<< "${_args_out}"

# ---------------------------------------------------------------------------
# --dry-run: print the one build command and stop
# ---------------------------------------------------------------------------
if [[ "${DRY_RUN}" -eq 1 ]]; then
    _cmd="$(printf '%q ' "${ENGINE}" "${BUILD_ARGV[@]}")"
    printf '%s\n' "${_cmd% }"
    exit 0
fi

# ---------------------------------------------------------------------------
# Real build: operator-only (prints SKIPPED and exits 0 when not authorized)
# ---------------------------------------------------------------------------
container_tier_require_operator "${_SCRIPT_PATH}" ${_ORIGINAL_ARGS[@]+"${_ORIGINAL_ARGS[@]}"}

if ! command -v "${ENGINE}" >/dev/null 2>&1; then
    if [[ "$(basename "${ENGINE}")" == "podman" ]]; then _other="docker"; else _other="podman"; fi
    _message="${_SCRIPT_NAME} refused: container engine '${ENGINE}' is not on PATH (from --engine, or kanban.cfg [docker] docker_exe in ${_KANBAN_ROOT}/kanban.cfg). Nothing was built."
    _remedy="$(printf 'PGAI_CONTAINER_TIER=1 %q %q --engine %q' "${_SCRIPT_PATH}" "${FLAVOR}" "${_other}")"
    # BLOCK-WORTHY: the requested build cannot run at all; nothing is attempted.
    pgai_emit_error_contract "docker_build.engine_not_found" "${_message}" "${_remedy}"
    # CENSUS-ANCHOR: docker-build-engine-not-found-01
    exit 1
fi

echo "${_SCRIPT_NAME}: building flavor=${FLAVOR} engine=${ENGINE} context=${REPO_ROOT}" >&2
# Through the shared engine helper, which records START/FINISH in the
# docker-use log; this script's exit status is the build's own.
_build_rc=0
pgai_docker "${ENGINE}" "${BUILD_ARGV[@]}" || _build_rc=$?
if [[ "${_build_rc}" -eq 0 ]]; then
    _tags=()
    for (( _i = 0; _i < ${#BUILD_ARGV[@]} - 1; _i++ )); do
        [[ "${BUILD_ARGV[_i]}" == "-t" ]] && _tags+=("${BUILD_ARGV[_i + 1]}")
    done
    echo "${_SCRIPT_NAME}: built ${FLAVOR} as ${_tags[*]}" >&2
else
    echo "${_SCRIPT_NAME}: the ${ENGINE} build of ${FLAVOR} failed with exit ${_build_rc}; the engine output above names the failing step." >&2
fi
exit "${_build_rc}"
