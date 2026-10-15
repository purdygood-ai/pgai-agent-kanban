#!/usr/bin/env bash
# team/scripts/lib/lint_bug_filer.sh
# Bash-facing surface for the hygiene lint's aggregate bug filer.
#
# The filing logic (grouping the hygiene lint's per-instance findings by
# their own "class" field, dedup against an existing open/running bug
# covering the same class, BUG-NNNN numbering via the shared claim helper,
# and the rendered bug-report content) lives in the Python module
# pgai_agent_kanban.lint.bug_filer -- this file is a thin bash adapter over
# it, following the same shape as team/scripts/lib/deep_verify_bug_filer.sh
# (bash CLI + project-path resolution, Python owns the on-disk mutation and
# parsing).
#
# Source this file to get the lint_bug_filer_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/lint_bug_filer.sh"
#
# Public API
# ----------
#   lint_bug_filer_file <project_name> <findings_file>
#       Reads <findings_file> (team/scripts/lib/gated_runner_lint.sh's
#       findings-file schema), groups every hygiene-lint finding by its own
#       "class" field, and files at most one aggregated bug per class into
#       the named project's bugs/ directory (pp_bugs_dir) -- or cites an
#       existing open/running bug that already covers a class instead of
#       filing a duplicate. Prints one block of "key=value" lines per class
#       result to stdout (class/outcome/bug_id/bug_path/count -- see
#       pgai_agent_kanban.lint.bug_filer's own module docstring for the
#       exact shape), blocks separated by a blank line. Prints nothing when
#       the findings file carries no hygiene-lint findings.
#
#       Returns 0 whenever the filer ran, regardless of how many bugs (if
#       any) it filed. Returns 1 (with a diagnostic on stderr, no stdout)
#       only on an internal failure -- e.g. the named project cannot be
#       resolved. This function writes to the project's bugs/ directory
#       ONLY; it never writes release-state.md, any task status.md, any
#       queue file, or any HALT-family signal file, and it never creates or
#       deletes a branch (mirrors deep_verify_bug_filer.sh's own contract;
#       see that file's header for the "Gates and Refusals" rationale,
#       which applies identically here -- a hygiene finding files a bug
#       like any other quality-class finding and never touches whatever RC
#       happens to be open).
#
#       A CALLER THAT NEEDS THIS FUNCTION TO NEVER AFFECT ITS OWN EXIT CODE
#       (both gated runners) must still wrap the call with `|| true` at the
#       call site -- this function's own non-zero return (project
#       resolution failure) is a real signal a caller MAY want to see; it
#       is the runners' own choice, not this function's, to swallow it.
#
# Design note (mirrors deep_verify_bug_filer.sh's own note): this adapter is
# pure bash plus one python3 subprocess per call -- every consumer named in
# this release (run-unit-tests.sh, run-integration-tests.sh) is itself a
# bash entry point.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_LINT_BUG_FILER_SH_LOADED:-}" ]] && return 0
_PGAI_LINT_BUG_FILER_SH_LOADED=1

_LINT_BUG_FILER_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! declare -F pp_bugs_dir >/dev/null 2>&1; then
    # shellcheck source=project_paths.sh
    source "${_LINT_BUG_FILER_SH_DIR}/project_paths.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_LINT_BUG_FILER_SH_DIR}/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# lint_bug_filer_file <project_name> <findings_file>
# See "Public API" above.
# ---------------------------------------------------------------------------
lint_bug_filer_file() {
    local name="${1:-}" findings_file="${2:-}"
    name="$(pp_require_project_context "$name")" || return 1

    local bugs_dir
    bugs_dir="$(pp_bugs_dir "$name")" || return 1

    pp_run_ops pgai_agent_kanban.lint.bug_filer \
        "$bugs_dir" "$name" "$findings_file"
}
