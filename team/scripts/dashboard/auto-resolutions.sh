#!/usr/bin/env bash
# auto-resolutions.sh
# Human-review window — Auto-Resolutions pane (beside Approvals).
#
# Lists every OVERWATCH autonomous resolution ledger entry
# (projects/<name>/auto-resolutions.md) across ALL registered projects.
# Each row shows: RC, attempt N/M, decision, outcome, and evidence links
# (evidence paths recorded on the entry, plus a pointer to the ledger
# entry itself).
#
# Empty state (no resolution events yet): one line — "no auto-resolutions."
#
# READ-ONLY: this pane never writes to the ledger, never touches a HALT
# file, and prints no action affordances — it informs, it never approves
# or mutates state.
#
# Designed to run under:
#   watch -t -c -n N -- auto-resolutions.sh [--kanban-root <path>]
#
# Usage:
#   auto-resolutions.sh [--kanban-root <path>] [-h|--help]
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   NO_COLOR                     — set to suppress ANSI colors
#   TERM=dumb                    — also suppresses ANSI colors

set -euo pipefail
# -h/--help prints the header comment above and exits 0. It is handled before
# env_bootstrap.sh so help never resolves a kanban root, reads an argument as
# data, or starts the script's own work.
for __help_arg in "$@"; do
  case "$__help_arg" in
    -h|--help)
      # shellcheck source=../lib/header_help.sh
      source "$(dirname "${BASH_SOURCE[0]}")/../lib/header_help.sh"
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
  esac
done

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"
# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=../lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --*)
      shift
      ;;
    *)
      shift
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Color support (honor NO_COLOR and TERM=dumb)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

C_CYAN="$(ansi_code cyan)"
C_BOLD="$(ansi_code bold)"
C_RESET="$(ansi_code reset)"

# ---------------------------------------------------------------------------
# Resolve script directories
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source shared Python-helper resolver (live-install anchor first).
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"

# Source dev_tree helper (resolve_global_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  DIVIDER="$(printf '%.0s\xe2\x94\x80' {1..68})"
  printf '%s%s⚙ AUTO-RESOLUTIONS%s\n' "$C_CYAN" "$C_BOLD" "$C_RESET"
else
  DIVIDER="$(printf '%.0s-' {1..68})"
  printf '* AUTO-RESOLUTIONS\n'
fi
printf '%s\n' "$DIVIDER"

# ---------------------------------------------------------------------------
# Resolve scan_auto_resolutions.py via the shared helper resolver, then
# invoke it with the kanban root.  The resolver checks the live-install
# anchor first (the same D3-fix pattern used by attention.sh and
# human-review.sh).
# ---------------------------------------------------------------------------
_COLOR_ARG="--color"
if [[ "$USE_COLOR" != "true" ]]; then
  _COLOR_ARG="--no-color"
fi

_SCAN_AUTO_RESOLUTIONS_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/scan_auto_resolutions.py")"

if [[ -n "$_SCAN_AUTO_RESOLUTIONS_PY" ]]; then
  # PYTHONPATH must point at the parent of pgai_agent_kanban/ so imports resolve.
  _PY_PYTHONPATH="$(dirname "$(dirname "$(dirname "$_SCAN_AUTO_RESOLUTIONS_PY")")")"
  PYTHONPATH="${_PY_PYTHONPATH}" python3 "$_SCAN_AUTO_RESOLUTIONS_PY" "$KANBAN_ROOT" "$_COLOR_ARG"
else
  # Fallback: scan_auto_resolutions.py not found — emit a clear diagnostic.
  printf '  (scan_auto_resolutions.py not found; run install.sh to install)\n'
fi
unset _SCAN_AUTO_RESOLUTIONS_PY _COLOR_ARG _PY_PYTHONPATH
