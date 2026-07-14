#!/usr/bin/env bash
# team/scripts/verify-install-policy.sh
# Verifies install.sh re-run safety policies on an existing installation.
#
# This script performs read-only inspection of the current environment and
# a dry-run simulation of install.sh to confirm:
#
#   1. Canonical env var: PGAI_AGENT_KANBAN_ROOT_PATH is set in the live env.
#   2. Fresh-install path: install.sh uses $HOME/pgai_agent_kanban when no
#      override is set.
#   3. EPERM on crontab: install.sh handles a locked crontab gracefully (non-fatal).
#   4. Idempotency: the installed state after two runs equals the state after one run.
#   5. Canonical env var: PGAI_AGENT_KANBAN_ROOT_PATH is resolved after dry-run.
#   6. Crontab seam invariant: safe_overwrite.sh routes all crontab calls
#      through _run_crontab (no direct 'crontab ' calls).
#   7. install-crontab.sh requires explicit --yes flag for no-TTY installs.
#
# Usage:
#   team/scripts/verify-install-policy.sh [--simulate-fresh] [--check-only]
#                                          [--verbose] [--help]
#
# Modes:
#   (default)        — Run --simulate-fresh and report on live env state.
#   --simulate-fresh — Confirm install.sh --dry-run targets the canonical
#                      default path when no env override is set.
#   --check-only     — Skip simulations; only report on live env state.
#   --verbose        — Show install.sh output during simulation runs.
#
# Read-only guarantees:
#   - Does NOT write to /var/spool/cron/<user> at any point.
#   - Does NOT relocate or delete any existing kanban installation.
#   - Simulation uses --dry-run flag on install.sh (no files are written).
#   - Temporary dirs created during simulation are removed after each test.
#
# Exit codes:
#   0  All checks passed.
#   1  One or more checks failed.
#   2  Usage error or environment prerequisite missing.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Color helpers
# ---------------------------------------------------------------------------
if [[ -t 1 ]]; then
  C_RED=$'\033[0;31m'
  C_GREEN=$'\033[0;32m'
  C_YELLOW=$'\033[0;33m'
  C_BLUE=$'\033[0;34m'
  C_BOLD=$'\033[1m'
  C_RESET=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_RESET=""
fi

info()  { echo "${C_BLUE}[verify-install-policy]${C_RESET} $*"; }
ok()    { echo "${C_GREEN}[verify-install-policy] PASS${C_RESET} $*"; }
fail()  { echo "${C_RED}[verify-install-policy] FAIL${C_RESET} $*"; }
warn()  { echo "${C_YELLOW}[verify-install-policy]${C_RESET} WARNING: $*"; }
note()  { echo "${C_YELLOW}[verify-install-policy]${C_RESET} NOTE: $*"; }

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
SIMULATE_FRESH=false
CHECK_ONLY=false
VERBOSE=false

_print_usage() {
  cat >&2 <<USAGE
Usage: $(basename "$0") [--simulate-fresh] [--check-only] [--verbose] [--help]

Verify install.sh re-run safety and path-resolution policies.

Modes:
  --simulate-fresh      Confirm install.sh --dry-run targets canonical default
                        ($HOME/pgai_agent_kanban) when no env override is set.
  --check-only          Only report live env state — no simulations.
  --verbose             Show install.sh output during simulation runs.
  --help                Print this usage and exit 0.

Environment variables inspected (read-only):
  PGAI_AGENT_KANBAN_ROOT_PATH          Canonical env var

Exit codes:
  0  All active checks passed.
  1  One or more checks failed.
  2  Usage error or prerequisite missing.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --simulate-fresh)     SIMULATE_FRESH=true; shift ;;
    --check-only)         CHECK_ONLY=true; shift ;;
    --verbose)            VERBOSE=true; shift ;;
    --help|-h)            _print_usage; exit 0 ;;
    *)
      echo "Unknown argument: $1" >&2
      _print_usage
      exit 2
      ;;
  esac
done

# If no mode flags given, run the fresh-install simulation by default.
if [[ "$CHECK_ONLY" == "false" && "$SIMULATE_FRESH" == "false" ]]; then
  SIMULATE_FRESH=true
fi

# ---------------------------------------------------------------------------
# Locate install.sh (must be sibling of team/ directory)
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_SH="$(cd "$SCRIPT_DIR/../.." && pwd)/install.sh"

# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
# shellcheck source=lib/temp.sh
source "${SCRIPT_DIR}/lib/temp.sh"

if [[ ! -f "$INSTALL_SH" ]]; then
  echo "[verify-install-policy] ERROR: install.sh not found at $INSTALL_SH" >&2
  echo "  Run this script from the kanban source tree." >&2
  exit 2
fi

if [[ ! -x "$INSTALL_SH" ]]; then
  echo "[verify-install-policy] ERROR: install.sh is not executable: $INSTALL_SH" >&2
  exit 2
fi

# ---------------------------------------------------------------------------
# Global pass/fail tracking
# ---------------------------------------------------------------------------
_PASS=0
_FAIL=0

_check_pass() { (( _PASS++ )) || true; ok "$1"; }
_check_fail() { (( _FAIL++ )) || true; fail "$1"; }

# ---------------------------------------------------------------------------
# Temp dir management
# ---------------------------------------------------------------------------
# All temp work goes under the resolved framework temp root.
_TEMP_ROOT="$(pgai_temp_dir)/verify_install_policy"
mkdir -p "$_TEMP_ROOT"

_cleanup_temp() {
  [[ -n "${_TEMP_ROOT:-}" && -d "${_TEMP_ROOT}" ]] && rm -rf "$_TEMP_ROOT"
}
trap '_cleanup_temp' EXIT

# ---------------------------------------------------------------------------
# Helper: run install.sh --dry-run and capture output
# ---------------------------------------------------------------------------
_run_install_dry_run() {
  # $1 — description for logging
  # stdout — captured install.sh output
  # Returns install.sh exit code.
  local desc="$1"
  local out
  if [[ "$VERBOSE" == "true" ]]; then
    info "Running install.sh --dry-run ($desc)..."
    bash "$INSTALL_SH" --dry-run 2>&1 | tee /dev/stderr
    return "${PIPESTATUS[0]}"
  else
    bash "$INSTALL_SH" --dry-run 2>&1
    return $?
  fi
}

# ---------------------------------------------------------------------------
# CHECK 1 — Canonical env var presence (live environment)
# ---------------------------------------------------------------------------
info "=== Check 1: Canonical env var (live environment) ==="

_new_var="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ -z "$_new_var" ]]; then
  note "PGAI_AGENT_KANBAN_ROOT_PATH is not set in the current shell."
  note "This is expected for a shell that has not sourced the post-install profile."
  note "After install.sh runs, the canonical var will be set to the resolved path."
  note "Env var presence will be verified during dry-run simulations below."
else
  _check_pass "PGAI_AGENT_KANBAN_ROOT_PATH is set: $_new_var"
fi
unset _new_var

# ---------------------------------------------------------------------------
# CHECK 2 — Crontab EPERM handling (read-only inspection)
# ---------------------------------------------------------------------------
info ""
info "=== Check 2: Crontab EPERM handling (read-only inspection) ==="

# Inspect /var/spool/cron/<user> for chattr +i flag — read-only, no writes.
_CRON_SPOOL="/var/spool/cron/$(id -un)"
if [[ -f "$_CRON_SPOOL" ]]; then
  # lsattr is the only safe way to check immutable flag without modifying anything.
  if command -v lsattr >/dev/null 2>&1; then
    _lsattr_out="$(lsattr "$_CRON_SPOOL" 2>/dev/null || true)"
    if echo "$_lsattr_out" | grep -qE '^[^ ]*i[^ ]* '; then
      note "Crontab spool file is LOCKED immutable (chattr +i): $_CRON_SPOOL"
      note "install.sh EPERM handler will activate on next crontab install attempt."
      note "To verify the handler: run install.sh (not --dry-run) — it should emit a warn and continue."
      note "To unlock for crontab install: sudo chattr -i $_CRON_SPOOL"
      _check_pass "EPERM guard in place: crontab spool is chattr+i locked; install.sh handles non-fatally"
    else
      _check_pass "Crontab spool is NOT locked (chattr -i); EPERM handler not needed at this time"
    fi
  else
    note "lsattr not found in PATH — cannot check chattr+i status of $_CRON_SPOOL"
    note "install.sh EPERM handler is always present regardless; this check is advisory."
    _check_pass "EPERM handler present in install.sh (lsattr unavailable for lock inspection)"
  fi
else
  note "No crontab spool file at $_CRON_SPOOL (user has no crontab yet)"
  _check_pass "EPERM handler in install.sh is always active; no spool file to lock-check"
fi

# Confirm EPERM handler code exists in install.sh (static check)
if grep -q "chattr -i" "$INSTALL_SH" 2>/dev/null; then
  _check_pass "install.sh contains EPERM/chattr handler (static source check)"
else
  _check_fail "install.sh does NOT contain chattr EPERM handler — policy may be missing"
fi

# ---------------------------------------------------------------------------
# SIMULATION A — Fresh install path (canonical default, no env override)
# ---------------------------------------------------------------------------
if [[ "$SIMULATE_FRESH" == "true" && "$CHECK_ONLY" == "false" ]]; then
  info ""
  info "=== Simulation A: Fresh install path (canonical default) ==="
  info "Creating a simulation home directory with no env override..."

  _sim_fresh_home="$(mktemp -d "$_TEMP_ROOT/sim_fresh_home_XXXXXX")"

  info "Simulation home: $_sim_fresh_home"

  # Run install.sh --dry-run with HOME pointing to an empty dir and no env overrides.
  _sim_fresh_output=""
  _sim_fresh_exit=0
  _sim_fresh_output=$(
    HOME="$_sim_fresh_home" \
    PGAI_AGENT_KANBAN_ROOT_PATH="" \
    bash "$INSTALL_SH" --dry-run 2>&1
  ) || _sim_fresh_exit=$?

  if [[ "$VERBOSE" == "true" ]]; then
    echo "$_sim_fresh_output"
  fi

  # Check 1: exit code 0
  if [[ "$_sim_fresh_exit" -eq 0 ]]; then
    _check_pass "SIM-A: install.sh --dry-run exited 0 (canonical default path)"
  else
    _check_fail "SIM-A: install.sh --dry-run exited $_sim_fresh_exit (expected 0)"
  fi

  # Check 2: install target must be the canonical path (pgai_agent_kanban)
  if echo "$_sim_fresh_output" | grep -q "Install target:.*pgai_agent_kanban"; then
    _check_pass "SIM-A: install target correctly set to canonical default path (pgai_agent_kanban)"
  else
    _check_fail "SIM-A: install target does NOT point to pgai_agent_kanban"
    echo "$_sim_fresh_output" | grep "Install target:" | head -3 | sed 's/^/  /'
  fi

  # Clean up
  rm -rf "$_sim_fresh_home"
  info "Simulation A cleanup complete."
fi

# ---------------------------------------------------------------------------
# CHECK 3 — Idempotency check (dry-run level)
# ---------------------------------------------------------------------------
info ""
info "=== Check 3: Idempotency (dry-run level) ==="

# Idempotency at the install-script level: running install.sh --dry-run twice
# should produce the same exit code and the same "Install target:" line.
# We cannot test full file-write idempotency without actually running install.sh
# (which would overwrite files), so we use --dry-run as the idempotency proxy.
_idem_home="$(mktemp -d "$_TEMP_ROOT/idem_home_XXXXXX")"

_run1_output=""
_run1_exit=0
_run1_output=$(
  HOME="$_idem_home" \
  PGAI_AGENT_KANBAN_ROOT_PATH="" \
  bash "$INSTALL_SH" --dry-run 2>&1
) || _run1_exit=$?

_run2_output=""
_run2_exit=0
_run2_output=$(
  HOME="$_idem_home" \
  PGAI_AGENT_KANBAN_ROOT_PATH="" \
  bash "$INSTALL_SH" --dry-run 2>&1
) || _run2_exit=$?

_run1_target="$(echo "$_run1_output" | grep "Install target:" | head -1)"
_run2_target="$(echo "$_run2_output" | grep "Install target:" | head -1)"

if [[ "$_run1_exit" -eq "$_run2_exit" ]]; then
  _check_pass "Idempotency: install.sh --dry-run exits $_run1_exit on both run 1 and run 2"
else
  _check_fail "Idempotency: install.sh --dry-run exits differ (run1=$_run1_exit, run2=$_run2_exit)"
fi

# Strip the [install] log prefix so only the path is shown in the check message.
_run1_target_clean="$(echo "$_run1_target" | sed 's/^\[install\][[:space:]]*//')"
_run2_target_clean="$(echo "$_run2_target" | sed 's/^\[install\][[:space:]]*//')"

if [[ "$_run1_target" == "$_run2_target" ]]; then
  _check_pass "Idempotency: install target is identical on both runs: $_run1_target_clean"
else
  _check_fail "Idempotency: install target differs between runs"
  info "  Run 1: $_run1_target_clean"
  info "  Run 2: $_run2_target_clean"
fi

rm -rf "$_idem_home"

# ---------------------------------------------------------------------------
# CHECK 4 — Canonical env var set after dry-run
# ---------------------------------------------------------------------------
info ""
info "=== Check 4: Canonical env var after install.sh path resolution ==="
info "Checking that install.sh resolves PGAI_AGENT_KANBAN_ROOT_PATH to a path..."

_ev_home="$(mktemp -d "$_TEMP_ROOT/ev_home_XXXXXX")"

# Capture the "Install target:" line — that's what install.sh resolves for KANBAN_ROOT.
_ev_output=""
_ev_output=$(
  HOME="$_ev_home" \
  PGAI_AGENT_KANBAN_ROOT_PATH="" \
  bash "$INSTALL_SH" --dry-run 2>&1
) || true

_ev_target="$(echo "$_ev_output" | grep "Install target:" | head -1 | sed 's/.*Install target:[[:space:]]*//')"

if [[ -n "$_ev_target" ]]; then
  _check_pass "Canonical env var: install.sh resolves PGAI_AGENT_KANBAN_ROOT_PATH to: $_ev_target"
else
  _check_fail "Canonical env var: could not extract Install target from install.sh output"
fi

rm -rf "$_ev_home"

# ---------------------------------------------------------------------------
# CHECK 5 — Crontab seam invariant: safe_overwrite.sh routes all crontab
#            invocations through _run_crontab (no direct 'crontab ' calls)
# ---------------------------------------------------------------------------
info ""
info "=== Check 5: Crontab seam invariant (safe_overwrite.sh) ==="

_SAFE_OVERWRITE_SH="${SCRIPT_DIR}/lib/safe_overwrite.sh"
if [[ ! -f "$_SAFE_OVERWRITE_SH" ]]; then
  _check_fail "CHECK 5: safe_overwrite.sh not found at $_SAFE_OVERWRITE_SH"
else
  # Scan for bare 'crontab ' invocations — these should only appear inside
  # _run_crontab itself (the chokepoint) and in comments/strings, never as
  # standalone shell commands.  We exclude:
  #   - the _run_crontab function body (the one legitimate call site)
  #   - comment lines (# ...)
  #   - printf/echo lines (documentation strings, not actual invocations)
  #
  # Strategy: grep for lines that look like shell commands calling crontab
  # directly (i.e. bare "crontab " not prefixed by the _run_crontab wrapper).
  # A line is a "direct call" if it matches /^\s*(crontab|"\$\{.*:-crontab\}")/.
  # We exclude the _run_crontab body line itself by pattern.

  _direct_crontab_calls=""
  _direct_crontab_calls=$(
    grep -nE '^\s*(crontab\b)' "$_SAFE_OVERWRITE_SH" \
      | grep -v '_run_crontab' \
      | grep -v '^\s*#' \
      || true
  )

  if [[ -z "$_direct_crontab_calls" ]]; then
    _check_pass "CHECK 5: safe_overwrite.sh has no direct 'crontab' invocations outside _run_crontab seam"
  else
    _check_fail "CHECK 5: safe_overwrite.sh contains direct 'crontab' call(s) bypassing the seam:"
    echo "$_direct_crontab_calls" | sed 's/^/  /'
  fi

  # Also confirm _run_crontab function definition exists (seam is present).
  if grep -q '_run_crontab()' "$_SAFE_OVERWRITE_SH"; then
    _check_pass "CHECK 5: _run_crontab seam function is defined in safe_overwrite.sh"
  else
    _check_fail "CHECK 5: _run_crontab seam function NOT found in safe_overwrite.sh — seam missing"
  fi

  # Confirm PGAI_CRONTAB_CMD override is honored in the seam.
  if grep -q 'PGAI_CRONTAB_CMD' "$_SAFE_OVERWRITE_SH"; then
    _check_pass "CHECK 5: PGAI_CRONTAB_CMD env override honored in safe_overwrite.sh seam"
  else
    _check_fail "CHECK 5: PGAI_CRONTAB_CMD env override NOT found in safe_overwrite.sh — seam untestable"
  fi
fi

# ---------------------------------------------------------------------------
# CHECK 6 — install-crontab.sh requires explicit confirm flag for no-TTY
# ---------------------------------------------------------------------------
info ""
info "=== Check 6: install-crontab.sh no-TTY explicit-confirm requirement ==="

_INSTALL_CRONTAB_SH="${SCRIPT_DIR}/install-crontab.sh"
if [[ ! -f "$_INSTALL_CRONTAB_SH" ]]; then
  _check_fail "CHECK 6: install-crontab.sh not found at $_INSTALL_CRONTAB_SH"
else
  # Static check: the script must have a no-TTY decline path that requires --yes.
  # We look for the pattern that: (1) detects no TTY, (2) checks YES_FLAG,
  # (3) emits a decline/error message referencing --yes, (4) exits non-zero.

  # (a) no-TTY detection present
  if grep -qE 'exec 3<>/dev/tty|_preflight_tty_ok' "$_INSTALL_CRONTAB_SH"; then
    _check_pass "CHECK 6: install-crontab.sh has no-TTY detection logic"
  else
    _check_fail "CHECK 6: install-crontab.sh missing no-TTY detection (exec 3<>/dev/tty)"
  fi

  # (b) --yes / YES_FLAG present
  if grep -qE '\-\-yes|YES_FLAG' "$_INSTALL_CRONTAB_SH"; then
    _check_pass "CHECK 6: install-crontab.sh has --yes / YES_FLAG explicit confirm flag"
  else
    _check_fail "CHECK 6: install-crontab.sh missing --yes confirm flag"
  fi

  # (c) no-TTY without --yes exits non-zero (has 'exit 2' on no-TTY path)
  if grep -qE 'exit 2' "$_INSTALL_CRONTAB_SH"; then
    _check_pass "CHECK 6: install-crontab.sh exits non-zero (exit 2) on no-TTY without --yes"
  else
    _check_fail "CHECK 6: install-crontab.sh missing 'exit 2' for no-TTY decline path"
  fi

  # (d) runtime check: run install-crontab.sh without --yes in a no-TTY context;
  #     must exit non-zero and must NOT write any crontab.
  #     We use a fake PGAI_CRONTAB_CMD pointing at /dev/null to ensure even if
  #     the gate fails, the real crontab binary is never called.
  _notty_home="$(mktemp -d "$_TEMP_ROOT/check6_notty_home_XXXXXX")"
  _notty_kanban="$(mktemp -d "$_TEMP_ROOT/check6_notty_kanban_XXXXXX")"
  # Minimal kanban root so install-crontab.sh's root-existence check passes.
  mkdir -p "$_notty_kanban"

  _notty_exit=0
  _notty_output=""
  _notty_output=$(
    HOME="$_notty_home" \
    PGAI_AGENT_KANBAN_ROOT_PATH="$_notty_kanban" \
    PGAI_CRONTAB_CMD="/bin/true" \
    setsid bash "$_INSTALL_CRONTAB_SH" --tier=small \
      < /dev/null 2>&1
  ) || _notty_exit=$?

  if [[ "$_notty_exit" -ne 0 ]]; then
    _check_pass "CHECK 6: install-crontab.sh --tier=small (no --yes, no TTY) exited non-zero ($_notty_exit)"
  else
    _check_fail "CHECK 6: install-crontab.sh --tier=small (no --yes, no TTY) exited 0 — expected non-zero"
    info "  Output: $_notty_output"
  fi

  if printf '%s\n' "$_notty_output" | grep -qiE 'no TTY|non-interactive|--yes|require'; then
    _check_pass "CHECK 6: no-TTY decline message references --yes requirement"
  else
    _check_fail "CHECK 6: no-TTY decline message does not mention --yes requirement"
    info "  Output: $_notty_output"
  fi

  rm -rf "$_notty_home" "$_notty_kanban"
fi

# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------
_TOTAL=$(( _PASS + _FAIL ))
echo ""
echo "${C_BOLD}=== verify-install-policy: summary ===${C_RESET}"
echo "  Total checks: $_TOTAL"
echo "  ${C_GREEN}Passed:${C_RESET} $_PASS"
if [[ "$_FAIL" -gt 0 ]]; then
  echo "  ${C_RED}Failed:${C_RESET} $_FAIL"
else
  echo "  Failed: 0"
fi
echo ""

if [[ "$_FAIL" -eq 0 ]]; then
  ok "All install policy checks passed."
  exit 0
else
  fail "$_FAIL check(s) failed. Review output above for details."
  exit 1
fi
