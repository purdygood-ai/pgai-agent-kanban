#!/usr/bin/env bash
# dashboard-status-right.sh
# Helper for tmux status-right — evaluated per refresh via #() syntax.
# Usage: dashboard-status-right.sh <KANBAN_ROOT>
#
# Color mode:
#   USE_COLOR (default): HALT rendered in ANSI red; HALT-AFTER in yellow.
#   NO_COLOR / TERM=dumb: plain text markers (no escape codes).
#
# Halt detection delegates to scripts/dashboard/lib/halt_scope.sh (single
# decision point for halt scope).
#   Global:      $KANBAN_ROOT/HALT and $KANBAN_ROOT/HALT-AFTER
#   Per-project: $KANBAN_ROOT/projects/<name>/HALT and HALT-AFTER
#
# Output: <version>  <PM:mode>  <HALT:state>  <day> <ISO-time>
#   Normal:   HALT:off
#   Halted:   <red>HALT GLOBAL<reset>  (or  [HALT GLOBAL]  in no-color mode)
#   Draining: <yellow>HALT-AFTER GLOBAL <token><reset>

KANBAN_ROOT="${1:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"

# Source project_paths lib for pp_* helpers and shared halt scope helper
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/project_paths.sh
source "${_SCRIPT_DIR}/../lib/project_paths.sh"
# Source shared Python-helper resolver (live-install anchor first — D3 fix).
# halt_scope.sh also sources this; guard against double-sourcing via sentinel.
# shellcheck source=lib/helper_resolver.sh
source "${_SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared halt scope helper (single decision point for halt scope)
source "${_SCRIPT_DIR}/lib/halt_scope.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/../lib/dev_tree.sh"
unset _SCRIPT_DIR

# Source optional config files so env-file variables (e.g. PGAI_KANBAN_PM_MODE)
# are available in fresh subshells (tmux status-right #() syntax).
[[ -f "$KANBAN_ROOT/bashrc" ]] && source "$KANBAN_ROOT/bashrc" 2>/dev/null || true
[[ -f "$KANBAN_ROOT/env" ]] && source "$KANBAN_ROOT/env" 2>/dev/null || true
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg" 2>/dev/null || true
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# read_ini available via project_paths.sh (sourced above).
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# Version
VERSION_FILE="${KANBAN_ROOT}/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
    VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
else
    VERSION="v?"
fi

# PM mode
PM_RAW="${PGAI_KANBAN_PM_MODE:-automatic}"
if [[ "$PM_RAW" == "manual" ]]; then
    PM_STATUS="#[fg=yellow]PM:manual#[default]"
else
    PM_STATUS="#[fg=green]PM:auto#[default]"
fi

# ---------------------------------------------------------------------------
# Color mode detection — honors NO_COLOR env var and TERM=dumb.
# When color is disabled, halt indicators fall back to bracketed text markers.
# ---------------------------------------------------------------------------
_SR_USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _SR_USE_COLOR=false
fi

# ANSI color codes — red for HALT, yellow for HALT-AFTER/draining.
# Using $'...' quoting so the ESC byte (octal \033) is embedded correctly.
if [[ "$_SR_USE_COLOR" == "true" ]]; then
    _SR_C_RED=$'\033[0;31m'
    _SR_C_YELLOW=$'\033[0;33m'
    _SR_C_RESET=$'\033[0m'
else
    _SR_C_RED=""
    _SR_C_YELLOW=""
    _SR_C_RESET=""
fi

# ---------------------------------------------------------------------------
# HALT state detection via shared halt_scope.sh helper.
#
# Single decision point for halt scope: sourced from lib/halt_scope.sh.
# dashboard_halt_scope returns state<TAB>scope<TAB>event where:
#   state — "halted" | "draining" | "normal"
#   scope — "GLOBAL" | <project_name> | ""
#   event — halt event token or ""
#
# HALT_TEXT is set to one of:
#   ""                           — normal (no sentinel)
#   "HALT GLOBAL"                — global halt sentinel
#   "HALT PROJECT"               — per-project halt sentinel
#   "HALT-AFTER GLOBAL <event>"  — global draining sentinel
#   "HALT-AFTER PROJECT <event>" — per-project draining sentinel
# ---------------------------------------------------------------------------
HALT_TEXT=""
_sr_hs_state=""
_sr_hs_scope=""
_sr_hs_event=""

IFS=$'\t' read -r _sr_hs_state _sr_hs_scope _sr_hs_event \
    < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "")

# Collapse per-project name to the word PROJECT for the status-right display.
_sr_hs_scope_word=""
if [[ "$_sr_hs_scope" == "GLOBAL" ]]; then
    _sr_hs_scope_word="GLOBAL"
elif [[ -n "$_sr_hs_scope" ]]; then
    _sr_hs_scope_word="PROJECT"
fi

if [[ "$_sr_hs_state" == "halted" && -n "$_sr_hs_scope_word" ]]; then
    HALT_TEXT="HALT ${_sr_hs_scope_word}"
elif [[ "$_sr_hs_state" == "draining" && -n "$_sr_hs_scope_word" ]]; then
    if [[ -n "$_sr_hs_event" ]]; then
        HALT_TEXT="HALT-AFTER ${_sr_hs_scope_word} ${_sr_hs_event}"
    else
        HALT_TEXT="HALT-AFTER ${_sr_hs_scope_word}"
    fi
fi
unset _sr_hs_state _sr_hs_scope _sr_hs_scope_word _sr_hs_event

# ---------------------------------------------------------------------------
# Build HALT_STATUS from HALT_TEXT.
#
# HALT_TEXT forms:
#   "HALT GLOBAL"                — global halt (red)
#   "HALT PROJECT"               — per-project halt (red)
#   "HALT-AFTER GLOBAL <event>"  — global draining (yellow)
#   "HALT-AFTER PROJECT <event>" — per-project draining (yellow)
#
# Color enabled:  HALT GLOBAL/PROJECT → red; HALT-AFTER → yellow
# No color:       HALT GLOBAL → "[HALT GLOBAL]"; HALT-AFTER → "[HALT-AFTER:...]"
# Normal (empty): "HALT:off"
# ---------------------------------------------------------------------------
if [[ -n "$HALT_TEXT" ]]; then
    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        if [[ "$HALT_TEXT" == HALT\ GLOBAL || "$HALT_TEXT" == HALT\ PROJECT ]]; then
            HALT_STATUS="${_SR_C_RED}${HALT_TEXT}${_SR_C_RESET}"
        else
            # Draining: "HALT-AFTER GLOBAL/PROJECT <event>" — yellow
            HALT_STATUS="${_SR_C_YELLOW}${HALT_TEXT}${_SR_C_RESET}"
        fi
    else
        # NO_COLOR / dumb terminal: bracketed text marker.
        if [[ "$HALT_TEXT" == HALT\ GLOBAL || "$HALT_TEXT" == HALT\ PROJECT ]]; then
            HALT_STATUS="[${HALT_TEXT}]"
        elif [[ "$HALT_TEXT" == HALT-AFTER\ * ]]; then
            _sr_ha_rest="${HALT_TEXT#HALT-AFTER }"
            HALT_STATUS="[HALT-AFTER:${_sr_ha_rest}]"
            unset _sr_ha_rest
        else
            HALT_STATUS="[HALT-AFTER]"
        fi
    fi
else
    HALT_STATUS="HALT:off"
fi
unset _SR_C_RED _SR_C_YELLOW _SR_C_RESET _SR_USE_COLOR

# Date/time — ISO 8601 with day-of-week prefix (e.g. Sun 2026-05-10T04:55:23)
DATETIME="$(date '+%a %Y-%m-%dT%H:%M:%S')"

# Output: version PM:mode HALT:state date
printf '%s  %s  %s  %s' "$VERSION" "$PM_STATUS" "$HALT_STATUS" "$DATETIME"
