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
#     "v[0-9]*", preserving the latest-exclusion behavior for empty-prefix
#     installs.
#
#     All four describe call sites (version_stamp.sh, check-version-divergence.sh,
#     cm/finalize-release.sh, and upgrade.sh advisory) call this function so that
#     the pattern is defined once and applied consistently.
#
#   stamp_version_files <repo_dir> <kanban_root> [<stamp_version>] [<branch_prefix>] [<installed_tag>]
#
#     Writes version identity and forensic-provenance files under <kanban_root>.
#
#     When <stamp_version> (the --stamp-version override) is a non-empty string:
#       - VERSION  ← the override value, written verbatim.
#       - VERSION_DETAIL is NOT written (the explicit value is clean by definition
#         and carries no forensic suffix to record).
#       - Any VERSION_DETAIL already under <kanban_root> is removed, so it never
#         names an earlier deposit beside the stamped VERSION.
#
#     When <stamp_version> is empty or absent (normal deposit path):
#       - VERSION identity source (choose the first that applies):
#           1. <repo_dir>/VERSION exists  → copy it verbatim to <kanban_root>/VERSION.
#              The committed file IS the identity; no git describe needed for VERSION.
#           2. <repo_dir>/VERSION absent and <repo_dir>/.git exists (a .git
#              directory, or the gitdir pointer file of a linked `git worktree`)
#              → derive VERSION from `git describe --tags --match <pattern>`, stripping
#              the describe suffix (-N-gSHA) to produce a clean tag string.
#              This is the backward-compatible path for trees without a committed VERSION.
#           3. Neither file nor .git present → skip VERSION write silently.
#       - VERSION_DETAIL ← full describe string + deposit SHA on a single line,
#                          space-separated:  "<full-describe>  deposit-sha=<sha>"
#         VERSION_DETAIL is always tool-written; it is deployment-provenance, not
#         identity, and is never committed.  Written whenever <repo_dir> is a
#         git checkout — <repo_dir>/.git is a directory or a linked worktree's
#         pointer file — whether or not <installed_tag> is passed; git -C works
#         against either shape.  With no .git at all it cannot be produced: it
#         is not written, and any VERSION_DETAIL already under <kanban_root> is
#         removed, so it never names an older release beside the new VERSION.
#
#     The describe pattern for VERSION_DETAIL (and for the fallback VERSION path)
#     is resolved by git_describe_tag_pattern using <branch_prefix>.  The filter
#     ensures alias tags (e.g. 'latest') are never considered; only release tags
#     of the project's own shape match.
#
#     When <branch_prefix> and <installed_tag> are both empty (the prefix is
#     not known, as on a plain install from a checkout with no registered
#     project), any prefixed release tag ("*_v[0-9]*") is tried first and the
#     bare "v[0-9]*" shape only when none is reachable.  A tree tagged
#     "ai_vX.Y.Z" is therefore described from that tag, not from an older bare
#     "vX.Y.Z" ancestor, while a tree with only bare tags behaves as before.
#
#     When <installed_tag> is non-empty (a version-targeted install or upgrade,
#     which already resolved the exact tag it laid down, e.g. "ai_v2.33.0"):
#       - The describe matches that tag alone, so VERSION_DETAIL names the tag
#         actually installed whatever <branch_prefix> is.  A fresh install has
#         no registered project to read a prefix from, and a bare "v[0-9]*"
#         pattern would land on an older unprefixed tag instead.
#       - If describe still yields nothing, <installed_tag> itself is used.
#       - A version-targeted install or upgrade lays down from a detached
#         `git worktree`, whose .git is a pointer file; that counts as a git
#         checkout here exactly as it does without <installed_tag>.
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
#     .git (neither a directory nor a worktree pointer file) and
#     <stamp_version> is also empty.  After a normal-deposit call (empty
#     <stamp_version>), <kanban_root>/VERSION_DETAIL exists exactly when this
#     call wrote it, so a caller tests for that file before saying the detail
#     was written.  The <stamp_version> path never writes it and removes any
#     already there, so after that call <kanban_root>/VERSION_DETAIL is absent.
#
#     Callers are responsible for the staged-vs-published divergence advisory;
#     this function does not emit any advisory output.
#
# Usage:
#   source "${SCRIPT_DIR}/lib/version_stamp.sh"
#   stamp_version_files "$REPO_DIR" "$KANBAN_ROOT" "${STAMP_VERSION:-}" "${BRANCH_PREFIX:-}"
#   stamp_version_files "$REPO_DIR" "$KANBAN_ROOT" "" "" "$RESOLVED_TAG"   # version-targeted install
#   stamp_version_files "$DEV_TREE" "$KANBAN_ROOT" "" "$BRANCH_PREFIX" "$RESOLVED_TAG"   # version-targeted upgrade
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

# stamp_version_files <repo_dir> <kanban_root> [<stamp_version>] [<branch_prefix>] [<installed_tag>]
stamp_version_files() {
    local repo_dir="${1:-}"
    local kanban_root="${2:-}"
    local stamp_version="${3:-}"
    local branch_prefix="${4:-}"
    local installed_tag="${5:-}"

    if [[ -n "$stamp_version" ]]; then
        # Operator override: write verbatim to VERSION.  No VERSION_DETAIL is
        # produced here, so remove one an earlier deposit left rather than let
        # it name an older release beside the stamped VERSION.
        printf '%s\n' "$stamp_version" > "${kanban_root}/VERSION"
        rm -f "${kanban_root}/VERSION_DETAIL"
        return 0
    fi

    # Normal deposit path.
    #
    # VERSION_DETAIL requires git — skip all git work when no .git is present.
    # -e, not -d: a linked `git worktree` has a .git pointer FILE rather than
    # a directory, and git -C works against either shape, so both count as a
    # git checkout whatever the caller passed.
    if [[ ! -e "${repo_dir}/.git" ]]; then
        # Not a git checkout.  Copy committed VERSION if present.  No
        # VERSION_DETAIL can be produced, so remove one an earlier deposit
        # left rather than let it name an older release beside the new VERSION.
        if [[ -f "${repo_dir}/VERSION" ]]; then
            cp "${repo_dir}/VERSION" "${kanban_root}/VERSION"
        fi
        rm -f "${kanban_root}/VERSION_DETAIL"
        return 0
    fi

    # Describe patterns, tried in order until one yields a result.
    local -a _match_patterns
    if [[ -n "$installed_tag" ]]; then
        # The caller resolved the exact tag it laid down: describe against it
        # alone rather than guessing the project's tag shape.
        _match_patterns=("$installed_tag")
    elif [[ -n "$branch_prefix" ]]; then
        _match_patterns=("$(git_describe_tag_pattern "$branch_prefix")")
    else
        # Prefix unknown (a plain install from a checkout has no registered
        # project to read one from): prefer any prefixed release tag, then
        # fall back to the bare shape, so a tree tagged "<prefix>vX.Y.Z" is
        # not described from an older bare "vX.Y.Z" ancestor.
        _match_patterns=("$(git_describe_tag_pattern '*_')" "$(git_describe_tag_pattern "")")
    fi

    local full_describe="" _match_pattern
    for _match_pattern in "${_match_patterns[@]}"; do
        full_describe="$(git -C "$repo_dir" describe --tags --match "$_match_pattern" 2>/dev/null || true)"
        [[ -n "$full_describe" ]] && break
    done
    if [[ -z "$full_describe" && -n "$installed_tag" ]]; then
        full_describe="$installed_tag"
    fi
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
