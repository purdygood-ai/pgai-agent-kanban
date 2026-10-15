#!/usr/bin/env bash
# status-right.sh
# Helper for tmux status-right — evaluated per refresh via #() syntax.
# Usage: status-right.sh <KANBAN_ROOT>
#        status-right.sh -h|--help   (print this usage and exit 0)
#
# Color mode:
#   USE_COLOR (default): segments rendered with ANSI colors and leading glyphs.
#   NO_COLOR / TERM=dumb: plain text markers, no escape codes, no glyphs.
#     NO_COLOR / dumb output is byte-identical to the pre-glyph baseline.
#
# Halt detection delegates to scripts/dashboard/lib/halt_scope.sh
# (dashboard_halt_scope — single decision point for halt scope). That
# function is a FULL ENUMERATION, not first-wins: it emits one row per
# ACTIVE scope — the global kanban-root sentinel AND every project with
# its own active sentinel, simultaneously. This script renders one segment
# per active row, in the function's guaranteed emission order (GLOBAL
# first, then projects alphabetically). A global HALT and a project's own
# HALT-AFTER are not mutually exclusive here: both render, each naming its
# own scope (the SOP vocabulary exactly — "GLOBAL" or the literal project
# name, never a generic placeholder). A draining row's event token is
# rendered through dashboard_halt_describe_event (lib/halt_scope.sh) — the
# one shared "until vX.Y.Z" describer for a version-pinned token.
#   Global:      $KANBAN_ROOT/HALT and $KANBAN_ROOT/HALT-AFTER
#   Per-project: $KANBAN_ROOT/projects/<name>/HALT and HALT-AFTER (every
#                registered project, not just one)
#
# Approval detection: scans $KANBAN_ROOT/projects/*/tasks/HUMAN-APPROVE-*/status.md
#   for tasks whose State field is WAITING or BACKLOG (pending approval gate).
#   Emits '✋ APPROVAL(n)' when n>=1, omitted when n=0.
#
# Output: <version>  <PM:mode>  <HALT:state>...  [<APPROVAL:flag>]  [<DEFER:flag>]  <day> <ISO-time>
#   Rich mode (color-capable terminal):
#     Normal:   📝 <ver>  🟢 PM:auto  HALT:off  📅 <day> <time>
#     Halted:   📝 <ver>  🟢 PM:auto  <red>🛑 HALT GLOBAL<reset>  📅 <day> <time>
#     Draining: 📝 <ver>  🟢 PM:auto  <yellow>⚠️ HALT-AFTER GLOBAL until v2.9.15<reset>  📅 <day> <time>
#     Pending:  📝 <ver>  🟢 PM:auto  HALT:off  <yellow>✋ APPROVAL(n)<reset>  📅 <day> <time>
#     Simultaneous: 📝 <ver>  🟢 PM:auto  <red>🛑 HALT GLOBAL<reset>  <yellow>⚠️ HALT-AFTER <proj> until v2.9.15<reset>  ✋ DEFER GLOBAL <=low (n waiting)  📅 <day> <time>
#   NO_COLOR / TERM=dumb (byte-identical to pre-glyph baseline):
#     Normal:   <ver>  PM:auto  HALT:off  <day> <time>
#     Halted:   <ver>  PM:auto  [HALT GLOBAL]  <day> <time>
#     Draining: <ver>  PM:auto  [HALT-AFTER:GLOBAL until v2.9.15]  <day> <time>
#     Pending:  <ver>  PM:auto  HALT:off  [APPROVAL(n)]  <day> <time>
#   Every HALT/HALT-AFTER/APPROVAL/DEFER segment is independently optional
#   and may appear in ANY combination simultaneously. Segment order (fixed):
#   version, PM mode, every active HALT/HALT-AFTER segment (GLOBAL first,
#   then projects alphabetically), APPROVAL, DEFER, timestamp last. The
#   NO_COLOR form carries exactly the same set of segments as the rich
#   form — color mode never drops a segment the rich form would have shown.
#
# Glyph literals live exclusively in team/scripts/lib/status_glyphs.sh (sourced below).

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
# Source shared deferral-posture scope helper (single decision point for the
# DEFER segments — resolves through team/scripts/lib/defer_posture.sh,
# never re-derives its precedence here)
source "${_SCRIPT_DIR}/lib/defer_scope.sh"
# shellcheck source=lib/dev_tree.sh
source "${_SCRIPT_DIR}/../lib/dev_tree.sh"
# Source glyph map (single home for all status-bar glyph literals)
# shellcheck source=../lib/status_glyphs.sh
source "${_SCRIPT_DIR}/../lib/status_glyphs.sh"
# Source shared semantic dashboard palette reader (single home for the
# halt/approval/defer color meanings below).
# shellcheck source=../lib/dashboard_palette.sh
source "${_SCRIPT_DIR}/../lib/dashboard_palette.sh"
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

# Semantic color codes — resolved from the single-source palette
# (team/config/default_palette.txt) via dashboard_semantic_color(), which
# reads the caller's USE_COLOR global exactly like ansi_code() does.
USE_COLOR="$_SR_USE_COLOR"
_SR_C_HALT_ACTIVE="$(dashboard_semantic_color halt_active)"
_SR_C_HALT_DRAINING="$(dashboard_semantic_color halt_draining)"
_SR_C_APPROVAL="$(dashboard_semantic_color approval_pending)"
_SR_C_DEFER_WARN="$(dashboard_semantic_color defer_warning)"
_SR_C_RESET="$(ansi_code reset)"

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
# HALT / HALT-AFTER detection via shared halt_scope.sh helper.
#
# FULL ENUMERATION: dashboard_halt_scope prints one line per ACTIVE scope
# (GLOBAL and/or any number of projects) rather than the first one found —
# see lib/halt_scope.sh's header for the full contract. Every active line
# becomes its own entry in _SR_HALT_DISPLAYS, built in the function's
# guaranteed emission order (GLOBAL first, then projects alphabetically).
#
# Scope word rendered is the SOP vocabulary exactly ("GLOBAL" or the
# literal project name) — never a generic placeholder. A draining row's
# event token is rendered through dashboard_halt_describe_event (the one
# shared "until vX.Y.Z" describer) rather than the raw token.
#
# _SR_HALT_DISPLAYS collects one rendered segment per active row:
#   0 active rows  — array stays empty; the final assembly renders the
#                    literal "HALT:off" segment (unchanged normal-state text)
#   1+ active rows — every row's own segment is spliced into the final
#                    assembly as its own protected entry, alongside
#                    APPROVAL/DEFER
# ---------------------------------------------------------------------------
_SR_HALT_DISPLAYS=()

while IFS=$'\t' read -r _sr_hs_state _sr_hs_scope _sr_hs_event; do
    [[ "$_sr_hs_state" == "normal" ]] && continue
    [[ -z "$_sr_hs_scope" ]] && continue

    _sr_hs_text=""
    if [[ "$_sr_hs_state" == "halted" ]]; then
        _sr_hs_text="HALT ${_sr_hs_scope}"
    else
        _sr_hs_described=""
        if [[ -n "$_sr_hs_event" ]]; then
            _sr_hs_described="$(dashboard_halt_describe_event "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "$_sr_hs_event")"
        fi
        if [[ -n "$_sr_hs_described" ]]; then
            _sr_hs_text="HALT-AFTER ${_sr_hs_scope} ${_sr_hs_described}"
        else
            _sr_hs_text="HALT-AFTER ${_sr_hs_scope}"
        fi
    fi

    if [[ "$_SR_USE_COLOR" == "true" ]]; then
        if [[ "$_sr_hs_state" == "halted" ]]; then
            _SR_HALT_DISPLAYS+=("${_SR_C_HALT_ACTIVE}${GLYPH_HALT} ${_sr_hs_text}${_SR_C_RESET}")
        else
            _SR_HALT_DISPLAYS+=("${_SR_C_HALT_DRAINING}${GLYPH_HALT_AFTER} ${_sr_hs_text}${_SR_C_RESET}")
        fi
    else
        # NO_COLOR / dumb terminal: bracketed text marker, one per active scope.
        if [[ "$_sr_hs_state" == "halted" ]]; then
            _SR_HALT_DISPLAYS+=("[${_sr_hs_text}]")
        else
            _sr_ha_rest="${_sr_hs_text#HALT-AFTER }"
            _SR_HALT_DISPLAYS+=("[HALT-AFTER:${_sr_ha_rest}]")
            unset _sr_ha_rest
        fi
    fi
done < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "")
unset _sr_hs_state _sr_hs_scope _sr_hs_event _sr_hs_text _sr_hs_described

# _SR_HALT_DISPLAYS stays a plain array here (one entry per active scope) so
# the final assembly below can splice each entry in as its own protected
# segment. When nothing is active, the array is empty and the assembly
# falls back to the literal "HALT:off" segment (unchanged normal-state text).

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
        APPROVAL_STATUS="${_SR_C_APPROVAL}${GLYPH_APPROVAL} APPROVAL(${_sr_approval_count})${_SR_C_RESET}"
    else
        # NO_COLOR / dumb: bracketed text — byte-identical to pre-RC (no glyph).
        APPROVAL_STATUS="[APPROVAL(${_sr_approval_count})]"
    fi
fi
unset _sr_approval_count _sr_sf _sr_state

# ---------------------------------------------------------------------------
# DEFER — status-bar segments for the deferral posture in force.
# Resolves via dashboard/lib/defer_scope.sh (single-source; never re-derives
# defer_posture.sh's tier precedence or discovery_defer.sh's severity
# decision here). status-right.sh is a global bar, the same as
# status-bottom.sh's global bar, and shows the same segments:
# dashboard_defer_segments reports one per deferral scope in force — GLOBAL
# first (a kanban.cfg or root-campaign deferral, counting every project that
# inherits it), then each project whose own settings defer its bugs,
# alphabetically — shown whether or not any bug is waiting.
#
# Each _SR_DEFER_SEGMENTS entry is one of:
#   "<glyph> DEFER <scope> <=level[ until vX.Y.Z] (n waiting)" — rich mode
#   "DEFER <scope> <=level[ until vX.Y.Z] (n waiting)"          — NO_COLOR/dumb;
#       byte-identical to the rich text minus its "<glyph> " prefix
#   "<glyph> DEFER CONFIG UNREADABLE: <path>" (rich) /
#   "DEFER CONFIG UNREADABLE: <path>" (NO_COLOR)      — resolve_defer_posture
#       refused; never the deferral glyph, never a silently omitted segment
# ---------------------------------------------------------------------------
_SR_DEFER_SEGMENTS=()
while IFS="$DFS_SEP" read -r _sr_defer_mode _sr_defer_text; do
    [[ -z "$_sr_defer_text" ]] && continue
    if [[ "$_sr_defer_mode" == "malformed" ]]; then
        if [[ "$_SR_USE_COLOR" == "true" ]]; then
            _SR_DEFER_SEGMENTS+=("${_SR_C_DEFER_WARN}${GLYPH_DEFER_WARNING} ${_sr_defer_text}${_SR_C_RESET}")
        else
            _SR_DEFER_SEGMENTS+=("$_sr_defer_text")
        fi
    elif [[ "$_SR_USE_COLOR" == "true" ]]; then
        _SR_DEFER_SEGMENTS+=("${GLYPH_DEFERRED_BUG} ${_sr_defer_text}")
    else
        _SR_DEFER_SEGMENTS+=("$_sr_defer_text")
    fi
done < <(dashboard_defer_segments "$KANBAN_ROOT")
unset _sr_defer_mode _sr_defer_text

unset _SR_C_HALT_ACTIVE _SR_C_HALT_DRAINING _SR_C_APPROVAL _SR_C_DEFER_WARN _SR_C_RESET _SR_USE_COLOR USE_COLOR

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

# Assemble the fixed segments plus every independently-optional one (each
# active HALT/HALT-AFTER row, APPROVAL_STATUS, every DEFER segment) via an array
# join rather than one printf branch per presence combination. Segment
# order (fixed, deterministic): version, PM mode, every active
# HALT/HALT-AFTER segment (GLOBAL first, then projects alphabetically),
# APPROVAL, DEFER, timestamp last.
#
# Width budget: PGAI_DASHBOARD_STATUS_MAX_WIDTH (default 320 characters)
# bounds the assembled line. When exceeded, DROPPABLE (informational)
# segments are removed one at a time from the RIGHTMOST droppable segment
# inward, re-measuring after each removal, until the line fits or none
# remain. PROTECTED segments — every HALT/HALT-AFTER row, APPROVAL, and
# DEFER — are never dropped (mirrors status-bottom.sh's identical rule).
_SR_SEGMENTS=()
_SR_SEGMENT_PROTECTED=()

_sr_push_segment() {
    # $1 = segment text, $2 = "1" (protected/flag) or "0" (droppable/info)
    [[ -z "$1" ]] && return 0
    _SR_SEGMENTS+=("$1")
    _SR_SEGMENT_PROTECTED+=("$2")
}

_sr_push_segment "$VERSION_DISPLAY" "0"
_sr_push_segment "$PM_STATUS" "0"
if [[ "${#_SR_HALT_DISPLAYS[@]}" -eq 0 ]]; then
    _sr_push_segment "HALT:off" "0"
else
    for _sr_hd in "${_SR_HALT_DISPLAYS[@]}"; do
        _sr_push_segment "$_sr_hd" "1"
    done
    unset _sr_hd
fi
_sr_push_segment "$APPROVAL_STATUS" "1"
for _sr_ds in ${_SR_DEFER_SEGMENTS[@]+"${_SR_DEFER_SEGMENTS[@]}"}; do
    _sr_push_segment "$_sr_ds" "1"
done
unset _sr_ds _SR_DEFER_SEGMENTS
_sr_push_segment "$_SR_DATETIME_DISPLAY" "0"

_sr_assemble() {
    local out="" i
    for i in "${!_SR_SEGMENTS[@]}"; do
        [[ -z "${_SR_SEGMENTS[$i]}" ]] && continue
        if [[ -z "$out" ]]; then
            out="${_SR_SEGMENTS[$i]}"
        else
            out="${out}  ${_SR_SEGMENTS[$i]}"
        fi
    done
    printf '%s' "$out"
}

_SR_MAX_WIDTH="${PGAI_DASHBOARD_STATUS_MAX_WIDTH:-320}"
_SR_OUT="$(_sr_assemble)"
while [[ "${#_SR_OUT}" -gt "$_SR_MAX_WIDTH" ]]; do
    _sr_dropped=0
    for (( _sr_i=${#_SR_SEGMENTS[@]}-1; _sr_i>=0; _sr_i-- )); do
        if [[ "${_SR_SEGMENT_PROTECTED[$_sr_i]}" == "0" && -n "${_SR_SEGMENTS[$_sr_i]}" ]]; then
            _SR_SEGMENTS[$_sr_i]=""
            _sr_dropped=1
            break
        fi
    done
    [[ "$_sr_dropped" -eq 0 ]] && break
    _SR_OUT="$(_sr_assemble)"
done
unset _sr_dropped _sr_i _SR_MAX_WIDTH

printf '%s' "$_SR_OUT"
unset _SR_DATETIME_DISPLAY _SR_SEGMENTS _SR_SEGMENT_PROTECTED _SR_HALT_DISPLAYS _SR_OUT
