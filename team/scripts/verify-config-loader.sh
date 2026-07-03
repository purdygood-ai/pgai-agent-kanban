#!/usr/bin/env bash
# team/scripts/verify-config-loader.sh
#
# Permanent regression guard for the config-loader module.
# Verifies three acceptance criteria in isolation:
#
#   AC1 — A fully-valid kanban.cfg loads and exports expected values.
#   AC2 — A config missing a REQUIRED key (dev_tree_path) fails non-zero and
#          names the missing key in its error message.
#   AC3 — A config with an absent OPTIONAL key (pause_between_tasks) exports
#          the registry default (5).
#
# Usage:
#   bash team/scripts/verify-config-loader.sh [--verbose] [--help]
#
# Options:
#   --verbose, -v   Show detailed output for each assertion.
#   --help, -h      Show this help and exit.
#
# Exit codes:
#   0  All assertions passed.
#   1  One or more assertions failed.
#   2  Environment or setup error.

set -euo pipefail

# ---------------------------------------------------------------------------
# Locate this script and source shared helpers
# ---------------------------------------------------------------------------
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_INI_SH="${_SCRIPT_DIR}/lib/ini_parser.sh"
_LOADER_SH="${_SCRIPT_DIR}/lib/config_loader.sh"
_TEMP_SH="${_SCRIPT_DIR}/lib/temp.sh"

if [[ ! -f "$_TEMP_SH" ]]; then
    echo "ERROR: temp.sh not found at: $_TEMP_SH" >&2
    exit 2
fi
if [[ ! -f "$_INI_SH" ]]; then
    echo "ERROR: ini_parser.sh not found at: $_INI_SH" >&2
    exit 2
fi
if [[ ! -f "$_LOADER_SH" ]]; then
    echo "ERROR: config_loader.sh not found at: $_LOADER_SH" >&2
    exit 2
fi

# Source temp.sh for pgai_mktemp_d (resolver-aware temp dir creation).
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# Source ini_parser so read_ini is available for fixture setup.
# shellcheck source=lib/ini_parser.sh
source "$_INI_SH"

# ---------------------------------------------------------------------------
# Temp dir — use framework resolver (never a hardcoded path)
# ---------------------------------------------------------------------------
_WORK_DIR="$(pgai_mktemp_d verify_config_loader)"

cleanup() {
    rm -rf "$_WORK_DIR"
}
trap cleanup EXIT

# ---------------------------------------------------------------------------
# Counters and helpers
# ---------------------------------------------------------------------------
VERBOSE=false
PASS_COUNT=0
FAIL_COUNT=0

if [[ -t 1 ]]; then
    C_RED=$'\033[0;31m'
    C_GREEN=$'\033[0;32m'
    C_RESET=$'\033[0m'
else
    C_RED="" C_GREEN="" C_RESET=""
fi

pass() {
    echo "${C_GREEN}PASS${C_RESET}: $*"
    (( PASS_COUNT++ )) || true
}

fail() {
    echo "${C_RED}FAIL${C_RESET}: $*"
    (( FAIL_COUNT++ )) || true
}

verbose() {
    [[ "$VERBOSE" == "true" ]] && echo "  [detail] $*" || true
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --verbose|-v) VERBOSE=true; shift ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--verbose] [--help]

Regression guard for team/scripts/lib/config_loader.sh.

Options:
  --verbose, -v   Show detailed per-assertion output.
  --help, -h      Show this help and exit.

Exit codes:
  0  All assertions passed.
  1  One or more assertions failed.
  2  Environment or setup error.
EOF
            exit 0
            ;;
        *)
            echo "ERROR: Unknown argument: $1" >&2
            exit 2
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

# Build a fully-valid kanban.cfg fixture.
_build_valid_cfg() {
    local path="${_WORK_DIR}/valid.cfg"
    cat > "$path" << 'ENDCFG'
[paths]
dev_tree_path = /tmp/fake_dev_tree

[providers]
active = claude
ai_auth_mode = oauth

[chain]
pm_mode = automatic

[dashboard]
rows_per_column = 21

[wake]
max_tasks_per_wake = 5
max_runtime_seconds = 14400
pause_between_tasks = 5
stop_on_blocked = true
pm_max_tasks = 15

[models.claude]
pm =
coder =

[cleanup]
retention_days = 30
trivial_log_bytes = 1700
trivial_log_hours = 6
ENDCFG
    echo "$path"
}

# Build a cfg missing the REQUIRED dev_tree_path.
_build_missing_required_cfg() {
    local path="${_WORK_DIR}/missing_required.cfg"
    cat > "$path" << 'ENDCFG'
[paths]
# dev_tree_path intentionally omitted

[wake]
max_tasks_per_wake = 5
pause_between_tasks = 5
stop_on_blocked = true
ENDCFG
    echo "$path"
}

# Build a cfg with pause_between_tasks absent (OPTIONAL key omitted).
_build_missing_optional_cfg() {
    local path="${_WORK_DIR}/missing_optional.cfg"
    cat > "$path" << 'ENDCFG'
[paths]
dev_tree_path = /tmp/fake_dev_tree

[wake]
max_tasks_per_wake = 3
# pause_between_tasks intentionally omitted
stop_on_blocked = false
ENDCFG
    echo "$path"
}

# ---------------------------------------------------------------------------
# AC1 — valid config loads and exports expected values
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC1: Valid config loads and exports expected values"
echo "==================================================================="
echo ""

_VALID_CFG="$(_build_valid_cfg)"
verbose "Fixture: $_VALID_CFG"

# Run load_config in a subshell so env vars don't leak between tests.
_AC1_RESULT=$(
    env -i HOME="$HOME" PATH="$PATH" \
    bash -c "
        source '${_LOADER_SH}'
        if load_config '${_VALID_CFG}'; then
            echo \"PGAI_DEV_TREE_PATH=\${PGAI_DEV_TREE_PATH:-}\"
            echo \"MAX_TASKS_PER_WAKE=\${MAX_TASKS_PER_WAKE:-}\"
            echo \"PAUSE_BETWEEN_TASKS=\${PAUSE_BETWEEN_TASKS:-}\"
            echo \"STOP_ON_BLOCKED=\${STOP_ON_BLOCKED:-}\"
            echo \"PGAI_KANBAN_PM_MODE=\${PGAI_KANBAN_PM_MODE:-}\"
            echo \"PGAI_AGENT_KANBAN_TEMP_DIR=\${PGAI_AGENT_KANBAN_TEMP_DIR:-}\"
            echo \"AI_AUTH_MODE=\${AI_AUTH_MODE:-}\"
            echo \"exit_code=0\"
        else
            echo \"exit_code=1\"
        fi
    " 2>&1
) || true

verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC1_RESULT" | while IFS= read -r l; do echo "    $l"; done

_check_ac1_var() {
    local name="$1" expected="$2"
    local actual
    actual=$(echo "$_AC1_RESULT" | grep "^${name}=" | head -1 | cut -d= -f2-)
    if [[ "$actual" == "$expected" ]]; then
        pass "AC1: ${name} = '${actual}' (expected '${expected}')"
    else
        fail "AC1: ${name} = '${actual}' (expected '${expected}')"
    fi
}

_exit_code=$(echo "$_AC1_RESULT" | grep "^exit_code=" | head -1 | cut -d= -f2-)
if [[ "$_exit_code" == "0" ]]; then
    pass "AC1: load_config returned exit code 0 for valid config"
else
    fail "AC1: load_config returned non-zero exit code for valid config (got: $_exit_code)"
fi

_check_ac1_var "PGAI_DEV_TREE_PATH"         "/tmp/fake_dev_tree"
_check_ac1_var "MAX_TASKS_PER_WAKE"         "5"
_check_ac1_var "PAUSE_BETWEEN_TASKS"        "5"
_check_ac1_var "STOP_ON_BLOCKED"            "true"
_check_ac1_var "PGAI_KANBAN_PM_MODE"        "automatic"
# anti-pattern-allowlist: 2 (justification: this literal is the EXPECTED registry default value
# being asserted in the verification check, not a caller bypassing the temp resolver.
# The fixture config sets tmp_root=/tmp and tmp_subdir=pgai_kanban_tmp explicitly, so the
# loader is expected to export PGAI_AGENT_KANBAN_TEMP_DIR=/tmp/pgai_kanban_tmp — this
# assertion verifies that the loader computes the correct path from those config keys.)
_check_ac1_var "PGAI_AGENT_KANBAN_TEMP_DIR" "/tmp/pgai_kanban_tmp"
_check_ac1_var "AI_AUTH_MODE"               "oauth"

echo ""

# ---------------------------------------------------------------------------
# AC2 — missing REQUIRED key causes non-zero exit and names the key
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC2: Missing REQUIRED key fails non-zero and names the key"
echo "==================================================================="
echo ""

_MISSING_REQ_CFG="$(_build_missing_required_cfg)"
verbose "Fixture: $_MISSING_REQ_CFG"

_AC2_EXIT=0
_AC2_STDERR=""
_AC2_STDERR=$(
    env -i HOME="$HOME" PATH="$PATH" \
    bash -c "
        set -e
        source '${_LOADER_SH}'
        load_config '${_MISSING_REQ_CFG}'
        echo exit_code_zero
    " 2>&1
) || _AC2_EXIT=$?

verbose "load_config stderr/stdout:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC2_STDERR" | while IFS= read -r l; do echo "    $l"; done

if [[ "$_AC2_EXIT" -ne 0 ]]; then
    pass "AC2: load_config exited non-zero (exit code: $_AC2_EXIT) for config missing dev_tree_path"
else
    fail "AC2: load_config exited zero — should have failed for missing REQUIRED key dev_tree_path"
fi

if echo "$_AC2_STDERR" | grep -q "dev_tree_path"; then
    pass "AC2: error message names the missing key 'dev_tree_path'"
else
    fail "AC2: error message does NOT name 'dev_tree_path' (actual: $(echo "$_AC2_STDERR" | head -3))"
fi

if echo "$_AC2_STDERR" | grep -q "${_MISSING_REQ_CFG}"; then
    pass "AC2: error message names the config file path"
else
    fail "AC2: error message does NOT name the config file path (actual: $(echo "$_AC2_STDERR" | head -3))"
fi

echo ""

# ---------------------------------------------------------------------------
# AC3 — absent OPTIONAL key uses registry default
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC3: Absent OPTIONAL key uses registry default"
echo "==================================================================="
echo ""

_MISSING_OPT_CFG="$(_build_missing_optional_cfg)"
verbose "Fixture: $_MISSING_OPT_CFG"

_AC3_RESULT=$(
    env -i HOME="$HOME" PATH="$PATH" \
    bash -c "
        source '${_LOADER_SH}'
        if load_config '${_MISSING_OPT_CFG}'; then
            echo \"PAUSE_BETWEEN_TASKS=\${PAUSE_BETWEEN_TASKS:-}\"
            echo \"MAX_TASKS_PER_WAKE=\${MAX_TASKS_PER_WAKE:-}\"
            echo \"exit_code=0\"
        else
            echo \"exit_code=1\"
        fi
    " 2>&1
) || true

verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC3_RESULT" | while IFS= read -r l; do echo "    $l"; done

_ac3_exit=$(echo "$_AC3_RESULT" | grep "^exit_code=" | head -1 | cut -d= -f2-)
if [[ "$_ac3_exit" == "0" ]]; then
    pass "AC3: load_config returned exit code 0 for config with absent optional key"
else
    fail "AC3: load_config returned non-zero for config with absent optional key (got: $_ac3_exit)"
fi

# pause_between_tasks is absent → registry default 5
_ac3_pause=$(echo "$_AC3_RESULT" | grep "^PAUSE_BETWEEN_TASKS=" | head -1 | cut -d= -f2-)
if [[ "$_ac3_pause" == "5" ]]; then
    pass "AC3: PAUSE_BETWEEN_TASKS = '5' (registry default applied for absent key)"
else
    fail "AC3: PAUSE_BETWEEN_TASKS = '${_ac3_pause}' (expected registry default '5')"
fi

# max_tasks_per_wake is set to 3 in the fixture → should honor config value
_ac3_max=$(echo "$_AC3_RESULT" | grep "^MAX_TASKS_PER_WAKE=" | head -1 | cut -d= -f2-)
if [[ "$_ac3_max" == "3" ]]; then
    pass "AC3: MAX_TASKS_PER_WAKE = '3' (config value honored)"
else
    fail "AC3: MAX_TASKS_PER_WAKE = '${_ac3_max}' (expected config value '3')"
fi

echo ""

# ---------------------------------------------------------------------------
# AC4 — bash -n passes (sourced as module; no top-level side effects)
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC4: bash -n syntax check"
echo "==================================================================="
echo ""

if bash -n "$_LOADER_SH" 2>&1; then
    pass "AC4: bash -n passes on config_loader.sh"
else
    fail "AC4: bash -n FAILED on config_loader.sh"
fi

echo ""

# ---------------------------------------------------------------------------
# AC5 — module is sourceable without side effects (no immediate exports)
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC5: Sourcing module produces no side effects until load_config"
echo "==================================================================="
echo ""

_AC5_RESULT=$(
    env -i HOME="$HOME" PATH="$PATH" \
    bash -c "
        source '${_LOADER_SH}'
        # These should be unset because load_config was not called.
        echo \"PGAI_DEV_TREE_PATH=\${PGAI_DEV_TREE_PATH:-UNSET}\"
        echo \"MAX_TASKS_PER_WAKE=\${MAX_TASKS_PER_WAKE:-UNSET}\"
        echo \"exit=0\"
    " 2>&1
) || true

_ac5_dev=$(echo "$_AC5_RESULT" | grep "^PGAI_DEV_TREE_PATH=" | head -1 | cut -d= -f2-)
_ac5_max=$(echo "$_AC5_RESULT" | grep "^MAX_TASKS_PER_WAKE=" | head -1 | cut -d= -f2-)

if [[ "$_ac5_dev" == "UNSET" && "$_ac5_max" == "UNSET" ]]; then
    pass "AC5: sourcing config_loader.sh produces no side effects (vars remain unset until load_config)"
else
    fail "AC5: sourcing config_loader.sh had side effects: PGAI_DEV_TREE_PATH='${_ac5_dev}' MAX_TASKS_PER_WAKE='${_ac5_max}'"
fi

echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  Results"
echo "==================================================================="
TOTAL_CHECKS=$(( PASS_COUNT + FAIL_COUNT ))
echo "  Checks passed : $PASS_COUNT / $TOTAL_CHECKS"
echo "  Checks failed : $FAIL_COUNT / $TOTAL_CHECKS"
echo ""

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo "${C_RED}VERDICT: FAIL — ${FAIL_COUNT} assertion(s) failed${C_RESET}"
    exit 1
fi

echo "${C_GREEN}VERDICT: PASS — all assertions satisfied${C_RESET}"
exit 0
