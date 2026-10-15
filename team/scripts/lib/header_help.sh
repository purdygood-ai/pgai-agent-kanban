#!/usr/bin/env bash
# team/scripts/lib/header_help.sh
# Prints a script's header comment block as its --help text, so a script
# whose usage lives in its header never has to hard-code which lines to
# print. A fixed `sed -n 'A,Bp'` range truncates the help or runs into code
# the moment the header gains or loses a line; reading up to the first
# non-comment line follows the header wherever it ends.
#
# SOURCING
# --------
# This file does not self-locate — the caller resolves its own directory
# and sources this file relative to it, like every other lib/ helper:
#
#   # From a script directly under team/scripts/:
#   # shellcheck source=lib/header_help.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/header_help.sh"
#
#   # From a script one level deeper (team/scripts/dashboard/*.sh, ...):
#   # shellcheck source=../lib/header_help.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
#
# Sourcing only defines the function: it sets no shell options, reads no
# configuration and needs no kanban root, so a script may source it and
# answer --help before its own bootstrap runs.
#
# FUNCTIONS
# ---------
# header_help_print <script-path>
#   Writes the header comment block of <script-path> to stdout: every line
#   after a leading `#!` shebang, up to but not including the first line
#   that does not start with `#` (a blank line ends the header too). Each
#   line loses its leading `#` and at most one following space; a bare `#`
#   prints as an empty line. `# shellcheck ...` directive lines are linter
#   instructions, not usage, and are left out. awk is the only process
#   writing, straight to stdout, so no internal pipe can race. Returns 0 on
#   success and when the reader closes early (`--help | head -1`: awk's
#   SIGPIPE status 141 is not an error), 2 when no path is given, and awk's
#   own non-zero status otherwise (e.g. <script-path> cannot be read).
#
# Example:
#   case "$1" in
#     --help|-h) header_help_print "${BASH_SOURCE[0]}"; exit 0 ;;
#   esac

header_help_print() {
  local _hh_file="${1:-}"
  if [[ -z "$_hh_file" ]]; then
    echo "header_help_print: a script path is required, e.g. header_help_print \"\${BASH_SOURCE[0]}\"" >&2
    return 2
  fi
  local _hh_rc=0
  awk '
    NR == 1 && /^#!/ { next }
    /^#[[:space:]]*shellcheck[[:space:]]/ { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$_hh_file" || _hh_rc=$?
  if [[ "$_hh_rc" -eq 141 ]]; then
    return 0
  fi
  return "$_hh_rc"
}
