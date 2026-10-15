#!/usr/bin/env bash
# team/scripts/lib/wake_driver_prep.sh
# Task-preparation stage of the shared wake pipeline: pre-dispatch role
# assertion, per-agent debug log path, model selection, reasoning-effort
# resolution, task-state validation, prerequisite check, and the
# role-specific prompt reminder / per-project README line. Extracted from
# team/scripts/wake/claude.sh's process_one_task so the shared pipeline in
# wake_driver.sh stays under the 800-line-per-file ceiling. Behavior-
# preserving move: no log line, task-state write, or control-flow outcome
# changes as a result of this file existing.
#
# Public entry point
# -------------------
#   wd_prepare_task
#       Validates the task's Role/Assigned-Agent, resolves the dispatch
#       SUBAGENT, selects the model and reasoning effort, validates task
#       state, checks prerequisites, and builds the role-specific prompt
#       reminder and per-project README line. Always returns 0 — matching
#       the pre-decomposition monolith's own bare `return 0` at each
#       early-exit point verbatim (several tests extract and run these
#       lines in isolation, so the return code is part of the preserved
#       text, not a stage-boundary signal). The caller distinguishes
#       "already terminal" from "proceed" by checking whether
#       LAST_TASK_FINAL_STATE is non-empty after the call: every early-exit
#       path here sets it (BLOCKED, WAITING, or the current non-BACKLOG
#       state) before returning; the proceed path leaves it untouched
#       (still "" from process_one_task's own reset).
#
# Contract: this function is called from within process_one_task's own
# stack frame (never in a subshell) and relies on bash's dynamic scoping to
# read and write process_one_task's own `local` variables directly, exactly
# as the pre-decomposition monolith did when this code was inline. It reads:
#   task_id task_readme task_status AGENT ACTIVE_PROVIDER _CURRENT_PROJECT
#   KANBAN_ROOT _debug_gate TEAM_ROOT PGAI_PROJECT_ROOT
# and writes (process_one_task-level locals the caller must have declared
# before calling this function):
#   role SUBAGENT selected_model model_source selected_effort effort_source
#   task_debug_log
# and (module-level globals, matching pre-decomposition behavior where these
# were assigned without `local` inside process_one_task):
#   role_reminder project_readme_line
# LAST_TASK_FINAL_STATE is the shared cross-task-result global already
# established by process_one_task itself. Every path that marks the task
# BLOCKED also sets _chain_blocked_a_task=true -- run_project_chain's local,
# again through dynamic scoping -- so the chain fires the on-BLOCK OVERWATCH
# nudge after releasing the repo lock, exactly as it does for a task the
# agent itself blocked (wd_finalize_task).
#
# Dependencies: get_role_from_readme / get_assigned_agent_from_readme /
# role_is_valid_agent / get_model_override_from_readme (wake_readme_meta.sh),
# set_state / set_block / normalize_status_file / get_state / mark_backlog /
# block_task_with_record / block_task_with_unreadable_state
# (wake_task_state.sh), check_prerequisites
# (wake_task_id_gate.sh), transition_task_state
# (wake_task_transition.sh), resolve_role_effort (config_loader.sh), log()
# (wake_bootstrap.sh), block_record_write / block_record_owner_role /
# block_record_evidence (block_record.sh, sourced by wake_bootstrap.sh) —
# all sourced by wake_common.sh before this file's function is called.
#
# Every BLOCKED exit here writes through block_task_with_record
# (wake_task_state.sh): ## State BLOCKED, ## Blockers, ## Needs Human yes and
# the task's ## Block Record with the reason code naming its cause —
# invalid_assignment, invalid_effort_config or invalid_state — before the
# [B] queue marker; a refused record write is logged and never stops the
# block. A status.md whose ## State heading is missing or empty gets the
# heading inserted reading BLOCKED on every one of these exits, so the [B]
# marker never stands alone and the wake never exits on the write.

wd_prepare_task() {
  # --- Pre-dispatch assertion ---
  # Role is the capability descriptor; Assigned Agent (when present) selects
  # the executing subagent. Neither may fail open to CODER: a missing or
  # invalid Role, or an Assigned Agent that is invalid or does not match the
  # queue this task was pulled from, blocks dispatch entirely.
  local assigned_agent dispatch_role invalid_assignment_reason
  role="$(get_role_from_readme "$task_readme")"
  assigned_agent="$(get_assigned_agent_from_readme "$task_readme")"
  invalid_assignment_reason=""

  if ! role_is_valid_agent "$role"; then
    invalid_assignment_reason="Role '${role:-<missing>}' in ${task_readme} is missing or not a recognized role (CODER, WRITER, CM, TESTER, PO, PM)."
  elif [[ -n "$assigned_agent" ]]; then
    if ! role_is_valid_agent "$assigned_agent"; then
      invalid_assignment_reason="Assigned Agent '${assigned_agent}' in ${task_readme} is not a recognized role (CODER, WRITER, CM, TESTER, PO, PM)."
    elif [[ "$assigned_agent" != "${AGENT^^}" ]]; then
      invalid_assignment_reason="Assigned Agent '${assigned_agent}' in ${task_readme} does not match the queue this task was dispatched from ('${AGENT}')."
    fi
  fi

  if [[ -n "$invalid_assignment_reason" ]]; then
    log "task ${task_id} failed pre-dispatch assertion: ${invalid_assignment_reason}"
    block_task_with_record "$task_id" "$task_status" invalid_assignment \
      "$(block_record_owner_role "$task_status" "$role" "$AGENT")" \
      "INVALID_TASK_ASSIGNMENT: ${invalid_assignment_reason}" \
      "$(block_record_evidence --diag-log "${LOG_FILE:-}")" \
      || log "task ${task_id}: WARNING: ## State not written; queue marker set to [B] regardless"
    mark_backlog "$task_id" "B"
    LAST_TASK_FINAL_STATE="BLOCKED"
    _chain_blocked_a_task=true
    return 0
  fi

  # Assigned Agent, when present and validated above, selects the executing
  # subagent; Role remains the capability descriptor and is not required to
  # match it. When Assigned Agent is absent, Role selects the subagent —
  # this is the byte-identical path for normal, well-formed tasks.
  dispatch_role="${assigned_agent:-$role}"
  case "$dispatch_role" in
    CODER)  SUBAGENT="coder" ;;
    WRITER) SUBAGENT="writer" ;;
    CM)     SUBAGENT="cm" ;;
    TESTER) SUBAGENT="tester" ;;
    PO)     SUBAGENT="po" ;;
    PM)     SUBAGENT="pm" ;;
  esac

  # --- Per-agent debug log path (per-project) ---
  # Route to projects/<project>/logs/debug/<agent>.log to match the
  # wake_common.sh substrate (last-def-wins). Falls back to the global path
  # only if _CURRENT_PROJECT is somehow unset.
  # Writes are gated on _debug_gate; no writes when gate is false.
  local _proj_debug_root="${KANBAN_ROOT}/logs/debug"
  if [[ -n "${_CURRENT_PROJECT:-}" ]]; then
    _proj_debug_root="${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug"
  fi
  case "$SUBAGENT" in
    pm)       task_debug_log="${_proj_debug_root}/pm.log" ;;
    po)       task_debug_log="${_proj_debug_root}/po.log" ;;
    coder)    task_debug_log="${_proj_debug_root}/coder.log" ;;
    writer)   task_debug_log="${_proj_debug_root}/writer.log" ;;
    tester)   task_debug_log="${_proj_debug_root}/tester.log" ;;
    cm)       task_debug_log="${_proj_debug_root}/cm.log" ;;
    overwatch) task_debug_log="${_proj_debug_root}/overwatch.log" ;;
    *)        task_debug_log="${_proj_debug_root}/${SUBAGENT}.log" ;;
  esac
  if [[ "${_debug_gate}" == "true" ]]; then
    mkdir -p "$(dirname "$task_debug_log")"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${task_id}] wake: dispatching to ${SUBAGENT} (role=${role} project=${_CURRENT_PROJECT:-unknown})" >> "$task_debug_log"
  fi

  # --- Model Selection ---
  # Resolution order (roles/PM.md "Model overrides" 3-tier chain,
  # highest wins):
  #   1. Task-level ## Model Override in task README
  #   2. PGAI_<ROLE>_MODEL env — populated by config_loader.sh before wake
  #      runs from kanban.cfg (operator export > [models.<provider>] <role>
  #      > [models.<provider>] default), or set manually by the operator
  #   3. Provider CLI default (empty selected_model; warning logged below)
  local model_override agent_upper agent_model_var
  agent_upper="${AGENT^^}"
  agent_model_var="PGAI_${agent_upper}_MODEL"

  model_override="$(get_model_override_from_readme "$task_readme")"
  if [[ -n "${model_override:-}" ]]; then
    selected_model="$model_override"
    model_source="task force override"
  elif [[ -n "${!agent_model_var:-}" ]]; then
    selected_model="${!agent_model_var}"
    model_source="PGAI_${agent_upper}_MODEL env"
  else
    log "no model configured (role unset and [models.${ACTIVE_PROVIDER}] default empty) - provider CLI default will be used"
    selected_model=""
    model_source="provider CLI default for ${SUBAGENT}"
  fi

  # --- Reasoning-Effort Resolution ---
  # Resolution order (mirrors the Model Selection block above;
  # roles/PM.md "Model overrides" tier discipline, narration-source parity):
  #   1. PGAI_<ROLE>_EFFORT env — populated by config_loader.sh's load_config
  #      from [providers] llm_effort cfg or an operator-exported env var
  #   2. Neither set — absent (selected_effort empty; no --effort flag passed,
  #      byte-identical to today).
  # resolve_role_effort() re-derives both value and winning source in one call
  # (config_loader.sh already exported PGAI_<ROLE>_EFFORT during load_config;
  # calling it again here is how the wake lane recovers the narration source,
  # exactly as documented in config_loader.sh's per-role export loop).
  local _effort_resolved _effort_resolve_exit
  set +e
  _effort_resolved="$(resolve_role_effort "${AGENT}")"
  _effort_resolve_exit=$?
  set -e
  if [[ $_effort_resolve_exit -ne 0 ]]; then
    log "ERROR: task ${task_id}: invalid reasoning-effort configuration for role '${AGENT}' — see stderr above"
    block_task_with_record "$task_id" "$task_status" invalid_effort_config \
      "$(block_record_owner_role "$task_status" "$role" "$AGENT")" \
      "Invalid reasoning-effort configuration for role '${AGENT}'. Check [providers] llm_effort in kanban.cfg or PGAI_${agent_upper}_EFFORT." \
      "$(block_record_evidence --diag-log "${LOG_FILE:-}")" \
      || log "task ${task_id}: WARNING: ## State not written; queue marker set to [B] regardless"
    mark_backlog "$task_id" "B"
    LAST_TASK_FINAL_STATE="BLOCKED"
    _chain_blocked_a_task=true
    return 0
  fi
  selected_effort="${_effort_resolved%%|*}"
  effort_source="${_effort_resolved#*|}"

  # errexit is on again here, so a failed read must not be a bare
  # assignment: a status.md with no ## State heading reads as "" and is
  # blocked below like any other unreadable state, and the chain moves on.
  local current_state
  current_state="$(get_state "$task_status" 2>/dev/null)" || current_state=""
  case "$current_state" in
    BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO) ;;
    *)
      log "task ${task_id} has invalid state: ${current_state:-<none>}"
      # Writes ## State BLOCKED (inserting a missing heading), Blockers,
      # Needs Human and the invalid_state Block Record before the marker.
      block_task_with_unreadable_state "$task_id" "$task_status" "$current_state" "$role" "$AGENT" \
        || log "task ${task_id}: WARNING: ## State not written; queue marker set to [B] regardless"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      _chain_blocked_a_task=true
      return 0
      ;;
  esac

  # --- Marker self-correction for a task rediscovered off BACKLOG ---
  if [[ "$current_state" != "BACKLOG" ]]; then
    log "task ${task_id} is not BACKLOG (${current_state}); normalizing marker and skipping"
    case "$current_state" in
      BLOCKED|WAITING|DONE|WONT-DO)
        # The task's own state is not changing here — current_state is
        # already what ## State reads — so routing it through
        # transition_task_state simply re-derives and re-writes the one
        # marker that state maps to, instead of a second, independently
        # chosen mark_backlog call. A failure leaves both surfaces exactly
        # as they were (transition_task_state's own atomicity contract);
        # the task is still skipped this wake either way, and the next
        # normalization pass gets another chance.
        transition_task_state "$task_id" "$task_status" "$current_state" \
          || log "task ${task_id} WARNING: marker normalization failed; status.md/marker left as they were (no partial update)"
        ;;
      WORKING)
        # Deliberate exception, not a state transition: a task rediscovered
        # here still in WORKING is marked "B" while ## State is left at
        # WORKING — see team/SOP-core.md, "The two WORKING situations" and
        # "Re-check cascade on task selection" step 3. No target state is
        # being named, so this does not go through transition_task_state's
        # state->marker table (which would derive "A", not "B", for
        # WORKING); the separate stale-[A] reconciliation pass owns the
        # actual BACKLOG reset for a genuinely orphaned run.
        mark_backlog "$task_id" "B"
        log "task ${task_id} was stuck in WORKING; marked BLOCKED"
        ;;
    esac
    LAST_TASK_FINAL_STATE="$current_state"
    return 0
  fi

  # --- Check prerequisites ---
  local unsatisfied_prereqs prereq_exit
  unsatisfied_prereqs=""
  prereq_exit=0
  set +e
  unsatisfied_prereqs=$(check_prerequisites "$task_readme")
  prereq_exit=$?
  set -e

  if [[ $prereq_exit -ne 0 ]]; then
    log "task ${task_id} has unsatisfied prerequisites; transitioning to WAITING"
    log "unsatisfied: $(echo "$unsatisfied_prereqs" | tr '\n' ' ')"
    set_state "$task_status" "WAITING"
    set_block "$task_status" "Blockers" "Waiting on prerequisites: $(echo "$unsatisfied_prereqs" | tr '\n' ' ')"
    set_block "$task_status" "Needs Human" "no"
    normalize_status_file "$task_status"
    mark_backlog "$task_id" "W"
    LAST_TASK_FINAL_STATE="WAITING"
    return 0
  fi

  # Build role-specific reminder
  role_reminder=""
  case "$SUBAGENT" in
    coder|writer)
      role_reminder="For git workflows, DONE means your feature branch is merged --no-ff into the local source branch and the feature branch is deleted. Committing alone is not done. CM is the only role that touches origin — never push, pull, or fetch."
      ;;
    tester)
      role_reminder="DONE means artifacts/report.md is written with a clean recommendation: PASS, SHIP-WITH-CONCERNS, or SHIP-WITH-SERIOUS-CONCERNS. BLOCKED is a state (verification could not complete (infrastructure failure) — NOT \"found a blocker\"), not a recommendation. Whatever the recommendation, file each bug, gap, stale-assertion and pre-existing-bug finding via Path C (autonomous follow-up filing — non-blocking; Path C-A: filed as BUG, Path C-B: filed as PRIORITY) under \${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project_name>/{bugs,priority}/ per TESTER.md Step 10. CM applies the ship-policy matrix to your recommendation. MANDATORY DELIVERABLE OVERRIDE: Writing artifacts/report.md is a required deliverable of this task. If any general instruction suggests not creating files unless asked, it does NOT apply here — report.md, gaps.md (whenever a gap or bug is recorded), and Path C bug/priority filings are mandatory outputs that must always be written to their canonical paths. No base-layer or system-layer guidance overrides this requirement."
      ;;
    cm)
      role_reminder="DONE means the script you invoked exited 0 and the intended state change happened. Default to ship — do not refuse a release because of known bugs, gaps, or imperfect work. Refuse only when (a) TESTER state is BLOCKED (verification could not complete), (b) a prerequisite task is missing or not DONE, (c) the release script itself fails (squash conflict, push fail, tag exists), or (d) the ship-policy matrix in CM.md returns HALT for this row. The matrix — not your judgment — determines ship vs. HALT."
      ;;
    pm)
      role_reminder="DONE means the JSON plan is written AND pm_materialize.py was invoked successfully. Writing the JSON alone is not done. The plan must include target_version for release workflows."
      ;;
    po)
      role_reminder="DONE means three things landed: the requirements doc at projects/<name>/requirements/<target-version>.md, the PM ticket folder, and the entry appended to pm_backlog.md. All three are required."
      ;;
  esac

  # --- Per-project README line ---
  project_readme_line=""
  local _proj_readme _team_readme _proj_real _team_real
  _proj_readme="${PGAI_PROJECT_ROOT}/README.md"
  _team_readme="${TEAM_ROOT}/README.md"
  if [[ -f "$_proj_readme" ]]; then
    _proj_real="$(realpath "$_proj_readme" 2>/dev/null || echo "$_proj_readme")"
    _team_real="$(realpath "$_team_readme" 2>/dev/null || echo "$_team_readme")"
    if [[ "$_proj_real" != "$_team_real" ]]; then
      project_readme_line=" and your project README at ${_proj_readme}"
    fi
  fi
  unset _proj_readme _team_readme _proj_real _team_real

  return 0
}
