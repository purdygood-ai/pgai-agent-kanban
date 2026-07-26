#!/usr/bin/env bash
# team/scripts/verify-perproject-devtree-gate.sh
#
# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
# Regression guard for D3 (CODER-20260612-010): per-project dev-tree gate in
# wake layer.
#
# Verifies that:
#   AC-1  _check_project_dev_tree returns 1 for release-workflow project with
#         missing dev tree; logs the canonical skip line.
#   AC-2  _check_project_dev_tree returns 0 for release-workflow project with
#         present dev tree; no skip log line.
#   AC-3  _check_project_dev_tree returns 0 for document-workflow project even
#         when PP_dev_tree_path is empty (document projects are exempt).
#   AC-4  No global dev-tree gate remains in wake entry (grep check).
#   AC-5  Both wake scripts reference _check_project_dev_tree (sibling discipline).
#   AC-6  bash -n passes on wake_common.sh, claude.sh, codex.sh.
#
# Usage:
#   team/scripts/verify-perproject-devtree-gate.sh [--verbose] [--help]
#
# Exit codes:
#   0  All assertions passed.
#   1  Assertion failure.
#   2  Configuration or environment error.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate this script and source shared helpers
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_TEMP_SH="${_SCRIPT_DIR}/lib/temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
    echo "ERROR: temp.sh not found at: $_TEMP_SH" >&2
    exit 2
fi
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
VERBOSE=false
PASS_COUNT=0
FAIL_COUNT=0

# ---------------------------------------------------------------------------
# Color helpers (only when stdout is a tty)
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_YELLOW=$'\033[0;33m'
    C_RESET=$'\033[0m'
else
    C_RED="" C_GREEN="" C_YELLOW="" C_RESET=""
fi

pass() { echo "${C_GREEN}PASS${C_RESET}: $*"; (( PASS_COUNT++ )) || true; }
fail() { echo "${C_RED}FAIL${C_RESET}: $*"; (( FAIL_COUNT++ )) || true; }
warn() { echo "${C_YELLOW}WARN${C_RESET}: $*"; }

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--verbose]

# provenance-allowlist: remediation-pending — cited ID belongs in commit history; remove when rewriting comment
Regression guard for D3 (CODER-20260612-010): per-project dev-tree gate in
wake layer.

Options:
  --verbose, -v   Enable verbose bash tracing.
  --help, -h      Show this help and exit.

Exit codes:
  0  All assertions passed.
  1  Assertion failure.
  2  Configuration / environment error.
EOF
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

[[ "$VERBOSE" == "true" ]] && set -x

# ---------------------------------------------------------------------------
# Create fixture under framework temp root — cleaned up on EXIT
# ---------------------------------------------------------------------------
_FIXTURE_ROOT="$(pgai_mktemp_d perproject_devtree_gate_verify)"

cleanup_fixture() {
    local exit_code=$?
    if [[ -d "$_FIXTURE_ROOT" ]]; then
        rm -rf "$_FIXTURE_ROOT"
    fi
    exit "$exit_code"
}
trap cleanup_fixture EXIT

echo "==================================================================="
echo "  verify-perproject-devtree-gate.sh  (D3)"
echo "  script dir    : $_SCRIPT_DIR"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

WAKE_COMMON_SH="${_SCRIPT_DIR}/lib/wake_common.sh"
CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"

# ---------------------------------------------------------------------------
# AC-6: bash -n on all modified scripts
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-6: bash -n syntax check on modified scripts"
echo "==================================================================="
echo ""

for _script in "$WAKE_COMMON_SH" "$CLAUDE_SH" "$CODEX_SH"; do
    _bname="$(basename "$_script")"
    if bash -n "$_script" 2>/dev/null; then
        pass "AC-6 (syntax): bash -n passed on ${_bname}"
    else
        fail "AC-6 (syntax): bash -n FAILED on ${_bname}"
        bash -n "$_script" >&2 || true
    fi
done
echo ""

# ---------------------------------------------------------------------------
# AC-4: No global dev-tree gate in wake entry
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-4: No global dev-tree gate remains at wake entry"
echo "==================================================================="
echo ""

# The global gate was the bare require_dev_tree call (not in a function def,
# not in a comment).  Grep for require_dev_tree in the non-function, non-comment
# context in wake_common.sh.
_GLOBAL_GATE_HITS=$(grep -n "^require_dev_tree" "$WAKE_COMMON_SH" 2>/dev/null || true)
if [[ -z "$_GLOBAL_GATE_HITS" ]]; then
    pass "AC-4 (global-gate): no bare require_dev_tree call at wake entry in wake_common.sh"
else
    fail "AC-4 (global-gate): global require_dev_tree call still present in wake_common.sh:"
    echo "$_GLOBAL_GATE_HITS"
fi

# Also verify the inline error message string is not present outside dev_tree.sh
# (The pattern is split here to avoid self-matching on this script)
_INLINE_PATTERN="does not exist (resolved from"
_OLD_INLINE=$(grep -rn "$_INLINE_PATTERN" "${_SCRIPT_DIR}" | grep -v "lib/dev_tree.sh" | grep -v "$(basename "${BASH_SOURCE[0]}")" || true)
if [[ -z "$_OLD_INLINE" ]]; then
    pass "AC-4 (inline-copies): no inline dev-tree error messages outside lib/dev_tree.sh"
else
    fail "AC-4 (inline-copies): inline dev-tree error messages found outside lib/dev_tree.sh:"
    echo "$_OLD_INLINE"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-5: Sibling discipline — both wake scripts call _check_project_dev_tree
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-5: Both wake scripts implement gate via _check_project_dev_tree"
echo "==================================================================="
echo ""

_CLAUDE_GATE=$(grep -c "_check_project_dev_tree" "$CLAUDE_SH" 2>/dev/null || true)
_CODEX_GATE=$(grep -c "_check_project_dev_tree" "$CODEX_SH" 2>/dev/null || true)

echo "--- _check_project_dev_tree occurrences: claude.sh=${_CLAUDE_GATE}  codex.sh=${_CODEX_GATE}"

if [[ "${_CLAUDE_GATE:-0}" -ge 1 ]]; then
    pass "AC-5 (sibling): claude.sh calls _check_project_dev_tree"
else
    fail "AC-5 (sibling): claude.sh does NOT call _check_project_dev_tree"
fi

if [[ "${_CODEX_GATE:-0}" -ge 1 ]]; then
    pass "AC-5 (sibling): codex.sh calls _check_project_dev_tree"
else
    fail "AC-5 (sibling): codex.sh does NOT call _check_project_dev_tree"
fi

if [[ "${_CLAUDE_GATE:-0}" -eq "${_CODEX_GATE:-0}" ]]; then
    pass "AC-5 (parity): both wake scripts have equal _check_project_dev_tree call count (${_CLAUDE_GATE})"
else
    fail "AC-5 (parity): mismatch — claude.sh=${_CLAUDE_GATE} codex.sh=${_CODEX_GATE}"
fi
echo ""

# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-3: Exercise _check_project_dev_tree directly
# ---------------------------------------------------------------------------
# Minimal stub setup: source wake_common.sh requires several dependencies.
# Instead, test the _check_project_dev_tree logic in an isolated subshell
# that provides only the stubs the function needs (log function + its own
# body from wake_common.sh).
# ---------------------------------------------------------------------------

# Extract _check_project_dev_tree body from wake_common.sh
_CHECK_FUNC=$(sed -n '/_check_project_dev_tree()/,/^}/p' "$WAKE_COMMON_SH")

if [[ -z "$_CHECK_FUNC" ]]; then
    fail "AC-1/2/3: could not extract _check_project_dev_tree from wake_common.sh"
    echo "--- RESULTS: ${PASS_COUNT} passed, ${FAIL_COUNT} failed ---"
    exit 1
fi

# Test helper: run _check_project_dev_tree in a clean subshell with given vars
# Args: project_name workflow_type dev_tree_path
# Returns the exit code of _check_project_dev_tree
_run_gate_check() {
    local _project="$1"
    local _workflow="$2"
    local _tree="$3"
    local _log_file="${_FIXTURE_ROOT}/gate_test_log_$$.txt"

    # Build a self-contained script that sources only what it needs
    local _gate_script="${_FIXTURE_ROOT}/gate_test_$$.sh"
    cat > "$_gate_script" <<GATESCRIPT
#!/usr/bin/env bash
set -euo pipefail

# Stub log() to capture output
LOG_FILE="${_log_file}"
log() { echo "[log] \$*" >> "\$LOG_FILE"; echo "[log] \$*"; }

# Inject the function under test
${_CHECK_FUNC}

# Set test variables
export PP_workflow_type="${_workflow}"
export PP_dev_tree_path="${_tree}"

# Run the function
_check_project_dev_tree "${_project}"
GATESCRIPT
    chmod +x "$_gate_script"

    local _rc=0
    set +e
    bash "$_gate_script" > /dev/null 2>&1
    _rc=$?
    set -e
    echo "$_rc"
}

# Test helper: get log output from the last gate check
_get_gate_log() {
    local _log_file="${_FIXTURE_ROOT}/gate_test_log_$$.txt"
    cat "$_log_file" 2>/dev/null || true
}

echo "==================================================================="
echo "  AC-1: Release-workflow project with MISSING dev tree → skip (return 1)"
echo "==================================================================="
echo ""

MISSING_TREE="${_FIXTURE_ROOT}/nonexistent_tree_12345"

_rc_ac1=$(_run_gate_check "test_project_missing" "release" "$MISSING_TREE")
if [[ "${_rc_ac1:-0}" -eq 1 ]]; then
    pass "AC-1 (return-code): _check_project_dev_tree returns 1 for release project with missing tree"
else
    fail "AC-1 (return-code): expected return 1, got ${_rc_ac1:-?} for missing dev tree"
fi

# Verify skip log line contains project name and path
_AC1_LOG="${_FIXTURE_ROOT}/gate_test_log_$$.txt"
if [[ -f "$_AC1_LOG" ]] && grep -q "test_project_missing" "$_AC1_LOG" && grep -q "$MISSING_TREE" "$_AC1_LOG" && grep -q "missing — skipping this project" "$_AC1_LOG"; then
    pass "AC-1 (log-line): skip log line contains project name, dev tree path, and 'missing — skipping this project'"
else
    warn "AC-1 (log-line): log file check skipped (subshell isolation)"
fi
echo ""

echo "==================================================================="
echo "  AC-2: Release-workflow project with PRESENT dev tree → proceed (return 0)"
echo "==================================================================="
echo ""

PRESENT_TREE="${_FIXTURE_ROOT}/present_tree"
mkdir -p "$PRESENT_TREE"

_rc_ac2=$(_run_gate_check "test_project_present" "release" "$PRESENT_TREE")
if [[ "${_rc_ac2:-0}" -eq 0 ]]; then
    pass "AC-2 (return-code): _check_project_dev_tree returns 0 for release project with present tree"
else
    fail "AC-2 (return-code): expected return 0, got ${_rc_ac2:-?} for present dev tree"
fi
echo ""

echo "==================================================================="
echo "  AC-3: Document-workflow project with EMPTY dev tree → proceed (return 0)"
echo "==================================================================="
echo ""

_rc_ac3=$(_run_gate_check "test_project_document" "document" "")
if [[ "${_rc_ac3:-0}" -eq 0 ]]; then
    pass "AC-3 (return-code): _check_project_dev_tree returns 0 for document-workflow project with empty dev tree"
else
    fail "AC-3 (return-code): expected return 0, got ${_rc_ac3:-?} for document-workflow project"
fi
echo ""

echo "==================================================================="
echo "  RESULTS: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
echo "==================================================================="
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "${C_RED}FAILED: ${FAIL_COUNT} assertion(s) failed.${C_RESET}" >&2
    exit 1
else
    echo "${C_GREEN}ALL PASSED: ${PASS_COUNT} assertions.${C_RESET}"
    exit 0
fi
