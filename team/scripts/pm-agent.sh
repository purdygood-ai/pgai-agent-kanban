#!/usr/bin/env bash
# pm-agent.sh
# Validate a requirements document and drop a PM decomposition ticket into
# the pm_backlog queue. The PM subagent processes it when wake-batch.sh --agent=pm
# is invoked.
#
# This is a one-shot human-initiated tool. You run it when you want to queue
# a project spec for decomposition into kanban tickets.
# It does NOT start execution — that's a separate decision (run wake-batch.sh --agent=pm
# after reviewing the queue).
#
# Usage:
#   pm-agent.sh <requirements.md>                          # queue PM ticket (ceiling enforced)
#   pm-agent.sh <requirements.md> --dry-run                # preview only, don't write ticket
#   pm-agent.sh <requirements.md> --max-tasks 10           # limit task count (stored in ticket)
#   pm-agent.sh <requirements.md> --override-ceiling       # queue even if Target Version exceeds ceiling
#   pm-agent.sh --auto                                     # run discovery pipeline until idle or blocked
#                                                          # (scans bugs/, priority/, and requirements/
#                                                          #  looping until no more work or Active RC guard)
#
# Configuration:
#   PGAI_AGENT_KANBAN_ROOT_PATH  — kanban root (default: $HOME/pgai_agent_kanban)
#   PM_MAX_TASKS                         — max tasks to generate (default: 15)
#
# Optional sourced files (in kanban root):
#   bashrc  — personal shell config
#   env     — wake script tunables (PM_MAX_TASKS lives here too)

# --- Bootstrap: self-locate → source shell-env → fail loud ---
# Must happen before the first use of PGAI_AGENT_KANBAN_ROOT_PATH so the
# script runs from a fresh shell without manual pre-sourcing.  Explicit
# operator exports win via env_bootstrap.sh's idempotency guard.
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh" || exit 1

# --- Resolve kanban root ---
# PGAI_AGENT_KANBAN_ROOT_PATH is now set by env_bootstrap.sh or the operator.
TEAM_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"

if [[ ! -d "$TEAM_ROOT" ]]; then
  echo "ERROR: kanban root not found: $TEAM_ROOT" >&2
  echo "Set PGAI_AGENT_KANBAN_ROOT_PATH or run install.sh" >&2
  exit 1
fi

# --- Source optional config files ---
# This MUST happen before `set -euo pipefail`. User bashrc files commonly
# contain unset variable references, conditional aliases that return non-zero,
# or interactive-only checks that would trip strict mode and silently kill
# the script. We accept whatever the user sources, then enable strict mode
# for our own code.
[[ -f "$TEAM_ROOT/bashrc" ]] && source "$TEAM_ROOT/bashrc"
[[ -f "$TEAM_ROOT/env" ]] && source "$TEAM_ROOT/env"
# $HOME/.config/pgai-kanban.cfg is operator-local bash config; sourced as-is.
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"
# Source: kanban.cfg [chain/paths] — INI format replaces legacy config.cfg
# Source ini_parser.sh directly since project_paths.sh has not been sourced yet.
_PM_SCRIPT_DIR_TMP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "${_PM_SCRIPT_DIR_TMP}/lib/ini_parser.sh" ]] && source "${_PM_SCRIPT_DIR_TMP}/lib/ini_parser.sh"
# shellcheck source=lib/dev_tree.sh
source "${_PM_SCRIPT_DIR_TMP}/lib/dev_tree.sh"
if [[ -f "$TEAM_ROOT/kanban.cfg" ]]; then
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$TEAM_ROOT/kanban.cfg" chain pm_mode automatic)}"
fi
unset _PM_SCRIPT_DIR_TMP
export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-$(resolve_global_dev_tree)}"
# Classification (a): pm-agent only reads/writes $KANBAN_ROOT task queue files;
# no dev tree access required. Global require_dev_tree removed (D5).

# --- Now enable strict mode for our own code ---
set -euo pipefail

# --- Source project paths helper ---
KANBAN_ROOT="$TEAM_ROOT"
export KANBAN_ROOT
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$_SCRIPT_DIR/lib/project_paths.sh" ]]; then
    source "$_SCRIPT_DIR/lib/project_paths.sh"
fi

# --- Source project registry helpers (projects_cfg_list, pp_project_halted) ---
# Must be sourced AFTER project_paths.sh (it uses pp_* helpers internally).
if [[ -f "$_SCRIPT_DIR/lib/projects.sh" ]]; then
    source "$_SCRIPT_DIR/lib/projects.sh"
fi

# --- Source shared task-ID helper ---
if [[ -f "$_SCRIPT_DIR/lib/task_ids.sh" ]]; then
    source "$_SCRIPT_DIR/lib/task_ids.sh"
fi

# --- Source shared argument parser ---
# shellcheck source=lib/argparse.sh
source "${_SCRIPT_DIR}/lib/argparse.sh"

# --- Source workflow dispatcher ---
# Required for wf_load_plugin / wf_version_semantics in the ceiling check.
# Include-guarded so re-sourcing (e.g. in test shells) is safe.
if ! declare -F wf_load_plugin >/dev/null 2>&1; then
    # shellcheck source=lib/workflow.sh
    source "${_SCRIPT_DIR}/lib/workflow.sh"
fi

# --- Defaults ---
MAX_TASKS="${PM_MAX_TASKS:-15}"
DRY_RUN=false
REQUIREMENTS_FILE=""
AUTO=false
OVERRIDE_CEILING=false

# --- Parse args ---
# Value-taking flags: max-tasks.  Boolean flags: dry-run, override-ceiling, auto, help, h.
_pm_print_usage() {
  cat <<EOF
Usage: pm-agent.sh <requirements.md> [options]
       pm-agent.sh --auto

Three operating modes:
  1. Manual:       pm-agent.sh <requirements.md>
                   Queue a specific requirements doc for PM decomposition.
                   Bypasses the discovery pipeline but still enforces version
                   ceilings from project.cfg. Use --override-ceiling to bypass.
  2. One-shot:     pm-agent.sh --auto
                   Run the discovery pipeline until idle or blocked. Loops
                   through bugs/, priority/, and requirements/ in order,
                   advancing one step per iteration until nothing remains
                   to process or Active RC blocks further work. Equivalent
                   to running multiple cron iterations in one command.
  3. Continuous:   (set up cron with wake-batch.sh --agent=pm)
                   The same pipeline as --auto, but driven by cron and
                   running one iteration per cron firing over time.

Options:
  --auto               Run discovery pipeline until idle or blocked (no requirements file needed)
  --dry-run            Preview the ticket that would be created without writing anything
                       (manual mode only — has no effect with --auto)
  --max-tasks N        Maximum number of tasks for the PM agent to generate (default: 15)
                       Also accepted as --max-tasks=N.
  --override-ceiling   Bypass the version ceiling check for this invocation. A warning
                       is emitted to stderr when the ceiling is exceeded but --override-ceiling
                       is passed; the PM ticket is created anyway. Without this flag, a
                       requirements file whose Target Version exceeds the configured project
                       ceiling (max_minor, max_major, max_patch in project.cfg) causes the
                       script to exit non-zero without creating any task folder or backlog entry.
  --project <name>     Project name (required when PGAI_PROJECT_NAME is not set)
  --help, -h           Show this help

The PM agent does NOT start execution. After queueing, run:
  wake-batch.sh --agent=pm
to have the PM subagent process the ticket and decompose it into tasks.
EOF
}

argparse_parse --value-flags "max-tasks project" -- "$@"

# Emit clear error for value-taking flags given with no value.
if argparse_missing "max-tasks"; then
  echo "ERROR: --max-tasks requires a value." >&2
  exit 1
fi
if argparse_missing "project"; then
  echo "ERROR: --project requires a project name." >&2
  exit 1
fi

# Handle --help / -h.
if argparse_has "help" || argparse_has "h"; then
  _pm_print_usage; exit 0
fi

# Reject unknown flags.
for _flag in "${!ARGPARSE_FLAGS[@]}"; do
  case "$_flag" in
    max-tasks|dry-run|override-ceiling|auto|help|h|project) ;;
    *)
      echo "ERROR: Unknown option: --${_flag}" >&2
      exit 1 ;;
  esac
done
unset _flag

# Extract positional arguments (first positional = REQUIREMENTS_FILE; no others allowed).
for _pos in "${ARGPARSE_POSITIONAL[@]+"${ARGPARSE_POSITIONAL[@]}"}"; do
  case "$_pos" in
    -h) _pm_print_usage; exit 0 ;;
    -*)
      echo "ERROR: Unknown option: $_pos" >&2
      exit 1 ;;
    *)
      if [[ -z "$REQUIREMENTS_FILE" ]]; then
        REQUIREMENTS_FILE="$_pos"
      else
        echo "ERROR: Unexpected argument: $_pos" >&2
        exit 1
      fi
      ;;
  esac
done
unset _pos

# Extract boolean flags.
if argparse_has "dry-run";           then DRY_RUN=true; fi
if argparse_has "override-ceiling";  then OVERRIDE_CEILING=true; fi
if argparse_has "auto";              then AUTO=true; fi

# Extract value flags.
if argparse_has "max-tasks"; then MAX_TASKS="${ARGPARSE_FLAGS[max-tasks]}"; fi
_PM_PROJECT_ARG=""
if argparse_has "project"; then _PM_PROJECT_ARG="${ARGPARSE_FLAGS[project]}"; fi

if [[ "$AUTO" == "true" ]]; then
  # --- Auto mode: run the discovery pipeline until idle or blocked. ---
  # Each iteration advances the pipeline by one step (bugs → priority →
  # requirements → idle). We loop while any registered project still has work
  # (DISCOVERY_LAST_STATUS == produced_work) so that a single --auto invocation
  # achieves what multiple cron firings do over time, without changing the
  # pipeline's single-iteration semantics.
  #
  # Multi-project iteration:
  # Loops over all projects registered in projects.cfg via projects_cfg_list.
  # For each
  # project:
  #   - Per-project HALT (projects/<name>/HALT) skips that project.
  #   - Global HALT ($TEAM_ROOT/HALT) exits the entire auto mode.
  #   - MAX_AUTO_ITERS is enforced PER PROJECT so project A cannot starve B.
  #
  # The cron-driven path (wake-batch.sh) calls discovery_run_pipeline
  # directly and is NOT affected by this loop — it only calls the function once
  # per cron firing, as before.

  if [[ -f "$_SCRIPT_DIR/lib/discovery.sh" ]]; then
    # shellcheck source=lib/discovery.sh
    source "$_SCRIPT_DIR/lib/discovery.sh"
  else
    echo "ERROR: discovery.sh not found at $_SCRIPT_DIR/lib/discovery.sh" >&2
    exit 1
  fi

  # Safety cap per project. Applied independently so one project's activity
  # cannot exhaust the budget for other registered projects.
  MAX_AUTO_ITERS="${MAX_AUTO_ITERS:-10}"

  echo "=== PM Agent: --auto (discovery pipeline until idle or blocked) ==="

  # Global HALT check: if the global halt flag is present, exit before the loop.
  # Per-project HALT is checked inside the loop (project-by-project).
  if [[ -f "${TEAM_ROOT}/HALT" ]]; then
    echo "--- auto: global HALT flag present at ${TEAM_ROOT}/HALT; stopping ---"
    exit 0
  fi

  # Per-project iteration counters (associative array keyed by project name).
  # Tracks how many iterations we have run for each project so MAX_AUTO_ITERS
  # is enforced independently per project.
  declare -A _pm_auto_iters=()
  # Per-project done flag: set to 1 when a project reaches idle or blocked or
  # exceeds MAX_AUTO_ITERS. Once done, the project is skipped in all subsequent
  # rounds without logging redundant messages.
  declare -A _pm_auto_done=()

  # Outer loop: each pass visits all projects once. Exits when every registered
  # project is done (idle / blocked / capped) or when no project produced work
  # in the most recent pass (quiescence guard).
  while true; do
    _round_any_produced=false
    _round_all_done=true

    while IFS= read -r _pm_proj; do
      [[ -z "$_pm_proj" ]] && continue

      # Skip projects already marked done in a previous round.
      if [[ "${_pm_auto_done[$_pm_proj]:-0}" == "1" ]]; then
        continue
      fi

      _round_all_done=false

      # Global HALT re-check inside the loop: honor a HALT dropped mid-run.
      if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        echo "--- auto: global HALT detected mid-run; stopping ---"
        exit 0
      fi

      # Per-project HALT check.
      if pp_project_halted "$_pm_proj" 2>/dev/null; then
        echo "--- auto: project ${_pm_proj}: per-project HALT present, skipping ---"
        _pm_auto_done[$_pm_proj]=1
        continue
      fi

      # Per-project iteration cap.
      _pm_auto_iters[$_pm_proj]=$(( ${_pm_auto_iters[$_pm_proj]:-0} + 1 ))
      _pm_proj_iter="${_pm_auto_iters[$_pm_proj]}"

      if [[ "$_pm_proj_iter" -gt "$MAX_AUTO_ITERS" ]]; then
        echo "WARNING: project ${_pm_proj}: --auto hit MAX_AUTO_ITERS (${MAX_AUTO_ITERS}); stopping for this project to prevent infinite loop." >&2
        _pm_auto_done[$_pm_proj]=1
        continue
      fi

      echo "--- auto project=${_pm_proj} iteration=${_pm_proj_iter} ---"
      discovery_run_pipeline "$_pm_proj"

      case "${DISCOVERY_LAST_STATUS:-idle}" in
        produced_work)
          # Pipeline did something; this project may have more work next round.
          echo "--- project ${_pm_proj} iteration ${_pm_proj_iter}: produced_work ---"
          _round_any_produced=true

          # --- Post-discovery iteration ---
          # When discovery Step 3 queued a PM task (chain was idle), immediately
          # process the fresh task via wake-batch.sh --agent=pm --max-tasks 1 rather
          # than waiting for the next cron firing. Mirrors the same fix in
          # run_project_chain. Bounded to ONE task. Three safety gates:
          #   Gate 1: HALT re-check (HALT could arrive between discovery and now)
          #   Gate 2: Active RC re-check (defense-in-depth against Step 1/2 false positives)
          #   Gate 3: wake-batch.sh pm's own empty-queue guard (natural fallback)
          if [[ -f "${TEAM_ROOT}/HALT" ]]; then
            echo "--- project ${_pm_proj}: post-discovery iteration: HALT flag present, skipping ---"
          else
            _pm_post_disc_active_rc="$(_disc_get_release_field "$_pm_proj" "Active RC" 2>/dev/null || echo "none")"
            if [[ -n "$_pm_post_disc_active_rc" && "$_pm_post_disc_active_rc" != "none" ]]; then
              echo "--- project ${_pm_proj}: post-discovery iteration: Active RC=${_pm_post_disc_active_rc}, skipping (safety gate) ---"
            else
              echo "--- project ${_pm_proj}: post-discovery iteration: chain idle, processing fresh PM task ---"
              _wake_script="${_SCRIPT_DIR}/wake-batch.sh"
              if [[ -x "$_wake_script" ]]; then
                # --max-tasks 1: process exactly one task (the freshly-queued PM task).
                # STOP_REASON in that invocation will be "reached MAX_TASKS_PER_WAKE"
                # (not "no more BACKLOG tasks"), so discovery does NOT re-fire inside it.
                "$_wake_script" --agent=pm --max-tasks 1
              else
                echo "--- WARNING: wake-batch.sh not executable at ${_wake_script}; PM task queued but not processed in this firing ---" >&2
              fi
            fi
          fi
          ;;
        blocked)
          echo "--- project ${_pm_proj}: pipeline blocked (Active RC in flight or HALT); done for this project ---"
          _pm_auto_done[$_pm_proj]=1
          ;;
        idle|*)
          echo "--- project ${_pm_proj}: pipeline idle; nothing more to do ---"
          _pm_auto_done[$_pm_proj]=1
          ;;
      esac
    done < <(projects_cfg_list 2>/dev/null)

    # Exit if every project is done (all produced nothing or all halted/capped).
    if [[ "$_round_all_done" == "true" || "$_round_any_produced" == "false" ]]; then
      echo "--- auto: all projects idle or blocked; exiting ---"
      exit 0
    fi
  done
fi

# --- Manual mode: validate requirements file ---
if [[ -z "$REQUIREMENTS_FILE" ]]; then
  echo "ERROR: Requirements file is required (or use --auto for one-shot pipeline mode)." >&2
  echo "Usage: pm-agent.sh <requirements.md> [--dry-run] [--max-tasks N]" >&2
  echo "       pm-agent.sh --auto" >&2
  exit 1
fi

if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  echo "ERROR: Requirements file not found: $REQUIREMENTS_FILE" >&2
  exit 1
fi

# Resolve to absolute path so the subagent can read it from any cwd
REQUIREMENTS_FILE="$(cd "$(dirname "$REQUIREMENTS_FILE")" && pwd)/$(basename "$REQUIREMENTS_FILE")"

# --- Extract Target Version from requirements file ---
TARGET_VERSION="$(awk '/^## Target Version/{flag=1; next} /^##/{flag=0} flag && /^v[0-9]/{print; exit}' "$REQUIREMENTS_FILE" | tr -d '[:space:]')"
TARGET_VERSION="${TARGET_VERSION:-none}"

# --- Resolve target project ---
# Resolution order (highest to lowest precedence):
#   1. --project <name> CLI flag (captured in _PM_PROJECT_ARG above)
#   2. $PGAI_PROJECT_NAME environment variable (set by discovery.sh or operator)
#   3. FAIL — no silent default to the first registered project.
# Absent an explicit project, the PM agent cannot determine where to write the
# task folder, the pm_backlog marker, or the materializer --team-root.
_PM_EFFECTIVE_PROJECT="${_PM_PROJECT_ARG:-${PGAI_PROJECT_NAME:-}}"
_TARGET_PROJECT="$(pp_require_project_context "$_PM_EFFECTIVE_PROJECT")" || {
    echo "ERROR: no project specified; pass --project <name> or set PGAI_PROJECT_NAME" >&2
    exit 1
}

# --- Version ceiling check (manual mode) ---
# Reuses pp_version_within_ceiling from project_paths.sh — the same helper the
# autonomous discovery pipeline uses — so both paths enforce an identical rule.
# A Target Version of "none" (no ## Target Version field) is skipped; the PM
# agent will treat it as an auto-sentinel when it runs.
#
# Version semantics gate (mirrors the equivalent gate in discovery eligibility):
# Ceiling checks are semver consumption checkpoints — they are meaningless for
# label or none projects where versions are names, not numbers.  Load the
# workflow plugin to query the project's declared version_semantics; skip the
# entire ceiling block when the semantics are not "semver".
if [[ "$TARGET_VERSION" != "none" ]]; then
    # Resolve version_semantics for this project's workflow type.
    _pm_wf_type="$(pp_workflow_type "$_TARGET_PROJECT" 2>/dev/null)" || _pm_wf_type="release"
    _pm_wf_load_exit=0
    set +e
    wf_load_plugin "$_pm_wf_type"
    _pm_wf_load_exit=$?
    set -e
    if [[ $_pm_wf_load_exit -ne 0 ]]; then
        # Plugin load failed — default to semver (safe: enforces ceiling rather
        # than skipping it; matches the analogous fallback in discovery.sh).
        _pm_version_semantics="semver"
    else
        _pm_version_semantics="$(wf_version_semantics)"
    fi

    if [[ "$_pm_version_semantics" != "semver" ]]; then
        # Label or none project: versions are names, not numbers.
        # Ceiling arithmetic does not apply — skip the ceiling block entirely.
        true
    elif ! pp_version_within_ceiling "$_TARGET_PROJECT" "$TARGET_VERSION" 2>/dev/null; then
        # Semver project whose Target Version exceeds the configured ceiling.
        # Determine which component was violated for a precise error message.
        _ceil_max_major="$(pp_max_major "$_TARGET_PROJECT" 2>/dev/null)" || _ceil_max_major=""
        _ceil_max_minor="$(pp_max_minor "$_TARGET_PROJECT" 2>/dev/null)" || _ceil_max_minor=""
        _ceil_max_patch="$(pp_max_patch "$_TARGET_PROJECT" 2>/dev/null)" || _ceil_max_patch=""

        # Extract and validate version components before arithmetic.
        # A malformed semver on a semver project (e.g. garbage string) must
        # produce a named parse error, never an unbound-variable crash.
        _v_clean="${TARGET_VERSION#v}"; _v_clean="${_v_clean#V}"
        _v_major="${_v_clean%%.*}"
        _v_rest="${_v_clean#*.}"; _v_minor="${_v_rest%%.*}"; _v_patch="${_v_rest#*.}"

        # Guard: only use arithmetic when each component is a non-negative integer.
        # A component that contains non-digit characters (e.g. a hyphen from a
        # label suffix) is not a valid semver field; skip the per-component detail
        # and fall through to the generic ceiling message instead of crashing.
        _ceil_detail=""
        if [[ "$_v_major" =~ ^[0-9]+$ && "$_v_minor" =~ ^[0-9]+$ && "$_v_patch" =~ ^[0-9]+$ ]]; then
            if [[ -n "$_ceil_max_major" ]] && (( _v_major > _ceil_max_major )); then
                _ceil_detail="major version ${_v_major} exceeds max_major=${_ceil_max_major}"
            elif [[ -n "$_ceil_max_minor" ]] && (( _v_minor > _ceil_max_minor )); then
                _ceil_detail="minor version ${_v_minor} exceeds max_minor=${_ceil_max_minor}"
            elif [[ -n "$_ceil_max_patch" ]] && (( _v_patch > _ceil_max_patch )); then
                _ceil_detail="patch version ${_v_patch} exceeds max_patch=${_ceil_max_patch}"
            else
                _ceil_detail="version exceeds configured project ceiling"
            fi
        else
            _ceil_detail="version '${TARGET_VERSION}' could not be parsed as semver (expected vX.Y.Z)"
        fi

        if [[ "$OVERRIDE_CEILING" == "true" ]]; then
            echo "WARNING: Target Version ${TARGET_VERSION} ceiling check failed: ${_ceil_detail}" >&2
            echo "WARNING: --override-ceiling was passed; proceeding despite ceiling violation." >&2
        else
            echo "ERROR: Target Version ${TARGET_VERSION} ceiling check failed: ${_ceil_detail}" >&2
            echo "       To override: rerun with --override-ceiling" >&2
            exit 1
        fi
    fi
fi

# --- Validate dependencies ---
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }

# --- Generate ticket ID ---
# Format: PM-YYYYMMDD-NNN-decompose-<short-name>  (new format, no PARTICIPANT prefix)
# short-name is derived from the requirements filename (no extension, kebab-case).
DATE_STAMP="$(date +%Y%m%d)"
REQ_BASENAME="$(basename "$REQUIREMENTS_FILE")"
REQ_NOEXT="${REQ_BASENAME%.*}"
# Convert to lowercase kebab-case, strip non-alphanumeric except hyphens, truncate
SHORT_NAME="$(echo "$REQ_NOEXT" | tr '[:upper:]' '[:lower:]' | tr '_' '-' | tr -cs 'a-z0-9-' '-' | sed 's/--*/-/g; s/^-//; s/-$//' | cut -c1-30)"

# Find next available sequence number using the shared task-ID helper.
# The helper scans both old CLAUDE-PM-<date>-* folders and new PM-<date>-* folders
# so that consecutive PM tasks on the same date receive distinct sequence numbers.
TASKS_DIR="$(pp_tasks_dir "$_TARGET_PROJECT")"
mkdir -p "$TASKS_DIR"
TASK_ID="$(kanban_task_id "$TASKS_DIR" "PM" "$DATE_STAMP" "decompose-${SHORT_NAME}")"
TASK_DIR="${TASKS_DIR}/${TASK_ID}"
PM_BACKLOG="$(pp_queue_path "$_TARGET_PROJECT" "pm")"

# Resolve the project root path once so we can bake it into the PM task's
# README template.  Using the literal path (rather than \$PGAI_PROJECT_ROOT)
# ensures the PM subagent always runs the materializer against the correct
# project even if it is processed in a context where PGAI_PROJECT_ROOT has
# been reset (e.g. processed via another registered project's own chain
# because the PM task was found there).
_BAKED_PROJECT_ROOT="$(pp_project_root "$_TARGET_PROJECT")"

# --- Set content variables ---
TICKET_GOAL="Decompose this requirements file into kanban tickets"
TICKET_INPUTS="- ${REQUIREMENTS_FILE}"
TICKET_NOTES="Queued by pm-agent.sh on $(date -Iseconds). Requirements file: ${REQUIREMENTS_FILE}"

# --- Preview in dry-run mode ---
if [[ "$DRY_RUN" == "true" ]]; then
  echo "=== DRY-RUN: PM Ticket Preview ==="
  echo ""
  echo "Task ID    : $TASK_ID"
  echo "Task Dir   : $TASK_DIR"
  echo "Requirements: $REQUIREMENTS_FILE"
  echo "Max Tasks  : $MAX_TASKS"
  echo "PM Backlog : $PM_BACKLOG"
  echo ""
  echo "README.md would contain:"
  echo "---"
  cat <<EOF
# Task: Decompose requirements into kanban tickets

## Task ID
${TASK_ID}

## Owner
CLAUDE

## Role
PM

## Assigned Agent
PM

## Working Directory
none

## Git Repo
none

## Source Branch
none

## Feature Branch
none

## Goal
${TICKET_GOAL}

## Inputs
${TICKET_INPUTS}

## Context Paths

## Required Output
Produce a JSON plan file at \${TASK_DIR}/artifacts/plan.json with the structure described in team/roles/PM.md "Output Format" section. After writing the JSON, you MUST invoke the materializer directly via Bash:
  python3 \$PGAI_AGENT_KANBAN_ROOT_PATH/pm-agent/pm_materialize.py --team-root ${_BAKED_PROJECT_ROOT} --requirements-path ${REQUIREMENTS_FILE} \${TASK_DIR}/artifacts/plan.json
The materializer will create the task folders in \$PGAI_PROJECT_ROOT/tasks/ and append entries to the appropriate per-agent queue files (writer_backlog.md, coder_backlog.md, etc.) AND inject CM bookends (cm-open-rc, cm-release) and TESTER tasks for release workflows. PM does NOT write task folders or queue files directly. PM does NOT inject bookends — that's the materializer's job. See team/roles/PM.md for full guidance.

## Release Version
${TARGET_VERSION}

## Constraints
- Maximum tasks to generate: ${MAX_TASKS}

## Acceptance Criteria
- [ ] Tasks have been decomposed from the requirements file
- [ ] Each task has a README.md and status.md in BACKLOG state
- [ ] Tasks have been appended to the appropriate backlog queue

## Prerequisites
none

## Notes
${TICKET_NOTES}
EOF
  echo "---"
  echo ""
  echo "Dry run complete. No files written."
  echo "To queue the ticket: run pm-agent.sh without --dry-run"
  exit 0
fi

# --- Create the PM ticket ---
echo "=== PM Agent: Queueing decomposition ticket ==="
echo ""
echo "Task ID     : $TASK_ID"
echo "Requirements: $REQUIREMENTS_FILE"
echo "Max Tasks   : $MAX_TASKS"
echo ""

mkdir -p "${TASK_DIR}/artifacts"
mkdir -p "${TASK_DIR}/logs"

# Write README.md
cat > "${TASK_DIR}/README.md" <<EOF
# Task: Decompose requirements into kanban tickets

## Task ID
${TASK_ID}

## Owner
CLAUDE

## Role
PM

## Assigned Agent
PM

## Working Directory
none

## Git Repo
none

## Source Branch
none

## Feature Branch
none

## Goal
${TICKET_GOAL}

## Inputs
${TICKET_INPUTS}

## Context Paths

## Required Output
Produce a JSON plan file at \${TASK_DIR}/artifacts/plan.json with the structure described in team/roles/PM.md "Output Format" section. After writing the JSON, you MUST invoke the materializer directly via Bash:
  python3 \$PGAI_AGENT_KANBAN_ROOT_PATH/pm-agent/pm_materialize.py --team-root ${_BAKED_PROJECT_ROOT} --requirements-path ${REQUIREMENTS_FILE} \${TASK_DIR}/artifacts/plan.json
The materializer will create the task folders in \$PGAI_PROJECT_ROOT/tasks/ and append entries to the appropriate per-agent queue files (writer_backlog.md, coder_backlog.md, etc.) AND inject CM bookends (cm-open-rc, cm-release) and TESTER tasks for release workflows. PM does NOT write task folders or queue files directly. PM does NOT inject bookends — that's the materializer's job. See team/roles/PM.md for full guidance.

## Release Version
${TARGET_VERSION}

## Constraints
- Maximum tasks to generate: ${MAX_TASKS}

## Acceptance Criteria
- [ ] Tasks have been decomposed from the requirements file
- [ ] Each task has a README.md and status.md in BACKLOG state
- [ ] Tasks have been appended to the appropriate backlog queue

## Prerequisites
none

## Notes
${TICKET_NOTES}
EOF

# Write initial status.md
cat > "${TASK_DIR}/status.md" <<EOF
# Status

## Task
${TASK_ID}

## Participant
CLAUDE

## Role
PM

## State
BACKLOG

## Summary
Task created by pm-agent.sh. Waiting for PM subagent to pull from backlog and begin work.

## Artifacts
none

## Blockers
none

## Blocked By Agent
none

## Blocked Reason
none

## Needs Human
no

## Next Recommended Step
Run wake-batch.sh --agent=pm to have the PM subagent process this ticket.

## Instruction Conflicts
none
EOF

# Ensure pm_backlog.md exists (it may be empty or not exist yet)
mkdir -p "$(dirname "$PM_BACKLOG")"
if [[ ! -f "$PM_BACKLOG" ]]; then
  cat > "$PM_BACKLOG" <<EOF
# PM Agent Backlog

Tasks for the pm subagent (project decomposition).
EOF
fi

# Append the task ID to the pm_backlog
echo "- [ ] ${TASK_ID}" >> "$PM_BACKLOG"

echo "PM ticket created:"
echo "  Folder  : $TASK_DIR"
echo "  Backlog : $PM_BACKLOG"
echo ""
echo "To process it, run:"
echo "  wake-batch.sh --agent=pm"
echo ""
echo "=== PM Agent Complete ==="
