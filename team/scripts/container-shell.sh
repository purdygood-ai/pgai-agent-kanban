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
#   -t, --image-tag TAG       Image to run. Default: pgai-agent-kanban-debian:latest
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
# The standard mounts, the environment and the --mount extras are assembled
# by team/scripts/lib/container_mounts.sh, shared with container-up.sh, so
# every kanban container sees the same paths.
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
# shellcheck source=lib/docker_use.sh
source "${_SCRIPT_DIR}/lib/docker_use.sh"
# shellcheck source=lib/script_common.sh
source "${_SCRIPT_DIR}/lib/script_common.sh"
# shellcheck source=lib/container_mounts.sh
source "${_SCRIPT_DIR}/lib/container_mounts.sh"

_DEFAULT_IMAGE_TAG="pgai-agent-kanban-debian:latest"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's documented convention).
# This script's usage intentionally prints to stderr on BOTH the --help
# (exit 0) and error (exit 2) paths — print_usage_and_exit's stdout-on-0
# contract does not fit that shape, so the destination is pinned here
# instead of delegated.
_USAGE_TEXT="$(cat <<EOF
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
)"

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
            printf '%s\n' "$_USAGE_TEXT" >&2
            exit 0
            ;;
        *)
            echo "${_SCRIPT_NAME}: unknown argument: $1" >&2
            printf '%s\n' "$_USAGE_TEXT" >&2
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
# Assemble the standard mounts and environment (lib/container_mounts.sh):
# the kanban root and $HOME, validated and realpath'd, mounted at matching
# paths and exported, then each --mount value as an extra bind mount. A
# missing path exits 1; a malformed --mount value exits 2. Every check here
# is on arguments and the filesystem, not the environment's tooling, so it
# runs before engine resolution below -- a malformed --mount value is a
# usage error (exit 2) on every host, including a host with no container
# engine on PATH at all, instead of being masked behind an unrelated exit 1.
# ---------------------------------------------------------------------------
container_mounts_assemble "${_SCRIPT_NAME}" \
    "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" "${HOME:-}" "${EXTRA_MOUNTS[@]}" || exit $?
_KANBAN_ROOT="${CONTAINER_MOUNTS_KANBAN_ROOT}"
_HOST_HOME="${CONTAINER_MOUNTS_HOME}"

# ---------------------------------------------------------------------------
# Resolve the container engine (docker by default, podman when configured).
# ---------------------------------------------------------------------------
_DOCKER_EXE="$(resolve_docker_exe "${_KANBAN_ROOT}")" || exit 1

echo "${_SCRIPT_NAME}: engine=${_DOCKER_EXE} image=${IMAGE_TAG} kanban_root=${_KANBAN_ROOT} home=${_HOST_HOME}" >&2

# Through the shared engine helper (lib/docker_use.sh), which records the
# launch and its exit in the docker-use log; this script's exit status is
# the container shell's own.
pgai_docker "${_DOCKER_EXE}" run --rm -it \
    "${CONTAINER_MOUNTS_ARGS[@]}" \
    "${IMAGE_TAG}"
exit $?
