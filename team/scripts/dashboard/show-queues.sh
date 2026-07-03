#!/usr/bin/env bash
# show-queues.sh
# Renders the queue state pane (left ~50%).
#
# Example output (no flags):
#   pm:     1/1 done
#   coder:  4/7 done, 1 working
#   writer: 0/0
#   tester: 0/1 (waiting)
#   cm:     0/2 (waiting)
#
#   — currently working —
#   CODER-...-bug1-cleanup
#     started: 18:35:14
#     elapsed: 7m 17s
#
# Example output (--details):
#   === coder ===
#   [WORKING]
#     CODER-...-task1
#   [BLOCKED]
#     CODER-...-task2
#   [BACKLOG]
#     CODER-...-task3
#
# Usage:
#   show-queues.sh [--kanban-root <path>] [--details]
#
# Configuration (via config.cfg):
#   PGAI_DASHBOARD_COLOR_DONE     — done count color (default: green)
#   PGAI_DASHBOARD_COLOR_WORKING  — working indicator color (default: yellow)
#   PGAI_DASHBOARD_COLOR_BLOCKED  — blocked indicator color (default: red)
#   PGAI_DASHBOARD_COLOR_WAITING  — waiting indicator color (default: yellow)
#
# Environment:
#   TERM=dumb  — disables all ANSI codes

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source task_ids lib for kanban_parse_task_id and related helpers.
# Required for dual-format (old CLAUDE-ROLE-... / new ROLE-...) task ID support.
# shellcheck source=lib/task_ids.sh
source "${SCRIPT_DIR}/../lib/task_ids.sh"

# Source active_provider lib for read_active_provider.
# shellcheck source=lib/active_provider.sh
source "${SCRIPT_DIR}/../lib/active_provider.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# --- Parse args ---
SHOW_DETAILS=false
PROJECT_NAME=""
DATA_ARGS=()
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  if [[ "$arg" == "--details" ]]; then
    SHOW_DETAILS=true
  elif [[ "$arg" == "--project" ]]; then
    _next=$(( _i + 1 ))
    PROJECT_NAME="${_args[$_next]:-}"
    _i=$_next
    # Forward --project to dashboard-data.sh so it scans the correct
    # project's tasks directory; without this, data.sh would read the default
    # project's counts regardless of which project was requested.
    [[ -n "$PROJECT_NAME" ]] && DATA_ARGS+=("--project" "$PROJECT_NAME")
  else
    DATA_ARGS+=("$arg")
  fi
  _i=$(( _i + 1 ))
done

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  if [[ "${_args[$_i]}" == "--kanban-root" ]]; then
    _next=$(( _i + 1 ))
    KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
    break
  fi
  _i=$(( _i + 1 ))
done
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_done green)}"
    export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_working yellow)}"
    export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_blocked red)}"
    export PGAI_DASHBOARD_COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_waiting yellow)}"
    export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_label cyan)}"
    export PGAI_DASHBOARD_COLOR_DIM="${PGAI_DASHBOARD_COLOR_DIM:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_dim white)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"
COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-red}"
COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-yellow}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"
COLOR_DIM="${PGAI_DASHBOARD_COLOR_DIM:-white}"
COLOR_HALT="${PGAI_DASHBOARD_COLOR_HALT:-red}"

ansi_code() {
  local color="$1"
  if [[ "$USE_COLOR" != "true" ]]; then echo ""; return; fi
  case "$color" in
    black)   echo $'\033[0;30m' ;;
    red)     echo $'\033[0;31m' ;;
    green)   echo $'\033[0;32m' ;;
    yellow)  echo $'\033[0;33m' ;;
    blue)    echo $'\033[0;34m' ;;
    magenta) echo $'\033[0;35m' ;;
    cyan)    echo $'\033[0;36m' ;;
    white)   echo $'\033[0;37m' ;;
    bold)    echo $'\033[1m' ;;
    dim)     echo $'\033[2m' ;;
    reset)   echo $'\033[0m' ;;
    *)       echo "" ;;
  esac
}

RESET="$(ansi_code reset)"
C_DONE="$(ansi_code "$COLOR_DONE")"
C_WORK="$(ansi_code "$COLOR_WORKING")"
C_BLCK="$(ansi_code "$COLOR_BLOCKED")"
C_WAIT="$(ansi_code "$COLOR_WAITING")"
C_LABL="$(ansi_code "$COLOR_LABEL")"
C_DIM="$(ansi_code dim)"
C_HALT="$(ansi_code "$COLOR_HALT")"
C_YELLOW="$(ansi_code yellow)"

# --- Collect data ---
DATA="$("$SCRIPT_DIR/data.sh" "${DATA_ARGS[@]}")"

get_val() {
  local key="$1"
  local default="${2:-}"
  echo "$DATA" | awk -F= -v k="$key" 'NR==1{OFS="="} $1 == k { $1=""; sub(/^=/, ""); print; found=1 } END { if (!found) print "'"$default"'" }'
}

# --- Halt state (mirrors show-header.sh logic for color consistency) ---
HALT_FLAG="$(get_val HALT_FLAG "no")"
HALT_TEXT="$(get_val HALT_TEXT "")"

# no halt = normal (empty = use default C_LABL), HALT-AFTER draining = yellow, HALT = red
HALT_HEADER_COLOR=""
if [[ "$HALT_FLAG" == "yes" ]]; then
  HALT_HEADER_COLOR="$C_HALT"
elif [[ -n "$HALT_TEXT" ]]; then
  HALT_HEADER_COLOR="$C_YELLOW"
fi

# --- Render queue rows (compact single-line format) ---
# Format: agent:  XX/YY  [state-codes]  PCT%
#
# state codes (only when count > 0):
#   nW   = n tasks in WORKING state
#   nB   = n tasks in BLOCKED state
#   nWa  = n tasks in WAITING state
#
# Percentage is right-aligned to PANE_WIDTH (default 36 chars).
# No inline progress bar — percentage number is sufficient at this width.
PANE_WIDTH="${PGAI_AGENT_PANE_WIDTH:-36}"

render_queue_row() {
  local label="$1"    # e.g. "coder"
  local role="$2"     # e.g. "CODER"

  local total done working blocked waiting
  total="$(get_val "QUEUE_${role}_TOTAL" 0)"
  done="$(get_val "QUEUE_${role}_DONE" 0)"
  working="$(get_val "QUEUE_${role}_WORKING" 0)"
  blocked="$(get_val "QUEUE_${role}_BLOCKED" 0)"
  waiting="$(get_val "QUEUE_${role}_WAITING" 0)"

  # Pad label to 8 chars (including colon)
  local padded_label
  padded_label="$(printf '%-8s' "${label}:")"

  if [[ "$total" -eq 0 ]]; then
    printf '%s%s%s%s0/0%s\n' "$C_LABL" "$padded_label" "$RESET" "$C_DIM" "$RESET"
    return
  fi

  local pct=$(( done * 100 / total ))
  local xx_yy="${done}/${total}"
  local pct_str="${pct}%"

  # Build state codes string — only include codes with count > 0
  local codes=""
  if [[ "$working" -gt 0 ]]; then
    codes="${codes}${working}W "
  fi
  if [[ "$blocked" -gt 0 ]]; then
    codes="${codes}${blocked}B "
  fi
  if [[ "$waiting" -gt 0 ]]; then
    codes="${codes}${waiting}Wa "
  fi
  # Strip trailing space
  codes="${codes% }"

  # Compute visible lengths (no ANSI codes in these raw strings)
  local label_vlen="${#padded_label}"
  local xx_yy_vlen="${#xx_yy}"
  local codes_vlen="${#codes}"
  local pct_vlen="${#pct_str}"
  # Layout: label(8) + xx_yy + "  " + codes + padding + pct
  # Minimum separator between codes and pct: 1 space
  local fixed_vlen=$(( label_vlen + xx_yy_vlen + 2 + codes_vlen ))
  local pad=$(( PANE_WIDTH - fixed_vlen - pct_vlen ))
  [[ "$pad" -lt 1 ]] && pad=1
  local padding
  padding="$(printf '%*s' "$pad" "")"

  # Colorize state codes (only color the codes that represent problems)
  local codes_colored="${codes}"
  if [[ "$USE_COLOR" == "true" ]] && [[ -n "$codes" ]]; then
    # Working is yellow, blocked is red, waiting is dim-yellow — rebuild colored
    codes_colored=""
    if [[ "$working" -gt 0 ]]; then
      codes_colored="${codes_colored}${C_WORK}${working}W${RESET} "
    fi
    if [[ "$blocked" -gt 0 ]]; then
      codes_colored="${codes_colored}${C_BLCK}${blocked}B${RESET} "
    fi
    if [[ "$waiting" -gt 0 ]]; then
      codes_colored="${codes_colored}${C_WAIT}${waiting}Wa${RESET} "
    fi
    codes_colored="${codes_colored% }"
  fi

  printf '%s%s%s%s%s%s  %s%s%s\n' \
    "$C_LABL" "$padded_label" "$RESET" \
    "$C_DONE" "$xx_yy" "$RESET" \
    "$codes_colored" \
    "$padding" \
    "$pct_str"
}

# --- Details mode ---
# render_details [<project_name>]
# When project_name is supplied (from --project flag or iterate-all loop),
# render the details view for that project.  The previous fallback
# that resolved the first-registered project when none was given has been
# removed; callers must now supply an explicit project name.
render_details() {
  local tasks_root
  local _project_name="${1:-${PROJECT_NAME:-}}"
  tasks_root="$(pp_tasks_dir "$_project_name" 2>/dev/null || true)"

  # Resolve per-agent queue path through pp_queue_path (canonical flat layout:
  # queues/<agent>_backlog.md).  The resolved parent directory is passed to
  # Python to glob *_backlog.md files.  We derive the queues directory from
  # the resolved path of one known agent (coder).
  local _coder_queue_path
  _coder_queue_path="$(pp_queue_path "$_project_name" "coder" 2>/dev/null || true)"
  local _queues_dir=""
  if [[ -n "$_coder_queue_path" ]]; then
    _queues_dir="$(dirname "$_coder_queue_path")"
  fi

  python3 - "$tasks_root" "$USE_COLOR" "${_queues_dir}" "$HALT_HEADER_COLOR" <<'PY'
import os, re, sys, pathlib

tasks_root = pathlib.Path(sys.argv[1])
use_color = sys.argv[2].lower() == "true"
# sys.argv[3]: queues directory resolved by pp_queue_path (may be empty string
# when called in environments where project context is unavailable).
_queues_dir_arg = sys.argv[3] if len(sys.argv) > 3 else ""
# sys.argv[4]: halt header color ANSI code (empty = no halt, use default C_LABL)
halt_header_color = sys.argv[4] if len(sys.argv) > 4 else ""

SKIP = {"archive", "queues", "plans"}
DONE_STATES = {"DONE", "WONT-DO"}

# Canonical agent order
AGENT_ORDER = ["pm", "coder", "writer", "tester", "cm", "po", "human", "unknown"]

# State display order within each agent section
STATE_ORDER = ["WORKING", "BLOCKED", "WAITING", "BACKLOG"]

# ANSI colors (only when use_color is true)
def ansi(code):
    if not use_color:
        return ""
    return code

RESET  = ansi("\033[0m")
C_LABL = ansi("\033[0;36m")   # cyan  — agent header
C_WORK = ansi("\033[0;33m")   # yellow — WORKING
C_BLCK = ansi("\033[0;31m")   # red   — BLOCKED
C_WAIT = ansi("\033[0;33m")   # yellow — WAITING
C_DIM  = ansi("\033[2m")      # dim   — BACKLOG / task ids
C_BOLD = ansi("\033[1m")      # bold

STATE_COLOR = {
    "WORKING": C_WORK,
    "BLOCKED": C_BLCK,
    "WAITING": C_WAIT,
    "BACKLOG": C_DIM,
}

def read_field(text, heading):
    """Return the first non-blank content line after '## heading'."""
    pat = re.compile(r'##\s+' + re.escape(heading) + r'\s*$', re.M | re.I)
    m = pat.search(text)
    if not m:
        return ""
    rest = text[m.end():]
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped:
            return stripped
    return ""

def normalize_role(role_raw):
    """Map raw role string to canonical lowercase agent name."""
    r = role_raw.strip().lower().split("|")[0].strip()
    mapping = {
        "pm":     "pm",
        "coder":  "coder",
        "writer": "writer",
        "tester": "tester",
        "cm":     "cm",
        "po":     "po",
        "human":  "human",
    }
    return mapping.get(r, "unknown")

VALID_STATES = {"WORKING", "BLOCKED", "WAITING", "DONE", "WONT-DO", "BACKLOG"}

def normalize_state(state_raw):
    """Return uppercase canonical state, DONE for done-family, or raw value for unknown."""
    s = state_raw.strip().upper()
    if s in ("DONE", "WONT-DO"):
        return "DONE"
    if s in VALID_STATES:
        return s
    if not s:
        return "BACKLOG"
    # Unknown state — return as-is so it renders as a warning
    return s

# Queue marker → set of valid canonical states
# Space = not started (BACKLOG or WAITING), W = working, B = blocked
QUEUE_MARKER_VALID_STATES = {
    " ": {"BACKLOG", "WAITING", "WORKING"},
    "W": {"WAITING"},
    "A": {"WORKING"},
    "B": {"BLOCKED"},
}
# Markers to skip: done or under review
QUEUE_SKIP_MARKERS = {"x", "R"}

# Map queue file stem (e.g. "coder_backlog") to agent name
def queue_file_to_agent(stem):
    parts = stem.split("_")
    candidate = parts[0].lower() if parts else ""
    mapping = {
        "pm":     "pm",
        "coder":  "coder",
        "writer": "writer",
        "tester": "tester",
        "cm":     "cm",
        "po":     "po",
        "bug":    "unknown",
    }
    return mapping.get(candidate, "unknown")

# Resolve the queues directory from the path passed by the shell (via
# pp_queue_path).  Fall back to the legacy queues/claude/ path when the
# shell did not supply one (e.g. during isolated unit testing).
if _queues_dir_arg:
    queues_dir = pathlib.Path(_queues_dir_arg)
else:
    queues_dir = tasks_root / "queues" / "claude"

# Pattern: "- [X] TASK-ID" where X is a single marker character.
# Accepts BOTH task ID formats:
#   Old format: CLAUDE-<AGENT>-YYYYMMDD-NNN-slug   (PARTICIPANT prefix)
#   New format: <AGENT>-YYYYMMDD-NNN-slug            (no PARTICIPANT prefix)
# The pattern is intentionally liberal on the non-date portion so that new
# agent names introduced after this script was written still match.
queue_line_pat = re.compile(r'^\s*-\s+\[(.)\]\s+([A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)?-\d{8}-\d+-\S+)\s*$')

# Build a map of task_id -> queue marker for all active (non-skip) queue entries.
# This is used later to reconcile status.md state against queue state.
queue_marker_map = {}   # task_id -> marker character

if queues_dir.is_dir():
    for queue_file in sorted(queues_dir.glob("*_backlog.md")):
        try:
            text = queue_file.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = queue_line_pat.match(line)
            if not m:
                continue
            marker, task_id = m.group(1), m.group(2)
            # Record all markers, including skip markers, so we can detect
            # when a done/review marker matches a still-active status.md state.
            if task_id not in queue_marker_map:
                queue_marker_map[task_id] = marker

# warn_type constants used in grouped tuples
WARN_NONE          = None
WARN_FOLDER_MISS   = "folder_missing"
WARN_NO_QUEUE      = "no_queue_entry"
WARN_MISMATCH      = "marker_mismatch"

# agent -> state -> [(task_id, warn_type), ...]
grouped = {agent: {state: [] for state in STATE_ORDER} for agent in AGENT_ORDER}

if tasks_root.is_dir():
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in SKIP or task_id.startswith("TASK-"):
            continue
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            continue
        readme_file = task_dir / "README.md"

        try:
            status_text = status_file.read_text(errors="replace")
        except OSError:
            continue

        state_raw = read_field(status_text, "State")
        state = normalize_state(state_raw)

        # Skip done tasks in details view
        if state == "DONE":
            continue

        # Determine agent from README Role field
        agent = "unknown"
        if readme_file.is_file():
            try:
                readme_text = readme_file.read_text(errors="replace")
                role_raw = read_field(readme_text, "Role")
                agent = normalize_role(role_raw)
            except OSError:
                pass

        # --- Reconcile status.md state against queue marker ---
        if state not in STATE_ORDER:
            # Unknown state — skip normal marker reconciliation; show as unknown-state warning
            warn_type = f"unknown_state:{state}"
            grouped[agent]["BACKLOG"].append((task_id, warn_type))
            continue

        warn_type = WARN_NONE
        if task_id in queue_marker_map:
            marker = queue_marker_map[task_id]
            if marker in QUEUE_SKIP_MARKERS:
                # Queue says done/review but status.md still shows active state
                warn_type = WARN_MISMATCH
            else:
                valid_states = QUEUE_MARKER_VALID_STATES.get(marker, {"BACKLOG"})
                if state not in valid_states:
                    warn_type = WARN_MISMATCH
        else:
            # Active task has no queue entry at all
            warn_type = WARN_NO_QUEUE

        grouped[agent][state].append((task_id, warn_type))

# Collect task IDs already seen from existing directories
seen_task_ids = {tid for agent in AGENT_ORDER for state in STATE_ORDER for tid, _ in grouped[agent][state]}
# Also collect IDs from directories that were DONE (skipped above) so we don't warn about them
done_task_ids = set()
if tasks_root.is_dir():
    for task_dir in tasks_root.iterdir():
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in SKIP or task_id.startswith("TASK-"):
            continue
        done_task_ids.add(task_id)

# Scan queue backlog files for task IDs that have no corresponding task folder
if queues_dir.is_dir():
    for queue_file in sorted(queues_dir.glob("*_backlog.md")):
        agent = queue_file_to_agent(queue_file.stem)
        try:
            text = queue_file.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = queue_line_pat.match(line)
            if not m:
                continue
            marker, task_id = m.group(1), m.group(2)
            if marker in QUEUE_SKIP_MARKERS:
                continue
            # Only warn about task IDs with no task folder at all
            if task_id in seen_task_ids or task_id in done_task_ids:
                continue
            # For folder-missing entries, pick first valid state from the marker set
            valid = QUEUE_MARKER_VALID_STATES.get(marker, {"BACKLOG"})
            state = "BACKLOG" if "BACKLOG" in valid else next(iter(valid))
            # Check across all groups to avoid double-adding (e.g. same task in two queue files)
            already_present = any(
                tid == task_id
                for ag in AGENT_ORDER
                for st in STATE_ORDER
                for tid, _ in grouped[ag][st]
            )
            if not already_present:
                grouped[agent][state].append((task_id, WARN_FOLDER_MISS))

# Render output
C_WARN = ansi("\033[0;33m")   # yellow — warning marker

first_agent = True
for agent in AGENT_ORDER:
    states_with_tasks = [s for s in STATE_ORDER if grouped[agent][s]]
    if not states_with_tasks:
        continue

    if not first_agent:
        print("")
    first_agent = False

    # Agent header — color driven by halt state when present, otherwise C_LABL
    _h_color = halt_header_color if halt_header_color else C_LABL
    print(f"{_h_color}{C_BOLD}=== {agent} ==={RESET}")

    for state in STATE_ORDER:
        tasks = grouped[agent][state]
        if not tasks:
            continue
        sc = STATE_COLOR.get(state, "")
        print(f"{sc}[{state}]{RESET}")
        for tid, warn_type in sorted(tasks, key=lambda x: x[0]):
            if warn_type == WARN_FOLDER_MISS:
                print(f"  {C_DIM}{tid}{RESET}  {C_WARN}\u26a0 task folder missing{RESET}")
            elif warn_type == WARN_NO_QUEUE:
                print(f"  {C_DIM}{tid}{RESET}  {C_WARN}\u26a0 no queue entry{RESET}")
            elif warn_type == WARN_MISMATCH:
                print(f"  {C_DIM}{tid}{RESET}  {C_WARN}\u26a0 marker/status mismatch{RESET}")
            elif isinstance(warn_type, str) and warn_type.startswith("unknown_state:"):
                bad_state = warn_type.split(":", 1)[1]
                print(f"  {C_DIM}{tid}{RESET}  {C_WARN}\u26a0 [UNKNOWN STATE: {bad_state}]{RESET}")
            else:
                print(f"  {C_DIM}{tid}{RESET}")
PY
}

# --- Main rendering ---

# Print active provider as the first line of all output modes.
# read_active_provider defaults to 'claude' when the file is missing or invalid,
# matching the constraint: "fall back to 'Active provider: claude (default)'".
_ACTIVE_PROVIDER="$(read_active_provider "$KANBAN_ROOT")"
printf 'Active provider: %s\n' "$_ACTIVE_PROVIDER"

if [[ "$SHOW_DETAILS" == "true" ]]; then
  if [[ -n "${PROJECT_NAME:-}" ]]; then
    # --project given: render details for the named project only
    render_details "$PROJECT_NAME"
  else
    # No --project given: iterate all registered projects (iterate-all).
    # On a single-project install the loop runs once; behaviour is unchanged.
    _sqd_projects=()
    while IFS= read -r _sqd_proj; do
      [[ -z "$_sqd_proj" ]] && continue
      _sqd_projects+=("$_sqd_proj")
    done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
    for _sqd_proj in "${_sqd_projects[@]:-}"; do
      [[ -z "$_sqd_proj" ]] && continue
      render_details "$_sqd_proj"
    done
  fi
else
  render_queue_row "pm"     "PM"
  render_queue_row "coder"  "CODER"
  render_queue_row "writer" "WRITER"
  render_queue_row "tester" "TESTER"
  render_queue_row "cm"     "CM"

  # --- Currently working section ---
  WORKING_ID="$(get_val WORKING_TASK_ID "")"
  WORKING_MTIME="$(get_val WORKING_TASK_MTIME "")"

  if [[ -n "$WORKING_ID" ]]; then
    echo ""
    printf '%s\342\200\224 currently working \342\200\224%s\n' "$C_DIM" "$RESET"

    # Abbreviate task ID to the trailing slug to keep within PANE_WIDTH.
    # Use kanban_parse_task_id (from task_ids.sh) to extract the slug field,
    # which works for both ID formats:
    #   Old: CLAUDE-ROLE-YYYYMMDD-NNN-slug  (PARTICIPANT prefix)
    #   New: ROLE-YYYYMMDD-NNN-slug          (no prefix)
    WORKING_SHORT="$WORKING_ID"
    kanban_parse_task_id "$WORKING_ID" && _slug="$_TASK_SLUG" || _slug=""
    [[ -n "$_slug" ]] && WORKING_SHORT="$_slug"
    # Hard-truncate to pane width
    WORKING_SHORT="${WORKING_SHORT:0:${PANE_WIDTH}}"
    printf '%s\n' "$WORKING_SHORT"

    # Compute started time from mtime and elapsed
    if [[ -n "$WORKING_MTIME" ]] && [[ "$WORKING_MTIME" -gt 0 ]]; then
      STARTED="$(date -d "@${WORKING_MTIME}" '+%H:%M:%S' 2>/dev/null || date -r "$WORKING_MTIME" '+%H:%M:%S' 2>/dev/null || echo "unknown")"
      NOW_EPOCH="$(date +%s)"
      ELAPSED_SECS=$(( NOW_EPOCH - WORKING_MTIME ))

      # Format elapsed: Xm Ys or Xh Ym or Xs
      if [[ "$ELAPSED_SECS" -lt 60 ]]; then
        ELAPSED_STR="${ELAPSED_SECS}s"
      elif [[ "$ELAPSED_SECS" -lt 3600 ]]; then
        MINS=$(( ELAPSED_SECS / 60 ))
        SECS=$(( ELAPSED_SECS % 60 ))
        ELAPSED_STR="${MINS}m ${SECS}s"
      else
        HRS=$(( ELAPSED_SECS / 3600 ))
        MINS=$(( (ELAPSED_SECS % 3600) / 60 ))
        ELAPSED_STR="${HRS}h ${MINS}m"
      fi

      printf '  started: %s\n' "$STARTED"
      printf '  elapsed: %s\n' "$ELAPSED_STR"
    fi
  fi
fi
