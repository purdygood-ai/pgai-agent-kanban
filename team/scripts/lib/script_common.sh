#!/usr/bin/env bash
# team/scripts/lib/script_common.sh
# Single home for the small pieces of boilerplate nearly every
# team/scripts/ entry point re-implements on its own: ANSI colour
# rendering, the EXIT-trap cleanup body, and a generic usage-text emitter.
# Source this file once per script; it only defines functions — it
# installs no trap, sets no shell options, and parses no arguments, so
# sourcing it is always a safe no-op until the caller invokes one of the
# functions below.
#
# SOURCING
# --------
# This file does not self-locate — the caller resolves its own directory
# and sources this file relative to it, matching the existing lib/
# sourcing convention used throughout team/scripts/ (SCRIPT_DIR-relative,
# never assuming the caller's cwd):
#
#   # From a script directly under team/scripts/ (e.g. kanban-status.sh):
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=lib/script_common.sh
#   source "${SCRIPT_DIR}/lib/script_common.sh"
#
#   # From a script one level deeper (e.g. team/scripts/dashboard/*.sh):
#   SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#   # shellcheck source=../lib/script_common.sh
#   source "${SCRIPT_DIR}/../lib/script_common.sh"
#
# FUNCTIONS
# ---------
# ansi_code <color>
#   Echoes the ANSI escape sequence for <color>, or an empty string when
#   colour output is disabled or <color> is not recognized. Reads the
#   caller's USE_COLOR variable ("true" enables colour; anything else,
#   including unset, disables it) — callers own the USE_COLOR/NO_COLOR/
#   TERM=dumb detection and set USE_COLOR before calling this. Supported
#   names: black, red, green, yellow, blue, magenta, cyan, white, bold,
#   dim, reset, plus three legacy compound/variant codes carried over
#   byte-for-byte from pre-existing renderer call sites rather than
#   normalized away: gray_bg (gray/white background + black foreground),
#   bold_cyan ("\033[1;36m", a single combined escape distinct from
#   bold+cyan issued as two separate codes), and yellow_bright /
#   green_bright ("\033[33m" / "\033[32m", the no-"0;"-prefix SGR variants
#   of yellow/green). Callers capture the result via command substitution,
#   e.g. RED="$(ansi_code red)" — the substitution strips the trailing
#   newline `echo` adds, so callers never see it.
#
# cleanup_on_exit
#   Standard EXIT-trap body shared by scripts that spawn background jobs:
#   reaps any jobs still running and re-raises the script's own exit
#   code. Callers install it themselves after sourcing this file —
#   `trap cleanup_on_exit EXIT` (and, where a script also wants to reap on
#   an operator-sent signal, `trap cleanup_on_exit SIGTERM SIGINT SIGHUP`).
#   A script whose cleanup needs are not the plain "kill jobs, wait, exit"
#   shape (a rollback sequence, a child-PID-specific wait) defines its own
#   trap body instead of using this one — this is the shared case, not the
#   only legal one.
#
#   Order of work, every time it fires: (1) stop the process group named by
#   CLEANUP_ON_EXIT_PROCESS_GROUP, (2) the pre-hook, (3) kill the remaining
#   jobs and wait, (4) exit. A process group comes first because a pre-hook
#   may release something (a lock) that a still-running group member is
#   relying on: releasing first lets a second process take it while the
#   first group's members keep working.
#
#   CLEANUP_ON_EXIT_PROCESS_GROUP (optional): a process-group id the caller
#   launched (for example a `set -m` background job, whose pgid is its own
#   pid). When set and the group still has a live member, cleanup_on_exit
#   calls cleanup_stop_process_group on it before anything else. `jobs -p`
#   alone cannot do this: it names only the job's leader, and a leader that
#   ignores or traps SIGTERM shields every other member of its group. The
#   caller clears the variable once the group is known to be empty, so a
#   recycled pgid is never signalled. Unset by default.
#
#   CLEANUP_ON_EXIT_PROCESS_GROUP_GRACE (optional): seconds to wait after
#   SIGTERM before SIGKILL (and again after SIGKILL for the kernel to finish
#   the teardown). Defaults to KILL_GRACE_SECONDS, then 30.
#
# cleanup_stop_process_group <pgid> [grace_seconds]
#   Stops every member of process group <pgid>: SIGTERM to the group, poll
#   the group about once a second until it is empty or <grace_seconds>
#   (default 30) have passed, then SIGKILL the group and poll once more for
#   up to <grace_seconds>. Narrates each step on stderr. Returns 0 when the
#   group is empty, 1 when members remain after SIGKILL (a process in kernel
#   teardown runs no further user code), 2 on a pgid that is not a positive
#   integer (nothing is signalled — zero and negative ids name the caller's
#   own group or every process). A no-op returning 0 when the group is
#   already empty.
#
#   CLEANUP_ON_EXIT_PRE_HOOK (optional): when a caller sets this variable to
#   the name of a function defined in its own scope, cleanup_on_exit calls
#   that function (passing the captured exit code as $1) before reaping jobs
#   — a failure in the hook is swallowed (`|| true`) so it never masks the
#   script's real exit code. This is how a script guarantees some obligation
#   (e.g. flushing an accumulated findings file) runs on EVERY exit path —
#   a normal fall-through, an early `exit N`, or a `set -e` abort — without
#   duplicating the write call at each exit site. Unset by default, so every
#   existing caller of cleanup_on_exit is unaffected until it opts in.
#
# print_usage_and_exit <exit_code> <usage_text>
#   Prints <usage_text> to stdout when <exit_code> is 0 (the --help path)
#   or to stderr otherwise (the usage-error path), then exits with
#   <exit_code>. The usage text itself — every flag, every example — stays
#   authored in the calling script; this function only carries the
#   print-destination-by-exit-code convention and the exit call, so that
#   convention lives in one place instead of being re-decided per script.

ansi_code() {
  local color="$1"
  if [[ "${USE_COLOR:-}" != "true" ]]; then echo ""; return; fi
  case "$color" in
    black)   echo $'\033[0;30m' ;;
    red)     echo $'\033[0;31m' ;;
    green)   echo $'\033[0;32m' ;;
    yellow)  echo $'\033[0;33m' ;;
    blue)    echo $'\033[0;34m' ;;
    magenta) echo $'\033[0;35m' ;;
    cyan)    echo $'\033[0;36m' ;;
    white)   echo $'\033[0;37m' ;;
    bold)    echo $'\033[1m' ;;
    dim)     echo $'\033[2m' ;;
    reset)   echo $'\033[0m' ;;
    gray_bg) echo $'\033[47;30m' ;;
    bold_cyan)     echo $'\033[1;36m' ;;
    yellow_bright) echo $'\033[33m' ;;
    green_bright)  echo $'\033[32m' ;;
    *)       echo "" ;;
  esac
}

cleanup_stop_process_group() {
  local pgid="${1:-}" grace="${2:-30}" deadline
  [[ "$pgid" =~ ^[1-9][0-9]*$ ]] || return 2
  [[ "$grace" =~ ^[0-9]+$ ]] || grace=30
  kill -0 -- "-${pgid}" 2>/dev/null || return 0

  echo "cleanup_on_exit: stopping process group ${pgid} before exit hooks run: SIGTERM, then SIGKILL after ${grace}s if any member remains" >&2
  kill -TERM -- "-${pgid}" 2>/dev/null || true
  deadline=$(( $(date +%s) + grace ))
  while kill -0 -- "-${pgid}" 2>/dev/null && (( $(date +%s) < deadline )); do
    sleep 1
  done
  kill -0 -- "-${pgid}" 2>/dev/null || return 0

  echo "cleanup_on_exit: process group ${pgid} still has members ${grace}s after SIGTERM; sending SIGKILL to the group" >&2
  kill -KILL -- "-${pgid}" 2>/dev/null || true
  deadline=$(( $(date +%s) + grace ))
  while kill -0 -- "-${pgid}" 2>/dev/null && (( $(date +%s) < deadline )); do
    sleep 1
  done
  kill -0 -- "-${pgid}" 2>/dev/null || return 0
  echo "cleanup_on_exit: process group ${pgid} still has members ${grace}s after SIGKILL; they are in kernel teardown and run no further code" >&2
  return 1
}

cleanup_on_exit() {
  local exit_code=$?
  if [[ -n "${CLEANUP_ON_EXIT_PROCESS_GROUP:-}" ]]; then
    cleanup_stop_process_group "$CLEANUP_ON_EXIT_PROCESS_GROUP" \
      "${CLEANUP_ON_EXIT_PROCESS_GROUP_GRACE:-${KILL_GRACE_SECONDS:-30}}" || true
    # Stopped (or past SIGKILL): a signal trap's own `exit` re-enters this
    # function through the EXIT trap, which must not wait on it twice.
    CLEANUP_ON_EXIT_PROCESS_GROUP=""
  fi
  if [[ -n "${CLEANUP_ON_EXIT_PRE_HOOK:-}" ]]; then
    "$CLEANUP_ON_EXIT_PRE_HOOK" "$exit_code" || true
  fi
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}

print_usage_and_exit() {
  local exit_code="$1"
  local usage_text="$2"
  if [[ "$exit_code" -eq 0 ]]; then
    printf '%s\n' "$usage_text"
  else
    printf '%s\n' "$usage_text" >&2
  fi
  exit "$exit_code"
}
