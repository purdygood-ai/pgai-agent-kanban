#!/usr/bin/env bash
# team/scripts/lib/wake_entry.sh
# The provider-neutral body of every wake entry point: argument loop, root
# resolution, active-provider fast exit, HALT routing, the per-firing header
# log and the hand-off to the shared wake driver. Sourced (never executed)
# by the provider shims wake/claude.sh and wake/codex.sh; it runs as it is
# sourced and ends the process through wake_common_run or an early exit.
#
# Contract with the sourcing shim, which sets strict mode (set -euo
# pipefail) on its own first executable line and then sets, before sourcing:
#   WAKE_ENTRY_PROVIDER      provider name kanban.cfg [providers] active
#                            selects (claude, codex); also names the log
#                            prefix "wake-<provider>(<agent>)".
#   WAKE_ENTRY_PROVIDER_LIB  basename of the provider-interface library
#                            under lib/ (provider_claude.sh,
#                            provider_codex.sh); see that file's header for
#                            the five-function provider interface.
#   WAKE_ENTRY_API_KEY_VAR   the provider's API-key environment variable
#                            (ANTHROPIC_API_KEY, OPENAI_API_KEY); with
#                            ai_auth_mode=oauth, a non-empty value is logged
#                            as a WARNING because the CLI may prefer it over
#                            OAuth credentials.
#
# Usage (of the shim):
#   wake/<provider>.sh --agent=<agent> [--sleep=N] [--max-tasks=N] [--help]
#   wake/<provider>.sh -a <agent> [-s N] [-h]
#   wake/<provider>.sh <agent>    (deprecated positional form; emits a warning)
#
# Options:
#   --agent=AGENT / -a AGENT  Agent to run (required).
#                             Must be one of: pm, coder, writer, tester, cm, bug, po, overwatch
#   --sleep=N / -s N          Sleep N seconds before doing real work (default: 0).
#                             Useful for sub-minute cron stagger. N must be >= 0.
#                             Values >= 60 emit a warning but still run.
#   --max-tasks=N             Override MAX_TASKS_PER_WAKE for this invocation.
#   --help / -h               Print this usage and exit 0.
#
# Exits immediately (< 1 second, status 0) when kanban.cfg [providers]
# active names another provider -- before the provider library is sourced
# or its CLI probed -- so cron can schedule every provider's entry point
# and the inactive ones add no overhead.
#
# Otherwise loops until one of these stop conditions is met:
#   - the backlog has no more BACKLOG-state tasks
#   - MAX_TASKS_PER_WAKE tasks have been completed this run
#   - MAX_RUNTIME_SECONDS of walltime has elapsed
#   - the current task ended in BLOCKED state and STOP_ON_BLOCKED=true
#     (default false: a BLOCKED task parks only itself and its dependants;
#     the batch moves on to the next independent task)
#     (note: WAITING does NOT count as blocked — it's a soft auto-resolved state)
#   - STOP_FILE exists (operator stop file, shared by every agent's batch;
#     stays on disk until the operator removes it)
#   - halt flag present (see "Agent-aware HALT routing" below)
#
# Agent-aware HALT routing: ${TEAM_ROOT}/HALT stops all chain agents (NOT
# OVERWATCH); ${TEAM_ROOT}/HALT_OVERWATCH stops OVERWATCH only. Routing keys
# on $AGENT (overwatch checks HALT_OVERWATCH and ignores HALT; every other
# agent does the reverse) so touching one flag never stops the other lane.
#
# At the end of the run, the driver re-checks every WAITING task in the
# current agent's queue and promotes satisfied ones back to BACKLOG.
#
# Tunables (override via env or kanban-root/env file):
#   MAX_TASKS_PER_WAKE     default 5
#   MAX_RUNTIME_SECONDS    default 14400 (4 hours)
#   PAUSE_BETWEEN_TASKS    default 5 (seconds)
#   STOP_ON_BLOCKED        default false (true: a BLOCKED task ends the project's batch)
#   STOP_FILE              default <PGAI_AGENT_KANBAN_TEMP_DIR>/wakeup/stop
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH — kanban root (default: $HOME/pgai_agent_kanban)
#
# Optional sourced files (in kanban root): bashrc (personal shell config),
# env (wake script tunables).
#
# Architecture: the provider-independent wake pipeline (process_one_task,
# run_project_chain) lives in lib/wake_driver.sh and its sibling stage
# files; the four provider-specific concerns (dispatch, env, model
# preflight, output parse) are reached only through the provider interface
# implemented by the library WAKE_ENTRY_PROVIDER_LIB names.
#
# The bootstrap lifts strict mode while it sources operator-authored files
# and turns it back on at its one documented point (lib/wake_bootstrap.sh).

: "${WAKE_ENTRY_PROVIDER:?lib/wake_entry.sh: the sourcing wake shim must set WAKE_ENTRY_PROVIDER (e.g. claude) before sourcing}"
: "${WAKE_ENTRY_PROVIDER_LIB:?lib/wake_entry.sh: the sourcing wake shim must set WAKE_ENTRY_PROVIDER_LIB (e.g. provider_claude.sh) before sourcing}"
: "${WAKE_ENTRY_API_KEY_VAR:?lib/wake_entry.sh: the sourcing wake shim must set WAKE_ENTRY_API_KEY_VAR (e.g. ANTHROPIC_API_KEY) before sourcing}"

VALID_AGENTS="pm coder writer tester cm bug po overwatch"

# --- Argument parsing ---
# Named-parameter style (--agent, --sleep, --help) with deprecated positional fallback.
# Done before kanban root resolution so we can print usage cleanly.
# shellcheck source=script_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/script_common.sh"

# Usage text as data (per lib/script_common.sh's documented convention).
# The wake usage intentionally prints to stderr on every path (--help's
# exit 0 included), so the destination is pinned here rather than delegated
# to print_usage_and_exit (which routes exit 0 to stdout).
_USAGE_TEXT="$(cat <<USAGE
Usage: $(basename "$0") --agent=AGENT [--sleep=N] [--max-tasks=N] [--help]
       $(basename "$0") -a AGENT [-s N] [-h]
       $(basename "$0") AGENT        (deprecated positional form)

Options:
  --agent=AGENT / -a AGENT  Agent to run (required).
                            Valid agents: $VALID_AGENTS
  --sleep=N / -s N          Sleep N seconds before doing real work (default: 0).
                            Useful for sub-minute cron stagger.
  --max-tasks=N             Override MAX_TASKS_PER_WAKE for this invocation.
  --help / -h               Print this usage and exit 0.
USAGE
)"

AGENT=""
SLEEP=0
_CLI_MAX_TASKS=""

# Detect deprecated positional invocation: single non-flag arg as first arg.
if [[ $# -gt 0 && "${1:-}" != -* ]]; then
  AGENT="$1"
  shift
  echo "WARNING: positional agent argument is deprecated." >&2
  echo "  Use: $(basename "$0") --agent=${AGENT}" >&2
  echo "  Continuing with agent='${AGENT}' for backward compatibility." >&2
fi

# Parse remaining options
while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      printf '%s\n' "$_USAGE_TEXT" >&2
      exit 0
      ;;
    --agent=*)
      AGENT="${1#--agent=}"
      shift
      ;;
    --agent|-a)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --agent requires an argument" >&2
        printf '%s\n' "$_USAGE_TEXT" >&2
        exit 1
      fi
      AGENT="$2"
      shift 2
      ;;
    --sleep=*)
      SLEEP="${1#--sleep=}"
      shift
      ;;
    --sleep|-s)
      if [[ $# -ge 2 ]]; then
        SLEEP="${2:-}"
        shift 2
      else
        SLEEP=""
        shift
      fi
      ;;
    --max-tasks)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --max-tasks requires an integer argument" >&2
        exit 1
      fi
      _CLI_MAX_TASKS="$2"
      shift 2
      ;;
    --max-tasks=*)
      _CLI_MAX_TASKS="${1#--max-tasks=}"
      shift
      ;;
    *)
      echo "ERROR: unknown option: $1" >&2
      echo "" >&2
      printf '%s\n' "$_USAGE_TEXT" >&2
      exit 1
      ;;
  esac
done

# --- Validate --agent ---
if [[ -z "$AGENT" ]]; then
  echo "ERROR: --agent is required" >&2
  echo "" >&2
  printf '%s\n' "$_USAGE_TEXT" >&2
  exit 1
fi

AGENT_VALID=false
for a in $VALID_AGENTS; do
  if [[ "$AGENT" == "$a" ]]; then
    AGENT_VALID=true
    break
  fi
done

if [[ "$AGENT_VALID" != "true" ]]; then
  echo "ERROR: unknown agent '${AGENT}'" >&2
  echo "" >&2
  echo "Valid agents: $VALID_AGENTS" >&2
  exit 1
fi

# --- Validate --sleep ---
if [[ -z "$SLEEP" ]]; then
  SLEEP=0
fi

if ! [[ "$SLEEP" =~ ^[0-9]+$ ]]; then
  echo "ERROR: --sleep value must be a non-negative integer; got '${SLEEP}'" >&2
  exit 1
fi

if [[ "$SLEEP" -lt 0 ]]; then
  echo "ERROR: --sleep value must be >= 0; got '${SLEEP}'" >&2
  exit 1
fi

if [[ "$SLEEP" -ge 60 ]]; then
  echo "WARNING: --sleep=${SLEEP} is >= 60 seconds; this is likely an operator error but will proceed." >&2
fi

# --- Resolve kanban root ---
# Canonical var; falls back to default install path if unset.
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}"

if [[ ! -d "$TEAM_ROOT" ]]; then
  echo "ERROR: kanban root not found: $TEAM_ROOT" >&2
  echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
  exit 1
fi

# --- Resolve script directory ---
# The wake/ directory of the shim that sourced this file: every stage below,
# and wake_bootstrap.sh's own sourcing, resolves lib/ as ${SCRIPT_DIR}/../lib.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[1]:-$0}")" && pwd)"

# --- Source active_provider.sh for read_active_provider ---
# Must be sourced before the provider check below and before wake_common.sh.
# shellcheck source=active_provider.sh
source "${SCRIPT_DIR}/../lib/active_provider.sh"

# --- Read active provider and exit immediately if not this shim's ---
# Done before provider_env_preflight and wake_common.sh to guarantee < 1
# second exit when this wake script is not the active provider.
ACTIVE_PROVIDER="$(read_active_provider "$TEAM_ROOT" 2>/dev/null || echo "claude")"
if [[ "$ACTIVE_PROVIDER" != "$WAKE_ENTRY_PROVIDER" ]]; then
  # Not the active provider — exit cleanly in < 1 second.
  # Use a direct echo since log() is not defined until wake_common.sh is sourced.
  echo "[$(date -Iseconds)] wake-${WAKE_ENTRY_PROVIDER}(${AGENT}): active-provider is '${ACTIVE_PROVIDER}', not '${WAKE_ENTRY_PROVIDER}' — exiting immediately" >&2
  exit 0
fi

# --- Source provider lib (the five-function provider interface) ---
# Must be sourced AFTER the active-provider check (to avoid loading
# provider-specific state when this script exits early) and BEFORE
# wake_driver.sh (so provider_dispatch, provider_parse_output, and
# provider_model_preflight are defined when wake_driver.sh's functions
# reference them). See the provider library's header for the documented
# interface contract.
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/../lib/${WAKE_ENTRY_PROVIDER_LIB}"

# --- Preflight: verify the provider CLI is available ---
provider_env_preflight

# --- Source common substrate lib ---
# wake_common.sh sources all other required libs (project_paths, projects, etc.),
# reads config files, sets tunables, defines all substrate functions, and
# exposes wake_common_preflight and wake_common_run.
# shellcheck source=wake_common.sh
source "${SCRIPT_DIR}/../lib/wake_common.sh"

# --- Source worktree lifecycle helpers ---
# Provides create_task_worktree and teardown_task_worktree for CODER/WRITER
# git-workflow tasks dispatched into isolated worktrees.
# worktree.sh depends on temp.sh; every provider library already sources
# temp.sh so the include guard inside worktree.sh makes this idempotent.
# shellcheck source=worktree.sh
source "${SCRIPT_DIR}/../lib/worktree.sh"

# --- Source wake-bracket shared lib ---
# Provides wake_bracket_compute_temp_subtree, wake_bracket_pre_dispatch, and
# wake_bracket_post_session — shared across every wake provider so the
# bracket functions live in exactly one place.
# shellcheck source=wake_bracket.sh
source "${SCRIPT_DIR}/../lib/wake_bracket.sh"

# --- Source the shared, provider-neutral wake pipeline ---
# Defines process_one_task and run_project_chain (and their four stage-file
# helpers), reaching the provider interface sourced above for the four
# provider-specific concerns. See lib/wake_driver.sh's header for the full
# sourcing contract.
# shellcheck source=wake_driver.sh
source "${SCRIPT_DIR}/../lib/wake_driver.sh"

# --- Agent-aware HALT flag check (startup, before entering the main loop) ---
# OVERWATCH checks HALT_OVERWATCH; all other agents check HALT.
# wake_common_run also performs this check; this early check provides
# defense-in-depth and allows source-analysis tests to verify the routing.
if [[ "$AGENT" == "overwatch" ]]; then
  if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
    echo "[$(date -Iseconds)] wake-${WAKE_ENTRY_PROVIDER}(${AGENT}): HALT_OVERWATCH flag detected — exiting cleanly" >&2
    exit 0
  fi
else
  if [[ -f "${TEAM_ROOT}/HALT" ]]; then
    echo "[$(date -Iseconds)] wake-${WAKE_ENTRY_PROVIDER}(${AGENT}): HALT flag detected — exiting cleanly" >&2
    exit 0
  fi
fi

# --- AI_AUTH_MODE log and sanity check ---
# AI_AUTH_MODE is exported by wake_common.sh from kanban.cfg [providers] ai_auth_mode
# (default: oauth) BEFORE secrets is sourced. Log the active value so operators can
# confirm which mode is in effect from cron logs.
log "auth mode: ${AI_AUTH_MODE:-oauth}"

# Sanity check: if ai_auth_mode=oauth but the provider's API key variable is
# still set after sourcing secrets, the operator likely left an API key export
# in their secrets file while intending OAuth. The provider CLI may prefer the
# API key over OAuth credentials whenever the env var is set, which silently
# bypasses OAuth mode. Log a WARNING so the operator can investigate; do not
# abort.
if [[ "${AI_AUTH_MODE:-oauth}" == "oauth" && -n "${!WAKE_ENTRY_API_KEY_VAR:-}" ]]; then
    log "WARNING: AI_AUTH_MODE=oauth but ${WAKE_ENTRY_API_KEY_VAR} is set and non-empty. If you intend to use OAuth, remove or comment out ${WAKE_ENTRY_API_KEY_VAR} from your secrets file to avoid credential conflicts."
fi

# --- Provider CLI version + effective model identity log ---
# Runs once per firing, on EVERY firing (including firings that find no work
# and exit immediately) — this is the only place a future provider-behaviour
# shift (a new wait ceiling, a new refusal, a new validation) can be
# correlated back to the exact CLI build and model that was running when it
# started. Never a gate: the version query is bounded by `timeout` and
# always falls back to an explicit unknown marker instead of aborting the
# wake — a missing binary, a timed-out query, and unparseable output all
# land on the same fallback. One block for every provider (same field
# names: "provider CLI version:" / "model:") so one grep pattern matches
# them all.
# PROVIDER-IDENTITY-LOG-BEGIN
set +e
_pil_cli_version="$(timeout 10 "$PROVIDER_CLI" --version 2>/dev/null | head -n1 | tr -d '\r')"
_pil_cli_version_exit=$?
set -e
if [[ $_pil_cli_version_exit -ne 0 || -z "${_pil_cli_version}" ]]; then
  _pil_cli_version="unknown (version query failed, timed out, or ${PROVIDER_CLI} not on PATH)"
fi
log "provider CLI version: ${_pil_cli_version}"

# Effective model at header time: the same PGAI_<ROLE>_MODEL tier the
# per-task model-selection block (wake_driver_prep.sh) resolves from, minus
# the task-level '## Model Override' tier — that tier is not knowable until
# a specific task README is read, which has not happened yet here. When a
# task does carry an override, provider_dispatch's own per-task "model:"
# narration line (logged at dispatch time) records the value actually used.
_pil_agent_upper="${AGENT^^}"
_pil_agent_model_var="PGAI_${_pil_agent_upper}_MODEL"
if [[ -n "${!_pil_agent_model_var:-}" ]]; then
  _pil_model="${!_pil_agent_model_var} (configured default for ${AGENT}; a task-level Model Override may replace this per task)"
else
  _pil_model="<provider CLI default> (no ${_pil_agent_model_var} configured; a task-level Model Override may replace this per task)"
fi
log "model: ${_pil_model}"
unset _pil_cli_version _pil_cli_version_exit _pil_agent_upper _pil_agent_model_var _pil_model
# PROVIDER-IDENTITY-LOG-END

# --- Preflight: substrate checks ---
wake_common_preflight

# --- Run ---
wake_common_run
