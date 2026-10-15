#!/usr/bin/env bash
# show-multi.sh
# Multi-project orchestrator for the dashboard.
#
# Reads $KANBAN_ROOT/projects.cfg, iterates each registered project in
# priority order, and composes a stacked view by calling per-project
# show-*.sh scripts with --project <name>.
#
# Architecture: stacked overview (Option A from ARCHITECTURE.md):
# all projects visible simultaneously, no per-project drilldown windows.
#
# Layout:
#   framework: v0.21.0    13:42:31 EDT    HALT(global): off
#
#   ═══ pgai-agent-kanban (priority 1) ═══
#     RC: v0.21.0 (15m active)    Last Released: v0.20.0
#     Progress: ▓▓▓▓▓░░░░ 45%   HALT(local): off
#
#   ═══ video-editor (priority 2) ═══
#     RC: none    Last Released: v0.0.0
#     Progress: (idle)
#
#   Next cron firings:           pm: in 6 min   coder: in 1 min
#
# When projects.cfg has a single entry, show-multi.sh delegates to the
# single-project show-header.sh (legacy single-project layout).
#
# Usage:
#   show-multi.sh [--kanban-root <path>] [--mode header|queues|progress] [-h|--help]
#
# Flags:
#   --kanban-root <path>  Kanban root to render (default: $PGAI_AGENT_KANBAN_ROOT_PATH);
#                         passed on to each per-project show-*.sh
#   --mode <mode>         What to render (see Modes; default: header)
#   -h, --help            Print this usage and exit 0
#   Any other argument is passed on to each per-project show-*.sh unchanged.
#
# Modes:
#   header    — render the stacked header (default)
#   queues    — render queues for each registered project
#   progress  — render progress for each registered project
#
# Configuration (kanban.cfg [dashboard], overridden by the environment):
#   color_header / PGAI_DASHBOARD_COLOR_HEADER   — header text color (default: cyan)
#   color_halt   / PGAI_DASHBOARD_COLOR_HALT     — halt warning color (default: red)
#   color_ok     / PGAI_DASHBOARD_COLOR_OK       — ok indicator color (default: green)
#
# Environment:
#   TERM=dumb  — disables ANSI codes
#   NO_COLOR=1 — disables ANSI codes

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# --- Resolve script dir ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source helpers
# shellcheck source=lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# Local-only render guard: this is a dashboard render path, so
# pp_last_released_version below (per-project last-released resolution) must
# never perform a network git fetch.
export PGAI_GIT_LOCAL_ONLY=1

# shellcheck source=lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"
# shellcheck source=lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"
# shellcheck source=lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
# shellcheck source=../lib/header_help.sh
source "${SCRIPT_DIR}/../lib/header_help.sh"
# get_val reads one KEY=VALUE pair from DATA (lib/dashboard_common.sh).
# shellcheck source=../lib/dashboard_common.sh
source "${SCRIPT_DIR}/../lib/dashboard_common.sh"
# release_state_active_rc reads each project's Active RC (lib/cm/common.sh).
# shellcheck source=../lib/cm/common.sh
source "${SCRIPT_DIR}/../lib/cm/common.sh"
# Source shared version helper (single tier-order decision point — same as show-header.sh).
# Note: lib/version.sh lives at dashboard/lib/version.sh (SCRIPT_DIR/lib/), not ../lib/.
# shellcheck source=lib/version.sh
source "${SCRIPT_DIR}/lib/version.sh"

# --- Parse args ---
MODE="header"
KANBAN_ROOT_ARG=""
PASSTHRU=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      MODE="${2:-header}"
      shift 2
      ;;
    --kanban-root)
      KANBAN_ROOT_ARG="${2:-}"
      PASSTHRU+=("--kanban-root" "$KANBAN_ROOT_ARG")
      shift 2
      ;;
    --help|-h)
      header_help_print "${BASH_SOURCE[0]}"
      exit 0
      ;;
    *)
      PASSTHRU+=("$1")
      shift
      ;;
  esac
done

KANBAN_ROOT="${KANBAN_ROOT_ARG:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"
# Source config — INI format (kanban.cfg) replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "$KANBAN_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$KANBAN_ROOT/kanban.cfg" chain pm_mode automatic)}"
    export PGAI_DASHBOARD_COLOR_HEADER="${PGAI_DASHBOARD_COLOR_HEADER:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_header cyan)}"
    export PGAI_DASHBOARD_COLOR_HALT="${PGAI_DASHBOARD_COLOR_HALT:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_halt red)}"
    export PGAI_DASHBOARD_COLOR_OK="${PGAI_DASHBOARD_COLOR_OK:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_ok green)}"
    export PGAI_DASHBOARD_COLOR_DIM="${PGAI_DASHBOARD_COLOR_DIM:-$(read_ini "$KANBAN_ROOT/kanban.cfg" dashboard color_dim white)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

set -euo pipefail

# --- ANSI color support ---
USE_COLOR=true
if [[ "${TERM:-}" == "dumb" ]] || [[ "${NO_COLOR:-}" == "1" ]]; then
  USE_COLOR=false
fi

COLOR_HEADER="${PGAI_DASHBOARD_COLOR_HEADER:-cyan}"
COLOR_HALT="${PGAI_DASHBOARD_COLOR_HALT:-red}"
COLOR_OK="${PGAI_DASHBOARD_COLOR_OK:-green}"
COLOR_DIM="${PGAI_DASHBOARD_COLOR_DIM:-white}"

# ansi_code() is defined in lib/script_common.sh (sourced above). The
# per-file version this replaced used printf for matched colors and echo
# for the unmatched/disabled case; every call site captures the result via
# command substitution, which strips the trailing newline either form
# leaves behind, so the shared echo-only implementation renders identically.

C_HEADER="$(ansi_code "$COLOR_HEADER")"
C_HALT="$(ansi_code "$COLOR_HALT")"
C_OK="$(ansi_code "$COLOR_OK")"
C_DIM="$(ansi_code "$COLOR_DIM")"
C_BOLD="$(ansi_code bold)"
RESET="$(ansi_code reset)"

# --- Get list of registered projects ---
PROJECTS=()
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  PROJECTS+=("$line")
done < <(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_list 2>/dev/null || true)

# --- Fallback: if projects.cfg is empty or missing, use single-project mode ---
if [[ ${#PROJECTS[@]} -eq 0 ]]; then
  # Single-project legacy mode — call the per-project script with no --project
  # flag, preserving the existing single-project layout. In queues mode that
  # reads PGAI_PROJECT_NAME's project; with it unset, show-queues.sh refuses
  # (show_queues.no_project, exit 1, nothing on stdout) and exec passes the
  # refusal and its exit status straight through -- the pane never shows
  # counts that were not read.
  case "$MODE" in
    header)   exec "${SCRIPT_DIR}/show-header.sh" "${PASSTHRU[@]}" ;;
    queues)   exec "${SCRIPT_DIR}/show-queues.sh" "${PASSTHRU[@]}" ;;
    progress) exec "${SCRIPT_DIR}/show-progress.sh" "${PASSTHRU[@]}" ;;
    *) echo "show-multi.sh: unknown mode '$MODE'" >&2; exit 1 ;;
  esac
fi

# --- Single-project shortcut: if exactly one project, delegate to legacy script ---
# This keeps the single-project UX identical (richer top-line layout with
# cron firings sidebar) when only one project is registered.
if [[ ${#PROJECTS[@]} -eq 1 ]]; then
  proj="${PROJECTS[0]}"
  case "$MODE" in
    header)   exec "${SCRIPT_DIR}/show-header.sh" "${PASSTHRU[@]}" --project "$proj" ;;
    queues)   exec "${SCRIPT_DIR}/show-queues.sh" "${PASSTHRU[@]}" --project "$proj" ;;
    progress) exec "${SCRIPT_DIR}/show-progress.sh" "${PASSTHRU[@]}" --project "$proj" ;;
    *) echo "show-multi.sh: unknown mode '$MODE'" >&2; exit 1 ;;
  esac
fi

# --- Multi-project rendering ---
case "$MODE" in
  header)
    # Top line: kanban version + time + global HALT
    # Use the shared resolver (Tier 1 = $KANBAN_ROOT/VERSION) — same decision
    # point as show-header.sh. Do not re-derive version here.
    KANBAN_VERSION="$(get_kanban_version "$KANBAN_ROOT" "" "")"
    TIMESTAMP="$(date '+%H:%M:%S %Z')"

    # Obtain HALT_SUMMARY from the data layer (same mechanism as show-header.sh).
    # HALT_SUMMARY: GLOBAL (global halt active), PROJECT (per-project halt, no global), none.
    # GLOBAL wins when both are present — precedence is enforced inside data.sh.
    DATA="$("$SCRIPT_DIR/data.sh" --kanban-root "$KANBAN_ROOT" 2>/dev/null || true)"
    HALT_SUMMARY="$(get_val HALT_SUMMARY "none")"

    if [[ "$HALT_SUMMARY" == "GLOBAL" ]]; then
      HALT_GLOBAL_STR="${C_HALT}HALT GLOBAL${RESET}"
    elif [[ "$HALT_SUMMARY" == "PROJECT" ]]; then
      HALT_GLOBAL_STR="${C_HALT}HALT PROJECT${RESET}"
    else
      HALT_GLOBAL_STR="${C_OK}HALT: off${RESET}"
    fi

    printf '%sframework: %s%s    %s    %s\n' \
      "$C_BOLD" "$KANBAN_VERSION" "$RESET" \
      "$TIMESTAMP" \
      "$HALT_GLOBAL_STR"
    echo ""

    # Per-project section
    for proj in "${PROJECTS[@]}"; do
      prio="$(KANBAN_ROOT="$KANBAN_ROOT" projects_cfg_priority "$proj" 2>/dev/null || echo "?")"
      printf '%s═══ %s (priority %s) ═══%s\n' \
        "$C_HEADER" "$proj" "$prio" "$RESET"

      # Get project-scoped data
      RELEASE_STATE="${KANBAN_ROOT}/projects/${proj}/release-state.md"
      # A vX.Y.Z version; anything else (missing, empty, malformed) reads "none".
      ACTIVE_RC="$(release_state_active_rc "$RELEASE_STATE" none)"

      # Resolve last-released via the canonical helper (pp_last_released_version).
      # Map the v0.0.0 sentinel to "none" to preserve the "no releases yet" UX.
      _raw_last="$(KANBAN_ROOT="$KANBAN_ROOT" pp_last_released_version "$proj" 2>/dev/null || echo "v0.0.0")"
      if [[ "$_raw_last" == "v0.0.0" ]]; then
        LAST_RELEASED="none"
      else
        LAST_RELEASED="$_raw_last"
      fi

      # Per-project HALT (separate from global)
      if [[ -f "${KANBAN_ROOT}/projects/${proj}/HALT" ]]; then
        HALT_LOCAL_STR="${C_HALT}HALT(local): YES${RESET}"
      else
        HALT_LOCAL_STR="${C_DIM}HALT(local): off${RESET}"
      fi

      printf '  RC: %s    Last Released: %s    %s\n' \
        "$ACTIVE_RC" "$LAST_RELEASED" "$HALT_LOCAL_STR"
      echo ""
    done

    # Cron firings (global, rendered once at the bottom)
    CRONTAB_TEXT="$(crontab -l 2>/dev/null || true)"
    if [[ -n "$CRONTAB_TEXT" ]]; then
      # cron_parser.py is resolved from the live-install anchor only.
      # The dev tree is DATA to the live runtime; executing python code under
      # PGAI_DEV_TREE_PATH is prohibited for live-runtime scripts.
      CRON_PARSER="${KANBAN_ROOT}/pm-agent/lib/cron_parser.py"
      if [[ -f "$CRON_PARSER" ]]; then
        printf '%sNext cron firings:%s ' "$C_HEADER" "$RESET"
        python3 - "$CRONTAB_TEXT" "$CRON_PARSER" <<'PY' 2>/dev/null || echo ""
import sys, importlib.util, pathlib
crontab_text = sys.argv[1]
parser_path = pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("cron_parser", parser_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
result = mod.next_firings(crontab_text)
# Roster comes from the schedule source itself — iterating result.items()
# preserves crontab line order, never a literal agent tuple that can go
# stale as the installed schedule grows.
parts = []
for agent, val in result.items():
    if val is None:
        continue
    if isinstance(val, int):
        if val <= 0:
            s = "now"
        elif val == 1:
            s = "in 1 min"
        else:
            s = f"in {val} min"
    else:
        s = str(val)
    parts.append(f"{agent}: {s}")
print("   ".join(parts))
PY
      fi
    fi
    ;;

  queues)
    for proj in "${PROJECTS[@]}"; do
      printf '%s═══ %s ═══%s\n' "$C_HEADER" "$proj" "$RESET"
      "${SCRIPT_DIR}/show-queues.sh" "${PASSTHRU[@]}" --project "$proj" || true
      echo ""
    done
    ;;

  progress)
    for proj in "${PROJECTS[@]}"; do
      printf '%s═══ %s ═══%s\n' "$C_HEADER" "$proj" "$RESET"
      "${SCRIPT_DIR}/show-progress.sh" "${PASSTHRU[@]}" --project "$proj" --compact || true
      echo ""
    done
    ;;

  *)
    echo "show-multi.sh: unknown mode '$MODE'" >&2
    exit 1
    ;;
esac
