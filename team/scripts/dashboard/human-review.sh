#!/usr/bin/env bash
# dashboard-human-review.sh
# Window 14 — pending human-approval listing.
#
# Lists every pending HUMAN-APPROVE gate task across ALL registered projects.
# Each row shows:
#   project, target RC, age, show content (what is being approved / where the
#   report lives), plus the two verbatim copy-pasteable commands:
#     Approve:  scripts/close.sh  --project <proj> --key <task-id>
#     Reject:   scripts/wontdo.sh --project <proj> --key <task-id>
#
# Empty state (no pending approvals): one line — "no approvals pending."
#
# A task is "pending" when its task ID starts with HUMAN-APPROVE and its
# status.md ## State is WAITING or BACKLOG.
#
# Designed to run under:
#   watch -t -c -n N -- dashboard-human-review.sh [--kanban-root <path>]
#
# Usage:
#   dashboard-human-review.sh [--kanban-root <path>]
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   NO_COLOR                     — set to suppress ANSI colors
#   TERM=dumb                    — also suppresses ANSI colors

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

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

if [[ "$USE_COLOR" == "true" ]]; then
  C_RED=$'\033[0;31m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_RED=""
  C_BOLD=""
  C_RESET=""
fi

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

# Source config — INI format (kanban.cfg) replaces legacy config.cfg.
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  DIVIDER="$(printf '%.0s\xe2\x94\x80' {1..68})"
  printf '%s%s✋ PENDING APPROVALS%s\n' "$C_RED" "$C_BOLD" "$C_RESET"
else
  DIVIDER="$(printf '%.0s-' {1..68})"
  printf '! PENDING APPROVALS\n'
fi
printf '%s\n' "$DIVIDER"

# ---------------------------------------------------------------------------
# Resolve scan_human_approvals.py via the shared helper resolver, then invoke
# it with the kanban root.  The resolver checks the live-install anchor first
# (the same D3-fix pattern used by attention.sh for scan_attention.py).
# ---------------------------------------------------------------------------
_COLOR_ARG="--color"
if [[ "$USE_COLOR" != "true" ]]; then
  _COLOR_ARG="--no-color"
fi

_SCAN_APPROVALS_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/scan_human_approvals.py")"

if [[ -n "$_SCAN_APPROVALS_PY" ]]; then
  # PYTHONPATH must point at the parent of pgai_agent_kanban/ so imports resolve.
  _PY_PYTHONPATH="$(dirname "$(dirname "$(dirname "$_SCAN_APPROVALS_PY")")")"
  PYTHONPATH="${_PY_PYTHONPATH}" python3 "$_SCAN_APPROVALS_PY" "$KANBAN_ROOT" "$_COLOR_ARG"
else
  # Fallback: scan_human_approvals.py not found — emit a clear diagnostic.
  printf '  (scan_human_approvals.py not found; run install.sh to install)\n'
fi
unset _SCAN_APPROVALS_PY _COLOR_ARG _PY_PYTHONPATH
