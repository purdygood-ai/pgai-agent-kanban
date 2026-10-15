#!/usr/bin/env bash
# team/scripts/lib/deep_verify_ledger.sh
# Bash-facing read/write surface for the per-project deep-verify ledger.
#
# The ledger itself (the record model, the append-only markdown format, the
# atomic write, and the parse/latest-record logic) lives in the Python
# module pgai_agent_kanban.deep_verify.ledger -- this file is a thin bash
# adapter over it, following the same shape as team/scripts/halt.sh /
# team/scripts/defer-low-bugs.sh (bash CLI parsing + project-path
# resolution, Python owns the on-disk mutation and parsing).
#
# Source this file to get the deep_verify_ledger_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/deep_verify_ledger.sh"
#
# Public API
# ----------
#   deep_verify_ledger_path [project_name]
#       Echoes the absolute path to the named project's deep-verify ledger
#       file: <kanban_root>/projects/<project_name>/deep-verify-ledger.md.
#       Does not create the file or its parent directory.
#
#   deep_verify_ledger_write <project_name> <version> <commit> <verdict> <detail>
#       Appends a new record with the current UTC timestamp. <verdict> must
#       be one of "built", "failed-to-build", "could-not-attempt" -- the
#       Python module refuses (return 1, diagnostic on stderr) any other
#       value, and no record is written. Returns 0 on success.
#
#   deep_verify_ledger_read_latest [project_name]
#       THE single reader every consumer of this ledger should call --
#       "one reader every other consumer calls, never re-derived" (this
#       ticket's Required Output). Prints a block of "key=value" lines to
#       stdout, one field per line, so a caller can parse it with a plain
#       bash loop (no jq dependency):
#
#         found=true|false
#         timestamp=...      (only when found=true)
#         project=...         (only when found=true)
#         version=...         (only when found=true)
#         commit=...           (only when found=true)
#         verdict=...           (only when found=true)
#         detail=...             (only when found=true)
#
#       found=false means "never run" -- distinguishable from "ran and was
#       green" (found=true, verdict=built) without parsing prose. Returns 0
#       in both of those cases. Returns 1 (with a diagnostic on stderr, no
#       stdout) when the ledger file exists but is malformed -- unparseable
#       content, or a most-recent record missing a required field / carrying
#       an out-of-vocabulary verdict. A caller MUST check the exit code
#       before trusting "found=false" as "never run": a malformed ledger is
#       a different condition and refuses instead.
#
# Design note: this adapter is pure bash plus one pp_run_ops call per
# function (the ledger module's append and latest-fields commands) -- every
# consumer (deep-verify.sh, the scheduled dispatcher, the pre-release
# freshness gate) is itself a bash entry point.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_DEEP_VERIFY_LEDGER_SH_LOADED:-}" ]] && return 0
_PGAI_DEEP_VERIFY_LEDGER_SH_LOADED=1

_DEEP_VERIFY_LEDGER_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! declare -F pp_project_root >/dev/null 2>&1; then
    # shellcheck source=project_paths.sh
    source "${_DEEP_VERIFY_LEDGER_SH_DIR}/project_paths.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_DEEP_VERIFY_LEDGER_SH_DIR}/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# deep_verify_ledger_path [project_name]
# See "Public API" above.
# ---------------------------------------------------------------------------
deep_verify_ledger_path() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/deep-verify-ledger.md"
}

# ---------------------------------------------------------------------------
# deep_verify_ledger_write <project_name> <version> <commit> <verdict> <detail>
# See "Public API" above.
# ---------------------------------------------------------------------------
deep_verify_ledger_write() {
    local name="${1:-}" version="${2:-}" commit="${3:-}" verdict="${4:-}" detail="${5:-}"
    name="$(pp_require_project_context "$name")" || return 1

    local ledger_path
    ledger_path="$(deep_verify_ledger_path "$name")" || return 1

    pp_run_ops pgai_agent_kanban.deep_verify.ledger append \
        "$ledger_path" "$name" "$version" "$commit" "$verdict" "$detail" >/dev/null
}

# ---------------------------------------------------------------------------
# deep_verify_ledger_read_latest [project_name]
# See "Public API" above.
# ---------------------------------------------------------------------------
deep_verify_ledger_read_latest() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local ledger_path
    ledger_path="$(deep_verify_ledger_path "$name")" || return 1

    pp_run_ops pgai_agent_kanban.deep_verify.ledger latest-fields "$ledger_path"
}
