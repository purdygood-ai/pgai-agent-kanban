#!/usr/bin/env bash
# dashboard-attention.sh
# Window 3 attention panel — shows BLOCKED tasks, quarantine alerts, and
# active halt scopes.
#
# Designed to be run under `watch -c -n N -- dashboard-attention.sh`.
#
# BLOCKED TASKS section:
#   ⚠ BLOCKED TASKS
#   ──────────────────────────────────────────────────────────────
#   ▶ TASK-ID-HERE
#     Blocked since: HH:MM:SS (Xm ago)
#     Reason:
#       reason text here
#
#     Recommended next step:
#       next step text here
#
# QUARANTINE ALERTS section:
#
# Warning entry (count 1..threshold-1 — file approaching quarantine):
#   ⚠ QUARANTINE ALERTS
#   ──────────────────────────────────────────────────────────────
#   ⚠ pgai-video-generator [priority]: 1 file(s) approaching quarantine
#       PRIORITY-foo.md  (2/3)  malformed filename (expected pattern: ...)
#
# Terminal entry (file already in .rejected/):
#   🔴 pgai-video-generator [bugs]: 1 file(s) quarantined
#       bad-name.md
#       Recover: scripts/recover-rejected.sh pgai-video-generator bad-name.md
#
# Display when no quarantine signals:
#   ⚠ QUARANTINE ALERTS
#   ──────────────────────────────────────────────────────────────
#
#   (no quarantine alerts)
#
# HALTED section: omitted entirely when no halt files present.
# When one or more halts are active:
#   🛑 HALTED
#   ──────────────────────────────────────────────────────────────
#   GLOBAL
#   pgai-chomp-man
#
# Each line names one active halt scope: GLOBAL if $KANBAN_ROOT/HALT is present,
# then each projects/<name>/HALT by project name.
#
# Usage:
#   dashboard-attention.sh [--kanban-root <path>]
#
# Environment:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: ~/pgai_agent_kanban)
#   NO_COLOR                            — set to suppress ANSI colors

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
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
  C_GRAY_BG=$'\033[47;30m'
else
  C_RED=""
  C_GREEN=""
  C_YELLOW=""
  C_BOLD=""
  C_RESET=""
  C_GRAY_BG=""
fi

# ---------------------------------------------------------------------------
# Status bar: [PM:mode] [HALT:<state>]
# HALT state values: off (normal), HALT (halted), HALT-AFTER <event> (draining)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TEAM_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$TEAM_DIR/.." && pwd)"

# Source project_paths lib for pp_* helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Source dev_tree helper (resolve_global_dev_tree / require_dev_tree)
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# Source semver helpers (needed for discovery_scan_stale_requirements)
# shellcheck source=lib/semver.sh
source "${SCRIPT_DIR}/../lib/semver.sh"

# Source projects helpers (needed for projects_cfg_list)
# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# Source shared Python-helper resolver (live-install anchor first — D3 fix).
# halt_scope.sh also sources this; guard against double-sourcing via sentinel.
# shellcheck source=lib/helper_resolver.sh
source "${SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared halt scope helper (single decision point for halt scope)
source "${SCRIPT_DIR}/lib/halt_scope.sh"

# Source discovery helpers (provides discovery_scan_stale_requirements and deps)
# TEAM_ROOT must be set before sourcing discovery.sh (used by log shim).
TEAM_ROOT="${TEAM_DIR}"
# shellcheck source=lib/discovery.sh
source "${SCRIPT_DIR}/../lib/discovery.sh"

# Source optional config files so env-file variables (e.g. PGAI_KANBAN_PM_MODE)
# are available in fresh subshells (watch -n invocation).
# Pattern matches wake/claude.sh lines ~136-139.
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc" 2>/dev/null || true
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env" 2>/dev/null || true
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg" 2>/dev/null || true
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# PM mode
PM_RAW="${PGAI_KANBAN_PM_MODE:-automatic}"
if [[ "$PM_RAW" == "manual" ]]; then
  PM_LABEL="manual"
else
  PM_LABEL="auto"
fi

# HALT state — via shared halt_scope.sh helper (single decision point).
# dashboard_halt_scope returns state<TAB>scope<TAB>event; scope is GLOBAL or
# a project name.  HALT_LABEL is the display value for the status bar header.
# Passes empty explicit-project so ALL projects are scanned.
_hs_state=""
_hs_scope=""
_hs_event=""
IFS=$'\t' read -r _hs_state _hs_scope _hs_event \
    < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "")

# Determine scope word for the status-bar label (GLOBAL stays GLOBAL; project
# names collapse to PROJECT — the HALTED section below lists per-project detail).
_hs_scope_word=""
if [[ "$_hs_scope" == "GLOBAL" ]]; then
    _hs_scope_word="GLOBAL"
elif [[ -n "$_hs_scope" ]]; then
    _hs_scope_word="PROJECT"
fi

HALT_LABEL="off"
if [[ "$_hs_state" == "halted" && -n "$_hs_scope_word" ]]; then
    HALT_LABEL="HALT ${_hs_scope_word}"
elif [[ "$_hs_state" == "draining" && -n "$_hs_scope_word" ]]; then
    if [[ -n "$_hs_event" ]]; then
        HALT_LABEL="HALT-AFTER ${_hs_scope_word} ${_hs_event}"
    else
        HALT_LABEL="HALT-AFTER ${_hs_scope_word}"
    fi
fi
unset _hs_state _hs_scope _hs_scope_word _hs_event

# Render status bar
if [[ "$USE_COLOR" == "true" ]]; then
  if [[ "$PM_LABEL" == "auto" ]]; then
    PM_COLOR="$C_GREEN"
  else
    PM_COLOR="$C_YELLOW"
  fi

  if [[ "$HALT_LABEL" == "off" ]]; then
    HALT_COLOR="$C_GREEN"
  elif [[ "$HALT_LABEL" == HALT\ GLOBAL || "$HALT_LABEL" == HALT\ PROJECT ]]; then
    HALT_COLOR="$C_RED"
  else
    # draining (HALT-AFTER GLOBAL/PROJECT <event>): use yellow
    HALT_COLOR="$C_YELLOW"
  fi

  printf '%s [%sPM:%s%s] [%sHALT:%s%s] %s\n\n' \
    "$C_GRAY_BG" \
    "$PM_COLOR" "$PM_LABEL" "$C_GRAY_BG" \
    "$HALT_COLOR" "$HALT_LABEL" "$C_GRAY_BG" \
    "$C_RESET"
else
  printf '[PM:%s] [HALT:%s]\n\n' "$PM_LABEL" "$HALT_LABEL"
fi

# ---------------------------------------------------------------------------
# Print header
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  DIVIDER="$(printf '%.0s\xe2\x94\x80' {1..68})"
  printf '%s%s\u26a0 BLOCKED TASKS%s\n' "$C_RED" "$C_BOLD" "$C_RESET"
else
  DIVIDER="$(printf '%.0s-' {1..68})"
  printf '! BLOCKED TASKS\n'
fi
printf '%s\n' "$DIVIDER"

# ---------------------------------------------------------------------------
# Collect task directories for all registered projects (iterate-all).
# scan_attention.py is called once per project so blocked/transient/stale
# sections cover every registered project rather than silently substituting
# the first-registered project.  On a single-project install the loop runs
# once and behaviour is unchanged.  When no projects are registered the
# array contains one empty-string entry so the Python script renders its
# graceful "(no blocked tasks)" message.
# ---------------------------------------------------------------------------
_ATTN_TASK_ROOTS=()
while IFS= read -r _attn_proj; do
    [[ -z "$_attn_proj" ]] && continue
    _attn_root="$(KANBAN_ROOT="$KANBAN_ROOT" pp_tasks_dir "$_attn_proj" 2>/dev/null || true)"
    [[ -n "$_attn_root" ]] && _ATTN_TASK_ROOTS+=("$_attn_root")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)
# Fallback: empty string so Python renders a graceful no-data message
if [[ ${#_ATTN_TASK_ROOTS[@]} -eq 0 ]]; then
    _ATTN_TASK_ROOTS=("")
fi

_COLOR_ARG="--color"
if [[ "$USE_COLOR" != "true" ]]; then
  _COLOR_ARG="--no-color"
fi
# Resolve scan_attention.py via shared helper resolver (live-install anchor first \u2014 D3 fix).
_SCAN_ATTENTION_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "dashboard/scan_attention.py")"
for _attn_root in "${_ATTN_TASK_ROOTS[@]}"; do
    python3 "$_SCAN_ATTENTION_PY" \
      blocked-tasks "$_attn_root" "$_COLOR_ARG"
done

# ---------------------------------------------------------------------------
# Transient API error tasks: surface tasks blocked by transient
# provider errors (529/5xx/rate-limit) that are queued for auto-retry.
# These have Needs Human=no and are visually distinct from real BLOCKED tasks.
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s\u27f3 TRANSIENT / RETRYABLE%s\n' "$C_YELLOW" "$C_BOLD" "$C_RESET"
else
  printf '\n> TRANSIENT / RETRYABLE\n'
fi
printf '%s\n' "$DIVIDER"
for _attn_root in "${_ATTN_TASK_ROOTS[@]}"; do
    python3 "$_SCAN_ATTENTION_PY" \
      transient-tasks "$_attn_root" "$_COLOR_ARG"
done

# ---------------------------------------------------------------------------
# Stale WORKING tasks: surface tasks stuck in WORKING longer than
# max_task_seconds BEFORE the watchdog kill lands so the operator can
# intervene.  Reads max_task_seconds from kanban.cfg (default 5400s).
# ---------------------------------------------------------------------------
_MAX_TASK_SEC="${MAX_TASK_SECONDS:-}"
if [[ -z "$_MAX_TASK_SEC" && -f "$KANBAN_ROOT/kanban.cfg" ]]; then
  _MAX_TASK_SEC="$(read_ini "$KANBAN_ROOT/kanban.cfg" wake max_task_seconds 5400 2>/dev/null || echo 5400)"
fi
_MAX_TASK_SEC="${_MAX_TASK_SEC:-5400}"

if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s⏰ STALE WORKING TASKS%s\n' "$C_YELLOW" "$C_BOLD" "$C_RESET"
else
  printf '\n~ STALE WORKING TASKS\n'
fi
printf '%s\n' "$DIVIDER"
for _attn_root in "${_ATTN_TASK_ROOTS[@]}"; do
    python3 "$_SCAN_ATTENTION_PY" \
      stale-working-tasks "$_attn_root" "$_COLOR_ARG" \
      --max-task-seconds "${_MAX_TASK_SEC}"
done
unset _MAX_TASK_SEC

# ---------------------------------------------------------------------------
# Quarantined files (CODER-20260528-024): list files in each project's
# rejected/ directory with type, filename, reason, and retry count.
# Section is suppressed entirely when no files are quarantined.
# ---------------------------------------------------------------------------
python3 "$_SCAN_ATTENTION_PY" \
  rejected-files "$KANBAN_ROOT" "$_COLOR_ARG"

# ---------------------------------------------------------------------------
# Quarantine alerts: scan all projects for rejected files
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s\u26a0 QUARANTINE ALERTS%s\n' "$C_YELLOW" "$C_BOLD" "$C_RESET"
else
  printf '\n! QUARANTINE ALERTS\n'
fi
printf '%s\n' "$DIVIDER"

python3 "$_SCAN_ATTENTION_PY" \
  quarantine "$KANBAN_ROOT" "$_COLOR_ARG" \
  --threshold "${PGAI_DISCOVERY_REJECT_THRESHOLD:-3}"

# ---------------------------------------------------------------------------
# HALTED section: enumerate all active halts and drains — GLOBAL entry when
# $KANBAN_ROOT/HALT is present (or GLOBAL (draining: TOKEN) when
# $KANBAN_ROOT/HALT-AFTER is present with a valid token), plus one entry per
# project for HALT and HALT-AFTER.
# Section is omitted entirely when nothing is halted or draining.
# ---------------------------------------------------------------------------
_halted_scopes=()

# Check global halt (HALT beats HALT-AFTER per halt_state.py precedence)
if [[ -f "${KANBAN_ROOT}/HALT" ]]; then
  _halted_scopes+=("GLOBAL")
elif [[ -f "${KANBAN_ROOT}/HALT-AFTER" ]]; then
  _halt_after_token="$(head -1 "${KANBAN_ROOT}/HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
  if [[ -n "$_halt_after_token" ]]; then
    _halted_scopes+=("GLOBAL (draining: ${_halt_after_token})")
  else
    _halted_scopes+=("GLOBAL (draining)")
  fi
  unset _halt_after_token
fi

# Check per-project halts and drains (also checks HALT-AFTER)
while IFS= read -r _halt_proj; do
  [[ -z "$_halt_proj" ]] && continue
  _halt_proj_root="$(pp_project_root "$_halt_proj" 2>/dev/null || true)"
  [[ -z "$_halt_proj_root" ]] && continue
  if [[ -f "${_halt_proj_root}/HALT" ]]; then
    _halted_scopes+=("$_halt_proj")
  elif [[ -f "${_halt_proj_root}/HALT-AFTER" ]]; then
    _halt_after_token="$(head -1 "${_halt_proj_root}/HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
    if [[ -n "$_halt_after_token" ]]; then
      _halted_scopes+=("${_halt_proj} (draining: ${_halt_after_token})")
    else
      _halted_scopes+=("${_halt_proj} (draining)")
    fi
    unset _halt_after_token
  fi
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

if [[ "${#_halted_scopes[@]}" -gt 0 ]]; then
  if [[ "$USE_COLOR" == "true" ]]; then
    printf '\n%s%s\U0001F6D1 HALTED%s\n' "$C_RED" "$C_BOLD" "$C_RESET"
  else
    printf '\n! HALTED\n'
  fi
  printf '%s\n' "$DIVIDER"

  for _scope in "${_halted_scopes[@]}"; do
    # Draining scopes contain "(draining:" — render in yellow; full halts in red.
    if [[ "$USE_COLOR" == "true" ]]; then
      if [[ "$_scope" == *"(draining"* ]]; then
        printf '  %s%s%s\n' "$C_YELLOW" "$_scope" "$C_RESET"
      else
        printf '  %s%s%s\n' "$C_RED" "$_scope" "$C_RESET"
      fi
    else
      printf '  %s\n' "$_scope"
    fi
  done
fi
unset _halted_scopes _halt_proj _halt_proj_root _scope

# ---------------------------------------------------------------------------
# Stale requirements scan: scan all projects for orphaned operator-authored
# requirements files whose target_version <= current_live and have no
# matching release-notes entry.
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s⚠ STALE REQUIREMENTS%s\n' "$C_YELLOW" "$C_BOLD" "$C_RESET"
else
  printf '\n! STALE REQUIREMENTS\n'
fi
printf '%s\n' "$DIVIDER"

# Build list of all registered projects.  A projects_cfg_list failure means the
# project registry is unreadable — a real error, not an occasion to fall back to
# a single guessed project.  Fail loudly so the operator knows the registry is broken.
# The discovery_scan_stale_requirements function requires KANBAN_ROOT to be set,
# which it is (set at the top of this script).
KANBAN_ROOT="$KANBAN_ROOT"
export KANBAN_ROOT

_stale_project_list=()
if ! mapfile -t _stale_project_list < <(projects_cfg_list 2>/dev/null); then
  printf 'ERROR: projects_cfg_list failed — projects.cfg may be missing or unreadable\n' >&2
  exit 1
fi

_stale_found_any=false

for _proj in "${_stale_project_list[@]}"; do
  [[ -z "$_proj" ]] && continue

  # Collect stale entries for this project into an array.
  # Each line: fname<TAB>target_version<TAB>current_live<TAB>hint
  _stale_entries=()
  while IFS=$'\t' read -r _fname _tver _clive _hint; do
    [[ -z "$_fname" ]] && continue
    _stale_entries+=("${_fname}	${_tver}	${_clive}	${_hint}")
  done < <(discovery_scan_stale_requirements "$_proj" 2>/dev/null || true)

  if [[ "${#_stale_entries[@]}" -eq 0 ]]; then
    continue
  fi

  _stale_found_any=true

  # Render header line for this project.
  _proj_upper="$(printf '%s' "$_proj" | tr '[:lower:]' '[:upper:]' | tr '-' '_')"
  _count="${#_stale_entries[@]}"
  if [[ "$USE_COLOR" == "true" ]]; then
    printf '\n  %s%s⚠ %s: %d stale requirements file(s) (target_version <= current_live)%s\n' \
      "$C_YELLOW" "$C_BOLD" "$_proj_upper" "$_count" "$C_RESET"
  else
    printf '\n  ! %s: %d stale requirements file(s) (target_version <= current_live)\n' \
      "$_proj_upper" "$_count"
  fi

  for _entry in "${_stale_entries[@]}"; do
    IFS=$'\t' read -r _fname _tver _clive _hint <<< "$_entry"

    if [[ "$USE_COLOR" == "true" ]]; then
      printf '      %s%s%s\n' "$C_BOLD" "$_fname" "$C_RESET"
      printf '      target=%s  current_live=%s  (will never be picked up)\n' "$_tver" "$_clive"
    else
      printf '      %s\n' "$_fname"
      printf '      target=%s  current_live=%s  (will never be picked up)\n' "$_tver" "$_clive"
    fi

    if [[ -n "$_hint" ]]; then
      # Resolve the project's requirements dir for the move hint.
      _hint_req_dir="$(pp_requirements_dir "$_hint" 2>/dev/null || true)"
      if [[ "$USE_COLOR" == "true" ]]; then
        printf '      %sHint:%s filename references '\''%s'\'' -- may belong to %s\n' \
          "$C_YELLOW" "$C_RESET" "$_hint" "$_hint"
      else
        printf '      Hint: filename references '\''%s'\'' -- may belong to %s\n' \
          "$_hint" "$_hint"
      fi
      if [[ -n "$_hint_req_dir" ]]; then
        printf '      Recover: mv projects/%s/requirements/%s %s/\n' \
          "$_proj" "$_fname" "$_hint_req_dir"
      else
        printf '      Recover: mv file to the correct project'\''s requirements/ directory\n'
      fi
    else
      printf '      Recover: mv or delete if obsolete\n'
    fi
    printf '\n'
  done

done

if [[ "$_stale_found_any" == "false" ]]; then
  if [[ "$USE_COLOR" == "true" ]]; then
    printf '\n  \033[2m(no stale requirements)\033[0m\n'
  else
    printf '\n  (no stale requirements)\n'
  fi
fi
printf '\n'

# ---------------------------------------------------------------------------
# Pending-approvals needs-human stratum: render each pending HUMAN-APPROVE
# gate task with the raised-hand class and project label.  Placed ABOVE the
# OVERWATCH ledger so approval gates are immediately visible.
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s✋ PENDING APPROVALS%s\n' "$C_RED" "$C_BOLD" "$C_RESET"
else
  printf '\n! PENDING APPROVALS\n'
fi
printf '%s\n' "$DIVIDER"

python3 "$_SCAN_ATTENTION_PY" \
  pending-approvals "$KANBAN_ROOT" "$_COLOR_ARG"

# ---------------------------------------------------------------------------
# OVERWATCH section: surface sweep results from the per-project OVERWATCH
# action logs.  TRANSIENT items (auto-requeued transient API errors) are
# rendered distinctly from needs-human items (ceiling reached, bug filed).
# Section is always present so operators can verify OVERWATCH is running.
# ---------------------------------------------------------------------------
if [[ "$USE_COLOR" == "true" ]]; then
  printf '\n%s%s\U0001f6e1 OVERWATCH%s\n' "$C_YELLOW" "$C_BOLD" "$C_RESET"
else
  printf '\n[W] OVERWATCH\n'
fi
printf '%s\n' "$DIVIDER"

python3 "$_SCAN_ATTENTION_PY" \
  overwatch-section "$KANBAN_ROOT" "$_COLOR_ARG"
unset _SCAN_ATTENTION_PY
