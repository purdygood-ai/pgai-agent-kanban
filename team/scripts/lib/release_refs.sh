#!/usr/bin/env bash
# team/scripts/lib/release_refs.sh
#
# The refs an out-of-band push site may publish for one project: the
# project's prefixed main branch and its own release tags. Shared by CM's
# push watchdog (team/scripts/cm/push-watchdog.sh) and OVERWATCH's push-lag
# check (team/scripts/lib/overwatch-checks/check-push-lag.sh), so both apply
# one rule. In a repository shared by two lanes (bare v* tags and main for
# one, ai_v* tags and ai_main for the other), each lane's push site sees only
# its own refs and never publishes the other lane's.
#
# SOURCE THIS FILE; do not execute it directly. Side-effect-free on source.
# It sources project_paths.sh (beside it) when pp_branch_prefix is not
# already defined.
#
# Functions:
#   release_refs_tag_prefix <project>
#       Echo the project's branch_prefix ("ai_", or "" for none) -- the
#       prefix pp_prefix_tag and pp_prefix_branch put in front of its tags
#       and branches. Status 1 when the configured prefix cannot be read
#       (invalid characters); the caller then checks no tag and no branch
#       this time rather than guessing, since an empty-prefix guess would
#       name another lane's bare refs.
#
#   release_refs_main_branch <tag_prefix>
#       Echo the project's main branch for that prefix: "<prefix>main".
#
#   release_refs_local_release_tags <dev_tree> <tag_prefix>
#       Echo, one per line, every local tag in <dev_tree> named exactly
#       <tag_prefix>vX.Y.Z. Any other tag -- another lane's bare vX.Y.Z when
#       the prefix is "ai_", an ai_vX.Y.Z when the prefix is empty, a
#       non-release tag -- is never listed.
#
# Requires KANBAN_ROOT (read by pp_branch_prefix).

if ! declare -F pp_branch_prefix >/dev/null 2>&1; then
    # shellcheck source=project_paths.sh
    source "$(dirname "${BASH_SOURCE[0]}")/project_paths.sh"
fi

release_refs_tag_prefix() {
    local project_name="$1"
    KANBAN_ROOT="${KANBAN_ROOT:-}" PGAI_PROJECT_NAME="$project_name" pp_branch_prefix "$project_name" 2>/dev/null
}

release_refs_main_branch() {
    printf '%smain\n' "${1:-}"
}

release_refs_local_release_tags() {
    local dev_tree="$1" tag_prefix="${2:-}" tag
    local release_tag_re='^v[0-9]+\.[0-9]+\.[0-9]+$'
    while IFS= read -r tag; do
        [[ -z "$tag" ]] && continue
        [[ "${tag#"$tag_prefix"}" =~ $release_tag_re ]] || continue
        printf '%s\n' "$tag"
    done < <(git -C "$dev_tree" tag -l "${tag_prefix}v*" 2>/dev/null || true)
}
