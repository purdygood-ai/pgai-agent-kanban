#!/usr/bin/env bash
# team/scripts/lib/docker_engine.sh
# Container-engine resolver for the pgai-agent-kanban framework.
#
# Source this file to get resolve_docker_exe in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/docker_engine.sh"
#
# The container engine binary is read from kanban.cfg [docker] docker_exe.
# This lets RHEL-lineage shops point the launcher at podman instead of
# docker without editing any script.
#
# Resolution rules
# ----------------
#   - kanban.cfg absent, [docker] section absent, or docker_exe key absent
#     or blank -> resolves to "docker" (no error).
#   - docker_exe set to a value -> that value is used.
#   - Whatever value is resolved (default or configured) must exist on
#     PATH. A missing binary is a hard failure: the function writes an
#     error to stderr naming both the docker_exe key and the resolved
#     value, and returns non-zero. Nothing is printed to stdout on
#     failure.
#
# Functions
# ---------
#   resolve_docker_exe <kanban_root>
#     Echo the resolved, PATH-verified container-engine binary name.
#     kanban_root: path to the kanban root directory (required).
#     Returns 0 with the binary name on stdout when the resolved binary
#     exists on PATH. Returns 1 with an error on stderr (stdout empty)
#     when the resolved binary cannot be found.

# ---------------------------------------------------------------------------
# resolve_docker_exe <kanban_root>
# Echo the resolved container-engine binary (e.g. "docker" or "podman").
# Fails loud, naming the docker_exe key and the resolved value, when the
# binary does not exist on PATH.
# ---------------------------------------------------------------------------
resolve_docker_exe() {
    local kanban_root="${1:-}"
    if [[ -z "$kanban_root" ]]; then
        echo "docker_engine.sh: resolve_docker_exe: kanban_root argument is required" >&2
        return 1
    fi

    local cfg="${kanban_root}/kanban.cfg"

    # Source ini_parser.sh if read_ini isn't already defined (defensive --
    # most callers already have it sourced via project_paths.sh or a wake
    # bootstrap path).
    if ! command -v read_ini >/dev/null 2>&1; then
        local ini_parser
        ini_parser="$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
        if [[ -f "$ini_parser" ]]; then
            # shellcheck source=/dev/null
            source "$ini_parser"
        else
            echo "docker_engine.sh: ini_parser.sh not found at '${ini_parser}'" >&2
            return 1
        fi
    fi

    # read_ini already returns the default ("docker") with exit 0 when the
    # file is missing, or the [docker] section or docker_exe key is absent,
    # and it strips leading/trailing whitespace from a found value -- no
    # extra existence or trimming checks are needed for the "absent" case.
    local value
    value="$(read_ini "$cfg" docker docker_exe "docker")"
    # A key present but set to a blank value falls back to the default,
    # same as an absent key.
    value="${value:-docker}"

    if ! command -v "$value" >/dev/null 2>&1; then
        echo "docker_engine.sh: resolve_docker_exe: configured [docker] docker_exe '${value}' is not an executable found on PATH (kanban.cfg [docker] docker_exe='${value}')" >&2
        return 1
    fi

    printf '%s\n' "$value"
    return 0
}
