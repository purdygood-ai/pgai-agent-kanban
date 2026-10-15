#!/usr/bin/env bash
# team/scripts/lib/worktree.sh
# Worktree lifecycle helpers for the pgai-agent-kanban wake-script layer.
#
# Provides:
#   pgai_worktree_base                               — resolve the worktree base dir (SOT)
#   pgai_worktree_path <task_id>                     — resolve the per-task worktree path
#   pgai_worktree_path_strictly_inside <path> <base> — canonicalised inside-base predicate
#   create_task_worktree <task_id> <rc_branch> <feature_branch> [dev_tree] [resume]
#   create_detached_worktree <task_id> <ref> [dev_tree]
#   teardown_task_worktree <task_id> [dev_tree]
#
# Source this file to get the functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/worktree.sh"
#
# This library depends on temp.sh for temp-root resolution and on
# error_contract.sh for the dual-channel refusal the delete guard speaks.
# Source both before sourcing this file, or let the include guard below
# source them automatically when their functions are not yet defined.
#
# Recursive-delete guard
# ----------------------
# The only recursive delete in this file is the orphaned-residue removal in
# create_task_worktree (a directory at the task's worktree path that git
# does not recognise as a worktree). It runs only when
# pgai_worktree_path_strictly_inside says the candidate path is a strict
# descendant of the worktree base — both paths canonicalised first
# (realpath: symlinks resolved, '.' and '..' collapsed), then compared —
# never on a string-prefix test of the unresolved text. A candidate of
# <base>/.., a symlink inside the base that resolves outside it, or the base
# itself all fail that predicate, and the refusal is dual-channel with
# nothing deleted.
#
# Worktree base-path resolution
# -----------------------------------------
# ALL worktrees are created under a single base directory resolved by the
# named function pgai_worktree_base().  Every internal call site — creation,
# prompt builder, teardown, logs — consumes pgai_worktree_base() rather than
# inlining any path.  This eliminates the class of divergence bugs where
# different callers compute different base paths.
#
# Resolution order for pgai_worktree_base (highest precedence first):
#   1. PGAI_WORKTREE_BASE env var — direct override (e.g. set by the operator
#      or a test harness).  This is a FULL-PATH override; the per-project
#      layout does not apply.
#   2. worktree_base key under [paths] in kanban.cfg — config-driven override.
#      This is also a FULL-PATH override.
#   3. Default: per-project subtree when PP_project_name is set:
#        $(pgai_temp_dir)/projects/<PP_project_name>/worktrees
#      PP_project_name is exported by pp_load_config (the existing PP project-
#      resolution path); the function reads it from the environment.
#   3b. Fallback when PP_project_name is unset: $(pgai_temp_dir)/worktrees
#       (non-project context, e.g. standalone test or operator invocation).
#
# Only the DEFAULT (Tier 3) becomes per-project.  Tiers 1 and 2 remain as
# explicit full-path overrides and are honored exactly as before.
#
# The per-task path is always:
#   $(pgai_worktree_base)/<task_id>
#
# BLOCKED-path feature-branch retention policy
# --------------------------------------------
# When a wake script encounters a BLOCKED task (e.g. git merge conflict,
# missing prerequisite, authentication failure) it should call
# teardown_task_worktree only AFTER the feature branch has been inspected
# and/or preserved in the main worktree for operator review.
#
# Recommended BLOCKED procedure:
#   1. Do NOT call teardown_task_worktree immediately on detecting the blocker.
#   2. Record the blocker in the task's status.md (state=BLOCKED, Needs Human=yes).
#   3. Leave the worktree in place so the operator can inspect partial work.
#   4. Optionally log the worktree path in status.md ## Artifacts so the
#      operator can locate it.
#   5. On the next wake cycle, if the task is still BLOCKED, the wake script
#      may call teardown_task_worktree to reclaim space — by then the operator
#      has had a chance to inspect.
#
# teardown_task_worktree does NOT delete the git feature branch. On a BLOCKED
# path the feature branch must be preserved on the main worktree so the operator
# can switch to it, inspect the state, and decide how to resolve the blocker.
# The feature branch is only deleted by CODER PHASE 2 (git branch -d) after a
# successful merge into the source branch.
#
# Safety invariants:
#   - create_task_worktree: idempotent — tears down any prior worktree dir and,
#     in the default fresh resume mode, the feature branch before (re)creating,
#     so a retry after a transient interruption succeeds rather than failing on
#     the pre-existing branch.
#   - create_task_worktree: in the branch resume mode an existing feature branch
#     is attached as-is (git worktree add <path> <branch>): it is never deleted,
#     reset or re-created, so every commit on it survives the re-dispatch.
#     'git branch -D' runs only in the fresh resume mode.
#   - create_task_worktree: the orphaned-residue rm -rf runs only after
#     pgai_worktree_path_strictly_inside accepts the canonicalised candidate
#     as a strict descendant of the canonicalised worktree base; a refusal
#     is dual-channel (worktree.residue.outside-base), deletes nothing, and
#     exits non-zero.
#   - create_task_worktree: exits non-zero + stderr on missing RC branch.
#   - create_task_worktree: exits non-zero + stderr if worktree path cannot be created.
#   - create_task_worktree: git stderr from 'worktree add' flows to caller's stderr
#     (not suppressed) so diagnostics appear in the wake log.
#   - teardown_task_worktree: idempotent (safe to call when worktree already gone).
#   - teardown_task_worktree: uses 'git worktree remove'; logs ERROR and returns 1
#     on failure (no silent rm -rf fallback). Logs "torn down" ONLY after verifying
#     the directory is gone and git registration pruned.
#   - teardown_task_worktree: never deletes the git feature branch.
#   - No top-level side effects when sourced (only function definitions +
#     include guard + one _WORKTREE_SH_DIR capture + conditional sourcing of
#     the two function-only sibling libs, temp.sh and error_contract.sh).
#
# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_WORKTREE_SH_LOADED:-}" ]] && return 0
_PGAI_WORKTREE_SH_LOADED=1

# Capture the directory of this file at source time so we can locate temp.sh
# relative to this library without relying on BASH_SOURCE inside a function.
_WORKTREE_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Ensure temp.sh is loaded (idempotent — temp.sh has its own include guard).
if ! command -v pgai_temp_dir >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "${_WORKTREE_SH_DIR}/temp.sh"
fi

# Ensure the dual-channel emitter is loaded (function-only file; the delete
# guard in create_task_worktree refuses through it).
if ! command -v pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "${_WORKTREE_SH_DIR}/error_contract.sh"
fi

# ---------------------------------------------------------------------------
# pgai_worktree_base
#
# Resolve and echo the base directory under which ALL task worktrees live.
# This is the single source of truth for the worktree base path.
# Every call site — creation, prompt builder, teardown, logs — must call
# this function rather than inlining any path.
#
# Resolution order (highest precedence first):
#   1. PGAI_WORKTREE_BASE env var — direct FULL-PATH override.
#   2. worktree_base key under [paths] in kanban.cfg — direct FULL-PATH override.
#   3. Default (per-project layout):
#        $(pgai_temp_dir)/projects/<PP_project_name>/worktrees
#      PP_project_name is resolved from the PP_project_name env var (exported
#      by pp_load_config via the existing PP project-resolution path).
#   3b. Fallback when PP_project_name is unset (non-project context):
#        $(pgai_temp_dir)/worktrees
#      This ensures standalone/test callers without a project context still work.
#
# Only Tier 3 is per-project. Tiers 1 and 2 remain as explicit full-path
# overrides — a caller that sets PGAI_WORKTREE_BASE controls the absolute path.
#
# The resolved directory is created (mkdir -p) before being echoed.
# Never returns empty or '/'.
#
# Usage:
#   base=$(pgai_worktree_base)
# ---------------------------------------------------------------------------
pgai_worktree_base() {
    local base

    # Tier 1: env-var override (explicit full-path; per-project layout does not apply).
    if [[ -n "${PGAI_WORKTREE_BASE:-}" ]]; then
        base="${PGAI_WORKTREE_BASE}"
    else
        # Tier 2: read worktree_base from kanban.cfg [paths] (explicit full-path override).
        local _cfg_dir="${PGAI_AGENT_KANBAN_ROOT_PATH:-}"
        local _cfg_val=""
        if [[ -n "$_cfg_dir" && -r "${_cfg_dir}/kanban.cfg" ]]; then
            if ! command -v read_ini >/dev/null 2>&1; then
                # shellcheck source=ini_parser.sh
                source "${_WORKTREE_SH_DIR}/ini_parser.sh"
            fi
            _cfg_val="$(read_ini "${_cfg_dir}/kanban.cfg" paths worktree_base "")"
        fi

        if [[ -n "$_cfg_val" ]]; then
            base="$_cfg_val"
        else
            # Tier 3: default — per-project subtree when PP_project_name is set.
            # PP_project_name is exported by pp_load_config (the PP project-resolution path).
            # With a project context the base is:
            #   $(pgai_temp_dir)/projects/<PP_project_name>/worktrees
            # Without a project context (PP_project_name unset) fall back to the
            # shared base so standalone callers and tests that do not load a project config
            # continue to work without error.
            if [[ -n "${PP_project_name:-}" ]]; then
                base="$(pgai_temp_dir)/projects/${PP_project_name}/worktrees"
            else
                base="$(pgai_temp_dir)/worktrees"
            fi
        fi
    fi

    # Safety invariant: resolver must never return empty or root '/'.
    if [[ -z "$base" || "$base" == "/" ]]; then
        base="$(pgai_temp_dir)/worktrees"
    fi

    mkdir -p "$base"
    printf '%s\n' "$base"
}

# ---------------------------------------------------------------------------
# pgai_worktree_path <task_id>
#
# Echo the absolute path for the given task's worktree:
#   $(pgai_worktree_base)/<task_id>
#
# This is a convenience wrapper so all call sites agree on the full path
# without re-implementing the base lookup and path join.
#
# Arguments:
#   task_id — the task identifier (used as the worktree directory name).
#
# Usage:
#   wt_path=$(pgai_worktree_path "$TASK_ID")
# ---------------------------------------------------------------------------
pgai_worktree_path() {
    local task_id="$1"
    if [[ -z "$task_id" ]]; then
        echo "worktree.sh: pgai_worktree_path: task_id argument is required" >&2
        return 1
    fi
    local base
    base="$(pgai_worktree_base)"
    printf '%s\n' "${base}/${task_id}"
}

# ---------------------------------------------------------------------------
# pgai_worktree_path_strictly_inside <candidate> <base>
#
# Canonicalise both paths — realpath -e: symlinks resolved, '.' and '..'
# collapsed, so both must exist — and succeed only when <candidate> is a
# strict descendant of <base>. The base itself, its parent, a sibling whose
# name merely starts with the base's name, and a symlink inside the base
# that resolves elsewhere all fail. Every recursive delete in this file is
# gated on this predicate applied to the canonical forms, never on a
# string-prefix comparison of the unresolved text (which <base>/.. passes).
#
# Returns 0 when strictly inside, 1 otherwise — including when either path
# cannot be canonicalised, or the base canonicalises to '/' or empty. Prints
# nothing.
#
# Usage:
#   if pgai_worktree_path_strictly_inside "$candidate" "$(pgai_worktree_base)"; then rm -rf -- "$candidate"; fi
# ---------------------------------------------------------------------------
pgai_worktree_path_strictly_inside() {
    local candidate="${1:-}"
    local base="${2:-}"
    [[ -n "$candidate" && -n "$base" ]] || return 1
    local canon_candidate canon_base
    canon_candidate="$(realpath -e -- "$candidate" 2>/dev/null)" || return 1
    canon_base="$(realpath -e -- "$base" 2>/dev/null)" || return 1
    [[ -n "$canon_base" && "$canon_base" != "/" ]] || return 1
    [[ "$canon_candidate" != "$canon_base" ]] || return 1
    [[ "$canon_candidate" == "${canon_base}/"* ]]
}

# ---------------------------------------------------------------------------
# _wt_clear_task_worktree_path <caller> <task_id> <worktree_path> <dev_tree> [prune]
#
# The pre-clean both create functions run before 'git worktree add', so a
# second create for the same task id succeeds instead of failing with
# "already exists": whatever sits at <worktree_path> (a worktree a previous
# attempt left, a retained TESTER worktree, or unregistered residue) is
# removed. With the literal [prune] argument (create_task_worktree, as it
# always has), 'git worktree prune' follows such a removal.
#
# Nothing is pruned without [prune], nor when the path was already clear:
# prune drops every stale registration in the repository, and one whose
# directory is gone may still hold another task's feature branch with
# commits found nowhere else -- the create that then fails is what hands it
# to OVERWATCH's worktree-residue triage, which checks for those commits
# first. A detached create clears only its own path's registration
# (create_detached_worktree's 'add --force').
#
# Removal is confined to the worktree base. Before anything is removed --
# by git or by rm -- the path's canonical form must be a strict descendant
# of the canonical worktree base (pgai_worktree_path_strictly_inside:
# symlinks resolved, '..' collapsed). A path that is the base itself, its
# parent, or a symlink leading out of it is never this function's to
# remove: the create is refused dual-channel with nothing deleted.
#
# Inside the base, 'git worktree remove --force' is tried first; a
# directory git does not know as a worktree (a .git stub left by an
# interrupted run) is removed directly. Failures there are non-fatal: a
# warning goes to stderr and the caller's 'git worktree add' surfaces the
# real error if the slate is still dirty. Every git call suppresses stdout
# so nothing contaminates the path the caller captures via $(...).
#
# <caller> names the create function in narration. Returns 0 when the path
# is clear (or was never there), 1 on the refusal.
# ---------------------------------------------------------------------------
_wt_clear_task_worktree_path() {
    local caller="$1" task_id="$2" worktree_path="$3" dev_tree="$4" prune="${5:-}"

    if [[ -e "$worktree_path" || -L "$worktree_path" ]]; then
        local _wt_base
        _wt_base="$(pgai_worktree_base)"
        if ! pgai_worktree_path_strictly_inside "$worktree_path" "$_wt_base"; then
            # BLOCK-WORTHY (docs/blocking-site-census.md): the only
            # alternative is removing something outside the directory this
            # function owns — a destructive path — so the create is refused
            # outright and nothing is removed.
            pgai_emit_error_contract \
                "worktree.residue.outside-base" \
                "worktree.sh: ${caller}: '${worktree_path}' (task '${task_id}') exists, and its canonical path does not resolve strictly inside the worktree base '${_wt_base}' (it is the base itself, outside it, or a symlink leading out of it); nothing was deleted and the worktree was not created." \
                "ls -ld -- '${worktree_path}' && realpath -e -- '${worktree_path}' '${_wt_base}'"
            # CENSUS-ANCHOR: worktree-residue-outside-base-01
            return 1
        fi
        echo "worktree.sh: ${caller}: worktree dir already exists at '${worktree_path}'; removing before re-create" >&2
        if ! git -C "$dev_tree" worktree remove --force "$worktree_path" >/dev/null 2>&1; then
            # Not a registered worktree: orphaned residue. Re-checked at the
            # delete itself so the recursive delete is never ungated.
            if pgai_worktree_path_strictly_inside "$worktree_path" "$_wt_base"; then
                echo "worktree.sh: ${caller}: '${worktree_path}' is not a registered worktree (orphaned residue); removing directly under worktree base '${_wt_base}'" >&2
                if rm -rf -- "$worktree_path" 2>/dev/null; then
                    echo "worktree.sh: ${caller}: orphaned residue removed: '${worktree_path}'" >&2
                else
                    echo "worktree.sh: ${caller}: WARNING: rm -rf failed for orphaned residue '${worktree_path}'; will attempt create anyway" >&2
                fi
            fi
        fi
        # Prune stale administrative metadata after removal.
        # Suppress stdout (git worktree prune can emit informational lines).
        if [[ "$prune" == "prune" ]]; then
            git -C "$dev_tree" worktree prune >/dev/null 2>&1 || true
        fi
    fi
    return 0
}

# ---------------------------------------------------------------------------
# create_task_worktree <task_id> <rc_branch> <feature_branch> [dev_tree] [resume]
#
# Create a git worktree for the given task at the path resolved by the
# single SOT resolver (pgai_worktree_base):
#   $(pgai_worktree_base)/<task_id>
#
# Resume modes (passed by the wake's worktree stage, wd_setup_task_worktree:
# the task's Block Record 'resume:' value, or for a task with no Block Record
# its ## Resume Mode section -- 'branch' after the wake's time budget stopped
# a dispatch, so the commits that dispatch made are kept -- else fresh):
#   fresh  (default; also an empty argument) — the worktree is checked out on
#          a new local <feature_branch> branched from the local <rc_branch>.
#          A pre-existing <feature_branch> is force-deleted (git branch -D)
#          first, discarding any commits on it.
#   branch — when <feature_branch> already exists locally, the worktree is
#          attached to it unchanged (git worktree add <path> <feature_branch>,
#          no -b, no branch -D), so HEAD is the branch's existing tip and every
#          commit on it is kept. When it does not exist, the worktree is
#          created fresh from <rc_branch> and one stderr line names the
#          missing branch; nothing is deleted.
# The mode is matched case-insensitively with surrounding whitespace
# ignored. Any other value is reported on stderr and handled as 'branch',
# the mode that never deletes a branch.
#
# All git / git-worktree calls use explicit '-C "$dev_tree"' so the operations
# target the canonical dev-tree repository regardless of the caller's CWD.
#
# On success:
#   - Echoes the absolute worktree path to stdout.
#   - Exits 0.
#
# On failure (non-zero exit + diagnostic message on stderr):
#   - dev_tree cannot be resolved (not passed and PGAI_DEV_TREE_PATH unset).
#   - RC branch does not exist locally.
#   - Worktree parent directory cannot be created.
#   - git worktree add fails for any reason.
#
# Arguments:
#   task_id        — unique task identifier (used as worktree directory name)
#   rc_branch      — local RC branch name (e.g. rc/v0.56.0); must exist locally
#   feature_branch — name of the task's feature branch: created in the worktree,
#                    or attached as-is in the branch resume mode when it exists
#                    (e.g. feature/CODER-20260608-052-worktree-lifecycle-helper)
#   dev_tree       — (optional) absolute path to the canonical dev-tree git repo.
#                    When omitted, falls back to $PGAI_DEV_TREE_PATH. One of the
#                    two must be non-empty; the function errors if neither is set.
#                    Pass "" to use the fallback while still passing resume.
#   resume         — (optional) 'fresh' (default) or 'branch'; see Resume modes.
#                    The wake passes 'branch' for a triage requeue whose Block
#                    Record says so and for a task its time budget stopped.
#
# Usage:
#   worktree_path=$(create_task_worktree "$TASK_ID" "rc/v0.56.0" "feature/$TASK_ID" "$DEV_TREE") || exit 1
#   # or, when PGAI_DEV_TREE_PATH is exported:
#   worktree_path=$(create_task_worktree "$TASK_ID" "rc/v0.56.0" "feature/$TASK_ID") || exit 1
#   # re-dispatch onto the commits already on the feature branch (a triage
#   # requeue with resume: branch, or a task the wake's time budget stopped):
#   worktree_path=$(create_task_worktree "$TASK_ID" "rc/v0.56.0" "feature/$TASK_ID" "$DEV_TREE" branch) || exit 1
# ---------------------------------------------------------------------------
create_task_worktree() {
    local task_id="$1"
    local rc_branch="$2"
    local feature_branch="$3"
    local dev_tree="${4:-${PGAI_DEV_TREE_PATH:-}}"
    local resume_arg="${5:-}"

    # --- Resume mode: liberal read, canonical value ---
    local resume_mode
    resume_mode="$(printf '%s' "$resume_arg" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')"
    case "$resume_mode" in
        ""|fresh) resume_mode="fresh" ;;
        branch) ;;
        *)
            echo "worktree.sh: create_task_worktree: unrecognised resume mode '${resume_arg}' (expected fresh or branch); keeping any existing feature branch '${feature_branch}' as the branch mode does" >&2
            resume_mode="branch"
            ;;
    esac

    # --- Argument validation ---
    if [[ -z "$task_id" ]]; then
        echo "worktree.sh: create_task_worktree: task_id argument is required" >&2
        return 1
    fi
    if [[ -z "$rc_branch" ]]; then
        echo "worktree.sh: create_task_worktree: rc_branch argument is required" >&2
        return 1
    fi
    if [[ -z "$feature_branch" ]]; then
        echo "worktree.sh: create_task_worktree: feature_branch argument is required" >&2
        return 1
    fi
    if [[ -z "$dev_tree" ]]; then
        echo "worktree.sh: create_task_worktree: dev_tree argument is required (pass as \$4 or set PGAI_DEV_TREE_PATH)" >&2
        return 1
    fi

    # --- Verify the RC branch exists locally in the canonical dev tree ---
    if ! git -C "$dev_tree" rev-parse --verify "refs/heads/${rc_branch}" >/dev/null 2>&1; then
        echo "worktree.sh: create_task_worktree: RC branch '${rc_branch}' does not exist locally in '${dev_tree}'. CM-open-rc may not have completed, or the branch name is wrong." >&2
        return 1
    fi

    # --- Resolve the worktree path via the single SOT resolver ---
    # pgai_worktree_path calls pgai_worktree_base (env > kanban.cfg > default)
    # and appends /<task_id>. pgai_worktree_base mkdir -p's the base for us.
    local worktree_path
    if ! worktree_path="$(pgai_worktree_path "$task_id")"; then
        echo "worktree.sh: create_task_worktree: pgai_worktree_path failed for task '${task_id}'" >&2
        return 1
    fi

    # --- Idempotent teardown before create ---
    # A prior attempt interrupted mid-run (e.g. by a transient 529) may have
    # left a worktree dir and/or feature branch on disk.  Tear them down before
    # (re)creating so a second invocation succeeds rather than failing because
    # 'git worktree add -b' refuses to create a branch that already exists.
    #
    # Order: clear the worktree path first (the branch may be checked out
    # there; _wt_clear_task_worktree_path below); then, in the fresh resume
    # mode only, delete the feature branch.  Mirrors the teardown pattern in
    # reset.sh Step 4 (pgai_reset_agent_task) and the TESTER teardown in
    # pgai_reset_tester_task. Removing the directory never loses a commit —
    # commits live on the branch, not in the directory — but it does discard
    # uncommitted changes left in it, in every resume mode.
    _wt_clear_task_worktree_path create_task_worktree "$task_id" "$worktree_path" "$dev_tree" prune || return 1
    # --- Feature branch: attach the existing one (branch mode) or start fresh ---
    # In the branch resume mode an existing feature branch carries work a
    # previous attempt committed; it is attached unchanged below and nothing
    # here deletes, resets or re-creates it. Only the fresh mode deletes.
    local attach_existing_branch=0
    local _fb_exists=0
    if git -C "$dev_tree" rev-parse --verify "refs/heads/${feature_branch}" >/dev/null 2>&1; then
        _fb_exists=1
    fi
    if [[ "$resume_mode" == "branch" && $_fb_exists -eq 1 ]]; then
        attach_existing_branch=1
        local _fb_tip
        _fb_tip="$(git -C "$dev_tree" rev-parse --short "refs/heads/${feature_branch}" 2>/dev/null || echo unknown)"
        echo "worktree.sh: create_task_worktree: resume mode branch: attaching existing feature branch '${feature_branch}' at ${_fb_tip}; the branch is not deleted or re-created" >&2
        # A worktree directory removed without 'git worktree remove' leaves its
        # registration behind, and git refuses to check the branch out again
        # while that stale entry still claims it. Pruning drops only entries
        # whose directory is gone; it never touches a branch or a commit.
        git -C "$dev_tree" worktree prune >/dev/null 2>&1 || true
    elif [[ "$resume_mode" == "branch" ]]; then
        echo "worktree.sh: create_task_worktree: resume mode branch: feature branch '${feature_branch}' does not exist in '${dev_tree}'; creating it fresh from '${rc_branch}' (nothing deleted)" >&2
    elif [[ $_fb_exists -eq 1 ]]; then
        echo "worktree.sh: create_task_worktree: feature branch '${feature_branch}' already exists; deleting before re-create" >&2
        # Use -D (force) because the branch may have been created but never merged —
        # soft -d would refuse it.  This is safe: we are about to recreate the branch
        # from scratch on the RC tip; any prior commits on it are from an interrupted
        # attempt and are intentionally discarded here.
        # Suppress stdout as well as stderr — git branch -D emits
        # "Deleted branch <name> (was <sha>)." to stdout, which the caller
        # captures via $(...) and prepends to the intended worktree path output.
        if ! git -C "$dev_tree" branch -D "$feature_branch" >/dev/null 2>&1; then
            echo "worktree.sh: create_task_worktree: WARNING: could not delete feature branch '${feature_branch}'; will attempt create anyway" >&2
        fi
    fi

    # --- Add the git worktree ---
    # Fresh: -b creates a new branch from rc_branch. Attach: no -b — the
    # existing branch is checked out at its current tip. Either way the
    # worktree is checked out at worktree_path.
    # Explicit -C "$dev_tree" ensures the operation targets the canonical repo,
    # not the caller's CWD.
    #
    # Suppress git's stdout ("Preparing worktree...") to prevent progress chatter
    # from contaminating the path value captured by the caller via $(...).
    # Stderr is intentionally NOT suppressed: git's diagnostic messages
    # (e.g. "fatal: branch already exists") flow to the caller's stderr/cron log
    # so failures are self-diagnosing. The caller captures only stdout, so git's
    # stderr does not contaminate _task_worktree_path.
    local -a _wt_add_args
    if [[ $attach_existing_branch -eq 1 ]]; then
        _wt_add_args=(worktree add "$worktree_path" "$feature_branch")
    else
        _wt_add_args=(worktree add -b "$feature_branch" "$worktree_path" "$rc_branch")
    fi
    if ! git -C "$dev_tree" "${_wt_add_args[@]}" >/dev/null; then
        echo "worktree.sh: create_task_worktree: git worktree add failed for task '${task_id}' (feature branch: '${feature_branch}', rc branch: '${rc_branch}', resume mode: '${resume_mode}', path: '${worktree_path}', repo: '${dev_tree}')" >&2
        # Clean up a partially created worktree path if it exists.
        if [[ -d "$worktree_path" ]]; then
            git -C "$dev_tree" worktree remove --force "$worktree_path" 2>/dev/null || true
        fi
        return 1
    fi

    # --- Print the worktree path to stdout for the caller to capture ---
    # Use printf instead of echo: printf is immune to word-splitting quirks when
    # the path contains leading dashes or special sequences.
    printf '%s\n' "$worktree_path"
}

# ---------------------------------------------------------------------------
# create_detached_worktree <task_id> <ref> [dev_tree]
#
# Create a git worktree in DETACHED HEAD mode for the given task at the
# path resolved by the single SOT resolver (pgai_worktree_base):
#   $(pgai_worktree_base)/<task_id>
#
# The worktree is checked out at <ref> without creating a new feature branch.
# This is used when a task needs read-only access to a specific commit, tag,
# or branch tip without advancing any branch pointer.
#
# Uses `git worktree add --detach` so no feature branch is created in the
# repository.  All git calls use explicit '-C "$dev_tree"'.
#
# On success:
#   - Echoes the absolute worktree path to stdout.
#   - Exits 0.
#   - HEAD in the worktree points at the commit resolved from <ref>.
#   - No new local branches are created.
#
# An existing worktree or registration at that path (a TESTER worktree kept
# after a BLOCKED run, a previous attempt's residue, or a registration whose
# directory is gone) is replaced, strictly inside the worktree base only --
# see _wt_clear_task_worktree_path.
#
# On failure (non-zero exit + diagnostic message on stderr):
#   - dev_tree cannot be resolved (not passed and PGAI_DEV_TREE_PATH unset).
#   - <ref> does not exist locally.
#   - Something exists at the worktree path whose canonical form is not
#     strictly inside the worktree base (refused; nothing deleted).
#   - Worktree parent directory cannot be created.
#   - git worktree add fails for any reason.
#
# Arguments:
#   task_id   — unique task identifier (used as worktree directory name)
#   ref       — any git ref that exists locally (branch, tag, or commit SHA).
#               The worktree HEAD is detached at the commit resolved from ref.
#   dev_tree  — (optional) absolute path to the canonical dev-tree git repo.
#               When omitted, falls back to $PGAI_DEV_TREE_PATH.
#
# Teardown: reuse teardown_task_worktree unchanged.
#
# Usage:
#   wt=$(create_detached_worktree "$TASK_ID" "rc/v0.59.0" "$DEV_TREE") || exit 1
#   # or, when PGAI_DEV_TREE_PATH is exported:
#   wt=$(create_detached_worktree "$TASK_ID" "rc/v0.59.0") || exit 1
# ---------------------------------------------------------------------------
create_detached_worktree() {
    local task_id="$1"
    local ref="$2"
    local dev_tree="${3:-${PGAI_DEV_TREE_PATH:-}}"

    # --- Argument validation ---
    if [[ -z "$task_id" ]]; then
        echo "worktree.sh: create_detached_worktree: task_id argument is required" >&2
        return 1
    fi
    if [[ -z "$ref" ]]; then
        echo "worktree.sh: create_detached_worktree: ref argument is required" >&2
        return 1
    fi
    if [[ -z "$dev_tree" ]]; then
        echo "worktree.sh: create_detached_worktree: dev_tree argument is required (pass as \$3 or set PGAI_DEV_TREE_PATH)" >&2
        return 1
    fi

    # --- Verify the ref exists locally in the canonical dev tree ---
    if ! git -C "$dev_tree" rev-parse --verify "${ref}" >/dev/null 2>&1; then
        echo "worktree.sh: create_detached_worktree: ref '${ref}' does not exist locally in '${dev_tree}'." >&2
        return 1
    fi

    # --- Resolve the worktree path via the single SOT resolver ---
    # pgai_worktree_path calls pgai_worktree_base (env > kanban.cfg > default)
    # and appends /<task_id>. pgai_worktree_base mkdir -p's the base for us.
    local worktree_path
    if ! worktree_path="$(pgai_worktree_path "$task_id")"; then
        echo "worktree.sh: create_detached_worktree: pgai_worktree_path failed for task '${task_id}'" >&2
        return 1
    fi

    # --- Idempotent clear before create ---
    # A TESTER worktree retained on a BLOCKED path, or one an interrupted
    # attempt left, sits at this same path when the task is dispatched again;
    # 'git worktree add' would refuse it with "already exists". Cleared
    # exactly as create_task_worktree does.
    _wt_clear_task_worktree_path create_detached_worktree "$task_id" "$worktree_path" "$dev_tree" || return 1

    # --- Add the git worktree in detached HEAD mode ---
    # --detach: do not create or check out a branch; HEAD is detached at <ref>.
    # --force: with --detach, overrides only git's refusal of a path that is
    # still registered as a worktree whose directory is gone -- this task's
    # own path, just cleared above. Other registrations are not pruned and no
    # branch is involved.
    # Explicit -C "$dev_tree" ensures the operation targets the canonical repo,
    # not the caller's CWD.
    #
    # Redirect BOTH stdout and stderr of git worktree add to /dev/null.
    # git emits progress messages ("Preparing worktree...") on stderr; suppressing
    # only stdout (>/dev/null) allows those messages to leak through to the caller's
    # stderr, which the caller captures via 2>&1 into _task_worktree_path, embedding
    # progress chatter in the dispatch prompt and wake-log lines.
    if ! git -C "$dev_tree" worktree add --force --detach "$worktree_path" "$ref" >/dev/null 2>/dev/null; then
        echo "worktree.sh: create_detached_worktree: git worktree add --detach failed for task '${task_id}' (ref: '${ref}', path: '${worktree_path}', repo: '${dev_tree}')" >&2
        # Clean up a partially created worktree path if it exists.
        if [[ -d "$worktree_path" ]]; then
            git -C "$dev_tree" worktree remove --force "$worktree_path" 2>/dev/null || true
        fi
        return 1
    fi

    # --- Print the worktree path to stdout for the caller to capture ---
    # Use printf instead of echo: printf is immune to word-splitting quirks when
    # the path contains leading dashes or special sequences.
    printf '%s\n' "$worktree_path"
}

# ---------------------------------------------------------------------------
# teardown_task_worktree <task_id> [dev_tree]
#
# Remove the git worktree for the given task.
#
# Path resolution: uses pgai_worktree_path() — the same SOT resolver used at
# creation time — so the removal always targets the correct directory.
#
# Removal: 'git worktree remove --force' is the primary and only mechanism.
# There is NO silent rm -rf fallback.  If removal fails, the function logs
# ERROR to stderr and returns 1.  The caller's log line "worktree torn down"
# appears ONLY after the function returns 0 and the directory is verified gone.
#
# All git / git-worktree calls use explicit '-C "$dev_tree"' so the operations
# target the canonical dev-tree repository regardless of the caller's CWD.
#
# This function is idempotent: if the worktree does not exist (already removed,
# or never created), it exits 0 without error.
#
# This function does NOT delete the git feature branch.  The feature branch
# must be preserved on the BLOCKED path (see BLOCKED-path policy above).
# On the success path, CODER PHASE 2 deletes the feature branch explicitly
# with 'git branch -d' after a successful merge.
#
# Arguments:
#   task_id   — the same task_id that was passed to create_task_worktree
#   dev_tree  — (optional) absolute path to the canonical dev-tree git repo.
#               When omitted, falls back to $PGAI_DEV_TREE_PATH. When neither
#               is set, the function attempts the operation without -C
#               and logs a warning to stderr.
#
# Usage:
#   teardown_task_worktree "$TASK_ID" "$DEV_TREE"
#   # or, when PGAI_DEV_TREE_PATH is exported:
#   teardown_task_worktree "$TASK_ID"
# ---------------------------------------------------------------------------
teardown_task_worktree() {
    local task_id="$1"
    local dev_tree="${2:-${PGAI_DEV_TREE_PATH:-}}"

    if [[ -z "$task_id" ]]; then
        echo "worktree.sh: teardown_task_worktree: task_id argument is required" >&2
        return 1
    fi

    if [[ -z "$dev_tree" ]]; then
        echo "worktree.sh: teardown_task_worktree: dev_tree not provided and PGAI_DEV_TREE_PATH unset; git operations may use wrong repository" >&2
        # Non-fatal: continue so teardown remains idempotent, but the caller
        # should always supply the dev_tree for correct CWD-independent behaviour.
    fi

    # --- Resolve the expected worktree path via the single SOT resolver ---
    # pgai_worktree_path uses pgai_worktree_base (env > kanban.cfg > default).
    local worktree_path
    if ! worktree_path="$(pgai_worktree_path "$task_id")"; then
        echo "worktree.sh: teardown_task_worktree: pgai_worktree_path failed for task '${task_id}'" >&2
        return 1
    fi

    # Helper: run git with explicit repo context when available, else bare git.
    _wt_git() {
        if [[ -n "$dev_tree" ]]; then
            git -C "$dev_tree" "$@"
        else
            git "$@"
        fi
    }

    # --- Idempotency: if the path does not exist, nothing to do ---
    if [[ ! -d "$worktree_path" ]]; then
        # Prune stale worktree metadata even when the directory is gone.
        _wt_git worktree prune 2>/dev/null || true
        return 0
    fi

    # --- Remove the worktree ---
    # Use 'git worktree remove' as the primary and only removal mechanism.
    # --force handles dirty or locked worktrees.
    # Do NOT use '|| true' here: failure must surface as ERROR, not be silenced.
    # Do NOT fall back to rm -rf: that would hide registration problems and
    # would succeed even when the worktree is still registered in .git/worktrees.
    if ! _wt_git worktree remove --force "$worktree_path" 2>/dev/null; then
        echo "worktree.sh: teardown_task_worktree: ERROR: 'git worktree remove --force' failed for path '${worktree_path}' (task: '${task_id}'). The worktree may still be registered in .git/worktrees. Manual cleanup required." >&2
        return 1
    fi

    # --- Prune stale worktree administrative files ---
    _wt_git worktree prune 2>/dev/null || true

    # --- Verify removal: assert directory is gone and registration pruned ---
    # Log "torn down" ONLY after verification passes.
    if [[ -d "$worktree_path" ]]; then
        echo "worktree.sh: teardown_task_worktree: ERROR: 'git worktree remove' reported success but '${worktree_path}' still exists on disk. Manual cleanup required." >&2
        return 1
    fi

    return 0
}
