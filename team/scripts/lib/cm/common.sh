#!/usr/bin/env bash
# team/scripts/lib/cm/common.sh
# Single home for the release-state.md field readers the CM scripts and the
# dashboard panes share. Every one reads through read_md_field
# (lib/md_field.sh) with the release-state reading -- --value
# --exact-heading --comments, the reading
# team/pgai_agent_kanban/cm/read_state_field.py applies -- so none parses
# release-state.md itself. Sourcing it only defines functions -- it sets no
# shell options, installs no trap, and parses no arguments.
#
# SOURCING
# --------
#   # From team/scripts/cm/*.sh or team/scripts/dashboard/*.sh:
#   # shellcheck source=../lib/cm/common.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/cm/common.sh"
#
#   # From a sibling lib/cm/*.sh stage:
#   # shellcheck source=common.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
#
# This file sources lib/md_field.sh and lib/pp_run_ops.sh from its own tree.
# _active_rc also calls pp_project_root, so its callers source
# lib/project_paths.sh first.
#
# FUNCTIONS
# ---------
# read_field <heading>
#   Prints the value under "## <heading>" in the caller's RELEASE_STATE file
#   (the first non-blank, non-comment line after the heading and before the
#   next "##" heading), or "none" when the file, the heading, or a value is
#   missing -- an empty field never reads as the field after it. The caller
#   sets RELEASE_STATE. A file that exists but cannot be read is the
#   reader's failure: its stderr line is shown and read_field returns 1.
#
# _active_rc <project> [none_value]
#   Prints <project>'s Active RC from its release-state.md when it is a
#   vX.Y.Z version; otherwise prints [none_value] (empty when omitted) --
#   for a project root that cannot be resolved, a missing release-state.md,
#   or a value that is "none", empty, or not a version. The field is read
#   by release_state_active_rc below, as a section ending at the next "##"
#   heading, so an empty Active RC is never the next field's value. Reads
#   the caller's KANBAN_ROOT for pp_project_root.
#
# The three readers below take the release-state.md path itself and read
# each field as a section that ends at the next "##" heading, so a field
# with no value never reads as the field after it. A missing file returns
# without starting python.
#
# release_state_section <file> <heading> [<heading>...]
#   Prints one line per heading, in the order named: its value, or an empty
#   line when the heading has no value or is absent. Returns 1 with no
#   output when <file> does not exist, and 1 after printing every line when
#   any heading is absent; 0 otherwise. Several headings cost one python
#   launch, so a caller needing more than one field names them all here.
#
# release_state_value <file> <heading>
#   Prints the heading's value, or "none" when the file, the heading or a
#   value is missing. Always returns 0.
#
# release_state_active_rc <file> [none_value]
#   Prints the Active RC with whitespace removed when it is a vX.Y.Z
#   version; otherwise prints [none_value] (empty when omitted) -- for a
#   missing file or heading, an empty value, "none", or anything that is not
#   a version. Always returns 0.

# Guard against double-sourcing.
[[ -n "${_PGAI_CM_COMMON_SH_LOADED:-}" ]] && return 0
_PGAI_CM_COMMON_SH_LOADED=1

# shellcheck source=../pp_run_ops.sh
source "$(dirname "${BASH_SOURCE[0]}")/../pp_run_ops.sh"
# shellcheck source=../md_field.sh
source "$(dirname "${BASH_SOURCE[0]}")/../md_field.sh"

# The release-state reading: the first value line of a section whose heading
# reads exactly "## <heading>", where a "##" inside an HTML comment is inert.
_CM_RELEASE_STATE_READING=(--value --exact-heading --comments)

read_field() {
  local heading="$1"
  local value rc=0
  if [[ ! -e "$RELEASE_STATE" ]]; then
    printf 'none\n'
    return 0
  fi
  value="$(read_md_field "${_CM_RELEASE_STATE_READING[@]}" "$RELEASE_STATE" "$heading")" || rc=$?
  # 3 is an absent heading, folded into "none" like an empty value.
  if [[ $rc -ne 0 && $rc -ne 3 ]]; then
    return "$rc"
  fi
  printf '%s\n' "${value:-none}"
}

_active_rc() {
  local project_name="$1"
  local none_value="${2:-}"
  local release_state
  release_state="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)/release-state.md" || { echo "$none_value"; return; }

  release_state_active_rc "$release_state" "$none_value"
}

release_state_section() {
  local file="$1"
  shift
  [[ -f "$file" ]] || return 1
  read_md_field "${_CM_RELEASE_STATE_READING[@]}" "$file" "$@" 2>/dev/null || return 1
}

release_state_value() {
  local value
  value="$(release_state_section "$1" "$2")" || true
  if [[ -n "$value" ]]; then
    printf '%s\n' "$value"
  else
    printf 'none\n'
  fi
}

release_state_active_rc() {
  local file="$1"
  local none_value="${2:-}"
  local value
  value="$(release_state_section "$file" "Active RC")" || true
  value="${value//[[:space:]]/}"
  if [[ "$value" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$none_value"
  fi
}
