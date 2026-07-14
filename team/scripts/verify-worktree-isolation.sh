#!/usr/bin/env bash
# team/scripts/verify-worktree-isolation.sh
#
# End-to-end regression guard for the worktree-isolation feature.
# Mirrors the verify-temp-root-relocation.sh pattern: stands up a
# synthetic local git repo + RC branch fixture, exercises the worktree
# lifecycle helpers directly (create + merge + teardown), and asserts each
# acceptance criterion in the requirements brief.
#
# What this script asserts:
#   AC-a  A worktree existed under $(pgai_temp_dir)/worktrees/ during the task.
#   AC-b  After the task the worktree directory is gone and git worktree prune
#         reports nothing to clean up.
#   AC-c  The feature branch's commits landed on the canonical local RC branch
#         via a --no-ff merge commit.
#   AC-d  The canonical tree is parked off the RC branch with a clean working
#         tree after the simulated task.
#   AC-e  No /tmp/pgai_kanban_* litter remains under /tmp after the run.
#   AC-f  A simulated BLOCKED-path run also leaves no worktree litter (the
#         worktree is torn down on the BLOCKED path too, simulating operator
#         clean-up on the next wake cycle).
#
# Usage:
#   team/scripts/verify-worktree-isolation.sh [--kanban-root <path>]
#                                              [--verbose]
#                                              [--help]
#
# Options:
#   --kanban-root   Kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH or
#                   $HOME/pgai_agent_kanban).
#   --verbose       Enable verbose bash tracing.
#   --help, -h      Show this help and exit.
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
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

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

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
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
        --kanban-root)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --kanban-root requires a value." >&2
                exit 2
            fi
            KANBAN_ROOT="$2"
            shift 2
            ;;
        --kanban-root=*)
            KANBAN_ROOT="${1#--kanban-root=}"
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--kanban-root <path>] [--verbose]

End-to-end regression guard for the worktree-isolation feature.

Options:
  --kanban-root <path>  Kanban root (default: \$PGAI_AGENT_KANBAN_ROOT_PATH
                        or \$HOME/pgai_agent_kanban)
  --verbose, -v         Enable verbose output
  --help, -h            Show this help and exit

Exit codes:
  0  All assertions passed
  1  Assertion failure
  2  Configuration or environment error
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
# Snapshot /tmp pgai_kanban items before the run
# (for AC-e: detect NEW litter, not pre-existing items)
# ---------------------------------------------------------------------------
# anti-pattern-allowlist: 2 (justification: grep pattern for the litter-detection check, not a caller site bypassing the temp resolver)
_TMP_BEFORE="$(ls -A /tmp 2>/dev/null | grep -E 'pgai_kanban' || true)"

# ---------------------------------------------------------------------------
# Create fixture under framework temp root — cleaned up on EXIT
# ---------------------------------------------------------------------------
_FIXTURE_ROOT="$(pgai_mktemp_d worktree_isolation_verify)"

cleanup_fixture() {
    local exit_code=$?
    if [[ -d "$_FIXTURE_ROOT" ]]; then
        rm -rf "$_FIXTURE_ROOT"
    fi
    exit "$exit_code"
}
trap cleanup_fixture EXIT

echo "==================================================================="
echo "  verify-worktree-isolation.sh"
echo "  kanban root   : $KANBAN_ROOT"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

# ---------------------------------------------------------------------------
# build_fixture_repo <dir>
#
# Creates a minimal git repo with one commit on main and a local rc/v0.56.0
# branch. The worktree lifecycle functions operate against this repo.
# Git user config is set locally so no global git config is required.
# ---------------------------------------------------------------------------
build_fixture_repo() {
    local repo_dir="$1"
    mkdir -p "$repo_dir"

    git -C "$repo_dir" init -b main --quiet
    git -C "$repo_dir" config user.email "verify-worktree@test.local"
    git -C "$repo_dir" config user.name "VerifyWorktreeScript"

    echo "# Fixture repo for verify-worktree-isolation.sh" > "${repo_dir}/README.md"
    git -C "$repo_dir" add README.md
    git -C "$repo_dir" commit --quiet -m "Initial commit"

    # Create the RC branch that worktree lifecycle functions require.
    git -C "$repo_dir" branch "rc/v0.56.0"
}

# ---------------------------------------------------------------------------
# SCENARIO 1 — Happy path: create → do work → merge --no-ff → teardown
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  SCENARIO 1: Happy-path lifecycle (create + merge + teardown)"
echo "==================================================================="
echo ""

FIXTURE_REPO="${_FIXTURE_ROOT}/happy_path"
build_fixture_repo "$FIXTURE_REPO"

TASK_ID="verify-worktree-happy-path-$$"
RC_BRANCH="rc/v0.56.0"
FEATURE_BRANCH="feature/${TASK_ID}"

# Resolve the framework temp dir once for this run so assertions can check it.
FRAMEWORK_TEMP_DIR="$(pgai_temp_dir)"
EXPECTED_WORKTREE_PATH="${FRAMEWORK_TEMP_DIR}/worktrees/${TASK_ID}"

# ---- Step 1: create the worktree ----
echo "--- Creating worktree for task ${TASK_ID} ..."
CREATED_PATH=""
CREATED_PATH=$(
    create_task_worktree "$TASK_ID" "$RC_BRANCH" "$FEATURE_BRANCH" "$FIXTURE_REPO"
)
CREATE_EXIT=$?

if [[ $CREATE_EXIT -ne 0 ]]; then
    fail "AC-a: create_task_worktree exited $CREATE_EXIT — cannot continue scenario 1"
    echo ""
else
    # AC-a: worktree must exist under $(pgai_temp_dir)/worktrees/
    echo "--- AC-a: checking worktree exists under \$(pgai_temp_dir)/worktrees/ ..."
    if [[ -d "$CREATED_PATH" ]]; then
        pass "AC-a: worktree directory exists at ${CREATED_PATH}"
    else
        fail "AC-a: worktree directory not found at ${CREATED_PATH}"
    fi

    # AC-a (path check): worktree must be under framework temp, not under the canonical dev tree
    if [[ "$CREATED_PATH" == "${FRAMEWORK_TEMP_DIR}/worktrees/"* ]]; then
        pass "AC-a (path): worktree is under \$(pgai_temp_dir)/worktrees/ (not canonical tree)"
    else
        fail "AC-a (path): worktree path '${CREATED_PATH}' is NOT under ${FRAMEWORK_TEMP_DIR}/worktrees/"
    fi

    # ---- Step 2: commit a file in the worktree (simulated CODER work) ----
    echo "--- Committing work in worktree ..."
    echo "Simulated task output" > "${CREATED_PATH}/task-output.txt"
    git -C "$CREATED_PATH" add task-output.txt
    git -C "$CREATED_PATH" commit --quiet -m "feat: simulated CODER task work"

    # ---- Step 3: merge feature branch into RC branch with --no-ff ----
    echo "--- Merging ${FEATURE_BRANCH} into ${RC_BRANCH} via --no-ff ..."
    git -C "$FIXTURE_REPO" checkout "$RC_BRANCH" --quiet
    MERGE_OUTPUT=""
    MERGE_EXIT=0
    MERGE_OUTPUT=$(git -C "$FIXTURE_REPO" merge --no-ff "$FEATURE_BRANCH" -m "Merge feature branch ${FEATURE_BRANCH} into ${RC_BRANCH}" 2>&1) || MERGE_EXIT=$?

    if [[ $MERGE_EXIT -ne 0 ]]; then
        fail "AC-c: git merge --no-ff failed (exit ${MERGE_EXIT}): ${MERGE_OUTPUT}"
    else
        # AC-c: verify a --no-ff merge commit exists on RC branch
        # A --no-ff merge commit has exactly two parents (is a merge commit).
        MERGE_COMMIT_SHA=$(git -C "$FIXTURE_REPO" rev-parse HEAD)
        PARENT_COUNT=$(git -C "$FIXTURE_REPO" cat-file -p "$MERGE_COMMIT_SHA" | grep -c "^parent " || true)

        if [[ "$PARENT_COUNT" -ge 2 ]]; then
            pass "AC-c: --no-ff merge commit present on ${RC_BRANCH} (SHA: ${MERGE_COMMIT_SHA:0:8}, parents: ${PARENT_COUNT})"
        else
            fail "AC-c: commit ${MERGE_COMMIT_SHA:0:8} on ${RC_BRANCH} has ${PARENT_COUNT} parent(s) — expected a merge commit (>=2 parents)"
        fi

        # AC-c: feature branch commits must be reachable from RC branch
        FEATURE_COMMIT=$(git -C "$FIXTURE_REPO" rev-parse "${FEATURE_BRANCH}")
        if git -C "$FIXTURE_REPO" merge-base --is-ancestor "$FEATURE_COMMIT" HEAD 2>/dev/null; then
            pass "AC-c (reachability): feature branch commits are reachable from ${RC_BRANCH}"
        else
            fail "AC-c (reachability): feature branch commits are NOT reachable from ${RC_BRANCH}"
        fi
    fi

    # ---- Step 4: tear down the worktree ----
    # Worktree must be torn down BEFORE deleting the feature branch, because
    # git refuses to delete a branch that is checked out in an active worktree.
    echo "--- Tearing down worktree ..."
    TEARDOWN_EXIT=0
    teardown_task_worktree "$TASK_ID" "$FIXTURE_REPO" || TEARDOWN_EXIT=$?

    # ---- Step 5: delete the feature branch (CODER PHASE 2 step 8) ----
    git -C "$FIXTURE_REPO" branch -d "$FEATURE_BRANCH" 2>/dev/null || \
        warn "Feature branch deletion failed (branch already removed or -d guard triggered)"

    # AC-b: worktree directory must be gone
    if [[ ! -d "$CREATED_PATH" ]]; then
        pass "AC-b: worktree directory removed after teardown"
    else
        fail "AC-b: worktree directory still present at ${CREATED_PATH} after teardown"
    fi

    # AC-b: git worktree list must not show the removed worktree
    WT_LIST=$(git -C "$FIXTURE_REPO" worktree list 2>/dev/null || true)
    if echo "$WT_LIST" | grep -qF "$TASK_ID"; then
        fail "AC-b (prune): worktree '${TASK_ID}' still listed in 'git worktree list' after teardown"
    else
        pass "AC-b (prune): worktree '${TASK_ID}' not listed in 'git worktree list' after teardown"
    fi

    # AC-d: canonical tree must be on RC branch with a clean working tree
    CURRENT_BRANCH=$(git -C "$FIXTURE_REPO" symbolic-ref --short HEAD 2>/dev/null || echo "(detached)")
    if [[ "$CURRENT_BRANCH" == "$RC_BRANCH" ]]; then
        pass "AC-d (branch): canonical tree is parked on ${RC_BRANCH}"
    else
        fail "AC-d (branch): canonical tree is on '${CURRENT_BRANCH}', expected '${RC_BRANCH}'"
    fi

    DIRTY=$(git -C "$FIXTURE_REPO" status --porcelain 2>/dev/null || true)
    if [[ -z "$DIRTY" ]]; then
        pass "AC-d (clean tree): canonical working tree is clean"
    else
        fail "AC-d (clean tree): canonical working tree has uncommitted changes:\n${DIRTY}"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# SCENARIO 2 — BLOCKED path: create worktree, detect blocker, teardown
#
# Simulates the BLOCKED-path teardown AC-f:
#   - A worktree is created for the task.
#   - A simulated blocker is detected (merge conflict or missing prerequisite).
#   - The worktree is torn down on the next "wake cycle" (we call it directly).
#   - After teardown, no worktree litter remains.
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  SCENARIO 2: BLOCKED-path teardown (no worktree litter)"
echo "==================================================================="
echo ""

FIXTURE_REPO_B="${_FIXTURE_ROOT}/blocked_path"
build_fixture_repo "$FIXTURE_REPO_B"

TASK_ID_B="verify-worktree-blocked-path-$$"
FEATURE_BRANCH_B="feature/${TASK_ID_B}"
EXPECTED_WORKTREE_PATH_B="${FRAMEWORK_TEMP_DIR}/worktrees/${TASK_ID_B}"

# Create the worktree
echo "--- Creating worktree for BLOCKED task ${TASK_ID_B} ..."
CREATED_PATH_B=""
CREATED_PATH_B=$(
    create_task_worktree "$TASK_ID_B" "$RC_BRANCH" "$FEATURE_BRANCH_B" "$FIXTURE_REPO_B"
)
CREATE_EXIT_B=$?

if [[ $CREATE_EXIT_B -ne 0 ]]; then
    fail "AC-f: create_task_worktree for BLOCKED scenario exited $CREATE_EXIT_B — cannot continue"
else
    # Confirm worktree is present (pre-condition)
    if [[ -d "$CREATED_PATH_B" ]]; then
        echo "  worktree created at: ${CREATED_PATH_B}"
    else
        fail "AC-f: pre-condition failed — worktree not created at ${CREATED_PATH_B}"
    fi

    # Commit some partial work (partial CODER work before BLOCKED detection)
    echo "Partial work before BLOCKED detection" > "${CREATED_PATH_B}/partial.txt"
    git -C "$CREATED_PATH_B" add partial.txt
    git -C "$CREATED_PATH_B" commit --quiet -m "wip: partial work (task will be BLOCKED)"

    # Simulate BLOCKED detection — we do NOT merge (the conflict path)
    # On next wake cycle, teardown is called to reclaim space.
    echo "--- Simulating BLOCKED teardown (operator's next wake cycle) ..."
    TEARDOWN_EXIT_B=0
    teardown_task_worktree "$TASK_ID_B" "$FIXTURE_REPO_B" || TEARDOWN_EXIT_B=$?

    # AC-f: worktree must be gone after teardown on BLOCKED path
    if [[ ! -d "$CREATED_PATH_B" ]]; then
        pass "AC-f: BLOCKED-path worktree removed after teardown"
    else
        fail "AC-f: BLOCKED-path worktree still present at ${CREATED_PATH_B} after teardown"
    fi

    # AC-f: git worktree list must not show the removed worktree
    WT_LIST_B=$(git -C "$FIXTURE_REPO_B" worktree list 2>/dev/null || true)
    if echo "$WT_LIST_B" | grep -qF "$TASK_ID_B"; then
        fail "AC-f (prune): BLOCKED-path worktree '${TASK_ID_B}' still in 'git worktree list'"
    else
        pass "AC-f (prune): BLOCKED-path worktree '${TASK_ID_B}' not in 'git worktree list'"
    fi

    # AC-f: feature branch must still exist (BLOCKED policy: branch preserved)
    if git -C "$FIXTURE_REPO_B" rev-parse --verify "refs/heads/${FEATURE_BRANCH_B}" >/dev/null 2>&1; then
        pass "AC-f (branch preserved): feature branch '${FEATURE_BRANCH_B}' still exists after BLOCKED teardown"
    else
        fail "AC-f (branch preserved): feature branch '${FEATURE_BRANCH_B}' was unexpectedly deleted on BLOCKED path"
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# AC-e: no /tmp/pgai_kanban_* litter after the run
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-e: /tmp litter check"
echo "==================================================================="
echo ""
# anti-pattern-allowlist: 2 (justification: grep pattern used to detect litter after the run, not a caller site bypassing the temp resolver)
_TMP_AFTER="$(ls -A /tmp 2>/dev/null | grep -E 'pgai_kanban' || true)"
_TMP_NEW=""
if [[ -n "$_TMP_AFTER" ]]; then
    while IFS= read -r _item; do
        if [[ -z "$_item" ]]; then continue; fi
        if ! echo "$_TMP_BEFORE" | grep -qxF "$_item"; then
            _TMP_NEW="${_TMP_NEW}${_item}"$'\n'
        fi
    done <<< "$_TMP_AFTER"
fi
_TMP_NEW="${_TMP_NEW%$'\n'}"
AC_E_COUNT=0
[[ -n "$_TMP_NEW" ]] && AC_E_COUNT=$(echo "$_TMP_NEW" | grep -c .) || AC_E_COUNT=0
if [[ "$AC_E_COUNT" -eq 0 ]]; then
    pass "AC-e: no new /tmp/pgai_kanban_* litter created during the run"
    if [[ -n "$_TMP_BEFORE" ]]; then
        echo "  (note: pre-existing items were already present before the run)"
    fi
else
    fail "AC-e: ${AC_E_COUNT} new /tmp/pgai_kanban_* item(s) appeared during the run — temp isolation broken"
    echo "$_TMP_NEW" | while IFS= read -r f; do [[ -n "$f" ]] && echo "  stray: /tmp/$f"; done
fi
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  Results"
echo "==================================================================="
TOTAL_CHECKS=$((PASS_COUNT + FAIL_COUNT))
echo "  Checks passed : $PASS_COUNT / $TOTAL_CHECKS"
echo "  Checks failed : $FAIL_COUNT / $TOTAL_CHECKS"
echo ""

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo "${C_RED}VERDICT: FAIL — $FAIL_COUNT assertion(s) failed (see FAIL lines above)${C_RESET}"
    exit 1
fi

echo "${C_GREEN}VERDICT: PASS — all ${PASS_COUNT} assertions satisfied${C_RESET}"
exit 0
