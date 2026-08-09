#!/usr/bin/env bash
# team/scripts/lib/version_resolve.sh
# Shared version-tag resolution and clean-source laydown mechanism.
#
# Sourceable by both install.sh and team/scripts/upgrade.sh so the two
# scripts cannot drift on how a requested version is normalized, resolved
# against the source repo's tags, and materialized into a throwaway
# worktree for laydown — independent of whatever branch happens to be
# checked out in that repo.
#
# Source this file to get the version_resolve_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/version_resolve.sh"
#
# Sourcing this file ALSO makes the semver_* functions from semver.sh
# available (semver_compare, semver_lt, semver_lte, semver_gt, semver_gte,
# semver_eq, semver_from_filename) — see "Semver comparison" below.
#
# Public functions
# ----------------
#   version_resolve_normalize <requested> [<branch_prefix>]
#       Strips a leading <branch_prefix> from <requested> when present, so
#       an operator who (mistakenly) includes the project's internal branch
#       prefix in a version argument still resolves correctly.
#       Example: version_resolve_normalize "ai_v2.1.0" "ai_"  ->  "v2.1.0"
#
#   version_resolve_tag_name <normalized_version> [<branch_prefix>]
#       Builds the actual tag name expected in the repo by re-applying
#       <branch_prefix> to the normalized version.
#       Example: version_resolve_tag_name "v2.1.0" "ai_"  ->  "ai_v2.1.0"
#
#   version_resolve_tag_exists <repo_path> <tag_name>
#       True (0) if <tag_name> exists as a real tag ref in <repo_path>.
#
#   version_resolve_list_tags <repo_path> [<branch_prefix>]
#       Echoes, one per line, every tag in <repo_path> matching the
#       project's tag shape (via git_describe_tag_pattern), sorted
#       ascending with `sort -V`. Alias tags (e.g. "latest") are excluded.
#
#   version_resolve_resolve_tag <requested_version> <repo_path> [<branch_prefix>]
#       Normalizes, computes the expected tag name, and verifies it exists
#       in <repo_path>.  On success echoes the resolved tag name and
#       returns 0.  On failure, prints a fail-loud message naming the
#       requested version and the nearest available tags to stderr, and
#       returns 1.  Does not touch the repo's working tree or HEAD.
#
#   version_resolve_materialize <repo_path> <resolved_tag>
#       Materializes <resolved_tag>'s tree into a brand-new temporary
#       detached git worktree (never the repo's own working directory) and
#       echoes the worktree's absolute path on success.  The repo's
#       checked-out branch and HEAD are never touched.  On failure, any
#       partial worktree directory is removed before returning 1.
#
#   version_resolve_prune <repo_path> <worktree_path>
#       Tears down a worktree created by version_resolve_materialize.
#       Idempotent: a no-op (returns 0) when <worktree_path> does not
#       exist.  Never touches the repo's checked-out branch or HEAD.
#
#   version_resolve_run_with_clean_source \
#       <repo_path> <requested_version> <branch_prefix> <callback> [args...]
#       The single entry point that ties (a)-(d) together: normalizes and
#       resolves the requested version, materializes a clean detached
#       worktree for it, invokes <callback> with the worktree path as its
#       first argument (any [args...] follow), then guarantees the
#       worktree is pruned — on the callback's normal return, on a
#       callback-triggered `exit`, and on SIGINT/SIGTERM/uncaught-error
#       shell termination (via a chained EXIT trap; see "Cleanup
#       guarantee" below).  Returns the callback's exit status, or 1 if
#       resolution/materialization itself failed (in which case the
#       callback is never invoked).
#
#   version_resolve_forward_only_check <installed_version> <target_version>
#       True (0) when <target_version> is STRICTLY newer than
#       <installed_version> (semver-aware, via semver_gt). Intended for the
#       upgrade forward-only rule: refuse when target <= installed.
#
# Design notes
# ------------
# WHY A DETACHED WORKTREE (not "git checkout" / not archive extraction):
#   `git worktree add --detach` materializes a tag's tree into a brand new
#   directory without touching the source repo's HEAD, index, or checked-
#   out branch.  This is the same mechanism team/scripts/lib/worktree.sh
#   uses for task worktrees; it is reused here at the resolution layer
#   rather than duplicated.  Archive extraction (`git archive | tar -x`)
#   would also satisfy the "clean source" requirement but is not
#   implemented here: it loses the .git metadata some deposit steps (e.g.
#   the VERSION_DETAIL git-describe path in version_stamp.sh) may want to
#   read from the materialized tree, whereas a detached worktree keeps a
#   full, ordinary git checkout.  A caller with an archive-only constraint
#   can add that as an alternate branch later without touching this file's
#   public contract.
#
# WHY THIS DOES NOT REUSE team/scripts/lib/worktree.sh's task-keyed helpers:
#   worktree.sh's create_detached_worktree/teardown_task_worktree are keyed
#   by a kanban task_id and resolve their path via pgai_worktree_base
#   (which depends on PP_project_name / kanban.cfg).  install.sh and
#   upgrade.sh run for real operators with no kanban task context at all
#   (a bare "./install.sh v2.1.0" invocation on a laptop has no task_id).
#   This module intentionally uses its own plain pgai_mktemp_d-rooted
#   temp directory instead of a task-keyed path, so it works identically
#   inside and outside the kanban.
#
# Cleanup guarantee (trap chaining)
# ----------------------------------
# version_resolve_run_with_clean_source registers a cleanup command on the
# calling shell's EXIT trap around the callback invocation, then removes it
# again (restoring whatever EXIT trap existed before) once the explicit
# post-callback prune has run.  In bash, an uncaught SIGINT/SIGTERM (no
# handler registered for that signal) terminates the shell via its normal
# exit path, which still runs the EXIT trap — so a single EXIT trap covers
# "error and interrupt" without this library needing to install its own
# INT/TERM handlers (which would risk swallowing a caller's own signal
# handling). Any EXIT trap the caller already had installed (e.g.
# upgrade.sh's own `_cleanup`) is chained, not clobbered — see
# _version_resolve_chain_exit_trap.
#
# No-argument back-compat:
#   This module only acts when explicitly invoked with a requested
#   version. Scripts that never call these functions (the classic
#   no-version-argument path) are completely unaffected.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_VERSION_RESOLVE_SH_LOADED:-}" ]] && return 0
_PGAI_VERSION_RESOLVE_SH_LOADED=1

# Capture the directory of this file at source time so sibling libs can be
# located relative to this file without relying on BASH_SOURCE inside a
# function (matches the convention in worktree.sh / dev_tree.sh / temp.sh).
_VERSION_RESOLVE_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --- Ensure sibling libraries are loaded (each has its own include guard) ---

# temp.sh: pgai_mktemp_d for the clean-source worktree location.
if ! command -v pgai_mktemp_d >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "${_VERSION_RESOLVE_SH_DIR}/temp.sh"
fi

# semver.sh: re-exported so any script that sources version_resolve.sh gets
# semver_compare/semver_lt/semver_gt/etc for free (see AC5 / Goal item "e").
if ! command -v semver_compare >/dev/null 2>&1; then
    # shellcheck source=semver.sh
    source "${_VERSION_RESOLVE_SH_DIR}/semver.sh"
fi

# version_stamp.sh: git_describe_tag_pattern is the single existing
# definition of "what does this project's tag shape look like"; reused here
# rather than re-encoding the "<prefix>v[0-9]*" pattern a second time.
if ! command -v git_describe_tag_pattern >/dev/null 2>&1; then
    # shellcheck source=version_stamp.sh
    source "${_VERSION_RESOLVE_SH_DIR}/version_stamp.sh"
fi

# ---------------------------------------------------------------------------
# version_resolve_normalize <requested> [<branch_prefix>]
#
# Strips a leading <branch_prefix> from <requested> when <requested> starts
# with it.  <branch_prefix> defaults to "" (no-op) when omitted — callers
# that know their project's branch_prefix (e.g. via pp_branch_prefix) pass
# it explicitly.
#
# Examples:
#   version_resolve_normalize "ai_v2.1.0" "ai_"   -> "v2.1.0"
#   version_resolve_normalize "v2.1.0"    "ai_"   -> "v2.1.0"   (no-op: no prefix present)
#   version_resolve_normalize "ai_v2.1.0"          -> "ai_v2.1.0" (no-op: no prefix given)
# ---------------------------------------------------------------------------
version_resolve_normalize() {
    local requested="${1:-}"
    local branch_prefix="${2:-}"

    if [[ -n "$branch_prefix" && "$requested" == "${branch_prefix}"* ]]; then
        requested="${requested#"${branch_prefix}"}"
    fi

    printf '%s' "$requested"
}

# ---------------------------------------------------------------------------
# version_resolve_tag_name <normalized_version> [<branch_prefix>]
#
# Builds the tag name expected in the repo by re-applying <branch_prefix>
# to <normalized_version>. <branch_prefix> defaults to "" (bare tag name).
#
# Example:
#   version_resolve_tag_name "v2.1.0" "ai_"  -> "ai_v2.1.0"
#   version_resolve_tag_name "v2.1.0"        -> "v2.1.0"
# ---------------------------------------------------------------------------
version_resolve_tag_name() {
    local normalized_version="${1:-}"
    local branch_prefix="${2:-}"
    printf '%s' "${branch_prefix}${normalized_version}"
}

# ---------------------------------------------------------------------------
# version_resolve_tag_exists <repo_path> <tag_name>
#
# True (0) if <tag_name> exists as a real tag ref in <repo_path>.
# False (1) otherwise, including when <repo_path> is not a git repo.
# ---------------------------------------------------------------------------
version_resolve_tag_exists() {
    local repo_path="${1:-}"
    local tag_name="${2:-}"

    if [[ -z "$repo_path" || -z "$tag_name" ]]; then
        return 1
    fi

    git -C "$repo_path" rev-parse --verify --quiet "refs/tags/${tag_name}" >/dev/null 2>&1
}

# ---------------------------------------------------------------------------
# version_resolve_list_tags <repo_path> [<branch_prefix>]
#
# Echoes, one per line, every tag in <repo_path> matching the project's tag
# shape (git_describe_tag_pattern applied to <branch_prefix>), sorted
# ascending via `sort -V`.  Prints nothing (and returns 0) when there are no
# matching tags or <repo_path> is not a git repo.
# ---------------------------------------------------------------------------
version_resolve_list_tags() {
    local repo_path="${1:-}"
    local branch_prefix="${2:-}"

    if [[ -z "$repo_path" ]]; then
        return 0
    fi

    local pattern
    pattern="$(git_describe_tag_pattern "$branch_prefix")"

    git -C "$repo_path" tag -l "$pattern" 2>/dev/null | sort -V
}

# ---------------------------------------------------------------------------
# _version_resolve_nearest_tags <target_tag> <repo_path> [<branch_prefix>] [<window>]
#
# Internal helper. Echoes, one per line, up to <window> tags immediately
# below and <window> tags immediately above where <target_tag> WOULD sort
# among the repo's existing matching tags (via `sort -V` on the merged
# set). <target_tag> itself is never echoed (it does not exist in the repo
# — that is the reason this helper is being called). <window> defaults to 5.
#
# Used to build the "nearest available tags" fail-loud listing in
# version_resolve_resolve_tag.  When the repo has no matching tags at all,
# prints nothing.
# ---------------------------------------------------------------------------
_version_resolve_nearest_tags() {
    local target_tag="${1:-}"
    local repo_path="${2:-}"
    local branch_prefix="${3:-}"
    local window="${4:-5}"

    local existing
    existing="$(version_resolve_list_tags "$repo_path" "$branch_prefix")"

    if [[ -z "$existing" ]]; then
        return 0
    fi

    # Merge the (nonexistent) target into the sorted list so we can locate
    # its insertion point, then print a window of real tags around it.
    local merged
    merged="$(printf '%s\n%s\n' "$existing" "$target_tag" | sort -V)"

    local target_line
    target_line="$(printf '%s\n' "$merged" | grep -n -x -F -- "$target_tag" | head -n1 | cut -d: -f1)"

    if [[ -z "$target_line" ]]; then
        # Should not happen (we just inserted it), but degrade gracefully by
        # printing the whole matching tag set rather than nothing.
        printf '%s\n' "$existing"
        return 0
    fi

    local start=$(( target_line - window ))
    local end=$(( target_line + window ))
    (( start < 1 )) && start=1

    printf '%s\n' "$merged" | sed -n "${start},${end}p" | grep -v -x -F -- "$target_tag"
}

# ---------------------------------------------------------------------------
# version_resolve_resolve_tag <requested_version> <repo_path> [<branch_prefix>]
#
# Normalizes <requested_version>, computes the tag it must resolve to in
# <repo_path>, and verifies that tag exists.
#
# On success: echoes the resolved tag name to stdout, returns 0.
# On failure: prints a fail-loud message to stderr naming the requested
#   version and the tag that was looked for, lists the nearest available
#   tags (or notes that none exist), and returns 1. Nothing is echoed to
#   stdout on failure.
#
# Never touches <repo_path>'s working tree, index, or HEAD.
# ---------------------------------------------------------------------------
version_resolve_resolve_tag() {
    local requested_version="${1:-}"
    local repo_path="${2:-}"
    local branch_prefix="${3:-}"

    if [[ -z "$requested_version" ]]; then
        echo "version_resolve.sh: version_resolve_resolve_tag: requested_version argument is required" >&2
        return 1
    fi
    if [[ -z "$repo_path" ]]; then
        echo "version_resolve.sh: version_resolve_resolve_tag: repo_path argument is required" >&2
        return 1
    fi
    if [[ ! -d "$repo_path" ]] || ! git -C "$repo_path" rev-parse --git-dir >/dev/null 2>&1; then
        echo "version_resolve.sh: version_resolve_resolve_tag: '${repo_path}' is not a git repository" >&2
        return 1
    fi

    local normalized tag_name
    normalized="$(version_resolve_normalize "$requested_version" "$branch_prefix")"
    tag_name="$(version_resolve_tag_name "$normalized" "$branch_prefix")"

    if version_resolve_tag_exists "$repo_path" "$tag_name"; then
        printf '%s\n' "$tag_name"
        return 0
    fi

    echo "ERROR: requested version '${requested_version}' (resolved tag '${tag_name}') was not found in '${repo_path}'." >&2
    echo "" >&2

    local nearest
    nearest="$(_version_resolve_nearest_tags "$tag_name" "$repo_path" "$branch_prefix")"
    if [[ -n "$nearest" ]]; then
        echo "Nearest available tags:" >&2
        while IFS= read -r _line; do
            [[ -n "$_line" ]] && echo "  ${_line}" >&2
        done <<< "$nearest"
    else
        echo "No tags matching this project's tag shape were found in '${repo_path}'." >&2
    fi
    echo "" >&2
    echo "To fix: pass an existing released tag (see the list above), or omit the" >&2
    echo "version argument entirely to use today's default (unversioned) behavior." >&2

    return 1
}

# ---------------------------------------------------------------------------
# version_resolve_materialize <repo_path> <resolved_tag>
#
# Materializes <resolved_tag>'s tree into a brand-new temporary DETACHED git
# worktree (created via `git worktree add --detach`).  The worktree lives
# under pgai_temp_dir (never bare /tmp), distinct from <repo_path>'s own
# working directory.  <repo_path>'s checked-out branch, index, and HEAD are
# never touched by this call.
#
# On success: echoes the absolute worktree path to stdout, returns 0.
# On failure: removes any partial worktree directory, prints a diagnostic
#   to stderr, returns 1. Nothing is echoed to stdout on failure.
# ---------------------------------------------------------------------------
version_resolve_materialize() {
    local repo_path="${1:-}"
    local resolved_tag="${2:-}"

    if [[ -z "$repo_path" ]]; then
        echo "version_resolve.sh: version_resolve_materialize: repo_path argument is required" >&2
        return 1
    fi
    if [[ -z "$resolved_tag" ]]; then
        echo "version_resolve.sh: version_resolve_materialize: resolved_tag argument is required" >&2
        return 1
    fi
    if ! git -C "$repo_path" rev-parse --verify --quiet "refs/tags/${resolved_tag}" >/dev/null 2>&1; then
        echo "version_resolve.sh: version_resolve_materialize: tag '${resolved_tag}' does not exist in '${repo_path}'" >&2
        return 1
    fi

    local worktree_path
    worktree_path="$(pgai_mktemp_d "version_resolve_clean_source")" || {
        echo "version_resolve.sh: version_resolve_materialize: failed to create a temp directory" >&2
        return 1
    }

    # `git worktree add` refuses a pre-existing non-empty target directory;
    # pgai_mktemp_d already created an empty one, so remove it first and let
    # git create the final directory itself.
    rmdir "$worktree_path" 2>/dev/null || true

    if ! git -C "$repo_path" worktree add --detach "$worktree_path" "refs/tags/${resolved_tag}" >/dev/null 2>&1; then
        echo "version_resolve.sh: version_resolve_materialize: 'git worktree add --detach' failed for tag '${resolved_tag}' in '${repo_path}'" >&2
        [[ -d "$worktree_path" ]] && rm -rf "$worktree_path" 2>/dev/null
        git -C "$repo_path" worktree prune >/dev/null 2>&1 || true
        return 1
    fi

    printf '%s\n' "$worktree_path"
}

# ---------------------------------------------------------------------------
# version_resolve_prune <repo_path> <worktree_path>
#
# Tears down a worktree created by version_resolve_materialize.
# Idempotent: returns 0 without error when <worktree_path> does not exist
# (already removed, or never created).  Never touches <repo_path>'s
# checked-out branch, index, or HEAD — only the detached worktree itself.
# ---------------------------------------------------------------------------
version_resolve_prune() {
    local repo_path="${1:-}"
    local worktree_path="${2:-}"

    if [[ -z "$worktree_path" ]]; then
        echo "version_resolve.sh: version_resolve_prune: worktree_path argument is required" >&2
        return 1
    fi

    if [[ ! -d "$worktree_path" ]]; then
        # Already gone (or never created) — prune stale metadata and succeed.
        if [[ -n "$repo_path" ]]; then
            git -C "$repo_path" worktree prune >/dev/null 2>&1 || true
        fi
        return 0
    fi

    if [[ -z "$repo_path" ]]; then
        echo "version_resolve.sh: version_resolve_prune: repo_path argument is required when worktree_path exists" >&2
        return 1
    fi

    if ! git -C "$repo_path" worktree remove --force "$worktree_path" >/dev/null 2>&1; then
        echo "version_resolve.sh: version_resolve_prune: 'git worktree remove --force' failed for '${worktree_path}'; falling back to rm -rf" >&2
        rm -rf "$worktree_path" 2>/dev/null || true
    fi

    git -C "$repo_path" worktree prune >/dev/null 2>&1 || true

    if [[ -d "$worktree_path" ]]; then
        echo "version_resolve.sh: version_resolve_prune: ERROR: '${worktree_path}' still exists after removal attempt" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# _version_resolve_chain_exit_trap <command>
#
# Internal helper. Registers <command> on the CALLING shell's EXIT trap
# without clobbering whatever EXIT trap was already registered (e.g.
# upgrade.sh's own `_cleanup`).  The new command runs FIRST, then the prior
# trap (if any).
#
# Must be called directly (never via command substitution / a subshell) so
# the trap takes effect in the caller's actual shell.
# ---------------------------------------------------------------------------
_version_resolve_chain_exit_trap() {
    local new_cmd="${1:-}"
    [[ -z "$new_cmd" ]] && return 0

    local existing
    existing="$(trap -p EXIT)"

    if [[ -n "$existing" ]]; then
        # `trap -p EXIT` prints: trap -- '<cmd>' EXIT
        # Extract <cmd> so it can be chained after our new command.
        local existing_cmd
        existing_cmd="$(printf '%s' "$existing" | sed -E "s/^trap -- '(.*)' EXIT\$/\\1/")"
        trap "${new_cmd}; ${existing_cmd}" EXIT
    else
        trap "${new_cmd}" EXIT
    fi
}

# ---------------------------------------------------------------------------
# version_resolve_run_with_clean_source \
#     <repo_path> <requested_version> <branch_prefix> <callback> [args...]
#
# The single (a)-(d) entry point: normalize + resolve the requested version
# against <repo_path>'s tags, materialize a temporary detached worktree for
# the resolved tag, invoke <callback> with the worktree path as its first
# argument (followed by any [args...]), and guarantee the worktree is
# pruned on every exit path — the callback's normal return, a callback-
# triggered `exit`, or shell termination via an uncaught signal (see
# "Cleanup guarantee" in the file header).
#
# <callback> must be a shell function name already defined in the calling
# shell (this function does not fork a subshell to invoke it, so the
# callback runs with full access to the caller's variables and functions).
#
# Returns:
#   1   if resolution or materialization itself failed (callback never runs;
#       the fail-loud diagnostics from version_resolve_resolve_tag /
#       version_resolve_materialize are already on stderr).
#   The callback's own exit status otherwise.
#
# Usage:
#   my_deposit() {
#       local clean_src="$1"
#       cp -r "$clean_src"/* "$KANBAN_ROOT"/
#   }
#   version_resolve_run_with_clean_source \
#       "$DEV_TREE" "$REQUESTED_VERSION" "$BRANCH_PREFIX" my_deposit
# ---------------------------------------------------------------------------
version_resolve_run_with_clean_source() {
    local repo_path="${1:-}"
    local requested_version="${2:-}"
    local branch_prefix="${3:-}"
    local callback="${4:-}"
    shift 4 2>/dev/null || shift "$#"

    if [[ -z "$callback" ]]; then
        echo "version_resolve.sh: version_resolve_run_with_clean_source: callback argument is required" >&2
        return 1
    fi
    if ! declare -f "$callback" >/dev/null 2>&1; then
        echo "version_resolve.sh: version_resolve_run_with_clean_source: callback '${callback}' is not a defined shell function" >&2
        return 1
    fi

    local resolved_tag
    resolved_tag="$(version_resolve_resolve_tag "$requested_version" "$repo_path" "$branch_prefix")" || return 1

    local clean_src
    clean_src="$(version_resolve_materialize "$repo_path" "$resolved_tag")" || return 1

    # Preserve whatever EXIT trap already existed so it can be restored
    # verbatim once our own cleanup has run on the ordinary return path.
    local prior_trap
    prior_trap="$(trap -p EXIT)"

    _version_resolve_chain_exit_trap \
        "version_resolve_prune '${repo_path}' '${clean_src}' >/dev/null 2>&1 || true"

    local callback_status=0
    "$callback" "$clean_src" "$@" || callback_status=$?

    # Ordinary return path: prune explicitly now (idempotent even if the
    # chained EXIT trap also fires later) and restore the prior trap so this
    # function's cleanup does not linger for the rest of the caller's
    # process lifetime.
    version_resolve_prune "$repo_path" "$clean_src" >/dev/null 2>&1 || true
    if [[ -n "$prior_trap" ]]; then
        eval "$prior_trap"
    else
        trap - EXIT
    fi

    return "$callback_status"
}

# ---------------------------------------------------------------------------
# version_resolve_forward_only_check <installed_version> <target_version>
#
# True (0) when <target_version> is STRICTLY newer than <installed_version>
# (semver-aware comparison via semver_gt from semver.sh). False (1)
# otherwise — including when the two versions are equal, which the upgrade
# forward-only rule also refuses.
#
# This is the semver-aware comparison entry point the upgrade forward-only
# check consumes; it delegates to semver.sh rather than reimplementing
# version comparison (see semver.sh for the underlying `sort -V` mechanics).
#
# Example:
#   version_resolve_forward_only_check "v0.9.7" "v0.17.1"   # true (0)
#   version_resolve_forward_only_check "v0.17.1" "v0.9.7"   # false (1)
#   version_resolve_forward_only_check "v0.17.1" "v0.17.1"  # false (1) — equal is refused
# ---------------------------------------------------------------------------
version_resolve_forward_only_check() {
    local installed_version="${1:-}"
    local target_version="${2:-}"
    semver_gt "$target_version" "$installed_version"
}
