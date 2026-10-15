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
#   operator_args_argparse [--value-flags "name1 name2 ..."] [--] "$@"
#       Drop-in replacement for argparse_parse for a script that declares its
#       own value-taking flags instead of the canonical set.  Takes exactly
#       argparse_parse's arguments and passes them through unchanged, except
#       that each -h in the caller's argv is normalized to --help first (the
#       same mapping operator_args_parse applies).  Like argparse_parse, it
#       does not reset the ARGPARSE_* arrays.  A script that renders help with
#       operator_args_render_help_for_flags advertises "--help, -h", so it
#       parses through this function or operator_args_parse, never through
#       argparse_parse directly.
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
#       Uses built-in descriptions for known canonical flags.  A flag outside
#       the canonical set that an EXTRA_LINE already describes gets no line of
#       its own; one described nowhere gets a "(no description)" line.  The h
#       alias is shown only on the "--help, -h" line.  A caller whose
#       flag means something narrower than the canonical one (close.sh's
#       --state takes done|wont-do, not task states) declares the global
#       associative array OPERATOR_FLAG_HELP before calling; an entry there
#       replaces the built-in line for that flag.
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
# operator_args_parse [--extra-value-flags "flag1 flag2 ..."] "$@"
#
# Parse the caller's argv using the canonical operator flag set.
# Boolean flags (--yes, --force, --dry-run, --help) are recognized
# automatically by argparse_parse (they are not in the value-flags list).
# The short form -h is normalized to --help before delegation.
#
# --extra-value-flags is optional and additive: it appends caller-specific
# value-taking flag names (e.g. "version") to the canonical set for this
# parse only, so a wrapper that needs a value flag outside the canonical
# vocabulary (project/key/agent/state) does not have to hand-roll its own
# argparse_parse call. Omitting it preserves the exact prior behavior for
# every existing caller.
#
# After this function returns, the standard ARGPARSE_FLAGS, ARGPARSE_POSITIONAL,
# and ARGPARSE_MISSING arrays are populated and available to the caller.
#
# Example:
#   OPERATOR_VALID_FLAGS=(project version help)
#   operator_args_parse --extra-value-flags "version" "$@"
# ---------------------------------------------------------------------------
operator_args_parse() {
    local _extra_value_flags=""
    if [[ "${1:-}" == "--extra-value-flags" ]]; then
        _extra_value_flags="${2:-}"
        shift 2
    fi

    # Normalize -h → --help in the argument list before delegating.
    local _normalized_args=()
    _operator_args_normalize_help_alias _normalized_args "$@"

    argparse_reset
    argparse_parse \
        --value-flags "${_OPERATOR_ARGS_VALUE_FLAGS}${_extra_value_flags:+ ${_extra_value_flags}}" \
        -- "${_normalized_args[@]+"${_normalized_args[@]}"}"
}

# ---------------------------------------------------------------------------
# _operator_args_normalize_help_alias OUT_ARRAY_NAME "$@"
#
# Copy "$@" into the caller's indexed array OUT_ARRAY_NAME (nameref), with
# every argument that is exactly "-h" replaced by "--help".  Every other
# argument is copied verbatim, in order.  This is the one place the -h alias
# is defined; operator_args_parse and operator_args_argparse both use it.
# ---------------------------------------------------------------------------
_operator_args_normalize_help_alias() {
    local -n _oanha_out="$1"
    shift
    local _oanha_arg
    for _oanha_arg in "$@"; do
        if [[ "${_oanha_arg}" == "-h" ]]; then
            _oanha_out+=("--help")
        else
            _oanha_out+=("${_oanha_arg}")
        fi
    done
}

# ---------------------------------------------------------------------------
# operator_args_argparse [--value-flags "flag1 flag2 ..."] [--] "$@"
#
# argparse_parse with the operator -h alias.  For a script that names its own
# value-taking flags rather than the canonical set operator_args_parse uses.
#
# The leading options are argparse_parse's own (--value-flags LIST,
# --value-flags=LIST, then an optional -- separator) and are passed through
# unchanged.  Each "-h" in the caller's argv after them becomes "--help"
# (_operator_args_normalize_help_alias), then argparse_parse parses the result.
# For every argv with no "-h" in it the outcome is exactly argparse_parse's.
# The ARGPARSE_* arrays are not reset first, matching argparse_parse.
#
# Example:
#   OPERATOR_VALID_FLAGS=(project out help h)
#   operator_args_argparse --value-flags "project out" -- "$@"
#   if argparse_has help; then ...render help...; exit 0; fi
# ---------------------------------------------------------------------------
operator_args_argparse() {
    # argparse_parse's own leading options, read with argparse_parse's grammar.
    local _oaa_options=()
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --value-flags)
                _oaa_options+=("$1")
                shift
                if [[ $# -gt 0 ]]; then
                    _oaa_options+=("$1")
                    shift
                fi
                ;;
            --value-flags=*)
                _oaa_options+=("$1")
                shift
                ;;
            --)
                shift
                break
                ;;
            *)
                break
                ;;
        esac
    done

    local _oaa_args=()
    _operator_args_normalize_help_alias _oaa_args "$@"

    argparse_parse "${_oaa_options[@]+"${_oaa_options[@]}"}" \
        -- "${_oaa_args[@]+"${_oaa_args[@]}"}"
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
# Each declared flag is listed once in the Options block, with a description:
#
#   1. An OPERATOR_FLAG_HELP entry for the flag (below) is printed verbatim.
#   2. Otherwise a known canonical flag prints its built-in line.
#   3. Otherwise, when an EXTRA_LINE already describes the flag, the flags block
#      prints nothing for it and the caller's own line is its listing.  An
#      EXTRA_LINE describes --FLAG when it is
#        - option-shaped: one to four whitespace characters of indent, an
#          optional "(", then --FLAG ending at whitespace, ":" or the end of
#          the line ("  --archive PATH   Archive file", "  (--apply: ...)");
#        - a mode line: --FLAG at column 0, optionally followed by one value
#          word, then ":" ("--details: ...", "--release NAME: ...");
#        - or names it as an alias anywhere ("alias: --dev-tree-path").
#      A flag merely mentioned in prose or in an example is not described.
#   4. Otherwise the flag is described nowhere, and it still gets a line of its
#      own, padded like the built-ins: "  --FLAG             (no description)".
#      An accepted option never silently disappears from the help.
#
# The "h" entry callers declare so that -h parses is the --help alias: it is
# shown only as part of the "--help, -h" line and never gets a line of its own.
#
# Per-script override: when the caller has declared the global associative
# array OPERATOR_FLAG_HELP, an entry for a flag is printed verbatim in place of
# the built-in line (format it like the built-ins: two-space indent, the flag
# and its value placeholder padded to column 21).  Flags without an entry, and
# callers that never declare the array, follow steps 2-4 above.
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
#
#   declare -A OPERATOR_FLAG_HELP=(
#       [state]="  --state STATE      Closed state: done|wont-do (default: done)"
#   )
#   operator_args_render_help_for_flags "close.sh" "Close an item." OPERATOR_VALID_FLAGS
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
        [until]="  --until vX.Y.Z     Release version boundary (a leading 'v' is optional)"
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

    # Caller overrides win over the built-ins (see the header above).
    local _oarhf_have_overrides=false
    if [[ "$(declare -p OPERATOR_FLAG_HELP 2>/dev/null)" == "declare -A"* ]]; then
        _oarhf_have_overrides=true
    fi

    # Flags the caller's extra lines already describe (rule 3 in the header).
    local _oarhf_name_re='([A-Za-z0-9][A-Za-z0-9_-]*)'
    local _oarhf_option_re="^[[:space:]]{1,4}[(]?--${_oarhf_name_re}([[:space:]:]|\$)"
    local _oarhf_mode_re="^--${_oarhf_name_re}([[:space:]]+[^[:space:]:]+)?:"
    local _oarhf_alias_re="[Aa]lias(es)?:?[[:space:]]+--${_oarhf_name_re}"
    local -A _oarhf_described=()
    local _oarhf_line _oarhf_rest
    for _oarhf_line in "$@"; do
        if [[ "${_oarhf_line}" =~ ${_oarhf_option_re} ]]; then
            _oarhf_described["${BASH_REMATCH[1]}"]=1
        elif [[ "${_oarhf_line}" =~ ${_oarhf_mode_re} ]]; then
            _oarhf_described["${BASH_REMATCH[1]}"]=1
        fi
        _oarhf_rest="${_oarhf_line}"
        while [[ "${_oarhf_rest}" =~ ${_oarhf_alias_re} ]]; do
            _oarhf_described["${BASH_REMATCH[2]}"]=1
            _oarhf_rest="${_oarhf_rest#*"${BASH_REMATCH[0]}"}"
        done
    done

    local _oarhf_f
    for _oarhf_f in "${_oarhf_valid_flags[@]}"; do
        if [[ "${_oarhf_f}" == "h" ]]; then
            # -h is shown on the --help line; it is not an option of its own.
            continue
        elif [[ "${_oarhf_have_overrides}" == true && -v "OPERATOR_FLAG_HELP[${_oarhf_f}]" ]]; then
            printf '%s\n' "${OPERATOR_FLAG_HELP[${_oarhf_f}]}"
        elif [[ -v "_oarhf_descriptions[${_oarhf_f}]" ]]; then
            printf '%s\n' "${_oarhf_descriptions[${_oarhf_f}]}"
        elif [[ -v "_oarhf_described[${_oarhf_f}]" ]]; then
            # The caller's own extra line lists and describes this flag.
            continue
        else
            printf '  %-18s %s\n' "--${_oarhf_f}" "(no description)"
        fi
    done

    # Append any extra lines the wrapper supplied.
    local _oarhf_extra
    for _oarhf_extra in "$@"; do
        printf '%s\n' "${_oarhf_extra}"
    done
}
