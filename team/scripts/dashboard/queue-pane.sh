#!/usr/bin/env bash
# dashboard-queue-pane.sh
# Pretty-prints the content and recent task statuses for a single agent queue.
#
# Designed to run under `watch -n 5 -- dashboard-queue-pane.sh <queue_name>`.
#
# Output includes:
#   - Queue name header with counts (pending / working / done / total)
#   - Per-entry listing with status badges from status.md
#   - WAITING-eligibility hints (lists prerequisite tasks not yet done)
#   - Missing-folder warnings for queue entries without a task directory
#
# Usage:
#   dashboard-queue-pane.sh <queue_name> [--kanban-root <path>]
#
# Arguments:
#   queue_name   One of: pm, coder, writer, tester, cm
#                (also accepts: bug, priority — for the auxiliary backlogs)
#
# Options:
#   --kanban-root <path>   Override the kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH
#                          or ~/pgai_agent_kanban)
#   -h, --help             Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root directory
#   NO_COLOR                            — set non-empty to disable ANSI colors
#   TERM=dumb                           — also disables ANSI colors

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# ---------------------------------------------------------------------------
# Valid queue names — map to their backlog filename stem
# ---------------------------------------------------------------------------
declare -A QUEUE_FILE_MAP
QUEUE_FILE_MAP["pm"]="pm_backlog"
QUEUE_FILE_MAP["coder"]="coder_backlog"
QUEUE_FILE_MAP["writer"]="writer_backlog"
QUEUE_FILE_MAP["tester"]="tester_backlog"
QUEUE_FILE_MAP["cm"]="cm_backlog"
QUEUE_FILE_MAP["bug"]="bug_backlog"
QUEUE_FILE_MAP["priority"]="priority_backlog"

VALID_QUEUES="pm coder writer tester cm bug priority"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
QUEUE_NAME=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  arg="${_args[$_i]}"
  case "$arg" in
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -40
      exit 0
      ;;
    --kanban-root)
      _next=$(( _i + 1 ))
      KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
      _i=$_next
      ;;
    -*)
      echo "ERROR: Unknown option: $arg" >&2
      echo "Usage: $0 <queue_name> [--kanban-root <path>]" >&2
      exit 1
      ;;
    *)
      if [[ -z "$QUEUE_NAME" ]]; then
        QUEUE_NAME="$arg"
      else
        echo "ERROR: Unexpected extra argument: $arg" >&2
        echo "Usage: $0 <queue_name> [--kanban-root <path>]" >&2
        exit 1
      fi
      ;;
  esac
  _i=$(( _i + 1 ))
done

# Queue name is required
if [[ -z "$QUEUE_NAME" ]]; then
  echo "ERROR: queue_name is required." >&2
  echo "Usage: $0 <queue_name> [--kanban-root <path>]" >&2
  echo "Valid queues: ${VALID_QUEUES}" >&2
  exit 1
fi

# Reject unknown queue names
if [[ -z "${QUEUE_FILE_MAP[$QUEUE_NAME]:-}" ]]; then
  echo "ERROR: Unknown queue name: '${QUEUE_NAME}'" >&2
  echo "Valid queues: ${VALID_QUEUES}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_done green)}"
    export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_working yellow)}"
    export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_blocked red)}"
    export PGAI_DASHBOARD_COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_waiting yellow)}"
    export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_label cyan)}"
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ -n "${NO_COLOR:-}" ]]; then
  USE_COLOR=false
fi

COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-green}"
COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-yellow}"
COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-red}"
COLOR_WAITING="${PGAI_DASHBOARD_COLOR_WAITING:-yellow}"
COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-cyan}"

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
C_BOLD="$(ansi_code bold)"
C_WARN="$(ansi_code yellow)"

# ---------------------------------------------------------------------------
# Resolve paths
# Explicit-drop: this pane renders a single named queue which is
# inherently project-scoped.  Without an explicit --project argument there is
# no safe way to pick the correct project; leaving TASKS_DIR/QUEUE_FILE empty
# causes the missing-queue-file handler below to render "(queue file not
# found)" rather than silently substituting the first-registered project.
# ---------------------------------------------------------------------------
TASKS_DIR=""
QUEUES_DIR=""
QUEUE_FILE=""

# ---------------------------------------------------------------------------
# Handle missing queue file
# ---------------------------------------------------------------------------
if [[ ! -f "$QUEUE_FILE" ]]; then
  printf '%s%s%s%s%s\n' \
    "$C_LABL" "$C_BOLD" "=== ${QUEUE_NAME} ===" "$RESET" ""
  printf '%s%s: empty (queue file not found)%s\n' "$C_DIM" "$QUEUE_NAME" "$RESET"
  exit 0
fi

# ---------------------------------------------------------------------------
# Parse queue file — extract entries in order
# ---------------------------------------------------------------------------
# Each entry line: "- [X] TASK-ID — optional description"
# Marker meanings:
#   ' ' (space or empty) → BACKLOG/WAITING (not started)
#   'W'                  → WAITING
#   'A'                  → WORKING (actively being worked)
#   'x'                  → DONE/WONT-DO
#   'B'                  → BLOCKED (rare marker)
# ---------------------------------------------------------------------------

# We'll use python3 for the heavy lifting to keep line-by-line parsing clean
python3 - \
  "$QUEUE_FILE" \
  "$TASKS_DIR" \
  "$QUEUE_NAME" \
  "$USE_COLOR" \
  "${C_LABL}" "${C_BOLD}" "${C_DONE}" "${C_WORK}" "${C_BLCK}" \
  "${C_WAIT}" "${C_DIM}" "${C_WARN}" "${RESET}" \
<<'PY'
import os, re, sys, pathlib, datetime

queue_file  = pathlib.Path(sys.argv[1])
tasks_root  = pathlib.Path(sys.argv[2])
queue_name  = sys.argv[3]
use_color   = sys.argv[4].lower() == "true"

# Color codes passed from bash (already empty when color disabled)
C_LABL, C_BOLD, C_DONE, C_WORK, C_BLCK = sys.argv[5:10]
C_WAIT, C_DIM,  C_WARN, RESET          = sys.argv[10:14]

DONE_MARKERS    = {"x"}
WORKING_MARKERS = {"A"}
WAITING_MARKERS = {"W"}
BLOCKED_MARKERS = {"B"}

def read_field(text, heading):
    """Return first non-blank content line after '## heading'."""
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

def read_prereqs(text):
    """Return list of prerequisite task IDs from ## Prerequisites field."""
    pat = re.compile(r'##\s+Prerequisites\s*$', re.M | re.I)
    m = pat.search(text)
    if not m:
        return []
    rest = text[m.end():]
    prereqs = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        # Match "- TASK-ID" or "TASK-ID" lines
        tid_m = re.match(r'^-?\s*(CLAUDE-[A-Z0-9]+-\S+)\s*$', stripped)
        if tid_m:
            prereqs.append(tid_m.group(1))
    return prereqs

def get_task_state(task_dir):
    """Read ## State from status.md, return canonical uppercase state or ''."""
    status_file = task_dir / "status.md"
    if not status_file.is_file():
        return ""
    try:
        text = status_file.read_text(errors="replace")
        s = read_field(text, "State").upper()
        return s if s else "BACKLOG"
    except OSError:
        return ""

def get_prereqs_status(task_id):
    """Return list of (prereq_id, state) for the task's prerequisites."""
    task_dir = tasks_root / task_id
    readme_file = task_dir / "README.md"
    if not readme_file.is_file():
        return []
    try:
        text = readme_file.read_text(errors="replace")
        prereqs = read_prereqs(text)
    except OSError:
        return []
    result = []
    for pid in prereqs:
        pdir = tasks_root / pid
        pstate = get_task_state(pdir) if pdir.is_dir() else "MISSING"
        result.append((pid, pstate))
    return result

def fmt_state_badge(state):
    """Return colored state badge string."""
    if state in ("DONE", "WONT-DO"):
        return f"{C_DONE}[{state}]{RESET}"
    elif state == "WORKING":
        return f"{C_WORK}[{state}]{RESET}"
    elif state == "BLOCKED":
        return f"{C_BLCK}[{state}]{RESET}"
    elif state == "WAITING":
        return f"{C_WAIT}[{state}]{RESET}"
    elif state in ("BACKLOG", ""):
        return f"{C_DIM}[BACKLOG]{RESET}"
    else:
        return f"{C_WARN}[{state}]{RESET}"

# ---------------------------------------------------------------------------
# Parse queue file
# ---------------------------------------------------------------------------
queue_line_pat = re.compile(r'^\s*-\s+\[(.)\]\s+(CLAUDE-[A-Z0-9]+-\S+)(.*)?$')

entries = []  # list of (marker, task_id, rest_description)
try:
    content = queue_file.read_text(errors="replace")
except OSError:
    content = ""

for line in content.splitlines():
    m = queue_line_pat.match(line)
    if not m:
        continue
    marker   = m.group(1)
    task_id  = m.group(2)
    rest_raw = (m.group(3) or "").strip()
    # Strip optional " — description" prefix
    desc = re.sub(r'^[—–-]\s*', '', rest_raw).strip()
    entries.append((marker, task_id, desc))

# ---------------------------------------------------------------------------
# Build counts
# ---------------------------------------------------------------------------
total   = len(entries)
done    = sum(1 for m, _, __ in entries if m in DONE_MARKERS)
working = sum(1 for m, _, __ in entries if m in WORKING_MARKERS)
waiting = sum(1 for m, _, __ in entries if m in WAITING_MARKERS)
blocked = sum(1 for m, _, __ in entries if m in BLOCKED_MARKERS)
pending = total - done - working - waiting - blocked

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------
header = f"=== {queue_name} ==="
count_parts = []
if done:
    count_parts.append(f"{C_DONE}{done} done{RESET}")
if working:
    count_parts.append(f"{C_WORK}{working} working{RESET}")
if waiting:
    count_parts.append(f"{C_WAIT}{waiting} waiting{RESET}")
if blocked:
    count_parts.append(f"{C_BLCK}{blocked} blocked{RESET}")
if pending:
    count_parts.append(f"{C_DIM}{pending} pending{RESET}")

count_str = ", ".join(count_parts) if count_parts else f"{C_DIM}empty{RESET}"
print(f"{C_LABL}{C_BOLD}{header}{RESET}  {count_str}")
print(f"{C_DIM}{'─' * 60}{RESET}")

# ---------------------------------------------------------------------------
# Per-entry listing
# ---------------------------------------------------------------------------
if not entries:
    print(f"{C_DIM}(no entries){RESET}")
else:
    for marker, task_id, desc in entries:
        task_dir = tasks_root / task_id

        # Determine effective state
        if marker in DONE_MARKERS:
            # Quickly confirm via status.md; default to DONE if folder missing
            if task_dir.is_dir():
                actual = get_task_state(task_dir)
                if actual in ("DONE", "WONT-DO", ""):
                    display_state = actual if actual else "DONE"
                else:
                    display_state = actual   # mismatch — show real state
            else:
                display_state = "DONE"
        elif marker in WORKING_MARKERS:
            display_state = get_task_state(task_dir) if task_dir.is_dir() else "WORKING"
        elif marker in WAITING_MARKERS:
            display_state = "WAITING"
        elif marker in BLOCKED_MARKERS:
            display_state = "BLOCKED"
        else:
            # Space / empty / other — read from status.md
            display_state = get_task_state(task_dir) if task_dir.is_dir() else "BACKLOG"

        badge   = fmt_state_badge(display_state)
        tid_str = f"{C_DIM}{task_id}{RESET}"

        if not task_dir.is_dir() and marker not in DONE_MARKERS:
            warn = f"  {C_WARN}⚠ task folder missing{RESET}"
        else:
            warn = ""

        # Short description (trim to keep line readable)
        short_desc = ""
        if desc:
            # Extract slug from task ID for context if no desc
            parts = task_id.split("-")
            short_desc = f"  {C_DIM}{desc[:50]}{RESET}" if desc else ""

        print(f"  {badge} {tid_str}{warn}{short_desc}")

        # WAITING-eligibility hints: list unmet prerequisites
        if display_state == "WAITING" and task_dir.is_dir():
            prereqs = get_prereqs_status(task_id)
            unmet = [(pid, ps) for pid, ps in prereqs if ps not in ("DONE", "WONT-DO")]
            if unmet:
                print(f"    {C_WAIT}waiting on:{RESET}")
                for pid, ps in unmet:
                    ps_badge = fmt_state_badge(ps)
                    print(f"      {ps_badge} {C_DIM}{pid}{RESET}")

# ---------------------------------------------------------------------------
# Footer: timestamp
# ---------------------------------------------------------------------------
now_str = datetime.datetime.now().strftime("%H:%M:%S")
print(f"\n{C_DIM}updated {now_str}{RESET}")
PY
