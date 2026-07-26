#!/usr/bin/env bash
# team/scripts/lint_skip_bug_gate.sh
#
# Grep-gated enforcement: every skip()/SKIP: annotation in team/tests/ that
# cites a follow-up bug must reference a real BUG-NNNN whose file exists in
# the project's bugs/ directory.
#
# Two failure modes detected:
#   1. Placeholder skip IDs — BUG-SKIP-* or any non-BUG-NNNN pattern cited
#      in a skip annotation (grep pattern: BUG-SKIP or skip.*BUG-[A-Za-z]).
#   2. Missing bug file — a BUG-NNNN ID is cited but the corresponding file
#      (BUG-NNNN-*.md) does not exist in the bugs/ directory.
#
# Usage:
#   lint_skip_bug_gate.sh [--tests-dir PATH] [--bugs-dir PATH] [--verbose]
#
#   --tests-dir PATH   Directory to scan for skip annotations.
#                      Default: team/tests/ relative to this script's repo root.
#   --bugs-dir PATH    Directory containing BUG-NNNN-*.md files.
#                      Default: $PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-agent-kanban/bugs/
#                      If the directory does not exist, the file-existence check
#                      is skipped with a warning (exit 0).
#   --verbose          Print each finding and each scanned file.
#
# Exit codes:
#   0   All checks passed (or bugs-dir absent — see above).
#   1   One or more findings: placeholder IDs or missing bug files.
#   2   Usage error or required directory not found.
#
# This script IS the "grep-gated check" for skip-citation policy. It is
# called by run-unit-tests.sh and run-integration-tests.sh before pytest so
# that a violation surfaces immediately — before the full test suite runs.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Locate the repo root relative to this script.
# ---------------------------------------------------------------------------
_LSG_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_LSG_REPO_ROOT="$(cd "${_LSG_SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_LSG_TESTS_DIR="${_LSG_REPO_ROOT}/team/tests"
# Bugs dir: prefer PGAI_AGENT_KANBAN_ROOT_PATH if set; default to live install.
_LSG_KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
_LSG_BUGS_DIR="${_LSG_KANBAN_ROOT}/projects/pgai-agent-kanban/bugs"
_LSG_VERBOSE=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tests-dir)
            _LSG_TESTS_DIR="$2"; shift 2 ;;
        --tests-dir=*)
            _LSG_TESTS_DIR="${1#*=}"; shift ;;
        --bugs-dir)
            _LSG_BUGS_DIR="$2"; shift 2 ;;
        --bugs-dir=*)
            _LSG_BUGS_DIR="${1#*=}"; shift ;;
        --verbose|-v)
            _LSG_VERBOSE=true; shift ;;
        --help|-h)
            cat <<'HELP'
Usage: lint_skip_bug_gate.sh [--tests-dir PATH] [--bugs-dir PATH] [--verbose]

Enforce that every skip()/SKIP: annotation in the test suite that cites a
bug references a real BUG-NNNN whose file exists in the bugs/ directory.

Options:
  --tests-dir PATH   Test directory to scan (default: team/tests/)
  --bugs-dir PATH    Bugs directory to validate against (default: inferred
                     from PGAI_AGENT_KANBAN_ROOT_PATH)
  --verbose, -v      Print scan progress
  --help, -h         Show this help

Exit codes:
  0   All checks passed.
  1   One or more placeholder or missing-file findings.
  2   Usage error or tests-dir not found.
HELP
            exit 0 ;;
        *)
            echo "lint_skip_bug_gate.sh: unknown argument: $1" >&2
            exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Validate tests directory
# ---------------------------------------------------------------------------
if [[ ! -d "${_LSG_TESTS_DIR}" ]]; then
    echo "lint_skip_bug_gate.sh: ERROR: tests directory not found: ${_LSG_TESTS_DIR}" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Helper: verbose log
# ---------------------------------------------------------------------------
_lsg_log() {
    if [[ "${_LSG_VERBOSE}" == "true" ]]; then
        echo "lint_skip_bug_gate: $*"
    fi
}

_lsg_log "scanning tests dir: ${_LSG_TESTS_DIR}"
_lsg_log "bugs dir: ${_LSG_BUGS_DIR}"

# ---------------------------------------------------------------------------
# GATE 1 — Placeholder skip IDs
#
# Pattern: BUG-SKIP (placeholder prefix) OR skip.*BUG-[A-Za-z] (non-numeric
# BUG suffix — e.g. BUG-FOO, BUG-SKIP-compute-layout-floor-n1).
#
# A match here means the annotation cites a placeholder bug that was never
# filed, or uses an informal identifier instead of the canonical BUG-NNNN form.
# ---------------------------------------------------------------------------
echo "lint_skip_bug_gate: checking for placeholder skip IDs in ${_LSG_TESTS_DIR}..."

_LSG_PLACEHOLDER_HITS=0
# grep -rn returns non-zero when no matches; use set +e around it.
set +e
_LSG_PLACEHOLDER_OUTPUT="$(grep -rnE 'BUG-SKIP|skip.*BUG-[A-Za-z]' "${_LSG_TESTS_DIR}" 2>/dev/null)"
_LSG_GREP1_EXIT=$?
set -e

if [[ -n "${_LSG_PLACEHOLDER_OUTPUT}" ]]; then
    _LSG_PLACEHOLDER_HITS=$(echo "${_LSG_PLACEHOLDER_OUTPUT}" | wc -l | tr -d ' ')
    echo "lint_skip_bug_gate: FAIL — ${_LSG_PLACEHOLDER_HITS} placeholder skip citation(s) found:" >&2
    echo "${_LSG_PLACEHOLDER_OUTPUT}" >&2
else
    _lsg_log "placeholder check: 0 findings"
fi

# ---------------------------------------------------------------------------
# GATE 2 — Cited BUG-NNNN must exist in bugs/
#
# Extract all BUG-NNNN IDs cited in skip annotations across the test suite,
# then verify each one has a corresponding file in the bugs directory.
#
# A skip annotation that cites a bug is any line matching:
#   pytest.skip(...)  containing BUG-NNNN
#   @pytest.mark.skip(...)  containing BUG-NNNN
#   @pytest.mark.skipif(...)  containing BUG-NNNN
#   reason=... containing BUG-NNNN
#   # SKIP: ... containing BUG-NNNN
#   skip() in a bash context containing BUG-NNNN
#
# The pattern extracts all BUG-NNNN IDs from lines containing skip-related
# keywords. Each ID is then checked against the bugs directory.
# ---------------------------------------------------------------------------
echo "lint_skip_bug_gate: checking that cited BUG-NNNN IDs exist in bugs/ ..."

_LSG_MISSING_BUG_HITS=0
_LSG_MISSING_BUG_DETAILS=""

# Warn and skip the file-existence check when bugs dir is absent.
# This prevents false failures in fresh checkouts and CI environments
# where PGAI_AGENT_KANBAN_ROOT_PATH is not set.
if [[ ! -d "${_LSG_BUGS_DIR}" ]]; then
    echo "lint_skip_bug_gate: WARNING: bugs directory not found: ${_LSG_BUGS_DIR}" >&2
    echo "lint_skip_bug_gate: WARNING: skipping BUG-NNNN file-existence check (no bugs dir)" >&2
    echo "lint_skip_bug_gate: WARNING: set PGAI_AGENT_KANBAN_ROOT_PATH to the live kanban root to enable full validation." >&2
else
    # Extract skip annotations citing BUG-NNNN IDs.
    # Look for lines that contain both a skip keyword and a BUG-NNNN pattern.
    # The skip keyword forms covered:
    #   pytest.skip, mark.skip, mark.skipif, reason=, SKIP:, skip() [bash]
    set +e
    _LSG_SKIP_LINES="$(grep -rnEi '(pytest\.skip|mark\.skip|reason=|#\s*SKIP:|\bskip\s*\()[^)]*BUG-[0-9]{4,}' \
        "${_LSG_TESTS_DIR}" 2>/dev/null)"
    set -e

    if [[ -n "${_LSG_SKIP_LINES}" ]]; then
        # Extract all BUG-NNNN IDs from those lines, deduplicate.
        _LSG_CITED_IDS="$(echo "${_LSG_SKIP_LINES}" \
            | grep -oE 'BUG-[0-9]{4,}' \
            | sort -u)"

        _lsg_log "cited BUG-NNNN IDs: $(echo "${_LSG_CITED_IDS}" | tr '\n' ' ')"

        while IFS= read -r _bug_id; do
            [[ -z "${_bug_id}" ]] && continue
            # Check for any file matching BUG-NNNN-*.md in the bugs dir.
            # The bug ID is at the start of the filename (before the slug).
            _LSG_MATCH_COUNT=0
            set +e
            _LSG_MATCH_COUNT=$(find "${_LSG_BUGS_DIR}" -maxdepth 1 \
                -name "${_bug_id}-*.md" 2>/dev/null | wc -l | tr -d ' ')
            set -e
            if [[ "${_LSG_MATCH_COUNT}" -eq 0 ]]; then
                _LSG_MISSING_BUG_HITS=$(( _LSG_MISSING_BUG_HITS + 1 ))
                _LSG_MISSING_BUG_DETAILS="${_LSG_MISSING_BUG_DETAILS}  ${_bug_id}: no matching file found in ${_LSG_BUGS_DIR}\n"
            else
                _lsg_log "  ${_bug_id}: OK (${_LSG_MATCH_COUNT} file(s) found)"
            fi
        done <<< "${_LSG_CITED_IDS}"
    else
        _lsg_log "no skip annotations citing BUG-NNNN IDs found — nothing to check"
    fi

    if [[ "${_LSG_MISSING_BUG_HITS}" -gt 0 ]]; then
        echo "lint_skip_bug_gate: FAIL — ${_LSG_MISSING_BUG_HITS} cited BUG-NNNN ID(s) have no file in ${_LSG_BUGS_DIR}:" >&2
        printf "%b" "${_LSG_MISSING_BUG_DETAILS}" >&2
    else
        echo "lint_skip_bug_gate: BUG-NNNN file-existence check: 0 missing files"
    fi
fi

# ---------------------------------------------------------------------------
# Summary and exit
# ---------------------------------------------------------------------------
_LSG_TOTAL_FINDINGS=$(( _LSG_PLACEHOLDER_HITS + _LSG_MISSING_BUG_HITS ))

if [[ "${_LSG_TOTAL_FINDINGS}" -gt 0 ]]; then
    echo "lint_skip_bug_gate: FAIL — ${_LSG_TOTAL_FINDINGS} finding(s)." >&2
    echo "lint_skip_bug_gate: Fix by either:" >&2
    echo "  (a) replacing the placeholder/missing citation with a real filed BUG-NNNN" >&2
    echo "      whose file exists in the bugs/ directory, or" >&2
    echo "  (b) un-skipping the test if the underlying issue is resolved." >&2
    # provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
    echo "  See PRIORITY-0100-20260624-skipped-tests-cite-real-bug-grep-gated.md for policy." >&2
    exit 1
fi

echo "lint_skip_bug_gate: OK — all skip annotations are compliant."
exit 0
