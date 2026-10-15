#!/usr/bin/env bash
# version.sh — single-source version resolution for dashboard scripts.
#
# Provides two functions:
#
#   get_latest_released_tag <repo_root> [<remote>] [<branch_prefix>]
#
#     Returns (via stdout) the highest <branch_prefix>vX.Y.Z tag (prefix
#     stripped) that has been merged into <remote>/<branch_prefix>main, using
#     a reachability-independent query:
#
#       git -C <repo_root> tag --merged <remote>/<branch_prefix>main | sort -V | tail -1
#
#     <remote> defaults to origin and <branch_prefix> to none, so the
#     one-argument form reads origin/main and bare vX.Y.Z tags.
#
#     Best-effort: returns empty string on any failure (no git repo, no tags,
#     no network) — never returns a describe commit-offset string and never
#     fails the caller.  This is the single canonical definition of
#     "latest released tag" for the codebase.  Both the dashboard's Tier 3
#     and upgrade.sh call this function; neither duplicates the query.
#
#   get_kanban_version <kanban_root> <repo_root> [<last_released>]
#
#     Returns (via stdout) the best-available deployed version string, walking
#     through the following tier order:
#
#       Tier 1: $kanban_root/VERSION          — install-generated; always present
#                                               on an installed system; this is the
#                                               canonical deployed version.
#       Tier 2: $repo_root/VERSION            — dev-tree VERSION if present (unusual;
#                                               VERSION is install-generated and not
#                                               committed, but kept as a fallback).
#       Tier 3: get_latest_released_tag()     — reachability-independent tag lookup;
#                                               the canonical pattern for dev-tree
#                                               launches with no VERSION file present.
#                                               Reads <remote>/<prefix>main with the
#                                               framework project's own
#                                               git_remote_name and branch_prefix
#                                               (<kanban_root>/projects/pgai-agent-kanban/
#                                               project.cfg, via project_paths.sh when the
#                                               caller has sourced it); origin and no
#                                               prefix without that file or without
#                                               project_paths.sh; an invalid
#                                               git_remote_name skips the tier.
#       Tier 4: last_released argument        — caller-supplied value from data.sh's
#                                               LAST_RELEASED field; used when no
#                                               VERSION file and no git tags are
#                                               resolvable.
#       Tier 5: "unknown"                     — final fallback.
#
# Arguments for get_kanban_version:
#   kanban_root    — path to the live kanban install directory (KANBAN_ROOT)
#   repo_root      — path to the git dev-tree root (REPO_ROOT / PGAI_DEV_TREE_PATH)
#   last_released  — (optional) value from data.sh's LAST_RELEASED key;
#                    pass "" or omit to skip tier 4.
#
# get_kanban_version is the single decision point for version resolution in
# scripts/dashboard/.  Do not add per-script tier-order logic to any sibling
# script.
#
# Usage from callers:
#   source "${SCRIPT_DIR}/lib/version.sh"
#   KANBAN_VERSION="$(get_kanban_version "$KANBAN_ROOT" "$REPO_ROOT" "${LAST_RELEASED:-}")"
#
# Guard against double-sourcing:
[[ -n "${_PGAI_DASHBOARD_VERSION_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_VERSION_SH_LOADED=1

# get_latest_released_tag <repo_root> [<remote>] [<branch_prefix>]
#
# Returns the highest <branch_prefix>vX.Y.Z tag (prefix stripped) merged into
# <remote>/<branch_prefix>main using a reachability-independent query.
# <remote> defaults to origin, <branch_prefix> to none. Returns empty string
# on any failure. Never returns a describe commit-offset string. Never fails
# the caller.
get_latest_released_tag() {
    local repo_root="${1:-}"
    local remote="${2:-origin}"
    local prefix="${3:-}"

    if [[ -z "$repo_root" ]] || [[ ! -d "${repo_root}/.git" ]]; then
        return 0
    fi

    local escaped_prefix
    escaped_prefix="$(printf '%s' "$prefix" | sed 's/[.[\*^${}|()]/\\&/g')"
    git -C "$repo_root" tag --merged "${remote}/${prefix}main" 2>/dev/null \
        | grep -E "^${escaped_prefix}v[0-9]+\.[0-9]+\.[0-9]+\$" \
        | sed "s|^${escaped_prefix}||" \
        | sort -V \
        | tail -n1 \
        || true
}

# The project whose config names the framework repository's remote and
# branch prefix (the Python twin: pgai_agent_kanban.lib.released_version,
# FRAMEWORK_PROJECT).
PGAI_DASHBOARD_FRAMEWORK_PROJECT="pgai-agent-kanban"

# _pgai_framework_release_refs <kanban_root>
#
# Echoes "<remote> <branch_prefix>" for get_latest_released_tag: the
# framework project's git_remote_name (pp_git_remote_name --quiet) and
# branch_prefix (one surrounding double quote stripped) read from
# <kanban_root>/projects/pgai-agent-kanban/project.cfg. "origin" and no
# prefix when that file is absent or project_paths.sh is not loaded.
# Returns 1 (no output) when git_remote_name is invalid. Local file reads
# only.
_pgai_framework_release_refs() {
    local kanban_root="${1:-}"
    local name="$PGAI_DASHBOARD_FRAMEWORK_PROJECT"
    if ! declare -F pp_git_remote_name >/dev/null 2>&1 || [[ -z "$kanban_root" ]]; then
        echo "origin "
        return 0
    fi
    local remote cfg_file prefix=""
    remote="$(KANBAN_ROOT="$kanban_root" pp_git_remote_name --quiet "$name" 2>/dev/null)" || return 1
    cfg_file="$(_pp_project_cfg_file "${kanban_root}/projects/${name}")"
    if [[ -n "$cfg_file" ]]; then
        prefix="$(_pp_read_cfg_key "$cfg_file" project branch_prefix "" 2>/dev/null || true)"
        prefix="${prefix%\"}"
        prefix="${prefix#\"}"
    fi
    echo "${remote} ${prefix}"
}

get_kanban_version() {
    local kanban_root="${1:-}"
    local repo_root="${2:-}"
    local last_released="${3:-}"

    local _ver=""

    # Tier 1: $kanban_root/VERSION (install-generated, canonical deployed version)
    if [[ -f "${kanban_root}/VERSION" ]]; then
        _ver="$(tr -d '[:space:]' < "${kanban_root}/VERSION" 2>/dev/null || true)"
    fi

    # Tier 2: $repo_root/VERSION (dev-tree VERSION if present)
    if [[ -z "$_ver" ]] && [[ -n "$repo_root" ]] && [[ -f "${repo_root}/VERSION" ]]; then
        _ver="$(tr -d '[:space:]' < "${repo_root}/VERSION" 2>/dev/null || true)"
    fi

    # Tier 3: get_latest_released_tag() (reachability-independent)
    # This is a dashboard-render-only helper: it never performs a network git
    # operation. get_latest_released_tag() reads "git tag --merged
    # <remote>/<prefix>main", which is a purely local ref comparison (the
    # remote-tracking ref is whatever it already holds on disk) — no fetch,
    # ls-remote, pull, push, or clone. A stale or absent ref simply yields an
    # empty result here, which falls through to Tier 4/5 below.
    if [[ -z "$_ver" ]] && [[ -n "$repo_root" ]] && [[ -d "${repo_root}/.git" ]]; then
        local _refs _remote _prefix
        if _refs="$(_pgai_framework_release_refs "$kanban_root")"; then
            _remote="${_refs%% *}"
            _prefix="${_refs#* }"
            _ver="$(get_latest_released_tag "$repo_root" "$_remote" "$_prefix")"
        fi
    fi

    # Tier 4: last_released argument (from data.sh's LAST_RELEASED key)
    if [[ -z "$_ver" ]] && [[ -n "$last_released" ]] && [[ "$last_released" != "none" ]]; then
        _ver="$last_released"
    fi

    # Tier 5: unknown (final fallback)
    if [[ -z "$_ver" ]]; then
        _ver="unknown"
    fi

    echo "$_ver"
}
