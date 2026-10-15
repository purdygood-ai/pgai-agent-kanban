#!/usr/bin/env bash
# status-bottom.sh
# Renderer for the dashboard's bottom status line — emits the bottom-line
# status string. create.sh does not run it from a tmux #() job: one
# background loop (status-bottom-loop.sh) runs it on the refresh cadence
# while a client is attached and stores the line in the session's
# @pgai_status_bottom option, which status-format[2] reads with no fork.
#
# Usage:
#   status-bottom.sh <KANBAN_ROOT> [<PROJECT_NAME>]
#   status-bottom.sh -h|--help   (print this usage and exit 0)
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
#     Normal:   📝 v<version> | 🟢 PM:auto | 📅 <day> <HH:MM>        (rich mode)
#               v<version> | PM:auto | <day> <HH:MM>                   (NO_COLOR/dumb)
#     Draining: 📝 v<version> | 🟢 PM:auto | ⚠️ HALT-AFTER GLOBAL <event> | 📅 <day> <HH:MM>
#     Halted:   📝 v<version> | 🟢 PM:auto | 🛑 HALT GLOBAL | 📅 <day> <HH:MM>
#     Pending:  📝 v<version> | 🟢 PM:auto | ✋ APPROVAL(n) | 📅 <day> <HH:MM>
#     Deferring: 📝 v<version> | 🟢 PM:auto | ✋ DEFER <proj> <=low until v2.33.0 (2 waiting) | 📅 <day> <HH:MM>
#               one DEFER segment per deferral scope in force — GLOBAL for a
#               kanban.cfg or root-campaign deferral, then each project whose
#               own settings defer its bugs — shown even at (0 waiting)
#     Simultaneous (a global HALT, a project's own HALT-AFTER, and a
#     project's own deferral all in force at once — every flag renders,
#     none suppresses another):
#               📝 v<version> | 🟢 PM:auto | 🛑 HALT GLOBAL | ⚠️ HALT-AFTER <proj> until v2.9.15 | ✋ DEFER <proj> <=low (3 waiting) | 📅 <day> <HH:MM>
#
#   Per-project bar (PROJECT_NAME supplied):
#     Normal:   📝 v<ver> | 📝 <proj>:<ver> | workflow:<type> | 🟢 PM:auto | 📅 <day> <HH:MM>
#     (NO_COLOR/dumb forms are unchanged from pre-glyph baseline)
#
#   Glyphs are rendered in rich mode only; NO_COLOR / TERM=dumb output is
#   byte-identical to the pre-glyph baseline.  Glyph literals live exclusively
#   in team/scripts/lib/status_glyphs.sh (sourced below).
#
#   <version> is the live install's ${KANBAN_ROOT}/VERSION file content (D2 fix).
#
# HALT / HALT-AFTER — FULL ENUMERATION, not first-wins.
#   Every currently-active halt scope — the global kanban-root sentinel AND
#   every project with its own active sentinel — is reported by the one
#   pgai_agent_kanban.dashboard.status_line call below, which reads each
#   scope with the same compute_halt_state the shared halt-scope helper
#   (lib/halt_scope.sh) uses.  This script renders ONE segment per active
#   scope, in the order it passes the scopes: GLOBAL first, then projects
#   in the order the projects/*/ glob lists them (alphabetical).  A global HALT and a project's own HALT-AFTER
#   are not mutually exclusive on the board: both render, each naming its
#   own scope.  The scope word is the SOP vocabulary exactly — "GLOBAL" for
#   the kanban-root sentinel, or the literal project name for a per-project
#   sentinel — never a generic placeholder.
#   A draining segment's event token is put into words by describe_token
#   (pgai_agent_kanban/halt_after/token.py) — the one shared "until vX.Y.Z"
#   describer for a version-pinned HALT-AFTER token; every other token
#   renders unchanged.
#   Segment order (fixed, deterministic across repeated renders of the same
#   state): info segments (version/project/workflow/PM), then every active
#   HALT/HALT-AFTER segment (GLOBAL first, then projects alphabetically),
#   then APPROVAL, then DEFER (global bar; GLOBAL first, then projects
#   alphabetically) or BUGS DEFERRED (drill bar), then the timestamp last.
#
# Color mode:
#   USE_COLOR (default): each HALT/HALT-AFTER segment rendered in tmux
#     native #[fg=red] / #[fg=yellow] / #[default] markup so tmux interprets
#     the color instead of displaying raw ANSI escape sequences.  APPROVAL
#     indicator rendered in tmux native #[fg=yellow] markup.
#   NO_COLOR / TERM=dumb: bracketed text marker per segment —
#     [HALT GLOBAL] / [HALT <project>] or [HALT-AFTER:GLOBAL <event>] /
#     [HALT-AFTER:<project> <event>]; APPROVAL marker as [APPROVAL(n)].
#     The NO_COLOR form carries exactly the same set of segments as the
#     rich form — color mode never drops a segment the rich form would
#     have shown.
#   When no halt files exist anywhere, no halt segment is rendered at all.
#   When no pending approvals exist the APPROVAL segment is omitted.
#
# Width budget / truncation rule (PGAI_DASHBOARD_STATUS_MAX_WIDTH, default
# 320 characters): if the assembled line would exceed the budget, DROPPABLE
# (informational) segments are removed one at a time, starting from the
# RIGHTMOST droppable segment and working left, re-measuring after each
# removal, until the line fits or no droppable segment remains.  PROTECTED
# segments — every HALT/HALT-AFTER row, APPROVAL, and DEFER/BUGS DEFERRED — are
# NEVER removed by this rule: an active flag is never hidden to make room
# for the clock or the version string.  See _dsb_push_segment below for how
# each segment is marked protected (flag) vs. droppable (informational).
#
# Approval detection: scans $KANBAN_ROOT/projects/*/tasks/HUMAN-APPROVE-*/status.md
#   for tasks whose State field is WAITING or BACKLOG.  Cross-project scan.
#
# One Python call per render: the halt scopes, the approval count and the
#   deferred-bug counts all come from one
#   "pgai_agent_kanban.dashboard.status_line facts" call (through
#   pp_run_ops); the deferral posture itself is resolved in bash first by
#   lib/defer_scope.sh's dashboard_defer_scopes, which reads no bug file.
#
# Sources read (all optional — fallback values used if missing or unreadable):
#   $KANBAN_ROOT/VERSION                              — deployed version
#   $KANBAN_ROOT/projects/<PROJECT_NAME>/project.cfg  — workflow_type (falls back to PROJECT.cfg for legacy installs)
#   $KANBAN_ROOT/HALT, $KANBAN_ROOT/HALT-AFTER        — global halt sentinels
#   $KANBAN_ROOT/projects/<name>/HALT[AFTER]          — per-project halt sentinels (every project, not just PROJECT_NAME)
#   $KANBAN_ROOT/bashrc, env, kanban.cfg              — PGAI_KANBAN_PM_MODE env var
#
# Truncation:
#   Project names longer than 20 characters are truncated to 20 chars with
#   an appended ellipsis ("...").  See "Width budget / truncation rule"
#   above for the separate whole-line width budget.
#
# Exit codes:
#   0   Always. Missing sources produce fallback values; the script never fails.
#       -h/--help also exits 0 after printing this usage.

set -uo pipefail
# -h/--help prints the header comment above and exits 0. It is handled before
# env_bootstrap.sh so help never resolves a kanban root, reads an argument as
# data, or starts the script's own work.
for __help_arg in "$@"; do
  case "$__help_arg" in
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:117}"
      exit 0
      ;;
  esac
done

# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
KANBAN_ROOT="${1:-${PGAI_AGENT_KANBAN_ROOT_PATH}}"
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
# Local-only render guard: this is a dashboard render path, so
# pp_last_released_version below (PROJECT_VERSION resolution) must never
# perform a network git fetch.
export PGAI_GIT_LOCAL_ONLY=1
# Source shared deferral-posture scope helper (single decision point for the
# BUGS DEFERRED segment — resolves through team/scripts/lib/defer_posture.sh,
# never re-derives its precedence here). It also sources pp_run_ops.sh, the
# cwd-independent package-module runner (pp_run_ops) used below.
source "${_DSB_SCRIPT_DIR}/lib/defer_scope.sh"
# Source shared version helper (single tier-order decision point)
# shellcheck source=lib/version.sh
source "${_DSB_SCRIPT_DIR}/lib/version.sh"
# shellcheck source=lib/dev_tree.sh
source "${_DSB_SCRIPT_DIR}/../lib/dev_tree.sh"
# Source glyph map (single home for all status-bar glyph literals)
# shellcheck source=../lib/status_glyphs.sh
source "${_DSB_SCRIPT_DIR}/../lib/status_glyphs.sh"
# Source shared semantic dashboard palette reader (single home for the
# halt/approval/defer tmux-markup color meanings below).
# shellcheck source=../lib/dashboard_palette.sh
source "${_DSB_SCRIPT_DIR}/../lib/dashboard_palette.sh"
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
# Rich-mode glyph detection — re-evaluated here (before HALT rendering).
# Glyphs are prepended in rich (color-capable) mode only; NO_COLOR / TERM=dumb
# output is byte-identical to the pre-glyph baseline (regression lock).
# ---------------------------------------------------------------------------
_DSB_RICH=false
if [[ -z "${NO_COLOR:-}" ]] && [[ "${TERM:-}" != "dumb" ]]; then
    _DSB_RICH=true
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
# Facts — one Python call for every file-system fact the flag segments need.
#
# Halt scopes, in display order: GLOBAL (the kanban root) first, then either
# the one explicit project or every projects/*/ directory in glob order.
# Deferral scopes come from dashboard_defer_scopes (the posture resolved in
# bash, no bug file read); each scope that is "on" is counted by the same
# call. The call prints DFS_SEP-joined lines:
#   halt<SEP>state<SEP>scope<SEP>described_event
#   approvals<SEP>n
#   defer<SEP>scope<SEP>n
# ---------------------------------------------------------------------------
_dsb_fact_args=(--halt-scope GLOBAL "$KANBAN_ROOT")
if [[ -n "${_DSB_EXPLICIT_PROJECT}" ]]; then
    _dsb_fact_args+=(--halt-scope "$PROJECT_NAME" "${KANBAN_ROOT}/projects/${PROJECT_NAME}")
else
    for _dsb_proj_dir in "${KANBAN_ROOT}/projects"/*/; do
        [[ -d "$_dsb_proj_dir" ]] || continue
        _dsb_proj_dir="${_dsb_proj_dir%/}"
        _dsb_fact_args+=(--halt-scope "${_dsb_proj_dir##*/}" "$_dsb_proj_dir")
    done
    unset _dsb_proj_dir
fi

_DSB_DEFER_SCOPES=()
while IFS= read -r _dsb_scope_line; do
    [[ -z "$_dsb_scope_line" ]] && continue
    _DSB_DEFER_SCOPES+=("$_dsb_scope_line")
    IFS="$DFS_SEP" read -r -a _dsb_scope_fields <<< "$_dsb_scope_line"
    if [[ "${_dsb_scope_fields[0]:-}" == "on" ]]; then
        _dsb_fact_args+=(--defer-count "${_dsb_scope_fields[1]}" "${_dsb_scope_fields[2]}" \
            "${_dsb_scope_fields[@]:5}")
    fi
done < <(dashboard_defer_scopes "$KANBAN_ROOT" "${_DSB_EXPLICIT_PROJECT:+$PROJECT_NAME}")
unset _dsb_scope_line _dsb_scope_fields

_DSB_HALT_ROWS=()
_DSB_APPROVAL_COUNT=0
declare -A _DSB_DEFER_COUNTS=()
while IFS="$DFS_SEP" read -r _dsb_kind _dsb_f1 _dsb_f2 _dsb_f3; do
    case "$_dsb_kind" in
        halt)      _DSB_HALT_ROWS+=("${_dsb_f1}${DFS_SEP}${_dsb_f2}${DFS_SEP}${_dsb_f3}") ;;
        approvals) _DSB_APPROVAL_COUNT="${_dsb_f1:-0}" ;;
        defer)     _DSB_DEFER_COUNTS["$_dsb_f1"]="${_dsb_f2:-0}" ;;
    esac
done < <(pp_run_ops pgai_agent_kanban.dashboard.status_line facts "$KANBAN_ROOT" "${_dsb_fact_args[@]}" 2>/dev/null)
unset _dsb_kind _dsb_f1 _dsb_f2 _dsb_f3 _dsb_fact_args

# ---------------------------------------------------------------------------
# HALT / HALT-AFTER state — taskbar halt segments.  FULL ENUMERATION: one
# row per ACTIVE scope (GLOBAL and/or any number of projects) rather than
# the first one found.  Every row becomes its own entry in
# _DSB_HALT_DISPLAYS, in the scope order passed to the facts call above
# (GLOBAL first, then projects alphabetically) — this is what keeps segment
# order identical across repeated renders of the same state.
#
# Scope word rendered is the SOP vocabulary exactly ("GLOBAL" or the
# literal project name) — never a generic placeholder.  A draining row's
# event token arrives already described by describe_token (the one shared
# "until vX.Y.Z" describer) rather than as the raw token.
# ---------------------------------------------------------------------------
_DSB_HALT_DISPLAYS=()

# Save whether an explicit project was supplied ($2) before
# _DSB_EXPLICIT_PROJECT is cleared below (used to gate the project segment).
_DSB_HAS_EXPLICIT_PROJECT="${_DSB_EXPLICIT_PROJECT}"

for _dsb_hs_row in ${_DSB_HALT_ROWS[@]+"${_DSB_HALT_ROWS[@]}"}; do
    IFS="$DFS_SEP" read -r _dsb_hs_state _dsb_hs_scope _dsb_hs_described <<< "$_dsb_hs_row"
    [[ "$_dsb_hs_state" == "normal" ]] && continue
    [[ -z "$_dsb_hs_scope" ]] && continue

    _dsb_hs_text=""
    if [[ "$_dsb_hs_state" == "halted" ]]; then
        _dsb_hs_text="HALT ${_dsb_hs_scope}"
    else
        if [[ -n "$_dsb_hs_described" ]]; then
            _dsb_hs_text="HALT-AFTER ${_dsb_hs_scope} ${_dsb_hs_described}"
        else
            _dsb_hs_text="HALT-AFTER ${_dsb_hs_scope}"
        fi
    fi

    if [[ "$_DSB_USE_COLOR" == "true" ]]; then
        # dashboard_semantic_tmux_fg() reads the USE_COLOR global exactly
        # like ansi_code() does; _DSB_USE_COLOR is already "true" in this
        # branch, so this assignment just makes it visible under the
        # conventional name.
        USE_COLOR="$_DSB_USE_COLOR"
        if [[ "$_dsb_hs_state" == "halted" ]]; then
            _DSB_HALT_DISPLAYS+=("$(dashboard_semantic_tmux_fg halt_active)${GLYPH_HALT} ${_dsb_hs_text}#[default]")
        else
            _DSB_HALT_DISPLAYS+=("$(dashboard_semantic_tmux_fg halt_draining)${GLYPH_HALT_AFTER} ${_dsb_hs_text}#[default]")
        fi
    else
        # NO_COLOR / dumb terminal: bracketed text marker — byte-identical
        # in shape to the pre-enumeration baseline, one per active scope.
        # "HALT GLOBAL"                    → [HALT GLOBAL]
        # "HALT <project>"                 → [HALT <project>]
        # "HALT-AFTER GLOBAL <event>"      → [HALT-AFTER:GLOBAL <event>]
        # "HALT-AFTER <project> <event>"   → [HALT-AFTER:<project> <event>]
        if [[ "$_dsb_hs_state" == "halted" ]]; then
            _DSB_HALT_DISPLAYS+=("[${_dsb_hs_text}]")
        else
            _dsb_ha_rest="${_dsb_hs_text#HALT-AFTER }"
            _DSB_HALT_DISPLAYS+=("[HALT-AFTER:${_dsb_ha_rest}]")
            unset _dsb_ha_rest
        fi
    fi
done
unset _dsb_hs_row _dsb_hs_state _dsb_hs_scope _dsb_hs_text _dsb_hs_described _DSB_EXPLICIT_PROJECT _DSB_HALT_ROWS
unset _DSB_USE_COLOR USE_COLOR
# Note: _DSB_HAS_EXPLICIT_PROJECT is retained here — used in the printf block below.

# ---------------------------------------------------------------------------
# APPROVAL state — count pending HUMAN-APPROVE tasks (WAITING or BACKLOG)
# across all registered projects.  Uses a cross-project filesystem glob over
# the standard layout (projects/*/tasks/HUMAN-APPROVE-*/status.md) — matches
# HALT detection's pattern of direct file inspection rather than registry lookup.
#
# The count comes from the facts call above, which reads each State field
# with the canonical status.md reader (task_status.read_state).
# WAITING and BACKLOG both indicate the gate is pending operator action.
#
# APPROVAL_DISPLAY is set to one of:
#   ""                       — no pending approvals (n=0; segment omitted)
#   "#[fg=yellow]✋ APPROVAL(n)#[default]" — n pending (USE_COLOR mode; tmux markup)
#   "[APPROVAL(n)]"          — n pending (NO_COLOR / dumb terminal mode)
# ---------------------------------------------------------------------------
APPROVAL_DISPLAY=""
_dsb_approval_count="${_DSB_APPROVAL_COUNT:-0}"
[[ "$_dsb_approval_count" =~ ^[0-9]+$ ]] || _dsb_approval_count=0

# Re-read color mode (USE_COLOR was unset above).
_dsb_approval_use_color=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _dsb_approval_use_color=false
fi

if [[ "$_dsb_approval_count" -ge 1 ]]; then
    if [[ "$_dsb_approval_use_color" == "true" ]]; then
        USE_COLOR="$_dsb_approval_use_color"
        APPROVAL_DISPLAY="$(dashboard_semantic_tmux_fg approval_pending)${GLYPH_APPROVAL} APPROVAL(${_dsb_approval_count})#[default]"
        unset USE_COLOR
    else
        # NO_COLOR / dumb: bracketed text — byte-identical to pre-RC (no glyph).
        APPROVAL_DISPLAY="[APPROVAL(${_dsb_approval_count})]"
    fi
fi
unset _dsb_approval_count _dsb_approval_use_color _DSB_APPROVAL_COUNT

# ---------------------------------------------------------------------------
# DEFER / BUGS DEFERRED — status-bar segments for the deferral posture in
# force. Resolves via dashboard/lib/defer_scope.sh (single-source; never
# re-derives defer_posture.sh's tier precedence or discovery_defer.sh's
# severity decision here); the posture comes from dashboard_defer_scopes and
# the counts from the facts call above.
#
# Per-project drill bar (explicit PROJECT_NAME): that project's own posture,
# counting only its own bugs/ dir, as one segment:
#   ""                                        — nothing deferred; omitted
#   "<glyph> BUGS DEFERRED n (<=level)[ until vX.Y.Z]" — rich mode
#   "BUGS DEFERRED n (<=level)[ until vX.Y.Z]"          — NO_COLOR/dumb;
#       byte-identical to the rich text minus its "<glyph> " prefix
#
# Global bar: one segment per scope dashboard_defer_scopes reports — GLOBAL
# first (a kanban.cfg or root-campaign deferral, counting every project that
# inherits it), then each project whose own settings defer its bugs,
# alphabetically — shown whether or not any bug is waiting:
#   "<glyph> DEFER <scope> <=level[ until vX.Y.Z] (n waiting)" — rich mode
#   "DEFER <scope> <=level[ until vX.Y.Z] (n waiting)"          — NO_COLOR/dumb
#
# Either bar, when resolve_defer_posture refused:
#   "<glyph> DEFER CONFIG UNREADABLE: <path>" (rich) /
#   "DEFER CONFIG UNREADABLE: <path>" (NO_COLOR) — never the deferral glyph,
#       never a silently omitted segment
# ---------------------------------------------------------------------------
_DSB_DEFER_DISPLAYS=()
_dsb_defer_use_color=true
if [[ -n "${NO_COLOR:-}" ]] || [[ "${TERM:-}" == "dumb" ]]; then
    _dsb_defer_use_color=false
fi

for _dsb_defer_line in ${_DSB_DEFER_SCOPES[@]+"${_DSB_DEFER_SCOPES[@]}"}; do
    _dsb_defer_mode="" _dsb_defer_scope="" _dsb_defer_level="" _dsb_defer_until="" _dsb_defer_errfile=""
    IFS="$DFS_SEP" read -r _dsb_defer_mode _dsb_defer_scope _dsb_defer_level _dsb_defer_until _dsb_defer_errfile _ \
        <<< "$_dsb_defer_line"
    _dsb_defer_count=0
    [[ -n "$_dsb_defer_scope" ]] && _dsb_defer_count="${_DSB_DEFER_COUNTS[$_dsb_defer_scope]:-0}"

    _dsb_defer_text=""
    if [[ "$_dsb_defer_mode" == "malformed" ]]; then
        _dsb_defer_text="DEFER CONFIG UNREADABLE: ${_dsb_defer_errfile}"
        if [[ "$_dsb_defer_use_color" == "true" ]]; then
            USE_COLOR="$_dsb_defer_use_color"
            _DSB_DEFER_DISPLAYS+=("$(dashboard_semantic_tmux_fg defer_warning)${GLYPH_DEFER_WARNING} ${_dsb_defer_text}#[default]")
            unset USE_COLOR
        else
            _DSB_DEFER_DISPLAYS+=("$_dsb_defer_text")
        fi
        continue
    fi
    [[ "$_dsb_defer_mode" == "on" ]] || continue

    if [[ -n "$_DSB_HAS_EXPLICIT_PROJECT" ]]; then
        [[ "${_dsb_defer_count:-0}" -ge 1 ]] || continue
        _dsb_defer_text="BUGS DEFERRED ${_dsb_defer_count} (<=${_dsb_defer_level})"
        [[ -n "$_dsb_defer_until" ]] && _dsb_defer_text="${_dsb_defer_text} until ${_dsb_defer_until}"
    else
        _dsb_defer_text="$(dashboard_defer_segment_text on "$_dsb_defer_scope" "$_dsb_defer_level" \
            "$_dsb_defer_until" "${_dsb_defer_count:-0}" "")"
    fi
    if [[ "$_dsb_defer_use_color" == "true" ]]; then
        _DSB_DEFER_DISPLAYS+=("${GLYPH_DEFERRED_BUG} ${_dsb_defer_text}")
    else
        _DSB_DEFER_DISPLAYS+=("$_dsb_defer_text")
    fi
done
unset _dsb_defer_line _dsb_defer_scope _dsb_defer_mode _dsb_defer_count _dsb_defer_level _dsb_defer_until _dsb_defer_errfile
unset _dsb_defer_use_color _dsb_defer_text _DSB_DEFER_SCOPES _DSB_DEFER_COUNTS

# ---------------------------------------------------------------------------
# Day + time — ISO 8601 with day-of-week prefix (e.g. Sun 2026-05-10T04:55:23)
# ---------------------------------------------------------------------------
DATETIME="$(date '+%a %Y-%m-%dT%H:%M:%S')"

# ---------------------------------------------------------------------------
# Rich-mode display variants for version, PM mode, and timestamp segments.
# Glyphs are prepended in rich (color-capable) mode; NO_COLOR / dumb output
# is the plain text form — byte-identical to the pre-RC baseline.
#
# _DSB_RICH was set in the PM mode block above.
# ---------------------------------------------------------------------------
if [[ "$_DSB_RICH" == "true" ]]; then
    _DSB_FRAMEWORK_DISPLAY="${GLYPH_VERSION} ${FRAMEWORK_VERSION}"
    _DSB_PROJECT_VER_PREFIX="${GLYPH_VERSION} "
    if [[ "$PM_MODE" == "manual" ]]; then
        _DSB_PM_DISPLAY="${GLYPH_PM_MANUAL} PM:manual"
    else
        _DSB_PM_DISPLAY="${GLYPH_PM_AUTO} PM:auto"
    fi
    _DSB_DATETIME_DISPLAY="${GLYPH_TIMESTAMP} ${DATETIME}"
else
    # NO_COLOR / dumb: plain text — no glyph prefix (regression lock).
    _DSB_FRAMEWORK_DISPLAY="${FRAMEWORK_VERSION}"
    _DSB_PROJECT_VER_PREFIX=""
    _DSB_PM_DISPLAY="PM:${PM_MODE}"
    _DSB_DATETIME_DISPLAY="${DATETIME}"
fi
unset _DSB_RICH

# ---------------------------------------------------------------------------
# Emit single formatted line — no trailing newline.
#
# The global bottom bar (PROJECT_NAME not explicitly supplied)
# renders install-global info only: framework version, PM mode, every
# active HALT/HALT-AFTER segment, APPROVAL flag (when pending), BUGS
# DEFERRED (when in force), and date+time.  The per-project segment is
# suppressed in the global bar.
#
# D2 fix: the live framework version (v<FRAMEWORK_VERSION>) is prepended to
# BOTH the global bar and the per-project bar.  FRAMEWORK_VERSION is resolved
# via get_kanban_version (VERSION-file-first — see above).
#
# When PROJECT_NAME was explicitly supplied (per-project drill window caller),
# the project segment is included — preserving B319/B320 behavior.
#
# Segment order (fixed, deterministic across repeated renders of the same
# state — see module header "HALT / HALT-AFTER state" above):
#   info segments -> every active HALT/HALT-AFTER segment (GLOBAL first,
#   then projects alphabetically) -> APPROVAL -> DEFER / BUGS DEFERRED ->
#   timestamp.
# Every HALT/HALT-AFTER/APPROVAL/DEFER segment is independently optional and
# they may appear in ANY combination simultaneously — assembled via an
# array join (with a protected/droppable flag per entry) rather than one
# printf branch per presence combination.
#
# Width budget: see the module header "Width budget / truncation rule"
# above.  _dsb_push_segment below tags each entry "1" (protected — never
# dropped: every HALT/HALT-AFTER/APPROVAL/DEFER flag) or "0" (droppable —
# informational: version/project/workflow/PM/timestamp).
# ---------------------------------------------------------------------------
_DSB_SEGMENTS=()
_DSB_SEGMENT_PROTECTED=()

_dsb_push_segment() {
    # $1 = segment text, $2 = "1" (protected/flag) or "0" (droppable/info)
    [[ -z "$1" ]] && return 0
    _DSB_SEGMENTS+=("$1")
    _DSB_SEGMENT_PROTECTED+=("$2")
}

if [[ -n "$_DSB_HAS_EXPLICIT_PROJECT" ]]; then
    # Per-project drill bar: <version> | <project>:<version> | workflow:<type> | ...
    # _DSB_FRAMEWORK_DISPLAY, _DSB_PROJECT_VER_PREFIX, _DSB_PM_DISPLAY, and
    # _DSB_DATETIME_DISPLAY carry rich-mode glyphs or plain text (NO_COLOR).
    _dsb_push_segment "$_DSB_FRAMEWORK_DISPLAY" "0"
    _dsb_push_segment "${_DSB_PROJECT_VER_PREFIX}${DISPLAY_PROJECT}:${PROJECT_VERSION}" "0"
    _dsb_push_segment "workflow:${WORKFLOW_TYPE}" "0"
    _dsb_push_segment "$_DSB_PM_DISPLAY" "0"
else
    # Global bar: install-global info only — no per-project segment.
    # Prepends live framework version (VERSION-file-first via get_kanban_version).
    _dsb_push_segment "$_DSB_FRAMEWORK_DISPLAY" "0"
    _dsb_push_segment "$_DSB_PM_DISPLAY" "0"
fi
for _dsb_hd in "${_DSB_HALT_DISPLAYS[@]}"; do
    _dsb_push_segment "$_dsb_hd" "1"
done
_dsb_push_segment "$APPROVAL_DISPLAY" "1"
for _dsb_dd in ${_DSB_DEFER_DISPLAYS[@]+"${_DSB_DEFER_DISPLAYS[@]}"}; do
    _dsb_push_segment "$_dsb_dd" "1"
done
_dsb_push_segment "$_DSB_DATETIME_DISPLAY" "0"
unset _dsb_hd _dsb_dd _DSB_DEFER_DISPLAYS

_dsb_assemble() {
    local out="" i
    for i in "${!_DSB_SEGMENTS[@]}"; do
        [[ -z "${_DSB_SEGMENTS[$i]}" ]] && continue
        if [[ -z "$out" ]]; then
            out="${_DSB_SEGMENTS[$i]}"
        else
            out="${out} | ${_DSB_SEGMENTS[$i]}"
        fi
    done
    printf '%s' "$out"
}

_DSB_MAX_WIDTH="${PGAI_DASHBOARD_STATUS_MAX_WIDTH:-320}"
_DSB_OUT="$(_dsb_assemble)"
while [[ "${#_DSB_OUT}" -gt "$_DSB_MAX_WIDTH" ]]; do
    _dsb_dropped=0
    for (( _dsb_i=${#_DSB_SEGMENTS[@]}-1; _dsb_i>=0; _dsb_i-- )); do
        if [[ "${_DSB_SEGMENT_PROTECTED[$_dsb_i]}" == "0" && -n "${_DSB_SEGMENTS[$_dsb_i]}" ]]; then
            _DSB_SEGMENTS[$_dsb_i]=""
            _dsb_dropped=1
            break
        fi
    done
    [[ "$_dsb_dropped" -eq 0 ]] && break
    _DSB_OUT="$(_dsb_assemble)"
done
unset _dsb_dropped _dsb_i _DSB_MAX_WIDTH

printf '%s' "$_DSB_OUT"
unset _DSB_HAS_EXPLICIT_PROJECT _DSB_FRAMEWORK_DISPLAY _DSB_PROJECT_VER_PREFIX _DSB_PM_DISPLAY _DSB_DATETIME_DISPLAY
unset _DSB_SEGMENTS _DSB_SEGMENT_PROTECTED _DSB_HALT_DISPLAYS _DSB_OUT
