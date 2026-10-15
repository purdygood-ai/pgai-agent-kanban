#!/usr/bin/env bash
# team/scripts/lib/cm/release_operator_override.sh
#
# CM release operator-force-override stage: the single helper that decides,
# for one named ship-policy MATRIX trigger, whether to HALT as before or to
# pass the stop because cm/release.sh's operator-only --force / --reason
# flags are active and that trigger is in the flag's enumerated scope.
# Sourced by team/scripts/cm/release.sh; split into its own file (rather
# than left inline in release_ship_policy.sh, where the logic was first
# written) to keep that file under the shared per-file size ceiling
# (docs/coding-standards.md; enforced by
# team/scripts/lint_engine_size_dupe.py) — the same reason
# release_known_issue.sh was split out of release_ship_policy.sh earlier.
#
# Public entry point
# -------------------
#   _cm_apply_matrix_trigger <error_key> <message> <remedy> <overridable:yes|no>
#       Called only from release_ship_policy.sh's cm_release_apply_ship_policy,
#       once per named ship-policy matrix trigger, in place of a direct
#       cm_halt call. See its own header doc below for the full contract.
#
# Operator --force override scope
# ---------------------------------
# release.sh's --force / --reason flags (parsed and refused-if-invalid in
# release.sh itself, before this file or release_ship_policy.sh ever runs)
# pass exactly three named ship-policy matrix stops, enumerated at each of
# release_ship_policy.sh's call sites: trigger-1 (TESTER BLOCKED), trigger-2
# (systemic_risk=high), and trigger-3 (SHIP-WITH-SERIOUS-CONCERNS + a
# large-fix finding) — the matrix's own judgement calls about THIS release's
# findings. Everything else the matrix can HALT on is explicitly OUT of
# --force's reach, by default and by enumeration at those same call sites:
#   - the found-but-unrecognized-TESTER-state row and the "no known matrix
#     row matched" catch-all (a data-integrity guard, not a judgement call —
#     a HALT here means the inputs themselves are untrustworthy, which an
#     operator's opinion about severity cannot fix);
#   - Step 4c's last-3-RCs-NON-FUNCTIONAL pattern guard (trigger-8) — a
#     deliberate design decision: this guard fires on a PATTERN across
#     several releases (the autonomous chain repeatedly failing to
#     self-correct), not a judgement about the release in front of it, so it
#     is a systemic stop the matrix-only flag may not pass. It is passed
#     instead by acknowledgment: the guard records the window of three
#     releases it halted on, the operator's unhalt acknowledges it, and a
#     re-run over the same window does not halt again
#     (release_ship_policy_report.sh, cm_record_non_functional_pattern_window);
#   - Step 4e's release-notes stamp guards (trigger-9) and Step 4f's squash
#     pollution guard (trigger-10), and every gate outside release_ship_policy.sh
#     entirely (squash conflicts, the fidelity gate, hook failures, push
#     failures) — mechanical integrity checks, never routed through this
#     helper at all.
# A future matrix row is therefore out of --force's scope unless a later
# change explicitly routes its HALT through _cm_apply_matrix_trigger with
# overridable=yes.
#
# Dependencies (must already be sourced by the caller before this file)
# -----------------------------------------------------------------------
#   team/scripts/lib/cm/release_preflight.sh — cm_halt,
#     _cm_record_operator_override_event
#   release.sh's own argument parser — FORCE_ARG, FORCE_REASON (validated
#     non-empty and refused-if-run-under-an-agent-wake before this file's
#     entry point is ever reached)
#
# The caller must also have `set -euo pipefail` enabled.

# Guard against double-sourcing.
if declare -F _cm_apply_matrix_trigger >/dev/null 2>&1; then
    return 0
fi

# Helper: apply either a HALT or an operator-forced pass-through for one
# named ship-policy MATRIX trigger (never call this for trigger-8/9/10 or
# any other non-matrix refusal — those always call cm_halt directly).
# Usage: _cm_apply_matrix_trigger <error_key> <message> <remedy> <overridable:yes|no>
#
# When release.sh's --force flag is not set, or the caller passes
# overridable=no, this is a plain call-through to cm_halt with the same
# three arguments — byte-identical to the pre-existing behavior for every
# unforced invocation and for every out-of-scope trigger.
#
# When --force IS set (FORCE_ARG=true, validated non-empty FORCE_REASON —
# both already enforced by release.sh before this file's entry point ever
# runs) and overridable=yes, the HALT is passed instead: it records the
# Operator Override event for the project's release-state.md
# (_cm_record_operator_override_event, release_preflight.sh — appended by
# release_finalize.sh's Step 12 post-write block, same deferred-write
# pattern as that file's existing _CM_AUTORESOLVE_LOG), sets
# _RELEASE_NOTES_STATUS so the notes stamp (release_ship_policy.sh's Step 4e,
# and the auto-generated-notes path in release_notes.sh) records
# SHIPPED-BY-OPERATOR-OVERRIDE instead of the ordinary decision, and sets
# _SHIP_DECISION to SHIP-FORCED so the "Proceeding with release" log line
# reads correctly. Nothing here is persisted as a MODE: FORCE_ARG/
# FORCE_REASON are release.sh's own process-local shell variables, never
# written to project.cfg or any file this or a later invocation reads back —
# the release-state.md event is a record of what happened, not a switch a
# later invocation could inherit. The very next cm/release.sh invocation,
# run with no --force flag, applies the matrix normally.
_cm_apply_matrix_trigger() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  local overridable="$4"

  if [[ "${FORCE_ARG:-false}" == "true" && "$overridable" == "yes" ]]; then
    echo "" >&2
    echo "[cm-release] OPERATOR OVERRIDE: --force is passing matrix stop '${error_key}' for ${ACTIVE_RC}." >&2
    echo "[cm-release] Reason: ${FORCE_REASON}" >&2
    _cm_record_operator_override_event "$ACTIVE_RC" "$error_key" "$FORCE_REASON"
    _RELEASE_NOTES_STATUS="SHIPPED-BY-OPERATOR-OVERRIDE"
    _SHIP_DECISION="SHIP-FORCED"
    return 0
  fi

  cm_halt "$error_key" "$message" "$remedy"
}
