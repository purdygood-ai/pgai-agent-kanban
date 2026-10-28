#!/usr/bin/env bash
# team/scripts/lib/wake_readme_meta.sh
#
# Wake README-metadata stage: pure readers for the per-task README fields
# that drive dispatch — ## Role, ## Assigned Agent, and ## Force Model / ##
# Model Override — plus the shared role-name validator both readers feed
# into. Extracted from team/scripts/lib/wake_common.sh's former inline body
# so wake_common.sh's own remaining code stays a thinner shell over sourced
# lib stages. Behavior-preserving: no stdout/stderr line, exit code, or
# on-disk file content changes as a result of this file existing — it is
# one stage split out of a larger driver so each stays a focused,
# independently testable unit.
#
# Public entry point
# -------------------
#   get_role_from_readme <readme-path>
#       Extracts a task README's raw ## Role value, uppercased and trimmed.
#       Called by both wake provider scripts (claude.sh, codex.sh) during
#       task dispatch, before the extracted value is validated against
#       role_is_valid_agent and used to select a subagent.
#
# Other functions in this file — get_assigned_agent_from_readme,
# role_is_valid_agent, get_model_override_from_readme — are shared
# primitives, not private helpers of get_role_from_readme alone. Both wake
# provider scripts call all four directly during dispatch. Neither reader
# validates or defaults the value it extracts; callers fail closed (BLOCKED,
# never a silent CODER fallback) on a bad or unrecognized value. Defined at
# source time, available as soon as this file is sourced.
#
# Dependencies
# ------------
#   md_field.sh — read_md_field, the one bash section reader; self-sourced
#   below unless the caller already defines it. Every README section here
#   is read with the README reading: any letter case, any run of blanks
#   after "##", and the next "##" line -- a "###" subheading included --
#   ends the section. python3 on PATH (the reader runs as a module).
#
# Sourced by wake_common.sh alongside its other README-metadata-adjacent
# stages; has no dependency on wake_bootstrap.sh or wake_task_state.sh
# globals, so sourcing order relative to those two is not load-bearing.

# shellcheck source=md_field.sh
declare -F read_md_field >/dev/null || source "$(dirname "${BASH_SOURCE[0]}")/md_field.sh"

# get_role_from_readme <readme-path>
# Extracts the raw ## Role value, uppercased and trimmed; a "CODER | WRITER"
# choice list reads as its first entry. Prints an empty string when the
# section is absent or empty; returns 1, printing nothing, when the README
# cannot be read. Does NOT validate or default the value — callers must
# check the result against role_is_valid_agent and fail closed (BLOCKED,
# never a silent CODER fallback) on a bad value.
get_role_from_readme() {
  local value rc=0
  value="$(read_md_field --end-at-subheading "$1" Role)" || rc=$?
  [[ $rc -eq 1 ]] && return 1
  value="${value%%|*}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "${value^^}"
}

# get_assigned_agent_from_readme <readme-path>
# Extracts the raw ## Assigned Agent value, uppercased and trimmed (first
# entry of a "|" choice list, as get_role_from_readme). Prints an empty
# string when the section is absent, empty, or explicitly "none"
# (case-insensitive) — those all mean "not present" to dispatch; returns 1,
# printing nothing, when the README cannot be read. A non-empty result may
# still be invalid; callers validate with role_is_valid_agent before using
# it to select a subagent.
get_assigned_agent_from_readme() {
  local value rc=0
  value="$(read_md_field --end-at-subheading "$1" "Assigned Agent")" || rc=$?
  [[ $rc -eq 1 ]] && return 1
  value="${value%%|*}"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  value="${value^^}"
  [[ "$value" == "NONE" ]] && value=""
  printf '%s\n' "$value"
}

# role_is_valid_agent <value>
# True (exit 0) when <value> is one of the six recognized task roles /
# subagent names. Shared by every wake provider's dispatch assertion so the
# valid-role set is defined in exactly one place.
role_is_valid_agent() {
  case "$1" in
    CODER|WRITER|CM|TESTER|PO|PM) return 0 ;;
    *) return 1 ;;
  esac
}

# get_model_override_from_readme <readme-path>
# Prints the ## Force Model value, or -- when that heading is absent -- the
# ## Model Override value, trimmed. Prints nothing when neither heading is
# present or the value is empty or "none" (any case); returns 1, printing
# nothing, when the README cannot be read. Both headings take any run of
# blanks between their words.
get_model_override_from_readme() {
  local readme="$1" value rc=0
  value="$(read_md_field --free-spacing --end-at-subheading "$readme" "Force Model")" || rc=$?
  if [[ $rc -eq 3 ]]; then
    rc=0
    value="$(read_md_field --free-spacing --end-at-subheading "$readme" "Model Override")" || rc=$?
  fi
  [[ $rc -eq 1 ]] && return 1
  [[ $rc -eq 0 && -n "$value" && "${value,,}" != "none" ]] || return 0
  printf '%s\n' "$value"
}
