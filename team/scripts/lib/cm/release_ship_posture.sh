#!/usr/bin/env bash
# team/scripts/lib/cm/release_ship_posture.sh
#
# CM release ship-posture stage: how the project's resolved ship posture
# changes CM's answer to a ship-policy trigger that would otherwise HALT the
# release. Sourced by team/scripts/cm/release.sh ahead of
# release_ship_policy.sh, whose cm_release_apply_ship_policy calls into it
# from Steps 4c and 4d; split into its own file so release_ship_policy.sh
# stays under the shared per-file size ceiling (docs/coding-standards.md;
# enforced by team/scripts/lint_engine_size_dupe.py), the same reason
# release_operator_override.sh and release_known_issue.sh were split out.
#
# The two postures
# -----------------
# The posture is read once per release through pp_ship_posture
# (team/scripts/lib/project_paths.sh), which applies the project.cfg-then-
# kanban.cfg chain and the remote-lane guard and always answers "matrix" or
# "always".
#   matrix — the ship-policy matrix as written: every HALT row halts. Under
#            matrix nothing in this file prints a line or changes a value,
#            so the release's output, exit code, HALT file and release-state
#            writes are exactly what they are without this file.
#   always — the triggers listed in _CM_SHIP_POSTURE_COVERED_TRIGGERS ship
#            instead of halting, and each one records what it would have
#            stopped on:
#              trigger 2 (systemic_risk high, not clear to ship) and
#              trigger 3 (SHIP-WITH-SERIOUS-CONCERNS plus a large fix) ship
#                with release-notes Status NON-FUNCTIONAL and a "## Known
#                Issues" block that carries the non-functional warning and
#                names the bugs filed for the findings behind the stop;
#              trigger 8 (the last three releases all NON-FUNCTIONAL) files
#                one bug naming the pattern through
#                pgai_agent_kanban.cm.ship_policy_bug_filer and the release
#                carries on to Step 4d.
#            Each of them appends one "## Auto-Ship Event" block to the
#            project's release-state.md (write_rc_state.py auto-ship-event),
#            naming the trigger, its error key, the posture and the TESTER
#            task, plus the filed bug for trigger 8, and puts one
#            "Auto-shipped under ship_posture=always (would have halted:
#            trigger N — <error key>)" line under the release notes'
#            "## Known Issues", on both notes paths. Every other HALT —
#            trigger 1 (TESTER BLOCKED), the unrecognized-TESTER-state and
#            no-row-matched rows, and the mechanical triggers 4-11 — halts
#            exactly as it does under matrix.
#
# The --force / --reason operator override is unchanged: under always,
# triggers 2 and 3 never reach a HALT for it to pass, and it still passes
# trigger 1 as before.
#
# Public entry points
# --------------------
#   cm_ship_policy_halt_key <tester_state> <task_found> <systemic_risk> <recommendation> <has_large_fix_effort>
#       Pure. For a matrix decision of HALT, prints the error key of the
#       HALT row that fired. The one place that tells the HALT rows apart:
#       release_ship_policy.sh's HALT dispatch and the posture both read it.
#   cm_ship_policy_trigger_number <error_key>
#       Pure. Prints the trigger number an error key names
#       ("cm-release.trigger-2.…" -> 2), or "none" for a row with no number.
#   cm_ship_posture_passes <posture> <trigger>
#       Pure. Status 0 when <posture> ships through <trigger>, 1 otherwise.
#   cm_ship_posture_read
#       Sets _SHIP_POSTURE from pp_ship_posture for PROJECT_NAME.
#   cm_ship_posture_pass_pattern_stop <release_notes_dir> <pattern_check_output>
#       Step 4c. Status 0 when the posture ships through trigger 8 (after
#       filing the pattern bug and recording the event); 1 when the caller
#       must HALT as before. Prints nothing under matrix.
#   cm_ship_posture_apply_decision
#       Step 4d, right after the matrix decision. Sets _SHIP_HALT_KEY; on a
#       covered trigger under always, rewrites _SHIP_DECISION to
#       SHIP-NON-FUNCTIONAL and records the event.
#   cm_ship_posture_resolve_known_issues
#       Step 4d. After a trigger-2/3 auto-ship, sets _KNOWN_ISSUE_BUG_FILES
#       and _KNOWN_ISSUE_NOTES_BLOCK for the release notes.
#   cm_ship_posture_stamp_notes <notes_file>
#       Step 4e (WRITER-authored notes, after the Status stamp) and Step 11a
#       (generated notes, before the commit). Puts one "Auto-shipped under
#       ship_posture=always (would have halted: trigger N — <error key>)"
#       line per recorded stop under the file's "## Known Issues". Does
#       nothing, and prints nothing, when no stop was shipped through.
#   cm_release_append_auto_ship_events
#       release_finalize.sh's Step 12 post-write block. Appends every
#       recorded Auto-Ship Event to release-state.md.
#
# Globals set for later stages
# -----------------------------
#   _SHIP_POSTURE          "matrix" or "always".
#   _SHIP_HALT_KEY         error key of the matrix HALT row, whenever the
#                          matrix returned HALT (including a HALT the
#                          posture shipped through); "" otherwise.
#   _AUTO_SHIP_TRIGGER     the Step 4d trigger the posture shipped through
#                          (2 or 3), else "none".
#   _CM_AUTO_SHIP_EVENTS   every stop the posture shipped through this run,
#                          one "<timestamp>\t<trigger>\t<error_key>\t<filed_bug>"
#                          entry each, in the order they happened.
#
# Deferred write: the events are recorded in Steps 4c/4d but appended only
# at release_finalize.sh's Step 12 post-write block, beside the Operator
# Override event — a release that halts later, at a mechanical gate,
# leaves no Auto-Ship Event behind, because it did not ship.
#
# Dependencies (must already be sourced by the caller)
# -----------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_ship_posture
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops (stop_bug_files)
#   team/scripts/lib/cm/release_known_issue.sh — build_known_issue_notes_block
#   team/scripts/lib/cm/release_ship_policy.sh — _cm_ship_policy_file_input_bug
#     (called at run time, so it may be sourced after this file)
# Globals read: PROJECT_NAME, ACTIVE_RC, KANBAN_ROOT, RELEASE_STATE,
# _CM_PROJECT_ROOT, and the _TESTER_* / _HAS_LARGE_FIX_EFFORT values Step 4d
# sets. No inline Python: every Python call goes through a module.

# Guard against double-sourcing.
if declare -F cm_ship_posture_passes >/dev/null 2>&1; then
    return 0
fi

# The triggers the always posture ships through. Everything else halts.
_CM_SHIP_POSTURE_COVERED_TRIGGERS=(2 3 8)

# Deferred Auto-Ship Event records; see the header doc.
_CM_AUTO_SHIP_EVENTS=()

# Helper: name the HALT row a matrix HALT came from.
# Usage: cm_ship_policy_halt_key <tester_state> <task_found> <systemic_risk> <recommendation> <has_large_fix_effort>
# The arguments are the values cm_ship_policy decided on (systemic_risk and
# recommendation are read_ship_policy_report_values's normalized tokens).
# The rows are tested in the matrix's own order, first match wins.
cm_ship_policy_halt_key() {
  local tester_state="${1:-none}" task_found="${2:-no}" systemic_risk="${3:-none}"
  local recommendation="${4:-none}" has_large_fix_effort="${5:-no}"
  tester_state="$(echo "$tester_state" | tr '[:upper:]' '[:lower:]')"
  if [[ "$tester_state" == "blocked" ]]; then
    echo "cm-release.trigger-1.tester-blocked"
  elif [[ "$task_found" == "yes" && "$tester_state" != "done" ]]; then
    echo "cm-release.ship-policy.tester-state-unrecognized"
  elif [[ "$systemic_risk" == "high" ]]; then
    echo "cm-release.trigger-2.systemic-risk-high"
  elif [[ "$recommendation" == "ship-with-serious-concerns" && "$has_large_fix_effort" == "yes" ]]; then
    echo "cm-release.trigger-3.serious-concerns-large-fix"
  else
    echo "cm-release.ship-policy.halt"
  fi
}

# Helper: the trigger number an error key names, or "none".
# Usage: cm_ship_policy_trigger_number <error_key>
cm_ship_policy_trigger_number() {
  if [[ "${1:-}" =~ ^cm-release\.trigger-([0-9]+)\. ]]; then
    echo "${BASH_REMATCH[1]}"
  else
    echo "none"
  fi
}

# Helper: does <posture> ship through <trigger>?
# Usage: cm_ship_posture_passes <posture> <trigger>
# Status 0 only for posture "always" and a trigger listed in
# _CM_SHIP_POSTURE_COVERED_TRIGGERS; 1 for "matrix", any other posture
# string, and every other trigger (including "none").
cm_ship_posture_passes() {
  local posture="${1:-matrix}" trigger="${2:-none}" covered
  [[ "$posture" == "always" ]] || return 1
  for covered in "${_CM_SHIP_POSTURE_COVERED_TRIGGERS[@]}"; do
    [[ "$trigger" == "$covered" ]] && return 0
  done
  return 1
}

# Helper: read the project's ship posture once, into _SHIP_POSTURE.
# pp_ship_posture prints its own warning line for an unrecognized value or
# an unconfirmed remote lane and answers "matrix"; anything other than
# "always" here, including a resolver that is unavailable or fails, reads
# as "matrix". Prints one line under always and nothing under matrix.
cm_ship_posture_read() {
  _SHIP_POSTURE="matrix"
  if declare -F pp_ship_posture >/dev/null 2>&1; then
    _SHIP_POSTURE="$(pp_ship_posture "$PROJECT_NAME")" || _SHIP_POSTURE="matrix"
  fi
  [[ "$_SHIP_POSTURE" == "always" ]] || _SHIP_POSTURE="matrix"
  if [[ "$_SHIP_POSTURE" == "always" ]]; then
    local covered="${_CM_SHIP_POSTURE_COVERED_TRIGGERS[*]}"
    echo "  Ship posture:         always — triggers ${covered// /, } ship and record what they would have stopped on"
  fi
  return 0
}

# Helper: record one Auto-Ship Event, appended to release-state.md later by
# cm_release_append_auto_ship_events (see "Deferred write" in the header).
# Usage: _cm_record_auto_ship_event <trigger> <error_key> [<filed_bug>]
_cm_record_auto_ship_event() {
  local trigger="$1" error_key="$2" filed_bug="${3:-}" timestamp
  timestamp="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  _CM_AUTO_SHIP_EVENTS+=("${timestamp}"$'\t'"${trigger}"$'\t'"${error_key}"$'\t'"${filed_bug}")
  if [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]]; then
    # A decide-only run ends before Step 12 (release_decide_only.sh).
    echo "[cm-release] decide-only: a full run records an Auto-Ship Event for trigger ${trigger}; nothing is written."
    return 0
  fi
  echo "[cm-release] Auto-Ship Event recorded for trigger ${trigger} (appended to release-state.md after Step 12's field update)."
}

# Step 4c: the last-three-releases-NON-FUNCTIONAL pattern was detected.
# Usage: cm_ship_posture_pass_pattern_stop <release_notes_dir> <pattern_check_output>
# Under always: files (or cites) the bug naming the pattern, quoting the
# three releases and their statuses from check_last_three_non_functional's
# output, records the Auto-Ship Event with that bug, and returns 0 so the
# release carries on. A filer that files nothing is narrated and the event
# is recorded without a bug — a bug filer never stops a release. Under
# matrix: prints nothing and returns 1, and the caller halts as before.
cm_ship_posture_pass_pattern_stop() {
  local notes_dir="$1" check_output="$2"
  local error_key="cm-release.trigger-8.last-3-rcs-non-functional"
  cm_ship_posture_passes "${_SHIP_POSTURE:-matrix}" 8 || return 1
  local releases line
  releases="$check_output"
  while IFS= read -r line; do
    if [[ "$line" =~ last[[:space:]]+3[[:space:]]+releases:[[:space:]]*(.+)$ ]]; then
      releases="${BASH_REMATCH[1]}"
      break
    fi
  done <<< "$check_output"
  echo "  Ship posture:         always — trigger 8 (${error_key}) files a bug naming the pattern instead of halting"
  _CM_LAST_FILED_BUG_ID=""
  _cm_ship_policy_file_input_bug "release-pattern" "Status" "$releases" "$notes_dir"
  _cm_record_auto_ship_event 8 "$error_key" "${_CM_LAST_FILED_BUG_ID:-}"
  return 0
}

# Step 4d, right after cm_ship_policy: name the HALT row and apply the
# posture to it. Sets _SHIP_HALT_KEY and _AUTO_SHIP_TRIGGER (header doc).
# On a covered trigger under always the decision becomes
# SHIP-NON-FUNCTIONAL (release-notes Status NON-FUNCTIONAL), two lines are
# printed and the event is recorded; otherwise nothing is printed and the
# decision is untouched.
cm_ship_posture_apply_decision() {
  _SHIP_HALT_KEY=""
  _AUTO_SHIP_TRIGGER="none"
  [[ "$_SHIP_DECISION" == "HALT" ]] || return 0
  _SHIP_HALT_KEY="$(cm_ship_policy_halt_key "$_TESTER_STATUS" "$_TESTER_TASK_FOUND" \
    "$_TESTER_SYSTEMIC_RISK" "$_TESTER_RECOMMENDATION" "$_HAS_LARGE_FIX_EFFORT")"
  local trigger
  trigger="$(cm_ship_policy_trigger_number "$_SHIP_HALT_KEY")"
  cm_ship_posture_passes "${_SHIP_POSTURE:-matrix}" "$trigger" || return 0
  _AUTO_SHIP_TRIGGER="$trigger"
  _SHIP_DECISION="SHIP-NON-FUNCTIONAL"
  echo "  Ship posture:         always — trigger ${trigger} (${_SHIP_HALT_KEY}) ships NON-FUNCTIONAL instead of halting"
  echo "  Ship decision:       ${_SHIP_DECISION}"
  _cm_record_auto_ship_event "$trigger" "$_SHIP_HALT_KEY"
}

# Helper: render the "## Known Issues" block for a trigger-2/3 auto-ship.
# Usage: cm_ship_posture_known_issues_block [<bug_file> ...]
# The first line is the non-functional warning the generated release notes
# carry for every NON-FUNCTIONAL release (release_notes.sh, Step 11a). With
# bug files it goes on to name each one by substance, through the same
# build_known_issue_notes_block (release_known_issue.sh) a known-issue ship
# uses, so no internal bug ID reaches the notes; with none it points at the
# bug ledger, as the generated warning does.
cm_ship_posture_known_issues_block() {
  echo "This release is shipped non-functional. Do not use in production. Fix expected in the next patch."
  if [[ $# -gt 0 ]]; then
    echo "The findings behind this status are tracked for a fix in the next release:"
    build_known_issue_notes_block "$@"
  else
    echo "The findings behind this status are tracked on the project's internal bug ledger."
  fi
}

# Step 4d: after a trigger-2/3 auto-ship, resolve the bugs behind the stop
# and the notes block that names them. Trigger 2's are the findings whose
# own Systemic Risk is high; trigger 3's are the findings whose own Fix
# Effort is large (pgai_agent_kanban.cm.stop_bug_files). Sets
# _KNOWN_ISSUE_BUG_FILES and _KNOWN_ISSUE_NOTES_BLOCK, which Step 4e's notes
# stamp and Step 11a's notes generator both read, and which keep those bugs
# out of "## Bugs Skipped". The known-issue carry-forward marker stays
# exclusive to SHIP-KNOWN-ISSUE. Does nothing after any other decision.
cm_ship_posture_resolve_known_issues() {
  [[ "${_AUTO_SHIP_TRIGGER:-none}" == "2" || "${_AUTO_SHIP_TRIGGER:-none}" == "3" ]] || return 0
  local cause="systemic-risk-high"
  [[ "$_AUTO_SHIP_TRIGGER" == "3" ]] && cause="fix-effort-large"
  _KNOWN_ISSUE_BUG_FILES=()
  if [[ -n "${_TESTER_REPORT_PATH:-}" && -f "$_TESTER_REPORT_PATH" ]]; then
    mapfile -t _KNOWN_ISSUE_BUG_FILES < <(
      pp_run_ops pgai_agent_kanban.cm.stop_bug_files \
        "$_TESTER_REPORT_PATH" "${_CM_PROJECT_ROOT}/bugs" "$cause"
    )
  fi
  echo "  Known-issue bug(s) resolved: ${#_KNOWN_ISSUE_BUG_FILES[@]}"
  _KNOWN_ISSUE_NOTES_BLOCK="$(cm_ship_posture_known_issues_block "${_KNOWN_ISSUE_BUG_FILES[@]}")"
}

# Steps 4e and 11a: name every stop the posture shipped through in the
# release notes. Usage: cm_ship_posture_stamp_notes <notes_file>
# Puts one "Auto-shipped under ship_posture=always (would have halted:
# trigger N — <error key>)" line per recorded Auto-Ship Event, in the order
# the stops happened, at the top of the file's "## Known Issues" section,
# whatever the release-notes Status (a trigger-8 stop can ship FUNCTIONAL).
# pgai_agent_kanban.cm.auto_ship_notes owns the wording and placement. The
# caller commits the file afterwards. With no recorded event — always the
# case under matrix — it prints nothing and leaves the file byte-identical.
# A file it cannot update is a WARNING naming the line to add by hand,
# never an exit: the release has passed every gate by then.
cm_ship_posture_stamp_notes() {
  local notes_file="$1" entry timestamp trigger error_key filed_bug
  local -a stop_args=()
  for entry in "${_CM_AUTO_SHIP_EVENTS[@]}"; do
    IFS=$'\t' read -r timestamp trigger error_key filed_bug <<< "$entry"
    stop_args+=(--stop "$trigger" "$error_key")
  done
  [[ ${#stop_args[@]} -gt 0 ]] || return 0
  pp_run_ops pgai_agent_kanban.cm.auto_ship_notes "$notes_file" "${stop_args[@]}" \
    || echo "[cm-release] WARNING: the auto-ship line(s) could not be added to ${notes_file}; the release continues. Add them by hand and commit the file: python3 $KANBAN_ROOT/pgai_agent_kanban/cm/auto_ship_notes.py ${notes_file} ${stop_args[*]}" >&2
  return 0
}

# release_finalize.sh's Step 12 post-write block: append every recorded
# Auto-Ship Event to release-state.md through write_rc_state.py
# auto-ship-event, in the order the events happened. The release has
# already been tagged by then, so a writer failure is a WARNING naming the
# command that appends the event by hand, never an exit — the remaining
# steps still run. Prints nothing when no event was recorded.
cm_release_append_auto_ship_events() {
  local entry timestamp trigger error_key filed_bug
  local -a bug_args
  for entry in "${_CM_AUTO_SHIP_EVENTS[@]}"; do
    IFS=$'\t' read -r timestamp trigger error_key filed_bug <<< "$entry"
    bug_args=()
    [[ -n "$filed_bug" ]] && bug_args=(--filed-bug "$filed_bug")
    if python3 "$KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py" auto-ship-event "$RELEASE_STATE" \
        --version "$ACTIVE_RC" --trigger "$trigger" --error-key "$error_key" \
        --ship-posture "${_SHIP_POSTURE:-always}" --tester-task "${_TESTER_TASK_ID:-none}" \
        --timestamp "$timestamp" "${bug_args[@]}"; then
      echo "[cm-release] Auto-Ship Event appended to release-state.md (trigger ${trigger})."
    else
      echo "[cm-release] WARNING: the Auto-Ship Event for trigger ${trigger} could not be appended to ${RELEASE_STATE}; the release has shipped. Append it by hand: python3 $KANBAN_ROOT/pgai_agent_kanban/cm/write_rc_state.py auto-ship-event ${RELEASE_STATE} --version ${ACTIVE_RC} --trigger ${trigger} --error-key ${error_key} --ship-posture ${_SHIP_POSTURE:-always} --tester-task ${_TESTER_TASK_ID:-none} --timestamp ${timestamp}${filed_bug:+ --filed-bug ${filed_bug}}" >&2
    fi
  done
  return 0
}
