#!/usr/bin/env bash
# team/scripts/lib/wake_governance_composer.sh
#
# Wake governance-composer stage: turns the dispatched role's read-order
# manifest into the governance-stack sentence every wake prompt embeds
# after "read the governance stack: ". The resolver (read_order.sh) knows
# the manifest format; this file knows the prose. One of the single-concern
# lib stages of the shared wake pipeline, sourced by wake_driver_invoke.sh
# itself (self-located, so the composer is defined whether the invoke stage
# is loaded through wake_driver.sh or sourced on its own by a test), and
# kept separate so the invoke stage stays under the engine-decomposition
# line ceiling (team/scripts/lint_engine_size_dupe.py). Behavior-preserving:
# the sentence, the fallback list, and the log line are unchanged by this
# file existing.
#
# Public entry point
# -------------------
#   _wd_build_governance_line <subagent> <role> <team_root> <project_readme_line>
#       Writes the composed sentence into the caller's own
#       `_wd_governance_line` variable (bash dynamic scoping; never echoed).
#       Full contract in the function header below and in
#       docs/read-order-manifests.md ("The composer contract").
#
# Dependencies (must already be defined by the caller before this function
# runs)
# -----------------------------------------------------------------------
#   read_order_resolve_role (read_order.sh, sourced by wake_driver.sh ahead
#   of the invoke stage; a test sources it directly) and log()
#   (wake_bootstrap.sh) for the fallback narration. LOG_FILE, when set,
#   receives the resolver's stderr.

# ---------------------------------------------------------------------------
# _wd_build_governance_line <subagent> <role> <team_root> <project_readme_line>
# (private)
#
# Resolves <subagent>'s read-order manifest (<team_root>/read-order/
# <subagent>.txt via read_order_resolve_role) and joins the resulting files,
# in manifest order, into the prose fragment _wd_build_task_prompt's heredoc
# (wake_driver_invoke.sh) embeds after "read the governance stack: ". Writes
# the result into the caller's own `_wd_governance_line` local (bash dynamic
# scoping — this function declares no local of that name itself, matching
# the caller-writes contract documented at the top of wake_driver_invoke.sh
# for wd_invoke_task) rather than echoing it, because echoing would require
# capturing this function's stdout via command substitution, and the log()
# call in the fallback branch below tees to stdout — capturing that call's
# output would silently splice the log line into the governance sentence.
#
# <project_readme_line> is appended after the last manifest entry (the role
# file), so the project layer is read after the role layer — it is resolved
# by the caller at runtime, never a manifest line itself (see
# read-order/*.txt headers).
#
# Falls back to the pre-manifest hardcoded five-document list, unchanged,
# when the manifest resolves zero files (missing manifest file, or every
# listed line names a file that does not exist) — narrated via log() so an
# operator can see the degradation happened. This is prompt-content
# assembly, not a gate: it never refuses and never aborts a wake.
# ---------------------------------------------------------------------------
_wd_build_governance_line() {
  local _wd_gov_subagent="$1"
  local _wd_gov_role="$2"
  local _wd_gov_team_root="$3"
  local _wd_gov_readme_line="${4:-}"

  local -a _wd_governance_files=()
  local _wd_gov_path
  while IFS= read -r _wd_gov_path; do
    [[ -n "$_wd_gov_path" ]] && _wd_governance_files+=("$_wd_gov_path")
  done < <(read_order_resolve_role "$_wd_gov_subagent" "$_wd_gov_team_root" 2>>"${LOG_FILE:-/dev/null}")

  if [[ ${#_wd_governance_files[@]} -eq 0 ]]; then
    _wd_governance_files=(
      "${_wd_gov_team_root}/DIRECTIVES.md"
      "${_wd_gov_team_root}/OVERVIEW.md"
      "${_wd_gov_team_root}/SOP-core.md"
      "${_wd_gov_team_root}/README.md"
      "${_wd_gov_team_root}/roles/${_wd_gov_role}.md"
    )
    log "read_order: no manifest resolved for role '${_wd_gov_subagent}' -- governance stack fell back to the hardcoded list"
  fi

  # The entry that resolves to this role's own role file (always last in
  # every shipped manifest today) keeps the "your role file at" phrasing so
  # the fallback sentence is byte-identical to the pre-manifest text.
  local _wd_gov_role_file="${_wd_gov_team_root}/roles/${_wd_gov_role}.md"
  local _wd_gov_count=${#_wd_governance_files[@]}
  local _wd_gov_idx=0
  local _wd_gov_entry _wd_gov_text
  _wd_governance_line=""
  for _wd_gov_entry in "${_wd_governance_files[@]}"; do
    _wd_gov_text="$_wd_gov_entry"
    _wd_gov_idx=$(( _wd_gov_idx + 1 ))
    if [[ "$_wd_gov_idx" -eq "$_wd_gov_count" && "$_wd_gov_entry" == "$_wd_gov_role_file" ]]; then
      _wd_gov_text="your role file at ${_wd_gov_text}"
    fi
    if [[ -z "$_wd_governance_line" ]]; then
      _wd_governance_line="$_wd_gov_text"
    elif [[ "$_wd_gov_idx" -eq "$_wd_gov_count" ]]; then
      _wd_governance_line="${_wd_governance_line}, and ${_wd_gov_text}"
    else
      _wd_governance_line="${_wd_governance_line}, ${_wd_gov_text}"
    fi
  done
  # The project layer (projects/<name>/README.md) is read after the role
  # file: kanban-wide law, then the role, then the project, then the task
  # (OVERVIEW.md, "How to read the kanban's instruction stack").
  if [[ -n "$_wd_gov_readme_line" ]]; then
    _wd_governance_line="${_wd_governance_line}${_wd_gov_readme_line/# and your project README at/, then your project README at}"
  fi
}
