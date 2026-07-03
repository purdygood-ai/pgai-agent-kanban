#!/usr/bin/env bash
# team/scripts/lib/semver.sh
# Semantic version comparison helpers.
#
# Source this file to get the semver_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/semver.sh"
#
# All functions accept versions with or without a leading "v" prefix.
# Comparison is performed via sort -V (GNU version sort), which correctly
# handles cross-decade components (e.g. v0.9.x < v0.10.x).
#
# Functions
# ---------
#   semver_compare A B      — echoes -1, 0, or 1
#   semver_lt A B           — returns 0 (true) if A < B
#   semver_lte A B          — returns 0 (true) if A <= B
#   semver_gt A B           — returns 0 (true) if A > B
#   semver_gte A B          — returns 0 (true) if A >= B
#   semver_eq A B           — returns 0 (true) if A == B
#   semver_from_filename F  — echoes first v\d+\.\d+\.\d+ match from F,
#                             or empty string if none found

# ---------------------------------------------------------------------------
# Internal: normalise a version string so both A and B are comparable by
# sort -V.  Strips an optional leading "v" then re-adds it so that versions
# with and without the prefix sort identically (sort -V handles "v" well, but
# mixing "0.9.0" and "v0.9.0" can confuse it on some implementations).
# ---------------------------------------------------------------------------
_semver_normalise() {
    local v="$1"
    # Strip leading 'v' or 'V'
    v="${v#v}"
    v="${v#V}"
    echo "v${v}"
}

# ---------------------------------------------------------------------------
# semver_compare A B
# Echoes -1 if A < B, 0 if A == B, 1 if A > B.
# ---------------------------------------------------------------------------
semver_compare() {
    local a b lowest
    a="$(_semver_normalise "$1")"
    b="$(_semver_normalise "$2")"

    if [[ "$a" == "$b" ]]; then
        echo 0
        return 0
    fi

    # sort -V outputs the lesser version first.
    lowest="$(printf '%s\n%s\n' "$a" "$b" | sort -V | head -n1)"

    if [[ "$lowest" == "$a" ]]; then
        echo -1
    else
        echo 1
    fi
}

# ---------------------------------------------------------------------------
# semver_lt A B — true if A < B
# ---------------------------------------------------------------------------
semver_lt() {
    local result
    result="$(semver_compare "$1" "$2")"
    [[ "$result" == "-1" ]]
}

# ---------------------------------------------------------------------------
# semver_lte A B — true if A <= B
# ---------------------------------------------------------------------------
semver_lte() {
    local result
    result="$(semver_compare "$1" "$2")"
    [[ "$result" == "-1" || "$result" == "0" ]]
}

# ---------------------------------------------------------------------------
# semver_gt A B — true if A > B
# ---------------------------------------------------------------------------
semver_gt() {
    local result
    result="$(semver_compare "$1" "$2")"
    [[ "$result" == "1" ]]
}

# ---------------------------------------------------------------------------
# semver_gte A B — true if A >= B
# ---------------------------------------------------------------------------
semver_gte() {
    local result
    result="$(semver_compare "$1" "$2")"
    [[ "$result" == "1" || "$result" == "0" ]]
}

# ---------------------------------------------------------------------------
# semver_eq A B — true if A == B
# ---------------------------------------------------------------------------
semver_eq() {
    local result
    result="$(semver_compare "$1" "$2")"
    [[ "$result" == "0" ]]
}

# ---------------------------------------------------------------------------
# semver_from_filename FILENAME
# Echoes the first v\d+\.\d+\.\d+ token found in FILENAME (basename only),
# or an empty string if no match is found.
#
# Examples:
#   semver_from_filename "v0.17.0-bugfix-cascade.md"  -> "v0.17.0"
#   semver_from_filename "no-version-here.md"         -> ""
# ---------------------------------------------------------------------------
semver_from_filename() {
    local filename
    # Use only the basename so directory components don't confuse the pattern.
    filename="$(basename "$1")"

    # Use grep -oE to extract the first match of the pattern.
    # -o: print only the matching part; -E: extended regex; -m1: stop after first.
    local match
    match="$(printf '%s' "$filename" | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    echo "$match"
}
