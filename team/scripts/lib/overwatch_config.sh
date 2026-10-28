#!/usr/bin/env bash
# team/scripts/lib/overwatch_config.sh
#
# The configuration readers every OVERWATCH check shares, so no check parses
# projects.cfg or project.cfg itself:
#
#   overwatch_registered_projects     — the registered project names, read by
#                                       projects_cfg_list (lib/projects.sh)
#   overwatch_branch_prefix <project> — the project's [project] branch_prefix,
#                                       read by pp_branch_prefix
#                                       (lib/project_paths.sh)
#
# SOURCE THIS FILE; do not execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"
#
# Side-effect-free and safe to source any number of times: it defines the two
# functions and nothing else. Each function sources the library it reads
# through on first use, and only when that library's reader is not already
# loaded (the sweep runner loads both before any check), because
# lib/projects.sh sets a readonly palette and cannot be sourced twice.
#
# Both functions read the kanban root from KANBAN_ROOT (falling back to
# PGAI_AGENT_KANBAN_ROOT_PATH, then $HOME/pgai_agent_kanban, for the
# registry), which every check's environment resolver sets before calling
# them.

# ---------------------------------------------------------------------------
# _overwatch_config_source <file> <function>
# Internal. Source lib/<file> beside this file unless <function> is already
# defined. Returns 1, naming the missing path on stderr, when the library is
# absent or fails to load.
# ---------------------------------------------------------------------------
_overwatch_config_source() {
    local file="$1" fn="$2"
    declare -F "$fn" >/dev/null 2>&1 && return 0
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || lib_dir=""
    if [[ -z "$lib_dir" || ! -f "${lib_dir}/${file}" ]]; then
        echo "overwatch_config.sh: ${file} not found beside ${BASH_SOURCE[0]}; reinstall the kanban so lib/${file} is present" >&2
        return 1
    fi
    # shellcheck source=/dev/null
    source "${lib_dir}/${file}" || {
        echo "overwatch_config.sh: sourcing ${lib_dir}/${file} failed" >&2
        return 1
    }
    declare -F "$fn" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# overwatch_registered_projects
# Echo every project registered in $KANBAN_ROOT/projects.cfg, one per line,
# in projects_cfg_list order (priority ascending, then registration order),
# from either registry format. Echoes nothing when projects.cfg is missing or
# registers no project; projects_cfg_list's own diagnostics are discarded, so
# the calling check's message is the one an operator reads. Always returns 0.
#
# Example:
#   _all_projects="$(overwatch_registered_projects)"
# ---------------------------------------------------------------------------
overwatch_registered_projects() {
    _overwatch_config_source projects.sh projects_cfg_list || return 0
    projects_cfg_list 2>/dev/null || true
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_branch_prefix <project>
# Echo <project>'s branch_prefix through pp_branch_prefix: empty when the key
# is absent, blank or the quoted empty string (""), the value otherwise
# (e.g. "ai_" -> ai_main, ai_rc/vX.Y.Z, ai_vX.Y.Z).
#
# Returns 1 when the project name is missing, when lib/project_paths.sh
# cannot be loaded, or when the configured value holds a character outside
# [a-zA-Z0-9_-] (pp_branch_prefix names the value on stderr), so a check
# never builds branch or tag names from an invalid prefix.
#
# Example:
#   _PREFIX="$(overwatch_branch_prefix "${OVERWATCH_PROJECT}")" || return 1
# ---------------------------------------------------------------------------
overwatch_branch_prefix() {
    local project="${1:-}"
    if [[ -z "$project" ]]; then
        echo "overwatch_config.sh: overwatch_branch_prefix: a project name is required, e.g. overwatch_branch_prefix pgai-agent-kanban" >&2
        return 1
    fi
    _overwatch_config_source project_paths.sh pp_branch_prefix || return 1
    pp_branch_prefix "$project" && return 0
    echo "overwatch_config.sh: overwatch_branch_prefix: set [project] branch_prefix in ${KANBAN_ROOT:-}/projects/${project}/project.cfg to letters, digits, _ and - only, or leave it empty" >&2
    return 1
}
