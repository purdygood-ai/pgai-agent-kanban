#!/usr/bin/env bash
# team/scripts/lib/argparse.sh
# Shared flag parser for operator scripts.
#
# Source this file to get the argparse_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/argparse.sh"
#
# DESIGN PRINCIPLE — parse only, never validate
# ---------------------------------------------
# This library PARSES: it splits argv into name/value pairs accepting either
# separator (--flag value  or  --flag=value) and reports flags missing their
# expected values.  It does NOT validate:
#   - It does not know which flags are legal for a given caller.
#   - It does not know whether a value is acceptable.
#   - Unknown flags are recorded, not rejected.
# Validation is always the CALLER'S responsibility.
#
# Bash baseline: bash 5.1+ (RHEL 9 / Rocky 9).
#   Associative arrays (4.0+), local -n namerefs (4.3+), ${var,,} (4.0+)
#   are freely used.  No bash 3 / RHEL 8 / macOS-system-bash fallbacks.
#
# Functions
# ---------
#   argparse_parse [--value-flags "name1 name2 ..."] [--] "$@"
#                         — Parse argv.  Populates three module-level arrays:
#                             ARGPARSE_FLAGS       — associative: flag → value
#                             ARGPARSE_POSITIONAL  — indexed: positional args
#                             ARGPARSE_MISSING     — indexed: flags whose values
#                                                    were absent from argv
#
#   argparse_has FLAG      — Returns 0 (true) if FLAG was seen in argv
#                            (value or boolean).  1 otherwise.
#   argparse_missing FLAG  — Returns 0 (true) if FLAG was a value-taking flag
#                            that had no value in argv.  1 otherwise.
#   argparse_reset         — Clear all three output arrays (call before
#                            re-parsing if needed in the same process).
#
# API contract
# ------------
# The caller declares which flags take values via --value-flags:
#
#   argparse_parse --value-flags "dev-tree sleep color" -- "$@"
#
# With that configuration:
#   --dev-tree /path   → ARGPARSE_FLAGS[dev-tree]=/path
#   --dev-tree=/path   → ARGPARSE_FLAGS[dev-tree]=/path   (identical result)
#   --dry-run          → ARGPARSE_FLAGS[dry-run]=1         (boolean — not in value set)
#   --color '#abc'     → ARGPARSE_FLAGS[color]=#abc        (# preserved verbatim)
#   --foo a=b          → ARGPARSE_FLAGS[foo]=a=b           (first = only)
#   --dev-tree=        → ARGPARSE_FLAGS[dev-tree]=""       (empty, not missing)
#   --dev-tree         → ARGPARSE_MISSING+=(dev-tree)      (at end of argv or
#                                                           next token is a flag)
#   --                 → end of flags; remainder is positional
#   unknown --bar      → ARGPARSE_FLAGS[bar]=1             (recorded, not rejected)
#
# Output arrays (module-level — caller reads these after argparse_parse returns):
#
#   declare -A ARGPARSE_FLAGS      — flag name → value (or "1" for booleans)
#   declare -a ARGPARSE_POSITIONAL — positional arguments in order
#   declare -a ARGPARSE_MISSING    — names of value-taking flags with no value

# ---------------------------------------------------------------------------
# Include guard: prevent double-loading in the same shell process.
# ---------------------------------------------------------------------------
if [[ -n "${_ARGPARSE_SH_LOADED:-}" ]]; then
    return 0
fi
_ARGPARSE_SH_LOADED=1

# ---------------------------------------------------------------------------
# Module-level output arrays.
# Declared here so they exist even before the first argparse_parse call.
# ---------------------------------------------------------------------------
declare -gA ARGPARSE_FLAGS=()
declare -ga ARGPARSE_POSITIONAL=()
declare -ga ARGPARSE_MISSING=()

# ---------------------------------------------------------------------------
# _ARGPARSE_VALUE_FLAGS — module-level associative array that holds the set of
# value-taking flag names for the current parse.  Stored at module scope so
# helper functions can consult it without nameref collisions.
# ---------------------------------------------------------------------------
declare -gA _ARGPARSE_VALUE_FLAGS=()

# ---------------------------------------------------------------------------
# argparse_reset
# Clear all three output arrays.  Call before re-parsing if you need to
# re-use argparse_parse in a long-running script that processes multiple
# argv sets.
# ---------------------------------------------------------------------------
argparse_reset() {
    ARGPARSE_FLAGS=()
    ARGPARSE_POSITIONAL=()
    ARGPARSE_MISSING=()
    _ARGPARSE_VALUE_FLAGS=()
}

# ---------------------------------------------------------------------------
# argparse_parse [--value-flags "flag1 flag2 ..."] [--] "$@"
#
# Parses the supplied arguments.  Populates ARGPARSE_FLAGS, ARGPARSE_POSITIONAL,
# and ARGPARSE_MISSING.
#
# --value-flags "..."  is optional.  When omitted, EVERY --flag is treated as a
# boolean (ARGPARSE_FLAGS[flag]=1).  Pass an explicit empty string to declare
# "no flags take values."
#
# The -- separator between the library's own options and the caller's argv is
# optional but recommended for clarity:
#
#   argparse_parse --value-flags "dev-tree color" -- "$@"
#   argparse_parse -- "$@"          # no value-taking flags
#   argparse_parse "$@"             # no value-taking flags, no separator
# ---------------------------------------------------------------------------
argparse_parse() {
    # --- Step 1: extract our own --value-flags option from the front of "$@" ---
    # Populate _ARGPARSE_VALUE_FLAGS for O(1) lookup by later helpers.
    _ARGPARSE_VALUE_FLAGS=()

    # Consume argparse_parse's own leading options.
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --value-flags)
                shift
                # "$1" is now the space-separated list of flag names.
                local _vf_entry
                for _vf_entry in $1; do
                    _ARGPARSE_VALUE_FLAGS["${_vf_entry}"]=1
                done
                shift
                ;;
            --value-flags=*)
                local _vf_list="${1#--value-flags=}"
                local _vf_entry
                for _vf_entry in $_vf_list; do
                    _ARGPARSE_VALUE_FLAGS["${_vf_entry}"]=1
                done
                shift
                ;;
            --)
                # End of argparse_parse's own options; caller's argv follows.
                shift
                break
                ;;
            *)
                # First non-option token: treat as start of caller's argv.
                break
                ;;
        esac
    done

    # --- Step 2: parse the caller's argv ---
    local _flags_done=0

    while [[ $# -gt 0 ]]; do
        local _arg="$1"
        shift

        # -- signals end of flags; rest are positional.
        if [[ "$_arg" == "--" ]] && [[ $_flags_done -eq 0 ]]; then
            _flags_done=1
            continue
        fi

        # After --, or if the token does not start with --, treat as positional.
        if [[ $_flags_done -eq 1 ]] || [[ "$_arg" != --* ]]; then
            ARGPARSE_POSITIONAL+=("$_arg")
            continue
        fi

        # Strip the leading "--".
        local _raw="${_arg#--}"

        # Determine flag name and value.
        if [[ "$_raw" == *=* ]]; then
            # --flag=value form: split on FIRST = only.
            local _name="${_raw%%=*}"
            local _value="${_raw#*=}"
            ARGPARSE_FLAGS["${_name}"]="${_value}"
        else
            # --flag  form: check if this flag takes a value.
            local _name="${_raw}"
            if [[ -v "_ARGPARSE_VALUE_FLAGS[${_name}]" ]]; then
                # This flag expects a value.  Peek at the next token.
                if [[ $# -gt 0 ]] && [[ "$1" != --* ]]; then
                    # Next token exists and is not a flag: consume as value.
                    ARGPARSE_FLAGS["${_name}"]="$1"
                    shift
                else
                    # No value available (end of argv, or next token is a flag).
                    ARGPARSE_MISSING+=("${_name}")
                fi
            else
                # Boolean / standalone flag: record presence as "1".
                ARGPARSE_FLAGS["${_name}"]=1
            fi
        fi
    done
}

# ---------------------------------------------------------------------------
# argparse_has FLAG
# Returns 0 (true) if FLAG appears in ARGPARSE_FLAGS (value or boolean).
# Returns 1 otherwise.
# ---------------------------------------------------------------------------
argparse_has() {
    local _flag="$1"
    [[ -v "ARGPARSE_FLAGS[${_flag}]" ]]
}

# ---------------------------------------------------------------------------
# argparse_missing FLAG
# Returns 0 (true) if FLAG is in ARGPARSE_MISSING (value-taking flag with
# no value supplied in argv).  Returns 1 otherwise.
# ---------------------------------------------------------------------------
argparse_missing() {
    local _flag="$1"
    local _entry
    for _entry in "${ARGPARSE_MISSING[@]+"${ARGPARSE_MISSING[@]}"}"; do
        [[ "$_entry" == "$_flag" ]] && return 0
    done
    return 1
}
