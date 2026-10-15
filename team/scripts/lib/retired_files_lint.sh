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
# With a second argument the lint also scans a live root: every manifest
# entry still present at <live-root> is reported as a stale file the next
# upgrade will retire (or that an upgrade failed to retire). The root scan is
# a hygiene report over an install; it never moves or deletes anything.
#
# Usage (standalone — callable from CI or the command line):
#   retired_files_lint.sh <manifest-file> [<live-root>]
#
# Usage (sourced — exposes the two functions for use from upgrade.sh or other
# scripts):
#   source "$(dirname "${BASH_SOURCE[0]}")/retired_files_lint.sh"
#   lint_retired_files_manifest <manifest-file>
#   lint_retired_files_present  <manifest-file> <live-root>
#
# Exit codes:
#   0   All manifest lines pass (and, with <live-root>, none is present there).
#   1   One or more manifest lines fail validation, or one or more manifest
#       entries are still present at <live-root> (details on stderr).
#   2   Usage error (missing argument, file or root not found).
#
# Examples:
#   retired_files_lint.sh team/templates/retired-files.txt   # exits 0
#   printf 'workflows/*.yaml\n' | ...                        # exits 1 (glob)
#   printf '/etc/kanban\n'      | ...                        # exits 1 (absolute)
#   printf '../sibling/f\n'     | ...                        # exits 1 (..)
#   retired_files_lint.sh "$KANBAN_ROOT/templates/retired-files.txt" "$KANBAN_ROOT"
#                                        # exits 1 while a listed file is still at the root

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
# lint_retired_files_present <manifest-file> <live-root>
#
# Report every data line of <manifest-file> that still resolves to a regular
# file under <live-root>. Such a file is one the framework no longer ships:
# either the install has not been upgraded since the entry was added, or an
# upgrade's retirement step did not fire. Prints one diagnostic to stderr
# per stale file and never touches the root.
# Returns 0 when no listed path is present, 1 when any is, 2 on usage error.
# ---------------------------------------------------------------------------
lint_retired_files_present() {
    local manifest_file="$1"
    local live_root="$2"
    local present=0
    local line

    if [[ -z "$manifest_file" || -z "$live_root" ]]; then
        printf 'retired_files_lint: manifest file and live root arguments required\n' >&2
        return 2
    fi

    if [[ ! -f "$manifest_file" ]]; then
        printf 'retired_files_lint: file not found: %s\n' "$manifest_file" >&2
        return 2
    fi

    if [[ ! -d "$live_root" ]]; then
        printf 'retired_files_lint: live root not found: %s\n' "$live_root" >&2
        return 2
    fi

    while IFS= read -r line || [[ -n "$line" ]]; do
        [[ -z "$line" ]]           && continue
        [[ "$line" == \#* ]]       && continue

        if [[ -f "${live_root}/${line}" ]]; then
            printf 'retired_files_lint: retired file still present at live root (the next upgrade moves it to retired/): %s\n' \
                "${live_root}/${line}" >&2
            (( present++ )) || true
        fi
    done < "$manifest_file"

    if [[ "$present" -gt 0 ]]; then
        printf 'retired_files_lint: %d retired file(s) still present under %s\n' "$present" "$live_root" >&2
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# Standalone entry point: run when this script is executed directly.
# When sourced, only the functions above are loaded — no side effects.
# ---------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    if [[ $# -lt 1 || $# -gt 2 ]]; then
        printf 'Usage: %s <manifest-file> [<live-root>]\n' "$(basename "$0")" >&2
        exit 2
    fi

    lint_retired_files_manifest "$1" || exit $?

    if [[ $# -eq 2 ]]; then
        lint_retired_files_present "$1" "$2"
        exit $?
    fi

    exit 0
fi
