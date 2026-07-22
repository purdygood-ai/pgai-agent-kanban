#!/usr/bin/env bash
# version_stamp.sh — shared version-stamp write logic for install.sh and upgrade.sh.
#
# Provides two functions:
#
#   git_describe_tag_pattern [<branch_prefix>]
#
#     Returns (via stdout) the git-describe --match pattern appropriate for
#     the project's tag naming convention.
#
#     When <branch_prefix> is non-empty (e.g. "ai_"), the pattern is
#     "${branch_prefix}v[0-9]*" (e.g. "ai_v[0-9]*"), ensuring prefixed
#     lanes resolve their own tags rather than walking back to bare v-ancestors.
#     When <branch_prefix> is empty or absent, the pattern collapses to
#     "v[0-9]*", preserving the an earlier defect latest-exclusion for empty-prefix
#     installs.
#
#     All four describe call sites (version_stamp.sh, check-version-divergence.sh,
#     cm/finalize-release.sh, and upgrade.sh advisory) call this function so that
#     the pattern is defined once and applied consistently.
#
#   stamp_version_files <repo_dir> <kanban_root> [<stamp_version>] [<branch_prefix>]
#
#     Writes version identity and forensic-provenance files under <kanban_root>.
#
#     When <stamp_version> (the --stamp-version override) is a non-empty string:
#       - VERSION  ← the override value, written verbatim.
#       - VERSION_DETAIL is NOT written (the explicit value is clean by definition
#         and carries no forensic suffix to record).
#
#     When <stamp_version> is empty or absent (normal deposit path):
#       - VERSION identity source (choose the first that applies):
#           1. <repo_dir>/VERSION exists  → copy it verbatim to <kanban_root>/VERSION.
#              The committed file IS the identity; no git describe needed for VERSION.
#           2. <repo_dir>/VERSION absent and <repo_dir>/.git exists
#              → derive VERSION from `git describe --tags --match <pattern>`, stripping
#              the describe suffix (-N-gSHA) to produce a clean tag string.
#              This is the backward-compatible path for trees without a committed VERSION.
#           3. Neither file nor .git present → skip VERSION write silently.
#       - VERSION_DETAIL ← full describe string + deposit SHA on a single line,
#                          space-separated:  "<full-describe>  deposit-sha=<sha>"
#         VERSION_DETAIL is always tool-written; it is deployment-provenance, not
#         identity, and is never committed.  Written whenever a .git repo is present;
#         skipped silently otherwise.
#
#     The describe pattern for VERSION_DETAIL (and for the fallback VERSION path)
#     is resolved by git_describe_tag_pattern using <branch_prefix>.  The filter
#     ensures alias tags (e.g. 'latest') are never considered; only release tags
#     of the project's own shape match.
#
#     Suffix-strip examples (fallback path only):
#       ai_v1.19.0               → ai_v1.19.0           (tag-exact)
#       ai_v1.19.0-1-gab12cd3   → ai_v1.19.0           (tag+1 polish commit)
#
#     Both files are written with printf '%s\n' to produce exactly one trailing
#     newline (consistent with the rest of the codebase).
#
#     Return value: always 0.  The caller decides what to do on a missing git
#     repo; this function skips all git work silently when <repo_dir> has no
#     .git directory and <stamp_version> is also empty.
#
#     Callers are responsible for the staged-vs-published divergence advisory;
#     this function does not emit any advisory output.
#
# Usage:
#   source "${SCRIPT_DIR}/lib/version_stamp.sh"
#   stamp_version_files "$REPO_DIR" "$KANBAN_ROOT" "${STAMP_VERSION:-}" "${BRANCH_PREFIX:-}"
#
# Guard against double-sourcing:
[[ -n "${_PGAI_VERSION_STAMP_SH_LOADED:-}" ]] && return 0
_PGAI_VERSION_STAMP_SH_LOADED=1

# git_describe_tag_pattern [<branch_prefix>]
#
# Returns (via stdout) the git-describe --match pattern for the project's tag
# naming convention:
#   - Non-empty prefix (e.g. "ai_") → "<prefix>v[0-9]*"  (e.g. "ai_v[0-9]*")
#   - Empty or absent prefix        → "v[0-9]*"
#
# This is the single definition of the describe match pattern.  All describe
# call sites call this function so that the pattern is always prefix-aware and
# defined in one place.
git_describe_tag_pattern() {
    local prefix="${1:-}"
    if [[ -n "$prefix" ]]; then
        printf '%s' "${prefix}v[0-9]*"
    else
        printf '%s' "v[0-9]*"
    fi
}

# stamp_version_files <repo_dir> <kanban_root> [<stamp_version>] [<branch_prefix>]
stamp_version_files() {
    local repo_dir="${1:-}"
    local kanban_root="${2:-}"
    local stamp_version="${3:-}"
    local branch_prefix="${4:-}"

    if [[ -n "$stamp_version" ]]; then
        # Operator override: write verbatim to VERSION; no VERSION_DETAIL.
        printf '%s\n' "$stamp_version" > "${kanban_root}/VERSION"
        return 0
    fi

    # Normal deposit path.
    #
    # VERSION_DETAIL requires git — skip all git work when no .git is present.
    if [[ ! -d "${repo_dir}/.git" ]]; then
        # No git repo.  Copy committed VERSION if present; skip everything else.
        if [[ -f "${repo_dir}/VERSION" ]]; then
            cp "${repo_dir}/VERSION" "${kanban_root}/VERSION"
        fi
        return 0
    fi

    local _match_pattern
    _match_pattern="$(git_describe_tag_pattern "$branch_prefix")"

    local full_describe
    full_describe="$(git -C "$repo_dir" describe --tags --match "$_match_pattern" 2>/dev/null || true)"
    full_describe="${full_describe:-unknown-dev}"

    local deposit_sha
    deposit_sha="$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null || true)"
    deposit_sha="${deposit_sha:-unknown}"

    # VERSION: use the committed file when present; otherwise derive from git describe.
    if [[ -f "${repo_dir}/VERSION" ]]; then
        # The committed VERSION IS the identity.  Copy it verbatim so the live
        # install contains exactly what the tree carries.
        cp "${repo_dir}/VERSION" "${kanban_root}/VERSION"
    else
        # Backward-compatible path: no committed VERSION in the source tree.
        # Derive a clean tag by stripping the describe suffix (-N-gSHA).
        # At a tag-exact checkout, describe returns just the tag and the strip
        # is a no-op.  Past a tag the suffix is removed.
        local clean_tag="${full_describe%%-[0-9]*-g*}"
        # Fallback: if stripping left an empty string (should not happen with
        # valid git describe output), use the full describe as-is.
        clean_tag="${clean_tag:-$full_describe}"
        printf '%s\n' "$clean_tag" > "${kanban_root}/VERSION"
    fi

    # VERSION_DETAIL: forensic deployment provenance.  Always tool-written;
    # never committed.  Carries the full describe output (including any
    # -N-gSHA suffix) and the deposit SHA for debugging.
    # Displayed nowhere by default.
    printf '%s  deposit-sha=%s\n' "$full_describe" "$deposit_sha" > "${kanban_root}/VERSION_DETAIL"

    return 0
}
