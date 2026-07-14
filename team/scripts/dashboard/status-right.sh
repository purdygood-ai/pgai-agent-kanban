#!/usr/bin/env bash
# dashboard-status-right.sh
# Helper for tmux status-right — evaluated per refresh via #() syntax.
# Usage: dashboard-status-right.sh <KANBAN_ROOT>
#
# Color mode:
#   USE_COLOR (default): segments rendered with ANSI colors and leading glyphs.
#   NO_COLOR / TERM=dumb: plain text markers, no escape codes, no glyphs.
#     NO_COLOR / dumb output is byte-identical to the pre-glyph baseline.
#
# Halt detection delegates to scripts/dashboard/lib/halt_scope.sh (single
# decision point for halt scope).
#   Global:      $KANBAN_ROOT/HALT and $KANBAN_ROOT/HALT-AFTER
#   Per-project: $KANBAN_ROOT/projects/<name>/HALT and HALT-AFTER
#
# Approval detection: scans $KANBAN_ROOT/projects/*/tasks/HUMAN-APPROVE-*/status.md
#   for tasks whose State field is WAITING or BACKLOG (pending approval gate).
#   Emits '✋ APPROVAL(n)' when n>=1, omitted when n=0.
#
# Output: <version>  <PM:mode>  <HALT:state>  [<APPROVAL:flag>]  <day> <ISO-time>
#   Rich mode (color-capable terminal):
#     Normal:   📝 <ver>  🟢 PM:auto  HALT:off  📅 <day> <time>
#     Halted:   📝 <ver>  🟢 PM:auto  <red>🛑 HALT GLOBAL<reset>  📅 <day> <time>
#     Draining: 📝 <ver>  🟢 PM:auto  <yellow>⚠️ HALT-AFTER GLOBAL <token><reset>  📅 <day> <time>
#     Pending:  📝 <ver>  🟢 PM:auto  HALT:off  <yellow>✋ APPROVAL(n)<reset>  📅 <day> <time>
#   NO_COLOR / TERM=dumb (byte-identical to pre-glyph baseline):
#     Normal:   <ver>  PM:auto  HALT:off  <day> <time>
#     Halted:   <ver>  PM:auto  [HALT GLOBAL]  <day> <time>
#     Draining: <ver>  PM:auto  [HALT-AFTER:GLOBAL <token>]  <day> <time>
#     Pending:  <ver>  PM:auto  HALT:off  [APPROVAL(n)]  <day> <time>
#
# Glyph literals live exclusively in team/scripts/lib/status_glyphs.sh (sourced below).

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

KANBAN_ROOT="${1:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"

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
# Source glyph map (single home for all status-bar glyph literals)
# shellcheck source=../lib/status_glyphs.sh
source "${_SCRIPT_DIR}/../lib/status_glyphs.sh"
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

# ---------------------------------------------------------------------------
# Color mode detection — single check, used throughout.
# Honors NO_COLOR env var and TERM=dumb; both indicate a terminal that cannot
# render ANSI escape codes.  When color is disabled, no glyphs are emitted and
# halt / approval indicators fall back to bracketed text markers.
# ---------------------------------------------------------------------------
_SR_USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _SR_USE_COLOR=false
fi

# ANSI color codes — red for HALT, yellow for HALT-AFTER/draining and APPROVAL.
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
# Version — raw value read from VERSION file.
# ---------------------------------------------------------------------------
VERSION_FILE="${KANBAN_ROOT}/VERSION"
if [[ -f "$VERSION_FILE" ]]; then
    VERSION="$(tr -d '[:space:]' < "$VERSION_FILE")"
else
    VERSION="v?"
fi

# Version display — glyph prefix in rich mode; plain text in NO_COLOR / dumb
# (byte-identical to pre-RC baseline).
if [[ "$_SR_USE_COLOR" == "true" ]]; then
    VERSION_DISPLAY="${GLYPH_VERSION} ${VERSION}"
else
    VERSION_DISPLAY="${VERSION}"
fi

# ---------------------------------------------------------------------------
# PM mode — glyph prefix in rich mode; plain text in NO_COLOR / dumb.
# ---------------------------------------------------------------------------
PM_RAW="${PGAI_KANBAN_PM_MODE:-automatic}"
if [[ "$PM_RAW" == "manual" ]]; then
    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        PM_STATUS="${GLYPH_PM_MANUAL} PM:manual"
    else
        PM_STATUS="PM:manual"
    fi
else
    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        PM_STATUS="${GLYPH_PM_AUTO} PM:auto"
    else
        PM_STATUS="PM:auto"
    fi
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
#   "HALT GLOBAL"                — global halt (red, 🛑 glyph in rich mode)
#   "HALT PROJECT"               — per-project halt (red, 🛑 glyph in rich mode)
#   "HALT-AFTER GLOBAL <event>"  — global draining (yellow, ⚠️ glyph in rich mode)
#   "HALT-AFTER PROJECT <event>" — per-project draining (yellow, ⚠️ glyph)
#
# Rich mode:    HALT GLOBAL/PROJECT → red + 🛑 glyph; HALT-AFTER → yellow + ⚠️ glyph
# NO_COLOR:     HALT GLOBAL → "[HALT GLOBAL]"; HALT-AFTER → "[HALT-AFTER:...]"
# Normal:       "HALT:off"
# ---------------------------------------------------------------------------
if [[ -n "$HALT_TEXT" ]]; then
    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        if [[ "$HALT_TEXT" == HALT\ GLOBAL || "$HALT_TEXT" == HALT\ PROJECT ]]; then
            HALT_STATUS="${_SR_C_RED}${GLYPH_HALT} ${HALT_TEXT}${_SR_C_RESET}"
        else
            # Draining: "HALT-AFTER GLOBAL/PROJECT <event>" — yellow with draining glyph
            HALT_STATUS="${_SR_C_YELLOW}${GLYPH_HALT_AFTER} ${HALT_TEXT}${_SR_C_RESET}"
        fi
    else
        # NO_COLOR / dumb terminal: bracketed text marker — byte-identical to pre-RC.
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

# ---------------------------------------------------------------------------
# APPROVAL state — count pending HUMAN-APPROVE tasks (WAITING or BACKLOG)
# across all registered projects.  A cross-project glob over the standard
# layout (projects/<name>/tasks/HUMAN-APPROVE-*/status.md) avoids the need
# to enumerate the projects registry from this script.
#
# State field is read with awk: the first non-blank line after "## State".
# WAITING and BACKLOG both indicate the gate is pending operator action.
# DONE, WONT-DO, WORKING, or BLOCKED mean the gate has been acted on or is
# not waiting for human input; they do not contribute to the count.
#
# APPROVAL_STATUS is set to one of:
#   ""                       — no pending approvals (n=0; indicator omitted)
#   "✋ APPROVAL(n)"          — n pending approvals (color-wrapped in USE_COLOR)
#   "[APPROVAL(n)]"          — n pending approvals (no-color / dumb terminal)
# ---------------------------------------------------------------------------
APPROVAL_STATUS=""
_sr_approval_count=0

for _sr_sf in "${KANBAN_ROOT}/projects"/*/tasks/HUMAN-APPROVE-*/status.md; do
    [[ -f "$_sr_sf" ]] || continue
    _sr_state="$(awk '
        /^## State[[:space:]]*$/ { found=1; next }
        found && /^## / { exit }
        found && /[^[:space:]]/ { print; exit }
    ' "$_sr_sf" | tr -d '[:space:]' | tr '[:lower:]' '[:upper:]')"
    if [[ "$_sr_state" == "WAITING" || "$_sr_state" == "BACKLOG" ]]; then
        _sr_approval_count=$(( _sr_approval_count + 1 ))
    fi
done

if [[ "$_sr_approval_count" -ge 1 ]]; then
    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        APPROVAL_STATUS="${_SR_C_YELLOW}${GLYPH_APPROVAL} APPROVAL(${_sr_approval_count})${_SR_C_RESET}"
    else
        # NO_COLOR / dumb: bracketed text — byte-identical to pre-RC (no glyph).
        APPROVAL_STATUS="[APPROVAL(${_sr_approval_count})]"
    fi
fi
unset _sr_approval_count _sr_sf _sr_state
unset _SR_C_RED _SR_C_YELLOW _SR_C_RESET _SR_USE_COLOR

# ---------------------------------------------------------------------------
# Date/time — ISO 8601 with day-of-week prefix (e.g. Sun 2026-05-10T04:55:23)
# Glyph prefix for timestamp is embedded in the output below when in rich mode
# via VERSION_DISPLAY (already a glyph-prefixed string).  The timestamp itself
# uses a dedicated glyph set from the lib.
# ---------------------------------------------------------------------------
DATETIME="$(date '+%a %Y-%m-%dT%H:%M:%S')"

# ---------------------------------------------------------------------------
# Output: version  PM:mode  HALT:state  [APPROVAL:flag]  date
# APPROVAL flag is omitted (no segment) when no approvals are pending.
# VERSION_DISPLAY and PM_STATUS already carry glyph prefixes (rich mode) or
# plain text (NO_COLOR / dumb).  DATETIME uses GLYPH_TIMESTAMP prefix when
# _SR_USE_COLOR was true (now unset; use VERSION_DISPLAY as proxy: if it
# contains a space-prefixed emoji, color was on — but simpler to re-check).
# ---------------------------------------------------------------------------
_sr_final_use_color=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _sr_final_use_color=false
fi
if [[ "$_sr_final_use_color" == "true" ]]; then
    _SR_DATETIME_DISPLAY="${GLYPH_TIMESTAMP} ${DATETIME}"
else
    _SR_DATETIME_DISPLAY="${DATETIME}"
fi
unset _sr_final_use_color

if [[ -n "$APPROVAL_STATUS" ]]; then
    printf '%s  %s  %s  %s  %s' "$VERSION_DISPLAY" "$PM_STATUS" "$HALT_STATUS" "$APPROVAL_STATUS" "$_SR_DATETIME_DISPLAY"
else
    printf '%s  %s  %s  %s' "$VERSION_DISPLAY" "$PM_STATUS" "$HALT_STATUS" "$_SR_DATETIME_DISPLAY"
fi
unset _SR_DATETIME_DISPLAY
