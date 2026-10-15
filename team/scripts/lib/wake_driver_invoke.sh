#!/usr/bin/env bash
# team/scripts/lib/wake_driver_invoke.sh
# Task-invocation stage of the shared wake pipeline: builds the task prompt
# (including the verbose-mode and documentation-feedback-mode injections),
# performs the BACKLOG -> WORKING state transition, stamps the resolved model,
# rotates stale artifacts, activates the git remote-boundary guard for non-CM
# roles (wd_install_git_boundary_guard, wake_guard_wiring.sh), takes the pre-task
# pollution/litter snapshots, and invokes the provider CLI (through the
# five-function provider interface) in its own process group under the watchdog.
#
# Public entry point
# -------------------
#   wd_invoke_task
#       Builds the prompt, transitions the task to WORKING, stamps the
#       model, rotates stale report.md/gaps.md artifacts, takes the
#       pre-task snapshots, and runs the provider CLI (via provider_dispatch
#       / provider_parse_output, orchestrated by the private
#       _wd_run_provider_and_capture_tokens helper below), optionally under
#       the per-task deadline watchdog. Returns 0 to proceed to the finalize
#       stage, or 2 for the FATAL internal-state-loss condition, matching
#       process_one_task's own (arguably overloaded, but
#       behavior-preserving) reuse of exit code 1 when it in turn `return`s.
#
# Contract: called from within process_one_task's own stack frame (never a
# subshell); relies on bash's dynamic scoping to read/write process_one_task's
# own `local` variables directly. Reads: task_id task_dir task_readme task_status
# task_log_dir task_artifact_dir role SUBAGENT selected_model model_source
# selected_effort effort_source _task_worktree_path _task_feature_branch
# _canonical_dev_tree_path _park_resting_branch _sweep_canonical_tree _debug_gate
# _CURRENT_PROJECT TEAM_ROOT KANBAN_ROOT SCRIPT_DIR; and reads (module-level
# globals set by the prep stage, assigned without `local`): role_reminder
# project_readme_line. Writes (process_one_task-level locals the caller must have
# declared first): task_start_ts agent_exit _task_killed _litter_snapshot_file
# _pollution_pre_snapshot _pollution_snapshot_ok. LAST_TASK_FINAL_STATE is the
# shared cross-task-result global already established by process_one_task itself;
# PROVIDER_AGENT_EXIT_CODE is the shared global the provider interface writes its
# CLI exit code to; provider_dispatch_refused <task_id> (provider_claude.sh)
# answers whether that CLI refused the dispatch before any work began.
# CLEANUP_ON_EXIT_PROCESS_GROUP (script_common.sh) is the global through which
# the provider's process group is exposed to the wake's exit trap while it may
# still have members; wd_finalize_task clears it after its drain. Before the
# [A] write, task_finalize_record_clear (wake_reconcile_sweeps.sh) drops the
# previous dispatch's finalize record, so a DONE this dispatch reaches without
# the wake's own finalize is gated by the reconciliation sweeps.
#
# Dependencies: wake_bracket_compute_temp_subtree / wake_bracket_pre_dispatch
# (wake_bracket.sh), _resolve_training_gate (wake_bootstrap.sh),
# mark_backlog / set_state and the model-stamp writer (wake_task_state.sh),
# wf_load_plugin / wf_git_mode (workflow.sh), teardown_task_worktree
# (worktree.sh), pp_project_root / pp_tasks_dir (project_paths.sh),
# pgai_temp_dir / pgai_mktemp (temp.sh), token_capture_merge_into_task_artifacts
# / token_canonicalize_model (token_capture.sh), get_release_state_field
# (wake_autonomous_scan.sh), provider_dispatch / provider_parse_output (the
# five-function provider interface — provider_claude.sh today), log()
# (wake_bootstrap.sh), _wd_build_governance_line (wake_governance_composer.sh,
# self-sourced below; it in turn calls read_order_resolve_role from
# read_order.sh), _wd_is_safeguard_refusal (wake_safeguard_refusal.sh,
# self-sourced below), pgai_emit_error_contract (error_contract.sh),
# wd_install_git_boundary_guard (wake_guard_wiring.sh) — all sourced before
# this file's functions run.
#
# Telemetry-block safeguard retry: the optional documentation-feedback block
# is pure prompt-engineering instrumentation, so a provider safeguard
# refusing it must never take the whole wake down. wd_invoke_task's call
# sites route through _wd_run_provider_with_telemetry_safeguard_retry (see
# its own header below) instead of the bare capture function.

# The governance composer (_wd_build_governance_line) and the safeguard-refusal
# classifier (_wd_is_safeguard_refusal) are self-sourced here rather than by
# wake_driver.sh so they are defined whether this stage is loaded through the
# driver or sourced on its own; the same shape wake_task_state.sh uses for
# atomic_write.sh.
_WAKE_DRIVER_INVOKE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=wake_governance_composer.sh
source "${_WAKE_DRIVER_INVOKE_LIB_DIR}/wake_governance_composer.sh"
# shellcheck source=wake_safeguard_refusal.sh
source "${_WAKE_DRIVER_INVOKE_LIB_DIR}/wake_safeguard_refusal.sh"
# The JSON field reads below go through pp_run_ops (wake_bootstrap.sh's,
# when this stage is loaded through the driver).
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_WAKE_DRIVER_INVOKE_LIB_DIR}/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# _wd_run_provider_and_capture_tokens (private)
#
# Provider-neutral orchestration around the two provider-owned interface
# calls: creates the raw-output/result-text temp files, calls
# provider_dispatch then provider_parse_output, resolves the canonical model
# id, merges usage into tokens.json via token_capture_merge_into_task_artifacts,
# and verifies the merge landed. This is the boilerplate that was previously
# duplicated near-verbatim inside wake_claude_provider.sh's and
# wake_codex_provider.sh's own provider_invoke_agent — centralizing it here
# so the boilerplate exists once is the whole point of the shared driver.
#
# Reads (dynamic scope, from wd_invoke_task's own locals, which are in turn
# process_one_task's locals): prompt selected_model model_source
# selected_effort effort_source task_id task_artifact_dir SUBAGENT LOG_FILE
# AGENT. Sets PROVIDER_AGENT_EXIT_CODE (global).
# ---------------------------------------------------------------------------
_wd_run_provider_and_capture_tokens() {
  local _tc_raw_output _tc_result_text _tc_capture_dir
  _tc_capture_dir="$(pgai_temp_dir)/token_capture"
  mkdir -p "$_tc_capture_dir" 2>/dev/null || true
  _tc_raw_output="$(mktemp "${_tc_capture_dir}/raw_output.XXXXXX" 2>/dev/null || echo "")"
  _tc_result_text="$(mktemp "${_tc_capture_dir}/result_text.XXXXXX" 2>/dev/null || echo "")"

  local _tc_invocation_start
  _tc_invocation_start="$(date +%s)"

  set +e
  if [[ -n "$_tc_raw_output" && -n "$_tc_result_text" ]]; then
    provider_dispatch \
      "$prompt" \
      "${selected_model:-}" \
      "${model_source:-}" \
      "${selected_effort:-}" \
      "${effort_source:-}" \
      "$task_id" \
      "$LOG_FILE" \
      "$SUBAGENT" \
      "$_tc_raw_output"

    # provider_dispatch's own set+e/set-e pair (a global bash flag, not
    # function-scoped) leaves errexit reactivated here; re-assert set +e so
    # a failed provider_parse_output below cannot abort this function.
    set +e
    local _tc_usage_record _tc_parse_exit
    _tc_usage_record="$(provider_parse_output "$_tc_raw_output" "$_tc_result_text" "${selected_model:-}" 2>>"$LOG_FILE")"
    _tc_parse_exit=$?
    if [[ -f "$_tc_result_text" ]]; then
      tee -a "$LOG_FILE" < "$_tc_result_text" >/dev/null
    fi

    # Compute elapsed seconds
    local _tc_elapsed
    _tc_elapsed=$(( $(date +%s) - _tc_invocation_start ))

    # Determine canonical model ID for tokens.json pricing lookups.
    local _tc_model _tc_canonical_model_id
    if command -v jq >/dev/null 2>&1; then
      _tc_canonical_model_id="$(printf '%s' "$_tc_usage_record" | jq -r '.canonical_model_id // ""' 2>/dev/null || echo "")"
    else
      _tc_canonical_model_id="$(pp_run_ops pgai_agent_kanban.lib.json_field "$_tc_usage_record" canonical_model_id 2>/dev/null || echo "")"
    fi
    if [[ -n "$_tc_canonical_model_id" ]]; then
      _tc_model="$_tc_canonical_model_id"
    else
      local _tc_raw_model
      _tc_raw_model="${selected_model:-unknown}"
      _tc_model="$(token_canonicalize_model "$_tc_raw_model" 2>>"$LOG_FILE" || echo "$_tc_raw_model")"
      if [[ "$_tc_model" != "$_tc_raw_model" ]]; then
        echo "[$(date -Iseconds)] wake(${AGENT}): token capture: mapped model shortname '${_tc_raw_model}' -> '${_tc_model}'" | tee -a "$LOG_FILE"
      fi
    fi

    local _tc_provider="${PROVIDER_NAME}"

    # Determine rc_version for tokens.json
    local _tc_rc_version
    _tc_rc_version="$(get_release_state_field "Active RC" 2>/dev/null || echo "none")"
    if [[ -z "$_tc_rc_version" || "$_tc_rc_version" == "none" ]]; then
      _tc_rc_version="none"
    fi

    # Merge usage into task artifacts/tokens.json.
    # On parse failure, _tc_usage_record contains a captured:false sentinel
    # from provider_parse_output, pass it directly so merge writes an
    # explicit failure record rather than zeroed counts.
    local _tc_merge_exit=0
    if [[ $_tc_parse_exit -eq 0 ]]; then
      token_capture_merge_into_task_artifacts \
        "$_tc_usage_record" "$task_artifact_dir" \
        "$_tc_model" "$_tc_provider" "$SUBAGENT" "$_tc_elapsed" "$_tc_rc_version" \
        "${selected_effort:-}" \
        2>>"$LOG_FILE" || _tc_merge_exit=$?
    else
      echo "token capture: WARNING: parse_result failed (exit ${_tc_parse_exit}) — no usage captured for ${task_id}" \
        | tee -a "$LOG_FILE" >&2
      token_capture_merge_into_task_artifacts \
        "$_tc_usage_record" "$task_artifact_dir" \
        "$_tc_model" "$_tc_provider" "$SUBAGENT" "$_tc_elapsed" "$_tc_rc_version" \
        "${selected_effort:-}" \
        2>>"$LOG_FILE" || _tc_merge_exit=$?
    fi

    # Verify tokens.json landed and determine capture status.
    if [[ -f "${task_artifact_dir}/tokens.json" ]]; then
      local _tc_was_captured
      if command -v jq >/dev/null 2>&1; then
        _tc_was_captured="$(jq -r '.captured // "true"' "${task_artifact_dir}/tokens.json" 2>/dev/null || echo "true")"
      else
        _tc_was_captured="$(pp_run_ops pgai_agent_kanban.lib.json_field --file --default true "${task_artifact_dir}/tokens.json" captured 2>/dev/null || echo "true")"
        _tc_was_captured="${_tc_was_captured,,}"
      fi
      # A dispatch the CLI refused before any work began still writes tokens.json (the attempt stays accounted for) but is never reported as success.
      if provider_dispatch_refused "$task_id"; then
        echo "token capture status: NOT STARTED (provider refused the dispatch before any work began) — tokens.json written for ${task_id}" | tee -a "$LOG_FILE" >&2
      elif [[ "$_tc_was_captured" == "false" ]]; then
        echo "token capture status: INCOMPLETE (no usage emitted) — tokens.json written for ${task_id}" | tee -a "$LOG_FILE" >&2
      else
        echo "token capture status: success — tokens.json written for ${task_id}" | tee -a "$LOG_FILE" >&2
      fi
    else
      echo "token capture status: WARNING — tokens.json NOT found at ${task_artifact_dir}/tokens.json after merge (merge_exit=${_tc_merge_exit}, parse_exit=${_tc_parse_exit}) for ${task_id}" \
        | tee -a "$LOG_FILE" >&2
    fi

    rm -f "$_tc_raw_output" "$_tc_result_text" 2>/dev/null || true
  else
    # Temp file creation failed — fall back to plain invocation (no token capture)
    echo "token capture: WARNING: cannot create temp files under ${_tc_capture_dir} — token capture disabled for ${task_id}" \
      | tee -a "$LOG_FILE" >&2
    provider_dispatch \
      "$prompt" \
      "${selected_model:-}" \
      "${model_source:-}" \
      "${selected_effort:-}" \
      "${effort_source:-}" \
      "$task_id" \
      "$LOG_FILE" \
      "$SUBAGENT" \
      ""
  fi
  set -e
}

# ---------------------------------------------------------------------------
# _wd_build_task_prompt (private)
# Builds the `prompt` variable (process_one_task's local), including the
# working-directory override line, the verbose-mode progress-logging block,
# and the documentation-feedback-mode block.
# ---------------------------------------------------------------------------
_wd_build_task_prompt() {
  # Build the working-directory override line for the prompt.
  # For git-workflow CODER/WRITER tasks: the agent works in the isolated worktree
  # with a feature branch.  For TESTER: the agent works in a detached worktree;
  # no feature branch is mentioned.
  local _prompt_working_dir_override=""
  if [[ -n "$_task_worktree_path" ]]; then
    if [[ "$SUBAGENT" == "tester" ]]; then
      _prompt_working_dir_override="IMPORTANT: The working directory for this task is the isolated git worktree at ${_task_worktree_path} (not the path listed in the task README's ## Working Directory field). The worktree is checked out in detached HEAD mode at the RC branch. All source reads and test runs must happen inside ${_task_worktree_path}. Do NOT create a feature branch. PGAI_DEV_TREE_PATH has been set to ${_task_worktree_path} for the duration of this task."
    else
      _prompt_working_dir_override="IMPORTANT: The working directory for this task is the isolated git worktree at ${_task_worktree_path} (not the path listed in the task README's ## Working Directory field, and whatever its ## Git Repo field says — 'none' there means no remote, not no git). All source-tree edits must happen inside ${_task_worktree_path}. The feature branch ${_task_feature_branch} is already checked out there; merge it --no-ff into the source branch and delete it with -d before you set DONE."
    fi
  fi

  # Resolve the per-project temp subtree at dispatch time so the emitted prompt
  # contains the absolute path rather than an unexpanded variable reference.
  local _project_temp_subtree
  _project_temp_subtree="$(wake_bracket_compute_temp_subtree "${_CURRENT_PROJECT:-}")"

  # Resolve the governance read list for the dispatched role from its
  # read-order manifest (read-order/${SUBAGENT}.txt) so that adding a
  # document to a role's wake is a manifest edit, not a code change here.
  # See _wd_build_governance_line's own header (wake_governance_composer.sh)
  # for the fallback and project-readme-splice behavior.
  local _wd_governance_line=""
  _wd_build_governance_line "$SUBAGENT" "$role" "$TEAM_ROOT" "$project_readme_line"

  prompt=$(cat <<EOF
Use the ${SUBAGENT} subagent to process the kanban task at ${task_dir}.

The task ID is ${task_id}. The kanban root is ${TEAM_ROOT}.

Before starting, read the governance stack: ${_wd_governance_line}. The role file is your procedure.

Read the task README at ${task_readme} for the assignment. Read the status at ${task_status} to see what earlier sessions left. The wake set it to WORKING before starting you, so the state alone does not mean resume: treat this as a resume only if ${task_log_dir}/progress.log, ${task_artifact_dir} or the feature branch shows earlier work, otherwise as a fresh start, and replace any placeholder status fields before beginning. Do the work: source changes go in your working directory (the worktree named below, when there is one), and reports, notes and other task deliverables go in ${task_artifact_dir}. Update ${task_status} with the final state when done.

${_prompt_working_dir_override}
Scratch/diagnostic output goes under ${_project_temp_subtree} — never bare /tmp (SOP: Temporary File Convention).

You are running autonomously. No human is reviewing your output in real time. Your terminal states are exactly: DONE (work shipped), BLOCKED (specific named obstacle), or WONT-DO (cancelled). There is no third state for "I want a human to look at this." If you find yourself reaching for one, either name a concrete obstacle and BLOCK, or finish the work and document any concerns in the status Summary while marking DONE.

${role_reminder}
EOF
)

  # --- Verbose mode prompt injection ---
  if [[ "${_debug_gate}" == "true" ]]; then
    mkdir -p "${task_log_dir}"
    # Derive central_debug_log explicitly from _CURRENT_PROJECT so that the
    # agent-progress stream always routes per-project when a project is resolved,
    # and to root only for genuine pre-project/framework work.
    # This avoids relying on task_debug_log, which may have been captured before
    # _CURRENT_PROJECT was fully resolved in edge cases.
    local central_debug_log
    if [[ -n "${_CURRENT_PROJECT:-}" ]]; then
      central_debug_log="${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug/${SUBAGENT}.log"
    else
      central_debug_log="${KANBAN_ROOT}/logs/debug/${SUBAGENT}.log"
    fi
    prompt="${prompt}

## Progress Logging (verbose mode)

As you work this task, append progress notes to ${task_log_dir}/progress.log AND mirror each line to the central agent debug log. After every significant action — reading a key file, making an edit, running a test, completing a subtask — write a single one-line note using this pattern:

  _msg=\"<description>\"
  echo \"\$(date -Iseconds) \${_msg}\" >> ${task_log_dir}/progress.log
  echo \"\$(date -u +%Y-%m-%dT%H:%M:%S+00:00) [${task_id}] \${_msg}\" >> ${central_debug_log}

Examples of significant actions worth logging:
- \"Read task README and identified files to modify\"
- \"Created feature branch feature/<task-id>\"
- \"Edit 1 of 3: <file> — <what changed>\"
- \"Running bash -n on modified scripts\"
- \"All tests pass; merging into RC\"
- \"Marking task DONE\"

Do not over-log. One note per significant action, not one per line of code. The operator wants to know roughly where you are in the task, not a transcript of your reasoning."
    log "debug gate active: verbose prompt block injected; progress log: ${task_log_dir}/progress.log; central debug log: ${central_debug_log}"
  fi

  # --- Documentation-feedback mode prompt injection ---
  # Gate on the same condition as wake_driver_finalize.sh's training-corpus
  # copy block so that injection and copy are always in sync.
  # _resolve_training_gate handles the legacy PGAI_REASONING_TRACE=1
  # env-var shim internally, so this call preserves backward compat without
  # duplicating the env-var check here. (wake_common.sh carries no
  # prompt-injection logic of its own today — it is a thin sourcing shell
  # over the staged lib files this header describes; the training-copy
  # counterpart of this gate lives in wake_driver_finalize.sh.)
  #
  # _wd_prompt_sans_telemetry and _wd_telemetry_gate_active are declared
  # local by the caller (wd_invoke_task) and written here without a local
  # of the same name, per this file's own dynamic-scoping convention (see
  # _wd_build_governance_line in wake_governance_composer.sh) —
  # wd_invoke_task's invocation call
  # sites read both to decide whether a safeguard-refusal retry applies.
  _wd_prompt_sans_telemetry="$prompt"
  _wd_telemetry_gate_active="$(_resolve_training_gate "${_CURRENT_PROJECT:-}" "${SUBAGENT}")"
  # One-shot deprecation-warning guard propagation (mirrors Stage-2 pattern).
  if [[ "${PGAI_REASONING_TRACE:-0}" == "1" ]]; then
      _REASONING_TRACE_DEPRECATION_WARNED=true
  fi
  if [[ "${_wd_telemetry_gate_active}" == "true" ]]; then
    mkdir -p "${task_artifact_dir}"
    prompt="${prompt}

## Documentation Feedback (documentation-feedback mode)

In addition to your normal work, write a documentation-feedback file to ${task_artifact_dir}/task-notes.md as you work the task. The file has seven sections, written in this order as you reach each phase of the task. It is everything a project's \`[training]\` settings ask of you: the wake copies it into the training corpus after you exit, so write nothing else for them. Write plainly: where the documentation was clear, say so; where it wasn't, say that too — do not paper over a gap with invented certainty.

### 1. Task framing

What the task README and role file told you to do, and what \"done\" looks like per that documentation. Note anywhere the written scope was ambiguous.

### 2. Inputs gathered

For each file you read: file path, what you found in it, why the documentation pointed you there.

### 3. Decisions made

For each meaningful choice the documentation left open: what the instructions didn't specify, what you chose to fill the gap, and why. Not \"I edited file F\" — that's a fact, not a decision. We want \"the docs didn't say X, so I chose Y because Z.\"

### 4. Anti-patterns avoided

What you almost did but the role file's guidance steered you away from — cite the section that caught you. Empty if nothing applied; that is valid signal too.

### 5. Documentation sufficiency

Which parts of the task documentation gave you everything you needed on the first read, and which you had to re-read or guess past. Be specific about which sections were thin.

### 6. Validation strategy

How you decided the work was done. What checks ran. What would have told you it wasn't done yet.

### 7. Documentation feedback (optional, plain)

Where the role file or task README could have made your job easier, or where they already did. Be specific:

- \"The role file's section on X was clear and useful.\" (positive — keep)
- \"I had to make a judgment call on X without guidance; here's what I chose and why.\" (genuine gap — add scaffolding)
- \"The role file said X but I read it as meaning Y; could be clearer.\" (structural — restructure)
- \"The role file's anti-pattern section caught me from doing Z.\" (positive — keep, this works)
- Empty if no notes — that's also valid signal.

Important: do NOT suggest improvements that just amount to more detailed step-by-step instructions. The role files are deliberately principle-based, not procedural. Note what left you uncertain, not what would have removed all uncertainty.

This file is for human documentation-improvement review, not for orchestration. It does not affect task state, queue progression, or your task outcome. Write it as plain feedback on the documentation, not as a performance."
    log "training gate true (${_CURRENT_PROJECT:-<unknown>}/${SUBAGENT}): documentation-feedback prompt block injected; notes file: ${task_artifact_dir}/task-notes.md"
  fi
}

# ---------------------------------------------------------------------------
# _wd_run_provider_with_telemetry_safeguard_retry (private)
#
# CENSUS CLASS: CONVERT — a provider safeguard refusing the optional
# documentation-feedback block carries zero risk to the shipped artefact
# (the block is pure prompt-engineering instrumentation; it contributes
# nothing to whether the task ships), so a refusal here must warn and
# continue, never stop the wake. See docs/blocking-site-census.md.
#
# Wraps _wd_run_provider_and_capture_tokens with a single, narrowly-scoped
# retry: when the documentation-feedback block was injected into `prompt`
# (_wd_telemetry_gate_active == true) AND the first invocation's newly
# logged output carries a safeguard-shaped refusal (_wd_is_safeguard_refusal
# in wake_safeguard_refusal.sh), swap `prompt` for the pre-injection copy
# _wd_build_task_prompt saved in _wd_prompt_sans_telemetry and invoke
# exactly once more. A second
# refusal after the block is already gone is a real provider failure and is
# returned to the caller unchanged — this function never loops.
#
# When the block was not injected (_wd_telemetry_gate_active != true, the
# default path since the training gate is off by default), this function is
# a bare pass-through to _wd_run_provider_and_capture_tokens: no log-offset
# capture, no pattern match, no extra log line — byte-for-byte today's
# behaviour on the default path.
#
# Runs inside the SAME watchdog wrapper wd_invoke_task already applies to a
# single invocation (the background subshell / MAX_TASK_SECONDS polling
# loop below calls this function in place of the bare capture function) —
# the retry therefore draws from the one MAX_TASK_SECONDS budget already in
# force rather than being granted a second budget of its own.
#
# Reads (dynamic scope, same as _wd_run_provider_and_capture_tokens, plus):
#   _wd_telemetry_gate_active _wd_prompt_sans_telemetry LOG_FILE AGENT
#   task_id. Sets PROVIDER_AGENT_EXIT_CODE (global, via
#   _wd_run_provider_and_capture_tokens) and may overwrite `prompt`.
# ---------------------------------------------------------------------------
_wd_run_provider_with_telemetry_safeguard_retry() {
  if [[ "${_wd_telemetry_gate_active:-false}" != "true" ]]; then
    _wd_run_provider_and_capture_tokens
    return 0
  fi

  local _sr_log_offset_before=0
  if [[ -f "${LOG_FILE:-}" ]]; then
    _sr_log_offset_before="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
  fi

  _wd_run_provider_and_capture_tokens
  local _sr_first_exit="${PROVIDER_AGENT_EXIT_CODE:-0}"

  if [[ "$_sr_first_exit" -eq 0 ]]; then
    return 0
  fi

  local _sr_new_output=""
  if [[ -f "${LOG_FILE:-}" ]]; then
    _sr_new_output="$(tail -c +"$(( _sr_log_offset_before + 1 ))" "$LOG_FILE" 2>/dev/null || echo "")"
  fi

  if ! _wd_is_safeguard_refusal "$_sr_new_output"; then
    # Not a safeguard-shaped refusal -- a real provider failure (timeout,
    # network, auth, rate-limit, or anything else). Follow the existing
    # failure path unchanged; PROVIDER_AGENT_EXIT_CODE is already set from
    # the attempt above.
    return 0
  fi

  {
    echo "[$(date -Iseconds)] wake(${AGENT}): task ${task_id}: documentation-feedback block refused by provider safeguard (exit ${_sr_first_exit}) -- verbatim provider output follows:"
    printf '%s\n' "$_sr_new_output"
    echo "[$(date -Iseconds)] wake(${AGENT}): task ${task_id}: skipping trace injection: provider safeguard"
  } | tee -a "$LOG_FILE" >/dev/null
  # CENSUS-ANCHOR: wake-driver-invoke-01
  pgai_emit_error_contract \
    "wake-driver-invoke-telemetry-safeguard" \
    "task ${task_id}: provider refused the first invocation (exit ${_sr_first_exit}) while the optional documentation-feedback block was in the prompt; the refusal text matched a safeguard pattern, not a timeout/network/auth/rate-limit signature." \
    "Read the verbatim provider output logged immediately above in ${LOG_FILE}. The task retried automatically without the block, so no operator action is required unless this recurs across tasks -- if it does, the safeguard's trigger vocabulary has likely shifted again; reword the block built in _wd_build_task_prompt (team/scripts/lib/wake_driver_invoke.sh)." \
    2>&1 | tee -a "$LOG_FILE" >/dev/null

  prompt="$_wd_prompt_sans_telemetry"
  _wd_run_provider_and_capture_tokens
  return 0
}

# ---------------------------------------------------------------------------
# _wd_refresh_project_lock_mtime
# Refresh the held per-project repo lock's mtime from the watchdog poll loop
# so a live mid-task chain's lock stays fresh (lock_provenance.sh, "Held-lock
# refresh"). _project_lock_file/_project_lock_fd are run_project_chain's
# locals via dynamic scope; blank outside a chain means nothing to refresh.
# Never takes or releases the flock, never fails the loop (always 0).
# ---------------------------------------------------------------------------
_wd_refresh_project_lock_mtime() {
  pgai_lock_refresh_held_mtime "${_project_lock_file:-}" "${_project_lock_fd:-}"
}

wd_invoke_task() {
  local prompt
  local _wd_prompt_sans_telemetry=""
  local _wd_telemetry_gate_active="false"
  _wd_build_task_prompt

  # A finalize record names the dispatch whose finalize wrote it; this one has
  # not finalized yet, so the previous record goes before the [A] is written.
  task_finalize_record_clear "$task_dir" \
    || log "task ${task_id}: WARNING: previous finalize record could not be removed ($(task_finalize_record_path "$task_dir" 2>/dev/null)); a DONE this dispatch reaches without finalize would skip the sweep's completion gate"

  # --- State transition: BACKLOG -> WORKING, marker [ ] -> [A] ---
  task_start_ts="$(date -u +%Y%m%dT%H%M%S)"
  mark_backlog "$task_id" "A"
  set_state "$task_status" "WORKING"
  log "task ${task_id}: transitioned to WORKING [A]"

  # --- Wake-stamp resolved model into status.md ## Model section ---
  # The wake script holds the authoritative resolved model string at this point.
  # Stamping here (before spawn) makes the field an execution record written by
  # the party that knows, not a self-report from the agent (which cannot reliably
  # self-identify). Only ## Model is written; all other status fields are preserved.
  stamp_model_field "$task_status" "${selected_model:-<provider default>}"
  log "task ${task_id}: stamped ## Model = '${selected_model:-<provider default>}' into status.md"

  # --- Stale-artifact preservation on re-run ---
  _rotate_artifact() {
    local artifact_path="$1"
    if [[ ! -f "$artifact_path" ]]; then
      return 0
    fi
    local dir base n dest
    dir="$(dirname "$artifact_path")"
    base="$(basename "$artifact_path")"
    n=1
    while [[ -e "${dir}/${base}.previous-RUN-${n}" ]]; do
      (( n++ )) || true
    done
    dest="${dir}/${base}.previous-RUN-${n}"
    mv "$artifact_path" "$dest"
    log "task ${task_id}: stale artifact rotated: ${artifact_path} -> ${dest}"
  }
  _rotate_artifact "${task_artifact_dir}/report.md"
  _rotate_artifact "${task_artifact_dir}/gaps.md"

  log "starting task ${task_id} with subagent ${SUBAGENT} (role ${role})"

  # --- INVARIANT: _CURRENT_PROJECT must be set by run_project_chain ---
  if [[ -z "${_CURRENT_PROJECT:-}" ]]; then
    log "FATAL: _CURRENT_PROJECT unset in process_one_task — outer loop internal state lost"
    log "       Refusing to proceed; this would route a task to the wrong project."
    # Restore PGAI_DEV_TREE_PATH before teardown (TESTER may have overridden it).
    if [[ "$SUBAGENT" == "tester" && -n "${_task_worktree_path:-}" ]]; then
      export PGAI_DEV_TREE_PATH="${_canonical_dev_tree_path}"
    fi
    # Tear down any worktree created for this task before returning.
    if [[ -n "${_task_worktree_path:-}" ]]; then
      teardown_task_worktree "$task_id" "${_canonical_dev_tree_path}" || true
      log "task ${task_id}: worktree torn down (FATAL path)"
    fi
    # Restore canonical dev tree HEAD to resting branch (FATAL early-exit).
    if [[ -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
      set +e
      git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
      set -e
      log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (FATAL early-exit)"
    fi
    return 2
  fi

  # Export per-project/per-task env for subagents; PGAI_TASK_ID names the session for claim_next_bug_id's provenance record (pm-agent/lib/bug_scanner.py).
  export PGAI_PROJECT_ROOT="$(pp_project_root "$_CURRENT_PROJECT")" PGAI_TASK_ID="$task_id"
  export PGAI_PROJECT_NAME="$_CURRENT_PROJECT"
  export PGAI_TASKS_DIR="$(pp_tasks_dir "$_CURRENT_PROJECT")"

  # Positive wake-context signal: "an agent wake is running this", read by
  # team/scripts/cm/release.sh to refuse --force (the operator-only ship
  # override) whenever an agent — CM's own automated release task included —
  # is the one invoking it. Set here as a positive signal, never inferred from
  # a TTY, a parent process name, or an incidental environment variable.
  export PGAI_AGENT_WAKE_ACTIVE=1

  # Activates the git remote-boundary guard for this dispatch; both
  # exported variables (PGAI_AGENT_ROLE, PGAI_GIT_BOUNDARY_GUARD_EVIDENCE_FILE)
  # are documented in wake_guard_wiring.sh, sourced by wake_driver.sh.
  wd_install_git_boundary_guard

  # --- Pre-dispatch /tmp litter snapshot ---
  _litter_snapshot_file="$(wake_bracket_pre_dispatch "$task_id")"

  # --- Pre-task pollution snapshot ---
  # Snapshot the canonical dev tree's porcelain state before the agent runs.
  # The diff is compared post-task to detect files left behind.
  # Best-effort: any failure here logs a WARNING and does not block the task.
  _pollution_pre_snapshot=""
  _pollution_snapshot_ok=false
  {
    if [[ -d "${_sweep_canonical_tree:-}" ]]; then
      _pollution_pre_snapshot="$(git -C "${_sweep_canonical_tree}" status --porcelain=v1 --untracked-files=all 2>/dev/null)"
      _pollution_snapshot_ok=true
    else
      # Distinguish intentional absence (git_mode=none workflow / no dev_tree_path
      # configured) from a misconfiguration (git_mode=rw project with missing path).
      # Workflows with git_mode=none have no dev tree by design; skip silently at
      # info level. Reserve WARNING for git-workflow projects whose configured
      # dev_tree_path does not resolve.
      # Use WF_MANIFEST_GIT_MODE from the plugin loaded for this task when
      # available; otherwise attempt a best-effort load of PP_workflow_type.
      local _sweep_wf_git_mode="${WF_MANIFEST_GIT_MODE:-}"
      if [[ -z "$_sweep_wf_git_mode" && -n "${PP_workflow_type:-}" ]]; then
        local _sweep_wf_load_exit=0
        set +e
        wf_load_plugin "${PP_workflow_type}"
        _sweep_wf_load_exit=$?
        set -e
        if [[ $_sweep_wf_load_exit -eq 0 ]]; then
          _sweep_wf_git_mode="$(wf_git_mode)"
        fi
      fi
      if [[ -z "${_sweep_canonical_tree:-}" || "${_sweep_wf_git_mode:-rw}" == "none" ]]; then
        log "INFO: pollution sweep: no dev tree configured for this project (${PP_workflow_type:-release} workflow; git_mode=${_sweep_wf_git_mode:-unknown}); snapshot not applicable"
      else
        log "WARNING: pollution sweep: canonical dev tree '${_sweep_canonical_tree}' not found; pre-task snapshot skipped"
      fi
    fi
  } || {
    log "WARNING: pollution sweep: pre-task snapshot failed for ${task_id}; sweep disabled for this task"
    _pollution_snapshot_ok=false
  }

  # --- Invoke provider CLI (through the five-function provider interface) ---
  # _wd_run_provider_with_telemetry_safeguard_retry calls
  # _wd_run_provider_and_capture_tokens (provider_dispatch then
  # provider_parse_output) and sets PROVIDER_AGENT_EXIT_CODE to the CLI exit
  # code; when the documentation-feedback block is in the prompt and the
  # first attempt is refused by a provider safeguard, it retries exactly
  # once with the block omitted before returning. On the default path (no
  # block injected) it is a bare pass-through — see the function's own
  # header above for the full contract.
  #
  # Per-task hard preemption: when the armed limit is > 0 the invocation (retry
  # included -- it draws from this budget, never a second one) runs in a
  # background subshell launched under `set -m`. The armed limit is
  # run_project_chain's per-dispatch _task_timeout_budget_seconds -- the
  # smallest of MAX_TASK_SECONDS and the remaining project and batch budgets
  # (wake_task_budget.sh), so a task never outlives the batch that dispatched
  # it -- or MAX_TASK_SECONDS alone when no chain set one. Job control gives that subshell
  # -- and so the provider CLI and every tool it spawns -- its own process group
  # (PGID == the subshell's pid); an external re-exec could not carry this
  # shell's functions and dynamic scope into the child. At the deadline the
  # watchdog SIGTERMs the group, polls the GROUP (not the wrapper pid) through
  # KILL_GRACE_SECONDS, and SIGKILLs the group if anything survived. The
  # wrapper's no-op SIGTERM handler lets token capture and the exit-code file
  # (124 on a kill) finish after the group SIGTERM; a handler, unlike an ignored
  # signal, resets to default across exec, so the CLI itself still dies. The
  # _provider_* variables are process_one_task's locals, read by finalize's
  # wd_drain_provider_process_group to hold finalization until the group is
  # empty. While the poll loop runs it also refreshes the held project
  # lock's mtime every _PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS
  # (_wd_refresh_project_lock_mtime above), so a live chain's lock never
  # ages into lock_provenance.sh's overdue-holder classification while a
  # task is running. An armed limit of 0 (every ceiling disabled) disables
  # all of it. process_one_task's _provider_timeout_seconds records the armed
  # limit so finalize reports the limit that fired, not MAX_TASK_SECONDS.
  # DISPATCH-LEDGER-BEGIN — sentinel comment marking the span a unit test
  # (team/tests/unit/test_wake_dispatch_ledger.py) extracts verbatim, through
  # the watchdog block below to DISPATCH-LEDGER-END, to exercise the ledger
  # writes with a stub provider. Purely a test-extraction anchor.
  # --- Dispatch ledger: open this task's span of the batch log ---
  # The provider's own stdout/stderr goes to LOG_FILE (the firing-scoped batch
  # log), not to the task's logs/ directory. Record which batch log this
  # dispatch writes and the byte offset its output starts at, so a later
  # reader (OVERWATCH's transient-error check) can tail exactly this task's
  # provider output. Best-effort: a ledger write failure is narrated and the
  # dispatch proceeds — the ledger informs a diagnosis, it never gates one.
  local _dispatch_log_offset_start=0
  if [[ -f "${LOG_FILE:-}" ]]; then
    _dispatch_log_offset_start="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
  fi
  if dispatch_record_append "$task_dir" "dispatched" "$LOG_FILE" "$_dispatch_log_offset_start"; then
    log "task ${task_id}: dispatch ledger: batch log ${LOG_FILE} from byte ${_dispatch_log_offset_start} (${task_log_dir}/dispatch.jsonl)"
  else
    log "WARNING: task ${task_id}: dispatch ledger write failed (${task_log_dir}/dispatch.jsonl); the transient-error check will skip this task until its next dispatch"
  fi

  # WATCHDOG-INVOKE-BEGIN
  PROVIDER_AGENT_EXIT_CODE=0
  _task_killed=false
  _provider_pgid=""; _provider_deadline_epoch=""; _provider_group_sigkilled=false
  _provider_timeout_seconds="${_task_timeout_budget_seconds:-${MAX_TASK_SECONDS:-0}}"
  [[ "${_provider_timeout_seconds}" =~ ^[0-9]+$ ]] || _provider_timeout_seconds="${MAX_TASK_SECONDS:-0}"
  if [[ "${_provider_timeout_seconds}" -gt 0 ]]; then
    local _limit="${_provider_timeout_seconds}" _grace="${KILL_GRACE_SECONDS:-30}" _timed_out=false
    local _limit_note=""
    if [[ "${_limit}" != "${MAX_TASK_SECONDS:-0}" ]]; then
      _limit_note="; capped from max_task_seconds=${MAX_TASK_SECONDS:-0}s by the remaining project/batch budget"
    fi
    log "task ${task_id}: per-task timeout active: ${_limit}s (kill grace: ${_grace}s; held lock mtime refreshed every ${_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS:-60}s${_limit_note})"

    # Temp file to receive the provider invocation exit code from the subshell.
    local _exit_code_file
    _exit_code_file="$(pgai_mktemp pgai_invoke_exit 2>/dev/null || mktemp "${PGAI_AGENT_KANBAN_TEMP_DIR}/pgai_invoke_exit.XXXXXX" 2>/dev/null || echo "")"

    # Job control on for the launch only: the subshell leads a new process group.
    set -m
    (
      set +e; trap ':' TERM
      _wd_run_provider_with_telemetry_safeguard_retry
      _sub_exit="${PROVIDER_AGENT_EXIT_CODE:-$?}"
      [[ -n "${_exit_code_file:-}" ]] && echo "${_sub_exit}" > "${_exit_code_file}"
    ) &
    _provider_pgid=$!
    set +m
    # Expose the group to the wake's exit trap (script_common.sh's
    # cleanup_on_exit): a SIGTERM/SIGINT/SIGHUP landing on the wake from here
    # on stops this group -- TERM, KILL_GRACE_SECONDS, KILL -- before the
    # pre-hook releases the project lock. Cleared once the group is empty
    # (below, or by finalize's drain) so a recycled pgid is never signalled.
    CLEANUP_ON_EXIT_PROCESS_GROUP="$_provider_pgid"
    # Watchdog: poll the wrapper about once a second until the wall clock
    # (_wd_epoch_now, wake_task_budget.sh) reaches an absolute deadline taken
    # at launch. Comparing against the clock rather than counting iterations
    # keeps the deadline honest under load: a slow iteration costs elapsed
    # time, never extra iterations. The wrapper, not the group, marks
    # completion: a stray left in the group is the finalize drain's to reap.
    # The held project lock's mtime is refreshed every _lock_refresh_every
    # seconds of wall-clock time; a non-positive interval disables it.
    local _lock_refresh_every="${_PGAI_LOCK_HELD_REFRESH_INTERVAL_SECONDS:-60}"
    [[ "$_lock_refresh_every" =~ ^[0-9]+$ ]] || _lock_refresh_every=60
    local _launch_epoch _deadline_epoch _now_epoch _next_refresh_epoch
    _launch_epoch="$(_wd_epoch_now)"
    _deadline_epoch=$(( _launch_epoch + _limit ))
    _next_refresh_epoch=$(( _launch_epoch + _lock_refresh_every ))
    while kill -0 "$_provider_pgid" 2>/dev/null; do
      _now_epoch="$(_wd_epoch_now)"
      (( _now_epoch < _deadline_epoch )) || break   # deadline reached
      if (( _lock_refresh_every > 0 && _now_epoch >= _next_refresh_epoch )); then
        _wd_refresh_project_lock_mtime
        _next_refresh_epoch=$(( _now_epoch + _lock_refresh_every ))
      fi
      sleep 1
    done

    if kill -0 "$_provider_pgid" 2>/dev/null; then
      # At the deadline: SIGTERM the group, then poll the GROUP through the
      # grace window -- itself a wall-clock deadline, for the same reason.
      _provider_deadline_epoch="$(_wd_epoch_now)"
      log "task ${task_id}: timeout fired after $(( _provider_deadline_epoch - _launch_epoch ))s (limit ${_limit}s); sending SIGTERM to provider process group ${_provider_pgid}"
      kill -TERM -- "-${_provider_pgid}" 2>/dev/null || true
      _timed_out=true
      local _grace_deadline_epoch=$(( _provider_deadline_epoch + _grace ))
      while kill -0 -- "-${_provider_pgid}" 2>/dev/null; do
        (( $(_wd_epoch_now) < _grace_deadline_epoch )) || break
        sleep 1
      done
      if kill -0 -- "-${_provider_pgid}" 2>/dev/null; then
        log "task ${task_id}: grace period (${_grace}s) expired; provider process group ${_provider_pgid} still alive; sending SIGKILL to the group"
        kill -KILL -- "-${_provider_pgid}" 2>/dev/null || true
        _provider_group_sigkilled=true
      fi
    fi

    # Collect the wrapper (suppress "Killed" noise on stderr).
    wait "$_provider_pgid" 2>/dev/null || true
    kill -0 -- "-${_provider_pgid}" 2>/dev/null || CLEANUP_ON_EXIT_PROCESS_GROUP=""

    # Read back the provider exit code (or 124 on timeout).
    local _raw_exit=""
    if [[ -n "${_exit_code_file:-}" && -f "${_exit_code_file}" ]]; then
      _raw_exit="$(cat "${_exit_code_file}" 2>/dev/null || echo "")"
      rm -f "${_exit_code_file}" 2>/dev/null || true
    fi
    if [[ "${_timed_out}" == "true" ]]; then
      PROVIDER_AGENT_EXIT_CODE=124
      _task_killed=true
    elif [[ -n "${_raw_exit}" && "${_raw_exit}" =~ ^[0-9]+$ ]]; then
      PROVIDER_AGENT_EXIT_CODE="${_raw_exit}"
    else
      # Could not read exit code from temp file — treat as unknown failure.
      PROVIDER_AGENT_EXIT_CODE=1
    fi
  else
    # Timeout disabled — invoke directly (original path).
    set +e
    _wd_run_provider_with_telemetry_safeguard_retry
    set -e
  fi
  # WATCHDOG-INVOKE-END

  agent_exit="${PROVIDER_AGENT_EXIT_CODE:-0}"

  # --- Dispatch ledger: close the span now that the provider has exited ---
  local _dispatch_log_offset_end=""
  if [[ -f "${LOG_FILE:-}" ]]; then
    _dispatch_log_offset_end="$(wc -c < "$LOG_FILE" 2>/dev/null || echo "")"
  fi
  dispatch_record_append "$task_dir" "returned" "$LOG_FILE" "$_dispatch_log_offset_start" \
    "$_dispatch_log_offset_end" "$agent_exit" \
    || log "WARNING: task ${task_id}: dispatch ledger close failed (${task_log_dir}/dispatch.jsonl); the open record still names the batch log"
  # DISPATCH-LEDGER-END

  return 0
}
