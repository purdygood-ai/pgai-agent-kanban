#!/usr/bin/env bash
# team/scripts/lib/error_contract.sh
#
# Shared dual-channel error-contract emitter. Every fail-loud site — HALT
# reasons, gate refusals, hook failures, blocker narrations, release-state
# events — speaks both channels through this one primitive: a stable
# machine key a tool can grep and match against the resolution matrix, and
# a plain-language message plus a runnable remedy a stranger can act on
# without the internal ledger open.
#
# House shape (fixed field order, colon-aligned, exactly three lines):
#   error:   <machine key>
#   message: <what stopped and why, in a stranger's words>
#   remedy:  <what to do next, verbatim-runnable where possible>
#
# This module provides the primitive only. It does not itself convert any
# existing fail-loud site — call sites are converted by later tickets in
# this release; this file is the shared implementation they all import.
#
# Public API
# ----------
#   pgai_format_error_contract <error_key> <message> <remedy>
#       Echoes the three-line house-shape block to stdout (no trailing
#       blank line). Use this when the caller needs the formatted text as
#       a string — embedding it in a HALT reason, a release-state event
#       body, or a return value — rather than printing it directly.
#
#   pgai_emit_error_contract <error_key> <message> <remedy> [project]
#       Prints the three-line house-shape block to stderr. Use this at the
#       point of failure, the same place a bare "echo ... >&2" fail-loud
#       line would go today.
#
#       Also records the refusal as the most-recent-refusal entry for
#       <project> — see "Refusal-ledger recording" below. <project> is
#       optional and additive: every existing 3-argument call site keeps
#       working exactly as before.
#
# Both functions require all three of error_key, message, and remedy to be
# non-empty — a missing field defeats the point of a dual-channel contract,
# so a blank error_key, message, or remedy is treated as a caller bug and
# refused rather than silently emitting a two-field or one-field block.
# This validation prints a plain diagnostic (not the house shape itself, to
# avoid a tautological self-reference) and returns 1.
#
# Callers source this file directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"
#
# Refusal-ledger recording
# -------------------------
# Every call to pgai_emit_error_contract also records the refusal as the
# most-recent-refusal entry for the project in scope
# (team/scripts/lib/refusal_ledger.sh) — wired into this ONE shared
# function rather than into each call site, so a refusal converted to this
# emitter next year is queryable for free, with no per-site plumbing.
#
#   - Project scope: the optional 4th argument, falling back to the
#     ambient PGAI_PROJECT_NAME environment variable (the same resolution
#     order team/scripts/lib/project_paths.sh's pp_require_project_context
#     uses everywhere else in this framework). When neither is set — a
#     kanban-root-level refusal, or one that fires before a project is
#     resolved — recording is skipped entirely: it goes NOWHERE, never
#     guesses a project, and never writes into an arbitrary project's
#     directory.
#   - Surface: derived automatically from the immediate caller
#     (${BASH_SOURCE[1]}:${FUNCNAME[1]}) — no call site needs to pass this.
#   - Recording is best-effort: it never changes this function's own exit
#     status, stdout, or the block it prints. A ledger-path-unwritable
#     failure is swallowed silently, exactly like a project-less call. The
#     one exception to silence is a project the root's projects.cfg does
#     not register: nothing is recorded and one "refusal_ledger: ..." line
#     is narrated on stderr. Recording runs after the fields validate and
#     BEFORE the block is printed, so that line precedes the "error:" label
#     and a reader parsing the block (parse_error_contract ignores text
#     before the label) never sees it appended to the remedy.
#
# Module runner
# -------------
# Sourcing this file also defines pp_run_ops (the sibling pp_run_ops.sh,
# function definitions only) unless the caller already has one, so it is
# not replaced. The refusal ledger records through it, and every lib stage
# that speaks this contract (the wake's status and queue writers among
# them) runs its pgai_agent_kanban module calls through it. A function
# lifted out of its own lib file therefore still finds the runner wherever
# this primitive is loaded, without depending on the working directory.
# Best-effort, like the refusal-ledger load below: a copy of this file with
# no pp_run_ops.sh beside it loads exactly as before, defining no runner.

if ! declare -F pp_run_ops >/dev/null 2>&1; then
    _pgai_error_contract_runner="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)/pp_run_ops.sh"
    # shellcheck source=pp_run_ops.sh
    [[ -f "${_pgai_error_contract_runner}" ]] && source "${_pgai_error_contract_runner}"
    unset _pgai_error_contract_runner
fi

# ---------------------------------------------------------------------------
# pgai_format_error_contract <error_key> <message> <remedy>
# Echoes the formatted three-line house-shape block. Returns 1 (no output)
# if any argument is missing or empty.
# ---------------------------------------------------------------------------
pgai_format_error_contract() {
    local error_key="${1:-}"
    local message="${2:-}"
    local remedy="${3:-}"

    if [[ -z "$error_key" || -z "$message" || -z "$remedy" ]]; then
        echo "pgai_format_error_contract: all three arguments (error_key, message, remedy) are required and must be non-empty; got error_key='${error_key}' message='${message}' remedy='${remedy}'" >&2
        return 1
    fi

    printf 'error:   %s\nmessage: %s\nremedy:  %s' "$error_key" "$message" "$remedy"
}

# ---------------------------------------------------------------------------
# _pgai_error_contract_source_refusal_ledger
# Internal: lazily sources refusal_ledger.sh (only on the first call that
# actually needs it — pgai_format_error_contract never touches this).
# Returns 1 (recording is skipped for this call) if the sibling file is
# missing or fails to source; never propagates a diagnostic, because a
# ledger-plumbing problem must not become visible as part of the refusal
# itself.
# ---------------------------------------------------------------------------
_pgai_error_contract_source_refusal_ledger() {
    local _dir
    _dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd)" || return 1
    [[ -f "${_dir}/refusal_ledger.sh" ]] || return 1
    # shellcheck source=refusal_ledger.sh
    source "${_dir}/refusal_ledger.sh" 2>/dev/null
}

# ---------------------------------------------------------------------------
# pgai_emit_error_contract <error_key> <message> <remedy> [project]
# Prints the formatted three-line house-shape block to stderr, followed by
# a trailing newline. Returns 1 (nothing printed to stderr beyond the
# validation diagnostic) if any of error_key, message, or remedy is missing
# or empty.
#
# Also records the refusal into the most-recent-refusal ledger for the
# project in scope — see "Refusal-ledger recording" in this file's header.
# Recording is best-effort: it can never change the return code below.
# ---------------------------------------------------------------------------
pgai_emit_error_contract() {
    local formatted
    formatted="$(pgai_format_error_contract "$1" "$2" "$3")" || return 1

    # Recorded before printing: any ledger narration then precedes the block
    # rather than trailing its remedy (see "Refusal-ledger recording").
    local _pgai_ec_project="${4:-${PGAI_PROJECT_NAME:-}}"
    if [[ -n "$_pgai_ec_project" ]]; then
        if declare -F refusal_ledger_record_best_effort >/dev/null 2>&1 \
            || _pgai_error_contract_source_refusal_ledger; then
            local _pgai_ec_surface
            _pgai_ec_surface="$(basename "${BASH_SOURCE[1]:-unknown}")${FUNCNAME[1]:+:${FUNCNAME[1]}}"
            refusal_ledger_record_best_effort "$1" "$2" "$3" "$_pgai_ec_project" "$_pgai_ec_surface"
        fi
    fi

    printf '%s\n' "$formatted" >&2
    return 0
}
