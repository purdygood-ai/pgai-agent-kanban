#!/usr/bin/env bash
# dashboard-status-bottom.sh
# Helper for tmux status-format[1] — emits the bottom-line status string for
# the two-line tmux dashboard status bar.
#
# Usage:
#   dashboard-status-bottom.sh <KANBAN_ROOT> [<PROJECT_NAME>]
#
#   KANBAN_ROOT    Path to the live kanban install directory.
#                  Defaults to $PGAI_AGENT_KANBAN_ROOT_PATH or
#                  $HOME/pgai_agent_kanban.
#   PROJECT_NAME   Optional project name override.  When omitted the global
#                  bar renders install-global info only.
#                  When supplied (e.g. from a per-project drill window), the
#                  bar also renders <project>:<version> | workflow:<type> for
#                  that specific project.
#
# Output (single line, no trailing newline):
#
#   Global bar (PROJECT_NAME not supplied):
#     Normal:   v<version> | PM:<mode> | <day> <HH:MM>
#     Draining: v<version> | PM:<mode> | HALT-AFTER GLOBAL <event> | <day> <HH:MM>
#     Halted:   v<version> | PM:<mode> | HALT GLOBAL | <day> <HH:MM>
#
#   Per-project bar (PROJECT_NAME supplied):
#     Normal:   v<version> | <project>:<version> | workflow:<type> | PM:<mode> | <day> <HH:MM>
#     Draining: v<version> | <project>:<version> | workflow:<type> | PM:<mode> | HALT-AFTER GLOBAL <event> | <day> <HH:MM>
#     Halted:   v<version> | <project>:<version> | workflow:<type> | PM:<mode> | HALT GLOBAL | <day> <HH:MM>
#
#   <version> is the live install's ${KANBAN_ROOT}/VERSION file content (D2 fix).
#
#   (scope is GLOBAL or PROJECT depending on which sentinel is active)
#
# Halt detection delegates to scripts/dashboard/lib/halt_scope.sh (single
# decision point for halt scope).
#   Per-project: $KANBAN_ROOT/projects/<PROJECT_NAME>/HALT[AFTER]
#   Global:      $KANBAN_ROOT/HALT and $KANBAN_ROOT/HALT-AFTER
# Global takes precedence over per-project.
#
# Color mode:
#   USE_COLOR (default): HALT indicator rendered in tmux native #[fg=red] /
#     #[fg=yellow] / #[default] markup so tmux interprets the color instead of
#     displaying raw ANSI escape sequences.
#   NO_COLOR / TERM=dumb: bracketed text marker  [HALT GLOBAL] / [HALT PROJECT]
#     or [HALT-AFTER:GLOBAL <event>].
#   When no halt files exist the output is empty for the halt segment.
#
# Sources read (all optional — fallback values used if missing or unreadable):
#   $KANBAN_ROOT/VERSION                              — deployed version
#   $KANBAN_ROOT/projects/<PROJECT_NAME>/project.cfg  — workflow_type (falls back to PROJECT.cfg for legacy installs)
#   $KANBAN_ROOT/HALT, $KANBAN_ROOT/HALT-AFTER        — global halt sentinels
#   $KANBAN_ROOT/projects/<PROJECT_NAME>/HALT[AFTER]  — per-project halt sentinels
#   $KANBAN_ROOT/bashrc, env, kanban.cfg              — PGAI_KANBAN_PM_MODE env var
#
# Truncation:
#   Project names longer than 20 characters are truncated to 20 chars with
#   an appended ellipsis ("...").
#
# Exit codes:
#   0   Always. Missing sources produce fallback values; the script never fails.

set -uo pipefail

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${1:-${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}}"
PROJECT_NAME="${2:-}"
# Track whether an explicit project name was provided.  When PROJECT_NAME is
# empty here (not provided as $2), HALT detection will scan all project roots.
# When PROJECT_NAME is explicit, HALT detection is scoped to that project only.
_DSB_EXPLICIT_PROJECT="${2:+yes}"

# ---------------------------------------------------------------------------
# Source optional config files so env-file variables (e.g. PGAI_KANBAN_PM_MODE)
# are available in fresh subshells (tmux status-format[1] #() syntax).
# ---------------------------------------------------------------------------
[[ -f "$KANBAN_ROOT/bashrc" ]]                 && source "$KANBAN_ROOT/bashrc"              2>/dev/null || true
[[ -f "$KANBAN_ROOT/env" ]]                    && source "$KANBAN_ROOT/env"                 2>/dev/null || true
[[ -f "$HOME/.config/pgai-kanban.cfg" ]]       && source "$HOME/.config/pgai-kanban.cfg"   2>/dev/null || true
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# Source ini_parser.sh for read_ini (project_paths.sh not available here).
# Source project_paths.sh and projects.sh for projects_cfg_list (project name resolution).
_DSB_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_DSB_SCRIPT_DIR}/../lib/ini_parser.sh" ]]    && source "${_DSB_SCRIPT_DIR}/../lib/ini_parser.sh"
[[ -f "${_DSB_SCRIPT_DIR}/../lib/project_paths.sh" ]] && source "${_DSB_SCRIPT_DIR}/../lib/project_paths.sh"
[[ -f "${_DSB_SCRIPT_DIR}/../lib/projects.sh" ]]      && source "${_DSB_SCRIPT_DIR}/../lib/projects.sh"
# Source shared Python-helper resolver (live-install anchor first — D3 fix).
# halt_scope.sh also sources this; guard against double-sourcing via sentinel.
# shellcheck source=lib/helper_resolver.sh
source "${_DSB_SCRIPT_DIR}/lib/helper_resolver.sh"
# Source shared halt scope helper (single decision point for halt scope)
source "${_DSB_SCRIPT_DIR}/lib/halt_scope.sh"
# Source shared version helper (single tier-order decision point)
# shellcheck source=lib/version.sh
source "${_DSB_SCRIPT_DIR}/lib/version.sh"
# shellcheck source=lib/dev_tree.sh
source "${_DSB_SCRIPT_DIR}/../lib/dev_tree.sh"
if [[ -f "${KANBAN_ROOT}/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "${KANBAN_ROOT}/kanban.cfg" chain pm_mode automatic)}"
fi
unset _DSB_SCRIPT_DIR
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"

# ---------------------------------------------------------------------------
# Per-project segment resolution — only needed when an explicit PROJECT_NAME
# was supplied ($2 set).  The global bar renders install-global
# info only and does not need project/version/workflow resolution.
#
# When PROJECT_NAME was not supplied (global bar), skip all resolution below.
# When it was supplied (per-project drill caller), resolve the full set.
# ---------------------------------------------------------------------------
DISPLAY_PROJECT=""
PROJECT_VERSION=""
WORKFLOW_TYPE="unknown"
VERSION=""

# ---------------------------------------------------------------------------
# Framework version — always resolved (global bar and per-project bar both
# display the live install version).  VERSION-file-first tier order:
#   Tier 1: $KANBAN_ROOT/VERSION (install-generated canonical value — D2 fix)
#   Tier 2: $PGAI_DEV_TREE_PATH/VERSION (dev-tree fallback)
#   Tier 3: git describe / unknown (no VERSION file present)
# MUST NOT call pp_last_released_version (avoids B315/B319/B320 self-
# privileging bug class).
# ---------------------------------------------------------------------------
FRAMEWORK_VERSION="$(get_kanban_version "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "")"

if [[ -n "${_DSB_EXPLICIT_PROJECT}" ]]; then
    # Explicit project supplied — resolve project display name, version, workflow.

    # Project name (truncate to 20 chars + ellipsis if longer)
    if [[ ${#PROJECT_NAME} -gt 20 ]]; then
        DISPLAY_PROJECT="${PROJECT_NAME:0:20}..."
    else
        DISPLAY_PROJECT="$PROJECT_NAME"
    fi

    # Deployed VERSION (via shared helper)
    # Tier order: KANBAN_ROOT/VERSION > REPO_ROOT/VERSION > git tag --merged > unknown
    VERSION="$(get_kanban_version "$KANBAN_ROOT" "${PGAI_DEV_TREE_PATH:-}" "")"

    # Per-project taskbar version: the taskbar pairs DISPLAY_PROJECT with the
    # project's OWN last-released version, not the kanban framework VERSION.
    # pp_last_released_version with v0.0.0 fallback matches the resolution used
    # in show-multi.sh so the two render paths agree.
    PROJECT_VERSION="$(KANBAN_ROOT="$KANBAN_ROOT" pp_last_released_version "$PROJECT_NAME" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$PROJECT_VERSION" ]] && PROJECT_VERSION="v0.0.0"

    # Workflow type (from project.cfg; falls back to PROJECT.cfg for legacy installs)
    _proj_cfg_dir="${KANBAN_ROOT}/projects/${PROJECT_NAME}"
    PROJECT_CFG=""
    if [[ -f "${_proj_cfg_dir}/project.cfg" ]]; then
        PROJECT_CFG="${_proj_cfg_dir}/project.cfg"
    elif [[ -f "${_proj_cfg_dir}/PROJECT.cfg" ]]; then
        PROJECT_CFG="${_proj_cfg_dir}/PROJECT.cfg"
    fi
    unset _proj_cfg_dir
    if [[ -n "$PROJECT_CFG" && -f "$PROJECT_CFG" ]]; then
        _wf="$(grep -E '^[[:space:]]*workflow_type[[:space:]]*=' "$PROJECT_CFG" 2>/dev/null \
               | head -n1 \
               | sed 's/^[^=]*=[[:space:]]*//; s/^["'"'"']//; s/["'"'"']$//')"
        [[ -n "$_wf" ]] && WORKFLOW_TYPE="$_wf"
        unset _wf
    fi
fi

# ---------------------------------------------------------------------------
# PM mode (PGAI_KANBAN_PM_MODE env var — sourced from config above)
# ---------------------------------------------------------------------------
PM_RAW="${PGAI_KANBAN_PM_MODE:-automatic}"
if [[ "$PM_RAW" == "manual" ]]; then
    PM_MODE="manual"
else
    PM_MODE="auto"
fi

# ---------------------------------------------------------------------------
# Color mode detection (matches column-render.sh USE_COLOR convention).
# Honors NO_COLOR env var and TERM=dumb, both of which indicate a terminal
# that cannot render ANSI escape codes.  When color is disabled, halt
# indicators fall back to bracketed text markers ([HALT], [HALT-AFTER:token]).
# ---------------------------------------------------------------------------
_DSB_USE_COLOR=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _DSB_USE_COLOR=false
fi

# ---------------------------------------------------------------------------
# HALT state — taskbar halt text via shared halt_scope.sh helper.
#
# Single decision point for halt scope: sourced from lib/halt_scope.sh.
# dashboard_halt_scope returns state<TAB>scope<TAB>event where:
#   state — "halted" | "draining" | "normal"
#   scope — "GLOBAL" | <project_name> | ""
#   event — halt event token or ""
#
# Result stored in HALT_TEXT (plain text, scope-aware):
#   ""                          — normal (no sentinel)
#   "HALT GLOBAL"               — global halt sentinel
#   "HALT PROJECT"              — per-project halt sentinel
#   "HALT-AFTER GLOBAL <event>" — global draining sentinel
#   "HALT-AFTER PROJECT <event>"— per-project draining sentinel
#
# HALT_DISPLAY is the rendered form:
#   USE_COLOR:   tmux #[fg=red]/#[fg=yellow]/#[default] markup
#   NO_COLOR:    bracketed text marker  [HALT GLOBAL] / [HALT PROJECT] etc.
# ---------------------------------------------------------------------------
HALT_TEXT=""
_dsb_hs_scope=""
_dsb_hs_state=""
_dsb_hs_event=""

# Save whether an explicit project was supplied ($2) before the halt-scope
# call clears _DSB_EXPLICIT_PROJECT (used to gate the project segment).
_DSB_HAS_EXPLICIT_PROJECT="${_DSB_EXPLICIT_PROJECT}"

IFS=$'\t' read -r _dsb_hs_state _dsb_hs_scope _dsb_hs_event \
    < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "${_DSB_EXPLICIT_PROJECT:+$PROJECT_NAME}")

# Determine the display scope word: GLOBAL stays GLOBAL; any project name → PROJECT
# (status-bottom.sh collapses per-project names to the word PROJECT for the taskbar;
# the per-project status column shows which project is halted).
_dsb_hs_scope_word=""
if [[ "$_dsb_hs_scope" == "GLOBAL" ]]; then
    _dsb_hs_scope_word="GLOBAL"
elif [[ -n "$_dsb_hs_scope" ]]; then
    _dsb_hs_scope_word="PROJECT"
fi

if [[ "$_dsb_hs_state" == "halted" && -n "$_dsb_hs_scope_word" ]]; then
    HALT_TEXT="HALT ${_dsb_hs_scope_word}"
elif [[ "$_dsb_hs_state" == "draining" && -n "$_dsb_hs_scope_word" ]]; then
    if [[ -n "$_dsb_hs_event" ]]; then
        HALT_TEXT="HALT-AFTER ${_dsb_hs_scope_word} ${_dsb_hs_event}"
    else
        HALT_TEXT="HALT-AFTER ${_dsb_hs_scope_word}"
    fi
fi
unset _dsb_hs_state _dsb_hs_scope _dsb_hs_scope_word _dsb_hs_event _DSB_EXPLICIT_PROJECT
# Note: _DSB_HAS_EXPLICIT_PROJECT is retained here — used in the printf block below.

# Build HALT_DISPLAY: tmux-native markup or bracketed text depending on color mode.
# When HALT_TEXT is empty (normal state), HALT_DISPLAY is also empty and the
# halt segment is omitted from the status bar.
#
# USE_COLOR path uses tmux #[fg=...] / #[default] markup so that tmux renders
# the color rather than displaying raw ANSI escape bytes.
# Matches the #[fg=...] pattern already used by status-right.sh for PM_STATUS.
#
# HALT_TEXT forms:
#   "HALT GLOBAL"                — global halt (red)
#   "HALT PROJECT"               — per-project halt (red)
#   "HALT-AFTER GLOBAL <event>"  — global draining (yellow)
#   "HALT-AFTER PROJECT <event>" — per-project draining (yellow)
HALT_DISPLAY=""
if [[ -n "$HALT_TEXT" ]]; then
    if [[ "$_DSB_USE_COLOR" == "true" ]]; then
        if [[ "$HALT_TEXT" == HALT\ GLOBAL || "$HALT_TEXT" == HALT\ PROJECT ]]; then
            # halted — red, matching column-render's HALT color palette
            HALT_DISPLAY="#[fg=red]${HALT_TEXT}#[default]"
        else
            # draining: "HALT-AFTER GLOBAL/PROJECT <event>" — yellow
            HALT_DISPLAY="#[fg=yellow]${HALT_TEXT}#[default]"
        fi
    else
        # NO_COLOR / dumb terminal: bracketed text marker.
        # "HALT GLOBAL"               → [HALT GLOBAL]
        # "HALT PROJECT"              → [HALT PROJECT]
        # "HALT-AFTER GLOBAL <event>" → [HALT-AFTER:GLOBAL <event>]
        # "HALT-AFTER PROJECT <event>"→ [HALT-AFTER:PROJECT <event>]
        if [[ "$HALT_TEXT" == HALT\ GLOBAL || "$HALT_TEXT" == HALT\ PROJECT ]]; then
            HALT_DISPLAY="[${HALT_TEXT}]"
        elif [[ "$HALT_TEXT" == HALT-AFTER\ * ]]; then
            _dsb_ha_rest="${HALT_TEXT#HALT-AFTER }"
            HALT_DISPLAY="[HALT-AFTER:${_dsb_ha_rest}]"
            unset _dsb_ha_rest
        else
            HALT_DISPLAY="[HALT-AFTER]"
        fi
    fi
fi
unset _DSB_USE_COLOR

# ---------------------------------------------------------------------------
# Day + time — ISO 8601 with day-of-week prefix (e.g. Sun 2026-05-10T04:55:23)
# ---------------------------------------------------------------------------
DATETIME="$(date '+%a %Y-%m-%dT%H:%M:%S')"

# ---------------------------------------------------------------------------
# Emit single formatted line — no trailing newline.
#
# The global bottom bar (PROJECT_NAME not explicitly supplied)
# renders install-global info only: framework version, PM mode, HALT/HALT-AFTER,
# and date+time.  The per-project <project>:<version> | workflow:<type> segment
# is suppressed in the global bar to avoid showing one arbitrary project's data.
#
# D2 fix: the live framework version (v<FRAMEWORK_VERSION>) is prepended to
# BOTH the global bar and the per-project bar.  FRAMEWORK_VERSION is resolved
# via get_kanban_version (VERSION-file-first — see above).
#
# When PROJECT_NAME was explicitly supplied (per-project drill window caller),
# the project segment is included — preserving B319/B320 behavior.
#
# The HALT segment is omitted entirely when HALT_TEXT is empty (normal state).
# ---------------------------------------------------------------------------
if [[ -n "$_DSB_HAS_EXPLICIT_PROJECT" ]]; then
    # Per-project drill bar: include <version> | <project>:<version> | workflow:<type>
    if [[ -n "$HALT_TEXT" ]]; then
        printf '%s | %s:%s | workflow:%s | PM:%s | %s | %s' \
            "$FRAMEWORK_VERSION" \
            "$DISPLAY_PROJECT" \
            "$PROJECT_VERSION" \
            "$WORKFLOW_TYPE" \
            "$PM_MODE" \
            "$HALT_DISPLAY" \
            "$DATETIME"
    else
        printf '%s | %s:%s | workflow:%s | PM:%s | %s' \
            "$FRAMEWORK_VERSION" \
            "$DISPLAY_PROJECT" \
            "$PROJECT_VERSION" \
            "$WORKFLOW_TYPE" \
            "$PM_MODE" \
            "$DATETIME"
    fi
else
    # Global bar: install-global info only — no per-project segment.
    # Prepends live framework version (VERSION-file-first via get_kanban_version).
    if [[ -n "$HALT_TEXT" ]]; then
        printf '%s | PM:%s | %s | %s' \
            "$FRAMEWORK_VERSION" \
            "$PM_MODE" \
            "$HALT_DISPLAY" \
            "$DATETIME"
    else
        printf '%s | PM:%s | %s' \
            "$FRAMEWORK_VERSION" \
            "$PM_MODE" \
            "$DATETIME"
    fi
fi
unset _DSB_HAS_EXPLICIT_PROJECT
