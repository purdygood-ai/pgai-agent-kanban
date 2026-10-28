#!/usr/bin/env bash
# team/scripts/lib/cm/release_verification_retry.sh
#
# CM release verification-retry stage: what Step 4d does when the RC's
# verification did not run, or when the TESTER task's state is one the
# ship-policy matrix has no row for. None of these stops the release, under
# either ship posture. Sourced by release_ship_policy.sh itself (below its
# double-sourcing guard), so a caller that sources only that file resolves
# every function here; split into its own file so release_ship_policy.sh
# stays under the shared per-file size ceiling (docs/coding-standards.md).
#
# Verification did not run (the TESTER task reads BLOCKED, or reads DONE
# with no artifacts/report.md, while a TESTER task exists for the RC)
# ---------------------------------------------------------------------
#   First time for this RC: the matrix decides REQUEUE-VERIFICATION. The
#     TESTER task is returned to BACKLOG with its block cause in its
#     README's ## Triage Note, and the RC's retry record is written
#     (pgai_agent_kanban.cm.verification_retry requeue). No HALT file is
#     written and nothing ships: release.sh exits 4. When the release runs
#     inside its own CM wake (PGAI_TASK_ID names a CM task of this project),
#     that task is set to WAITING, so the wake runs the release again once
#     the requeued TESTER task finishes.
#   After the RC's one requeue is spent (the record exists, whichever lane
#     wrote it -- the decision wake returns a BLOCKED TESTER task the same
#     way, and closes it WONT-DO when it blocks again), while the requeued
#     TESTER task is still BACKLOG, WORKING or WAITING: verification is
#     still pending. The matrix decides VERIFICATION-PENDING and release.sh
#     exits 4 again, writing nothing -- no requeue, no HALT file, no
#     release-state.md change, no inbox entry. Run inside its own CM wake,
#     that task is set to WAITING again, exactly as on the requeue.
#   After the requeue is spent and the TESTER task is BLOCKED or WONT-DO
#     again, or DONE with no report: the release ships
#     SHIP-NON-FUNCTIONAL. Its notes' ## Known Issues opens with the
#     non-functional warning and the "Verification did not run" line, and
#     once the release has shipped (release_finalize.sh's Step 12 post-write
#     block, through cm_release_append_auto_ship_events) one operator inbox
#     entry names the TESTER task and its block cause.
#   A requeue that cannot be written (the module refused or failed) is
#     narrated and takes the after-the-requeue path: a release is never
#     stopped because its retry could not be recorded.
#
# Unrecognised TESTER state (a TESTER task was found, its state is neither
# BLOCKED nor DONE -- WORKING, WONT-DO, a typo -- and the RC's requeue is
# not spent), and the no-row-matched catch-all when TESTER values reach it
# ---------------------------------------------------------------------
#   The release takes the SHIP-WITH-CONCERNS row (SHIP-KNOWN-BUGS,
#   release-notes Status KNOWN-BUGS) and files one bug quoting the state (or
#   the field values) through _cm_ship_policy_file_input_bug.
#
# Public entry points
# --------------------
#   cm_verification_retry_read
#       Sets _VERIFICATION_RETRY (available|spent) and the record's
#       _VERIFICATION_RETRY_TASK / _CASE / _CAUSE for ACTIVE_RC.
#   cm_verification_state_pending <tester_state>
#       Pure. Succeeds when the state is BACKLOG, WORKING or WAITING (any
#       case): a requeued TESTER task that has not finished yet.
#   cm_verification_case <tester_state> <task_found> <report_path>
#       Pure. Prints tester-blocked, report-missing, verification-pending,
#       or nothing.
#   cm_verification_retry_apply_decision
#       Step 4d, right after cm_ship_policy. Carries out a
#       REQUEUE-VERIFICATION decision (sets _VERIFICATION_REQUEUED=yes on a
#       written requeue), narrates a VERIFICATION-PENDING decision (writes
#       nothing but this release's own CM task state), marks an
#       after-the-requeue NON-FUNCTIONAL ship
#       (_VERIFICATION_UNVERIFIED=yes), and files the unrecognised-state bug.
#   cm_verification_floor_unreadable_halt
#       Step 4d, after cm_ship_posture_apply_decision. A matrix HALT whose
#       row is not trigger 2 or 3 floors to SHIP-KNOWN-BUGS and files a bug.
#   cm_verification_retry_resolve_known_issues
#       Step 4d, after the known-issue resolvers. Sets
#       _KNOWN_ISSUE_NOTES_BLOCK on an unverified ship (both notes paths
#       place it under ## Known Issues).
#   cm_verification_retry_after_ship
#       Called by cm_release_append_auto_ship_events (release_ship_posture.sh)
#       after the release has shipped: writes the unverified ship's operator
#       inbox entry. Never fails the release.
#
# Globals read: PROJECT_NAME, ACTIVE_RC, KANBAN_ROOT, _CM_PROJECT_ROOT,
# _CM_DECIDE_ONLY, PGAI_TASK_ID, and the _TESTER_* values Step 4d sets.
# Dependencies (sourced before these run): pp_run_ops (pp_run_ops.sh),
# pgai_emit_error_contract and pgai_format_error_contract (error_contract.sh),
# _cm_ship_policy_file_input_bug
# (release_ship_policy.sh). No inline Python: every Python call is a module.

# Guard against double-sourcing.
if declare -F cm_verification_retry_apply_decision >/dev/null 2>&1; then
    return 0
fi

_VERIFICATION_RETRY="available"
_VERIFICATION_RETRY_TASK=""
_VERIFICATION_RETRY_CASE=""
_VERIFICATION_RETRY_CAUSE=""
_VERIFICATION_CASE=""
_VERIFICATION_CAUSE=""
_VERIFICATION_REQUEUED="no"
_VERIFICATION_UNVERIFIED="no"

# Helper: one dual-channel notice for a site that warns and continues.
# Usage: _cm_verification_notice <error_key> <message> <remedy>
# Through the shared emitter (which also records the project's latest
# refusal), except under --decide-only, which writes nothing: the same
# three-line block is only formatted there, on stderr.
_cm_verification_notice() {
  if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
    pgai_format_error_contract "$1" "$2" "$3" >&2
  else
    pgai_emit_error_contract "$1" "$2" "$3" "$PROJECT_NAME"
  fi
}

# Helper: read the RC's retry record into the _VERIFICATION_RETRY* globals.
# A record that cannot be read through the module reads as available; the
# requeue's own check (it refuses to write over an existing record) still
# keeps the requeue to once per RC.
cm_verification_retry_read() {
  _VERIFICATION_RETRY="available"
  _VERIFICATION_RETRY_TASK="" _VERIFICATION_RETRY_CASE="" _VERIFICATION_RETRY_CAUSE=""
  local line
  while IFS= read -r line; do
    case "$line" in
      retry_spent=yes) _VERIFICATION_RETRY="spent" ;;
      tester_task=*)   _VERIFICATION_RETRY_TASK="${line#tester_task=}" ;;
      case=*)          _VERIFICATION_RETRY_CASE="${line#case=}" ;;
      cause=*)         _VERIFICATION_RETRY_CAUSE="${line#cause=}" ;;
    esac
  done < <(pp_run_ops pgai_agent_kanban.cm.verification_retry status \
             --project-root "$_CM_PROJECT_ROOT" --version "$ACTIVE_RC" 2>/dev/null || true)
  if [[ "$_VERIFICATION_RETRY" == "spent" ]]; then
    echo "  Verification retry:   spent for ${ACTIVE_RC} (${_VERIFICATION_RETRY_CASE} on ${_VERIFICATION_RETRY_TASK}: ${_VERIFICATION_RETRY_CAUSE})"
  else
    echo "  Verification retry:   available for ${ACTIVE_RC}"
  fi
  return 0
}

# Helper: whether a TESTER state is one a requeued task passes through
# before it finishes (BACKLOG, WORKING, WAITING; matched case-insensitively,
# surrounding whitespace ignored).
# Usage: cm_verification_state_pending <tester_state>
cm_verification_state_pending() {
  local state="${1,,}"
  state="${state#"${state%%[![:space:]]*}"}"
  state="${state%"${state##*[![:space:]]}"}"
  [[ "$state" == "backlog" || "$state" == "working" || "$state" == "waiting" ]]
}

# Helper: which verification-did-not-run case the TESTER inputs are.
# Usage: cm_verification_case <tester_state> <task_found> <report_path>
cm_verification_case() {
  local state="${1,,}" found="${2:-no}" report="${3:-}"
  if [[ "$state" == "blocked" ]]; then
    echo "tester-blocked"
  elif [[ "$found" == "yes" && "$state" == "done" && ( -z "$report" || ! -f "$report" ) ]]; then
    echo "report-missing"
  elif [[ "$found" == "yes" && "$_VERIFICATION_RETRY" == "spent" ]] && cm_verification_state_pending "$state"; then
    # After the requeue, a task that has not finished yet: verification is
    # still pending, never a verification that did not run.
    echo "verification-pending"
  elif [[ "$found" == "yes" && "$state" != "done" && "$_VERIFICATION_RETRY" == "spent" ]]; then
    # After the requeue, any other state (WONT-DO from the decision wake's
    # close-unverified, a typo) is a verification that did not run, as
    # BLOCKED is.
    echo "tester-blocked"
  fi
}

# Helper: the TESTER task's block cause for <case>, one line.
_cm_verification_cause() {
  local case="$1" line cause=""
  while IFS= read -r line; do
    [[ "$line" == cause=* ]] && cause="${line#cause=}"
  done < <(pp_run_ops pgai_agent_kanban.cm.verification_retry cause \
             --task-dir "${_CM_PROJECT_ROOT}/tasks/${_TESTER_TASK_ID}" --case "$case" 2>/dev/null || true)
  echo "${cause:-no block cause could be read for ${_TESTER_TASK_ID}}"
}

# Helper: set this release's own CM task to WAITING after a requeue, or
# while the requeued TESTER task is still pending, when the release runs
# inside that task's wake. Never fails the release.
_cm_verification_wait_cm_task() {
  local task="${PGAI_TASK_ID:-}"
  [[ "$task" == CM-* && -f "${_CM_PROJECT_ROOT}/tasks/${task}/status.md" ]] || return 0
  # The requeue's own case: from this run's requeue, or from the RC's
  # retry record when verification is still pending.
  local requeue_case="$_VERIFICATION_CASE"
  [[ "$requeue_case" == "verification-pending" ]] && requeue_case="${_VERIFICATION_RETRY_CASE:-tester-blocked}"
  if pp_run_ops pgai_agent_kanban.task_status --project "$PROJECT_NAME" --key "$task" --state WAITING \
      --needs-human no \
      --summary "Release of ${ACTIVE_RC} waits on its verification: TESTER task ${_TESTER_TASK_ID} was requeued once (${requeue_case}). The wake runs this release again when that task finishes; leave this state as it is." \
      >/dev/null 2>&1; then
    echo "  CM task:              ${task} set to WAITING; the wake re-runs the release when ${_TESTER_TASK_ID} finishes"
  else
    echo "  CM task:              ${task} could not be set to WAITING; set it with: bash ${KANBAN_ROOT}/scripts/task-status.sh --project ${PROJECT_NAME} --key ${task} --state WAITING --needs-human no" >&2
  fi
  return 0
}

# Step 4d, right after cm_ship_policy (see the header doc).
cm_verification_retry_apply_decision() {
  _VERIFICATION_CASE="$(cm_verification_case "$_TESTER_STATUS" "$_TESTER_TASK_FOUND" "$_TESTER_REPORT_PATH")"
  _VERIFICATION_REQUEUED="no"
  _VERIFICATION_UNVERIFIED="no"
  local status_path="${_CM_PROJECT_ROOT}/tasks/${_TESTER_TASK_ID}/status.md"

  if [[ "$_SHIP_DECISION" == "REQUEUE-VERIFICATION" ]]; then
    _VERIFICATION_CAUSE="$(_cm_verification_cause "$_VERIFICATION_CASE")"
    [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]] && return 0
    local line outcome="" rc=0
    while IFS= read -r line; do
      [[ "$line" == outcome=* ]] && outcome="${line#outcome=}"
    done < <(pp_run_ops pgai_agent_kanban.cm.verification_retry requeue --kanban-root "$KANBAN_ROOT" \
               --project "$PROJECT_NAME" --version "$ACTIVE_RC" --task "$_TESTER_TASK_ID" \
               --case "$_VERIFICATION_CASE" --cause "$_VERIFICATION_CAUSE" --by cm-release \
               --project-root "$_CM_PROJECT_ROOT" || echo "rc=$?")
    if [[ "$outcome" == "requeued" ]]; then
      _VERIFICATION_REQUEUED="yes"
      if [[ "$_VERIFICATION_CASE" == "report-missing" ]]; then
        # CONVERT: an ordered verification report that is missing is
        # requeued once, then shipped NON-FUNCTIONAL; never a HALT.
        # CENSUS-ANCHOR: release-verification-retry-02
        _cm_verification_notice "cm-release.ship-policy.report-missing" \
          "TESTER task ${_TESTER_TASK_ID} reads DONE for ${ACTIVE_RC} but wrote no report, so verification did not run; the task was returned to BACKLOG with the cause in its ## Triage Note (the RC's one requeue). Nothing ships until it finishes." \
          "cat $(printf '%q' "$status_path")"
      else
        # CONVERT: a BLOCKED TESTER task is requeued once with its block
        # cause, then shipped NON-FUNCTIONAL; never a HALT.
        # CENSUS-ANCHOR: release-verification-retry-01
        _cm_verification_notice "cm-release.trigger-1.tester-blocked" \
          "TESTER task ${_TESTER_TASK_ID} is BLOCKED for ${ACTIVE_RC}, so verification did not run; the task was returned to BACKLOG with its block cause in its ## Triage Note (the RC's one requeue). Nothing ships until it finishes." \
          "cat $(printf '%q' "$status_path")"
      fi
      echo "  Ship decision:       REQUEUE-VERIFICATION — ${_TESTER_TASK_ID} returned to BACKLOG (cause: ${_VERIFICATION_CAUSE})"
      _cm_verification_wait_cm_task
      return 0
    fi
    echo "  Verification retry:   the requeue of ${_TESTER_TASK_ID} was not written (outcome='${outcome:-none}'); the release ships as unverified instead" >&2
    _SHIP_DECISION="SHIP-NON-FUNCTIONAL"
  fi

  if [[ "$_SHIP_DECISION" == "VERIFICATION-PENDING" ]]; then
    [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]] && return 0
    # CONVERT: the RC's one requeue is spent and the requeued TESTER task
    # has not finished yet; the release waits (exit 4) and writes nothing,
    # never a HALT and never a second requeue.
    # CENSUS-ANCHOR: release-verification-retry-05
    _cm_verification_notice "cm-release.verification-pending" \
      "TESTER task ${_TESTER_TASK_ID} reads ${_TESTER_STATUS} for ${ACTIVE_RC}: the RC's one verification requeue is spent and that task has not finished, so verification is still pending. Nothing ships and nothing is written; the release runs again once the task finishes." \
      "cat $(printf '%q' "$status_path")"
    echo "  Ship decision:       VERIFICATION-PENDING — ${_TESTER_TASK_ID} is ${_TESTER_STATUS}; no second requeue"
    _cm_verification_wait_cm_task
    return 0
  fi

  if [[ "$_SHIP_DECISION" == "SHIP-NON-FUNCTIONAL" && -n "$_VERIFICATION_CASE" ]]; then
    _VERIFICATION_UNVERIFIED="yes"
    [[ -n "$_VERIFICATION_CAUSE" ]] || _VERIFICATION_CAUSE="$(_cm_verification_cause "$_VERIFICATION_CASE")"
    _cm_verification_notice "cm-release.verification-did-not-run" \
      "Verification of ${ACTIVE_RC} did not run after its one requeue (TESTER task ${_TESTER_TASK_ID}: ${_VERIFICATION_CASE}, cause: ${_VERIFICATION_CAUSE}); the release ships NON-FUNCTIONAL with 'Verification did not run' under ## Known Issues and an operator inbox entry." \
      "cat $(printf '%q' "$status_path")"
    echo "  Ship decision:       SHIP-NON-FUNCTIONAL — verification did not run after the RC's one requeue"
    return 0
  fi

  if [[ "$_SHIP_DECISION" == "SHIP-KNOWN-BUGS" && "$_TESTER_TASK_FOUND" == "yes" ]]; then
    local state_lc="${_TESTER_STATUS,,}"
    if [[ "$state_lc" != "done" && "$state_lc" != "blocked" ]]; then
      # CONVERT: an unrecognised TESTER state floors to the
      # SHIP-WITH-CONCERNS row and files a bug quoting it; never a HALT.
      # CENSUS-ANCHOR: release-verification-retry-03
      _cm_verification_notice "cm-release.ship-policy.tester-state-unrecognized" \
        "TESTER task ${_TESTER_TASK_ID} for ${ACTIVE_RC} has State='${_TESTER_STATUS}', which the ship-policy matrix has no row for; the release takes the SHIP-WITH-CONCERNS row (Status KNOWN-BUGS) and a bug is filed quoting the state." \
        "cat $(printf '%q' "$status_path")"
      _cm_ship_policy_file_input_bug "tester-state" "State" "${_TESTER_STATUS}" "$status_path"
    fi
  fi
  return 0
}

# Step 4d, after cm_ship_posture_apply_decision: a matrix HALT the posture
# did not ship through, on a row other than trigger 2 or 3 -- a TESTER value
# the matrix has no row for -- floors to the SHIP-WITH-CONCERNS row and
# files a bug quoting the field values. Triggers 2 and 3 halt as before
# (and stay within --force's reach).
cm_verification_floor_unreadable_halt() {
  [[ "$_SHIP_DECISION" == "HALT" ]] || return 0
  case "$_SHIP_HALT_KEY" in
    cm-release.trigger-2.*|cm-release.trigger-3.*) return 0 ;;
  esac
  local values="state=${_TESTER_STATUS} systemic_risk=${_TESTER_SYSTEMIC_RISK} recommendation=${_TESTER_RECOMMENDATION}"
  local source="${_CM_PROJECT_ROOT}/tasks/${_TESTER_TASK_ID}/status.md"
  [[ -n "${_TESTER_REPORT_PATH:-}" ]] && source="$_TESTER_REPORT_PATH"
  _SHIP_DECISION="SHIP-KNOWN-BUGS"
  # CONVERT: the no-row-matched catch-all floors to the SHIP-WITH-CONCERNS
  # row and files a bug quoting the field values; never a HALT.
  # CENSUS-ANCHOR: release-verification-retry-04
  _cm_verification_notice "${_SHIP_HALT_KEY:-cm-release.ship-policy.halt}" \
    "Ship-policy matrix found no row for ${ACTIVE_RC}'s TESTER values (${values}); the release takes the SHIP-WITH-CONCERNS row (Status KNOWN-BUGS) and a bug is filed quoting them." \
    "cat $(printf '%q' "$source")"
  echo "  Ship decision:       ${_SHIP_DECISION} (no matrix row matched; floored to SHIP-WITH-CONCERNS)"
  _cm_ship_policy_file_input_bug "tester-state" "Ship-Policy Inputs" "$values" "$source"
  return 0
}

# Step 4d, after the known-issue resolvers: the ## Known Issues body of an
# unverified ship (pgai_agent_kanban.cm.verification_retry notes-block).
cm_verification_retry_resolve_known_issues() {
  [[ "$_VERIFICATION_UNVERIFIED" == "yes" ]] || return 0
  _KNOWN_ISSUE_NOTES_BLOCK="$(pp_run_ops pgai_agent_kanban.cm.verification_retry notes-block \
    --case "${_VERIFICATION_CASE:-tester-blocked}" 2>/dev/null)" \
    || _KNOWN_ISSUE_NOTES_BLOCK="Verification did not run: this release candidate ships unverified."
  return 0
}

# After the release has shipped (cm_release_append_auto_ship_events): the
# unverified ship's one operator inbox entry. A write that fails is a
# WARNING naming the command that writes it by hand, never an exit.
cm_verification_retry_after_ship() {
  [[ "$_VERIFICATION_UNVERIFIED" == "yes" ]] || return 0
  local -a args=(inbox --kanban-root "$KANBAN_ROOT" --project "$PROJECT_NAME" --version "$ACTIVE_RC"
                 --task "$_TESTER_TASK_ID" --case "${_VERIFICATION_CASE:-tester-blocked}"
                 --cause "${_VERIFICATION_CAUSE:-unknown}")
  local out
  if out="$(pp_run_ops pgai_agent_kanban.cm.verification_retry "${args[@]}" 2>&1)"; then
    echo "[cm-release] Operator inbox: ${out#entry_id=} records that verification of ${ACTIVE_RC} did not run (${_TESTER_TASK_ID})."
  else
    echo "[cm-release] WARNING: the operator inbox entry for the unverified ship could not be written (${out}); the release has shipped. Write it by hand: $(printf '%q' "${KANBAN_ROOT}/scripts/lib/pp_run_ops.sh") pgai_agent_kanban.cm.verification_retry $(printf '%q ' "${args[@]}")" >&2
  fi
  return 0
}
