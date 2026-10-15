#!/usr/bin/env bash
# team/scripts/lib/discovery_steps.sh
#
# Discovery pipeline step stage: the two bundle-minting per-category step
# functions (bugs, priority) and the patch-version-slot computation they
# share. Sourced by team/scripts/lib/discovery.sh; extracted from that
# file's former inline body so discovery.sh's own body stays a thin driver
# composing these steps in order. This is a behavior-preserving move: no
# stdout/stderr line, exit code, bundle filename, or bundle body text changes
# as a result of this file existing — it is one stage of a driver that
# was split into single-purpose stage libraries so each stays a focused,
# independently testable unit.
#
# Step 3 of the pipeline, discovery_step_requirements, lives in its own
# sibling file, team/scripts/lib/discovery_step_requirements.sh -- it had
# grown large enough on its own that sharing a file with these two minting
# steps pushed this file over the engine-decomposition's line ceiling
# (docs/blocking-site-census.md row 53).
#
# Public entry points
# --------------------
#   discovery_step_bugs <project_name>
#       Step 1 of the pipeline. Scans bugs/ for unhandled BUG-*.md files and
#       bundles all of them EXCEPT the ones the deferral gate
#       (discovery_defer.sh's _disc_bug_deferral_filter) holds back at or
#       below the resolved deferral level -- unless the hygiene sweep
#       condition fires (bug queue and requirements queue both empty), in
#       which case the held-back litter is what gets bundled. Mint governor:
#       declines (returns 1, narrating the holder's path) when a bug-type
#       bundle already pending in requirements/ (## Status not yet a
#       terminal value -- done or wont-do -- and neither superseded by an
#       unwind nor stuck behind a finished PM task) would otherwise be
#       joined by a second one; a stuck bundle is reported instead -- see
#       _disc_pending_bundle_of_type below. Returns 0 if
#       a bundle was produced (caller stops the iteration); returns 1 if nothing to
#       bundle or the mint governor declined (caller proceeds to Step 2).
#       Called directly by discovery_run_pipeline in discovery.sh.
#   discovery_step_priority <project_name>
#       Step 2 of the pipeline. Same shape as Step 1 but for priority/ items,
#       with an additional max_priorities_per_run cap, numeric-ascending
#       sort, and its own instance of the same mint governor (guarding
#       priority-type bundles instead of bug-type ones). Only reached when
#       Step 1 produced no work. Called directly by discovery_run_pipeline
#       in discovery.sh.
#
# discovery_compute_next_patch <project_name> is a shared primitive, not a
# pipeline step itself — it computes the next free patch (vX.Y.Z) slot for
# bug/priority bundle output, bumping around requirements/ drafts, materialized
# markers, and git tags. It is called by discovery_step_bugs and
# discovery_step_priority in this file, and by discovery_step_requirements
# (team/scripts/lib/discovery_step_requirements.sh, for auto-sentinel Target
# Version resolution) and directly by team/scripts/pm-agent.sh and
# team/scripts/cm/promote_bundled_items.py's shell-out equivalents elsewhere
# in the tree.
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/project_paths.sh — pp_require_project_context,
#     pp_last_released_version, pp_prefix_tag, pp_requirements_dir, pp_tasks_dir,
#     pp_bugs_dir, pp_priority_dir, pp_queue_path, pp_project_root,
#     pp_max_priorities_per_run, pp_max_minor, pp_max_major, pp_max_patch,
#     pp_workflow_type, _pp_project_cfg_file, _pp_read_cfg_key.
#   team/scripts/lib/discovery_guards.sh — _disc_active_rc_blocks,
#     _disc_check_ceiling_and_log, _disc_git_tag_exists.
#   team/scripts/lib/discovery_items.sh — _disc_check_bug_id_collisions,
#     _disc_find_unhandled_items.
#   team/scripts/lib/discovery_defer.sh — _disc_bug_deferral_filter (the
#     bug-step deferral gate discovery_step_bugs applies before bundling).
#   team/scripts/lib/discovery_state.sh — _disc_write_bundle,
#     _disc_mark_cache_bundled, _disc_set_status_field, _disc_read_bundle_field.
#   team/scripts/lib/workflow.sh — wf_load_plugin, wf_version_semantics,
#     WF_LOAD_ERROR.
#   team/scripts/lib/discovery.sh itself — the log() shim, TEAM_ROOT for
#     locating pm-agent.sh, and _disc_enter_write_phase (each step takes the
#     repo lock through it once it has found something to bundle, before
#     the mint governor and the write; the scan before it runs unlocked).
#   team/scripts/lib/error_contract.sh — self-sourced below (mirrors
#     lib/discovery_guards.sh's own self-sourcing idiom) so
#     pgai_emit_error_contract is available whether this file is sourced
#     through the full wake/discovery pipeline or on its own, as unit
#     tests do.
#   team/scripts/lib/pp_run_ops.sh — self-sourced below when the caller
#     has not defined pp_run_ops; _disc_pending_bundle_of_type reads the
#     bundling slot through pgai_agent_kanban.lib.bundle_items pending.
#
# python3 must be on PATH via the sourced helpers above (no direct heredocs
# in this file).

# Guard against double-sourcing.
if declare -F discovery_step_bugs >/dev/null 2>&1; then
    return 0
fi

if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/error_contract.sh"
fi
if ! declare -F pp_run_ops >/dev/null 2>&1; then
    # shellcheck source=pp_run_ops.sh
    source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/pp_run_ops.sh"
fi

# The operator close tool in this file's own tree, named by the stuck-bundle
# remedy so the printed command runs as shown.
_DISC_CLOSE_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/close.sh"

# ---------------------------------------------------------------------------
# _disc_scan_output_has_item_paths <step_name> <scan_output>
#
# Pre-filter guard shared by discovery_step_bugs and discovery_step_priority.
# <scan_output> is the captured stdout of _disc_find_unhandled_items, whose
# contract (discovery_items.sh, "Public entry point") is absolute item paths
# only, one per line.
#
# Returns 0 when at least one line starts with '/'. Otherwise narrates, on
# the log channel, every non-empty line of <scan_output> quoted verbatim --
# a non-empty scan output holding no path means something other than a
# path reached the finder's stdout, and the rejected content in the log
# beside the skip is what lets that occurrence diagnose itself -- and
# returns 1 so the step skips the bundle rather than rendering that content
# into it. Belt-and-suspenders: the finder already drops non-path lines
# itself, so this fires only when that enforcement was bypassed.
#
# The line test is a pure-bash pattern match, never a producer piped into
# `grep -q`: callers run under `set -euo pipefail`, where grep exiting on
# its first match can SIGPIPE a writer still emitting later lines and turn
# the match into a no-match. Prefixing a newline makes "a line starts with
# '/'" the same test as "the text contains newline-then-slash".
# ---------------------------------------------------------------------------
_disc_scan_output_has_item_paths() {
    local step_name="$1"
    local scan_output="$2"
    if [[ $'\n'"$scan_output" == *$'\n/'* ]]; then
        return 0
    fi
    local _line
    local -a _rejected=()
    while IFS= read -r _line; do
        [[ -z "$_line" ]] && continue
        _rejected+=("$_line")
    done <<< "$scan_output"
    log "${step_name}: no valid item paths after filtering; skipping bundle -- the item scan's stdout carried ${#_rejected[@]} non-path line(s) where absolute paths were expected"
    for _line in "${_rejected[@]}"; do
        log "${step_name}: rejected non-path line: '${_line}'"
    done
    return 1
}

# ---------------------------------------------------------------------------
# _disc_bundle_item_shape_ok <candidate_list>
#
# Shared predicate used by discovery_step_bugs and discovery_step_priority's
# post-filter guards below. <candidate_list> is a newline-separated list of
# lines, each expected to be either a bare absolute item path, or an
# absolute item path followed by a tab and a "RIDE_ALONG:<trigger-id>"
# annotation -- the wire format _disc_bug_deferral_filter's piggyback carry
# writes (discovery_defer.sh) and _disc_write_bundle's item renderer
# (discovery_state.sh) both understand. Blank lines are ignored.
#
# Returns 0 when every non-empty line matches that shape. Returns 1 on the
# first line that does not -- a single malformed line invalidates the whole
# list, because a caller that captured narration alongside real paths
# cannot tell which lines are trustworthy without re-deriving this same
# rule itself.
# ---------------------------------------------------------------------------
_disc_bundle_item_shape_ok() {
    local candidates="$1"
    local _line _path _rest
    while IFS= read -r _line; do
        [[ -z "$_line" ]] && continue
        if [[ "$_line" == *$'\t'* ]]; then
            _path="${_line%%$'\t'*}"
            _rest="${_line#*$'\t'}"
            if [[ -z "$_rest" || "$_rest" != RIDE_ALONG:* ]]; then
                return 1
            fi
        else
            _path="$_line"
        fi
        [[ "$_path" == /* ]] || return 1
    done <<< "$candidates"
    return 0
}

# ---------------------------------------------------------------------------
# _disc_pending_bundle_of_type <requirements_dir> <type_slug> [project_name] [tasks_dir] [release_state]
#
# Mint-governor predicate shared by discovery_step_bugs and
# discovery_step_priority (below): scans <requirements_dir> for a bundle
# file matching this category's own naming convention --
# "*-<type_slug>-bundle-*.md" ("bugfix" or "priority", the same infix
# discovery_step_bugs / discovery_step_priority use when building their own
# bundle_path) -- and reports the first one that still holds the bundling
# slot. The decision is pgai_agent_kanban.lib.bundle_items pending_bundle,
# read through pp_run_ops:
#
#   - A "*.SUPERSEDED-on-cancel-*" name (a bundle an RC unwind superseded)
#     never holds the slot, whatever its "## Status" says.
#   - The candidate's own "## Status" header is read through the canonical
#     state reader (lib.terminal_states.canonical_state): "done" and
#     "wont-do" are terminal and free the slot, and a legacy "superseded"
#     reads as "wont-do". A bundle with no "## Status" reads as "open" and
#     holds the slot. Never a backlog marker: those are derived caches.
#   - A "running" bundle whose "## PM Task" reads DONE or WONT-DO and whose
#     version is not the Active RC is stuck: nothing will ship it. It does
#     not hold the slot; each one is reported through the shared emitter
#     (discovery.bundle-stuck) with the close.sh command that retires it,
#     which also lets discovery re-offer the items it left running.
#
# [project_name] names the project in the stuck report's remedy (default:
# the directory holding <requirements_dir>); [tasks_dir] and
# [release_state] default to the "tasks" directory and "release-state.md"
# beside <requirements_dir>.
#
# Echoes the pending bundle's absolute path on stdout and returns 0 when one
# is found (first match in file-name order wins -- there should only ever be
# one once this governor is in place). Returns 1, no stdout, when no
# candidate of this type holds the slot or none exist. When the module
# cannot answer, the first candidate that is not superseded is reported as
# pending, with a WARNING on stderr: a governor that cannot see declines to
# mint rather than risk a second bundle.
# ---------------------------------------------------------------------------
_disc_pending_bundle_of_type() {
    local requirements_dir="$1"
    local type_slug="$2"
    local project_name="${3:-}"
    local tasks_dir="${4:-}"
    local release_state="${5:-}"
    [[ -n "$project_name" ]] || project_name="$(basename "$(dirname "$requirements_dir")")"

    local answer candidate
    if ! answer="$(pp_run_ops pgai_agent_kanban.lib.bundle_items pending \
            "$requirements_dir" "$type_slug" "$tasks_dir" "$release_state")"; then
        echo "_disc_pending_bundle_of_type: WARNING could not read the ${type_slug} bundles in ${requirements_dir}; treating the first one as pending" >&2
        for candidate in "${requirements_dir}"/*-"${type_slug}"-bundle-*.md; do
            [[ -e "$candidate" ]] || continue
            [[ "$candidate" == *.SUPERSEDED-on-cancel-* ]] && continue
            echo "$candidate"
            return 0
        done
        return 1
    fi

    local kind path pm_task pm_state version pending="" stem
    while IFS=$'\t' read -r kind path pm_task pm_state version; do
        case "$kind" in
            pending)
                pending="$path"
                ;;
            stuck)
                stem="$(basename "$path" .md)"
                # CONVERT (docs/blocking-site-census.md): a report, not a
                # refusal -- the stuck bundle stops holding the slot, so
                # discovery goes on minting; nothing halts and no state
                # changes. The remedy retires the bundle, which also lets
                # discovery re-offer the items it left running.
                # CENSUS-ANCHOR: discovery-steps-stuck-bundle-01
                pgai_emit_error_contract \
                    "discovery.bundle-stuck" \
                    "the ${type_slug} bundle ${path} reads '## Status running', but its PM task ${pm_task} is ${pm_state^^} and its version ${version:-(none)} is not the Active RC, so nothing will ship it; discovery no longer counts it as pending, and the items it lists stay running until it is closed." \
                    "${_DISC_CLOSE_SCRIPT} --project ${project_name} --key ${stem} --state wont-do --note 'PM task ${pm_task} ended ${pm_state^^} without shipping this bundle'" \
                    "${project_name}" || true
                ;;
        esac
    done <<< "$answer"

    [[ -n "$pending" ]] || return 1
    echo "$pending"
    return 0
}

# ---------------------------------------------------------------------------
# discovery_compute_next_patch <project_name> [exclude_file]
#
# Compute the next available patch version slot for bug/priority bundle output,
# and for an auto-sentinel requirements file (discovery_step_requirements).
# [exclude_file], when given, is that requirements file: it never takes a slot
# in source 1 below, so a file named after the slot it is about to get does
# not push itself to the next one.
# Bumps the Z component of current_live (X.Y.Z) and bumps-around within patch
# space if the slot is taken in any of three sources:
#   1. requirements/vX.Y.Z-*.md or vX.Y.Z.md exists (drafted; exact-version match)
#   2. tasks/queues/plans/.materialized.* marker whose content references vX.Y.Z
#   3. the project's release tag for vX.Y.Z exists (pp_prefix_tag: the
#      branch_prefix in front, e.g. ai_vX.Y.Z; bare vX.Y.Z with no prefix) --
#      exact match in the project's own dev tree (_disc_git_tag_exists: local
#      tags, then origin unless PGAI_GIT_LOCAL_ONLY is set)
#
# Fresh-system semantics: if Last Released = v0.0.0 (or unset), starts at v0.0.1.
#
# Echoes the chosen version (e.g. "v0.18.5") on stdout. Exit 0 on success.
# ---------------------------------------------------------------------------
discovery_compute_next_patch() {
    local project_name="$1"
    local _exclude_file="${2:-}"
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

    # Release tags carry the project's branch_prefix (pp_prefix_tag's rule).
    local _tag_prefix
    _tag_prefix="$(pp_prefix_tag "$project_name" "")" || _tag_prefix=""

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
        # An unmatched glob stays literal and fails the -e test; the excluded
        # file (the auto-sentinel file being resolved) never counts.
        local _req_match _req_taken=0
        for _req_match in "${requirements_dir}/${candidate}"-*.md "${requirements_dir}/${candidate}.md"; do
            [[ -e "$_req_match" ]] || continue
            [[ -n "$_exclude_file" && "$_req_match" -ef "$_exclude_file" ]] && continue
            _req_taken=1
            break
        done
        if [[ "$_req_taken" -eq 1 ]]; then
            continue
        fi

        # Source 2: materialized marker in tasks/queues/plans/
        # The marker filename convention is .materialized.<sha256-hash>
        # (pure lowercase hex), so the version string cannot appear in the filename.
        # Search marker file CONTENTS for the candidate version as a delimited token
        # (i.e., not followed by another digit) to avoid vX.Y.Z matching vX.Y.Z+N.
        # One grep -q, no pipe: listing matching files into a second grep -q
        # under pipefail could SIGPIPE the lister once several markers match,
        # reporting a taken slot as free.
        if [[ -d "${plans_dir}" ]] && \
           grep -rqE "(^|[^.0-9])${candidate//./\\.}([^0-9]|$)" \
               "${plans_dir}"/.materialized.* 2>/dev/null; then
            continue
        fi

        # Source 3: git tag — use the dev tree resolved above so this check
        # works when the caller is the live install (no .git directory). The
        # tag is the project's release tag name (branch_prefix + version),
        # the same name release tags with, so a hybrid repo's bare tags from
        # another lane never take this lane's slot.
        if _disc_git_tag_exists "${_tag_prefix}${candidate}" "$_dev_tree"; then
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
    # stdout from _disc_find_unhandled_items carries only absolute paths (its
    # stdout contract), so a non-empty value with no line starting with '/'
    # means something other than a path (a log line, stray narration)
    # reached it. Belt-and-suspenders: skip the bundle rather than corrupt
    # it, and let the helper log the rejected content verbatim.
    if ! _disc_scan_output_has_item_paths "discovery_step_bugs" "$unhandled_files"; then
        return 1
    fi

    # Deferral gate: hold back every bug at or below the resolved deferral
    # level so it never triggers a bundle -- the founding rule that a
    # deferred bug never opens an RC. See discovery_defer.sh for the
    # severity-vs-posture decision and the hygiene-sweep condition that
    # eventually bundles the held-back litter once the bug queue and the
    # requirements queue are both empty.
    unhandled_files="$(_disc_bug_deferral_filter "$project_name" "$unhandled_files")" || return 1
    if [[ -z "$unhandled_files" ]]; then
        return 1
    fi

    # Post-filter guard: the pre-filter shape check above (grep -q '^/')
    # only ever looked at the candidate list BEFORE _disc_bug_deferral_filter
    # ran -- it cannot catch anything the filter itself puts on stdout. This
    # is the list _disc_write_bundle actually renders, so validate its shape
    # here too: refuse the whole bundle on the first line that is not a
    # bundleable item path, rather than writing a bundle that may carry
    # non-path content through to the shipped requirements file.
    #
    # CONVERT (docs/blocking-site-census.md): the step declines to bundle
    # this tick and returns non-zero to its own in-process caller, exactly
    # as the emptiness check just above already does -- it never creates a
    # HALT flag, never alters task state, and the next wake retries once
    # whatever produced the malformed line is fixed.
    if ! _disc_bundle_item_shape_ok "$unhandled_files"; then
        log "discovery_step_bugs: refusing to bundle -- post-filter item list contains a line that is not a bundleable item path"
        pgai_emit_error_contract \
            "discovery.bug-bundle-item-shape-invalid" \
            "discovery_step_bugs: the post-deferral-filter item list for project '${project_name}' contains a line that is not a bundleable item path (expected an absolute path, optionally suffixed with a tab and 'RIDE_ALONG:<trigger-id>')." \
            "Inspect _disc_bug_deferral_filter's stdout for this run and confirm every log()/narration call in its call chain writes to stderr, not stdout; discovery retries automatically on the next wake once the leak is fixed." \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-steps-bug-item-shape-01
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

    # Write phase: there are bugs to bundle, so take the repo lock before
    # anything below decides the bundle slot or writes. The mint governor,
    # the next-patch computation and the ceiling gate then run under the
    # lock, so the slot they pick cannot be taken by another writer between
    # the check and the write. A deferred write phase (lock held elsewhere,
    # or the chain turned busy) writes nothing; the pipeline stops there.
    if ! _disc_enter_write_phase "$project_name" "discovery_step_bugs"; then
        return 1
    fi

    # Mint governor: refuse to mint a second bug-type bundle while one is
    # already pending in requirements/. Closes the minting-storm gap: a bug
    # filed after Step 1 already produced one bundle (still Status: open,
    # not yet queued for PM) would otherwise mint a second bundle targeting
    # a different patch slot before the first is even processed. Payload
    # validation (discovery-state-write-bundle-no-valid-items-01,
    # discovery_state.sh) stops a bundle without cargo; this stops a second
    # bundle while the first is still unprocessed.
    #
    # CONVERT (docs/blocking-site-census.md): a pass finding the slot
    # already taken has behaved correctly -- the step declines to bundle
    # this tick and returns non-zero to its own in-process caller, exactly
    # as the emptiness/shape checks above already do -- it never creates a
    # HALT flag, never alters task state, and the next wake retries once the
    # pending bundle's own ## Status reaches "done".
    local requirements_dir _pending_bug_bundle
    requirements_dir="$(pp_requirements_dir "$project_name")"
    if _pending_bug_bundle="$(_disc_pending_bundle_of_type "$requirements_dir" "bugfix" \
            "$project_name" "$(pp_tasks_dir "$project_name")" "$(pp_release_state "$project_name")")"; then
        log "discovery_step_bugs: declining to mint -- a bug bundle is already pending at ${_pending_bug_bundle}"
        pgai_emit_error_contract \
            "discovery.bug-bundle-already-pending" \
            "discovery_step_bugs: a bug-type bundle for project '${project_name}' is already pending at ${_pending_bug_bundle}; refusing to mint a second bundle of the same type while it holds the slot." \
            "No action needed -- discovery retries automatically once ${_pending_bug_bundle} reaches a terminal ## Status (done or wont-do); inspect that file directly if it appears stuck." \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-steps-bug-mint-governor-01
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
    local today bundle_path
    today="$(date +%Y%m%d)"
    bundle_path="${requirements_dir}/${target_version}-bugfix-bundle-${today}.md"

    log "discovery_step_bugs: bundling $(echo "$unhandled_files" | wc -l) bug(s) into ${bundle_path}"

    # A refused write produced nothing: report it and return "nothing
    # bundled" rather than marking items running and logging success (same
    # CONVERT handling as discovery_step_priority's write below).
    if ! _disc_write_bundle "$bundle_path" "$target_version" "release" "true" "Bug bundle" "$unhandled_files" "BUG"; then
        log "discovery_step_bugs: bundle write refused for ${bundle_path}; nothing was bundled and the items stay open"
        return 1
    fi

    # Mark each bundled item: cache [x] AND header status=running.
    # A piggybacked ride-along carries a trailing "\tRIDE_ALONG:<trigger-id>"
    # annotation (see _disc_bug_deferral_filter) -- strip it before deriving
    # the item id or touching the file, so a carried bug is marked handled
    # exactly like any other bundled item.
    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        local f_path="${f%%$'\t'*}"
        # Consumer-side validation (defense in depth): a line that is not
        # an existing file cannot be a bundled item -- skip it loudly,
        # naming the rejected line, rather than deriving a bogus item_id
        # from it or handing it to _disc_set_status_field's read_text.
        if [[ ! -f "$f_path" ]]; then
            log "discovery_step_bugs: WARNING skipping non-path line in bundled-item list: '${f_path}'"
            continue
        fi
        local item_id
        item_id="$(basename "${f_path%.md}")"
        _disc_mark_cache_bundled "$bug_backlog" "$item_id"
        _disc_set_status_field "$f_path" "running"
    done <<< "$unhandled_files"

    log "discovery_step_bugs: bundle written, ${target_version} ready for Step 3 next iteration"
    return 0
}

# ---------------------------------------------------------------------------
# discovery_step_priority <project_name>
#
# Step 2 of the pipeline. Same shape as Step 1 but for priority items.
# Returns 0 if a bundle was produced. Returns 1 if nothing to bundle --
# including when [versioning] max_priorities_per_run is 0, which disables
# priority ingest: the step logs that once and returns before scanning, so
# open PRIORITY items stay untouched and the pipeline moves on to Step 3.
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

    # max_priorities_per_run cap. Read the configured value; default to 1
    # when the helper returns empty (key absent or project.cfg missing). A
    # cap of 0 disables automatic priority ingest: nothing is scanned,
    # bundled or marked, and the step reports "nothing to do" so the
    # pipeline continues to the requirements step.
    local _cap_raw _cap
    _cap_raw="$(pp_max_priorities_per_run "$project_name")"
    _cap="${_cap_raw:-1}"
    if [[ "$_cap" =~ ^[0-9]+$ ]] && (( 10#$_cap == 0 )); then
        log "discovery_step_priority: priority ingest is disabled for project '${project_name}' (max_priorities_per_run = 0); leaving open PRIORITY items untouched"
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
    # stdout from _disc_find_unhandled_items carries only absolute paths (its
    # stdout contract), so a non-empty value with no line starting with '/'
    # means something other than a path (a log line, stray narration)
    # reached it. Belt-and-suspenders: skip the bundle rather than corrupt
    # it, and let the helper log the rejected content verbatim.
    if ! _disc_scan_output_has_item_paths "discovery_step_priority" "$unhandled_files"; then
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

    # Apply the max_priorities_per_run cap read above.
    # Slice: keep only the first $_cap files for this bundle.  The remainder
    # stay [ ] / Status open in priority_backlog.md and are picked up on the
    # next discovery iteration once the current RC closes.
    local bundle_files
    bundle_files="$(echo "$unhandled_files" | head -n "$_cap")"

    # Post-filter guard: bundle_files is exactly the list _disc_write_bundle
    # renders below, so validate its shape now for the same defense-in-depth
    # reason discovery_step_bugs applies to its own post-filter list --
    # refuse the whole bundle on the first line that is not a bundleable
    # item path.
    #
    # CONVERT (docs/blocking-site-census.md): the step declines to bundle
    # this tick and returns non-zero to its own in-process caller, exactly
    # as the emptiness check above already does -- it never creates a HALT
    # flag, never alters task state, and the next wake retries once
    # whatever produced the malformed line is fixed.
    if ! _disc_bundle_item_shape_ok "$bundle_files"; then
        log "discovery_step_priority: refusing to bundle -- finalized item list contains a line that is not a bundleable item path"
        pgai_emit_error_contract \
            "discovery.priority-bundle-item-shape-invalid" \
            "discovery_step_priority: the finalized bundle item list for project '${project_name}' contains a line that is not a bundleable item path (expected an absolute path)." \
            "Inspect the priority intake scan's stdout for this run and confirm every log()/narration call in its call chain writes to stderr, not stdout; discovery retries automatically on the next wake once the leak is fixed." \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-steps-priority-item-shape-01
        return 1
    fi

    # Active-RC guard: refuse to bundle when an RC is in flight.
    # A priority bundle produced while an RC is open would target a version below
    # the in-flight RC, which is incoherent.  Defer until after the current RC ships.
    # Shared helper used by both discovery_step_bugs and discovery_step_priority
    # so the two steps cannot diverge in their Active-RC guard semantics.
    if ! _disc_active_rc_blocks "discovery_step_priority" "$project_name"; then
        return 1
    fi

    # Write phase: same repo-lock entry as discovery_step_bugs above.
    if ! _disc_enter_write_phase "$project_name" "discovery_step_priority"; then
        return 1
    fi

    # Mint governor: refuse to mint a second priority-type bundle while one
    # is already pending in requirements/. Same defence as
    # discovery_step_bugs's own governor above, applied to this step's own
    # category -- a priority item filed after this step already produced
    # one bundle (still Status: open, not yet queued for PM) would
    # otherwise mint a second bundle targeting a different patch slot
    # before the first is even processed.
    #
    # CONVERT (docs/blocking-site-census.md): a pass finding the slot
    # already taken has behaved correctly -- the step declines to bundle
    # this tick and returns non-zero to its own in-process caller, exactly
    # as the emptiness/shape checks above already do -- it never creates a
    # HALT flag, never alters task state, and the next wake retries once the
    # pending bundle's own ## Status reaches "done".
    local requirements_dir _pending_priority_bundle
    requirements_dir="$(pp_requirements_dir "$project_name")"
    if _pending_priority_bundle="$(_disc_pending_bundle_of_type "$requirements_dir" "priority" \
            "$project_name" "$(pp_tasks_dir "$project_name")" "$(pp_release_state "$project_name")")"; then
        log "discovery_step_priority: declining to mint -- a priority bundle is already pending at ${_pending_priority_bundle}"
        pgai_emit_error_contract \
            "discovery.priority-bundle-already-pending" \
            "discovery_step_priority: a priority-type bundle for project '${project_name}' is already pending at ${_pending_priority_bundle}; refusing to mint a second bundle of the same type while it holds the slot." \
            "No action needed -- discovery retries automatically once ${_pending_priority_bundle} reaches a terminal ## Status (done or wont-do); inspect that file directly if it appears stuck." \
            "${project_name}"
        # CENSUS-ANCHOR: discovery-steps-priority-mint-governor-01
        return 1
    fi

    local target_version
    target_version="$(discovery_compute_next_patch "$project_name")"

    # Ceiling gate: skip bundling if the target version exceeds the project ceiling.
    # Emits a structured rejection log line per the documented format.
    if ! _disc_check_ceiling_and_log "$project_name" "$target_version" "discovery_step_priority"; then
        return 1
    fi

    local today bundle_path
    today="$(date +%Y%m%d)"
    bundle_path="${requirements_dir}/${target_version}-priority-bundle-${today}.md"

    log "discovery_step_priority: bundling $(echo "$bundle_files" | wc -l) of $(echo "$unhandled_files" | wc -l) priority item(s) (cap=${_cap}) into ${bundle_path}"

    # A refused write (no item resolves, the workflow plugin will not load)
    # produced nothing: report it and return "nothing bundled" rather than
    # marking items running and logging success. CONVERT: the refusal is
    # _disc_write_bundle's own; this step only stops claiming work it did
    # not do, and the next wake retries.
    if ! _disc_write_bundle "$bundle_path" "$target_version" "release" "true" "Priority bundle" "$bundle_files" "PRIORITY"; then
        log "discovery_step_priority: bundle write refused for ${bundle_path}; nothing was bundled and the items stay open"
        return 1
    fi

    while IFS= read -r f; do
        [[ -z "$f" ]] && continue
        # Consumer-side validation (defense in depth): same guard as
        # discovery_step_bugs's sibling loop -- a line that is not an
        # existing file cannot be a bundled item.
        if [[ ! -f "$f" ]]; then
            log "discovery_step_priority: WARNING skipping non-path line in bundled-item list: '${f}'"
            continue
        fi
        local item_id
        item_id="$(basename "${f%.md}")"
        _disc_mark_cache_bundled "$priority_backlog" "$item_id"
        _disc_set_status_field "$f" "running"
    done <<< "$bundle_files"

    log "discovery_step_priority: bundle written, ${target_version} ready for Step 3 next iteration"
    return 0
}
