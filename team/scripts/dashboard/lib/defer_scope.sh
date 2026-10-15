#!/usr/bin/env bash
# defer_scope.sh — single-source deferral-posture surfacing for dashboard scripts.
#
# The dashboard's two deferral surfaces (the "BUGS DEFERRED n (<=level)"
# status-bar segment and the per-bug card glyph in the BUGS column) both need
# the same two things: what the deferral posture currently resolves to, and
# which specific open bugs it is holding back. Both questions already have
# one owner each in the tree — team/scripts/lib/defer_posture.sh's
# resolve_defer_posture (the posture) and team/scripts/lib/discovery_defer.sh's
# _disc_bug_severity / _disc_severity_defer_decision (the per-bug severity
# decision, byte-identical to what discovery itself applies). This file adds
# no third copy of either: it is a thin dashboard-side integration point that
# calls both and shapes their answers into what a renderer needs, so
# status-bottom.sh, status-right.sh, and column-render.sh never re-derive the
# precedence or the severity comparison themselves (halt_scope.sh precedent).
#
# Field separator note: every multi-field line below is separated by
# DFS_SEP (ASCII Unit Separator, 0x1F), not a tab. Bash's word-splitting
# treats a run of IFS *whitespace* characters (space/tab/newline) as ONE
# delimiter and strips a trailing run entirely, even when IFS is set to
# only one of them — so two consecutive empty fields ("mode\t\t\tpath")
# silently collapse and "read" hands the non-empty tail to the wrong
# variable. 0x1F is not whitespace, so bash never collapses or trims runs
# of it: every field boundary in a DFS_SEP-joined line survives "read"
# exactly once per separator, empty fields included.
#
# Public entry points
# --------------------
#   dashboard_defer_status_segment <kanban_root> [project_name]
#       For the status-bar segment. Prints one DFS_SEP-separated line:
#         mode<SEP>count<SEP>level<SEP>until<SEP>error_file
#       mode is one of:
#         "off"       — deferred_bugs is not in force. count/level/until/
#                       error_file are all empty; the caller renders nothing.
#         "on"        — deferred_bugs=true. count is the number of currently
#                       deferred bugs (0 is valid — the caller omits the
#                       segment on 0, per the brief's "a zero count is not
#                       rendered" rule); level is the resolved
#                       deferred_bug_level; until is the campaign's until=
#                       target (empty when no campaign file is in force).
#         "malformed" — resolve_defer_posture refused (an unparseable
#                       campaign file, or an invalid [release] value at the
#                       tier the precedence chain actually landed on).
#                       count/level/until are empty; error_file names the
#                       exact file responsible.
#       <project_name> omitted (empty) resolves in resolve_defer_posture's
#       own root-only mode (kanban.cfg + the root-scoped campaign file only)
#       and aggregates count across every registered project's own bugs/ dir
#       at that single resolved level — the natural reading of "the global
#       bar" when postures are inherently per-project-overridable but the
#       framework-wide default is exactly what root-only mode already means.
#       <project_name> supplied resolves that project's own posture (its own
#       project.cfg and project-scoped campaign file take precedence) and
#       counts only that project's own bugs/ dir.
#
#   dashboard_defer_scopes <kanban_root> [project_name]
#       For a status line that counts the deferred bugs itself (status-bottom.sh
#       passes every scope to one pgai_agent_kanban.dashboard.status_line
#       call). Prints one DFS_SEP-separated line per deferral scope:
#         mode<SEP>scope<SEP>level<SEP>until<SEP>error_file[<SEP>bugs_dir...]
#       mode, level, until and error_file mean what they mean for
#       dashboard_defer_status_segment above. When mode is "on" the line ends
#       with the bugs/ directories whose deferred bugs the scope counts.
#       <project_name> supplied: one line, scope = that project, its own
#       posture and bugs/ dir (the per-project drill bar).
#       <project_name> omitted (the global bar): a GLOBAL line when the
#       root-only posture is on or malformed — counting every project that
#       inherits it — then one line per registered project, alphabetically,
#       whose posture is on by its own settings (its own DEFER-LOW-BUGS
#       campaign file or project.cfg) or malformed in a file not already
#       named. Off scopes print nothing; an on scope prints even when no
#       bug is waiting.
#
#   dashboard_defer_segment_text <mode> <scope> <level> <until> <count> <error_file>
#       The global bar's plain segment text for one scope:
#       "DEFER <scope> <=<level>[ until <until>] (<count> waiting)" or
#       "DEFER CONFIG UNREADABLE: <error_file>"; each renderer adds its own
#       glyph and colour markup.
#
#   dashboard_defer_segments <kanban_root>
#       The global bar's segments, counted in one status_line call: one line
#       mode<SEP>text per scope dashboard_defer_scopes emits for the global
#       bar (status-right.sh).
#
#   dashboard_defer_card_ids <kanban_root> <project_name> <bugs_dir>
#       For the per-bug card glyph. Prints:
#         line 1:  mode<SEP>error_file
#         lines 2+: one deferred bug's file stem (no ".md") per line — only
#                   when mode is "on".
#       mode is "off" | "on" | "malformed", same meaning as above.
#       On "malformed", the caller does not get an ID list — the calling
#       convention documented in team/scripts/dashboard/column-render.sh is
#       that EVERY currently-open bug card in <bugs_dir> renders the warning
#       glyph in that case, because the code could not determine which of
#       them the (unreadable) posture would have deferred; this matches
#       discovery's own behavior when resolve_defer_posture refuses —
#       nothing gets bundled, so every open bug is held in the same limbo.
#
# Usage
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/defer_scope.sh"
#   IFS="$DFS_SEP" read -r mode count level until_v errfile \
#       < <(dashboard_defer_status_segment "$KANBAN_ROOT" "${PROJECT_NAME:-}")
#
# Dependencies: this file sources team/scripts/lib/discovery_defer.sh (which
# self-sources defer_posture.sh), team/scripts/lib/projects.sh and
# team/scripts/lib/pp_run_ops.sh, each guarded against double-sourcing —
# callers do not need to source any of them themselves. project_paths.sh (for pp_bugs_dir) must already be sourced by
# the caller, matching every sibling dashboard/lib/*.sh file's contract.

# Guard against double-sourcing.
if declare -F dashboard_defer_status_segment >/dev/null 2>&1; then
    return 0
fi

# Shared field separator — exported so callers can split this file's
# multi-field output without hand-transcribing the escape (see the file
# header's "Field separator note" above for why it is not a tab).
DFS_SEP=$'\x1f'

_DFS_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DFS_LIB_DIR="$(cd "${_DFS_SELF_DIR}/../../lib" && pwd)"

# discovery_defer.sh self-sources defer_posture.sh (guarded), giving this
# file both resolve_defer_posture and the two severity-decision primitives
# from one source line.
if ! declare -F _disc_bug_deferral_filter >/dev/null 2>&1; then
    # shellcheck source=../../lib/discovery_defer.sh
    source "${_DFS_LIB_DIR}/discovery_defer.sh"
fi
if ! declare -F projects_cfg_list >/dev/null 2>&1; then
    # shellcheck source=../../lib/projects.sh
    source "${_DFS_LIB_DIR}/projects.sh"
fi
if ! declare -F pgai_mktemp >/dev/null 2>&1; then
    # shellcheck source=../../lib/temp.sh
    source "${_DFS_LIB_DIR}/temp.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=../../lib/pp_run_ops.sh
    source "${_DFS_LIB_DIR}/pp_run_ops.sh"
fi
unset _DFS_SELF_DIR _DFS_LIB_DIR

# ---------------------------------------------------------------------------
# dashboard_defer_scan_bugs <bugs_dir> <level>
#
# Echoes the COMPACT bug ID (BUG-NNNN — the file stem's leading "BUG-<digits>"
# run, falling back to the full stem when a file name does not start with
# that pattern) of every currently-open bug in <bugs_dir> that <level>
# defers — the severity decision discovery_step_bugs itself applies per
# candidate file. The compact form (not the raw stem) is what is echoed
# because it is what every card renderer's own compact_id()/BUG_ID_RE
# already displays (team/scripts/lib/dashboard/column_render_cells.sh and
# column_render_data.sh) — a bug filed as "BUG-NNNN-some-title.md" renders
# as the card "BUG-NNNN", so the deferred-ID set has to speak the same
# compact form or membership tests against it would silently never match a
# slugged filename.
#
# Bugs whose own Status is not "open" (already bundled, running, done, ...)
# are not candidates and are silently skipped, matching discovery's own
# unhandled-items scope.
#
# The whole directory is judged in one Python process
# (pgai_agent_kanban.dashboard.status_line deferred-ids), whose status and
# severity readers and severity decision match the card renderers' STATUS_RE
# and discovery_defer.sh's _disc_bug_severity / _disc_severity_defer_decision.
# ---------------------------------------------------------------------------
dashboard_defer_scan_bugs() {
    local bugs_dir="$1" level="$2"
    [[ -d "$bugs_dir" ]] || return 0
    pp_run_ops pgai_agent_kanban.dashboard.status_line deferred-ids "$bugs_dir" "$level" 2>/dev/null || true
    return 0
}

# ---------------------------------------------------------------------------
# _defer_scope_resolve <kanban_root> [project_name]
#
# Internal: the one call to resolve_defer_posture every public entry point
# below builds on. Prints one DFS_SEP-separated line:
#   mode<SEP>level<SEP>until<SEP>error_file<SEP>owner
# mode is "off" | "on" | "malformed" — see the public entry points' header
# comments above for the full contract each field carries. owner (set when
# mode is "on") is "project" when the named project's own settings put the
# posture in force — its own DEFER-LOW-BUGS campaign file, or its
# project.cfg's deferred_bugs or deferred_bug_level — and "root" when it
# comes from the kanban root (kanban.cfg or the root campaign file) or the
# built-in default.
#
# An empty <project_name> always resolves root-only, whatever
# PGAI_PROJECT_NAME the calling environment carries.
#
# resolve_defer_posture is called WITHOUT a $(...) command substitution
# around it — a command substitution forks a subshell, and a subshell's
# writes to DEFER_POSTURE_LAST_ERROR_FILE never reach back out to this
# function's caller. Its stdout is captured via a temp file instead so the
# call runs directly in this shell and the global stays readable right after.
# ---------------------------------------------------------------------------
_defer_scope_resolve() {
    local kanban_root="$1" project_name="${2:-}"
    export KANBAN_ROOT="$kanban_root"

    local _dsr_out
    _dsr_out="$(pgai_mktemp defer_scope_resolve)"
    PGAI_PROJECT_NAME="$project_name" resolve_defer_posture "$project_name" >"$_dsr_out" 2>/dev/null
    local _dsr_rc=$?
    local posture
    posture="$(cat "$_dsr_out" 2>/dev/null)"
    rm -f "$_dsr_out"
    unset _dsr_out

    if [[ $_dsr_rc -ne 0 ]]; then
        printf 'malformed%s%s%s%s\n' "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" \
            "${DEFER_POSTURE_LAST_ERROR_FILE:-${kanban_root}/DEFER-LOW-BUGS}"
        return 0
    fi

    local deferred_bugs="" level="" until_value=""
    local deferred_bugs_tier="" level_tier="" campaign_file=""
    while IFS= read -r _line; do
        [[ -z "$_line" ]] && continue
        local _field="${_line%%=*}"
        local _rest="${_line#*=}"
        case "$_field" in
            deferred_bugs) deferred_bugs="${_rest%%|*}"; deferred_bugs_tier="${_rest#*|}" ;;
            deferred_bug_level) level="${_rest%%|*}"; level_tier="${_rest#*|}" ;;
            until) until_value="${_rest%%|*}" ;;
            campaign_file) campaign_file="${_rest%%|*}" ;;
        esac
    done <<< "$posture"

    if [[ "$deferred_bugs" != "true" ]]; then
        printf 'off%s%s%s%s\n' "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" "$DFS_SEP"
        return 0
    fi

    local owner="root"
    if [[ -n "$project_name" ]]; then
        if [[ "$campaign_file" == "${kanban_root}/projects/${project_name}/DEFER-LOW-BUGS" \
              || "$deferred_bugs_tier" == "project.cfg" || "$level_tier" == "project.cfg" ]]; then
            owner="project"
        fi
    fi
    printf 'on%s%s%s%s%s%s%s\n' "$DFS_SEP" "$level" "$DFS_SEP" "$until_value" "$DFS_SEP" "$DFS_SEP" "$owner"
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_defer_status_segment <kanban_root> [project_name]
# See "Public entry points" above.
# ---------------------------------------------------------------------------
dashboard_defer_status_segment() {
    local kanban_root="$1" project_name="${2:-}"

    local resolved mode level until_v errfile _owner
    resolved="$(_defer_scope_resolve "$kanban_root" "$project_name")"
    IFS="$DFS_SEP" read -r mode level until_v errfile _owner <<< "$resolved"

    if [[ "$mode" == "malformed" ]]; then
        printf 'malformed%s0%s%s%s%s\n' "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" "$errfile"
        return 0
    fi
    if [[ "$mode" != "on" ]]; then
        printf 'off%s0%s%s%s\n' "$DFS_SEP" "$DFS_SEP" "$DFS_SEP"
        return 0
    fi

    local count=0
    if [[ -n "$project_name" ]]; then
        local bdir
        bdir="$(KANBAN_ROOT="$kanban_root" pp_bugs_dir "$project_name" 2>/dev/null || echo "")"
        if [[ -n "$bdir" && -d "$bdir" ]]; then
            count="$(dashboard_defer_scan_bugs "$bdir" "$level" | wc -l | tr -d ' ')"
        fi
    else
        local _p _bdir _n
        while IFS= read -r _p; do
            [[ -z "$_p" ]] && continue
            _bdir="$(KANBAN_ROOT="$kanban_root" pp_bugs_dir "$_p" 2>/dev/null || echo "")"
            [[ -n "$_bdir" && -d "$_bdir" ]] || continue
            _n="$(dashboard_defer_scan_bugs "$_bdir" "$level" | wc -l | tr -d ' ')"
            count=$(( count + _n ))
        done < <(KANBAN_ROOT="$kanban_root" projects_cfg_list 2>/dev/null || true)
    fi

    printf 'on%s%s%s%s%s%s\n' "$DFS_SEP" "$count" "$DFS_SEP" "$level" "$DFS_SEP" "$until_v"
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_defer_scopes <kanban_root> [project_name]
# See "Public entry points" above. Resolves the posture of every scope the
# status line renders, but leaves the counting to the caller: no bug file
# is read here.
#
# With <project_name>: one line, that project's own posture (its bugs/ dir
# when "on").
#
# Without (the global bar): the root-only posture as the GLOBAL scope, then
# every registered project, alphabetically, whose posture is its own:
#   - GLOBAL is emitted when the root-only posture is "on" (kanban.cfg or the
#     root DEFER-LOW-BUGS campaign file) or "malformed". When "on" it counts
#     the bugs/ dirs of every project that inherits it (on, and not by its
#     own settings) — so an all-projects deferral renders once, as GLOBAL.
#   - a project is emitted when its posture is "on" by its own settings
#     (its DEFER-LOW-BUGS campaign file or project.cfg), counting its own
#     bugs/ dir, or "malformed" naming a file no earlier line has named.
# ---------------------------------------------------------------------------
dashboard_defer_scopes() {
    local kanban_root="$1" project_name="${2:-}"

    local resolved mode level until_v errfile owner
    if [[ -n "$project_name" ]]; then
        resolved="$(_defer_scope_resolve "$kanban_root" "$project_name")"
        IFS="$DFS_SEP" read -r mode level until_v errfile owner <<< "$resolved"
        local line="${mode}${DFS_SEP}${project_name}${DFS_SEP}${level}${DFS_SEP}${until_v}${DFS_SEP}${errfile}"
        if [[ "$mode" == "on" ]]; then
            local _bdir
            _bdir="$(KANBAN_ROOT="$kanban_root" pp_bugs_dir "$project_name" 2>/dev/null || echo "")"
            [[ -n "$_bdir" && -d "$_bdir" ]] && line+="${DFS_SEP}${_bdir}"
        fi
        printf '%s\n' "$line"
        return 0
    fi

    local g_mode g_level g_until g_errfile _g_owner
    resolved="$(_defer_scope_resolve "$kanban_root" "")"
    IFS="$DFS_SEP" read -r g_mode g_level g_until g_errfile _g_owner <<< "$resolved"

    local -A named_errfiles=()
    [[ "$g_mode" == "malformed" ]] && named_errfiles["$g_errfile"]=1
    local -a inherited_dirs=() project_lines=() projects=()
    mapfile -t projects < <(KANBAN_ROOT="$kanban_root" projects_cfg_list 2>/dev/null | sort -u)

    local _p _bdir
    for _p in ${projects[@]+"${projects[@]}"}; do
        [[ -z "$_p" ]] && continue
        resolved="$(_defer_scope_resolve "$kanban_root" "$_p")"
        IFS="$DFS_SEP" read -r mode level until_v errfile owner <<< "$resolved"
        case "$mode" in
            malformed)
                [[ -n "${named_errfiles[$errfile]:-}" ]] && continue
                named_errfiles["$errfile"]=1
                project_lines+=("malformed${DFS_SEP}${_p}${DFS_SEP}${DFS_SEP}${DFS_SEP}${errfile}")
                ;;
            on)
                _bdir="$(KANBAN_ROOT="$kanban_root" pp_bugs_dir "$_p" 2>/dev/null || echo "")"
                [[ -n "$_bdir" && -d "$_bdir" ]] || _bdir=""
                if [[ "$owner" == "project" ]]; then
                    project_lines+=("on${DFS_SEP}${_p}${DFS_SEP}${level}${DFS_SEP}${until_v}${DFS_SEP}${_bdir:+${DFS_SEP}${_bdir}}")
                elif [[ -n "$_bdir" ]]; then
                    inherited_dirs+=("$_bdir")
                fi
                ;;
        esac
    done

    case "$g_mode" in
        on)
            local g_line="on${DFS_SEP}GLOBAL${DFS_SEP}${g_level}${DFS_SEP}${g_until}${DFS_SEP}"
            for _bdir in ${inherited_dirs[@]+"${inherited_dirs[@]}"}; do
                g_line+="${DFS_SEP}${_bdir}"
            done
            printf '%s\n' "$g_line"
            ;;
        malformed)
            printf 'malformed%sGLOBAL%s%s%s%s\n' "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" "$DFS_SEP" "$g_errfile"
            ;;
    esac
    local _l
    for _l in ${project_lines[@]+"${project_lines[@]}"}; do
        printf '%s\n' "$_l"
    done
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_defer_segment_text <mode> <scope> <level> <until> <count> <error_file>
#
# The global bar's text for one deferral scope, without glyph or colour
# (each renderer adds its own markup):
#   on        -> "DEFER <scope> <=<level>[ until <until>] (<count> waiting)"
#                (shown whatever the count, 0 included)
#   malformed -> "DEFER CONFIG UNREADABLE: <error_file>"
#   anything else -> nothing
# ---------------------------------------------------------------------------
dashboard_defer_segment_text() {
    local mode="$1" scope="$2" level="$3" until_v="$4" count="${5:-0}" errfile="$6"
    case "$mode" in
        on)
            local text="DEFER ${scope} <=${level}"
            [[ -n "$until_v" ]] && text="${text} until ${until_v}"
            printf '%s (%s waiting)' "$text" "$count"
            ;;
        malformed)
            printf 'DEFER CONFIG UNREADABLE: %s' "$errfile"
            ;;
    esac
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_defer_segments <kanban_root>
#
# The global bar's deferral segments, counted: one DFS_SEP-separated line
# per scope dashboard_defer_scopes emits for the global bar,
#   mode<SEP>text
# with text from dashboard_defer_segment_text. Every "on" scope is counted in
# one pgai_agent_kanban.dashboard.status_line call. For a renderer that does
# not already make that call itself (status-bottom.sh folds the counting
# into its own facts call instead).
# ---------------------------------------------------------------------------
dashboard_defer_segments() {
    local kanban_root="$1"
    local -a scope_lines=() count_args=() fields=()
    local line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        scope_lines+=("$line")
        IFS="$DFS_SEP" read -r -a fields <<< "$line"
        if [[ "${fields[0]:-}" == "on" ]]; then
            count_args+=(--defer-count "${fields[1]}" "${fields[2]}" "${fields[@]:5}")
        fi
    done < <(dashboard_defer_scopes "$kanban_root" "")
    [[ ${#scope_lines[@]} -gt 0 ]] || return 0

    local -A counts=()
    local kind name n _rest
    if [[ ${#count_args[@]} -gt 0 ]]; then
        while IFS="$DFS_SEP" read -r kind name n _rest; do
            [[ "$kind" == "defer" ]] && counts["$name"]="$n"
        done < <(pp_run_ops pgai_agent_kanban.dashboard.status_line facts "$kanban_root" "${count_args[@]}" 2>/dev/null)
    fi

    local mode scope level until_v errfile
    for line in "${scope_lines[@]}"; do
        IFS="$DFS_SEP" read -r mode scope level until_v errfile _rest <<< "$line"
        printf '%s%s%s\n' "$mode" "$DFS_SEP" \
            "$(dashboard_defer_segment_text "$mode" "$scope" "$level" "$until_v" "${counts[$scope]:-0}" "$errfile")"
    done
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_defer_card_ids <kanban_root> <project_name> <bugs_dir>
# See "Public entry points" above.
# ---------------------------------------------------------------------------
dashboard_defer_card_ids() {
    local kanban_root="$1" project_name="$2" bugs_dir="$3"

    local resolved mode level until_v errfile _owner
    resolved="$(_defer_scope_resolve "$kanban_root" "$project_name")"
    IFS="$DFS_SEP" read -r mode level until_v errfile _owner <<< "$resolved"

    if [[ "$mode" == "malformed" ]]; then
        printf 'malformed%s%s\n' "$DFS_SEP" "$errfile"
        return 0
    fi
    if [[ "$mode" != "on" ]]; then
        printf 'off%s\n' "$DFS_SEP"
        return 0
    fi

    printf 'on%s\n' "$DFS_SEP"
    [[ -n "$bugs_dir" && -d "$bugs_dir" ]] && dashboard_defer_scan_bugs "$bugs_dir" "$level"
    return 0
}
