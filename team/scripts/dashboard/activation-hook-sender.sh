#!/usr/bin/env bash
# activation-hook-sender.sh
# The "sender" half of the dashboard's activation-hook lifecycle
# (see lib/activation_hook.sh for the install/remove half).
#
# Invoked by the session-scoped tmux hooks lib/activation_hook.sh installs
# -- every time the operator switches windows inside the dashboard session
# (session-window-changed) and every time a client attaches to it
# (client-attached) -- via `run-shell -b`, so the hook never blocks the
# switch or attach it is reacting to. Resolves the session's now-current
# window, and for every pane in that window:
#   - if the pane has reported armed (lib/pane_readiness.sh's readiness
#     marker for that pane exists), sends PGAI_DASHBOARD_PANE_SIGNAL to the
#     pane's shell -- the same signal every armed pane's first statement
#     already ignores (`trap '' <signal>`), so delivery is inert-safe by
#     construction -- and wakes the pane's cadence loop, if one has
#     registered for that pane, with PGAI_DASHBOARD_LOOP_SIGNAL
#     (lib/cadence.sh dashboard_cadence_wake_loop; see that file's "HOW A
#     LOOP IS WOKEN" for why the loop has its own signal). The woken loop
#     re-checks visibility and renders -- or continues its stopped stream
#     -- at once. An interactive shell pane registers no loop, so only its
#     shell is signalled, never the commands an operator runs from it.
#   - if the pane has NOT reported armed, it is skipped — sending the
#     signal to a pane whose trap has not landed yet would kill it under
#     the OS default disposition, exactly the failure the whole readiness
#     handshake exists to prevent. The skip is narrated to a pane-scoped
#     debug log (never stdout — see below), naming the pane, and the loop
#     continues to every remaining pane.
#
# This script NEVER exits non-zero from its own runtime logic (an
# unarmed pane, a torn-down session, a window mid-teardown are all
# ordinary races this sender must ride out silently) — CONVERT-class per
# team/SOP-core.md "Gates and Refusals": each skip is a continue, not a
# stop. The only non-zero exits below are CLI usage errors (unrecognized
# flag), the same class of refusal every other dashboard script in this
# directory uses for a malformed invocation.
#
# WHY SKIP NARRATION NEVER REACHES STDOUT
# --------------------------------------------
# This sender is invoked by tmux's `run-shell -b` on every window switch,
# with its stdout landing wherever run-shell happens to paint it — the
# pane the operator just switched to. A plain `echo` here used to stamp
# "not armed yet" onto that pane's display, permanently, on every switch,
# for any window whose single-pane arm marker create.sh wrote under the
# bare window name (see the dual-label armed check below) rather than
# "<window>.0" the way this sender's tmux query expects. Every skip line
# below is routed instead to the SAME per-pane debug log a running
# cadence-loop.sh instance already appends its own renderer's narration
# to (lib/cadence.sh's dashboard_cadence_pane_debug_log_path) — one
# routing convention shared by both channels rather than two invented
# independently.
#
# Usage:
#   activation-hook-sender.sh --session <name> [--kanban-root <path>] [--window <name>]
#
# Flags:
#   --kanban-root   Kanban root, used to resolve the readiness-marker
#                    directory (default: $PGAI_AGENT_KANBAN_ROOT_PATH)
#   --session        tmux session to operate on (required)
#   --window         Window to signal panes in (default: the session's
#                    current window, resolved via `tmux display-message`)
#   -h, --help       Show this help and exit
#
# Environment:
#   PGAI_DASHBOARD_LOOP_SIGNAL   Signal that wakes a registered cadence
#                                 loop (default: USR2; see lib/cadence.sh)
#   PGAI_DASHBOARD_PANE_SIGNAL   Signal name sent to armed panes
#                                 (default: USR1 — must match the ignore
#                                 trap lib/pane_readiness.sh installs)

set -uo pipefail
# shellcheck source=../lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/../lib/env_bootstrap.sh"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/pane_readiness.sh
source "${SCRIPT_DIR}/lib/pane_readiness.sh"
# shellcheck source=lib/cadence.sh
# dashboard_cadence_pane_debug_log_path is this sender's narration target —
# the same per-pane debug log dashboard_cadence_run_loop's own renderer
# narration lands in (see this file's header).
source "${SCRIPT_DIR}/lib/cadence.sh"

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
SESSION_NAME="${PGAI_DASHBOARD_SESSION_NAME:-pgai-kanban-dashboard}"
WINDOW_NAME=""

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --kanban-root)
      KANBAN_ROOT="${2:-$KANBAN_ROOT}"
      shift 2
      ;;
    --session)
      SESSION_NAME="${2:-$SESSION_NAME}"
      shift 2
      ;;
    --window)
      WINDOW_NAME="${2:-}"
      shift 2
      ;;
    -h|--help)
      # Read the whole header via mapfile so grep/sed always finish writing
      # before anything reads their output, then slice to the line limit
      # locally -- head -N as a pipe stage can close its input early and
      # SIGPIPE the writers under pipefail.
      mapfile -t __help_lines < <(grep '^#' "$0" | grep -v '^#!' | sed 's/^# \{0,1\}//')
      printf '%s\n' "${__help_lines[@]:0:68}"
      exit 0
      ;;
    *)
      echo "ERROR: Unknown argument: $1" >&2
      echo "Usage: $0 --session <name> [--kanban-root <path>] [--window <name>]" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# _sender_debug_narrate <pane_label> <message>
# Appends <message> to <pane_label>'s pane-scoped debug log instead of this
# script's own stdout — see this file's "WHY SKIP NARRATION NEVER REACHES
# STDOUT" header. Honors --kanban-root the same way dashboard_pane_armed_dir
# already does (a temporary override of PGAI_AGENT_KANBAN_ROOT_PATH for the
# resolution call only). Best-effort, CONVERT-class: an unwritable log
# target loses the narration silently rather than blocking the sender — the
# same fallback lib/cadence.sh's own render loop uses for the identical
# target.
# ---------------------------------------------------------------------------
_sender_debug_narrate() {
  local pane_label="$1" message="$2"
  local log_path
  log_path="$(PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" dashboard_cadence_pane_debug_log_path "$pane_label" 2>/dev/null)"
  [[ -n "$log_path" ]] && printf '%s\n' "$message" >> "$log_path" 2>/dev/null
  return 0
}

# ---------------------------------------------------------------------------
# Race-tolerant early exits — the hook can fire after the session (or the
# window it targeted) has already gone away; neither is this sender's
# business to report on, only to ride out cleanly.
# ---------------------------------------------------------------------------
if ! command -v tmux &>/dev/null; then
  exit 0
fi

if ! tmux has-session -t "$SESSION_NAME" 2>/dev/null; then
  exit 0
fi

# The panes are listed through WINDOW_TARGET: the window's id (@N) when the
# sender resolved the current window itself -- an id stays valid however the
# window is renamed, and a renamed drill window's name ("drill-1: <project>")
# carries a ':' -- or "<session>:<name>" for an explicit --window.
WINDOW_TARGET=""
if [[ -z "$WINDOW_NAME" ]]; then
  IFS=$'\t' read -r WINDOW_TARGET WINDOW_NAME < <(
    tmux display-message -t "$SESSION_NAME" -p '#{window_id}	#{window_name}' 2>/dev/null || true
  ) || true
fi

if [[ -z "$WINDOW_NAME" ]]; then
  exit 0
fi
[[ -n "$WINDOW_TARGET" ]] || WINDOW_TARGET="${SESSION_NAME}:${WINDOW_NAME}"

# ---------------------------------------------------------------------------
# Resolve the readiness-marker directory this session's panes reported
# into (the exact directory create.sh's all-armed handshake polled).
# ---------------------------------------------------------------------------
ARMED_DIR="$(dashboard_pane_armed_dir "$KANBAN_ROOT" "$SESSION_NAME")"

# ---------------------------------------------------------------------------
# Signal every armed pane in the now-current window; skip and narrate (to
# its debug log, never stdout) every unarmed one. list-panes failing
# (window torn down mid-flight) is the same kind of harmless race as the
# session checks above — an empty result, not an error.
#
# ARMED_DIR is checked under the tmux-reported "<window>.<index>" key, under
# "<arm label>.<index>" when the window carries the @pgai_arm_label window
# option, AND, for pane_index 0, under the bare window-name (or arm-label)
# key create.sh's dashboard_send_armed_keys wrote for several single-pane
# windows (attention, metadata, agent-metrics). The arm label is the name a
# window's panes were armed under before create.sh renamed it: a drill
# window is armed as "drill-N" and then renamed "drill-N: <project>", so its
# current name never matches a marker, and without the label every drill
# pane would be skipped as unarmed on every switch.
# ---------------------------------------------------------------------------
PANE_SIGNAL="${PGAI_DASHBOARD_PANE_SIGNAL}"

while IFS=$'\t' read -r pane_label pane_id pane_pid arm_label; do
  [[ -z "$pane_label" ]] && continue

  pane_index="${pane_label##*.}"
  if [[ -e "${ARMED_DIR}/${pane_label}" ]] || \
     [[ -n "$arm_label" && -e "${ARMED_DIR}/${arm_label}.${pane_index}" ]] || \
     [[ "$pane_index" == "0" && -e "${ARMED_DIR}/${WINDOW_NAME}" ]] || \
     [[ "$pane_index" == "0" && -n "$arm_label" && -e "${ARMED_DIR}/${arm_label}" ]]; then
    kill -s "$PANE_SIGNAL" "$pane_pid" 2>/dev/null || true
    PGAI_AGENT_KANBAN_ROOT_PATH="$KANBAN_ROOT" dashboard_cadence_wake_loop "$pane_id" || true
  else
    # CONVERT (team/SOP-core.md "Gates and Refusals"): a pane whose trap
    # is not yet armed is skipped, never signalled — narrated to its
    # pane-scoped debug log (never stdout, which tmux would paint onto
    # whichever pane happens to be active) so the skip and its reason
    # remain inspectable.
    _sender_debug_narrate "$pane_label" \
      "activation-hook-sender: skipping pane ${pane_label} (${pane_id}): not armed yet, signal trap not confirmed installed"
  fi
done < <(tmux list-panes -t "${WINDOW_TARGET}" -F '#{window_name}.#{pane_index}	#{pane_id}	#{pane_pid}	#{@pgai_arm_label}' 2>/dev/null || true)

exit 0
