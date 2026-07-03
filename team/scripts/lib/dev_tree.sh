#!/usr/bin/env bash
# team/scripts/lib/dev_tree.sh
#
# Shared helper: resolve the global dev_tree_path and gate on its presence.
#
# Usage
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/dev_tree.sh"
#
#   # Resolve the global dev tree (env > kanban.cfg), no validation.
#   # Prints the value; may be empty when not configured.
#   PGAI_DEV_TREE_PATH="$(resolve_global_dev_tree)"
#
#   # Require the dev tree to be non-empty and a real directory.
#   # Prints an error and exits 1 on failure.
#   # <path>    — the already-resolved path to check
#   # <context> — source description for the error message (e.g. "$KANBAN_ROOT/kanban.cfg")
#   require_dev_tree "${PGAI_DEV_TREE_PATH}" "${KANBAN_ROOT}/kanban.cfg"
#
# Functions
# ---------
#   resolve_global_dev_tree        — env > kanban.cfg, no validation, prints value (possibly empty)
#   require_dev_tree <path> <ctx>  — non-empty + is-directory check; exit 1 on failure
#
# Safety invariants
# -----------------
#   - No top-level side effects when sourced.
#   - resolve_global_dev_tree never fails; it only prints.
#   - require_dev_tree preserves the canonical error wording so that the
#     acceptance grep
#       grep -rn 'does not exist (resolved from' team/scripts/ | grep -v lib/dev_tree.sh
#     returns zero matches.
#
# Bootstrap: ensure read_ini is available (only needed by resolve_global_dev_tree).
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# resolve_global_dev_tree
#
# Resolution order (first non-empty value wins):
#   1. PGAI_DEV_TREE_PATH environment variable (already set by caller or env)
#   2. [paths] dev_tree_path from kanban.cfg (located via PGAI_AGENT_KANBAN_ROOT_PATH
#      or the default $HOME/pgai_agent_kanban)
#
# Prints the resolved value; may be empty when neither source is configured.
# Never exits non-zero; never validates the path.
# ---------------------------------------------------------------------------
resolve_global_dev_tree() {
    # Honor env var first — if already set, we are done.
    if [[ -n "${PGAI_DEV_TREE_PATH:-}" ]]; then
        printf '%s' "${PGAI_DEV_TREE_PATH}"
        return 0
    fi

    # Fall back to kanban.cfg.
    local _kanban_root="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
    local _cfg="${_kanban_root}/kanban.cfg"
    if [[ -f "$_cfg" ]] && command -v read_ini >/dev/null 2>&1; then
        local _val
        _val="$(read_ini "$_cfg" paths dev_tree_path "")"
        printf '%s' "${_val}"
        return 0
    fi

    # Neither source available; return empty.
    printf ''
    return 0
}

# ---------------------------------------------------------------------------
# require_dev_tree <path> <context>
#
# Gate: the given <path> must be non-empty and must be an existing directory.
# <context> is a human-readable source descriptor used in error messages
# (typically "$KANBAN_ROOT/kanban.cfg" or similar).
#
# On success: returns 0.
# On failure: prints an error to stderr and exits 1.
#
# The exact error wording is preserved here so it lives in exactly one place:
#   "dev_tree_path not configured in <context>. Set it before running."
#   "dev_tree_path '<path>' does not exist (resolved from <context>)."
# ---------------------------------------------------------------------------
require_dev_tree() {
    local _path="${1:-}"
    local _ctx="${2:-kanban.cfg}"

    if [[ -z "${_path}" ]]; then
        echo "ERROR: dev_tree_path not configured in ${_ctx}. Set it before running." >&2
        exit 1
    fi

    if [[ ! -d "${_path}" ]]; then
        echo "ERROR: dev_tree_path '${_path}' does not exist (resolved from ${_ctx})." >&2
        exit 1
    fi

    return 0
}
