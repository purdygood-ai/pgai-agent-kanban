#!/usr/bin/env bash
# team/scripts/lib/container_mounts.sh
#
# The workspace container's standard mounts and environment, assembled in
# one place for every script that starts the kanban container
# (container-shell.sh, container-up.sh).
#
# What it assembles
# -----------------
#   - The operator's $HOME and the kanban root, each validated as an
#     existing directory and resolved with realpath, bind-mounted at the
#     SAME path inside the container, so no path needs translating on
#     either side of the mount.
#   - HOME exported as the mounted host home (the entrypoint's login bash
#     then sources the operator's real dotfiles, not the image-baked
#     user's), and PGAI_AGENT_KANBAN_ROOT_PATH exported as the mounted
#     kanban root (docker/entrypoint.sh verifies it and cd's there).
#   - Each extra "<host-path>:<container-path>" mount the caller was given
#     (the --mount flag), as one more bind mount after the standard ones.
#
# The mounts and environment use only flags docker and podman both accept
# ("-v H:C", "-e K=V"), so callers need no engine-specific branching. The
# one engine-specific flag is added here, not by callers: when kanban.cfg
# [docker] docker_exe names podman, the arguments start with
# "--userns=keep-id". The images run as an unprivileged user, and rootless
# podman otherwise maps that user to a host subuid that cannot write the
# operator's bind-mounted files (the operator's own files map to container
# root); keep-id maps the operator's uid and gid into the container
# unchanged. With docker (the default) the arguments carry no such flag.
# The engine NAME is read from kanban.cfg only -- whether that binary is on
# PATH is still the caller's resolve_docker_exe check, made afterwards.
# This file never calls a container engine itself: callers pass the
# assembled arguments to pgai_docker (lib/docker_use.sh), which records the
# run in the docker-use log.
#
# Validation order, and why it matters to callers
# -----------------------------------------------
#   1. kanban root   missing / not a directory      -> return 1
#   2. $HOME         missing / not a directory      -> return 1
#   3. extra mounts  a value without ':'            -> return 2
# Every check is local to the arguments and the filesystem, so a caller
# runs this BEFORE resolving its container engine: a malformed --mount is
# then a usage error (exit 2) on every host, including one with no engine
# installed, rather than being masked by an unrelated engine failure.
#
# Public API
# ----------
#   container_mounts_assemble <caller-name> <kanban-root> <home> [<host:container> ...]
#       Validates and resolves the two standard mount sources and the
#       extra mounts, then sets, in the caller's shell:
#         CONTAINER_MOUNTS_KANBAN_ROOT  the realpath'd kanban root
#         CONTAINER_MOUNTS_HOME         the realpath'd home directory
#         CONTAINER_MOUNTS_ARGS         array, in this order:
#             [--userns=keep-id]          (podman only, see above)
#             -v HOME:HOME -v ROOT:ROOT
#             -e HOME=HOME -e PGAI_AGENT_KANBAN_ROOT_PATH=ROOT
#             [-v EXTRA ...]
#       Returns 0 on success; 1 when the kanban root or home is missing;
#       2 when an extra mount is malformed. On failure one line goes to
#       stderr, prefixed "<caller-name>: " and naming the offending value,
#       and the three variables are left empty. The function returns
#       rather than exits, so callers map the status to their own exit:
#
#           container_mounts_assemble "${_SCRIPT_NAME}" \
#               "${PGAI_AGENT_KANBAN_ROOT_PATH:-}" "${HOME:-}" \
#               "${EXTRA_MOUNTS[@]}" || exit $?
#           pgai_docker "${engine}" run --rm -it \
#               "${CONTAINER_MOUNTS_ARGS[@]}" "${image}"
#
#   container_mounts_engine_flags <kanban-root>
#       Print, one per line, the engine-specific run flags for the engine
#       kanban.cfg [docker] docker_exe names (default docker): the
#       user-namespace mapping for podman, nothing for docker. Never fails;
#       a missing kanban.cfg reads as docker.

[[ -n "${_PGAI_CONTAINER_MOUNTS_SH_LOADED:-}" ]] && return 0
_PGAI_CONTAINER_MOUNTS_SH_LOADED=1

CONTAINER_MOUNTS_KANBAN_ROOT=""
CONTAINER_MOUNTS_HOME=""
CONTAINER_MOUNTS_ARGS=()

container_mounts_engine_flags() {
    local kanban_root="${1:-}"
    if ! command -v read_ini >/dev/null 2>&1; then
        # shellcheck source=ini_parser.sh
        source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh" || return 0
    fi
    local engine
    engine="$(read_ini "${kanban_root}/kanban.cfg" docker docker_exe "docker" 2>/dev/null || true)"
    engine="${engine:-docker}"
    case "$(basename -- "${engine}")" in
        podman*) printf '%s\n' "--userns=keep-id" ;;
    esac
    return 0
}

container_mounts_assemble() {
    local caller="${1:-container_mounts.sh}"
    local kanban_root="${2:-}"
    local home="${3:-}"
    shift 3 2>/dev/null || shift $#

    CONTAINER_MOUNTS_KANBAN_ROOT=""
    CONTAINER_MOUNTS_HOME=""
    CONTAINER_MOUNTS_ARGS=()

    if [[ -z "${kanban_root}" || ! -d "${kanban_root}" ]]; then
        echo "${caller}: PGAI_AGENT_KANBAN_ROOT_PATH is not set to an existing directory: '${kanban_root}'" >&2
        return 1
    fi
    kanban_root="$(realpath "${kanban_root}")" || return 1

    if [[ -z "${home}" || ! -d "${home}" ]]; then
        echo "${caller}: \$HOME is not set to an existing directory: '${home}'" >&2
        return 1
    fi
    home="$(realpath "${home}")" || return 1

    local extra_flags=() mount_spec
    for mount_spec in "$@"; do
        if [[ "${mount_spec}" != *:* ]]; then
            echo "${caller}: --mount value must be '<host-path>:<container-path>': '${mount_spec}'" >&2
            return 2
        fi
        extra_flags+=(-v "${mount_spec}")
    done

    local engine_flags=()
    mapfile -t engine_flags < <(container_mounts_engine_flags "${kanban_root}")

    CONTAINER_MOUNTS_KANBAN_ROOT="${kanban_root}"
    CONTAINER_MOUNTS_HOME="${home}"
    CONTAINER_MOUNTS_ARGS=(
        "${engine_flags[@]}"
        -v "${home}:${home}"
        -v "${kanban_root}:${kanban_root}"
        -e "HOME=${home}"
        -e "PGAI_AGENT_KANBAN_ROOT_PATH=${kanban_root}"
        "${extra_flags[@]}"
    )
    return 0
}
