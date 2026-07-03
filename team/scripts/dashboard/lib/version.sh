#!/usr/bin/env bash
# version.sh — single-source version resolution for dashboard scripts.
#
# Provides two functions:
#
#   get_latest_released_tag <repo_root>
#
#     Returns (via stdout) the highest semver tag that has been merged into
#     origin/main, using a reachability-independent query:
#
#       git -C <repo_root> tag --merged origin/main | sort -V | tail -1
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

# get_latest_released_tag <repo_root>
#
# Returns the highest semver tag merged into origin/main using a
# reachability-independent query.  Returns empty string on any failure.
# Never returns a describe commit-offset string.  Never fails the caller.
get_latest_released_tag() {
    local repo_root="${1:-}"

    if [[ -z "$repo_root" ]] || [[ ! -d "${repo_root}/.git" ]]; then
        return 0
    fi

    git -C "$repo_root" tag --merged origin/main 2>/dev/null \
        | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
        | sort -V \
        | tail -n1 \
        || true
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
    if [[ -z "$_ver" ]] && [[ -n "$repo_root" ]] && [[ -d "${repo_root}/.git" ]]; then
        # Best-effort fetch so the tag list is current; failure is silently ignored.
        git -C "$repo_root" fetch origin --tags --quiet 2>/dev/null || true
        _ver="$(get_latest_released_tag "$repo_root")"
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
