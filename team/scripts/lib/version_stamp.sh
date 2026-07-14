#!/usr/bin/env bash
# version_stamp.sh — shared version-stamp write logic for install.sh and upgrade.sh.
#
# Provides one function:
#
#   stamp_version_files <repo_dir> <kanban_root> [<stamp_version>]
#
#     Resolves the installed version from the git repository at <repo_dir>
#     and writes the result to the VERSION and VERSION_DETAIL files under
#     <kanban_root>.
#
#     When <stamp_version> (the --stamp-version override) is a non-empty string:
#       - VERSION  ← the override value, written verbatim.
#       - VERSION_DETAIL is NOT written (the explicit value is clean by definition
#         and carries no forensic suffix to record).
#
#     When <stamp_version> is empty or absent (normal deposit path):
#       - Runs `git describe --tags` against <repo_dir> HEAD.
#       - Strips the describe suffix (-N-gSHA) to produce a clean tag string.
#         At an exact tag the strip is a no-op; the clean tag equals the full
#         describe output.  Past a tag the suffix is removed.
#         Examples:
#           ai_v1.19.0               → ai_v1.19.0           (tag-exact)
#           ai_v1.19.0-1-gab12cd3   → ai_v1.19.0           (tag+1 polish commit)
#       - VERSION        ← clean tag (suffix-stripped).
#       - VERSION_DETAIL ← full describe string + deposit SHA on a single line,
#                          space-separated:  "<full-describe>  deposit-sha=<sha>"
#         This is tool-owned, sibling to VERSION, and displayed nowhere by default.
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
#   stamp_version_files "$REPO_DIR" "$KANBAN_ROOT" "${STAMP_VERSION:-}"
#
# Guard against double-sourcing:
[[ -n "${_PGAI_VERSION_STAMP_SH_LOADED:-}" ]] && return 0
_PGAI_VERSION_STAMP_SH_LOADED=1

# stamp_version_files <repo_dir> <kanban_root> [<stamp_version>]
stamp_version_files() {
    local repo_dir="${1:-}"
    local kanban_root="${2:-}"
    local stamp_version="${3:-}"

    if [[ -n "$stamp_version" ]]; then
        # Operator override: write verbatim to VERSION; no VERSION_DETAIL.
        printf '%s\n' "$stamp_version" > "${kanban_root}/VERSION"
        return 0
    fi

    # Normal deposit path: resolve from git.
    if [[ ! -d "${repo_dir}/.git" ]]; then
        # No git repo — skip stamp silently.  Caller can warn if appropriate.
        return 0
    fi

    local full_describe
    full_describe="$(git -C "$repo_dir" describe --tags 2>/dev/null || true)"
    full_describe="${full_describe:-unknown-dev}"

    # Strip the describe suffix (-N-gSHA) to produce a clean tag.
    # Pattern: remove the shortest suffix that starts with a dash followed by
    # one or more digits, a dash, and a hex commit abbreviation (g<sha>).
    # At a tag-exact checkout, describe returns just the tag and this is a no-op.
    local clean_tag="${full_describe%%-[0-9]*-g*}"
    # Fallback: if stripping left an empty string (should not happen with valid
    # git describe output), use the full describe as-is.
    clean_tag="${clean_tag:-$full_describe}"

    local deposit_sha
    deposit_sha="$(git -C "$repo_dir" rev-parse HEAD 2>/dev/null || true)"
    deposit_sha="${deposit_sha:-unknown}"

    # VERSION: clean operator-facing label.
    printf '%s\n' "$clean_tag" > "${kanban_root}/VERSION"

    # VERSION_DETAIL: forensics for debugging a deploy.  Displayed nowhere by default.
    printf '%s  deposit-sha=%s\n' "$full_describe" "$deposit_sha" > "${kanban_root}/VERSION_DETAIL"

    return 0
}
