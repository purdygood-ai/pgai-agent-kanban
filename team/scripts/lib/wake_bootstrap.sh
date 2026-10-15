#!/usr/bin/env bash
# team/scripts/lib/wake_bootstrap.sh
#
# Wake bootstrap stage: environment/config/secrets sourcing, tunables, path
# and lock resolution, and the debug/training gate resolvers. Extracted from
# team/scripts/lib/wake_common.sh's former inline preamble so wake_common.sh
# itself stays a thinner shell over sourced lib stages. This is a
# behavior-preserving move: no stdout/stderr line, exit code, sourcing order,
# or on-disk file content changes as a result of this file existing — it
# is one stage split out of a larger driver so each stays a focused,
# independently testable unit.
#
# Contract (identical to wake_common.sh's own — unchanged by this move): the
# following variables MUST already be set by the caller before this file is
# sourced:
#   AGENT       — the agent name (pm, coder, writer, etc.)
#   SLEEP       — seconds to sleep before work (0 or more)
#   _CLI_MAX_TASKS — optional CLI override for MAX_TASKS_PER_WAKE (may be "")
#   TEAM_ROOT   — resolved kanban root path
#   SCRIPT_DIR  — absolute path to the directory containing the wake script
#
# Public entry point
# -------------------
#   wake_bootstrap_init
#       Runs the full bootstrap sequence exactly once, in the exact order
#       the trap below preserves: lib sourcing (project_paths, projects,
#       task_ids, discovery, workflow, config_loader, dev_tree, pp_run_ops,
#       overwatch_protocol, error_contract) -> env_bootstrap.sh ->
#       shell-env -> Python >=3.12 guard -> AI_AUTH_MODE pre-read ->
#       secrets -> operator INI overrides -> load_config ->
#       container-tier authorization scrub -> coverage switch scrub
#       (PGAI_TESTING_COVERAGE) -> strict mode (`set -euo pipefail`) enabled for the remainder of the wake process
#       -> script_common.sh + EXIT/SIGTERM/SIGINT/SIGHUP traps -> tunables ->
#       paths -> log directory/lock directory creation -> log() defined ->
#       CLI background-wait ceiling derived (BASH_MAX_TIMEOUT_MS, from
#       MAX_TASK_SECONDS + KILL_GRACE_SECONDS; skipped when an operator
#       override is already set, or when MAX_TASK_SECONDS=0).
#
#       Sourcing order is THE trap here (see wake_common.sh's own Notes):
#       moving the strict-mode enable point earlier or later changes failure
#       behavior across the whole wake substrate. wake_bootstrap_init exists
#       so that order lives in exactly one function body instead of being
#       re-derivable only from top-level sourcing order; it is called
#       unconditionally at the bottom of this file so that sourcing
#       wake_bootstrap.sh has the identical effect sourcing the old inline
#       wake_common.sh preamble had — every side effect below fires the
#       moment this file is sourced, not on some later explicit call.
#
# Other functions defined in this file (_resolve_debug_gate,
# _resolve_training_gate, require_file, log) are shared primitives used
# throughout wake_common.sh and the wake provider scripts (claude.sh,
# codex.sh) — not private helpers of wake_bootstrap_init alone. They are
# defined as a side effect of wake_bootstrap_init running (see the function
# body below) and are available globally from then on, exactly as they were
# when this code lived inline in wake_common.sh.
#
# Dependencies
# ------------
#   Everything wake_common.sh itself depended on for this code region:
#   team/scripts/lib/project_paths.sh, projects.sh, task_ids.sh,
#   discovery.sh, workflow.sh, config_loader.sh, dev_tree.sh, pp_run_ops.sh,
#   overwatch_protocol.sh, error_contract.sh, env_bootstrap.sh,
#   script_common.sh (all resolved relative to SCRIPT_DIR, exactly as
#   before). read_ini (sourced transitively via project_paths.sh ->
#   ini_parser.sh) must be available before the AI_AUTH_MODE pre-read below.
#
# Sourced by team/scripts/lib/wake_common.sh immediately after the caller's
# contract variables are set. Must be sourced before wake_task_state.sh and
# before any of wake_common.sh's own remaining code, since both depend on
# globals this file establishes (KANBAN_ROOT, TASKS_ROOT, BACKLOG, LOG_FILE,
# the tunables, and the log() function).

wake_bootstrap_init() {
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

# --- Source pp_run_ops helper (cwd-independent python3 -m invocations) ---
# Provides pp_run_ops() so all pgai_agent_kanban.* module calls are
# cwd-independent and do not require a pre-set PYTHONPATH from the caller.
# shellcheck source=scripts/lib/pp_run_ops.sh
source "${SCRIPT_DIR}/../lib/pp_run_ops.sh"

# --- Source OVERWATCH protocol helpers (transient_retry_* substrate) ---
# Provides transient_retry_limit / transient_retry_field / transient_retry_write:
# the ONE durable machine-readable retry record used by
# handle_working_after_exit and recheck_blocked_tasks below. Side-effect-free
# on source (no top-level commands run; KANBAN_ROOT/OVERWATCH_PROJECT are only
# needed by the overwatch_backup_file/overwatch_log_action/overwatch_halt_first_fix
# functions, none of which wake calls).
# shellcheck source=scripts/lib/overwatch_protocol.sh
source "${SCRIPT_DIR}/../lib/overwatch_protocol.sh"

# --- Source the ## Block Record writers (block_record.sh) ---
# Provides block_record_write and its evidence helpers (block_record_owner_role,
# block_record_evidence, block_record_worktree_failure_code,
# block_record_zero_progress_code): every wake block site records why it
# blocked a task through them. Side-effect-free on source (defines functions).
# shellcheck source=scripts/lib/block_record.sh
source "${SCRIPT_DIR}/../lib/block_record.sh"

# --- Source overwatch_backlog_seed.sh (overwatch_backlog_seed_content) ---
# Needed by run_project_chain's self-heal path (wake_driver.sh): a missing
# tasks/queues/overwatch_backlog.md at wake time is recreated
# rather than treated as a reason to skip the project, so the same seeded
# content create-project.sh and the upgrade migration write is available
# here too. Side-effect-free on source (defines one function, runs nothing).
# shellcheck source=scripts/lib/overwatch_backlog_seed.sh
source "${SCRIPT_DIR}/../lib/overwatch_backlog_seed.sh"

# --- Source the shared dual-channel error-contract emitter ---
# coder_writer_completion_gate's refusal narrations speak both channels
# through this one primitive: see team/scripts/lib/error_contract.sh for the
# house shape.
# shellcheck source=scripts/lib/error_contract.sh
source "${SCRIPT_DIR}/../lib/error_contract.sh"

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

# --- Source optional claude-env for provider-runtime tuning ---
# env_bootstrap.sh's own claude-env sourcing block (lib/env_bootstrap.sh,
# "Source claude-env when present") is unreachable from the source call two
# blocks above: this function always pre-exports PGAI_AGENT_KANBAN_ROOT_PATH
# from TEAM_ROOT before sourcing env_bootstrap.sh (see the "Why pre-export"
# comment above), which trips env_bootstrap.sh's own idempotency guard --
# "already set -> absolutize, export, return 0 immediately" -- the exact
# early-return branch that sits BEFORE the claude-env block in that file.
# claude-env is documented (claude-env_example) to be sourced "on every
# entry path that resolves PGAI_AGENT_KANBAN_ROOT_PATH -- including a cron
# or pseudocron firing", so the wake path needs its own direct source here,
# mirroring the shell-env re-source immediately above and reproducing
# env_bootstrap.sh's own syntax-check-before-source contract for this file
# (never source a syntactically broken file: `source` executes a script's
# earlier top-level statements as it parses along, so sourcing a broken
# claude-env directly could silently half-configure BASH_MAX_TIMEOUT_MS and
# whatever else an operator has tuned here before the parser ever reaches
# the bad line). Optional: most installs never create a claude-env, and an
# absent file is a clean no-op -- no error, no warning, no output.
if [[ -f "$TEAM_ROOT/claude-env" ]]; then
    if ! bash -n "$TEAM_ROOT/claude-env" 2>/dev/null; then
        pgai_emit_error_contract \
            "wake_bootstrap.claude_env_syntax_error" \
            "claude-env at ${TEAM_ROOT}/claude-env has a bash syntax error and was not sourced." \
            "Run: bash -n ${TEAM_ROOT}/claude-env -- fix the reported syntax error, then re-run this script."
        # CENSUS-ANCHOR: wake-bootstrap-claude-env-01
        # Refusal class: BLOCK-WORTHY (docs/blocking-site-census.md) -- a
        # half-sourced claude-env is a state-corrupting failure a cron or
        # pseudocron firing has no human present to notice, so the wake
        # firing must stop rather than continue on a partial,
        # unverifiable environment. This refusal never prints the VALUE of
        # anything claude-env sets, only its path.
        exit 1
    fi
    # shellcheck source=/dev/null
    source "$TEAM_ROOT/claude-env"
fi

# --- Python >= 3.12 guard (after shell-env so post-venv interpreter is checked) ---
# The version is read from `python3 --version` ("Python X.Y.Z", a suffix such
# as "rc1" or "+" after Z ignored); no python3 on PATH, or output with no
# X.Y.Z in it, reads as 0.0.0.
_py_ver="0.0.0"
_py_maj=0
_py_min=0
if [[ "$(python3 --version 2>&1)" =~ ([0-9]+)\.([0-9]+)\.([0-9]+) ]]; then
    _py_maj="${BASH_REMATCH[1]}"
    _py_min="${BASH_REMATCH[2]}"
    _py_ver="${_py_maj}.${_py_min}.${BASH_REMATCH[3]}"
fi
if [[ "$_py_maj" -lt 3 ]] || [[ "$_py_maj" -eq 3 && "$_py_min" -lt 12 ]]; then
    echo "FATAL: pgai-agent-kanban requires Python 3.12+. Found: ${_py_ver}." >&2
    # BLOCK-WORTHY: the wake substrate requires Python 3.12+ throughout --
    # continuing on an older interpreter risks silent, hard-to-diagnose
    # failures deeper in the chain rather than one clear stop here.
    # CENSUS-ANCHOR: wake-bootstrap-python-version-01
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
    # BLOCK-WORTHY: an unloadable kanban.cfg leaves every downstream
    # tunable/path resolution undefined -- the wake firing must stop
    # rather than continue on a half-configured environment.
    # CENSUS-ANCHOR: wake-bootstrap-load-config-failure-01
    exit 1
fi

# --- Strip the container-tier authorization from the wake environment ---
# The container tier (image builds, base-image pulls, containers) is
# operator-invoked only. An operator shell, shell-env, claude-env, secrets
# or the INI overrides above may export its authorization variable; it is
# removed here, after every one of them has been sourced, so no agent this
# wake spawns inherits it (team/scripts/lib/container_tier.sh).
# shellcheck source=scripts/lib/container_tier.sh
source "${SCRIPT_DIR}/../lib/container_tier.sh"
container_tier_scrub_env
# --- Strip the operator's coverage switch from the wake environment ---
# PGAI_TESTING_COVERAGE=1 lets an operator collect coverage for one gated
# runner invocation while kanban.cfg [testing] coverage is false. It is an
# operator's switch, never an agent's: removed here, beside the
# container-tier scrub and after the same sourced files, so no agent this
# wake spawns inherits it (team/scripts/lib/gated_runner_lanes.sh).
# shellcheck source=scripts/lib/gated_runner_lanes.sh
source "${SCRIPT_DIR}/../lib/gated_runner_lanes.sh"
gated_runner_coverage_scrub_env
# --- Now enable strict mode for our own code ---
set -euo pipefail

# Deprecation warning state — one-shot per wake-batch firing.
_VERBOSE_MODE_DEPRECATION_WARNED=false
_REASONING_TRACE_DEPRECATION_WARNED=false

# --- Clean exit handling ---
# cleanup_on_exit (reap any lingering child processes on exit) is the
# canonical definition in lib/script_common.sh; source it rather than
# redefine it here so lint_lib_function_dedupe.py sees exactly one
# lib/*.sh definition of the name. SIGHUP is trapped like SIGTERM: an
# operator closing the terminal a wake-now.sh run started from is a stop
# like any other. Whichever signal lands, cleanup_on_exit first stops the
# provider's process group (CLEANUP_ON_EXIT_PROCESS_GROUP, set by
# wake_driver_invoke.sh while a provider runs) and only then runs the
# pre-hook that releases this wake's locks (lock_provenance.sh).
# shellcheck source=scripts/lib/script_common.sh
source "${SCRIPT_DIR}/../lib/script_common.sh"
trap cleanup_on_exit EXIT
trap cleanup_on_exit SIGTERM SIGINT SIGHUP

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
# Because the provider CLI's own output for every task in the firing lands
# here rather than under the task folder, wd_invoke_task records which
# batch log (and which byte span of it) each dispatch wrote in the task's
# own logs/dispatch.jsonl — see dispatch_record_* in overwatch_protocol.sh.
LOG_DIR="${KANBAN_ROOT}/logs/agents"
LOG_FILE="${LOG_DIR}/${AGENT}-batch-$(date +%Y%m%d-%H%M%S).log"

# --- Lock paths ---
LOCK_DIR="${TEAM_ROOT}/locks"

mkdir -p "$LOG_DIR" "$LOCK_DIR"

WAKE_START_EPOCH=$(date +%s)

# log() is the narration channel every sourced stage (discovery included)
# calls into. It must never write to real stdout: any function whose
# result a caller captures via `$(...)` — discovery_step_bugs collecting a
# list of bundled item paths is the sharpest example — would otherwise get
# a narration line spliced into its data, indistinguishable from a real
# path until something downstream tries to read it. `tee`'s own stdout
# copy is therefore redirected to stderr (`>&2`): the line still reaches
# the log file AND the operator's terminal/cron log (cron redirects both
# streams to the same file), it just never reaches fd 1, so it can never
# contaminate a command substitution.
log() {
  echo "[$(date -Iseconds)] wake(${AGENT}): $*" | tee -a "$LOG_FILE" >&2
}

# --- CLI background-wait ceiling (derived from the per-task budget) ---
# The installed Claude CLI enforces its own background-wait ceiling via
# BASH_MAX_TIMEOUT_MS (default 600000ms / 10 minutes, per the CLI's own
# --help and docs). That ceiling is an order of magnitude smaller than the
# kanban's own per-task budget (MAX_TASK_SECONDS, default 5400s) plus its
# kill grace (KILL_GRACE_SECONDS, default 30s), so an un-derived CLI
# ceiling silently truncates work the kanban's watchdog was still happily
# waiting on. We derive it here instead of pinning a literal: a pinned
# ceiling stops tracking the moment an operator raises MAX_TASK_SECONDS,
# which is exactly the failure this derivation exists to prevent. The
# CLI ceiling must be at least as generous as the kanban's own watchdog,
# so it is derived from the same two live values the watchdog uses,
# converted to milliseconds because that is the unit BASH_MAX_TIMEOUT_MS
# takes.
#
# MAX_TASK_SECONDS=0 disables the per-task watchdog by design (see the
# hard-preemption comment above wake_driver_invoke.sh's watchdog block). A
# disabled watchdog asked for no ceiling, so deriving one from
# KILL_GRACE_SECONDS alone would invent a bound nothing requested — no
# ceiling is exported in that case, leaving the CLI's own default (or
# whatever an operator has configured) untouched.
#
# Operator-env-wins: claude-env is sourced (via env_bootstrap.sh, earlier in
# this function) before this point runs, so an operator override already
# lives in BASH_MAX_TIMEOUT_MS by the time we get here. This derivation only
# fires when the variable is unset or empty — the same no-masking posture
# env_bootstrap.sh already applies to PGAI_AGENT_KANBAN_ROOT_PATH.
#
# BG-WAIT-CEILING-DERIVATION-BEGIN — sentinel comment marking the block a
# unit test (team/tests/unit/test_wake_bootstrap_bg_ceiling.py) extracts
# verbatim to exercise this arithmetic without sourcing this function's full
# dependency chain. Purely a test-extraction anchor; it changes no behavior.
if [[ -z "${BASH_MAX_TIMEOUT_MS:-}" ]]; then
    if [[ "${MAX_TASK_SECONDS}" -gt 0 ]]; then
        export BASH_MAX_TIMEOUT_MS=$(( (MAX_TASK_SECONDS + KILL_GRACE_SECONDS) * 1000 ))
        log "background-wait ceiling: derived BASH_MAX_TIMEOUT_MS=${BASH_MAX_TIMEOUT_MS} from MAX_TASK_SECONDS=${MAX_TASK_SECONDS}s + KILL_GRACE_SECONDS=${KILL_GRACE_SECONDS}s"
    else
        log "background-wait ceiling: MAX_TASK_SECONDS=0 (per-task watchdog disabled); BASH_MAX_TIMEOUT_MS left unset"
    fi
else
    log "background-wait ceiling: operator override in force (BASH_MAX_TIMEOUT_MS already set via claude-env)"
fi
# BG-WAIT-CEILING-DERIVATION-END

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

    # Capture the whole normalised list, then match it from a here-string, so
    # grep -q stopping at its first match cannot SIGPIPE a still-writing stage.
    # The sentinel keeps trailing empty entries that $(...) would strip.
    local va_lines
    va_lines="$(printf '%s\n' "$va" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; printf x)"
    va_lines="${va_lines%$'\n'x}"
    if grep -qx "$agent" <<< "$va_lines"; then
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

    # Capture the whole normalised list, then match it from a here-string, so
    # grep -q stopping at its first match cannot SIGPIPE a still-writing stage.
    # The sentinel keeps trailing empty entries that $(...) would strip.
    local ta_lines
    ta_lines="$(printf '%s\n' "$ta" | tr ',' '\n' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'; printf x)"
    ta_lines="${ta_lines%$'\n'x}"
    if grep -qx "$agent" <<< "$ta_lines"; then
        echo "true"
        return 0
    fi

    echo "false"
}

require_file() {
  local f="$1"
  # BLOCK-WORTHY: a caller-required file that does not exist means the
  # wake substrate cannot proceed with whatever it needed that file for.
  # CENSUS-ANCHOR: wake-bootstrap-require-file-missing-01
  [[ -f "$f" ]] || { log "missing file: $f"; exit 1; }
}
}

# Invoke immediately: sourcing this file performs the bootstrap side
# effects, matching the behavior of the former inline wake_common.sh code.
wake_bootstrap_init
