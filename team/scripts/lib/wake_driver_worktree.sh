#!/usr/bin/env bash
# team/scripts/lib/wake_driver_worktree.sh
# Worktree/branch lifecycle stage of the shared wake pipeline: computes the
# prefixed feature branch and creates the isolated git worktree for
# CODER/WRITER git-workflow tasks, or the detached worktree for TESTER
# release-workflow tasks. Extracted from team/scripts/wake/claude.sh's
# process_one_task so the shared pipeline in wake_driver.sh stays under the
# 800-line-per-file ceiling.
# Behavior-preserving move: no log line, task-state write, worktree path, or
# control-flow outcome changes as a result of this file existing.
#
# Public entry point
# -------------------
#   wd_setup_task_worktree
#       No-op for any SUBAGENT other than coder/writer/tester. For
#       coder/writer: computes the prefixed feature branch, loads the task's
#       workflow plugin, and (when the plugin's git_mode != none) parks the
#       canonical dev tree and creates an isolated worktree on the feature
#       branch, appending worktree guidance to role_reminder. The task's
#       Block Record 'resume:' field picks how the feature branch is set up:
#       'branch' attaches the worktree to an existing feature branch and its
#       commits unchanged; 'fresh' creates the branch anew from the source
#       branch (create_task_worktree, worktree.sh). A task with no Block
#       Record resumes in the mode its ## Resume Mode section names -- the
#       wake writes 'branch' there when its time budget stops a dispatch
#       (requeue_after_budget_stop, wake_task_budget.sh) -- and starts fresh
#       when it has no such section either. For tester:
#       loads the workflow plugin and (when git_mode != none) parks the
#       canonical dev tree and creates a detached worktree at the RC branch
#       tip, exporting PGAI_DEV_TREE_PATH to it. Always returns 0 — matching
#       the pre-decomposition monolith's own bare `return 0` at each
#       early-exit point verbatim (a test extracts and runs some of these
#       lines in isolation, so the return code is part of the preserved
#       text, not a stage-boundary signal). The caller distinguishes
#       "already terminal (BLOCKED)" from "proceed" by checking whether
#       LAST_TASK_FINAL_STATE is non-empty after the call: every early-exit
#       path here sets it to "BLOCKED" before returning; the proceed path
#       leaves it untouched (still "" from process_one_task's own reset).
#
# Contract: this function is called from within process_one_task's own
# stack frame (never in a subshell) and relies on bash's dynamic scoping to
# read and write process_one_task's own `local` variables directly, exactly
# as the pre-decomposition monolith did when this code was inline. It reads:
#   task_id task_readme task_status SUBAGENT _CURRENT_PROJECT TEAM_ROOT
# and writes (process_one_task-level locals the caller must have declared
# before calling this function, all defaulting to ""):
#   _task_feature_branch _task_worktree_path _task_workflow_type
#   _task_source_branch _canonical_dev_tree_path _park_resting_branch
# and appends to (module-level global, matching pre-decomposition behavior
# where this was assigned without `local` inside process_one_task):
#   role_reminder
# LAST_TASK_FINAL_STATE is the shared cross-task-result global already
# established by process_one_task itself. Every path that marks the task
# BLOCKED also sets _chain_blocked_a_task=true -- run_project_chain's local,
# again through dynamic scoping -- so the chain fires the on-BLOCK OVERWATCH
# nudge after releasing the repo lock, exactly as it does for a task the
# agent itself blocked (wd_finalize_task).
#
# Dependencies: pp_prefix_branch (project_paths.sh), _update_readme_feature_branch
# (wake_project.sh), get_workflow_type_from_readme / get_source_branch_from_readme
# (wake_driver_task_setup.sh), wf_load_plugin / wf_git_mode (workflow.sh),
# create_task_worktree / create_detached_worktree (worktree.sh), set_state /
# set_block / normalize_status_file / mark_backlog (wake_task_state.sh),
# log() (wake_bootstrap.sh), block_record_write, block_record_field and the
# evidence helpers (block_record.sh, sourced by wake_bootstrap.sh), pp_run_ops
# (pp_run_ops.sh; reads the ## Resume Mode section) — all sourced before this
# file's function is called.
#
# Every BLOCKED exit here also writes the task's ## Block Record with the
# reason code naming its cause: branch_prefix_failed,
# workflow_type_unparseable, plugin_load_failed, source_branch_missing (no
# '## Source Branch' in the README, or a worktree create that failed because
# that branch is absent from the dev tree) or worktree_create_failed (any
# other create failure). The State, Blockers, Needs Human and queue-marker
# writes are unchanged; a refused record write is logged and never stops the
# block.
#
# Design decision: should a task worktree ever check out the RC branch
# itself, rather than a branch created for the task? No, by construction,
# for both roles this function handles. CODER/WRITER worktrees are created
# via create_task_worktree with `git worktree add -b <feature_branch> ...
# <rc_branch>`, or `git worktree add <path> <feature_branch>` when the resume
# mode is branch and the feature branch exists — the new worktree's checked-out
# branch is always the task's feature branch, never the RC branch, so the RC is never held
# exclusively by a task worktree at creation time. TESTER worktrees are
# created via create_detached_worktree with `--detach`, so they check out
# the RC branch's *commit* without checking out the *branch* — the same
# reason the wake script has always detached TESTER, and the reason the
# exclusivity conflict is structurally impossible for a TESTER worktree
# regardless of teardown timing. The one place a task worktree legitimately
# holds the RC branch checked out is transient and expected: CODER/WRITER's
# own PHASE 2 (`team/roles/CODER.md`) runs `git checkout <rc-branch> && git
# merge --no-ff <feature-branch>` INSIDE this worktree to perform the
# sanctioned merge, which necessarily switches this one worktree onto the RC
# branch for the rest of that task's session. That state does not outlive
# the task: wd_finalize_task's worktree-teardown step (wake_driver_finalize.sh)
# removes the worktree on every terminal state reached through the normal
# finalize path, and reconcile_terminal_worktrees (wake_reconcile_sweeps.sh) is
# the companion sweep that reclaims it on the rarer path where finalize
# itself was skipped by a lock-loss reverification (see that function's own
# header). Detaching every task worktree by default (mirroring TESTER) was
# considered and rejected: it would require the sanctioned `git merge
# --no-ff` itself to run somewhere other than a checked-out branch, which is
# not how git merge works — the branch must be checked out to merge into
# it. Teardown timing, not never-checking-out-the-RC, is therefore the
# correct fix.

wd_setup_task_worktree() {
  if [[ "$SUBAGENT" == "coder" || "$SUBAGENT" == "writer" ]]; then
    local _fb_exit=0
    set +e
    _task_feature_branch="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "feature/${task_id}")"
    _fb_exit=$?
    set -e
    if [[ $_fb_exit -ne 0 || -z "$_task_feature_branch" ]]; then
      log "task ${task_id}: ERROR: pp_prefix_branch failed (exit ${_fb_exit}); cannot compute feature branch name"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script failed to compute prefixed feature branch name (pp_prefix_branch exit ${_fb_exit}). Check project.cfg branch_prefix config."
      set_block "$task_status" "Needs Human" "yes"
      block_record_write "$task_status" branch_prefix_failed "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
        "$(block_record_evidence --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      _chain_blocked_a_task=true
      return 0
    fi
    _update_readme_feature_branch "$task_readme" "$_task_feature_branch"
    log "task ${task_id}: feature branch: ${_task_feature_branch}"

    # --- Worktree lifecycle: CODER/WRITER git-workflow tasks ---
    # Detect workflow type and query the plugin for git mode to decide whether
    # a git worktree is required.  Fail-closed: a missing or broken plugin blocks
    # the task rather than silently defaulting to release behavior.
    local _wt_exit=0
    set +e
    _task_workflow_type="$(get_workflow_type_from_readme "$task_readme")"
    _wt_exit=$?
    set -e
    if [[ $_wt_exit -ne 0 || -z "$_task_workflow_type" ]]; then
      log "task ${task_id}: ERROR: cannot parse '## Workflow Type' from ${task_readme}; blocking task (fail-closed)"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script could not parse '## Workflow Type' from ${task_readme}. Ensure the field is present and non-empty."
      set_block "$task_status" "Needs Human" "yes"
      block_record_write "$task_status" workflow_type_unparseable "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
        "$(block_record_evidence --branch "$_task_feature_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      _chain_blocked_a_task=true
      return 0
    fi

    # Load the workflow plugin to query git mode via wf_git_mode.
    # Plugins with git_mode != "none" require an isolated git worktree.
    # Fail-closed: unknown type, missing/invalid manifest, or status=scaffold
    # routes the task to BLOCKED — never silently defaults to release behavior.
    local _wf_load_exit=0 _wf_git_mode="none"
    set +e
    wf_load_plugin "$_task_workflow_type"
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -ne 0 ]]; then
      log "task ${task_id}: ERROR: workflow plugin '${_task_workflow_type}' failed to load: ${WF_LOAD_ERROR}"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Workflow plugin for type '${_task_workflow_type}' could not be loaded: ${WF_LOAD_ERROR}. Ensure team/workflows/${_task_workflow_type}/ has a valid workflow.cfg (status=ready) and workflow.sh."
      set_block "$task_status" "Needs Human" "yes"
      block_record_write "$task_status" plugin_load_failed "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
        "$(block_record_evidence --branch "$_task_feature_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      _chain_blocked_a_task=true
      return 0
    fi
    _wf_git_mode="$(wf_git_mode)"

    if [[ "$_wf_git_mode" != "none" ]]; then
      # Git-workflow task: create an isolated worktree and park the canonical tree.

      # Extract the source (RC) branch from the task README.
      local _sb_exit=0
      _task_source_branch=""
      set +e
      _task_source_branch="$(get_source_branch_from_readme "$task_readme")"
      _sb_exit=$?
      set -e
      if [[ $_sb_exit -ne 0 || -z "$_task_source_branch" ]]; then
        log "task ${task_id}: ERROR: cannot extract Source Branch from README; cannot create worktree"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script could not read '## Source Branch' from ${task_readme}. Ensure the field is present and non-empty."
        set_block "$task_status" "Needs Human" "yes"
        block_record_write "$task_status" source_branch_missing "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
          "$(block_record_evidence --branch "$_task_feature_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _chain_blocked_a_task=true
        return 0
      fi
      log "task ${task_id}: source branch: ${_task_source_branch}"

      # Park the canonical dev tree off the RC branch so git will not refuse
      # to create a worktree that checks out the same branch.
      # Use the project's resting branch (prefix+main) via pp_prefix_branch,
      # falling back to --detach when the branch does not exist locally.  The
      # resting branch is recorded in _park_resting_branch and restored after
      # the worktree is torn down.
      #
      # Sandbox guard: skip parking when the kanban root (TEAM_ROOT) is under a
      # temp directory. Integration tests build a synthetic kanban root under /tmp
      # (pytest tmp_path) but point dev_tree_path at the real dev tree. Without
      # this guard the parking step mutates the real dev tree, breaking subsequent
      # tests. Real operator installs sit at $HOME/pgai_agent_kanban and are never
      # under /tmp, so production parking is unaffected.
      # Resolve per-project dev tree first; fall back to global for
      # single-project / legacy installs where PP_dev_tree_path is unset.
      local _dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
      local _sys_tmp="${TMPDIR:-/tmp}"
      local _kanban_root_is_sandbox=0
      # Resolve the actual temp dir (strip trailing slash for a clean prefix match)
      _sys_tmp="${_sys_tmp%/}"
      if [[ -n "$TEAM_ROOT" && ( "$TEAM_ROOT" == /tmp/* || "$TEAM_ROOT" == /tmp || "$TEAM_ROOT" == "${_sys_tmp}/"* || "$TEAM_ROOT" == "$_sys_tmp" ) ]]; then
        _kanban_root_is_sandbox=1
      fi
      if [[ -n "$_dev_tree" && -d "$_dev_tree/.git" ]]; then
        if [[ $_kanban_root_is_sandbox -eq 1 ]]; then
          log "task ${task_id}: skipping dev-tree parking: kanban root (${TEAM_ROOT}) is a temp/sandbox tree — will not mutate ${_dev_tree}"
        else
          # Compute prefix-aware resting branch via pp_prefix_branch.
          local _pb_exit=0
          local _resting_main=""
          set +e
          _resting_main="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "main" 2>/dev/null)"
          _pb_exit=$?
          set -e
          if [[ $_pb_exit -ne 0 || -z "$_resting_main" ]]; then
            _resting_main="main"   # safe fallback when helper fails
          fi

          local _park_exit=0
          set +e
          git -C "$_dev_tree" checkout "$_resting_main" 2>/dev/null
          _park_exit=$?
          set -e
          if [[ $_park_exit -eq 0 ]]; then
            _park_resting_branch="$_resting_main"
          else
            # Resting branch does not exist locally; detach HEAD so the branch is free.
            set +e
            git -C "$_dev_tree" checkout --detach 2>/dev/null
            _park_exit=$?
            set -e
          fi
          if [[ $_park_exit -ne 0 ]]; then
            log "task ${task_id}: WARNING: could not park canonical dev tree at ${_dev_tree}; worktree creation may fail if RC branch is currently checked out there"
          else
            log "task ${task_id}: canonical dev tree parked on ${_park_resting_branch:-<detached>} (was: ${_task_source_branch})"
          fi
        fi
      fi

      # Resume mode from the task's Block Record 'resume:' field. 'branch'
      # attaches the worktree to the existing feature branch so the commits a
      # previous attempt made are kept; 'fresh' creates the branch anew from
      # the source branch, deleting a stale one first. A record that cannot
      # be read is treated as 'branch', the mode that never deletes a branch;
      # the reader's own dual-channel diagnostic flows to stderr.
      #
      # A task with no Block Record reads the wake-owned ## Resume Mode
      # section instead: a budget stop (requeue_after_budget_stop) writes
      # 'branch' there, so a task the wake's own clock stopped keeps its
      # commits. No section, or any body other than 'branch' (first word,
      # any case), is 'fresh' -- the answer for a task that never ran. A
      # status.md the section reader cannot read is 'branch'.
      local _resume_mode="fresh" _rm_exit=0
      set +e
      _resume_mode="$(block_record_field "$task_status" resume none)"
      _rm_exit=$?
      set -e
      if [[ $_rm_exit -ne 0 || -z "$_resume_mode" ]]; then
        log "task ${task_id}: WARNING: could not read the Block Record resume mode (exit ${_rm_exit}); resuming in branch mode so an existing feature branch is kept"
        _resume_mode="branch"
      elif [[ "$_resume_mode" == "branch" ]]; then
        log "task ${task_id}: Block Record resume mode: branch; an existing feature branch is attached unchanged"
      elif [[ "$_resume_mode" == "none" ]]; then
        local _rs_body="" _rs_rest="" _rs_exit=0
        set +e
        _rs_body="$(pp_run_ops pgai_agent_kanban.task_status read "$task_status" --section="Resume Mode" --raw)"
        _rs_exit=$?
        set -e
        read -r _rs_body _rs_rest <<< "${_rs_body,,}" || true
        if [[ $_rs_exit -eq 0 && "$_rs_body" == "branch" ]]; then
          log "task ${task_id}: ## Resume Mode: branch (the wake's time budget stopped an earlier dispatch); an existing feature branch is attached unchanged"
          _resume_mode="branch"
        elif [[ $_rs_exit -ne 0 && $_rs_exit -ne 3 ]]; then
          log "task ${task_id}: WARNING: could not read the ## Resume Mode section (exit ${_rs_exit}); resuming in branch mode so an existing feature branch is kept"
          _resume_mode="branch"
        else
          _resume_mode="fresh"
        fi
      fi

      # Create the isolated worktree for this task.
      # Capture ONLY stdout (the resolved worktree path). Do NOT use 2>&1
      # here — that merges stderr into the captured value, allowing git
      # progress messages ("Preparing worktree...") to contaminate the path
      # variable and the dispatch prompt. Stderr from create_task_worktree
      # (error diagnostics) flows to the wake-script stderr/cron log directly.
      # The git worktree add inside the helper suppresses git's own progress
      # messages at the source (>/dev/null 2>/dev/null).
      local _ct_exit=0
      set +e
      _task_worktree_path="$(create_task_worktree "$task_id" "$_task_source_branch" "$_task_feature_branch" "$_dev_tree" "$_resume_mode")"
      _ct_exit=$?
      set -e
      if [[ $_ct_exit -ne 0 || -z "$_task_worktree_path" ]]; then
        log "task ${task_id}: ERROR: create_task_worktree failed (exit ${_ct_exit}); see stderr for diagnostic"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script failed to create git worktree for task (create_task_worktree exit ${_ct_exit}). Check stderr/cron log for diagnostic. Verify that RC branch '${_task_source_branch}' exists locally and the temp root is writable."
        set_block "$task_status" "Needs Human" "yes"
        block_record_write "$task_status" "$(block_record_worktree_failure_code "$_dev_tree" "$_task_source_branch")" "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
          "$(block_record_evidence --repo "$_dev_tree" --branch "$_task_feature_branch" --source "$_task_source_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _chain_blocked_a_task=true
        _task_worktree_path=""   # ensure teardown guard is clear
        # Restore canonical dev tree HEAD to resting branch (early-exit path).
        if [[ -n "${_park_resting_branch:-}" && -n "${_dev_tree:-}" && -d "${_dev_tree}/.git" && $_kanban_root_is_sandbox -eq 0 ]]; then
          set +e
          git -C "$_dev_tree" checkout "$_park_resting_branch" 2>/dev/null
          set -e
          log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (worktree-create-fail early-exit)"
        fi
        return 0
      fi
      log "task ${task_id}: worktree created at ${_task_worktree_path} (branch: ${_task_feature_branch})"

      # Update role_reminder: the worktree already has the feature branch checked
      # out; the agent must NOT run 'git checkout -b' in the shared canonical tree.
      role_reminder="${role_reminder}

Feature branch for this task: ${_task_feature_branch}
Your working directory is an isolated git worktree at ${_task_worktree_path} that is already checked out on the feature branch. Do NOT run 'git checkout -b' — the branch is already active. All git operations (commit, merge, branch -d) happen inside this worktree."
    else
      # No worktree (git_mode=none): keep the existing git checkout-b reminder.
      role_reminder="${role_reminder}

Feature branch for this task: ${_task_feature_branch}
Use this exact name when running: git checkout -b"
    fi
  elif [[ "$SUBAGENT" == "tester" ]]; then
    # --- Worktree lifecycle: TESTER git-workflow tasks ---
    # TESTER reads the RC branch at its current tip in a detached worktree.
    # No feature branch is created.  Tasks whose plugin declares git_mode=none
    # (e.g. document workflow) are out of scope for this path.
    #
    # TESTER parks the canonical dev tree onto the project's resting branch
    # (${prefix}main → --detach) and restores it after the task, so the dev
    # tree is never left parked or detached.
    local _wt_exit=0
    set +e
    _task_workflow_type="$(get_workflow_type_from_readme "$task_readme")"
    _wt_exit=$?
    set -e
    if [[ $_wt_exit -ne 0 ]]; then
      _task_workflow_type="release"   # safe default on parse failure
    fi

    # Load the workflow plugin to determine git mode via wf_git_mode.
    # Tasks with git_mode=none have no git worktree (e.g. document workflow).
    local _wf_tester_load_exit=0 _wf_tester_git_mode="none"
    set +e
    wf_load_plugin "$_task_workflow_type"
    _wf_tester_load_exit=$?
    set -e
    if [[ $_wf_tester_load_exit -ne 0 ]]; then
      log "task ${task_id}: ERROR: workflow plugin '${_task_workflow_type}' failed to load for tester: ${WF_LOAD_ERROR}"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script could not load workflow plugin '${_task_workflow_type}' for tester: ${WF_LOAD_ERROR}. Ensure team/workflows/${_task_workflow_type}/ has a valid workflow.cfg (status=ready) and workflow.sh."
      set_block "$task_status" "Needs Human" "yes"
      block_record_write "$task_status" plugin_load_failed "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
        "$(block_record_evidence --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      _chain_blocked_a_task=true
      return 0
    fi
    _wf_tester_git_mode="$(wf_git_mode)"

    if [[ "$_wf_tester_git_mode" != "none" ]]; then
      # Extract the source (RC) branch from the task README.
      local _sb_exit=0
      _task_source_branch=""
      set +e
      _task_source_branch="$(get_source_branch_from_readme "$task_readme")"
      _sb_exit=$?
      set -e
      if [[ $_sb_exit -ne 0 || -z "$_task_source_branch" ]]; then
        log "task ${task_id}: ERROR: cannot extract Source Branch from README; cannot create detached worktree for tester"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script could not read '## Source Branch' from ${task_readme}. Ensure the field is present and non-empty."
        set_block "$task_status" "Needs Human" "yes"
        block_record_write "$task_status" source_branch_missing "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
          "$(block_record_evidence --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _chain_blocked_a_task=true
        return 0
      fi
      log "task ${task_id}: tester source branch (RC): ${_task_source_branch}"

      # Park the canonical dev tree onto the project's resting branch before
      # creating the detached worktree, and record the branch for restore.
      # Sandbox guard: skip parking when TEAM_ROOT is under /tmp.
      # Resolve per-project dev tree first; fall back to global.
      local _dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
      local _sys_tmp="${TMPDIR:-/tmp}"
      local _kanban_root_is_sandbox=0
      _sys_tmp="${_sys_tmp%/}"
      if [[ -n "$TEAM_ROOT" && ( "$TEAM_ROOT" == /tmp/* || "$TEAM_ROOT" == /tmp || "$TEAM_ROOT" == "${_sys_tmp}/"* || "$TEAM_ROOT" == "$_sys_tmp" ) ]]; then
        _kanban_root_is_sandbox=1
      fi
      if [[ -n "$_dev_tree" && -d "$_dev_tree/.git" ]]; then
        if [[ $_kanban_root_is_sandbox -eq 1 ]]; then
          log "task ${task_id}: skipping dev-tree parking: kanban root (${TEAM_ROOT}) is a temp/sandbox tree — will not mutate ${_dev_tree}"
        else
          local _pb_exit=0
          local _resting_main=""
          set +e
          _resting_main="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "main" 2>/dev/null)"
          _pb_exit=$?
          set -e
          if [[ $_pb_exit -ne 0 || -z "$_resting_main" ]]; then
            _resting_main="main"
          fi

          local _park_exit=0
          set +e
          git -C "$_dev_tree" checkout "$_resting_main" 2>/dev/null
          _park_exit=$?
          set -e
          if [[ $_park_exit -eq 0 ]]; then
            _park_resting_branch="$_resting_main"
          else
            set +e
            git -C "$_dev_tree" checkout --detach 2>/dev/null
            _park_exit=$?
            set -e
          fi
          if [[ $_park_exit -ne 0 ]]; then
            log "task ${task_id}: WARNING: could not park canonical dev tree at ${_dev_tree}"
          else
            log "task ${task_id}: tester: canonical dev tree parked on ${_park_resting_branch:-<detached>} (was: ${_task_source_branch})"
          fi
        fi
      fi

      # Create a detached worktree at the RC branch tip.
      # No feature branch is created.
      # Capture ONLY stdout (the resolved worktree path). Do NOT use 2>&1
      # here — stderr from create_detached_worktree flows to the wake-script
      # stderr/cron log directly. git's own progress messages are suppressed
      # at the source inside worktree.sh (>/dev/null 2>/dev/null).
      local _ct_exit=0
      set +e
      _task_worktree_path="$(create_detached_worktree "$task_id" "$_task_source_branch" "$_dev_tree")"
      _ct_exit=$?
      set -e
      if [[ $_ct_exit -ne 0 || -z "$_task_worktree_path" ]]; then
        log "task ${task_id}: ERROR: create_detached_worktree failed (exit ${_ct_exit}); see stderr for diagnostic"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script failed to create detached git worktree for tester task (create_detached_worktree exit ${_ct_exit}). Check stderr/cron log for diagnostic. Verify that RC branch '${_task_source_branch}' exists locally and the temp root is writable."
        set_block "$task_status" "Needs Human" "yes"
        block_record_write "$task_status" "$(block_record_worktree_failure_code "$_dev_tree" "$_task_source_branch")" "$(block_record_owner_role "$task_status" "${role:-}" "$SUBAGENT")" \
          "$(block_record_evidence --repo "$_dev_tree" --source "$_task_source_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _chain_blocked_a_task=true
        _task_worktree_path=""   # ensure teardown guard is clear
        # Restore canonical dev tree HEAD to resting branch (early-exit path).
        if [[ -n "${_park_resting_branch:-}" && -n "${_dev_tree:-}" && -d "${_dev_tree}/.git" && $_kanban_root_is_sandbox -eq 0 ]]; then
          set +e
          git -C "$_dev_tree" checkout "$_park_resting_branch" 2>/dev/null
          set -e
          log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (detached-worktree-create-fail early-exit)"
        fi
        return 0
      fi
      log "task ${task_id}: tester detached worktree created at ${_task_worktree_path} (rc: ${_task_source_branch})"

      # Export PGAI_DEV_TREE_PATH to the worktree for the duration of the task.
      # The canonical value was captured in _canonical_dev_tree_path above.
      export PGAI_DEV_TREE_PATH="${_task_worktree_path}"
      log "task ${task_id}: PGAI_DEV_TREE_PATH overridden to worktree (was: ${_canonical_dev_tree_path:-<unset>})"

      # Update role_reminder: TESTER works in a detached worktree; no feature branch.
      role_reminder="${role_reminder}

Your working directory is an isolated git worktree at ${_task_worktree_path} checked out in detached HEAD mode at the RC branch ${_task_source_branch}. No feature branch was created. Do NOT run 'git checkout -b'. PGAI_DEV_TREE_PATH points at this worktree for the duration of the task."
    fi
  fi

  return 0
}
