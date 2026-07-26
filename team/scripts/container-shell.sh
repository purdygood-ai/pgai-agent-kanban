#!/usr/bin/env bash
# team/scripts/container-shell.sh
# Launcher for the pgai-agent-kanban workspace container.
#
# Starts an interactive shell in the container with the standard workspace
# mounts and environment, using the container engine resolved from
# kanban.cfg [docker] docker_exe (docker by default, podman when
# configured — see team/scripts/lib/docker_engine.sh). The image's
# ENTRYPOINT (docker/entrypoint.sh) verifies PGAI_AGENT_KANBAN_ROOT_PATH,
# cd's there, and execs a login bash — this script's job is only to supply
# the mounts, env, and TTY the entrypoint depends on.
#
# Usage:
#   container-shell.sh [OPTIONS]
#
# Options:
#   -t, --image-tag TAG       Image to run. Default: pgai-agent-kanban-rocky:latest
#   --mount HOST:CONTAINER    Extra bind mount ("<host-path>:<container-path>").
#                             Repeatable.
#   --kanban-root PATH        Override PGAI_AGENT_KANBAN_ROOT_PATH.
#   -h, --help                Print this usage and exit 0.
#
# Standard mounts (always applied, at matching host/container paths so no
# translation is needed on either side of the mount):
#   $HOME                          -> same path in the container
#   $PGAI_AGENT_KANBAN_ROOT_PATH   -> same path in the container
#
# Environment exported into the container:
#   HOME                           set to the mounted host $HOME so the
#                                  entrypoint's login bash sources the
#                                  operator's real dotfiles, not the
#                                  image-baked user's.
#   PGAI_AGENT_KANBAN_ROOT_PATH    set to the mounted kanban root.
#
# Exit codes:
#   0  the container exited normally (this script execs into the engine, so
#      the container's own exit code is what the caller sees)
#   1  a required path (kanban root or HOME) is missing, or the configured
#      engine binary cannot be resolved
#   2  usage error (unknown flag, missing argument, malformed --mount value)

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

_SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=lib/docker_engine.sh
source "${_SCRIPT_DIR}/lib/docker_engine.sh"

_DEFAULT_IMAGE_TAG="pgai-agent-kanban-rocky:latest"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat >&2 <<EOF
Usage: ${_SCRIPT_NAME} [OPTIONS]

Starts an interactive workspace shell in the pgai-agent-kanban container.
Mounts HOME and the kanban root at matching host/container paths, exports
PGAI_AGENT_KANBAN_ROOT_PATH, and runs the image's entrypoint
(docker/entrypoint.sh), which cd's into the kanban root and execs a login
bash.

Options:
  -t, --image-tag TAG       Image to run.
                             Default: ${_DEFAULT_IMAGE_TAG}
  --mount HOST:CONTAINER     Extra bind mount ("<host-path>:<container-path>").
                             Repeatable.
  --kanban-root PATH         Override PGAI_AGENT_KANBAN_ROOT_PATH.
  -h, --help                 Print this usage and exit 0.

Standard mounts (always applied):
  \$HOME                          -> same path in the container
  \$PGAI_AGENT_KANBAN_ROOT_PATH   -> same path in the container

Environment:
  PGAI_AGENT_KANBAN_ROOT_PATH   Kanban root directory. Required (or
                                --kanban-root). Must name an existing
                                directory.
  HOME                          Operator home directory. Must name an
                                existing directory.

The container engine (docker or podman) is resolved from kanban.cfg
[docker] docker_exe — see team/scripts/lib/docker_engine.sh. Works
identically under either engine; the run invocation below uses only flags
both engines share.
EOF
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
IMAGE_TAG="${_DEFAULT_IMAGE_TAG}"
EXTRA_MOUNTS=()
KANBAN_ROOT_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        -t|--image-tag)
            [[ $# -lt 2 ]] && { echo "${_SCRIPT_NAME}: ${1} requires a value" >&2; exit 2; }
            IMAGE_TAG="$2"
            shift 2
            ;;
        --image-tag=*)
            IMAGE_TAG="${1#--image-tag=}"
            shift
            ;;
        --mount)
            [[ $# -lt 2 ]] && { echo "${_SCRIPT_NAME}: --mount requires a value ('<host-path>:<container-path>')" >&2; exit 2; }
            EXTRA_MOUNTS+=("$2")
            shift 2
            ;;
        --mount=*)
            EXTRA_MOUNTS+=("${1#--mount=}")
            shift
            ;;
        --kanban-root)
            [[ $# -lt 2 ]] && { echo "${_SCRIPT_NAME}: --kanban-root requires a value" >&2; exit 2; }
            KANBAN_ROOT_OVERRIDE="$2"
            shift 2
            ;;
        --kanban-root=*)
            KANBAN_ROOT_OVERRIDE="${1#--kanban-root=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "${_SCRIPT_NAME}: unknown argument: $1" >&2
            usage
            exit 2
            ;;
    esac
done

# Apply --kanban-root override when given (operator env still wins by default
# via env_bootstrap.sh above; an explicit flag on this invocation wins over that).
if [[ -n "${KANBAN_ROOT_OVERRIDE}" ]]; then
    PGAI_AGENT_KANBAN_ROOT_PATH="${KANBAN_ROOT_OVERRIDE}"
    export PGAI_AGENT_KANBAN_ROOT_PATH
fi

# ---------------------------------------------------------------------------
# Resolve and validate the two standard mount sources
# ---------------------------------------------------------------------------
_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
if [[ -z "${_KANBAN_ROOT}" || ! -d "${_KANBAN_ROOT}" ]]; then
    echo "${_SCRIPT_NAME}: PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '${_KANBAN_ROOT}'" >&2
    exit 1
fi
_KANBAN_ROOT="$(realpath "${_KANBAN_ROOT}")"

_HOST_HOME="${HOME:-}"
if [[ -z "${_HOST_HOME}" || ! -d "${_HOST_HOME}" ]]; then
    echo "${_SCRIPT_NAME}: \$HOME is not set to an existing directory: '${_HOST_HOME}'" >&2
    exit 1
fi
_HOST_HOME="$(realpath "${_HOST_HOME}")"

# ---------------------------------------------------------------------------
# Resolve the container engine (docker by default, podman when configured).
# ---------------------------------------------------------------------------
_DOCKER_EXE="$(resolve_docker_exe "${_KANBAN_ROOT}")" || exit 1

# ---------------------------------------------------------------------------
# Translate --mount values into engine bind-mount flags. Both docker and
# podman accept "-v <host>:<container>", so no engine-specific branching
# is needed here.
# ---------------------------------------------------------------------------
_MOUNT_FLAGS=()
if [[ ${#EXTRA_MOUNTS[@]} -gt 0 ]]; then
    for _m in "${EXTRA_MOUNTS[@]}"; do
        if [[ "${_m}" != *:* ]]; then
            echo "${_SCRIPT_NAME}: --mount value must be '<host-path>:<container-path>': '${_m}'" >&2
            exit 2
        fi
        _MOUNT_FLAGS+=(-v "${_m}")
    done
fi

echo "${_SCRIPT_NAME}: engine=${_DOCKER_EXE} image=${IMAGE_TAG} kanban_root=${_KANBAN_ROOT} home=${_HOST_HOME}" >&2

exec "${_DOCKER_EXE}" run --rm -it \
    -v "${_HOST_HOME}:${_HOST_HOME}" \
    -v "${_KANBAN_ROOT}:${_KANBAN_ROOT}" \
    -e "HOME=${_HOST_HOME}" \
    -e "PGAI_AGENT_KANBAN_ROOT_PATH=${_KANBAN_ROOT}" \
    "${_MOUNT_FLAGS[@]}" \
    "${IMAGE_TAG}"
