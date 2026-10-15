#!/usr/bin/env bash
# team/scripts/lib/discovery_step_requirements.sh
#
# Discovery pipeline step stage: Step 3 of the pipeline
# (discovery_step_requirements) on its own. Sourced by
# team/scripts/lib/discovery.sh; extracted from
# team/scripts/lib/discovery_steps.sh's own former body so that file's two
# bundle-minting steps (bugs, priority) and their shared
# discovery_compute_next_patch primitive are not sharing a file with this
# considerably larger, single-purpose PM-queueing step -- the split keeps
# both files single-purpose lib stages under the engine-decomposition's
# line ceiling (docs/blocking-site-census.md row 53). This is a
# behavior-preserving move: no stdout/stderr line, exit code, bundle
# filename, or bundle body text changes as a result of this file existing.
#
# Public entry point
# -------------------
#   discovery_step_requirements <project_name>
#       Step 3 of the pipeline. Scans requirements/ for the lowest-version
#       (or filename-lexical, on label projects) unprocessed bundle and
#       queues a PM ticket for it, using each bundle's own ## Status field
#       as the idempotency gate (open / running / done — see the function's
#       own header comment below for the full state machine). Only reached
#       when discovery_step_bugs and discovery_step_priority (in
#       team/scripts/lib/discovery_steps.sh) produced no work, and only on
#       an idle chain (discovery_guards.sh's _disc_chain_idle, which
#       discovery_run_pipeline runs before any scan). The requirements scan
#       runs without the repo lock; the step takes it through
#       _disc_enter_write_phase (discovery.sh) just before it queues PM.
#       Called directly by discovery_run_pipeline in discovery.sh.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_require_project_context,
#     pp_requirements_dir, pp_last_released_version, pp_max_minor,
#     pp_max_major, pp_max_patch, pp_workflow_type, pp_queue_path.
#   team/scripts/lib/workflow.sh — wf_load_plugin, wf_version_semantics,
#     WF_LOAD_ERROR.
#   team/scripts/lib/discovery_guards.sh — _disc_get_release_field (the
#     belt-and-suspenders Active-RC guard), _disc_chain_idle (the caller's
#     own gate before this step is reached, re-run under the repo lock by
#     _disc_enter_write_phase).
#   team/scripts/lib/discovery.sh — _disc_enter_write_phase (repo lock
#     before the PM ticket is queued).
#   team/scripts/lib/discovery_eligibility.sh — _disc_list_all_eligible_requirements.
#   team/scripts/lib/discovery_steps.sh — discovery_compute_next_patch (for
#     auto-sentinel Target Version resolution).
#   team/scripts/lib/discovery_guards.sh — _disc_check_ceiling_and_log (the
#     ceiling check on a resolved auto-sentinel version).
#   team/scripts/lib/discovery_state.sh — _disc_read_bundle_field,
#     _disc_update_bundle_pm_queued, and the pp_run_ops / pgai_atomic_write
#     pair it loads (the resolved Target Version write).
#   team/scripts/lib/error_contract.sh — pgai_emit_error_contract (the
#     Target Version write refusal); self-sourced below when absent, as
#     discovery_steps.sh does.
#   team/scripts/lib/discovery.sh itself — the log() shim, and TEAM_ROOT
#     for locating pm-agent.sh.
#   team/scripts/pm-agent.sh — invoked as a subprocess to decompose the
#     queued bundle into tickets.
#
# Functions in this file are defined at source time (available as soon as
# this file is sourced, independent of when discovery_step_requirements
# itself is invoked) -- bash resolves the cross-file calls above at call
# time, not source time, so sourcing order among the sibling stage files
# does not matter as long as every stage is sourced before the pipeline
# actually runs.

# Guard against double-sourcing.
if declare -F discovery_step_requirements >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/error_contract.sh"
fi

# ---------------------------------------------------------------------------
# _disc_resolve_auto_target_version <project_name> <requirements_file>
#
# Resolve an auto-sentinel ## Target Version (auto, next-patch, empty or
# absent) to the next free patch slot through discovery_compute_next_patch
# (prefix-aware tags, the project's own dev tree; <requirements_file> itself
# never takes a slot), check it against the
# project's max_* ceilings with _disc_check_ceiling_and_log, and write it
# into <requirements_file>'s ## Target Version (md_fields render-value,
# committed by pgai_atomic_write). The caller holds the repo lock.
#
# Returns 0 when the file now carries the resolved version. Returns 1, the
# file unchanged, when the version exceeds a ceiling (the ceiling rejection
# log line and its dual-channel refusal come from
# _disc_check_ceiling_and_log) or when the write fails (refused here).
# ---------------------------------------------------------------------------
_disc_resolve_auto_target_version() {
    local project_name="$1"
    local requirements_file="$2"
    local _resolved_version
    _resolved_version="$(discovery_compute_next_patch "$project_name" "$requirements_file")" || _resolved_version=""
    log "discovery_step_requirements: auto-sentinel Target Version in ${requirements_file} resolved to ${_resolved_version:-(nothing)} via discovery_compute_next_patch"

    if ! _disc_check_ceiling_and_log "$project_name" "$_resolved_version" "discovery_step_requirements"; then
        log "discovery_step_requirements: ceiling-blocked, skipping ${requirements_file}"
        return 1
    fi

    if ! pgai_atomic_write "$requirements_file" pp_run_ops pgai_agent_kanban.lib.md_fields \
            render-value "$requirements_file" "Target Version" "$_resolved_version"; then
        # BLOCK-WORTHY: state-corrupting -- queueing PM for a file that still
        # carries a sentinel would hand the materializer a version it refuses;
        # the file is untouched and the next wake retries the resolution.
        pgai_emit_error_contract \
            "discovery.target-version-write-failed" \
            "discovery resolved the auto Target Version of ${requirements_file} to ${_resolved_version} for project '${project_name}' but could not write it into the file, so PM was not queued; the file is unchanged" \
            "Check that $(dirname "$requirements_file") is writable (ls -ld $(dirname "$requirements_file")); discovery retries on the next wake." \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-step-requirements-01
        return 1
    fi
    log "discovery_step_requirements: wrote Target Version ${_resolved_version} into ${requirements_file}"
    return 0
}

# ---------------------------------------------------------------------------
# discovery_step_requirements <project_name>
#
# Step 3 of the pipeline. Scans requirements/ for *.md files that are eligible
# for PM pickup, iterating them in order (semver ascending for semver projects,
# filename-lexical ascending for label projects), applying per-file rules:
#
#   - File matched in pm_backlog as [x] (done): skip it and continue to the
#     next bundle — this is a historical record, not an obstacle.
#   - File matched in pm_backlog as [ ], [W], or [A] (in flight): exit with
#     a 'PM in flight' log line. Only one PM task is allowed at a time.
#   - File not in pm_backlog at all: queue a PM ticket and return 0.
#
# Semver projects (version_semantics = semver):
#   Files with Target Version set to 'auto', 'next-patch', empty string, or the
#   field entirely absent are treated as auto-sentinel files. Their effective
#   version is computed via discovery_compute_next_patch at pickup time, under
#   the repo lock, checked against the project ceiling, and written into the
#   file's ## Target Version before pm-agent.sh runs
#   (_disc_resolve_auto_target_version); a log line records the computed
#   version and the source file path. An auto file whose resolved version
#   exceeds the ceiling is skipped with the ceiling rejection log line.
#   Auto-sentinel files are sorted AFTER all explicit-version files.
#
#   Ceiling gate: files whose target_version exceeds the project ceiling are
#   logged as 'ceiling-blocked, skipping' and skipped during iteration.
#   If no eligible file survives the ceiling+backlog checks, Step 3 produces no
#   work (pipeline returns idle).
#
# Label projects (version_semantics = label or none):
#   All unhandled items in requirements/ are eligible.  No semver parse, no
#   ceiling filter.  Auto-sentinel values (auto, next-patch, absent Target
#   Version) are invalid on label projects and fail loud with a named reason.
#
# Returns 0 if a PM ticket was queued. Returns 1 if nothing eligible.
# ---------------------------------------------------------------------------
discovery_step_requirements() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    # Belt-and-suspenders guard: refuse to queue PM when Active RC is in flight.
    # The pipeline orchestration in discovery_run_pipeline gates this step more
    # strictly via _disc_chain_idle (which also checks every agent backlog entry), but
    # any direct caller bypassing the pipeline is caught here by the Active RC
    # check alone, which is the critical single-RC-in-flight invariant.
    local _guard_active_rc
    _guard_active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || echo "none")"
    if [[ -n "$_guard_active_rc" && "$_guard_active_rc" != "none" ]]; then
        log "discovery_step_requirements: ERROR Active RC=${_guard_active_rc}; refusing to queue PM (single-RC-in-flight invariant)"
        return 1
    fi

    local requirements_dir last_released
    requirements_dir="$(pp_requirements_dir "$project_name")"
    last_released="$(pp_last_released_version "$project_name" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$last_released" || "$last_released" == "none" ]] && last_released="v0.0.0"

    if [[ ! -d "$requirements_dir" ]]; then
        return 1
    fi

    # Resolve ceiling values for this project: a number (configured or the
    # documented default), or an empty string for an explicit `none`. A
    # malformed value makes the accessor print its one narrated line (file,
    # key, value) on stderr -- left visible here -- and fail; the project is
    # then ceiling-blocked for this run, never treated as "no ceiling".
    local max_minor max_major max_patch _ceiling_unreadable=0
    max_minor="$(pp_max_minor "$project_name")" || _ceiling_unreadable=1
    max_major="$(pp_max_major "$project_name")" || _ceiling_unreadable=1
    max_patch="$(pp_max_patch "$project_name")" || _ceiling_unreadable=1
    if (( _ceiling_unreadable )); then
        log "discovery_step_requirements: ceiling-blocked for ${project_name}: a version ceiling is unreadable (named above); no requirement is queued this run"
        # BLOCK-WORTHY: approval/invariant-by-design -- an unreadable
        # ceiling cannot be compared against, and reading it as "no ceiling"
        # would remove the operator's version gate without a word.
        pgai_emit_error_contract \
            "discovery.ceiling-unreadable" \
            "discovery will not queue PM for project '${project_name}' this run because a version ceiling in its project config is unreadable (one line printed above names the file, key and value)." \
            "Inspect and correct the ceilings: team/scripts/set-version-ceiling.sh --project ${project_name} --show" \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-step-requirements-ceiling-unreadable-01
        return 1
    fi

    # Resolve the project's version_semantics by loading its workflow plugin.
    # The plugin's manifest [capabilities] version_semantics field controls
    # how eligibility is computed — engine queries the capability flag;
    # zero workflow-type string comparisons occur here.
    local _wf_type _version_semantics
    _wf_type="$(pp_workflow_type "$project_name" 2>/dev/null)" || _wf_type="release"
    local _wf_load_exit=0
    set +e
    wf_load_plugin "$_wf_type"
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -ne 0 ]]; then
        log "discovery_step_requirements: WARNING cannot load workflow plugin '${_wf_type}': ${WF_LOAD_ERROR}; defaulting to semver eligibility"
        _version_semantics="semver"
    else
        _version_semantics="$(wf_version_semantics)"
    fi

    # Get ALL eligible requirements files in order appropriate for the semantics.
    # Each line is either an absolute path or a path with a special suffix:
    #   ":auto"          — auto-sentinel (semver path only)
    #   ":auto-invalid"  — auto-sentinel on a label project (fails loud on pickup)
    #   ":ceiling-blocked:<version>:..." — exceeds project ceiling (semver only)
    # Ceiling-blocked and auto-invalid files are NOT filtered here — we want
    # the shell caller to log them with named reasons and continue iterating.
    local all_eligible
    all_eligible="$(_disc_list_all_eligible_requirements "$requirements_dir" "$last_released" "$max_minor" "$max_major" "$max_patch" "$_version_semantics")"

    if [[ -z "$all_eligible" ]]; then
        return 1
    fi

    # pm_backlog for in-flight checks (used only when bundle ## Status is "running").
    local pm_backlog_file
    pm_backlog_file="$(pp_queue_path "$project_name" "pm")"

    # Iterate all eligible files in order (semver ascending or filename-lexical
    # ascending, depending on version_semantics; ordered by the Python caller).
    while IFS= read -r file_raw; do
        [[ -z "$file_raw" ]] && continue

        local chosen_file _auto_sentinel
        if [[ "$file_raw" == *:auto ]]; then
            chosen_file="${file_raw%:auto}"
            _auto_sentinel=1
        else
            chosen_file="$file_raw"
            _auto_sentinel=0
        fi

        # Ceiling-blocked files are passed through by _disc_list_all_eligible_requirements
        # with a ":ceiling-blocked:<version>:<component>:<value>:<cname>:<cvalue>" suffix
        # (or bare ":ceiling-blocked") so we can emit the structured rejection log
        # line here and continue iterating.
        if [[ "$file_raw" == *:ceiling-blocked:* ]]; then
            # Structured format: path:ceiling-blocked:<version>:<component>:<value>:<cname>:<cvalue>
            local _cb_rest="${file_raw#*:ceiling-blocked:}"
            chosen_file="${file_raw%%:ceiling-blocked:*}"
            local _cb_ver _cb_comp _cb_val _cb_cname _cb_cval
            IFS=: read -r _cb_ver _cb_comp _cb_val _cb_cname _cb_cval <<< "$_cb_rest"
            log "PM not queued for ${_cb_ver}: ${_cb_comp} version ${_cb_val} exceeds ${_cb_cname}=${_cb_cval} (file: ${chosen_file})"
            continue
        fi
        if [[ "$file_raw" == *:ceiling-blocked ]]; then
            chosen_file="${file_raw%:ceiling-blocked}"
            log "discovery_step_requirements: ceiling-blocked, skipping ${chosen_file}"
            continue
        fi

        # Auto-sentinel on a label-semantics project: fail loud and skip.
        # Auto-sentinel values (auto / next-patch / absent Target Version) compute
        # a next semver patch slot, which has no meaning for label projects.
        if [[ "$file_raw" == *:auto-invalid ]]; then
            chosen_file="${file_raw%:auto-invalid}"
            log "discovery_step_requirements: skipping $(basename "${chosen_file}"): auto-sentinel Target Version is not valid for label-semantics projects (version_semantics=${_version_semantics})"
            continue
        fi

        # -------------------------------------------------------------------
        # Idempotency gate: read ## Status from the bundle file itself.
        #
        # This replaces the former slug-grep against pm_backlog filenames,
        # which was fragile when bundle version numbers shared a numeric prefix
        # (e.g. v0.1.6 matching inside v0.1.16).  The ## Status field written
        # into each bundle by _disc_write_bundle is the authoritative gate:
        #
        #   open    — bundle not yet queued for PM → eligible
        #   running — bundle already queued; use ## PM Task for exact pm_backlog
        #             lookup to distinguish in-flight from done
        #   done    — RC shipped; skip forever
        #
        # Bundles that lack a ## Status field are treated as 'open'.
        # -------------------------------------------------------------------
        local _bundle_status
        _bundle_status="$(_disc_read_bundle_field "$chosen_file" "Status" 2>/dev/null || echo "open")"
        _bundle_status="${_bundle_status,,}"  # lowercase

        if [[ "$_bundle_status" == "done" ]]; then
            log "discovery_step_requirements: PM already done for ${chosen_file} (bundle ## Status: done), skipping"
            continue
        fi

        if [[ "$_bundle_status" == "running" ]]; then
            # Bundle was queued for PM.  Read the exact task ID written at queue
            # time and do a precise pm_backlog lookup — no slug guessing required.
            local _pm_task_id
            _pm_task_id="$(_disc_read_bundle_field "$chosen_file" "PM Task" 2>/dev/null || echo "")"

            if [[ -n "$_pm_task_id" && "$_pm_task_id" != "none" && -f "$pm_backlog_file" ]]; then
                # Exact task-ID match: look for a line containing the task ID token.
                local _pm_marker_line
                _pm_marker_line="$(grep -F "${_pm_task_id}" "$pm_backlog_file" | head -n1 || true)"
                if [[ -n "$_pm_marker_line" ]] && grep -qE '^\s*-\s+\[x\]' <<< "$_pm_marker_line"; then
                    # PM task is marked [x] — PM is done.
                    # The bundle status will be promoted to 'done' when the RC ships
                    # (cm/release.sh Step 16b).  Until then, skip this bundle.
                    log "discovery_step_requirements: PM already done for ${chosen_file} (pm_backlog [x] for ${_pm_task_id}), skipping"
                    continue
                else
                    # PM task present but not [x] — in flight.
                    log "discovery_step_requirements: PM in flight for ${chosen_file} (${_pm_task_id} not [x] in pm_backlog), aborting scan"
                    return 1
                fi
            else
                # ## PM Task field is absent or 'none'.  Distinguish two sub-cases:
                #
                # Stuck state: Status=running + PM Task absent/none + no pm_backlog
                # entry — this is a half-write left by a pm-agent failure.  The
                # item will never self-recover under the old generic deferral.
                # Emit a stuck-state diagnosis naming the reset recovery so an
                # operator (or OVERWATCH) knows exactly what action unblocks it.
                #
                # Conservative deferral (fallback): Status=running + PM Task absent/none
                # + pm_backlog file does not exist yet — treat as in-flight to avoid
                # re-queueing a PM task whose backlog file may not have been written yet.
                if [[ -f "$pm_backlog_file" ]]; then
                    # Backlog file exists but contains no entry matching this bundle —
                    # definitively a stuck state from a failed or incomplete pm-agent run.
                    log "discovery_step_requirements: stuck-state detected for ${chosen_file} (## Status: running, ## PM Task not set, no pm_backlog match) — reset ## Status to ready to recover"
                    return 1
                else
                    # No backlog file at all — defer conservatively; pm-agent may not
                    # have written it yet.
                    log "discovery_step_requirements: PM in flight for ${chosen_file} (## Status: running, ## PM Task not set), aborting scan"
                    return 1
                fi
            fi
        fi

        # ## Status is 'open' (or absent/unrecognised) — bundle not yet queued.
        # Take the repo lock before queueing (pm-agent.sh writes the PM task
        # folder and pm_backlog entry, and the bundle's ## Status flips
        # below); the scan above ran unlocked. A deferred write phase queues
        # nothing.
        if ! _disc_enter_write_phase "$project_name" "discovery_step_requirements"; then
            return 1
        fi

        # Auto-sentinel: resolve the version here, once, under the repo lock,
        # and write it into the file so pm-agent.sh, PM and the materializer
        # only ever see an explicit ## Target Version (the materializer
        # refuses a sentinel). The ceiling check the explicit-version files
        # got in the eligibility scan applies to the resolved value too.
        if [[ "$_auto_sentinel" -eq 1 ]]; then
            if ! _disc_resolve_auto_target_version "$project_name" "$chosen_file"; then
                continue
            fi
        fi

        # Queue a PM ticket for this bundle.
        log "discovery_step_requirements: queueing PM for ${chosen_file}"

        local pm_agent_script
        pm_agent_script="${TEAM_ROOT}/scripts/pm-agent.sh"
        if [[ ! -x "$pm_agent_script" ]]; then
            log "discovery_step_requirements: ERROR pm-agent.sh not executable at ${pm_agent_script}"
            return 1
        fi

        # Capture pm_agent.sh stdout to extract the task ID for the bundle's
        # ## PM Task field.  Diagnostic output from pm_agent.sh goes to stderr
        # (2>&2 is its default for log lines) — we only parse stdout here.
        #
        # Pass the active project name via PGAI_PROJECT_NAME so that pm-agent.sh
        # creates the PM task folder and pm_backlog entry under the correct
        # project's tasks/ directory.
        local _pm_output _pm_exit _queued_task_id
        _pm_output="$(PGAI_PROJECT_NAME="$project_name" "$pm_agent_script" "$chosen_file" 2>&1)" || _pm_exit=$?
        _pm_exit="${_pm_exit:-0}"
        echo "$_pm_output" >&2  # re-emit so it appears in caller's log

        # Extract task ID from "  Folder  : <path>" line produced by pm-agent.sh.
        # The task ID is the basename of the folder path.
        _queued_task_id="$(echo "$_pm_output" \
            | grep -E '^\s*Folder\s*:' \
            | head -n1 \
            | sed -E 's|.*Folder\s*:\s*||; s|/[[:space:]]*$||; s|.*/||')" || true

        # Mark running ONLY when pm-agent succeeded: exit 0, non-empty task ID
        # parsed from the Folder line, AND the pm_backlog entry is present.
        # On any failure, emit a fail-loud ERROR and leave ## Status unchanged
        # so the next discovery tick re-selects this bundle and retries.
        local _pm_backlog_entry_present=0
        if [[ -n "$_queued_task_id" && -f "$pm_backlog_file" ]] \
            && grep -qF "$_queued_task_id" "$pm_backlog_file" 2>/dev/null; then
            _pm_backlog_entry_present=1
        fi

        if [[ "$_pm_exit" -eq 0 && -n "$_queued_task_id" && "$_pm_backlog_entry_present" -eq 1 ]]; then
            # Verified success: task ID obtained and pm_backlog entry confirmed present.
            _disc_update_bundle_pm_queued "$chosen_file" "$_queued_task_id"
            log "discovery_step_requirements: bundle marked running (PM task: ${_queued_task_id})"
        else
            # pm-agent failed or produced no verifiable output — leave ## Status
            # unchanged so the next tick retries this bundle.
            log "discovery_step_requirements: ERROR pm-agent failed for ${chosen_file} (exit ${_pm_exit}) — leaving status 'ready' for retry"
        fi

        return 0
    done <<< "$all_eligible"

    # Exhausted all eligible files without finding unprocessed work.
    return 1
}
