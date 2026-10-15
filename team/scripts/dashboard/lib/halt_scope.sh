#!/usr/bin/env bash
# halt_scope.sh — single-source halt scope detection for dashboard scripts.
#
# Provides two functions:
#
#   dashboard_halt_scope <KANBAN_ROOT> <PGAI_DEV_TREE_PATH> [<EXPLICIT_PROJECT>]
#   dashboard_halt_describe_event <KANBAN_ROOT> <PGAI_DEV_TREE_PATH> <event>
#
# dashboard_halt_scope — FULL ENUMERATION, not first-wins.
# -----------------------------------------------------------------------
# Writes ZERO OR MORE lines to stdout, one per ACTIVE (halted or draining)
# scope, each a tab-separated triple:
#
#   state<TAB>scope<TAB>event
#
# Tokens:
#   state  — "halted" | "draining"
#   scope  — "GLOBAL" | <project_name>   (the project's own directory name —
#            SOP-core.md vocabulary exactly; never the placeholder word
#            "PROJECT")
#   event  — the raw halt/halt-after event token (e.g. "rc", "rc:v2.9.15"),
#            or "" when no event was recorded
#
# When NOTHING is active anywhere, exactly one line is printed:
#
#   normal<TAB><TAB>
#
# Detection (every call checks ALL of the following — no early return):
#   1. Global kanban root ($KANBAN_ROOT/HALT and HALT-AFTER) — GLOBAL scope.
#      Checked via halt_state.py when available; file-existence fallback
#      otherwise. Always checked, regardless of EXPLICIT_PROJECT.
#   2. Per-project directories ($KANBAN_ROOT/projects/<name>/HALT[AFTER]):
#      a. When EXPLICIT_PROJECT is non-empty: check only that project.
#      b. When EXPLICIT_PROJECT is empty: iterate ALL project/* dirs, in
#         directory-listing (alphabetical, per bash glob expansion) order.
#      Scope is the project directory basename (i.e. project name).
#
# GLOBAL and one or more per-project sentinels MAY all be active
# simultaneously — every active line is emitted. This is the whole point:
# an operator must see a global HALT and a project's own HALT-AFTER at the
# same time, never just the first one a first-wins search happened to find.
#
# Usage from callers (loop over every active row — do not assume one line):
#   while IFS=$'\t' read -r _hs_state _hs_scope _hs_event; do
#       [[ "$_hs_state" == "normal" ]] && continue
#       # ... compose one display segment per (state, scope, event) ...
#   done < <(dashboard_halt_scope "$KANBAN_ROOT" "$PGAI_DEV_TREE_PATH" "${_EXPLICIT_PROJECT:-}")
#
# Callers then compose display strings (scope is the literal word GLOBAL or
# the literal project name — never a generic placeholder):
#   halted   + GLOBAL     → "HALT GLOBAL"
#   halted   + <project>  → "HALT <project>"
#   draining + GLOBAL     → "HALT-AFTER GLOBAL <described-event>"
#   draining + <project>  → "HALT-AFTER <project> <described-event>"
#   nothing active        → no halt display at all (no "normal" line ever
#                            rendered — the empty display IS the "normal"
#                            signal)
#
# dashboard_halt_describe_event — the one shared "until vX.Y.Z" describer.
# -----------------------------------------------------------------------
# See its own header comment below. Every caller that renders a draining
# event token calls this instead of locally formatting "until <version>" —
# the literal phrase is composed in exactly one place in the tree:
# pgai_agent_kanban.halt_after.token.describe_token.
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

# Source pp_run_ops, the cwd-independent bridge to pgai_agent_kanban modules
# that dashboard_halt_describe_event reaches the token describer through.
# Skipped when a caller already loaded it; sourced by absolute path so the
# helper can locate its own tree whatever the caller's working directory.
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=../../lib/pp_run_ops.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../lib" && pwd)/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# _dhs_check_scope_python <hs_py> <hs_team> <root> <scope_name>
#
# Checks one root directory via halt_state.py and, when active
# (halted/draining), prints its "state<TAB>scope<TAB>event" line. Prints
# nothing and returns 1 when normal or the root does not exist.
# ---------------------------------------------------------------------------
_dhs_check_scope_python() {
    local _dcs_hs_py="$1" _dcs_hs_team="$2" _dcs_root="$3" _dcs_scope="$4"
    [[ -d "$_dcs_root" ]] || return 1

    local _dcs_raw _dcs_state _dcs_event
    _dcs_raw="$(PYTHONPATH="$_dcs_hs_team" python3 "$_dcs_hs_py" "$_dcs_root" 2>/dev/null \
                || echo "normal	None")"
    IFS=$'\t' read -r _dcs_state _dcs_event <<< "$_dcs_raw"
    [[ "$_dcs_event" == "None" ]] && _dcs_event=""

    if [[ "$_dcs_state" == "halted" || "$_dcs_state" == "draining" ]]; then
        printf '%s\t%s\t%s\n' "$_dcs_state" "$_dcs_scope" "$_dcs_event"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# _dhs_check_scope_files <root> <scope_name>
#
# Fallback when halt_state.py is unavailable — plain file-existence checks.
# Same output contract as _dhs_check_scope_python above.
# ---------------------------------------------------------------------------
_dhs_check_scope_files() {
    local _dcf_root="$1" _dcf_scope="$2"

    if [[ -f "${_dcf_root}/HALT" ]]; then
        printf 'halted\t%s\t\n' "$_dcf_scope"
        return 0
    elif [[ -f "${_dcf_root}/HALT-AFTER" ]]; then
        local _dcf_content
        _dcf_content="$(head -1 "${_dcf_root}/HALT-AFTER" 2>/dev/null | tr -d '[:space:]')"
        printf 'draining\t%s\t%s\n' "$_dcf_scope" "$_dcf_content"
        return 0
    fi
    return 1
}

# ---------------------------------------------------------------------------
# dashboard_halt_scope <KANBAN_ROOT> <PGAI_DEV_TREE_PATH> [<EXPLICIT_PROJECT>]
# See the file header for the full contract.
# ---------------------------------------------------------------------------
dashboard_halt_scope() {
    local _dhs_kanban_root="$1"
    local _dhs_dev_tree="$2"
    local _dhs_explicit_proj="${3:-}"

    # Resolve halt_state.py via the shared helper resolver (live-install anchor first).
    local _dhs_hs_py
    _dhs_hs_py="$(resolve_dashboard_helper "$_dhs_kanban_root" "$_dhs_dev_tree" "dashboard/halt_state.py")"
    local _dhs_use_python=0
    local _dhs_hs_team=""
    if [[ -n "$_dhs_hs_py" && -f "$_dhs_hs_py" ]]; then
        # Derive team/ directory from the resolved halt_state.py path for PYTHONPATH.
        # halt_state.py lives at <anchor>/pgai_agent_kanban/dashboard/halt_state.py;
        # three dirname calls yield <anchor>.  The PYTHONPATH must be the parent of
        # pgai_agent_kanban/, i.e. <anchor>, so Python can import pgai_agent_kanban.*.
        _dhs_hs_team="$(dirname "$(dirname "$(dirname "$_dhs_hs_py")")")"
        _dhs_use_python=1
    fi

    local _dhs_emitted=0
    local _dhs_out

    # --- Step 1: global kanban root (GLOBAL scope) — ALWAYS checked ---
    #
    # Every capture below is followed by "|| true": _dhs_check_scope_python
    # / _dhs_check_scope_files return 1 (with no output) for the extremely
    # common "this scope is not active" case. That is a normal, expected
    # result here, not an error -- but a bare "var=$(cmd)" assignment whose
    # command exits non-zero WILL trip errexit in any caller that invoked
    # dashboard_halt_scope from a "set -e" context (show-header.sh,
    # attention.sh). Without "|| true" a single non-halted scope partway
    # through the enumeration silently aborts the whole function inside its
    # <(...)  process-substitution subshell -- every scope after it (and
    # even the final "normal" fallback line) never gets checked, which is
    # the opposite of "full enumeration". "|| true" makes a "not active"
    # result a normal, zero-exit-status outcome so the loop below always
    # runs to completion regardless of the caller's own shell options.
    if [[ "$_dhs_use_python" -eq 1 ]]; then
        _dhs_out="$(_dhs_check_scope_python "$_dhs_hs_py" "$_dhs_hs_team" "$_dhs_kanban_root" "GLOBAL" || true)"
    else
        if [[ -d "$_dhs_kanban_root" ]]; then
            _dhs_out="$(_dhs_check_scope_files "$_dhs_kanban_root" "GLOBAL" || true)"
        else
            _dhs_out=""
        fi
    fi
    if [[ -n "$_dhs_out" ]]; then
        printf '%s\n' "$_dhs_out"
        _dhs_emitted=1
    fi

    # --- Step 2: per-project roots (scope = actual project name) ---
    if [[ -n "$_dhs_explicit_proj" ]]; then
        # Explicit project: check only that one.
        local _dhs_proj_root="${_dhs_kanban_root}/projects/${_dhs_explicit_proj}"
        if [[ "$_dhs_use_python" -eq 1 ]]; then
            _dhs_out="$(_dhs_check_scope_python "$_dhs_hs_py" "$_dhs_hs_team" "$_dhs_proj_root" "$_dhs_explicit_proj" || true)"
        else
            if [[ -d "$_dhs_proj_root" ]]; then
                _dhs_out="$(_dhs_check_scope_files "$_dhs_proj_root" "$_dhs_explicit_proj" || true)"
            else
                _dhs_out=""
            fi
        fi
        if [[ -n "$_dhs_out" ]]; then
            printf '%s\n' "$_dhs_out"
            _dhs_emitted=1
        fi
    else
        # No explicit project: iterate all project directories (bash glob
        # expansion is alphabetically sorted — deterministic emission order).
        local _dhs_proj_dir _dhs_proj_name
        for _dhs_proj_dir in "${_dhs_kanban_root}/projects"/*/; do
            [[ -d "$_dhs_proj_dir" ]] || continue
            _dhs_proj_name="$(basename "${_dhs_proj_dir%/}")"
            if [[ "$_dhs_use_python" -eq 1 ]]; then
                _dhs_out="$(_dhs_check_scope_python "$_dhs_hs_py" "$_dhs_hs_team" "${_dhs_proj_dir%/}" "$_dhs_proj_name" || true)"
            else
                _dhs_out="$(_dhs_check_scope_files "${_dhs_proj_dir%/}" "$_dhs_proj_name" || true)"
            fi
            if [[ -n "$_dhs_out" ]]; then
                printf '%s\n' "$_dhs_out"
                _dhs_emitted=1
            fi
        done
    fi

    if [[ "$_dhs_emitted" -eq 0 ]]; then
        printf 'normal\t\t\n'
    fi
    return 0
}

# ---------------------------------------------------------------------------
# dashboard_halt_describe_event <KANBAN_ROOT> <PGAI_DEV_TREE_PATH> <event>
#
# Renders a raw halt-after event token as operator-facing text through the
# ONE shared definition of "what does this token mean" —
# pgai_agent_kanban.halt_after.token.describe_token. A version-pinned token
# ("rc:vX.Y.Z") renders as "until vX.Y.Z"; every other token (the bare "rc",
# or a role token such as "coder") renders unchanged.
#
# This is the single call site every dashboard renderer (status-bottom.sh,
# status-right.sh, show-header.sh) uses for this — none of them locally
# compose the "until " phrase themselves. why.sh --details' posture section
# (pgai_agent_kanban.ops.verdict) calls the same shared function, so the
# phrase is composed in exactly one place in the tree
# (pgai_agent_kanban/halt_after/token.py).
#
# Falls back to the raw token unchanged when python3 or the module is
# unavailable — matches halt_state.py's own fallback doctrine above (never
# refuse the render over an optional dependency).
#
# An empty <event> prints an empty string (no-op) — callers only need to
# call this for a "draining" row whose event is non-empty.
# ---------------------------------------------------------------------------
dashboard_halt_describe_event() {
    local _dhd_kanban_root="$1"
    local _dhd_dev_tree="$2"
    local _dhd_event="${3:-}"

    if [[ -z "$_dhd_event" ]]; then
        printf ''
        return 0
    fi

    local _dhd_token_py
    _dhd_token_py="$(resolve_dashboard_helper "$_dhd_kanban_root" "$_dhd_dev_tree" "halt_after/token.py")"
    if [[ -z "$_dhd_token_py" || ! -f "$_dhd_token_py" ]]; then
        printf '%s' "$_dhd_event"
        return 0
    fi

    local _dhd_token_team
    _dhd_token_team="$(dirname "$(dirname "$(dirname "$_dhd_token_py")")")"
    # The resolved anchor goes first on PYTHONPATH, so pp_run_ops imports the
    # describer from the same tree token.py was found in.
    PYTHONPATH="$_dhd_token_team" pp_run_ops pgai_agent_kanban.halt_after.describe describe "$_dhd_event" 2>/dev/null \
        || printf '%s' "$_dhd_event"
}
