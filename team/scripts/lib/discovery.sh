#!/usr/bin/env bash
# team/scripts/lib/discovery.sh
#
# Unified discovery pipeline for autonomous work intake.
#
# This library is the single source of truth for "what does the system work on
# next." It is used by both cron-driven mode (wake/claude.sh) and one-shot
# mode (pm-agent.sh --auto). One iteration per call: each step that produces
# work stops the iteration so the next iteration sees the new file via Step 3.
#
# The pipeline:
#
#   GUARD   (1) validate project.cfg (or PROJECT.cfg) exists and is readable; skip project on error
#           (2) if per-project HALT file (projects/<name>/HALT) is present, block
#           (3) if global HALT file ($TEAM_ROOT/HALT) is present, block
#           (4) if pm_backlog has a pending decompose-v* entry ([ ] or [A] marker),
#               exit cleanly — RC is opening soon; avoid phantom patch bundles
#   STEP 1  scan bugs/, bundle ALL unhandled into a patch-bumped requirements file
#           (skipped when GUARD 4 fires — PM ticket already in flight)
#   STEP 2  scan priority/, bundle ALL unhandled
#           (skipped when GUARD 4 fires — PM ticket already in flight)
#   STEP 3  scan requirements/, queue the lowest-version unprocessed file for PM
#           (gated: only runs when the ENTIRE chain is idle — Active RC = none
#           AND every named agent backlog's latest entry is [x])
#   STEP 4  idle exit (no error)
#
# GUARD 4 (pm_ticket_in_flight) prevents the bug/priority bundler from writing
# phantom patch versions while a requirements file has been picked up by discovery
# but CM-open-rc has not yet fired.  The window is small but hit regularly when
# an operator drops a requirements file and bugs in the same session.  Only
# the pre-open-rc window (pm ticket pending, Active RC = none) is blocked here.
#
# Active-RC guard (inside discovery_step_bugs / discovery_step_priority): Steps 1
# and 2 are additionally blocked when Active RC is set.  Although CM-open-rc has
# fired by that point, the patch bundle's target_version would be derived from
# the last *shipped* version — which is below the in-flight RC version, producing
# an incoherent bundle that requires manual cleanup.  Bundling is deferred until
# after the in-flight RC ships, at which point the patch version is safe again.
#
# Step 3 uses a strict in-flight gate (_disc_chain_idle) to prevent queuing a
# new PM-decompose task while any agent is still working on the current RC.
# This prevents the "future PM visible during in-flight RC" noise on the
# dashboard. The gate checks both Active RC and the tail of each agent backlog.
#
# Idempotency invariant (Step 3)
# ------------------------------
# Each requirements bundle file written by the discovery pipeline carries a
# '## Status' field and a '## PM Task' field in its header.  Step 3 uses these
# fields — not a grep over pm_backlog filenames — to determine whether a bundle
# has already been queued for PM:
#
#   ## Status: open     — bundle exists but has NOT been queued yet; eligible
#   ## Status: running  — bundle was queued for PM (PM task ID in ## PM Task);
#                         Step 3 checks pm_backlog for that exact task ID to
#                         distinguish in-flight from done
#   ## Status: done     — RC shipped; bundle is fully processed; skip forever
#
# The invariant is robust against filename collisions because it is anchored to
# the bundle file itself, not to a substring match over a derived slug.  Two
# bundles whose version+kind portion overlaps (e.g., v0.1.6 vs v0.1.16) cannot
# trigger false idempotency gates because each bundle's own Status field is the
# authoritative check.
#
# cm-release.sh Step 16b is responsible for setting the bundle's ## Status from
# 'running' to 'done' when the RC ships.  Until that step runs, Step 3 uses the
# pm_backlog exact-match check to detect PM completion.
#
# Public API
# ----------
#   discovery_run_pipeline <project_name>          — runs steps 1→2→3, returns 0 always
#   discovery_step_bugs <project_name>             — bundle bugs, return 0 if work produced
#   discovery_step_priority <project_name>         — bundle priority items, return 0 if work produced
#   discovery_step_requirements <project_name>     — find lowest-version doc, queue PM
#   discovery_compute_next_patch <project_name>    — compute next patch slot with bump-around
#
# Source order
# ------------
# This library MUST be sourced AFTER lib/project_paths.sh in the caller, because
# it relies on pp_* helpers. The caller must also have TEAM_ROOT defined.

# ---------------------------------------------------------------------------
# Source temp helpers so pgai_temp_dir / pgai_mktemp are available.
# ---------------------------------------------------------------------------
_DISC_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F pgai_temp_dir >/dev/null 2>&1; then
    # shellcheck source=temp.sh
    source "${_DISC_LIB_DIR}/temp.sh"
fi
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
# discovery_compute_next_patch <project_name>
#
# Compute the next available patch version slot for bug/priority bundle output.
# Bumps the Z component of current_live (X.Y.Z) and bumps-around within patch
# space if the slot is taken in any of three sources:
#   1. requirements/vX.Y.Z-*.md or vX.Y.Z.md exists (drafted; exact-version match)
#   2. tasks/queues/plans/.materialized.* marker whose content references vX.Y.Z
#   3. git tag vX.Y.Z exists in origin (already exact-match via git tag -l)
#
# Fresh-system semantics: if Last Released = v0.0.0 (or unset), starts at v0.0.1.
#
# Echoes the chosen version (e.g. "v0.18.5") on stdout. Exit 0 on success.
# ---------------------------------------------------------------------------
discovery_compute_next_patch() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local last_released requirements_dir plans_dir
    last_released="$(pp_last_released_version "$project_name" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$last_released" || "$last_released" == "none" ]] && last_released="v0.0.0"

    requirements_dir="$(pp_requirements_dir "$project_name")"
    plans_dir="$(pp_tasks_dir "$project_name")/queues/plans"

    # Resolve the dev tree path from project.cfg (INI) or PROJECT.cfg (fallback)
    # once and reuse for all bump-around tag-existence checks.  This ensures
    # the same git worktree is used here as in pp_last_released_version, so
    # bump-around works even when invoked from the live install (which has no
    # .git/ directory).
    local _dev_tree=""
    local _cfg_file
    # Source: project.cfg [project] dev_tree_path (falls back to PROJECT.cfg)
    _cfg_file="$(_pp_project_cfg_file "$(pp_project_root "$project_name" 2>/dev/null)")"
    if [[ -n "$_cfg_file" ]]; then
        _dev_tree="$(_pp_read_cfg_key "$_cfg_file" project dev_tree_path "")"
    fi
    # Fall back to PGAI_DEV_TREE_PATH if project cfg did not yield a path
    [[ -z "$_dev_tree" ]] && _dev_tree="${PGAI_DEV_TREE_PATH:-}"

    # Parse X.Y.Z from last_released
    local clean="${last_released#v}"
    local x y z
    IFS=. read -r x y z <<< "$clean"
    x="${x:-0}"
    y="${y:-0}"
    z="${z:-0}"

    # Loop: increment z, check all three sources
    local candidate
    while true; do
        z=$((z + 1))
        candidate="v${x}.${y}.${z}"

        # Source 1: drafted file in requirements/
        # Use exact-version match to prevent vX.Y.Z*.md from matching vX.Y.Z+N-foo.md.
        # Requirements filenames follow the convention vX.Y.Z-<slug>.md, so anchor
        # on the hyphen after the version.  Also check the bare exact form vX.Y.Z.md.
        if compgen -G "${requirements_dir}/${candidate}-*.md" >/dev/null 2>&1; then
            continue
        fi
        if [[ -f "${requirements_dir}/${candidate}.md" ]]; then
            continue
        fi

        # Source 2: materialized marker in tasks/queues/plans/
        # The marker filename convention is .materialized.<sha256-hash>
        # (pure lowercase hex), so the version string cannot appear in the filename.
        # Search marker file CONTENTS for the candidate version as a delimited token
        # (i.e., not followed by another digit) to avoid vX.Y.Z matching vX.Y.Z+N.
        if [[ -d "${plans_dir}" ]] && \
           grep -rlE "(^|[^.0-9])${candidate//./\\.}([^0-9]|$)" \
               "${plans_dir}"/.materialized.* 2>/dev/null | grep -q .; then
            continue
        fi

        # Source 3: git tag — use the dev tree resolved above so this check
        # works when the caller is the live install (no .git directory).
        if _disc_git_tag_exists "$candidate" "$_dev_tree"; then
            continue
        fi

        # Slot is free
        echo "$candidate"
        return 0
    done
}

# ---------------------------------------------------------------------------
# discovery_step_bugs <project_name>
#
# Step 1 of the pipeline. Scans bugs/ for unhandled BUG-*.md files
# (cross-referenced against bug_backlog.md cache and each file's ## Status
# header). Bundles ALL into one requirements file.
#
# Ceiling gate: after computing the target version, checks pp_version_within_ceiling.
# If the target version exceeds the project ceiling, logs the reason and returns 1
# (no bundle produced, pipeline returns idle for this iteration).
#
# Returns 0 if a bundle was produced (caller stops iteration).
# Returns 1 if nothing to bundle (caller proceeds to Step 2).
# ---------------------------------------------------------------------------
discovery_step_bugs() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local bugs_dir bug_backlog
    bugs_dir="$(pp_bugs_dir "$project_name")"
    bug_backlog="$(pp_queue_path "$project_name" "bug")"

    # Check for duplicate BUG-NNNN numeric prefixes and emit WARNINGs if any
    # are found.  Collisions should not occur when claim_next_bug_id() is used
    # consistently, but this guard detects out-of-band creations.
    _disc_check_bug_id_collisions "$bugs_dir"

    # Quarantine state dir: tracks per-file rejection counts across cron firings.
    local state_dir
    state_dir="$(pp_project_root "$project_name")/.discovery-state"

    # Find unhandled bugs: cache says open AND header says open
    local unhandled_files
    unhandled_files="$(_disc_find_unhandled_items "$bugs_dir" "$bug_backlog" "BUG-" "$state_dir" "$project_name")"

    if [[ -z "$unhandled_files" ]]; then
        return 1
    fi

    # Guard: refuse to write a bundle when no valid file paths remain.
    # stdout from _disc_find_unhandled_items contains only absolute paths.
    # This check is a belt-and-suspenders defence: if unhandled_files is
    # non-empty but contains no lines starting with '/', something unexpected ended
    # up in the variable (e.g., a log line) — do not corrupt a bundle with it.
    if ! echo "$unhandled_files" | grep -q '^/'; then
        log "discovery_step_bugs: no valid item paths after filtering (possible malformed-filename rejections only); skipping bundle"
        return 1
    fi

    # Active-RC guard: refuse to bundle when an RC is in flight.
    # Bundling while an RC is open can produce a requirements file whose target
    # version is below the in-flight RC version, which is incoherent.  Defer all
    # bundling until after the current RC ships.
    # Shared helper used by both discovery_step_bugs and discovery_step_priority
    # so the two steps cannot diverge in their Active-RC guard semantics.
    if ! _disc_active_rc_blocks "discovery_step_bugs" "$project_name"; then
        return 1
    fi

    # Compute target version
    local target_version
    target_version="$(discovery_compute_next_patch "$project_name")"

    # Ceiling gate: skip bundling if the target version exceeds the project ceiling.
    # Emits a structured rejection log line per the documented format for each
    # failing ceiling component so the operator knows exactly what was blocked.
    if ! _disc_check_ceiling_and_log "$project_name" "$target_version" "discovery_step_bugs"; then
        return 1
    fi

    # Bundle into requirements file
    local today requirements_dir bundle_path
    today="$(date +%Y%m%d)"
    requirements_dir="$(pp_requirements_dir "$project_name")"
    bundle_path="${requirements_dir}/${target_version}-bugfix-bundle-${today}.md"

    log "discovery_step_bugs: bundling $(echo "$unhandled_files" | wc -l) bug(s) into ${bundle_path}"

    _disc_write_bundle "$bundle_path" "$target_version" "release" "true" "Bug bundle" "$unhandled_files" "BUG"

    # Mark each bundled item: cache [x] AND header status=running
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        local item_id
        item_id="$(basename "${f%.md}")"
        _disc_mark_cache_bundled "$bug_backlog" "$item_id"
        _disc_set_status_field "$f" "running"
    done <<< "$unhandled_files"

    log "discovery_step_bugs: bundle written, ${target_version} ready for Step 3 next iteration"
    return 0
}

# ---------------------------------------------------------------------------
# discovery_step_priority <project_name>
#
# Step 2 of the pipeline. Same shape as Step 1 but for priority items.
# Returns 0 if a bundle was produced. Returns 1 if nothing to bundle.
# ---------------------------------------------------------------------------
discovery_step_priority() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local priority_dir priority_backlog
    priority_dir="$(pp_priority_dir "$project_name")"
    priority_backlog="$(pp_queue_path "$project_name" "priority")"

    # Skip if priority/ doesn't exist (older installs may not have it)
    if [[ ! -d "$priority_dir" ]]; then
        return 1
    fi

    # Quarantine state dir: tracks per-file rejection counts across cron firings.
    local state_dir
    state_dir="$(pp_project_root "$project_name")/.discovery-state"

    local unhandled_files
    unhandled_files="$(_disc_find_unhandled_items "$priority_dir" "$priority_backlog" "PRIORITY-" "$state_dir" "$project_name")"

    if [[ -z "$unhandled_files" ]]; then
        return 1
    fi

    # Guard: refuse to write a bundle when no valid file paths remain.
    # stdout from _disc_find_unhandled_items contains only absolute paths.
    # This check is a belt-and-suspenders defence: if unhandled_files is
    # non-empty but contains no lines starting with '/', something unexpected ended
    # up in the variable (e.g., a log line) — do not corrupt a bundle with it.
    if ! echo "$unhandled_files" | grep -q '^/'; then
        log "discovery_step_priority: no valid item paths after filtering (possible malformed-filename rejections only); skipping bundle"
        return 1
    fi

    # Sort unhandled priority files by PRIORITY-NNNN integer ascending so that
    # lower-numbered priorities ship first.  Extract the numeric suffix from the
    # basename (e.g. PRIORITY-0007-... → 7) and use it as a sort key.
    local sorted_files
    sorted_files="$(while IFS= read -r _f; do
        [[ -z "$_f" ]] && continue
        local _base _num
        _base="$(basename "$_f")"
        _num="$(echo "$_base" | grep -oE 'PRIORITY-([0-9]+)' | head -n1 | grep -oE '[0-9]+')"
        printf '%s\t%s\n' "${_num:-0}" "$_f"
    done <<< "$unhandled_files" \
        | sort -t$'\t' -k1,1n \
        | cut -f2-)"
    unhandled_files="$sorted_files"

    # Apply max_priorities_per_run cap.  Read the configured value; default to 1
    # when the helper returns empty (key absent or project.cfg missing).
    local _cap_raw _cap
    _cap_raw="$(pp_max_priorities_per_run "$project_name")"
    _cap="${_cap_raw:-1}"

    # Slice: keep only the first $_cap files for this bundle.  The remainder
    # stay [ ] / Status open in priority_backlog.md and are picked up on the
    # next discovery iteration once the current RC closes.
    local bundle_files
    bundle_files="$(echo "$unhandled_files" | head -n "$_cap")"

    # Active-RC guard: refuse to bundle when an RC is in flight.
    # A priority bundle produced while an RC is open would target a version below
    # the in-flight RC, which is incoherent.  Defer until after the current RC ships.
    # Shared helper used by both discovery_step_bugs and discovery_step_priority
    # so the two steps cannot diverge in their Active-RC guard semantics.
    if ! _disc_active_rc_blocks "discovery_step_priority" "$project_name"; then
        return 1
    fi

    local target_version
    target_version="$(discovery_compute_next_patch "$project_name")"

    # Ceiling gate: skip bundling if the target version exceeds the project ceiling.
    # Emits a structured rejection log line per the documented format.
    if ! _disc_check_ceiling_and_log "$project_name" "$target_version" "discovery_step_priority"; then
        return 1
    fi

    local today requirements_dir bundle_path
    today="$(date +%Y%m%d)"
    requirements_dir="$(pp_requirements_dir "$project_name")"
    bundle_path="${requirements_dir}/${target_version}-priority-bundle-${today}.md"

    log "discovery_step_priority: bundling $(echo "$bundle_files" | wc -l) of $(echo "$unhandled_files" | wc -l) priority item(s) (cap=${_cap}) into ${bundle_path}"

    _disc_write_bundle "$bundle_path" "$target_version" "release" "true" "Priority bundle" "$bundle_files" "PRIORITY"

    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        local item_id
        item_id="$(basename "${f%.md}")"
        _disc_mark_cache_bundled "$priority_backlog" "$item_id"
        _disc_set_status_field "$f" "running"
    done <<< "$bundle_files"

    log "discovery_step_priority: bundle written, ${target_version} ready for Step 3 next iteration"
    return 0
}

# ---------------------------------------------------------------------------
# discovery_step_requirements <project_name>
#
# Step 3 of the pipeline. Scans requirements/ for *.md files where
# target_version > current_live, sorted by version (lowest first). Iterates
# ALL eligible files in semver order, applying the following rules per file:
#
#   - File matched in pm_backlog as [x] (done): skip it and continue to the
#     next bundle — this is a historical record, not an obstacle.
#   - File matched in pm_backlog as [ ], [W], or [A] (in flight): exit with
#     a 'PM in flight' log line. Only one PM task is allowed at a time.
#   - File not in pm_backlog at all: queue a PM ticket and return 0.
#
# Files with Target Version set to 'auto', 'next-patch', empty string, or the
# field entirely absent are treated as auto-sentinel files. Their effective
# version is computed via discovery_compute_next_patch at pickup time, and a
# log line is emitted recording the computed version and the source file path.
# Auto-sentinel files are sorted AFTER all explicit-version files.
#
# Ceiling gate: files whose target_version exceeds the project ceiling are
# logged as 'ceiling-blocked, skipping' and skipped during iteration.
# If no eligible file survives the ceiling+backlog checks, Step 3 produces no
# work (pipeline returns idle).
#
# Returns 0 if a PM ticket was queued. Returns 1 if nothing eligible.
# ---------------------------------------------------------------------------
discovery_step_requirements() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    # Belt-and-suspenders guard: refuse to queue PM when Active RC is in flight.
    # The pipeline orchestration in discovery_run_pipeline gates this step more
    # strictly via _disc_chain_idle (which also checks agent backlog tails), but
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

    # Resolve ceiling values for this project (empty string = no constraint).
    local max_minor max_major max_patch
    max_minor="$(pp_max_minor "$project_name" 2>/dev/null)" || max_minor=""
    max_major="$(pp_max_major "$project_name" 2>/dev/null)" || max_major=""
    max_patch="$(pp_max_patch "$project_name" 2>/dev/null)" || max_patch=""

    # Get ALL eligible requirements files sorted by version (lowest first).
    # Each line is either an absolute path or a path with ":auto" suffix for
    # auto-sentinel files. Ceiling-blocked files are NOT filtered here — we
    # want to log them explicitly as 'ceiling-blocked, skipping'.
    local all_eligible
    all_eligible="$(_disc_list_all_eligible_requirements "$requirements_dir" "$last_released" "$max_minor" "$max_major" "$max_patch")"

    if [[ -z "$all_eligible" ]]; then
        return 1
    fi

    # pm_backlog for in-flight checks (used only when bundle ## Status is "running").
    local pm_backlog_file
    pm_backlog_file="$(pp_queue_path "$project_name" "pm")"

    # Iterate all eligible files in version order.
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

        # Auto-sentinel: compute the next patch slot at pickup time and log it.
        if [[ "$_auto_sentinel" -eq 1 ]]; then
            local _resolved_version
            _resolved_version="$(discovery_compute_next_patch "$project_name")"
            log "discovery_step_requirements: auto-sentinel Target Version in ${chosen_file} resolved to ${_resolved_version} via discovery_compute_next_patch"
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
                if [[ -n "$_pm_marker_line" ]] && echo "$_pm_marker_line" | grep -qE '^\s*-\s+\[x\]'; then
                    # PM task is marked [x] — PM is done.
                    # The bundle status will be promoted to 'done' when the RC ships
                    # (cm-release.sh Step 16b).  Until then, skip this bundle.
                    log "discovery_step_requirements: PM already done for ${chosen_file} (pm_backlog [x] for ${_pm_task_id}), skipping"
                    continue
                else
                    # PM task present but not [x] — in flight.
                    log "discovery_step_requirements: PM in flight for ${chosen_file} (${_pm_task_id} not [x] in pm_backlog), aborting scan"
                    return 1
                fi
            else
                # ## PM Task field is absent or 'none' (older bundle or interrupted write).
                # Treat as in-flight to be conservative: do not re-queue PM.
                log "discovery_step_requirements: PM in flight for ${chosen_file} (## Status: running, ## PM Task not set), aborting scan"
                return 1
            fi
        fi

        # ## Status is 'open' (or absent/unrecognised) — bundle not yet queued.
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
        local _pm_output _queued_task_id
        _pm_output="$(PGAI_PROJECT_NAME="$project_name" "$pm_agent_script" "$chosen_file" 2>&1)"
        echo "$_pm_output" >&2  # re-emit so it appears in caller's log

        # Extract task ID from "  Folder  : <path>" line produced by pm-agent.sh.
        # The task ID is the basename of the folder path.
        _queued_task_id="$(echo "$_pm_output" \
            | grep -E '^\s*Folder\s*:' \
            | head -n1 \
            | sed -E 's|.*Folder\s*:\s*||; s|/[[:space:]]*$||; s|.*/||')" || true

        # Mark the bundle as queued-for-PM.  Even if task-ID extraction failed,
        # setting ## Status to 'running' prevents a second PM queuing attempt.
        _disc_update_bundle_pm_queued "$chosen_file" "${_queued_task_id:-none}"
        log "discovery_step_requirements: bundle marked running (PM task: ${_queued_task_id:-none})"

        return 0
    done <<< "$all_eligible"

    # Exhausted all eligible files without finding unprocessed work.
    return 1
}

# ---------------------------------------------------------------------------
# DISCOVERY_LAST_STATUS
#
# Set by discovery_run_pipeline on every invocation. Callers that need to
# know whether work was produced (e.g. pm-agent.sh --auto loop) can inspect
# this variable after each call. Three values:
#
#   produced_work  — pipeline did something useful (step 1, 2, or 3 fired)
#   idle           — pipeline found nothing to do (step 4 exit)
#   blocked        — pipeline was blocked by Active RC or HALT flag
#
# The return code of discovery_run_pipeline is ALWAYS 0 so that existing
# cron callers (wake/claude.sh) are unaffected by this new signal.
# ---------------------------------------------------------------------------
DISCOVERY_LAST_STATUS="idle"

# ---------------------------------------------------------------------------
# discovery_run_pipeline <project_name>
#
# Top-level entry. Runs the 4-step pipeline once. Always returns 0 (idle is
# a normal outcome, not an error). Sets DISCOVERY_LAST_STATUS to one of:
#   produced_work / idle / blocked
# so that looping callers (pm-agent.sh --auto) know whether to iterate again.
# ---------------------------------------------------------------------------
discovery_run_pipeline() {
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
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # GUARD: global HALT flag blocks the entire pipeline (all steps, all projects)
    if [[ -f "${TEAM_ROOT}/HALT" ]]; then
        log "discovery_run_pipeline [${project_name}]: global HALT, pipeline blocked"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # Log active ceilings when at least one is configured — visible in cron logs
    # without being noisy on every iteration when no ceilings are set.
    local _ceil_max_minor _ceil_max_major _ceil_max_patch _ceil_parts
    _ceil_max_minor="$(pp_max_minor "$project_name" 2>/dev/null)" || _ceil_max_minor=""
    _ceil_max_major="$(pp_max_major "$project_name" 2>/dev/null)" || _ceil_max_major=""
    _ceil_max_patch="$(pp_max_patch "$project_name" 2>/dev/null)" || _ceil_max_patch=""
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

    # GUARD 4: pm_ticket_in_flight — exit before Steps 1/2/3 when a decompose-v*
    # PM ticket is pending in pm_backlog ([ ] or [A] marker).  This closes the
    # window between "operator drops requirements file → discovery queues PM" and
    # "CM-open-rc fires → Active RC is set".  During that window Active RC is still
    # "none", so the existing chain-idle guard would not block Steps 1+2.  Any bug
    # or priority filed in this window would be bundled into a patch version that
    # ends up stranded once the RC ships.
    if _disc_pm_ticket_in_flight "$project_name"; then
        log "discovery_run_pipeline [${project_name}]: pending PM ticket detected; exiting (RC opening soon)"
        DISCOVERY_LAST_STATUS="blocked"
        return 0
    fi

    # STEP 1: bundle bugs.
    # Skipped when GUARD 4 fires (PM ticket pending, RC opening soon).
    # Also skipped when Active RC is set — discovery_step_bugs contains an
    # active-RC guard that defers bundling until after the in-flight RC ships,
    # preventing bundles whose target version would be below the RC version.
    local _step1_produced=0
    if discovery_step_bugs "$project_name"; then
        _step1_produced=1
    fi

    # STEP 2: bundle priority items (same guard logic as Step 1 above).
    local _step2_produced=0
    if discovery_step_priority "$project_name"; then
        _step2_produced=1
    fi

    # STEP 3: queue PM for the lowest-version unprocessed requirements bundle.
    # Gated by _disc_chain_idle: only runs when Active RC = none AND every
    # named agent backlog's latest entry is [x]. This prevents queuing a new
    # PM task while any agent is still finishing the current RC.
    if _disc_chain_idle "$project_name"; then
        if discovery_step_requirements "$project_name"; then
            DISCOVERY_LAST_STATUS="produced_work"
            return 0
        fi
    fi

    # STEP 4: idle (Steps 1 and 2 found nothing; Step 3 found nothing or was gated)
    if [[ "$_step1_produced" -eq 1 || "$_step2_produced" -eq 1 ]]; then
        # Steps 1 or 2 produced work but Step 3 found nothing — still produced_work
        DISCOVERY_LAST_STATUS="produced_work"
        return 0
    fi

    log "discovery_run_pipeline: no work pending, idle exit"
    DISCOVERY_LAST_STATUS="idle"
    return 0
}

# ===========================================================================
# Public helpers for stale-requirements detection (used by dashboard-attention.sh
# and any other external caller that needs to inspect requirements/ directly).
# ===========================================================================

# ---------------------------------------------------------------------------
# parse_target_version_from_filename <filename>
#
# Parse the target version (vX.Y.Z) from a requirements file's basename.
# The expected filename pattern is: vX.Y.Z-<slug>.md
#
# On success: echoes the version string (e.g. "v0.2.0") and returns 0.
# On failure (no match): echoes nothing and returns 1.
#
# This is a wrapper around semver_from_filename that makes the return code
# unambiguous (semver_from_filename always returns 0; this returns 1 on miss).
#
# Examples:
#   parse_target_version_from_filename "v0.2.0-pvg-freeze-pip-20260517.md"  -> "v0.2.0"
#   parse_target_version_from_filename "no-version-here.md"                 -> "" (return 1)
# ---------------------------------------------------------------------------
parse_target_version_from_filename() {
    local fname
    fname="$(basename "${1:-}")"
    local ver
    ver="$(semver_from_filename "$fname")"
    if [[ -z "$ver" ]]; then
        return 1
    fi
    echo "$ver"
    return 0
}

# ---------------------------------------------------------------------------
# is_requirements_bundle_file <filename>
#
# Return 0 (true) when the filename matches an auto-generated bundle pattern:
#   *-bugfix-bundle-*.md
#   *-priority-bundle-*.md
#
# Return 1 (false) for all other filenames (operator-authored requirements).
#
# Examples:
#   is_requirements_bundle_file "v0.21.10-bugfix-bundle-20260507.md"   -> 0 (true)
#   is_requirements_bundle_file "v0.2.0-pvg-freeze-pip-20260517.md"    -> 1 (false)
# ---------------------------------------------------------------------------
is_requirements_bundle_file() {
    local fname
    fname="$(basename "${1:-}")"
    [[ "$fname" == *-bugfix-bundle-*.md || "$fname" == *-priority-bundle-*.md ]]
}

# ---------------------------------------------------------------------------
# discovery_scan_stale_requirements <project_name>
#
# Stateless scan: for each *.md file in a project's requirements/ directory,
# identify operator-authored files whose target_version <= current_live and
# which have no matching release-notes/<target_version>*.md entry (i.e.,
# orphaned — never shipped).
#
# For each stale file found, outputs one line in TSV format:
#   <filename>\t<target_version>\t<current_live>\t<cross_project_hint>
#
# Where <cross_project_hint> is the name of another registered project whose
# full name appears verbatim in the filename slug or the file's first 10 lines,
# or an empty string when no unique match is found.
#
# Files are excluded from output when ANY of the following apply:
#   - Filename matches *-bugfix-bundle-*.md or *-priority-bundle-*.md
#   - target_version cannot be parsed (filename lacks vX.Y.Z prefix)
#   - target_version > current_live (normal queued work, not stale)
#   - file's own ## Status header is 'done'
#   - release-notes/<target_version>*.md exists (shipped artifact; not orphaned)
#
# Arguments:
#   $1 — project_name (required)
#
# Requires: semver_lte (from lib/semver.sh), pp_requirements_dir,
#           pp_project_root, projects_cfg_list (from lib/projects.sh, if
#           already loaded by the caller). If projects_cfg_list is not
#           available, cross-project hints are skipped silently.
#
# Returns 0 always (empty output when no stale files found).
# ---------------------------------------------------------------------------
discovery_scan_stale_requirements() {
    local project_name="${1:-}"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local requirements_dir release_notes_dir dev_release_notes_dir
    requirements_dir="$(pp_requirements_dir "$project_name" 2>/dev/null)" || return 0
    # Primary release-notes dir: under the kanban project root.
    release_notes_dir="$(pp_project_root "$project_name" 2>/dev/null)/release-notes"
    # Secondary and tertiary release-notes dirs: under the dev tree.
    # The dev tree is the canonical location where cm-release.sh writes release notes;
    # the live install's project directory typically does not have release-notes/.
    # Two locations to check in the dev tree:
    #   - projects/<name>/release-notes/  (per-project layout)
    #   - release-notes/                  (root-level location)
    dev_release_notes_dir=""
    local dev_release_notes_root_dir=""
    local _rn_cfg_file
    # Source: project.cfg [project] dev_tree_path (falls back to PROJECT.cfg)
    _rn_cfg_file="$(_pp_project_cfg_file "$(pp_project_root "$project_name" 2>/dev/null)")"
    if [[ -n "$_rn_cfg_file" ]]; then
        local _dev_tree
        _dev_tree="$(_pp_read_cfg_key "$_rn_cfg_file" project dev_tree_path "")"
        if [[ -n "$_dev_tree" ]]; then
            # Per-project release-notes
            dev_release_notes_dir="${_dev_tree}/projects/${project_name}/release-notes"
            # Root-level release-notes
            dev_release_notes_root_dir="${_dev_tree}/release-notes"
        fi
    fi

    [[ -d "$requirements_dir" ]] || return 0

    # Determine current_live for this project.
    local current_live
    current_live="$(pp_last_released_version "$project_name" 2>/dev/null || echo "v0.0.0")"
    [[ -z "$current_live" || "$current_live" == "none" ]] && current_live="v0.0.0"

    # Collect other registered project names (for cross-project hints).
    # projects_cfg_list may not be available if projects.sh was not sourced;
    # in that case we silently skip the hint check.
    local all_project_names=()
    if declare -F projects_cfg_list >/dev/null 2>&1; then
        while IFS= read -r _pn; do
            [[ -z "$_pn" || "$_pn" == "$project_name" ]] && continue
            all_project_names+=("$_pn")
        done < <(projects_cfg_list 2>/dev/null || true)
    fi

    # Iterate requirements/*.md files.
    local req_file
    for req_file in "${requirements_dir}"/*.md; do
        [[ -f "$req_file" ]] || continue
        local fname
        fname="$(basename "$req_file")"

        # Skip bundle files (legitimate stale artifacts from past iterations).
        if is_requirements_bundle_file "$fname"; then
            continue
        fi

        # Parse target_version from filename.
        local target_version
        target_version="$(parse_target_version_from_filename "$fname")" || continue

        # Skip when target_version > current_live (normal queued work).
        if ! semver_lte "$target_version" "$current_live"; then
            continue
        fi

        # Honor ## Status: done as authoritative retirement signal.
        # A requirements file that declares ## Status: done was explicitly marked
        # shipped by the operator.  Skip it unconditionally, before the (more
        # expensive) release-notes lookup.  This mirrors the done-suppression
        # already used in the bundle pickup path (~line 523) and the discovery
        # bundle path (~line 1612).
        local _req_status
        _req_status="$(_disc_read_bundle_field "$req_file" "Status" 2>/dev/null || echo "open")"
        _req_status="${_req_status,,}"  # lowercase
        if [[ "$_req_status" == "done" ]]; then
            continue
        fi

        # Check for a matching release-notes entry (shipped artifact → skip).
        # Three locations are checked in priority order:
        #   1. Kanban project root's release-notes/ (live install)
        #   2. Dev tree's per-project release-notes/
        #   3. Dev tree's root release-notes/
        # Use exact-version match to prevent vX.Y.Z*.md from matching
        # vX.Y.Z(N+1)-... release notes.  Check bare exact vX.Y.Z.md plus
        # hyphen-anchored vX.Y.Z-*.md for any future hyphenated release note names.
        local _release_note_found=false
        if [[ -d "$release_notes_dir" ]] && \
           { compgen -G "${release_notes_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${release_notes_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "false" && -n "$dev_release_notes_dir" && \
              -d "$dev_release_notes_dir" ]] && \
           { compgen -G "${dev_release_notes_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${dev_release_notes_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "false" && -n "$dev_release_notes_root_dir" && \
              -d "$dev_release_notes_root_dir" ]] && \
           { compgen -G "${dev_release_notes_root_dir}/${target_version}-*.md" >/dev/null 2>&1 || \
             [[ -f "${dev_release_notes_root_dir}/${target_version}.md" ]]; }; then
            _release_note_found=true
        fi
        if [[ "$_release_note_found" == "true" ]]; then
            continue
        fi

        # File is stale and orphaned. Compute cross-project hint.
        local hint=""
        if [[ "${#all_project_names[@]}" -gt 0 ]]; then
            # Scan filename slug and first 10 lines of file content.
            local file_head
            file_head="$(head -n 10 "$req_file" 2>/dev/null || true)"
            local scan_text="${fname} ${file_head}"

            local matched_projects=()
            local _other_proj
            for _other_proj in "${all_project_names[@]}"; do
                # Exact full-name match only (avoids false positives on shared substrings).
                if [[ "$scan_text" == *"${_other_proj}"* ]]; then
                    matched_projects+=("$_other_proj")
                fi
            done

            # Emit hint only when exactly one other project matched.
            if [[ "${#matched_projects[@]}" -eq 1 ]]; then
                hint="${matched_projects[0]}"
            fi
        fi

        # Output TSV line: filename<TAB>target_version<TAB>current_live<TAB>hint
        printf '%s\t%s\t%s\t%s\n' "$fname" "$target_version" "$current_live" "$hint"
    done

    return 0
}

# ===========================================================================
# Internal helpers (prefixed _disc_)
# ===========================================================================

# _disc_active_rc_blocks <step_name> <project_name>
#
# Shared active-RC guard: returns 1 (blocks) when an RC is in flight for the
# given project, 0 (allows) when the Active RC field is "none".
#
# Callers (discovery_step_bugs and discovery_step_priority) use this helper
# so the two bundler steps cannot diverge in their Active-RC guard semantics.
#
# Three cases:
#   - release-state.md missing or unreadable → log WARNING, return 1 (safe-fail)
#   - Active RC = "none" (case-insensitive, whitespace-stripped) → return 0 (allow)
#   - Active RC = any non-"none" value → log skip message, return 1 (block)
_disc_active_rc_blocks() {
    local step_name="$1"
    local project_name="$2"
    local _active_rc
    if ! _active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null)"; then
        log "${step_name}: WARNING release-state.md missing or unreadable for ${project_name}; skipping bundle (cannot verify RC state)"
        return 1
    fi
    _active_rc="${_active_rc:-none}"
    # Normalise: strip leading/trailing whitespace, compare case-insensitively
    _active_rc="$(echo "$_active_rc" | tr -d '[:space:]')"
    if [[ "${_active_rc,,}" != "none" ]]; then
        log "skipping bundle — Active RC ${_active_rc} in flight"
        return 1
    fi
    return 0
}

# _disc_check_ceiling_and_log <project_name> <version> <caller_label>
#
# Reads max_major, max_minor, and max_patch from project.cfg (or PROJECT.cfg) and checks all
# three against the given version. If any ceiling is exceeded, emits the
# documented rejection log line:
#
#   [<timestamp>] discovery: PM not queued for <version>: <component> version
#                 <value> exceeds <ceiling_name>=<ceiling_value>
#
# Returns 0 when the version is within all configured ceilings (proceed).
# Returns 1 when at least one ceiling is exceeded (skip/defer).
#
# The first failing ceiling component is reported and the function returns
# immediately (one log line per call per rejected version — as required).
_disc_check_ceiling_and_log() {
    local project_name="$1"
    local version="$2"
    # caller_label is unused in log output (log format is operator-facing)
    # but kept as a positional argument for future diagnostic use.

    # Strip leading 'v' or 'V'.
    local v_clean="${version#v}"
    v_clean="${v_clean#V}"

    # Parse X.Y.Z — malformed versions are treated as exceeding ceiling
    # (conservative: do not queue PM for an unparseable target version).
    if ! [[ "$v_clean" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        log "PM not queued for ${version}: target version is malformed (expected vX.Y.Z)"
        return 1
    fi
    local ver_major="${BASH_REMATCH[1]}"
    local ver_minor="${BASH_REMATCH[2]}"
    local ver_patch="${BASH_REMATCH[3]}"

    local max_major max_minor max_patch
    max_major="$(pp_max_major "$project_name" 2>/dev/null)" || max_major=""
    max_minor="$(pp_max_minor "$project_name" 2>/dev/null)" || max_minor=""
    max_patch="$(pp_max_patch "$project_name" 2>/dev/null)" || max_patch=""

    if [[ -n "$max_major" ]] && (( ver_major > max_major )); then
        log "PM not queued for ${version}: major version ${ver_major} exceeds max_major=${max_major}"
        return 1
    fi

    if [[ -n "$max_minor" ]] && (( ver_minor > max_minor )); then
        log "PM not queued for ${version}: minor version ${ver_minor} exceeds max_minor=${max_minor}"
        return 1
    fi

    if [[ -n "$max_patch" ]] && (( ver_patch > max_patch )); then
        log "PM not queued for ${version}: patch version ${ver_patch} exceeds max_patch=${max_patch}"
        return 1
    fi

    return 0
}

# _disc_get_release_field <project> <field> — read ## FIELD from release-state.md
_disc_get_release_field() {
    local project_name="$1"
    local field="$2"
    local release_state
    release_state="$(pp_release_state "$project_name")"
    [[ -f "$release_state" ]] || return 1
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

# _disc_chain_idle <project_name>
#
# Returns 0 (true) when the entire RC chain is idle, meaning it is safe to
# queue a new PM-decompose task (Step 3). Returns 1 (false, gate closed) and
# emits a log line naming the offending condition when the chain is not idle.
#
# The chain is idle when ALL of the following hold:
#   1. Active RC in release-state.md is "none"
#   2. The latest task entry in each of the named agent backlogs is marked [x]
#      (or the backlog file has no task entries yet, which counts as idle)
#
# Agent backlogs checked (in this order):
#   pm_backlog, cm_backlog, coder_backlog, tester_backlog, writer_backlog (if present)
#
# "Latest entry" means the last line in the backlog file that matches the
# task entry pattern "- [marker] TASK-ID". Lines that are not task entries
# (headers, comments, blank lines) are ignored.
#
# Usage:
#   if _disc_chain_idle "$project_name"; then
#       # safe to queue PM
#   fi
_disc_chain_idle() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    # Check 1: Active RC must be "none"
    local active_rc
    active_rc="$(_disc_get_release_field "$project_name" "Active RC" 2>/dev/null || echo "none")"
    if [[ -n "$active_rc" && "$active_rc" != "none" ]]; then
        log "discovery_step_requirements: skipped — Active RC=${active_rc} (chain not idle)"
        return 1
    fi

    # Check 2: Each named agent backlog's latest task entry must be [x].
    # Use pp_queue_path for each agent so queue paths resolve correctly.
    local _backlog_agents=("pm" "cm" "coder" "tester")
    local _agent _backlog_file _tail_marker _tail_line

    for _agent in "${_backlog_agents[@]}"; do
        _backlog_file="$(pp_queue_path "$project_name" "$_agent")"
        if [[ ! -f "$_backlog_file" ]]; then
            # Missing backlog file counts as idle (no tasks in flight)
            continue
        fi
        # Find the last line that looks like a task entry: "- [x] ...", "- [ ] ...", etc.
        _tail_line="$(grep -E '^\s*-\s+\[.\]\s+\S' "$_backlog_file" | tail -n1 || true)"
        if [[ -z "$_tail_line" ]]; then
            # No task entries at all — counts as idle
            continue
        fi
        # Extract the marker character from the last task entry
        _tail_marker="$(echo "$_tail_line" | sed -E 's/^\s*-\s+\[([^]]*)\]\s+.*/\1/' | tr '[:upper:]' '[:lower:]')"
        if [[ "$_tail_marker" != "x" ]]; then
            log "discovery_step_requirements: skipped — ${_agent}_backlog tail is [${_tail_marker}] (chain not idle)"
            return 1
        fi
    done

    # Also check writer_backlog if it exists (optional)
    _backlog_file="$(pp_queue_path "$project_name" "writer")"
    if [[ -f "$_backlog_file" ]]; then
        _tail_line="$(grep -E '^\s*-\s+\[.\]\s+\S' "$_backlog_file" | tail -n1 || true)"
        if [[ -n "$_tail_line" ]]; then
            _tail_marker="$(echo "$_tail_line" | sed -E 's/^\s*-\s+\[([^]]*)\]\s+.*/\1/' | tr '[:upper:]' '[:lower:]')"
            if [[ "$_tail_marker" != "x" ]]; then
                log "discovery_step_requirements: skipped — writer_backlog tail is [${_tail_marker}] (chain not idle)"
                return 1
            fi
        fi
    fi

    # All checks passed — chain is idle
    return 0
}

# _disc_pm_ticket_in_flight <project_name>
#
# Returns 0 (true) when pm_backlog.md contains at least one pending or active
# decompose-v* entry (marker [ ] or [A]).  Returns 1 (false) when all entries
# are done ([x]) or the backlog does not exist.
#
# "Pending" means the PM agent has been queued but not yet started;
# "active" means the PM agent is currently working (marker [A]).  Either
# state means an RC is conceptually in flight even if Active RC in
# release-state.md has not yet been set (i.e. CM-open-rc has not fired yet).
#
# Pattern rationale: only entries whose task ID contains "decompose-v" followed
# by a version-with-hyphens (e.g. "decompose-v0-21-40-") are matched.  This
# is conservative — it avoids false-positives on hypothetical non-decompose PM
# tickets.  The pattern matches both dot-format (future) and hyphen-format
# (current pm-agent.sh output) by anchoring on "decompose-v" and a digit.
#
# Usage:
#   if _disc_pm_ticket_in_flight "$project_name"; then
#       # PM ticket pending; exit before Steps 1/2/3
#   fi
_disc_pm_ticket_in_flight() {
    local project_name="$1"
    project_name="$(pp_require_project_context "${project_name:-}")" || return 1

    local pm_backlog_file
    pm_backlog_file="$(pp_queue_path "$project_name" "pm")"

    # No backlog file → nothing in flight
    [[ -f "$pm_backlog_file" ]] || return 1

    # Look for any task entry with a non-[x] marker ([ ] or [A]) whose ID
    # contains "decompose-v<digit>" — the canonical form for PM decompose tickets.
    # Marker is the single character inside the brackets at the start of the line.
    if grep -qE '^\s*-\s+\[([^xX])\]\s+\S.*decompose-v[0-9]' "$pm_backlog_file" 2>/dev/null; then
        return 0  # at least one pending/active decompose-v* ticket found
    fi

    return 1  # all decompose-v* entries are [x] or there are none
}

# _disc_git_tag_exists <tag> [dev_tree] — check both local and origin tags
# Resolution order for the git working directory:
#   1. Explicit dev_tree argument (passed by discovery_compute_next_patch)
#   2. $PGAI_DEV_TREE_PATH environment variable (for callers that set it directly)
#   3. cwd-based fallback (preserves test behavior when neither is set)
#
# Using an explicit dev_tree keeps bump-around consistent with
# pp_last_released_version, which reads its dev tree from project.cfg (or PROJECT.cfg).
_disc_git_tag_exists() {
    local tag="$1"
    local dev_tree="${2:-${PGAI_DEV_TREE_PATH:-}}"

    # Prefer dev tree if set and valid
    if [[ -n "$dev_tree" && -d "$dev_tree/.git" ]]; then
        git -C "$dev_tree" tag -l "$tag" 2>/dev/null | grep -qx "$tag" && return 0
        # Also check origin (cheap if refs already fetched recently)
        git -C "$dev_tree" ls-remote --tags origin "refs/tags/$tag" 2>/dev/null | grep -q "refs/tags/$tag" && return 0
        return 1
    fi

    # Fallback: cwd-based check (preserves behavior when dev tree is not configured)
    if git rev-parse --git-dir >/dev/null 2>&1; then
        git tag -l "$tag" 2>/dev/null | grep -qx "$tag" && return 0
        git ls-remote --tags origin "refs/tags/$tag" 2>/dev/null | grep -q "refs/tags/$tag" && return 0
    fi
    return 1
}

# _disc_check_bug_id_collisions <bugs_dir>
#
# Scan bugs_dir for files sharing the same BUG-NNNN numeric prefix and emit a
# WARNING log line for each collision detected.  Delegates to
# detect_duplicate_bug_ids() in pm-agent/lib/bug_scanner.py.
# Non-fatal: always returns 0 so the pipeline continues.
_disc_check_bug_id_collisions() {
    local bugs_dir="$1"
    [[ -d "$bugs_dir" ]] || return 0

    # Locate the bug_scanner module.  Search order:
    #   1. ${TEAM_ROOT}/pm-agent  — live install layout
    #   2. ${_disc_this_dir}/../../../team/pm-agent — dev-tree layout when sourced
    #      from team/scripts/lib/ (BASH_SOURCE[0] resolves 3 levels up to repo root,
    #      then descend into team/pm-agent)
    #   3. ${_disc_this_dir}/../pm-agent — alternate dev-tree layout
    # The first directory whose lib/bug_scanner.py exists is used.
    local _disc_this_dir _scanner_dir
    _disc_this_dir="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"

    local _candidate
    for _candidate in \
        "${TEAM_ROOT}/pm-agent" \
        "${_disc_this_dir}/../../pm-agent" \
        "${_disc_this_dir}/../../../team/pm-agent" \
        "${_disc_this_dir}/../pm-agent"
    do
        if [[ -f "${_candidate}/lib/bug_scanner.py" ]]; then
            _scanner_dir="$_candidate"
            break
        fi
    done

    if [[ -z "$_scanner_dir" ]]; then
        # Cannot locate bug_scanner; skip collision check silently (non-fatal).
        return 0
    fi

    python3 - "$bugs_dir" "$_scanner_dir" <<'PY'
import sys, warnings

bugs_dir = sys.argv[1]
scanner_dir = sys.argv[2]

# Insert pm-agent into path so we can import bug_scanner
sys.path.insert(0, scanner_dir)
try:
    from lib.bug_scanner import detect_duplicate_bug_ids
except ImportError as e:
    print(f"WARNING: _disc_check_bug_id_collisions: could not import bug_scanner: {e}", file=sys.stderr)
    sys.exit(0)

# Capture warnings emitted by detect_duplicate_bug_ids and forward them as
# WARNING-prefixed lines to stderr so the shell caller's log() shim sees them.
with warnings.catch_warnings(record=True) as caught:
    warnings.simplefilter("always")
    detect_duplicate_bug_ids(bugs_dir)

for w in caught:
    print(f"WARNING: {w.message}", file=sys.stderr)

sys.exit(0)
PY
    return 0
}

# _disc_maybe_quarantine <state_dir> <parent_dir> <filename> [reason] [project_name]
#
# Increment the per-file rejection counter for <filename> in the state file at
# <state_dir>/rejected-counts.tsv.  If the counter reaches the configured
# threshold (PGAI_DISCOVERY_REJECT_THRESHOLD, default 3), move the file from
# <parent_dir>/<filename> to projects/<project_name>/rejected/<filename>
# (project-level rejected directory via pp_rejected_dir).
#
# When project_name is empty or pp_rejected_dir fails, falls back to the
# <parent_dir>/.rejected/<filename> destination.
#
# After a successful move, a sidecar file <filename>.reason is written into
# the same rejected/ directory with the following key=value fields:
#   original_type  — basename of parent_dir (bugs|priority|requirements)
#   original_dir   — absolute path of parent_dir (used by recover-rejected.sh)
#   reason         — rejection reason string (may be empty)
#   rejected_at    — ISO 8601 UTC timestamp of the quarantine event
#   retry_count    — number of times this file has been quarantined (starts at 0)
#
# Re-quarantine: if the sidecar already exists in the rejected/ directory,
# retry_count is read from it, incremented by 1, and the sidecar is rewritten
# with an updated rejected_at.  Sidecar write failures are logged but do not
# abort the move (the file move is the authoritative effect).
#
# State file format (TSV, one record per file):
#   <filename>\t<count>\t<last-seen-ISO8601>\t<reason>
#
# The optional 4th column <reason> carries the last rejection reason string
# (e.g. "malformed filename (expected pattern: BUG-NNNN-<slug>.md)").
# Rows with only 3 columns have the reason field defaulting to empty string.
#
# Constraints:
#   - Files are moved, never deleted.
#   - Rejected directory is created on demand (via pp_rejected_dir or mkdir -p).
#   - The state file is safe to delete; it is recreated on the next rejection.
#   - Counter resets implicitly when a file is moved (the source path no longer
#     exists, so the next cron tick does not see it for rejection; if a same-named
#     file is later re-introduced, it starts with count=0 in the state file or is
#     absent from it, so it gets a fresh count).
#
# Logs actions to stderr (operator-visible).
# ---------------------------------------------------------------------------
_disc_maybe_quarantine() {
    local state_dir="$1"
    local parent_dir="$2"
    local filename="$3"
    local reason="${4:-}"        # optional rejection reason (4th column in TSV)
    local project_name="${5:-}"  # optional project name for pp_rejected_dir routing
    local source_file="${parent_dir}/${filename}"

    # Guard: source file must still exist (could have been moved by a concurrent call)
    [[ -f "$source_file" ]] || return 0

    local threshold="${PGAI_DISCOVERY_REJECT_THRESHOLD:-3}"

    # Ensure state directory exists
    mkdir -p "$state_dir"

    local state_file="${state_dir}/rejected-counts.tsv"
    local now
    now="$(date -Iseconds 2>/dev/null || date '+%Y-%m-%dT%H:%M:%S')"

    # Read current count for this filename (0 if absent).
    # State file format: <filename>\t<count>\t<timestamp>[\t<reason>]
    # 3-column rows are accepted; reason defaults to ''.
    local current_count=0
    if [[ -f "$state_file" ]]; then
        local found_line
        found_line="$(grep -P "^${filename}\t" "$state_file" 2>/dev/null | head -1 || true)"
        if [[ -n "$found_line" ]]; then
            current_count="$(echo "$found_line" | cut -f2)"
            # Validate numeric
            [[ "$current_count" =~ ^[0-9]+$ ]] || current_count=0
        fi
    fi

    local new_count=$(( current_count + 1 ))

    # Update or insert the record in the state file using Python for safety.
    # Writes 4-column rows: filename, count, timestamp, reason.
    # When reading existing rows, preserves 3-column rows for other files
    # untouched; only the row for <filename> is rewritten in 4-column form.
    python3 - "$state_file" "$filename" "$new_count" "$now" "$reason" <<'PY'
import pathlib, sys

state_file = pathlib.Path(sys.argv[1])
filename = sys.argv[2]
new_count = sys.argv[3]
now = sys.argv[4]
reason = sys.argv[5] if len(sys.argv) > 5 else ""

lines = []
if state_file.is_file():
    for line in state_file.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0] == filename:
            continue  # will be replaced below
        lines.append(line)

lines.append(f"{filename}\t{new_count}\t{now}\t{reason}")
state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

    if [[ "$new_count" -lt "$threshold" ]]; then
        log "_disc_maybe_quarantine: ${filename}: rejection count ${new_count}/${threshold}; not yet quarantined"
        return 0
    fi

    # Threshold reached: move file to the project-level rejected/ directory.
    # Use pp_rejected_dir when project_name is known; fall back to
    # <parent_dir>/.rejected/ when it is empty or the helper is unavailable.
    local rejected_dir=""
    if [[ -n "$project_name" ]] && declare -F pp_rejected_dir >/dev/null 2>&1; then
        rejected_dir="$(pp_rejected_dir "$project_name" 2>/dev/null)" || rejected_dir=""
    fi
    if [[ -z "$rejected_dir" ]]; then
        # Legacy fallback: keep files co-located with their source directory.
        rejected_dir="${parent_dir}/.rejected"
        mkdir -p "$rejected_dir"
    fi
    local dest_file="${rejected_dir}/${filename}"

    # Determine sidecar retry_count: if the sidecar already exists (re-quarantine),
    # read the existing retry_count and increment; otherwise start at 0.
    local sidecar_file="${rejected_dir}/${filename}.reason"
    local retry_count=0
    if [[ -f "$sidecar_file" ]]; then
        local _existing_count
        _existing_count="$(grep -E '^retry_count=' "$sidecar_file" 2>/dev/null | head -1 | cut -d= -f2 || true)"
        if [[ "$_existing_count" =~ ^[0-9]+$ ]]; then
            retry_count=$(( _existing_count + 1 ))
        else
            retry_count=1
        fi
    fi

    # Derive original_type from the basename of parent_dir (bugs|priority|requirements).
    local original_type
    original_type="$(basename "$parent_dir")"

    # ISO 8601 UTC timestamp for the sidecar.
    local rejected_at
    rejected_at="$(date -u +'%Y-%m-%dT%H:%M:%SZ' 2>/dev/null || date -Iseconds)"

    if mv "$source_file" "$dest_file"; then
        # Emit multi-line quarantine log with reason and recovery hint.
        # Line 1 — summary
        log "_disc_maybe_quarantine: ${filename}: quarantined after ${new_count} failed parse(s)."
        # Line 2 — reason (from the 4th TSV column written by the validator)
        if [[ -n "$reason" ]]; then
            log "  Reason: ${reason}"
        else
            log "  Reason: unknown"
        fi
        # Line 3 — recovery hint with example rename, chosen based on filename prefix.
        # Constraints: use the reason/pattern already stored; do not re-derive it.
        # Note: PRIORITY and BUG share the same date-optional pattern family
        # (^{PREFIX}-[0-9]{4,}-.+\.md$); the PRIORITY branch is intentionally
        # absent here — a dateless PRIORITY is valid and does not need renaming.
        if [[ "$filename" == BUG-* ]]; then
            log "  Recovery: rename file to match pattern ^BUG-[0-9]{4,}-.+\\.md\$, e.g."
            local _stem="${filename%.md}"
            local _num _slug _example_name
            _num="$(echo "$_stem" | sed 's/^BUG-\([0-9]*\)-.*/\1/')"
            _slug="$(echo "$_stem" | sed 's/^BUG-[0-9]*-//')"
            _example_name="BUG-${_num}-${_slug}.md"
            log "    '${_example_name}', then move back to $(basename "${parent_dir}")/"
        else
            log "  Recovery: rename file to match the expected pattern, then move back to $(basename "${parent_dir}")/"
        fi
        # Line 4 — quarantine path
        log "  Quarantine path: ${dest_file}"

        # Write sidecar <filename>.reason co-located with the quarantined file.
        # Atomicity: the file move above is the authoritative effect; sidecar write
        # failures are logged and do not abort.  On re-quarantine, the sidecar is
        # overwritten with an incremented retry_count and refreshed rejected_at.
        if printf '%s\n' \
            "original_type=${original_type}" \
            "original_dir=${parent_dir}" \
            "reason=${reason}" \
            "rejected_at=${rejected_at}" \
            "retry_count=${retry_count}" \
            > "$sidecar_file" 2>/dev/null; then
            log "  Sidecar written: ${sidecar_file} (retry_count=${retry_count})"
        else
            log "  WARNING: failed to write sidecar ${sidecar_file}; quarantine move succeeded but reason metadata is absent"
        fi

        # Remove the entry from the state file so a re-introduced same-named file
        # starts with a fresh counter.
        python3 - "$state_file" "$filename" <<'PY'
import pathlib, sys
state_file = pathlib.Path(sys.argv[1])
filename = sys.argv[2]
if state_file.is_file():
    lines = [l for l in state_file.read_text(encoding="utf-8").splitlines()
             if not (l.split("\t")[0] == filename)]
    state_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
    else
        log "_disc_maybe_quarantine: WARNING: failed to move ${source_file} to ${dest_file}; will retry next cron tick"
    fi
}

# _disc_find_unhandled_items <dir> <backlog_cache> <id_prefix> [state_dir] [project_name]
# Find items in <dir> that:
#   1. Match the canonical filename pattern for the given id_prefix
#      BUG-    -> ^BUG-[0-9]{4,}-.+\.md$      (date-optional; sibling of PRIORITY-)
#      PRIORITY- -> ^PRIORITY-[0-9]{4,}-.+\.md$  (date-optional; sibling of BUG-)
#                  OR (TESTER Path C) ^v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+\.md$
#   2. Are NOT marked [x] in the cache file
#   3. Have ## Status: open in their header
# Outputs newline-separated absolute paths on stdout.
#
# .md files in the directory that do NOT match the canonical pattern (and are
# not README.md and not inside templates/) produce a rejection message on
# stderr ONLY — never on stdout.  This ensures that rejection log output
# cannot contaminate the validated-items list returned to the caller.
# Rejection format: ERROR: rejecting <filename>: <reason> (expected pattern: <expected>)
#
# When the optional [state_dir] argument is supplied, each rejected file is
# passed to _disc_maybe_quarantine, which increments a per-file counter and
# moves the file to projects/<project_name>/rejected/<filename> (or
# <dir>/.rejected/<filename> when project_name is absent) once the counter
# reaches PGAI_DISCOVERY_REJECT_THRESHOLD (default 3).  This stops the
# discovery loop from re-rejecting the same misnamed file on every cron tick.
#
# The optional [project_name] argument (5th) is forwarded to
# _disc_maybe_quarantine to enable project-level rejected/ routing via
# pp_rejected_dir.  When absent, quarantine uses the local .rejected/ fallback.
#
# All rejection output goes directly to stderr from Python — stdout carries
# only validated absolute paths.
_disc_find_unhandled_items() {
    local dir="$1"
    local cache_file="$2"
    local id_prefix="$3"
    local state_dir="${4:-}"    # optional: path to .discovery-state/ for quarantine tracking
    local project_name="${5:-}" # optional: project name for pp_rejected_dir routing

    [[ -d "$dir" ]] || return 0

    # When quarantine tracking is requested, Python writes rejected basename+reason
    # pairs to a temp file so bash can call _disc_maybe_quarantine without touching
    # stdout.  Format: one TSV line per rejected file: <basename>\t<reason>
    local rejected_tmp=""
    if [[ -n "$state_dir" ]]; then
        rejected_tmp="$(pgai_mktemp disc_rejected)"
    fi

    # stdout  → validated item paths (captured by caller via $())
    # stderr  → rejection log messages (operator-visible; never captured as content)
    # $rejected_tmp (when set) → one "<basename>\t<reason>" line per rejected file
    python3 - "$dir" "$cache_file" "$id_prefix" "${rejected_tmp:-}" <<'PY'
import os, re, sys, pathlib

dir_path = pathlib.Path(sys.argv[1])
cache_file = pathlib.Path(sys.argv[2])
id_prefix = sys.argv[3]  # e.g. "BUG-" or "PRIORITY-"
rejected_tmp = sys.argv[4] if len(sys.argv) > 4 else ""  # path to rejected-basenames file

# TESTER Path C filenames produced in priority/: v<X.Y.Z>-bugfix-<slug>.md
# This regex is accepted in addition to PRIORITY- when id_prefix == "PRIORITY-".
PATH_C_RE = re.compile(r'^v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+\.md$', re.IGNORECASE)

# Read bundled IDs from cache.
# For PRIORITY- prefix we also capture v*-bugfix-* stems that may have been
# stored verbatim (they do not begin with "PRIORITY-" so the prefix-anchored
# regex would miss them).
bundled = set()
if cache_file.is_file():
    cache_text = cache_file.read_text()
    # Prefix-anchored pattern: catches BUG- and PRIORITY- stems
    prefix_cache_re = re.compile(
        rf'^\s*-\s+\[x\]\s+({re.escape(id_prefix)}\S+)', re.IGNORECASE
    )
    # Path C stem pattern: catches v*-bugfix-* stems in the cache
    path_c_cache_re = re.compile(
        r'^\s*-\s+\[x\]\s+(v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+)', re.IGNORECASE
    )
    for line in cache_text.splitlines():
        m = prefix_cache_re.match(line)
        if m:
            bundled.add(m.group(1))
        if id_prefix.upper() == "PRIORITY-":
            m2 = path_c_cache_re.match(line)
            if m2:
                bundled.add(m2.group(1))

# Canonical filename patterns per id_prefix.
# BUG- and PRIORITY- are siblings: both use the date-optional form
#   ^{PREFIX}-[0-9]{4,}-.+\.md$
# They must stay in sync — if you tighten one, tighten the other.
# PRIORITY- additionally accepts (TESTER Path C) ^v[0-9]+\.[0-9]+\.[0-9]+-bugfix-\S+\.md$
# All other prefixes fall back to the historical loose pattern.
if id_prefix.upper() == "BUG-":
    canonical_re = re.compile(r'^BUG-[0-9]{4,}-.+\.md$', re.IGNORECASE)
    expected_pattern = "BUG-NNNN-<slug>.md"
    path_c_re_active = None
elif id_prefix.upper() == "PRIORITY-":
    canonical_re = re.compile(r'^PRIORITY-[0-9]{4,}-.+\.md$', re.IGNORECASE)
    expected_pattern = "PRIORITY-NNNN-<slug>.md"
    path_c_re_active = PATH_C_RE  # also accept TESTER Path C names in priority/
else:
    canonical_re = re.compile(rf'^{re.escape(id_prefix)}\d{{4,}}-.+\.md$', re.IGNORECASE)
    expected_pattern = f"{id_prefix}NNNN-<slug>.md"
    path_c_re_active = None

status_re = re.compile(r'^##\s+Status\s*\n\s*(\S+)', re.M | re.IGNORECASE)

# Open the rejected-basenames file for writing if quarantine tracking is active.
rejected_fh = open(rejected_tmp, "w", encoding="utf-8") if rejected_tmp else None

results = []
for entry in sorted(dir_path.iterdir()):
    if not entry.is_file():
        continue
    # Skip non-.md files silently
    if not entry.name.endswith(".md"):
        continue
    # Skip README.md silently
    if entry.name == "README.md":
        continue
    # Skip anything inside a templates/ subdirectory — but since we are
    # iterating the top-level dir_path, entries inside templates/ are
    # directories, not files at this level.  Still, guard by name.
    if "templates" in entry.parts:
        continue
    # Skip anything inside a rejected/ subdirectory — quarantined files are
    # not eligible for re-processing by the discovery pipeline.
    if "rejected" in entry.parts:
        continue

    # Accept if it matches the canonical pattern OR the Path C pattern
    # (Path C is only active when id_prefix == "PRIORITY-").
    is_path_c = path_c_re_active is not None and path_c_re_active.match(entry.name)
    if not canonical_re.match(entry.name) and not is_path_c:
        # Emit rejection to stderr ONLY.
        # Never print to stdout — stdout is the validated-items stream consumed
        # by the caller as bundle content.
        rejection_reason = f"malformed filename (expected pattern: {expected_pattern})"
        print(
            f"ERROR: rejecting {entry.name}: {rejection_reason}",
            file=sys.stderr,
        )
        # Write "<basename>\t<reason>" to the quarantine temp file so
        # _disc_maybe_quarantine can store the reason in the 4th TSV column.
        if rejected_fh is not None:
            rejected_fh.write(entry.name + "\t" + rejection_reason + "\n")
        continue

    item_id = entry.name[:-len(".md")]  # stem without extension

    # Authoritative check: ## Status header is the source of truth.
    # A file whose Status is 'open' (or absent) is eligible for bundling
    # regardless of whether its cache marker is [x].  Only 'running' or
    # 'done' status unconditionally suppresses the file.
    text = entry.read_text(encoding="utf-8", errors="replace")
    sm = status_re.search(text)
    if sm:
        status = sm.group(1).strip().lower()
        # Skip done items unconditionally (already shipped)
        if status == "done":
            continue
        # Skip running items unconditionally (already bundled, RC in flight)
        if status == "running":
            continue
        # 'open' (or anything else) → eligible, proceed regardless of cache
    # Missing header: treat as open (forgiving for older items)
    # NOTE: the cache bundled set is NOT consulted here.  Status field is the
    # sole gate.  The cache remains a convenience view; it does not suppress
    # files whose Status disagrees with it.

    results.append(str(entry.resolve()))

if rejected_fh is not None:
    rejected_fh.close()

for r in results:
    print(r)
PY

    # Quarantine tracking: process each rejected basename+reason through _disc_maybe_quarantine.
    # Each line in rejected_tmp is: <basename>\t<reason>.
    # Lines with no tab character are treated as basename-only (reason='').
    if [[ -n "$state_dir" && -n "$rejected_tmp" && -s "$rejected_tmp" ]]; then
        while IFS=$'\t' read -r rejected_name rejected_reason; do
            [[ -z "$rejected_name" ]] && continue
            _disc_maybe_quarantine "$state_dir" "$dir" "$rejected_name" "${rejected_reason:-}" "${project_name:-}"
        done < "$rejected_tmp"
    fi

    # Clean up the temp file (ignore errors — failure to clean is not fatal)
    [[ -n "$rejected_tmp" ]] && rm -f "$rejected_tmp" 2>/dev/null || true
}

# _disc_set_status_field <file> <new_status>
# Update the ## Status field in the file header. If the field doesn't exist,
# insert one near the top (after the first heading and any frontmatter blank lines).
_disc_set_status_field() {
    local file="$1"
    local new_status="$2"
    python3 - "$file" "$new_status" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
new_status = sys.argv[2]
text = path.read_text(encoding="utf-8")

pattern = re.compile(r'(^##\s+Status\s*\n)(.*?)(\n+##|\Z)', flags=re.M | re.S | re.IGNORECASE)
if pattern.search(text):
    new_text = pattern.sub(lambda m: m.group(1) + new_status + "\n" + (m.group(3) if m.group(3) else ''), text, count=1)
else:
    # Insert after the first heading (e.g. "# BUG-0001-foo")
    lines = text.splitlines(keepends=True)
    insert_idx = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_idx = i + 1
            break
    block = f"\n## Status\n{new_status}\n"
    lines.insert(insert_idx, block)
    new_text = "".join(lines)

path.write_text(new_text, encoding="utf-8")
PY
}

# _disc_read_bundle_field <bundle_file> <field_name>
# Read a named ## field value from a requirements bundle file.
# Echoes the trimmed value of the first non-blank line after the heading.
# Exits 1 and echoes nothing if the field is absent or the file does not exist.
_disc_read_bundle_field() {
    local bundle_file="$1"
    local field_name="$2"
    [[ -f "$bundle_file" ]] || return 1
    python3 - "$bundle_file" "$field_name" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
field = sys.argv[2]
text = path.read_text(encoding="utf-8", errors="replace")
# Match the heading, then capture the first non-blank content line.
m = re.search(
    rf'^##\s+{re.escape(field)}\s*\n\s*(\S[^\n]*)',
    text,
    flags=re.M | re.IGNORECASE,
)
if not m:
    sys.exit(1)
print(m.group(1).strip())
PY
}

# _disc_update_bundle_pm_queued <bundle_file> <pm_task_id>
# After discovery queues PM for a bundle, update the bundle's ## Status to
# 'running' and ## PM Task to the given task ID.  Uses two in-place Python
# rewrites via _disc_set_status_field plus a direct write for ## PM Task.
# This replaces the old slug-grep idempotency check with an authoritative
# status field anchored to the bundle file itself.
_disc_update_bundle_pm_queued() {
    local bundle_file="$1"
    local pm_task_id="$2"
    # Update ## Status field
    _disc_set_status_field "$bundle_file" "running"
    # Update ## PM Task field
    python3 - "$bundle_file" "$pm_task_id" <<'PY'
import pathlib, re, sys
path = pathlib.Path(sys.argv[1])
task_id = sys.argv[2]
text = path.read_text(encoding="utf-8")
pattern = re.compile(
    r'(^##\s+PM Task\s*\n\s*)(none|[^\n]*)(\n?)',
    flags=re.M | re.IGNORECASE,
)
if pattern.search(text):
    new_text = pattern.sub(lambda m: m.group(1) + task_id + "\n", text, count=1)
else:
    # Field absent — append it before the first non-status ## heading
    heading_re = re.compile(r'^##\s+(?!Status\b|PM Task\b)', re.M | re.IGNORECASE)
    m = heading_re.search(text)
    if m:
        insert_pos = m.start()
        new_text = text[:insert_pos] + f"## PM Task\n\n{task_id}\n\n" + text[insert_pos:]
    else:
        suffix = "" if text.endswith("\n") else "\n"
        new_text = text + suffix + f"\n## PM Task\n\n{task_id}\n"
path.write_text(new_text, encoding="utf-8")
PY
}

# _disc_mark_cache_bundled <cache_file> <item_id>
# Add or update a "- [x] <item_id>" line in the cache. Idempotent.
_disc_mark_cache_bundled() {
    local cache_file="$1"
    local item_id="$2"

    python3 - "$cache_file" "$item_id" <<'PY'
import pathlib, re, sys
cache = pathlib.Path(sys.argv[1])
item_id = sys.argv[2]

if not cache.is_file():
    # Create cache with header and the new entry
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(f"# Backlog Cache\n\n## Queue\n\n- [x] {item_id}\n")
    raise SystemExit(0)

text = cache.read_text()

# Look for existing "- [ ] item_id" or "- [x] item_id" line
entry_re = re.compile(rf'^(\s*-\s+\[)([x ])(\]\s+{re.escape(item_id)})(\b.*)?$', re.M)
m = entry_re.search(text)
if m:
    new_text = entry_re.sub(lambda mm: mm.group(1) + 'x' + mm.group(3) + (mm.group(4) or ''), text, count=1)
    cache.write_text(new_text)
    raise SystemExit(0)

# Not present: append under ## Queue, or create the section
if re.search(r'^## Queue\s*$', text, re.M):
    # Append after the last existing entry under Queue, or right after the Queue heading
    new_text = re.sub(
        r'(^## Queue\s*\n(?:.*\n)*?)(?=\n##|\Z)',
        lambda m: m.group(1).rstrip() + f"\n- [x] {item_id}\n",
        text,
        count=1,
        flags=re.M,
    )
    cache.write_text(new_text)
else:
    # No Queue section — append one
    suffix = "" if text.endswith("\n") else "\n"
    cache.write_text(text + suffix + f"\n## Queue\n\n- [x] {item_id}\n")
PY
}

# _disc_list_all_eligible_requirements <dir> <last_released> [max_minor] [max_major] [max_patch]
#
# List ALL requirements files that are eligible for PM pickup, in ascending
# semver order (lowest version first), followed by any auto-sentinel files.
#
#   - Outputs every file that has target_version > last_released
#   - Marks ceiling-blocked files with a structured suffix so the shell caller
#     can emit the documented rejection log line and continue iterating:
#       ":ceiling-blocked:<version>:<component>:<value>:<ceiling_name>:<ceiling_value>"
#   - Marks auto-sentinel files with a ":auto" suffix (same convention as the
#     sibling function)
#
# This allows discovery_step_requirements to iterate the full list and apply
# the skip-done / exit-on-in-flight / queue-first logic per file.
#
# max_minor, max_major, max_patch are optional (pass empty string for "no constraint").
# Output: one line per file, absolute path, possibly with ":auto" or
#         structured ":ceiling-blocked:..." suffix.
#         Outputs nothing if no eligible file found.
_disc_list_all_eligible_requirements() {
    local dir="$1"
    local last_released="$2"
    local max_minor="${3:-}"
    local max_major="${4:-}"
    local max_patch="${5:-}"

    python3 - "$dir" "$last_released" "$max_minor" "$max_major" "$max_patch" <<'PY'
import pathlib, re, sys

req_dir = pathlib.Path(sys.argv[1])
last_released = sys.argv[2].lstrip("v") or "0.0.0"
max_minor_str = sys.argv[3] if len(sys.argv) > 3 else ""
max_major_str = sys.argv[4] if len(sys.argv) > 4 else ""
max_patch_str = sys.argv[5] if len(sys.argv) > 5 else ""

max_minor = int(max_minor_str) if max_minor_str.strip() else None
max_major = int(max_major_str) if max_major_str.strip() else None
max_patch = int(max_patch_str) if max_patch_str.strip() else None

# Sentinel values that indicate "compute version at runtime"
AUTO_SENTINELS = {"auto", "next-patch", ""}

def parse_ver(s):
    s = s.lstrip("v")
    parts = s.split(".")
    while len(parts) < 3:
        parts.append("0")
    try:
        return tuple(int(p) for p in parts[:3])
    except ValueError:
        return None

def ceiling_violation(tv):
    """Return (component, value, ceiling_name, ceiling_value) for first failing ceiling,
    or None when the version is within all configured ceilings."""
    major, minor, patch = tv
    if max_major is not None and major > max_major:
        return ("major", major, "max_major", max_major)
    if max_minor is not None and minor > max_minor:
        return ("minor", minor, "max_minor", max_minor)
    if max_patch is not None and patch > max_patch:
        return ("patch", patch, "max_patch", max_patch)
    return None

last_tuple = parse_ver(last_released) or (0, 0, 0)

target_field_re = re.compile(
    r'^##\s+Target Version\s*\n+\s*(\S+)',
    re.M | re.IGNORECASE,
)
explicit_ver_re = re.compile(r'^v?\d+\.\d+\.\d+$')

# Three buckets:
#   explicit_eligible        — (tv_tuple, path) within ceiling
#   explicit_ceiling_blocked — (tv_tuple, path, structured_suffix) above ceiling
#   auto_eligible            — [path] for sentinel/missing Target Version
explicit_eligible = []
explicit_ceiling_blocked = []
auto_eligible = []

BUNDLE_RE = re.compile(
    r'^(v[0-9]+\.[0-9]+\.[0-9]+-.+|PRIORITY-[0-9]{4,}-.+|BUG-[0-9]{4,}-.+)\.md$',
    re.IGNORECASE,
)

for entry in sorted(req_dir.iterdir()):
    if not entry.is_file() or not entry.name.endswith(".md"):
        continue
    # Skip README.md and any documentation-level files
    if entry.name == "README.md":
        continue
    # Skip templates/ subdirectory contents (guard by parent path segment)
    if "templates" in entry.parts:
        continue
    # Skip rejected/ subdirectory contents — quarantined files are not
    # eligible for re-processing by the discovery pipeline.
    if "rejected" in entry.parts:
        continue
    # Strict allowlist: only files matching bundle naming patterns are eligible
    if not BUNDLE_RE.match(entry.name):
        continue
    text = entry.read_text(encoding="utf-8", errors="replace")
    m = target_field_re.search(text)
    if m:
        raw_val = m.group(1).strip().lower()
    else:
        raw_val = ""

    if raw_val in AUTO_SENTINELS or not explicit_ver_re.match(raw_val):
        auto_eligible.append(entry.resolve())
        continue

    tv = parse_ver(raw_val)
    if tv is None:
        continue
    if tv <= last_tuple:
        continue
    # Check ceilings — both blocked and within-ceiling files are returned so the
    # shell caller can log each blocked entry explicitly with the exact reason.
    viol = ceiling_violation(tv)
    if viol is not None:
        comp, val, cname, cval = viol
        # The version string in the rejection log uses raw_val (preserves 'v' prefix if present)
        ver_str = raw_val if raw_val.startswith("v") else "v" + raw_val
        # Structured suffix: ceiling-blocked:<version>:<component>:<value>:<cname>:<cval>
        suffix = f":ceiling-blocked:{ver_str}:{comp}:{val}:{cname}:{cval}"
        explicit_ceiling_blocked.append((tv, entry.resolve(), suffix))
    else:
        explicit_eligible.append((tv, entry.resolve()))

# Output in semver order: within-ceiling files first (sorted), then
# ceiling-blocked files (sorted), then auto-sentinel files.
explicit_eligible.sort()
explicit_ceiling_blocked.sort()

for _tv, path in explicit_eligible:
    print(path)
for _tv, path, suffix in explicit_ceiling_blocked:
    print(str(path) + suffix)
for path in auto_eligible:
    print(str(path) + ":auto")
PY
}

# _disc_write_bundle <bundle_path> <target_version> <workflow_type> <test_required> <summary> <files> <kind>
# Write a minimal but valid requirements bundle file referencing each input.
#
# Source Branch selection:
#   - workflow_type == "release" → rc/<target_version>  (bug/priority bundles are RC patches)
#   - workflow_type == "feature" (or anything else) → develop  (existing behaviour)
_disc_write_bundle() {
    local bundle_path="$1"
    local target_version="$2"
    local workflow_type="$3"
    local test_required="$4"
    local summary="$5"
    local files="$6"
    local kind="$7"  # "BUG" or "PRIORITY"

    # Compute the correct source branch based on workflow type.
    # Release bundles target an RC branch derived from the target version;
    # feature bundles and any other workflow type continue to use develop.
    local source_branch
    if [[ "$workflow_type" == "release" ]]; then
        source_branch="rc/${target_version}"
    else
        source_branch="develop"
    fi

    mkdir -p "$(dirname "$bundle_path")"

    {
        echo "# Requirements: ${target_version} — ${kind} bundle"
        echo ""
        # ## Status is the authoritative idempotency gate for Step 3.
        # Values: open (not yet queued), running (PM queued), done (RC shipped).
        # discovery_step_requirements reads this field to determine eligibility
        # without relying on filename-derived slug matching.
        echo "## Status"
        echo ""
        echo "open"
        echo ""
        # ## PM Task records the exact PM task ID written to pm_backlog.md when
        # discovery queues this bundle for PM.  Step 3 uses this ID for a precise
        # pm_backlog lookup to distinguish 'PM in flight' from 'PM done'.
        # Value is 'none' until discovery queues PM for this bundle.
        echo "## PM Task"
        echo ""
        echo "none"
        echo ""
        echo "## Target Version"
        echo ""
        echo "${target_version}"
        echo ""
        echo "## Workflow Type"
        echo ""
        echo "${workflow_type}"
        echo ""
        echo "## Test Required"
        echo ""
        echo "${test_required}"
        echo ""
        echo "## Source Branch"
        echo ""
        echo "${source_branch}"
        echo ""
        echo "## Human Approval Required"
        echo ""
        echo "auto"
        echo ""
        echo "## Summary"
        echo ""
        echo "${summary} produced by the discovery pipeline on $(date -Iseconds)."
        echo "This bundle aggregates ${kind} items into a single RC."
        echo ""
        echo "## Bundled Items"
        echo ""
        while IFS= read -r f; do
            [[ -z "$f" ]] && continue
            local fname
            fname="$(basename "$f")"
            echo "- ${fname} (\`${f}\`)"
        done <<< "$files"
        echo ""
        echo "## Acceptance Criteria"
        echo ""
        echo "- [ ] Each bundled item is addressed in the implementation"
        echo "- [ ] All Python files compile cleanly"
        echo "- [ ] All bash files pass syntax check"
        echo ""
        echo "## Notes"
        echo ""
        echo "Bundled by discovery pipeline. PM should read each referenced item file"
        echo "to understand the work to do. After this RC ships, cm-release.sh Step 16b"
        echo "will set this bundle's \`## Status\` to \`done\` and each bundled item's"
        echo "\`## Status\` to \`done\`."
    } > "$bundle_path"
}
