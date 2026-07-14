#!/usr/bin/env bash
# team/scripts/overwatch-sweep.sh
#
# OVERWATCH Tier-1 deterministic sweep runner.
#
# Aggregation form: iterates ALL registered projects from the project registry.
# No --project argument — the runner sweeps every project on each firing.
#
# For each project this script:
#   1. Checks global HALT and HALT_OVERWATCH flags — suppresses the entire run
#      if either is present.
#   2. Bootstraps the OVERWATCH state directory (backups/, locks/).
#   3. Sources every check-*.sh module from scripts/lib/overwatch-checks/ in
#      lexical order and invokes its overwatch_check_<slug> function.
#   4. Writes a per-project sweep log under:
#        $KANBAN_ROOT/projects/<name>/logs/overwatch/sweep.log
#   5. Records sweep start/end entries in the project's action log via
#      overwatch_log_action (from overwatch_protocol.sh).
#
# Checks are sourced per-project with OVERWATCH_PROJECT set to the current
# project name. Each check reads OVERWATCH_PROJECT and KANBAN_ROOT from the
# environment; it must NOT accept arguments.
#
# HALT gates (both independent — either alone suppresses the run):
#   $KANBAN_ROOT/HALT          — global halt; stops all agents
#   $KANBAN_ROOT/HALT_OVERWATCH — overwatch-specific halt; stops only sweep
#
# Exit codes:
#   0  — completed (all projects swept, or suppressed by a halt flag)
#   1  — fatal setup error (library missing, registry unreadable)
#
# Usage:
#   team/scripts/overwatch-sweep.sh
#
# Environment:
#   KANBAN_ROOT / PGAI_AGENT_KANBAN_ROOT_PATH — kanban installation root
#   PGAI_AGENT_KANBAN_TEMP_DIR                — temp root (resolved via pgai_temp_dir in temp.sh)
#
# No options accepted. Cadence is controlled via the installed cron tier file.

set -euo pipefail
# shellcheck source=lib/env_bootstrap.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/env_bootstrap.sh"

# ---------------------------------------------------------------------------
# Locate this script's directory and resolve library paths.
# ---------------------------------------------------------------------------
_SWEEP_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_SWEEP_LIB_DIR="${_SWEEP_SCRIPT_DIR}/lib"
_SWEEP_CHECKS_DIR="${_SWEEP_LIB_DIR}/overwatch-checks"

# ---------------------------------------------------------------------------
# Source required libraries.
# ---------------------------------------------------------------------------
if [[ ! -f "${_SWEEP_LIB_DIR}/temp.sh" ]]; then
    echo "overwatch-sweep: fatal: temp.sh not found: ${_SWEEP_LIB_DIR}/temp.sh" >&2
    exit 1
fi
# shellcheck source=lib/temp.sh
source "${_SWEEP_LIB_DIR}/temp.sh"

if [[ ! -f "${_SWEEP_LIB_DIR}/project_paths.sh" ]]; then
    echo "overwatch-sweep: fatal: project_paths.sh not found" >&2
    exit 1
fi
# shellcheck source=lib/project_paths.sh
source "${_SWEEP_LIB_DIR}/project_paths.sh"

if [[ ! -f "${_SWEEP_LIB_DIR}/projects.sh" ]]; then
    echo "overwatch-sweep: fatal: projects.sh not found" >&2
    exit 1
fi
# shellcheck source=lib/projects.sh
source "${_SWEEP_LIB_DIR}/projects.sh"

if [[ ! -f "${_SWEEP_LIB_DIR}/overwatch_lib.sh" ]]; then
    echo "overwatch-sweep: fatal: overwatch_lib.sh not found" >&2
    exit 1
fi
# shellcheck source=lib/overwatch_lib.sh
source "${_SWEEP_LIB_DIR}/overwatch_lib.sh"

if [[ ! -f "${_SWEEP_LIB_DIR}/overwatch_protocol.sh" ]]; then
    echo "overwatch-sweep: fatal: overwatch_protocol.sh not found" >&2
    exit 1
fi
# shellcheck source=lib/overwatch_protocol.sh
source "${_SWEEP_LIB_DIR}/overwatch_protocol.sh"

# ---------------------------------------------------------------------------
# Resolve KANBAN_ROOT.
# ---------------------------------------------------------------------------
if [[ -z "${KANBAN_ROOT:-}" ]]; then
    KANBAN_ROOT="${PGAI_AGENT_KANBAN_ROOT_PATH}"
fi
export KANBAN_ROOT

if [[ ! -d "${KANBAN_ROOT}" ]]; then
    echo "overwatch-sweep: fatal: KANBAN_ROOT does not exist: ${KANBAN_ROOT}" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# HALT gate 1: global HALT flag.
# When $KANBAN_ROOT/HALT exists, all agents stop — including the sweep.
# ---------------------------------------------------------------------------
_SWEEP_HALT_FLAG="${KANBAN_ROOT}/HALT"
if [[ -f "${_SWEEP_HALT_FLAG}" ]]; then
    echo "overwatch-sweep: HALT flag present (${_SWEEP_HALT_FLAG}); suppressing sweep run." >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# HALT gate 2: HALT_OVERWATCH flag.
# When $KANBAN_ROOT/HALT_OVERWATCH exists, the sweep is suppressed but normal
# agent activity continues.
# ---------------------------------------------------------------------------
_SWEEP_HALT_OVERWATCH_FLAG="${KANBAN_ROOT}/HALT_OVERWATCH"
if [[ -f "${_SWEEP_HALT_OVERWATCH_FLAG}" ]]; then
    echo "overwatch-sweep: HALT_OVERWATCH flag present (${_SWEEP_HALT_OVERWATCH_FLAG}); suppressing sweep run." >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Enumerate registered projects via the canonical project registry.
# projects_cfg_list echoes project names in priority order, one per line.
# ---------------------------------------------------------------------------
_sweep_project_list=""
if ! _sweep_project_list="$(projects_cfg_list 2>/dev/null)"; then
    echo "overwatch-sweep: fatal: projects_cfg_list failed — is projects.cfg present and non-empty?" >&2
    exit 1
fi

if [[ -z "${_sweep_project_list}" ]]; then
    echo "overwatch-sweep: no projects registered in projects.cfg; nothing to sweep." >&2
    exit 0
fi

# ---------------------------------------------------------------------------
# Discover check modules from the checks directory.
# We collect the list once and reuse it for every project.
# ---------------------------------------------------------------------------
_sweep_check_scripts=()
if [[ -d "${_SWEEP_CHECKS_DIR}" ]]; then
    while IFS= read -r _cs; do
        [[ -f "${_cs}" ]] && _sweep_check_scripts+=("${_cs}")
    done < <(find "${_SWEEP_CHECKS_DIR}" -maxdepth 1 -name 'check-*.sh' -type f | sort)
fi

if (( ${#_sweep_check_scripts[@]} == 0 )); then
    echo "overwatch-sweep: warning: no check-*.sh modules found in ${_SWEEP_CHECKS_DIR}" >&2
fi

# ---------------------------------------------------------------------------
# Record a firing timestamp for this sweep run so all backup entries share it.
# ---------------------------------------------------------------------------
OVERWATCH_FIRING_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export OVERWATCH_FIRING_TIMESTAMP

echo "overwatch-sweep: firing at ${OVERWATCH_FIRING_TIMESTAMP}" >&2

# ---------------------------------------------------------------------------
# Per-project sweep function.
# Called once per project in the registry.
#
# Args:
#   $1 — project_name
#
# Returns 0 on success; non-zero if the sweep encountered a fatal per-project
# error (the runner continues to the next project regardless).
# ---------------------------------------------------------------------------
_sweep_one_project() {
    local project_name="$1"
    export OVERWATCH_PROJECT="${project_name}"

    local project_root="${KANBAN_ROOT}/projects/${project_name}"
    local sweep_log_dir="${project_root}/logs/overwatch"
    local sweep_log="${sweep_log_dir}/sweep.log"
    local overwatch_state_dir="${project_root}/overwatch"
    local overwatch_backups_dir="${overwatch_state_dir}/backups"

    # Guard: project directory must exist.
    if [[ ! -d "${project_root}" ]]; then
        echo "overwatch-sweep: project '${project_name}': project root missing: ${project_root}; skipping" >&2
        return 1
    fi

    # Bootstrap OVERWATCH state directory for this project (idempotent).
    mkdir -p "${overwatch_state_dir}" "${overwatch_backups_dir}" 2>/dev/null || true

    # Bootstrap per-project sweep log directory (distinct from action-log state dir).
    mkdir -p "${sweep_log_dir}" 2>/dev/null || true

    local ts_now
    ts_now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    # Append sweep-start entry to the human-readable sweep log.
    printf '%s\toverwatch-sweep\t%s\tsweep-start\tnone\tTier-1 sweep started\n' \
        "${ts_now}" "${project_name}" >> "${sweep_log}" || true

    echo "overwatch-sweep: [${project_name}] sweep start" >&2

    # Log sweep-start to the canonical action log.
    overwatch_log_action \
        "overwatch-sweep" \
        "${project_name}" \
        "sweep-start" \
        "none" \
        "Tier-1 sweep started; ${#_sweep_check_scripts[@]} check(s) to run" \
    2>/dev/null || true

    local check_pass_count=0
    local check_fail_count=0

    # Run each check module in lexical order.
    local check_script check_slug fn_name check_exit
    for check_script in "${_sweep_check_scripts[@]}"; do
        # Re-check HALT flags before each check (a manual halt mid-sweep should stop promptly).
        if [[ -f "${_SWEEP_HALT_FLAG}" ]]; then
            echo "overwatch-sweep: [${project_name}] HALT detected mid-sweep; stopping." >&2
            overwatch_log_action \
                "overwatch-sweep" \
                "${project_name}" \
                "sweep-halted" \
                "none" \
                "Global HALT flag detected mid-sweep after $(( check_pass_count + check_fail_count )) check(s)" \
            2>/dev/null || true
            printf '%s\toverwatch-sweep\t%s\tsweep-halted\tnone\tGlobal HALT detected mid-sweep\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" >> "${sweep_log}" || true
            return 0
        fi
        if [[ -f "${_SWEEP_HALT_OVERWATCH_FLAG}" ]]; then
            echo "overwatch-sweep: [${project_name}] HALT_OVERWATCH detected mid-sweep; stopping." >&2
            overwatch_log_action \
                "overwatch-sweep" \
                "${project_name}" \
                "sweep-halted" \
                "none" \
                "HALT_OVERWATCH flag detected mid-sweep after $(( check_pass_count + check_fail_count )) check(s)" \
            2>/dev/null || true
            printf '%s\toverwatch-sweep\t%s\tsweep-halted\tnone\tHALT_OVERWATCH detected mid-sweep\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" >> "${sweep_log}" || true
            return 0
        fi

        # Derive the check slug from the filename: check-<slug>.sh -> <slug>
        check_slug="$(basename "${check_script}" .sh | sed 's/^check-//')"
        # Replace hyphens with underscores to form the function name.
        fn_name="overwatch_check_$(echo "${check_slug}" | tr '-' '_')"

        echo "overwatch-sweep: [${project_name}] running ${check_slug}" >&2

        # Source the check script to load its function into the current shell.
        # Checks must be sourceable without side effects.
        # shellcheck source=/dev/null
        if ! source "${check_script}" 2>/dev/null; then
            echo "overwatch-sweep: [${project_name}] failed to source ${check_script}; skipping" >&2
            check_fail_count=$(( check_fail_count + 1 ))
            printf '%s\toverwatch-sweep\t%s\tcheck-source-error\tnone\t%s\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" "${check_slug}" >> "${sweep_log}" || true
            continue
        fi

        # Verify the expected function was defined.
        if ! declare -f "${fn_name}" >/dev/null 2>&1; then
            echo "overwatch-sweep: [${project_name}] check module ${check_slug} did not define ${fn_name}; skipping" >&2
            check_fail_count=$(( check_fail_count + 1 ))
            printf '%s\toverwatch-sweep\t%s\tcheck-fn-missing\tnone\t%s\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" "${check_slug}" >> "${sweep_log}" || true
            continue
        fi

        # Invoke the check function.
        check_exit=0
        "${fn_name}" || check_exit=$?

        if (( check_exit == 0 )); then
            check_pass_count=$(( check_pass_count + 1 ))
            printf '%s\toverwatch-sweep\t%s\tcheck-ok\tnone\t%s\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" "${check_slug}" >> "${sweep_log}" || true
        else
            check_fail_count=$(( check_fail_count + 1 ))
            printf '%s\toverwatch-sweep\t%s\tcheck-error\tnone\t%s exit=%d\n' \
                "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" "${check_slug}" "${check_exit}" >> "${sweep_log}" || true
            echo "overwatch-sweep: [${project_name}] check ${check_slug} returned ${check_exit}" >&2
        fi
    done

    # Append sweep-end entry.
    local ts_end
    ts_end="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '%s\toverwatch-sweep\t%s\tsweep-end\tnone\t%d ok, %d error\n' \
        "${ts_end}" "${project_name}" "${check_pass_count}" "${check_fail_count}" >> "${sweep_log}" || true

    overwatch_log_action \
        "overwatch-sweep" \
        "${project_name}" \
        "sweep-end" \
        "none" \
        "${check_pass_count} check(s) ok, ${check_fail_count} check(s) error" \
    2>/dev/null || true

    echo "overwatch-sweep: [${project_name}] sweep end — ${check_pass_count} ok, ${check_fail_count} error" >&2
    return 0
}

# ---------------------------------------------------------------------------
# Main loop: sweep each registered project.
# ---------------------------------------------------------------------------
_sweep_total_projects=0
_sweep_total_errors=0

while IFS= read -r _project; do
    [[ -z "${_project}" ]] && continue
    _sweep_total_projects=$(( _sweep_total_projects + 1 ))

    _proj_exit=0
    _sweep_one_project "${_project}" || _proj_exit=$?

    if (( _proj_exit != 0 )); then
        _sweep_total_errors=$(( _sweep_total_errors + 1 ))
    fi
done <<< "${_sweep_project_list}"

echo "overwatch-sweep: complete — ${_sweep_total_projects} project(s) swept, ${_sweep_total_errors} error(s)" >&2

# Exit 0 even when individual project sweeps had non-fatal errors.
# Error details are in the per-project sweep logs and action logs.
exit 0
