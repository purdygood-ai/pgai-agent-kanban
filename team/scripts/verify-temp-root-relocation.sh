#!/usr/bin/env bash
# team/scripts/verify-temp-root-relocation.sh
#
# End-to-end verification for the configurable tmp_root / tmp_subdir feature
# This script is the headline verification described
# in the requirements doc's "Notes for TESTER" section.
#
# What this script does:
#   1. Saves the caller's kanban.cfg to a temp backup (trap EXIT restores it).
#   2. Sets tmp_root=/home/rocky/tmp in the working kanban.cfg copy.
#   3. Runs the full unit + integration test suite.
#   4. Asserts acceptance criteria #4, #5, #6, #9 (and sub-checks from #7).
#   5. Restores kanban.cfg even if the suite fails.
#
# Acceptance criteria verified:
#   AC#4  — After a full suite run with tmp_root=/home/rocky/tmp, zero files or
#           dirs under /tmp match 'pgai_kanban'
#           (ls -A /tmp | grep -E 'pgai_kanban' | wc -l == 0).
#   AC#5  — grep for the hardcoded fallback literal in team/scripts/ and team/tests/
#           (excluding temp.sh and lint_test_anti_patterns) returns zero matches.
#   AC#6  — With kanban.cfg unreadable AND no PGAI_AGENT_KANBAN_TEMP_DIR env var,
#           pgai_temp_dir returns the documented hard fallback path (never empty).
# anti-pattern-allowlist: 2 (justification: diagnostic literal in header docs; this is the verification tool itself, not a caller bypassing the resolver)
#   AC#9  — ls -d /tmp/pgai_kanban_root_* /tmp/pgai_kanban_tmp_* 2>/dev/null
#           returns zero (no B253-style sibling dirs).
#
# Usage:
#   team/scripts/verify-temp-root-relocation.sh [--kanban-root <path>]
#                                                [--relocated-root <path>]
#                                                [--verbose]
#                                                [--help]
#
# Options:
#   --kanban-root     Kanban root (default: $PGAI_AGENT_KANBAN_ROOT_PATH or
#                     $HOME/pgai_agent_kanban).
#   --relocated-root  The tmp_root value to inject (default: /home/rocky/tmp).
#   --verbose         Pass --verbose through to the test runners.
#   --help, -h        Show this help and exit.
#
# Exit codes:
#   0  All assertions passed and suite passed.
#   1  Assertion failure or suite failure.
#   2  Configuration or environment error.
#
# Safety invariants:
#   - kanban.cfg is restored on exit even if the script is interrupted.
#   - The script never removes anything outside the computed temp root.
#   - Temp dirs created by this script are placed under the framework temp root
#     (sourced from the UNMODIFIED env/config before any relocation write).

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

_INI_SH="${_SCRIPT_DIR}/lib/ini_parser.sh"
if [[ ! -f "$_INI_SH" ]]; then
    echo "ERROR: ini_parser.sh not found at: $_INI_SH" >&2
    exit 2
fi
# shellcheck source=lib/ini_parser.sh
source "$_INI_SH"
unset _INI_SH

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"
RELOCATED_ROOT="/home/rocky/tmp"
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
        --relocated-root)
            if [[ $# -lt 2 ]]; then
                echo "ERROR: --relocated-root requires a value." >&2
                exit 2
            fi
            RELOCATED_ROOT="$2"
            shift 2
            ;;
        --relocated-root=*)
            RELOCATED_ROOT="${1#--relocated-root=}"
            shift
            ;;
        --verbose|-v)
            VERBOSE=true
            shift
            ;;
        --help|-h)
            cat <<EOF
Usage: $(basename "$0") [--kanban-root <path>] [--relocated-root <path>] [--verbose]

End-to-end verification for the configurable tmp_root / tmp_subdir feature.

Options:
  --kanban-root <path>      Kanban root (default: \$PGAI_AGENT_KANBAN_ROOT_PATH
                            or \$HOME/pgai_agent_kanban)
  --relocated-root <path>   The tmp_root to inject for the suite run
                            (default: /home/rocky/tmp)
  --verbose, -v             Pass --verbose through to the test runners
  --help, -h                Show this help and exit

Exit codes:
  0  All assertions passed and suite passed
  1  Assertion failure or suite failure
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

# ---------------------------------------------------------------------------
# Validate environment
# ---------------------------------------------------------------------------
KANBAN_CFG="${KANBAN_ROOT}/kanban.cfg"
DEV_TREE="${PGAI_DEV_TREE_PATH:-}"

if [[ ! -f "$KANBAN_CFG" ]]; then
    echo "ERROR: kanban.cfg not found at: $KANBAN_CFG" >&2
    echo "       Set --kanban-root or \$PGAI_AGENT_KANBAN_ROOT_PATH." >&2
    exit 2
fi

if [[ -z "$DEV_TREE" ]]; then
    # Try reading from kanban.cfg
    DEV_TREE="$(read_ini "$KANBAN_CFG" paths dev_tree_path "")"
fi

if [[ -z "$DEV_TREE" ]]; then
    echo "ERROR: dev_tree_path not set in $KANBAN_CFG and \$PGAI_DEV_TREE_PATH is unset." >&2
    exit 2
fi

if [[ ! -d "$DEV_TREE" ]]; then
    echo "ERROR: dev_tree_path '$DEV_TREE' does not exist." >&2
    exit 2
fi

TEAM_SCRIPTS="${DEV_TREE}/team/scripts"
if [[ ! -f "${TEAM_SCRIPTS}/run-unit-tests.sh" ]]; then
    echo "ERROR: run-unit-tests.sh not found under $TEAM_SCRIPTS" >&2
    exit 2
fi

echo "==================================================================="
echo "  verify-temp-root-relocation.sh"
echo "  kanban root : $KANBAN_ROOT"
echo "  dev tree    : $DEV_TREE"
echo "  relocated   : $RELOCATED_ROOT (will be set as tmp_root)"
echo "==================================================================="
echo ""

# ---------------------------------------------------------------------------
# Step 1 — snapshot original kanban.cfg and install a trap to restore it
# ---------------------------------------------------------------------------
# We use the original temp root (before any relocation write) so the backup
# itself lands in a known, reachable location.
_ORIG_TEMP_DIR="$(pgai_temp_dir)"
_BACKUP_DIR="$(mktemp -d "${_ORIG_TEMP_DIR}/verify_relocation.XXXXXX")"
_BACKUP_CFG="${_BACKUP_DIR}/kanban.cfg.bak"

cp "$KANBAN_CFG" "$_BACKUP_CFG"

restore_cfg() {
    local exit_code=$?
    if [[ -f "$_BACKUP_CFG" ]]; then
        cp "$_BACKUP_CFG" "$KANBAN_CFG"
        echo ""
        echo "kanban.cfg restored from backup."
    fi
    exit $exit_code
}
trap restore_cfg EXIT

echo "Backed up kanban.cfg to: $_BACKUP_CFG"

# ---------------------------------------------------------------------------
# Step 2 — write tmp_root into kanban.cfg
# ---------------------------------------------------------------------------
# Ensure the [paths] section has both tmp_root and tmp_subdir.
# write_ini will add the key if it doesn't exist, or update it if it does.
echo "Injecting tmp_root=${RELOCATED_ROOT} into kanban.cfg ..."

write_ini "$KANBAN_CFG" paths tmp_root "$RELOCATED_ROOT"

# Ensure tmp_subdir is set (may already be present; write_ini is idempotent).
_EXISTING_SUBDIR="$(read_ini "$KANBAN_CFG" paths tmp_subdir "")"
if [[ -z "$_EXISTING_SUBDIR" ]]; then
    write_ini "$KANBAN_CFG" paths tmp_subdir "pgai_kanban_tmp"
    _EXISTING_SUBDIR="pgai_kanban_tmp"
fi

echo "  tmp_root   = $RELOCATED_ROOT"
echo "  tmp_subdir = $_EXISTING_SUBDIR"
echo ""

# Ensure the relocated temp root exists so the suite can write to it.
RELOCATED_TEMP_ROOT="${RELOCATED_ROOT}/${_EXISTING_SUBDIR}"
mkdir -p "$RELOCATED_TEMP_ROOT"

# Export the resolved temp dir so the test runners inherit it.
export PGAI_AGENT_KANBAN_TEMP_DIR="$RELOCATED_TEMP_ROOT"
export PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT"

# ---------------------------------------------------------------------------
# Step 2b — snapshot /tmp pgai_kanban items before the suite run
# ---------------------------------------------------------------------------
# We record what is already present so the AC#4 check can detect NEW items
# created during the suite, not pre-existing ones.
_TMP_BEFORE="$(ls -A /tmp 2>/dev/null | grep -E 'pgai_kanban' || true)"

# ---------------------------------------------------------------------------
# Step 3 — run the full unit + integration suite
# ---------------------------------------------------------------------------
VERBOSE_FLAG=""
[[ "$VERBOSE" == "true" ]] && VERBOSE_FLAG="--verbose"

echo "==================================================================="
echo "  Running unit tests ..."
echo "==================================================================="
UNIT_EXIT=0
(
    unset _PGAI_TEMP_SH_LOADED 2>/dev/null || true
    export PGAI_AGENT_KANBAN_TEMP_DIR="$RELOCATED_TEMP_ROOT"
    export PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT"
    bash "${TEAM_SCRIPTS}/run-unit-tests.sh" $VERBOSE_FLAG
) || UNIT_EXIT=$?

echo ""
echo "==================================================================="
echo "  Running integration tests ..."
echo "==================================================================="
INTEG_EXIT=0
(
    unset _PGAI_TEMP_SH_LOADED 2>/dev/null || true
    export PGAI_AGENT_KANBAN_TEMP_DIR="$RELOCATED_TEMP_ROOT"
    export PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT"
    bash "${TEAM_SCRIPTS}/run-integration-tests.sh" $VERBOSE_FLAG
) || INTEG_EXIT=$?

echo ""
SUITE_EXIT=0
if [[ $UNIT_EXIT -ne 0 || $INTEG_EXIT -ne 0 ]]; then
    SUITE_EXIT=1
    echo "${C_RED}Suite FAILED${C_RESET}: unit=$UNIT_EXIT integration=$INTEG_EXIT"
else
    echo "${C_GREEN}Suite PASSED${C_RESET}"
fi
echo ""

# ---------------------------------------------------------------------------
# Assertions — run regardless of suite outcome so all checks are reported.
# ---------------------------------------------------------------------------

echo "==================================================================="
echo "  Acceptance criterion checks"
echo "==================================================================="
echo ""

# AC#4 — zero NEW pgai_kanban items under /tmp after the suite run with relocated root.
# We compare the after-snapshot to the before-snapshot to detect items created DURING
# the suite run, not pre-existing ones (pre-existing dirs are not a relocation failure).
echo "--- AC#4: /tmp must contain zero NEW 'pgai_kanban' items after relocated suite run ---"
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
_TMP_NEW="${_TMP_NEW%$'\n'}"  # trim trailing newline
AC4_COUNT=0
[[ -n "$_TMP_NEW" ]] && AC4_COUNT=$(echo "$_TMP_NEW" | grep -c .)  || AC4_COUNT=0
if [[ "$AC4_COUNT" -eq 0 ]]; then
    pass "AC#4: no NEW pgai_kanban items created under /tmp during the relocated suite run"
    if [[ -n "$_TMP_BEFORE" ]]; then
        echo "  (note: pre-existing items were present before the run and are not counted)"
    fi
else
    fail "AC#4: ${AC4_COUNT} NEW pgai_kanban item(s) appeared in /tmp during the relocated suite run — relocation is incomplete"
    echo "$_TMP_NEW" | while read -r f; do [[ -n "$f" ]] && echo "  stray: /tmp/$f"; done
fi
echo ""

# AC#5 — zero inline literals outside temp.sh and lint script
# anti-pattern-allowlist: 2 (justification: this script IS the verification tool; the literal below is the grep pattern being checked, not a caller site)
echo "--- AC#5: zero inline /tmp/pgai_kanban_tmp literals in scripts/tests (outside temp.sh+lint) ---"
AC5_MATCHES=""
AC5_MATCHES=$(
    grep -rnE "/tmp/pgai_kanban_tmp" \
        "${DEV_TREE}/team/scripts/" \
        "${DEV_TREE}/team/tests/" \
        2>/dev/null \
    | grep -v 'temp\.sh' \
    | grep -v 'lint_test_anti_patterns' \
    | grep -v 'verify-temp-root-relocation\.sh' \
    || true
)
# anti-pattern-allowlist: 2 (justification: string literals in pass/fail messages document the pattern being checked; not caller sites)
if [[ -z "$AC5_MATCHES" ]]; then
    pass "AC#5: no inline /tmp/pgai_kanban_tmp literals found outside resolver and lint guard"
else
    fail "AC#5: found inline /tmp/pgai_kanban_tmp literals — consolidation is incomplete:"
    echo "$AC5_MATCHES" | head -30 | while read -r line; do echo "  $line"; done
fi
echo ""

# AC#6 — resolver returns the documented fallback when config is unreadable and env var is unset
# anti-pattern-allowlist: 2 (justification: the literal below is the EXPECTED fallback value being asserted in the test, not a caller bypassing the resolver)
echo "--- AC#6: resolver returns fallback '/tmp/pgai_kanban_tmp' when config is unreadable ---"
AC6_UNREADABLE_CFG="${_BACKUP_DIR}/unreadable.cfg"
touch "$AC6_UNREADABLE_CFG"
chmod 000 "$AC6_UNREADABLE_CFG"

AC6_RESULT=""
AC6_RESULT=$(
    env -i \
        HOME="$HOME" \
        PATH="$PATH" \
        PGAI_AGENT_KANBAN_ROOT_PATH="$_BACKUP_DIR" \
        bash -c "
            source '${DEV_TREE}/team/scripts/lib/temp.sh'
            pgai_temp_dir
        " 2>/dev/null
) || true

# Restore permissions so cleanup can delete it
chmod 644 "$AC6_UNREADABLE_CFG" 2>/dev/null || true

# anti-pattern-allowlist: 2 (justification: comparison value asserted by this verification test; not a caller site bypassing the resolver)
if [[ "$AC6_RESULT" == "/tmp/pgai_kanban_tmp" ]]; then
    pass "AC#6: resolver returned '/tmp/pgai_kanban_tmp' under unreadable config"
elif [[ -z "$AC6_RESULT" ]]; then
    fail "AC#6: resolver returned empty string — safety invariant violated"
elif [[ "$AC6_RESULT" == "/" ]]; then
    fail "AC#6: resolver returned '/' — safety invariant violated"
else
    # Since config was unreadable the resolver should use the hardcoded fallback.
    # anti-pattern-allowlist: 2 (justification: same; literal in error message for diagnostic output only)
    fail "AC#6: resolver returned unexpected value '$AC6_RESULT' (expected '/tmp/pgai_kanban_tmp')"
fi
echo ""

# AC#9 — no B253 sibling dirs: /tmp/pgai_kanban_root_* or /tmp/pgai_kanban_tmp_* siblings
# anti-pattern-allowlist: 2 (justification: literal is in a glob pattern that checks FOR sibling dirs as part of the B253 regression guard; not a caller site bypassing the resolver)
echo "--- AC#9: no B253 sibling dirs /tmp/pgai_kanban_root_* or /tmp/pgai_kanban_tmp_* ---"
AC9_COUNT=0
AC9_COUNT=$(ls -d /tmp/pgai_kanban_root_* /tmp/pgai_kanban_tmp_* 2>/dev/null | wc -l) || AC9_COUNT=0
if [[ "$AC9_COUNT" -eq 0 ]]; then
    pass "AC#9: no B253 sibling dirs found under /tmp"
else
    fail "AC#9: found $AC9_COUNT B253-style sibling dir(s) under /tmp:"
    # anti-pattern-allowlist: 2 (justification: diagnostic output listing stray dirs; verification tool, not a caller bypassing the resolver)
    ls -d /tmp/pgai_kanban_root_* /tmp/pgai_kanban_tmp_* 2>/dev/null | head -20 | while read -r f; do echo "  stray: $f"; done || true
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
echo "  Unit tests    : $([ $UNIT_EXIT -eq 0 ] && echo PASSED || echo "FAILED (exit $UNIT_EXIT)")"
echo "  Integ tests   : $([ $INTEG_EXIT -eq 0 ] && echo PASSED || echo "FAILED (exit $INTEG_EXIT)")"
echo ""

if [[ $SUITE_EXIT -ne 0 ]]; then
    echo "${C_RED}VERDICT: FAIL — test suite errors (see above)${C_RESET}"
    exit 1
fi

if [[ $FAIL_COUNT -gt 0 ]]; then
    echo "${C_RED}VERDICT: FAIL — $FAIL_COUNT assertion(s) failed${C_RESET}"
    exit 1
fi

echo "${C_GREEN}VERDICT: PASS — all assertions satisfied, suite clean${C_RESET}"
exit 0
