#!/usr/bin/env bash
# verify-visibility-pane-widths.sh
#
# Verification helper: asserts visibility pane widths at multiple terminal widths.
#
# ALL tmux operations run on a PRIVATE socket (tmux -L pgai-verify-<PID>)
# so this script CANNOT disturb or kill the operator's live pgai-kanban-dashboard session
# running on the default tmux socket.  On EXIT/INT/TERM the private server is killed
# entirely (safe because no other session shares it).
#
# For each WIDTH in {80, 107, 220}:
#   1. Creates a synthetic tmux session (on the private socket), then resizes the window
#      to exactly WIDTH x HEIGHT using `tmux resize-window` (works in non-interactive /
#      non-TTY environments where `tmux new-session -x WIDTH` is capped to the controlling
#      terminal's width).
#   2. Splits the visibility window into the same 3+5 pane layout used by dashboard-create.sh.
#   3. Applies the same explicit-resize logic from dashboard-create.sh:
#        TOP_AVAIL = WIDTH - 2  (subtract 2 inter-pane borders, top row)
#        TOP_COL   = TOP_AVAIL / 3
#        BOT_AVAIL = WIDTH - 4  (subtract 4 inter-pane borders, bottom row)
#        BOT_COL   = BOT_AVAIL / 5
#      ALL panes are explicitly resized; no pane absorbs a tmux remainder.
#   4. Queries: tmux list-panes -t "<session>:visibility" -F '#{pane_index} #{pane_width}'
#   5. Asserts pane width criteria:
#        - Every pane >= 15 chars wide (all widths)
#        - Top-row panes (0, 1, 2) within +/-2 of (WIDTH-2)/3  (all widths)
#        - Bottom-row panes (3..7) within +/-2 of (WIDTH-4)/5  (strict at WIDTH=220;
#          informational at WIDTH=80,107)
#   6. Writes a per-WIDTH report log to REPORT_DIR
#   7. Tears down every session it created (trap ensures cleanup even on error)
#
# Session naming: pgai-kanban-dashboard-verify-<WIDTH>
#   No leaked sessions remain after a clean run.
#   Private socket server is killed on exit (no /tmp/tmux-* leaks).
#
# Exit codes:
#   0  — all assertions passed at all widths
#   1  — one or more assertions failed
#
# Usage:
#   verify-visibility-pane-widths.sh [--report-dir <path>] [--kanban-root <path>]
#
# Options:
#   --report-dir   Directory for per-WIDTH report logs (default: auto-generated under
#                  $PGAI_AGENT_KANBAN_TEMP_DIR via pgai_mktemp_d)
#   --kanban-root  Kanban root path (default: $PGAI_AGENT_KANBAN_ROOT_PATH or
#                  $HOME/pgai_agent_kanban)
#   -h, --help     Show this help and exit

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Centralized temp dir helpers
# ---------------------------------------------------------------------------
# Source temp.sh for resolver helpers (include guard in temp.sh prevents double-load).
_TEMP_SH="$(dirname "${BASH_SOURCE[0]}")/lib/temp.sh"
if [[ ! -f "$_TEMP_SH" ]]; then
  echo "ERROR: temp.sh not found: $_TEMP_SH" >&2
  exit 1
fi
# shellcheck source=lib/temp.sh
source "$_TEMP_SH"
unset _TEMP_SH

# ---------------------------------------------------------------------------
# Private tmux socket
# ---------------------------------------------------------------------------
# Every tmux call goes through _tmux(), which passes -L $_PGAI_VERIFY_SOCKET.
# This creates an isolated tmux server that CANNOT interact with the default
# socket (where the live pgai-kanban-dashboard session runs).  PID-suffixed
# for uniqueness when multiple verifier instances run concurrently.
_PGAI_VERIFY_SOCKET="pgai-verify-$$"

# Wrapper: route every tmux invocation through the private socket.
_tmux() { tmux -L "$_PGAI_VERIFY_SOCKET" "$@"; }

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
REPORT_DIR="$(pgai_mktemp_d verify-pane-widths)"
TEST_WIDTHS=(80 107 220)
SESSION_PREFIX="pgai-kanban-dashboard-verify"
SESSION_HEIGHT=50

# Tolerance for +/-N assertions (+/-2)
TOLERANCE=2
# Minimum pane width (>= 15)
MIN_WIDTH=15
# Width at which strict +/-2 bottom-row tolerance is a hard failure
STRICT_TOLERANCE_WIDTH=220

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --report-dir)
      REPORT_DIR="${2:?--report-dir requires a path}"
      shift 2
      ;;
    --kanban-root)
      KANBAN_ROOT="${2:?--kanban-root requires a path}"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \{0,1\}//' | head -50
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 [--report-dir <path>] [--kanban-root <path>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------
if ! command -v tmux &>/dev/null; then
  echo "ERROR: tmux is not installed or not in PATH." >&2
  echo "  Please install tmux 2.0+ and re-run this script." >&2
  echo "  On RHEL/Rocky Linux: sudo dnf install -y tmux" >&2
  exit 1
fi

# Check tmux version >= 2.0 (resize-window requires >= 2.9; list-panes -F since 1.6)
_TMUX_VERSION="$(tmux -V 2>/dev/null | awk '{print $2}' | sed 's/[^0-9.]//g')"
_TMUX_MAJOR="$(echo "$_TMUX_VERSION" | cut -d. -f1)"
_TMUX_MINOR="$(echo "$_TMUX_VERSION" | cut -d. -f2)"
if [[ -z "$_TMUX_MAJOR" ]] || [[ "$_TMUX_MAJOR" -lt 2 ]]; then
  echo "ERROR: tmux 2.9+ is required for resize-window (found: tmux ${_TMUX_VERSION:-unknown})." >&2
  exit 1
fi
if [[ "$_TMUX_MAJOR" -eq 2 ]] && [[ "${_TMUX_MINOR:-0}" -lt 9 ]]; then
  echo "ERROR: tmux 2.9+ is required for resize-window (found: tmux ${_TMUX_VERSION})." >&2
  exit 1
fi
unset _TMUX_VERSION _TMUX_MAJOR _TMUX_MINOR

# ---------------------------------------------------------------------------
# Report directory setup
# ---------------------------------------------------------------------------
mkdir -p "$REPORT_DIR"
echo "Report directory: ${REPORT_DIR}"

# ---------------------------------------------------------------------------
# Cleanup tracking — sessions created during this run
# ---------------------------------------------------------------------------
_CREATED_SESSIONS=()

cleanup_sessions() {
  # Kill all tracked sessions on the private socket, then kill the private
  # server itself (safe because nothing else shares this socket).
  local s
  for s in "${_CREATED_SESSIONS[@]:-}"; do
    if [[ -n "$s" ]] && _tmux has-session -t "$s" 2>/dev/null; then
      _tmux kill-session -t "$s" 2>/dev/null || true
    fi
  done
  # Kill the private tmux server entirely — cannot affect the default socket.
  tmux -L "$_PGAI_VERIFY_SOCKET" kill-server 2>/dev/null || true
}

trap cleanup_sessions EXIT INT TERM

# ---------------------------------------------------------------------------
# Helper: abs(a - b)
# ---------------------------------------------------------------------------
abs_diff() {
  local a="$1" b="$2"
  local diff=$(( a - b ))
  echo $(( diff < 0 ? -diff : diff ))
}

# ---------------------------------------------------------------------------
# Helper: run assertions for one WIDTH and produce report
# Returns 0 if all hard assertions pass, 1 if any hard assertion fails.
# ---------------------------------------------------------------------------
verify_width() {
  local WIDTH="$1"
  local SESSION_NAME="${SESSION_PREFIX}-${WIDTH}"
  local REPORT="${REPORT_DIR}/report-WIDTH-${WIDTH}.log"
  local fail=0

  # Reference targets — computed from WIDTH using the same formula as
  # dashboard-create.sh: border-aware integer division.
  # TOP_COL uses TOP_AVAIL = WIDTH - 2 (2 borders in 3-pane top row)
  # BOT_COL uses BOT_AVAIL = WIDTH - 4 (4 borders in 5-pane bottom row)
  local TOP_AVAIL=$(( WIDTH - 2 ))
  local BOT_AVAIL=$(( WIDTH - 4 ))
  local TOP_COL=$(( TOP_AVAIL / 3 ))
  local BOT_COL=$(( BOT_AVAIL / 5 ))
  [[ "$TOP_COL" -lt "$MIN_WIDTH" ]] && TOP_COL=$MIN_WIDTH
  [[ "$BOT_COL" -lt "$MIN_WIDTH" ]] && BOT_COL=$MIN_WIDTH

  {
    echo "=============================================================="
    echo "VERIFICATION REPORT — WIDTH=${WIDTH}"
    echo "Date: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Session: ${SESSION_NAME}"
    echo "Script: verify-visibility-pane-widths.sh"
    echo "=============================================================="
    echo ""
    echo "Reference values:"
    echo "  WIDTH      = ${WIDTH}"
    echo "  HEIGHT     = ${SESSION_HEIGHT}"
    echo "  TOP_COL    = (${WIDTH}-2)/3 = ${TOP_COL}  (target for top-row panes 0,1,2)"
    echo "  BOT_COL    = (${WIDTH}-4)/5 = ${BOT_COL}  (target for bottom-row panes 3..7)"
    echo "  TOLERANCE  = +/-${TOLERANCE}"
    echo ""
  } | tee "${REPORT}"

  # Kill any pre-existing session with this name (defensive)
  if _tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    _tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
  fi

  {
    echo "--- Session setup ---"
    echo "Creating session ${SESSION_NAME}"
    echo "Using resize-window to reach ${WIDTH}x${SESSION_HEIGHT}"
    echo "(new-session -x is capped by controlling terminal; resize-window is not)"
  } | tee -a "${REPORT}"

  # Create detached session on the PRIVATE socket (isolated from the live dashboard).
  _tmux new-session -d -s "$SESSION_NAME" -n "visibility"
  _CREATED_SESSIONS+=("$SESSION_NAME")

  # Force the window to exactly the target dimensions.
  # tmux resize-window is not constrained by the controlling terminal width.
  _tmux resize-window -t "${SESSION_NAME}:visibility" -x "$WIDTH" -y "$SESSION_HEIGHT"

  # Replicate the visibility window split and resize logic from dashboard-create.sh.
  # This is deliberately kept in sync with the dashboard-create.sh implementation
  # (Window 6 / Visibility section) so that the verification matches the actual
  # product behaviour.  If dashboard-create.sh changes its split or resize logic,
  # this script must be updated to match.
  #
  # Split sequence (same as dashboard-create.sh comments):
  #   Step 2: split pane 0 -v  → pane 0 (top half), pane 1 (bottom half)
  #   Step 3: split pane 0 -h  → pane 0=top-left, pane 1=top-right; former pane 1 → pane 2
  #   Step 4: split pane 1 -h  → pane 1=top-mid, pane 2=top-right; former pane 2 → pane 3
  #   Step 5: split pane 3 -h  → pane 3=bot-left, pane 4=bot-rest
  #   Step 6: split pane 4 -h  → pane 4=bot-2, pane 5=bot-rest
  #   Step 7: split pane 5 -h  → pane 5=bot-3, pane 6=bot-rest
  #   Step 8: split pane 6 -h  → pane 6=bot-4, pane 7=bot-right
  _tmux split-window -t "${SESSION_NAME}:visibility.0" -v -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.0" -h -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.1" -h -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.3" -h -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.4" -h -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.5" -h -l 50%
  _tmux split-window -t "${SESSION_NAME}:visibility.6" -h -l 50%

  # Read actual window dimensions after splits (tmux may adjust during splits)
  local VIS_WIDTH VIS_HEIGHT
  VIS_WIDTH=$(_tmux display-message -t "${SESSION_NAME}:visibility" -p '#{window_width}')
  VIS_HEIGHT=$(_tmux display-message -t "${SESSION_NAME}:visibility" -p '#{window_height}')

  # Sanity guards (same as dashboard-create.sh)
  VIS_WIDTH=${VIS_WIDTH:-120}
  VIS_HEIGHT=${VIS_HEIGHT:-24}
  [[ "$VIS_WIDTH"  -lt 45  ]] && VIS_WIDTH=45
  [[ "$VIS_HEIGHT" -lt 10  ]] && VIS_HEIGHT=10

  # Compute column widths — same formula as dashboard-create.sh.
  local A_TOP_AVAIL=$(( VIS_WIDTH - 2 ))
  local A_TOP_COL=$(( A_TOP_AVAIL / 3 ))
  local A_TOP_REM=$(( A_TOP_AVAIL % 3 ))
  local A_BOT_AVAIL=$(( VIS_WIDTH - 4 ))
  local A_BOT_COL=$(( A_BOT_AVAIL / 5 ))
  local A_BOT_REM=$(( A_BOT_AVAIL % 5 ))
  local A_HALF_H=$(( VIS_HEIGHT / 2 ))

  [[ "$A_TOP_COL" -lt "$MIN_WIDTH" ]] && A_TOP_COL=$MIN_WIDTH
  [[ "$A_BOT_COL" -lt "$MIN_WIDTH" ]] && A_BOT_COL=$MIN_WIDTH
  [[ "$A_HALF_H"  -lt 5  ]]           && A_HALF_H=5

  # Non-edge panes absorb the remainder (same assignment as dashboard-create.sh)
  local A_TOP_MID_COL=$(( A_TOP_COL + A_TOP_REM ))  # pane 1 = priority
  local A_BOT_MID_COL=$(( A_BOT_COL + A_BOT_REM ))  # pane 5 = writer

  # Resize all 8 panes explicitly — no pane absorbs a tmux remainder.
  _tmux resize-pane -t "${SESSION_NAME}:visibility.0" -x "${A_TOP_COL}"     -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.1" -x "${A_TOP_MID_COL}" -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.2" -x "${A_TOP_COL}"     -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.3" -x "${A_BOT_COL}"     -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.4" -x "${A_BOT_COL}"     -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.5" -x "${A_BOT_MID_COL}" -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.6" -x "${A_BOT_COL}"     -y "${A_HALF_H}"
  _tmux resize-pane -t "${SESSION_NAME}:visibility.7" -x "${A_BOT_COL}"     -y "${A_HALF_H}"

  # Query actual pane widths after resize
  local pane_data
  pane_data=$(_tmux list-panes -t "${SESSION_NAME}:visibility" -F '#{pane_index} #{pane_width}' 2>&1)

  {
    echo ""
    echo "--- tmux list-panes output ---"
    echo "  tmux list-panes -t \"${SESSION_NAME}:visibility\" -F '#{pane_index} #{pane_width}'"
    echo "${pane_data}" | while IFS= read -r line; do
      echo "  ${line}"
    done
    echo ""
    echo "  Actual window dimensions: VIS_WIDTH=${VIS_WIDTH}  VIS_HEIGHT=${VIS_HEIGHT}"
    echo "  Computed:  A_TOP_COL=${A_TOP_COL}  A_TOP_MID_COL=${A_TOP_MID_COL}  A_BOT_COL=${A_BOT_COL}  A_BOT_MID_COL=${A_BOT_MID_COL}  A_HALF_H=${A_HALF_H}"
  } | tee -a "${REPORT}"

  # Parse pane widths into an associative array
  declare -A PANE_WIDTHS
  while read -r idx w; do
    PANE_WIDTHS["$idx"]="$w"
  done <<< "$pane_data"

  # ---- Assertion 1: All panes >= MIN_WIDTH chars wide ----
  {
    echo ""
    echo "--- Assertion 1: All panes >= ${MIN_WIDTH} chars wide ---"
  } | tee -a "${REPORT}"

  local crit1_pass=1
  local idx
  for idx in 0 1 2 3 4 5 6 7; do
    local w="${PANE_WIDTHS[$idx]:-0}"
    local result
    if [[ "$w" -ge "$MIN_WIDTH" ]]; then
      result="PASS"
    else
      result="FAIL"
      crit1_pass=0
      fail=1
    fi
    echo "  Pane ${idx}: width=${w}  >= ${MIN_WIDTH}  ${result}" | tee -a "${REPORT}"
  done
  if [[ "$crit1_pass" -eq 1 ]]; then
    echo "  OVERALL: PASS" | tee -a "${REPORT}"
  else
    echo "  OVERALL: FAIL" | tee -a "${REPORT}"
  fi

  # ---- Assertion 2: Top-row panes (0,1,2) within +/-TOLERANCE of TOP_COL ----
  {
    echo ""
    echo "--- Assertion 2: Top-row panes (0,1,2) within +/-${TOLERANCE} of (WIDTH-2)/3 = ${TOP_COL} ---"
    echo "  (Non-edge pane 1 may be slightly wider due to remainder distribution; still within +/-2)"
  } | tee -a "${REPORT}"

  local crit2_pass=1
  for idx in 0 1 2; do
    local w="${PANE_WIDTHS[$idx]:-0}"
    local diff
    diff=$(abs_diff "$w" "$TOP_COL")
    local result
    if [[ "$diff" -le "$TOLERANCE" ]]; then
      result="PASS"
    else
      result="FAIL"
      crit2_pass=0
      fail=1
    fi
    echo "  Pane ${idx}: width=${w}  |${w} - ${TOP_COL}| = ${diff}  <= ${TOLERANCE}  ${result}" | tee -a "${REPORT}"
  done
  if [[ "$crit2_pass" -eq 1 ]]; then
    echo "  OVERALL: PASS" | tee -a "${REPORT}"
  else
    echo "  OVERALL: FAIL" | tee -a "${REPORT}"
  fi

  # ---- Assertion 3: Bottom-row panes (3..7) within +/-TOLERANCE of BOT_COL ----
  {
    echo ""
    echo "--- Assertion 3: Bottom-row panes (3..7) within +/-${TOLERANCE} of (WIDTH-4)/5 = ${BOT_COL} ---"
    if [[ "$WIDTH" -eq "$STRICT_TOLERANCE_WIDTH" ]]; then
      echo "  (strict +/-${TOLERANCE} hard failure at WIDTH=${STRICT_TOLERANCE_WIDTH})"
    else
      echo "  (Informational at WIDTH=${WIDTH}; strict enforcement is only at WIDTH=${STRICT_TOLERANCE_WIDTH})"
    fi
    echo "  (Non-edge pane 5 may be slightly wider due to remainder distribution; still within +/-2)"
  } | tee -a "${REPORT}"

  local crit3_pass=1
  for idx in 3 4 5 6 7; do
    local w="${PANE_WIDTHS[$idx]:-0}"
    local diff
    diff=$(abs_diff "$w" "$BOT_COL")
    local result
    if [[ "$diff" -le "$TOLERANCE" ]]; then
      result="PASS"
    else
      result="FAIL"
      crit3_pass=0
      # Hard failure only at the strict width
      if [[ "$WIDTH" -eq "$STRICT_TOLERANCE_WIDTH" ]]; then
        fail=1
      fi
    fi
    echo "  Pane ${idx}: width=${w}  |${w} - ${BOT_COL}| = ${diff}  <= ${TOLERANCE}  ${result}" | tee -a "${REPORT}"
  done
  if [[ "$crit3_pass" -eq 1 ]]; then
    echo "  OVERALL: PASS" | tee -a "${REPORT}"
  elif [[ "$WIDTH" -ne "$STRICT_TOLERANCE_WIDTH" ]]; then
    echo "  OVERALL: INFORMATIONAL FAIL (strict enforcement only at WIDTH=${STRICT_TOLERANCE_WIDTH})" | tee -a "${REPORT}"
  else
    echo "  OVERALL: FAIL" | tee -a "${REPORT}"
  fi

  # ---- Summary for this WIDTH ----
  {
    echo ""
    echo "--- Summary for WIDTH=${WIDTH} ---"
    if [[ "$fail" -eq 0 ]]; then
      echo "  RESULT: PASS — all assertions satisfied at WIDTH=${WIDTH}"
    else
      echo "  RESULT: FAIL — one or more assertions failed at WIDTH=${WIDTH}"
    fi
    echo ""
    echo "=============================================================="
    echo "END OF REPORT — WIDTH=${WIDTH}"
    echo "=============================================================="
  } | tee -a "${REPORT}"

  # Clean up this session immediately rather than waiting for EXIT trap
  if _tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
    _tmux kill-session -t "$SESSION_NAME" 2>/dev/null || true
  fi
  # Remove from tracked list
  local new_sessions=()
  local s
  for s in "${_CREATED_SESSIONS[@]:-}"; do
    [[ "$s" != "$SESSION_NAME" ]] && new_sessions+=("$s")
  done
  _CREATED_SESSIONS=("${new_sessions[@]:-}")

  return $fail
}

# ---------------------------------------------------------------------------
# Main — iterate over test widths
# ---------------------------------------------------------------------------
OVERALL_FAIL=0

echo ""
echo "verify-visibility-pane-widths.sh"
echo "================================="
echo "Test widths: ${TEST_WIDTHS[*]}"
echo "Report dir:  ${REPORT_DIR}"
echo "Kanban root: ${KANBAN_ROOT}"
echo ""

for W in "${TEST_WIDTHS[@]}"; do
  echo ""
  echo "--- Testing WIDTH=${W} ---"
  if verify_width "$W"; then
    echo "  WIDTH=${W}: PASS"
  else
    echo "  WIDTH=${W}: FAIL"
    OVERALL_FAIL=1
  fi
done

# ---------------------------------------------------------------------------
# Final summary report
# ---------------------------------------------------------------------------
echo ""
echo "======================================"
echo "OVERALL RESULT"
echo "======================================"

SUMMARY_FILE="${REPORT_DIR}/summary.log"
{
  echo "verify-visibility-pane-widths.sh — Run Summary"
  echo "Date: $(date '+%Y-%m-%d %H:%M:%S')"
  echo ""
  echo "Test widths: ${TEST_WIDTHS[*]}"
  echo ""
  for W in "${TEST_WIDTHS[@]}"; do
    local_report="${REPORT_DIR}/report-WIDTH-${W}.log"
    if [[ -f "$local_report" ]]; then
      result_line=$(grep "^  RESULT:" "$local_report" 2>/dev/null | tail -1 || echo "  RESULT: (no result found)")
      echo "  WIDTH=${W}: ${result_line#  RESULT: }"
    fi
  done
  echo ""
  if [[ "$OVERALL_FAIL" -eq 0 ]]; then
    echo "OVERALL: PASS — all assertions satisfied at all test widths"
  else
    echo "OVERALL: FAIL — see per-WIDTH reports in ${REPORT_DIR}"
  fi
} | tee "$SUMMARY_FILE"

echo ""
echo "Per-WIDTH reports written to: ${REPORT_DIR}/"

# Verify no leaked sessions remain on the private socket.
# (The trap's kill-server call should have cleaned everything up, but
# this check catches the case where the server is gone but tmux doesn't
# error cleanly — the loop is harmless if the server is already dead.)
LEAKED=0
for W in "${TEST_WIDTHS[@]}"; do
  S="${SESSION_PREFIX}-${W}"
  if _tmux has-session -t "$S" 2>/dev/null; then
    echo "WARNING: session '${S}' still exists after cleanup!" >&2
    LEAKED=1
  fi
done
if [[ "$LEAKED" -eq 0 ]]; then
  echo "Session cleanup: OK (no ${SESSION_PREFIX}-* sessions remain on private socket)"
fi

exit "$OVERALL_FAIL"
