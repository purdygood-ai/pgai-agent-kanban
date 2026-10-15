#!/usr/bin/env bash
# team/scripts/lib/dashboard_window_census.sh
#
# The one bash reader for the dashboard window census
# (team/config/dashboard_windows.census). Every bash consumer that needs
# the tmux dashboard's window/pane list reads it through this file instead
# of hand-parsing the census a second time — the sibling Python reader is
# team/pgai_agent_kanban/dashboard/window_census.py; both implement the
# same parse rule the census file's own header documents, independently,
# because bash and Python cannot share a parser module.
#
# Source this file, then call:
#
#   dwc_census_path
#       Echoes the default census file path, resolved relative to this
#       library's own location (each tree finds itself).
#
#   dwc_window_names [census_path]
#       Echoes one window name per line, in tmux creation order. A window
#       with two variants (drill-N: release / document) is echoed once per
#       variant, matching the Python reader's Window-per-variant model.
#
#   dwc_pane_count <tmux_index> <variant> [census_path]
#       Echoes the pane_count field for the matching W record. Exits 1 with
#       nothing echoed if no matching W record exists.
#
#   dwc_pane_scripts <tmux_index> <variant> [census_path]
#       Echoes one "<pane_seq>\t<script>" line per P record belonging to
#       the given window, in pane_seq order.
#
# Usage:
#   source "${SCRIPT_DIR}/lib/dashboard_window_census.sh"
#   dwc_window_names

# ---------------------------------------------------------------------------
# dwc_census_path
# ---------------------------------------------------------------------------
dwc_census_path() {
  local lib_dir
  lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # lib/ -> scripts/ -> team/ -> config/dashboard_windows.census
  echo "${lib_dir}/../../config/dashboard_windows.census"
}

# ---------------------------------------------------------------------------
# dwc_window_names [census_path]
# ---------------------------------------------------------------------------
dwc_window_names() {
  local census="${1:-$(dwc_census_path)}"
  local kind name
  while IFS=$'\t' read -r kind _idx name _variant _count; do
    [[ "$kind" == "W" ]] || continue
    echo "$name"
  done < "$census"
}

# ---------------------------------------------------------------------------
# dwc_pane_count <tmux_index> <variant> [census_path]
# ---------------------------------------------------------------------------
dwc_pane_count() {
  local target_index="$1"
  local target_variant="$2"
  local census="${3:-$(dwc_census_path)}"
  local kind idx variant count
  while IFS=$'\t' read -r kind idx _name variant count; do
    [[ "$kind" == "W" ]] || continue
    if [[ "$idx" == "$target_index" && "$variant" == "$target_variant" ]]; then
      echo "$count"
      return 0
    fi
  done < "$census"
  return 1
}

# ---------------------------------------------------------------------------
# dwc_pane_scripts <tmux_index> <variant> [census_path]
# ---------------------------------------------------------------------------
dwc_pane_scripts() {
  local target_index="$1"
  local target_variant="$2"
  local census="${3:-$(dwc_census_path)}"
  local kind idx variant seq position script cmd
  while IFS=$'\t' read -r kind idx variant seq position script cmd; do
    [[ "$kind" == "P" ]] || continue
    if [[ "$idx" == "$target_index" && "$variant" == "$target_variant" ]]; then
      printf '%s\t%s\n' "$seq" "$script"
    fi
  done < "$census"
}
