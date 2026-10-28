#!/usr/bin/env bash
# team/verification/lib/verification_common.sh
#
# Sourced by the team/verification scripts for the two things each of them
# needs before it checks anything:
#
#   pgai_emit_error_contract <error> <message> <remedy>
#       The shared dual-channel emitter, sourced from the scripts lib tree
#       beside this one (team/verification/lib -> team/scripts/lib, and the
#       same relative layout in an install). When that tree is missing, a
#       minimal stand-in prints the same three fields on stderr, so a
#       refusal still reads the same and still exits non-zero.
#
#   verification_dev_tree
#       Echoes the dev tree a verification run checks when the caller named
#       none: PGAI_DEV_TREE_PATH when set, else the project's [project]
#       dev_tree_path (pp_dev_tree_path from project_paths.sh, for
#       PGAI_PROJECT_NAME or the kanban's own project, under KANBAN_ROOT or
#       PGAI_AGENT_KANBAN_ROOT_PATH). Echoes nothing when neither is
#       available.
#
#   VERIFICATION_EXAMPLE_DEV_TREE
#       A neutral example checkout path, for a remedy that has no resolved
#       dev tree to name.
#
# Usage (from a script in team/verification/):
#   source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/verification_common.sh"

_VERIFICATION_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_VERIFICATION_SCRIPTS_LIB="${_VERIFICATION_LIB_DIR}/../../scripts/lib"

if [[ -r "${_VERIFICATION_SCRIPTS_LIB}/error_contract.sh" ]]; then
    # shellcheck source=../../scripts/lib/error_contract.sh
    source "${_VERIFICATION_SCRIPTS_LIB}/error_contract.sh"
else
    pgai_emit_error_contract() {
        printf 'error:   %s\nmessage: %s\nremedy:  %s\n' "$1" "$2" "$3" >&2
    }
fi

VERIFICATION_EXAMPLE_DEV_TREE="/srv/develop/my-app"

# The project whose dev_tree_path a run falls back to when PGAI_PROJECT_NAME
# is unset: the verification scripts check the kanban's own tree.
_VERIFICATION_DEFAULT_PROJECT="pgai-agent-kanban"

verification_dev_tree() {
    if [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        echo "$PGAI_DEV_TREE_PATH"
        return 0
    fi
    local root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}"
    [[ -n "$root" && -r "${_VERIFICATION_SCRIPTS_LIB}/project_paths.sh" ]] || return 0
    (
        # A subshell, so project_paths.sh's functions and settings stay out
        # of the calling script.
        set +e +u
        # shellcheck source=../../scripts/lib/project_paths.sh
        source "${_VERIFICATION_SCRIPTS_LIB}/project_paths.sh" >/dev/null 2>&1 || exit 0
        KANBAN_ROOT="$root" pp_dev_tree_path "${PGAI_PROJECT_NAME:-$_VERIFICATION_DEFAULT_PROJECT}" 2>/dev/null
    ) || true
}
