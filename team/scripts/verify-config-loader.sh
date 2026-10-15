#!/usr/bin/env bash
# team/scripts/verify-config-loader.sh
#
# Permanent regression guard for the config-loader module.
# Verifies these acceptance criteria in isolation:
#
#   AC1 — A fully-valid kanban.cfg loads and exports expected values.
#   AC2 — A config with dev_tree_path absent loads with exit 0 and exports
#          PGAI_DEV_TREE_PATH empty: the key is OPTIONAL (an install may
#          leave it unset; consumers that need a dev tree call
#          require_dev_tree from lib/dev_tree.sh).
#   AC3 — A config with an absent OPTIONAL key (pause_between_tasks) exports
#          the registry default (5).
#   AC4 — bash -n passes on config_loader.sh and ini_parser.sh.
#   AC5 — Sourcing the module has no side effects until load_config runs.
#   AC6 — An inline comment after a value ("300 # five min") is stripped.
#   AC7 — A CRLF-saved config loads every key exactly as the LF file does.
#   AC8 — A [wake] integer key with a non-numeric value ("300s") refuses
#          dual-channel with a stable error key, a message naming the key
#          and value, and a remedy naming the config path.
#   AC9 — The same refusal fires for a non-numeric environment override.
#   AC10 — A misspelled key under a known section warns, names the nearest
#          known key, and does not refuse.
#   AC11 — An unknown section warns, names the nearest known section, and
#          does not refuse.
#   AC12 — The shipped kanban.cfg_example loads with no unknown-key or
#          unknown-section warning (registry / example drift guard).
#
# Every load_config call runs in an env -i subshell so exported variables
# never leak between cases. The subshell is given a UTF-8 locale on purpose:
# the loader's one-visible-character check on dashboard_status_glyph counts
# characters with wc -m, which counts bytes under the C locale and would
# refuse the default "■" glyph in every case.
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

# Environment handed to every isolated load_config subshell (see header).
_ISO_ENV=(HOME="$HOME" PATH="$PATH" LC_ALL=C.UTF-8)

# Repo root — kanban.cfg_example lives beside team/ (AC12).
_REPO_ROOT="$(cd "${_SCRIPT_DIR}/../.." && pwd)"
_CFG_EXAMPLE="${_REPO_ROOT}/kanban.cfg_example"

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

# Build a cfg with dev_tree_path absent (OPTIONAL: loads, exports empty).
_build_missing_dev_tree_cfg() {
    local path="${_WORK_DIR}/missing_dev_tree.cfg"
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
    env -i "${_ISO_ENV[@]}" \
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
    actual=$(echo "$_AC1_RESULT" | grep "^${name}=" | head -1 | cut -d= -f2- || true)
    if [[ "$actual" == "$expected" ]]; then
        pass "AC1: ${name} = '${actual}' (expected '${expected}')"
    else
        fail "AC1: ${name} = '${actual}' (expected '${expected}')"
    fi
}

_exit_code=$(echo "$_AC1_RESULT" | grep "^exit_code=" | head -1 | cut -d= -f2- || true)
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
# AC2 — dev_tree_path absent: loads (exit 0), exports PGAI_DEV_TREE_PATH empty
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC2: Absent dev_tree_path loads and exports an empty value"
echo "==================================================================="
echo ""

_MISSING_DEV_TREE_CFG="$(_build_missing_dev_tree_cfg)"
verbose "Fixture: $_MISSING_DEV_TREE_CFG"

_AC2_RESULT=$(
    env -i "${_ISO_ENV[@]}" \
    bash -c "
        source '${_LOADER_SH}'
        if load_config '${_MISSING_DEV_TREE_CFG}'; then
            echo \"PGAI_DEV_TREE_PATH=[\${PGAI_DEV_TREE_PATH:-}]\"
            echo \"exit_code=0\"
        else
            echo \"exit_code=1\"
        fi
    " 2>&1
) || true

verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC2_RESULT" | while IFS= read -r l; do echo "    $l"; done

_ac2_exit=$(echo "$_AC2_RESULT" | grep "^exit_code=" | head -1 | cut -d= -f2- || true)
if [[ "$_ac2_exit" == "0" ]]; then
    pass "AC2: load_config returned exit code 0 with dev_tree_path absent (key is OPTIONAL)"
else
    fail "AC2: load_config returned non-zero with dev_tree_path absent (got: $_ac2_exit; key is OPTIONAL)"
fi

_ac2_dev=$(echo "$_AC2_RESULT" | grep "^PGAI_DEV_TREE_PATH=" | head -1 | cut -d= -f2- || true)
if [[ "$_ac2_dev" == "[]" ]]; then
    pass "AC2: PGAI_DEV_TREE_PATH exported empty when dev_tree_path is absent"
else
    fail "AC2: PGAI_DEV_TREE_PATH = '${_ac2_dev}' (expected '[]' when dev_tree_path is absent)"
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
    env -i "${_ISO_ENV[@]}" \
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

_ac3_exit=$(echo "$_AC3_RESULT" | grep "^exit_code=" | head -1 | cut -d= -f2- || true)
if [[ "$_ac3_exit" == "0" ]]; then
    pass "AC3: load_config returned exit code 0 for config with absent optional key"
else
    fail "AC3: load_config returned non-zero for config with absent optional key (got: $_ac3_exit)"
fi

# pause_between_tasks is absent → registry default 5
_ac3_pause=$(echo "$_AC3_RESULT" | grep "^PAUSE_BETWEEN_TASKS=" | head -1 | cut -d= -f2- || true)
if [[ "$_ac3_pause" == "5" ]]; then
    pass "AC3: PAUSE_BETWEEN_TASKS = '5' (registry default applied for absent key)"
else
    fail "AC3: PAUSE_BETWEEN_TASKS = '${_ac3_pause}' (expected registry default '5')"
fi

# max_tasks_per_wake is set to 3 in the fixture → should honor config value
_ac3_max=$(echo "$_AC3_RESULT" | grep "^MAX_TASKS_PER_WAKE=" | head -1 | cut -d= -f2- || true)
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

if bash -n "$_INI_SH" 2>&1; then
    pass "AC4: bash -n passes on ini_parser.sh"
else
    fail "AC4: bash -n FAILED on ini_parser.sh"
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
    env -i "${_ISO_ENV[@]}" \
    bash -c "
        source '${_LOADER_SH}'
        # These should be unset because load_config was not called.
        echo \"PGAI_DEV_TREE_PATH=\${PGAI_DEV_TREE_PATH:-UNSET}\"
        echo \"MAX_TASKS_PER_WAKE=\${MAX_TASKS_PER_WAKE:-UNSET}\"
        echo \"exit=0\"
    " 2>&1
) || true

_ac5_dev=$(echo "$_AC5_RESULT" | grep "^PGAI_DEV_TREE_PATH=" | head -1 | cut -d= -f2- || true)
_ac5_max=$(echo "$_AC5_RESULT" | grep "^MAX_TASKS_PER_WAKE=" | head -1 | cut -d= -f2- || true)

if [[ "$_ac5_dev" == "UNSET" && "$_ac5_max" == "UNSET" ]]; then
    pass "AC5: sourcing config_loader.sh produces no side effects (vars remain unset until load_config)"
else
    fail "AC5: sourcing config_loader.sh had side effects: PGAI_DEV_TREE_PATH='${_ac5_dev}' MAX_TASKS_PER_WAKE='${_ac5_max}'"
fi

echo ""

# ---------------------------------------------------------------------------
# Shared runner for AC6..AC12: load <cfg> in an isolated subshell with any
# extra VAR=value pairs applied, and print the named variables plus
# exit_code=<n>. stderr (warnings, refusals) is interleaved into the result.
#   _run_load <cfg> "<VAR ...to print>" [ENV=value ...]
# ---------------------------------------------------------------------------
_run_load() {
    local cfg="$1" vars="$2"
    shift 2
    local print_lines="" v
    for v in $vars; do
        print_lines+="echo \"${v}=\${${v}:-}\"; "
    done
    env -i "${_ISO_ENV[@]}" "$@" \
    bash -c "
        source '${_LOADER_SH}'
        if load_config '${cfg}'; then
            ${print_lines}
            echo \"exit_code=0\"
        else
            echo \"exit_code=\$?\"
        fi
    " 2>&1 || true
}

# _field <result> <name> — value of the first "name=..." line in <result>.
_field() {
    echo "$1" | grep "^${2}=" | head -1 | cut -d= -f2- || true
}

# ---------------------------------------------------------------------------
# AC6 — inline comment after a value is stripped
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC6: Inline comment after a value is stripped"
echo "==================================================================="
echo ""

_AC6_CFG="${_WORK_DIR}/inline_comment.cfg"
cat > "$_AC6_CFG" << 'ENDCFG'
[wake]
max_runtime_seconds = 300 # five min
pause_between_tasks = 7 ; semicolon comment
ENDCFG

_AC6_RESULT="$(_run_load "$_AC6_CFG" "MAX_RUNTIME_SECONDS PAUSE_BETWEEN_TASKS")"
verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC6_RESULT" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC6_RESULT" exit_code)" == "0" ]]; then
    pass "AC6: load_config returned exit code 0 with inline comments present"
else
    fail "AC6: load_config refused a config whose only oddity is an inline comment ($(echo "$_AC6_RESULT" | grep -E '^(error|message):' | head -2))"
fi
if [[ "$(_field "$_AC6_RESULT" MAX_RUNTIME_SECONDS)" == "300" ]]; then
    pass "AC6: 'max_runtime_seconds = 300 # five min' loads as 300"
else
    fail "AC6: MAX_RUNTIME_SECONDS = '$(_field "$_AC6_RESULT" MAX_RUNTIME_SECONDS)' (expected '300' with the '# five min' comment stripped)"
fi
if [[ "$(_field "$_AC6_RESULT" PAUSE_BETWEEN_TASKS)" == "7" ]]; then
    pass "AC6: 'pause_between_tasks = 7 ; semicolon comment' loads as 7"
else
    fail "AC6: PAUSE_BETWEEN_TASKS = '$(_field "$_AC6_RESULT" PAUSE_BETWEEN_TASKS)' (expected '7' with the '; ...' comment stripped)"
fi

echo ""

# ---------------------------------------------------------------------------
# AC7 — a CRLF-saved config loads every key exactly as the LF file does
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC7: CRLF-saved config loads every key correctly"
echo "==================================================================="
echo ""

_AC7_CFG="${_WORK_DIR}/valid_crlf.cfg"
sed 's/$/\r/' "$_VALID_CFG" > "$_AC7_CFG"
if grep -q $'\r' "$_AC7_CFG"; then
    verbose "Fixture: $_AC7_CFG (carriage returns present)"
else
    fail "AC7: fixture setup did not produce CRLF line endings"
fi

_AC7_VARS="PGAI_DEV_TREE_PATH MAX_TASKS_PER_WAKE MAX_RUNTIME_SECONDS PAUSE_BETWEEN_TASKS STOP_ON_BLOCKED PM_MAX_TASKS PGAI_KANBAN_PM_MODE AI_AUTH_MODE PGAI_CLEANUP_RETENTION_DAYS"
_AC7_LF="$(_run_load "$_VALID_CFG" "$_AC7_VARS")"
_AC7_CRLF="$(_run_load "$_AC7_CFG" "$_AC7_VARS")"
verbose "CRLF load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC7_CRLF" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC7_CRLF" exit_code)" == "0" ]]; then
    pass "AC7: load_config returned exit code 0 for the CRLF config"
else
    fail "AC7: load_config returned non-zero for the CRLF config"
fi
for _v in $_AC7_VARS; do
    _lf="$(_field "$_AC7_LF" "$_v")"
    _crlf="$(_field "$_AC7_CRLF" "$_v")"
    if [[ "$_lf" == "$_crlf" && -n "$_crlf" ]]; then
        pass "AC7: ${_v} = '${_crlf}' from CRLF matches LF"
    else
        fail "AC7: ${_v} = '${_crlf}' from CRLF (LF gave '${_lf}')"
    fi
done
if [[ "$(_field "$_AC7_CRLF" STOP_ON_BLOCKED)" == "true" ]]; then
    pass "AC7: STOP_ON_BLOCKED carries no trailing carriage return"
else
    fail "AC7: STOP_ON_BLOCKED = '$(_field "$_AC7_CRLF" STOP_ON_BLOCKED | cat -A)' (expected exactly 'true')"
fi

echo ""

# ---------------------------------------------------------------------------
# AC8 — non-numeric [wake] integer key refuses dual-channel
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC8: Non-numeric [wake] integer key refuses dual-channel"
echo "==================================================================="
echo ""

_AC8_CFG="${_WORK_DIR}/unit_suffix.cfg"
cat > "$_AC8_CFG" << 'ENDCFG'
[wake]
max_runtime_seconds = 300s
ENDCFG

_AC8_RESULT="$(_run_load "$_AC8_CFG" "MAX_RUNTIME_SECONDS")"
verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC8_RESULT" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC8_RESULT" exit_code)" != "0" ]]; then
    pass "AC8: load_config exited non-zero for 'max_runtime_seconds = 300s'"
else
    fail "AC8: load_config exited zero for 'max_runtime_seconds = 300s' (MAX_RUNTIME_SECONDS='$(_field "$_AC8_RESULT" MAX_RUNTIME_SECONDS)')"
fi
if grep -q '^error: *config_loader\.wake\.not_an_integer$' <<< "$_AC8_RESULT"; then
    pass "AC8: refusal carries the stable error key config_loader.wake.not_an_integer"
else
    fail "AC8: refusal does not carry error key config_loader.wake.not_an_integer (got: $(echo "$_AC8_RESULT" | grep '^error:' | head -1))"
fi
if grep -q "max_runtime_seconds.*'300s'" <<< "$(grep '^message:' <<< "$_AC8_RESULT")"; then
    pass "AC8: message names the key (max_runtime_seconds) and the value ('300s')"
else
    fail "AC8: message does not name both key and value (got: $(echo "$_AC8_RESULT" | grep '^message:' | head -1))"
fi
if grep -q "max_runtime_seconds = 300" <<< "$(grep '^remedy:' <<< "$_AC8_RESULT")" \
    && grep -qF "$_AC8_CFG" <<< "$(grep '^remedy:' <<< "$_AC8_RESULT")"; then
    pass "AC8: remedy is a runnable edit naming the config path and the digits the value started with"
else
    fail "AC8: remedy is not a runnable edit of the config (got: $(echo "$_AC8_RESULT" | grep '^remedy:' | head -1))"
fi
if grep -q '<' <<< "$(grep '^remedy:' <<< "$_AC8_RESULT")"; then
    fail "AC8: remedy carries an angle-bracket placeholder"
else
    pass "AC8: remedy carries no placeholder"
fi

echo ""

# ---------------------------------------------------------------------------
# AC9 — non-numeric environment override refuses the same way
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC9: Non-numeric environment override refuses"
echo "==================================================================="
echo ""

_AC9_RESULT="$(_run_load "$_VALID_CFG" "MAX_TASKS_PER_WAKE" MAX_TASKS_PER_WAKE=abc)"
verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC9_RESULT" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC9_RESULT" exit_code)" != "0" ]]; then
    pass "AC9: load_config exited non-zero for MAX_TASKS_PER_WAKE=abc in the environment"
else
    fail "AC9: load_config exited zero with MAX_TASKS_PER_WAKE=abc in the environment"
fi
if grep -q '^error: *config_loader\.wake\.not_an_integer$' <<< "$_AC9_RESULT" \
    && grep -q "MAX_TASKS_PER_WAKE.*'abc'" <<< "$(grep '^message:' <<< "$_AC9_RESULT")"; then
    pass "AC9: refusal names the environment variable and the value"
else
    fail "AC9: refusal does not name MAX_TASKS_PER_WAKE and 'abc' (got: $(echo "$_AC9_RESULT" | grep -E '^(error|message):' | head -2))"
fi
if grep -q 'export MAX_TASKS_PER_WAKE=5' <<< "$(grep '^remedy:' <<< "$_AC9_RESULT")"; then
    pass "AC9: remedy re-exports the variable with the key's default"
else
    fail "AC9: remedy does not re-export MAX_TASKS_PER_WAKE (got: $(echo "$_AC9_RESULT" | grep '^remedy:' | head -1))"
fi

echo ""

# ---------------------------------------------------------------------------
# AC10 — misspelled key under a known section warns, never refuses
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC10: Misspelled key warns with the nearest known key"
echo "==================================================================="
echo ""

_AC10_CFG="${_WORK_DIR}/misspelled_key.cfg"
cat > "$_AC10_CFG" << 'ENDCFG'
[wake]
max_task_per_wake = 3
ENDCFG

_AC10_RESULT="$(_run_load "$_AC10_CFG" "MAX_TASKS_PER_WAKE")"
verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC10_RESULT" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC10_RESULT" exit_code)" == "0" ]]; then
    pass "AC10: load_config returned exit code 0 with a misspelled key present (warns, never refuses)"
else
    fail "AC10: load_config refused a config with a misspelled key"
fi
if grep -q "max_task_per_wake.*max_tasks_per_wake" <<< "$(grep 'WARNING' <<< "$_AC10_RESULT")"; then
    pass "AC10: warning names the misspelled key and the nearest known key max_tasks_per_wake"
else
    fail "AC10: no warning naming max_task_per_wake -> max_tasks_per_wake (got: $(echo "$_AC10_RESULT" | grep WARNING | head -1))"
fi
if [[ "$(_field "$_AC10_RESULT" MAX_TASKS_PER_WAKE)" == "5" ]]; then
    pass "AC10: MAX_TASKS_PER_WAKE = '5' (registry default; the misspelled key is ignored)"
else
    fail "AC10: MAX_TASKS_PER_WAKE = '$(_field "$_AC10_RESULT" MAX_TASKS_PER_WAKE)' (expected registry default '5')"
fi

echo ""

# ---------------------------------------------------------------------------
# AC11 — unknown section warns, never refuses
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC11: Unknown section warns with the nearest known section"
echo "==================================================================="
echo ""

_AC11_CFG="${_WORK_DIR}/unknown_section.cfg"
cat > "$_AC11_CFG" << 'ENDCFG'
[wak]
max_tasks_per_wake = 3
ENDCFG

_AC11_RESULT="$(_run_load "$_AC11_CFG" "MAX_TASKS_PER_WAKE")"
verbose "load_config output:"
[[ "$VERBOSE" == "true" ]] && echo "$_AC11_RESULT" | while IFS= read -r l; do echo "    $l"; done

if [[ "$(_field "$_AC11_RESULT" exit_code)" == "0" ]]; then
    pass "AC11: load_config returned exit code 0 with an unknown section present"
else
    fail "AC11: load_config refused a config with an unknown section"
fi
if grep -q '\[wak\].*\[wake\]' <<< "$(grep 'WARNING' <<< "$_AC11_RESULT")"; then
    pass "AC11: warning names section [wak] and the nearest known section [wake]"
else
    fail "AC11: no warning naming [wak] -> [wake] (got: $(echo "$_AC11_RESULT" | grep WARNING | head -1))"
fi
if [[ "$(echo "$_AC11_RESULT" | grep -c 'WARNING')" == "1" ]]; then
    pass "AC11: keys under the unknown section are not warned about a second time"
else
    fail "AC11: expected exactly one warning, got $(echo "$_AC11_RESULT" | grep -c 'WARNING')"
fi

echo ""

# ---------------------------------------------------------------------------
# AC12 — shipped kanban.cfg_example loads with no unknown-entry warning
# ---------------------------------------------------------------------------
echo "==================================================================="
echo "  AC12: kanban.cfg_example loads with no unknown-entry warning"
echo "==================================================================="
echo ""

if [[ -f "$_CFG_EXAMPLE" ]]; then
    _AC12_RESULT="$(_run_load "$_CFG_EXAMPLE" "MAX_TASKS_PER_WAKE")"
    verbose "load_config output:"
    [[ "$VERBOSE" == "true" ]] && echo "$_AC12_RESULT" | while IFS= read -r l; do echo "    $l"; done
    if [[ "$(_field "$_AC12_RESULT" exit_code)" == "0" ]]; then
        pass "AC12: kanban.cfg_example loads with exit code 0"
    else
        fail "AC12: kanban.cfg_example does not load (got: $(echo "$_AC12_RESULT" | grep -E '^(error|message):|ERROR' | head -2))"
    fi
    if grep -q 'WARNING' <<< "$_AC12_RESULT"; then
        fail "AC12: kanban.cfg_example carries an entry the Key Registry does not list: $(echo "$_AC12_RESULT" | grep WARNING | head -1)"
    else
        pass "AC12: every section and key in kanban.cfg_example is in the Key Registry"
    fi
else
    fail "AC12: kanban.cfg_example not found at ${_CFG_EXAMPLE}"
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
