#!/usr/bin/env bash
# show-document-drill.sh
# Renders the document-workflow drill pane for a registered document project.
#
# This script is the document analog of the release drill's combined
# RC-state + pipeline pane. It renders two sections:
#
#   ARTIFACT LIBRARY — lists versioned deliverables from projects/<name>/artifacts/
#                      newest version first, with file basenames.
#   PIPELINE PROGRESS — shows current task states across the document pipeline
#                       (WRITER outline/draft/polish, TESTER review, CM finalize).
#
# Example output:
#   === pgai-three-bears — Artifact Library ===
#   v0.0.2-polished.md   (current)
#   v0.0.1-polished.md
#   (2 artifacts)
#
#   === Document Pipeline ===
#   writer:  2/3 done, 1 working
#   tester:  0/1 (waiting)
#   cm:      0/1 (waiting)
#
# Usage:
#   show-document-drill.sh --project <name> [--kanban-root <path>]
#
# Flags:
#   --project <name>     Project name (required)
#   --kanban-root <path> Override kanban root path
#   -h, --help           Show this help and exit
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   TERM=dumb                    — disables ANSI codes
#   NO_COLOR=1                   — disables ANSI codes

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script dir
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
PROJECT_NAME=""
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      PROJECT_NAME="${2:-}"
      shift 2
      ;;
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -35
      exit 0
      ;;
    *)
      # Ignore unknown flags silently (forward-compat)
      shift
      ;;
  esac
done

if [[ -z "$PROJECT_NAME" ]]; then
  echo "ERROR: --project <name> is required." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Source config (non-strict)
# ---------------------------------------------------------------------------
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
  export PGAI_DASHBOARD_COLOR_DONE="${PGAI_DASHBOARD_COLOR_DONE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_done green)}"
  export PGAI_DASHBOARD_COLOR_WORKING="${PGAI_DASHBOARD_COLOR_WORKING:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_working yellow)}"
  export PGAI_DASHBOARD_COLOR_BLOCKED="${PGAI_DASHBOARD_COLOR_BLOCKED:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_blocked red)}"
  export PGAI_DASHBOARD_COLOR_LABEL="${PGAI_DASHBOARD_COLOR_LABEL:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" dashboard color_label cyan)}"
fi

# ---------------------------------------------------------------------------
# ANSI color support
# ---------------------------------------------------------------------------
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

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
C_DONE="$(ansi_code "${PGAI_DASHBOARD_COLOR_DONE:-green}")"
C_WORK="$(ansi_code "${PGAI_DASHBOARD_COLOR_WORKING:-yellow}")"
C_BLCK="$(ansi_code "${PGAI_DASHBOARD_COLOR_BLOCKED:-red}")"
C_LABL="$(ansi_code "${PGAI_DASHBOARD_COLOR_LABEL:-cyan}")"
C_BOLD="$(ansi_code bold)"
C_DIM="$(ansi_code dim)"

# ---------------------------------------------------------------------------
# Section 1: Artifact version library
#
# Lists basenames from projects/<name>/artifacts/, sorted newest-first by
# filename (version-aware descending sort). The first entry is labeled
# "(current)".  A summary line shows total artifact count.
# ---------------------------------------------------------------------------

ARTIFACTS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/artifacts"

printf '%s=== %s — Artifact Library ===%s\n' "${C_LABL}${C_BOLD}" "${PROJECT_NAME}" "${RESET}"

if [[ ! -d "$ARTIFACTS_DIR" ]]; then
  printf '%s(artifacts/ directory not found)%s\n' "${C_DIM}" "${RESET}"
else
  # Collect artifact basenames, sorted reverse-version order so newest is first.
  # Use version sort (-V) in descending order to handle vX.Y.Z prefixes correctly.
  mapfile -t _ARTIFACTS < <(
    ls -1 "$ARTIFACTS_DIR" 2>/dev/null \
      | sort -Vr \
      | grep -v '^\..*'
  )

  if [[ ${#_ARTIFACTS[@]} -eq 0 ]]; then
    printf '%s(no artifacts yet)%s\n' "${C_DIM}" "${RESET}"
  else
    _FIRST=true
    for _artifact in "${_ARTIFACTS[@]}"; do
      if [[ "$_FIRST" == "true" ]]; then
        printf '%s%s%s  %s(current)%s\n' \
          "${C_DONE}" "${_artifact}" "${RESET}" \
          "${C_DIM}" "${RESET}"
        _FIRST=false
      else
        printf '%s%s%s\n' "${C_DIM}" "${_artifact}" "${RESET}"
      fi
    done
    printf '\n%s(%d artifact%s)%s\n' \
      "${C_DIM}" \
      "${#_ARTIFACTS[@]}" \
      "$([ "${#_ARTIFACTS[@]}" -eq 1 ] && echo "" || echo "s")" \
      "${RESET}"
  fi
fi

echo ""

# ---------------------------------------------------------------------------
# Section 2: Document-pipeline progress
#
# Scans the project's tasks directory and counts tasks by role and state.
# Shows WRITER / TESTER / CM queue summaries — the three roles that own the
# document pipeline (outline -> draft -> polish -> review -> finalize).
# Does NOT show PM or CODER tasks (those are not document-pipeline roles).
# ---------------------------------------------------------------------------

printf '%s=== Document Pipeline ===%s\n' "${C_LABL}${C_BOLD}" "${RESET}"

TASKS_DIR="${KANBAN_ROOT}/projects/${PROJECT_NAME}/tasks"

if [[ ! -d "$TASKS_DIR" ]]; then
  printf '%s(tasks/ directory not found)%s\n' "${C_DIM}" "${RESET}"
else
  # Use python3 for robust state reading — mirrors data.sh and show-progress.sh
  python3 - "${TASKS_DIR}" "${USE_COLOR}" \
    "${C_LABL}" "${C_DONE}" "${C_WORK}" "${C_BLCK}" "${C_DIM}" "${RESET}" <<'PYEOF'
import os, re, sys, pathlib

tasks_dir = pathlib.Path(sys.argv[1])
use_color  = sys.argv[2].lower() == "true"
C_LABL  = sys.argv[3]
C_DONE  = sys.argv[4]
C_WORK  = sys.argv[5]
C_BLCK  = sys.argv[6]
C_DIM   = sys.argv[7]
RESET   = sys.argv[8]

if not use_color:
    C_LABL = C_DONE = C_WORK = C_BLCK = C_DIM = RESET = ""

SKIP = {"archive", "queues", "plans"}

# Document pipeline roles — PM and CODER are infrastructure/planning roles,
# not part of the document content pipeline.
DOC_PIPELINE_ROLES = ["writer", "tester", "cm"]

DONE_STATES  = {"DONE", "WONT-DO"}
VALID_STATES = {"WORKING", "BLOCKED", "WAITING", "DONE", "WONT-DO", "BACKLOG"}

def read_field(text, heading):
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
    r = role_raw.strip().lower().split("|")[0].strip()
    return r if r in DOC_PIPELINE_ROLES else None

def normalize_state(state_raw):
    s = state_raw.strip().upper()
    if s in ("DONE", "WONT-DO"):
        return "DONE"
    if s in VALID_STATES:
        return s
    return "BACKLOG"

# agent -> counts
counts = {role: {"total": 0, "done": 0, "working": 0, "blocked": 0, "waiting": 0}
          for role in DOC_PIPELINE_ROLES}

if tasks_dir.is_dir():
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        task_id = task_dir.name
        if task_id in SKIP or task_id.startswith("TASK-"):
            continue

        status_file = task_dir / "status.md"
        readme_file = task_dir / "README.md"

        if not status_file.is_file() or not readme_file.is_file():
            continue

        try:
            status_text = status_file.read_text(errors="replace")
            readme_text = readme_file.read_text(errors="replace")
        except OSError:
            continue

        role_raw  = read_field(readme_text, "Role")
        role      = normalize_role(role_raw)
        if role is None:
            continue

        state_raw = read_field(status_text, "State")
        state     = normalize_state(state_raw)

        c = counts[role]
        c["total"] += 1
        if state == "DONE":
            c["done"] += 1
        elif state == "WORKING":
            c["working"] += 1
        elif state == "BLOCKED":
            c["blocked"] += 1
        elif state in ("WAITING", "BACKLOG"):
            c["waiting"] += 1

# Render one line per document-pipeline role
PANE_WIDTH = 36
for role in DOC_PIPELINE_ROLES:
    c = counts[role]
    total   = c["total"]
    done    = c["done"]
    working = c["working"]
    blocked = c["blocked"]
    waiting = c["waiting"]

    label = f"{role}:"
    padded_label = f"{label:<8}"

    if total == 0:
        print(f"{C_LABL}{padded_label}{RESET}{C_DIM}0/0{RESET}")
        continue

    pct    = done * 100 // total
    xx_yy  = f"{done}/{total}"
    pct_str = f"{pct}%"

    codes = ""
    if working > 0:
        codes += f"{working}W "
    if blocked > 0:
        codes += f"{blocked}B "
    if waiting > 0:
        codes += f"{waiting}Wa "
    codes = codes.rstrip()

    label_vlen = len(padded_label)
    xxyy_vlen  = len(xx_yy)
    codes_vlen = len(codes)
    pct_vlen   = len(pct_str)
    fixed_vlen = label_vlen + xxyy_vlen + 2 + codes_vlen
    pad = max(1, PANE_WIDTH - fixed_vlen - pct_vlen)
    padding = " " * pad

    codes_colored = codes
    if use_color and codes:
        codes_colored = ""
        if working > 0:
            codes_colored += f"{C_WORK}{working}W{RESET} "
        if blocked > 0:
            codes_colored += f"{C_BLCK}{blocked}B{RESET} "
        if waiting > 0:
            codes_colored += f"{C_DIM}{waiting}Wa{RESET} "
        codes_colored = codes_colored.rstrip()

    print(f"{C_LABL}{padded_label}{RESET}{C_DONE}{xx_yy}{RESET}  {codes_colored}{padding}{pct_str}")
PYEOF
fi
