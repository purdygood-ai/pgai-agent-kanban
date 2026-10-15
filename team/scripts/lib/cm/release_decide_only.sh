#!/usr/bin/env bash
# team/scripts/lib/cm/release_decide_only.sh
#
# CM release decide-only stage: what `cm/release.sh --decide-only` adds to
# the release driver. A decide-only run resolves the project, finds and
# validates the active RC (cm_release_preflight), then runs the ship-policy
# matrix with the project's ship posture applied (Steps 4c and 4d of
# cm_release_apply_ship_policy) and stops there. It prints the decision a
# full run would reach and changes nothing: no fetch or push, no checkout,
# no commit, branch or tag, no HALT file, no release-state.md write, no bug
# filed, no release-notes stamp and no refusal-ledger record.
#
# Sourced by team/scripts/cm/release.sh beside the other lib/cm stages. The
# stages it hooks into read one global, _CM_DECIDE_ONLY, and call into this
# file only when it is "true"; when it is unset every stage behaves exactly
# as it does without this file.
#
# Output contract
# ----------------
# Every narration line the stages print goes to stderr; stdout carries only
# the decision:
#
#   ship-policy: decision=<SHIP-…|HALT> posture=<matrix|always> trigger=<n|none> notes_status=<…>
#
# decision      the ship decision the full run would reach (SHIP-FUNCTIONAL,
#               SHIP-KNOWN-BUGS, SHIP-NON-FUNCTIONAL, SHIP-KNOWN-ISSUE) or HALT.
# posture       the resolved ship posture (pp_ship_posture).
# trigger       on HALT, the trigger number of the stop ("none" for the
#               unrecognized-TESTER-state and no-row-matched stops). On a
#               ship, each trigger the always posture shipped through, in
#               the order they fired, comma-separated when more than one
#               ("8,2"); "none" when nothing would have stopped.
# notes_status  the release-notes ## Status value the ship would stamp;
#               "none" on HALT.
#
# On HALT the line is followed by the would-be HALT's dual-channel contract
# (error/message/remedy, team/scripts/lib/error_contract.sh), whose
# "error:" line is the HALT error key the full run writes to the project's
# HALT file.
#
# Exit status: 0 for every SHIP-* decision, 3 for HALT. A run that cannot
# reach a decision (no project context, no RC branch, a release-state.md
# that disagrees with the branch) exits 1 from the preflight exactly as a
# full run does, with nothing on stdout.
#
# Public entry points
# --------------------
#   cm_release_decide_only_begin
#       Called by release.sh before cm_release_preflight. Sets
#       _CM_DECIDE_ONLY=true, keeps Python from writing bytecode caches, and
#       moves stdout to stderr while keeping the original stdout on a saved
#       descriptor for the decision line.
#   cm_release_decide_only_locate_notes
#       Called by cm_release_preflight in place of Step 4a's checkout. When
#       the dev tree already has the RC branch checked out, Step 4c reads
#       its release-notes/ directly, as a full run does. Otherwise the RC
#       branch's release-notes/ is extracted (git archive, which writes
#       nothing to the repository) into a scratch directory under the
#       framework temp root, and Step 4c reads that copy — the same files a
#       full run reads after its checkout. Sets _CM_DECIDE_ONLY_NOTES_DIR
#       and _CM_DECIDE_ONLY_SCRATCH; cm_release_cleanup_on_exit removes the
#       scratch directory.
#   cm_release_decide_only_skip_bug <field> <source_path>
#       Called by _cm_ship_policy_file_input_bug (release_ship_policy.sh) in
#       place of the bug filer. Narrates the bug a full run would file or
#       cite and leaves _CM_LAST_FILED_BUG_ID empty.
#   cm_release_decide_only_halt <error_key> <message> <remedy>
#       Called by cm_halt (release_preflight.sh) before it writes anything.
#       Prints the HALT decision line and the contract, then exits 3.
#   cm_release_decide_only_report
#       Called by release.sh once cm_release_apply_ship_policy returns with
#       a ship decision. Prints the decision line and exits 0.
#
# Globals read: _SHIP_DECISION, _SHIP_POSTURE, _RELEASE_NOTES_STATUS,
# _CM_AUTO_SHIP_EVENTS (release_ship_posture.sh), REPO_ROOT, RC_BRANCH.
#
# Dependencies (sourced by the caller): team/scripts/lib/error_contract.sh
# (pgai_format_error_contract), team/scripts/lib/temp.sh (pgai_mktemp_d,
# pgai_temp_cleanup; loaded by project_paths.sh), release_ship_posture.sh
# (cm_ship_policy_trigger_number, at run time).

# Guard against double-sourcing.
if declare -F cm_release_decide_only_begin >/dev/null 2>&1; then
    return 0
fi

# Off until cm_release_decide_only_begin turns it on: an inherited
# environment value never switches a full run into decide-only mode.
_CM_DECIDE_ONLY="false"
_CM_DECIDE_ONLY_NOTES_DIR=""
_CM_DECIDE_ONLY_SCRATCH=""

# Helper: enter decide-only mode (see the header doc).
cm_release_decide_only_begin() {
  _CM_DECIDE_ONLY="true"
  export PYTHONDONTWRITEBYTECODE=1
  exec {_CM_DECIDE_ONLY_FD}>&1 1>&2
  echo "[cm-release] decide-only: running preflight and the ship-policy matrix; nothing is written. The decision is printed on stdout."
}

# Helper: point Step 4c at the RC branch's release-notes/ without a checkout.
cm_release_decide_only_locate_notes() {
  local current_branch
  current_branch="$(git -C "$REPO_ROOT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  if [[ "$current_branch" == "$RC_BRANCH" ]]; then
    echo "[Step 4a] decide-only: ${RC_BRANCH} is already checked out — Step 4c reads release-notes/ from the dev tree."
    return 0
  fi
  _CM_DECIDE_ONLY_SCRATCH="$(pgai_mktemp_d cm-release-decide-only)"
  _CM_DECIDE_ONLY_NOTES_DIR="${_CM_DECIDE_ONLY_SCRATCH}/release-notes"
  if git -C "$REPO_ROOT" archive --format=tar "$RC_BRANCH" -- release-notes 2>/dev/null \
      | tar -x -C "$_CM_DECIDE_ONLY_SCRATCH" 2>/dev/null; then
    echo "[Step 4a] decide-only: no checkout — Step 4c reads ${RC_BRANCH}'s release-notes/ from a scratch copy."
  else
    # No release-notes/ on the RC branch: Step 4c then finds no directory
    # and skips the pattern check, as a full run does after its checkout.
    rm -rf "$_CM_DECIDE_ONLY_NOTES_DIR"
    echo "[Step 4a] decide-only: no checkout — ${RC_BRANCH} has no release-notes/ directory."
  fi
  return 0
}

# Helper: stand in for the ship-policy bug filer.
# Usage: cm_release_decide_only_skip_bug <field> <source_path>
cm_release_decide_only_skip_bug() {
  echo "  Bug filer:            decide-only — a full run files or cites a bug for ${1} in ${2}; nothing is written" >&2
  _CM_LAST_FILED_BUG_ID=""
  return 0
}

# Helper: the trigger field of the decision line for a ship decision.
_cm_release_decide_only_ship_triggers() {
  local entry timestamp trigger error_key filed_bug joined=""
  for entry in "${_CM_AUTO_SHIP_EVENTS[@]}"; do
    IFS=$'\t' read -r timestamp trigger error_key filed_bug <<< "$entry"
    joined="${joined:+${joined},}${trigger}"
  done
  echo "${joined:-none}"
}

# Helper: print the decision line on the saved stdout.
# Usage: _cm_release_decide_only_line <decision> <trigger> <notes_status>
_cm_release_decide_only_line() {
  printf 'ship-policy: decision=%s posture=%s trigger=%s notes_status=%s\n' \
    "$1" "${_SHIP_POSTURE:-matrix}" "$2" "${3:-none}" >&"${_CM_DECIDE_ONLY_FD:-1}"
}

# Helper: report the HALT a full run would write, and stop.
# Usage: cm_release_decide_only_halt <error_key> <message> <remedy>
cm_release_decide_only_halt() {
  local error_key="$1" message="$2" remedy="$3"
  echo "[cm-release] decide-only: a full run HALTs here (${error_key}); no HALT file or release-state.md event is written." >&2
  _cm_release_decide_only_line "HALT" "$(cm_ship_policy_trigger_number "$error_key")" "none"
  printf '%s\n' "$(pgai_format_error_contract "$error_key" "$message" "$remedy")" >&"${_CM_DECIDE_ONLY_FD:-1}"
  # BLOCK-WORTHY: the decide-only report of a ship-policy HALT -- the stop
  # itself is the censused trigger named by error_key; this exit only
  # reports it (status 3) and writes nothing.
  # CENSUS-ANCHOR: release-decide-only-halt-01
  exit 3
}

# Helper: report a ship decision, and stop.
cm_release_decide_only_report() {
  _cm_release_decide_only_line "${_SHIP_DECISION}" "$(_cm_release_decide_only_ship_triggers)" "${_RELEASE_NOTES_STATUS:-none}"
  exit 0
}
