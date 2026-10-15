#!/usr/bin/env bash
# data.sh
# Collects all dashboard metrics and outputs structured key=value data.
#
# Usage:
#   data.sh --project <name> [--kanban-root <path>]
#   data.sh -h|--help
#
# --project <name> is required when PGAI_PROJECT_NAME is not set.
#
# Output (stdout): key=value pairs, one per line. Consumers source or eval
# this output to get variables, or they grep for specific keys.
#
# Keys produced:
#   KANBAN_VERSION        — VERSION file content (via get_kanban_version() in lib/version.sh)
#   KANBAN_ROOT           — absolute path to kanban root
#   ACTIVE_RC             — Active RC version from release-state.md, or "none"
#   RC_OPENED_AT          — RC Opened At from release-state.md as written, or
#                           empty when the file, heading or value is missing
#   LAST_RELEASED         — Last Released from release-state.md
#   HALT_FLAG             — "yes" if HALT sentinel present (project or global scope), "no" otherwise
#   HALT_OVERWATCH_FLAG    — "yes" if HALT_OVERWATCH file exists, "no" otherwise
#   HALT_TEXT             — per-project halt display string: "" (normal),
#                           "HALT-AFTER <event>" (draining), or "HALT" (halted)
#   HALT_SUMMARY          — taskbar summary token: GLOBAL (global halt active),
#                           PROJECT (one or more per-project halts, no global),
#                           or "none" (no halts active).
#                           GLOBAL wins when both global and per-project halts are present.
#   HALT_SCOPES           — colon-separated named list of all active halt scopes.
#                           Contains "GLOBAL" if the global halt is active, plus
#                           one entry per halted project name.  Empty when no halts.
#                           Example: "GLOBAL:pgai-chomp-man" or "pgai-chomp-man:pgai-three-bears"
#
#   TOTAL_TICKETS         — total task count (all states)
#   DONE_COUNT            — tasks in DONE or WONT-DO state
#   WORKING_COUNT         — tasks in WORKING state
#   BLOCKED_COUNT         — tasks in BLOCKED state
#   WAITING_COUNT         — tasks in WAITING state
#   BACKLOG_COUNT         — tasks in BACKLOG state
#
#   QUEUE_<ROLE>_TOTAL    — total tickets in queue for role
#   QUEUE_<ROLE>_DONE     — done tickets for role
#   QUEUE_<ROLE>_WORKING  — working tickets for role
#   QUEUE_<ROLE>_BLOCKED  — blocked tickets for role
#   QUEUE_<ROLE>_WAITING  — waiting tickets for role
#   (ROLE = PM, CODER, WRITER, TESTER, CM)
#
#   WORKING_TASK_ID       — ID of the currently-working task, or ""
#   WORKING_TASK_MTIME    — mtime of working task status.md (epoch seconds), or ""
#   WORKING_TASK_ROLE     — role of working task, or ""
#
#   LAST_DONE_1 .. LAST_DONE_5  — IDs of most-recently-done tasks (newest first)
#   LAST_DONE_ROLE_1 .. _5      — roles of those tasks
#
#   LAST_SHIPPED_1 .. LAST_SHIPPED_5    — semver tag names, newest first
#   LAST_SHIPPED_AGO_1 .. _AGO_5       — human-readable elapsed time (e.g. "2m ago")
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH — kanban root override
#   PGAI_PROJECT_NAME           — project name (alternative to --project)

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

# --- Argument parsing ---
KANBAN_ROOT_ARG=""
PROJECT_NAME=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT_ARG="${2:-}"
      shift 2
      ;;
    --project)
      PROJECT_NAME="${2:-}"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

# --- Resolve kanban root ---
KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"

# --- Resolve script dir / repo root ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [[ "$REPO_ROOT" != "/" ]] && [[ ! -d "$REPO_ROOT/.git" ]]; do
  REPO_ROOT="$(dirname "$REPO_ROOT")"
done
if [[ "$REPO_ROOT" == "/" ]]; then
  # Fallback to relative path if .git not found (e.g. shallow export or unusual layout)
  REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
fi

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Local-only render guard: this is a dashboard render path, so
# pp_last_released_version below must never perform a network git fetch.
# Honored by pp_last_released_version before its `git fetch origin --tags`
# call; local tags/refs already on disk are authoritative for LAST_RELEASED.
export PGAI_GIT_LOCAL_ONLY=1

# Source projects lib for projects_cfg_list (resolve project from registry)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# Source shared Python-helper resolver (live-install anchor first — D3 fix)
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared version helper (single tier-order decision point for framework version)
# shellcheck source=lib/version.sh
source "${SCRIPT_DIR}/lib/version.sh"
# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# Source pp_run_ops for the task-count scan (pgai_agent_kanban.dashboard.task_counts)
# shellcheck source=lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"
# Source the release-state.md field readers (release_state_section)
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"

# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# Resolve the project name: explicit --project arg takes precedence,
# then the PGAI_PROJECT_NAME env var.  If neither is set, fail loud.
# Silent first-project fallback has been removed (Category-B fail-loud sweep).
_resolved_project="${PROJECT_NAME:-${PGAI_PROJECT_NAME:-}}"
if [[ -z "$_resolved_project" ]]; then
    echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
    exit 1
fi
TASKS_ROOT="$(pp_tasks_dir "$_resolved_project")"

# Resolve branch_prefix for the active project.  Used to filter git tag and
# branch listings so only project-owned refs appear in dashboard output.
# When prefix is empty all tags/branches are shown (legacy behaviour).
_BRANCH_PREFIX="$(pp_branch_prefix "$_resolved_project" 2>/dev/null || true)"

# Tag pattern for git tag --list:
#   - Empty prefix  → '*'  (passing '' to --list matches nothing)
#   - Non-empty prefix → '${PREFIX}*'
if [[ -z "$_BRANCH_PREFIX" ]]; then
  _TAG_PATTERN="*"
else
  _TAG_PATTERN="${_BRANCH_PREFIX}*"
fi

# Project-scoped release-state.md resolved via _resolved_project (always set above,
# either from the --project arg or PGAI_PROJECT_NAME).
RELEASE_STATE="$(pp_project_root "$_resolved_project")/release-state.md"

# HALT: per-project file if present (resolved via _resolved_project), else global.
# Multi-project orchestrator may treat them additively (per-project HALT halts
# that project, global HALT halts everything).
#
# Use _resolved_project (resolved from --project or PGAI_PROJECT_NAME) rather
# than the raw $PROJECT_NAME so that per-project HALT sentinels are found
# regardless of how the script is invoked.
_proj_root_for_halt="$(pp_project_root "$_resolved_project" 2>/dev/null || true)"
if [[ -n "$_proj_root_for_halt" ]] && [[ -f "${_proj_root_for_halt}/HALT" ]]; then
  HALT_FILE="${_proj_root_for_halt}/HALT"
else
  HALT_FILE="$KANBAN_ROOT/HALT"
fi

# Expose the resolved project name for downstream consumers
PROJECT_NAME_RESOLVED="$_resolved_project"

# -------------------------------------------------------------------
# Helper: emit a key=value line, quoting the value for safety.
# We use single-line values only; newlines in values are replaced with space.
# -------------------------------------------------------------------
emit() {
  local key="$1"
  local val="${2:-}"
  # Strip any newlines from val
  val="${val//$'\n'/ }"
  printf '%s=%s\n' "$key" "$val"
}

# -------------------------------------------------------------------
# Kanban version (single source: VERSION-file-first via shared helper)
# Tier order: $KANBAN_ROOT/VERSION > $REPO_ROOT/VERSION > git tag > unknown
# Do not add per-script tier-order logic here; get_kanban_version() in
# lib/version.sh is the single decision point for framework version resolution.
# LAST_RELEASED is not yet available here; tier 4 (last_released) is skipped —
# tier 1 ($KANBAN_ROOT/VERSION, written by install.sh) handles the normal case.
# -------------------------------------------------------------------
KANBAN_VERSION="$(get_kanban_version "$KANBAN_ROOT" "$REPO_ROOT" "")"
emit KANBAN_VERSION "$KANBAN_VERSION"
emit KANBAN_ROOT "$KANBAN_ROOT"
emit PROJECT_NAME "$PROJECT_NAME_RESOLVED"

# -------------------------------------------------------------------
# Release state
# -------------------------------------------------------------------
# Active RC and RC Opened At in one read through the one release-state
# reader. ACTIVE_RC is a vX.Y.Z version, or "none" for a missing file, an
# empty value, or anything that is not a version. RC_OPENED_AT is the value as
# written with whitespace removed, or empty when the file, heading or value is
# missing.
_rs_fields="$(release_state_section "$RELEASE_STATE" "Active RC" "RC Opened At")" || true
_rs_arc=""
RC_OPENED_AT=""
{ IFS= read -r _rs_arc || true; IFS= read -r RC_OPENED_AT || true; } <<< "$_rs_fields"
_rs_arc="${_rs_arc//[[:space:]]/}"
RC_OPENED_AT="${RC_OPENED_AT//[[:space:]]/}"
if [[ "$_rs_arc" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  ACTIVE_RC="$_rs_arc"
else
  ACTIVE_RC="none"
fi
# Last Released is derived from git tags via pp_last_released_version rather
# than read from release-state.md, so it stays accurate when the live install
# is not a git repo or when deterministic patches advance tags without touching
# the file.
LAST_RELEASED="$(pp_last_released_version "$_resolved_project" 2>/dev/null || echo "none")"
[[ -z "$LAST_RELEASED" ]] && LAST_RELEASED="none"
emit ACTIVE_RC "$ACTIVE_RC"
emit RC_OPENED_AT "$RC_OPENED_AT"
emit LAST_RELEASED "$LAST_RELEASED"

# -------------------------------------------------------------------
# HALT_FLAG, HALT_TEXT — detect halt state via the shared halt_state.py
# helper (same helper used by attention.sh and the wake path).
#
# halt_state.py returns (state, event) where state is one of:
#   normal   — no halt sentinel present
#   draining — HALT-AFTER sentinel present with valid token (chain still running)
#   halted   — HALT sentinel present (chain stopped)
#
# HALT_FLAG:
#   "yes" when state=halted (HALT sentinel present at project or global scope)
#   "no"  when state=draining or normal
# HALT_TEXT (display string consumed by show-header.sh coloring):
#   ""               when normal
#   "HALT-AFTER <event>" when draining  → show-header renders yellow
#   "HALT"               when halted    → show-header renders red
#
# Global-scope fallback: if halt_state.py is unavailable or the project root
# cannot be resolved, fall back to the file-existence check against HALT_FILE
# (which is set to $KANBAN_ROOT/HALT when no per-project HALT is present).
# -------------------------------------------------------------------
_HALT_TEXT=""
_HALT_FLAG="no"
# Resolve halt_state.py via the shared helper resolver (live-install anchor first — D3 fix).
_HS_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/halt_state.py")"
if [[ -n "$_proj_root_for_halt" && -n "$_HS_PY" ]]; then
  # PYTHONPATH must point at the parent of pgai_agent_kanban/ so imports resolve.
  _HS_PY_PYTHONPATH="$(dirname "$(dirname "$(dirname "$_HS_PY")")")"
  _hs_raw="$(PYTHONPATH="$_HS_PY_PYTHONPATH" python3 "$_HS_PY" "$_proj_root_for_halt" 2>/dev/null || echo "normal	None")"
  _hs_state="${_hs_raw%%	*}"
  _hs_event="${_hs_raw##*	}"
  if [[ "$_hs_state" == "halted" ]]; then
    _HALT_FLAG="yes"
    _HALT_TEXT="HALT"
  elif [[ "$_hs_state" == "draining" ]]; then
    if [[ -n "$_hs_event" && "$_hs_event" != "None" ]]; then
      _HALT_TEXT="HALT-AFTER ${_hs_event}"
    else
      _HALT_TEXT="HALT-AFTER"
    fi
  else
    # state=normal: fall back to global HALT file check (catches $KANBAN_ROOT/HALT
    # when the project root has no sentinel of its own).
    if [[ -f "$HALT_FILE" ]]; then
      _HALT_FLAG="yes"
      _HALT_TEXT="HALT"
    fi
  fi
else
  # halt_state.py unavailable: fall back to file-existence check.
  if [[ -f "$HALT_FILE" ]]; then
    _HALT_FLAG="yes"
    _HALT_TEXT="HALT"
  fi
fi
emit HALT_FLAG "$_HALT_FLAG"
emit HALT_TEXT "$_HALT_TEXT"
unset _HALT_TEXT _HALT_FLAG _HS_PY _HS_PY_PYTHONPATH _hs_raw _hs_state _hs_event

# -------------------------------------------------------------------
# HALT_SUMMARY, HALT_SCOPES — enumerate ALL active halts across every
# registered project plus the global HALT sentinel.
#
# HALT_SUMMARY: GLOBAL | PROJECT | none
#   GLOBAL  — $KANBAN_ROOT/HALT is present (global master switch).
#             GLOBAL wins even when per-project halts are also active.
#   PROJECT — no global halt, but one or more projects/<name>/HALT files exist.
#   none    — no halt files found anywhere.
#
# HALT_SCOPES: colon-separated list of named scopes (order: GLOBAL first,
#   then alphabetical project names).  Empty string when no halts.
#   Downstream consumers split on ':' to render the full set.
#   Project names are filesystem directory names and cannot contain ':'.
# -------------------------------------------------------------------
_halt_global="no"
_halt_scopes=""

# Check global halt first
if [[ -f "${KANBAN_ROOT}/HALT" ]]; then
  _halt_global="yes"
  _halt_scopes="GLOBAL"
fi

# Iterate all registered projects and check for per-project HALT files.
# projects_cfg_list is sourced from lib/projects.sh (already sourced above).
while IFS= read -r _halt_proj; do
  [[ -z "$_halt_proj" ]] && continue
  _halt_proj_root="$(pp_project_root "$_halt_proj" 2>/dev/null || true)"
  if [[ -n "$_halt_proj_root" ]] && [[ -f "${_halt_proj_root}/HALT" ]]; then
    if [[ -z "$_halt_scopes" ]]; then
      _halt_scopes="$_halt_proj"
    else
      _halt_scopes="${_halt_scopes}:${_halt_proj}"
    fi
  fi
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

# Determine HALT_SUMMARY
if [[ "$_halt_global" == "yes" ]]; then
  _halt_summary="GLOBAL"
elif [[ -n "$_halt_scopes" ]]; then
  _halt_summary="PROJECT"
else
  _halt_summary="none"
fi

emit HALT_SUMMARY "$_halt_summary"
emit HALT_SCOPES  "$_halt_scopes"
unset _halt_global _halt_scopes _halt_summary _halt_proj _halt_proj_root

# -------------------------------------------------------------------
# HALT_OVERWATCH flag
# -------------------------------------------------------------------
HALT_OVERWATCH_FILE="$KANBAN_ROOT/HALT_OVERWATCH"
if [[ -f "$HALT_OVERWATCH_FILE" ]]; then
  emit HALT_OVERWATCH_FLAG "yes"
else
  emit HALT_OVERWATCH_FLAG "no"
fi

# -------------------------------------------------------------------
# Last 5 shipped releases — git tags sorted by version (newest first),
# with a human-readable elapsed-time hint next to each tag.
#
# Keys produced:
#   LAST_SHIPPED_1 .. LAST_SHIPPED_5  — semver tag name (e.g. "v0.21.28")
#   LAST_SHIPPED_AGO_1 .. _5         — elapsed time string (e.g. "2 min ago")
# When fewer than 5 tags exist the remaining slots are emitted as empty.
#
# Elapsed-time granularity (coarse-grained, no millisecond precision):
#   < 60 s        → "Ns ago"
#   < 3600 s      → "Nm ago"
#   < 86400 s     → "Nh ago"
#   >= 86400 s    → "Nd ago"
# -------------------------------------------------------------------
_shipped_out="$(git -C "$REPO_ROOT" tag --list "$_TAG_PATTERN" --sort=-version:refname 2>/dev/null | head -5 || true)"
_now="$(date +%s)"
_shipped_idx=1
if [[ -n "$_shipped_out" ]]; then
  while IFS= read -r _tag; do
    [[ -z "$_tag" ]] && continue
    # Get the tagger/commit timestamp for this tag
    _tag_epoch="$(git -C "$REPO_ROOT" log -1 --format="%ct" "$_tag" 2>/dev/null || echo "")"
    _ago=""
    if [[ -n "$_tag_epoch" ]] && [[ "$_tag_epoch" =~ ^[0-9]+$ ]]; then
      _diff=$(( _now - _tag_epoch ))
      if [[ $_diff -lt 60 ]]; then
        _ago="${_diff}s ago"
      elif [[ $_diff -lt 3600 ]]; then
        _ago="$(( _diff / 60 ))m ago"
      elif [[ $_diff -lt 86400 ]]; then
        _ago="$(( _diff / 3600 ))h ago"
      else
        _ago="$(( _diff / 86400 ))d ago"
      fi
    fi
    emit "LAST_SHIPPED_${_shipped_idx}" "$_tag"
    emit "LAST_SHIPPED_AGO_${_shipped_idx}" "$_ago"
    _shipped_idx=$(( _shipped_idx + 1 ))
  done <<< "$_shipped_out"
fi
# Pad empty slots up to 5
while [[ $_shipped_idx -le 5 ]]; do
  emit "LAST_SHIPPED_${_shipped_idx}" ""
  emit "LAST_SHIPPED_AGO_${_shipped_idx}" ""
  _shipped_idx=$(( _shipped_idx + 1 ))
done

# -------------------------------------------------------------------
# Task state counts, per-role queue counters, the working task and the last
# five done tasks -- one scan by pgai_agent_kanban.dashboard.task_counts,
# the same function the queue pane (show-queues.sh / GET /show-queues)
# reads its counters from. It prints the KEY=value lines emitted below.
# -------------------------------------------------------------------

METRICS="$(pp_run_ops pgai_agent_kanban.dashboard.task_counts "$TASKS_ROOT")"

# Emit all metrics from Python output
while IFS='=' read -r key val; do
  [[ -n "$key" ]] || continue
  emit "$key" "$val"
done <<< "$METRICS"
