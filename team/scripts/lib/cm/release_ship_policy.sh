#!/usr/bin/env bash
# team/scripts/lib/cm/release_ship_policy.sh
#
# CM release ship-policy stage: the ship-policy decision matrix
# (cm_ship_policy), the last-3-RCs-NON-FUNCTIONAL guard, the TESTER
# prerequisite lookup that feeds the decision, the release-notes
# ## Status placeholder stamp, and the squash pollution guard. Sourced by
# team/scripts/cm/release.sh; extracted from that script's former inline
# Steps 4c-4f so release.sh's own body stays a thin driver over sourced lib
# stages. Behavior-preserving (no stdout/stderr, exit code, HALT-file, or
# release-state.md changes) — one stage of a driver split for focused units.
#
# Public entry point
# -------------------
#   cm_release_apply_ship_policy <pre_squash_hook_step_fn>
#       Runs, in the RC branch's checked-out working tree (set up by
#       cm_release_preflight's Step 4a), the original Steps 4c through 4f in
#       their original order:
#         4c. last-3-RCs-NON-FUNCTIONAL pattern check (HALT if triggered)
#         4d. TESTER prerequisite lookup + ship-policy decision (HALT if required)
#         Both apply the project's ship posture, read once at the top
#         (release_ship_posture.sh): under "always", triggers 2, 3 and 8
#         ship and record an Auto-Ship Event instead of halting.
#         4b. the pre-squash hook (invoked via the caller-supplied callback —
#             see "Why a callback" below; NOT a lib function in this file)
#         4e. stamp release-notes ## Status placeholder with the ship decision
#             (plus any auto-ship line under ## Known Issues)
#         4f. squash pollution guard
#       Takes no arguments other than the callback. Must be called directly
#       (never via command substitution) — several of its internal error
#       paths call `exit` or `cm_halt` directly.
#
#       On return, these globals are set for the rest of release.sh to read:
#         _SHIP_DECISION, _RELEASE_NOTES_STATUS, _TESTER_TASK_ID,
#         _TESTER_STATUS, _TESTER_RECOMMENDATION, _TESTER_SYSTEMIC_RISK
#         (normalized tokens), _TESTER_RECOMMENDATION_RAW,
#         _TESTER_SYSTEMIC_RISK_RAW (the raw report lines),
#         _TESTER_REPORT_PATH, _HAS_LARGE_FIX_EFFORT, _TESTER_TASK_FOUND,
#         _TESTER_IS_REGRESSION, _TESTER_INTEGRITY_DAMAGE,
#         _KNOWN_ISSUE_BUG_FILES (array), _KNOWN_ISSUE_NOTES_BLOCK — both
#         non-empty only on SHIP-KNOWN-ISSUE; also read by
#         cm_release_generate_notes (release_notes.sh).
#         _SKIPPED_BUG_PAIRS, _BUGS_SKIPPED_NOTES_BLOCK — "## Bugs Skipped" render.
#         _SHIP_POSTURE, _SHIP_HALT_KEY, _AUTO_SHIP_TRIGGER — release_ship_posture.sh.
#
# Operator --force override scope: trigger-1/2/3 only (release_operator_override.sh's header doc has the rationale).
#
# Why a callback (pre_squash_hook_step_fn)
# -----------------------------------------
# Step 4b (the pre-squash hook) sits, in the original file, physically
# between Step 4d and Step 4e — but its body is one of the three
# `cm_resolve_and_enforce_hook` call sites that
# team/tests/unit/test_ship_rc_hooks.py counts directly in release.sh's own
# file text (release.sh must contain exactly three non-comment
# `cm_resolve_and_enforce_hook` calls: pre-squash, pre-tag, post-tag). Moving
# that call site's text into this lib file would drop release.sh's count to
# two and fail that pre-existing structural test. Step 4b's body therefore
# stays defined in release.sh (as a small named function) and is threaded
# through here by name so cm_release_apply_ship_policy can invoke it at
# exactly the right point — preserving the original step order (and thus
# identical stdout and HALT-precedence) without duplicating the hook body.
#
# Other functions in this file (cm_ship_policy, cm_ship_policy_notes_status,
# _cm_ship_policy_file_input_bug)
# are internal helpers used by
# cm_release_apply_ship_policy; they are ordinary functions (not
# `local`-scoped closures) only because bash has no nested lexical scoping,
# not because they are meant to be called independently. The readers Step 4d
# calls — read_ship_policy_tester_lookup, read_ship_policy_report_values and
# read_ship_policy_finding_dimensions — and Step 4c's
# check_last_three_non_functional are defined in the sibling stage
# release_ship_policy_report.sh, which this file sources itself (below the
# double-sourcing guard) so that sourcing this file alone still resolves
# every one of them alongside cm_ship_policy and cm_release_apply_ship_policy.
#
# Input normalization and the unparseable-value outcome
# ------------------------------------------------------
# The matrix never reads a raw report field. Step 4d reads the report's
# Recommendation and Systemic Risk through read_ship_policy_report_values,
# which takes them from the one TESTER-report parser
# (pgai_agent_kanban.reports.tester_report): bold markers, backticks, a
# "Label:" prefix and any annotation after the token are ignored, the report
# template's HTML comments are skipped, and each field reduces to one token
# the matrix has a row for — so a bold, label-prefixed
# SHIP-WITH-SERIOUS-CONCERNS reaches the SHIP-WITH-SERIOUS-CONCERNS row and
# `High — <annotation>` reaches the systemic_risk=high row. A value that
# still cannot be parsed (a typo, an unedited template placeholder, a
# missing section on a DONE task) never HALTs and never selects the permissive SHIP-FUNCTIONAL row:
# cm_ship_policy floors it to the SHIP-WITH-CONCERNS row (SHIP-KNOWN-BUGS,
# release-notes Status KNOWN-BUGS), and Step 4d files a bug through
# _cm_ship_policy_file_input_bug quoting the raw field text and the report
# path so the next iteration heals the document. The TESTER-task lookup
# matches ## Role / ## Release Version by normalized token too: a task
# whose fields match only after normalization is still used (the RC is never treated as
# untested because of formatting), the mismatch is narrated on stderr, and a
# bug is filed quoting the raw field and the README path. The
# has_large_fix_effort input is read from each finding's own Fix Effort
# field, as the parser reads it, only — never from a whole-report grep,
# which matched negations, "larger", the template legend and prose.
#
# Dependencies (must already be sourced by the caller before this file)
# -----------------------------------------------------------------------
#   team/scripts/lib/cm/release_preflight.sh — cm_halt
#     (called from Step 4c/4e below)
#   team/scripts/lib/cm/release_known_issue.sh — the SHIP-KNOWN-ISSUE
#     helpers called from Step 4e below; split out to keep this file
#     under the engine-decomposition size ceiling
#   team/scripts/lib/cm/release_operator_override.sh — _cm_apply_matrix_trigger
#   team/scripts/lib/cm/release_ship_posture.sh — the posture read, the HALT-row
#     classifier and the ship-through calls in Steps 4c/4d
#   team/scripts/lib/pp_run_ops.sh — pp_run_ops (invokes the bug filer
#     pgai_agent_kanban.cm.ship_policy_bug_filer from Step 4d below)
#   team/scripts/lib/cm/release_decide_only.sh — under _CM_DECIDE_ONLY=true:
#     the Step 4c notes copy, the bug-filer stand-in, and the stop after 4d
# Sourced by this file itself (no caller action needed):
#   team/scripts/lib/cm/release_ship_policy_report.sh —
#     read_ship_policy_tester_lookup, read_ship_policy_report_values and
#     read_ship_policy_finding_dimensions (all called from Step 4d below)
#     and check_last_three_non_functional (called from Step 4c below); split
#     out to keep this file under the engine-decomposition size ceiling
# The caller must also have `set -euo pipefail` enabled, and must have
# already run cm_release_preflight (so ACTIVE_RC, RC_BRANCH, REPO_ROOT,
# MAIN_BRANCH, PROJECT_NAME are set) before calling
# cm_release_apply_ship_policy.

# Guard against double-sourcing.
if declare -F cm_release_apply_ship_policy >/dev/null 2>&1; then
    return 0
fi

# The finding-dimension reader (read_ship_policy_finding_dimensions) lives in
# its own sibling stage and is sourced here rather than by the caller, so a
# script that sources only this file still resolves all three public
# functions. The sibling carries its own double-sourcing guard.
if ! declare -F read_ship_policy_finding_dimensions >/dev/null 2>&1; then
    # shellcheck source=release_ship_policy_report.sh
    source "$(dirname "${BASH_SOURCE[0]}")/release_ship_policy_report.sh"
fi

# Helper: apply the ship-policy decision matrix.
# Usage: cm_ship_policy <tester_state> <systemic_risk> <recommendation> <has_large_fix_effort> <task_found> <is_regression> <integrity_damage>
# Outputs one of: SHIP-FUNCTIONAL  SHIP-KNOWN-BUGS  SHIP-NON-FUNCTIONAL  SHIP-KNOWN-ISSUE  HALT
# systemic_risk / recommendation: the NORMALIZED tokens
#   read_ship_policy_report_values prints (see release_ship_policy_report.sh)
#   — never a raw report line. Recognized systemic_risk tokens: high, low,
#   medium, and none (an absent report-level field reads as not-high, the
#   template's own default being low). Recognized recommendation tokens:
#   pass, ship-with-concerns, ship-with-serious-concerns. On a DONE task
#   whose risk is not high, any other value on either field — "unparseable",
#   "none", a typo passed straight in — floors the decision to the
#   SHIP-WITH-CONCERNS row (SHIP-KNOWN-BUGS): never HALT, and never the
#   permissive SHIP-FUNCTIONAL row a garbled field used to select. A
#   recommendation stricter than that floor (ship-with-serious-concerns)
#   keeps its own row. Filing the bug that records the unreadable value is
#   Step 4d's job, not this function's.
# has_large_fix_effort: "yes" if any finding has Fix Effort=large; "no" otherwise.
# task_found: "yes" if a TESTER task was located for this Release Version (regardless
#   of its state); "no" if no TESTER task exists at all. This is passed explicitly
#   (rather than inferred from tester_state=="none") so a TESTER task that was FOUND
#   but has an empty/malformed State field is never confused with the "no TESTER task
#   exists" case — see CM.md "(no report, Test Required: false) -> Ship" vs
#   "(unrecognized values) -> HALT" rows, which are distinct.
# is_regression / integrity_damage: split the DONE-plus-systemic_risk=high row
#   between HALT and a ship outcome. Read from the TESTER report by
#   read_ship_policy_finding_dimensions (release_ship_policy_report.sh; see
#   its header doc), never inferred here. is_regression="yes" (this RC's own work introduced the finding) vs
#   "no" (predates this RC); only "no" clears it. integrity_damage=
#   "breakage"/"destructive" (two of CM.md's four "HALT is reserved for
#   system-breaking states" classes — the other two are already covered by
#   tester_state/task_found and systemic_risk/has_large_fix_effort) vs "none";
#   only "none" clears it. Any other value on either, including the default
#   "unknown" for an absent field, does not clear.
# Evaluation is top-to-bottom; first matching row wins (per CM.md specification).
cm_ship_policy() {
  local tester_state="${1:-none}"
  local systemic_risk="${2:-none}"
  local recommendation="${3:-none}"
  local has_large_fix_effort="${4:-no}"
  local task_found="${5:-no}"
  local is_regression="${6:-unknown}"
  local integrity_damage="${7:-unknown}"

  # Normalize to lowercase for comparison robustness.
  tester_state="$(echo "$tester_state" | tr '[:upper:]' '[:lower:]')"
  systemic_risk="$(echo "$systemic_risk" | tr '[:upper:]' '[:lower:]')"
  recommendation="$(echo "$recommendation" | tr '[:upper:]' '[:lower:]')"
  has_large_fix_effort="$(echo "$has_large_fix_effort" | tr '[:upper:]' '[:lower:]')"
  task_found="$(echo "$task_found" | tr '[:upper:]' '[:lower:]')"
  is_regression="$(echo "$is_regression" | tr '[:upper:]' '[:lower:]')"
  integrity_damage="$(echo "$integrity_damage" | tr '[:upper:]' '[:lower:]')"

  # Row 1: TESTER state is BLOCKED → HALT regardless of other fields.
  if [[ "$tester_state" == "blocked" ]]; then
    echo "HALT"
    return 0
  fi

  # Rows 2–6: TESTER state is DONE.
  if [[ "$tester_state" == "done" ]]; then
    # Row 2: systemic_risk=high. A pre-existing, non-integrity-damaging
    # finding with no large-fix-effort finding ships as a known issue;
    # a regression, integrity damage, a large fix effort, or an
    # unrecognized/absent value on either new dimension still halts.
    if [[ "$systemic_risk" == "high" ]]; then
      if [[ "$is_regression" == "no" && "$integrity_damage" == "none" && "$has_large_fix_effort" == "no" ]]; then
        echo "SHIP-KNOWN-ISSUE"
        return 0
      fi
      echo "HALT"
      return 0
    fi
    # Rows 3–6: systemic_risk is not high. A systemic_risk token that is
    # neither low, medium nor none is an unreadable field: it cannot clear
    # the PASS row, so a PASS recommendation is floored to SHIP-WITH-CONCERNS
    # below (a stricter recommendation keeps its own row).
    if [[ "$systemic_risk" != "low" && "$systemic_risk" != "medium" && "$systemic_risk" != "none" \
          && "$recommendation" == "pass" ]]; then
      recommendation="ship-with-concerns"
    fi
    # Row 3: recommendation=PASS → SHIP-FUNCTIONAL.
    if [[ "$recommendation" == "pass" ]]; then
      echo "SHIP-FUNCTIONAL"
      return 0
    fi
    # Row 4: recommendation=SHIP-WITH-CONCERNS → SHIP-KNOWN-BUGS.
    if [[ "$recommendation" == "ship-with-concerns" ]]; then
      echo "SHIP-KNOWN-BUGS"
      return 0
    fi
    # Rows 5–6: recommendation=SHIP-WITH-SERIOUS-CONCERNS.
    if [[ "$recommendation" == "ship-with-serious-concerns" ]]; then
      if [[ "$has_large_fix_effort" == "yes" ]]; then
        # Row 6: any large fix effort → HALT.
        echo "HALT"
        return 0
      else
        # Row 5: all small/medium → SHIP-NON-FUNCTIONAL.
        echo "SHIP-NON-FUNCTIONAL"
        return 0
      fi
    fi
    # Absent or unrecognized recommendation ("none", "unparseable", a typo):
    # the field cannot be read, so under the ship-always posture the release
    # takes the SHIP-WITH-CONCERNS row — never HALT, and never the permissive
    # SHIP-FUNCTIONAL row an unreadable field used to select silently.
    echo "SHIP-KNOWN-BUGS"
    return 0
  fi

  # Row 7 (no report found): no TESTER task exists for this Release Version at all.
  # This is the documented "(no report, Test Required: false) -> Ship" row — the
  # only case that legitimately defaults to SHIP-FUNCTIONAL when tester_state is
  # not "blocked" or "done".
  if [[ "$task_found" != "yes" ]]; then
    echo "SHIP-FUNCTIONAL"
    return 0
  fi

  # Row 8 (unrecognized values): a TESTER task WAS found (task_found=yes) but its
  # state is neither "blocked" nor "done" (e.g. WONT-DO, WORKING, a typo). Per
  # CM.md's "(unrecognized values) -> HALT -- quote the unrecognized field and
  # value verbatim" row, this must HALT rather than silently default-ship — a
  # high-risk report attached to a non-DONE task must never be treated as
  # equivalent to "no report at all".
  echo "HALT"
}

# Helper: the release-notes ## Status value a ship decision stamps.
# Usage: cm_ship_policy_notes_status <ship_decision>
# Prints "" for HALT (no release notes are written on HALT).
cm_ship_policy_notes_status() {
  case "${1:-}" in
    SHIP-FUNCTIONAL)     echo "FUNCTIONAL" ;;
    SHIP-KNOWN-BUGS)     echo "KNOWN-BUGS" ;;
    SHIP-NON-FUNCTIONAL) echo "NON-FUNCTIONAL" ;;
    SHIP-KNOWN-ISSUE)    echo "KNOWN-ISSUE" ;;
    HALT)                echo "" ;;
    *)                   echo "FUNCTIONAL" ;;
  esac
}

# Helper: file (or cite) the bug that records one unreadable ship-policy
# input. Usage: _cm_ship_policy_file_input_bug <kind> <field> <raw_value> <source_path>
#   kind: report-value (a TESTER report field the matrix could not parse),
#         lookup-field (a TESTER task README field matched only after
#         normalization) or release-pattern (the last three releases all
#         NON-FUNCTIONAL) — pgai_agent_kanban.cm.ship_policy_bug_filer's own
#         vocabulary; that module owns the dedup rule and the filed shape.
# Writes only into ${_CM_PROJECT_ROOT}/bugs. Never fails the release: a
# filer error is narrated on stderr and the decision proceeds — a bug filer
# that could stop a release would be a gate no requirement authorized.
# Prints one indented line naming the filed or cited bug, and leaves its
# BUG-NNNN id (empty when nothing was filed) in _CM_LAST_FILED_BUG_ID.
_cm_ship_policy_file_input_bug() {
  local kind="$1" field="$2" raw_value="$3" source_path="$4"
  local bugs_dir="${_CM_PROJECT_ROOT}/bugs"
  [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]] && { cm_release_decide_only_skip_bug "$field" "$source_path"; return 0; }
  local -a filer_lines=()
  local filer_rc=0 line outcome="" bug_id="" bug_path=""
  mapfile -t filer_lines < <(
    pp_run_ops pgai_agent_kanban.cm.ship_policy_bug_filer \
      "$bugs_dir" "$PROJECT_NAME" "$ACTIVE_RC" "$kind" "$field" "$raw_value" "$source_path" 2>&1
  ) || filer_rc=$?
  for line in "${filer_lines[@]}"; do
    case "$line" in
      outcome=*)  outcome="${line#outcome=}" ;;
      bug_id=*)   bug_id="${line#bug_id=}" ;;
      bug_path=*) bug_path="${line#bug_path=}" ;;
      *)          echo "  Bug filer:            ${line}" >&2 ;;
    esac
  done
  case "$outcome" in
    filed)     echo "  Bug filed:            ${bug_id} (${field} in ${source_path}) -> ${bug_path}" ;;
    duplicate) echo "  Bug already open:     ${bug_id} records ${field} in ${source_path}; no duplicate filed" ;;
    *)         echo "  Bug filer:            nothing filed for ${field} in ${source_path} (rc=${filer_rc}) — release continues" >&2 ;;
  esac
  _CM_LAST_FILED_BUG_ID="$bug_id"
  return 0
}

# -----------------------------------------------------------------------
# cm_release_apply_ship_policy <pre_squash_hook_step_fn> — entry point
# (see header doc above for the callback rationale).
# -----------------------------------------------------------------------
cm_release_apply_ship_policy() {
  local pre_squash_hook_step_fn="$1"

  # --- Step 4c: Last-3-RCs-NON-FUNCTIONAL pattern check ---
  # Scan the dev tree's release-notes/ for the last three shipped releases; all three
  # NON-FUNCTIONAL means degraded work ships repeatedly — HALT for operator review. A window
  # already halted on was acknowledged by the unhalt and passes; a newer release forms a new one.
  echo "[Step 4c] Checking last-3-RCs-NON-FUNCTIONAL pattern..."
  cm_ship_posture_read
  local _NOTES_DIR_FOR_PATTERN_CHECK="${_CM_DECIDE_ONLY_NOTES_DIR:-$REPO_ROOT/release-notes}"
  local _PATTERN_CHECK_RESULT=""
  # Initialize to 1 (pattern NOT detected / skipped) so a missing notes dir is a safe pass.
  local _PATTERN_DETECTED=1
  if [[ -d "$_NOTES_DIR_FOR_PATTERN_CHECK" ]]; then
    # set +e / capture / set -e keeps the shell alive AND the exit code ('|| true' masks it).
    set +e
    _PATTERN_CHECK_RESULT="$(check_last_three_non_functional "$_NOTES_DIR_FOR_PATTERN_CHECK" "$ACTIVE_RC" 2>&1)"
    _PATTERN_DETECTED=$?
    set -e
    # Print each line of the check output with a two-space indent.
    while IFS= read -r _pat_line; do
      [[ -n "$_pat_line" ]] && echo "  $_pat_line"
    done <<< "$_PATTERN_CHECK_RESULT"
  else
    echo "  release-notes/ directory not found in dev tree — skipping pattern check."
  fi

  local _PATTERN_WINDOW
  _PATTERN_WINDOW="$(cm_non_functional_pattern_window "$_PATTERN_CHECK_RESULT")"
  if [[ $_PATTERN_DETECTED -ne 0 ]]; then
    echo "  Pattern check passed — not all 3 consecutive releases are NON-FUNCTIONAL."
  elif cm_non_functional_pattern_window_acknowledged "$_PATTERN_WINDOW"; then
    echo "  Pattern already halted on this window (${_PATTERN_WINDOW}) and the HALT was cleared — continuing."
  elif ! cm_ship_posture_pass_pattern_stop "$_NOTES_DIR_FOR_PATTERN_CHECK" "$_PATTERN_CHECK_RESULT"; then
    cm_record_non_functional_pattern_window "$_PATTERN_WINDOW"
    cm_halt \
      "cm-release.trigger-8.last-3-rcs-non-functional" \
      "Trigger 8: the last three consecutive releases for ${PROJECT_NAME} (${_PATTERN_WINDOW}) were all marked NON-FUNCTIONAL — the autonomous chain is shipping degraded work repeatedly without self-correcting." \
      "Review the NON-FUNCTIONAL releases under release-notes/ in the dev tree, confirm a bug was filed naming the pattern (CM.md Trigger 8 procedure), fix the root cause, then run: team/scripts/unhalt.sh --project ${PROJECT_NAME} (the next release passes this same window and halts again only on a new one)"
  fi

  # --- Step 4d: TESTER prerequisite check + ship-policy decision ---
  # Read the TESTER report for this RC from the project's task tree.
  # Policy: TESTER state must be DONE AND systemic_risk must not be high to proceed.
  # See CM.md "Ship-Policy Decision Matrix" for the full table.
  echo "[Step 4d] Checking TESTER prerequisite and applying ship-policy decision matrix..."

  # Locate the TESTER task's status.md and report.md for this RC in one pass
  # (read_ship_policy_tester_lookup, release_ship_policy_report.sh). RCs can
  # be cancelled and re-cut (roles/PM.md "RC immutability" / cm/cancel-rc.sh),
  # so more than one TESTER attempt per Release Version is a supported,
  # EXPECTED condition: the reader collects every candidate and prefers the
  # most recently created DONE one, else the most recently created of any
  # state, so the selection reflects the RC's CURRENT verification attempt
  # rather than a stale one. ## Role and ## Release Version are matched by
  # normalized token; a task that matched only after normalization is still
  # selected — the RC is never treated as untested because of formatting —
  # and the mismatch is narrated and filed as a bug below.
  _TESTER_STATUS="none"
  _TESTER_RECOMMENDATION="none"
  _TESTER_SYSTEMIC_RISK="none"
  _TESTER_REPORT_PATH=""
  _TESTER_TASK_ID="none"
  _TESTER_TASK_FOUND="no"
  local _CM_TASKS_DIR="${_CM_PROJECT_ROOT}/tasks"
  local -a _lookup_lines=() _lookup_mismatches=()
  local _lookup_line _lookup_key _lookup_value
  local _mismatch_field="" _mismatch_raw=""
  mapfile -t _lookup_lines < <(read_ship_policy_tester_lookup "$_CM_TASKS_DIR" "$ACTIVE_RC" 2>&1)
  for _lookup_line in "${_lookup_lines[@]}"; do
    _lookup_key="${_lookup_line%%=*}"
    _lookup_value="${_lookup_line#*=}"
    case "$_lookup_key" in
      tester_task_id)    _TESTER_TASK_ID="$_lookup_value" ;;
      tester_state)      _TESTER_STATUS="$_lookup_value" ;;
      tester_report_path) _TESTER_REPORT_PATH="$_lookup_value" ;;
      tester_task_found) _TESTER_TASK_FOUND="$_lookup_value" ;;
      tester_task_dir)   ;;
      mismatch_field)    _mismatch_field="$_lookup_value" ;;
      mismatch_raw)      _mismatch_raw="$_lookup_value" ;;
      mismatch_path)     _lookup_mismatches+=("${_mismatch_field}" "${_mismatch_raw}" "${_lookup_value}") ;;
      *)                 echo "  TESTER lookup:        ${_lookup_line}" >&2 ;;
    esac
  done

  # Read the report-level values the matrix's DONE rows consult, normalized,
  # plus the per-finding large-fix-effort flag — see
  # read_ship_policy_report_values (release_ship_policy_report.sh) for the
  # normalization rules and the per-finding Fix Effort read. Narration lines
  # (a value that normalized, or could not be parsed) belong on this step's
  # own output. Without a report every value stays at its "none" default.
  _TESTER_RECOMMENDATION_RAW="none"
  _TESTER_SYSTEMIC_RISK_RAW="none"
  _HAS_LARGE_FIX_EFFORT="no"
  if [[ -n "$_TESTER_REPORT_PATH" && -f "$_TESTER_REPORT_PATH" ]]; then
    local -a _values_lines=()
    local _values_line _values_key _values_value
    mapfile -t _values_lines < <(read_ship_policy_report_values "$_TESTER_REPORT_PATH" 2>&1)
    for _values_line in "${_values_lines[@]}"; do
      _values_key="${_values_line%%=*}"
      _values_value="${_values_line#*=}"
      case "$_values_key" in
        recommendation)       _TESTER_RECOMMENDATION="$_values_value" ;;
        recommendation_raw)   _TESTER_RECOMMENDATION_RAW="$_values_value" ;;
        systemic_risk)        _TESTER_SYSTEMIC_RISK="$_values_value" ;;
        systemic_risk_raw)    _TESTER_SYSTEMIC_RISK_RAW="$_values_value" ;;
        has_large_fix_effort) _HAS_LARGE_FIX_EFFORT="$_values_value" ;;
        *)                    echo "  Report reader:        ${_values_line}" >&2 ;;
      esac
    done
  fi

  echo "  TESTER task:         ${_TESTER_TASK_ID}"
  echo "  TESTER state:        ${_TESTER_STATUS}"
  echo "  TESTER report:       ${_TESTER_REPORT_PATH:-not found}"
  echo "  Recommendation:      ${_TESTER_RECOMMENDATION} (raw: ${_TESTER_RECOMMENDATION_RAW})"
  echo "  Systemic Risk:       ${_TESTER_SYSTEMIC_RISK} (raw: ${_TESTER_SYSTEMIC_RISK_RAW})"
  echo "  Has large fix effort: ${_HAS_LARGE_FIX_EFFORT}"

  # File the bug for every input the matrix could not read. A lookup field
  # that matched only after normalization is filed whatever the state; a
  # report value is filed only on a DONE task, the one state whose rows
  # read those values (BLOCKED and unrecognized states HALT on the state
  # alone). "none" on a DONE task's Recommendation is a report that never
  # named a value — or no report at all — and is filed as such; "none" on
  # Systemic Risk reads as not-high (the template's own default is low) and
  # is not a defect. Each filing narrates on stderr and prints the bug it
  # filed or cited; none can stop the release.
  local _mismatch_idx
  for (( _mismatch_idx=0; _mismatch_idx<${#_lookup_mismatches[@]}; _mismatch_idx+=3 )); do
    echo "  TESTER lookup:        ${_lookup_mismatches[_mismatch_idx]} matched only after normalization in ${_lookup_mismatches[_mismatch_idx+2]}: raw value '${_lookup_mismatches[_mismatch_idx+1]}'" >&2
    _cm_ship_policy_file_input_bug "lookup-field" "${_lookup_mismatches[_mismatch_idx]}" "${_lookup_mismatches[_mismatch_idx+1]}" "${_lookup_mismatches[_mismatch_idx+2]}"
  done
  if [[ "$(echo "$_TESTER_STATUS" | tr '[:upper:]' '[:lower:]')" == "done" ]]; then
    local _report_for_bug="${_TESTER_REPORT_PATH:-${_CM_TASKS_DIR}/${_TESTER_TASK_ID}/artifacts/report.md}"
    if [[ "$_TESTER_RECOMMENDATION" == "unparseable" || "$_TESTER_RECOMMENDATION" == "none" ]]; then
      echo "  Recommendation:       cannot be read from ${_report_for_bug} (raw value '${_TESTER_RECOMMENDATION_RAW}'); the matrix floors the release to SHIP-WITH-CONCERNS and a bug is filed" >&2
      _cm_ship_policy_file_input_bug "report-value" "Recommendation" "${_TESTER_RECOMMENDATION_RAW}" "${_report_for_bug}"
    fi
    if [[ "$_TESTER_SYSTEMIC_RISK" == "unparseable" ]]; then
      echo "  Systemic Risk:        cannot be read from ${_report_for_bug} (raw value '${_TESTER_SYSTEMIC_RISK_RAW}'); the matrix floors the release to SHIP-WITH-CONCERNS and a bug is filed" >&2
      _cm_ship_policy_file_input_bug "report-value" "Systemic Risk" "${_TESTER_SYSTEMIC_RISK_RAW}" "${_report_for_bug}"
    fi
  fi

  # Read the regression/integrity-damage dimensions for the report's
  # systemic_risk=high finding(s) — see read_ship_policy_finding_dimensions's
  # header doc (release_ship_policy_report.sh) for the field contract. Default "unknown" (never "no"/"none")
  # when there is no report, so a report predating these fields still fails
  # loud on the systemic_risk=high row exactly as it did before.
  _TESTER_IS_REGRESSION="unknown"
  _TESTER_INTEGRITY_DAMAGE="unknown"
  if [[ -n "$_TESTER_REPORT_PATH" && -f "$_TESTER_REPORT_PATH" ]]; then
    # The reader prints its two values as the last line of stdout and
    # narrates on stderr ahead of it (a report that yields zero finding
    # blocks says so, naming the report path), so both streams are captured
    # together: the final line carries the values and every earlier line is
    # narration that belongs on this step's own output.
    local -a _dims_lines=()
    local _dims_narration
    mapfile -t _dims_lines < <(read_ship_policy_finding_dimensions "$_TESTER_REPORT_PATH" 2>&1)
    if (( ${#_dims_lines[@]} > 0 )); then
      read -r _TESTER_IS_REGRESSION _TESTER_INTEGRITY_DAMAGE <<< "${_dims_lines[-1]}"
      for _dims_narration in "${_dims_lines[@]:0:${#_dims_lines[@]}-1}"; do
        echo "  Finding reader:       ${_dims_narration}"
      done
    fi
  fi
  echo "  Regression:           ${_TESTER_IS_REGRESSION}"
  echo "  Integrity damage:     ${_TESTER_INTEGRITY_DAMAGE}"

  # Apply the ship-policy decision matrix (table-driven, single function, no scattered if-else).
  _SHIP_DECISION="$(cm_ship_policy "$_TESTER_STATUS" "$_TESTER_SYSTEMIC_RISK" "$_TESTER_RECOMMENDATION" "$_HAS_LARGE_FIX_EFFORT" "$_TESTER_TASK_FOUND" "$_TESTER_IS_REGRESSION" "$_TESTER_INTEGRITY_DAMAGE")"
  echo "  Ship decision:       $_SHIP_DECISION"
  cm_ship_posture_apply_decision  # sets _SHIP_HALT_KEY; may ship trigger 2/3 NON-FUNCTIONAL

  # Determine release-notes Status field value from ship decision (set before any HALT check).
  # Exported so Step 11a can use it when generating release notes.
  _RELEASE_NOTES_STATUS="$(cm_ship_policy_notes_status "$_SHIP_DECISION")"

  # Resolve the SHIP-KNOWN-ISSUE causing bug(s) and their notes block (both
  # functions live in release_known_issue.sh). Empty for every other
  # decision, so neither can leak onto an ordinary ship. Read again by Step
  # 4e below and by cm_release_generate_notes in release_notes.sh — both
  # stages run in this same shell session.
  _KNOWN_ISSUE_BUG_FILES=()
  _KNOWN_ISSUE_NOTES_BLOCK=""
  if [[ "$_SHIP_DECISION" == "SHIP-KNOWN-ISSUE" ]]; then
    mapfile -t _KNOWN_ISSUE_BUG_FILES < <(read_ship_policy_known_issue_bug_files "${_TESTER_REPORT_PATH}" "${_CM_PROJECT_ROOT}/bugs")
    echo "  Known-issue bug(s) resolved: ${#_KNOWN_ISSUE_BUG_FILES[@]}"
    [[ "${#_KNOWN_ISSUE_BUG_FILES[@]}" -gt 0 ]] && _KNOWN_ISSUE_NOTES_BLOCK="$(build_known_issue_notes_block "${_KNOWN_ISSUE_BUG_FILES[@]}")"
  fi
  cm_ship_posture_resolve_known_issues

  cm_build_bugs_skipped_ship_block "${_TESTER_REPORT_PATH}" "${_CM_PROJECT_ROOT}/bugs" "${_KNOWN_ISSUE_BUG_FILES[@]}"
  echo "  Skipped-bug pair(s) resolved: $(( ${#_SKIPPED_BUG_PAIRS[@]} / 2 ))"
  # Apply HALT decisions from the policy matrix, via _cm_apply_matrix_trigger
  # (release_operator_override.sh): trigger-1/2/3 pass under --force ("yes"),
  # every other row here is a hard stop ("no"). Messages/remedies unchanged.
  # The row is the one cm_ship_policy_halt_key named (release_ship_posture.sh).
  if [[ "$_SHIP_DECISION" == "HALT" ]]; then
    case "$_SHIP_HALT_KEY" in
    cm-release.trigger-1.tester-blocked)
      _cm_apply_matrix_trigger \
        "cm-release.trigger-1.tester-blocked" \
        "Trigger 1: TESTER task ${_TESTER_TASK_ID} is BLOCKED for ${ACTIVE_RC} — verification could not complete, so the release of an unverified RC is refused." \
        "Read task ${_TESTER_TASK_ID}'s status.md Blockers field at ${_CM_TASKS_DIR}, resolve the underlying issue, re-run TESTER so it reaches DONE, then re-invoke scripts/cm/release.sh." \
        "yes" ;;
    cm-release.ship-policy.tester-state-unrecognized)
      # Found but State is neither BLOCKED nor DONE (WONT-DO, WORKING, a typo).
      # Per CM.md's "(unrecognized values) -> HALT" row. Out of --force's
      # scope: a bad input, not a judgement call.
      _cm_apply_matrix_trigger \
        "cm-release.ship-policy.tester-state-unrecognized" \
        "TESTER task ${_TESTER_TASK_ID} for ${ACTIVE_RC} has State='${_TESTER_STATUS}' — the ship-policy decision matrix only defines rows for State=BLOCKED and State=DONE, so an unrecognized state is refused rather than default-shipped." \
        "Inspect task ${_TESTER_TASK_ID} at ${_CM_TASKS_DIR}, confirm the correct TESTER outcome (re-run TESTER if its State should be DONE or BLOCKED instead), then re-invoke scripts/cm/release.sh." \
        "no" ;;
    cm-release.trigger-2.systemic-risk-high)
      _cm_apply_matrix_trigger \
        "cm-release.trigger-2.systemic-risk-high" \
        "Trigger 2: TESTER report systemic_risk=high for ${ACTIVE_RC} (task ${_TESTER_TASK_ID}) and the finding is not clear to ship: regression=${_TESTER_IS_REGRESSION} integrity=${_TESTER_INTEGRITY_DAMAGE} has_large_fix_effort=${_HAS_LARGE_FIX_EFFORT} — a regression, integrity damage, or a large fix effort each independently refuse the release; only regression=no plus integrity=none plus has_large_fix_effort=no ships as a known issue instead." \
        "Read ${_TESTER_REPORT_PATH:-the TESTER report for this RC} for the high-risk findings, fix or triage them, re-run TESTER, then re-invoke scripts/cm/release.sh." \
        "yes" ;;
    cm-release.trigger-3.serious-concerns-large-fix)
      _cm_apply_matrix_trigger \
        "cm-release.trigger-3.serious-concerns-large-fix" \
        "Trigger 3: TESTER recommendation=SHIP-WITH-SERIOUS-CONCERNS and at least one finding has Fix Effort=large for ${ACTIVE_RC} — the scope is too large to auto-ship." \
        "Review the large-effort finding(s) in ${_TESTER_REPORT_PATH:-the TESTER report for this RC}, scope the fix work (or an explicit operator decision to proceed anyway), then re-invoke scripts/cm/release.sh." \
        "yes" ;;
    *)
      # Out of --force's scope: an unrecognized-input catch-all.
      _cm_apply_matrix_trigger \
        "cm-release.ship-policy.halt" \
        "Ship-policy matrix returned HALT for ${ACTIVE_RC}: TESTER state=${_TESTER_STATUS} systemic_risk=${_TESTER_SYSTEMIC_RISK} recommendation=${_TESTER_RECOMMENDATION} — none of the known auto-ship rows matched." \
        "Cross-reference team/roles/CM.md's ship-policy decision matrix against these TESTER field values, decide the correct disposition, then re-invoke scripts/cm/release.sh." \
        "no" ;;
    esac
  fi
  # A decide-only run ends at the decision; Steps 4b, 4e and 4f do not run.
  [[ "${_CM_DECIDE_ONLY:-false}" == "true" ]] && return 0

  echo "  Proceeding with release: $_SHIP_DECISION (release notes Status: ${_RELEASE_NOTES_STATUS})"
  echo ""

  # --- Step 4b: Pre-squash hook (if configured); HALT on failure. ---
  # Invoked via callback — see "Why a callback" in this file's header doc.
  # This preserves the original code's execution order (4c, 4d, 4b, 4e, 4f)
  # exactly, including which HALT fires first when more than one gate would
  # otherwise trip in the same run.
  "$pre_squash_hook_step_fn"

  # --- Step 4e: Stamp release-notes ## Status placeholder with ship-policy decision ---
  # When WRITER authors release notes on the RC branch before the ship decision is known,
  # the notes file contains a "## Status: PENDING-RELEASE" placeholder (per CM.md convention).
  # This step replaces that placeholder with the actual ship decision value
  # (_RELEASE_NOTES_STATUS, set by Step 4d above) and commits the result on the RC branch
  # so the squash carries the stamped notes directly to main.
  #
  # If no WRITER-authored notes file exists for this RC, this step is a no-op — Step 11a
  # generates fresh notes with the correct status after the squash to main.
  #
  # Both placeholder checks read only the "## Status" section, outside HTML comments
  # (cm_release_notes_status_has_placeholder); the word quoted elsewhere — a template
  # comment, a rendered "## Bugs Skipped" rationale — is never the placeholder.
  # Idempotency: if the placeholder is absent from ## Status (already stamped or never
  # present), the step logs and skips — composing cleanly with idempotent re-run semantics.
  # HALT triggers: the placeholder survives under ## Status after the stamp (stamp bug);
  # the stamp exits non-zero; no recognizable ## Status line found (malformed notes).
  echo "[Step 4e] Checking for WRITER-authored release notes to stamp..."
  local _STAMP_NOTES_FILE="$REPO_ROOT/release-notes/${ACTIVE_RC}.md"
  if [[ ! -f "$_STAMP_NOTES_FILE" ]]; then
    echo "[Step 4e] No WRITER-authored release notes found at release-notes/${ACTIVE_RC}.md — skipping stamp (Step 11a will auto-generate)."
  else
    # File exists — check whether its ## Status section holds the PENDING-RELEASE placeholder.
    if ! cm_release_notes_status_has_placeholder "$_STAMP_NOTES_FILE"; then
      # Placeholder is absent.  Either already stamped (re-run) or WRITER used a different value.
      # Verify a recognizable ## Status line exists; warn if not.
      if grep -qE "^## Status" "$_STAMP_NOTES_FILE" 2>/dev/null; then
        echo "[Step 4e] step 4e: already complete, continuing — release-notes/${ACTIVE_RC}.md has no PENDING-RELEASE placeholder (already stamped or WRITER used a concrete status value)."
      else
        cm_halt \
          "cm-release.trigger-9.notes-missing-status-heading" \
          "Trigger 9: release-notes/${ACTIVE_RC}.md exists but has no recognizable '## Status' heading, so the release record cannot state the actual ship decision." \
          "Inspect ${_STAMP_NOTES_FILE}, add a '## Status' heading with the value ${_RELEASE_NOTES_STATUS}, commit it on the RC branch, then re-run scripts/cm/release.sh."
      fi
    else
      # Placeholder present — stamp. cm_stamp_release_notes_status also injects
      # non-empty "## Known Issues" / "## Bugs Skipped" blocks (see its doc).
      echo "[Step 4e] Stamping release-notes/${ACTIVE_RC}.md: PENDING-RELEASE -> ${_RELEASE_NOTES_STATUS}..."
      # Captured in-statement: under set -e a bare non-zero call would exit before the HALT below.
      local _stamp_py_rc=0
      cm_stamp_release_notes_status "$_STAMP_NOTES_FILE" "${_RELEASE_NOTES_STATUS}" "${_KNOWN_ISSUE_NOTES_BLOCK}" "${_BUGS_SKIPPED_NOTES_BLOCK}" \
        || _stamp_py_rc=$?
      if [[ $_stamp_py_rc -ne 0 ]]; then
        cm_halt \
          "cm-release.trigger-9.stamp-step-failed" \
          "Trigger 9: the release-notes stamp step exited non-zero (rc=${_stamp_py_rc}) for release-notes/${ACTIVE_RC}.md — the PENDING-RELEASE placeholder could not be replaced." \
          "Inspect ${_STAMP_NOTES_FILE} for a malformed '## Status' block, fix it by hand, then re-run scripts/cm/release.sh."
      fi

      # Guard (BLOCK-WORTHY): the placeholder must be gone from ## Status after the stamp.
      if cm_release_notes_status_has_placeholder "$_STAMP_NOTES_FILE"; then
        cm_halt \
          "cm-release.trigger-9.placeholder-survived" \
          "Trigger 9: release-notes/${ACTIVE_RC}.md still contains the literal 'PENDING-RELEASE' placeholder after the stamp step ran — the stamp logic did not apply cleanly." \
          "Inspect ${_STAMP_NOTES_FILE}, manually replace PENDING-RELEASE with ${_RELEASE_NOTES_STATUS} under '## Status', commit it, then re-run scripts/cm/release.sh."
      fi

      # Guard: verify a recognizable ## Status line exists in the stamped notes.
      if ! grep -qE "^## Status" "$_STAMP_NOTES_FILE" 2>/dev/null; then
        cm_halt \
          "cm-release.trigger-9.stamped-notes-missing-status-heading" \
          "Trigger 9: release-notes/${ACTIVE_RC}.md has no recognizable '## Status' heading after the stamp attempt." \
          "Inspect ${_STAMP_NOTES_FILE}, add a '## Status' heading with the value ${_RELEASE_NOTES_STATUS}, commit it, then re-run scripts/cm/release.sh."
      fi
      # One auto-ship line per stop the ship posture shipped through, under
      # "## Known Issues"; nothing under matrix (release_ship_posture.sh).
      cm_ship_posture_stamp_notes "$_STAMP_NOTES_FILE"

      echo "[Step 4e] Stamp complete. Committing stamped release notes on RC branch..."
      git_step "git add release-notes stamp" git -C "$REPO_ROOT" add "release-notes/${ACTIVE_RC}.md"
      if git -C "$REPO_ROOT" diff --cached --quiet 2>/dev/null; then
        echo "[Step 4e] step 4e: already complete, continuing — release notes stamp commit already present (no staged changes)."
      else
        local _stamp_commit_out="" _stamp_commit_rc=0
        _stamp_commit_out="$(git -C "$REPO_ROOT" commit -m "Stamp release-notes/${ACTIVE_RC}.md ## Status: ${_RELEASE_NOTES_STATUS}" 2>&1)" || _stamp_commit_rc=$?
        if [[ $_stamp_commit_rc -ne 0 ]]; then
          echo "" >&2
          echo "ERROR: git commit release-notes stamp failed (rc=${_stamp_commit_rc})" >&2
          echo "$_stamp_commit_out" >&2
          # CENSUS-ANCHOR: release-ship-policy-stamp-commit-failure-01
          exit 1
        else
          echo "$_stamp_commit_out"
        fi
      fi
      echo "[Step 4e] Release notes stamped and committed on ${RC_BRANCH}."
    fi
  fi

  # --- Step 4e (continued): stamp the known-issue carry-forward marker ---
  # Independent of notes-file existence above (the marker is a bugs-
  # directory write, not a notes write) so it lands on both notes paths.
  # Fires only on SHIP-KNOWN-ISSUE. Not committed to git: bugs/ is kanban
  # process state, not a path inside REPO_ROOT — same reasoning as
  # release_finalize.sh Step 16b's ## Fixed In writeback, which this step
  # otherwise mirrors (idempotent, tolerant, non-fatal on error).
  echo "[Step 4e] Stamping known-issue carry-forward marker on causing bug(s)..."
  if [[ "$_SHIP_DECISION" != "SHIP-KNOWN-ISSUE" ]]; then
    echo "[Step 4e] Ship decision is ${_SHIP_DECISION} — no carry-forward marker to write (the marker is exclusive to SHIP-KNOWN-ISSUE)."
  elif [[ "${#_KNOWN_ISSUE_BUG_FILES[@]}" -eq 0 ]]; then
    echo "[Step 4e] SHIP-KNOWN-ISSUE decision but no causing bug file resolved from the TESTER report's Filed As field(s) — no marker written." >&2
  else
    ( set +e; cm_stamp_known_issue_marker "$ACTIVE_RC" "${_KNOWN_ISSUE_BUG_FILES[@]}" ) \
      || echo "[Step 4e] WARNING: known-issue carry-forward marker write encountered an error — release continues." >&2
  fi

  # --- Step 4f: Squash pollution guard ---
  # Before squashing, inspect the set of files that the RC branch introduces
  # relative to the squash target (main).  Flag any file matching a
  # non-source / stray pattern:
  #
  #   (a) repo-root artifacts/          — task scratch escaped to wrong location
  #   (c) task-ID-named paths at repo   — CODER/TESTER/etc. dirs outside
  #       root or outside projects/*/   projects/<name>/tasks/
  #   (d) PRIORITY-*.md outside         — priority-intake files outside their
  #       projects/<name>/priority/       sanctioned project priority/ directory
  #   (e) requirements v*.md outside    — requirements-intake files outside their
  #       projects/<name>/requirements/   sanctioned project requirements/ directory
  #
  # Legitimate paths that MUST pass silently:
  #   projects/<name>/artifacts/**        — document deliverables
  #   projects/<name>/tasks/*/artifacts/**— per-task outputs
  #   projects/<name>/priority/PRIORITY-*.md  — correctly-placed priority files
  #   projects/<name>/requirements/v*.md      — correctly-placed requirements files
  #   team/templates/**                  — template source tree; files here are
  #                                        framework scaffolding, not intake strays
  #
  # Default mode: warn loudly (POLLUTION-GUARD WARN) for each flagged path.
  # Strict mode  (CM_SQUASH_STRICT_POLLUTION_GUARD=true): cm_halt after listing
  # all flagged paths.
  #
  # The guard logs to stdout so the release log captures every flagged path.
  # No files are modified or removed — the guard is observation-only.
  echo "[Step 4f] Running squash pollution guard on ${RC_BRANCH}..."
  local _guard_flagged=()
  local _guard_diff_base=""

  # Compute the merge-base of main and RC so the diff covers exactly
  # what the squash would commit (files introduced or changed on the RC).
  _guard_diff_base="$(git -C "$REPO_ROOT" merge-base "$MAIN_BRANCH" "$RC_BRANCH" 2>/dev/null || true)"
  if [[ -z "$_guard_diff_base" ]]; then
    echo "[Step 4f] WARNING: could not compute merge-base for pollution guard; skipping path scan." >&2
  else
    local _gpath _basename_gpath_d _basename_gpath_e
    while IFS= read -r _gpath; do
      [[ -z "$_gpath" ]] && continue

      # --- Templates tree: location-based exclusion ---
      # Files under team/templates/ are framework scaffolding (source), not
      # deposited intake items.  Skip all pattern checks for this tree so that
      # legitimate template files (BUG-TEMPLATE.md, PRIORITY-TEMPLATE.md, etc.)
      # are never flagged — regardless of their names.  A real intake stray
      # deposited in an actual intake/backlog directory will NOT match this
      # prefix and will still be caught by the patterns below.
      if [[ "$_gpath" == team/templates/* ]]; then
        continue
      fi

      # --- Pattern (a): repo-root artifacts/ tree ---
      # Any path starting with "artifacts/" at the repo root is stray.
      # Legitimate: projects/<name>/artifacts/ (starts with "projects/")
      if [[ "$_gpath" == artifacts/* ]]; then
        _guard_flagged+=("$_gpath  [stray: repo-root artifacts/]")
        continue
      fi

      # --- Pattern (c): task-ID-named dirs outside projects/<name>/tasks/ ---
      # Matches the canonical task-ID shape: ROLE-YYYYMMDD-NNN-slug
      # Legitimate: projects/<name>/tasks/ROLE-YYYYMMDD-NNN-slug/
      # Stray: ROLE-YYYYMMDD-NNN-slug/ at repo root OR under team/ etc.
      if grep -qE '^[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' <<< "$_gpath" ; then
        _guard_flagged+=("$_gpath  [stray: task-ID-named path outside projects/<name>/tasks/]")
        continue
      fi
      if grep -qE '^team/[A-Z]+-[0-9]{8}-[0-9]+-[a-z0-9-]+/' <<< "$_gpath" ; then
        _guard_flagged+=("$_gpath  [stray: task-ID-named path under team/ (outside projects/<name>/tasks/)]")
        continue
      fi

      # --- Pattern (d): PRIORITY-*.md misplaced intake files ---
      # Sanctioned location: projects/<name>/priority/PRIORITY-*.md
      # Any PRIORITY-*.md outside projects/<name>/priority/ is stray.
      _basename_gpath_d="$(basename "$_gpath")"
      if [[ "$_basename_gpath_d" == PRIORITY-*.md ]]; then
        if [[ "$_gpath" != projects/*/priority/PRIORITY-*.md ]]; then
          _guard_flagged+=("$_gpath  [stray: priority-intake file outside projects/<name>/priority/]")
          continue
        fi
      fi

      # --- Pattern (e): requirements v*.md misplaced intake files ---
      # Sanctioned location: projects/<name>/requirements/v*.md
      # Any v*.md (requirements version file) outside projects/<name>/requirements/ is stray.
      _basename_gpath_e="$(basename "$_gpath")"
      if [[ "$_basename_gpath_e" == v*.md ]]; then
        if [[ "$_gpath" != projects/*/requirements/v*.md ]]; then
          _guard_flagged+=("$_gpath  [stray: requirements-intake file outside projects/<name>/requirements/]")
          continue
        fi
      fi

    done < <(git -C "$REPO_ROOT" diff --name-only "$_guard_diff_base" "$RC_BRANCH" 2>/dev/null)
  fi

  if [[ "${#_guard_flagged[@]}" -gt 0 ]]; then
    echo "" >&2
    echo "[cm-release] POLLUTION-GUARD: ${#_guard_flagged[@]} stray path(s) detected in squash set for ${ACTIVE_RC}:" >&2
    local _gf
    for _gf in "${_guard_flagged[@]}"; do
      echo "[cm-release] POLLUTION-GUARD WARN:  ${_gf}" >&2
    done
    echo "[cm-release] POLLUTION-GUARD: These paths will be committed into release history unless removed before squash." >&2
    echo "[cm-release] POLLUTION-GUARD: To prevent absorption: operator should remove/unstage the stray files and re-run." >&2
    echo "[cm-release] POLLUTION-GUARD: Set CM_SQUASH_STRICT_POLLUTION_GUARD=true to halt instead of warn." >&2
    echo ""
    if [[ "${CM_SQUASH_STRICT_POLLUTION_GUARD:-false}" == "true" ]]; then
      cm_halt \
        "cm-release.trigger-10.pollution-guard-strict" \
        "Trigger 10: the squash pollution guard (strict mode) found ${#_guard_flagged[@]} stray path(s) that would be absorbed into release history by squashing ${ACTIVE_RC}." \
        "Remove or unstage the stray path(s) listed above from ${RC_BRANCH}, commit the cleanup, then re-run scripts/cm/release.sh (or set CM_SQUASH_STRICT_POLLUTION_GUARD=false to warn instead of halt)."
    fi
  else
    echo "[Step 4f] Squash pollution guard: no stray paths detected — proceeding."
  fi
}
