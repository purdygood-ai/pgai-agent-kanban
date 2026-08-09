#!/usr/bin/env bash
# team/scripts/lib/config_loader.sh
#
# Single source of truth for every kanban.cfg key consumed by the wake chain.
#
# Usage
# -----
#   source "$(dirname "${BASH_SOURCE[0]}")/config_loader.sh"
#   load_config /path/to/kanban.cfg          # validate and export
#   config_get paths dev_tree_path           # optional explicit accessor
#
# Safety invariants
# -----------------
#   - No top-level side effects when sourced — only function definitions.
#   - load_config reads the file once, validates, then exports env vars.
#   - config_get performs a single read_ini call against the already-validated
#     config path (set by load_config), or against an explicit path argument.
#   - Failure messages name the missing key AND the config file path.
#   - This module depends on ini_parser.sh (read_ini) being sourced already,
#     OR it sources ini_parser.sh from the same directory if needed.
#
# Key Registry
# ------------
# Every kanban.cfg key known to the wake chain is listed here.
# Status: REQUIRED = fail loud + non-zero if absent or empty.
#         OPTIONAL = apply default if absent; never fails.
# Default shown is the canonical single default for that key.
#
# [paths]
#   dev_tree_path           OPTIONAL  ""  (install-time convenience and fallback
#                                          for projects whose project.cfg omits
#                                          dev_tree_path; an install may leave
#                                          this empty)
#   tmp_root                OPTIONAL  /tmp
#   tmp_subdir              OPTIONAL  pgai_kanban_tmp
#
# [providers]
#   ai_auth_mode            OPTIONAL  oauth
#   active                  OPTIONAL  claude
#   initial                 OPTIONAL  claude
#   available               OPTIONAL  claude codex
#   llm_effort              OPTIONAL  ""  (global reasoning-effort level; resolved
#                                          per role into PGAI_<ROLE>_EFFORT using
#                                          the same precedence tier and narration-
#                                          source discipline as PGAI_<ROLE>_MODEL —
#                                          see resolve_role_effort() below. Absent
#                                          everywhere = no effort resolved, nothing
#                                          exported, byte-identical to today.)
#
# [chain]
#   pm_mode                 OPTIONAL  automatic
#   cron_stagger_minutes    OPTIONAL  2
#   agent_lock_timeout_seconds  OPTIONAL  3600
#
# [dashboard]
#   rows_per_column         OPTIONAL  21
#   min_rows_per_column     OPTIONAL  13
#   max_rows_per_column     OPTIONAL  34
#   min_rows_per_project    OPTIONAL  3
#   max_rows_per_project    OPTIONAL  8
#   max_rows                OPTIONAL  20
#   dashboard_status_glyph  OPTIONAL  ■  (single visible character; multi-char values
#                                         are rejected non-zero naming the key)
#
# [wake]
#   max_tasks_per_wake      OPTIONAL  5
#   max_runtime_seconds     OPTIONAL  14400
#   pause_between_tasks     OPTIONAL  5
#   stop_on_blocked         OPTIONAL  true
#   stop_file               OPTIONAL  ${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop
#   pm_max_tasks            OPTIONAL  15
#   max_task_seconds        OPTIONAL  5400
#   max_project_seconds     OPTIONAL  14400  (per-project runtime cap; 0 = disabled)
#   kill_grace_seconds      OPTIONAL  30
#   transient_retry_cap     OPTIONAL  3  (single ceiling for the durable
#                                         ## Transient Retry record; see
#                                         transient_retry_limit in
#                                         overwatch_protocol.sh)
#
# [models.claude]   — all OPTIONAL, default ""
#   default                               — fallback model for any unset role
#   pm, coder, writer, tester, cm, po    — per-role overrides
# [models.codex]    — all OPTIONAL, default ""
#   default                               — fallback model for any unset role
#   pm, coder, writer, tester, cm, po    — per-role overrides
# [models.gemini]   — all OPTIONAL, default ""
#   default                               — fallback model for any unset role
#   pm, coder, writer, tester, cm, po    — per-role overrides
#
# [cleanup]
#   retention_days          OPTIONAL  30
#   trivial_log_bytes       OPTIONAL  1700
#   trivial_log_hours       OPTIONAL  6

# ---------------------------------------------------------------------------
# Bootstrap: ensure read_ini is available.
# If ini_parser.sh has already been sourced (the normal case when wake_common.sh
# sources us), this is a cheap no-op guard.
# ---------------------------------------------------------------------------
if ! command -v read_ini >/dev/null 2>&1; then
    # shellcheck source=ini_parser.sh
    source "$(dirname "${BASH_SOURCE[0]}")/ini_parser.sh"
fi

# ---------------------------------------------------------------------------
# _CONFIG_LOADER_CFG_PATH — set by load_config; used by config_get.
# ---------------------------------------------------------------------------
_CONFIG_LOADER_CFG_PATH=""

# ---------------------------------------------------------------------------
# _PGAI_VALID_EFFORT_VALUES — the reasoning-effort levels recognized across
# both provider CLIs (union of the claude and codex lexicons). Deliberately
# permissive: the kanban does not enforce a per-model or per-provider
# matrix ("supported values vary by model" is an operator/provider concern,
# per the brief's deliberate-simplicity constraint) — this list only exists
# to catch clearly-wrong values (typos, stray text) at config-load time.
# ---------------------------------------------------------------------------
_PGAI_VALID_EFFORT_VALUES="minimal low medium high xhigh max"

# ---------------------------------------------------------------------------
# _config_loader_validate_effort <value>
#
# Returns 0 if <value> is a recognized reasoning-effort level, 1 otherwise.
# ---------------------------------------------------------------------------
_config_loader_validate_effort() {
    local value="$1"
    local candidate
    for candidate in $_PGAI_VALID_EFFORT_VALUES; do
        if [[ "$value" == "$candidate" ]]; then
            return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# resolve_role_effort <role>
#
# Resolve the reasoning-effort value in force for <role>, mirroring the
# PGAI_<ROLE>_MODEL precedence tier and narration-source discipline:
#
#   1. PGAI_<ROLE>_EFFORT already exported (env) — wins.
#   2. [providers] llm_effort from the most recently load_config'd cfg file
#      (a single global key; there is no per-role or per-provider matrix).
#   3. Neither set — absent. No effort resolved; this is today's behavior.
#
# This is the shared resolution mechanism both provider wake lanes
# (scripts/wake/claude.sh, scripts/wake/codex.sh) call to determine the
# effort value and its narration source, exactly as they call the model
# resolution logic today.
#
# On success, prints "<value>|<source>" to stdout and returns 0.
#   <source> is one of:
#     "PGAI_<ROLE>_EFFORT env"        (tier 1)
#     "[providers] llm_effort cfg"    (tier 2)
#     ""                              (absent — <value> is also empty)
# On an invalid value, writes an error to stderr naming the exact source
# key that supplied the bad value, prints nothing to stdout, and returns 1.
# ---------------------------------------------------------------------------
resolve_role_effort() {
    local role="${1:-}"
    if [[ -z "$role" ]]; then
        echo "ERROR: resolve_role_effort: role argument is required." >&2
        return 1
    fi

    local upper="${role^^}"
    local env_var="PGAI_${upper}_EFFORT"
    local env_value="${!env_var:-}"

    if [[ -n "$env_value" ]]; then
        if ! _config_loader_validate_effort "$env_value"; then
            echo "ERROR: resolve_role_effort: invalid reasoning-effort value '${env_value}' supplied by ${env_var}. Valid values: ${_PGAI_VALID_EFFORT_VALUES}." >&2
            return 1
        fi
        printf '%s|%s\n' "$env_value" "${env_var} env"
        return 0
    fi

    local cfg_value=""
    if [[ -n "$_CONFIG_LOADER_CFG_PATH" ]]; then
        cfg_value="$(read_ini "$_CONFIG_LOADER_CFG_PATH" providers llm_effort "")"
    fi

    if [[ -n "$cfg_value" ]]; then
        if ! _config_loader_validate_effort "$cfg_value"; then
            echo "ERROR: resolve_role_effort: invalid reasoning-effort value '${cfg_value}' supplied by [providers] llm_effort. Valid values: ${_PGAI_VALID_EFFORT_VALUES}." >&2
            return 1
        fi
        printf '%s|%s\n' "$cfg_value" "[providers] llm_effort cfg"
        return 0
    fi

    printf '|\n'
    return 0
}

# ---------------------------------------------------------------------------
# load_config <cfg_path>
#
# Read kanban.cfg, validate all REQUIRED keys, apply defaults for absent
# OPTIONAL keys, and export the canonical env vars.
#
# On success: returns 0 and all env vars are exported.
# On failure: writes an error to stderr naming the missing key + file path,
#             returns non-zero.
# ---------------------------------------------------------------------------
load_config() {
    local cfg="${1:-}"

    if [[ -z "$cfg" ]]; then
        echo "ERROR: load_config: no config file path supplied." >&2
        return 1
    fi

    if [[ ! -f "$cfg" ]]; then
        echo "ERROR: load_config: config file not found: $cfg" >&2
        return 1
    fi

    # Store path for config_get.
    _CONFIG_LOADER_CFG_PATH="$cfg"

    # -----------------------------------------------------------------------
    # OPTIONAL keys — apply defaults when absent, then export env vars.
    # Precedence: env var already set > config value > registry default.
    # -----------------------------------------------------------------------

    # [paths] dev_tree_path → PGAI_DEV_TREE_PATH (OPTIONAL; empty when not set)
    # Install-time convenience and fallback for projects whose project.cfg omits
    # dev_tree_path. An install may leave this empty;
    # downstream consumers that require a dev tree use require_dev_tree() from
    # scripts/lib/dev_tree.sh. No disk-existence check here — the loader
    # validates config shape, not infrastructure state.
    local _dev_tree_path
    _dev_tree_path="$(read_ini "$cfg" paths dev_tree_path "")"
    export PGAI_DEV_TREE_PATH="${PGAI_DEV_TREE_PATH:-${_dev_tree_path}}"

    # [paths] tmp_root + tmp_subdir → PGAI_AGENT_KANBAN_TEMP_DIR
    # If PGAI_AGENT_KANBAN_TEMP_DIR is already set in the environment, skip.
    if [[ -z "${PGAI_AGENT_KANBAN_TEMP_DIR:-}" ]]; then
        local _tmp_root _tmp_subdir
        _tmp_root="$(read_ini "$cfg" paths tmp_root "/tmp")"
        _tmp_subdir="$(read_ini "$cfg" paths tmp_subdir "pgai_kanban_tmp")"
        _tmp_root="${_tmp_root:-/tmp}"
        _tmp_subdir="${_tmp_subdir:-pgai_kanban_tmp}"
        export PGAI_AGENT_KANBAN_TEMP_DIR="${_tmp_root}/${_tmp_subdir}"
    fi

    # Export TMPDIR so that mktemp and other tools that respect TMPDIR
    # land under the configured framework temp root rather than /tmp.
    # TMPDIR is derived only from the already-resolved PGAI_AGENT_KANBAN_TEMP_DIR
    # value above — it MUST NOT re-read tmp_root/tmp_subdir or re-implement
    # the concat.  Mirror pgai_temp_dir's mkdir -p to ensure the directory exists.
    mkdir -p "${PGAI_AGENT_KANBAN_TEMP_DIR}"
    export TMPDIR="${PGAI_AGENT_KANBAN_TEMP_DIR}"

    # [providers] ai_auth_mode → AI_AUTH_MODE
    export AI_AUTH_MODE="${AI_AUTH_MODE:-$(read_ini "$cfg" providers ai_auth_mode oauth)}"

    # [chain] pm_mode → PGAI_KANBAN_PM_MODE
    export PGAI_KANBAN_PM_MODE="${PGAI_KANBAN_PM_MODE:-$(read_ini "$cfg" chain pm_mode automatic)}"

    # [dashboard] rows_per_column → DASHBOARD_ROWS_PER_COLUMN
    DASHBOARD_ROWS_PER_COLUMN="${DASHBOARD_ROWS_PER_COLUMN:-$(read_ini "$cfg" dashboard rows_per_column 21)}"
    export DASHBOARD_ROWS_PER_COLUMN

    # [dashboard] dashboard_status_glyph → DASHBOARD_STATUS_GLYPH
    # OPTIONAL; default is U+25A0 "■". Validated: must be exactly one visible
    # character. Multi-character values exit non-zero with an error naming the
    # key and the config file path — do not silently truncate.
    if [[ -z "${DASHBOARD_STATUS_GLYPH:-}" ]]; then
        local _glyph _char_count
        _glyph="$(read_ini "$cfg" dashboard dashboard_status_glyph "■")"
        _glyph="${_glyph:-■}"
        _char_count=$(printf '%s' "$_glyph" | wc -m)
        if [[ "$_char_count" -ne 1 ]]; then
            echo "ERROR: load_config: dashboard_status_glyph must be exactly one character (got ${_char_count}): ${cfg}" >&2
            return 1
        fi
        export DASHBOARD_STATUS_GLYPH="$_glyph"
    fi

    # [wake] max_tasks_per_wake → MAX_TASKS_PER_WAKE
    export MAX_TASKS_PER_WAKE="${MAX_TASKS_PER_WAKE:-$(read_ini "$cfg" wake max_tasks_per_wake 5)}"

    # [wake] max_runtime_seconds → MAX_RUNTIME_SECONDS
    export MAX_RUNTIME_SECONDS="${MAX_RUNTIME_SECONDS:-$(read_ini "$cfg" wake max_runtime_seconds 14400)}"

    # [wake] pause_between_tasks → PAUSE_BETWEEN_TASKS
    export PAUSE_BETWEEN_TASKS="${PAUSE_BETWEEN_TASKS:-$(read_ini "$cfg" wake pause_between_tasks 5)}"

    # [wake] stop_on_blocked → STOP_ON_BLOCKED
    export STOP_ON_BLOCKED="${STOP_ON_BLOCKED:-$(read_ini "$cfg" wake stop_on_blocked true)}"

    # [wake] stop_file → STOP_FILE
    # Default derives from the already-resolved temp dir (single-resolver discipline:
    # consumes PGAI_AGENT_KANBAN_TEMP_DIR set above; does not re-read tmp_root/tmp_subdir).
    export STOP_FILE="${STOP_FILE:-$(read_ini "$cfg" wake stop_file "${PGAI_AGENT_KANBAN_TEMP_DIR}/wakeup/stop")}"

    # [wake] pm_max_tasks → PM_MAX_TASKS
    export PM_MAX_TASKS="${PM_MAX_TASKS:-$(read_ini "$cfg" wake pm_max_tasks 15)}"

    # [wake] max_task_seconds → MAX_TASK_SECONDS
    # Hard per-task timeout.  On expiry the wake script sends SIGTERM then
    # SIGKILL (after kill_grace_seconds) and marks the task BLOCKED with the
    # exceeded-runtime reason text.  Default: 5400 (90 minutes).
    export MAX_TASK_SECONDS="${MAX_TASK_SECONDS:-$(read_ini "$cfg" wake max_task_seconds 5400)}"

    # [wake] max_project_seconds → MAX_PROJECT_SECONDS
    # Per-project runtime cap.  Each project in the multi-project iteration gets
    # its own elapsed clock that resets at the start of run_project_chain.  When
    # elapsed time for that project exceeds MAX_PROJECT_SECONDS the project task
    # loop exits and the next project gets its own fresh clock.  This prevents a
    # stalled project 1 from consuming the entire batch budget and starving
    # projects 2..N.  Default: same as MAX_RUNTIME_SECONDS when unset.
    # Set to 0 to disable the per-project cap (unlimited per project).
    export MAX_PROJECT_SECONDS="${MAX_PROJECT_SECONDS:-$(read_ini "$cfg" wake max_project_seconds "${MAX_RUNTIME_SECONDS:-14400}")}"

    # [wake] kill_grace_seconds → KILL_GRACE_SECONDS
    # Seconds between SIGTERM and SIGKILL when the per-task timeout fires.
    # Default: 30.
    export KILL_GRACE_SECONDS="${KILL_GRACE_SECONDS:-$(read_ini "$cfg" wake kill_grace_seconds 30)}"

    # [wake] transient_retry_cap → TRANSIENT_RETRY_CAP
    # The ONE ceiling for the durable ## Transient Retry record.
    # Consumed by wake_common.sh's handle_working_after_exit / recheck_blocked_tasks
    # and by OVERWATCH's check-transient-api-error.sh, both via
    # transient_retry_limit() in overwatch_protocol.sh — never hardcode a second
    # ceiling elsewhere. Default: 3.
    export TRANSIENT_RETRY_CAP="${TRANSIENT_RETRY_CAP:-$(read_ini "$cfg" wake transient_retry_cap 3)}"

    # [cleanup] retention_days → PGAI_CLEANUP_RETENTION_DAYS
    export PGAI_CLEANUP_RETENTION_DAYS="${PGAI_CLEANUP_RETENTION_DAYS:-$(read_ini "$cfg" cleanup retention_days 30)}"

    # [cleanup] trivial_log_bytes → PGAI_CLEANUP_TRIVIAL_LOG_BYTES
    export PGAI_CLEANUP_TRIVIAL_LOG_BYTES="${PGAI_CLEANUP_TRIVIAL_LOG_BYTES:-$(read_ini "$cfg" cleanup trivial_log_bytes 1700)}"

    # [cleanup] trivial_log_hours → PGAI_CLEANUP_TRIVIAL_LOG_HOURS
    export PGAI_CLEANUP_TRIVIAL_LOG_HOURS="${PGAI_CLEANUP_TRIVIAL_LOG_HOURS:-$(read_ini "$cfg" cleanup trivial_log_hours 6)}"

    # [models.<provider>] per-role model overrides → PGAI_<ROLE>_MODEL
    # The active provider is resolved lazily: if read_active_provider is available
    # (sourced by the provider lib), use it; otherwise fall back to the [providers]
    # active key from this same config.
    local _active_provider
    if command -v read_active_provider >/dev/null 2>&1; then
        _active_provider="$(read_active_provider "${_CONFIG_LOADER_CFG_PATH%/*}")"
    else
        _active_provider="$(read_ini "$cfg" providers active claude)"
        _active_provider="${_active_provider:-claude}"
    fi

    local _models_section="models.${_active_provider}"

    # Read the provider default once; applied uniformly to every unset role below.
    # Resolution order per role:
    #   1. PGAI_<ROLE>_MODEL already exported (e.g. from env) — wins, never overwritten.
    #   2. [models.<provider>] <role>   — per-role config value.
    #   3. [models.<provider>] default  — provider-wide fallback (this tier).
    # When all three are empty the role's env var remains unset; the wake path
    # will log a non-blocking warning and let the provider CLI choose its default.
    local _provider_default_model
    _provider_default_model="$(read_ini "$cfg" "$_models_section" "default" "")"

    local _role _model _upper _var
    for _role in pm coder writer tester cm po; do
        _upper="${_role^^}"
        _var="PGAI_${_upper}_MODEL"
        # Skip roles already set in the environment (tier 1 wins).
        if [[ -n "${!_var:-}" ]]; then
            continue
        fi
        # Tier 2: per-role config value.
        _model="$(read_ini "$cfg" "$_models_section" "$_role" "")"
        # Tier 3: provider default fallback when per-role is also empty.
        if [[ -z "$_model" ]]; then
            _model="$_provider_default_model"
        fi
        if [[ -n "$_model" ]]; then
            export "$_var"="$_model"
        fi
    done

    # [providers] llm_effort → PGAI_<ROLE>_EFFORT
    # Resolved exactly like PGAI_<ROLE>_MODEL above (env wins over cfg) via
    # resolve_role_effort() — a single global cfg key, no per-role or
    # per-provider matrix. Absent everywhere means nothing is exported,
    # which is byte-identical to today's behavior (no PGAI_<ROLE>_EFFORT
    # var exists at all).  An invalid value fails load_config loud, naming
    # the exact source key that supplied it.
    local _role_e _resolved_e _resolved_e_value _resolved_e_source _var_e
    for _role_e in pm coder writer tester cm po; do
        _resolved_e=""
        _resolved_e="$(resolve_role_effort "$_role_e")"
        if [[ $? -ne 0 ]]; then
            return 1
        fi
        _resolved_e_value="${_resolved_e%%|*}"
        _resolved_e_source="${_resolved_e#*|}"
        if [[ -n "$_resolved_e_value" ]]; then
            _var_e="PGAI_${_role_e^^}_EFFORT"
            export "$_var_e"="$_resolved_e_value"
        fi
        # _resolved_e_source is not exported here — it is derivable on demand
        # by re-calling resolve_role_effort(), which is what the provider
        # wake lanes do at dispatch time for their narration line.
        unset _resolved_e_source
    done

    return 0
}

# ---------------------------------------------------------------------------
# config_get <section> <key> [default]
#
# Explicit accessor for a single kanban.cfg key.
# Uses the config path set by the most recent load_config call.
# If load_config has not been called, returns default (or empty string).
#
# Example:
#   value=$(config_get paths dev_tree_path "")
# ---------------------------------------------------------------------------
config_get() {
    local section="${1:-}"
    local key="${2:-}"
    local default="${3:-}"

    if [[ -z "$section" || -z "$key" ]]; then
        echo "ERROR: config_get: section and key are required." >&2
        return 1
    fi

    if [[ -z "$_CONFIG_LOADER_CFG_PATH" ]]; then
        # load_config has not been called; return default.
        printf '%s' "$default"
        return 0
    fi

    read_ini "$_CONFIG_LOADER_CFG_PATH" "$section" "$key" "$default"
}
