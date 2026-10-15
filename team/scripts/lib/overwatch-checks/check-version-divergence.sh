#!/usr/bin/env bash
# team/scripts/lib/overwatch-checks/check-version-divergence.sh
#
# OVERWATCH Tier-1 detection module: compare the installed VERSION at
# $KANBAN_ROOT/VERSION with the dev tree's `git describe --tags HEAD`.
#
# This check is REPORT-ONLY. It never modifies any file, never auto-fixes.
# Deploys are operator verbs.
#
# Detection:
#   - Read $KANBAN_ROOT/VERSION (written by install.sh; identifies the deployed
#     version of the kanban framework).  When the dev tree commits a VERSION
#     file, install copies it verbatim, so this value is normally the bare
#     version (e.g. "v2.26.10"); a tree without a committed VERSION yields the
#     prefixed tag name instead (e.g. "ai_v2.26.10").
#   - Read the dev tree path from the project's project.cfg (dev_tree_path key),
#     or fall back to PGAI_DEV_TREE_PATH.
#   - Read branch_prefix through pp_branch_prefix (project_paths.sh), which
#     reads the project's project.cfg.  The prefix is used twice: to build the
#     describe --match pattern, and to normalize both values before comparing.
#     The pattern is constructed by git_describe_tag_pattern (from
#     version_stamp.sh): non-empty prefix produces "<prefix>v[0-9]*" (e.g.
#     "ai_v[0-9]*"); empty prefix collapses to "v[0-9]*", which also keeps
#     alias tags such as 'latest' out of the result.
#   - Run `git describe --tags --match <pattern> HEAD` in the dev tree to get
#     the head commit description. The --match filter restricts to the project's
#     own release tags so alias tags (e.g. 'latest') and mismatched-prefix tags
#     are never considered.
#
# Comparison:
#   - What is compared: the installed VERSION string against the full describe
#     output, after removing one leading branch prefix from each side.  With
#     branch_prefix "ai_", installed "v1.23.0" and describe "ai_v1.23.0" both
#     normalize to "v1.23.0" and read as no divergence; installed "ai_v1.23.0"
#     normalizes the same way.  With an empty prefix nothing is removed and the
#     raw strings are compared as-is.
#   - A describe carrying a "-N-g<sha>" suffix (dev tree HEAD ahead of its
#     latest release tag) does not equal any installed VERSION, so it is
#     reported as a divergence: the check reports any difference between the
#     installed release and the dev tree HEAD, not only a version-number lag.
#   - If the normalized strings differ, log an action-log entry via
#     overwatch_log_action and emit a diagnostic to stderr.  Both the entry
#     and the diagnostic name the raw installed VERSION and the raw describe
#     output, so the report shows exactly what was read.
#
# When no dev tree is configured, the check skips silently (no dev tree to
# compare against).
#
# When KANBAN_ROOT/VERSION is absent, the installed version is treated as
# "unknown" and the check still reports if git describe produces a result.
#
# Module contract:
#   - Sourceable without side effects.
#   - Exports: overwatch_check_version_divergence
#   - Zero arguments. All context from environment variables.
#   - Returns 0 in all cases (REPORT-ONLY; errors in detection are logged, not fatal).
#
# Required environment variables (set by OVERWATCH driver or sweep runner):
#   KANBAN_ROOT       — absolute path to the kanban root
#   OVERWATCH_PROJECT — project name (e.g. "pgai-agent-kanban")
#
# Optional:
#   PGAI_DEV_TREE_PATH — fallback dev tree path when project.cfg has no dev_tree_path
#
# Usage (standalone):
#   bash check-version-divergence.sh
#
# Exit codes:
#   0 — always (REPORT-ONLY check)

# ---------------------------------------------------------------------------
# _cvd_resolve_env
# Resolve KANBAN_ROOT, OVERWATCH_PROJECT, and dev tree path.
# Sets _CVD_DEV_TREE in the calling scope.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cvd_resolve_env() {
    if [[ -z "${KANBAN_ROOT:-}" ]]; then
        KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-${HOME}/pgai_agent_kanban}"
    fi
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "check-version-divergence: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
        return 1
    fi

    if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
        local _all_projects=""
        if declare -f projects_cfg_list >/dev/null 2>&1; then
            _all_projects="$(projects_cfg_list 2>/dev/null)"
        else
            local _cfg="${KANBAN_ROOT}/projects.cfg"
            _all_projects="$(awk '/^\[project:[a-zA-Z0-9_-]+\]/{match($0,/\[project:([a-zA-Z0-9_-]+)\]/,a);print a[1]}' "${_cfg}" 2>/dev/null)"
        fi
        local _project_count
        _project_count="$(echo "${_all_projects}" | grep -c '[^[:space:]]' 2>/dev/null || echo 0)"
        if [[ "${_project_count}" -gt 1 ]]; then
            echo "check-version-divergence: ERROR: OVERWATCH_PROJECT not set and multiple projects registered." >&2
            return 1
        fi
        OVERWATCH_PROJECT="$(echo "${_all_projects}" | grep '[^[:space:]]' | head -n1)"
        if [[ -z "${OVERWATCH_PROJECT:-}" ]]; then
            echo "check-version-divergence: ERROR: no project specified and none resolvable from projects.cfg" >&2
            return 1
        fi
    fi

    # Resolve dev tree path from project.cfg.
    local _proj_root="${KANBAN_ROOT}/projects/${OVERWATCH_PROJECT}"
    local _cfg_file=""
    if [[ -f "${_proj_root}/project.cfg" ]]; then
        _cfg_file="${_proj_root}/project.cfg"
    elif [[ -f "${_proj_root}/PROJECT.cfg" ]]; then
        _cfg_file="${_proj_root}/PROJECT.cfg"
    fi

    _CVD_DEV_TREE=""
    if [[ -n "${_cfg_file}" ]]; then
        _CVD_DEV_TREE="$(grep -E '^[[:space:]]*dev_tree_path[[:space:]]*=' "${_cfg_file}" \
            | head -n1 \
            | sed 's|^[^=]*=[[:space:]]*||; s|[[:space:]]*$||; s|^["'"'"']||; s|["'"'"']$||')"
    fi
    if [[ -z "${_CVD_DEV_TREE}" ]] && [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        _CVD_DEV_TREE="${PGAI_DEV_TREE_PATH}"
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _cvd_resolve_branch_prefix
# Resolve the project's configured branch_prefix through pp_branch_prefix
# (project_paths.sh), sourcing that helper from this tree's lib dir when it is
# not already loaded.  Sets _CVD_BRANCH_PREFIX in the calling scope; an
# unavailable helper or an invalid configured value leaves it empty and emits
# a diagnostic to stderr (REPORT-ONLY: never fatal).
# Requires KANBAN_ROOT and OVERWATCH_PROJECT (set by _cvd_resolve_env).
# ---------------------------------------------------------------------------
_cvd_resolve_branch_prefix() {
    _CVD_BRANCH_PREFIX=""

    if ! declare -f pp_branch_prefix >/dev/null 2>&1; then
        local lib_dir
        lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd)" || lib_dir=""
        if [[ -n "${lib_dir}" && -f "${lib_dir}/project_paths.sh" ]]; then
            # shellcheck source=../project_paths.sh
            source "${lib_dir}/project_paths.sh"
        fi
    fi

    if ! declare -f pp_branch_prefix >/dev/null 2>&1; then
        echo "check-version-divergence: pp_branch_prefix unavailable (project_paths.sh not found beside this check); comparing without branch-prefix normalization" >&2
        return 0
    fi

    local prefix
    if ! prefix="$(KANBAN_ROOT="${KANBAN_ROOT}" pp_branch_prefix "${OVERWATCH_PROJECT}")"; then
        echo "check-version-divergence: branch_prefix for project '${OVERWATCH_PROJECT}' could not be read; comparing without branch-prefix normalization" >&2
        prefix=""
    fi
    _CVD_BRANCH_PREFIX="${prefix}"
    return 0
}

# ---------------------------------------------------------------------------
# _cvd_strip_branch_prefix <value> <branch_prefix>
# Echo <value> with one leading <branch_prefix> removed.  Values that do not
# start with the prefix, and every value when the prefix is empty, are echoed
# unchanged.  Used to put the installed VERSION and the describe output into
# the same bare-version shape before they are compared.
# Example: _cvd_strip_branch_prefix "ai_v1.23.0" "ai_"  ->  v1.23.0
# ---------------------------------------------------------------------------
_cvd_strip_branch_prefix() {
    local value="$1"
    local branch_prefix="${2:-}"
    if [[ -n "${branch_prefix}" && "${value}" == "${branch_prefix}"* ]]; then
        value="${value#"${branch_prefix}"}"
    fi
    echo "${value}"
}

# ---------------------------------------------------------------------------
# _cvd_load_protocol
# Ensure overwatch_log_action and git_describe_tag_pattern are available.
# Returns 0 on success, 1 on failure.
# ---------------------------------------------------------------------------
_cvd_load_protocol() {
    local lib_dir
    lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)" || {
        echo "check-version-divergence: cannot resolve lib dir" >&2
        return 1
    }

    # Load git_describe_tag_pattern from the shared version_stamp helper.
    if ! declare -f git_describe_tag_pattern >/dev/null 2>&1; then
        local version_stamp_sh="${lib_dir}/../version_stamp.sh"
        if [[ -f "${version_stamp_sh}" ]]; then
            # shellcheck source=/dev/null
            source "${version_stamp_sh}"
        fi
    fi

    if declare -f overwatch_log_action >/dev/null 2>&1; then
        return 0
    fi
    local protocol_sh="${lib_dir}/../overwatch_protocol.sh"
    if [[ ! -f "${protocol_sh}" ]]; then
        echo "check-version-divergence: overwatch_protocol.sh not found at ${protocol_sh}" >&2
        return 1
    fi
    # shellcheck source=/dev/null
    source "${protocol_sh}"
}

# ---------------------------------------------------------------------------
# _cvd_read_installed_version <kanban_root>
# Read the installed VERSION from $kanban_root/VERSION.
# Echoes the trimmed content, or "unknown" if the file is absent or empty.
# ---------------------------------------------------------------------------
_cvd_read_installed_version() {
    local kanban_root="$1"
    local version_file="${kanban_root}/VERSION"

    if [[ ! -f "${version_file}" ]]; then
        echo "unknown"
        return 0
    fi

    local ver
    ver="$(cat "${version_file}" 2>/dev/null | tr -d '[:space:]')"
    if [[ -z "${ver}" ]]; then
        echo "unknown"
    else
        echo "${ver}"
    fi
}

# ---------------------------------------------------------------------------
# _cvd_read_dev_describe <dev_tree> [<branch_prefix>]
# Run `git describe --tags --match <pattern> HEAD` in the dev tree, where
# <pattern> is resolved by git_describe_tag_pattern using <branch_prefix>.
# The --match filter restricts results to numeric release tags of the
# project's own shape, preventing alias tags (e.g. 'latest') from being
# picked when they share a commit, and preventing bare-v ancestors from
# being picked on prefixed lanes.
# Echoes the result, or "unknown" on any failure.
# ---------------------------------------------------------------------------
_cvd_read_dev_describe() {
    local dev_tree="$1"
    local branch_prefix="${2:-}"

    if [[ -z "${dev_tree}" || ! -d "${dev_tree}" ]]; then
        echo "unknown"
        return 0
    fi

    local _match_pattern
    if declare -f git_describe_tag_pattern >/dev/null 2>&1; then
        _match_pattern="$(git_describe_tag_pattern "$branch_prefix")"
    else
        # Fallback when helper is unavailable: use prefix directly.
        _match_pattern="${branch_prefix}v[0-9]*"
    fi

    local desc
    desc="$(git -C "${dev_tree}" describe --tags --match "$_match_pattern" HEAD 2>/dev/null || true)"
    if [[ -z "${desc}" ]]; then
        # Fallback: try without --tags (any ref)
        desc="$(git -C "${dev_tree}" describe HEAD 2>/dev/null || true)"
    fi
    if [[ -z "${desc}" ]]; then
        echo "unknown"
    else
        echo "${desc}"
    fi
}

# ---------------------------------------------------------------------------
# overwatch_check_version_divergence
# Main detection function. REPORT-ONLY — never modifies any file.
# Returns 0 in all cases.
# ---------------------------------------------------------------------------
overwatch_check_version_divergence() {
    _cvd_resolve_env || return 0
    _cvd_load_protocol || return 0
    _cvd_resolve_branch_prefix

    local kanban_root="${KANBAN_ROOT}"
    local dev_tree="${_CVD_DEV_TREE:-}"
    local branch_prefix="${_CVD_BRANCH_PREFIX:-}"

    echo "check-version-divergence: reading installed VERSION from ${kanban_root}/VERSION" >&2

    local installed_version
    installed_version="$(_cvd_read_installed_version "${kanban_root}")"
    echo "check-version-divergence: installed_version=${installed_version}" >&2

    if [[ -z "${dev_tree}" ]]; then
        echo "check-version-divergence: no dev tree configured; skipping git describe comparison" >&2
        overwatch_log_action \
            "check-version-divergence" \
            "${OVERWATCH_PROJECT}" \
            "skipped-no-dev-tree" \
            "none" \
            "No dev_tree_path configured; installed_version=${installed_version}" \
        || true
        return 0
    fi

    if [[ ! -d "${dev_tree}" ]]; then
        echo "check-version-divergence: dev tree path does not exist: ${dev_tree}; skipping" >&2
        overwatch_log_action \
            "check-version-divergence" \
            "${OVERWATCH_PROJECT}" \
            "skipped-dev-tree-missing" \
            "none" \
            "dev_tree_path=${dev_tree} does not exist; installed_version=${installed_version}" \
        || true
        return 0
    fi

    echo "check-version-divergence: running git describe in ${dev_tree}" >&2
    local dev_describe
    dev_describe="$(_cvd_read_dev_describe "${dev_tree}" "${branch_prefix}")"
    echo "check-version-divergence: dev_describe=${dev_describe}" >&2

    # Compare in bare-version shape: remove the branch prefix from both sides
    # so a committed unprefixed VERSION matches its prefixed release tag.
    local installed_cmp dev_cmp
    installed_cmp="$(_cvd_strip_branch_prefix "${installed_version}" "${branch_prefix}")"
    dev_cmp="$(_cvd_strip_branch_prefix "${dev_describe}" "${branch_prefix}")"
    echo "check-version-divergence: comparing installed=${installed_cmp} with dev=${dev_cmp} (branch_prefix='${branch_prefix}' removed)" >&2

    if [[ "${installed_cmp}" == "${dev_cmp}" ]]; then
        echo "check-version-divergence: installed version matches dev describe (${installed_version} vs ${dev_describe}); no divergence" >&2
        overwatch_log_action \
            "check-version-divergence" \
            "${OVERWATCH_PROJECT}" \
            "version-match" \
            "none" \
            "installed=${installed_version} matches dev=${dev_describe} (branch_prefix='${branch_prefix}' ignored); no divergence" \
        || true
        return 0
    fi

    # Versions differ — log the divergence (REPORT-ONLY, no auto-fix).
    echo "check-version-divergence: VERSION DIVERGENCE DETECTED: installed=${installed_version}, dev=${dev_describe}" >&2
    overwatch_log_action \
        "check-version-divergence" \
        "${OVERWATCH_PROJECT}" \
        "report:version-divergence" \
        "none" \
        "installed=${installed_version} differs from dev git-describe=${dev_describe}; REPORT-ONLY; deploy is an operator verb" \
    || true

    # Return 0 — REPORT-ONLY check; finding is not a halt condition.
    return 0
}

# ---------------------------------------------------------------------------
# Standalone invocation entry point.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]:-}" == "${0}" ]]; then
    overwatch_check_version_divergence "$@"
    exit $?
fi
