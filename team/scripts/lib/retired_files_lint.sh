#!/usr/bin/env bash
# team/scripts/lib/retired_files_lint.sh
# Hygiene lint for retired-files.txt manifest files.
#
# Validates that every non-blank, non-comment line in a manifest is an exact
# relative path suitable for use as a retirement manifest entry:
#   - No glob characters  (* ? [)
#   - Does not start with '/'  (no absolute paths)
#   - Contains no '..' segment  (no upward traversal)
#
# Blank lines and lines beginning with '#' are permitted and silently skipped.
#
# Usage (standalone — callable from CI or the command line):
#   retired_files_lint.sh <manifest-file>
#
# Usage (sourced — exposes lint_retired_files_manifest for use from upgrade.sh
# or other scripts):
#   source "$(dirname "${BASH_SOURCE[0]}")/retired_files_lint.sh"
#   lint_retired_files_manifest <manifest-file>
#
# Exit codes:
#   0   All manifest lines pass.
#   1   One or more manifest lines fail validation (error details on stderr).
#   2   Usage error (missing argument, file not found).
#
# Examples:
#   retired_files_lint.sh team/templates/retired-files.txt   # exits 0
#   printf 'workflows/*.yaml\n' | ...                        # exits 1 (glob)
#   printf '/etc/kanban\n'      | ...                        # exits 1 (absolute)
#   printf '../sibling/f\n'     | ...                        # exits 1 (..)

# ---------------------------------------------------------------------------
# lint_retired_files_manifest <manifest-file>
#
# Validate every data line (non-blank, non-comment) in <manifest-file>.
# Prints one diagnostic to stderr per violation.
# Returns 0 when all lines pass, 1 when any violation is found.
# ---------------------------------------------------------------------------
lint_retired_files_manifest() {
    local manifest_file="$1"
    local errors=0
    local lineno=0
    local line

    if [[ -z "$manifest_file" ]]; then
        printf 'retired_files_lint: manifest file argument required\n' >&2
        return 2
    fi

    if [[ ! -f "$manifest_file" ]]; then
        printf 'retired_files_lint: file not found: %s\n' "$manifest_file" >&2
        return 2
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        (( lineno++ )) || true

        # Skip blank lines and comment lines.
        [[ -z "$line" ]]           && continue
        [[ "$line" == \#* ]]       && continue

        # Rule 1: no glob characters.
        if [[ "$line" == *'*'* || "$line" == *'?'* || "$line" == *'['* ]]; then
            printf 'retired_files_lint: line %d: glob character in manifest entry (entries must be exact paths): %s\n' \
                "$lineno" "$line" >&2
            (( errors++ )) || true
            continue
        fi

        # Rule 2: must not start with '/' (no absolute paths).
        if [[ "$line" == /* ]]; then
            printf 'retired_files_lint: line %d: absolute path not allowed (entries must be relative to the live root): %s\n' \
                "$lineno" "$line" >&2
            (( errors++ )) || true
            continue
        fi

        # Rule 3: no '..' path segment (no upward traversal).
        if [[ "$line" == '..'* || "$line" == *'/..'* || "$line" == *'/../'* ]]; then
            printf 'retired_files_lint: line %d: ".." segment not allowed in manifest entry: %s\n' \
                "$lineno" "$line" >&2
            (( errors++ )) || true
            continue
        fi
    done < "$manifest_file"

    if [[ "$errors" -gt 0 ]]; then
        printf 'retired_files_lint: %d violation(s) found in %s\n' "$errors" "$manifest_file" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Standalone entry point: run when this script is executed directly.
# When sourced, only the function above is loaded — no side effects.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [[ $# -ne 1 ]]; then
        printf 'Usage: %s <manifest-file>\n' "$(basename "$0")" >&2
        exit 2
    fi

    lint_retired_files_manifest "$1"
    exit $?
fi
