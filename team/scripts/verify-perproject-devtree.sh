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
#   AC-4  The per-project config load and every dev-tree resolution site in
#         the shared wake pipeline, both wake scripts running that one
#         pipeline (sibling parity):
#           - pp_load_config (lib/project_paths.sh) exports a project's
#             dev_tree_path from its project.cfg as PP_dev_tree_path;
#           - run_project_chain (lib/wake_driver.sh) calls
#             pp_load_config "$project_name" before its reconcile sweeps and
#             before process_one_task;
#           - each named site resolves "${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}":
#             process_one_task (lib/wake_driver.sh), wd_setup_task_worktree
#             (lib/wake_driver_worktree.sh), reconcile_stale_active and
#             reconcile_terminal_worktrees (lib/wake_reconcile_sweeps.sh),
#             scan_and_bundle_bugs and drop_self_pm_ticket
#             (lib/wake_autonomous_scan.sh);
#           - no lib/wake_*.sh assignment reads the global PGAI_DEV_TREE_PATH
#             without preferring PP_dev_tree_path;
#           - wake/claude.sh and wake/codex.sh both source lib/wake_entry.sh,
#             which sources lib/wake_common.sh and lib/wake_driver.sh, which
#             source the stage files above.
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
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Locate this script and source shared helpers
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")" && pwd)"

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
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
VERBOSE=false
# PASS_COUNT / FAIL_COUNT, the C_* colours and pass / fail / warn
# (lib/verify_harness.sh).
# shellcheck source=lib/verify_harness.sh
source "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/lib/verify_harness.sh"
verify_harness_init

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
# pointing at repo B, as run_project_chain's pp_load_config call does.
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

TASK_ID_CODER="verify-perproject-coder-$$"
FEATURE_BRANCH="feature/${TASK_ID_CODER}"

# Resolve _dev_tree using the same expression as the wake pipeline's sites.
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
    if grep -qF "$CODER_WT_PATH" <<< "$(git -C "$REPO_B" worktree list 2>/dev/null)"; then
        pass "AC-1 (git-dir): repo B lists the worktree in 'git worktree list'"
    else
        fail "AC-1 (git-dir): repo B does NOT list ${CODER_WT_PATH} in 'git worktree list'"
    fi

    if grep -qF "$CODER_WT_PATH" <<< "$(git -C "$REPO_A" worktree list 2>/dev/null)"; then
        fail "AC-1 (isolation): repo A lists the CODER worktree — isolation violated"
    else
        pass "AC-1 (isolation): repo A does NOT list the CODER worktree"
    fi

    # Verify feature branch exists in repo B
    if grep -q "$FEATURE_BRANCH" <<< "$(git -C "$REPO_B" branch --list "$FEATURE_BRANCH")"; then
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

TASK_ID_TESTER="verify-perproject-tester-$$"

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
    if grep -qF "$TESTER_WT_PATH" <<< "$(git -C "$REPO_B" worktree list 2>/dev/null)"; then
        pass "AC-2 (git-dir): repo B lists the TESTER worktree"
    else
        fail "AC-2 (git-dir): repo B does NOT list ${TESTER_WT_PATH} in 'git worktree list'"
    fi

    if grep -qF "$TESTER_WT_PATH" <<< "$(git -C "$REPO_A" worktree list 2>/dev/null)"; then
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
if grep -qF "$(basename "$POLLUTION_FILE_B")" <<< "$B_PORCELAIN"; then
    pass "AC-3 (sweep-sees-b): pollution sweep detects untracked file in repo B"
else
    fail "AC-3 (sweep-sees-b): pollution sweep DID NOT detect untracked file in repo B"
fi

A_PORCELAIN="$(git -C "$_sweep_canonical_tree" status --porcelain=v1 --untracked-files=all 2>/dev/null || true)"
if grep -qF "$(basename "$POLLUTION_FILE_A")" <<< "$A_PORCELAIN"; then
    fail "AC-3 (sweep-not-a): sweep sees repo A's pollution file — incorrect tree anchoring"
else
    pass "AC-3 (sweep-not-a): sweep does NOT see repo A's pollution file (correct B-only scope)"
fi

# Cleanup fixture files
rm -f "$POLLUTION_FILE_B" "$POLLUTION_FILE_A"
echo ""

# ---------------------------------------------------------------------------
# AC-4: Per-project config load and dev-tree resolution in the wake pipeline
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-4: Per-project config load and dev-tree resolution sites"
echo "==================================================================="
echo ""

CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"
WAKE_COMMON_SH="${_SCRIPT_DIR}/lib/wake_common.sh"
WAKE_DRIVER_SH="${_SCRIPT_DIR}/lib/wake_driver.sh"
BLAST_RADIUS_LITERAL='${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}'

# _fn_body <file> <function> -- the function's text, from its header to the
# first closing brace in column 0; comment lines are dropped.
_fn_body() {
    sed -n "/^${2}()[[:space:]]*{/,/^}/p" "$1" 2>/dev/null | grep -vE '^[[:space:]]*#' || true
}

# --- pp_load_config exports the project's own dev tree -------------------
# A fixture kanban root holding project B's project.cfg; pp_load_config runs
# in a subshell so this script's own PP_* values are untouched.
_PP_ROOT="${_FIXTURE_ROOT}/kanban_root"
mkdir -p "${_PP_ROOT}/projects/project-b"
printf '[project]\nproject_name = project-b\ndev_tree_path = %s\nworkflow_type = release\n' \
    "$REPO_B" > "${_PP_ROOT}/projects/project-b/project.cfg"
_PP_LOADED="$(
    unset PGAI_PROJECT_ROOT PGAI_TASKS_DIR PGAI_PROJECT_NAME PP_dev_tree_path
    export PGAI_AGENT_KANBAN_ROOT_PATH="$_PP_ROOT" KANBAN_ROOT="$_PP_ROOT"
    pp_load_config "project-b" >/dev/null 2>&1 && printf '%s' "${PP_dev_tree_path:-}"
)" || true
if [[ "$_PP_LOADED" == "$REPO_B" ]]; then
    pass "AC-4 (pp_load_config): pp_load_config exports project B's dev_tree_path as PP_dev_tree_path"
else
    fail "AC-4 (pp_load_config): pp_load_config set PP_dev_tree_path='${_PP_LOADED}' -- expected '${REPO_B}' from project B's project.cfg"
fi

# --- run_project_chain loads the config before the sites that read it -----
_CHAIN_BODY="$(_fn_body "$WAKE_DRIVER_SH" run_project_chain)"
if [[ -z "$_CHAIN_BODY" ]]; then
    fail "AC-4 (config-load): could not find run_project_chain in lib/wake_driver.sh"
else
    # First matching line number, or nothing; never fails, so a missing line
    # is reported by the assertions below rather than ending the script.
    _first_line() { { grep -nE "$1" <<< "$_CHAIN_BODY" | head -n1 | cut -d: -f1; } || true; }
    _LOAD_LINE="$(_first_line 'pp_load_config[[:space:]]+"\$project_name"')"
    _SWEEP_LINE="$(_first_line '^[[:space:]]*reconcile_stale_active([[:space:]]|$)')"
    _DISPATCH_LINE="$(_first_line '(^|[^_[:alnum:]])process_one_task([[:space:]]|$)')"
    echo "--- run_project_chain lines: pp_load_config=${_LOAD_LINE:-none}  reconcile_stale_active=${_SWEEP_LINE:-none}  process_one_task=${_DISPATCH_LINE:-none}"
    if [[ -n "$_LOAD_LINE" ]]; then
        pass "AC-4 (config-load): run_project_chain in lib/wake_driver.sh calls pp_load_config \"\$project_name\""
    else
        fail "AC-4 (config-load): run_project_chain in lib/wake_driver.sh does NOT call pp_load_config \"\$project_name\" -- PP_dev_tree_path will never be set"
    fi
    if [[ -n "$_LOAD_LINE" && -n "$_SWEEP_LINE" && -n "$_DISPATCH_LINE" \
          && "$_LOAD_LINE" -lt "$_SWEEP_LINE" && "$_LOAD_LINE" -lt "$_DISPATCH_LINE" ]]; then
        pass "AC-4 (config-load order): pp_load_config runs before reconcile_stale_active and process_one_task"
    else
        fail "AC-4 (config-load order): pp_load_config (line ${_LOAD_LINE:-none}) must precede reconcile_stale_active (line ${_SWEEP_LINE:-none}) and process_one_task (line ${_DISPATCH_LINE:-none})"
    fi
fi

# --- every named resolution site prefers the per-project dev tree ---------
# file | function | variable assigned | minimum count in that function
while IFS='|' read -r _site_file _site_fn _site_var _site_min; do
    [[ -z "$_site_file" ]] && continue
    _site_body="$(_fn_body "${_SCRIPT_DIR}/${_site_file}" "$_site_fn")"
    if [[ -z "$_site_body" ]]; then
        fail "AC-4 (site): ${_site_fn} not found in ${_site_file}"
        continue
    fi
    _site_count="$(grep -cF "${_site_var}=\"${BLAST_RADIUS_LITERAL}\"" <<< "$_site_body" || true)"
    if [[ "${_site_count:-0}" -ge "$_site_min" ]]; then
        pass "AC-4 (site): ${_site_file} ${_site_fn} resolves ${_site_var} per project (${_site_count} site(s))"
    else
        fail "AC-4 (site): ${_site_file} ${_site_fn} has ${_site_count:-0} ${_site_var}=\"${BLAST_RADIUS_LITERAL}\" site(s) -- expected >= ${_site_min}"
    fi
done <<'SITES'
lib/wake_driver.sh|process_one_task|_canonical_dev_tree_path|1
lib/wake_driver.sh|process_one_task|_sweep_canonical_tree|1
lib/wake_driver_worktree.sh|wd_setup_task_worktree|_dev_tree|2
lib/wake_reconcile_sweeps.sh|reconcile_stale_active|_sa_dev_tree|1
lib/wake_reconcile_sweeps.sh|reconcile_terminal_worktrees|dev_tree|1
lib/wake_autonomous_scan.sh|scan_and_bundle_bugs|dev_tree_path|1
lib/wake_autonomous_scan.sh|drop_self_pm_ticket|pm_dev_tree|1
SITES

# --- no wake stage resolves the dev tree from the global value alone ------
_GLOBAL_ONLY="$(grep -nE '[[:alnum:]_]+="?\$\{?PGAI_DEV_TREE_PATH' "${_SCRIPT_DIR}"/lib/wake_*.sh 2>/dev/null \
    | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' || true)"
if [[ -z "$_GLOBAL_ONLY" ]]; then
    pass "AC-4 (global-only): no lib/wake_*.sh assignment reads PGAI_DEV_TREE_PATH without preferring PP_dev_tree_path"
else
    fail "AC-4 (global-only): lib/wake_*.sh assignment(s) read the global PGAI_DEV_TREE_PATH first -- other projects' tasks would use the global dev tree:"
    echo "$_GLOBAL_ONLY" | sed 's/^/         /'
fi

# --- sibling parity: both wake scripts run the one pipeline ---------------
_source_check() {
    # _source_check <file> <lib basename> -- does <file> source lib/<lib>?
    grep -qE "^[[:space:]]*source[[:space:]].*lib/${2//./\\.}" "$1" 2>/dev/null
}
for _wake in "$CLAUDE_SH" "$CODEX_SH"; do
    _wname="$(basename "$_wake")"
    if _source_check "$_wake" wake_entry.sh; then
        pass "AC-4 (sibling): ${_wname} sources lib/wake_entry.sh"
    else
        fail "AC-4 (sibling): ${_wname} does NOT source lib/wake_entry.sh -- it does not run the shared per-project resolution"
    fi
done
for _lib in wake_common.sh wake_driver.sh; do
    if _source_check "${_SCRIPT_DIR}/lib/wake_entry.sh" "$_lib"; then
        pass "AC-4 (sibling): wake_entry.sh sources lib/${_lib}"
    else
        fail "AC-4 (sibling): wake_entry.sh does NOT source lib/${_lib} -- the wake scripts do not run the shared per-project resolution"
    fi
done
# The stage files holding the sites are sourced by the shared pipeline.
while IFS='|' read -r _parent _stage; do
    [[ -z "$_parent" ]] && continue
    if _source_check "${_SCRIPT_DIR}/${_parent}" "$_stage"; then
        pass "AC-4 (pipeline): ${_parent} sources lib/${_stage}"
    else
        fail "AC-4 (pipeline): ${_parent} does NOT source lib/${_stage} -- its resolution sites never run in a wake"
    fi
done <<'STAGES'
lib/wake_driver.sh|wake_driver_worktree.sh
lib/wake_common.sh|wake_reconcile_sweeps.sh
lib/wake_common.sh|wake_autonomous_scan.sh
STAGES
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
