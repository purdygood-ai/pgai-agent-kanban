#!/usr/bin/env bash
# show-metrics.sh
# Historical RC metrics viewer for the pgai kanban dashboard.
#
# Reads metrics from:
#   projects/<name>/metrics/history.csv   (default view, all projects)
#   projects/<name>/metrics/rc/v*.json    (--per-agent view, single project)
#
# Usage:
#   show-metrics.sh [options]
#
# Options:
#   --project <name>          Project name (default: all registered projects)
#   --last <N>                Show last N rows (default: 10)
#   --history-csv <path>      Override CSV path (for testing; implies single-project mode)
#   --per-agent               Show per-agent token breakdown for most recent RC
#   --kanban-root <path>      Override kanban root directory
#   --no-color                Disable ANSI color output
#   -h, --help                Show this help and exit
#
# Default CSV columns:  rc | wall_time | input | output | cache_read | cache_write | tasks
# Per-agent columns:    agent | input | output | cache_read | cache_write | invocations
#
# Wall time shows '--' when opened_at/closed_at/wall_time_minutes
# are not yet populated in history.csv.
#
# Numbers are formatted with commas for readability (e.g. 5,644,879).
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors

# ---------------------------------------------------------------------------
# Resolve script directory (before set -euo pipefail so sourcing works safely)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers (pp_project_root, pp_require_project_context)
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source projects lib for projects_cfg_list (multi-project iteration)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# ---------------------------------------------------------------------------
# Parse arguments (before set -euo pipefail — arg parsing tolerates unset vars)
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
PROJECT_NAME=""
LAST_N=10
HISTORY_CSV_OVERRIDE=""
PER_AGENT=false
NO_COLOR_ARG=false

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  case "$arg" in
    --project)
      _next=$(( _i + 1 ))
      PROJECT_NAME="${_args[$_next]:-}"
      _i=$(( _i + 1 ))
      ;;
    --last)
      _next=$(( _i + 1 ))
      LAST_N="${_args[$_next]:-10}"
      _i=$(( _i + 1 ))
      ;;
    --history-csv)
      _next=$(( _i + 1 ))
      HISTORY_CSV_OVERRIDE="${_args[$_next]:-}"
      _i=$(( _i + 1 ))
      ;;
    --per-agent)
      PER_AGENT=true
      ;;
    --kanban-root)
      _next=$(( _i + 1 ))
      KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
      _i=$(( _i + 1 ))
      ;;
    --no-color)
      NO_COLOR_ARG=true
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $arg" >&2
      echo "Usage: $0 [--project <name>] [--last N] [--per-agent] [--no-color] [--kanban-root <path>]" >&2
      exit 1
      ;;
  esac
  _i=$(( _i + 1 ))
done

set -euo pipefail

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

# ---------------------------------------------------------------------------
# Per-agent view: single-project mode only (--project required or defaulted).
# Mirror original behavior for --per-agent.
# ---------------------------------------------------------------------------
if [[ "$PER_AGENT" == "true" ]]; then
  # --per-agent requires an explicit project: --project <name> or $PGAI_PROJECT_NAME.
  if [[ -z "$PROJECT_NAME" ]]; then
    PROJECT_NAME="${PGAI_PROJECT_NAME:-}"
  fi
  if [[ -z "$PROJECT_NAME" ]]; then
    echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
    exit 1
  fi

  PROJ_ROOT="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)"
  METRICS_DIR="${PROJ_ROOT}/metrics"
  RC_DIR="${METRICS_DIR}/rc"

  # Find most recent RC json by sorting v*.json files by version number.
  # Uses sort -V (version sort) to handle vX.Y.Z ordering correctly.
  LATEST_RC_JSON=""
  if [[ -d "$RC_DIR" ]]; then
    LATEST_RC_JSON="$(ls -1 "${RC_DIR}"/v*.json 2>/dev/null \
      | sort -V \
      | tail -n1 || true)"
  fi

  if [[ -z "$LATEST_RC_JSON" ]] || [[ ! -f "$LATEST_RC_JSON" ]]; then
    echo "No RC metrics files found in ${RC_DIR}" >&2
    exit 1
  fi

  python3 /dev/stdin "$LATEST_RC_JSON" "$USE_COLOR" "$PROJECT_NAME" <<'PY_PER_AGENT'
import json
import sys

rc_file   = sys.argv[1]
use_color = sys.argv[2] == "true"
project   = sys.argv[3]

def _esc(code):
    return code if use_color else ""

RESET  = _esc("\033[0m")
BOLD   = _esc("\033[1m")
DIM    = _esc("\033[2m")
CYAN   = _esc("\033[0;36m")

def fmt_num(n):
    """Format an integer with comma separators."""
    try:
        return "{:,}".format(int(n))
    except (ValueError, TypeError):
        return str(n)

try:
    with open(rc_file, encoding="utf-8") as fh:
        data = json.load(fh)
except (OSError, json.JSONDecodeError) as e:
    print(f"ERROR: Cannot read {rc_file}: {e}", file=sys.stderr)
    sys.exit(1)

rc_version = data.get("rc", "unknown")
by_agent   = data.get("tokens", {}).get("by_agent", {})

# Sort agents alphabetically for consistent display
agents = sorted(by_agent.keys())

# Print header
print(f"{CYAN}{BOLD}=== RC Metrics: {project} — per-agent ({rc_version}) ==={RESET}")
print()

# Column widths: agent(12), input(10), output(10), cache_read(12), cache_write(13), invocations(11)
hdr = "{:<12} {:>10} {:>10} {:>12} {:>13} {:>11}".format(
    "Agent", "Input", "Output", "Cache Read", "Cache Write", "Invocations"
)
print(f"{BOLD}{hdr}{RESET}")

for agent in agents:
    tok = by_agent[agent]
    inp  = fmt_num(tok.get("input", 0))
    out  = fmt_num(tok.get("output", 0))
    cr   = fmt_num(tok.get("cache_read", 0))
    cw   = fmt_num(tok.get("cache_write", 0))
    inv  = fmt_num(tok.get("invocations", 0))
    row = "{:<12} {:>10} {:>10} {:>12} {:>13} {:>11}".format(agent, inp, out, cr, cw, inv)
    print(row)
PY_PER_AGENT
  exit 0
fi

# ---------------------------------------------------------------------------
# Default view: render history.csv for each registered project.
#
# When --history-csv is given (override for testing), use single-project mode:
#   resolve the project name (--project or $PGAI_PROJECT_NAME, required), render
#   that one CSV, and exit.
#
# When no override is given, iterate all registered projects via
# projects_cfg_list (mirrors metrics.sh left-pane approach).  For each project
# that has a history.csv, render its metrics table.  For projects without a
# history.csv, emit a graceful "(no releases yet)" message — never a raw
# file-not-found error.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Python renderer: renders one project's history CSV as a table.
# Called with: csv_path use_color project_name last_n
# ---------------------------------------------------------------------------
_render_history_csv() {
  local csv_path="$1"
  local use_color="$2"
  local project_name="$3"
  local last_n="$4"

  python3 /dev/stdin "$csv_path" "$use_color" "$project_name" "$last_n" <<'PY_HISTORY'
import sys
import csv

csv_path  = sys.argv[1]
use_color = sys.argv[2] == "true"
project   = sys.argv[3]
last_n    = int(sys.argv[4])

def _esc(code):
    return code if use_color else ""

RESET  = _esc("\033[0m")
BOLD   = _esc("\033[1m")
DIM    = _esc("\033[2m")
CYAN   = _esc("\033[0;36m")

def fmt_num(s):
    """Format a numeric string with comma separators. Returns '--' for empty."""
    s = str(s).strip()
    if not s:
        return "--"
    try:
        return "{:,}".format(int(float(s)))
    except (ValueError, TypeError):
        return s

def fmt_wall(s):
    """Wall time: always '--' (field always empty in CSV)."""
    s = str(s).strip()
    if not s:
        return "--"
    # If the data ever gets populated, display as-is
    return s

try:
    with open(csv_path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
except OSError as e:
    print(f"ERROR: Cannot read {csv_path}: {e}", file=sys.stderr)
    sys.exit(1)

if not rows:
    print("No data")
    sys.exit(0)

# Trim to last N rows
rows = rows[-last_n:]

# Print header
print(f"{CYAN}{BOLD}=== RC Metrics: {project} (last {last_n}) ==={RESET}")
print()

# Column header
hdr = "{:<12} {:<10} {:>10} {:>10} {:>12} {:>13}  {:>5}".format(
    "RC", "Wall", "Input", "Output", "Cache Read", "Cache Write", "Tasks"
)
print(f"{BOLD}{hdr}{RESET}")

for row in rows:
    rc         = str(row.get("rc", "")).strip()
    wall       = fmt_wall(row.get("wall_time_minutes", ""))
    input_tok  = fmt_num(row.get("input_tokens", ""))
    output_tok = fmt_num(row.get("output_tokens", ""))
    cache_rd   = fmt_num(row.get("cache_read_tokens", ""))
    cache_wrt  = fmt_num(row.get("cache_write_tokens", ""))
    tasks      = fmt_num(row.get("tasks_total", ""))

    line = "{:<12} {:<10} {:>10} {:>10} {:>12} {:>13}  {:>5}".format(
        rc, wall, input_tok, output_tok, cache_rd, cache_wrt, tasks
    )
    print(line)
PY_HISTORY
}

# ---------------------------------------------------------------------------
# Inline Python: emit a graceful no-releases section for a project.
# Called with: use_color project_name
# ---------------------------------------------------------------------------
_render_no_releases() {
  local use_color="$1"
  local project_name="$2"

  python3 - "$use_color" "$project_name" <<'PY_EMPTY'
import sys

use_color    = sys.argv[1] == "true"
project_name = sys.argv[2]

def _esc(code):
    return code if use_color else ""

RESET = _esc("\033[0m")
BOLD  = _esc("\033[1m")
DIM   = _esc("\033[2m")
CYAN  = _esc("\033[0;36m")

print(f"{CYAN}{BOLD}=== RC Metrics: {project_name} ==={RESET}")
print()
print(f"  {DIM}(no releases yet){RESET}")
print()
PY_EMPTY
}

# ---------------------------------------------------------------------------
# --history-csv override: single-project mode (for testing / operator use).
# ---------------------------------------------------------------------------
if [[ -n "$HISTORY_CSV_OVERRIDE" ]]; then
  # --history-csv requires an explicit project: --project <name> or $PGAI_PROJECT_NAME.
  if [[ -z "$PROJECT_NAME" ]]; then
    PROJECT_NAME="${PGAI_PROJECT_NAME:-}"
  fi
  if [[ -z "$PROJECT_NAME" ]]; then
    echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
    exit 1
  fi
  if [[ ! -f "$HISTORY_CSV_OVERRIDE" ]]; then
    echo "No history CSV found at: ${HISTORY_CSV_OVERRIDE}" >&2
    exit 1
  fi
  _render_history_csv "$HISTORY_CSV_OVERRIDE" "$USE_COLOR" "$PROJECT_NAME" "$LAST_N"
  exit 0
fi

# ---------------------------------------------------------------------------
# Single-project mode: --project was explicitly given.
# ---------------------------------------------------------------------------
if [[ -n "$PROJECT_NAME" ]]; then
  PROJ_ROOT="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$PROJECT_NAME" 2>/dev/null)"
  HISTORY_CSV="${PROJ_ROOT}/metrics/history.csv"
  if [[ ! -f "$HISTORY_CSV" ]]; then
    _render_no_releases "$USE_COLOR" "$PROJECT_NAME"
  else
    _render_history_csv "$HISTORY_CSV" "$USE_COLOR" "$PROJECT_NAME" "$LAST_N"
  fi
  exit 0
fi

# ---------------------------------------------------------------------------
# Multi-project mode: iterate all registered projects (default dashboard view).
# Mirrors metrics.sh left-pane approach: read projects_cfg_list, loop, render.
# ---------------------------------------------------------------------------
REGISTERED_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  REGISTERED_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  # Edge case: no projects registered at all.
  _use_color_val="$USE_COLOR"
  python3 - "$_use_color_val" <<'PY_NOPROJ'
import sys
use_color = sys.argv[1] == "true"
def _esc(code): return code if use_color else ""
RESET = _esc("\033[0m"); BOLD = _esc("\033[1m"); DIM = _esc("\033[2m"); CYAN = _esc("\033[0;36m")
print(f"{CYAN}{BOLD}=== RC Metrics History ==={RESET}")
print()
print(f"  {DIM}(no projects registered){RESET}")
print()
PY_NOPROJ
  exit 0
fi

for _proj in "${REGISTERED_PROJECTS[@]}"; do
  _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || {
    _render_no_releases "$USE_COLOR" "$_proj"
    continue
  }
  _history_csv="${_proj_root}/metrics/history.csv"
  if [[ -f "$_history_csv" ]]; then
    _render_history_csv "$_history_csv" "$USE_COLOR" "$_proj" "$LAST_N"
  else
    _render_no_releases "$USE_COLOR" "$_proj"
  fi
done
