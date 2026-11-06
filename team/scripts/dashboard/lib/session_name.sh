#!/usr/bin/env bash
# session_name.sh — the one resolution of the dashboard's tmux session name.
#
# Provides one function:
#
#   dashboard_resolve_session_name <flag_value> <kanban_root>
#
#     Prints the tmux session name a dashboard script acts on, taking the
#     first non-empty value of, in order:
#
#       1. <flag_value>                   — the script's own --session argument
#       2. $PGAI_DASHBOARD_SESSION_NAME   — the per-invocation environment override
#       3. [dashboard] session_name       — from <kanban_root>/kanban.cfg
#       4. pgai-kanban-dashboard          — the built-in default
#
#     A missing kanban.cfg, a missing key and a blank value all fall through
#     to the next tier. Always returns 0.
#
# create.sh, attach.sh, detach.sh and kill.sh all call it, so the four
# scripts cannot disagree about which session they mean.
#
# Safety: no top-level side effects when sourced beyond making read_ini
# available (sourced from scripts/lib/ini_parser.sh when the caller has not
# already defined it).

if ! declare -f read_ini >/dev/null 2>&1; then
    # shellcheck source=../../lib/ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/../../lib/ini_parser.sh"
fi

DASHBOARD_DEFAULT_SESSION_NAME="pgai-kanban-dashboard"

dashboard_resolve_session_name() {
    local flag_value="${1:-}"
    local kanban_root="${2:-}"
    local cfg_value=""

    if [[ -n "$flag_value" ]]; then
        printf '%s\n' "$flag_value"
        return 0
    fi
    if [[ -n "${PGAI_DASHBOARD_SESSION_NAME:-}" ]]; then
        printf '%s\n' "$PGAI_DASHBOARD_SESSION_NAME"
        return 0
    fi
    if [[ -n "$kanban_root" && -f "${kanban_root}/kanban.cfg" ]]; then
        cfg_value="$(read_ini "${kanban_root}/kanban.cfg" dashboard session_name pgai-kanban-dashboard 2>/dev/null || true)"
    fi
    if [[ -n "$cfg_value" ]]; then
        printf '%s\n' "$cfg_value"
        return 0
    fi
    printf '%s\n' "$DASHBOARD_DEFAULT_SESSION_NAME"
    return 0
}
