#!/usr/bin/env bash
# team/workflows/document/workflow.sh
#
# Document workflow plugin for pgai-agent-kanban.
#
# This plugin captures the document production behavior: no git repo, no RC
# branch lifecycle, CM manages a versioned artifact lifecycle by opening a
# working directory and finalizing by publishing the polished document to
# projects/<name>/artifacts/.  It is a verbatim port of the document-workflow
# behavior that was previously inline in the engine — all hooks reproduce the
# prior behavior exactly.
#
# Hook implementations
# --------------------
# Each wf_* function does one thing; no function dispatches on an internal
# type switch.  The engine calls the hooks and acts on their outputs.
#
# wf_git_mode
#   Document tasks have no git worktree.  Returns "none".
#
# wf_resolve_target_version [version]
#   The document workflow derives its target version from the requirements
#   document.  When the engine supplies a version, echo it back unchanged.
#   When called with no argument, print an error and return non-zero so
#   the engine catches a misconfigured call site.
#
# wf_pre_task [task_id] [source_branch]
#   Per-task setup for document tasks.  No worktree is created; this hook
#   is a no-op in the current implementation.
#
# wf_post_task [task_id]
#   Per-task teardown.  No worktree to remove; this hook is a no-op in the
#   current implementation.
#
# wf_finalize [version]
#   Document finalizes by publishing.  Returns "publish".  The actual
#   publish operation is performed by CM (cm-finalize.sh), which copies
#   the polished document to projects/<name>/artifacts/; this hook declares
#   the finalize mode.
#
# wf_agents
#   Returns the ordered agent roster for PM decomposition.
#   Document uses: pm,writer,tester,cm
#
# wf_bundle_source_branch [target_version]
#   Document workflow does not use RC branches; bundles target the main
#   branch.  Returns: main
#
# wf_dashboard_render [context...]
#   Document tasks have no git-column data (no branch, no commit, no RC
#   state).  Returns the empty string, which signals the dashboard to use
#   the default non-git render.

# ---------------------------------------------------------------------------
# wf_git_mode
#
# Document tasks have no git worktree.
# ---------------------------------------------------------------------------
wf_git_mode() {
    echo "none"
}

# ---------------------------------------------------------------------------
# wf_resolve_target_version [version]
#
# For document workflow, the target version is supplied by the engine from
# the requirements document.  Echo it back unchanged.
# ---------------------------------------------------------------------------
wf_resolve_target_version() {
    local version="${1:-}"
    if [[ -z "$version" ]]; then
        echo "document plugin: wf_resolve_target_version requires a version argument" >&2
        return 1
    fi
    echo "$version"
}

# ---------------------------------------------------------------------------
# wf_pre_task [task_id] [source_branch]
#
# Per-task setup hook.  Document tasks have no worktree; this hook is a
# no-op in the current implementation.
# ---------------------------------------------------------------------------
wf_pre_task() {
    # No-op: document tasks have no git worktree to create.
    return 0
}

# ---------------------------------------------------------------------------
# wf_post_task [task_id]
#
# Per-task teardown hook.  Document tasks have no worktree; this hook is a
# no-op in the current implementation.
# ---------------------------------------------------------------------------
wf_post_task() {
    # No-op: document tasks have no git worktree to remove.
    return 0
}

# ---------------------------------------------------------------------------
# wf_finalize [version]
#
# The document workflow finalizes by publishing.  The actual publish
# operation is performed by CM (cm-finalize.sh); this hook declares the
# finalize mode.
# ---------------------------------------------------------------------------
wf_finalize() {
    echo "publish"
}

# ---------------------------------------------------------------------------
# wf_agents
#
# Returns the ordered agent roster for PM decomposition of a document.
# ---------------------------------------------------------------------------
wf_agents() {
    echo "pm,writer,tester,cm"
}

# ---------------------------------------------------------------------------
# wf_bundle_source_branch [target_version]
#
# Document workflow does not use RC branches.  Bundles for document
# projects target the main branch, not an rc/<version> branch.
# ---------------------------------------------------------------------------
wf_bundle_source_branch() {
    echo "main"
}

# ---------------------------------------------------------------------------
# wf_dashboard_render [context...]
#
# Document tasks have no git-column data.  Returns the empty string so the
# dashboard uses the default non-git render (no branch, commit, or RC
# status columns).
# ---------------------------------------------------------------------------
wf_dashboard_render() {
    echo ""
}
