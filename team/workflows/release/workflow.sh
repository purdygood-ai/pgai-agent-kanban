#!/usr/bin/env bash
# team/workflows/release/workflow.sh
#
# Release workflow plugin for pgai-agent-kanban.
#
# This plugin captures the standard software-release behavior: semver
# versioning, read-write git, RC branch lifecycle, finalize by tagging.
# It is a verbatim port of the release-workflow behavior that was previously
# inline in the engine — all hooks reproduce the prior behavior exactly.
#
# Hook implementations
# --------------------
# Each wf_* function does one thing; no function dispatches on an internal
# type switch.  The engine calls the hooks and acts on their outputs.
#
# wf_git_mode
#   Release tasks require a read-write git worktree.  Returns "rw".
#
# wf_resolve_target_version [version]
#   The release workflow derives its target version from the requirements
#   document (semver).  When the engine supplies a version, echo it back
#   unchanged — the engine has already validated it is a semver string.
#   When called with no argument, print an error and return non-zero so
#   the engine catches a misconfigured call site.
#
# wf_pre_task [task_id] [source_branch]
#   Per-task setup for release tasks.  Worktree creation is managed by the
#   engine; this hook is a no-op in the current implementation.  Exists so
#   future releases can add per-task pre-flight checks without engine changes.
#
# wf_post_task [task_id]
#   Per-task teardown.  Worktree removal is managed by the engine; this hook
#   is a no-op in the current implementation.
#
# wf_finalize [version]
#   Release finalizes by tagging.  Returns "tag".  The actual git tag
#   operation is performed by CM; this hook declares the finalize mode.
#
# wf_agents
#   Returns the ordered agent roster for PM decomposition.
#   Release uses: pm,coder,writer,tester,cm
#
# wf_bundle_source_branch [target_version]
#   Bug and priority bundles for a release workflow target the RC branch
#   for the given version, not the main branch.
#   Returns: rc/<target_version>
#
# wf_dashboard_render [context...]
#   Release tasks render with git-column data (branch, commit, status).
#   Returns "git" to signal the dashboard to include git columns.
#   The engine interprets the empty string as the default non-git render;
#   "git" activates the git-column display rule.

# ---------------------------------------------------------------------------
# wf_git_mode
#
# Release tasks require a read-write git worktree.
# ---------------------------------------------------------------------------
wf_git_mode() {
    echo "rw"
}

# ---------------------------------------------------------------------------
# wf_resolve_target_version [version]
#
# For release workflow, the target version is supplied by the engine from
# the requirements document (semver).  Echo it back unchanged.
# ---------------------------------------------------------------------------
wf_resolve_target_version() {
    local version="${1:-}"
    if [[ -z "$version" ]]; then
        echo "release plugin: wf_resolve_target_version requires a version argument" >&2
        return 1
    fi
    echo "$version"
}

# ---------------------------------------------------------------------------
# wf_pre_task [task_id] [source_branch]
#
# Per-task setup hook.  Worktree creation is managed by the engine for
# the release workflow; this hook is a no-op in the current implementation.
# ---------------------------------------------------------------------------
wf_pre_task() {
    # No-op: the engine handles worktree creation for git_mode=rw tasks.
    return 0
}

# ---------------------------------------------------------------------------
# wf_post_task [task_id]
#
# Per-task teardown hook.  Worktree removal is managed by the engine for
# the release workflow; this hook is a no-op in the current implementation.
# ---------------------------------------------------------------------------
wf_post_task() {
    # No-op: the engine handles worktree removal for git_mode=rw tasks.
    return 0
}

# ---------------------------------------------------------------------------
# wf_finalize [version]
#
# The release workflow finalizes by tagging.  The actual git tag operation
# is performed by CM; this hook declares the finalize mode.
# ---------------------------------------------------------------------------
wf_finalize() {
    echo "tag"
}

# ---------------------------------------------------------------------------
# wf_agents
#
# Returns the ordered agent roster for PM decomposition of a release.
# ---------------------------------------------------------------------------
wf_agents() {
    echo "pm,coder,writer,tester,cm"
}

# ---------------------------------------------------------------------------
# wf_bundle_source_branch [target_version]
#
# Bug and priority bundles for a release workflow target the RC branch for
# the given version (rc/<target_version>), not main.
# ---------------------------------------------------------------------------
wf_bundle_source_branch() {
    local target_version="${1:-}"
    if [[ -z "$target_version" ]]; then
        echo "release plugin: wf_bundle_source_branch requires a target_version argument" >&2
        return 1
    fi
    echo "rc/${target_version}"
}

# ---------------------------------------------------------------------------
# wf_dashboard_render [context...]
#
# Release tasks render with git-column data.  Returns "git" to signal the
# dashboard to display git-aware columns (branch, commit, status).
# ---------------------------------------------------------------------------
wf_dashboard_render() {
    echo "git"
}
