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
#      if either is present. A project projects.cfg sets enabled=false for
#      (projects_cfg_enabled in lib/projects.sh) is skipped with one
#      "disabled ... skipped" line, before its firing lock or any check.
#   1a. Takes the project's OVERWATCH firing lock
#      ($KANBAN_ROOT/locks/overwatch-<name>.lock, overwatch_run_with_firing_lock
#      in overwatch_lib.sh) for the whole of that project's sweep. When another
#      firing -- a scheduled sweep, an on-BLOCK nudge, the overwatch wake --
#      holds it, the project is skipped: nothing runs, the skip is narrated
#      dual-channel on stderr and logged "firing-skipped:lock-held" in the
#      project's action log, and the sweep still exits 0.
#   2. Bootstraps the OVERWATCH state directory (backups/, locks/).
#   3. Sources every check-*.sh module from scripts/lib/overwatch-checks/ in
#      lexical order and invokes its overwatch_check_<slug> function — or,
#      with --only <slug>, that one module alone. The leaked-listener scan is
#      host-wide, so it runs once per sweep, before the first project, and
#      every project's check reads that one result
#      (OVERWATCH_LEAKED_LISTENER_SCAN) and acts only on its own listeners.
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
# A project HALT that OVERWATCH's own repair protocol created, and whose
# creating sweep is no longer running (overwatch_repair_halt_state in
# overwatch_protocol.sh reads its creator marker), is removed at that
# project's sweep start; any other HALT is left exactly as it is.
#
# Project HALT ($KANBAN_ROOT/projects/<name>/HALT) — narrows, never stops:
#   that project is still swept, so its read-only scans, reports and bug
#   filing run, but every fix is deferred. The sweep narrates one
#   "fixes deferred" row for the project, and each fix a check attempts is
#   declined by overwatch_halt_first_fix (sentinel 5, its own "fix deferred:
#   HALT present" row) with nothing changed. The one fix that still runs is
#   block triage handing a ship_policy_halt task to the resolution stage,
#   which exists to resolve the HALT CM wrote. Sibling projects are swept
#   as usual.
#
# Exit codes:
#   0  — completed (all projects swept, or suppressed by a halt flag)
#   1  — fatal setup error (library missing, registry unreadable)
#   2  — invalid argument (an unknown --only slug, a malformed --lock-wait,
#        an unrecognised option); refused dual-channel, nothing swept
#
# Usage:
#   team/scripts/overwatch-sweep.sh [--only <check-slug>] [--lock-wait <seconds>]
#
# Options:
#   --only <check-slug>    Run just the check-<check-slug>.sh module (e.g.
#                          --only block-triage) for every registered
#                          project. A leading "check-", a trailing ".sh" and
#                          underscores for hyphens are accepted. A slug that
#                          names no module is refused and the available
#                          slugs are listed. Without --only every module
#                          runs.
#   --lock-wait <seconds>  When a check finds a project's repo lock held,
#                          keep retrying it with backoff for up to this many
#                          seconds before declining (overwatch_halt_first_fix,
#                          via OVERWATCH_LOCK_WAIT_SECONDS). Default 0: one
#                          attempt, decline at once. The on-BLOCK nudge a
#                          wake chain fires after releasing that lock uses
#                          --only block-triage --lock-wait 60.
#   --help, -h             Print usage and exit 0.
#
# Environment:
#   KANBAN_ROOT / PGAI_AGENT_KANBAN_ROOT_PATH — kanban installation root
#   PGAI_AGENT_KANBAN_TEMP_DIR                — temp root (resolved via pgai_temp_dir in temp.sh)
#
# Cadence is controlled via the installed cron tier file (no options); the
# options above serve event-driven callers and operators.

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
# _sweep_check_scripts_found
# Print every check-*.sh module in the checks directory, one path per line,
# in lexical order (the order the sweep runs them in). Prints nothing when
# the directory is missing.
# ---------------------------------------------------------------------------
_sweep_check_scripts_found() {
    [[ -d "${_SWEEP_CHECKS_DIR}" ]] || return 0
    find "${_SWEEP_CHECKS_DIR}" -maxdepth 1 -name 'check-*.sh' -type f | sort
}

# ---------------------------------------------------------------------------
# _sweep_slug_of <check_script_path>
# Print the slug of a check module: check-<slug>.sh -> <slug>.
# ---------------------------------------------------------------------------
_sweep_slug_of() {
    local base
    base="$(basename "$1" .sh)"
    printf '%s\n' "${base#check-}"
}

# ---------------------------------------------------------------------------
# _sweep_available_slugs
# Print the slugs of every check module, comma-separated, for --help and for
# the unknown-slug refusal.
# ---------------------------------------------------------------------------
_sweep_available_slugs() {
    local -a slugs=()
    local cs
    while IFS= read -r cs; do
        [[ -n "${cs}" ]] && slugs+=("$(_sweep_slug_of "${cs}")")
    done < <(_sweep_check_scripts_found)
    local IFS=','
    printf '%s\n' "${slugs[*]}" | sed 's/,/, /g'
}

# ---------------------------------------------------------------------------
# _sweep_print_help
# The --help text: usage, every option, environment, HALT gates, exit codes.
# ---------------------------------------------------------------------------
_sweep_print_help() {
    echo "Usage: $(basename "${BASH_SOURCE[0]}") [--only <check-slug>] [--lock-wait <seconds>]"
    echo ""
    echo "OVERWATCH Tier-1 deterministic sweep runner."
    echo ""
    echo "Iterates all registered projects and runs every check-*.sh module"
    echo "from team/scripts/lib/overwatch-checks/ in lexical order. Cadence is"
    echo "set via cron, which passes no options."
    echo ""
    echo "Options:"
    echo "  --only <check-slug>    Run only check-<check-slug>.sh (for example"
    echo "                         --only block-triage) for every registered project."
    echo "                         A leading 'check-', a trailing '.sh' and underscores"
    echo "                         for hyphens are accepted. An unknown slug is refused,"
    echo "                         listing the available slugs (see Exit codes)."
    echo "  --lock-wait <seconds>  When a check finds a project's repo lock held, retry"
    echo "                         it with backoff for up to this many seconds before"
    echo "                         declining. Default 0 (decline at once). The on-BLOCK"
    echo "                         nudge a wake chain fires after releasing that lock"
    echo "                         runs --only block-triage --lock-wait 60."
    echo "  --help, -h             Print this help and exit 0."
    echo ""
    echo "Available check slugs: $(_sweep_available_slugs)"
    echo ""
    echo "Environment:"
    echo "  KANBAN_ROOT / PGAI_AGENT_KANBAN_ROOT_PATH  Kanban installation root."
    echo "  PGAI_AGENT_KANBAN_TEMP_DIR                  Temp root directory."
    echo ""
    echo "HALT gates (either alone suppresses the sweep run):"
    echo "  \$KANBAN_ROOT/HALT            Global halt; stops all agents."
    echo "  \$KANBAN_ROOT/HALT_OVERWATCH  Overwatch-specific halt."
    echo ""
    echo "Firing lock: each project is swept under its OVERWATCH firing lock"
    echo "(\$KANBAN_ROOT/locks/overwatch-<name>.lock). A project whose lock another"
    echo "firing holds (a sweep, an on-BLOCK nudge, the overwatch wake) is skipped and"
    echo "logged 'firing-skipped:lock-held'; the sweep still exits 0."
    echo ""
    echo "Project HALT (\$KANBAN_ROOT/projects/<name>/HALT): that project's read-only"
    echo "scans and bug filing still run, but every fix is deferred and logged"
    echo "'fix deferred: HALT present'; only the resolution stage's handling of a"
    echo "CM ship-policy HALT still acts. Other projects are swept as usual."
    echo ""
    echo "Exit codes:"
    echo "  0   Completed (all projects swept, or suppressed by a halt flag)."
    echo "  1   Fatal setup error (library missing, registry unreadable)."
    echo "  2   Invalid argument (unknown --only slug, malformed --lock-wait,"
    echo "      unrecognised option); nothing is swept."
}

# ---------------------------------------------------------------------------
# _sweep_emit_refusal <error_key> <message> <remedy>
# Print an argument refusal dual-channel (error / message / remedy) on
# stderr. Each caller exits 2 itself, before anything is swept.
# ---------------------------------------------------------------------------
_sweep_emit_refusal() {
    if [[ -f "${_SWEEP_LIB_DIR}/error_contract.sh" ]]; then
        # shellcheck source=lib/error_contract.sh
        source "${_SWEEP_LIB_DIR}/error_contract.sh"
        pgai_emit_error_contract "$1" "$2" "$3"
    else
        printf 'error:   %s\nmessage: %s\nremedy:  %s\n' "$1" "$2" "$3" >&2
    fi
}

# ---------------------------------------------------------------------------
# Argument handling: --only <check-slug>, --lock-wait <seconds>, --help/-h.
# ---------------------------------------------------------------------------
_SWEEP_ONLY_SLUG=""
_SWEEP_LOCK_WAIT_SECONDS=0
while (( $# > 0 )); do
    case "$1" in
        --help|-h)
            _sweep_print_help
            exit 0
            ;;
        --only|--lock-wait)
            if (( $# < 2 )) || [[ -z "$2" || "$2" == --* ]]; then
                # BLOCK-WORTHY: an option given without its value names no
                # check or wait to apply; refused rather than guessed.
                _sweep_emit_refusal "overwatch_sweep.option_value_required" \
                    "overwatch-sweep: $1 needs a value and none was given." \
                    "${_SWEEP_SCRIPT_DIR}/overwatch-sweep.sh --only block-triage --lock-wait 60"
                # CENSUS-ANCHOR: overwatch-sweep-option-value-required-01
                exit 2
            fi
            if [[ "$1" == "--only" ]]; then _SWEEP_ONLY_SLUG="$2"; else _SWEEP_LOCK_WAIT_SECONDS="$2"; fi
            shift 2
            ;;
        --only=*)
            _SWEEP_ONLY_SLUG="${1#--only=}"
            shift
            ;;
        --lock-wait=*)
            _SWEEP_LOCK_WAIT_SECONDS="${1#--lock-wait=}"
            shift
            ;;
        *)
            # BLOCK-WORTHY: an argument this runner does not define names no
            # sweep it could run; refused rather than ignored.
            _sweep_emit_refusal "overwatch_sweep.unknown_argument" \
                "overwatch-sweep: unrecognised argument '$1'. Accepted: --only <check-slug>, --lock-wait <seconds>, --help." \
                "${_SWEEP_SCRIPT_DIR}/overwatch-sweep.sh --help"
            # CENSUS-ANCHOR: overwatch-sweep-unknown-argument-01
            exit 2
            ;;
    esac
done

if [[ ! "${_SWEEP_LOCK_WAIT_SECONDS}" =~ ^[[:space:]]*[0-9]+[[:space:]]*$ ]]; then
    # BLOCK-WORTHY: a wait that is not a whole number of seconds cannot be
    # honoured; refused rather than silently read as no wait.
    _sweep_emit_refusal "overwatch_sweep.invalid_lock_wait" \
        "overwatch-sweep: --lock-wait must be a whole number of seconds (got '${_SWEEP_LOCK_WAIT_SECONDS}')." \
        "${_SWEEP_SCRIPT_DIR}/overwatch-sweep.sh --only block-triage --lock-wait 60"
    # CENSUS-ANCHOR: overwatch-sweep-invalid-lock-wait-01
    exit 2
fi
_SWEEP_LOCK_WAIT_SECONDS="$(( 10#$(tr -d '[:space:]' <<< "${_SWEEP_LOCK_WAIT_SECONDS}") ))"

if [[ -n "${_SWEEP_ONLY_SLUG}" ]]; then
    # Normalise the forms a caller may reasonably type: check-<slug>,
    # <slug>.sh, underscores for hyphens, surrounding whitespace.
    _SWEEP_ONLY_SLUG="$(tr -d '[:space:]' <<< "${_SWEEP_ONLY_SLUG}")"
    _SWEEP_ONLY_SLUG="${_SWEEP_ONLY_SLUG%.sh}"
    _SWEEP_ONLY_SLUG="${_SWEEP_ONLY_SLUG#check-}"
    _SWEEP_ONLY_SLUG="${_SWEEP_ONLY_SLUG//_/-}"
    if [[ ! -f "${_SWEEP_CHECKS_DIR}/check-${_SWEEP_ONLY_SLUG}.sh" ]]; then
        _sweep_available="$(_sweep_available_slugs)"
        _sweep_example="block-triage"
        [[ -f "${_SWEEP_CHECKS_DIR}/check-${_sweep_example}.sh" ]] \
            || _sweep_example="$(_sweep_check_scripts_found | head -n 1 | xargs -r basename -s .sh | sed 's/^check-//')"
        # BLOCK-WORTHY: --only names the one check to run; a slug that names
        # no module leaves nothing to run, and running every check instead
        # would do far more than the caller asked. Nothing is swept.
        _sweep_emit_refusal "overwatch_sweep.unknown_check" \
            "overwatch-sweep: --only '${_SWEEP_ONLY_SLUG}' names no check module in ${_SWEEP_CHECKS_DIR}. Available check slugs: ${_sweep_available:-none}." \
            "${_SWEEP_SCRIPT_DIR}/overwatch-sweep.sh --only ${_sweep_example:-block-triage}"
        # CENSUS-ANCHOR: overwatch-sweep-unknown-check-01
        exit 2
    fi
fi

# overwatch_halt_first_fix reads this when a check finds the repo lock held.
# Set from --lock-wait only (never inherited), and not exported: the checks
# run in this shell.
OVERWATCH_LOCK_WAIT_SECONDS="${_SWEEP_LOCK_WAIT_SECONDS}"

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
# With --only, the list holds that one module (validated above).
_sweep_check_scripts=()
while IFS= read -r _cs; do
    [[ -f "${_cs}" ]] || continue
    if [[ -n "${_SWEEP_ONLY_SLUG}" && "$(_sweep_slug_of "${_cs}")" != "${_SWEEP_ONLY_SLUG}" ]]; then
        continue
    fi
    _sweep_check_scripts+=("${_cs}")
done < <(_sweep_check_scripts_found)

if (( ${#_sweep_check_scripts[@]} == 0 )); then
    echo "overwatch-sweep: warning: no check-*.sh modules found in ${_SWEEP_CHECKS_DIR}" >&2
fi

# ---------------------------------------------------------------------------
# Record a firing timestamp for this sweep run so all backup entries share it.
# ---------------------------------------------------------------------------
OVERWATCH_FIRING_TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export OVERWATCH_FIRING_TIMESTAMP

if [[ -n "${_SWEEP_ONLY_SLUG}" ]]; then
    echo "overwatch-sweep: firing at ${OVERWATCH_FIRING_TIMESTAMP} (only ${_SWEEP_ONLY_SLUG}; lock wait ${OVERWATCH_LOCK_WAIT_SECONDS}s)" >&2
else
    echo "overwatch-sweep: firing at ${OVERWATCH_FIRING_TIMESTAMP}" >&2
fi

# ---------------------------------------------------------------------------
# One leaked-listener scan for the whole sweep: it walks every process on the
# host, so running it per project would repeat the same work and let one
# project's check act on another's processes. The scan file is removed when
# the sweep exits.
# ---------------------------------------------------------------------------
_sweep_listener_scan=""
for _cs in "${_sweep_check_scripts[@]}"; do
    [[ "$(_sweep_slug_of "${_cs}")" == "leaked-listeners" ]] || continue
    # shellcheck source=/dev/null
    if source "${_cs}" 2>/dev/null && declare -F overwatch_leaked_listener_scan >/dev/null 2>&1; then
        _sweep_listener_scan="$(mktemp "$(pgai_temp_dir)/.leaked-listener-scan.XXXXXX" 2>/dev/null)" || _sweep_listener_scan=""
        if [[ -n "${_sweep_listener_scan}" ]] && overwatch_leaked_listener_scan "${_sweep_listener_scan}"; then
            export OVERWATCH_LEAKED_LISTENER_SCAN="${_sweep_listener_scan}"
            trap 'rm -f "${_sweep_listener_scan}"' EXIT
        else
            [[ -n "${_sweep_listener_scan}" ]] && rm -f "${_sweep_listener_scan}"
            _sweep_listener_scan=""
        fi
    fi
done

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

    # Project HALT gate: a halted project is still observed -- its scans,
    # reports and bug filing run below -- but nothing of it is repaired.
    # Every fix a check attempts goes through overwatch_halt_first_fix,
    # which re-tests this same file under the project's repo lock and
    # defers (sentinel 5) with its own "fix deferred: HALT present" row; this
    # row says once, up front, why this project's sweep will change nothing.
    local project_halt_flag="${project_root}/HALT"
    # A HALT an earlier OVERWATCH repair created and could not remove (the
    # sweep was killed mid-repair) names its creator; once that process is
    # gone the HALT is removed here. An operator or CM HALT is never touched.
    overwatch_clear_dead_repair_halt "${project_halt_flag}" || true
    if [[ -f "${project_halt_flag}" ]]; then
        echo "overwatch-sweep: [${project_name}] project HALT present (${project_halt_flag}); read-only scans run, every fix is deferred" >&2
        overwatch_log_action \
            "overwatch-sweep" \
            "${project_name}" \
            "sweep-fixes-deferred" \
            "none" \
            "fix deferred: HALT present at ${project_halt_flag}; read-only scans and bug filing run, every fix is deferred" \
        2>/dev/null || true
        printf '%s\toverwatch-sweep\t%s\tsweep-fixes-deferred\tnone\tproject HALT present; fixes deferred\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${project_name}" >> "${sweep_log}" || true
    fi

    # Log sweep-start to the canonical action log.
    overwatch_log_action \
        "overwatch-sweep" \
        "${project_name}" \
        "sweep-start" \
        "none" \
        "Tier-1 sweep started; ${#_sweep_check_scripts[@]} check(s) to run${_SWEEP_ONLY_SLUG:+ (only ${_SWEEP_ONLY_SLUG})}" \
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
    # projects.cfg enabled=false: skipped before the firing lock, any check
    # or any state-dir bootstrap -- one line per firing, not counted as swept.
    if ! projects_cfg_enabled "${_project}"; then
        echo "overwatch-sweep: $(projects_cfg_disabled_skip_line "${_project}")" >&2
        continue
    fi
    _sweep_total_projects=$(( _sweep_total_projects + 1 ))

    _proj_exit=0
    overwatch_run_with_firing_lock "${_project}" "overwatch-sweep" _sweep_one_project "${_project}" \
        || _proj_exit=$?

    if (( _proj_exit != 0 )); then
        _sweep_total_errors=$(( _sweep_total_errors + 1 ))
    fi
done <<< "${_sweep_project_list}"

echo "overwatch-sweep: complete — ${_sweep_total_projects} project(s) swept, ${_sweep_total_errors} error(s)" >&2

# Exit 0 even when individual project sweeps had non-fatal errors.
# Error details are in the per-project sweep logs and action logs.
exit 0
