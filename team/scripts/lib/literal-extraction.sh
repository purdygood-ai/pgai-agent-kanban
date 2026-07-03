#!/usr/bin/env bash
# team/scripts/lib/literal-extraction.sh
#
# Helper library: extract changed string/integer literals from a git diff and
# grep test files for potential stale assertion matches.
#
# Source this file to get the literal_* functions in your shell:
#   source "$(dirname "${BASH_SOURCE[0]}")/literal-extraction.sh"
#
# This library is designed to be unit-testable in isolation.  It has no
# top-level side effects — only function definitions.
#
# Functions
# ---------
#   literal_extract_from_diff  DIFF_TEXT  — echo changed literals, one per line
#   literal_is_noise            LITERAL    — returns 0 (true) if literal is a
#                                            false-positive candidate (filter it)
#   literal_grep_tests          LITERAL  TEST_DIRS... — grep test dirs for literal
#                                            in assertion contexts; echo matches
#   literal_format_risks        LITERAL MATCHES — format risk entry as markdown lines
#
# False-positive filters (literal_is_noise returns true for):
#   - Empty strings or strings of all whitespace
#   - Numeric literals with fewer than 4 digits (0, 1, 99, 100, 999)
#   - String literals with content shorter than 4 characters
#   - The boolean/null tokens: true, false, null, None, True, False
#
# Literal extraction rules:
#   - Inspects BOTH added (+) and removed (-) lines in the diff to detect old
#     values that tests may still be asserting on.
#   - Skips diff header lines (+++ and --- which mark file boundaries)
#   - Extracts double-quoted strings: "..."  (non-empty, content >= 4 chars)
#   - Extracts single-quoted strings: '...'  (non-empty, content >= 4 chars)
#   - Extracts integer constants with 4+ digits
#   - Does NOT extract variables, function calls, or regex patterns
#   - Deduplicates across added and removed lines
#
# Stale literal detection rationale:
#   A stale assertion happens when production code changes a value (e.g., a
#   version string from "0.0.1" to "0.0.2") but a test file still asserts the
#   old value.  To catch this, the script extracts literals from REMOVED lines
#   (old values) in the diff, then greps test files for those old values
#   appearing in assertion contexts.  Literals from ADDED lines are also
#   extracted in case tests have been written ahead of production changes.

set -euo pipefail

# ---------------------------------------------------------------------------
# _literal_strip_quotes VALUE
# Internal: strip surrounding single or double quotes from a value.
# ---------------------------------------------------------------------------
_literal_strip_quotes() {
    local val="$1"
    # Strip surrounding double quotes
    if [[ "$val" == '"'*'"' ]]; then
        val="${val#\"}"
        val="${val%\"}"
    # Strip surrounding single quotes
    elif [[ "$val" == "'"*"'" ]]; then
        val="${val#\'}"
        val="${val%\'}"
    fi
    echo "$val"
}

# ---------------------------------------------------------------------------
# literal_is_noise LITERAL
# Return 0 (true) if the literal should be filtered out as a false positive.
# Return 1 (false) if the literal is a candidate worth grepping for.
#
# Filters:
#   - Empty or whitespace-only
#   - Noise boolean/null tokens (exact match, case-sensitive)
#   - Pure numeric literals with fewer than 4 digits
#   - String content shorter than 4 characters
# ---------------------------------------------------------------------------
literal_is_noise() {
    local raw="$1"

    # Empty
    [[ -z "$raw" ]] && return 0

    # Strip quotes to inspect the content
    local content
    content="$(_literal_strip_quotes "$raw")"

    # Empty content (empty string literal "")
    [[ -z "$content" ]] && return 0

    # Whitespace-only content
    [[ "$content" =~ ^[[:space:]]+$ ]] && return 0

    # Boolean/null noise tokens (case-sensitive exact match)
    case "$content" in
        true|false|null|None|True|False) return 0 ;;
    esac

    # Pure numeric literal: fewer than 4 digits → noise
    if [[ "$content" =~ ^[0-9]+$ ]]; then
        [[ ${#content} -lt 4 ]] && return 0
    fi

    # String literal with content shorter than 4 characters → noise
    if [[ ${#content} -lt 4 ]]; then
        return 0
    fi

    return 1
}

# ---------------------------------------------------------------------------
# _literal_extract_from_lines LINE_PREFIX DIFF_TEXT
# Internal helper: extract literals from diff lines starting with LINE_PREFIX.
# LINE_PREFIX is '+' for added lines or '-' for removed lines.
# Outputs unique literals (with surrounding quotes), one per line.
# ---------------------------------------------------------------------------
_literal_extract_from_lines() {
    local line_prefix="$1"
    local diff_text="$2"

    # Determine the first character of lines to process and the header to skip.
    # We use string comparison (not regex) to avoid ERE metacharacter issues
    # with '+' and '-' in [[ =~ ]] patterns.
    local first_char="$line_prefix"

    # Header lines to skip: '+++' for added context, '---' for removed context
    local header_triple
    if [[ "$first_char" == "+" ]]; then
        header_triple="+++"
    else
        header_triple="---"
    fi

    declare -A seen_locals

    local line
    while IFS= read -r line; do
        # Skip diff header lines (+++/--- file boundary markers)
        [[ "${line:0:3}" == "$header_triple" ]] && continue
        # Only process lines starting with the requested prefix character
        [[ "${line:0:1}" == "$first_char" ]] || continue

        # Strip the leading prefix character
        local stripped="${line:1}"

        # Extract double-quoted string literals
        local dq_matches sq_matches int_matches
        dq_matches="$(printf '%s\n' "$stripped" | grep -oE '"[^"]*"' 2>/dev/null || true)"
        sq_matches="$(printf '%s\n' "$stripped" | grep -oE "'[^']+'" 2>/dev/null || true)"
        int_matches="$(printf '%s\n' "$stripped" | grep -oE '\b[0-9]{4,}\b' 2>/dev/null || true)"

        local match
        while IFS= read -r match; do
            [[ -z "$match" ]] && continue
            if ! literal_is_noise "$match"; then
                seen_locals["$match"]=1
            fi
        done <<< "$dq_matches"

        while IFS= read -r match; do
            [[ -z "$match" ]] && continue
            if ! literal_is_noise "$match"; then
                seen_locals["$match"]=1
            fi
        done <<< "$sq_matches"

        while IFS= read -r match; do
            [[ -z "$match" ]] && continue
            if ! literal_is_noise "$match"; then
                seen_locals["$match"]=1
            fi
        done <<< "$int_matches"
    done <<< "$diff_text"

    local lit
    for lit in "${!seen_locals[@]}"; do
        echo "$lit"
    done
}

# ---------------------------------------------------------------------------
# literal_extract_from_diff DIFF_TEXT
# Parse added/changed lines from a git diff and emit one literal per line.
# Literals are raw values including quotes (e.g., "0.0.2" or '0.0.2' or 1234).
#
# Input:  full text of a git diff (from git diff or a file), passed as argument
# Output: unique literals, one per line, with surrounding quotes preserved
#
# Extracts from BOTH removed (old) and added (new) lines.  Old-value literals
# are the primary stale-detection signal — a test asserting an old removed
# value is definitively stale.  New-value literals catch tests written against
# expectations that may not yet exist in production.
# ---------------------------------------------------------------------------
literal_extract_from_diff() {
    local diff_text="$1"

    declare -A all_literals

    # Extract from removed lines (old values — primary stale-detection signal)
    local removed_lit
    while IFS= read -r removed_lit; do
        [[ -z "$removed_lit" ]] && continue
        all_literals["$removed_lit"]=1
    done < <(_literal_extract_from_lines "-" "$diff_text")

    # Extract from added lines (new values — catches ahead-of-production tests)
    local added_lit
    while IFS= read -r added_lit; do
        [[ -z "$added_lit" ]] && continue
        all_literals["$added_lit"]=1
    done < <(_literal_extract_from_lines "+" "$diff_text")

    # Emit unique literals
    local lit
    for lit in "${!all_literals[@]}"; do
        echo "$lit"
    done
}

# ---------------------------------------------------------------------------
# literal_grep_tests LITERAL TEST_DIR [TEST_DIR...]
# Search test directories for LITERAL appearing in assertion contexts.
#
# Assertion context patterns searched (ERE):
#   ==, !=, assert, expected, assertEqual, assertIn, assertRegex, in
#   followed by the literal content (with or without quotes)
#
# Output: matching lines in grep format (file:line:content), one per line.
#         Empty output when no matches are found.
#
# LITERAL is the raw value with surrounding quotes preserved.
# The function searches for the CONTENT (without quotes) in assertion lines.
# ---------------------------------------------------------------------------
literal_grep_tests() {
    local raw_literal="$1"
    shift
    local test_dirs=("$@")

    # Strip quotes to get content for grepping
    local content
    content="$(_literal_strip_quotes "$raw_literal")"

    # Skip empty content
    [[ -z "$content" ]] && return 0

    # Escape special regex characters in the content for safe embedding in ERE
    local escaped_content
    escaped_content="$(printf '%s' "$content" | sed 's/[.[\*^$(){}|+?]/\\&/g')"

    local results=""

    local test_dir
    for test_dir in "${test_dirs[@]}"; do
        [[ -d "$test_dir" ]] || continue

        # Pattern: assertion keyword on the same line as the literal content
        # Search in quotes ("content" or 'content') or bare (for numeric)
        local hits
        hits="$(grep -rn --include="*.py" --include="*.sh" --include="*.bats" \
            -E "(==|!=|assert[A-Za-z]*|expected)[^#]*(\"${escaped_content}\"|'${escaped_content}')" \
            "$test_dir" 2>/dev/null || true)"

        if [[ -n "$hits" ]]; then
            results="${results}${hits}"$'\n'
        fi
    done

    # Emit results (strip trailing blank line)
    printf '%s' "$results" | grep -v '^$' || true
}

# ---------------------------------------------------------------------------
# literal_format_risks LITERAL MATCHES
# Format a single risk entry for the markdown output.
#
# Arguments:
#   LITERAL  — the raw literal value (with quotes)
#   MATCHES  — newline-separated grep output (file:line:content)
#
# Output: formatted markdown lines for this risk entry (to stdout)
# ---------------------------------------------------------------------------
literal_format_risks() {
    local literal="$1"
    local matches="$2"

    [[ -z "$matches" ]] && return 0

    echo "- literal \`${literal}\` may be stale:"
    local line
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        # Extract file:lineno from grep output (format is file:lineno:content)
        local file_lineno content_part
        file_lineno="$(echo "$line" | cut -d: -f1-2)"
        content_part="$(echo "$line" | cut -d: -f3-)"
        echo "  - \`${file_lineno}\`: ${content_part}"
    done <<< "$matches"
}
