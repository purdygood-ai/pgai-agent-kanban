#!/usr/bin/env bash
# team/scripts/lib/discovery.sh
#
# Discovery pipeline driver for autonomous work intake.
#
# This library composes the four-step pipeline that decides "what does the
# system work on next" from the stage libraries sourced below. One iteration
# per call; the first step that produces work stops the iteration
# (category-first: bugs > priorities > requirements). Used by both
# cron-driven mode (wake_driver.sh, the PM wake's discovery branch) and
# one-shot mode (pm-agent.sh --auto).
#
#   GUARD   project.cfg readable; per-project HALT; global HALT;
#           pm_backlog has a pending decompose-v* entry (RC opening soon)
#   GUARD   chain busy -- Active RC set, or any task named in any role
#           queue (pm, cm, coder, tester, writer) not DONE/WONT-DO by its
#           own status.md: idle exit before any scan, logged as
#             discovery_run_pipeline [<project>]: chain busy, idle exit before any scan
#           so Steps 1-3 run only on an idle chain
#   STEP 1  discovery_step_bugs         — bundle bugs/ into a patch-bumped
#                                          requirements file
#   STEP 2  discovery_step_priority     — bundle priority/ (only if Step 1
#                                          found nothing)
#   STEP 3  discovery_step_requirements — queue the lowest-version unprocessed
#                                          file for PM (only if Steps 1+2
#                                          found nothing)
#   STEP 4  idle exit (no error)
#
# Repo-lock scope
# ---------------
# The guards and the Step 1-3 scans are read-only and run WITHOUT the
# project's repo lock, so a long scan never keeps another role's wake out.
# A step takes the lock once it has found something to write, through
# _disc_enter_write_phase, before its first write (minting a bundle into
# requirements/, flipping an intake item's ## Status and cache marker,
# queuing a PM ticket through pm-agent.sh). Under the lock the chain-busy
# test runs again: a chain that turned busy during the unlocked scan is not
# written into. Once taken, the lock is held for the rest of the iteration.
# Who takes it:
#   - DISCOVERY_WRITE_LOCK_HOOK names a caller function (called with the
#     project name; 0 = the caller now holds the repo lock). The caller
#     owns that lock afterwards and releases it -- the PM wake does this
#     so its post-discovery dispatch runs under the same hold.
#   - With no hook set, discovery takes the repo lock itself
#     (pgai_lock_acquire, op discovery-write:<project>) and releases it
#     before discovery_run_pipeline returns.
# The acquisition never waits: a lock held by another role defers the write
# to the next wake (logged, DISCOVERY_LAST_STATUS=idle), and no later step
# runs in that iteration, so category-first order holds across the deferral.
# The intake scan's own bookkeeping (the malformed-name rejection counter,
# quarantine of a malformed file name, settle markers) runs inside the
# scan, unlocked.
#
# Each step's own guard logic, idempotency invariants, and per-item state
# machine are documented in team/scripts/lib/discovery_steps.sh, not here.
# See also:
#   discovery_guards.sh — RC-in-flight / ceiling / chain-idle / PM-ticket /
#                          git-tag guard primitives the steps call
#   discovery_items.sh  — intake-item eligibility scanning + quarantine
#   discovery_defer.sh  — bug-step deferral gate: severity-vs-posture
#                          decision and the hygiene-sweep queue-emptiness
#                          check discovery_step_bugs applies
#   discovery_state.sh  — status-field mutation and bundle writer
#   discovery_eligibility.sh — requirements-directory eligibility enumeration
#   discovery_step_requirements.sh — Step 3 (queue the lowest-version
#                          unprocessed file for PM), split from
#                          discovery_steps.sh
#   discovery_bundle.sh — stale-requirements scan consumed by
#                          dashboard/attention.sh and list-stale-requirements.sh
#
# Public API
# ----------
#   discovery_run_pipeline <project_name>   — runs steps 1→2→3 once, always
#                                              returns 0; sets
#                                              DISCOVERY_LAST_STATUS
#   _disc_enter_write_phase <project_name> <step_name>
#                                            — called by each step before its
#                                              first write; 0 = the repo lock
#                                              is held and the chain is still
#                                              idle, 1 = do not write
#
# Source order
# ------------
# This library MUST be sourced AFTER lib/project_paths.sh in the caller, because
# it relies on pp_* helpers. The caller must also have TEAM_ROOT defined.

# ---------------------------------------------------------------------------
# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# ---------------------------------------------------------------------------
_DISC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Kept after the sourcing block below unsets _DISC_LIB_DIR: the default
# write lock sources lock_provenance.sh from here on first need.
_DISC_SELF_DIR="$_DISC_LIB_DIR"
if ! declare -F pgai_temp_dir >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "${_DISC_LIB_DIR}/temp.sh"
fi

# Source the workflow-type dispatcher so the stage libraries below can call
# wf_bundle_source_branch / wf_version_semantics rather than comparing the
# workflow_type string.
if ! declare -F wf_load_plugin >/dev/null 2>&1; then
    # shellcheck source=workflow.sh
    source "${_DISC_LIB_DIR}/workflow.sh"
fi

# ---------------------------------------------------------------------------
# Source the guard, item-scanning, state-mutation, step, and stale-scan lib
# stages. These files were extracted from this file's former inline body so
# each stage stays a focused, independently testable unit and this driver
# stays thin — see each file's own header for its concern, its documented
# public entry point(s), and cross-file dependencies. Source order does not matter for bash function resolution
# (functions resolve at call time, not source time), but these are listed in
# the order the pipeline below consumes them.
# ---------------------------------------------------------------------------
# shellcheck source=discovery_guards.sh
source "${_DISC_LIB_DIR}/discovery_guards.sh"
# shellcheck source=discovery_state.sh
source "${_DISC_LIB_DIR}/discovery_state.sh"
# shellcheck source=discovery_eligibility.sh
source "${_DISC_LIB_DIR}/discovery_eligibility.sh"
# shellcheck source=discovery_items.sh
source "${_DISC_LIB_DIR}/discovery_items.sh"
# shellcheck source=discovery_defer.sh
source "${_DISC_LIB_DIR}/discovery_defer.sh"
# shellcheck source=discovery_steps.sh
source "${_DISC_LIB_DIR}/discovery_steps.sh"
# shellcheck source=discovery_step_requirements.sh
source "${_DISC_LIB_DIR}/discovery_step_requirements.sh"
# shellcheck source=discovery_bundle.sh
source "${_DISC_LIB_DIR}/discovery_bundle.sh"
unset _DISC_LIB_DIR

# ---------------------------------------------------------------------------
# Logging shim
# ---------------------------------------------------------------------------
# If the caller defined log() (wake/claude.sh has it), use it. Otherwise
# define a minimal one that prints to stderr.
if ! declare -F log >/dev/null 2>&1; then
    log() {
        echo "[$(date -Iseconds)] discovery: $*" >&2
    }
fi

# ---------------------------------------------------------------------------
# DISCOVERY_LAST_STATUS
#
# Set by discovery_run_pipeline on every invocation. Callers that need to
# know whether work was produced (e.g. pm-agent.sh --auto loop) can inspect
# this variable after each call. Three values:
#
#   produced_work  — pipeline did something useful (step 1, 2, or 3 fired)
#   idle           — pipeline found nothing to do: the chain was busy, no
#                    step had work, or a step's write was deferred because
#                    the repo lock was not available
#   blocked        — pipeline was blocked by HALT, an unreadable project
#                    config, or a pending PM decompose ticket
#
# The return code of discovery_run_pipeline is ALWAYS 0 so that existing
# cron callers (wake/claude.sh) are unaffected by this new signal.
# ---------------------------------------------------------------------------
DISCOVERY_LAST_STATUS="idle"

# ---------------------------------------------------------------------------
# Write-phase state, reset by discovery_run_pipeline on every call:
#   _DISC_WRITE_PHASE   ""        no write attempted yet (the lock is not
#                                 held by this iteration)
#                       entered   the repo lock is held and the chain was
#                                 idle under it; later steps write freely
#                       deferred  the lock could not be taken, or the chain
#                                 was busy under it; nothing is written and
#                                 the iteration stops
#   _DISC_OWN_LOCK_FILE / _DISC_OWN_LOCK_FD
#                       the repo lock discovery took itself (no hook set);
#                       released by discovery_run_pipeline on return
# ---------------------------------------------------------------------------
_DISC_WRITE_PHASE=""
_DISC_OWN_LOCK_FILE=""
_DISC_OWN_LOCK_FD=""

# _disc_take_own_write_lock <project_name>
# Default write lock when the caller set no DISCOVERY_WRITE_LOCK_HOOK: one
# non-blocking pgai_lock_acquire on the project's repo lock. Returns 0 with
# _DISC_OWN_LOCK_FILE/_DISC_OWN_LOCK_FD set, 1 when the lock is held
# elsewhere or the path cannot be resolved (pgai_lock_acquire narrates the
# holder on contention).
_disc_take_own_write_lock() {
    local project_name="$1"
    local _disc_lock_file _disc_own_fd_out=""
    _disc_lock_file="$(pp_repo_lock_path "$project_name" 2>/dev/null)" || return 1
    [[ -n "$_disc_lock_file" ]] || return 1
    if ! declare -F pgai_lock_acquire >/dev/null 2>&1; then
        # shellcheck source=lock_provenance.sh
        source "${_DISC_SELF_DIR}/lock_provenance.sh" || return 1
    fi
    pgai_lock_acquire "$_disc_lock_file" "${AGENT:-pm}" "$$" \
        "discovery-write:${project_name}" "none" _disc_own_fd_out || return 1
    _DISC_OWN_LOCK_FILE="$_disc_lock_file"
    _DISC_OWN_LOCK_FD="$_disc_own_fd_out"
    return 0
}

# _disc_release_own_write_lock
# Release the lock _disc_take_own_write_lock took, if any. A hook-taken
# lock is the caller's and is never touched here.
_disc_release_own_write_lock() {
    if [[ -n "${_DISC_OWN_LOCK_FD:-}" ]]; then
        pgai_lock_release "$_DISC_OWN_LOCK_FILE" "$_DISC_OWN_LOCK_FD"
    fi
    _DISC_OWN_LOCK_FILE=""
    _DISC_OWN_LOCK_FD=""
}

# _disc_enter_write_phase <project_name> <step_name>
#
# Called by each step at the point it has found something to write, before
# the write. Takes the project's repo lock (the caller's
# DISCOVERY_WRITE_LOCK_HOOK when set, otherwise _disc_take_own_write_lock),
# then re-runs the chain-busy test under the lock. Returns 0 when this
# iteration holds the lock and the chain is still idle -- immediately, with
# no second acquisition, once an earlier step already entered. Returns 1
# when the lock is held elsewhere or the chain turned busy during the
# unlocked scan; the iteration is then deferred and the caller must not
# write.
_disc_enter_write_phase() {
    local project_name="$1"
    local step_name="${2:-discovery}"
    case "${_DISC_WRITE_PHASE:-}" in
        entered) return 0 ;;
        deferred) return 1 ;;
    esac

    local _disc_hook="${DISCOVERY_WRITE_LOCK_HOOK:-}"
    local _disc_took=0
    if [[ -n "$_disc_hook" ]]; then
        "$_disc_hook" "$project_name" && _disc_took=1
    else
        _disc_take_own_write_lock "$project_name" && _disc_took=1
    fi
    if [[ "$_disc_took" -ne 1 ]]; then
        log "${step_name}: repo lock not available for the discovery write; deferring to the next wake"
        _DISC_WRITE_PHASE="deferred"
        return 1
    fi

    if ! _disc_chain_idle "$project_name"; then
        log "${step_name}: chain became busy during the unlocked scan; not writing"
        _DISC_WRITE_PHASE="deferred"
        return 1
    fi
    log "${step_name}: repo lock held for the discovery write"
    _DISC_WRITE_PHASE="entered"
    return 0
}

# _disc_write_deferred
# True when a step's write phase was deferred this iteration; the pipeline
# then stops instead of letting a later category write ahead of it.
_disc_write_deferred() {
    [[ "${_DISC_WRITE_PHASE:-}" == "deferred" ]]
}

# ---------------------------------------------------------------------------
# discovery_run_pipeline <project_name>
#
# Top-level entry. Runs the 4-step pipeline once. Always returns 0 (idle is
# a normal outcome, not an error) unless the project context cannot be
# resolved. Sets DISCOVERY_LAST_STATUS to one of:
#   produced_work / idle / blocked
# so that looping callers (pm-agent.sh --auto) know whether to iterate again.
# Resets the write-phase state, runs one iteration, and releases a repo lock
# discovery took itself (never one a DISCOVERY_WRITE_LOCK_HOOK took).
# ---------------------------------------------------------------------------
discovery_run_pipeline() {
    _DISC_WRITE_PHASE=""
    _DISC_OWN_LOCK_FILE=""
    _DISC_OWN_LOCK_FD=""
    local _disc_rc=0
    _disc_run_pipeline_once "$@" || _disc_rc=$?
    _disc_release_own_write_lock
    return "$_disc_rc"
}

# _disc_run_pipeline_once <project_name>
# One pipeline iteration: the guards, then Steps 1-3; see the file header.
_disc_run_pipeline_once() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    # GUARD: validate project config (project.cfg or PROJECT.cfg) exists and is
    # readable for this project.  A missing or unreadable config causes the
    # pipeline to skip (log+return 0) rather than crash (Open Question #4).
    local _proj_root _proj_cfg
    _proj_root="$(pp_project_root "$project_name" 2>/dev/null)" || {
        log "discovery_run_pipeline [${project_name}]: ERROR pp_project_root failed; skipping project"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    }
    # Source: project.cfg (preferred) or PROJECT.cfg (fallback)
    _proj_cfg="$(_pp_project_cfg_file "$_proj_root")"
    if [[ -z "$_proj_cfg" ]]; then
        log "discovery_run_pipeline [${project_name}]: ERROR project.cfg not found at ${_proj_root}/project.cfg (and PROJECT.cfg not found); skipping project"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi
    if [[ ! -r "$_proj_cfg" ]]; then
        log "discovery_run_pipeline [${project_name}]: ERROR project config not readable at ${_proj_cfg}; skipping project"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # GUARD: per-project HALT check (fires before global HALT).
    # A HALT file under projects/<project_name>/HALT halts only this project;
    # it does not set the global HALT flag or affect other projects.
    if pp_project_halted "$project_name"; then
        log "discovery_run_pipeline [${project_name}]: per-project HALT, pipeline blocked"
        pgai_emit_error_contract \
            "discovery.per-project-halt" \
            "discovery skipped project '${project_name}' because its project-scoped HALT flag is present" \
            "Once the underlying issue is resolved, run: team/scripts/unhalt.sh --project ${project_name}" \
            "${project_name}"
        # BLOCK-WORTHY: approval-by-design — the per-project HALT is the
        # operator's (or CM's) scoped stop signal for this project's lane
        # (docs/blocking-site-census.md rollup row 4, "Per-project HALT guard").
        # Census-indexed by this call's own error key ("discovery.per-project-halt"),
        # no separate anchor marker needed — see the Locator Index.
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # GUARD: global HALT flag blocks the entire pipeline (all steps, all projects)
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "discovery_run_pipeline [${project_name}]: global HALT, pipeline blocked"
        pgai_emit_error_contract \
            "discovery.global-halt" \
            "discovery skipped project '${project_name}' because the fleet-wide HALT flag at ${TEAM_ROOT}/HALT is present" \
            "Once the underlying issue is resolved, run: team/scripts/unhalt-global.sh" \
            "${project_name}"
        # BLOCK-WORTHY: approval-by-design — the global HALT file is the
        # operator's/CM's explicit fleet-wide stop signal (docs/blocking-site-census.md
        # rollup row 3, "Global HALT guard").
        # Census-indexed by this call's own error key ("discovery.global-halt"),
        # no separate anchor marker needed — see the Locator Index.
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # GUARD 4: pm_ticket_in_flight — exit before Steps 1/2/3 when a decompose-v*
    # PM ticket is pending in pm_backlog (decided by its status.md, not the
    # marker).  This covers the window between "operator drops requirements
    # file → discovery queues PM" and "CM-open-rc fires → Active RC is set":
    # any bug or priority bundled in that window would land in a patch
    # version stranded once the RC ships.  The chain-busy guard below would
    # also stop such a wake (the pending ticket is a non-terminal queue
    # entry); this guard runs first so that case keeps its own log line and
    # its "blocked" status.
    if _disc_pm_ticket_in_flight "$project_name"; then
        log "discovery_run_pipeline [${project_name}]: pending PM ticket detected; exiting (RC opening soon)"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # GUARD 5: chain busy — idle-exit before any scan. Active RC set, or any
    # task named in any role queue not yet DONE/WONT-DO by its own
    # status.md, means the chain is still working: no step may queue PM
    # (Step 3's rule) and Steps 1/2 would only scan to find their writes
    # deferred (the Active-RC guard already defers them while an RC is
    # open). Exiting here keeps a busy-chain wake to a few seconds and
    # runs no scan at all. _disc_chain_idle logs which task or RC makes
    # the chain busy, just above this exit line.
    if ! _disc_chain_idle "$project_name"; then
        log "discovery_run_pipeline [${project_name}]: chain busy, idle exit before any scan"
        DISCOVERY_LAST_STATUS="idle"
        return 0
    fi

    # Read the version ceilings once, before any step: an absent key is its
    # documented default and only an explicit `none` (echoed empty) lifts a
    # cap. A malformed value makes its accessor print one line naming the
    # file, key and value -- left visible here -- and the whole run is
    # ceiling-blocked: every step below queues a version, and none of them
    # can be checked against a ceiling that cannot be read.
    local _ceil_max_minor _ceil_max_major _ceil_max_patch _ceil_parts _ceil_unreadable=0
    _ceil_max_minor="$(pp_max_minor "$project_name")" || _ceil_unreadable=1
    _ceil_max_major="$(pp_max_major "$project_name")" || _ceil_unreadable=1
    _ceil_max_patch="$(pp_max_patch "$project_name")" || _ceil_unreadable=1
    if (( _ceil_unreadable )); then
        log "discovery_run_pipeline [${project_name}]: ceiling-blocked: a version ceiling is unreadable (named above); nothing is queued this run"
        # BLOCK-WORTHY: approval/invariant-by-design -- the operator's version
        # gate cannot be evaluated, and reading it as "no ceiling" would let
        # any version through without a word.
        pgai_emit_error_contract \
            "discovery.ceiling-unreadable" \
            "discovery will not queue work for project '${project_name}' this run because a version ceiling in its project config is unreadable (one line printed above names the file, key and value)." \
            "Inspect and correct the ceilings: team/scripts/set-version-ceiling.sh --project ${project_name} --show" \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-ceiling-unreadable-01
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi
    # Log the ceilings in effect (a `none` component is left out).
    if [[ -n "$_ceil_max_minor" || -n "$_ceil_max_major" || -n "$_ceil_max_patch" ]]; then
        _ceil_parts=""
        [[ -n "$_ceil_max_major" ]] && _ceil_parts="max_major=${_ceil_max_major}"
        if [[ -n "$_ceil_max_minor" ]]; then
            [[ -n "$_ceil_parts" ]] && _ceil_parts="${_ceil_parts}, "
            _ceil_parts="${_ceil_parts}max_minor=${_ceil_max_minor}"
        fi
        if [[ -n "$_ceil_max_patch" ]]; then
            [[ -n "$_ceil_parts" ]] && _ceil_parts="${_ceil_parts}, "
            _ceil_parts="${_ceil_parts}max_patch=${_ceil_max_patch}"
        fi
        log "discovery_run_pipeline: active ceilings for ${project_name}: ${_ceil_parts}"
    fi

    # STEP 1: bundle bugs.
    # Reached only on an idle chain (the guards above). discovery_step_bugs
    # keeps its own active-RC guard for direct callers, and takes the repo
    # lock through _disc_enter_write_phase before it writes.
    #
    # Category-first ordering: if Step 1 produces work, stop immediately.
    # Steps 2 and 3 are skipped — bugs take priority over priorities and
    # requirements regardless of version numbers.  "First category with work
    # stops the iteration" is the contract stated in the pipeline header and SOP.
    if discovery_step_bugs "$project_name"; then
        DISCOVERY_LAST_STATUS="produced_work"
        return 0
    fi
    if _disc_write_deferred; then
        log "discovery_run_pipeline [${project_name}]: discovery write deferred, idle exit"
        DISCOVERY_LAST_STATUS="idle"
        return 0
    fi

    # STEP 2: bundle priority items (same guard logic as Step 1 above).
    # Only runs when Step 1 found nothing (category-first: bugs → priorities).
    # If Step 2 produces work, stop immediately — requirements are skipped.
    if discovery_step_priority "$project_name"; then
        DISCOVERY_LAST_STATUS="produced_work"
        return 0
    fi
    if _disc_write_deferred; then
        log "discovery_run_pipeline [${project_name}]: discovery write deferred, idle exit"
        DISCOVERY_LAST_STATUS="idle"
        return 0
    fi

    # STEP 3: queue PM for the lowest-version unprocessed requirements bundle.
    # Only runs when Steps 1 and 2 both found nothing (category-first ordering).
    # The chain-busy guard above already established that Active RC = none
    # AND every task entry named in every agent backlog has reached a
    # terminal status.md state (DONE or WONT-DO) — never decided by the
    # queue marker — and _disc_enter_write_phase re-checks it under the
    # repo lock before the PM ticket is queued, so a new PM task is never
    # queued while any agent is still finishing the current RC.
    if discovery_step_requirements "$project_name"; then
        DISCOVERY_LAST_STATUS="produced_work"
        return 0
    fi

    log "discovery_run_pipeline: no work pending, idle exit"
    DISCOVERY_LAST_STATUS="idle"
    return 0
}
