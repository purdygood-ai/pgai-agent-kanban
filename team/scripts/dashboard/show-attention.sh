#!/usr/bin/env bash
# show-attention.sh
# Renders the Window 3 "Attention" view showing BLOCKED tasks.
#
# Layout when tasks are blocked:
#   ┌─────────────────────────────────────────────────────────────────────────┐
#   │ ⚠ BLOCKED TASKS                                                          │
#   ├─────────────────────────────────────────────────────────────────────────┤
#   │                                                                          │
#   │ ▶ CLAUDE-CM-20260428-009-release-0-15-5                                  │
#   │   Blocked since: 14:23:18 (2m ago)                                       │
#   │   Reason: cm-release.sh exited 1 at Step 4 ...                           │
#   │   Next step: Update main's release-state.md ...                         │
#   └─────────────────────────────────────────────────────────────────────────┘
#
# Layout when nothing blocked:
#   ⚠ BLOCKED TASKS
#
#   (no blocked tasks — system running normally)
#
# Usage:
#   show-attention.sh [--kanban-root <path>]
#
# Auto-refresh is handled by the caller (dashboard-create.sh uses watch -n N).
#
# Environment:
#   NO_COLOR=1 — disables all ANSI codes
#   TERM=dumb  — disables all ANSI codes

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"
# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# --- Pass through args ---
DATA_ARGS=("$@")

# --- Source config (non-strict) ---
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_args=("$@")
_i=0
while [[ $_i -lt ${#_args[@]} ]]; do
  if [[ "${_args[$_i]}" == "--kanban-root" ]]; then
    _next=$(( _i + 1 ))
    KANBAN_ROOT="${_args[$_next]:-$KANBAN_ROOT}"
    break
  fi
  _i=$(( _i + 1 ))
done
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

ansi_code() {
  local color="$1"
  if [[ "$USE_COLOR" != "true" ]]; then echo ""; return; fi
  case "$color" in
    red)     echo $'\033[0;31m' ;;
    green)   echo $'\033[0;32m' ;;
    yellow)  echo $'\033[0;33m' ;;
    cyan)    echo $'\033[0;36m' ;;
    white)   echo $'\033[0;37m' ;;
    bold)    echo $'\033[1m' ;;
    dim)     echo $'\033[2m' ;;
    reset)   echo $'\033[0m' ;;
    *)       echo "" ;;
  esac
}

RESET="$(ansi_code reset)"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"
C_RED="$(ansi_code red)"
C_CYAN="$(ansi_code cyan)"
C_YELLOW="$(ansi_code yellow)"
C_GREEN="$(ansi_code green)"

# --- Header ---
if [[ "$USE_COLOR" == "true" ]]; then
  printf '%s%s⚠ BLOCKED TASKS%s\n' "$C_RED" "$C_BOLD" "$RESET"
else
  printf '! BLOCKED TASKS\n'
fi
echo ""

# --- Render blocked tasks via python ---
# Iterate all registered projects (iterate-all).
# The Python renderer is called once per project so blocked tasks from every
# registered project are shown.  On a single-project install the loop runs
# once and behaviour is unchanged.  When no projects are registered the
# renderer receives "" and shows "(no blocked tasks — system running normally)".
_sha_task_roots=()
while IFS= read -r _sha_project; do
    [[ -z "$_sha_project" ]] && continue
    _sha_dir="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_sha_project" 2>/dev/null || true)"
    [[ -n "$_sha_dir" ]] && _sha_task_roots+=("$_sha_dir")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
if [[ ${#_sha_task_roots[@]} -eq 0 ]]; then
    _sha_task_roots=("")
fi
for _sha_tasks_dir in "${_sha_task_roots[@]}"; do
python3 - "${_sha_tasks_dir}" "$USE_COLOR" <<'PY'
import os, re, sys, pathlib, time

tasks_root = pathlib.Path(sys.argv[1])
use_color  = sys.argv[2].lower() == "true"

RESET  = "\033[0m" if use_color else ""
C_RED  = "\033[0;31m" if use_color else ""
C_BOLD = "\033[1m" if use_color else ""
C_DIM  = "\033[2m" if use_color else ""
C_CYAN = "\033[0;36m" if use_color else ""
C_YEL  = "\033[0;33m" if use_color else ""
ARROW  = "\u25b6" if use_color else ">"

def read_field(text, heading):
    """Return first non-blank content line after '## heading'."""
    pat = re.compile(r'^##\s+' + re.escape(heading) + r'\s*$', re.M | re.I)
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

def read_section(text, heading, max_lines=5):
    """Return up to max_lines content lines from a ## section."""
    pat = re.compile(r'^##\s+' + re.escape(heading) + r'\s*$', re.M | re.I)
    m = pat.search(text)
    if not m:
        return []
    rest = text[m.end():]
    lines = []
    for line in rest.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped:
            lines.append(stripped)
            if len(lines) >= max_lines:
                break
    return lines

def format_elapsed(secs):
    if secs < 60:
        return f"{secs}s"
    elif secs < 3600:
        return f"{secs // 60}m ago"
    else:
        hrs = secs // 3600
        mins = (secs % 3600) // 60
        return f"{hrs}h {mins}m ago"

found_any = False
now = int(time.time())

if tasks_root.is_dir():
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in {"archive", "queues", "plans"}:
            continue
        status_file = task_dir / "status.md"
        if not status_file.is_file():
            continue
        try:
            status_text = status_file.read_text(errors="replace")
        except OSError:
            continue

        state = read_field(status_text, "State").upper()
        if state != "BLOCKED":
            continue

        found_any = True

        # Get mtime for "blocked since"
        try:
            mtime = int(status_file.stat().st_mtime)
            elapsed_secs = now - mtime
            from datetime import datetime
            blocked_since = datetime.fromtimestamp(mtime).strftime("%H:%M:%S")
            elapsed_str = format_elapsed(elapsed_secs)
        except (OSError, OverflowError):
            blocked_since = "unknown"
            elapsed_str = ""

        # Extract reason and next step
        reason_lines = read_section(status_text, "Blocked Reason", max_lines=3)
        if not reason_lines:
            reason_lines = read_section(status_text, "Blockers", max_lines=3)
        next_step_lines = read_section(status_text, "Next Recommended Step", max_lines=3)

        # Render the blocked task block
        print(f"{ARROW} {C_RED}{C_BOLD}{task_id}{RESET}")
        if blocked_since != "unknown":
            print(f"  Blocked since: {C_DIM}{blocked_since} ({elapsed_str}){RESET}")
        if reason_lines:
            print(f"  {C_YEL}Reason:{RESET}")
            for line in reason_lines:
                print(f"    {line}")
        if next_step_lines:
            print(f"  {C_CYAN}Recommended next step:{RESET}")
            for line in next_step_lines:
                print(f"    {line}")
        print("")

if not found_any:
    print(f"  {C_DIM}(no blocked tasks \u2014 system running normally){RESET}")
    print("")
PY
done
