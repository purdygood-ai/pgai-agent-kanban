#!/usr/bin/env bash
# wake-claude.sh
# Process multiple tasks from a per-agent backlog queue in a loop. Designed for cron.
#
# Usage:
#   wake-claude.sh --agent=<agent> [--sleep=N] [--max-tasks=N] [--help]
#   wake-claude.sh -a <agent> [-s N] [-h]
#   wake-claude.sh <agent>    (deprecated positional form; emits a warning)
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
#   - STOP_FILE exists (graceful halt signal)
#   - halt flag present (see "Agent-aware HALT routing" below)
#
# Agent-aware HALT routing
# ========================
# The kanban supports two independent halt flags so the operator can stop
# OVERWATCH and the regular chain agents (coder, pm, writer, tester, cm, po, bug)
# independently of each other:
#
#   ${TEAM_ROOT}/HALT          — stops all chain agents (NOT OVERWATCH)
#   ${TEAM_ROOT}/HALT_OVERWATCH — stops OVERWATCH only (NOT chain agents)
#
# Routing is keyed on the $AGENT variable (set from argv[1]):
#   - agent == "overwatch"  → check HALT_OVERWATCH; ignore HALT
#   - all other agents     → check HALT; ignore HALT_OVERWATCH
#
# This ensures that touching HALT never inadvertently kills OVERWATCH, and
# touching HALT_OVERWATCH never inadvertently stops the normal work queue.
# Setting both flags halts everything. Setting neither lets everything run.
#
# At the end of the run (after the main loop exits), this script re-checks
# every WAITING task in the current agent's queue. If a WAITING task's
# prerequisites are now satisfied, it gets promoted back to BACKLOG. The
# next wake invocation will pick it up.
#
# Tunables (override via env or kanban-root/env file):
#   MAX_TASKS_PER_WAKE     default 5
#   MAX_RUNTIME_SECONDS    default 14400 (4 hours)
#   PAUSE_BETWEEN_TASKS    default 5 (seconds)
#   STOP_ON_BLOCKED        default true
#   STOP_FILE              default <PGAI_AGENT_KANBAN_TEMP_DIR>/wakeup/stop
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH         — kanban root (default: $HOME/pgai_agent_kanban)
#
# Optional sourced files (in kanban root):
#   bashrc  — personal shell config (PATH, OAuth tokens, Python venv, aliases)
#   env     — wake script tunables

VALID_AGENTS="pm coder writer tester cm bug po overwatch"

# --- Argument parsing ---
# Named-parameter style (--agent, --sleep, --help) with deprecated positional fallback.
# Done before kanban root resolution so we can print usage cleanly.

_print_usage() {
  cat >&2 <<USAGE
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
}

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
      _print_usage
      exit 0
      ;;
    --agent=*)
      AGENT="${1#--agent=}"
      shift
      ;;
    --agent|-a)
      if [[ -z "${2:-}" ]]; then
        echo "ERROR: --agent requires an argument" >&2
        _print_usage
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
      _print_usage
      exit 1
      ;;
  esac
done

# --- Validate --agent ---
if [[ -z "$AGENT" ]]; then
  echo "ERROR: --agent is required" >&2
  echo "" >&2
  _print_usage
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

# --- Source provider lib (Claude-specific: token capture, provider_invoke_agent) ---
# Must be sourced BEFORE wake_common.sh so provider_invoke_agent is defined
# when wake_common.sh's functions reference it.
# shellcheck source=scripts/lib/wake_claude_provider.sh
source "${SCRIPT_DIR}/../lib/wake_claude_provider.sh"

# --- Preflight: verify Claude CLI is available ---
provider_preflight

# --- Source common substrate lib ---
# wake_common.sh sources all other required libs (project_paths, projects, etc.),
# reads config files, sets tunables, defines all substrate functions, and
# exposes wake_common_preflight and wake_common_run.
# shellcheck source=scripts/lib/wake_common.sh
source "${SCRIPT_DIR}/../lib/wake_common.sh"

# --- Source worktree lifecycle helpers ---
# Provides create_task_worktree and teardown_task_worktree for CODER/WRITER
# git-workflow tasks dispatched into isolated worktrees.
# worktree.sh depends on temp.sh; wake_claude_provider.sh already sources
# temp.sh so the include guard inside worktree.sh makes this idempotent.
# shellcheck source=scripts/lib/worktree.sh
source "${SCRIPT_DIR}/../lib/worktree.sh"

# --- Source wake-bracket shared lib ---
# Provides wake_bracket_compute_temp_subtree, wake_bracket_pre_dispatch, and
# wake_bracket_post_session — shared across both wake provider siblings so
# the bracket functions live in exactly one place.
# shellcheck source=scripts/lib/wake_bracket.sh
source "${SCRIPT_DIR}/../lib/wake_bracket.sh"

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

# ---------------------------------------------------------------------------
# Claude-provider substrate function overrides.
#
# These definitions override the provider-neutral defaults in wake_common.sh
# with Claude-specific versions.  Because bash last-definition-wins, these
# take effect at runtime while wake_common.sh retains neutral defaults for
# use by future providers (e.g. wake-codex.sh).
#
# Keeping these definitions here also preserves source-analysis test
# compatibility: test_wake_hygiene_bundle.sh greps this file for the
# structural patterns it validates.
# ---------------------------------------------------------------------------

setup_project_directories() {
  local task_log_dir="$1"
  local task_artifact_dir="$2"
  if ! mkdir -p "$task_log_dir" "$task_artifact_dir"; then
    log "FATAL: cannot create task directories: ${task_log_dir} ${task_artifact_dir}"
    log "       Check filesystem permissions, disk space, and read-only mounts."
    return 1
  fi
}

# --- get_workflow_type_from_readme <readme_path> ---
# Extract "## Workflow Type" from a task README.
# Prints the value (release, feature, or document) to stdout.
# Defaults to "release" when the field is absent or unreadable.
get_workflow_type_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text()
except Exception:
    print("release")
    raise SystemExit(0)
m = re.search(r'^##\s+Workflow\s+Type[^\S\n]*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M | re.I)
if not m:
    print("release")
    raise SystemExit(0)
val = m.group(1).strip().lower()
if not val:
    print("release")
    raise SystemExit(0)
print(val)
PY
}

# --- get_source_branch_from_readme <readme_path> ---
# Extract "## Source Branch" from a task README.
# Prints the value to stdout; returns non-zero with no output if field is absent.
get_source_branch_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text()
except Exception:
    raise SystemExit(1)
m = re.search(r'^##\s+Source\s+Branch[^\S\n]*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M | re.I)
if not m:
    raise SystemExit(1)
val = m.group(1).strip()
if not val or val.lower() == 'none':
    raise SystemExit(1)
print(val)
PY
}

# --- Process exactly one task (Claude provider override) ---
# Calls provider_invoke_agent (defined by wake_claude_provider.sh) for CLI invocation.
LAST_TASK_FINAL_STATE=""
process_one_task() {
  local task_id task_dir task_readme task_status task_log_dir task_artifact_dir
  local current_state final_state claude_exit prompt role SUBAGENT
  local unsatisfied_prereqs prereq_exit

  LAST_TASK_FINAL_STATE=""

  # --- D4 pollution sweep: snapshot anchoring ---
  # Capture canonical dev tree path at function entry, BEFORE any per-task
  # PGAI_DEV_TREE_PATH override (TESTER tasks override it to the worktree).
  # Both the pre-task and post-task snapshots use this variable so the sweep
  # always watches the canonical tree regardless of in-flight overrides.
  # Resolve per-project dev tree first; fall back to global for
  # single-project / legacy installs where PP_dev_tree_path is unset.
  local _sweep_canonical_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

  local _debug_gate
  _debug_gate="$(_resolve_debug_gate "${_CURRENT_PROJECT:-}" "${AGENT}")"

  # Return-code contract:
  #   0  — task found and processed
  #   1  — queue is empty
  #   2  — HALT flag detected
  if [[ "$AGENT" == "overwatch" ]]; then
    if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
      log "HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH — stopping batch loop cleanly"
      return 2
    fi
  else
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — stopping batch loop cleanly"
      return 2
    fi
  fi

  task_id="$(get_first_pending_task || true)"
  if [[ -z "${task_id:-}" ]]; then
    log "no pending Claude task found in ${AGENT} queue"
    return 1
  fi

  task_dir="${TASKS_ROOT}/${task_id}"
  task_readme="${task_dir}/README.md"
  task_status="${task_dir}/status.md"
  task_log_dir="${task_dir}/logs"
  task_artifact_dir="${task_dir}/artifacts"

  if [[ ! -f "$task_readme" ]]; then
    log "task ${task_id} missing README.md; marking BLOCKED"
    ensure_status_file "$task_status"
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Blockers" "Task README.md is missing at ${task_readme}"
    set_block "$task_status" "Needs Human" "yes"
    normalize_status_file "$task_status"
    mark_backlog "$task_id" "B"
    LAST_TASK_FINAL_STATE="BLOCKED"
    return 0
  fi

  ensure_status_file "$task_status"
  setup_project_directories "$task_log_dir" "$task_artifact_dir" || return 1

  role="$(get_role_from_readme "$task_readme")"
  case "$role" in
    CODER)  SUBAGENT="coder" ;;
    WRITER) SUBAGENT="writer" ;;
    CM)     SUBAGENT="cm" ;;
    TESTER) SUBAGENT="tester" ;;
    PO)     SUBAGENT="po" ;;
    PM)     SUBAGENT="pm" ;;
    *)
      log "unknown role '$role' in $task_readme; defaulting to coder"
      SUBAGENT="coder"
      role="CODER"
      ;;
  esac

  # --- Per-agent debug log path (per-project) ---
  # Route to projects/<project>/logs/debug/<agent>.log to match the
  # wake_common.sh substrate (last-def-wins). Falls back to the global path
  # only if _CURRENT_PROJECT is somehow unset.
  # Writes are gated on _debug_gate; no writes when gate is false.
  local _proj_debug_root="${KANBAN_ROOT}/logs/debug"
  if [[ -n "${_CURRENT_PROJECT:-}" ]]; then
    _proj_debug_root="${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug"
  fi
  local task_debug_log
  case "$SUBAGENT" in
    pm)       task_debug_log="${_proj_debug_root}/pm.log" ;;
    po)       task_debug_log="${_proj_debug_root}/po.log" ;;
    coder)    task_debug_log="${_proj_debug_root}/coder.log" ;;
    writer)   task_debug_log="${_proj_debug_root}/writer.log" ;;
    tester)   task_debug_log="${_proj_debug_root}/tester.log" ;;
    cm)       task_debug_log="${_proj_debug_root}/cm.log" ;;
    overwatch) task_debug_log="${_proj_debug_root}/overwatch.log" ;;
    *)        task_debug_log="${_proj_debug_root}/${SUBAGENT}.log" ;;
  esac
  if [[ "${_debug_gate}" == "true" ]]; then
    mkdir -p "$(dirname "$task_debug_log")"
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${task_id}] wake: dispatching to ${SUBAGENT} (role=${role} project=${_CURRENT_PROJECT:-unknown})" >> "$task_debug_log"
  fi

  # --- Model Selection ---
  # Resolution order (config_loader applies tiers 1-3 before wake runs):
  #   1. PGAI_<ROLE>_MODEL from env (set by operator or prior export)
  #   2. [models.<provider>] <role> in kanban.cfg (per-role config)
  #   3. [models.<provider>] default in kanban.cfg (provider-wide fallback)
  #   4. Task-level ## Model Override in task README
  #   5. Provider CLI default (empty selected_model; warning logged below)
  local model_override selected_model model_source agent_upper agent_model_var
  agent_upper="${AGENT^^}"
  agent_model_var="PGAI_${agent_upper}_MODEL"

  if [[ -n "${!agent_model_var:-}" ]]; then
    selected_model="${!agent_model_var}"
    model_source="PGAI_${agent_upper}_MODEL env"
  else
    model_override="$(get_model_override_from_readme "$task_readme")"
    if [[ -n "${model_override:-}" ]]; then
      selected_model="$model_override"
      model_source="task force override"
    else
      log "no model configured (role unset and [models.${ACTIVE_PROVIDER}] default empty) - provider CLI default will be used"
      selected_model=""
      model_source="subagent default for ${SUBAGENT}"
    fi
  fi

  current_state="$(get_state "$task_status")"
  case "$current_state" in
    BACKLOG|WAITING|WORKING|BLOCKED|DONE|WONT-DO) ;;
    *)
      log "task ${task_id} has invalid state: ${current_state}"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Invalid task state detected: ${current_state}"
      set_block "$task_status" "Needs Human" "yes"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      return 0
      ;;
  esac

  if [[ "$current_state" != "BACKLOG" ]]; then
    log "task ${task_id} is not BACKLOG (${current_state}); normalizing marker and skipping"
    case "$current_state" in
      BLOCKED)      mark_backlog "$task_id" "B" ;;
      WAITING)      mark_backlog "$task_id" "W" ;;
      DONE|WONT-DO) mark_backlog "$task_id" "x" ;;
      WORKING)      mark_backlog "$task_id" "B"
                    log "task ${task_id} was stuck in WORKING; marked BLOCKED"
                    ;;
    esac
    LAST_TASK_FINAL_STATE="$current_state"
    return 0
  fi

  # --- Check prerequisites ---
  unsatisfied_prereqs=""
  prereq_exit=0
  set +e
  unsatisfied_prereqs=$(check_prerequisites "$task_readme")
  prereq_exit=$?
  set -e

  if [[ $prereq_exit -ne 0 ]]; then
    log "task ${task_id} has unsatisfied prerequisites; transitioning to WAITING"
    log "unsatisfied: $(echo "$unsatisfied_prereqs" | tr '\n' ' ')"
    set_state "$task_status" "WAITING"
    set_block "$task_status" "Blockers" "Waiting on prerequisites: $(echo "$unsatisfied_prereqs" | tr '\n' ' ')"
    set_block "$task_status" "Needs Human" "no"
    normalize_status_file "$task_status"
    mark_backlog "$task_id" "W"
    LAST_TASK_FINAL_STATE="WAITING"
    return 0
  fi

  # Build role-specific reminder
  role_reminder=""
  case "$SUBAGENT" in
    coder|writer)
      role_reminder="For git workflows, DONE means your feature branch is merged --no-ff into the local source branch and the feature branch is deleted. Committing alone is not done. CM is the only role that touches origin — never push, pull, or fetch."
      ;;
    tester)
      role_reminder="DONE means artifacts/report.md is written with a clean recommendation: PASS, SHIP-WITH-CONCERNS, or SHIP-WITH-SERIOUS-CONCERNS. BLOCKED is a state (verification could not complete (infrastructure failure) — NOT \"found a blocker\"), not a recommendation. If recommendation is SHIP-WITH-CONCERNS or SHIP-WITH-SERIOUS-CONCERNS, file each finding via Path C (autonomous follow-up filing — non-blocking; Path C-A: filed as BUG, Path C-B: filed as PRIORITY) under \${PGAI_AGENT_KANBAN_ROOT_PATH}/projects/<project_name>/{bugs,priority}/ per TESTER.md Step 10. CM applies the ship-policy matrix to your recommendation. MANDATORY DELIVERABLE OVERRIDE: Writing artifacts/report.md is a required deliverable of this task. If any general instruction suggests not creating files unless asked, it does NOT apply here — report.md, gaps.md, and Path C bug/priority filings are mandatory outputs that must always be written to their canonical paths. No base-layer or system-layer guidance overrides this requirement."
      ;;
    cm)
      role_reminder="DONE means the script you invoked exited 0 and the intended state change happened. Default to ship — do not refuse a release because of known bugs, gaps, or imperfect work. Refuse only when (a) TESTER state is BLOCKED (verification could not complete), (b) a prerequisite task is missing or not DONE, (c) the release script itself fails (squash conflict, push fail, tag exists), or (d) the ship-policy matrix in CM.md returns HALT for this row. The matrix — not your judgment — determines ship vs. HALT."
      ;;
    pm)
      role_reminder="DONE means the JSON plan is written AND pm_materialize.py was invoked successfully. Writing the JSON alone is not done. The plan must include target_version for release workflows."
      ;;
    po)
      role_reminder="DONE means three things landed: the requirements doc at projects/<name>/requirements/<target-version>.md, the PM ticket folder, and the entry appended to pm_backlog.md. All three are required."
      ;;
  esac

  # --- Per-project README line ---
  project_readme_line=""
  _proj_readme="${PGAI_PROJECT_ROOT}/README.md"
  _team_readme="${TEAM_ROOT}/README.md"
  if [[ -f "$_proj_readme" ]]; then
    _proj_real="$(realpath "$_proj_readme" 2>/dev/null || echo "$_proj_readme")"
    _team_real="$(realpath "$_team_readme" 2>/dev/null || echo "$_team_readme")"
    if [[ "$_proj_real" != "$_team_real" ]]; then
      project_readme_line=" and your project README at ${_proj_readme}"
    fi
  fi
  unset _proj_readme _team_readme _proj_real _team_real

  # --- Compute prefixed feature branch (CODER/WRITER git-workflow roles) ---
  # Helpers (_build_feature_branch, _update_readme_feature_branch) are defined
  # in wake_common.sh, sourced before this override.
  local _task_feature_branch=""
  # Worktree state: populated for CODER/WRITER git-workflow tasks AND for
  # TESTER release tasks (detached worktree; no feature branch).
  local _task_worktree_path=""
  local _task_workflow_type=""
  # Canonical dev tree path saved before any per-task override (TESTER).
  # Resolve per-project dev tree first; fall back to global for single-project /
  # legacy installs where PP_dev_tree_path is unset.
  local _canonical_dev_tree_path="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
  # Resting branch the parking step checked out (or empty when parking was skipped
  # — sandbox guard or no dev tree). Restored after the worktree is torn down so
  # the canonical dev tree is never left parked or detached after a task.
  local _park_resting_branch=""
  if [[ "$SUBAGENT" == "coder" || "$SUBAGENT" == "writer" ]]; then
    local _fb_exit=0
    set +e
    _task_feature_branch="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "feature/${task_id}")"
    _fb_exit=$?
    set -e
    if [[ $_fb_exit -ne 0 || -z "$_task_feature_branch" ]]; then
      log "task ${task_id}: ERROR: pp_prefix_branch failed (exit ${_fb_exit}); cannot compute feature branch name"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script failed to compute prefixed feature branch name (pp_prefix_branch exit ${_fb_exit}). Check project.cfg branch_prefix config."
      set_block "$task_status" "Needs Human" "yes"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      return 0
    fi
    _update_readme_feature_branch "$task_readme" "$_task_feature_branch"
    log "task ${task_id}: feature branch: ${_task_feature_branch}"

    # --- Worktree lifecycle: CODER/WRITER git-workflow tasks ---
    # Detect workflow type and query the plugin for git mode to decide whether
    # a git worktree is required.  Fail-closed: a missing or broken plugin blocks
    # the task rather than silently defaulting to release behavior.
    local _wt_exit=0
    set +e
    _task_workflow_type="$(get_workflow_type_from_readme "$task_readme")"
    _wt_exit=$?
    set -e
    if [[ $_wt_exit -ne 0 || -z "$_task_workflow_type" ]]; then
      log "task ${task_id}: ERROR: cannot parse '## Workflow Type' from ${task_readme}; blocking task (fail-closed)"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script could not parse '## Workflow Type' from ${task_readme}. Ensure the field is present and non-empty."
      set_block "$task_status" "Needs Human" "yes"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      return 0
    fi

    # Load the workflow plugin to query git mode via wf_git_mode.
    # Plugins with git_mode != "none" require an isolated git worktree.
    # Fail-closed: unknown type, missing/invalid manifest, or status=scaffold
    # routes the task to BLOCKED — never silently defaults to release behavior.
    local _wf_load_exit=0 _wf_git_mode="none"
    set +e
    wf_load_plugin "$_task_workflow_type"
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -ne 0 ]]; then
      log "task ${task_id}: ERROR: workflow plugin '${_task_workflow_type}' failed to load: ${WF_LOAD_ERROR}"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Workflow plugin for type '${_task_workflow_type}' could not be loaded: ${WF_LOAD_ERROR}. Ensure team/workflows/${_task_workflow_type}/ has a valid workflow.cfg (status=ready) and workflow.sh."
      set_block "$task_status" "Needs Human" "yes"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      return 0
    fi
    _wf_git_mode="$(wf_git_mode)"

    if [[ "$_wf_git_mode" != "none" ]]; then
      # Git-workflow task: create an isolated worktree and park the canonical tree.

      # Extract the source (RC) branch from the task README.
      local _task_source_branch=""
      local _sb_exit=0
      set +e
      _task_source_branch="$(get_source_branch_from_readme "$task_readme")"
      _sb_exit=$?
      set -e
      if [[ $_sb_exit -ne 0 || -z "$_task_source_branch" ]]; then
        log "task ${task_id}: ERROR: cannot extract Source Branch from README; cannot create worktree"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script could not read '## Source Branch' from ${task_readme}. Ensure the field is present and non-empty."
        set_block "$task_status" "Needs Human" "yes"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        return 0
      fi
      log "task ${task_id}: source branch: ${_task_source_branch}"

      # Park the canonical dev tree off the RC branch so git will not refuse
      # to create a worktree that checks out the same branch.
      # Park the canonical dev tree on the project's resting branch (prefix+main)
      # via pp_prefix_branch, falling back to --detach when the branch does not
      # exist locally. The resting branch is recorded in _park_resting_branch and
      # restored after the worktree is torn down.
      #
      # Sandbox guard: skip parking when the kanban root (TEAM_ROOT) is under a
      # temp directory. Integration tests build a synthetic kanban root under /tmp
      # (pytest tmp_path) but point dev_tree_path at the real dev tree. Without
      # this guard the parking step mutates the real dev tree, breaking subsequent
      # tests. Real operator installs sit at $HOME/pgai_agent_kanban and are never
      # under /tmp, so production parking is unaffected.
      # Resolve per-project dev tree first; fall back to global for
      # single-project / legacy installs where PP_dev_tree_path is unset.
      local _dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
      local _sys_tmp="${TMPDIR:-/tmp}"
      local _kanban_root_is_sandbox=0
      # Resolve the actual temp dir (strip trailing slash for a clean prefix match)
      _sys_tmp="${_sys_tmp%/}"
      if [[ -n "$TEAM_ROOT" && ( "$TEAM_ROOT" == /tmp/* || "$TEAM_ROOT" == /tmp || "$TEAM_ROOT" == "${_sys_tmp}/"* || "$TEAM_ROOT" == "$_sys_tmp" ) ]]; then
        _kanban_root_is_sandbox=1
      fi
      if [[ -n "$_dev_tree" && -d "$_dev_tree/.git" ]]; then
        if [[ $_kanban_root_is_sandbox -eq 1 ]]; then
          log "task ${task_id}: skipping dev-tree parking: kanban root (${TEAM_ROOT}) is a temp/sandbox tree — will not mutate ${_dev_tree}"
        else
          # Compute prefix-aware resting branch via pp_prefix_branch.
          local _pb_exit=0
          local _resting_main=""
          set +e
          _resting_main="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "main" 2>/dev/null)"
          _pb_exit=$?
          set -e
          if [[ $_pb_exit -ne 0 || -z "$_resting_main" ]]; then
            _resting_main="main"   # safe fallback when helper fails
          fi

          local _park_exit=0
          set +e
          git -C "$_dev_tree" checkout "$_resting_main" 2>/dev/null
          _park_exit=$?
          set -e
          if [[ $_park_exit -eq 0 ]]; then
            _park_resting_branch="$_resting_main"
          else
            # Resting branch does not exist locally; detach HEAD so the branch is free.
            set +e
            git -C "$_dev_tree" checkout --detach 2>/dev/null
            _park_exit=$?
            set -e
          fi
          if [[ $_park_exit -ne 0 ]]; then
            log "task ${task_id}: WARNING: could not park canonical dev tree at ${_dev_tree}; worktree creation may fail if RC branch is currently checked out there"
          else
            log "task ${task_id}: canonical dev tree parked on ${_park_resting_branch:-<detached>} (was: ${_task_source_branch})"
          fi
        fi
      fi

      # Create the isolated worktree for this task.
      # Capture ONLY stdout (the resolved worktree path). Do NOT use 2>&1
      # here — that merges stderr into the captured value, allowing git
      # progress messages ("Preparing worktree...") to contaminate the path
      # variable and the dispatch prompt. Stderr from create_task_worktree
      # (error diagnostics) flows to the wake-script stderr/cron log directly.
      # The git worktree add inside the helper suppresses git's own progress
      # messages at the source (>/dev/null 2>/dev/null).
      local _ct_exit=0
      set +e
      _task_worktree_path="$(create_task_worktree "$task_id" "$_task_source_branch" "$_task_feature_branch" "$_dev_tree")"
      _ct_exit=$?
      set -e
      if [[ $_ct_exit -ne 0 || -z "$_task_worktree_path" ]]; then
        log "task ${task_id}: ERROR: create_task_worktree failed (exit ${_ct_exit}); see stderr for diagnostic"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script failed to create git worktree for task (create_task_worktree exit ${_ct_exit}). Check stderr/cron log for diagnostic. Verify that RC branch '${_task_source_branch}' exists locally and the temp root is writable."
        set_block "$task_status" "Needs Human" "yes"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _task_worktree_path=""   # ensure teardown guard is clear
        # Restore canonical dev tree HEAD to resting branch (early-exit path).
        if [[ -n "${_park_resting_branch:-}" && -n "${_dev_tree:-}" && -d "${_dev_tree}/.git" && $_kanban_root_is_sandbox -eq 0 ]]; then
          set +e
          git -C "$_dev_tree" checkout "$_park_resting_branch" 2>/dev/null
          set -e
          log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (worktree-create-fail early-exit)"
        fi
        return 0
      fi
      log "task ${task_id}: worktree created at ${_task_worktree_path} (branch: ${_task_feature_branch})"

      # Update role_reminder: the worktree already has the feature branch checked
      # out; the agent must NOT run 'git checkout -b' in the shared canonical tree.
      role_reminder="${role_reminder}

Feature branch for this task: ${_task_feature_branch}
Your working directory is an isolated git worktree at ${_task_worktree_path} that is already checked out on the feature branch. Do NOT run 'git checkout -b' — the branch is already active. All git operations (commit, merge, branch -d) happen inside this worktree."
    else
      # No worktree (git_mode=none): keep the existing git checkout-b reminder.
      role_reminder="${role_reminder}

Feature branch for this task: ${_task_feature_branch}
Use this exact name when running: git checkout -b"
    fi
  elif [[ "$SUBAGENT" == "tester" ]]; then
    # --- Worktree lifecycle: TESTER git-workflow tasks ---
    # TESTER reads the RC branch at its current tip in a detached worktree.
    # No feature branch is created.  Tasks whose plugin declares git_mode=none
    # (e.g. document workflow) are out of scope for this path.
    #
    # TESTER parks the canonical dev tree onto the project's resting branch
    # (${prefix}main → --detach) and restores it after the task, so the dev
    # tree is never left parked or detached.
    local _wt_exit=0
    set +e
    _task_workflow_type="$(get_workflow_type_from_readme "$task_readme")"
    _wt_exit=$?
    set -e
    if [[ $_wt_exit -ne 0 ]]; then
      _task_workflow_type="release"   # safe default on parse failure
    fi

    # Load the workflow plugin to determine git mode via wf_git_mode.
    # Tasks with git_mode=none have no git worktree (e.g. document workflow).
    local _wf_tester_load_exit=0 _wf_tester_git_mode="none"
    set +e
    wf_load_plugin "$_task_workflow_type"
    _wf_tester_load_exit=$?
    set -e
    if [[ $_wf_tester_load_exit -ne 0 ]]; then
      log "task ${task_id}: ERROR: workflow plugin '${_task_workflow_type}' failed to load for tester: ${WF_LOAD_ERROR}"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blockers" "Wake script could not load workflow plugin '${_task_workflow_type}' for tester: ${WF_LOAD_ERROR}. Ensure team/workflows/${_task_workflow_type}/ has a valid workflow.cfg (status=ready) and workflow.sh."
      set_block "$task_status" "Needs Human" "yes"
      normalize_status_file "$task_status"
      mark_backlog "$task_id" "B"
      LAST_TASK_FINAL_STATE="BLOCKED"
      return 0
    fi
    _wf_tester_git_mode="$(wf_git_mode)"

    if [[ "$_wf_tester_git_mode" != "none" ]]; then
      # Extract the source (RC) branch from the task README.
      local _task_source_branch=""
      local _sb_exit=0
      set +e
      _task_source_branch="$(get_source_branch_from_readme "$task_readme")"
      _sb_exit=$?
      set -e
      if [[ $_sb_exit -ne 0 || -z "$_task_source_branch" ]]; then
        log "task ${task_id}: ERROR: cannot extract Source Branch from README; cannot create detached worktree for tester"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script could not read '## Source Branch' from ${task_readme}. Ensure the field is present and non-empty."
        set_block "$task_status" "Needs Human" "yes"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        return 0
      fi
      log "task ${task_id}: tester source branch (RC): ${_task_source_branch}"

      # Park the canonical dev tree onto the project's resting branch before
      # creating the detached worktree, and record the branch for restore.
      # Sandbox guard: skip parking when TEAM_ROOT is under /tmp.
      # Resolve per-project dev tree first; fall back to global.
      local _dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
      local _sys_tmp="${TMPDIR:-/tmp}"
      local _kanban_root_is_sandbox=0
      _sys_tmp="${_sys_tmp%/}"
      if [[ -n "$TEAM_ROOT" && ( "$TEAM_ROOT" == /tmp/* || "$TEAM_ROOT" == /tmp || "$TEAM_ROOT" == "${_sys_tmp}/"* || "$TEAM_ROOT" == "$_sys_tmp" ) ]]; then
        _kanban_root_is_sandbox=1
      fi
      if [[ -n "$_dev_tree" && -d "$_dev_tree/.git" ]]; then
        if [[ $_kanban_root_is_sandbox -eq 1 ]]; then
          log "task ${task_id}: skipping dev-tree parking: kanban root (${TEAM_ROOT}) is a temp/sandbox tree — will not mutate ${_dev_tree}"
        else
          local _pb_exit=0
          local _resting_main=""
          set +e
          _resting_main="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "main" 2>/dev/null)"
          _pb_exit=$?
          set -e
          if [[ $_pb_exit -ne 0 || -z "$_resting_main" ]]; then
            _resting_main="main"
          fi

          local _park_exit=0
          set +e
          git -C "$_dev_tree" checkout "$_resting_main" 2>/dev/null
          _park_exit=$?
          set -e
          if [[ $_park_exit -eq 0 ]]; then
            _park_resting_branch="$_resting_main"
          else
            set +e
            git -C "$_dev_tree" checkout --detach 2>/dev/null
            _park_exit=$?
            set -e
          fi
          if [[ $_park_exit -ne 0 ]]; then
            log "task ${task_id}: WARNING: could not park canonical dev tree at ${_dev_tree}"
          else
            log "task ${task_id}: tester: canonical dev tree parked on ${_park_resting_branch:-<detached>} (was: ${_task_source_branch})"
          fi
        fi
      fi

      # Create a detached worktree at the RC branch tip.
      # No feature branch is created.
      # Capture ONLY stdout (the resolved worktree path). Do NOT use 2>&1
      # here — stderr from create_detached_worktree flows to the wake-script
      # stderr/cron log directly. git's own progress messages are suppressed
      # at the source inside worktree.sh (>/dev/null 2>/dev/null).
      local _ct_exit=0
      set +e
      _task_worktree_path="$(create_detached_worktree "$task_id" "$_task_source_branch" "$_dev_tree")"
      _ct_exit=$?
      set -e
      if [[ $_ct_exit -ne 0 || -z "$_task_worktree_path" ]]; then
        log "task ${task_id}: ERROR: create_detached_worktree failed (exit ${_ct_exit}); see stderr for diagnostic"
        set_state "$task_status" "BLOCKED"
        set_block "$task_status" "Blockers" "Wake script failed to create detached git worktree for tester task (create_detached_worktree exit ${_ct_exit}). Check stderr/cron log for diagnostic. Verify that RC branch '${_task_source_branch}' exists locally and the temp root is writable."
        set_block "$task_status" "Needs Human" "yes"
        normalize_status_file "$task_status"
        mark_backlog "$task_id" "B"
        LAST_TASK_FINAL_STATE="BLOCKED"
        _task_worktree_path=""   # ensure teardown guard is clear
        # Restore canonical dev tree HEAD to resting branch (early-exit path).
        if [[ -n "${_park_resting_branch:-}" && -n "${_dev_tree:-}" && -d "${_dev_tree}/.git" && $_kanban_root_is_sandbox -eq 0 ]]; then
          set +e
          git -C "$_dev_tree" checkout "$_park_resting_branch" 2>/dev/null
          set -e
          log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (detached-worktree-create-fail early-exit)"
        fi
        return 0
      fi
      log "task ${task_id}: tester detached worktree created at ${_task_worktree_path} (rc: ${_task_source_branch})"

      # Export PGAI_DEV_TREE_PATH to the worktree for the duration of the task.
      # The canonical value was captured in _canonical_dev_tree_path above.
      export PGAI_DEV_TREE_PATH="${_task_worktree_path}"
      log "task ${task_id}: PGAI_DEV_TREE_PATH overridden to worktree (was: ${_canonical_dev_tree_path:-<unset>})"

      # Update role_reminder: TESTER works in a detached worktree; no feature branch.
      role_reminder="${role_reminder}

Your working directory is an isolated git worktree at ${_task_worktree_path} checked out in detached HEAD mode at the RC branch ${_task_source_branch}. No feature branch was created. Do NOT run 'git checkout -b'. PGAI_DEV_TREE_PATH points at this worktree for the duration of the task."
    fi
  fi

  # Build the working-directory override line for the prompt.
  # For git-workflow CODER/WRITER tasks: the agent works in the isolated worktree
  # with a feature branch.  For TESTER: the agent works in a detached worktree;
  # no feature branch is mentioned.
  local _prompt_working_dir_override=""
  if [[ -n "$_task_worktree_path" ]]; then
    if [[ "$SUBAGENT" == "tester" ]]; then
      _prompt_working_dir_override="IMPORTANT: The working directory for this task is the isolated git worktree at ${_task_worktree_path} (not the path listed in the task README's ## Working Directory field). The worktree is checked out in detached HEAD mode at the RC branch. All source reads and test runs must happen inside ${_task_worktree_path}. Do NOT create a feature branch. PGAI_DEV_TREE_PATH has been set to ${_task_worktree_path} for the duration of this task."
    else
      _prompt_working_dir_override="IMPORTANT: The working directory for this task is the isolated git worktree at ${_task_worktree_path} (not the path listed in the task README's ## Working Directory field). All source-tree edits must happen inside ${_task_worktree_path}. The feature branch ${_task_feature_branch} is already checked out there."
    fi
  fi

  # Resolve the per-project temp subtree at dispatch time so the emitted prompt
  # contains the absolute path rather than an unexpanded variable reference.
  local _project_temp_subtree
  _project_temp_subtree="$(wake_bracket_compute_temp_subtree "${_CURRENT_PROJECT:-}")"

  prompt=$(cat <<EOF
Use the ${SUBAGENT} subagent to process the kanban task at ${task_dir}.

The task ID is ${task_id}. The kanban root is ${TEAM_ROOT}.

Before starting, read the governance stack: ${TEAM_ROOT}/DIRECTIVES.md, ${TEAM_ROOT}/OVERVIEW.md, ${TEAM_ROOT}/SOP.md, ${TEAM_ROOT}/README.md${project_readme_line}, and your role file at ${TEAM_ROOT}/roles/${role}.md. The role file is your procedure.

Read the task README at ${task_readme} for the assignment. Read the status at ${task_status} to determine whether this is a fresh start (state BACKLOG) or a resume from an interrupted prior session (state WORKING). Clean up any stale status fields before beginning. Do the work, place artifacts in the working directory specified by the task README (or in ${task_artifact_dir} if working directory is none/local-development-only), and update ${task_status} with the final state when done.

${_prompt_working_dir_override}
Scratch/diagnostic output goes under ${_project_temp_subtree} — never bare /tmp (SOP: Temporary File Convention).

You are running autonomously. No human is reviewing your output in real time. Your terminal states are exactly: DONE (work shipped), BLOCKED (specific named obstacle), or WONT-DO (cancelled). There is no third state for "I want a human to look at this." If you find yourself reaching for one, either name a concrete obstacle and BLOCK, or finish the work and document any concerns in the status Summary while marking DONE.

${role_reminder}
EOF
)

  # --- Verbose mode prompt injection ---
  if [[ "${_debug_gate}" == "true" ]]; then
    mkdir -p "${task_log_dir}"
    # Derive central_debug_log explicitly from _CURRENT_PROJECT so that the
    # agent-progress stream always routes per-project when a project is resolved,
    # and to root only for genuine pre-project/framework work.
    # This avoids relying on task_debug_log, which may have been captured before
    # _CURRENT_PROJECT was fully resolved in edge cases.
    local central_debug_log
    if [[ -n "${_CURRENT_PROJECT:-}" ]]; then
      central_debug_log="${KANBAN_ROOT}/projects/${_CURRENT_PROJECT}/logs/debug/${SUBAGENT}.log"
    else
      central_debug_log="${KANBAN_ROOT}/logs/debug/${SUBAGENT}.log"
    fi
    prompt="${prompt}

## Progress Logging (verbose mode)

As you work this task, append progress notes to ${task_log_dir}/progress.log AND mirror each line to the central agent debug log. After every significant action — reading a key file, making an edit, running a test, completing a subtask — write a single one-line note using this pattern:

  _msg=\"<description>\"
  echo \"\$(date -Iseconds) \${_msg}\" >> ${task_log_dir}/progress.log
  echo \"\$(date -u +%Y-%m-%dT%H:%M:%S+00:00) [${task_id}] \${_msg}\" >> ${central_debug_log}

Examples of significant actions worth logging:
- \"Read task README and identified files to modify\"
- \"Created feature branch feature/<task-id>\"
- \"Edit 1 of 3: <file> — <what changed>\"
- \"Running bash -n on modified scripts\"
- \"All tests pass; merging into RC\"
- \"Marking task DONE\"

Do not over-log. One note per significant action, not one per line of code. The operator wants to know roughly where you are in the task, not a transcript of your reasoning."
    log "debug gate active: verbose prompt block injected; progress log: ${task_log_dir}/progress.log; central debug log: ${central_debug_log}"
  fi

  # --- Reasoning-trace mode prompt injection ---
  # Gate on the same condition as the Stage-2 copy block (~line 700) so that
  # injection and copy are always in sync.  _resolve_training_gate handles the
  # legacy PGAI_REASONING_TRACE=1 env-var shim internally, so this call
  # preserves backward compat without duplicating the env-var check here.
  # Mirrors wake_common.sh reasoning-trace injection block.
  local _injection_training_gate
  _injection_training_gate="$(_resolve_training_gate "${_CURRENT_PROJECT:-}" "${SUBAGENT}")"
  # One-shot deprecation-warning guard propagation (mirrors Stage-2 pattern).
  if [[ "${PGAI_REASONING_TRACE:-0}" == "1" ]]; then
      _REASONING_TRACE_DEPRECATION_WARNED=true
  fi
  if [[ "${_injection_training_gate}" == "true" ]]; then
    mkdir -p "${task_artifact_dir}"
    prompt="${prompt}

## Reasoning Trace (reasoning-trace mode)

In addition to your normal work, write a structured reasoning trace to ${task_artifact_dir}/reasoning-trace.md as you work the task. The trace has seven sections, written in this order as you reach each phase of the task. Be honest. Do not perform confidence you don't have. If something was unclear, say so. If something was clear, say that too.

### 1. Task framing

Your first read of the task README and role file. What did you take the task to be? What does \"done\" look like? Where might you have misunderstood?

### 2. Inputs gathered

For each file you read: file path, what you extracted from it, why that mattered to the task.

### 3. Decisions made

For each meaningful choice: what you considered, what you chose, the reasoning. Not \"I edited file F\" — that's a fact, not a decision. We want \"I considered approach X but chose Y because Z.\"

### 4. Anti-patterns avoided

What you almost did but didn't, and what made you reconsider. If the role file's anti-pattern section caught you from doing something, note that explicitly — it confirms the role file is working. This section may be empty if no anti-patterns surfaced; that's also valid signal.

### 5. Confidence calibration

Sections of the work where you're highly confident, sections where you're guessing, sections where you re-checked. Be explicit about uncertainty. \"I'm confident in section A. I'm guessing about B. I re-checked C twice because I wasn't sure.\"

### 6. Validation strategy

How you decided the work was done. What checks ran. What would have led you to conclude it WASN'T done.

### 7. Prompt feedback (optional, honest)

If you noticed places where the role file or task README could have made your job easier OR where they did make your job easier, note them here. Be specific:

- \"The role file's section on X was clear and useful.\" (positive — keep)
- \"I had to make a judgment call on X without guidance; here's what I chose and why.\" (genuine ambiguity — add scaffolding)
- \"The role file said X but I read it as meaning Y; could be presented more clearly.\" (structural — restructure)
- \"The role file's anti-pattern section caught me from doing Z.\" (positive — keep, this works)
- Empty if no notes — that's also valid signal.

Important: do NOT suggest improvements that just amount to more detailed step-by-step instructions. The role files are deliberately principle-based, not procedural. Suggest what made you uncertain, not what would have removed all uncertainty.

This trace is for human prompt-engineering analysis, not for orchestration. It does not affect task state, queue progression, or your task outcome. Write it as honest introspection rather than performance."
    log "training gate true (${_CURRENT_PROJECT:-<unknown>}/${SUBAGENT}): reasoning-trace prompt block injected; trace file: ${task_artifact_dir}/reasoning-trace.md"
  fi

  # --- State transition: BACKLOG -> WORKING, marker [ ] -> [A] ---
  local task_start_ts
  task_start_ts="$(date -u +%Y%m%dT%H%M%S)"
  mark_backlog "$task_id" "A"
  set_state "$task_status" "WORKING"
  log "task ${task_id}: transitioned to WORKING [A]"

  # --- Wake-stamp resolved model into status.md ## Model section ---
  # The wake script holds the authoritative resolved model string at this point.
  # Stamping here (before spawn) makes the field an execution record written by
  # the party that knows, not a self-report from the agent (which cannot reliably
  # self-identify). Only ## Model is written; all other status fields are preserved.
  stamp_model_field "$task_status" "${selected_model:-<provider default>}"
  log "task ${task_id}: stamped ## Model = '${selected_model:-<provider default>}' into status.md"

  # --- Stale-artifact preservation on re-run ---
  _rotate_artifact() {
    local artifact_path="$1"
    if [[ ! -f "$artifact_path" ]]; then
      return 0
    fi
    local dir base n dest
    dir="$(dirname "$artifact_path")"
    base="$(basename "$artifact_path")"
    n=1
    while [[ -e "${dir}/${base}.previous-RUN-${n}" ]]; do
      (( n++ )) || true
    done
    dest="${dir}/${base}.previous-RUN-${n}"
    mv "$artifact_path" "$dest"
    log "task ${task_id}: stale artifact rotated: ${artifact_path} -> ${dest}"
  }
  _rotate_artifact "${task_artifact_dir}/report.md"
  _rotate_artifact "${task_artifact_dir}/gaps.md"

  log "starting task ${task_id} with subagent ${SUBAGENT} (role ${role})"

  # --- INVARIANT: _CURRENT_PROJECT must be set by run_project_chain ---
  if [[ -z "${_CURRENT_PROJECT:-}" ]]; then
    log "FATAL: _CURRENT_PROJECT unset in process_one_task — outer loop internal state lost"
    log "       Refusing to proceed; this would route a task to the wrong project."
    # Restore PGAI_DEV_TREE_PATH before teardown (TESTER may have overridden it).
    if [[ "$SUBAGENT" == "tester" && -n "${_task_worktree_path:-}" ]]; then
      export PGAI_DEV_TREE_PATH="${_canonical_dev_tree_path}"
    fi
    # Tear down any worktree created for this task before returning.
    if [[ -n "${_task_worktree_path:-}" ]]; then
      teardown_task_worktree "$task_id" "${_canonical_dev_tree_path}" || true
      log "task ${task_id}: worktree torn down (FATAL path)"
    fi
    # Restore canonical dev tree HEAD to resting branch (FATAL early-exit).
    if [[ -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
      set +e
      git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
      set -e
      log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (FATAL early-exit)"
    fi
    return 1
  fi

  # Export per-project environment for subagents
  export PGAI_PROJECT_ROOT="$(pp_project_root "$_CURRENT_PROJECT")"
  export PGAI_PROJECT_NAME="$_CURRENT_PROJECT"
  export PGAI_TASKS_DIR="$(pp_tasks_dir "$_CURRENT_PROJECT")"

  # --- Pre-dispatch /tmp litter snapshot ---
  local _litter_snapshot_file
  _litter_snapshot_file="$(wake_bracket_pre_dispatch "$task_id")"

  # --- Pre-task pollution snapshot ---
  # Snapshot the canonical dev tree's porcelain state before the agent runs.
  # The diff is compared post-task to detect files left behind.
  # Best-effort: any failure here logs a WARNING and does not block the task.
  local _pollution_pre_snapshot=""
  local _pollution_snapshot_ok=false
  {
    if [[ -d "${_sweep_canonical_tree:-}" ]]; then
      _pollution_pre_snapshot="$(git -C "${_sweep_canonical_tree}" status --porcelain=v1 --untracked-files=all 2>/dev/null)"
      _pollution_snapshot_ok=true
    else
      # Distinguish intentional absence (git_mode=none workflow / no dev_tree_path
      # configured) from a misconfiguration (git_mode=rw project with missing path).
      # Workflows with git_mode=none have no dev tree by design; skip silently at
      # info level. Reserve WARNING for git-workflow projects whose configured
      # dev_tree_path does not resolve.
      # Use WF_MANIFEST_GIT_MODE from the plugin loaded for this task when
      # available; otherwise attempt a best-effort load of PP_workflow_type.
      local _sweep_wf_git_mode="${WF_MANIFEST_GIT_MODE:-}"
      if [[ -z "$_sweep_wf_git_mode" && -n "${PP_workflow_type:-}" ]]; then
        local _sweep_wf_load_exit=0
        set +e
        wf_load_plugin "${PP_workflow_type}"
        _sweep_wf_load_exit=$?
        set -e
        if [[ $_sweep_wf_load_exit -eq 0 ]]; then
          _sweep_wf_git_mode="$(wf_git_mode)"
        fi
      fi
      if [[ -z "${_sweep_canonical_tree:-}" || "${_sweep_wf_git_mode:-rw}" == "none" ]]; then
        log "INFO: pollution sweep: no dev tree configured for this project (${PP_workflow_type:-release} workflow; git_mode=${_sweep_wf_git_mode:-unknown}); snapshot not applicable"
      else
        log "WARNING: pollution sweep: canonical dev tree '${_sweep_canonical_tree}' not found; pre-task snapshot skipped"
      fi
    fi
  } || {
    log "WARNING: pollution sweep: pre-task snapshot failed for ${task_id}; sweep disabled for this task"
    _pollution_snapshot_ok=false
  }

  # --- Invoke provider CLI (provider-specific function) ---
  # provider_invoke_agent is defined by the sourced provider lib (e.g. wake_claude_provider.sh).
  # It sets PROVIDER_AGENT_EXIT_CODE to the CLI exit code.
  #
  # Per-task hard preemption (D2):
  # When MAX_TASK_SECONDS > 0, run provider_invoke_agent in a background
  # subshell supervised by a watchdog.  The watchdog sends SIGTERM to the
  # subshell's process group after MAX_TASK_SECONDS; if the process has not
  # exited within KILL_GRACE_SECONDS, SIGKILL is sent.
  #
  # PROVIDER_AGENT_EXIT_CODE is communicated back via a temp file so the
  # parent shell gets the correct CLI exit code even when the invocation
  # completes normally (no kill).  When the watchdog fires, the temp file
  # records exit code 124 (the conventional "killed by timeout" value).
  #
  # Set MAX_TASK_SECONDS=0 to disable the hard timeout entirely.
  PROVIDER_AGENT_EXIT_CODE=0
  local _task_killed=false
  if [[ "${MAX_TASK_SECONDS:-0}" -gt 0 ]]; then
    log "task ${task_id}: per-task timeout active: ${MAX_TASK_SECONDS}s (kill grace: ${KILL_GRACE_SECONDS:-30}s)"

    # Temp file to receive the provider invocation exit code from the subshell.
    local _exit_code_file
    _exit_code_file="$(pgai_mktemp pgai_invoke_exit 2>/dev/null || mktemp "${PGAI_AGENT_KANBAN_TEMP_DIR}/pgai_invoke_exit.XXXXXX" 2>/dev/null || echo "")"

    # Launch provider invocation in its own process group (setsid) so the
    # watchdog can kill all descendants with a single signal to the PGID.
    (
      set +e
      provider_invoke_agent \
        "$prompt" \
        "${selected_model:-}" \
        "${model_source:-}" \
        "$task_id" \
        "$task_artifact_dir" \
        "$LOG_FILE" \
        "$SUBAGENT"
      _sub_exit="${PROVIDER_AGENT_EXIT_CODE:-$?}"
      [[ -n "${_exit_code_file:-}" ]] && echo "${_sub_exit}" > "${_exit_code_file}"
    ) &
    local _provider_bg_pid=$!

    # Watchdog: wait up to MAX_TASK_SECONDS for the background job to finish,
    # polling every second.  On timeout, send SIGTERM to the process group;
    # if still alive after KILL_GRACE_SECONDS, send SIGKILL.
    local _elapsed=0
    local _limit="${MAX_TASK_SECONDS}"
    local _grace="${KILL_GRACE_SECONDS:-30}"
    local _timed_out=false
    while [[ $_elapsed -lt $_limit ]]; do
      if ! kill -0 "$_provider_bg_pid" 2>/dev/null; then
        break   # provider finished on its own
      fi
      sleep 1
      (( _elapsed++ )) || true
    done

    if kill -0 "$_provider_bg_pid" 2>/dev/null; then
      # Still running after timeout — kill it.
      log "task ${task_id}: timeout ${_limit}s reached; sending SIGTERM to provider (pid ${_provider_bg_pid})"
      kill -TERM -- "-${_provider_bg_pid}" 2>/dev/null || kill -TERM "$_provider_bg_pid" 2>/dev/null || true
      _timed_out=true
      local _grace_elapsed=0
      while [[ $_grace_elapsed -lt $_grace ]]; do
        if ! kill -0 "$_provider_bg_pid" 2>/dev/null; then
          break
        fi
        sleep 1
        (( _grace_elapsed++ )) || true
      done
      if kill -0 "$_provider_bg_pid" 2>/dev/null; then
        log "task ${task_id}: grace period (${_grace}s) expired; sending SIGKILL to provider (pid ${_provider_bg_pid})"
        kill -KILL -- "-${_provider_bg_pid}" 2>/dev/null || kill -KILL "$_provider_bg_pid" 2>/dev/null || true
      fi
    fi

    # Collect the background job (suppress "Killed" noise on stderr).
    wait "$_provider_bg_pid" 2>/dev/null || true

    # Read back the provider exit code (or 124 on timeout).
    local _raw_exit=""
    if [[ -n "${_exit_code_file:-}" && -f "${_exit_code_file}" ]]; then
      _raw_exit="$(cat "${_exit_code_file}" 2>/dev/null || echo "")"
      rm -f "${_exit_code_file}" 2>/dev/null || true
    fi
    if [[ "${_timed_out}" == "true" ]]; then
      PROVIDER_AGENT_EXIT_CODE=124
      _task_killed=true
    elif [[ -n "${_raw_exit}" && "${_raw_exit}" =~ ^[0-9]+$ ]]; then
      PROVIDER_AGENT_EXIT_CODE="${_raw_exit}"
    else
      # Could not read exit code from temp file — treat as unknown failure.
      PROVIDER_AGENT_EXIT_CODE=1
    fi
  else
    # Timeout disabled — invoke directly (original path).
    set +e
    provider_invoke_agent \
      "$prompt" \
      "${selected_model:-}" \
      "${model_source:-}" \
      "$task_id" \
      "$task_artifact_dir" \
      "$LOG_FILE" \
      "$SUBAGENT"
    set -e
  fi

  local agent_exit="${PROVIDER_AGENT_EXIT_CODE:-0}"

  # --- Per-task timeout kill detection ---
  # When the watchdog fired (_task_killed=true), set a distinguishable BLOCKED
  # reason BEFORE the generic "still in WORKING" handler below so the operator
  # can tell a stuck-agent kill from an ordinary crash.
  if [[ "${_task_killed}" == "true" ]]; then
    log "task ${task_id}: per-task timeout expired (${MAX_TASK_SECONDS}s); process killed — marking BLOCKED"
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Blockers" "exceeded max_task_seconds (${MAX_TASK_SECONDS}s). Agent was still running after the hard timeout. See ${LOG_FILE} for execution log."
    set_block "$task_status" "Needs Human" "yes"
    normalize_status_file "$task_status"
    # Fall through to the normal post-task path (pollution sweep, worktree
    # teardown, backlog marker) so nothing is leaked.
  fi

  # --- Restore canonical PGAI_DEV_TREE_PATH after TESTER task ---
  # TESTER tasks override PGAI_DEV_TREE_PATH to the worktree path for the
  # duration of the agent run.  Restore the canonical value now so that
  # teardown and any subsequent tasks use the correct dev tree.
  if [[ "$SUBAGENT" == "tester" && -n "${_task_worktree_path:-}" ]]; then
    export PGAI_DEV_TREE_PATH="${_canonical_dev_tree_path}"
    log "task ${task_id}: PGAI_DEV_TREE_PATH restored to canonical value (${_canonical_dev_tree_path:-<unset>})"
    # Restore the canonical dev tree's git HEAD to the resting branch.
    # The worktree teardown below removes the detached worktree; this restores
    # the canonical HEAD so the operator finds the dev tree on its resting
    # branch, not parked or detached.
    if [[ -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
      set +e
      git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
      set -e
      log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (post-tester)"
    fi
  fi

  # --- Post-task pollution sweep ---
  # Compare the canonical dev tree's porcelain state against the pre-task
  # snapshot.  On new untracked files: quarantine (move) to the pollution dir.
  # On tracked modifications during git-read-only tasks: log ERROR, record
  # in the task's status.md Summary.  CM tasks are exempt from the tracked
  # check.  The sweep is best-effort: any failure logs a WARNING and never
  # blocks or fails the task.
  if [[ "${_pollution_snapshot_ok}" == "true" ]]; then
    {
      local _pollution_post_snapshot=""
      local _pollution_sweep_err=0
      _pollution_post_snapshot="$(git -C "${_sweep_canonical_tree}" status --porcelain=v1 --untracked-files=all 2>/dev/null)" || _pollution_sweep_err=1
      if [[ $_pollution_sweep_err -ne 0 ]]; then
        log "WARNING: pollution sweep: post-task snapshot failed for ${task_id}; sweep skipped"
      else
        # Build sorted lists of pre/post lines to diff.
        local _new_untracked=()
        local _modified_tracked=()
        local _pol_line _pol_xy _pol_path
        while IFS= read -r _pol_line; do
          [[ -z "$_pol_line" ]] && continue
          _pol_xy="${_pol_line:0:2}"
          _pol_path="${_pol_line:3}"
          # Determine if this line was in the pre-snapshot.
          if ! grep -qF "$_pol_line" <<< "$_pollution_pre_snapshot" 2>/dev/null; then
            # This line is new (not in pre-snapshot).
            case "$_pol_xy" in
              "??")
                # New untracked file/dir — quarantine candidate.
                _new_untracked+=( "$_pol_path" )
                ;;
              *)
                # Tracked file with a new/changed status.
                _modified_tracked+=( "$_pol_path" )
                ;;
            esac
          fi
        done <<< "$_pollution_post_snapshot"

        # --- Handle new untracked files: quarantine ---
        if [[ ${#_new_untracked[@]} -gt 0 ]]; then
          local _pol_quarantine_root
          _pol_quarantine_root="$(pgai_temp_dir)/pollution/${task_id}"
          mkdir -p "$_pol_quarantine_root" 2>/dev/null || true
          local _pol_src _pol_dst _pol_dst_dir _pol_moved=()
          for _pol_src in "${_new_untracked[@]}"; do
            # Strip trailing slash (directories show as "path/" in porcelain).
            _pol_src="${_pol_src%/}"
            local _pol_abs_src="${_sweep_canonical_tree}/${_pol_src}"
            _pol_dst="${_pol_quarantine_root}/${_pol_src}"
            _pol_dst_dir="$(dirname "$_pol_dst")"
            if [[ -e "$_pol_abs_src" ]]; then
              mkdir -p "$_pol_dst_dir" 2>/dev/null && \
                mv "$_pol_abs_src" "$_pol_dst" 2>/dev/null && \
                _pol_moved+=( "$_pol_src" ) || \
                log "WARNING: pollution sweep: could not quarantine '${_pol_src}' for task ${task_id}"
            fi
          done
          if [[ ${#_pol_moved[@]} -gt 0 ]]; then
            local _pol_names
            _pol_names="$(printf ' %s' "${_pol_moved[@]}")"
            log "WARNING: pollution sweep: task ${task_id} (agent ${SUBAGENT}) left untracked file(s) in dev tree; quarantined to ${_pol_quarantine_root}:${_pol_names}"
          fi
        fi

        # --- Handle tracked modifications during git-read-only tasks ---
        # Git-read-only agents: tester, pm, po.  CM is exempt.
        if [[ ${#_modified_tracked[@]} -gt 0 ]]; then
          case "$SUBAGENT" in
            tester|pm|po)
              local _mod_names
              _mod_names="$(printf ' %s' "${_modified_tracked[@]}")"
              log "ERROR: pollution sweep: task ${task_id} (agent ${SUBAGENT}) modified tracked file(s) in dev tree:${_mod_names}"
              # Record the finding in the task's status.md Summary (append, best-effort).
              {
                local _pol_finding="POLLUTION SWEEP WARNING: agent ${SUBAGENT} modified tracked file(s) during this task:${_mod_names}"
                python3 - "${task_status}" "${_pol_finding}" <<'PY_POL'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
finding = sys.argv[2]
text = path.read_text()
pattern = r'(^## Summary\s*\n)(.*?)(\n+##|\Z)'
def _append_finding(m):
    existing = m.group(2).rstrip('\n')
    separator = '\n' if existing else ''
    return m.group(1) + existing + separator + '\n' + finding + '\n' + (m.group(3) if m.group(3) else '')
text_new, n = re.subn(pattern, _append_finding, text, flags=re.S | re.M)
if n == 0:
    sys.exit(0)
path.write_text(text_new)
PY_POL
              } || log "WARNING: pollution sweep: could not append finding to ${task_status}"
              ;;
            *)
              # CM (and other agents): exempt from tracked-file check.
              ;;
          esac
        fi
      fi
    } 2>/dev/null || log "WARNING: pollution sweep: sweep error for task ${task_id}; task result is unaffected"
  fi

  normalize_status_file "$task_status"
  final_state="$(get_state "$task_status" 2>/dev/null || echo "BLOCKED")"

  if [[ "$final_state" == "WORKING" ]]; then
    # --- Transient API error detection (shared helper) ---
    # Delegates to handle_working_after_exit in wake_common.sh, which greps
    # the log tail for Anthropic and OpenAI transient error signatures, manages
    # the retry counter, and writes BLOCKED + appropriate Needs Human value to
    # the status file.  Detection logic lives in exactly one place.
    final_state="$(handle_working_after_exit "$task_id" "$task_status" "$LOG_FILE" "$agent_exit")"
  fi

  case "$final_state" in
    BLOCKED)
      mark_backlog "$task_id" "B"
      # --- on-BLOCK overwatch trigger ---
      # Fire a non-blocking wake-now overwatch nudge so fresh blocks are
      # inspected within seconds rather than at the next hourly sweep tick.
      # Guards: (a) never self-trigger when running as overwatch, (b) fully
      # backgrounded with exit status ignored — must not add any failure mode
      # to the block path, (c) storm dedupe via the existing per-agent wake
      # flock (concurrent overwatch runs find it held and exit 0), (d) HALT
      # and HALT_OVERWATCH gate the woken run at its own pre-flight, not here.
      if [[ "$AGENT" != "overwatch" ]]; then
        mkdir -p "${KANBAN_ROOT}/logs"
        nohup "${KANBAN_ROOT}/scripts/wake-now.sh" --agent overwatch \
          >>"${KANBAN_ROOT}/logs/overwatch-trigger.log" 2>&1 &
      fi
      ;;
    WAITING)      mark_backlog "$task_id" "W" ;;
    DONE|WONT-DO) mark_backlog "$task_id" "x" ;;
    BACKLOG|WORKING) mark_backlog "$task_id" " " ;;
    *)
      echo "ERROR: unexpected final state '${final_state}' for task ${task_id}" >&2
      echo "       This state should not occur in current kanban versions. Investigate task: ${task_id}" >&2
      exit 1
      ;;
  esac

  # --- Post-session /tmp litter check ---
  wake_bracket_post_session "$final_state" "${_litter_snapshot_file:-}" "$task_status" "$task_id" "${_CURRENT_PROJECT:-}" "${KANBAN_ROOT:-}"

  # --- Post-task training corpus copy ---
  # Gate: per-project [training] reasoning_trace=true AND current SUBAGENT in
  # [training] training_agents list.  Empty training_agents means NO agents
  # (narrow-start default).  The global PGAI_REASONING_TRACE=1 env var is a
  # deprecated shim that force-enables capture for all projects/agents; it emits
  # a single deprecation notice per wake (mirroring the PGAI_VERBOSE_MODE shim).
  # The destination is always per-project regardless of how the gate was opened.
  # Mirrors wake_common.sh training-corpus copy block.
  if [[ "$final_state" == "DONE" ]]; then
    local _training_gate
    _training_gate="$(_resolve_training_gate "${_CURRENT_PROJECT:-}" "${SUBAGENT}")"
    # One-shot guard propagation for the deprecation notice (mirrors verbose-mode
    # pattern: subshell cannot write parent-shell vars, so we persist flag here).
    if [[ "${PGAI_REASONING_TRACE:-0}" == "1" ]]; then
        _REASONING_TRACE_DEPRECATION_WARNED=true
    fi
    if [[ "${_training_gate}" == "true" ]]; then
      local trace_source="${task_artifact_dir}/reasoning-trace.md"
      if [[ -f "$trace_source" ]]; then
        # Destination is per-project: $PGAI_PROJECT_ROOT/logs/training/<role>/<ts>-<task_id>.md
        # Resolve the project root via pp_project_root to avoid rebuilding the path manually.
        local _training_proj_root
        _training_proj_root="$(pp_project_root "${_CURRENT_PROJECT:-}" 2>/dev/null || echo "")"
        if [[ -n "$_training_proj_root" ]]; then
          local training_dest_dir="${_training_proj_root}/logs/training/${SUBAGENT}"
          local training_dest_file="${training_dest_dir}/${task_start_ts}-${task_id}.md"
          mkdir -p "$training_dest_dir"
          cp "$trace_source" "$training_dest_file"
          log "training corpus: copied reasoning-trace to ${training_dest_file}"
        else
          log "training corpus: skipped — could not resolve project root for ${_CURRENT_PROJECT:-<unknown>}"
        fi
      fi
    fi
  fi

  # --- Post-TESTER event-hook: finalize=report intake closure ---
  # When the completing TESTER task reaches DONE, run the completion census to
  # determine whether all sibling tasks sharing the same requirements path are
  # terminal.  If so, flip the originating intake item from running to done.
  # Guards: SUBAGENT==tester, final_state==DONE, WF_MANIFEST_FINALIZE==report.
  # This is the low-latency path: closure lands in the same wake tick that
  # completes the final task.  The per-wake sweep (sweep_running_intake_census)
  # is the eventual-consistency guarantee for items whose closing tick is missed.
  if [[ "$SUBAGENT" == "tester" && "$final_state" == "DONE" ]]; then
    close_intake_on_finalize_report \
      "$task_id" \
      "$task_readme" \
      "${PGAI_PROJECT_ROOT:-}" \
      "${TASKS_ROOT:-}" \
      "${WF_MANIFEST_FINALIZE:-}"
  fi

  # --- Post-PM auto-materialization ---
  if [[ "$SUBAGENT" == "pm" && "$final_state" == "DONE" ]]; then
    local pm_materialize_script
    # Use KANBAN_ROOT (set by wake_common.sh from PGAI_AGENT_KANBAN_ROOT_PATH) to locate
    # pm_materialize.py. In the live install pm-agent/ lives directly under KANBAN_ROOT;
    # in the dev tree the script is installed alongside the rest of the kanban tree, so
    # the same relative path applies after install.sh runs.
    pm_materialize_script="${KANBAN_ROOT}/pm-agent/pm_materialize.py"
    # Fallback: dev-tree invocation where script lives under team/scripts/wake/ and
    # pm-agent is two levels up at team/pm-agent/.
    if [[ ! -f "$pm_materialize_script" ]]; then
      pm_materialize_script="${SCRIPT_DIR}/../../pm-agent/pm_materialize.py"
    fi
    local plan_json=""
    while IFS= read -r -d '' _f; do
      plan_json="$_f"
      break
    done < <(find "$task_artifact_dir" -maxdepth 1 -name '*plan*.json' -print0 2>/dev/null)

    if [[ -n "$plan_json" ]]; then
      log "PM task ${task_id} done: found plan JSON at ${plan_json}; invoking pm_materialize.py"
      # Extract the requirements file path from the PM task's
      # README so we can pass --requirements-path to the materializer.  The PM
      # task README lists the requirements file as the first bullet under
      # ## Inputs (written by pm-agent.sh).  Extract the path from that line
      # so the materializer receives the authoritative path independent of
      # whether the PM plan JSON carried requirements_path.
      local _pm_task_readme="${task_dir}/README.md"
      local _req_path_arg=""
      if [[ -f "$_pm_task_readme" ]]; then
        # Read lines under ## Inputs until the next ## heading; take the first
        # non-blank "- " bullet as the requirements file path.
        local _in_inputs=false
        while IFS= read -r _line; do
          if [[ "$_line" == "## Inputs" ]]; then
            _in_inputs=true
            continue
          fi
          if [[ "$_in_inputs" == "true" ]]; then
            if [[ "$_line" == "##"* ]]; then
              break
            fi
            # Match lines like "- /absolute/path/to/file.md"
            if [[ "$_line" =~ ^-[[:space:]]+(/[^[:space:]]+\.md) ]]; then
              _req_path_arg="${BASH_REMATCH[1]}"
              break
            fi
          fi
        done < "$_pm_task_readme"
      fi
      local _req_path_flag=""
      if [[ -n "$_req_path_arg" && -f "$_req_path_arg" ]]; then
        _req_path_flag="--requirements-path ${_req_path_arg}"
        log "pm_materialize.py: passing --requirements-path ${_req_path_arg}"
      else
        log "pm_materialize.py: requirements path not extracted from PM README; materializer will use plan JSON field"
      fi
      local materialize_exit=0
      set +e
      # shellcheck disable=SC2086
      python3 "$pm_materialize_script" "$plan_json" --team-root "$PGAI_PROJECT_ROOT" ${_req_path_flag} 2>&1 | tee -a "$LOG_FILE"
      materialize_exit=${PIPESTATUS[0]}
      set -e
      # Exit-code contract for pm_materialize.py:
      #   0 — materialization succeeded (tasks created).
      #   2 — idempotent no-op: plan was already materialized (hash-marker present).
      #       This is the expected result when the PM agent materialized inside the task
      #       and the wake-script safety re-run hits the marker. Log INFO, not WARNING.
      #   non-zero (not 2) — genuine materialization failure; surface as WARNING.
      if [[ $materialize_exit -eq 0 ]]; then
        log "pm_materialize.py completed successfully for ${plan_json}"
      elif [[ $materialize_exit -eq 2 ]]; then
        log "pm_materialize.py: plan already materialized (idempotent no-op) for ${plan_json}"
      else
        log "WARNING: pm_materialize.py exited ${materialize_exit} for ${plan_json} — PM task state unchanged"
      fi
    else
      log "PM task ${task_id} done: no *plan*.json found in ${task_artifact_dir}; skipping auto-materialization"
    fi
  fi

  # --- Shell-level debug log: task completion ---
  if [[ "${_debug_gate}" == "true" ]] && [ -n "${task_debug_log:-}" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [${task_id}] wake: task finished (final_state=${final_state} agent_exit=${agent_exit})" >> "$task_debug_log"
  fi

  # --- Worktree teardown ---
  # teardown_task_worktree is idempotent; safe to call when no worktree was
  # created (variable empty) or when it was already removed.
  #
  # CODER/WRITER: teardown on all exit paths (DONE, BLOCKED, WONT-DO).
  #
  # TESTER: teardown only on DONE/WONT-DO.  On BLOCKED, retain the worktree
  # per the worktree.sh retention policy so the operator can inspect partial
  # verification state.  _canonical_dev_tree_path holds the per-project value
  # captured at function start; use it directly to avoid the env-variable
  # round-trip; use _canonical_dev_tree_path directly.
  if [[ -n "${_task_worktree_path:-}" ]]; then
    if [[ "$SUBAGENT" == "tester" && "$final_state" == "BLOCKED" ]]; then
      log "task ${task_id}: tester worktree retained for operator inspection (BLOCKED path: ${_task_worktree_path})"
    else
      teardown_task_worktree "$task_id" "${_canonical_dev_tree_path}" || true
      log "task ${task_id}: worktree torn down (final_state=${final_state})"
    fi
  fi

  # Restore canonical dev tree HEAD to the resting branch (CODER/WRITER
  # post-teardown). For CODER/WRITER tasks the env-var restore above is not
  # needed (TESTER is the only path that overrides PGAI_DEV_TREE_PATH), but
  # the git HEAD MUST be restored here so the dev tree is never left parked
  # after the worktree is torn down. The TESTER restore already ran above;
  # this block is for CODER/WRITER tasks only (guard: SUBAGENT != tester).
  # Restore is also skipped when the worktree was retained (tester BLOCKED path)
  # because in that case the dev tree may still be needed parked.
  if [[ "$SUBAGENT" != "tester" && -n "${_park_resting_branch:-}" && -n "${_canonical_dev_tree_path:-}" && -d "${_canonical_dev_tree_path}/.git" ]]; then
    set +e
    git -C "$_canonical_dev_tree_path" checkout "$_park_resting_branch" 2>/dev/null
    set -e
    log "task ${task_id}: canonical dev tree HEAD restored to ${_park_resting_branch} (post-teardown)"
  fi

  log "finished task ${task_id} with state ${final_state}"
  LAST_TASK_FINAL_STATE="$final_state"
  return 0
}

# ---------------------------------------------------------------------------
# run_project_chain <project_name> (Claude provider override)
# Process the agent queue for a single project.
# ---------------------------------------------------------------------------
run_project_chain() {
  local project_name="$1"

  _CURRENT_PROJECT="$project_name"
  TASKS_ROOT="$(pp_tasks_dir "$project_name")"
  QUEUE_DIR="${TASKS_ROOT}/queues"
  BACKLOG="$(pp_queue_path "$project_name" "$AGENT")"
  # Per-firing batch logs at kanban-root scope.
  LOG_DIR="${KANBAN_ROOT}/logs/agents"
  if ! mkdir -p "$LOG_DIR"; then
    log "FATAL: cannot create wake batch log directory at ${LOG_DIR}"
    log "       Check filesystem permissions, disk space, and read-only mounts."
    return 0
  fi

  # --- Per-project repo flock ---
  local _project_repo_id
  _project_repo_id="$(printf '%s' "$project_name" | tr '/' '_' | tr -cd '[:alnum:]_-')"
  local _project_lock_file="${LOCK_DIR}/repo-wake-${_project_repo_id}.lock"

  local _project_lock_fd
  exec {_project_lock_fd}>"$_project_lock_file"

  if ! flock -n "$_project_lock_fd"; then
    log "project ${project_name}: another agent holds repo lock, skipping this project"
    exec {_project_lock_fd}>&-
    return 0
  fi

  if [[ ! -f "$BACKLOG" ]]; then
    log "project ${project_name}: ${AGENT} backlog not found at ${BACKLOG}, skipping"
    exec {_project_lock_fd}>&-
    return 0
  fi

  # --- Per-project clock ---
  # Each project gets its own elapsed measurement so a slow project 1 cannot
  # consume the batch budget of projects 2..N.
  local _project_start_epoch
  _project_start_epoch="$(date +%s)"

  # Validate bound nesting: task <= project <= batch.
  # Emit a WARNING (non-fatal) when the invariant is violated so operators can
  # correct their config.  0 means "disabled" (unlimited) for both task and
  # project bounds.
  local _eff_project="${MAX_PROJECT_SECONDS:-${MAX_RUNTIME_SECONDS}}"
  local _eff_task="${MAX_TASK_SECONDS:-0}"
  if [[ "${_eff_project:-0}" -gt 0 && "${_eff_project}" -gt "${MAX_RUNTIME_SECONDS:-0}" && "${MAX_RUNTIME_SECONDS:-0}" -gt 0 ]]; then
    log "WARNING: project ${project_name}: MAX_PROJECT_SECONDS (${_eff_project}s) > MAX_RUNTIME_SECONDS (${MAX_RUNTIME_SECONDS}s) — per-project cap exceeds batch cap; project cap has no effect"
  fi
  if [[ "${_eff_task:-0}" -gt 0 && "${_eff_project:-0}" -gt 0 && "${_eff_task}" -gt "${_eff_project}" ]]; then
    log "WARNING: project ${project_name}: MAX_TASK_SECONDS (${_eff_task}s) > MAX_PROJECT_SECONDS (${_eff_project}s) — task timeout exceeds project cap; task bound is unreachable"
  fi

  log "project ${project_name}: starting (agent=${AGENT} max_tasks=${MAX_TASKS_PER_WAKE} max_runtime=${MAX_RUNTIME_SECONDS}s max_project=${_eff_project}s max_task=${_eff_task}s stop_on_blocked=${STOP_ON_BLOCKED})"

  # --- Per-project config load ---
  # Load per-project config so PP_dev_tree_path is exported before any task
  # processing for this project.  process_one_task reads PP_dev_tree_path for
  # worktree creation and pollution-sweep operations; without this call it
  # falls back to the global PGAI_DEV_TREE_PATH (the global dev tree),
  # which is wrong for projects that have their own dev_tree_path.
  # Non-fatal: if pp_load_config fails (missing project.cfg) log a warning and
  # continue — the global fallback keeps single-project / legacy installs working.
  if ! pp_load_config "$project_name" 2>/dev/null; then
    log "project ${project_name}: WARNING: pp_load_config failed; PP_dev_tree_path unset — falling back to global PGAI_DEV_TREE_PATH for worktree operations"
  else
    log "project ${project_name}: pp_load_config loaded; PP_dev_tree_path=${PP_dev_tree_path:-<unset>}"
  fi

  # --- Per-project dev-tree existence gate (D3) ---
  # Fires AFTER pp_load_config so PP_dev_tree_path and PP_workflow_type are
  # populated.  Release-workflow projects with a missing dev tree are skipped
  # with a per-project log line; document-workflow projects are exempt.
  # The gate lives in wake_common.sh (_check_project_dev_tree) so both wake
  # scripts use identical logic (sibling discipline).
  if ! _check_project_dev_tree "$project_name"; then
    exec {_project_lock_fd}>&-
    return 0
  fi

  reconcile_stale_active

  # --- Model preflight ---
  # Validate the configured provider model once per project chain, before any
  # task is transitioned to WORKING. A bad model fails fast with a clear
  # operator-facing message rather than producing a cryptic mid-task BLOCKED.
  # Tasks are left unmodified — the queue is not disturbed by a config error.
  # Claude provider: provider_model_preflight is a documented no-op (returns 0
  # always) because Claude CLI model validation cannot be done cheaply. See
  # wake_claude_provider.sh for rationale.
  local _agent_upper _agent_model_var _preflight_model
  _agent_upper="${AGENT^^}"
  _agent_model_var="PGAI_${_agent_upper}_MODEL"
  _preflight_model="${!_agent_model_var:-}"   # env-var model; empty = subagent default

  local _preflight_exit=0
  set +e
  provider_model_preflight "$_preflight_model" "$LOG_FILE"
  _preflight_exit=$?
  set -e

  if [[ $_preflight_exit -ne 0 ]]; then
    log "project ${project_name}: model-preflight FAILED — skipping task processing for this wake (tasks remain queued; fix model config and retry)"
    exec {_project_lock_fd}>&-
    return 0
  fi

  # --- Blocked-task project guard ---
  # Scan ALL agent queue files for this project for any BLOCKED task with
  # "## Needs Human: yes".  If any exist, skip task dispatch this wake —
  # repeated wakes would otherwise grind the remaining backlog into BLOCKED
  # one task per firing.  Discovery / PM intake still runs after the loop
  # (via the AGENT==pm block below) — only new task dispatch is paused.
  # The guard clears itself automatically once the operator resolves the
  # blocking tasks (no new flag files or sentinels needed).
  local _blocked_nh_count=0
  _blocked_nh_count=$(python3 - "$QUEUE_DIR" "$TASKS_ROOT" <<'PY'
import pathlib, re, sys

queue_dir = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
count = 0
for queue_file in sorted(queue_dir.glob("*_backlog.md")):
    try:
        text = queue_file.read_text()
    except OSError:
        continue
    for line in text.splitlines():
        m = re.match(r'^\s*-?\s*\[\s*B\s*\]\s+([A-Za-z0-9._-]+)', line)
        if not m:
            continue
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        try:
            status_text = status_file.read_text()
        except OSError:
            continue
        state_m = re.search(
            r'^##\s+State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M
        )
        if not (state_m and state_m.group(1).strip().upper() == "BLOCKED"):
            continue
        nh_m = re.search(
            r'^##\s+Needs Human\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M
        )
        if nh_m and nh_m.group(1).strip().lower() == "yes":
            count += 1
print(count)
PY
)

  local _dispatch_gated=false
  if [[ "${_blocked_nh_count:-0}" -gt 0 ]]; then
    log "project ${project_name}: ${_blocked_nh_count} BLOCKED task(s) need human attention -- not dispatching new tasks"
    _dispatch_gated=true
  fi

  local TASKS_COMPLETED=0
  local STOP_REASON=""
  if [[ "$_dispatch_gated" == "true" ]]; then
    STOP_REASON="blocked tasks need human attention (not dispatching)"
  fi

  if [[ "$_dispatch_gated" == "false" ]]; then

  while true; do
    if (( TASKS_COMPLETED >= MAX_TASKS_PER_WAKE )); then
      STOP_REASON="reached MAX_TASKS_PER_WAKE (${MAX_TASKS_PER_WAKE})"
      break
    fi

    ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
    if (( ELAPSED >= MAX_RUNTIME_SECONDS )); then
      STOP_REASON="reached MAX_RUNTIME_SECONDS (batch) (${MAX_RUNTIME_SECONDS}s, batch_elapsed=${ELAPSED}s)"
      break
    fi

    # Per-project clock check (D3): each project gets its own elapsed budget.
    local _project_elapsed=$(( $(date +%s) - _project_start_epoch ))
    if [[ "${_eff_project:-0}" -gt 0 && "${_project_elapsed}" -ge "${_eff_project}" ]]; then
      STOP_REASON="reached MAX_PROJECT_SECONDS (project) (${_eff_project}s, project_elapsed=${_project_elapsed}s)"
      break
    fi

    if [[ -f "$STOP_FILE" ]]; then
      STOP_REASON="stop file present at ${STOP_FILE}"
      rm -f "$STOP_FILE" 2>/dev/null || true
      break
    fi

    reconcile_stale_active
    recheck_waiting_tasks
    recheck_blocked_tasks
    sweep_running_intake_census

    set +e
    process_one_task
    _pot_exit=$?
    set -e
    case $_pot_exit in
      0)
        TASKS_COMPLETED=$(( TASKS_COMPLETED + 1 ))
        ;;
      1)
        STOP_REASON="no more BACKLOG tasks in ${AGENT} queue"
        break
        ;;
      2)
        if [[ "$AGENT" == "overwatch" ]]; then
          STOP_REASON="HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH"
        else
          STOP_REASON="HALT flag detected at ${TEAM_ROOT}/HALT"
        fi
        break
        ;;
      *)
        STOP_REASON="unexpected exit code ${_pot_exit} from process_one_task"
        break
        ;;
    esac

    if [[ "$LAST_TASK_FINAL_STATE" == "BLOCKED" && "$STOP_ON_BLOCKED" == "true" ]]; then
      STOP_REASON="task ended in BLOCKED state (STOP_ON_BLOCKED=true)"
      break
    fi

    log "pausing ${PAUSE_BETWEEN_TASKS}s before next task"
    sleep "$PAUSE_BETWEEN_TASKS"
  done

  recheck_waiting_tasks
  recheck_blocked_tasks
  sweep_running_intake_census

  fi  # end: if [[ "$_dispatch_gated" == "false" ]]

  if [[ "$AGENT" == "pm" && ( "$STOP_REASON" == *"no more BACKLOG tasks"* || "$_dispatch_gated" == "true" ) && "${PGAI_KANBAN_PM_MODE:-automatic}" != "manual" ]]; then
      if [[ "$_dispatch_gated" == "true" ]]; then
          log "project ${project_name}: autonomous scan: dispatch gated (blocked tasks), running discovery pipeline (intake unaffected)"
      else
          log "project ${project_name}: autonomous scan: PM backlog empty, running discovery pipeline"
      fi

      set +e
      discovery_run_pipeline "$project_name"
      set -e

      if [[ "${DISCOVERY_LAST_STATUS:-idle}" == "produced_work" ]]; then
          if [[ "$_dispatch_gated" == "true" ]]; then
              log "project ${project_name}: post-discovery iteration: blocked-task guard active, skipping task dispatch"
          elif [[ -f "${TEAM_ROOT}/HALT" ]]; then
              log "project ${project_name}: post-discovery iteration: HALT flag now present, skipping"
          else
              _post_disc_active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || echo "none")"
              if [[ -n "$_post_disc_active_rc" && "$_post_disc_active_rc" != "none" ]]; then
                  log "project ${project_name}: post-discovery iteration: Active RC=${_post_disc_active_rc}, skipping (safety gate)"
              else
                  log "project ${project_name}: post-discovery iteration: chain idle and discovery produced work, processing"
                  reconcile_stale_active
                  recheck_waiting_tasks
                  recheck_blocked_tasks
                  sweep_running_intake_census
                  set +e
                  process_one_task
                  _post_disc_exit=$?
                  set -e
                  if [[ "$_post_disc_exit" -eq 0 ]]; then
                      TASKS_COMPLETED=$(( TASKS_COMPLETED + 1 ))
                      log "project ${project_name}: post-discovery iteration: task completed"
                  else
                      log "project ${project_name}: post-discovery iteration: no task to process (process_one_task returned ${_post_disc_exit})"
                  fi
              fi
          fi
      fi
  fi

  log "project ${project_name}: done: completed=${TASKS_COMPLETED} project_elapsed=$(( $(date +%s) - _project_start_epoch ))s batch_elapsed=$(( $(date +%s) - WAKE_START_EPOCH ))s reason=\"${STOP_REASON}\""

  exec {_project_lock_fd}>&-
}

# --- Preflight: substrate checks ---
wake_common_preflight

# --- Run ---
wake_common_run
