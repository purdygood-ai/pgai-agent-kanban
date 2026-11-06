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
#   # The repository a runner under scripts/ operates on (layout-aware).
#   REPO_ROOT="$(resolve_runner_repo_root "$SCRIPT_DIR" "${KANBAN_ROOT}/kanban.cfg")"
#
#   # The checkout a script sits in: the nearest .git directory above it.
#   REPO_ROOT="$(resolve_script_repo_root "$SCRIPT_DIR" "$SCRIPT_DIR/../../..")"
#
# Functions
# ---------
#   resolve_global_dev_tree [cfg]  — env > kanban.cfg, no validation, prints value (possibly empty);
#                                    the chain itself is pgai_agent_kanban.lib.dev_tree's, run
#                                    through this tree's pp_run_ops
#   require_dev_tree <path> <ctx>  — non-empty + is-directory check; exit 1 on failure
#   resolve_runner_repo_root <script_dir> [<ctx>]
#                                  — checkout layout: the runner's own checkout;
#                                    install layout: PGAI_DEV_TREE_PATH
#   resolve_script_repo_root <start_dir> <fallback_dir>
#                                  — nearest directory at or above <start_dir>
#                                    holding a .git directory, else <fallback_dir>
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
# Bootstrap: load read_ini when the caller has not. No function here reads
# an INI file itself; callers that source only this lib still get read_ini.
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# This lib's own directory, recorded while BASH_SOURCE[0] still names this
# file: resolve_global_dev_tree runs the pp_run_ops.sh beside it.
_PGAI_DEV_TREE_LIB_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# ---------------------------------------------------------------------------
# resolve_global_dev_tree [kanban.cfg]
#
# The global tiers of the documented dev_tree_path chain (the project's own
# value comes first: pp_resolve_dev_tree in project_paths.sh). Resolution
# order (first non-empty value wins):
#   1. PGAI_DEV_TREE_PATH (set by the caller or the environment)
#   2. [paths] dev_tree_path from <kanban.cfg> when given, else from the
#      kanban.cfg under PGAI_AGENT_KANBAN_ROOT_PATH (or the default
#      $HOME/pgai_agent_kanban)
#
# The chain is implemented once, in Python: this runs
#   pp_run_ops pgai_agent_kanban.lib.dev_tree global [--kanban-cfg <kanban.cfg>]
# through the pp_run_ops.sh beside this file, in a subshell, so it resolves
# with this tree's own package whatever pp_run_ops the caller defined. Both
# variables are handed to it explicitly, so a value the caller set without
# exporting still counts.
#
# Prints the resolved value with blanks and one pair of quotes removed; may
# be empty when neither source is configured. Never exits non-zero; never
# validates the path. When the resolver cannot run, its own error reaches
# stderr and nothing is printed.
# ---------------------------------------------------------------------------
resolve_global_dev_tree() {
    local -a _args=()
    [[ -n "${1:-}" ]] && _args=(--kanban-cfg "$1")
    (
        # shellcheck source=pp_run_ops.sh
        source "${_PGAI_DEV_TREE_LIB_DIR}/pp_run_ops.sh" || exit 0
        PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-}" \
            PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-}" \
            pp_run_ops pgai_agent_kanban.lib.dev_tree global ${_args[@]+"${_args[@]}"}
    ) || true
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

    # Partial remedy (docs/coding-standards.md "Gates prescribe, not just
    # diagnose"): both branches name the exact config source deterministically, but the
    # dev-tree path value itself is site-specific (only the operator knows
    # where their checkout lives) — the fix commands below print a concrete,
    # filled-in example the reader adapts rather than a bare placeholder.
    if [[ -z "${_path}" ]]; then
        echo "ERROR: dev_tree_path not configured in ${_ctx}. Set it before running." >&2
        echo "" >&2
        echo "Cause: neither PGAI_DEV_TREE_PATH nor a [paths] dev_tree_path key in" >&2
        echo "${_ctx} resolved to a value, and no self-located fallback applied." >&2
        echo "" >&2
        echo "To fix (pick one):" >&2
        echo "  1. export PGAI_DEV_TREE_PATH=\"/srv/develop/my-app\"   # example — use your actual checkout path" >&2
        echo "  2. printf '[paths]\\ndev_tree_path = %s\\n' \"/srv/develop/my-app\" >> \"${_ctx}\"   # example — use your actual checkout path" >&2
        echo "  3. Re-run the command that invoked this gate." >&2
        echo "" >&2
        echo "Rule: every gated runner requires a resolvable dev-tree path before it can" >&2
        echo "locate the test suite it runs." >&2
        # BLOCK-WORTHY: a gated runner cannot locate the test suite it runs
        # without a resolvable dev-tree path.
        # CENSUS-ANCHOR: dev-tree-path-unconfigured-01
        exit 1
    fi

    if [[ ! -d "${_path}" ]]; then
        echo "ERROR: dev_tree_path '${_path}' does not exist (resolved from ${_ctx})." >&2
        echo "" >&2
        echo "Cause: the resolved path '${_path}' (from ${_ctx}) is not a directory —" >&2
        echo "it was likely moved, renamed, or deleted since it was configured." >&2
        echo "" >&2
        echo "To fix:" >&2
        echo "  1. ls -la \"${_path}\"                          # confirm it is really absent" >&2
        echo "  2. export PGAI_DEV_TREE_PATH=\"/srv/develop/my-app\"   # example — point at the real checkout" >&2
        echo "     # or edit [paths] dev_tree_path in ${_ctx} to the real checkout path" >&2
        echo "  3. Re-run the command that invoked this gate." >&2
        echo "" >&2
        echo "Rule: the configured dev-tree path must be a real, existing directory —" >&2
        echo "a stale path fails loud here instead of silently operating on the wrong tree." >&2
        # BLOCK-WORTHY: operating on a stale, no-longer-existing dev-tree
        # path risks silently running against the wrong checkout.
        # CENSUS-ANCHOR: dev-tree-path-missing-01
        exit 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# resolve_runner_repo_root <script_dir> [<context>]
#
# The repository root a runner in <script_dir> operates on. The rule shared
# by every runner that resolves the dev tree and then works inside
# <repo>/team:
#
#   Checkout layout — <repo>/team/scripts/<runner>, recognised by the parent
#     of <script_dir> being named "team": the runner's own checkout,
#     <script_dir>/../.., whatever PGAI_DEV_TREE_PATH names. A run from a
#     worktree tests that worktree, not the configured dev tree.
#   Install layout — <root>/scripts/<runner> (install.sh flattens team/ onto
#     the kanban root, so no repository sits above the script): the resolved
#     dev tree, PGAI_DEV_TREE_PATH.
#
# Prints the absolute root. In the install layout PGAI_DEV_TREE_PATH must
# name an existing directory; otherwise require_dev_tree's refusal fires
# (naming <context>, default kanban.cfg) and the call exits 1. Callers run
# resolve_global_dev_tree and require_dev_tree first, so that refusal is a
# backstop rather than the normal path.
# ---------------------------------------------------------------------------
resolve_runner_repo_root() {
    local _script_dir="${1:?resolve_runner_repo_root: script directory required}"
    local _ctx="${2:-kanban.cfg}"
    local _parent
    _parent="$(cd "${_script_dir}/.." && pwd)" || return 1

    if [[ "$(basename "${_parent}")" == "team" ]]; then
        (cd "${_parent}/.." && pwd)
        return
    fi

    require_dev_tree "${PGAI_DEV_TREE_PATH:-}" "${_ctx}"
    (cd "${PGAI_DEV_TREE_PATH}" && pwd)
}

# ---------------------------------------------------------------------------
# resolve_script_repo_root <start_dir> <fallback_dir>
#
# The checkout a script is running from: walk up from <start_dir> to the
# nearest directory holding a .git directory, however deep the script sits.
# When none does below / (an exported tree, or an install layout with no
# repository above it), <fallback_dir> made absolute instead. Prints the
# path; returns 1 only when <fallback_dir> is needed and cannot be entered.
#
# Example (a dashboard script, team/scripts/dashboard/<name>.sh):
#   REPO_ROOT="$(resolve_script_repo_root "$SCRIPT_DIR" "$SCRIPT_DIR/../../..")"
# ---------------------------------------------------------------------------
resolve_script_repo_root() {
    local _dir="${1:?resolve_script_repo_root: start directory required}"
    local _fallback="${2:?resolve_script_repo_root: fallback directory required}"
    while [[ "${_dir}" != "/" ]] && [[ ! -d "${_dir}/.git" ]]; do
        _dir="$(dirname "${_dir}")"
    done
    if [[ "${_dir}" == "/" ]]; then
        (cd "${_fallback}" && pwd)
        return
    fi
    printf '%s\n' "${_dir}"
}
