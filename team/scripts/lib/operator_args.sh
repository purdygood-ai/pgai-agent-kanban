#!/usr/bin/env bash
# team/scripts/lib/operator_args.sh
# Shared argument layer for all operator CLI tools.
#
# Source this file to get the operator_args_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/operator_args.sh"
#
# DESIGN OVERVIEW
# ---------------
# This library is the canonical argument vocabulary for operator scripts.
# It wraps team/scripts/lib/argparse.sh (which does raw parsing) and adds:
#
#   1. Canonical flag definitions — the fixed set of value-flags and boolean
#      flags shared by every operator tool.
#   2. Project resolution — --project flag, falling back to PGAI_PROJECT_NAME.
#   3. Validation helpers — reject invalid --agent and --state values.
#   4. A uniform --help renderer so every wrapper produces a byte-identical
#      help-block shape.
#
# Bash baseline: bash 5.1+ (RHEL 9 / Rocky 9).
#
# Canonical flags
# ---------------
# Value-taking (accept --flag value or --flag=value):
#   --project   Project name.  Falls back to $PGAI_PROJECT_NAME when absent.
#   --key       Arbitrary key/identifier (caller-defined semantics).
#   --agent     Agent role.  Validated against the six canonical roles.
#   --state     Task state.  Validated against the six canonical states.
#
# Boolean (presence means "true"; value stored as "1"):
#   --yes       Skip confirmation prompts.
#   --force     Override safety guards.
#   --dry-run   Report what would happen without doing it.
#   --help / -h Print the help block and return.  (-h is normalized to --help.)
#
# Functions
# ---------
#   operator_args_parse "$@"
#       Parse the caller's argv using the canonical flag set.  Populates the
#       standard ARGPARSE_* arrays from argparse.sh.  Normalizes -h to --help
#       before delegating to argparse_parse.
#
#   operator_args_get FLAG
#       Return (echo) the value of FLAG from ARGPARSE_FLAGS.
#       Returns empty string when the flag was not supplied.
#
#   operator_args_project
#       Return (echo) the resolved project name: --project flag value if
#       present, else $PGAI_PROJECT_NAME, else empty string.
#
#   operator_args_validate_agent AGENT_VALUE
#       Validate AGENT_VALUE against the six canonical roles
#       (pm/coder/writer/tester/cm/po).  Returns 0 on success.
#       Prints an error to stderr and returns 1 on failure.
#
#   operator_args_validate_state STATE_VALUE
#       Validate STATE_VALUE against the six canonical task states
#       (BACKLOG/WAITING/WORKING/BLOCKED/DONE/WONT-DO).  Returns 0 on success.
#       Prints an error to stderr and returns 1 on failure.
#
#   operator_args_render_help SCRIPT_NAME DESCRIPTION [EXTRA_LINE ...]
#       DEPRECATED: use operator_args_render_help_for_flags instead.
#       Print a usage block to stdout.  The EXTRA_LINE arguments supply all
#       flag description lines; no hardcoded flags block is emitted.
#
# Include guard
# -------------
# Double-sourcing is safe; the second source is a no-op.
#
# API stability
# -------------
# This module is a load-bearing foundation.  Future operator wrappers
# (halt.sh, unhalt.sh, halt-after.sh, wontdo.sh, …) and the eventual REST
# adapter all source this file.  Change only additive modifications; do not
# rename or remove existing functions.

# ---------------------------------------------------------------------------
# Include guard: prevent double-loading in the same shell process.
# ---------------------------------------------------------------------------
if [[ -n "${_OPERATOR_ARGS_SH_LOADED:-}" ]]; then
    return 0
fi
_OPERATOR_ARGS_SH_LOADED=1
#   operator_args_validate_known SCRIPT_NAME VALID_FLAGS_ARRAY_NAME
#       Per-script unknown-flag validator.  Takes the name of the caller's
#       declared-flags array (nameref) and rejects any flag present in
#       ARGPARSE_FLAGS or ARGPARSE_MISSING that is NOT in that array.
#       Rejection message: '<script>: unknown argument: --FLAG' to stderr.
#       Returns 0 when all parsed flags are in the declared set.
#       Returns 1 (and prints rejection for each unknown flag) otherwise.
#       Usage:
#           OPERATOR_VALID_FLAGS=(project key force dry-run help)
#           operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS
#
#   operator_args_render_help_for_flags SCRIPT_NAME DESCRIPTION VALID_FLAGS_ARRAY_NAME [EXTRA_LINE ...]
#       Per-script help renderer.  Emits a usage block containing ONLY the
#       flags listed in the caller's declared-flags array (nameref).
#       Uses built-in descriptions for known canonical flags; falls back to a
#       generic line for any flag not in the canonical set.
#       EXTRA_LINE arguments (zero or more) are printed after the flags block.

# ---------------------------------------------------------------------------
# Locate and source the base argparse library.
# This file lives alongside argparse.sh in the same lib/ directory.
# ---------------------------------------------------------------------------
_OPERATOR_ARGS_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=argparse.sh
source "${_OPERATOR_ARGS_LIB_DIR}/argparse.sh"
unset _OPERATOR_ARGS_LIB_DIR

# ---------------------------------------------------------------------------
# Canonical value-taking flag names (passed to argparse_parse --value-flags).
# ---------------------------------------------------------------------------
_OPERATOR_ARGS_VALUE_FLAGS="project key agent state"

# ---------------------------------------------------------------------------
# Canonical valid sets (used by validate helpers).
# ---------------------------------------------------------------------------
_OPERATOR_ARGS_VALID_AGENTS="pm coder writer tester cm po"
_OPERATOR_ARGS_VALID_STATES="BACKLOG WAITING WORKING BLOCKED DONE WONT-DO"

# ---------------------------------------------------------------------------
# operator_args_parse "$@"
#
# Parse the caller's argv using the canonical operator flag set.
# Boolean flags (--yes, --force, --dry-run, --help) are recognized
# automatically by argparse_parse (they are not in the value-flags list).
# The short form -h is normalized to --help before delegation.
#
# After this function returns, the standard ARGPARSE_FLAGS, ARGPARSE_POSITIONAL,
# and ARGPARSE_MISSING arrays are populated and available to the caller.
# ---------------------------------------------------------------------------
operator_args_parse() {
    # Normalize -h → --help in the argument list before delegating.
    local _normalized_args=()
    local _arg
    for _arg in "$@"; do
        if [[ "$_arg" == "-h" ]]; then
            _normalized_args+=("--help")
        else
            _normalized_args+=("$_arg")
        fi
    done

    argparse_reset
    argparse_parse \
        --value-flags "${_OPERATOR_ARGS_VALUE_FLAGS}" \
        -- "${_normalized_args[@]+"${_normalized_args[@]}"}"
}

# ---------------------------------------------------------------------------
# operator_args_get FLAG
#
# Echo the value of FLAG from ARGPARSE_FLAGS.
# Outputs empty string (and returns 0) when the flag was not supplied.
# Callers should check argparse_has FLAG first when distinguishing
# "present-but-empty" from "absent".
# ---------------------------------------------------------------------------
operator_args_get() {
    local _flag="$1"
    if argparse_has "${_flag}"; then
        printf '%s' "${ARGPARSE_FLAGS[${_flag}]}"
    fi
}

# ---------------------------------------------------------------------------
# operator_args_project
#
# Resolve the project name with the standard precedence:
#   1. --project flag value (if present and non-empty)
#   2. $PGAI_PROJECT_NAME environment variable
#   3. Empty string (caller must handle)
# ---------------------------------------------------------------------------
operator_args_project() {
    local _from_flag
    _from_flag="$(operator_args_get project)"
    if [[ -n "${_from_flag}" ]]; then
        printf '%s' "${_from_flag}"
        return 0
    fi
    if [[ -n "${PGAI_PROJECT_NAME:-}" ]]; then
        printf '%s' "${PGAI_PROJECT_NAME}"
        return 0
    fi
    # Return empty string; caller decides whether this is an error.
    return 0
}

# ---------------------------------------------------------------------------
# operator_args_validate_agent AGENT_VALUE
#
# Validate AGENT_VALUE against the six canonical agent roles:
#   pm  coder  writer  tester  cm  po
#
# Returns 0 on success.
# Prints an error message to stderr and returns 1 on failure.
# ---------------------------------------------------------------------------
operator_args_validate_agent() {
    local _value="${1:-}"
    local _role
    for _role in ${_OPERATOR_ARGS_VALID_AGENTS}; do
        if [[ "${_value}" == "${_role}" ]]; then
            return 0
        fi
    done
    printf 'operator_args: invalid --agent value: %q (valid: %s)\n' \
        "${_value}" "${_OPERATOR_ARGS_VALID_AGENTS}" >&2
    return 1
}

# ---------------------------------------------------------------------------
# operator_args_validate_state STATE_VALUE
#
# Validate STATE_VALUE against the six canonical task states:
#   BACKLOG  WAITING  WORKING  BLOCKED  DONE  WONT-DO
#
# Returns 0 on success.
# Prints an error message to stderr and returns 1 on failure.
# ---------------------------------------------------------------------------
operator_args_validate_state() {
    local _value="${1:-}"
    local _state
    for _state in ${_OPERATOR_ARGS_VALID_STATES}; do
        if [[ "${_value}" == "${_state}" ]]; then
            return 0
        fi
    done
    printf 'operator_args: invalid --state value: %q (valid: %s)\n' \
        "${_value}" "${_OPERATOR_ARGS_VALID_STATES}" >&2
    return 1
}

# ---------------------------------------------------------------------------
# operator_args_render_help SCRIPT_NAME DESCRIPTION [EXTRA_LINE ...]
#
# DEPRECATED: use operator_args_render_help_for_flags instead, which derives
# the flags block from the caller's OPERATOR_VALID_FLAGS array so help and
# acceptance share one source of truth.
#
# This function is retained for callers that have not yet migrated.  It emits
# a usage block with SCRIPT_NAME, DESCRIPTION, and any EXTRA_LINE arguments.
# It does NOT emit a hardcoded flags block; callers supply all flag lines via
# EXTRA_LINE arguments.
#
# Parameters:
#   $1   SCRIPT_NAME  — the name of the calling script (e.g. "halt.sh")
#   $2   DESCRIPTION  — one-line description of what the script does
#   $3+  EXTRA_LINE   — flag description lines and any other caller content
# ---------------------------------------------------------------------------
operator_args_render_help() {
    local _name="${1:-<script>}"
    local _desc="${2:-}"
    shift 2 2>/dev/null || true

    printf 'Usage: %s [OPTIONS]\n' "${_name}"
    printf '\n'
    if [[ -n "${_desc}" ]]; then
        printf '%s\n' "${_desc}"
        printf '\n'
    fi
    printf 'Options:\n'

    # Append any extra lines the wrapper supplied.
    local _extra
    for _extra in "$@"; do
        printf '%s\n' "${_extra}"
    done
}

# ---------------------------------------------------------------------------
# operator_args_validate_known SCRIPT_NAME VALID_FLAGS_ARRAY_NAME
#
# Per-script unknown-flag validator.  Checks every flag that argparse recorded
# (in ARGPARSE_FLAGS and ARGPARSE_MISSING) against the caller's declared set.
# Any flag NOT in the declared set is rejected with the uniform message:
#   <script>: unknown argument: --FLAG
# to stderr.
#
# Parameters:
#   $1   SCRIPT_NAME            — the calling script name (e.g. "halt.sh")
#   $2   VALID_FLAGS_ARRAY_NAME — name of the caller's declared-flags array
#                                 (passed by nameref; the array must exist in
#                                 the caller's scope before this call).
#
# Returns 0 when all parsed flags are in the declared set.
# Returns 1 when one or more unknown flags were found (all are reported).
#
# Example:
#   OPERATOR_VALID_FLAGS=(project key force dry-run help)
#   operator_args_validate_known "$(basename "$0")" OPERATOR_VALID_FLAGS \
#       || exit 1
# ---------------------------------------------------------------------------
operator_args_validate_known() {
    local _script_name="${1:-<script>}"
    local -n _oavk_valid_flags="${2}"   # nameref to caller's declared array

    # Build an associative set from the declared flags for O(1) lookup.
    local -A _oavk_valid_set=()
    local _oavk_f
    for _oavk_f in "${_oavk_valid_flags[@]}"; do
        _oavk_valid_set["${_oavk_f}"]=1
    done

    local _oavk_rc=0

    # Check flags that were fully parsed (present in ARGPARSE_FLAGS).
    for _oavk_f in "${!ARGPARSE_FLAGS[@]}"; do
        if [[ ! -v "_oavk_valid_set[${_oavk_f}]" ]]; then
            printf '%s: unknown argument: --%s\n' "${_script_name}" "${_oavk_f}" >&2
            _oavk_rc=1
        fi
    done

    # Check value-taking flags whose value was absent (in ARGPARSE_MISSING).
    # These flags were parsed (their name was seen) but lack a value; they
    # still represent flags the user passed and must be validated.
    for _oavk_f in "${ARGPARSE_MISSING[@]+"${ARGPARSE_MISSING[@]}"}"; do
        # Only reject if it is also not in the declared set; callers may
        # handle missing-value errors themselves for flags they DO declare.
        if [[ ! -v "_oavk_valid_set[${_oavk_f}]" ]]; then
            printf '%s: unknown argument: --%s\n' "${_script_name}" "${_oavk_f}" >&2
            _oavk_rc=1
        fi
    done

    return "${_oavk_rc}"
}

# ---------------------------------------------------------------------------
# operator_args_render_help_for_flags SCRIPT_NAME DESCRIPTION VALID_FLAGS_ARRAY_NAME [EXTRA_LINE ...]
#
# Per-script help renderer.  Emits a usage block listing ONLY the flags in the
# caller's declared-flags array (nameref) — no canonical flags are shown unless
# the caller explicitly declared them.
#
# Built-in descriptions exist for the known canonical flags.  Any flag name not
# in the built-in set is rendered as "--FLAG  (no description)" so the output
# is always valid even for future or caller-specific flags.
#
# Parameters:
#   $1   SCRIPT_NAME            — the name of the calling script (e.g. "halt.sh")
#   $2   DESCRIPTION            — one-line description of what the script does
#   $3   VALID_FLAGS_ARRAY_NAME — name of the caller's declared-flags array (nameref)
#   $4+  EXTRA_LINE             — optional additional lines after the flags block
#
# Example:
#   OPERATOR_VALID_FLAGS=(project help)
#   operator_args_render_help_for_flags "halt.sh" "Halt all tasks for a project." \
#       OPERATOR_VALID_FLAGS
# ---------------------------------------------------------------------------
operator_args_render_help_for_flags() {
    local _name="${1:-<script>}"
    local _desc="${2:-}"
    local -n _oarhf_valid_flags="${3}"
    shift 3 2>/dev/null || true

    # Built-in descriptions for all canonical flags.
    local -A _oarhf_descriptions=(
        [project]="  --project NAME     Project name (default: \$PGAI_PROJECT_NAME)"
        [key]="  --key KEY          Arbitrary key or identifier"
        [agent]="  --agent ROLE       Agent role: pm|coder|writer|tester|cm|po"
        [state]="  --state STATE      Task state: BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO"
        [file]="  --file PATH        File path"
        [yes]="  --yes              Skip confirmation prompts"
        [force]="  --force            Override safety guards"
        [dry-run]="  --dry-run          Report what would happen without making changes"
        [help]="  --help, -h         Show this help and exit"
    )

    printf 'Usage: %s [OPTIONS]\n' "${_name}"
    printf '\n'
    if [[ -n "${_desc}" ]]; then
        printf '%s\n' "${_desc}"
        printf '\n'
    fi
    printf 'Options:\n'

    local _oarhf_f
    for _oarhf_f in "${_oarhf_valid_flags[@]}"; do
        if [[ -v "_oarhf_descriptions[${_oarhf_f}]" ]]; then
            printf '%s\n' "${_oarhf_descriptions[${_oarhf_f}]}"
        else
            printf '  --%s\n' "${_oarhf_f}"
        fi
    done

    # Append any extra lines the wrapper supplied.
    local _oarhf_extra
    for _oarhf_extra in "$@"; do
        printf '%s\n' "${_oarhf_extra}"
    done
}
