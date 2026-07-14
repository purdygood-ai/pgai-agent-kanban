#!/usr/bin/env bash
# team/workflows/testing-only/workflow.sh
#
# Testing-only workflow plugin for pgai-agent-kanban.
#
# This plugin runs a project's test suites in a detached read-only worktree at
# the requirement's named ref and writes a report artifact.  It is the first
# net-new workflow type added to the framework and serves as the litmus test
# that the plugin abstraction is complete: this plugin required zero edits to
# engine files (wake scripts, discovery, cm/*, dashboard/*, metrics).
#
# Capabilities
# ------------
# version_semantics = label
#   The requirement's version field is a label (a name for the artifact).  It
#   never enters release-state.md, the version ceiling, or the patch lane.
#   The intake filename keeps the frozen vX.Y.Z-slug.md public contract; the
#   version is a NAME only.
#
# git_mode = ro
#   A detached read-only worktree of the LOCAL dev tree at the ref named in
#   the requirement.  Working agents never fetch, pull, or push; CM remains
#   the sole origin-toucher.  Testing-only plugins have no CM step.
#
# agents = pm,tester
#   PM decomposes the requirement into test tickets.  TESTER executes them in
#   the read-only worktree and writes the report artifact.
#
# finalize = report
#   Finalization writes the test report to
#   projects/<name>/artifacts/v<label>-test-report-<slug>.md and exits.
#   No git tag, no publish to an artifact store.
#
# Dashboard rule (label semantics)
# ---------------------------------
# Label-version items render by their own status (open → running → done),
# NEVER by a version-vs-last-released semver comparison.  A testing-only
# requirement with a label that looks like "v0.5.0" on a project whose
# last_released is "v1.0.0" must still render as open, not as shipped.
# This is the fresh-project green-bug class: the shipped/green classification
# is a semver-semantics behavior that must not apply to label-versioned items.
# wf_dashboard_render returns "label" to declare this semantics to the engine.
#
# Hook implementations
# --------------------
# Each wf_* function does one thing; no function dispatches on an internal
# type switch.  The engine calls the hooks and acts on their outputs.
#
# wf_git_mode
#   Testing-only tasks use a read-only detached worktree.  Returns "ro".
#
# wf_resolve_target_version [label]
#   For the testing-only workflow, the target version is a label string
#   supplied by the engine from the requirements document.  Echo it back
#   unchanged — the engine has already read it from the requirement file.
#   When called with no argument, print an error and return non-zero.
#
# wf_pre_task [task_id] [source_branch]
#   Per-task setup for testing-only tasks.  The read-only detached worktree
#   is created by the engine for git_mode=ro tasks; this hook is a no-op
#   in the current implementation.  Exists so future testing-only extensions
#   can add pre-flight checks without engine changes.
#
# wf_post_task [task_id]
#   Per-task teardown.  Worktree removal is managed by the engine for
#   git_mode=ro tasks; this hook is a no-op in the current implementation.
#
# wf_finalize [label]
#   Testing-only finalizes by writing a report artifact.  Returns "report".
#   The actual report is written by TESTER to
#   projects/<name>/artifacts/v<label>-test-report-<slug>.md; this hook
#   declares the finalize mode so the engine routes to the report path.
#
# wf_agents
#   Returns the ordered agent roster for PM decomposition of a testing-only
#   requirement.  Testing-only uses: pm,tester
#
# wf_bundle_source_branch [target_label]
#   Testing-only requirements do not use RC branches.  There is no release
#   branch lifecycle.  Bundles target the main branch.  Returns: main
#
# wf_dashboard_render [context...]
#   Testing-only items render by their own status (open → running → done),
#   never by semver version-vs-last-released comparison.  Returns "label"
#   to signal to the dashboard that this item uses label semantics.

# ---------------------------------------------------------------------------
# wf_git_mode
#
# Testing-only tasks use a read-only detached worktree at the named ref.
# ---------------------------------------------------------------------------
wf_git_mode() {
    echo "ro"
}

# ---------------------------------------------------------------------------
# wf_resolve_target_version [label]
#
# For the testing-only workflow, the target version is a label string supplied
# by the engine from the requirements document.  Echo it back unchanged.
# ---------------------------------------------------------------------------
wf_resolve_target_version() {
    local label="${1:-}"
    if [[ -z "$label" ]]; then
        echo "testing-only plugin: wf_resolve_target_version requires a label argument" >&2
        return 1
    fi
    echo "$label"
}

# ---------------------------------------------------------------------------
# wf_pre_task [task_id] [source_branch]
#
# Per-task setup hook.  The engine handles read-only detached worktree
# creation for git_mode=ro tasks; this hook is a no-op in the current
# implementation.
# ---------------------------------------------------------------------------
wf_pre_task() {
    # No-op: the engine handles read-only worktree creation for git_mode=ro tasks.
    return 0
}

# ---------------------------------------------------------------------------
# wf_post_task [task_id]
#
# Per-task teardown hook.  The engine handles worktree removal for
# git_mode=ro tasks; this hook is a no-op in the current implementation.
# ---------------------------------------------------------------------------
wf_post_task() {
    # No-op: the engine handles worktree removal for git_mode=ro tasks.
    return 0
}

# ---------------------------------------------------------------------------
# wf_finalize [label]
#
# The testing-only workflow finalizes by writing a report artifact.  The
# actual report write is performed by TESTER; this hook declares the
# finalize mode so the engine routes to the report path (not tag or publish).
# ---------------------------------------------------------------------------
wf_finalize() {
    echo "report"
}

# ---------------------------------------------------------------------------
# wf_agents
#
# Returns the ordered agent roster for PM decomposition of a testing-only
# requirement.  No CM step: testing-only workflows never tag or push.
# ---------------------------------------------------------------------------
wf_agents() {
    echo "pm,tester"
}

# ---------------------------------------------------------------------------
# wf_bundle_source_branch [target_label]
#
# Testing-only requirements do not use RC branches.  Bundles for testing-only
# projects target the main branch, not an rc/<version> branch.
# ---------------------------------------------------------------------------
wf_bundle_source_branch() {
    echo "main"
}

# ---------------------------------------------------------------------------
# wf_dashboard_render [context...]
#
# Testing-only items render by their own status (open → running → done),
# never by a version-vs-last-released semver comparison.  Returns "label"
# to declare label semantics to the dashboard.
#
# This prevents the fresh-project green-bug class: a requirement with a
# label that resembles a semver string (e.g. "v0.5.0") must not be rendered
# as shipped (green) simply because the project's last_released exceeds the
# label value.  Label-versioned items are never part of the release lifecycle.
# ---------------------------------------------------------------------------
wf_dashboard_render() {
    echo "label"
}
