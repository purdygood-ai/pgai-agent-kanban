#!/usr/bin/env bash
# team/scripts/lib/wake_common.sh
# Shared substrate logic for all wake scripts (wake-claude.sh, wake-codex.sh, etc.).
#
# Source this file AFTER sourcing the provider-specific lib (which defines
# provider_invoke_agent) and AFTER argument parsing and provider checking are
# complete in the calling wake script.
#
# Contract: the following variables MUST be set by the caller before sourcing:
#   AGENT       — the agent name (pm, coder, writer, etc.)
#   SLEEP       — seconds to sleep before work (0 or more)
#   _CLI_MAX_TASKS — optional CLI override for MAX_TASKS_PER_WAKE (may be "")
#   TEAM_ROOT   — resolved kanban root path
#   SCRIPT_DIR  — absolute path to the directory containing the wake script
#
# Contract: the following function MUST be defined by the caller (provider lib):
#   provider_invoke_agent <prompt> <selected_model> <model_source>
#                         <task_id> <task_artifact_dir> <log_file>
#       Called by process_one_task to invoke the LLM provider CLI.
#       Sets PROVIDER_AGENT_EXIT_CODE to the exit code of the CLI invocation.
#
# This file sources project_paths.sh, projects.sh, task_ids.sh, discovery.sh,
# semver.sh, and other required libs. It does NOT source token_capture.sh or
# any other provider-specific lib — those are the provider lib's responsibility.
#
# Exported functions (public API for wake scripts):
#   wake_common_run — main entry point; sets up env, runs multi-project loop.
#
# Provider-neutral substrate shared by wake_claude_provider.sh,
# wake_codex_provider.sh, and future provider scripts.

# --- Env-var name migration guard ---
# PGAI_AGENT_KANBAN_ROOT_PATH is the canonical name. The legacy shim
# Only the canonical name PGAI_AGENT_KANBAN_ROOT_PATH is honored.
# name is honored.

# --- Resolve script and repo team directories (for autonomous scan helpers) ---
# SCRIPT_DIR is set by the caller (the wake script itself).
# REPO_TEAM_DIR: the team/ directory in the repository checkout, one level up.
REPO_TEAM_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# KANBAN_ROOT is required by lib/project_paths.sh pp_* helpers.
export KANBAN_ROOT="$TEAM_ROOT"

# --- Source project-path helpers (pp_* functions) ---
# Must be sourced before any pp_* call.
# shellcheck source=scripts/lib/project_paths.sh
source "${SCRIPT_DIR}/../lib/project_paths.sh"

# --- Source project registry helpers (projects_cfg_list, etc.) ---
# Must be sourced AFTER project_paths.sh (it uses pp_* helpers internally).
# shellcheck source=scripts/lib/projects.sh
source "${SCRIPT_DIR}/../lib/projects.sh"

# --- Source shared task-ID helper ---
# shellcheck source=scripts/lib/task_ids.sh
source "${SCRIPT_DIR}/../lib/task_ids.sh"

# --- Source discovery pipeline library ---
# shellcheck source=scripts/lib/discovery.sh
source "${SCRIPT_DIR}/../lib/discovery.sh"

# --- Source workflow-type dispatcher (wf_load_plugin and wf_* surface) ---
# Must be sourced after discovery.sh; provides wf_load_plugin and the uniform
# wf_* call surface used by CODER/WRITER task dispatch to select worktree
# behavior by capability rather than by workflow_type string comparison.
# shellcheck source=scripts/lib/workflow.sh
source "${SCRIPT_DIR}/../lib/workflow.sh"

# --- Source config loader (single source of truth for kanban.cfg keys) ---
# config_loader.sh defines load_config and config_get.  It self-bootstraps
# ini_parser.sh if read_ini is not yet available.  Must be sourced before
# any kanban.cfg read (load_config is called below after secrets are sourced).
# shellcheck source=scripts/lib/config_loader.sh
source "${SCRIPT_DIR}/../lib/config_loader.sh"

# --- Source dev_tree helper (resolve_global_dev_tree / require_dev_tree) ---
# shellcheck source=scripts/lib/dev_tree.sh
source "${SCRIPT_DIR}/../lib/dev_tree.sh"

# PGAI_PROJECT_ROOT / PGAI_PROJECT_NAME initial values.
# These are pre-loop placeholders overridden per-project in wake_common_run.
# Resolution: honor PGAI_PROJECT_NAME if already set in the environment
# (operator-explicit or single-project install that set it upstream); otherwise
# leave empty — wake_common_run's multi-project loop sets both per-project via
# pp_project_root() before any task work begins.  Never silently fall back to
# the first registered project: on a multi-project install that would silently
# operate on the wrong project.
if [[ -n "${PGAI_PROJECT_NAME:-}" ]]; then
    export PGAI_PROJECT_ROOT="$(pp_project_root "${PGAI_PROJECT_NAME}" 2>/dev/null || true)"
    export PGAI_PROJECT_NAME="${PGAI_PROJECT_NAME}"
else
    export PGAI_PROJECT_ROOT=""
    export PGAI_PROJECT_NAME=""
fi

# --- Bootstrap: delegate root absolutization to env_bootstrap.sh, then source
# shell-env for PATH/venv side effects.
#
# Why pre-export before sourcing env_bootstrap.sh:
#   env_bootstrap.sh walks up from BASH_SOURCE[1] to derive the kanban root.
#   When wake_common.sh is sourced (a lib file), BASH_SOURCE[1] resolves to
#   scripts/lib/wake_common.sh — pointing to the lib directory, not the root.
#   Sourcing env_bootstrap.sh without pre-setting the env var would cause the
#   walk to land at scripts/lib/ (not a recognized scripts-layer dir), produce
#   a wrong candidate, and fail loud — breaking cron's protected entry point.
#
#   By pre-exporting from TEAM_ROOT (already resolved by the caller via
#   ${PGAI_AGENT_KANBAN_ROOT_PATH:-$HOME/pgai_agent_kanban}), we trigger
#   env_bootstrap.sh's idempotency guard: it absolutizes the pre-set value and
#   returns 0 immediately without walking BASH_SOURCE or sourcing shell-env.
#   The result: env_bootstrap.sh handles absolutization for all its callers in
#   a single consistent path; wake gets the same treatment without a separate
#   implementation.
#
#   Operator-env-wins is preserved: if PGAI_AGENT_KANBAN_ROOT_PATH was already
#   set by the operator (cron, explicit export), the ${:-} below is a no-op and
#   env_bootstrap.sh absolutizes the operator's value; TEAM_ROOT carries the
#   default and never overwrites the operator's choice.
export PGAI_AGENT_KANBAN_ROOT_PATH="${PGAI_AGENT_KANBAN_ROOT_PATH:-$TEAM_ROOT}"
# shellcheck source=scripts/lib/env_bootstrap.sh
source "${SCRIPT_DIR}/../lib/env_bootstrap.sh"

# --- Source optional shell-env for PATH and Python venv activation ---
# Root resolution is now env_bootstrap.sh's responsibility (above).
# Shell-env's remaining side effects — PATH adjustments and venv activation —
# still need a direct source here.  Optional: wake scripts work without it
# when cron inherits a usable PATH and shell-env is absent.
[[ -f "$TEAM_ROOT/shell-env" ]] && source "$TEAM_ROOT/shell-env"

# --- Python >= 3.12 guard (after shell-env so post-venv interpreter is checked) ---
_py_ver=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo "0.0.0")
_py_maj=$(python3 -c "import sys; print(sys.version_info.major)" 2>/dev/null || echo "0")
_py_min=$(python3 -c "import sys; print(sys.version_info.minor)" 2>/dev/null || echo "0")
if [[ "$_py_maj" -lt 3 ]] || [[ "$_py_maj" -eq 3 && "$_py_min" -lt 12 ]]; then
    echo "FATAL: pgai-agent-kanban requires Python 3.12+. Found: ${_py_ver}." >&2
    exit 1
fi
unset _py_ver _py_maj _py_min

# --- Read ai_auth_mode from kanban.cfg BEFORE sourcing secrets ---
# This is the ONE legitimate pre-loader bootstrap read_ini call in this file.
# AI_AUTH_MODE must be set before secrets is sourced so the secrets file can
# branch on its value (e.g. to skip or set ANTHROPIC_API_KEY).
# load_config (called below, after secrets) will re-export AI_AUTH_MODE with
# the same value; the ${AI_AUTH_MODE:-...} precedence rule means this earlier
# assignment wins and load_config's export is a no-op.
# read_ini is available here because project_paths.sh sources ini_parser.sh.
# Default: oauth — backward-compatible; existing installs using `claude login`
# continue to work without modification.
export AI_AUTH_MODE="${AI_AUTH_MODE:-$(read_ini "${TEAM_ROOT}/kanban.cfg" providers ai_auth_mode oauth)}"

# --- Source optional secrets (OAuth tokens, API keys) ---
# secrets is a dedicated file for credentials, gitignored. Sourced after
# shell-env so credentials are available to any tool launched downstream.
# Optional — wake scripts work without it if credentials are provided
# through some other mechanism (e.g. ~/.claude/credentials from claude login).
[[ -f "$TEAM_ROOT/secrets" ]] && source "$TEAM_ROOT/secrets"

# --- Source operator's personal INI overrides (optional, rarely used) ---
[[ -f "$HOME/.config/pgai-kanban.cfg" ]] && source "$HOME/.config/pgai-kanban.cfg"

# --- Load kanban.cfg into env vars via config_loader.sh ---
# load_config reads every kanban.cfg key once, validates keys, applies OPTIONAL
# defaults from the registry, and exports canonical env vars.
# It supersedes the former load_kanban_cfg_to_env() function.
#
# Precedence preserved: env var already set > config value > registry default.
# The pre-loader AI_AUTH_MODE read above is honored by load_config's
# ${AI_AUTH_MODE:-...} export pattern.
#
# dev_tree_path is OPTIONAL: load_config exports PGAI_DEV_TREE_PATH
# as the config value (or empty when unset).  Per-project existence checks are
# done in run_project_chain via _check_project_dev_tree (after pp_load_config).
# There is no global dev-tree existence gate at wake entry (D3).
if ! load_config "${TEAM_ROOT}/kanban.cfg"; then
    echo "ERROR: load_config failed for ${TEAM_ROOT}/kanban.cfg." >&2
    exit 1
fi
# --- Now enable strict mode for our own code ---
set -euo pipefail

# Deprecation warning state — one-shot per wake-batch firing.
_VERBOSE_MODE_DEPRECATION_WARNED=false
_REASONING_TRACE_DEPRECATION_WARNED=false

# --- Clean exit handling ---
# Reap any lingering child processes on exit.
cleanup_on_exit() {
  local exit_code=$?
  jobs -p 2>/dev/null | xargs -r kill 2>/dev/null || true
  wait 2>/dev/null || true
  exit $exit_code
}
trap cleanup_on_exit EXIT
trap cleanup_on_exit SIGTERM SIGINT

# --- Tunables ---
MAX_TASKS_PER_WAKE="${MAX_TASKS_PER_WAKE:-5}"
MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-14400}"
PAUSE_BETWEEN_TASKS="${PAUSE_BETWEEN_TASKS:-5}"
STOP_ON_BLOCKED="${STOP_ON_BLOCKED:-true}"
STOP_FILE="${STOP_FILE:-${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop}"
MAX_TASK_SECONDS="${MAX_TASK_SECONDS:-5400}"
MAX_PROJECT_SECONDS="${MAX_PROJECT_SECONDS:-${MAX_RUNTIME_SECONDS}}"
KILL_GRACE_SECONDS="${KILL_GRACE_SECONDS:-30}"

# CLI options override environment / config-file tunables
[[ -n "${_CLI_MAX_TASKS:-}" ]] && MAX_TASKS_PER_WAKE="$_CLI_MAX_TASKS"

mkdir -p "$(dirname "$STOP_FILE")"

# --- Paths (initial values; overridden per-project by run_project_chain) ---
# Resolution: honor PGAI_PROJECT_NAME if already set in the environment;
# otherwise leave empty — run_project_chain sets _CURRENT_PROJECT, TASKS_ROOT,
# QUEUE_DIR, and BACKLOG per-project before process_one_task is called.
# Never silently fall back to the first registered project on a multi-project
# install: that picks the wrong project's queue.
if [[ -n "${PGAI_PROJECT_NAME:-}" ]]; then
    _CURRENT_PROJECT="${PGAI_PROJECT_NAME}"
    TASKS_ROOT="$(pp_tasks_dir "$_CURRENT_PROJECT")"
    QUEUE_DIR="${TASKS_ROOT}/queues"
    BACKLOG="$(pp_queue_path "$_CURRENT_PROJECT" "$AGENT")"
else
    _CURRENT_PROJECT=""
    TASKS_ROOT=""
    QUEUE_DIR=""
    BACKLOG=""
fi
# Per-firing batch logs live at $KANBAN_ROOT/logs/agents/.
# They are cron-firing-scope, not project-scope — a single wake firing may
# iterate multiple projects, so the artifact belongs at kanban root alongside
# cron-<agent>.log. Per-task progress logs remain at tasks/<task_id>/logs/.
LOG_DIR="${KANBAN_ROOT}/logs/agents"
LOG_FILE="${LOG_DIR}/${AGENT}-batch-$(date +%Y%m%d-%H%M%S).log"

# --- Lock paths ---
LOCK_DIR="${TEAM_ROOT}/locks"

mkdir -p "$LOG_DIR" "$LOCK_DIR"

WAKE_START_EPOCH=$(date +%s)

log() {
  echo "[$(date -Iseconds)] wake(${AGENT}): $*" | tee -a "$LOG_FILE"
}

# ---------------------------------------------------------------------------
# _resolve_debug_gate <project> <agent>
# Compute the single debug-enable boolean for a (project, agent) pair.
# Echoes "true" or "false".
#
# Resolution:
#   1. PGAI_VERBOSE_MODE=1 (legacy shim): true for all projects; emits a
#      one-shot deprecation warning on the first call per wake-batch firing.
#   2. pp_verbose_mode true AND agent in pp_verbose_agents: true.
#   3. Everything else: false.
# ---------------------------------------------------------------------------
_resolve_debug_gate() {
    local project="${1:-}"
    local agent="${2:-}"

    if [[ "${PGAI_VERBOSE_MODE:-0}" == "1" ]]; then
        # Emit deprecation warning to stderr only (never via log/tee) so that
        # stdout stays clean when this function is called via command substitution.
        # The _VERBOSE_MODE_DEPRECATION_WARNED guard suppresses repeat warnings when
        # the parent shell has already set the flag (see one-shot propagation in
        # process_one_task).  Assignments inside this subshell do not propagate back
        # to the parent shell, so the caller is responsible for setting the guard
        # after the first $(...) call returns (see call site in process_one_task).
        if [[ "${_VERBOSE_MODE_DEPRECATION_WARNED}" != "true" ]]; then
            local _warn_msg="[$(date -Iseconds)] wake(${AGENT:-wake}): WARNING: PGAI_VERBOSE_MODE env var is deprecated; move per-project debug control to project.cfg [debug] verbose_mode"
            # Write to stderr only — never via log()/tee — so stdout stays clean.
            # Also append to LOG_FILE directly so the warning still lands in the log.
            echo "${_warn_msg}" >&2
            echo "${_warn_msg}" >> "${LOG_FILE:-/dev/null}"
        fi
        echo "true"
        return 0
    fi

    [[ -n "$project" ]] || { echo "false"; return 0; }

    local vm
    vm="$(pp_verbose_mode "$project" 2>/dev/null || echo "false")"
    [[ "$vm" == "true" ]] || { echo "false"; return 0; }

    local va
    va="$(pp_verbose_agents "$project" 2>/dev/null || echo "pm,coder,writer,tester,cm")"

    if printf '%s\n' "$va" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -qx "$agent"; then
        echo "true"
        return 0
    fi

    echo "false"
}

# ---------------------------------------------------------------------------
# _resolve_training_gate <project> <agent>
# Compute the single training-capture boolean for a (project, agent) pair.
# Echoes "true" or "false".
#
# Resolution:
#   1. PGAI_REASONING_TRACE=1 (legacy shim): true for all projects/agents;
#      emits a one-shot deprecation warning on the first call per wake-batch.
#   2. pp_reasoning_trace true AND agent in pp_training_agents (non-empty list): true.
#   3. Everything else: false (includes empty pp_training_agents — narrow-start).
# ---------------------------------------------------------------------------
_resolve_training_gate() {
    local project="${1:-}"
    local agent="${2:-}"

    if [[ "${PGAI_REASONING_TRACE:-0}" == "1" ]]; then
        # Emit deprecation warning to stderr only (never via log/tee) so that
        # stdout stays clean when this function is called via command substitution.
        # The _REASONING_TRACE_DEPRECATION_WARNED guard suppresses repeat warnings
        # when the parent shell has already set the flag (see one-shot propagation
        # in process_one_task).  Assignments inside this subshell do not propagate
        # back to the parent shell, so the caller is responsible for setting the
        # guard after the first $(...) call returns (see call site in process_one_task).
        if [[ "${_REASONING_TRACE_DEPRECATION_WARNED}" != "true" ]]; then
            local _warn_msg="[$(date -Iseconds)] wake(${AGENT:-wake}): WARNING: PGAI_REASONING_TRACE env var is deprecated; move per-project training control to project.cfg [training] reasoning_trace and training_agents"
            # Write to stderr only — never via log()/tee — so stdout stays clean.
            # Also append to LOG_FILE directly so the warning still lands in the log.
            echo "${_warn_msg}" >&2
            echo "${_warn_msg}" >> "${LOG_FILE:-/dev/null}"
        fi
        echo "true"
        return 0
    fi

    [[ -n "$project" ]] || { echo "false"; return 0; }

    local rt
    rt="$(pp_reasoning_trace "$project" 2>/dev/null || echo "false")"
    [[ "$rt" == "true" ]] || { echo "false"; return 0; }

    local ta
    ta="$(pp_training_agents "$project" 2>/dev/null || echo "")"

    # Empty training_agents means NO agents enabled (narrow-start default).
    [[ -n "$ta" ]] || { echo "false"; return 0; }

    if printf '%s\n' "$ta" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//' | grep -qx "$agent"; then
        echo "true"
        return 0
    fi

    echo "false"
}

require_file() {
  local f="$1"
  [[ -f "$f" ]] || { log "missing file: $f"; exit 1; }
}

# --- Source semver helpers (needed for autonomous PM scan) ---
source "${SCRIPT_DIR}/../lib/semver.sh"

# ---------------------------------------------------------------------------
# get_release_state_field FIELD
# Read a named ## FIELD block from release-state.md and print its content.
# ---------------------------------------------------------------------------
get_release_state_field() {
    local field="$1"
    local release_state
    release_state="$(pp_release_state "${_CURRENT_PROJECT}")"
    python3 - "$release_state" "$field" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
field = sys.argv[2]
m = re.search(rf'^## {re.escape(field)}\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if not m:
    raise SystemExit(1)
print(m.group(1).strip())
PY
}

# ---------------------------------------------------------------------------
# scan_requirements_queue
# Scan projects/<name>/requirements/ NON-RECURSIVELY for eligible .md files.
# ---------------------------------------------------------------------------
scan_requirements_queue() {
    local active_rc last_released requirements_dir eligible sorted lowest_ver
    local count_at_lowest f ver

    active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "autonomous scan: Active RC is ${active_rc}, skipping requirements queue (RC in flight)"
        return 1
    fi

    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "autonomous scan: HALT flag present, skipping requirements queue"
        return 1
    fi

    last_released="$(get_release_state_field "Last Released" 2>/dev/null || true)"
    if [[ -z "$last_released" || "$last_released" == "none" ]]; then
        log "autonomous scan: cannot determine Last Released version from release-state.md"
        return 1
    fi

    requirements_dir="$(pp_requirements_dir "${_CURRENT_PROJECT}")"
    if [[ ! -d "$requirements_dir" ]]; then
        return 1
    fi

    eligible=()
    for f in "$requirements_dir"/*.md; do
        [[ -f "$f" ]] || continue
        ver="$(semver_from_filename "$f")"
        [[ -z "$ver" ]] && continue
        if semver_gt "$ver" "$last_released"; then
            eligible+=("${ver}"$'\t'"${f}")
        fi
    done

    if [[ ${#eligible[@]} -eq 0 ]]; then
        return 1
    fi

    sorted="$(printf '%s\n' "${eligible[@]}" | sort -t$'\t' -k1,1V -k2,2)"
    lowest_ver="$(printf '%s\n' "$sorted" | head -n1 | cut -f1)"

    count_at_lowest="$(printf '%s\n' "$sorted" | awk -F$'\t' -v v="$lowest_ver" '$1 == v {c++} END {print c+0}')"
    if [[ "$count_at_lowest" -gt 1 ]]; then
        log "autonomous scan: WARNING: ${count_at_lowest} requirements docs at version ${lowest_ver}; picking first lexically"
    fi

    printf '%s\n' "$sorted" | head -n1 | cut -f2
    return 0
}

# ---------------------------------------------------------------------------
# scan_and_bundle_bugs
# ---------------------------------------------------------------------------
scan_and_bundle_bugs() {
    local active_rc last_released bugs_dir bug_backlog priority_dir
    local today patch_ver req_doc_name req_doc_path
    local f slug summary line already_bundled

    active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "autonomous scan (bugs): Active RC is ${active_rc}, skipping bug scan"
        return 1
    fi

    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "autonomous scan (bugs): HALT flag present, skipping bug scan"
        return 1
    fi

    last_released="$(get_release_state_field "Last Released" 2>/dev/null || true)"
    if [[ -z "$last_released" || "$last_released" == "none" ]]; then
        log "autonomous scan (bugs): cannot determine Last Released from release-state.md, skipping"
        return 1
    fi

    bugs_dir="$(pp_bugs_dir "${_CURRENT_PROJECT}")"
    bug_backlog="$(pp_queue_path "${_CURRENT_PROJECT}" "bug")"
    priority_dir="$(pp_requirements_dir "${_CURRENT_PROJECT}")/priority"

    local bundled_ids=()
    if [[ -f "$bug_backlog" ]]; then
        while IFS= read -r line; do
            local bid
            bid="$(printf '%s' "$line" | grep -oE '\[x\]\s+BUG-[0-9]+-[A-Za-z0-9-]+' | grep -oE 'BUG-[0-9]+-[A-Za-z0-9-]+' | head -n1 || true)"
            [[ -n "$bid" ]] && bundled_ids+=("$bid")
        done < "$bug_backlog"
    fi

    local unhandled_slugs=()
    local unhandled_files=()
    local unhandled_summaries=()

    for f in "$bugs_dir"/BUG-*.md; do
        [[ -f "$f" ]] || continue
        local basename_f
        basename_f="$(basename "$f")"
        [[ "$basename_f" == "BUG-TEMPLATE.md" ]] && continue
        [[ "$basename_f" == "README.md" ]] && continue

        slug="${basename_f%.md}"

        already_bundled=false
        local bid
        for bid in "${bundled_ids[@]+"${bundled_ids[@]}"}"; do
            if [[ "$bid" == "$slug" ]]; then
                already_bundled=true
                break
            fi
        done
        [[ "$already_bundled" == "true" ]] && continue

        summary="$(grep -m1 '^# ' "$f" 2>/dev/null | sed 's/^# //' || true)"
        if [[ -z "$summary" ]]; then
            summary="$slug"
        fi

        unhandled_slugs+=("$slug")
        unhandled_files+=("$f")
        unhandled_summaries+=("$summary")
    done

    local bug_count="${#unhandled_slugs[@]}"
    if [[ "$bug_count" -eq 0 ]]; then
        log "autonomous scan (bugs): no unhandled bugs found"
        return 1
    fi

    log "autonomous scan (bugs): found ${bug_count} unhandled bug(s), bundling"

    local ver_stripped major minor patch
    ver_stripped="${last_released#v}"
    major="$(printf '%s' "$ver_stripped" | cut -d. -f1)"
    minor="$(printf '%s' "$ver_stripped" | cut -d. -f2)"
    patch="$(printf '%s' "$ver_stripped" | cut -d. -f3)"
    patch=$(( patch + 1 ))
    patch_ver="v${major}.${minor}.${patch}"

    today="$(date +%Y%m%d)"
    req_doc_name="${patch_ver}-bugfix-bundle-${today}.md"
    req_doc_path="${priority_dir}/${req_doc_name}"

    mkdir -p "$priority_dir"

    local bug_sections=""
    local i
    for i in "${!unhandled_slugs[@]}"; do
        local s="${unhandled_slugs[$i]}"
        local sv="${unhandled_summaries[$i]}"
        local rel_path="${bugs_dir#${KANBAN_ROOT}/}/${s}.md"
        bug_sections="${bug_sections}
#### ${s}
- **File:** ${rel_path}
- **Summary:** ${sv}
"
    done

    local dev_tree_path
    dev_tree_path="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
    local project_git_repo="none"
    if [[ -n "${PP_git_repo_url:-}" ]]; then
        project_git_repo="${PP_git_repo_url}"
    elif [[ -d "${dev_tree_path}/.git" ]]; then
        project_git_repo="$(git -C "${dev_tree_path}" remote get-url origin 2>/dev/null || echo "none")"
    fi

    cat > "$req_doc_path" <<EOF
# Requirements: ${patch_ver} — Bug Fix Bundle ($(date +%Y-%m-%d))

## Target Version
${patch_ver}

## Workflow Type
release

## Test Required
true

## Working Directory
${dev_tree_path}

## Git Repo
${project_git_repo}

## Summary
Priority bug fix bundle containing ${bug_count} unhandled bug report(s).
The Working Directory above is propagated to every task PM decomposes from this brief.
Each worker task operates in a per-task worktree off the RC branch.

## Scope

### Bug Reports
${bug_sections}
## Suggested Decomposition
(Leave this to PM to determine during decomposition)

## Acceptance Criteria
- [ ] All listed bugs are addressed
- [ ] No regressions introduced
EOF

    log "autonomous scan (bugs): wrote priority requirements doc ${req_doc_path}"
    log "autonomous scan (bugs): project Working Directory = ${dev_tree_path}"
    log "autonomous scan (bugs): project Git Repo = ${project_git_repo}"

    mkdir -p "$(dirname "$bug_backlog")"
    if [[ ! -f "$bug_backlog" ]]; then
        cat > "$bug_backlog" <<'BEOF'
# Bug Agent Backlog

<!-- Managed by wake script bug scan on PM's behalf. -->
<!-- Format: - [x] BUG-NNNN-slug — summary -->

## Queue

BEOF
    elif ! grep -q '^## Queue' "$bug_backlog"; then
        printf '\n## Queue\n\n' >> "$bug_backlog"
    fi

    for i in "${!unhandled_slugs[@]}"; do
        local entry="- [x] ${unhandled_slugs[$i]} — ${unhandled_summaries[$i]}"
        printf '%s\n' "$entry" >> "$bug_backlog"
        log "autonomous scan (bugs): marked bundled: ${unhandled_slugs[$i]}"
    done

    return 0
}

# ---------------------------------------------------------------------------
# drop_self_pm_ticket PATHS
# Create a PM task folder and append it to pm_backlog.md.
# ---------------------------------------------------------------------------
drop_self_pm_ticket() {
    local raw_paths="$1"
    local today task_id task_dir first_path brief_ver inputs_block line

    today="$(date +%Y%m%d)"

    first_path="$(printf '%s\n' "$raw_paths" | head -n1 | tr -d '[:space:]')"
    brief_ver="$(semver_from_filename "$first_path" 2>/dev/null || true)"
    if [[ -z "$brief_ver" ]]; then
        log "drop_self_pm_ticket: cannot parse version from '${first_path}'; skipping ticket creation"
        return 0
    fi

    task_id="$(kanban_task_id "${TASKS_ROOT}" "PM" "${today}" "decompose-${brief_ver}")"
    task_dir="${TASKS_ROOT}/${task_id}"

    if [[ -d "$task_dir" ]]; then
        log "drop_self_pm_ticket: task ${task_id} already exists, skipping"
        return 0
    fi

    mkdir -p "$task_dir"

    inputs_block=""
    while IFS= read -r line; do
        [[ -z "$line" ]] && continue
        inputs_block="${inputs_block}- ${line}"$'\n'
    done <<< "$raw_paths"
    inputs_block="${inputs_block%$'\n'}"

    local pm_dev_tree
    # PP_dev_tree_path is the per-project override (set by pp_load_config);
    # fall back to the process-wide default only when the per-project value
    # is unset.  Matches the resolution pattern in scan_and_bundle_bugs.
    pm_dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"

    cat > "${task_dir}/README.md" <<EOF
# Task: Autonomous PM — requirements brief ${brief_ver}

## Task ID
${task_id}

## Owner
Claude

## Role
PM

## Assigned Agent
none

## Working Directory
${pm_dev_tree}

## Goal
Decompose requirements brief(s) into an implementation plan

## Inputs
${inputs_block}

## Prerequisites
none

## Release Version
${brief_ver}

## Notes
Self-ticket created by wake script autonomous scan on ${today}.
EOF

    cat > "${task_dir}/status.md" <<EOF
# Status

## Task
${task_id}

## Participant
Claude

## Role
PM

## State
BACKLOG

## Summary
Autonomous PM self-ticket for requirements brief ${brief_ver}.

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
Process requirements brief(s) and produce a plan JSON.

## Instruction Conflicts
none
EOF

    local pm_backlog
    pm_backlog="$(pp_queue_path "${_CURRENT_PROJECT}" "pm")"
    mkdir -p "$(dirname "$pm_backlog")"
    if [[ ! -f "$pm_backlog" ]]; then
        cat > "$pm_backlog" <<'QEOF'
# PM Backlog

<!-- Auto-managed by wake script. One task per line. -->
<!-- Format: - [ ] TASK-ID -->

QEOF
    fi
    printf -- '- [ ] %s\n' "$task_id" >> "$pm_backlog"

    log "drop_self_pm_ticket: created task ${task_id} (${brief_ver}) from ${first_path}"
}

# --- Helpers ---

ensure_status_file() {
  local status_file="$1"
  if [[ ! -f "$status_file" ]] || [[ ! -s "$status_file" ]]; then
    cat > "$status_file" <<'EOF'
## State
BACKLOG

## Summary
TBD

## Artifacts
none

## Blockers
none

## Needs Human
no

## Next Recommended Step
TBD

## Instruction Conflicts
none
EOF
    log "created status.md template for task"
  fi
}

get_first_pending_task() {
  python3 - "$BACKLOG" "$TASKS_ROOT" <<'PY'
import re, sys, pathlib
backlog = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
text = backlog.read_text()
for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*[ B]\s*\]\s+([A-Za-z0-9._-]+)(\s+.*)?$', line)
    if not m:
        continue
    task_id = m.group(1)
    status_file = tasks_root / task_id / "status.md"
    if not status_file.is_file():
        print(task_id)
        raise SystemExit(0)
    status_text = status_file.read_text()
    state_match = re.search(r'^## State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M)
    if not state_match:
        print(task_id)
        raise SystemExit(0)
    state = state_match.group(1).strip().upper()
    if state == "BACKLOG":
        print(task_id)
        raise SystemExit(0)
raise SystemExit(1)
PY
}

get_role_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^## Role\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if not m:
    print("CODER")
    raise SystemExit(0)
role = m.group(1).strip().split('|')[0].strip().upper()
if role not in ("CODER", "WRITER", "CM", "TESTER", "PO", "PM"):
    role = "CODER"
print(role)
PY
}

get_model_override_from_readme() {
  local readme="$1"
  python3 - "$readme" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^##\s+(?:Force\s+Model|Model\s+Override)[^\S\n]*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M | re.I)
if not m:
    raise SystemExit(0)
value = m.group(1).strip()
if not value or value.lower() == 'none':
    raise SystemExit(0)
print(value)
PY
}

check_prerequisites() {
  local readme="$1"
  python3 - "$readme" "$TASKS_ROOT" <<'PY'
import pathlib, re, sys
readme = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])

text = readme.read_text()
m = re.search(r'^## Prerequisites\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if not m:
    raise SystemExit(0)

body = m.group(1).strip()
body = re.sub(r'<!--.*?-->', '', body, flags=re.S).strip()
if not body or body.lower() == "none":
    raise SystemExit(0)

prereqs = []
for line in body.splitlines():
    line = line.strip()
    if line.startswith("-"):
        line = line[1:].strip()
    if not line or line.lower() == "none":
        continue
    for token in line.split(","):
        token = token.strip()
        if token and token.lower() != "none" and re.match(r'^[A-Za-z0-9._-]+$', token):
            prereqs.append(token)

if not prereqs:
    raise SystemExit(0)

SATISFIED = {"DONE", "WONT-DO"}

unsatisfied = []
for prereq_id in prereqs:
    status_file = tasks_root / prereq_id / "status.md"
    if not status_file.is_file():
        unsatisfied.append(f"{prereq_id}:MISSING")
        continue
    status_text = status_file.read_text()
    sm = re.search(r'^## State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M)
    if not sm:
        unsatisfied.append(f"{prereq_id}:UNPARSEABLE")
        continue
    state = sm.group(1).strip().upper()
    if state not in SATISFIED:
        unsatisfied.append(f"{prereq_id}:{state}")

if unsatisfied:
    for u in unsatisfied:
        print(u)
    raise SystemExit(1)
raise SystemExit(0)
PY
}

get_state() {
  local status_file="$1"
  python3 - "$status_file" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'(^## State\s*\n)(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
if not m:
    raise SystemExit("Could not parse ## State block")
print(m.group(2).strip().upper())
PY
}

normalize_status_file() {
  local status_file="$1"
  python3 - "$status_file" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
text = path.read_text()
text = re.sub(r'\n\n\n+', '\n\n', text)
path.write_text(text)
PY
}

set_state() {
  local status_file="$1"
  local new_state="$2"
  python3 - "$status_file" "$new_state" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
new_state = sys.argv[2]
text = path.read_text()
text_new, n = re.subn(
    r'(^## State\s*\n)(.*?)(\n+##|\Z)',
    lambda m: m.group(1) + new_state.strip() + "\n" + (m.group(3) if m.group(3) else ''),
    text,
    flags=re.S | re.M,
)
if n == 0:
    raise SystemExit("Could not update ## State block")
path.write_text(text_new)
PY
}

set_block() {
  local status_file="$1"
  local heading="$2"
  local body="$3"
  python3 - "$status_file" "$heading" "$body" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
heading = sys.argv[2]
body = sys.argv[3]
text = path.read_text()
pattern = rf'(^## {re.escape(heading)}\s*\n)(.*?)(\n+##|\Z)'
text_new, n = re.subn(
    pattern,
    lambda m: m.group(1) + body.strip() + "\n" + (m.group(3) if m.group(3) else '\n'),
    text,
    flags=re.S | re.M,
)
if n == 0:
    raise SystemExit(f"Could not update ## {heading} block")
path.write_text(text_new)
PY
}

# --- stamp_model_field <status_file> <model_string> ---
# Write the resolved model string into the ## Model section of a task status file.
# If the section is absent it is inserted after ## Role (or after ## Participant
# when ## Role is absent, or at the end when neither anchor is found).
# Only ## Model is touched; all other sections are preserved byte-identically.
# Best-effort: on failure a warning is logged to stderr and the task continues.
stamp_model_field() {
  local status_file="$1"
  local model_value="$2"
  python3 - "$status_file" "$model_value" <<'PY'
import pathlib, re, sys

path = pathlib.Path(sys.argv[1])
model_value = sys.argv[2].strip()

try:
    text = path.read_text()
except OSError as e:
    raise SystemExit(f"stamp_model_field: cannot read {path}: {e}")

# If ## Model section already exists, replace its body (section-scoped update).
if re.search(r'^## Model\s*$', text, flags=re.M):
    text_new, n = re.subn(
        r'(^## Model\s*\n)(.*?)(\n+##|\Z)',
        lambda m: m.group(1) + model_value + "\n" + (m.group(3) if m.group(3) else ''),
        text,
        flags=re.S | re.M,
    )
    if n == 0:
        raise SystemExit("stamp_model_field: found ## Model header but subn matched 0 times")
    path.write_text(text_new)
    raise SystemExit(0)

# Section absent — insert after ## Role, or ## Participant, or at end.
# The replacement captures (anchor-header + body)(newline(s) + next-##) and
# injects the new ## Model section between them, normalising spacing so the
# output reads as two blank-line-separated sections.
new_section = f"## Model\n{model_value}\n"
for anchor in (r'^(## Role\s*\n.*?)(\n+##)', r'^(## Participant\s*\n.*?)(\n+##)'):
    text_new, n = re.subn(
        anchor,
        lambda m: m.group(1) + "\n\n" + new_section + "\n" + m.group(2).lstrip('\n'),
        text,
        count=1,
        flags=re.S | re.M,
    )
    if n > 0:
        # Collapse any run of 3+ newlines introduced by the splice.
        text_new = re.sub(r'\n{3,}', '\n\n', text_new)
        path.write_text(text_new)
        raise SystemExit(0)

# Fallback: append at end.
path.write_text(text.rstrip('\n') + "\n\n" + new_section)
PY
  local _stamp_exit=$?
  if [[ $_stamp_exit -ne 0 ]]; then
    echo "WARNING: stamp_model_field failed (exit ${_stamp_exit}) for ${status_file}; continuing" >&2
  fi
}

mark_backlog() {
  local task_id="$1"
  local marker="$2"
  python3 - "$BACKLOG" "$task_id" "$marker" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
marker = sys.argv[3]
text = path.read_text()
pattern = rf'^(\s*-\s*)\[[xRBWA\s]*\](\s+{re.escape(task_id)})(\s.*)?$'
text_new, n = re.subn(pattern, lambda m: m.group(1) + f'[{marker}]' + m.group(2) + (m.group(3) if m.group(3) else ''), text, flags=re.M)
if n == 0:
    raise SystemExit(f"Could not update backlog entry for {task_id}")
path.write_text(text_new)
PY
}

promote_waiting_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  python3 - "$BACKLOG" "$task_id" "$task_status" <<'PY'
import pathlib, re, sys

backlog_path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
status_path = pathlib.Path(sys.argv[3])

try:
    queue_text = backlog_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read queue file: {e}")

try:
    status_text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

queue_pattern = rf'^(\s*-\s*)\[\s*[W\s]\s*\](\s+{re.escape(task_id)})(\s.*)?$'
queue_new, queue_n = re.subn(
    queue_pattern,
    lambda m: m.group(1) + '[ ]' + m.group(2) + (m.group(3) if m.group(3) else ''),
    queue_text,
    flags=re.M,
)
if queue_n == 0:
    raise SystemExit(f"Could not find [W] or [ ] queue entry for {task_id}")

new_summary = "Promoted from WAITING to BACKLOG by wake script: prerequisites satisfied."

def replace_block(text, heading, new_body):
    pattern = rf'(^## {re.escape(heading)}\s*\n)(.*?)(\n+##|\Z)'
    text_new, n = re.subn(
        pattern,
        lambda m: m.group(1) + new_body.strip() + "\n" + (m.group(3) if m.group(3) else ''),
        text,
        flags=re.S | re.M,
    )
    if n == 0:
        raise SystemExit(f"Could not update ## {heading} block in status.md")
    return text_new

try:
    status_new = replace_block(status_text, "State", "BACKLOG")
    status_new = replace_block(status_new, "Blockers", "none")
    status_new = replace_block(status_new, "Summary", new_summary)
except SystemExit:
    raise

status_new = re.sub(r'\n\n\n+', '\n\n', status_new)

try:
    backlog_path.write_text(queue_new)
except OSError as e:
    raise SystemExit(f"Failed to write queue file: {e}")

try:
    status_path.write_text(status_new)
except OSError as e:
    try:
        backlog_path.write_text(queue_text)
    except OSError as rollback_err:
        raise SystemExit(
            f"CRITICAL: Failed to write status file ({e}) AND failed to roll back "
            f"queue file ({rollback_err}). Manual intervention required for {task_id}."
        )
    raise SystemExit(f"Failed to write status file (queue rolled back): {e}")

PY
}

promote_blocked_to_backlog() {
  local task_id="$1"
  local task_status="$2"
  local cleared_reason="$3"
  python3 - "$BACKLOG" "$task_id" "$task_status" "$cleared_reason" <<'PY'
import pathlib, re, sys

backlog_path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
status_path = pathlib.Path(sys.argv[3])
cleared_reason = sys.argv[4]

try:
    queue_text = backlog_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read queue file: {e}")

try:
    status_text = status_path.read_text()
except OSError as e:
    raise SystemExit(f"Cannot read status file: {e}")

queue_pattern = rf'^(\s*-\s*)\[\s*B\s*\](\s+{re.escape(task_id)})(\s.*)?$'
queue_new, queue_n = re.subn(
    queue_pattern,
    lambda m: m.group(1) + '[ ]' + m.group(2) + (m.group(3) if m.group(3) else ''),
    queue_text,
    flags=re.M,
)
if queue_n == 0:
    raise SystemExit(f"Could not find [B] queue entry for {task_id}")

new_summary = f"Promoted from BLOCKED to BACKLOG by wake script: blocker cleared ({cleared_reason})."

def replace_block(text, heading, new_body):
    pattern = rf'(^## {re.escape(heading)}\s*\n)(.*?)(\n+##|\Z)'
    text_new, n = re.subn(
        pattern,
        lambda m: m.group(1) + new_body.strip() + "\n" + (m.group(3) if m.group(3) else ''),
        text,
        flags=re.S | re.M,
    )
    if n == 0:
        return text
    return text_new

try:
    status_new = replace_block(status_text, "State", "BACKLOG")
    status_new = replace_block(status_new, "Blockers", "none")
    status_new = replace_block(status_new, "Blocked By Agent", "none")
    status_new = replace_block(status_new, "Blocked Reason", "none")
    status_new = replace_block(status_new, "Needs Human", "no")
    status_new = replace_block(status_new, "Summary", new_summary)
except SystemExit:
    raise

status_new = re.sub(r'\n\n\n+', '\n\n', status_new)

try:
    backlog_path.write_text(queue_new)
except OSError as e:
    raise SystemExit(f"Failed to write queue file: {e}")

try:
    status_path.write_text(status_new)
except OSError as e:
    try:
        backlog_path.write_text(queue_text)
    except OSError as rollback_err:
        raise SystemExit(
            f"CRITICAL: Failed to write status file ({e}) AND failed to roll back "
            f"queue file ({rollback_err}). Manual intervention required for {task_id}."
        )
    raise SystemExit(f"Failed to write status file (queue rolled back): {e}")

PY
}

# --- BLOCKED-task blocker-check dispatch table ---

_blocked_check_active_rc() {
  local active_rc
  active_rc="$(get_release_state_field "Active RC" 2>/dev/null || true)"
  if [[ -z "$active_rc" || "$active_rc" == "none" ]]; then
    return 0  # cleared
  fi
  return 1  # still blocked
}

recheck_waiting_tasks() {
  log "re-checking WAITING tasks for satisfied prerequisites..."

  local waiting_ids
  waiting_ids=$(python3 - "$BACKLOG" "$TASKS_ROOT" <<'PY'
import re, sys, pathlib

backlog_path = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
text = backlog_path.read_text()

for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*W\s*\]\s+([A-Za-z0-9._-]+)(\s+.*)?$', line)
    if m:
        print(m.group(1))
        continue
    m = re.match(r'^\s*-?\s*\[\s*\]\s+([A-Za-z0-9._-]+)(\s+.*)?$', line)
    if m:
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        status_text = status_file.read_text()
        state_match = re.search(r'^##\s+State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M)
        if state_match and state_match.group(1).strip().upper() == "WAITING":
            print(task_id)
PY
)

  if [[ -z "$waiting_ids" ]]; then
    log "no WAITING tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "WAITING task ${task_id} missing files; skipping re-check"
      continue
    fi

    local check_exit=0
    set +e
    check_prerequisites "$task_readme" >/dev/null
    check_exit=$?
    set -e

    if [[ $check_exit -eq 0 ]]; then
      log "WAITING task ${task_id}: prerequisites satisfied, promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_waiting_to_backlog "$task_id" "$task_status"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$waiting_ids"

  log "re-check complete: promoted ${promoted} task(s) from WAITING to BACKLOG"
}

recheck_blocked_tasks() {
  log "re-checking BLOCKED tasks for cleared blockers..."

  local blocked_ids
  blocked_ids=$(python3 - "$BACKLOG" "$TASKS_ROOT" <<'PY'
import re, sys, pathlib

backlog_path = pathlib.Path(sys.argv[1])
tasks_root = pathlib.Path(sys.argv[2])
text = backlog_path.read_text()

for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*B\s*\]\s+([A-Za-z0-9._-]+)(\s+.*)?$', line)
    if m:
        task_id = m.group(1)
        status_file = tasks_root / task_id / "status.md"
        if not status_file.is_file():
            continue
        status_text = status_file.read_text()
        state_match = re.search(r'^##\s+State\s*\n(.*?)(\n+##|\Z)', status_text, flags=re.S | re.M)
        if state_match and state_match.group(1).strip().upper() == "BLOCKED":
            print(task_id)
PY
)

  if [[ -z "$blocked_ids" ]]; then
    log "no BLOCKED tasks to re-check"
    return 0
  fi

  local promoted=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue
    local task_dir="${TASKS_ROOT}/${task_id}"
    local task_readme="${task_dir}/README.md"
    local task_status="${task_dir}/status.md"

    if [[ ! -f "$task_readme" ]] || [[ ! -f "$task_status" ]]; then
      log "BLOCKED task ${task_id} missing files; skipping re-check"
      continue
    fi

    local needs_human
    needs_human=$(python3 - "$task_status" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^##\s+Needs Human\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
print(m.group(1).strip().lower() if m else "yes")
PY
)
    if [[ "$needs_human" == "yes" ]]; then
      log "BLOCKED task ${task_id}: Needs Human=yes, skipping auto-resolve"
      continue
    fi

    local blocked_reason
    blocked_reason=$(python3 - "$task_status" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^##\s+Blocked Reason\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
print(m.group(1).strip() if m else "")
PY
)

    local cleared=false
    local cleared_desc=""

    if echo "$blocked_reason" | grep -qi "active rc"; then
      local check_exit=0
      set +e
      _blocked_check_active_rc "$task_id" "$blocked_reason"
      check_exit=$?
      set -e
      if [[ $check_exit -eq 0 ]]; then
        cleared=true
        cleared_desc="Active RC=none"
      fi
    fi

    # --- Transient API error auto-retry ---
    # A task whose Blocked Reason begins with "TRANSIENT API ERROR" and whose
    # Needs Human=no is eligible for automatic promotion back to BACKLOG so the
    # next wake can retry it.  The retry count is embedded in the reason string
    # (e.g. "retry 1/3").  If the count extracted here is already at or past the
    # cap, the wake script that set the reason should have already escalated to
    # Needs Human=yes — this branch is a safety double-check that also handles
    # the case where TRANSIENT_RETRY_CAP was lowered between runs.
    if [[ "$cleared" == "false" ]] && echo "$blocked_reason" | grep -qi "TRANSIENT API ERROR"; then
      local _transient_cap="${TRANSIENT_RETRY_CAP:-3}"
      local _retry_count=0
      if echo "$blocked_reason" | grep -qE 'retry ([0-9]+)/'; then
        _retry_count="$(echo "$blocked_reason" | grep -oE 'retry ([0-9]+)/' | grep -oE '[0-9]+' | tail -1 || echo 0)"
      fi
      if [[ "$_retry_count" -lt "$_transient_cap" ]]; then
        cleared=true
        cleared_desc="transient API error auto-retry (attempt ${_retry_count}/${_transient_cap})"
        log "BLOCKED task ${task_id}: transient API error — scheduling auto-retry (attempt ${_retry_count}/${_transient_cap})"
      else
        log "BLOCKED task ${task_id}: transient API error — retry cap reached (${_retry_count}/${_transient_cap}); leaving for human"
      fi
    fi

    if [[ "$cleared" == "true" ]]; then
      log "BLOCKED task ${task_id}: blocker cleared (${cleared_desc}), promoting to BACKLOG"
      local promote_exit=0
      set +e
      promote_blocked_to_backlog "$task_id" "$task_status" "$cleared_desc"
      promote_exit=$?
      set -e
      if [[ $promote_exit -ne 0 ]]; then
        log "WARNING: atomic promotion failed for ${task_id}; skipping (no partial update)"
        continue
      fi
      promoted=$((promoted + 1))
    fi
  done <<< "$blocked_ids"

  log "re-check complete: promoted ${promoted} BLOCKED task(s) to BACKLOG"
}

reconcile_stale_active() {
  local stale_ids
  stale_ids=$(python3 - "$BACKLOG" <<'PY'
import re, sys, pathlib

backlog_path = pathlib.Path(sys.argv[1])
text = backlog_path.read_text()

for line in text.splitlines():
    m = re.match(r'^\s*-?\s*\[\s*A\s*\]\s+([A-Za-z0-9._-]+)(\s+.*)?$', line)
    if m:
        print(m.group(1))
PY
)

  if [[ -z "$stale_ids" ]]; then
    return 0
  fi

  log "stale-active reconciliation: checking [A] markers for orphaned tasks..."

  local reconciled=0
  while IFS= read -r task_id; do
    [[ -z "$task_id" ]] && continue

    if pgrep -f "$task_id" >/dev/null 2>&1; then
      log "stale-active: ${task_id} has active process, keeping [A]"
      continue
    fi

    local task_status="${TASKS_ROOT}/${task_id}/status.md"
    if [[ ! -f "$task_status" ]]; then
      log "stale-active: ${task_id} missing status.md; resetting marker to [ ]"
      mark_backlog "$task_id" " "
      reconciled=$((reconciled + 1))
      continue
    fi

    log "stale-active: ${task_id} has no active process, resetting to [ ]/BACKLOG"

    # --- Teardown prior worktree/branch before BACKLOG reset ---
    # The interrupted attempt may have left a worktree directory and/or feature
    # branch on disk.  Tear them down so the subsequent create_task_worktree call
    # (in the next process_one_task invocation) gets a clean slate instead of
    # hitting "branch already exists" and hard-blocking with Needs Human=yes.
    #
    # Reuses:
    #   teardown_task_worktree (worktree.sh) — 'git worktree remove --force' + prune
    #   pp_prefix_branch (project_paths.sh) — compute prefix-aware feature branch name
    # Both helpers are available here: wake/claude.sh and wake/codex.sh both source
    # worktree.sh before calling run_project_chain, which calls reconcile_stale_active.
    #
    # PP_dev_tree_path is populated by pp_load_config (called in run_project_chain
    # before reconcile_stale_active); PGAI_DEV_TREE_PATH is used when unset.
    # Teardown is best-effort: failures emit a WARNING but never block the state reset
    # (create_task_worktree is itself idempotent; this is defense-in-depth).
    local _sa_dev_tree="${PP_dev_tree_path:-${PGAI_DEV_TREE_PATH:-}}"
    if [[ -n "$_sa_dev_tree" && -d "$_sa_dev_tree/.git" ]]; then
      # Compute the prefix-aware feature branch name (mirrors reset.sh Step 4).
      local _sa_feature_branch=""
      local _sa_fb_exit=0
      set +e
      _sa_feature_branch="$(pp_prefix_branch "${_CURRENT_PROJECT:-}" "feature/${task_id}" 2>/dev/null)"
      _sa_fb_exit=$?
      set -e
      if [[ $_sa_fb_exit -ne 0 || -z "$_sa_feature_branch" ]]; then
        _sa_feature_branch="feature/${task_id}"   # safe fallback when helper fails
      fi

      # Step 1: remove the worktree directory (branch may be checked out there).
      local _sa_wt_path=""
      if _sa_wt_path="$(pgai_worktree_path "$task_id" 2>/dev/null)" && [[ -d "$_sa_wt_path" ]]; then
        log "stale-active: ${task_id} tearing down prior worktree at ${_sa_wt_path}"
        if ! teardown_task_worktree "$task_id" "$_sa_dev_tree" 2>/dev/null; then
          log "stale-active: ${task_id} WARNING: worktree teardown failed (non-fatal; create_task_worktree will retry idempotently)"
        else
          log "stale-active: ${task_id} prior worktree removed"
        fi
      fi

      # Step 2: delete the feature branch if it still exists locally.
      if git -C "$_sa_dev_tree" rev-parse --verify "refs/heads/${_sa_feature_branch}" >/dev/null 2>&1; then
        log "stale-active: ${task_id} deleting prior feature branch ${_sa_feature_branch}"
        # Suppress stdout as well as stderr — git branch -D emits "Deleted branch ..."
        # to stdout, which would appear as stray output in cron logs.
        if git -C "$_sa_dev_tree" branch -D "$_sa_feature_branch" >/dev/null 2>&1; then
          log "stale-active: ${task_id} feature branch ${_sa_feature_branch} deleted"
        else
          log "stale-active: ${task_id} WARNING: could not delete feature branch ${_sa_feature_branch} (non-fatal)"
        fi
      fi
    else
      log "stale-active: ${task_id} no dev tree configured or accessible — skipping git teardown"
    fi

    set_state "$task_status" "BACKLOG"
    set_block "$task_status" "Summary" "Reset from stale WORKING state by wake script reconciliation."
    normalize_status_file "$task_status"
    mark_backlog "$task_id" " "
    reconciled=$((reconciled + 1))
  done <<< "$stale_ids"

  if [[ $reconciled -gt 0 ]]; then
    log "stale-active reconciliation: reset ${reconciled} orphaned [A] task(s)"
  fi
}

# ---------------------------------------------------------------------------
# handle_working_after_exit <task_id> <task_status> <log_file> <agent_exit>
#
# Provider-neutral WORKING-after-exit handler.  Called by both claude.sh and
# codex.sh when a task is still in WORKING state after the agent process exits.
#
# Behaviour:
#   1. Grep the tail of <log_file> for known transient provider error signatures
#      (Anthropic: 5xx/overloaded/rate-limit/429; OpenAI: server_error/Rate limit
#      reached/The server had an error/500-503/529).  'insufficient_quota' is
#      intentionally excluded — it is a billing failure, not a retry-worthy hiccup.
#   2. If a transient signature is found, read the previous retry count from the
#      task's Blocked Reason field, increment it, and:
#        - If still within TRANSIENT_RETRY_CAP: BLOCKED + Needs Human=no
#        - If cap reached: BLOCKED + Needs Human=yes (escalate)
#   3. If no transient signature, fall through to the generic path:
#        BLOCKED + Needs Human=yes (agent exited without updating state)
#   4. Normalizes the status file and prints the resulting final_state ("BLOCKED")
#      to stdout.
#
# The detection regex and retry-count logic live here exactly once; the two
# wake scripts' local log() calls may note the outcome, but neither script
# re-implements the detection substrate.
# ---------------------------------------------------------------------------
handle_working_after_exit() {
  local task_id="${1:-}"
  local task_status="${2:-}"
  local log_file="${3:-}"
  local agent_exit="${4:-}"

  local _transient_cap="${TRANSIENT_RETRY_CAP:-3}"
  local _transient_matched=false
  local _transient_sig=""

  # --- Transient API error detection ---
  # Grep the last 50 lines of the agent log for known transient provider
  # signatures.  Two regex patterns are used:
  #   _DETECT_RE  — fast boolean match (grep -q); determines if any signature
  #                 is present in the tail.
  #   _EXTRACT_RE — extract the first matching fragment for the label; must be
  #                 a subset of _DETECT_RE.
  # Anthropic signatures: API Error 5xx, overloaded, overloaded_error,
  #   rate_limit / rate limit / 429, 50[023], 529.
  # OpenAI/codex signatures: server_error, Rate limit reached,
  #   The server had an error, HTTP 500/502/503/529 in error context.
  # EXCLUDED: insufficient_quota (non-transient billing failure).
  # NOTE: bare numeric alternations (429, 50[023], 529) are digit-bounded via
  #   (^|[^0-9])N([^0-9]|$) so they cannot match inside task-ID date segments,
  #   token counts, or elapsed-ms values (e.g. 20260503, 15030, 5029ms).
  #   Each numeric is a separate alternation to avoid |N| substrings that would
  #   be caught by the bare-alternation lint check.
  local _DETECT_RE='API Error: 5[0-9][0-9]|[Oo]verloaded|overloaded_error|rate[_. -]?limit|(^|[^0-9])429([^0-9]|$)|(^|[^0-9])50[023]([^0-9]|$)|(^|[^0-9])529([^0-9]|$)|server_error|[Tt]he server had an error|[Rr]ate limit reached'
  local _EXTRACT_RE='API Error: 5[0-9][0-9][^\n]*|[Oo]verloaded[^\n]*|overloaded_error[^\n]*|rate[_. -]?limit[^\n]*|(^|[^0-9])429([^0-9]|$)|(^|[^0-9])50[023]([^0-9]|$)|(^|[^0-9])529([^0-9]|$)|server_error[^\n]*|[Tt]he server had an error[^\n]*|[Rr]ate limit reached[^\n]*'

  if [[ -f "$log_file" ]]; then
    local _tail_lines
    _tail_lines="$(tail -50 "$log_file" 2>/dev/null || true)"
    if echo "$_tail_lines" | grep -qiE "$_DETECT_RE"; then
      _transient_matched=true
      _transient_sig="$(echo "$_tail_lines" | grep -iEo "$_EXTRACT_RE" \
        | tail -1 | cut -c1-80 || true)"
      [[ -z "$_transient_sig" ]] && _transient_sig="transient provider error"
    fi
  fi

  if [[ "$_transient_matched" == "true" ]]; then
    # Read the current Blocked Reason to extract the previous retry count (if any).
    local _prev_reason
    _prev_reason="$(python3 - "$task_status" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'^##\s+Blocked Reason\s*\n(.*?)(\n+##|\Z)', text, flags=re.S | re.M)
print(m.group(1).strip() if m else "")
PY
)"
    local _retry_count=0
    if echo "$_prev_reason" | grep -qE 'TRANSIENT API ERROR.*retry ([0-9]+)/'; then
      _retry_count="$(echo "$_prev_reason" | grep -oE 'retry ([0-9]+)/' | grep -oE '[0-9]+' | tail -1 || echo 0)"
    fi
    _retry_count=$(( _retry_count + 1 ))

    if [[ "$_retry_count" -le "$_transient_cap" ]]; then
      log "task ${task_id} left in WORKING after transient API error (${_transient_sig}); marking BLOCKED+TRANSIENT (retry ${_retry_count}/${_transient_cap})"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blocked Reason" "TRANSIENT API ERROR — ${_transient_sig} — safe to retry (retry ${_retry_count}/${_transient_cap}); no human action required. See ${log_file}."
      set_block "$task_status" "Blockers" "TRANSIENT API ERROR (retry ${_retry_count}/${_transient_cap}): ${_transient_sig}. See ${log_file}."
      set_block "$task_status" "Needs Human" "no"
    else
      log "task ${task_id} left in WORKING after transient API error (${_transient_sig}); retry cap reached (${_retry_count}/${_transient_cap}) — escalating to Needs Human=yes"
      set_state "$task_status" "BLOCKED"
      set_block "$task_status" "Blocked Reason" "TRANSIENT API ERROR (retry cap ${_transient_cap} reached) — ${_transient_sig} — repeated transient failures; human investigation required. Exit code: ${agent_exit}. See ${log_file}."
      set_block "$task_status" "Blockers" "TRANSIENT API ERROR (retry cap ${_transient_cap} reached): ${_transient_sig}. Exit code: ${agent_exit}. See ${log_file}."
      set_block "$task_status" "Needs Human" "yes"
    fi
  else
    log "task ${task_id} left in WORKING state after agent exited; marking BLOCKED"
    set_state "$task_status" "BLOCKED"
    set_block "$task_status" "Blockers" "Agent exited without updating task state. Exit code: ${agent_exit}. See ${log_file}."
    set_block "$task_status" "Needs Human" "yes"
  fi

  normalize_status_file "$task_status"
  printf 'BLOCKED\n'
}

# ---------------------------------------------------------------------------
# close_intake_on_finalize_report
#   <task_id> <task_readme> <project_root> <tasks_root> <wf_manifest_finalize>
#
# Closure-parity helper for testing-only (finalize=report) workflows.
# Mirrors the CM intake-item closure that cm/release.sh Step 16b performs for
# release workflows via cm/promote_bundled_items.py, but keyed on:
#   (a) the workflow plugin's finalize=report capability (WF_MANIFEST_FINALIZE)
#   (b) the terminal roster ticket's README carrying "finalize_mode: report"
#       in its ## Constraints section (written by inject_simple_tester_task)
#
# Both guards must be true before any closure is attempted — neither fires in
# isolation.  This dual-gate prevents the closure from triggering on release
# or document workflows (finalize != report).
#
# Reference: BUG-0066 (this fix), BUG-0063 (report half), BUG-0051 (design).
# Parity with: cm/release.sh Step 16b + cm/promote_bundled_items.py.
#
# Behaviour:
#   1. Guard 1: <wf_manifest_finalize> must equal "report".  Any other value
#      (tag, publish, …) exits immediately — no-op for release/document paths.
#   2. Guard 2: <task_readme> ## Constraints section must contain the line
#      "finalize_mode: report" (written by inject_simple_tester_task).
#   3. Requirements path: the first absolute-path entry in <task_readme>'s
#      ## Inputs section (requirements file prepended by create_task_folder).
#   4. Mid-run failure check: scan <tasks_root> for any task whose README
#      lists the same requirements path in ## Inputs.  If any such sibling
#      task has ## State: BLOCKED, abort — item stays running.
#   5. Closure: invoke the canonical close_item helper via the ops package:
#        python3 -m pgai_agent_kanban.ops close_item <project_root> <key>
#      where <key> is the requirements file's basename (without .md extension).
#
# Returns 0 on success (closure fired or legitimately skipped).
# Returns non-zero only on unexpected internal errors (best-effort: never
# fails the task that called it).
# ---------------------------------------------------------------------------
close_intake_on_finalize_report() {
  local _task_id="${1:-}"
  local _task_readme="${2:-}"
  local _project_root="${3:-}"
  local _tasks_root="${4:-}"
  local _wf_finalize="${5:-}"

  # Guard 1: finalize capability must be "report".
  # All other values (tag, publish, none) skip this block entirely so that
  # release workflows and document workflows are byte-identical to their prior
  # behaviour.
  if [[ "$_wf_finalize" != "report" ]]; then
    return 0
  fi

  # Guard 2: task README ## Constraints must contain "finalize_mode: report".
  # inject_simple_tester_task writes this constraint on the TESTER finalizer
  # task; regular TESTER tasks in release workflows do not carry it.
  local _readme_has_finalize_report=false
  if [[ -f "$_task_readme" ]]; then
    _readme_has_finalize_report="$(python3 - "$_task_readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
except Exception:
    print("false"); raise SystemExit(0)
# Parse ## Constraints section (ends at next ## heading or EOF)
m = re.search(
    r'^##\s+Constraints\s*\n(.*?)(?=\n##|\Z)',
    text, flags=re.S | re.M
)
if m and re.search(r'^\s*[-*]?\s*finalize_mode\s*:\s*report\s*$', m.group(1), re.M):
    print("true")
else:
    print("false")
PY
)"
  fi

  if [[ "$_readme_has_finalize_report" != "true" ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: finalize_mode:report not found in README constraints — skipping intake closure (not a finalize=report terminal ticket)"
    return 0
  fi

  # Extract requirements file path: the first absolute path in ## Inputs.
  # create_task_folder prepends requirements_path to inputs so it is always
  # the first entry when present.
  local _req_path=""
  _req_path="$(python3 - "$_task_readme" <<'PY'
import pathlib, re, sys
try:
    text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
except Exception:
    raise SystemExit(1)
m = re.search(r'^##\s+Inputs\s*\n(.*?)(?=\n##|\Z)', text, flags=re.S | re.M)
if not m:
    raise SystemExit(1)
for line in m.group(1).splitlines():
    line = line.strip()
    if line.startswith('-'):
        line = line[1:].strip()
    if line.startswith('/') and line.endswith('.md'):
        print(line)
        raise SystemExit(0)
raise SystemExit(1)
PY
)" || true

  if [[ -z "$_req_path" ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: could not extract requirements path from README ## Inputs — skipping intake closure"
    return 0
  fi

  if [[ ! -f "$_req_path" ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: requirements file not found at ${_req_path} — skipping intake closure"
    return 0
  fi

  log "task ${_task_id}: close_intake_on_finalize_report: requirements file identified: ${_req_path}"

  # Mid-run failure check: scan sibling tasks (same requirements file in
  # ## Inputs) for BLOCKED state.  A BLOCKED sibling means the run did not
  # complete cleanly; intake item must remain running.
  local _blocked_sibling=false
  if [[ -d "$_tasks_root" ]]; then
    _blocked_sibling="$(python3 - "$_tasks_root" "$_req_path" <<'PY'
import pathlib, re, sys

tasks_root = pathlib.Path(sys.argv[1])
req_path   = sys.argv[2]

for task_dir in sorted(tasks_root.iterdir()):
    if not task_dir.is_dir() or task_dir.name == "queues":
        continue
    readme = task_dir / "README.md"
    status_file = task_dir / "status.md"
    if not readme.is_file() or not status_file.is_file():
        continue
    try:
        readme_text = readme.read_text(encoding="utf-8")
    except OSError:
        continue
    # Check if this task's ## Inputs contains the same requirements path
    m = re.search(r'^##\s+Inputs\s*\n(.*?)(?=\n##|\Z)', readme_text, flags=re.S | re.M)
    if not m:
        continue
    inputs_text = m.group(1)
    if req_path not in inputs_text:
        continue
    # Sibling task found — check its state
    try:
        status_text = status_file.read_text(encoding="utf-8")
    except OSError:
        continue
    state_m = re.search(r'^##\s+State\s*\n\s*(\S+)', status_text, re.M)
    if state_m and state_m.group(1).strip().upper() == "BLOCKED":
        print("true")
        raise SystemExit(0)

print("false")
PY
)" || true
  fi

  if [[ "$_blocked_sibling" == "true" ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: BLOCKED sibling task found — intake item stays running (mid-run failure guard)"
    return 0
  fi

  # All guards passed: invoke canonical close_item helper.
  # Key is the requirements file basename without the .md extension.
  local _req_key
  _req_key="$(basename "$_req_path" .md)"

  log "task ${_task_id}: close_intake_on_finalize_report: closing intake item '${_req_key}' (finalize=report terminal ticket complete; parity with CM Step 16b)"

  local _close_exit=0
  set +e
  python3 -m pgai_agent_kanban.ops close_item "$_project_root" "$_req_key" done 2>&1 | \
    while IFS= read -r _line; do log "close_intake_on_finalize_report: ${_line}"; done
  _close_exit="${PIPESTATUS[0]}"
  set -e

  if [[ $_close_exit -eq 0 ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: intake item '${_req_key}' closed done (BUG-0066 fix)"
  elif [[ $_close_exit -eq 2 ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: WARNING: ambiguous key '${_req_key}' — could not uniquely identify intake item; item left at running"
  elif [[ $_close_exit -eq 3 ]]; then
    log "task ${_task_id}: close_intake_on_finalize_report: WARNING: intake item '${_req_key}' not found — nothing to close"
  else
    log "task ${_task_id}: close_intake_on_finalize_report: WARNING: close_item exited ${_close_exit} for '${_req_key}'; item may not have been closed"
  fi

  return 0
}

# ---------------------------------------------------------------------------
# _build_feature_branch <task_id> <project_name>
# Calls pp_prefix_branch() to produce the prefixed feature branch name.
# Returns "<prefix>feature/<task_id>" when branch_prefix is set in project.cfg.
# Returns "feature/<task_id>" when branch_prefix is empty (pure-AI install).
# Returns non-zero when pp_prefix_branch fails (invalid config).
# ---------------------------------------------------------------------------
_build_feature_branch() {
    local task_id="${1:-}"
    local project="${2:-}"
    local base="feature/${task_id}"
    [[ -z "$project" ]] && { printf '%s' "$base"; return 0; }
    local result
    result="$(pp_prefix_branch "$project" "$base")"
    printf '%s' "${result:-$base}"
}

# ---------------------------------------------------------------------------
# _update_readme_feature_branch <readme_path> <feature_branch>
# Overwrites the ## Feature Branch field in a task README with <feature_branch>.
# No-op (non-fatal) when the field is absent from the README.
# ---------------------------------------------------------------------------
_update_readme_feature_branch() {
    local readme="$1"
    local branch="$2"
    python3 - "$readme" "$branch" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
b = sys.argv[2]
try:
    t = p.read_text()
    t2, n = re.subn(
        r'(^## Feature Branch\s*\n)(.*?)(\n+##|\Z)',
        lambda m: m.group(1) + b + "\n" + (m.group(3) if m.group(3) else ''),
        t, flags=re.S | re.M,
    )
    if n > 0:
        p.write_text(t2)
except Exception:
    pass
PY
}

# ---------------------------------------------------------------------------
# _check_halt_after_for_project <project_name>
# Evaluates the HALT-AFTER sentinel for a single project on each wake.
#
# Scope resolution (project scope takes precedence):
#   1. $project_root/HALT-AFTER  — project-scoped sentinel
#   2. $KANBAN_ROOT/HALT-AFTER   — root-scoped sentinel (applies to all projects)
#
# Delegates to the Python halt_after module (python3 -m halt_after <scope_root>)
# which parses the sentinel token, evaluates the drain condition, and when
# drain is satisfied performs the atomic promotion (rm HALT-AFTER, touch HALT,
# append audit entry to release-state.md).
#
# Return values (mirroring halt_after exit codes):
#   0 — HALT-AFTER was promoted to HALT; caller MUST skip this project
#   1 — drain not yet satisfied; chain continues normally
#   2 — invalid token; warning already logged by Python; chain continues
#   3 — no HALT-AFTER present; chain continues
#   4 — HALT-AFTER check could not execute; warning logged; chain continues
#
# Called once AFTER run_project_chain returns in wake_common_run (so promotion
# is decided after the agent drains its queue) and once inside the
# post-discovery block so a fresh promotion is honored mid-wake.
# ---------------------------------------------------------------------------
_check_halt_after_for_project() {
  local project_name="$1"
  local project_root
  project_root="$(pp_project_root "$project_name" 2>/dev/null)" || return 3

  # Determine the scope root: project scope takes precedence over root scope.
  local scope_root=""
  if [[ -f "${project_root}/HALT-AFTER" ]]; then
    scope_root="$project_root"
    log "project ${project_name}: HALT-AFTER sentinel found at project scope (${project_root}/HALT-AFTER)"
  elif [[ -f "${KANBAN_ROOT}/HALT-AFTER" ]]; then
    scope_root="$KANBAN_ROOT"
    log "project ${project_name}: HALT-AFTER sentinel found at root scope (${KANBAN_ROOT}/HALT-AFTER)"
  else
    return 3  # no sentinel present; chain continues
  fi

  # Delegate to the Python module for token parsing, drain evaluation, and promotion.
  # Put KANBAN_ROOT on PYTHONPATH explicitly so cron cwd never controls imports.
  #
  # When the sentinel is at root scope ($KANBAN_ROOT/HALT-AFTER), scope_root is
  # $KANBAN_ROOT but release-state.md lives under the per-project directory.
  # Pass project_root as the second argument so the Python module uses the correct
  # release-state.md for the rc-token bind and drain checks.  When sentinel is at
  # project scope, scope_root == project_root, so the second argument is redundant
  # but harmless (backward-compatible).
  local halt_after_exit
  local halt_after_output
  local halt_after_pythonpath
  halt_after_pythonpath="${KANBAN_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
  set +e
  halt_after_output="$(PYTHONPATH="$halt_after_pythonpath" python3 -m halt_after "$scope_root" "$project_root" 2>&1)"
  halt_after_exit="$?"
  set -e

  if [[ -n "$halt_after_output" ]]; then
    while IFS= read -r _ha_line; do
      log "project ${project_name}: halt_after: ${_ha_line}"
    done <<< "$halt_after_output"
  fi

  case "$halt_after_exit" in
    0)
      log "project ${project_name}: HALT-AFTER promoted to HALT (scope: ${scope_root}); project will be skipped this wake"
      ;;
    1)
      if grep -q "drain not yet satisfied" <<< "$halt_after_output"; then
        log "project ${project_name}: HALT-AFTER drain in progress — chain continues normally"
      else
        log "project ${project_name}: WARNING: HALT-AFTER check failed to run (exit ${halt_after_exit}) — chain continues"
        halt_after_exit=4
      fi
      ;;
    2)
      log "project ${project_name}: HALT-AFTER invalid token — warning logged; chain continues normally"
      ;;
    3)
      log "project ${project_name}: HALT-AFTER absent — chain continues normally"
      ;;
    *)
      log "project ${project_name}: WARNING: HALT-AFTER check failed to run (exit ${halt_after_exit}) — chain continues"
      halt_after_exit=4
      ;;
  esac

  return "$halt_after_exit"
}

# ---------------------------------------------------------------------------
# _check_project_dev_tree <project_name>
# Per-project dev-tree existence gate for run_project_chain.
#
# Called AFTER pp_load_config so PP_dev_tree_path and PP_workflow_type are set.
#
# For git-workflow projects (git_mode != none): if PP_dev_tree_path is empty
# or not a directory, log the canonical skip line and return 1 (caller must
# skip this project and continue to the next).
#
# Projects whose workflow plugin declares git_mode=none are exempt — they have
# no dev tree by design (e.g. document workflow).  The plugin is loaded
# best-effort; a failed load is treated conservatively (dev tree required).
#
# Return values:
#   0 — dev tree present (or exempt); caller continues normally
#   1 — dev tree missing; caller must skip this project
# ---------------------------------------------------------------------------
_check_project_dev_tree() {
    local project_name="${1:-}"

    # Determine the workflow's git mode by loading its plugin.
    # Projects with git_mode=none have no dev tree by design — exempt.
    local _wf="${PP_workflow_type:-release}"
    local _wf_git_mode="rw"   # conservative default: require dev tree
    local _wf_load_exit=0
    set +e
    wf_load_plugin "$_wf" 2>/dev/null
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -eq 0 ]]; then
        _wf_git_mode="$(wf_git_mode 2>/dev/null || echo "rw")"
    fi

    if [[ "$_wf_git_mode" == "none" ]]; then
        return 0
    fi

    local _tree="${PP_dev_tree_path:-}"

    # Empty path or non-directory — skip with the canonical log line.
    if [[ -z "$_tree" || ! -d "$_tree" ]]; then
        log "project ${project_name}: dev tree '${_tree}' missing — skipping this project"
        return 1
    fi

    return 0
}

# ---------------------------------------------------------------------------
# wake_common_preflight
# Script-level preflight checks. Called by the wake script after sourcing
# this file and the provider lib, before wake_common_run.
# ---------------------------------------------------------------------------
wake_common_preflight() {
  # Provider CLI is checked by the provider lib (it knows which binary to check).
  # Python3 is a substrate requirement.
  command -v python3 >/dev/null 2>&1 || { log "python3 not found in PATH"; exit 1; }
}

# ---------------------------------------------------------------------------
# wake_common_run
# Main entry point. Runs the agent-aware HALT check, stagger sleep, and
# multi-project iteration loop.
# ---------------------------------------------------------------------------
wake_common_run() {
  # --- Agent-aware HALT flag check at startup ---
  if [[ "$AGENT" == "overwatch" ]]; then
    if [[ -f "${TEAM_ROOT}/HALT_OVERWATCH" ]]; then
      log "HALT_OVERWATCH flag detected at ${TEAM_ROOT}/HALT_OVERWATCH — exiting cleanly without processing tasks"
      exit 0
    fi
  else
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
      log "HALT flag detected at ${TEAM_ROOT}/HALT — exiting cleanly without processing tasks"
      exit 0
    fi
  fi

  # --- Sub-minute stagger sleep ---
  if [[ "$SLEEP" -gt 0 ]]; then
    log "sleeping ${SLEEP}s before work (--sleep stagger)"
    sleep "$SLEEP"
  fi

  # Capture post-stagger work begin epoch; excludes the stagger from work_delta.
  WAKE_WORK_BEGIN_EPOCH=$(date +%s)

  # --- Multi-project iteration ---
  log "starting multi-project iteration (agent=${AGENT})"

  _ITER_PROJECT_COUNT=0

  while IFS= read -r _iter_project; do
    [[ -z "$_iter_project" ]] && continue

    # --- HALT fast-exit: skip project if an existing HALT sentinel is present ---
    # This honors an ALREADY-promoted HALT file so the wake exits fast without
    # spinning up the agent.  It does NOT evaluate HALT-AFTER drain/promotion —
    # that decision is made AFTER the agent's work loop (see below).
    if pp_project_halted "$_iter_project" 2>/dev/null; then
      log "project ${_iter_project}: per-project HALT present, skipping"
      continue
    fi

    export PGAI_PROJECT_ROOT="$(pp_project_root "$_iter_project")"
    export PGAI_PROJECT_NAME="$_iter_project"
    export PGAI_TASKS_DIR="$(pp_tasks_dir "$_iter_project")"

    _ITER_PROJECT_COUNT=$(( _ITER_PROJECT_COUNT + 1 ))
    log "project ${_iter_project}: beginning chain (project ${_ITER_PROJECT_COUNT})"

    run_project_chain "$_iter_project"

    # --- HALT-AFTER post-work check ---
    # Evaluate whether the HALT-AFTER drain condition is satisfied NOW that the
    # agent has drained its actionable queue for this project this wake.  Placing
    # the check here (after run_project_chain) prevents premature promotion where
    # the drain was assessed before the agent picked up its pending task, causing
    # HALT-AFTER to fire just before the agent did its work.
    #
    # The call MUST live in the `if` condition: _check_halt_after_for_project
    # returns non-zero (1/2/3) in the common cases, and wake_common.sh runs under
    # `set -e`.  A bare call on its own line would trip errexit and kill the wake
    # on every project that has no HALT-AFTER sentinel.  A function in an `if`
    # condition is exempt from errexit — this is the safe idiom.
    if _check_halt_after_for_project "$_iter_project"; then
      log "project ${_iter_project}: HALT-AFTER promoted after work loop — halted for next wake"
    fi

  done < <(projects_cfg_list 2>/dev/null)

  ELAPSED=$(( $(date +%s) - WAKE_START_EPOCH ))
  log "done: iterated ${_ITER_PROJECT_COUNT} project(s), total elapsed=${ELAPSED}s"

  # Capture post-stagger work end epoch before the tail pause so the fixed ~1s
  # sleep is excluded from work_delta; delta equals pure work time.
  WAKE_WORK_END_EPOCH=$(date +%s)
  WAKE_WORK_DELTA_SECS=$(( WAKE_WORK_END_EPOCH - WAKE_WORK_BEGIN_EPOCH ))
  log "timing: work_begin=${WAKE_WORK_BEGIN_EPOCH} work_end=${WAKE_WORK_END_EPOCH} work_delta=${WAKE_WORK_DELTA_SECS}s"

  sleep 1
  exit 0
}
