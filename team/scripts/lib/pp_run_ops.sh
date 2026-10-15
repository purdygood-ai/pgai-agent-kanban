#!/usr/bin/env bash
# team/scripts/lib/pp_run_ops.sh
# Shared helper that runs "python3 -m pgai_agent_kanban.*" with a correct,
# cwd-independent PYTHONPATH so callers are not fragile to the working directory.
#
# USAGE — two entry styles:
#
#   1. Sourced function (preferred for shell scripts):
#
#      source "$(dirname "${BASH_SOURCE[0]}")/pp_run_ops.sh"
#      pp_run_ops pgai_agent_kanban.ops close_item "$project_root" "$key" done
#
#   2. Direct exec (for scripts that cannot or prefer not to source):
#
#      exec /path/to/scripts/lib/pp_run_ops.sh \
#          pgai_agent_kanban.ops close_item "$project_root" "$key" done
#
#      The first argument is the fully-qualified module path (e.g.
#      "pgai_agent_kanban.ops"); remaining arguments are forwarded verbatim.
#
# QUEUE-MARKER VERBS (pgai_agent_kanban.ops) — the one queue-line marker
# writer; bash callers use these instead of a sed or regex of their own:
#
#      pp_run_ops pgai_agent_kanban.ops set_marker    "$queue_file" "$key" "$marker" [1]
#      pp_run_ops pgai_agent_kanban.ops remove_marker "$queue_file" "$key" [1]
#
#      set_marker rewrites only the bracket of every "- [?] KEY ..." line to
#      MARKER (one of " " W A B x; "" reads as " "), keeping the "- " prefix
#      and description; remove_marker deletes those lines. A trailing "1"
#      matches KEY case-insensitively (intake backlogs). Exit 0 on success,
#      3 when the file has no line for KEY, 1 for a bad MARKER, 4 on I/O error.
#
#      pp_run_ops pgai_agent_kanban.ops render_ensure_marker "$queue_file" "$key" "$marker"
#
#      render_ensure_marker prints the queue file with KEY's line set to
#      MARKER, adding "- [MARKER] KEY" under "## Queue" when there is no
#      such line (a missing file renders a new cache). It writes nothing;
#      commit its output with atomic_write.sh's pgai_atomic_write.
#
# PYTHONPATH PRECEDENCE CONTRACT (explicit; do not change without updating callers):
#
#   Order (highest to lowest):
#     1. Any PYTHONPATH entries the caller already set (caller wins).
#     2. The "own-tree root" — the directory containing pgai_agent_kanban/,
#        derived from this file's own location (scripts/lib/../../).
#        In the live install this equals the install root.
#        In a dev-tree worktree this equals the team/ directory, giving
#        in-development code precedence over the live install.
#     3. KANBAN_ROOT (when set) — the live install root appended as fallback
#        so that modules not yet shadowed by the dev tree are still found.
#
#   The resulting expression is:
#     PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}${_pp_own_root}${KANBAN_ROOT:+:${KANBAN_ROOT}}"
#
#   A caller that sets PYTHONPATH before sourcing/calling this helper keeps
#   its entries at the front.  The own-tree root is never duplicated when it
#   equals KANBAN_ROOT (the live-install case).
#
# CONSTRAINTS:
#   - Never depends on the current working directory.
#   - Safe with a scrubbed environment (env -i HOME=... PATH=... only).
#   - Self-locates using BASH_SOURCE[0] (sourced) or $0 (exec); never uses
#     relative paths, `pwd`, or environment variables to find itself.

# ---------------------------------------------------------------------------
# _pp_run_ops_locate_root
# Internal helper: derive the "own-tree root" from the location of this file.
# Returns the absolute path to the directory two levels above scripts/lib/
# (i.e. scripts/lib/../../), which in both the live install and the dev-tree
# worktree is the directory containing the pgai_agent_kanban/ package.
#
# Usage: local root; root="$(_pp_run_ops_locate_root "$_self_path")"
#   where _self_path is the absolute path to this file.
# ---------------------------------------------------------------------------
_pp_run_ops_locate_root() {
    local _self="${1:-}"
    if [[ -z "${_self}" ]]; then
        return 1
    fi
    local _lib_dir
    _lib_dir="$(cd "$(dirname "${_self}")" 2>/dev/null && pwd)" || return 1
    # Walk up two levels: lib/ -> scripts/ -> own-tree root (team/ or install root)
    local _scripts_dir _root
    _scripts_dir="$(dirname "${_lib_dir}")"
    _root="$(dirname "${_scripts_dir}")"
    echo "${_root}"
}

# ---------------------------------------------------------------------------
# pp_run_ops <module> [args...]
# Run "python3 -m <module> [args...]" with PYTHONPATH composed to guarantee
# the package is findable regardless of the current working directory.
#
# Arguments:
#   $1      Fully-qualified module path, e.g. "pgai_agent_kanban.ops"
#   $2...$N Arguments forwarded verbatim to python3 -m <module>
#
# Returns:
#   The exit code of python3.
#
# Environment:
#   PYTHONPATH (read)  — caller-set entries stay ahead of the own-tree root.
#   KANBAN_ROOT (read) — appended as lowest-priority fallback when set.
#
# Example:
#   pp_run_ops pgai_agent_kanban.ops close_item "$project_root" "$key" done
# ---------------------------------------------------------------------------
# pp_pythonpath
# Compose and print the PYTHONPATH value pp_run_ops() would use, per the
# precedence contract documented in the module header. Callers that need to
# `import`/`from` a pgai_agent_kanban module inside their OWN inline `python3
# -` heredoc (rather than invoking a module's CLI via `python3 -m`) use this
# to get the same cwd-independent path resolution pp_run_ops() gets, e.g.:
#
#   PYTHONPATH="$(pp_pythonpath)" python3 - "$status_file" <<'PY'
#   from pgai_agent_kanban.lib.state_parser import parse_state
#   ...
#   PY
#
# Returns 1 (no output) if the own-tree root cannot be derived — mirrors
# pp_run_ops()'s own failure mode.
# ---------------------------------------------------------------------------
pp_pythonpath() {
    local _pp_own_root
    _pp_own_root="$(_pp_run_ops_locate_root "${BASH_SOURCE[0]}")" || {
        echo "pp_pythonpath: could not derive own-tree root from BASH_SOURCE[0]=${BASH_SOURCE[0]:-<unset>}" >&2
        return 1
    }

    if [[ -n "${KANBAN_ROOT:-}" && "${KANBAN_ROOT}" != "${_pp_own_root}" ]]; then
        echo "${PYTHONPATH:+${PYTHONPATH}:}${_pp_own_root}:${KANBAN_ROOT}"
    else
        echo "${PYTHONPATH:+${PYTHONPATH}:}${_pp_own_root}"
    fi
}

# ---------------------------------------------------------------------------
pp_run_ops() {
    local _module="${1:-}"
    if [[ -z "${_module}" ]]; then
        echo "pp_run_ops: module argument required" >&2
        return 1
    fi
    shift

    # Compose PYTHONPATH per the precedence contract (see module header).
    local _pythonpath
    _pythonpath="$(pp_pythonpath)" || return 1

    PYTHONPATH="${_pythonpath}" python3 -m "${_module}" "$@"
}

# ---------------------------------------------------------------------------
# Exec-style entry point: when this script is run directly (not sourced),
# treat $1 as the module and $2...$N as arguments.
# This allows callers that prefer not to source a lib to use this as:
#   /path/to/pp_run_ops.sh pgai_agent_kanban.ops close_item ...
#
# Detection: when BASH_SOURCE[0] equals $0 the script is being executed, not
# sourced.  The test uses the real resolved paths to handle symlinks correctly.
# ---------------------------------------------------------------------------
if [[ "$(realpath "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")" == \
      "$(realpath "${0}" 2>/dev/null || echo "${0}")" ]]; then
    # Running as a direct invocation.
    _exec_module="${1:-}"
    if [[ -z "${_exec_module}" ]]; then
        echo "Usage: $(basename "${0}") <module> [args...]" >&2
        echo "  module: e.g. pgai_agent_kanban.ops" >&2
        exit 1
    fi
    shift

    _exec_own_root="$(_pp_run_ops_locate_root "${BASH_SOURCE[0]}")" || {
        echo "$(basename "${0}"): could not derive own-tree root" >&2
        exit 1
    }

    if [[ -n "${KANBAN_ROOT:-}" && "${KANBAN_ROOT}" != "${_exec_own_root}" ]]; then
        _exec_pythonpath="${PYTHONPATH:+${PYTHONPATH}:}${_exec_own_root}:${KANBAN_ROOT}"
    else
        _exec_pythonpath="${PYTHONPATH:+${PYTHONPATH}:}${_exec_own_root}"
    fi

    exec env PYTHONPATH="${_exec_pythonpath}" python3 -m "${_exec_module}" "$@"
fi
