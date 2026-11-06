#!/usr/bin/env bash
# team/scripts/lib/overwatch_config.sh
#
# The configuration readers every OVERWATCH check shares, so no check parses
# projects.cfg or project.cfg itself, nor carries its own copy of the
# project resolution or its own way of filing a bug:
#
#   overwatch_registered_projects     — the registered project names, read by
#                                       projects_cfg_list (lib/projects.sh)
#   overwatch_resolve_check_project <check>
#                                     — set KANBAN_ROOT and OVERWATCH_PROJECT
#                                       for a check, refusing an ambiguous or
#                                       empty registry
#   overwatch_branch_prefix <project> — the project's [project] branch_prefix,
#                                       read by pp_branch_prefix
#                                       (lib/project_paths.sh)
#   overwatch_dev_tree <project>      — the project's dev_tree_path chain
#   overwatch_deposit_bug <check> <project> <slug> <title>
#                                     — file a bug report (body on stdin)
#                                       through scripts/intake.sh, which
#                                       claims its number and refuses a
#                                       same-defect duplicate
#
# SOURCE THIS FILE; do not execute it directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/../overwatch_config.sh"
#
# Side-effect-free and safe to source any number of times: it defines the
# functions and records its own directory, and nothing else. Each function
# sources the library it reads through on first use, and only when that
# library's reader is not already loaded (the sweep runner loads both before
# any check), because
# lib/projects.sh sets a readonly palette and cannot be sourced twice.
#
# The readers take the kanban root from KANBAN_ROOT (falling back to
# PGAI_AGENT_KANBAN_ROOT_PATH, then $HOME/pgai_agent_kanban, for the
# registry), which overwatch_resolve_check_project sets before a check calls
# them.

# This file's directory, as an absolute path resolved once at load: no
# function depends on the caller's current directory or on whether this
# file was named by a relative path.
_OVERWATCH_CONFIG_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || _OVERWATCH_CONFIG_LIB_DIR=""
_OVERWATCH_CONFIG_SELF_FILE="${_OVERWATCH_CONFIG_LIB_DIR:-$(dirname "${BASH_SOURCE[0]}")}/$(basename "${BASH_SOURCE[0]}")"

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
    lib_dir="${_OVERWATCH_CONFIG_LIB_DIR}"
    if [[ -z "$lib_dir" || ! -f "${lib_dir}/${file}" ]]; then
        echo "overwatch_config.sh: ${file} not found beside ${_OVERWATCH_CONFIG_SELF_FILE}; reinstall the kanban so lib/${file} is present" >&2
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

# ---------------------------------------------------------------------------
# overwatch_dev_tree <project>
#
# Echo the repository the checks read for <project>: the documented
# dev_tree_path chain (pp_resolve_dev_tree in project_paths.sh) -- the
# project's own [project] dev_tree_path, else PGAI_DEV_TREE_PATH, else the
# kanban root's kanban.cfg [paths] dev_tree_path. Echoes nothing when no
# tier names one; returns 1 only when the project cannot be resolved.
#
# Example:
#   _DEV_TREE="$(overwatch_dev_tree "${OVERWATCH_PROJECT}")" || _DEV_TREE=""
# ---------------------------------------------------------------------------
overwatch_dev_tree() {
    local project="${1:-}"
    if [[ -z "$project" ]]; then
        echo "overwatch_config.sh: overwatch_dev_tree: a project name is required, e.g. overwatch_dev_tree pgai-agent-kanban" >&2
        return 1
    fi
    _overwatch_config_source project_paths.sh pp_resolve_dev_tree || return 1
    pp_resolve_dev_tree "$project" 2>/dev/null
}

# ---------------------------------------------------------------------------
# overwatch_resolve_check_project <check-name>
#
# The environment every OVERWATCH check resolves before it reads anything:
#   KANBAN_ROOT       — kept when set, else PGAI_AGENT_KANBAN_ROOT_PATH, else
#                       $HOME/pgai_agent_kanban; it must be a directory.
#   OVERWATCH_PROJECT — kept when set, else the one project projects.cfg
#                       registers. Several registered projects is a refusal,
#                       never a silent pick of the first: only a
#                       single-project install may leave it unset.
# Each refusal is printed on stderr prefixed with <check-name>, and returns 1.
# Returns 0 with both variables set otherwise.
#
# Example:
#   overwatch_resolve_check_project check-push-lag || return 1
# ---------------------------------------------------------------------------
overwatch_resolve_check_project() {
    local check="${1:-overwatch-check}"
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "${check}: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi
    [[ -n "${OVERWATCH_PROJECT:-}" ]] && return 0

    local all_projects="" project_count=0
    all_projects="$(overwatch_registered_projects)"
    project_count="$(grep -c '[^[:space:]]' <<< "${all_projects}" 2>/dev/null)" || project_count=0
    if [[ "${project_count}" -gt 1 ]]; then
        echo "${check}: ERROR: OVERWATCH_PROJECT is not set and multiple projects are registered." >&2
        echo "  Set OVERWATCH_PROJECT to the target project name before running this check." >&2
        echo "  Registered projects: $(tr '\n' ' ' <<< "${all_projects}")" >&2
        return 1
    fi
    OVERWATCH_PROJECT="$(grep '[^[:space:]]' <<< "${all_projects}" | head -n1)" || OVERWATCH_PROJECT=""
    if [[ -z "${OVERWATCH_PROJECT}" ]]; then
        echo "${check}: ERROR: no project specified and none resolvable from projects.cfg" >&2
        echo "  Set OVERWATCH_PROJECT or register a project in ${KANBAN_ROOT}/projects.cfg" >&2
        return 1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# _overwatch_prior_filings <bugs_dir> <slug>
# Internal. Echo the ID (BUG-NNNN) of every bug file directly in <bugs_dir>
# filed under exactly <slug> -- BUG-NNNN-<slug>.md or
# BUG-NNNN-YYYYMMDD-<slug>.md -- one per line, in filename order. Echoes
# nothing when there is none, or no such directory.
# ---------------------------------------------------------------------------
_overwatch_prior_filings() {
    local bugs_dir="$1" slug="$2" f name
    [[ -d "${bugs_dir}" ]] || return 0
    for f in "${bugs_dir}"/BUG-[0-9]*-"${slug}".md; do
        [[ -f "${f}" ]] || continue
        name="${f##*/}"
        [[ "${name}" =~ ^(BUG-[0-9]{4,})(-[0-9]{8})?-(.+)\.md$ ]] || continue
        [[ "${BASH_REMATCH[3]}" == "${slug}" ]] && printf '%s\n' "${BASH_REMATCH[1]}"
    done
    return 0
}

# ---------------------------------------------------------------------------
# overwatch_deposit_bug <check> <project> <slug> <title>
#
# File one bug report for an OVERWATCH check through the intake path --
# scripts/intake.sh beside this lib directory -- never by writing into
# bugs/ itself. The report body (everything after the title line) is read
# from stdin. The draft is staged under the temp root as BUG-NNNN-<slug>.md
# with the draft number 0000, titled "# BUG-NNNN: <title>"; intake claims
# the next number no live or archived bug holds, names the file for it and
# rewrites the title line to match. <slug> is lower-case kebab ([a-z0-9] words joined
# by single hyphens), or intake refuses the name.
#
# A bug already filed under exactly <slug> in the project's bugs/ is cited
# in the draft ("**Re-file of:** BUG-NNNN"), so intake refuses the draft as
# the same defect while that bug is live (open, running or needs-operator)
# and files it as a re-file once that bug is closed. Intake's own
# same-defect rule applies too: a slug differing from a live bug's only by
# a re-file word, an ordinal or a trailing counter is refused, so a slug
# that names a defect by a number ends in a word.
#
# Returns, echoing one line on stdout:
#   0 -- filed: the deposited bug's path
#   2 -- refused as the same defect as a live bug: that bug's path (its ID
#        when the file is not found); nothing was written
#   1 -- not filed (missing argument, staging failed, intake refused the
#        name or failed); intake's message is on stderr, nothing on stdout
#
# Example:
#   bug_path="$(printf '## Status\nopen\n' \
#       | overwatch_deposit_bug check-x "${OVERWATCH_PROJECT}" x-detected "X detected")"
#   rc=$?   # 0 filed, 2 already filed as ${bug_path}, 1 not filed
# ---------------------------------------------------------------------------
overwatch_deposit_bug() {
    local check="${1:-overwatch-check}" project="${2:-}" slug="${3:-}" title="${4:-}"
    if [[ -z "${project}" || -z "${slug}" ]]; then
        echo "${check}: overwatch_deposit_bug: a project and a slug are required, e.g. overwatch_deposit_bug ${check} pgai-agent-kanban empty-files-detected 'Empty files detected'" >&2
        return 1
    fi
    local root="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}}"
    local bugs_dir="${root}/projects/${project}/bugs"
    local lib_dir intake
    lib_dir="${_OVERWATCH_CONFIG_LIB_DIR}"
    intake="$(dirname "${lib_dir:-.}")/intake.sh"
    if [[ -z "${lib_dir}" || ! -x "${intake}" ]]; then
        echo "${check}: intake.sh not found at ${intake}; reinstall the kanban so scripts/intake.sh is present" >&2
        return 1
    fi
    _overwatch_config_source temp.sh pgai_mktemp_d || return 1

    local stage
    stage="$(pgai_mktemp_d overwatch-bug-draft)" || {
        echo "${check}: cannot create a draft directory under $(pgai_temp_dir); check that the temp root is writable" >&2
        return 1
    }
    local draft_id="BUG-0000"   # the BUG-NNNN draft number intake replaces
    local draft="${stage}/${draft_id}-${slug}.md"
    local -a cites=()
    mapfile -t cites < <(_overwatch_prior_filings "${bugs_dir}" "${slug}")
    if ! {
        printf '# %s: %s\n\n' "${draft_id}" "${title}"
        if (( ${#cites[@]} > 0 )); then
            printf '**Re-file of:** %s\n' "${cites[@]}"
            printf '\n'
        fi
        cat
    } > "${draft}"; then
        echo "${check}: cannot write the bug draft ${draft}" >&2
        rm -rf "${stage}"
        return 1
    fi

    local out="" err="" rc=0
    out="$(PGAI_AGENT_KANBAN_ROOT_PATH="${root}" KANBAN_ROOT="${root}" \
        "${intake}" --project "${project}" "${draft}" 2>"${stage}/intake.err")" || rc=$?
    err="$(cat "${stage}/intake.err" 2>/dev/null)" || err=""
    rm -rf "${stage}"

    local filed live_name live_id
    case "${rc}" in
        0)
            filed="$(sed -n 's/^intake: deposited[[:space:]]*//p' <<< "${out}" | tail -n1)"
            if [[ -n "${filed}" ]]; then
                printf '%s\n' "${filed}"
                return 0
            fi
            echo "${check}: intake.sh exited 0 without naming the deposited file; look for ${slug} in ${bugs_dir}" >&2
            return 1
            ;;
        5)
            [[ -n "${err}" ]] && printf '%s\n' "${err}" >&2
            live_id="$(grep -oE 'same defect as BUG-[0-9]+' <<< "${err}" | head -n1 | grep -oE 'BUG-[0-9]+')" || live_id=""
            live_name="$(sed -nE 's/.*same defect as BUG-[0-9]+[[:space:]]*\(([^,()]+\.md).*/\1/p' <<< "${err}" | head -n1)"
            if [[ -n "${live_name}" && -f "${bugs_dir}/${live_name}" ]]; then
                printf '%s\n' "${bugs_dir}/${live_name}"
            else
                printf '%s\n' "${live_id}"
            fi
            return 2
            ;;
        *)
            [[ -n "${err}" ]] && printf '%s\n' "${err}" >&2
            echo "${check}: intake.sh refused or failed to deposit the ${slug} bug (exit ${rc}); nothing was filed" >&2
            return 1
            ;;
    esac
}
