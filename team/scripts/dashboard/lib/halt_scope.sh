#!/usr/bin/env bash
# halt_scope.sh — single-source halt scope detection for dashboard scripts.
#
# Provides one function:
#
#   dashboard_halt_scope <KANBAN_ROOT> <PGAI_DEV_TREE_PATH> [<EXPLICIT_PROJECT>]
#
# Writes three tab-separated tokens to stdout:
#
#   state<TAB>scope<TAB>event
#
# Tokens:
#   state  — "halted" | "draining" | "normal"
#   scope  — "GLOBAL" | <project_name> | ""  (empty when normal)
#   event  — halt event token (e.g. "rc") | "" (empty when none or normal)
#
# Detection priority:
#   1. Global kanban root ($KANBAN_ROOT/HALT and HALT-AFTER) — GLOBAL scope.
#      Checked via halt_state.py when available; file-existence fallback otherwise.
#   2. Per-project directories ($KANBAN_ROOT/projects/<name>/HALT[AFTER]):
#      a. When EXPLICIT_PROJECT is non-empty: check only that project.
#      b. When EXPLICIT_PROJECT is empty: iterate ALL project/* dirs; first
#         halted/draining project wins.
#      Scope is the project directory basename (i.e. project name).
#   3. Normal: outputs "normal<TAB><TAB>" (empty scope and event).
#
# Usage from callers:
#   IFS=$'\t' read -r _hs_state _hs_scope _hs_event \
#       < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "${_EXPLICIT_PROJECT:-}")
#
# Callers then compose display strings:
#   halted   + GLOBAL   → "HALT GLOBAL"
#   halted   + <proj>   → "HALT PROJECT"
#   draining + GLOBAL   → "HALT-AFTER GLOBAL <event>"
#   draining + <proj>   → "HALT-AFTER PROJECT <event>"
#   normal              → "" (no halt display)
#
# This function is the single decision point for halt scope in scripts/dashboard/.
# Do not add a fourth private halt-detection ladder to any sibling script.
#
# Constraints:
#   - MUST NOT modify halt_state.py or any chain-execution code path.
#   - MUST NOT change data.sh's emitted key names or vocabulary.
#   - All tmux verification sessions MUST use a private socket (tmux -L <private>).

# Source the shared Python-helper resolver (live-install-first probe order).
# Guard against double-sourcing via PGAI_HELPER_RESOLVER_LOADED sentinel.
if [[ -z "${PGAI_HELPER_RESOLVER_LOADED:-}" ]]; then
    _dhs_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # shellcheck source=helper_resolver.sh
    source "${_dhs_self_dir}/helper_resolver.sh"
    PGAI_HELPER_RESOLVER_LOADED=1
    unset _dhs_self_dir
fi

dashboard_halt_scope() {
    local _dhs_kanban_root="$1"
    local _dhs_dev_tree="$2"
    local _dhs_explicit_proj="${3:-}"

    # Resolve halt_state.py via the shared helper resolver (live-install anchor first).
    local _dhs_hs_py
    _dhs_hs_py="$(resolve_dashboard_helper "$_dhs_kanban_root" "$_dhs_dev_tree" "dashboard/halt_state.py")"
    local _dhs_state="normal"
    local _dhs_scope=""
    local _dhs_event=""

    if [[ -n "$_dhs_hs_py" && -f "$_dhs_hs_py" ]]; then
        # Derive team/ directory from the resolved halt_state.py path for PYTHONPATH.
        # halt_state.py lives at <anchor>/pgai_agent_kanban/dashboard/halt_state.py;
        # three dirname calls yield <anchor>.  The PYTHONPATH must be the parent of
        # pgai_agent_kanban/, i.e. <anchor>, so Python can import pgai_agent_kanban.*.
        local _dhs_hs_team
        _dhs_hs_team="$(dirname "$(dirname "$(dirname "$_dhs_hs_py")")")"

        # --- Step 1: global kanban root (GLOBAL scope, highest precedence) ---
        if [[ -d "$_dhs_kanban_root" ]]; then
            local _dhs_raw
            _dhs_raw="$(PYTHONPATH="$_dhs_hs_team" python3 "$_dhs_hs_py" "$_dhs_kanban_root" 2>/dev/null \
                        || echo "normal	None")"
            IFS=$'\t' read -r _dhs_state _dhs_event <<< "$_dhs_raw"
            if [[ "$_dhs_state" == "halted" || "$_dhs_state" == "draining" ]]; then
                _dhs_scope="GLOBAL"
                # Normalize "None" event token to empty string
                [[ "$_dhs_event" == "None" ]] && _dhs_event=""
                printf '%s\t%s\t%s\n' "$_dhs_state" "$_dhs_scope" "$_dhs_event"
                return 0
            fi
        fi

        # --- Step 2: per-project roots (scope = project name) ---
        _dhs_state="normal"
        _dhs_event=""
        if [[ -n "$_dhs_explicit_proj" ]]; then
            # Explicit project: check only that one.
            local _dhs_proj_root="${_dhs_kanban_root}/projects/${_dhs_explicit_proj}"
            if [[ -d "$_dhs_proj_root" ]]; then
                local _dhs_raw
                _dhs_raw="$(PYTHONPATH="$_dhs_hs_team" python3 "$_dhs_hs_py" "$_dhs_proj_root" 2>/dev/null \
                            || echo "normal	None")"
                IFS=$'\t' read -r _dhs_state _dhs_event <<< "$_dhs_raw"
                if [[ "$_dhs_state" == "halted" || "$_dhs_state" == "draining" ]]; then
                    _dhs_scope="$_dhs_explicit_proj"
                    [[ "$_dhs_event" == "None" ]] && _dhs_event=""
                    printf '%s\t%s\t%s\n' "$_dhs_state" "$_dhs_scope" "$_dhs_event"
                    return 0
                fi
            fi
        else
            # No explicit project: iterate all project directories.
            local _dhs_proj_dir
            for _dhs_proj_dir in "${_dhs_kanban_root}/projects"/*/; do
                [[ -d "$_dhs_proj_dir" ]] || continue
                local _dhs_raw
                _dhs_raw="$(PYTHONPATH="$_dhs_hs_team" python3 "$_dhs_hs_py" "$_dhs_proj_dir" 2>/dev/null \
                            || echo "normal	None")"
                IFS=$'\t' read -r _dhs_state _dhs_event <<< "$_dhs_raw"
                if [[ "$_dhs_state" == "halted" || "$_dhs_state" == "draining" ]]; then
                    _dhs_scope="$(basename "${_dhs_proj_dir%/}")"
                    [[ "$_dhs_event" == "None" ]] && _dhs_event=""
                    printf '%s\t%s\t%s\n' "$_dhs_state" "$_dhs_scope" "$_dhs_event"
                    return 0
                fi
            done
            _dhs_state="normal"
            _dhs_event=""
        fi
    else
        # --- Fallback: halt_state.py unavailable — file-existence checks ---

        # Step 1: global halt
        if [[ -f "${_dhs_kanban_root}/HALT" ]]; then
            printf 'halted\tGLOBAL\t\n'
            return 0
        elif [[ -f "${_dhs_kanban_root}/HALT-AFTER" ]]; then
            local _dhs_ha_content
            _dhs_ha_content="$(head -1 "${_dhs_kanban_root}/HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
            if [[ -n "$_dhs_ha_content" ]]; then
                printf 'draining\tGLOBAL\t%s\n' "$_dhs_ha_content"
            else
                printf 'draining\tGLOBAL\t\n'
            fi
            return 0
        fi

        # Step 2: per-project halt
        if [[ -n "$_dhs_explicit_proj" ]]; then
            local _dhs_proj_root="${_dhs_kanban_root}/projects/${_dhs_explicit_proj}"
            if [[ -f "${_dhs_proj_root}/HALT" ]]; then
                printf 'halted\t%s\t\n' "$_dhs_explicit_proj"
                return 0
            elif [[ -f "${_dhs_proj_root}/HALT-AFTER" ]]; then
                local _dhs_ha_content
                _dhs_ha_content="$(head -1 "${_dhs_proj_root}/HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
                if [[ -n "$_dhs_ha_content" ]]; then
                    printf 'draining\t%s\t%s\n' "$_dhs_explicit_proj" "$_dhs_ha_content"
                else
                    printf 'draining\t%s\t\n' "$_dhs_explicit_proj"
                fi
                return 0
            fi
        else
            local _dhs_proj_dir
            for _dhs_proj_dir in "${_dhs_kanban_root}/projects"/*/; do
                [[ -d "$_dhs_proj_dir" ]] || continue
                local _dhs_proj_name
                _dhs_proj_name="$(basename "${_dhs_proj_dir%/}")"
                if [[ -f "${_dhs_proj_dir}HALT" ]]; then
                    printf 'halted\t%s\t\n' "$_dhs_proj_name"
                    return 0
                elif [[ -f "${_dhs_proj_dir}HALT-AFTER" ]]; then
                    local _dhs_ha_content
                    _dhs_ha_content="$(head -1 "${_dhs_proj_dir}HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
                    if [[ -n "$_dhs_ha_content" ]]; then
                        printf 'draining\t%s\t%s\n' "$_dhs_proj_name" "$_dhs_ha_content"
                    else
                        printf 'draining\t%s\t\n' "$_dhs_proj_name"
                    fi
                    return 0
                fi
            done
        fi
    fi

    # Normal state — no halts found anywhere
    printf 'normal\t\t\n'
    return 0
}
