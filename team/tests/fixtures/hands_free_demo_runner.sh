#!/usr/bin/env bash
# hands_free_demo_runner.sh
# ==========================
# Deliberate good-example fixture: a minimal gated-runner-shaped script that
# reuses the REAL breakage/quality split library (scripts/lib/gated_runner_lint.sh)
# and the real hygiene lint's provenance class
# (scripts/lint_hygiene.py --classes provenance) to demonstrate the
# hands-free demo's "runners exit 0 with the findings file written" behavior
# end to end, without running the full unit/integration suite.
#
# Given a prose directory and/or a code directory containing a deliberate
# quality-class finding (a release-notes file, or a code comment, citing a
# bare internal ledger ID) and a pytest target (file or directory) containing
# an otherwise-green test module, this runner:
#   1. Runs the hygiene lint's provenance class against --prose-dir and/or
#      --code-dir, merging its own per-instance findings-file output via the
#      shared gated_runner_merge_findings_file helper — the same wiring the
#      real gated runners use — never fatal, the finding is recorded and
#      printed, and the runner continues regardless of what the scan finds.
#      At least one of --prose-dir / --code-dir must be supplied; whichever
#      is omitted is scanned as an empty scratch directory (no findings from
#      that surface).
#   2. Runs pytest against --pytest-path — this IS fatal (mirrors
#      run-unit-tests.sh: a real test failure still fails the runner; only
#      the quality-class lint is non-fatal). Pass the specific test file when
#      the module's own filename does not match pytest's default
#      test_*.py/*_test.py discovery glob (as with this repo's own
#      fixtures/-directory convention of naming fixture modules so they are
#      never auto-collected elsewhere) — pytest always collects an explicitly
#      named file regardless of its name.
#   3. Writes the aggregate findings file via gated_runner_write_findings_file,
#      unconditionally, so its existence is proof the quality-class pre-flight
#      ran regardless of what it found.
#
# Exit codes:
#   0  = pytest passed (quality-class lint findings, if any, do not affect this)
#   1  = pytest failed, or a required argument is missing
#   2  = pytest not installed
#
# Usage:
#   hands_free_demo_runner.sh [--prose-dir PATH] [--code-dir PATH] \
#       --pytest-path PATH --findings-file PATH

set -euo pipefail

_HFDR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HFDR_LIB_DIR="$(cd "${_HFDR_SCRIPT_DIR}/../../scripts/lib" && pwd)"
_HFDR_SCRIPTS_DIR="$(cd "${_HFDR_SCRIPT_DIR}/../../scripts" && pwd)"

# shellcheck source=../../scripts/lib/error_contract.sh
source "${_HFDR_LIB_DIR}/error_contract.sh"
# shellcheck source=../../scripts/lib/gated_runner_lint.sh
source "${_HFDR_LIB_DIR}/gated_runner_lint.sh"

PROSE_DIR=""
CODE_DIR=""
PYTEST_PATH=""
FINDINGS_FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --prose-dir)
      PROSE_DIR="$2"
      shift 2
      ;;
    --code-dir)
      CODE_DIR="$2"
      shift 2
      ;;
    --pytest-path)
      PYTEST_PATH="$2"
      shift 2
      ;;
    --findings-file)
      FINDINGS_FILE="$2"
      shift 2
      ;;
    --help|-h)
      cat <<EOF
Usage: $(basename "$0") [--prose-dir PATH] [--code-dir PATH] \\
    --pytest-path PATH --findings-file PATH

Deliberate good-example fixture standing in for a gated runner's
breakage/quality split (scripts/lib/gated_runner_lint.sh), for the
hands-free demo (team/tests/integration/test_cm_release_synthetic.py,
classes TestHandsFreeDemoEndToEnd and
TestHandsFreeDemoCodeSurfaceLitterEndToEnd).

Options:
  --prose-dir PATH      Directory of .md release-notes-shaped prose scanned
                         by the real hygiene lint's provenance class (never
                         fatal — a finding is recorded, not blocking).
  --code-dir PATH       Directory of .py/.sh code-shaped files scanned by the
                         same provenance class's code surface (never fatal).
                         At least one of --prose-dir / --code-dir is
                         required; the other is scanned as an empty scratch
                         directory.
  --pytest-path PATH    File or directory containing the fixture's own green
                         pytest module (fatal on failure, like a real gated
                         runner). Pass the specific file when its name does
                         not match pytest's default discovery glob.
  --findings-file PATH  Where to write the aggregate quality-class findings
                         file (always written, per gated_runner_lint.sh).
  --help, -h            Show this help

Exit codes:
  0  = pytest passed
  1  = pytest failed, or a required argument is missing
  2  = pytest not installed
EOF
      exit 0
      ;;
    *)
      echo "FAIL: unrecognized argument '$1'." >&2
      echo "Run '$(basename "$0") --help' for the accepted flags." >&2
      exit 1
      ;;
  esac
done

if [[ -z "$PYTEST_PATH" || -z "$FINDINGS_FILE" ]]; then
  echo "FAIL: --pytest-path and --findings-file are both required." >&2
  echo "Run '$(basename "$0") --help' for usage." >&2
  exit 1
fi

if [[ -z "$PROSE_DIR" && -z "$CODE_DIR" ]]; then
  echo "FAIL: at least one of --prose-dir or --code-dir is required (nothing to scan)." >&2
  echo "Run '$(basename "$0") --help' for usage." >&2
  exit 1
fi

if ! command -v pytest >/dev/null 2>&1 && ! python3 -m pytest --version >/dev/null 2>&1; then
  echo "FAIL: pytest is not installed in this Python environment." >&2
  exit 2
fi

# --- Quality-class pre-flight: hygiene lint (provenance class) ---
# Whichever of --prose-dir / --code-dir was not supplied is scanned as an
# empty scratch directory, so the caller can exercise either surface (or
# both) without the other contributing spurious findings. --test-tree-dirs
# and --config-dirs are each pinned to their own empty scratch directory
# too, so this fixture never sweeps the real team/tests/ or team/config/
# trees in as a side effect (lint_hygiene.py's default --config-dirs is
# team/config/ relative to its own real repo root, which this fixture's
# unpinned --repo-root would otherwise resolve to).
_HFDR_EMPTY_PROSE_DIR="$(mktemp -d)"
_HFDR_EMPTY_CODE_DIR="$(mktemp -d)"
_HFDR_EMPTY_TEST_TREE_DIR="$(mktemp -d)"
_HFDR_EMPTY_CONFIG_DIR="$(mktemp -d)"
_HFDR_HYGIENE_FINDINGS="$(mktemp)"
trap 'rm -rf "${_HFDR_EMPTY_PROSE_DIR}" "${_HFDR_EMPTY_CODE_DIR}" "${_HFDR_EMPTY_TEST_TREE_DIR}" "${_HFDR_EMPTY_CONFIG_DIR}"; rm -f "${_HFDR_HYGIENE_FINDINGS}"' EXIT

echo "Running hygiene lint (provenance class) against prose-dir=${PROSE_DIR:-<none>} code-dir=${CODE_DIR:-<none>}..."
python3 "${_HFDR_SCRIPTS_DIR}/lint_hygiene.py" \
  --classes provenance \
  --code-dirs "${CODE_DIR:-${_HFDR_EMPTY_CODE_DIR}}" \
  --test-tree-dirs "${_HFDR_EMPTY_TEST_TREE_DIR}" \
  --prose-dirs "${PROSE_DIR:-${_HFDR_EMPTY_PROSE_DIR}}" \
  --config-dirs "${_HFDR_EMPTY_CONFIG_DIR}" \
  --findings-file "${_HFDR_HYGIENE_FINDINGS}"
gated_runner_merge_findings_file "${_HFDR_HYGIENE_FINDINGS}"

# --- Write the aggregate quality-class findings file (always, per contract) ---
gated_runner_write_findings_file "${FINDINGS_FILE}" "hands_free_demo_runner.sh"

# --- Fatal step: the fixture's own green pytest suite ---
# This is a NESTED pytest invocation launched from inside a caller that may
# itself be a pytest test (the hands-free demo integration test calls this
# script via subprocess). PYTEST_ADDOPTS's --basetemp, if inherited from
# that outer pytest session's environment, would collide: pytest clears and
# recreates an explicit --basetemp directory at session start, and the
# outer session's tmp_path (where the findings file this script just wrote
# lives) sits under that exact same basetemp -- so an inherited
# --basetemp would silently delete the just-written findings file the
# moment this nested session starts. Give the nested run its own isolated,
# disposable basetemp instead of inheriting the caller's.
echo "Running fixture pytest suite at ${PYTEST_PATH}..."
_HFDR_NESTED_BASETEMP_PARENT="${PGAI_AGENT_KANBAN_TEMP_DIR:-/tmp/pgai_kanban_tmp}/pytest/hands-free-demo-nested"
mkdir -p "${_HFDR_NESTED_BASETEMP_PARENT}"
_HFDR_NESTED_BASETEMP="$(mktemp -d -p "${_HFDR_NESTED_BASETEMP_PARENT}")"
unset _HFDR_NESTED_BASETEMP_PARENT
set +e
PYTEST_ADDOPTS="--basetemp=${_HFDR_NESTED_BASETEMP}" python3 -m pytest "${PYTEST_PATH}"
_HFDR_PYTEST_EXIT=$?
set -e
rm -rf "${_HFDR_NESTED_BASETEMP}"

if [[ $_HFDR_PYTEST_EXIT -ne 0 ]]; then
  pgai_emit_error_contract \
    "hands-free-demo.pytest-failure" \
    "The hands-free demo's fixture pytest suite at ${PYTEST_PATH} failed (exit ${_HFDR_PYTEST_EXIT}); a real test failure is fatal even though the quality-class lint above never is." \
    "Scroll up to the pytest output for the failing test's traceback, fix it, then re-run $(basename "$0")."
  exit 1
fi

echo "hands_free_demo_runner.sh: PASSED (pytest exit 0; quality-class findings, if any, are in ${FINDINGS_FILE})"
exit 0
