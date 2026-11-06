#!/usr/bin/env bash
# team/scripts/lib/script_common.sh
# Single home for the small pieces of boilerplate nearly every
# team/scripts/ entry point re-implements on its own: ANSI colour
# rendering, the colour table and coloured status-line logger, the
# EXIT-trap cleanup body, and a generic usage-text emitter.
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
# script_colour_table [tty|on|off]
#   Sets the shared colour table as globals: C_RED, C_RED_BOLD, C_GREEN,
#   C_YELLOW, C_BLUE, C_CYAN, C_BOLD, C_DIM and C_RESET, each to the escape
#   ansi_code gives that name (C_RED_BOLD is "\033[1;31m"), or to "" when
#   colour is off. tty (the default) turns colour on only when stdout is a
#   terminal; on/true and off/false force it. A caller with its own rule
#   (NO_COLOR, TERM=dumb) decides and passes on or off. Leaves the caller's
#   USE_COLOR untouched.
#
# script_log_init <tag> [--labelled]
#   Readies the status-line logger below: sets the colour table for stdout
#   (script_colour_table tty) and the tag every line carries. --labelled
#   adds "WARNING: " to warn lines and "ERROR: " to err lines after the
#   tag. An empty <tag> selects the bare form (see below).
#
# info / ok / warn / err <message...>
#   Print one status line: the tag in brackets, coloured blue (info),
#   green (ok), yellow (warn) or red (err), then a space and the message —
#   "[install] copying files". err writes to stderr; the rest to stdout.
#   With no tag set, the coloured head is the level word followed by a
#   colon instead: "INFO: ", "OK: ", "WARN: ", "ERROR: ". All return 0.
#
# die <message...>
#   err <message...>, then exit 1.
#
# log_line <colour> <head> <tail>
#   Prints <colour><head>, the colour reset, then <tail> verbatim, on
#   stdout. The primitive under the five above, for a script whose status
#   line has its own head ("[verify-install-policy] PASS").
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
#   CLEANUP_ON_EXIT_PROCESS_GROUP, then the agent run named by
#   CLEANUP_ON_EXIT_AGENT_RUN_ID, (2) the pre-hook, (3) kill the remaining
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
#   CLEANUP_ON_EXIT_AGENT_RUN_ID / CLEANUP_ON_EXIT_AGENT_RUN_LOG (optional):
#   an agent run still in progress and its task log file, set by
#   agent_run_begin (agent_run_reap.sh). When the run id is set and
#   agent_run_reap is defined, cleanup_on_exit stops every process carrying
#   that run id right after the process group -- TERM, the same grace, KILL
#   -- and logs each one, so a run's processes outside the group do not
#   outlive a wake stopped mid-run.
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

script_colour_table() {
  # Shadows the caller's USE_COLOR for the ansi_code calls below only.
  local USE_COLOR=false
  case "${1:-tty}" in
    on|true) USE_COLOR=true ;;
    tty) if [[ -t 1 ]]; then USE_COLOR=true; fi ;;
  esac
  C_RED="$(ansi_code red)"
  C_GREEN="$(ansi_code green)"
  C_YELLOW="$(ansi_code yellow)"
  C_BLUE="$(ansi_code blue)"
  C_CYAN="$(ansi_code cyan)"
  C_BOLD="$(ansi_code bold)"
  C_DIM="$(ansi_code dim)"
  C_RESET="$(ansi_code reset)"
  C_RED_BOLD=""
  if [[ "$USE_COLOR" == "true" ]]; then C_RED_BOLD=$'\033[1;31m'; fi
}

script_log_init() {
  SCRIPT_LOG_TAG="${1:-}"
  SCRIPT_LOG_WARN_LABEL=""
  SCRIPT_LOG_ERR_LABEL=""
  if [[ "${2:-}" == "--labelled" ]]; then
    SCRIPT_LOG_WARN_LABEL="WARNING: "
    SCRIPT_LOG_ERR_LABEL="ERROR: "
  fi
  script_colour_table tty
}

log_line() {
  printf '%s%s%s%s\n' "${1:-}" "${2:-}" "${C_RESET:-}" "${3:-}"
}

# _script_log <colour> <level-word> <label> <message...>: the tagged or
# bare status line info/ok/warn/err print.
_script_log() {
  local colour="$1" word="$2" label="$3"
  shift 3
  if [[ -n "${SCRIPT_LOG_TAG:-}" ]]; then
    log_line "$colour" "[${SCRIPT_LOG_TAG}]" " ${label}$*"
  else
    log_line "$colour" "$word" ": $*"
  fi
}

info() { _script_log "${C_BLUE:-}" INFO "" "$@"; }
ok()   { _script_log "${C_GREEN:-}" OK "" "$@"; }
warn() { _script_log "${C_YELLOW:-}" WARN "${SCRIPT_LOG_WARN_LABEL:-}" "$@"; }
err()  { _script_log "${C_RED:-}" ERROR "${SCRIPT_LOG_ERR_LABEL:-}" "$@" >&2; }
die()  { err "$@"; exit 1; }

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
  if [[ -n "${CLEANUP_ON_EXIT_AGENT_RUN_ID:-}" ]] && declare -F agent_run_reap >/dev/null 2>&1; then
    agent_run_reap "$CLEANUP_ON_EXIT_AGENT_RUN_ID" \
      "${CLEANUP_ON_EXIT_PROCESS_GROUP_GRACE:-${KILL_GRACE_SECONDS:-30}}" \
      "${CLEANUP_ON_EXIT_AGENT_RUN_LOG:-}" || true
    CLEANUP_ON_EXIT_AGENT_RUN_ID=""
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
