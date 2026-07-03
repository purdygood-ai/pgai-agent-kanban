#!/usr/bin/env bash
# team/scripts/coder-stale-literal-check.sh
#
# CODER pre-flight: extract literals changed in production code within a git
# diff range and grep test files for those literals appearing as assertion
# targets.  Emits a markdown-formatted ## Stale Literal Risks section that a
# CODER agent can paste directly into status.md.
#
# Usage:
#   coder-stale-literal-check.sh --diff <range> --project <name> [--help]
#
# Options:
#   --diff <range>     Git diff range to inspect (e.g., rc/v0.24.9..HEAD,
#                      develop..HEAD, SHA1..SHA2).  Required.
#   --project <name>   Project name as registered in projects.cfg.  Used to
#                      locate test directories relative to the project root.
#                      Required.
#   --help             Print this usage text and exit 0.
#
# Exit codes:
#   0  — always (script is advisory/non-blocking; risks found or not)
#   1  — missing required argument or unexpected argument
#
# Output format:
#   ## Stale Literal Risks
#
#   (none — no test assertions match literals changed in this task's production diff)
#
#   --- OR ---
#
#   ## Stale Literal Risks
#
#   - literal `"0.0.1"` may be stale:
#     - `tests/test_health.py:14`: assert response.json() == {"version": "0.0.1"}
#
# Design notes:
#   - Production code detection excludes paths matching: tests/, test_*.py,
#     *_test.py, *.test.* via git pathspec exclusions.
#   - False-positive filters: numeric literals < 4 digits, empty strings,
#     strings < 4 chars, and the tokens: true false null None True False.
#   - Only ADDED lines in the diff are inspected (changed-to values, not
#     changed-from values), so old values don't generate spurious noise.
#   - The script sources literal-extraction.sh for the parsing helpers,
#     keeping the helper independently unit-testable.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory and source the helper library
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"

if [[ ! -f "${LIB_DIR}/literal-extraction.sh" ]]; then
    echo "ERROR: helper library not found: ${LIB_DIR}/literal-extraction.sh" >&2
    exit 1
fi

# shellcheck source=lib/literal-extraction.sh
source "${LIB_DIR}/literal-extraction.sh"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<'EOF'
Usage: coder-stale-literal-check.sh --diff <range> --project <name> [--help]

Extract literals changed in production code within a git diff range and grep
test files for those literals appearing as assertion targets.  Emits a
markdown-formatted ## Stale Literal Risks section for pasting into status.md.

Options:
  --diff <range>     Git diff range to inspect.
                     Examples: rc/v0.24.9..HEAD  develop..HEAD  SHA1..SHA2
                     Required.

  --project <name>   Project name as registered in projects.cfg.  Used to
                     determine which test directories to grep.  Required.

  --help             Print this usage text and exit 0.

Exit codes:
  0  Advisory-only; exit 0 whether risks are found or not.
  1  Missing required argument (--diff or --project).

False-positive filters (automatically excluded):
  - Numeric literals with fewer than 4 digits (0, 1, 99, 100, 999)
  - String literals shorter than 4 characters
  - The tokens: true, false, null, None, True, False
  - Empty strings

Production-code detection:
  Files matching tests/, test_*.py, *_test.py, *.test.* are excluded from
  literal extraction via git pathspec exclusions.
EOF
}

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DIFF_RANGE=""
PROJECT_NAME=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --diff)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "ERROR: --diff requires a non-empty argument" >&2
                exit 1
            fi
            DIFF_RANGE="$2"
            shift 2
            ;;
        --project)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "ERROR: --project requires a non-empty argument" >&2
                exit 1
            fi
            PROJECT_NAME="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "$DIFF_RANGE" ]]; then
    echo "ERROR: --diff is required" >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

if [[ -z "$PROJECT_NAME" ]]; then
    echo "ERROR: --project is required" >&2
    echo "Run with --help for usage." >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Verify we are inside a git repository
# ---------------------------------------------------------------------------
if ! git rev-parse --git-dir >/dev/null 2>&1; then
    echo "ERROR: not inside a git repository; run from the project dev tree" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"

# ---------------------------------------------------------------------------
# Determine test directories to scan
# ---------------------------------------------------------------------------
# Standard test directories to probe (relative to repo root).
# The script probes all of these that exist; callers don't need to specify.
CANDIDATE_TEST_DIRS=(
    "team/tests"
    "tests"
    "test"
)

# For per-project context, also check under projects/<name>/tests
if [[ -n "$PROJECT_NAME" ]]; then
    CANDIDATE_TEST_DIRS+=("projects/${PROJECT_NAME}/tests")
fi

TEST_DIRS=()
for candidate in "${CANDIDATE_TEST_DIRS[@]}"; do
    full_path="${REPO_ROOT}/${candidate}"
    if [[ -d "$full_path" ]]; then
        TEST_DIRS+=("$full_path")
    fi
done

# ---------------------------------------------------------------------------
# Extract changed literals from production code in the diff
# ---------------------------------------------------------------------------
# Pathspec exclusions: skip test files/directories from literal extraction.
# We only want to flag literals that PRODUCTION code changed — test changes
# themselves are not interesting as stale-literal sources.
DIFF_OUTPUT=""
DIFF_OUTPUT="$(git diff "${DIFF_RANGE}" -- \
    ':!tests/' \
    ':!team/tests/' \
    ':!**/tests/' \
    ':!test_*.py' \
    ':!*test_*.py' \
    ':!*_test.py' \
    ':!*.test.js' \
    ':!*.test.ts' \
    ':!*.test.sh' \
    ':!*.spec.js' \
    ':!*.spec.ts' \
    2>/dev/null || true)"

if [[ -z "$DIFF_OUTPUT" ]]; then
    # No diff in production files — nothing to check
    printf '## Stale Literal Risks\n\n(none — no test assertions match literals changed in this task'"'"'s production diff)\n'
    exit 0
fi

# Extract unique literals from the diff
LITERALS=()
while IFS= read -r lit; do
    [[ -z "$lit" ]] && continue
    LITERALS+=("$lit")
done < <(literal_extract_from_diff "$DIFF_OUTPUT")

if [[ ${#LITERALS[@]} -eq 0 ]]; then
    printf '## Stale Literal Risks\n\n(none — no test assertions match literals changed in this task'"'"'s production diff)\n'
    exit 0
fi

# ---------------------------------------------------------------------------
# Grep test files for each literal in assertion contexts
# ---------------------------------------------------------------------------
RISKS_FOUND=0
RISKS_OUTPUT=""

if [[ ${#TEST_DIRS[@]} -gt 0 ]]; then
    for lit in "${LITERALS[@]}"; do
        matches="$(literal_grep_tests "$lit" "${TEST_DIRS[@]}")"
        if [[ -n "$matches" ]]; then
            RISKS_FOUND=1
            risk_entry="$(literal_format_risks "$lit" "$matches")"
            RISKS_OUTPUT="${RISKS_OUTPUT}${risk_entry}"$'\n'
        fi
    done
fi

# ---------------------------------------------------------------------------
# Emit markdown output
# ---------------------------------------------------------------------------
if [[ "$RISKS_FOUND" -eq 0 ]]; then
    printf '## Stale Literal Risks\n\n(none — no test assertions match literals changed in this task'"'"'s production diff)\n'
else
    printf '## Stale Literal Risks\n\n'
    printf '%s' "$RISKS_OUTPUT"
fi

# Advisory only — always exit 0
exit 0
