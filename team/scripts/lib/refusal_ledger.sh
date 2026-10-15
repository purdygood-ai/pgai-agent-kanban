#!/usr/bin/env bash
# team/scripts/lib/refusal_ledger.sh
# Bash-facing read/write surface for the per-project most-recent-refusal
# ledger.
#
# The ledger itself (the record model, the overwrite-not-append markdown
# format, the atomic write, and the parse/read logic) lives in the Python
# module pgai_agent_kanban.lib.refusal_ledger -- this file is a thin bash
# adapter over it, following the same shape as
# team/scripts/lib/deep_verify_ledger.sh (bash CLI parsing + project-path
# resolution, Python owns the on-disk mutation and parsing). Both the bash
# and Python dual-channel emitters (team/scripts/lib/error_contract.sh and
# team/pgai_agent_kanban/lib/error_contract.py) end up calling into the
# SAME Python module -- this file for bash refusals, a direct import for
# Python refusals -- so the two languages can never produce a different
# on-disk record shape.
#
# Source this file to get the refusal_ledger_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/refusal_ledger.sh"
#
# Public API
# ----------
#   refusal_ledger_path [project_name]
#       Echoes the absolute path to the named project's refusal ledger
#       file: <kanban_root>/projects/<project_name>/refusal-ledger.md.
#       Resolves project_name via pp_require_project_context (explicit
#       argument, then PGAI_PROJECT_NAME, then a loud failure) -- this
#       function is the RAW api and does not implement the "no project in
#       scope" soft-skip; see refusal_ledger_record_best_effort below for
#       that.
#
#   refusal_ledger_record <project> <error> <message> <remedy> <surface>
#       REPLACES the named project's ledger with exactly this one record
#       (overwrite, not append -- a second call replaces the first).
#       Returns 0 on success, non-zero (with a diagnostic on stderr) on
#       failure (project unresolvable, kanban root unresolvable, KANBAN_ROOT
#       does not exist as a directory, or the write itself failed). This is
#       the RAW api -- it fails loud, which is what a caller that WANTS to
#       know whether the record landed should use.
#
#       Returns 3 (REFUSAL_LEDGER_UNREGISTERED_RC), with one
#       "refusal_ledger: ..." narration line on stderr, when
#       $KANBAN_ROOT/projects.cfg exists and does not register <project>:
#       nothing is written and projects/<project>/ is never created for a
#       name the registry does not know. A root with no projects.cfg at all
#       records as before. The check is the Python module's own (its
#       'record --kanban-root' path), so both bindings refuse the same names.
#
#       The written record's context field (install|test) is stamped by
#       the Python module this function shells out to
#       (pgai_agent_kanban.lib.refusal_ledger's own 'record' CLI
#       subcommand) -- this function passes no context argument and
#       computes none; the python3 subprocess inherits this process's
#       PYTEST_CURRENT_TEST (set or absent) and stamps from that, so bash
#       and Python callers of the same underlying refusal always agree.
#
#       Deliberately never creates KANBAN_ROOT itself: a refusal recorded
#       from a guard that fires precisely because KANBAN_ROOT is absent
#       must not fabricate the very directory the guard is refusing on.
#       Directories BENEATH an already-existing KANBAN_ROOT (projects/,
#       projects/<name>/) are still created normally -- only the root is
#       guarded.
#
#   refusal_ledger_record_best_effort <error> <message> <remedy> <project> <surface>
#       The safe entry point team/scripts/lib/error_contract.sh's
#       pgai_emit_error_contract calls. NEVER fails and never prints to
#       stdout -- recording is diagnostics, not a gate (team/SOP-core.md
#       "Gates and Refusals"). It prints to stderr in exactly one case: a
#       <project> the root's projects.cfg does not register, where the
#       one-line "refusal_ledger: ..." narration from refusal_ledger_record
#       is passed through so the skipped record is visible. Two cases are a
#       deliberate, silent no-op rather than an error:
#         - <project> is empty (no explicit project and no ambient
#           PGAI_PROJECT_NAME) -- a refusal raised with no project in scope
#           goes NOWHERE rather than guessing which project's directory to
#           write into.
#         - anything downstream fails (KANBAN_ROOT unresolvable, the
#           project directory cannot be created, the write itself fails) --
#           swallowed entirely; the caller's own refusal is never affected.
#       Always returns 0, the unregistered-project case included.
#
#   refusal_ledger_read_latest [project_name]
#       THE single reader every consumer of this ledger should call.
#       Prints "key=value" lines to stdout, one field per line:
#
#         found=true|false
#         timestamp=...   (only when found=true)
#         project=...      (only when found=true)
#         surface=...       (only when found=true)
#         error=...          (only when found=true)
#         message=...         (only when found=true)
#         remedy=...            (only when found=true)
#         context=...             (only when found=true; install|test|unknown
#                                  -- "unknown" for a record written before
#                                  this field existed; never guessed as
#                                  "install")
#
#       found=false means "this project has never recorded a refusal" --
#       distinguishable from a malformed ledger without parsing prose.
#       Returns 0 in both of those cases. Returns 1 (diagnostic on stderr,
#       no stdout) when the ledger file exists but is malformed. A caller
#       MUST check the exit code before trusting "found=false".
#
# Design note (mirrors deep_verify_ledger.sh's own note): this adapter is
# pure bash plus one python3 subprocess per call -- there is no parsing
# complex enough here to justify anything more elaborate.

# Include guard: safe to source multiple times (only loads once).
[[ -n "${_PGAI_REFUSAL_LEDGER_SH_LOADED:-}" ]] && return 0
_PGAI_REFUSAL_LEDGER_SH_LOADED=1

_REFUSAL_LEDGER_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Exit status refusal_ledger_record returns when $KANBAN_ROOT/projects.cfg
# does not register the project -- the Python module's
# EXIT_UNREGISTERED_PROJECT, passed straight through pp_run_ops.
REFUSAL_LEDGER_UNREGISTERED_RC=3

if ! declare -F pp_project_root >/dev/null 2>&1; then
    # shellcheck source=project_paths.sh
    source "${_REFUSAL_LEDGER_SH_DIR}/project_paths.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "${_REFUSAL_LEDGER_SH_DIR}/pp_run_ops.sh"
fi

# ---------------------------------------------------------------------------
# refusal_ledger_path [project_name]
# See "Public API" above.
# ---------------------------------------------------------------------------
refusal_ledger_path() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1
    local project_root
    project_root="$(pp_project_root "$name")" || return 1
    echo "${project_root}/refusal-ledger.md"
}

# ---------------------------------------------------------------------------
# refusal_ledger_record <project> <error> <message> <remedy> <surface>
# See "Public API" above.
# ---------------------------------------------------------------------------
refusal_ledger_record() {
    local project="${1:-}" error="${2:-}" message="${3:-}" remedy="${4:-}" surface="${5:-}"
    project="$(pp_require_project_context "$project")" || return 1

    local ledger_path
    ledger_path="$(refusal_ledger_path "$project")" || return 1

    # Never fabricate the kanban root as a side effect of recording a
    # refusal. refusal_ledger_path (above) already required KANBAN_ROOT to
    # be set to get this far, so this checks only whether the path it named
    # actually exists on disk -- a refusal whose entire subject is "this
    # directory does not exist" must leave the filesystem exactly as it
    # found it. Directories beneath an existing root (projects/<name>/) are
    # still created by the Python writer below; only the root itself is
    # guarded here.
    if [[ ! -d "${KANBAN_ROOT}" ]]; then
        echo "refusal_ledger: KANBAN_ROOT does not exist or is not a directory: ${KANBAN_ROOT} -- refusing to create it as a side effect of recording a refusal" >&2
        return 1
    fi

    # CENSUS CLASS CONVERT (docs/blocking-site-census.md): --kanban-root
    # makes the Python writer refuse a project the root's projects.cfg does
    # not register -- it narrates one line on stderr and returns
    # REFUSAL_LEDGER_UNREGISTERED_RC, writing nothing. A skipped diagnostic
    # write, never a stop: the best-effort caller below stays status-neutral.
    # CENSUS-ANCHOR: refusal-ledger-unregistered-project-02
    pp_run_ops pgai_agent_kanban.lib.refusal_ledger record --kanban-root "${KANBAN_ROOT}" \
        "$ledger_path" "$project" "$error" "$message" "$remedy" "$surface" >/dev/null
}

# ---------------------------------------------------------------------------
# refusal_ledger_record_best_effort <error> <message> <remedy> <project> <surface>
# See "Public API" above. Always returns 0; never prints to stdout, and
# prints to stderr only the unregistered-project narration.
# ---------------------------------------------------------------------------
refusal_ledger_record_best_effort() {
    local error="${1:-}" message="${2:-}" remedy="${3:-}" project="${4:-}" surface="${5:-}"
    [[ -z "$project" ]] && return 0
    # Status-neutral under the caller's `set -e`: the `|| _rl_rc=$?` guard
    # keeps a non-zero ledger write from terminating the caller right here,
    # so `return 0` below is always reached ("always returns 0"). stderr is
    # captured (stdout discarded) so every failure stays silent except the
    # unregistered-project refusal, whose one narration line is forwarded.
    local _rl_err _rl_rc=0
    _rl_err="$(refusal_ledger_record "$project" "$error" "$message" "$remedy" "$surface" 2>&1 >/dev/null)" \
        || _rl_rc=$?
    if [[ "$_rl_rc" -eq "$REFUSAL_LEDGER_UNREGISTERED_RC" && -n "$_rl_err" ]]; then
        printf '%s\n' "$_rl_err" >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# refusal_ledger_read_latest [project_name]
# See "Public API" above.
# ---------------------------------------------------------------------------
refusal_ledger_read_latest() {
    local name
    name="$(pp_require_project_context "${1:-}")" || return 1

    local ledger_path
    ledger_path="$(refusal_ledger_path "$name")" || return 1

    local pythonpath
    pythonpath="$(pp_pythonpath)" || return 1

    PYTHONPATH="$pythonpath" python3 - "$ledger_path" <<'PY'
import sys

from pgai_agent_kanban.lib.refusal_ledger import RefusalLedgerError, read_latest

ledger_path = sys.argv[1]
try:
    record = read_latest(ledger_path)
except RefusalLedgerError as exc:
    print(f"refusal_ledger: {exc}", file=sys.stderr)
    sys.exit(1)

if record is None:
    print("found=false")
    sys.exit(0)

print("found=true")
for key in ("timestamp", "project", "surface", "error", "message", "remedy", "context"):
    value = record.get(key) or ""
    # Single-line values only, matching the ledger's own field-parse shape.
    value = str(value).replace("\n", " ")
    print(f"{key}={value}")
PY
}
