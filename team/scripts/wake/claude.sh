#!/usr/bin/env bash
# team/scripts/wake/claude.sh
# Process multiple tasks from a per-agent backlog queue in a loop. Designed for cron.
#
# Usage:
#   wake/claude.sh --agent=<agent> [--sleep=N] [--max-tasks=N] [--help]
#   wake/claude.sh -a <agent> [-s N] [-h]
#   wake/claude.sh <agent>    (deprecated positional form; emits a warning)
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
# <agent> must be one of: pm, coder, writer, tester, cm, bug, po, overwatch
#
# Loops until one of these stop conditions is met:
#   - the backlog has no more BACKLOG-state tasks
#   - MAX_TASKS_PER_WAKE tasks have been completed this run
#   - MAX_RUNTIME_SECONDS of walltime has elapsed
#   - the current task ended in BLOCKED state and STOP_ON_BLOCKED=true
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
# At the end of the run, this script re-checks every WAITING task in the
# current agent's queue and promotes satisfied ones back to BACKLOG.
#
# Tunables (override via env or kanban-root/env file):
#   MAX_TASKS_PER_WAKE     default 5
#   MAX_RUNTIME_SECONDS    default 14400 (4 hours)
#   PAUSE_BETWEEN_TASKS    default 5 (seconds)
#   STOP_ON_BLOCKED        default true
#   STOP_FILE              default <PGAI_AGENT_KANBAN_TEMP_DIR>/wakeup/stop
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH — kanban root (default: $HOME/pgai_agent_kanban)
#
# Optional sourced files (in kanban root): bashrc (personal shell config),
# env (wake script tunables).
#
# Architecture: this script is a thin driver. The provider-independent wake
# pipeline (process_one_task, run_project_chain) lives in lib/wake_driver.sh
# and its sibling stage files; the four Claude-specific concerns (dispatch,
# env, model preflight, output parse) are reached only through the provider
# interface implemented by lib/provider_claude.sh (see that file's header
# for the documented contract).

VALID_AGENTS="pm coder writer tester cm bug po overwatch"

# --- Argument parsing ---
# Named-parameter style (--agent, --sleep, --help) with deprecated positional fallback.
# Done before kanban root resolution so we can print usage cleanly.
# shellcheck source=../lib/script_common.sh
source "$(dirname "${BASH_SOURCE[0]:-$0}")/../lib/script_common.sh"

# Usage text as data (per lib/script_common.sh's documented convention).
# This script's usage intentionally prints to stderr on every path (--help's
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# --- Source provider lib (Claude's five-function provider interface) ---
# Must be sourced BEFORE wake_driver.sh so provider_dispatch,
# provider_parse_output, and provider_model_preflight are defined when
# wake_driver.sh's functions reference them. See lib/provider_claude.sh for
# the documented interface contract.
# shellcheck source=scripts/lib/provider_claude.sh
source "${SCRIPT_DIR}/../lib/provider_claude.sh"

# --- Preflight: verify Claude CLI is available ---
provider_env_preflight

# --- Source common substrate lib ---
# wake_common.sh sources all other required libs (project_paths, projects, etc.),
# reads config files, sets tunables, defines all substrate functions, and
# exposes wake_common_preflight and wake_common_run.
# shellcheck source=scripts/lib/wake_common.sh
source "${SCRIPT_DIR}/../lib/wake_common.sh"

# --- Source worktree lifecycle helpers ---
# Provides create_task_worktree and teardown_task_worktree for CODER/WRITER
# git-workflow tasks dispatched into isolated worktrees.
# worktree.sh depends on temp.sh; provider_claude.sh already sources
# temp.sh so the include guard inside worktree.sh makes this idempotent.
# shellcheck source=scripts/lib/worktree.sh
source "${SCRIPT_DIR}/../lib/worktree.sh"

# --- Source wake-bracket shared lib ---
# Provides wake_bracket_compute_temp_subtree, wake_bracket_pre_dispatch, and
# wake_bracket_post_session — shared across both wake provider siblings so
# the bracket functions live in exactly one place.
# shellcheck source=scripts/lib/wake_bracket.sh
source "${SCRIPT_DIR}/../lib/wake_bracket.sh"

# --- Source the shared, provider-neutral wake pipeline ---
# Defines process_one_task and run_project_chain (and their four stage-file
# helpers), reaching the provider interface sourced above for the four
# provider-specific concerns. See lib/wake_driver.sh's header for the full
# sourcing contract.
# shellcheck source=scripts/lib/wake_driver.sh
source "${SCRIPT_DIR}/../lib/wake_driver.sh"

# --- Read active provider and exit immediately if not claude ---
# Done after wake_common.sh is sourced so TEAM_ROOT, PGAI_*,
# and log() are all available.
# Source active_provider.sh to ensure read_active_provider is defined.
# shellcheck source=scripts/lib/active_provider.sh
source "${SCRIPT_DIR}/../lib/active_provider.sh"
ACTIVE_PROVIDER="$(read_active_provider "$TEAM_ROOT" 2>/dev/null || echo "claude")"
if [[ "$ACTIVE_PROVIDER" != "claude" ]]; then
  # Not the active provider — exit cleanly in < 1 second.
  # Use a direct echo since log() writes to LOG_FILE which may not be flushed.
  echo "[$(date -Iseconds)] wake-claude(${AGENT}): active-provider is '${ACTIVE_PROVIDER}', not 'claude' — exiting immediately" >&2
  exit 0
fi

# --- Agent-aware HALT flag check (startup, before entering the main loop) ---
# OVERWATCH checks HALT_OVERWATCH; all other agents check HALT.
# wake_common_run also performs this check; this early check provides
# defense-in-depth and allows source-analysis tests to verify the routing.
if [[ "$AGENT" == "overwatch" ]]; then
  if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
    echo "[$(date -Iseconds)] wake-claude(${AGENT}): HALT_OVERWATCH flag detected — exiting cleanly" >&2
    exit 0
  fi
else
  if [[ -f "${TEAM_ROOT}/HALT" ]]; then
    echo "[$(date -Iseconds)] wake-claude(${AGENT}): HALT flag detected — exiting cleanly" >&2
    exit 0
  fi
fi

# --- AI_AUTH_MODE log and sanity check ---
# AI_AUTH_MODE is exported by wake_common.sh from kanban.cfg [providers] ai_auth_mode
# (default: oauth) BEFORE secrets is sourced. Log the active value so operators can
# confirm which mode is in effect from cron logs.
log "auth mode: ${AI_AUTH_MODE:-oauth}"

# Sanity check: if ai_auth_mode=oauth but ANTHROPIC_API_KEY is still set after
# sourcing secrets, the operator likely left an API key export in their secrets
# file while intending OAuth. The Claude CLI prefers ANTHROPIC_API_KEY over OAuth
# credentials whenever the env var is set, so this silently bypasses OAuth mode.
# Log a WARNING so the operator can investigate; do not abort.
if [[ "${AI_AUTH_MODE:-oauth}" == "oauth" && -n "${ANTHROPIC_API_KEY:-}" ]]; then
    log "WARNING: AI_AUTH_MODE=oauth but ANTHROPIC_API_KEY is set and non-empty. If you intend to use OAuth, remove or comment out ANTHROPIC_API_KEY from your secrets file to avoid credential conflicts."
fi

# --- Provider CLI version + effective model identity log ---
# Runs once per firing, on EVERY firing (including firings that find no work
# and exit immediately) — this is the only place a future provider-behaviour
# shift (a new wait ceiling, a new refusal, a new validation) can be
# correlated back to the exact CLI build and model that was running when it
# started. Never a gate: the version query is bounded by `timeout` and
# always falls back to an explicit unknown marker instead of aborting the
# wake — a missing binary, a timed-out query, and unparseable output all
# land on the same fallback. Identical block in wake/codex.sh (same field
# names: "provider CLI version:" / "model:") so one grep pattern matches
# both providers.
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
