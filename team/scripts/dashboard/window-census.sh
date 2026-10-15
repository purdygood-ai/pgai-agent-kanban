#!/usr/bin/env bash
# window-census.sh
# Operator CLI over the dashboard window census
# (team/config/dashboard_windows.census). Prints the window list the
# tmux dashboard (create.sh) builds, read through the shared bash reader
# (team/scripts/lib/dashboard_window_census.sh) rather than re-parsing the
# census file itself.
#
# Usage:
#   window-census.sh --list
#   window-census.sh --panes <tmux_index> [<variant>]
#   -h, --help    Show this help and exit
#
# Flags:
#   --list                     Print "<tmux_index> <name> [variant]" one
#                              window per line, in tmux creation order.
#   --panes <tmux_index> [<variant>]
#                              Print "<pane_seq> <script>" one pane per
#                              line for the named window. <variant>
#                              defaults to "-" (the only variant most
#                              windows have; drill-N needs "release" or
#                              "document").
#
# Exit codes:
#   0  success
#   1  usage error (unknown flag, missing argument)
#   2  no window matches the requested tmux_index/variant

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/dashboard_window_census.sh
source "${SCRIPT_DIR}/../lib/dashboard_window_census.sh"

_usage() {
  # shellcheck source=../lib/header_help.sh
  source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
  header_help_print "${BASH_SOURCE[0]}"
}

if [[ $# -eq 0 ]]; then
  _usage
  exit 1
fi

case "$1" in
  -h|--help)
    _usage
    exit 0
    ;;
  --list)
    kind_idx=0
    while IFS=$'\t' read -r kind idx name variant _count; do
      [[ "$kind" == "W" ]] || continue
      if [[ "$variant" == "-" ]]; then
        echo "${idx} ${name}"
      else
        echo "${idx} ${name} [${variant}]"
      fi
    done < "$(dwc_census_path)"
    ;;
  --panes)
    if [[ $# -lt 2 ]]; then
      echo "ERROR: --panes requires <tmux_index>" >&2
      _usage
      exit 1
    fi
    target_index="$2"
    target_variant="${3:-"-"}"
    lines="$(dwc_pane_scripts "$target_index" "$target_variant")"
    if [[ -z "$lines" ]]; then
      echo "ERROR: no panes found for tmux_index=${target_index} variant=${target_variant}" >&2
      exit 2
    fi
    echo "$lines"
    ;;
  *)
    echo "ERROR: Unknown argument: $1" >&2
    _usage
    exit 1
    ;;
esac
