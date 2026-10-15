#!/usr/bin/env bash
# metrics.sh
# Renders the Metrics pane for the pgai kanban dashboard.
#
# Displays two sections:
#   Section 1: Today — per-project summary (RCs shipped, wall time, tokens, tasks)
#   Section 2: Current RC — per-project open RC progress (tasks done/total, elapsed, blocked minutes, tokens so far)
#
# Reads from:
#   projects/<name>/metrics/day/<YYYY-MM-DD>.json  (today block)
#   projects/<name>/metrics/rc/<v>.json             (current RC block)
#   projects/<name>/release-state.md               (active RC discovery)
#
# Designed to run under `watch -t -c -n $REFRESH_INTERVAL metrics.sh`
# in the tmux Metrics window created by dashboard/create.sh.
# The -c flag in watch preserves ANSI color sequences (truecolor safe).
# This script does NOT use `clear` at the top level — truecolor is preserved
# because watch -c handles screen clearing itself.
#
# Usage:
#   metrics.sh [--kanban-root <path>] [--no-color]
#
# Options:
#   --kanban-root <path>   Override the kanban root directory
#   --no-color             Disable ANSI color output
#   -h, --help             Show this help and exit
#
# Environment variables:
#   PGAI_AGENT_KANBAN_ROOT_PATH  Kanban root override
#   NO_COLOR                            Set non-empty to disable colors
#   TERM=dumb                           Also disables colors
#
# Read-only: this script never modifies any kanban state.
# Exits 0 always; renders friendly placeholders on missing data.

set -euo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Resolve script directory
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
NO_COLOR_ARG=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --no-color)
      NO_COLOR_ARG=true
      shift
      ;;
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:33}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--kanban-root <path>] [--no-color]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Source project libraries
# ---------------------------------------------------------------------------
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# Source shared ANSI color primitive (single home for the color-name ->
# escape-code table -- see team/scripts/lib/script_common.sh).
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"

# ---------------------------------------------------------------------------
# ANSI color support (honor NO_COLOR, TERM=dumb, and --no-color flag)
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "$NO_COLOR_ARG" == "true" ]] || \
   [[ -n "${NO_COLOR:-}" ]] || \
   [[ "${TERM:-}" == "dumb" ]]; then
  USE_COLOR=false
fi

RESET="$(ansi_code reset)"
BOLD="$(ansi_code bold)"
DIM="$(ansi_code dim)"
CYAN="$(ansi_code cyan)"
GREEN="$(ansi_code green)"
YELLOW="$(ansi_code yellow)"

# ---------------------------------------------------------------------------
# Shared helpers: section_header (lib/dashboard_common.sh) and
# release_state_section, the release-state.md field reader (lib/cm/common.sh).
# ---------------------------------------------------------------------------
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"

# ---------------------------------------------------------------------------
# Helper: find the per-day metrics rollup file for a project.
# Reads from projects/<name>/metrics/day/<date>.json
# ---------------------------------------------------------------------------
_day_metrics_file() {
  local project_name="$1"
  local date_str="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }
  local candidate="${proj_root}/metrics/day/${date_str}.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Helper: find the per-RC metrics rollup file for a project.
# Reads from projects/<name>/metrics/rc/<v>.json
# ---------------------------------------------------------------------------
_rc_metrics_file() {
  local project_name="$1"
  local rc_version="$2"
  local proj_root
  proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$project_name" 2>/dev/null)" || { echo ""; return; }
  local candidate="${proj_root}/metrics/rc/${rc_version}.json"
  if [[ -f "$candidate" ]]; then
    echo "$candidate"
  else
    echo ""
  fi
}

# ---------------------------------------------------------------------------
# Python metrics renderer — inline Python block.
#
# Renders two independent sections:
#   1. Per-project today block (from metrics/day/<date>.json)
#   2. Per-project current-RC block (from metrics/rc/<v>.json)
#
# Called with project descriptors JSON passed as a command-line argument.
# ---------------------------------------------------------------------------

_render_metrics_python() {
  # Arguments:
  #   $1  use_color        "true" or "false"
  #   $2  today_date       YYYY-MM-DD
  #   $3  projects_json    JSON array of project descriptors:
  #                        [ { "name": "...", "day_file": "...", "rc": "...",
  #                            "rc_file": "...", "opened_at": "..." }, ... ]
  local use_color="$1"
  local today_date="$2"
  local projects_json="$3"

  python3 - "$use_color" "$today_date" "$projects_json" \
      "$RESET" "$BOLD" "$DIM" "$CYAN" "$GREEN" "$YELLOW" "${SCRIPT_DIR}/../lib" \
      <<'PY_EOF'
"""
Inline metrics renderer for dashboard/metrics.sh.
Receives project descriptors JSON as sys.argv[3].
Emits the complete Metrics pane output.

sys.argv:
  1  use_color       "true" or "false"
  2  today_date      YYYY-MM-DD
  3  projects_json   JSON array of project descriptor dicts
  4-9  RESET, BOLD, DIM, CYAN, GREEN, YELLOW -- already the single-sourced
       escapes (or "" in NO_COLOR mode) computed by the bash caller via
       ansi_code() (team/scripts/lib/script_common.sh); threaded through
       argv rather than a second hand-transcribed copy of the same table.
  10  lib_dir         SCRIPT_DIR/../lib, threaded through argv so this
       heredoc (no __file__ of its own) can import the shared
       thousands-separator formatter, team/scripts/lib/number_format.py.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Any

use_color     = sys.argv[1] == "true"
today_date    = sys.argv[2]
projects_json = sys.argv[3]

RESET, BOLD, DIM, CYAN, GREEN, YELLOW = sys.argv[4:10]

# sys.argv[10] is the lib/ directory (SCRIPT_DIR/../lib), threaded through
# argv the same way the color escapes are above -- this heredoc has no
# __file__ of its own to self-locate lib/ from.
sys.path.insert(0, sys.argv[10])
from number_format import format_int  # noqa: E402


def section_header(title: str) -> str:
    return f"{CYAN}{BOLD}=== {title} ==={RESET}"


def fmt_tokens(n: int) -> str:
    """Format token count as compact string: e.g. 47.2M, 1.2M, 834K."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def cache_hit_pct(tok: dict[str, Any]) -> float | None:
    """Compute cache hit percentage: cache_read / (cache_read + cache_write + input + output)."""
    cr  = int(tok.get("cache_read",  0) or 0)
    cw  = int(tok.get("cache_write", 0) or 0)
    inp = int(tok.get("input",       0) or 0)
    out = int(tok.get("output",      0) or 0)
    total = cr + cw + inp + out
    if total == 0:
        return None
    return round(cr / total * 100, 1)


def total_tokens(tok: dict[str, Any]) -> int:
    """Sum all token types."""
    return (
        int(tok.get("input",       0) or 0)
        + int(tok.get("output",    0) or 0)
        + int(tok.get("cache_read", 0) or 0)
        + int(tok.get("cache_write", 0) or 0)
    )


def fmt_wall_time(minutes: int | float | None) -> str:
    """Format wall time as Xh Ym or Ym or (unknown)."""
    if minutes is None:
        return "(unknown)"
    minutes = int(math.ceil(float(minutes)))
    if minutes >= 60:
        h, m = divmod(minutes, 60)
        return f"{h}h {m:02d}m"
    return f"{minutes}m"


def fmt_elapsed_since(opened_at: str | None) -> str:
    """Compute elapsed time from opened_at (ISO 8601) to now."""
    if not opened_at:
        return "(unknown)"
    try:
        # Parse ISO 8601 — handle both Z and +offset forms
        dt_str = opened_at.replace("Z", "+00:00")
        opened = datetime.fromisoformat(dt_str)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        now = datetime.now(tz=timezone.utc)
        delta_sec = (now - opened).total_seconds()
        if delta_sec < 0:
            return "0m"
        minutes = int(delta_sec // 60)
        return fmt_wall_time(minutes)
    except (ValueError, OverflowError):
        return "(unknown)"


def fmt_blocked(minutes: int | float | None) -> str:
    """Format the current RC's blocked-minutes figure beside its elapsed
    time. An RC with no ledger yet, or whose rollup predates this field,
    reads back as a real 0 -- never blank, never a placeholder -- because
    "no blocking observed" is itself a measured figure for the pane in
    progress."""
    if minutes is None:
        return "0"
    return format_int(minutes)


def load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            return None
        return data
    except (OSError, json.JSONDecodeError, ValueError):
        return None


# Parse project descriptors from argv[3]
try:
    projects: list[dict[str, Any]] = json.loads(projects_json)
except (json.JSONDecodeError, ValueError):
    projects = []

# ---------------------------------------------------------------------------
# Section 1: Today
# ---------------------------------------------------------------------------
print(section_header(f"Today ({today_date})"))
print()

if not projects:
    print(f"  {DIM}(no projects registered){RESET}")
else:
    for proj in projects:
        name      = proj.get("name", "unknown")
        day_file  = proj.get("day_file")

        print(f"  {BOLD}{name}{RESET}")

        day_data = load_json(day_file)
        if day_data is None:
            print(f"    {DIM}(no activity yet today){RESET}")
        else:
            tok     = day_data.get("tokens", {}).get("total", {})
            tot_tok = total_tokens(tok)
            hit_pct = cache_hit_pct(tok)

            # Sum wall_time across included RC rollups if available
            rcs_included = day_data.get("rcs_included", [])
            rc_count = len(rcs_included)

            # wall_time_minutes may be summed across included RCs in the day rollup
            wall_min = day_data.get("wall_time_minutes")

            # tasks total from day rollup (if present) or sum from task count
            tasks_total = day_data.get("tasks", {})
            if isinstance(tasks_total, dict):
                tasks_total = tasks_total.get("total", 0)
            else:
                tasks_total = 0

            tok_str = fmt_tokens(tot_tok)
            if hit_pct is not None:
                tok_str += f"  (cache hit: {hit_pct:.0f}%)"

            rc_label = f"{rc_count}"

            print(f"    RCs shipped:  {rc_label}")
            print(f"    Wall time:    {fmt_wall_time(wall_min)}")
            print(f"    Tokens:       {tok_str}")
            print(f"    Tasks:        {format_int(tasks_total)}")

        print()

# ---------------------------------------------------------------------------
# Section 2: Current RC
# ---------------------------------------------------------------------------
print(section_header("Current RC"))
print()

if not projects:
    print(f"  {DIM}(no projects registered){RESET}")
else:
    any_active = any(proj.get("rc") for proj in projects)
    if not any_active:
        print(f"  {DIM}(no RC active in any project){RESET}")
    else:
        for proj in projects:
            name      = proj.get("name", "unknown")
            rc        = proj.get("rc")
            rc_file   = proj.get("rc_file")
            opened_at = proj.get("opened_at")

            if not rc:
                continue

            print(f"  {BOLD}{name} {rc}{RESET}")

            rc_data = load_json(rc_file)
            if rc_data is None:
                # No rollup yet — use elapsed from opened_at only, and no
                # ledger reading exists either: a real 0, not a blank.
                elapsed = fmt_elapsed_since(opened_at)
                print(f"    Tasks:    (no data yet)")
                print(f"    Elapsed:  {elapsed}")
                print(f"    Blocked:  {fmt_blocked(None)}")
                print(f"    Tokens:   (no data yet)")
            else:
                tok       = rc_data.get("tokens", {}).get("total", {})
                tot_tok   = total_tokens(tok)
                tasks     = rc_data.get("tasks", {})
                t_total   = int(tasks.get("total", 0) if isinstance(tasks, dict) else 0)

                # Count done tasks by scanning by_agent (all tasks in rollup are complete)
                # Per the aggregator spec, the rollup includes all tasks with tokens.json;
                # a task in the rollup = it ran. For "done/total" we use the rollup task
                # count as "done" and wall_time_minutes for elapsed.
                wall_min  = rc_data.get("wall_time_minutes")
                # opened_at comes from release-state.md (passed by caller)
                elapsed   = fmt_wall_time(wall_min) if wall_min is not None else fmt_elapsed_since(opened_at)
                blocked   = fmt_blocked(rc_data.get("blocked_minutes"))

                tok_str   = fmt_tokens(tot_tok)

                print(f"    Tasks:    {format_int(t_total)} done")
                print(f"    Elapsed:  {elapsed}")
                print(f"    Blocked:  {blocked}")
                print(f"    Tokens:   {tok_str} (so far)")

            print()

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
print(f"{DIM}Updated: {now_str}{RESET}")
print(f"{DIM}History CSV: projects/*/metrics/history.csv{RESET}")

PY_EOF
}

# ---------------------------------------------------------------------------
# Gather registered projects
# ---------------------------------------------------------------------------
TODAY="$(date +%Y-%m-%d)"

REGISTERED_PROJECTS=()
while IFS= read -r _p; do
  [[ -z "$_p" ]] && continue
  REGISTERED_PROJECTS+=("$_p")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

# ---------------------------------------------------------------------------
# Build JSON descriptor array for inline Python renderer
# ---------------------------------------------------------------------------
_build_project_descriptors_json() {
  local first=true
  printf '['
  for _proj in "${REGISTERED_PROJECTS[@]}"; do
    local _day_file _rc _rc_file _opened_at _proj_root _release_state

    _day_file="$(_day_metrics_file "$_proj" "$TODAY")"
    _rc=""
    _rc_file=""
    _opened_at=""

    # Active RC and RC Opened At in one read of the project's release-state.md:
    # _rc is a vX.Y.Z version or "" (no RC); _opened_at is the value as
    # written, or "" when the heading or value is missing.
    _proj_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_project_root "$_proj" 2>/dev/null)" || _proj_root=""
    if [[ -n "$_proj_root" ]]; then
      _release_state="$(release_state_section "${_proj_root}/release-state.md" "Active RC" "RC Opened At")" || true
      { IFS= read -r _rc || true; IFS= read -r _opened_at || true; } <<< "$_release_state"
      _rc="${_rc//[[:space:]]/}"
      [[ "$_rc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || _rc=""
    fi

    if [[ -n "$_rc" ]]; then
      _rc_file="$(_rc_metrics_file "$_proj" "$_rc")"
      _opened_at="${_opened_at//[[:space:]]/}"
    else
      _opened_at=""
    fi

    [[ "$first" == "false" ]] && printf ','
    first=false

    # Use printf to build safe JSON (no jq dependency)
    # Escape backslashes and double quotes in string values
    _safe_name="$(printf '%s' "$_proj" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_day_file="$(printf '%s' "$_day_file" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_rc="$(printf '%s' "$_rc" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_rc_file="$(printf '%s' "$_rc_file" | sed 's/\\/\\\\/g; s/"/\\"/g')"
    _safe_opened_at="$(printf '%s' "$_opened_at" | sed 's/\\/\\\\/g; s/"/\\"/g')"

    printf '{"name":"%s","day_file":"%s","rc":"%s","rc_file":"%s","opened_at":"%s"}' \
      "$_safe_name" "$_safe_day_file" "$_safe_rc" "$_safe_rc_file" "$_safe_opened_at"
  done
  printf ']'
}

# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
if [[ ${#REGISTERED_PROJECTS[@]} -eq 0 ]]; then
  section_header "Metrics"
  printf '\n'
  printf '%s(no projects registered)%s\n' "$DIM" "$RESET"
  printf '\n'
  printf '%supdated %s%s\n' "$DIM" "$(date '+%Y-%m-%d %H:%M:%S')" "$RESET"
else
  _PROJECTS_JSON="$(_build_project_descriptors_json)"
  _render_metrics_python "$USE_COLOR" "$TODAY" "$_PROJECTS_JSON"
fi
