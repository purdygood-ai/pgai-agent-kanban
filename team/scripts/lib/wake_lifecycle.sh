#!/usr/bin/env bash
# team/scripts/lib/wake_lifecycle.sh
#
# Wake task-completion-lifecycle stage: the post-exit WORKING handler, the
# CODER/WRITER completion gate, and the finalize=report intake-closure
# machinery (per-task closure plus the per-wake sweep that heals stranded
# items). Extracted from team/scripts/lib/wake_common.sh's former inline
# body so wake_common.sh's own remaining code stays a thinner shell over
# sourced lib stages. Behavior-preserving: no stdout/stderr line, exit code,
# state-transition semantics, or on-disk file content changes as a result of
# this file existing — it is one stage split out of a larger driver so
# each stays a focused, independently testable unit.
#
# Public entry point
# -------------------
#   coder_writer_completion_gate <task_id> <task_status> <subagent>
#                                 <feature_branch> <source_branch>
#                                 <worktree_path>
#       Mechanical completion gate for CODER/WRITER DONE-claims: verifies
#       the feature branch is merged into its target, the worktree is
#       clean, and the feature branch's own commits carry no empty
#       non-merge commit, BEFORE the DONE transition is accepted anywhere
#       else in wake reconciliation. Called by both wake provider scripts
#       (claude.sh, codex.sh) immediately after final_state is read from the
#       task's status.md. Censused refusal site (AGENT-RESOLVABLE class;
#       docs/blocking-site-census.md rows 6-7, plus the
#       completion-gate-empty-commit-01 inventory entry for check 3) —
#       classification, dual-channel emissions (pgai_format_error_contract),
#       and refusal/pass narration carried verbatim by this move.
#
# Other functions in this file — handle_working_after_exit,
# close_intake_on_finalize_report, sweep_running_intake_census — are shared
# primitives, not private helpers of coder_writer_completion_gate alone.
# Both wake provider scripts call all of them directly at their own points
# in the task lifecycle:
#   handle_working_after_exit        — provider-neutral WORKING-after-exit
#                                       classifier; the sole writer of the
#                                       durable ## Transient Retry record.
#   close_intake_on_finalize_report  — closure-parity helper for
#                                       finalize=report (testing-only)
#                                       workflows, invoked once a task's
#                                       sibling census goes fully terminal.
#   sweep_running_intake_census      — per-wake self-healing pass that heals
#                                       stranded "## Status: running" intake
#                                       items via the same census logic.
# Defined at source time, available as soon as this file is sourced.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   wake_bootstrap.sh    — log(), KANBAN_ROOT, ACTIVE_PROVIDER.
#   wake_task_state.sh   — set_state, set_block, normalize_status_file.
#   wake_task_transition.sh — transition_task_state (handle_working_after_exit).
#   Sourced transitively by wake_bootstrap.sh:
#     overwatch_protocol.sh — transient_retry_limit/_field/_write and
#                          zero_progress_advance_or_escalate (a separate
#                          counter shared with reconcile_stale_active).
#     error_contract.sh  — pgai_format_error_contract.
#     workflow.sh        — wf_load_plugin, WF_MANIFEST_FINALIZE.
#     pp_run_ops.sh      — pp_run_ops (intake closure, the signature clip).
#     block_record.sh    — each BLOCKED write also records ## Block Record
#                          (transient_api_error, gate_merge_not_found,
#                          gate_merge_evidence_not_found, gate_dirty_worktree,
#                          gate_empty_commit); a refused write only logs.
#
# python3 must be on PATH (used throughout this file's Python heredocs).
# Sourced by wake_common.sh immediately after wake_task_state.sh.

# ---------------------------------------------------------------------------
# handle_working_after_exit <task_id> <task_status> <log_file> <agent_exit>
#
# Provider-neutral WORKING-after-exit handler.  Called by both claude.sh and
# codex.sh when a task is still in WORKING state after the agent process exits.
#
# This is the ONE controller that classifies and records transient
# retry state: it is the sole writer of the durable ## Transient Retry record
# (via transient_retry_write, overwatch_protocol.sh). recheck_blocked_tasks
# below, and OVERWATCH's check-transient-api-error.sh, only ever READ this
# record — neither maintains its own counter or ceiling.
#
# Behaviour:
#   1. Grep the tail of <log_file> for known transient provider error signatures
#      (Anthropic: 5xx/overloaded/rate-limit/429; OpenAI: server_error/Rate limit
#      reached/The server had an error/500-503/529).  'insufficient_quota' is
#      intentionally excluded — it is a billing failure, not a retry-worthy hiccup.
#   2. If a transient signature is found, read the previous retry_count from the
#      durable ## Transient Retry record (never from ## Blocked Reason prose —
#      that section is promotion-erased and human-narrative-only), increment
#      it, write the updated record, and:
#        - If retry_count is still BELOW the ceiling (transient_retry_limit):
#          BLOCKED + Needs Human=no
#        - If retry_count has REACHED the ceiling: BLOCKED + Needs Human=yes
#          (escalate; exactly-at-cap is never left in limbo)
#   3. If no transient signature, the task made zero observable progress this
#      cycle (state never left WORKING, and no other classification explains
#      why) — this is the SAME zero-progress concept reconcile_stale_active
#      guards in wake_reconcile_sweeps.sh, and it is counted in the SAME durable
#      ## Zero Progress record (zero_progress_field/_record/_cap,
#      overwatch_protocol.sh; see that file's header for why this is a
#      separate counter from ## Transient Retry above). Below the
#      zero-progress cap: BACKLOG (retry on the next wake). At the cap:
#      BLOCKED + Needs Human=yes.
#   4. Normalizes the status file and prints the resulting final_state
#      (BLOCKED or BACKLOG) to stdout.
#
# The detection regex and retry-count logic live here exactly once; the two
# wake scripts' local log() calls may note the outcome, but neither script
# re-implements the detection substrate.
# ---------------------------------------------------------------------------
handle_working_after_exit() {
  local task_id="${1:-}"
  local task_status="${2:-}"
  local log_file="${3:-}"
  local agent_exit="${4:-}"

  local _transient_cap
  _transient_cap="$(transient_retry_limit)"
  local _transient_matched=false
  local _transient_sig=""
  # Default final_state: every branch below is a refusal-to-complete of one
  # shape or another. Only the zero-progress below-cap branch downgrades
  # this to BACKLOG (a retryable reset rather than a human escalation).
  local _result_state="BLOCKED"

  # --- Transient API error detection ---
  # Grep the last 50 lines of the agent log for known transient provider
  # signatures.  Two regex patterns are used:
  #   _DETECT_RE  — fast boolean match (grep -q); determines if any signature
  #                 is present in the tail.
  #   _EXTRACT_RE — the first matching fragment (a subset of _DETECT_RE) to end of
  #                 line via `.*`; in ERE `[^\n]` means "not \ or n", not "not newline".
  # Anthropic signatures: API Error 5xx, overloaded, overloaded_error,
  #   rate_limit / rate limit / 429, 50[023], 529.
  # OpenAI/codex signatures: server_error, Rate limit reached,
  #   The server had an error, HTTP 500/502/503/529 in error context.
  # EXCLUDED: insufficient_quota (non-transient billing failure).
  # NOTE: bare numeric alternations (429, 50[023], 529) are digit-bounded via
  #   (^|[^0-9])N([^0-9]|$) so they cannot match inside task-ID date segments,
  #   token counts, or elapsed-ms values (e.g. 20260503, 15030, 5029ms).
  #   Each numeric is a separate alternation to avoid |N| substrings that would
  #   be caught by the bare-alternation lint check.
  local _DETECT_RE='API Error: 5[0-9][0-9]|[Oo]verloaded|overloaded_error|rate[_. -]?limit|(^|[^0-9])429([^0-9]|$)|(^|[^0-9])50[023]([^0-9]|$)|(^|[^0-9])529([^0-9]|$)|server_error|[Tt]he server had an error|[Rr]ate limit reached'
  local _EXTRACT_RE='API Error: 5[0-9][0-9].*|[Oo]verloaded.*|overloaded_error.*|rate[_. -]?limit.*|(^|[^0-9])429([^0-9]|$)|(^|[^0-9])50[023]([^0-9]|$)|(^|[^0-9])529([^0-9]|$)|server_error.*|[Tt]he server had an error.*|[Rr]ate limit reached.*'

  if [[ -f "$log_file" ]]; then
    local _tail_lines
    _tail_lines="$(tail -50 "$log_file" 2>/dev/null || true)"
    # Here-string (<<<), not a pipe from echo into grep: such a pipe forks a
    # separate writer process whose write() can race grep's early exit-on-match.
    # Under host CPU contention, grep -q closing its read end mid-write can
    # deliver SIGPIPE to the echo side; with `set -o pipefail` (active in every
    # caller of this function) the pipeline's reported exit status becomes the
    # echo side's non-zero SIGPIPE code instead of grep's real (successful)
    # match, so the `if` silently takes the no-match branch even though the
    # tail content plainly contains the signature. A here-string hands grep its
    # input via a pre-written temp fd before grep execs, so there is no writer
    # process and no such race. Measured a nonzero false-negative rate for the
    # piped form under host CPU contention versus zero failures for the
    # here-string form across an equivalent number of trials.
    if grep -qiE "$_DETECT_RE" <<< "$_tail_lines"; then
      _transient_matched=true
      overwatch_load_module_runner || true  # pp_run_ops: clip counts characters, cut -c bytes
      _transient_sig="$(grep -iEo "$_EXTRACT_RE" <<< "$_tail_lines" \
        | tail -1 | pp_run_ops pgai_agent_kanban.task_status clip --chars 80 || true)"
      [[ -z "$_transient_sig" ]] && _transient_sig="transient provider error"
    fi
  fi

  if [[ "$_transient_matched" == "true" ]]; then
    # Read the previous retry_count from the durable ## Transient Retry record
    # — NOT from ## Blocked Reason prose. The record is the ONE authoritative
    # count and survives BACKLOG promotion; Blocked Reason is reset to "none"
    # by promote_blocked_to_backlog and must never be relied on for state.
    local _retry_count
    _retry_count="$(transient_retry_field "$task_status" "retry_count" "0")"
    [[ "$_retry_count" =~ ^[0-9]+$ ]] || _retry_count=0
    _retry_count=$(( _retry_count + 1 ))

    # Record the classification BEFORE branching so the durable record is
    # always up to date, regardless of which branch below fires.
    transient_retry_write "$task_status" "transient_api_error" "$_retry_count" \
      "$_transient_cap" "${ACTIVE_PROVIDER:-unknown}" "$_transient_sig"

    # Strictly-less-than the ceiling is the only
    # condition that leaves Needs Human=no. Exactly AT the ceiling escalates
    # immediately — there is no longer a limbo state where Needs Human=no but
    # retry_count has already reached the cap (recheck_blocked_tasks below
    # uses the identical "< ceiling" comparison against the same record, so
    # the two paths can never disagree).
    # NOTE: the "TRANSIENT API ERROR" prefix on ## Blocked Reason is preserved
    # verbatim (not reworded) because team/pgai_agent_kanban/dashboard/scan_attention.py's
    # _is_transient_blocked() does a case-insensitive startswith("TRANSIENT API
    # ERROR") check on this exact field to render the dashboard's TRANSIENT
    # section. The record (not this prose) is now the retry-count source of
    # truth, but the prefix itself remains a stable narrative marker.
    if [[ "$_retry_count" -lt "$_transient_cap" ]]; then
      log "task ${task_id} left in WORKING after transient API error (${_transient_sig}); marking BLOCKED+TRANSIENT (retry ${_retry_count}/${_transient_cap})"
      transition_task_state "$task_id" "$task_status" "BLOCKED" \
        || log "task ${task_id} WARNING: atomic transition to BLOCKED failed; status.md/marker left as they were (no partial update)"
      set_block "$task_status" "Blocked Reason" "TRANSIENT API ERROR — ${_transient_sig} — safe to retry (retry ${_retry_count}/${_transient_cap}; see ## Transient Retry record); no human action required. See ${log_file}."
      set_block "$task_status" "Blockers" "TRANSIENT API ERROR (retry ${_retry_count}/${_transient_cap}): ${_transient_sig}. See ${log_file}."
      set_block "$task_status" "Needs Human" "no"
    else
      log "task ${task_id} left in WORKING after transient API error (${_transient_sig}); retry cap reached (${_retry_count}/${_transient_cap}) — escalating to Needs Human=yes"
      transition_task_state "$task_id" "$task_status" "BLOCKED" \
        || log "task ${task_id} WARNING: atomic transition to BLOCKED failed; status.md/marker left as they were (no partial update)"
      set_block "$task_status" "Blocked Reason" "TRANSIENT API ERROR (retry cap ${_transient_cap} reached) — ${_transient_sig} — repeated transient failures; human investigation required (see ## Transient Retry record). Exit code: ${agent_exit}. See ${log_file}."
      set_block "$task_status" "Blockers" "TRANSIENT API ERROR (retry cap ${_transient_cap} reached): ${_transient_sig}. Exit code: ${agent_exit}. See ${log_file}."
      set_block "$task_status" "Needs Human" "yes"
    fi
    block_record_write "$task_status" transient_api_error "$(block_record_owner_role "$task_status" "${role:-}" "${SUBAGENT:-}")" "$(block_record_evidence --task-dir "$(dirname "$task_status")" --diag-log "$log_file")" || log "task ${task_id}: WARNING: Block Record not written"
  else
    # Zero progress: state never left WORKING and no transient signature
    # explains why. Counted in the SAME durable ## Zero Progress record (and
    # the SAME reset-below-cap/escalate-at-cap mechanics)
    # reconcile_stale_active uses — see
    # overwatch_protocol.sh's zero_progress_advance_or_escalate. This is a
    # SEPARATE counter from ## Transient Retry above.
    _result_state="$(zero_progress_advance_or_escalate "$task_id" "$task_status" \
      "clean_exit_no_progress" \
      "agent exited without moving task state, exit code ${agent_exit}; see ${log_file}" \
      "wake.working-after-exit.zero-progress-cap")"
  fi

  normalize_status_file "$task_status"
  printf '%s\n' "$_result_state"
}

# ---------------------------------------------------------------------------
# coder_writer_completion_gate <task_id> <task_status> <subagent>
#                               <feature_branch> <source_branch> <worktree_path>
#
# Mechanical completion gate for CODER/WRITER DONE-claims. Called by both
# claude.sh and codex.sh immediately after final_state is read from the
# task's status.md, BEFORE the DONE transition is accepted anywhere else in
# wake reconciliation (mark_backlog, the on-BLOCK overwatch trigger, the
# training-corpus copy, worktree teardown). Verifies the completion contract
# mechanically instead of trusting the agent's own done-claim:
#
#   1. Merge check — the task's feature branch must be merged into its
#      target (source/RC) branch. Checked via `git merge-base --is-ancestor`
#      when the feature branch ref still exists (the common "agent skipped
#      the merge" failure mode this gate exists to catch). When the ref is
#      already gone, the CODER/WRITER procedure's only deletion path is
#      `git branch -d` (never -D — see roles/CODER.md "Step 8"), which
#      refuses to delete a branch that is not already merged. A missing ref
#      is trusted as "merged" in either of two ways:
#        (a) the worktree's current HEAD is the expected source branch —
#            the state a plain `-d` performed IN this worktree requires; or
#        (b) merge-evidence read from the source branch's own history,
#            independent of this worktree's HEAD state — either a merge
#            commit naming the feature branch appears in the source
#            branch's recent log (the `--no-ff` procedure in CODER.md/
#            WRITER.md always produces one), or this worktree's current
#            HEAD commit is itself an ancestor of the source branch.
#      (b) exists because git allows a branch checked out in only one
#      worktree at a time, so when a sibling task's worktree legitimately
#      holds the source/RC branch, this task's own merge --no-ff happens
#      in THAT sibling worktree, not here — this worktree merely detaches
#      so its feature-branch ref can be deleted, and its HEAD never
#      becomes the source branch even though the merge is real. Evidence
#      is read from the source branch itself (shared across every
#      worktree) so the verdict does not depend on which worktree
#      performed the merge.
#   2. Cleanliness check — the task worktree must have no uncommitted
#      changes at done-claim time (`git status --porcelain`).
#   3. Empty-commit check — the feature branch's own commits must contain
#      no empty non-merge commit (a stray `git commit --allow-empty`, or an
#      edit reverted to byte-identical content). Reuses
#      `lint_rc_empty_commits.py`'s `find_empty_commits` logic (never
#      reimplemented) over `<source_branch>^..<feature_branch>` — valid
#      only once the source branch's tip is confirmed, by SHA, to be this
#      task's own --no-ff merge; otherwise the check is skipped (narrated).
#
# All three checks are delivery checks — they confirm the work actually
# landed on the source branch, that nothing was left uncommitted, and that
# what landed is accounted for; that is the gate's whole job. The gate runs
# no comment-provenance lint: by gate time the merge has usually already
# landed and the feature branch is already deleted, so a wording-only
# finding would cost an operator a reword-and-reset for litter the
# TESTER-time path already catches — the gated runners' quality-class
# hygiene lint's provenance class (`lint_hygiene.py`, tree-wide, no
# diff-scope mode) scans every release and TESTER cites-or-files the
# low-severity bug.
#
# Scope: only CODER/WRITER subagents route through this gate (the call site
# in each wake provider script only invokes it for those two). Tasks with no
# worktree (workflow git_mode=none, e.g. the document workflow) pass
# trivially — there is nothing to verify. CM/TESTER/PM completion semantics
# are untouched; this function is never called for those roles.
#
# On refusal, rewrites task_status to BLOCKED with a prescriptive remedy
# naming both branches (merge failure) or the dirty paths (cleanliness
# failure), sets Needs Human=yes, and echoes "BLOCKED". On pass, the status
# file is untouched and the function echoes "DONE" — byte-identical to the
# final_state the caller already had, so a properly merged, clean
# completion adds zero friction to the golden path.
#
# Narration: every outcome (pass or any refusal) is logged as exactly one
# line via log(), so intermittent agent omissions become a visible pattern
# in the wake log. Each log() call here is redirected to /dev/null at the
# call site (log() tees to both LOG_FILE and its own stdout — the ONE
# legitimate way to keep the durable log write while keeping this
# function's own stdout clean for the caller's command substitution).
# ---------------------------------------------------------------------------
coder_writer_completion_gate() {
  local task_id="${1:-}"
  local task_status="${2:-}"
  local subagent="${3:-}"
  local feature_branch="${4:-}"
  local source_branch="${5:-}"
  local worktree_path="${6:-}"

  if [[ "$subagent" != "coder" && "$subagent" != "writer" ]]; then
    printf 'DONE\n'
    return 0
  fi

  if [[ -z "$worktree_path" || -z "$feature_branch" || -z "$source_branch" ]]; then
    log "completion gate: task ${task_id} has no git worktree/branch to verify (workflow git_mode=none) — pass trivially" >/dev/null
    printf 'DONE\n'
    return 0
  fi

  if [[ ! -d "$worktree_path" ]]; then
    log "completion gate: task ${task_id} WARNING: worktree ${worktree_path} not found at gate time — cannot verify; passing (best-effort; gate must run before worktree teardown)" >/dev/null
    printf 'DONE\n'
    return 0
  fi

  # --- Merge check ---
  local merge_ok=false
  local feature_ref_exists=false
  local current_head=""
  local merge_evidence=""
  if git -C "$worktree_path" rev-parse --verify --quiet "$feature_branch" >/dev/null 2>&1; then
    feature_ref_exists=true
    if git -C "$worktree_path" merge-base --is-ancestor "$feature_branch" "$source_branch" 2>/dev/null; then
      merge_ok=true
    fi
  else
    current_head="$(git -C "$worktree_path" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
    if [[ "$current_head" == "$source_branch" ]]; then
      merge_ok=true
    else
      # Merge-evidence fallback. The ref is gone AND this
      # worktree's HEAD is not the source branch — the shape produced when
      # the merge legitimately happened in a SIBLING worktree that held the
      # source/RC branch at merge time (git allows a branch checked out in
      # only one worktree, so this worktree's own merge --no-ff was
      # impossible here). Read evidence from the source branch's own
      # history instead, which is shared across every worktree of the same
      # repository regardless of which one performed the merge.
      local _merge_log_hits
      _merge_log_hits="$(git -C "$worktree_path" log --format=%s -50 "$source_branch" 2>/dev/null | grep -cF -- "$feature_branch" || true)"
      if [[ "${_merge_log_hits:-0}" -gt 0 ]]; then
        merge_ok=true
        merge_evidence="merge commit naming ${feature_branch} found in ${source_branch}'s recent history"
      else
        local _worktree_head_commit
        _worktree_head_commit="$(git -C "$worktree_path" rev-parse --verify --quiet HEAD 2>/dev/null || true)"
        if [[ -n "$_worktree_head_commit" ]] && git -C "$worktree_path" merge-base --is-ancestor "$_worktree_head_commit" "$source_branch" 2>/dev/null; then
          merge_ok=true
          merge_evidence="worktree's current commit ${_worktree_head_commit} is an ancestor of ${source_branch}"
        fi
      fi
    fi
  fi

  if [[ "$merge_ok" != "true" ]]; then
    log "completion gate: task ${task_id} REFUSED — ${feature_branch} is not merged into ${source_branch}" >/dev/null
    if [[ "$feature_ref_exists" == "true" ]]; then
      set_block "$task_status" "Blockers" "$(pgai_format_error_contract \
        "wake.completion-gate.merge-not-found" \
        "The completion gate refused this DONE-claim because ${feature_branch} is not merged into ${source_branch} — the work is intact on the feature branch, but the merge step (CODER/WRITER PHASE 2 Step 7) never ran." \
        "Reset this task; on re-entry the agent merges and re-completes (or an operator can run: git checkout ${source_branch} && git merge --no-ff ${feature_branch}, then reset the task).")"
    else
      set_block "$task_status" "Blockers" "$(pgai_format_error_contract \
        "wake.completion-gate.merge-evidence-not-found" \
        "The completion gate refused this DONE-claim because ${feature_branch} no longer exists and the worktree at ${worktree_path} is not on ${source_branch} (HEAD=${current_head:-detached}) — the merge cannot be confirmed." \
        "A human must inspect ${worktree_path} for the feature branch's work (check 'git reflog' / dangling commits if the branch was deleted without merging), then reset this task.")"
    fi
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Needs Human" "yes"
    block_record_write "$task_status" "$([[ "$feature_ref_exists" == "true" ]] && echo gate_merge_not_found || echo gate_merge_evidence_not_found)" "$(block_record_owner_role "$task_status" "$subagent")" "$(block_record_evidence --worktree "$worktree_path" --branch "$feature_branch" --source "$source_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written" >/dev/null
    normalize_status_file "$task_status"
    printf 'BLOCKED\n'
    return 0
  fi

  # --- Cleanliness check ---
  local dirty_status
  dirty_status="$(git -C "$worktree_path" status --porcelain 2>/dev/null || true)"
  if [[ -n "$dirty_status" ]]; then
    local dirty_paths_oneline
    dirty_paths_oneline="$(printf '%s' "$dirty_status" | tr '\n' ';' | sed 's/;$//')"
    log "completion gate: task ${task_id} REFUSED — worktree ${worktree_path} has uncommitted changes" >/dev/null
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Blockers" "$(pgai_format_error_contract \
      "wake.completion-gate.dirty-worktree" \
      "The completion gate refused this DONE-claim because the task worktree ${worktree_path} has uncommitted changes at done-claim time (dirty paths: ${dirty_paths_oneline})." \
      "Reset this task; on re-entry the agent commits or discards the changes and re-completes (or an operator can inspect ${worktree_path}, commit or discard, then reset the task). The worktree is retained (not torn down) so the dirty paths remain inspectable.")"
    set_block "$task_status" "Needs Human" "yes"
    block_record_write "$task_status" gate_dirty_worktree "$(block_record_owner_role "$task_status" "$subagent")" "$(block_record_evidence --worktree "$worktree_path" --branch "$feature_branch" --source "$source_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written" >/dev/null
    normalize_status_file "$task_status"
    printf 'BLOCKED\n'
    return 0
  fi

  # --- Empty-commit check ---
  # Attempted only when the feature ref still exists and source_branch's
  # tip is confirmed, by SHA, to be this task's own --no-ff merge (second
  # parent == feature branch tip) -- what makes
  # "<source_branch>^..<feature_branch>" a safe, single-task range. A
  # failed confirmation skips the check (narrated) rather than risk
  # scanning another task's commits.
  if [[ "$feature_ref_exists" == "true" ]]; then
    local _empty_check_feat_tip _empty_check_source_parent2
    _empty_check_feat_tip="$(git -C "$worktree_path" rev-parse --verify --quiet "$feature_branch" 2>/dev/null || true)"
    _empty_check_source_parent2="$(git -C "$worktree_path" rev-parse --verify --quiet "${source_branch}^2" 2>/dev/null || true)"
    if [[ -n "$_empty_check_feat_tip" && -n "$_empty_check_source_parent2" && "$_empty_check_source_parent2" == "$_empty_check_feat_tip" ]]; then
      local _empty_check_output=""
      local _empty_check_rc=0
      _empty_check_output="$(python3 "${REPO_TEAM_DIR}/lint_rc_empty_commits.py" \
        --repo "$worktree_path" \
        --range "${source_branch}^..${feature_branch}" 2>&1)" || _empty_check_rc=$?
      if [[ "$_empty_check_rc" -eq 1 ]]; then
        log "completion gate: task ${task_id} REFUSED — empty non-merge commit found on ${feature_branch}" >/dev/null
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "$(pgai_format_error_contract \
          "wake.completion-gate.empty-commit" \
          "The completion gate refused this DONE-claim because ${feature_branch} carries an empty non-merge commit -- a commit whose tree is byte-identical to its parent's, so no task work accounts for it. Detail: ${_empty_check_output}" \
          "Reset this task; on re-entry the agent inspects the commit(s) named in the Detail above by running 'git show --stat' on each sha, drops the empty one (an interactive rebase dropping just that commit, or an amend if it is the branch tip), commits the result, re-merges --no-ff into ${source_branch}, and re-completes.")"
        set_block "$task_status" "Needs Human" "yes"
        block_record_write "$task_status" gate_empty_commit "$(block_record_owner_role "$task_status" "$subagent")" "$(block_record_evidence --worktree "$worktree_path" --branch "$feature_branch" --source "$source_branch" --diag-log "${LOG_FILE:-}")" || log "task ${task_id}: WARNING: Block Record not written" >/dev/null
        normalize_status_file "$task_status"
        printf 'BLOCKED\n'
        # AGENT-RESOLVABLE (docs/blocking-site-census.md's
        # completion-gate-empty-commit-01 entry): drop the commit, re-merge, re-complete.
        # CENSUS-ANCHOR: completion-gate-empty-commit-01
        return 0
      elif [[ "$_empty_check_rc" -ge 2 ]]; then
        log "completion gate: task ${task_id} WARNING: empty-commit check could not run (lint_rc_empty_commits.py exit ${_empty_check_rc}: ${_empty_check_output}) -- passing best-effort" >/dev/null
      fi
    else
      log "completion gate: task ${task_id} empty-commit check skipped -- ${source_branch}'s tip is not confirmed as this task's own merge commit (best-effort only, not blocking)" >/dev/null
    fi
  fi

  if [[ -n "$merge_evidence" ]]; then
    log "completion gate: task ${task_id} PASS — ${feature_branch} merged into ${source_branch} via merge-evidence fallback (${merge_evidence}), worktree clean" >/dev/null
  else
    log "completion gate: task ${task_id} PASS — ${feature_branch} merged into ${source_branch}, worktree clean" >/dev/null
  fi
  printf 'DONE\n'
  return 0
}

# ---------------------------------------------------------------------------
# close_intake_on_finalize_report
#   <task_id> <task_readme> <project_root> <tasks_root> <wf_manifest_finalize>
#   [<req_path_override>]
#
# Closure-parity helper for testing-only (finalize=report) workflows.
# Mirrors the CM intake-item closure that cm/release.sh Step 16b performs for
# release workflows via cm/promote_bundled_items.py.
#
# Root-cause evidence (observed 2026-07-14T14:04:10):
#   "finalize_mode:report not found in README constraints — skipping intake
#   closure (not a finalize=report terminal ticket)"
# The prior Guard 2 checked for a README marker that inject_simple_tester_task
# writes — but PM-authored one-ticket plans never carry it.  The fix replaces
# that marker check with a completion census: every sibling task that shares
# the same requirements path must be terminal before closure fires.  This is
# shape-blind (one-ticket, two-ticket, injected or PM-authored) and mirrors
# the same rule CM workflows implement implicitly (CM is positionally last).
#
# Parity with: cm/release.sh Step 16b + cm/promote_bundled_items.py.
#
# Behaviour:
#   1. Guard 1: <wf_manifest_finalize> must equal "report".  Any other value
#      (tag, publish, …) exits immediately — no-op for release/document paths.
#   2. Requirements path: when <req_path_override> (6th arg) is non-empty it is
#      used directly, bypassing README extraction.  Otherwise the first
#      absolute-path entry in <task_readme>'s ## Inputs section is used
#      (requirements file prepended by create_task_folder).
#   3. Completion census: scan ALL task folders under <tasks_root> whose
#      README ## Inputs cite the same requirements path.  Terminal states are
#      exactly {DONE, WONT-DO}; all others (WORKING, BACKLOG, WAITING,
#      BLOCKED) are non-terminal.  If every sibling is terminal, proceed to
#      closure.  Otherwise log the census result (counts, non-terminal siblings
#      by ID and state) and skip.  Note: the BLOCKED mid-run check from the
#      prior implementation is subsumed here — BLOCKED is non-terminal and will
#      prevent closure via the census, so no separate BLOCKED-only pass is
#      needed.
#   4. Closure: invoke the canonical close_item helper via the ops package:
#        pp_run_ops pgai_agent_kanban.ops close_item <project_root> <key>
#      where <key> is the requirements file's basename (without .md extension).
#
# Returns 0 on success (closure fired or legitimately skipped).
# Never fails the caller — all internal error paths return 0.
# ---------------------------------------------------------------------------
close_intake_on_finalize_report() {
  local _task_id="${1:-}"
  local _task_readme="${2:-}"
  local _project_root="${3:-}"
  local _tasks_root="${4:-}"
  local _wf_finalize="${5:-}"
  local _req_path_override="${6:-}"

  # Narration prefix: when called from sweep_running_intake_census (_task_id=="sweep"),
  # use "sweep-census:" so all sweep-mode helper lines share one grep-able prefix.
  # Non-sweep callers keep the "task <id>: close_intake_on_finalize_report:" form.
  local _log_pfx
  if [[ "${_task_id}" == "sweep" ]]; then
    _log_pfx="sweep-census:"
  else
    _log_pfx="task ${_task_id}: close_intake_on_finalize_report:"
  fi

  # Guard 1: finalize capability must be "report".
  # All other values (tag, publish, none) skip this block entirely so that
  # release workflows and document workflows are byte-identical to their prior
  # behaviour.
  if [[ "$_wf_finalize" != "report" ]]; then
    return 0
  fi

  # Resolve the requirements file path.
  # When a path override is provided (sweep call site), use it directly.
  # Otherwise extract the first absolute path from the task README ## Inputs
  # (create_task_folder always prepends the requirements path as the first
  # entry when one is present).
  local _req_path=""
  if [[ -n "$_req_path_override" ]]; then
    _req_path="$_req_path_override"
  else
    _req_path="$(python3 - "$_task_readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
except Exception:
    raise SystemExit(1)
m = re.search(r'(^##[^\S\n]+Inputs[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', text, flags=re.M)
if not m:
    raise SystemExit(1)
for line in m.group(2).splitlines():
    line = line.strip()
    if line.startswith('-'):
        line = line[1:].strip()
    if line.startswith('/') and line.endswith('.md'):
        print(line)
        raise SystemExit(0)
raise SystemExit(1)
PY
)" || true
  fi

  if [[ -z "$_req_path" ]]; then
    log "${_log_pfx} could not extract requirements path from README ## Inputs — skipping intake closure"
    return 0
  fi

  if [[ ! -f "$_req_path" ]]; then
    log "${_log_pfx} requirements file not found at ${_req_path} — skipping intake closure"
    return 0
  fi

  log "${_log_pfx} requirements file identified: ${_req_path}"

  # Completion census: scan ALL task folders under tasks_root whose README
  # ## Inputs cite the same requirements path.  Closure fires only when every
  # sibling (current task included, called post-transition as DONE) is
  # terminal.  Terminal states: DONE, WONT-DO.  Non-terminal: everything else
  # (WORKING, BACKLOG, WAITING, BLOCKED).  Prior-run corpses in WONT-DO are
  # terminal and do not prevent closure.
  local _census_result=""
  if [[ -d "$_tasks_root" ]]; then
    _census_result="$(python3 - "$_tasks_root" "$_req_path" <<'PY'
import pathlib, re, sys

TERMINAL = {"DONE", "WONT-DO"}

tasks_root = pathlib.Path(sys.argv[1])
req_path   = sys.argv[2]

total      = 0
non_terminal = []  # list of "id:state" strings

for task_dir in sorted(tasks_root.iterdir()):
    if not task_dir.is_dir() or task_dir.name == "queues":
        continue
    readme      = task_dir / "README.md"
    status_file = task_dir / "status.md"
    if not readme.is_file() or not status_file.is_file():
        continue
    try:
        readme_text = readme.read_text(encoding="utf-8")
    except OSError:
        continue
    # Filter to tasks whose ## Inputs cite the same requirements path
    m = re.search(r'(^##[^\S\n]+Inputs[^\S\n]*)((?:\n(?!##)[^\n]*)*?)(\n+##|\n*\Z)', readme_text, flags=re.M)
    if not m or req_path not in m.group(2):
        continue
    total += 1
    # Read sibling state
    try:
        status_text = status_file.read_text(encoding="utf-8")
    except OSError:
        # Unreadable status — treat as non-terminal to be safe
        non_terminal.append(f"{task_dir.name}:UNKNOWN")
        continue
    state_m = re.search(r'^##\s+State\s*\n\s*(\S+)', status_text, re.M)
    state = state_m.group(1).strip().upper() if state_m else "UNKNOWN"
    if state not in TERMINAL:
        non_terminal.append(f"{task_dir.name}:{state}")

if non_terminal:
    summary = ", ".join(non_terminal)
    print(f"skip:{total} sibling(s), {len(non_terminal)} non-terminal: {summary}")
else:
    print(f"ok:{total}")
PY
)" || true
  fi

  # Parse census result: "ok:N" means all siblings are terminal; "skip:..." means not.
  if [[ -z "$_census_result" || "$_census_result" == skip:* ]]; then
    local _census_detail="${_census_result#skip:}"
    log "${_log_pfx} census incomplete — skipping intake closure (${_census_detail:-tasks_root not found or unreadable})"
    return 0
  fi

  # All siblings are terminal: invoke canonical close_item helper.
  # Key is the requirements file basename without the .md extension.
  local _req_key
  _req_key="$(basename "$_req_path" .md)"

  log "${_log_pfx} closing intake item '${_req_key}' (finalize=report terminal ticket complete; parity with CM Step 16b)"

  local _close_exit=0
  set +e
  pp_run_ops pgai_agent_kanban.ops close_item "$_project_root" "$_req_key" done 2>&1 | \
    while IFS= read -r _line; do log "${_log_pfx} ${_line}"; done
  _close_exit="${PIPESTATUS[0]}"
  set -e

  if [[ $_close_exit -eq 0 ]]; then
    log "${_log_pfx} intake item '${_req_key}' closed done"
  elif [[ $_close_exit -eq 2 ]]; then
    log "${_log_pfx} WARNING: ambiguous key '${_req_key}' — could not uniquely identify intake item; item left at running"
  elif [[ $_close_exit -eq 3 ]]; then
    log "${_log_pfx} WARNING: intake item '${_req_key}' not found — nothing to close"
  else
    log "${_log_pfx} WARNING: close_item exited ${_close_exit} for '${_req_key}'; item may not have been closed"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# sweep_running_intake_census
#
# Per-wake self-healing pass: close stranded intake items whose sibling tasks
# all reached a terminal state but whose own ## Status is still "running".
# Called from the per-wake re-check sweep (alongside recheck_waiting_tasks and
# recheck_blocked_tasks) so that items whose closing moment was missed by a
# crash, restart, or clock-racing tick are healed on the next wake.
#
# Context: runs inside run_project_chain after pp_load_config, so the
# following shell variables are available:
#   _CURRENT_PROJECT — the project currently being processed
#   PGAI_PROJECT_ROOT — the project root directory
#   TASKS_ROOT — tasks/ directory for this project
#
# Resolution: workflow type and finalize capability are resolved PER ITEM from
# each requirements doc's "## Workflow Type" field (not from project.cfg).
# A project whose cfg lacks workflow_type, or a project hosting items of mixed
# workflow types, is handled correctly: each item is evaluated independently.
# project.cfg is at most a hint; it is never the gate.
#
# Plugin cache: wf_load_plugin is called at most once per distinct workflow
# type per wake — an associative array keyed by type memoises the finalize
# value within this function's scope so repeated items of the same type do
# not repeat the plugin load.
#
# Narration: every item-level outcome is logged at the standard log level so
# there is always at least one sweep-census line per wake when running items
# exist.  Silent early returns are eliminated.
#
# Guard (one glob): if the requirements directory is absent or empty the
# function logs a skip line and returns after one directory iteration.
#
# For each running intake item where finalize=report the existing
# close_intake_on_finalize_report census logic runs: all sibling tasks must
# be terminal (DONE or WONT-DO) before the item is flipped to done.
#
# Returns 0 always (never blocks the caller).
# ---------------------------------------------------------------------------
sweep_running_intake_census() {
  # Guard: find the requirements directory for this project.
  # pp_requirements_dir echoes "<project_root>/requirements".
  local _swc_req_dir=""
  _swc_req_dir="$(pp_requirements_dir "${_CURRENT_PROJECT:-}" 2>/dev/null || true)"
  if [[ -z "$_swc_req_dir" || ! -d "$_swc_req_dir" ]]; then
    log "sweep-census: project ${_CURRENT_PROJECT:-}: skipped — no requirements directory"
    return 0
  fi

  # Per-wake plugin cache: declare -A in local scope.
  # Keys are workflow type strings; values are the resolved finalize capability.
  # "LOAD_FAILED" is stored when wf_load_plugin returns non-zero so the
  # failure is cached and not retried for subsequent items of the same type.
  declare -A _swc_finalize_cache

  # Iterate over requirements files (one glob); check each for running status.
  local _swc_found_running=false
  local _swc_req_file
  for _swc_req_file in "$_swc_req_dir"/*.md; do
    [[ -f "$_swc_req_file" ]] || continue

    # Read ## Status from the requirements doc.
    local _swc_status=""
    _swc_status="$(python3 - "$_swc_req_file" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
except Exception:
    raise SystemExit(1)
m = re.search(r'^##\s+Status\s*\n\s*(\S+)', text, re.M)
print(m.group(1).strip().lower() if m else "")
PY
    )" || true

    [[ "$_swc_status" == "running" ]] || continue

    _swc_found_running=true

    # Read ## Workflow Type from the requirements doc (item-level resolution).
    # Defaults to "release" when the field is absent or unreadable.
    local _swc_item_wf_type=""
    _swc_item_wf_type="$(python3 - "$_swc_req_file" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
except Exception:
    print("release"); raise SystemExit(0)
m = re.search(r'^##\s+Workflow\s+Type[^\S\n]*\n(.*?)(\n+##|\Z)', text, flags=re.S|re.M|re.I)
if not m:
    print("release"); raise SystemExit(0)
val = m.group(1).strip().lower()
print(val if val else "release")
PY
    )" || _swc_item_wf_type="release"

    # Resolve finalize capability from cache or by loading the plugin.
    local _swc_item_finalize=""
    if [[ -v _swc_finalize_cache["$_swc_item_wf_type"] ]]; then
      _swc_item_finalize="${_swc_finalize_cache[$_swc_item_wf_type]}"
    else
      local _swc_plugin_exit=0
      set +e
      wf_load_plugin "${_swc_item_wf_type}" 2>/dev/null
      _swc_plugin_exit=$?
      set -e
      if [[ $_swc_plugin_exit -eq 0 ]]; then
        _swc_item_finalize="${WF_MANIFEST_FINALIZE:-}"
      else
        _swc_item_finalize="LOAD_FAILED"
      fi
      _swc_finalize_cache["$_swc_item_wf_type"]="$_swc_item_finalize"
    fi

    if [[ "$_swc_item_finalize" == "LOAD_FAILED" ]]; then
      log "sweep-census: skipped — ${_swc_req_file}: workflow type '${_swc_item_wf_type}' plugin failed to load"
      continue
    fi

    if [[ "$_swc_item_finalize" != "report" ]]; then
      log "sweep-census: skipped — ${_swc_req_file}: finalize=${_swc_item_finalize} — not report"
      continue
    fi

    log "sweep-census: project ${_CURRENT_PROJECT:-}: running intake item ${_swc_req_file}; attempting completion census"
    close_intake_on_finalize_report \
      "sweep" \
      "" \
      "${PGAI_PROJECT_ROOT:-}" \
      "${TASKS_ROOT:-}" \
      "report" \
      "$_swc_req_file"
  done

  if [[ "$_swc_found_running" == "false" ]]; then
    log "sweep-census: project ${_CURRENT_PROJECT:-}: no running intake items"
  fi

  return 0
}
