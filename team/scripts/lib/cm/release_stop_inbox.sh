#!/usr/bin/env bash
# team/scripts/lib/cm/release_stop_inbox.sh
#
# CM release stage: the operator inbox entry an integrity-invariant stop
# leaves. Triggers 4, 10 and 11, the pre-tag hook and the pre-tag VERSION
# gate stay real stops (BLOCK-WORTHY); each also writes one entry carrying
# the stop's error key, reason and remedy, so a returning operator reads the
# cause in the inbox. Sourced by release_preflight.sh, so every caller of
# cm_halt (whose --inbox flag calls the first helper) resolves both.
#
#   cm_release_stop_inbox_entry <error_key> <message> <remedy> <halted>
#   cm_release_version_gate_report <on_disk> <expected>
#
# Globals read: PROJECT_NAME, ACTIVE_RC, KANBAN_ROOT (else
# PGAI_AGENT_KANBAN_ROOT_PATH), PROJECT_HALT_FILE, REPO_ROOT, RELEASE_TAG,
# _CM_VERIFICATION_COMPLETE. Dependencies, resolved at call time:
# pgai_emit_error_contract (error_contract.sh) and pp_run_ops (pp_run_ops.sh;
# when absent the inbox write is narrated as not written). No inline Python:
# the entry is written by pgai_agent_kanban.overwatch.inbox.

# Guard against double-sourcing.
if declare -F cm_release_stop_inbox_entry >/dev/null 2>&1; then
    return 0
fi

# cm_release_stop_inbox_entry <error_key> <message> <remedy> <halted>
#   error_key, message, remedy — the stop's own dual-channel triple, carried
#               verbatim: the question names the key and the message, the
#               how-to-answer field starts with the remedy.
#   halted    — "yes" when cm_halt has already written the project HALT file
#               and HALT Event; "no" for a stop that exits without a HALT file
#               (the pre-tag hook failure, the pre-tag VERSION gate).
# Written through the inbox module (pgai_agent_kanban.overwatch.inbox
# write_entry --once, so a re-run that meets the same stop leaves one open
# entry). Call it after every other write of the stop: it never prevents,
# delays or replaces the HALT file, the HALT Event or the exit status. A
# write that fails (or an inbox module that cannot run) is one WARNING on
# stderr naming the command that writes the entry by hand; the function
# always returns 0.
cm_release_stop_inbox_entry() {
  local error_key="$1"
  local message="$2"
  local remedy="$3"
  local halted="${4:-no}"
  local version="${ACTIVE_RC:-}"
  local source="rc:${version}"
  [[ -n "$version" ]] || { source="scripts/cm/release.sh"; version="The release"; }
  local scripts_dir inbox_list release_cmd
  scripts_dir="${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}/scripts"
  inbox_list="$(printf '%q' "${scripts_dir}/inbox.sh") --project $(printf '%q' "${PROJECT_NAME:-}") list"
  release_cmd="$(printf '%q' "${scripts_dir}/cm/release.sh") --project $(printf '%q' "${PROJECT_NAME:-}")"
  local shipped="The release did not ship."
  if [[ "${_CM_VERIFICATION_COMPLETE:-0}" == "1" && -n "${RELEASE_TAG:-}" ]]; then
    shipped="The release is already tagged ${RELEASE_TAG}; do not cancel it."
  fi
  local question meanwhile how_to_answer
  question="${version} stopped at a release integrity check (error ${error_key}): ${message} Fix the cause and resume the release?"
  if [[ "$halted" == "yes" ]]; then
    meanwhile="${shipped} Wrote the project HALT file (${PROJECT_HALT_FILE:-unknown}) and a HALT Event in release-state.md with the same error, reason and remedy; this project's chain is paused and other projects keep running."
    how_to_answer="Remedy: ${remedy}"$'\n'"Then resume this project's chain with \`$(printf '%q' "${scripts_dir}/unhalt.sh") --project $(printf '%q' "${PROJECT_NAME:-}")\`. List this entry with \`${inbox_list}\` and close it with the same script's close command."
  else
    meanwhile="${shipped} scripts/cm/release.sh exited 1 before cutting the tag and returned the main branch to its pre-release commit. No HALT file was written; the CM release task records the stop."
    how_to_answer="Remedy: ${remedy}"$'\n'"Then run the release again with \`${release_cmd}\`. List this entry with \`${inbox_list}\` and close it with the same script's close command."
  fi
  local -a args=(write_entry --kanban-root "${KANBAN_ROOT:-${PGAI_AGENT_KANBAN_ROOT_PATH:-}}" --project "${PROJECT_NAME:-}"
                 --source "$source" --question "$question" --meanwhile "$meanwhile"
                 --how-to-answer "$how_to_answer" --once)
  local out="" rc=0
  if declare -F pp_run_ops >/dev/null 2>&1; then
    out="$(pp_run_ops pgai_agent_kanban.overwatch.inbox "${args[@]}" 2>&1)" || rc=$?
  else
    out="pp_run_ops is not loaded (team/scripts/lib/pp_run_ops.sh was not sourced)"
    rc=127
  fi
  local entry_id=""
  [[ $rc -eq 0 && "$out" =~ (INBOX-[0-9]+) ]] && entry_id="${BASH_REMATCH[1]}"
  if [[ -n "$entry_id" ]]; then
    echo "[cm-release] Operator inbox: ${entry_id} records this stop (${error_key})." >&2
  else
    echo "[cm-release] WARNING: the operator inbox entry for this stop (${error_key}) could not be written (exit ${rc}: ${out//$'\n'/ }); the stop above stands unchanged. Write it by hand: $(printf '%q' "${scripts_dir}/lib/pp_run_ops.sh") pgai_agent_kanban.overwatch.inbox $(printf '%q ' "${args[@]}")" >&2
  fi
  return 0
}

# cm_release_version_gate_report <on_disk> <expected>
# The pre-tag VERSION gate's stop report: the Cause(s)/To-fix/Rule block on
# stderr, the dual-channel cm-release.pre-tag-version-mismatch triple, then
# the inbox entry (no HALT file). Returns 0; the caller exits 1 (Step 13,
# release_finalize.sh). Reads REPO_ROOT, RELEASE_TAG, KANBAN_ROOT,
# PROJECT_NAME.
cm_release_version_gate_report() {
  local on_disk="$1"
  local expected="$2"
  echo "" >&2
  echo "[Pre-tag VERSION gate] FAIL — on-disk VERSION does not match the tag about to be created. No tag has been created." >&2
  echo "" >&2
  echo "Cause(s):" >&2
  echo "  - ${REPO_ROOT}/VERSION contains '${on_disk}', expected '${expected}' (release tag ${RELEASE_TAG})" >&2
  echo "" >&2
  echo "To fix:" >&2
  echo "  1. printf '%s\n' '${expected}' > '${REPO_ROOT}/VERSION'" >&2
  echo "  2. git -C '${REPO_ROOT}' add VERSION && git -C '${REPO_ROOT}' commit -m 'Fix VERSION for ${expected}'" >&2
  echo "  3. Re-run scripts/cm/release.sh." >&2
  echo "" >&2
  echo "Rule: the committed VERSION file must equal the release tag being cut, or the shipped tag would misreport the version." >&2
  # The same cause and fix as one dual-channel triple, also carried by the
  # operator inbox entry (written last; never fatal).
  local _vg_remedy _vg_key="cm-release.pre-tag-version-mismatch"
  local _vg_msg="Pre-tag VERSION gate: ${REPO_ROOT}/VERSION contains '${on_disk}', expected '${expected}' (release tag ${RELEASE_TAG}); no tag was created, because the shipped tag would misreport the version."
  _vg_remedy="printf '%s\\n' $(printf '%q' "$expected") > $(printf '%q' "${REPO_ROOT}/VERSION") && git -C $(printf '%q' "$REPO_ROOT") add VERSION && git -C $(printf '%q' "$REPO_ROOT") commit -m $(printf '%q' "Fix VERSION for ${expected}") && $(printf '%q' "${KANBAN_ROOT}/scripts/cm/release.sh") --project $(printf '%q' "$PROJECT_NAME")"
  pgai_emit_error_contract "$_vg_key" "$_vg_msg" "$_vg_remedy"
  cm_release_stop_inbox_entry "$_vg_key" "$_vg_msg" "$_vg_remedy" no
  return 0
}
