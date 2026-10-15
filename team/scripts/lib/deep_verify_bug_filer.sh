#!/usr/bin/env bash
# team/scripts/lib/deep_verify_bug_filer.sh
# Bash-facing surface for the DEEP-VERIFY tier's failure-path bug filer.
#
# The filing logic (dedup against an existing open/running bug covering the
# same failing (engine, flavor) pair set, BUG-NNNN numbering via the shared
# claim helper, and the rendered bug-report content) lives in the Python
# module pgai_agent_kanban.deep_verify.bug_filer -- this file is a thin bash
# adapter over it, following the same shape as
# team/scripts/lib/deep_verify_ledger.sh (bash CLI + project-path
# resolution, Python owns the on-disk mutation and parsing).
#
# Source this file to get the deep_verify_bug_filer_* functions in your
# shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/deep_verify_bug_filer.sh"
#
# Public API
# ----------
#   deep_verify_bug_filer_file <project_name> <version> <commit> <captured_output_file>
#       Files a bug for a failed deep-verify run into the named project's
#       bugs/ directory (pp_bugs_dir), or cites an existing open/running bug
#       that already covers the same failing (engine, flavor) pair set --
#       or, when the output names no structured pair, the same set of
#       failing cold-lane tests. Prints "key=value" lines to stdout, one
#       field per line:
#
#         outcome=filed|duplicate|no-pairs
#         bug_id=BUG-NNNN            (empty only on internal failure)
#         bug_path=/abs/path/to/file (empty for outcome=duplicate)
#         pairs=engine/flavor[,engine/flavor...]  (empty when none extracted)
#         tests=nodeid[,nodeid...]   (failing pytest node ids; empty when
#                                     none, or when pairs were extracted)
#
#       Returns 0 on success (any of the three outcomes). Returns 1 (with a
#       diagnostic on stderr, no stdout) on an internal failure -- e.g. the
#       captured output file is unreadable. This function writes to the
#       project's bugs/ directory ONLY; it never writes release-state.md,
#       any task status.md, any queue file, or any HALT-family signal file,
#       and it never creates or deletes a branch (team/SOP-core.md,
#       "Gates and Refusals" -- "Unusable build blocks; an unavailable
#       build environment does not" — a
#       deep-verify finding files a bug like any other TESTER finding and
#       never touches whatever RC happens to be open).
#
# Design note (mirrors deep_verify_ledger.sh's own note): this adapter is
# pure bash plus one python3 subprocess per call -- every consumer named in
# this release (team/scripts/deep-verify.sh) is itself a bash entry point.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_DEEP_VERIFY_BUG_FILER_SH_LOADED:-}" ]] && return 0
_PGAI_DEEP_VERIFY_BUG_FILER_SH_LOADED=1

_DEEP_VERIFY_BUG_FILER_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! declare -F pp_bugs_dir >/dev/null 2>&1; then
    # shellcheck source=project_paths.sh
    source "${_DEEP_VERIFY_BUG_FILER_SH_DIR}/project_paths.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_DEEP_VERIFY_BUG_FILER_SH_DIR}/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# deep_verify_bug_filer_file <project_name> <version> <commit> <captured_output_file>
# See "Public API" above.
# ---------------------------------------------------------------------------
deep_verify_bug_filer_file() {
    local name="${1:-}" version="${2:-}" commit="${3:-}" output_file="${4:-}"
    name="$(pp_require_project_context "$name")" || return 1

    local bugs_dir
    bugs_dir="$(pp_bugs_dir "$name")" || return 1

    pp_run_ops pgai_agent_kanban.deep_verify.bug_filer \
        "$bugs_dir" "$name" "$version" "$commit" "$output_file"
}
