#!/usr/bin/env bash
# scripts/coder-stale-literal-check.sh
#
# CODER pre-flight: extract literals changed in production code within a git
# diff range and grep test files for those literals — both as assertion
# targets in test source, and as bare occurrences in recorded-output fixture
# directories (captured board renders, wake logs, ship summaries, and other
# goldens, which hold no assertion syntax to anchor on).  Emits a
# markdown-formatted ## Stale Literal Risks section that a CODER agent can
# paste directly into status.md.
#
# Usage:
#   coder-stale-literal-check.sh --diff <range> --project <name>
#                                [--findings-file <path>] [--help]
#
# Options:
#   --diff <range>     Git diff range to inspect (e.g., rc/v0.24.9..HEAD,
#                      main..HEAD, SHA1..SHA2).  Required.
#   --project <name>   Project name as registered in projects.cfg.  Used to
#                      locate test directories relative to the project root.
#                      Required.
#   --findings-file <path>
#                      Also write one finding per stale-literal hit as a JSON
#                      array to <path> (written even when empty): lint
#                      "coder_stale_literal", severity "quality", summary,
#                      cause, remedy, kind "stale-literal" with the literal,
#                      file and line -- or one "check-not-run" finding when
#                      the check could not run. The gated runners'
#                      gated_runner_merge_findings_file reads this shape.
#   --help             Print this usage text and exit 0.
#
# Exit codes:
#   0  — whenever the arguments parse: risks found or not, and also when the
#        check could not run (a missing helper library, a working directory
#        outside a git repository, or a --diff range git cannot resolve), in
#        which case the section reads "(not checked — <reason>)" instead of
#        "(none ...)"; a range that resolves to no production change is
#        "(none ...)"
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
#   - Generated and frozen artifacts are excluded the same way: the frozen
#     ICD baseline snapshots under docs/api/baselines/ (an icd-v<version>.json
#     of thousands of generated lines is frozen on every ICD bump, and its
#     strings are not literals a person wrote).  The regenerated
#     docs/api/icd.json stays in scope: its diff per bump is a few lines and
#     carries the quoted ICD version string tests pin.
#   - False-positive filters: numeric literals < 4 digits, empty strings,
#     strings < 4 chars, and the tokens: true false null None True False.
#   - Both added (+) and removed (-) lines in the diff are inspected, as
#     literal_extract_from_diff in literal-extraction.sh does: removed lines
#     catch old values a test may still pin, added lines catch tests written
#     ahead of the production change.  A literal the diff only removes is
#     still reported.
#   - The script sources literal-extraction.sh for the parsing helpers,
#     keeping the helper independently unit-testable.
#   - Two distinct search surfaces run per literal, side by side, and report
#     into the same section: literal_grep_tests (assertion-context matches
#     in test source) and literal_grep_recorded_output (bare matches in
#     recorded-output fixture directories — engine-baseline and golden
#     captures, selected explicitly by location under each test directory,
#     never by guessing at file content). A recorded golden carries no
#     assertion syntax, so only the second search can ever see it.

set -euo pipefail

# ---------------------------------------------------------------------------
# Resolve script directory and source the helper library
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_DIR="${SCRIPT_DIR}/lib"
_CSL_SELF="${SCRIPT_DIR}/coder-stale-literal-check.sh"

# shellcheck source=lib/script_common.sh
source "${LIB_DIR}/script_common.sh"
# shellcheck source=lib/error_contract.sh
source "${LIB_DIR}/error_contract.sh"
# Per-instance findings: gated_runner_record_instance_finding accumulates,
# gated_runner_write_findings_array writes the --findings-file array.
# shellcheck source=lib/gated_runner_lint.sh
source "${LIB_DIR}/gated_runner_lint.sh"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
# Usage text as data (per lib/script_common.sh's print_usage_and_exit contract).
_USAGE_TEXT="$(cat <<'EOF'
Usage: coder-stale-literal-check.sh --diff <range> --project <name>
                                    [--findings-file <path>] [--help]

Extract literals changed in production code within a git diff range and grep
test files for those literals — both as assertion targets in test source and
as bare occurrences in recorded-output fixture directories (captured board
renders, wake logs, ship summaries, and other goldens, which hold no
assertion syntax to anchor on).  Emits a markdown-formatted
## Stale Literal Risks section for pasting into status.md.

Options:
  --diff <range>     Git diff range to inspect.
                     Examples: rc/v0.24.9..HEAD  main..HEAD  SHA1..SHA2
                     Required.

  --project <name>   Project name as registered in projects.cfg.  Used to
                     determine which test directories to grep.  Required.

  --findings-file <path>
                     Also write one finding per stale-literal hit (or one
                     "check-not-run" finding) as a JSON array to <path>,
                     in the shape the gated runners merge.

  --help             Print this usage text and exit 0.

Exit codes:
  0  Advisory-only; exit 0 whether risks are found or not, and also when
     the check could not run (missing helper library, not inside a git
     repository, or a --diff range git cannot resolve) -- the section then
     reads "(not checked — <reason>)" with one "check-not-run" finding.
     A range that resolves to no production change reads "(none ...)".
  1  Missing required argument (--diff or --project) or unknown argument.

False-positive filters (automatically excluded):
  - Numeric literals with fewer than 4 digits (0, 1, 99, 100, 999)
  - String literals shorter than 4 characters
  - The tokens: true, false, null, None, True, False
  - Empty strings

Production-code detection:
  Files matching tests/, test_*.py, *_test.py, *.test.* are excluded from
  literal extraction via git pathspec exclusions.  Generated and frozen
  artifacts are excluded the same way: the frozen ICD baseline snapshots
  under docs/api/baselines/.  The regenerated docs/api/icd.json stays in
  scope.

Search surfaces (both run per literal, reported together):
  - Test source, constrained to assertion contexts (==, !=, assert,
    expected, assertEqual, assertIn, assertRegex, in).
  - Recorded-output fixture directories (fixtures/engine-baseline,
    fixtures/golden under each test directory) — bare occurrences, no
    assertion-context constraint, since a captured golden carries no
    assertion syntax to anchor on.
EOF
)"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
DIFF_RANGE=""
PROJECT_NAME=""
FINDINGS_FILE=""

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
        --findings-file)
            if [[ $# -lt 2 || -z "$2" ]]; then
                echo "ERROR: --findings-file requires a non-empty argument" >&2
                exit 1
            fi
            FINDINGS_FILE="$2"
            shift 2
            ;;
        --help|-h)
            print_usage_and_exit 0 "$_USAGE_TEXT"
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
# Findings output
# ---------------------------------------------------------------------------
# _csl_write_findings: write the --findings-file array (no-op without it).
_csl_write_findings() {
    if [[ -n "$FINDINGS_FILE" ]]; then
        gated_runner_write_findings_array "$FINDINGS_FILE"
    fi
}

# The git work tree this script sits in (its dev tree), or -- for an
# installed copy outside any repository -- the dev tree the environment
# names, else $HOME: where a "not inside a git repository" re-run starts.
_CSL_SELF_TOPLEVEL="$(git -C "${SCRIPT_DIR}" rev-parse --show-toplevel 2>/dev/null || true)"
_CSL_RERUN_DIR="${_CSL_SELF_TOPLEVEL:-${PGAI_DEV_TREE_PATH:-${HOME}}}"

# _csl_rerun_command [dir]: this check re-run from a git work tree (default
# _CSL_RERUN_DIR).
_csl_rerun_command() {
    printf 'cd %q && bash %q --diff %q --project %q' \
        "${1:-${_CSL_RERUN_DIR}}" "${_CSL_SELF}" "${DIFF_RANGE}" "${PROJECT_NAME}"
}

# _csl_not_run <reason> <remedy>: print the section saying the check did
# not run, so a pasted section never reads as "(none ...)", and record it
# as a finding.
_csl_not_run() {
    printf '## Stale Literal Risks\n\n(not checked — %s)\n' "$1"
    gated_runner_record_instance_finding coder_stale_literal \
        "stale-literal check did not run for diff ${DIFF_RANGE}: $1" "$2" \
        "kind=check-not-run"
    _csl_write_findings
}

# ---------------------------------------------------------------------------
# Verify the helper library and the git repository
# ---------------------------------------------------------------------------
if [[ ! -f "${LIB_DIR}/literal-extraction.sh" ]]; then
    # Restore it from git where this copy is a checkout; otherwise list
    # what the library directory does hold.
    _csl_lib_remedy="ls -l $(printf '%q' "${LIB_DIR}")"
    if [[ -n "${_CSL_SELF_TOPLEVEL}" ]]; then
        _csl_lib_remedy="git -C $(printf '%q' "${LIB_DIR}") checkout HEAD -- literal-extraction.sh"
    fi
    _csl_not_run "helper library not found: ${LIB_DIR}/literal-extraction.sh" "${_csl_lib_remedy}"
    # CENSUS-ANCHOR: coder-stale-literal-check-lib-missing-01
    # CONVERT: precondition for this quality-class check -- a missing helper
    # library means the scan cannot run, not that the tree has a defect.
    pgai_emit_error_contract "coder_stale_literal_check.lib_missing" \
        "helper library not found: ${LIB_DIR}/literal-extraction.sh; no literal was checked." \
        "${_csl_lib_remedy}"
    exit 0
fi

# shellcheck source=lib/literal-extraction.sh
source "${LIB_DIR}/literal-extraction.sh"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    _csl_not_run "${PWD} is not inside a git repository; run from the project dev tree" \
        "$(_csl_rerun_command)"
    # CENSUS-ANCHOR: coder-stale-literal-check-not-a-repo-01
    # CONVERT: precondition for this quality-class check -- outside a git
    # repo there is no diff range to extract literals from.
    pgai_emit_error_contract "coder_stale_literal_check.not_a_repo" \
        "${PWD} is not inside a git repository, so there is no diff to check; no literal was checked." \
        "$(_csl_rerun_command)"
    exit 0
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
# Determine recorded-output fixture directories to scan — a search surface
# distinct from TEST_DIRS above (see literal-extraction.sh's
# literal_grep_recorded_output for why a golden needs its own search).
# ---------------------------------------------------------------------------
# Recorded-output fixtures are selected by location under each test
# directory, explicitly, rather than by guessing at file content:
# "fixtures/engine-baseline" and "fixtures/golden" are the two committed
# recorded-output conventions already in use in this tree (the same
# location convention lint_test_anti_patterns.py's golden-comparison check
# recognizes as `_GOLDEN_DIR`/`_FIXTURE_DIR`).
CANDIDATE_RECORDED_OUTPUT_SUBDIRS=(
    "fixtures/engine-baseline"
    "fixtures/golden"
)

RECORDED_OUTPUT_DIRS=()
for test_dir in "${TEST_DIRS[@]}"; do
    for subdir in "${CANDIDATE_RECORDED_OUTPUT_SUBDIRS[@]}"; do
        full_path="${test_dir}/${subdir}"
        if [[ -d "$full_path" ]]; then
            RECORDED_OUTPUT_DIRS+=("$full_path")
        fi
    done
done

# ---------------------------------------------------------------------------
# Extract changed literals from production code in the diff
# ---------------------------------------------------------------------------
# Pathspec exclusions: skip test files/directories from literal extraction.
# We only want to flag literals that PRODUCTION code changed — test changes
# themselves are not interesting as stale-literal sources.
PRODUCTION_PATHSPEC=(
    ':!tests/'
    ':!team/tests/'
    ':!**/tests/'
    ':!test_*.py'
    ':!*test_*.py'
    ':!*_test.py'
    ':!*.test.js'
    ':!*.test.ts'
    ':!*.test.sh'
    ':!*.spec.js'
    ':!*.spec.ts'
)

# Generated and frozen artifacts: their strings are machine output, not
# literals a person wrote, and a frozen ICD baseline adds thousands of them
# per ICD bump -- enough to keep the per-line extraction running for many
# minutes.  `top` anchors each path at the repository root whatever the
# working directory, so the exclusion holds from a subdirectory too.
GENERATED_ARTIFACT_PATHSPEC=(
    ':(top,exclude)docs/api/baselines/'
)
DIFF_PATHSPEC=("${PRODUCTION_PATHSPEC[@]}" "${GENERATED_ARTIFACT_PATHSPEC[@]}")

# stderr is dropped on success (a rename-detection warning is not diff
# text); a failing git diff means the range did not resolve, which is a
# check that did not run -- never an empty diff.
DIFF_OUTPUT=""
if ! DIFF_OUTPUT="$(git diff "${DIFF_RANGE}" -- "${DIFF_PATHSPEC[@]}" 2>/dev/null)"; then
    # Re-run on this failure path only, to quote git's own first line.
    _csl_git_msg="$(git diff "${DIFF_RANGE}" -- "${DIFF_PATHSPEC[@]}" 2>&1 >/dev/null \
        | sed -n '/[^[:space:]]/{s/^[[:space:]]*\(fatal\|error\):[[:space:]]*//;p;q;}' || true)"
    _csl_git_msg="${_csl_git_msg:-git diff exited non-zero}"
    _csl_range_remedy="git -C $(printf '%q' "${REPO_ROOT}") branch --all --list"
    _csl_not_run "diff range ${DIFF_RANGE} does not resolve: ${_csl_git_msg}" "${_csl_range_remedy}"
    # CENSUS-ANCHOR: coder-stale-literal-check-bad-range-01
    # CONVERT: precondition for this quality-class check -- a range git
    # cannot resolve leaves no diff to scan; not a defect in the tree.
    pgai_emit_error_contract "coder_stale_literal_check.range_unresolved" \
        "diff range ${DIFF_RANGE} does not resolve (${_csl_git_msg}); no literal was checked." \
        "${_csl_range_remedy}"
    exit 0
fi

if [[ -z "$DIFF_OUTPUT" ]]; then
    # No diff in production files — nothing to check
    printf '## Stale Literal Risks\n\n(none — no test assertions match literals changed in this task'"'"'s production diff)\n'
    _csl_write_findings
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
    _csl_write_findings
    exit 0
fi

# ---------------------------------------------------------------------------
# Grep test files for each literal — assertion-context matches in test
# source, plus bare matches in recorded-output fixture directories.  The two
# searches are independent (see literal-extraction.sh header); their hits
# are merged only for reporting, under the same risk entry per literal.
# ---------------------------------------------------------------------------
RISKS_FOUND=0
RISKS_OUTPUT=""

if [[ ${#TEST_DIRS[@]} -gt 0 || ${#RECORDED_OUTPUT_DIRS[@]} -gt 0 ]]; then
    for lit in "${LITERALS[@]}"; do
        matches=""
        if [[ ${#TEST_DIRS[@]} -gt 0 ]]; then
            matches="$(literal_grep_tests "$lit" "${TEST_DIRS[@]}")"
        fi

        if [[ ${#RECORDED_OUTPUT_DIRS[@]} -gt 0 ]]; then
            recorded_matches="$(literal_grep_recorded_output "$lit" "${RECORDED_OUTPUT_DIRS[@]}")"
            if [[ -n "$recorded_matches" ]]; then
                if [[ -n "$matches" ]]; then
                    matches="${matches}"$'\n'"${recorded_matches}"
                else
                    matches="$recorded_matches"
                fi
            fi
        fi

        if [[ -n "$matches" ]]; then
            RISKS_FOUND=1
            risk_entry="$(literal_format_risks "$lit" "$matches")"
            RISKS_OUTPUT="${RISKS_OUTPUT}${risk_entry}"$'\n'
            # One finding per hit; grep output is file:lineno:content.
            while IFS= read -r match_line; do
                [[ -n "$match_line" ]] || continue
                hit_file="${match_line%%:*}"
                hit_rest="${match_line#*:}"
                hit_line="${hit_rest%%:*}"
                [[ "$hit_line" =~ ^[0-9]+$ ]] || hit_line=""
                gated_runner_record_instance_finding coder_stale_literal \
                    "literal ${lit} changed in the production diff ${DIFF_RANGE} still appears at ${hit_file}${hit_line:+:${hit_line}}" \
                    "update ${hit_file}${hit_line:+ line ${hit_line}} to the literal's new value if it asserts the old one, then re-check: $(_csl_rerun_command "${REPO_ROOT}")" \
                    "kind=stale-literal" "literal=${lit}" "file=${hit_file}" ${hit_line:+"line=${hit_line}"}
            done <<< "$matches"
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
_csl_write_findings
exit 0
