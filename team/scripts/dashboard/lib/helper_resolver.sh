#!/usr/bin/env bash
# helper_resolver.sh — single-source Python-helper resolver for dashboard scripts.
#
# Provides one function:
#
#   resolve_dashboard_helper <kanban_root> <dev_tree_path> <rel_path>
#
# Returns (via stdout) the first existing absolute path from the probe order:
#   1. $kanban_root/pgai_agent_kanban/<rel_path>   — live-install anchor (always present)
#   2. $dev_tree_path/team/pgai_agent_kanban/<rel_path> — dev-tree fallback
#   3. empty string — when neither anchor exists
#
# Probe order rationale:
#   install.sh always ships pgai_agent_kanban into $KANBAN_ROOT/pgai_agent_kanban.
#   In a customer (live-install-only) layout PGAI_DEV_TREE_PATH points at the
#   customer's repo which has no team/ subtree, so helpers are only present at
#   the live-install anchor.  Probing it first ensures every dashboard script
#   works correctly in both dev-tree and live-install-only layouts.
#
# This function is the single decision point for Python-helper resolution in
# scripts/dashboard/.  Do not add per-script probe logic to any sibling script.
#
# Callers source this file and call the function:
#
#   source "${SCRIPT_DIR}/lib/helper_resolver.sh"
#   _MY_PY="$(resolve_dashboard_helper "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "dashboard/my_helper.py")"
#   if [[ -n "$_MY_PY" ]]; then
#       python3 "$_MY_PY" ...
#   fi
#
# Exit codes: always 0.  Missing helper is signalled via empty stdout.

resolve_dashboard_helper() {
    local _rdh_kanban_root="$1"
    local _rdh_dev_tree="$2"
    local _rdh_rel="$3"

    # Probe 1: live-install anchor ($KANBAN_ROOT/pgai_agent_kanban/<rel>)
    local _rdh_live="${_rdh_kanban_root}/pgai_agent_kanban/${_rdh_rel}"
    if [[ -f "$_rdh_live" ]]; then
        printf '%s\n' "$_rdh_live"
        return 0
    fi

    # Probe 2: dev-tree fallback ($dev_tree_path/team/pgai_agent_kanban/<rel>)
    if [[ -n "${_rdh_dev_tree:-}" ]]; then
        local _rdh_dev="${_rdh_dev_tree}/team/pgai_agent_kanban/${_rdh_rel}"
        if [[ -f "$_rdh_dev" ]]; then
            printf '%s\n' "$_rdh_dev"
            return 0
        fi
    fi

    # Neither anchor exists — return empty (caller handles graceful fallback)
    printf ''
    return 0
}
