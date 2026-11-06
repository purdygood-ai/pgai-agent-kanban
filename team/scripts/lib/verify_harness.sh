#!/usr/bin/env bash
# team/scripts/lib/verify_harness.sh
#
# The PASS/FAIL assertion harness the verify-*.sh scripts share, so none of
# them carries its own counters, colours and pass/fail printers.
#
# SOURCING
# --------
# This file does not self-locate; a script directly under team/scripts/
# sources it relative to its own directory, then initialises the harness:
#
#   # shellcheck source=lib/verify_harness.sh
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/verify_harness.sh"
#   verify_harness_init
#
# It sources script_common.sh from its own directory (BASH_SOURCE) for the
# colour table and warn.
# Sourcing only defines functions: it sets no variable, shell option or trap.
#
# FUNCTIONS
# ---------
# verify_harness_init
#   Sets PASS_COUNT=0 and FAIL_COUNT=0, and readies script_common.sh's
#   logger in its bare (untagged) form: the colour table (C_RED, C_GREEN,
#   C_YELLOW, C_RESET and the rest) holds ANSI codes when stdout is a
#   terminal and empty strings otherwise, so piped and captured output
#   carries no escape codes.
#
# pass <message...>
#   Prints "PASS: <message>" (PASS in green) and adds one to PASS_COUNT.
#
# fail <message...>
#   Prints "FAIL: <message>" (FAIL in red) and adds one to FAIL_COUNT.
#
# warn <message...>
#   Prints "WARN: <message>" (WARN in yellow); counts nothing. This is
#   script_common.sh's warn in its bare form.
#
# All three print on stdout and always return 0, so they are safe under
# set -e.
#
# Example:
#   verify_harness_init
#   [[ -f "$cfg" ]] && pass "kanban.cfg present" || fail "kanban.cfg missing: $cfg"
#   [[ "$FAIL_COUNT" -eq 0 ]]

# shellcheck source=script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/script_common.sh"

verify_harness_init() {
    PASS_COUNT=0
    FAIL_COUNT=0
    script_log_init ""
}

pass() { echo "${C_GREEN:-}PASS${C_RESET:-}: $*"; (( PASS_COUNT++ )) || true; }
fail() { echo "${C_RED:-}FAIL${C_RESET:-}: $*"; (( FAIL_COUNT++ )) || true; }
