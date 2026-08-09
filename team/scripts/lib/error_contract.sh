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
#   pgai_emit_error_contract <error_key> <message> <remedy>
#       Prints the three-line house-shape block to stderr. Use this at the
#       point of failure, the same place a bare "echo ... >&2" fail-loud
#       line would go today.
#
# Both functions require all three arguments to be non-empty — a missing
# field defeats the point of a dual-channel contract, so a blank error_key,
# message, or remedy is treated as a caller bug and refused rather than
# silently emitting a two-field or one-field block. This validation prints
# a plain diagnostic (not the house shape itself, to avoid a tautological
# self-reference) and returns 1.
#
# Callers source this file directly:
#   source "$(dirname "${BASH_SOURCE[0]}")/error_contract.sh"

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
# pgai_emit_error_contract <error_key> <message> <remedy>
# Prints the formatted three-line house-shape block to stderr, followed by
# a trailing newline. Returns 1 (nothing printed to stderr beyond the
# validation diagnostic) if any argument is missing or empty.
# ---------------------------------------------------------------------------
pgai_emit_error_contract() {
    local formatted
    formatted="$(pgai_format_error_contract "$1" "$2" "$3")" || return 1
    printf '%s\n' "$formatted" >&2
}
