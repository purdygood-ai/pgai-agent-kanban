#!/usr/bin/env bash
# team/scripts/lib/dashboard_common.sh
# Single home for the small helpers the dashboard panes and kanban-status.sh
# share: reading one KEY=VALUE pair from data.sh's output, and printing a
# section header. Sourcing it only defines functions -- it sets no shell
# options, installs no trap, and parses no arguments.
#
# SOURCING
# --------
# This file does not self-locate; the caller sources it relative to its own
# directory, like every other lib/ file:
#
#   # From team/scripts/dashboard/*.sh:
#   # shellcheck source=../lib/dashboard_common.sh
#   source "${SCRIPT_DIR}/../lib/dashboard_common.sh"
#
#   # From a script directly under team/scripts/ (kanban-status.sh):
#   # shellcheck source=lib/dashboard_common.sh
#   source "${SCRIPT_DIR}/lib/dashboard_common.sh"
#
# FUNCTIONS
# ---------
# get_val <key> [default]
#   Prints the value of <key> from the caller's DATA variable, which holds
#   data.sh's KEY=VALUE output (one pair per line). The value is everything
#   after the first "=", with any further "=" kept. Prints [default] (empty
#   when omitted) when no line carries <key>. Reads the caller's DATA.
#
# section_header [--rule] <title>
#   Prints a pane section header in the caller's CYAN/BOLD/RESET colours:
#     section_header "Today"          ->  === Today ===
#     section_header --rule "Repos"   ->  Repos
#                                         ──────────...  (60 chars, in DIM)
#   Reads the caller's CYAN, BOLD, RESET and (with --rule) DIM variables;
#   each may be empty when colour is off.

# Guard against double-sourcing.
[[ -n "${_PGAI_DASHBOARD_COMMON_SH_LOADED:-}" ]] && return 0
_PGAI_DASHBOARD_COMMON_SH_LOADED=1

get_val() {
  local key="$1"
  local default="${2:-}"
  echo "$DATA" | awk -F= -v k="$key" 'NR==1{OFS="="} $1 == k { $1=""; sub(/^=/, ""); print; found=1 } END { if (!found) print "'"$default"'" }'
}

section_header() {
  local rule=false
  if [[ "${1:-}" == "--rule" ]]; then
    rule=true
    shift
  fi
  local title="$1"
  if [[ "$rule" == "true" ]]; then
    printf '%s%s%s\n' "${CYAN}${BOLD}" "$title" "$RESET"
    printf '%s%s%s\n' "$DIM" "$(printf '─%.0s' {1..60})" "$RESET"
  else
    printf '%s%s%s\n' "${CYAN}${BOLD}" "=== ${title} ===" "$RESET"
  fi
}
