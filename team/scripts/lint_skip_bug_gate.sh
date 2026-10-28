#!/usr/bin/env bash
# team/scripts/lint_skip_bug_gate.sh
#
# Check: every skip statement in team/tests/ that cites a follow-up bug
# must reference a real BUG-NNNN whose file exists in the project's bugs/
# directory. Quality class (CONVERT, team/SOP-core.md "Gates and Refusals"):
# a bad citation is a provenance defect with zero runtime-failure risk, so
# each one is reported -- printed, and with --findings-file written as its
# own finding -- and the gate exits 0.
#
# Detection is per-surface, stated explicitly rather than inherited by
# accident:
#   - *.py files (where structure is available and a parser costs nothing
#     extra) are scanned STRUCTURALLY, via lib/skip_bug_citation_scan.py.
#     That module parses each file with the standard-library `ast` module
#     and identifies actual skip statements -- pytest.skip(...), a bare
#     skip(...), pytest.mark.skip(...)/skipif(...) decorators -- as AST
#     nodes, whatever line shape their arguments are written in. Only the
#     source text of those nodes is a citation candidate; a docstring or a
#     piece of sample data that merely mentions a bug identifier is never
#     examined, because it is never the argument list of a skip call.
#   - *.sh and *.md files (and anything else under the tests dir) have no
#     AST to parse. They keep the original line-oriented grep below --
#     genuinely the right tool where there is no structure to target.
#
# Two subtrees of the tests dir are not test code and are left out of the
# tree walk on both surfaces and for both gates:
#   - any directory named artifacts/ -- committed report evidence (coverage
#     baselines, run logs) whose recorded output can echo this gate's own
#     diagnostic strings back at it;
#   - fixtures/tester_reports/ directly under the tests dir -- the TESTER
#     report fixture corpus (verbatim verification reports, their golden
#     parses and the legacy readers they are compared against). Those
#     reports quote placeholder-skip shapes and other projects' bug IDs as
#     report prose, never as skip statements. Only that one path is left
#     out; a directory of the same name anywhere else is still scanned.
#
# Two failure modes detected (identically, whichever surface a citation
# came from):
#   1. Placeholder skip IDs — BUG-SKIP-* or any non-BUG-NNNN pattern cited
#      in a skip annotation (pattern: BUG-SKIP or skip.*BUG-[A-Za-z]; on
#      the Python surface "skip" is implied by the statement itself, so
#      the structural check is BUG-SKIP or BUG-[A-Za-z]).
#   2. Missing bug file — a BUG-NNNN ID is cited but the corresponding file
#      (BUG-NNNN-*.md) does not exist in the bugs/ directory. Each missing
#      ID is reported once, followed by every citing skip as
#      path:line:content, so the file and line to fix are named.
#
# Usage:
#   lint_skip_bug_gate.sh [--tests-dir PATH] [--bugs-dir PATH]
#                         [--findings-file PATH] [--verbose]
#   lint_skip_bug_gate.sh --files PATH [PATH ...] [--bugs-dir PATH]
#                         [--findings-file PATH] [--verbose]
#
#   --tests-dir PATH   Directory to scan for skip annotations.
#                      Default: team/tests/ relative to this script's repo root.
#   --files PATH ...   Named-file mode: check exactly these files and walk no
#                      directory. Consumes every following argument up to the
#                      next one starting with "-". Named *.py files go to
#                      lib/skip_bug_citation_scan.py --files (structural scan);
#                      every other named file gets the line-oriented grep. The
#                      tree walk's artifacts/ and fixtures/tester_reports/
#                      exclusions do not apply -- a named file is checked
#                      because it was named. Both
#                      gates run exactly as in the default mode, against the
#                      named files only. Paths that resolve to the same file
#                      (x.py and ./x.py) are checked once, under the first
#                      spelling given. Cannot be combined with --tests-dir.
#                      Without --files the whole-tree default is unchanged.
#   --bugs-dir PATH    Directory containing BUG-NNNN-*.md files.
#                      Default: $PGAI_AGENT_KANBAN_ROOT_PATH/projects/pgai-agent-kanban/bugs/
#                      If the directory does not exist, the file-existence check
#                      is skipped with a warning (exit 0).
#   --findings-file PATH
#                      Write the findings as a JSON array to PATH (parent
#                      directories created; written even when empty). Each
#                      finding carries lint "skip_cites_real_bug", severity
#                      "quality", summary, cause, remedy, file, line, and a
#                      kind: placeholder-citation (one per placeholder skip),
#                      unfiled-citation (one per skip citing a BUG-NNNN with
#                      no file, with its bug_id) or tests-dir-missing. Built
#                      by lib/gated_findings.py; the gated runners merge it
#                      per instance with gated_runner_merge_findings_file.
#   --verbose          Print each finding and each scanned file.
#
# Exit codes:
#   0   The gate ran, whatever it found (each finding is printed and, with
#       --findings-file, recorded) -- a missing tests directory included.
#   2   Usage error: an unknown argument, an empty --files list, a --files
#       path that is not an existing file, or --files combined with
#       --tests-dir.
#
# run-unit-tests.sh and run-integration-tests.sh call this before pytest and
# merge its findings file; a targeted --files run fails on any entry in it.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"
# shellcheck source=lib/error_contract.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/error_contract.sh"

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
_LSG_FINDINGS_FILE=""
# Named-file mode (--files): the exact paths to check, and whether
# --tests-dir was given explicitly (the two are mutually exclusive).
_LSG_FILES_MODE=false
_LSG_NAMED_FILES=()
_LSG_TESTS_DIR_GIVEN=false

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tests-dir)
            _LSG_TESTS_DIR="$2"; _LSG_TESTS_DIR_GIVEN=true; shift 2 ;;
        --tests-dir=*)
            _LSG_TESTS_DIR="${1#*=}"; _LSG_TESTS_DIR_GIVEN=true; shift ;;
        --files)
            # Consumes every following argument up to the next one
            # starting with "-" (the same shape run-unit-tests.sh uses).
            _LSG_FILES_MODE=true
            shift
            while [[ $# -gt 0 && "$1" != -* ]]; do
                _LSG_NAMED_FILES+=("$1")
                shift
            done
            ;;
        --bugs-dir)
            _LSG_BUGS_DIR="$2"; shift 2 ;;
        --bugs-dir=*)
            _LSG_BUGS_DIR="${1#*=}"; shift ;;
        --findings-file)
            _LSG_FINDINGS_FILE="${2:-}"; shift 2 ;;
        --findings-file=*)
            _LSG_FINDINGS_FILE="${1#*=}"; shift ;;
        --verbose|-v)
            _LSG_VERBOSE=true; shift ;;
        --help|-h)
            cat <<'HELP'
Usage: lint_skip_bug_gate.sh [--tests-dir PATH] [--bugs-dir PATH]
                             [--findings-file PATH] [--verbose]
       lint_skip_bug_gate.sh --files PATH [PATH ...] [--bugs-dir PATH]
                             [--findings-file PATH] [--verbose]

Enforce that every skip()/SKIP: annotation in the test suite that cites a
bug references a real BUG-NNNN whose file exists in the bugs/ directory.
The tree walk leaves out artifacts/ directories and the TESTER report
fixture corpus (<tests-dir>/fixtures/tester_reports/); --files checks
exactly what it is given.

Options:
  --tests-dir PATH   Test directory to scan (default: team/tests/)
  --files PATH ...   Check exactly these files instead of walking a test
                     directory (*.py structurally, others by grep). Each
                     must be an existing file. Not combinable with
                     --tests-dir.
  --bugs-dir PATH    Bugs directory to validate against (default: inferred
                     from PGAI_AGENT_KANBAN_ROOT_PATH)
  --findings-file PATH
                     Write the findings as a JSON array to PATH, one per
                     placeholder citation and per skip citing an unfiled
                     bug (written even when empty)
  --verbose, -v      Print scan progress
  --help, -h         Show this help

Quality class: findings are printed (and recorded with --findings-file)
and the gate exits 0 whatever it finds.

Exit codes:
  0   The gate ran, whatever it found (a missing tests dir is a finding).
  2   Usage error, or a --files path not found.
HELP
            exit 0 ;;
        *)
            echo "lint_skip_bug_gate.sh: unknown argument: $1" >&2
            exit 2 ;;
    esac
done

# ---------------------------------------------------------------------------
# Per-instance findings. Each is one JSON object built by
# lib/gated_findings.py instance; _lsg_write_findings writes them all as the
# --findings-file array (nothing when the flag was not given).
# ---------------------------------------------------------------------------
_LSG_FINDINGS_PY="${_LSG_SCRIPT_DIR}/lib/gated_findings.py"
_LSG_FINDINGS=()

# _lsg_record <cause> <remedy> [KEY=VALUE ...]
_lsg_record() {
    local finding=""
    if finding="$(python3 "${_LSG_FINDINGS_PY}" instance skip_cites_real_bug "$@" 2>/dev/null)" \
        && [[ -n "${finding}" ]]; then
        _LSG_FINDINGS+=("${finding}")
    else
        echo "lint_skip_bug_gate: could not record a finding: $1" >&2
    fi
    return 0
}

# _lsg_record_hit <kind> <path:line:content> <remedy-prefix> [KEY=VALUE ...]
# One finding for one path:line:content skip hit, naming its file and line.
_lsg_record_hit() {
    local kind="$1" hit="$2" fix="$3"
    shift 3
    local path="${hit%%:*}" rest line
    rest="${hit#*:}"
    line="${rest%%:*}"
    [[ "${line}" =~ ^[0-9]+$ ]] || line=""
    _lsg_record "${hit}" "${fix} ${path}${line:+ line ${line}}, then re-check: $(_lsg_recheck "${path}")" \
        "kind=${kind}" "file=${path}" ${line:+"line=${line}"} "$@"
}

# _lsg_command [arg ...]: this gate run with the given arguments, as a
# pasteable command line.
_lsg_command() {
    local quoted
    printf -v quoted '%q ' bash "${_LSG_SELF}" "$@"
    printf '%s' "${quoted% }"
}

# _lsg_recheck <path>: this gate re-run over the one named file.
_lsg_recheck() {
    _lsg_command --files "$1" --bugs-dir "${_LSG_BUGS_DIR}"
}

_lsg_write_findings() {
    [[ -n "${_LSG_FINDINGS_FILE}" ]] || return 0
    if [[ ${#_LSG_FINDINGS[@]} -gt 0 ]]; then
        printf '%s\n' "${_LSG_FINDINGS[@]}" | python3 "${_LSG_FINDINGS_PY}" array "${_LSG_FINDINGS_FILE}" \
            || echo "lint_skip_bug_gate: could not write findings file ${_LSG_FINDINGS_FILE}" >&2
    else
        python3 "${_LSG_FINDINGS_PY}" array "${_LSG_FINDINGS_FILE}" < /dev/null \
            || echo "lint_skip_bug_gate: could not write findings file ${_LSG_FINDINGS_FILE}" >&2
    fi
    return 0
}

# ---------------------------------------------------------------------------
# Usage refusal for named-file mode. One exit site for every --files usage
# error, so the census carries one anchor for the whole family.
# ---------------------------------------------------------------------------
_lsg_usage_refusal() {
    pgai_emit_error_contract "$1" "$2" "$3"
    echo "Usage: lint_skip_bug_gate.sh --files PATH [PATH ...] [--bugs-dir PATH] [--findings-file PATH] [--verbose]" >&2
    # CONVERT: quality-class lint precondition -- a malformed --files
    # request is an operator-CLI argument refusal: this run has nothing
    # valid to scan, not a defect in the tree, so it is the one exit 2 left.
    # CENSUS-ANCHOR: lint-skip-bug-gate-files-usage-01
    exit 2
}

# ---------------------------------------------------------------------------
# Validate named files (--files) or the tests directory (default mode)
# ---------------------------------------------------------------------------
_LSG_SELF="${_LSG_SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
_LSG_NAMED_PY=()
_LSG_NAMED_OTHER=()
if [[ "${_LSG_FILES_MODE}" == "true" ]]; then
    _LSG_EXAMPLE_FILE="${_LSG_REPO_ROOT}/team/tests/unit/test_semver.py"
    if [[ "${_LSG_TESTS_DIR_GIVEN}" == "true" ]]; then
        _lsg_usage_refusal "lint_skip_bug_gate.files_with_tests_dir" \
            "--files cannot be combined with --tests-dir: named-file mode checks exactly the named files and walks no directory." \
            "bash ${_LSG_SELF@Q} --files ${_LSG_NAMED_FILES[*]@Q}"
    fi
    if [[ ${#_LSG_NAMED_FILES[@]} -eq 0 ]]; then
        _lsg_usage_refusal "lint_skip_bug_gate.files_empty" \
            "--files was given without any file path after it; named-file mode needs at least one file to check." \
            "bash ${_LSG_SELF@Q} --files ${_LSG_EXAMPLE_FILE@Q}"
    fi
    _LSG_MISSING_NAMED=()
    # A file named twice is checked once, so its findings are not
    # double-counted. The key is the resolved path (realpath -m: absolute,
    # with ., .. and symlinks resolved, and defined for a missing path too),
    # so x.py, ./x.py and an absolute spelling of it are one file; the first
    # spelling given is the one scanned and printed.
    declare -A _LSG_SEEN_NAMED=()
    _LSG_UNIQUE_NAMED_COUNT=0
    for _lsg_path in "${_LSG_NAMED_FILES[@]}"; do
        _lsg_key="$(realpath -m -- "${_lsg_path}" 2>/dev/null || printf '%s' "${_lsg_path}")"
        [[ -n "${_LSG_SEEN_NAMED[${_lsg_key}]:-}" ]] && continue
        _LSG_SEEN_NAMED[${_lsg_key}]=1
        _LSG_UNIQUE_NAMED_COUNT=$(( _LSG_UNIQUE_NAMED_COUNT + 1 ))
        if [[ ! -f "${_lsg_path}" ]]; then
            _LSG_MISSING_NAMED+=("${_lsg_path}")
        elif [[ "${_lsg_path}" == *.py ]]; then
            _LSG_NAMED_PY+=("${_lsg_path}")
        else
            _LSG_NAMED_OTHER+=("${_lsg_path}")
        fi
    done
    if [[ ${#_LSG_MISSING_NAMED[@]} -gt 0 ]]; then
        _lsg_usage_refusal "lint_skip_bug_gate.files_not_found" \
            "--files path(s) not found or not a regular file: ${_LSG_MISSING_NAMED[*]}" \
            "bash ${_LSG_SELF@Q} --files ${_LSG_EXAMPLE_FILE@Q}"
    fi
    unset _lsg_key
    _LSG_SCOPE_LABEL="${_LSG_UNIQUE_NAMED_COUNT} named file(s)"
elif [[ ! -d "${_LSG_TESTS_DIR}" ]]; then
    echo "lint_skip_bug_gate.sh: ERROR: tests directory not found: ${_LSG_TESTS_DIR}" >&2
    _lsg_record "tests directory ${_LSG_TESTS_DIR} not found, so no skip citation was checked" \
        "$(printf 'ls -ld -- %q' "${_LSG_TESTS_DIR}") && $(_lsg_command --tests-dir "${_LSG_REPO_ROOT}/team/tests")" \
        "kind=tests-dir-missing" "file=${_LSG_TESTS_DIR}"
    _lsg_write_findings
    # CENSUS-ANCHOR: lint-skip-bug-gate-tests-dir-missing-01
    # CONVERT: quality-class lint precondition -- a missing tests directory
    # means this run has nothing to scan, not that the tree has a defect;
    # recorded as a finding, exits 0.
    pgai_emit_error_contract "lint_skip_bug_gate.tests_dir_missing" \
        "tests directory not found: ${_LSG_TESTS_DIR}; no skip citation was checked." \
        "$(_lsg_command --tests-dir "${_LSG_REPO_ROOT}/team/tests")"
    exit 0
fi

# ---------------------------------------------------------------------------
# Helper: verbose log
# ---------------------------------------------------------------------------
_lsg_log() {
    if [[ "${_LSG_VERBOSE}" == "true" ]]; then
        echo "lint_skip_bug_gate: $*"
    fi
}

if [[ "${_LSG_FILES_MODE}" == "true" ]]; then
    _lsg_log "scanning ${_LSG_SCOPE_LABEL}: ${_LSG_NAMED_FILES[*]}"
else
    _LSG_SCOPE_LABEL="${_LSG_TESTS_DIR}"
    _lsg_log "scanning tests dir: ${_LSG_TESTS_DIR}"
fi
_lsg_log "bugs dir: ${_LSG_BUGS_DIR}"

# ---------------------------------------------------------------------------
# Scan helpers: one place decides what each surface reads in each mode.
#
# _lsg_grep_scan FLAGS PATTERN -- the line-oriented surface (everything but
#   *.py). Default mode: recursive over the tests dir, excluding artifacts/,
#   the report fixture corpus and *.py. Named-file mode: exactly the named
#   non-*.py files, with -H so each hit keeps the same path:line:content
#   shape; nothing to grep prints nothing.
# _lsg_py_scan MODE -- the structural *.py surface via
#   lib/skip_bug_citation_scan.py: --tests-dir by default (minus the report
#   fixture corpus), --files with the named *.py files in named-file mode.
# _lsg_drop_report_fixtures -- the tree walk's report-corpus exclusion: a
#   filter on stdin that drops every path:line:content hit whose path lies
#   under _LSG_REPORT_FIXTURES_PREFIX. Both scanners print each path as the
#   tests dir joined with the path below it, so one prefix test anchors the
#   exclusion to <tests-dir>/fixtures/tester_reports/ on both surfaces.
# All print findings on stdout and never fail the caller (no-match is a
# normal outcome here).
# ---------------------------------------------------------------------------
_LSG_REPORT_FIXTURES_PREFIX="${_LSG_TESTS_DIR%/}/fixtures/tester_reports/"

_lsg_drop_report_fixtures() {
    awk -v prefix="${_LSG_REPORT_FIXTURES_PREFIX}" 'index($0, prefix) != 1'
}

_lsg_grep_scan() {
    local flags="$1" pattern="$2"
    if [[ "${_LSG_FILES_MODE}" == "true" ]]; then
        [[ ${#_LSG_NAMED_OTHER[@]} -eq 0 ]] && return 0
        grep -H "${flags}" "${pattern}" -- "${_LSG_NAMED_OTHER[@]}" 2>/dev/null || true
    else
        { grep -r "${flags}" --exclude-dir=artifacts --exclude='*.py' "${pattern}" \
            "${_LSG_TESTS_DIR}" 2>/dev/null || true; } | _lsg_drop_report_fixtures
    fi
}

_lsg_py_scan() {
    local mode="$1"
    if [[ "${_LSG_FILES_MODE}" == "true" ]]; then
        [[ ${#_LSG_NAMED_PY[@]} -eq 0 ]] && return 0
        python3 "${_LSG_SCRIPT_DIR}/lib/skip_bug_citation_scan.py" \
            --files "${_LSG_NAMED_PY[@]}" --mode "${mode}" 2>/dev/null || true
    else
        { python3 "${_LSG_SCRIPT_DIR}/lib/skip_bug_citation_scan.py" \
            --tests-dir "${_LSG_TESTS_DIR}" --mode "${mode}" 2>/dev/null || true; } \
            | _lsg_drop_report_fixtures
    fi
}

# ---------------------------------------------------------------------------
# GATE 1 — Placeholder skip IDs
#
# Pattern: BUG-SKIP (placeholder prefix) OR skip.*BUG-[A-Za-z] (non-numeric
# BUG suffix — e.g. BUG-FOO, BUG-SKIP-compute-layout-floor-n1).
#
# A match here means the annotation cites a placeholder bug that was never
# filed, or uses an informal identifier instead of the canonical BUG-NNNN form.
#
# artifacts/ and fixtures/tester_reports/ are excluded from the tree walk
# (file header): report evidence and report fixture data, not test source.
#
# *.py files are excluded from this grep and scanned structurally instead
# (below) — see the file header for why.
# ---------------------------------------------------------------------------
echo "lint_skip_bug_gate: checking for placeholder skip IDs in ${_LSG_SCOPE_LABEL}..."

_LSG_PLACEHOLDER_HITS=0
# grep -rn returns non-zero when no matches; use set +e around it.
set +e
_LSG_PLACEHOLDER_GREP_OUTPUT="$(_lsg_grep_scan -nE 'BUG-SKIP|skip.*BUG-[A-Za-z]')"
_LSG_PLACEHOLDER_PY_OUTPUT="$(_lsg_py_scan placeholder)"
set -e

# set +e around the merge: under set -e, a $(...) whose last evaluated
# command is a false `[[ ]]` test (both sources empty) would abort the
# script here rather than fall through to the "no findings" branch below.
set +e
_LSG_PLACEHOLDER_OUTPUT="$(
    { [[ -n "${_LSG_PLACEHOLDER_GREP_OUTPUT}" ]] && printf '%s\n' "${_LSG_PLACEHOLDER_GREP_OUTPUT}"
      [[ -n "${_LSG_PLACEHOLDER_PY_OUTPUT}" ]] && printf '%s\n' "${_LSG_PLACEHOLDER_PY_OUTPUT}"
      true; }
)"
set -e

if [[ -n "${_LSG_PLACEHOLDER_OUTPUT}" ]]; then
    _LSG_PLACEHOLDER_HITS=$(echo "${_LSG_PLACEHOLDER_OUTPUT}" | wc -l | tr -d ' ')
    echo "lint_skip_bug_gate: ${_LSG_PLACEHOLDER_HITS} placeholder skip citation(s) found:" >&2
    echo "${_LSG_PLACEHOLDER_OUTPUT}" >&2
    while IFS= read -r _lsg_hit; do
        [[ -n "${_lsg_hit}" ]] || continue
        _lsg_record_hit placeholder-citation "${_lsg_hit}" \
            "replace the placeholder with a filed BUG-NNNN whose file exists in ${_LSG_BUGS_DIR}, or un-skip the test, in"
    done <<< "${_LSG_PLACEHOLDER_OUTPUT}"
else
    _lsg_log "placeholder check: 0 findings"
fi

# ---------------------------------------------------------------------------
# GATE 2 — Cited BUG-NNNN must exist in bugs/
#
# Extract all BUG-NNNN IDs cited in skip annotations across the test suite,
# then verify each one has a corresponding file in the bugs directory.
#
# A skip annotation that cites a bug is:
#   - on the Python surface: the source text of an actual skip STATEMENT
#     (pytest.skip(...), mark.skip(...), mark.skipif(...), or a bare
#     skip(...)), found structurally by lib/skip_bug_citation_scan.py --
#     whatever line shape its arguments are written in — containing
#     BUG-NNNN. A string or comment that merely mentions BUG-NNNN and is
#     never passed to a skip call is not a candidate.
#   - on every other surface (*.sh, *.md, ...): any line matching
#       pytest.skip(...)  containing BUG-NNNN
#       @pytest.mark.skip(...)  containing BUG-NNNN
#       @pytest.mark.skipif(...)  containing BUG-NNNN
#       reason=... containing BUG-NNNN
#       # SKIP: ... containing BUG-NNNN
#       skip() in a bash context containing BUG-NNNN
#     via the original line-oriented grep — there is no AST for these
#     file types, so the line-shape heuristic is the correct tool.
#
# Every cited BUG-NNNN ID, from either surface, is checked against the
# bugs directory the same way. A missing ID is reported once, then each
# path:line:content hit that cites it, so the reader sees which file and
# line holds the citation.
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
    #
    # *.py files are excluded from this grep and scanned structurally
    # instead (below) — see the file header for why.
    set +e
    _LSG_SKIP_LINES_GREP="$(_lsg_grep_scan -nEi '(pytest\.skip|mark\.skip|reason=|#\s*SKIP:|\bskip\s*\()[^)]*BUG-[0-9]{4,}')"
    _LSG_SKIP_LINES_PY="$(_lsg_py_scan citation)"
    set -e

    # set +e around the merge: under set -e, a $(...) whose last evaluated
    # command is a false `[[ ]]` test (both sources empty) would abort the
    # script here rather than fall through to the "nothing to check" branch.
    set +e
    _LSG_SKIP_LINES="$(
        { [[ -n "${_LSG_SKIP_LINES_GREP}" ]] && printf '%s\n' "${_LSG_SKIP_LINES_GREP}"
          [[ -n "${_LSG_SKIP_LINES_PY}" ]] && printf '%s\n' "${_LSG_SKIP_LINES_PY}"
          true; }
    )"
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
                _LSG_MISSING_BUG_DETAILS="${_LSG_MISSING_BUG_DETAILS}  ${_bug_id}: no matching file found in ${_LSG_BUGS_DIR}; cited at:\n"
                # Every skip hit citing this exact ID (not a longer ID that
                # starts with it), as path:line:content.
                while IFS= read -r _lsg_hit; do
                    [[ -z "${_lsg_hit}" ]] && continue
                    _LSG_MISSING_BUG_DETAILS="${_LSG_MISSING_BUG_DETAILS}    ${_lsg_hit//\\/\\\\}\n"
                    _lsg_record_hit unfiled-citation "${_lsg_hit}" \
                        "${_bug_id} has no file in ${_LSG_BUGS_DIR}: cite a filed BUG-NNNN or un-skip the test in" \
                        "bug_id=${_bug_id}"
                done < <(printf '%s\n' "${_LSG_SKIP_LINES}" | grep -E "${_bug_id}([^0-9]|\$)" || true)
            else
                _lsg_log "  ${_bug_id}: OK (${_LSG_MATCH_COUNT} file(s) found)"
            fi
        done <<< "${_LSG_CITED_IDS}"
    else
        _lsg_log "no skip annotations citing BUG-NNNN IDs found — nothing to check"
    fi

    if [[ "${_LSG_MISSING_BUG_HITS}" -gt 0 ]]; then
        echo "lint_skip_bug_gate: ${_LSG_MISSING_BUG_HITS} cited BUG-NNNN ID(s) have no file in ${_LSG_BUGS_DIR}:" >&2
        printf "%b" "${_LSG_MISSING_BUG_DETAILS}" >&2
    else
        echo "lint_skip_bug_gate: BUG-NNNN file-existence check: 0 missing files"
    fi
fi

# ---------------------------------------------------------------------------
# Summary and exit
# ---------------------------------------------------------------------------
_LSG_TOTAL_FINDINGS=$(( _LSG_PLACEHOLDER_HITS + _LSG_MISSING_BUG_HITS ))
_lsg_write_findings

if [[ "${_LSG_TOTAL_FINDINGS}" -gt 0 ]]; then
    echo "lint_skip_bug_gate: ${_LSG_TOTAL_FINDINGS} finding(s)." >&2
    echo "lint_skip_bug_gate: Fix by either:" >&2
    echo "  (a) replacing the placeholder/missing citation with a real filed BUG-NNNN" >&2
    echo "      whose file exists in the bugs/ directory, or" >&2
    echo "  (b) un-skipping the test if the underlying issue is resolved." >&2
    echo "  See roles/TESTER.md \"Test Authoring Guidelines\" for the full skip-citation policy." >&2
    # CENSUS-ANCHOR: lint-skip-bug-gate-findings-01
    # CONVERT: quality-class finding, zero runtime-failure risk -- a
    # placeholder or missing bug citation on a skipped test is a
    # provenance/hygiene defect, not something that can break a shipped
    # artefact; reported per instance, exits 0.
    _lsg_scope_arg=(--tests-dir "${_LSG_TESTS_DIR}")
    # Named-file mode re-checks the files as scanned: one spelling each.
    [[ "${_LSG_FILES_MODE}" == "true" ]] && _lsg_scope_arg=(--files "${_LSG_NAMED_PY[@]}" "${_LSG_NAMED_OTHER[@]}")
    pgai_emit_error_contract "lint_skip_bug_gate.findings" \
        "${_LSG_TOTAL_FINDINGS} skip citation finding(s) in ${_LSG_SCOPE_LABEL}; each is listed above with its file and line." \
        "$(_lsg_command "${_lsg_scope_arg[@]}" --bugs-dir "${_LSG_BUGS_DIR}")"
    exit 0
fi

echo "lint_skip_bug_gate: OK — all skip annotations are compliant."
exit 0
