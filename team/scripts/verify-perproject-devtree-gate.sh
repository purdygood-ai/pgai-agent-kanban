#!/usr/bin/env bash
# team/scripts/verify-perproject-devtree-gate.sh
#
# Regression guard for the per-project dev-tree gate in the wake layer: the
# dev-tree presence check must be scoped per project, not a single global
# check shared across all projects.
#
# Where the behaviour lives:
#   lib/wake_project.sh  defines _check_project_dev_tree, the gate itself.
#   lib/wake_driver.sh   run_project_chain calls the gate once per project,
#                        after pp_load_config has set PP_dev_tree_path and
#                        PP_workflow_type.
#   lib/wake_common.sh   sources lib/wake_project.sh. wake/claude.sh and
#                        wake/codex.sh both source lib/wake_entry.sh, which
#                        sources wake_common.sh and wake_driver.sh, so both
#                        providers run the one gate.
#
# Verifies that:
#   AC-1  _check_project_dev_tree returns 1 for a release-workflow project
#         whose dev tree is missing or empty, and logs the canonical skip
#         line; a workflow type whose plugin cannot load also needs a tree.
#   AC-2  _check_project_dev_tree returns 0 for a release-workflow project
#         with a present dev tree, and logs no skip line.
#   AC-3  _check_project_dev_tree returns 0 for a document-workflow project
#         (its plugin's wf_git_mode answers none) even when PP_dev_tree_path
#         is empty.
#   AC-4  No global dev-tree gate remains at wake entry (grep check).
#   AC-5  The gate is defined once, in lib/wake_project.sh; run_project_chain
#         calls it after pp_load_config and before any task is dispatched;
#         both wake scripts reach it through the shared pipeline.
#   AC-6  bash -n passes on the wake files involved.
#
# AC-1..3 run the real gate from lib/wake_project.sh in a separate bash
# process, with the real lib/workflow.sh and this tree's own workflow
# plugins, so a change to either the gate or a plugin's git mode shows up.
#
# Usage:
#   team/scripts/verify-perproject-devtree-gate.sh [--verbose] [--help]
#
# Exit codes:
#   0  All assertions passed.
#   1  Assertion failure.
#   2  Configuration or environment error.

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
    # BLOCK-WORTHY: this gate cannot run without its own temp-path helper.
    # CENSUS-ANCHOR: verify-perproject-devtree-gate-temp-sh-missing-01
    exit 2
fi
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
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

Regression guard for the per-project dev-tree gate in the wake layer: the
dev-tree presence check must be scoped per project, not a single global
check shared across all projects.

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
echo "  verify-perproject-devtree-gate.sh"
echo "  script dir    : $_SCRIPT_DIR"
echo "  fixture root  : $_FIXTURE_ROOT"
echo "  framework tmp : $(pgai_temp_dir)"
echo "==================================================================="
echo ""

WAKE_COMMON_SH="${_SCRIPT_DIR}/lib/wake_common.sh"
WAKE_PROJECT_SH="${_SCRIPT_DIR}/lib/wake_project.sh"
WAKE_DRIVER_SH="${_SCRIPT_DIR}/lib/wake_driver.sh"
WORKFLOW_SH="${_SCRIPT_DIR}/lib/workflow.sh"
CLAUDE_SH="${_SCRIPT_DIR}/wake/claude.sh"
CODEX_SH="${_SCRIPT_DIR}/wake/codex.sh"
# The workflow plugins that ship beside these scripts: team/workflows in the
# repository, <kanban root>/workflows in an install.
WORKFLOWS_DIR="${_SCRIPT_DIR}/../workflows"

# ---------------------------------------------------------------------------
# AC-6: bash -n on the wake files that carry or reach the gate
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-6: bash -n syntax check on the wake files involved"
echo "==================================================================="
echo ""

for _script in "$WAKE_PROJECT_SH" "$WAKE_DRIVER_SH" "$WAKE_COMMON_SH" "$CLAUDE_SH" "$CODEX_SH"; do
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

# A global gate is a bare require_dev_tree call in a wake entry file -- the
# two provider scripts or any lib/wake_*.sh stage they source. Comments do
# not match (the pattern anchors on the command word).
_GLOBAL_GATE_HITS=$(grep -nE '^[[:space:]]*require_dev_tree([[:space:]]|$)' \
    "$CLAUDE_SH" "$CODEX_SH" "${_SCRIPT_DIR}"/lib/wake_*.sh 2>/dev/null || true)
if [[ -z "$_GLOBAL_GATE_HITS" ]]; then
    pass "AC-4 (global-gate): no bare require_dev_tree call in wake/claude.sh, wake/codex.sh or lib/wake_*.sh"
else
    fail "AC-4 (global-gate): global require_dev_tree call present at wake entry:"
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
# AC-5: One gate, called from run_project_chain, reached by both wake scripts
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC-5: The gate is defined once and both wake scripts reach it"
echo "==================================================================="
echo ""

_GATE_DEF_RE='^[[:space:]]*_check_project_dev_tree[[:space:]]*\(\)'

# Single definition: lib/wake_project.sh and nowhere else under lib/ or wake/.
_GATE_DEF_FILES=$(grep -lE "$_GATE_DEF_RE" "${_SCRIPT_DIR}"/lib/*.sh "$CLAUDE_SH" "$CODEX_SH" 2>/dev/null || true)
_GATE_DEF_COUNT=$(grep -c . <<< "$_GATE_DEF_FILES" || true)
if [[ "$_GATE_DEF_COUNT" -eq 1 && "$_GATE_DEF_FILES" == "$WAKE_PROJECT_SH" ]]; then
    pass "AC-5 (definition): _check_project_dev_tree is defined once, in lib/wake_project.sh"
else
    fail "AC-5 (definition): expected exactly one definition of _check_project_dev_tree, in lib/wake_project.sh; found ${_GATE_DEF_COUNT}:"
    echo "${_GATE_DEF_FILES:-  (none)}"
fi

# lib/wake_common.sh brings the gate into the wake by sourcing wake_project.sh.
if grep -qE '^[[:space:]]*source[[:space:]].*lib/wake_project\.sh' "$WAKE_COMMON_SH"; then
    pass "AC-5 (sourcing): lib/wake_common.sh sources lib/wake_project.sh"
else
    fail "AC-5 (sourcing): lib/wake_common.sh does NOT source lib/wake_project.sh -- the gate is never defined in a wake"
fi

# run_project_chain (lib/wake_driver.sh) calls the gate after pp_load_config
# and before the first process_one_task dispatch. Line numbers are relative
# to the extracted function body; comment lines are excluded.
_CHAIN_BODY=$(sed -n '/^run_project_chain()[[:space:]]*{/,/^}/p' "$WAKE_DRIVER_SH")
_first_code_line() {
    # _first_code_line <ERE> -- first non-comment line of _CHAIN_BODY matching it.
    # Prints nothing when there is no match; never fails, so a missing line
    # is reported by the assertion below rather than ending the script.
    { grep -nE "$1" <<< "$_CHAIN_BODY" | grep -vE '^[0-9]+:[[:space:]]*#' | head -n1 | cut -d: -f1; } || true
}
if [[ -z "$_CHAIN_BODY" ]]; then
    fail "AC-5 (call-site): could not find run_project_chain in lib/wake_driver.sh"
else
    _LOAD_LINE=$(_first_code_line 'pp_load_config[[:space:]]+"\$project_name"')
    _GATE_LINE=$(_first_code_line '_check_project_dev_tree[[:space:]]+"\$project_name"')
    _DISPATCH_LINE=$(_first_code_line '(^|[^_[:alnum:]])process_one_task([[:space:]]|$)')
    echo "--- run_project_chain lines: pp_load_config=${_LOAD_LINE:-none}  gate=${_GATE_LINE:-none}  process_one_task=${_DISPATCH_LINE:-none}"

    if [[ -n "$_GATE_LINE" ]]; then
        pass "AC-5 (call-site): run_project_chain in lib/wake_driver.sh calls _check_project_dev_tree"
    else
        fail "AC-5 (call-site): run_project_chain in lib/wake_driver.sh does NOT call _check_project_dev_tree -- no project is ever skipped for a missing dev tree"
    fi
    if [[ -n "$_LOAD_LINE" && -n "$_GATE_LINE" && "$_LOAD_LINE" -lt "$_GATE_LINE" ]]; then
        pass "AC-5 (order): pp_load_config runs before the gate, so the gate reads this project's PP_dev_tree_path"
    else
        fail "AC-5 (order): pp_load_config (line ${_LOAD_LINE:-none}) must precede _check_project_dev_tree (line ${_GATE_LINE:-none}) in run_project_chain"
    fi
    if [[ -n "$_GATE_LINE" && -n "$_DISPATCH_LINE" && "$_GATE_LINE" -lt "$_DISPATCH_LINE" ]]; then
        pass "AC-5 (order): the gate runs before the first process_one_task dispatch"
    else
        fail "AC-5 (order): _check_project_dev_tree (line ${_GATE_LINE:-none}) must precede process_one_task (line ${_DISPATCH_LINE:-none}) in run_project_chain"
    fi
fi

# Sibling discipline: both provider scripts source the same shared entry
# body, which sources the shared pipeline, so neither can drift onto a gate
# of its own.
_WAKE_ENTRY_SH="${_SCRIPT_DIR}/lib/wake_entry.sh"
for _wake in "$CLAUDE_SH" "$CODEX_SH"; do
    _wname="$(basename "$_wake")"
    if grep -qE "^[[:space:]]*source[[:space:]].*lib/wake_entry\\.sh" "$_wake"; then
        pass "AC-5 (sibling): ${_wname} sources lib/wake_entry.sh"
    else
        fail "AC-5 (sibling): ${_wname} does NOT source lib/wake_entry.sh -- it does not run the shared dev-tree gate"
    fi
done
for _lib in wake_common.sh wake_driver.sh; do
    if grep -qE "^[[:space:]]*source[[:space:]].*lib/${_lib//./\\.}" "$_WAKE_ENTRY_SH"; then
        pass "AC-5 (sibling): wake_entry.sh sources lib/${_lib}"
    else
        fail "AC-5 (sibling): wake_entry.sh does NOT source lib/${_lib} -- the wake scripts do not run the shared dev-tree gate"
    fi
done
echo ""

# ---------------------------------------------------------------------------
# AC-1, AC-2, AC-3: Exercise _check_project_dev_tree directly
# ---------------------------------------------------------------------------
# Each case runs in a separate bash process that sources the real
# lib/workflow.sh and lib/wake_project.sh with a log() stub. The process sees
# a fixture kanban root whose workflows/ is this tree's own plugin directory,
# so wf_load_plugin resolves the plugins that ship with the gate.
# ---------------------------------------------------------------------------

if ! grep -qE "$_GATE_DEF_RE" "$WAKE_PROJECT_SH" 2>/dev/null; then
    fail "AC-1/2/3: could not find _check_project_dev_tree in lib/wake_project.sh"
    echo "--- RESULTS: ${PASS_COUNT} passed, ${FAIL_COUNT} failed ---"
    # BLOCK-WORTHY: verification unable to run at all -- without the
    # function body under test, none of this gate's assertions mean anything.
    # CENSUS-ANCHOR: verify-perproject-devtree-gate-extract-failed-01
    exit 1
fi

_GATE_ROOT="${_FIXTURE_ROOT}/kanban_root"
mkdir -p "$_GATE_ROOT"
if [[ -d "$WORKFLOWS_DIR" ]]; then
    ln -s "$(cd "$WORKFLOWS_DIR" && pwd)" "${_GATE_ROOT}/workflows"
else
    warn "AC-1/2/3: no workflow plugins at ${WORKFLOWS_DIR}; every workflow type will load as unknown"
fi

# The per-case driver. It takes its inputs from the environment and prints
# "gate-rc=<N>" as its last line. It calls the gate as an if-condition, the
# way run_project_chain does: wf_load_plugin turns errexit back on inside
# the gate, so a bare call under set -e would end the process early on an
# unloadable workflow type instead of returning the gate's own answer.
_GATE_SCRIPT="${_FIXTURE_ROOT}/gate_case.sh"
cat > "$_GATE_SCRIPT" <<'GATESCRIPT'
#!/usr/bin/env bash
set -euo pipefail
log() { echo "[log] $*" >> "$GATE_LOG_FILE"; }
# shellcheck source=/dev/null
source "$GATE_WORKFLOW_SH"
# shellcheck source=/dev/null
source "$GATE_WAKE_PROJECT_SH"
_gate_rc=0
if _check_project_dev_tree "$GATE_PROJECT"; then
    _gate_rc=0
else
    _gate_rc=$?
fi
echo "gate-rc=${_gate_rc}"
GATESCRIPT

# _run_gate_check <project_name> <workflow_type> <dev_tree_path>
# Prints the gate's return code, or "none" when the driver died first.
# The case's log lines land in ${_FIXTURE_ROOT}/gate_log_<project_name>.txt.
_run_gate_check() {
    local _project="$1" _workflow="$2" _tree="$3"
    local _out=""
    : > "${_FIXTURE_ROOT}/gate_log_${_project}.txt"
    _out=$(env -u TEAM_ROOT -u PGAI_PROJECT_ROOT -u PGAI_TASKS_DIR \
        PGAI_AGENT_KANBAN_ROOT_PATH="$_GATE_ROOT" \
        GATE_LOG_FILE="${_FIXTURE_ROOT}/gate_log_${_project}.txt" \
        GATE_WORKFLOW_SH="$WORKFLOW_SH" \
        GATE_WAKE_PROJECT_SH="$WAKE_PROJECT_SH" \
        GATE_PROJECT="$_project" \
        PP_workflow_type="$_workflow" \
        PP_dev_tree_path="$_tree" \
        bash "$_GATE_SCRIPT" 2>/dev/null) || true
    local _rc
    _rc=$(sed -n 's/^gate-rc=\([0-9][0-9]*\)$/\1/p' <<< "$_out" | tail -n1)
    echo "${_rc:-none}"
}

# _gate_log <project_name> -- the log lines the gate wrote for that case.
_gate_log() {
    cat "${_FIXTURE_ROOT}/gate_log_${1}.txt" 2>/dev/null || true
}

echo "==================================================================="
echo "  AC-1: Release-workflow project with MISSING dev tree → skip (return 1)"
echo "==================================================================="
echo ""

MISSING_TREE="${_FIXTURE_ROOT}/nonexistent_tree_12345"

_rc_ac1=$(_run_gate_check "test_project_missing" "release" "$MISSING_TREE")
if [[ "$_rc_ac1" == "1" ]]; then
    pass "AC-1 (return-code): _check_project_dev_tree returns 1 for release project with missing tree"
else
    fail "AC-1 (return-code): expected return 1, got ${_rc_ac1} for missing dev tree"
fi

# The skip log line names the project and the path it could not find.
_AC1_LOG="$(_gate_log test_project_missing)"
if grep -q "project test_project_missing:" <<< "$_AC1_LOG" \
    && grep -qF "$MISSING_TREE" <<< "$_AC1_LOG" \
    && grep -q "missing — skipping this project" <<< "$_AC1_LOG"; then
    pass "AC-1 (log-line): skip log line contains project name, dev tree path, and 'missing — skipping this project'"
else
    fail "AC-1 (log-line): no canonical skip line for test_project_missing; gate logged: ${_AC1_LOG:-(nothing)}"
fi

_rc_ac1_empty=$(_run_gate_check "test_project_empty" "release" "")
if [[ "$_rc_ac1_empty" == "1" ]]; then
    pass "AC-1 (empty-path): _check_project_dev_tree returns 1 for release project with an empty PP_dev_tree_path"
else
    fail "AC-1 (empty-path): expected return 1, got ${_rc_ac1_empty} for an empty PP_dev_tree_path"
fi

_rc_ac1_unknown=$(_run_gate_check "test_project_unknown_wf" "no-such-workflow" "$MISSING_TREE")
if [[ "$_rc_ac1_unknown" == "1" ]]; then
    pass "AC-1 (unknown-workflow): an unloadable workflow type still requires a dev tree (returns 1)"
else
    fail "AC-1 (unknown-workflow): expected return 1, got ${_rc_ac1_unknown} -- an unloadable workflow type must not exempt a project"
fi
echo ""

echo "==================================================================="
echo "  AC-2: Release-workflow project with PRESENT dev tree → proceed (return 0)"
echo "==================================================================="
echo ""

PRESENT_TREE="${_FIXTURE_ROOT}/present_tree"
mkdir -p "$PRESENT_TREE"

_rc_ac2=$(_run_gate_check "test_project_present" "release" "$PRESENT_TREE")
if [[ "$_rc_ac2" == "0" ]]; then
    pass "AC-2 (return-code): _check_project_dev_tree returns 0 for release project with present tree"
else
    fail "AC-2 (return-code): expected return 0, got ${_rc_ac2} for present dev tree"
fi

if grep -q "skipping this project" <<< "$(_gate_log test_project_present)"; then
    fail "AC-2 (log-line): the gate logged a skip line for a project whose dev tree is present"
else
    pass "AC-2 (log-line): no skip log line for a project whose dev tree is present"
fi
echo ""

echo "==================================================================="
echo "  AC-3: Document-workflow project with EMPTY dev tree → proceed (return 0)"
echo "==================================================================="
echo ""

_rc_ac3=$(_run_gate_check "test_project_document" "document" "")
if [[ "$_rc_ac3" == "0" ]]; then
    pass "AC-3 (return-code): _check_project_dev_tree returns 0 for document-workflow project with empty dev tree"
else
    fail "AC-3 (return-code): expected return 0, got ${_rc_ac3} for document-workflow project"
fi
echo ""

echo "==================================================================="
echo "  RESULTS: ${PASS_COUNT} passed, ${FAIL_COUNT} failed"
echo "==================================================================="
echo ""

if [[ "$FAIL_COUNT" -gt 0 ]]; then
    echo "${C_RED}FAILED: ${FAIL_COUNT} assertion(s) failed.${C_RESET}" >&2
    # BLOCK-WORTHY: breakage-class -- a failed dev-tree-gate assertion
    # means the gate this verifies would misbehave on a real project.
    # CENSUS-ANCHOR: verify-perproject-devtree-gate-assertions-failed-01
    exit 1
else
    echo "${C_GREEN}ALL PASSED: ${PASS_COUNT} assertions.${C_RESET}"
    exit 0
fi
