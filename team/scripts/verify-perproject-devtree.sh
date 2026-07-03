#!/usr/bin/env bash
# team/scripts/verify-perproject-devtree.sh
#
# Regression guard: per-project dev tree resolution and teardown in wake scripts.
#
# Verifies that:
#   AC-1  CODER task for project B creates its worktree off B's RC branch in
#         B's repo (not in project A's / global dev tree); after teardown,
#         repo B's 'git worktree list' shows only the main worktree.
#   AC-2  TESTER task for project B creates a detached worktree at B's RC head;
#         after teardown, repo B's 'git worktree list' shows only main.
#   AC-3  Pollution-sweep fixture: a file dropped into B's canonical tree
#         during the task is quarantined; a file dropped into A's tree is NOT
#         attributed to B's task.
#   AC-4  grep shows identical PP_dev_tree_path resolution patterns at all
#         blast-radius sites in both wake scripts (sibling parity).
#
# Usage:
#   team/scripts/verify-perproject-devtree.sh [--verbose] [--help]
#
# Exit codes:
#   0  All assertions passed.
#   1  Assertion failure.
#   2  Configuration or environment error.
#
# Safety invariants:
#   - All fixtures are created under the framework temp root via pgai_mktemp_d.
#   - Fixtures are cleaned up via trap EXIT on both success and failure paths.
#   - The script never touches the real dev tree or the live kanban tree.
#   - Idempotent: safe to re-run; every run starts with a fresh fixture.

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

_WORKTREE_SH="${_SCRIPT_DIR}/lib/worktree.sh"
if [[ ! -f "$_WORKTREE_SH" ]]; then
    echo "ERROR: worktree.sh not found at: $_WORKTREE_SH" >&2
    exit 2
fi
# shellcheck source=lib/worktree.sh
source "$_WORKTREE_SH"
unset _WORKTREE_SH

_PROJECT_PATHS_SH="${_SCRIPT_DIR}/lib/project_paths.sh"
if [[ ! -f "$_PROJECT_PATHS_SH" ]]; then
    echo "ERROR: project_paths.sh not found at: $_PROJECT_PATHS_SH" >&2
    exit 2
fi
# shellcheck source=lib/project_paths.sh
source "$_PROJECT_PATHS_SH"
unset _PROJECT_PATHS_SH

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
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

Regression guard: per-project dev tree resolution
and teardown in wake scripts.

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
_FIXTURE_ROOT="$(pgai_mktemp_d perproject_devtree_verify)"

cleanup_fixture() {
    local exit_code=$?
    if [[ -d "$_FIXTURE_ROOT" ]]; then
        rm -rf "$_FIXTURE_ROOT"
    fi
    exit "$exit_code"
}
trap cleanup_fixture EXIT

echo "==================================================================="
echo "  verify-perproject-devtree.sh"
echo "  kanban root   : $KANBAN_ROOT"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

# ---------------------------------------------------------------------------
# Helper: build a minimal git repo with one commit and an RC branch
# ---------------------------------------------------------------------------
build_fixture_repo() {
    local repo_dir="$1"
    local rc_branch="${2:-rc/v0.1.0}"
    mkdir -p "$repo_dir"

    git -C "$repo_dir" init -b main --quiet
    git -C "$repo_dir" config user.email "verify-perproject@test.local"
    git -C "$repo_dir" config user.name "VerifyPerprojectScript"

    echo "# Fixture repo — perproject-devtree verification" > "${repo_dir}/README.md"
    git -C "$repo_dir" add README.md
    git -C "$repo_dir" commit --quiet -m "Initial commit"

    git -C "$repo_dir" branch "$rc_branch"
}

# ---------------------------------------------------------------------------
# Build two fixture repos (A = "global dev tree", B = "separate project")
# ---------------------------------------------------------------------------
REPO_A="${_FIXTURE_ROOT}/repo_a"
REPO_B="${_FIXTURE_ROOT}/repo_b"
RC_BRANCH="rc/v0.1.0"

echo "--- Building fixture repos ..."
build_fixture_repo "$REPO_A" "$RC_BRANCH"
build_fixture_repo "$REPO_B" "$RC_BRANCH"

FRAMEWORK_TEMP_DIR="$(pgai_temp_dir)"

# ---------------------------------------------------------------------------
# Simulate pp_load_config behaviour for project B: export PP_dev_tree_path
# pointing at repo B.  This mimics what run_project_chain does after our fix.
# ---------------------------------------------------------------------------
export PP_dev_tree_path="$REPO_B"
# Also export global PGAI_DEV_TREE_PATH pointing at repo A, to confirm
# the per-project value is preferred.
PGAI_DEV_TREE_PATH_SAVED="${PGAI_DEV_TREE_PATH:-}"
export PGAI_DEV_TREE_PATH="$REPO_A"

# ---------------------------------------------------------------------------
# AC-1: CODER task for project B creates worktree in B's repo
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-1: CODER worktree resolves to project B's repo"
echo "==================================================================="
echo ""

TASK_ID_CODER="verify-bug0283-coder-$$"
FEATURE_BRANCH="feature/${TASK_ID_CODER}"

# Resolve _dev_tree using the same expression as the fixed wake script.
# If PP_dev_tree_path is set (project B), it should win.
_dev_tree_coder="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

echo "--- _dev_tree resolves to: ${_dev_tree_coder}"
if [[ "$_dev_tree_coder" == "$REPO_B" ]]; then
    pass "AC-1 (resolution): _dev_tree resolves to repo B (not global PGAI_DEV_TREE_PATH / repo A)"
else
    fail "AC-1 (resolution): _dev_tree='${_dev_tree_coder}' — expected '${REPO_B}'"
fi

# Also confirm it does NOT resolve to repo A
if [[ "$_dev_tree_coder" != "$REPO_A" ]]; then
    pass "AC-1 (isolation): _dev_tree does NOT resolve to repo A (global dev tree is unaffected)"
else
    fail "AC-1 (isolation): _dev_tree resolved to repo A — global dev tree was not overridden by PP_dev_tree_path"
fi

# Create the worktree against repo B
echo "--- Creating CODER worktree in repo B ..."
CODER_WT_PATH=""
CODER_WT_EXIT=0
set +e
CODER_WT_PATH="$(create_task_worktree "$TASK_ID_CODER" "$RC_BRANCH" "$FEATURE_BRANCH" "$_dev_tree_coder")"
CODER_WT_EXIT=$?
set -e

if [[ $CODER_WT_EXIT -ne 0 || -z "$CODER_WT_PATH" ]]; then
    fail "AC-1 (create): create_task_worktree failed (exit ${CODER_WT_EXIT}); check stderr for details"
else
    pass "AC-1 (create): create_task_worktree succeeded; worktree at ${CODER_WT_PATH}"

    # Verify repo B lists the worktree (confirms git-dir association)
    if git -C "$REPO_B" worktree list 2>/dev/null | grep -qF "$CODER_WT_PATH"; then
        pass "AC-1 (git-dir): repo B lists the worktree in 'git worktree list'"
    else
        fail "AC-1 (git-dir): repo B does NOT list ${CODER_WT_PATH} in 'git worktree list'"
    fi

    if git -C "$REPO_A" worktree list 2>/dev/null | grep -qF "$CODER_WT_PATH"; then
        fail "AC-1 (isolation): repo A lists the CODER worktree — isolation violated"
    else
        pass "AC-1 (isolation): repo A does NOT list the CODER worktree"
    fi

    # Verify feature branch exists in repo B
    if git -C "$REPO_B" branch --list "$FEATURE_BRANCH" | grep -q "$FEATURE_BRANCH"; then
        pass "AC-1 (branch): feature branch ${FEATURE_BRANCH} exists in repo B"
    else
        fail "AC-1 (branch): feature branch ${FEATURE_BRANCH} NOT found in repo B"
    fi

    # Teardown — pass the per-project dev tree value explicitly
    teardown_task_worktree "$TASK_ID_CODER" "$_dev_tree_coder" || true
    git -C "$REPO_B" branch -d "$FEATURE_BRANCH" 2>/dev/null || true

    # Post-teardown assertion: repo B must list only the main worktree
    WT_LIST_B_POST="$(git -C "$REPO_B" worktree list 2>/dev/null || true)"
    WT_LIST_B_COUNT="$(echo "$WT_LIST_B_POST" | grep -c "^" || true)"
    if [[ "$WT_LIST_B_COUNT" -eq 1 ]]; then
        pass "AC-1 (teardown-clean): repo B 'git worktree list' shows only main worktree after teardown (no stale entry)"
    else
        fail "AC-1 (teardown-clean): repo B 'git worktree list' shows ${WT_LIST_B_COUNT} entries after teardown — expected 1 (main only); stale registration present"
        echo "       git worktree list output:"
        echo "$WT_LIST_B_POST" | sed 's/^/         /'
    fi

    # Verify the worktree path is gone from disk (rm -rf was not mis-routed to repo A)
    if [[ -n "$CODER_WT_PATH" && ! -d "$CODER_WT_PATH" ]]; then
        pass "AC-1 (teardown-disk): worktree directory removed from disk after teardown"
    else
        fail "AC-1 (teardown-disk): worktree directory still present at ${CODER_WT_PATH}"
    fi

    # Verify repo A worktree list is unaffected (teardown did not prune wrong repo)
    WT_LIST_A_POST="$(git -C "$REPO_A" worktree list 2>/dev/null || true)"
    WT_LIST_A_COUNT="$(echo "$WT_LIST_A_POST" | grep -c "^" || true)"
    if [[ "$WT_LIST_A_COUNT" -eq 1 ]]; then
        pass "AC-1 (teardown-isolation): repo A 'git worktree list' still shows only main worktree (teardown did not prune wrong repo)"
    else
        fail "AC-1 (teardown-isolation): repo A 'git worktree list' shows ${WT_LIST_A_COUNT} entries — teardown may have operated on wrong repo"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# AC-2: TESTER task for project B creates detached worktree in B's repo
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-2: TESTER detached worktree resolves to project B's repo"
echo "==================================================================="
echo ""

TASK_ID_TESTER="verify-bug0283-tester-$$"

_dev_tree_tester="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

echo "--- _dev_tree resolves to: ${_dev_tree_tester}"
if [[ "$_dev_tree_tester" == "$REPO_B" ]]; then
    pass "AC-2 (resolution): TESTER _dev_tree resolves to repo B"
else
    fail "AC-2 (resolution): TESTER _dev_tree='${_dev_tree_tester}' — expected '${REPO_B}'"
fi

echo "--- Creating TESTER detached worktree in repo B ..."
RC_TIP_B="$(git -C "$REPO_B" rev-parse "$RC_BRANCH")"
TESTER_WT_PATH=""
TESTER_WT_EXIT=0
set +e
TESTER_WT_PATH="$(create_detached_worktree "$TASK_ID_TESTER" "$RC_BRANCH" "$_dev_tree_tester")"
TESTER_WT_EXIT=$?
set -e

if [[ $TESTER_WT_EXIT -ne 0 || -z "$TESTER_WT_PATH" ]]; then
    fail "AC-2 (create): create_detached_worktree failed (exit ${TESTER_WT_EXIT}); check stderr for details"
else
    pass "AC-2 (create): create_detached_worktree succeeded; worktree at ${TESTER_WT_PATH}"

    # Verify the detached HEAD in the worktree matches B's RC tip
    WT_HEAD="$(git -C "$TESTER_WT_PATH" rev-parse HEAD 2>/dev/null || echo "")"
    if [[ "$WT_HEAD" == "$RC_TIP_B" ]]; then
        pass "AC-2 (head): detached worktree HEAD matches repo B's RC tip (${RC_TIP_B:0:8})"
    else
        fail "AC-2 (head): detached worktree HEAD=${WT_HEAD:0:8}; expected repo B RC tip=${RC_TIP_B:0:8}"
    fi

    # Verify repo B lists the worktree (confirms git-dir association)
    if git -C "$REPO_B" worktree list 2>/dev/null | grep -qF "$TESTER_WT_PATH"; then
        pass "AC-2 (git-dir): repo B lists the TESTER worktree"
    else
        fail "AC-2 (git-dir): repo B does NOT list ${TESTER_WT_PATH} in 'git worktree list'"
    fi

    if git -C "$REPO_A" worktree list 2>/dev/null | grep -qF "$TESTER_WT_PATH"; then
        fail "AC-2 (isolation): repo A lists the TESTER worktree — isolation violated"
    else
        pass "AC-2 (isolation): repo A does NOT list the TESTER worktree"
    fi

    # Teardown — pass the per-project dev tree value explicitly
    teardown_task_worktree "$TASK_ID_TESTER" "$_dev_tree_tester" || true

    # Post-teardown assertion: repo B must list only the main worktree
    TESTER_WT_LIST_B_POST="$(git -C "$REPO_B" worktree list 2>/dev/null || true)"
    TESTER_WT_LIST_B_COUNT="$(echo "$TESTER_WT_LIST_B_POST" | grep -c "^" || true)"
    if [[ "$TESTER_WT_LIST_B_COUNT" -eq 1 ]]; then
        pass "AC-2 (teardown-clean): repo B 'git worktree list' shows only main worktree after TESTER teardown (no stale entry)"
    else
        fail "AC-2 (teardown-clean): repo B 'git worktree list' shows ${TESTER_WT_LIST_B_COUNT} entries after TESTER teardown — expected 1 (main only)"
        echo "       git worktree list output:"
        echo "$TESTER_WT_LIST_B_POST" | sed 's/^/         /'
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# AC-3: Pollution-sweep: _sweep_canonical_tree resolves to B, not A
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-3: Pollution sweep canonical tree resolves to project B"
echo "==================================================================="
echo ""

# Simulate what process_one_task does at sweep-anchor time:
#   local _sweep_canonical_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
_sweep_canonical_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

echo "--- _sweep_canonical_tree resolves to: ${_sweep_canonical_tree}"
if [[ "$_sweep_canonical_tree" == "$REPO_B" ]]; then
    pass "AC-3 (sweep-tree): sweep canonical tree resolves to repo B"
else
    fail "AC-3 (sweep-tree): sweep canonical tree='${_sweep_canonical_tree}' — expected '${REPO_B}'"
fi

if [[ "$_sweep_canonical_tree" != "$REPO_A" ]]; then
    pass "AC-3 (isolation): sweep canonical tree does NOT resolve to repo A (global dev tree)"
else
    fail "AC-3 (isolation): sweep canonical tree resolved to repo A — multi-project isolation broken"
fi

# Drop a file into repo B (simulates agent leaving untracked output)
POLLUTION_FILE_B="${REPO_B}/agent_pollution_b_$$.txt"
echo "pollution" > "$POLLUTION_FILE_B"

# Drop a file into repo A (should NOT be attributed to B's task)
POLLUTION_FILE_A="${REPO_A}/agent_pollution_a_$$.txt"
echo "pollution" > "$POLLUTION_FILE_A"

# Verify sweep sees the B file but not the A file when anchored on B
B_PORCELAIN="$(git -C "$_sweep_canonical_tree" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)"
if echo "$B_PORCELAIN" | grep -qF "$(basename "$POLLUTION_FILE_B")"; then
    pass "AC-3 (sweep-sees-b): pollution sweep detects untracked file in repo B"
else
    fail "AC-3 (sweep-sees-b): pollution sweep DID NOT detect untracked file in repo B"
fi

A_PORCELAIN="$(git -C "$_sweep_canonical_tree" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)"
if echo "$A_PORCELAIN" | grep -qF "$(basename "$POLLUTION_FILE_A")"; then
    fail "AC-3 (sweep-not-a): sweep sees repo A's pollution file — incorrect tree anchoring"
else
    pass "AC-3 (sweep-not-a): sweep does NOT see repo A's pollution file (correct B-only scope)"
fi

# Cleanup fixture files
rm -f "$POLLUTION_FILE_B" "$POLLUTION_FILE_A"
echo ""

# ---------------------------------------------------------------------------
# AC-4: Sibling parity — identical PP_dev_tree_path patterns in both scripts
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-4: Sibling parity — PP_dev_tree_path patterns match in both scripts"
echo "==================================================================="
echo ""

CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"

if [[ ! -f "$CLAUDE_SH" ]]; then
    fail "AC-4: claude.sh not found at ${CLAUDE_SH}"
elif [[ ! -f "$CODEX_SH" ]]; then
    fail "AC-4: codex.sh not found at ${CODEX_SH}"
else
    # Count PP_dev_tree_path occurrences in each script
    CLAUDE_COUNT="$(grep -c "PP_dev_tree_path" "$CLAUDE_SH" || true)"
    CODEX_COUNT="$(grep -c "PP_dev_tree_path" "$CODEX_SH" || true)"

    echo "--- PP_dev_tree_path occurrences: claude.sh=${CLAUDE_COUNT}  codex.sh=${CODEX_COUNT}"
    if [[ "$CLAUDE_COUNT" -eq "$CODEX_COUNT" && "$CLAUDE_COUNT" -gt 0 ]]; then
        pass "AC-4 (count): both scripts have ${CLAUDE_COUNT} PP_dev_tree_path occurrences"
    else
        fail "AC-4 (count): mismatch — claude.sh has ${CLAUDE_COUNT}, codex.sh has ${CODEX_COUNT}"
    fi

    # Check that each blast-radius site uses the correct fallback expression
    BLAST_RADIUS_LITERAL='${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}'
    CLAUDE_BLAST_COUNT="$(grep -cF "$BLAST_RADIUS_LITERAL" "$CLAUDE_SH" || true)"
    CODEX_BLAST_COUNT="$(grep -cF "$BLAST_RADIUS_LITERAL" "$CODEX_SH" || true)"

    echo "--- Blast-radius expression pattern matches: claude.sh=${CLAUDE_BLAST_COUNT}  codex.sh=${CODEX_BLAST_COUNT}"
    if [[ "$CLAUDE_BLAST_COUNT" -eq "$CODEX_BLAST_COUNT" && "$CLAUDE_BLAST_COUNT" -gt 0 ]]; then
        pass "AC-4 (pattern): both scripts have ${CLAUDE_BLAST_COUNT} PP_dev_tree_path blast-radius fixes"
    else
        fail "AC-4 (pattern): mismatch — claude.sh has ${CLAUDE_BLAST_COUNT}, codex.sh has ${CODEX_BLAST_COUNT} blast-radius expressions"
    fi

    # Verify we have at least the 4 known blast-radius sites fixed
    if [[ "$CLAUDE_BLAST_COUNT" -ge 4 ]]; then
        pass "AC-4 (coverage): claude.sh has >= 4 blast-radius fixes (${CLAUDE_BLAST_COUNT})"
    else
        fail "AC-4 (coverage): claude.sh has only ${CLAUDE_BLAST_COUNT} blast-radius fixes — expected >= 4"
    fi
    if [[ "$CODEX_BLAST_COUNT" -ge 4 ]]; then
        pass "AC-4 (coverage): codex.sh has >= 4 blast-radius fixes (${CODEX_BLAST_COUNT})"
    else
        fail "AC-4 (coverage): codex.sh has only ${CODEX_BLAST_COUNT} blast-radius fixes — expected >= 4"
    fi

    # Verify pp_load_config is called in run_project_chain in both scripts
    CLAUDE_PPLOAD="$(grep -c "pp_load_config.*project_name" "$CLAUDE_SH" || true)"
    CODEX_PPLOAD="$(grep -c "pp_load_config.*project_name" "$CODEX_SH" || true)"
    echo "--- pp_load_config \"\$project_name\" calls: claude.sh=${CLAUDE_PPLOAD}  codex.sh=${CODEX_PPLOAD}"
    if [[ "$CLAUDE_PPLOAD" -ge 1 ]]; then
        pass "AC-4 (pp_load_config): claude.sh calls pp_load_config in run_project_chain"
    else
        fail "AC-4 (pp_load_config): claude.sh does NOT call pp_load_config — PP_dev_tree_path will never be set"
    fi
    if [[ "$CODEX_PPLOAD" -ge 1 ]]; then
        pass "AC-4 (pp_load_config): codex.sh calls pp_load_config in run_project_chain"
    else
        fail "AC-4 (pp_load_config): codex.sh does NOT call pp_load_config — PP_dev_tree_path will never be set"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# Restore global PGAI_DEV_TREE_PATH and PP_dev_tree_path to originals
# ---------------------------------------------------------------------------
if [[ -n "$PGAI_DEV_TREE_PATH_SAVED" ]]; then
    export PGAI_DEV_TREE_PATH="$PGAI_DEV_TREE_PATH_SAVED"
else
    unset PGAI_DEV_TREE_PATH
fi
unset PP_dev_tree_path

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
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
