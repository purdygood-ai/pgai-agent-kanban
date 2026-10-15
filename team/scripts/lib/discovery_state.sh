#!/usr/bin/env bash
# team/scripts/lib/discovery_state.sh
#
# Discovery on-disk state stage: the status-field and cache mutation
# primitives (## Status writes, ## PM Task writes, backlog-cache [x]
# marking) and the bundle-file writer. Sourced by
# team/scripts/lib/discovery.sh; extracted from that file's former inline
# "Internal helpers" section so discovery.sh's own body stays a thin driver
# over sourced lib stages. This is a behavior-preserving move: no
# stdout/stderr line, exit code, or on-disk file content changes as a
# result of this file existing — it is one stage of a driver that was
# split into single-purpose stage libraries so each stays a focused,
# independently testable unit.
#
# The requirements-directory eligibility enumeration this file used to
# carry (_disc_list_all_eligible_requirements) now lives in its own stage,
# team/scripts/lib/discovery_eligibility.sh -- that function had grown
# large enough on its own that sharing a file with the status/cache/writer
# concerns here pushed this file over the engine-decomposition's line
# ceiling (docs/blocking-site-census.md row 53).
#
# Public entry point
# -------------------
#   _disc_write_bundle <bundle_path> <target_version> <workflow_type> <test_required> <summary> <files> <kind>
#       Writes a requirements bundle file (## Status: open, ## PM Task: none,
#       and the rest of the standard bundle header) referencing each input
#       file, with the Source Branch resolved via the workflow plugin's
#       wf_bundle_source_branch hook. Called directly by discovery_step_bugs
#       and discovery_step_priority in discovery.sh, immediately followed by
#       the per-item _disc_mark_cache_bundled / _disc_set_status_field calls
#       in the same driver loop.
#
#       Before rendering the ## Bundled Items list, _disc_write_bundle passes
#       <files> through _disc_order_bundle_items (below), which stably
#       partitions the list so every item carrying a non-empty
#       "## Known Issue Carry Forward" value leads, in the order it arrived,
#       with every other item following, in the order it arrived. This is
#       the one place "why is this item first" is answered for both callers:
#       discovery_step_bugs and discovery_step_priority both hand their file
#       list to this same function, so both get the same ordering treatment
#       automatically, with no divergence to track between the two steps.
#       Neither this function nor _disc_order_bundle_items ever writes to an
#       input file -- the marker is read-only from the pipeline's side; only
#       a release ship stamps it (team/scripts/lib/cm/release_known_issue.sh).
#
#       Fail-closed guard: before writing anything, _disc_write_bundle
#       counts how many entries in <files> resolve to an existing regular
#       file. Zero valid entries refuses the write entirely (dual-channel
#       error/message/remedy, non-zero return, no file created) rather than
#       rendering a header-only bundle with an empty ## Bundled Items
#       section -- see the guard's own comment for the full rationale.
#
#       The bundle is rendered by _disc_render_bundle (stdout only) and
#       committed by pgai_atomic_write: a scratch file beside the bundle,
#       renamed into place only after the whole render succeeded. Any write
#       failure (unwritable requirements/, full disk) returns non-zero with
#       no bundle file, partial or otherwise, left behind, so the caller
#       leaves every item open.
#

#       A line in <files> may carry a trailing "\t<annotation>". The only
#       recognised annotation today is "RIDE_ALONG:<trigger-id>", written by
#       discovery_step_bugs's deferral filter (_disc_bug_deferral_filter in
#       discovery_defer.sh) for a piggybacked bug: the rendered ## Bundled
#       Items line for that item gets the trailing suffix
#       " — hygiene ride-along (piggyback on <trigger-id>)" -- the exact
#       wording team/roles/PM.md's ride-along-consolidation section
#       specifies -- after its closing backtick-paren. A line with no tab
#       renders exactly as before this annotation existed.
#
# Other functions in this file (_disc_set_status_field,
# _disc_read_bundle_field, _disc_update_bundle_pm_queued,
# _disc_mark_cache_bundled, _disc_order_bundle_items) are shared primitives,
# not private helpers of _disc_write_bundle alone:
#   - _disc_set_status_field and _disc_mark_cache_bundled are called
#     directly by discovery_step_bugs and discovery_step_priority in
#     discovery.sh, immediately after _disc_write_bundle, to mark each
#     bundled item running/[x].
#   - _disc_set_status_field is also called by
#     team/scripts/lib/discovery_items.sh's _disc_reset_item_status_to_open
#     (a cross-file dependency — see Dependencies below).
#   - _disc_read_bundle_field and _disc_update_bundle_pm_queued are called
#     directly by discovery_step_requirements in
#     team/scripts/lib/discovery_step_requirements.sh to read and then
#     advance a bundle's idempotency fields once PM is queued.
# They are defined at source time (available as soon as this file is
# sourced, independent of when _disc_write_bundle itself is invoked).
#
# Dependencies (must already be sourced/defined by the caller before this
# file's functions are called)
# -----------------------------------------------------------------------
#   team/scripts/lib/workflow.sh — wf_load_plugin, wf_bundle_source_branch,
#     WF_LOAD_ERROR (used by _disc_write_bundle to resolve the bundle's
#     Source Branch field).
#   team/scripts/lib/discovery.sh itself — the log() shim.
#   team/scripts/lib/error_contract.sh — self-sourced below (mirrors
#     lib/discovery_guards.sh's own self-sourcing idiom) so
#     pgai_emit_error_contract is available whether this file is sourced
#     through the full wake/discovery pipeline or on its own, as unit
#     tests do. Used by _disc_write_bundle's no-valid-items guard.
#
#   team/scripts/lib/pp_run_ops.sh and atomic_write.sh — self-sourced
#     below. Every status, field and cache read or rewrite is a module call
#     through pp_run_ops: pgai_agent_kanban.lib.md_fields read /
#     render-status / render-value, and pgai_agent_kanban.ops
#     render_ensure_marker. The render calls print the whole new file and
#     write nothing; pgai_atomic_write commits it (same-directory scratch
#     file plus rename, file mode kept), so a concurrent reader never sees a
#     half-written intake, bundle or cache file and a failed call leaves the
#     target untouched.
#
# python3 must be on PATH. _disc_order_bundle_items' Known Issue Carry
# Forward partition is pgai_agent_kanban.discovery.items order.
# _disc_write_bundle's wf_load_plugin probe is safe under the caller's
# `set -euo pipefail`: the function toggles set +e/-e around that one call
# internally so a failed plugin load returns 1 instead of aborting the shell.

# Guard against double-sourcing.
if declare -F _disc_write_bundle >/dev/null 2>&1; then
    return 0
fi

_DISC_STATE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! declare -F pgai_emit_error_contract >/dev/null 2>&1; then
    # shellcheck source=error_contract.sh
    source "${_DISC_STATE_LIB_DIR}/error_contract.sh"
fi
# A caller's own pp_run_ops (wake_bootstrap.sh's, or a test stub) wins.
# shellcheck source=pp_run_ops.sh
declare -F pp_run_ops >/dev/null || source "${_DISC_STATE_LIB_DIR}/pp_run_ops.sh"
# shellcheck source=atomic_write.sh
source "${_DISC_STATE_LIB_DIR}/atomic_write.sh"

# _disc_set_status_field <file> <new_status>
# Update the ## Status field in the file header. If the field doesn't exist,
# insert one near the top (after the first heading and any frontmatter blank lines).
# The rewrite is md_fields.set_status_text (render-status prints the new
# file); pgai_atomic_write commits it.
#
# Defense in depth: <file> is validated as an existing regular file before
# it is ever read. A caller loop iterating a captured list of
# "bundled item" lines can, if narration ever again leaks into that list
# upstream, hand this function a plain sentence instead of a path — this
# guard turns that into a named, loud skip on stderr (exit 0, no bundle
# mutation) instead of an unhandled read failure. The
# primary fix is keeping narration off that list in the first place (see
# log() in wake_bootstrap.sh); this is the belt-and-suspenders half.
_disc_set_status_field() {
    local file="$1"
    local new_status="$2"
    if [[ ! -f "$file" ]]; then
        echo "ERROR: _disc_set_status_field: skipping non-path line (no such file): ${file}" >&2
        return 0
    fi
    pgai_atomic_write "$file" pp_run_ops pgai_agent_kanban.lib.md_fields \
        render-status "$file" "$new_status"
}

# _disc_read_bundle_field <bundle_file> <field_name>
# Read a named ## field value from a requirements bundle file.
# Echoes the trimmed value of the first non-blank line after the heading
# (md_fields.field_value: an empty section reads empty, a bold value reads
# as the text inside). Exits 1 and echoes nothing if the field is absent or
# the file does not exist.
_disc_read_bundle_field() {
    local bundle_file="$1"
    local field_name="$2"
    [[ -f "$bundle_file" ]] || return 1
    pp_run_ops pgai_agent_kanban.lib.md_fields read "$bundle_file" "$field_name" || return 1
}

# _disc_update_bundle_pm_queued <bundle_file> <pm_task_id>
# After discovery queues PM for a bundle, update the bundle's ## Status to
# 'running' and ## PM Task to the given task ID: _disc_set_status_field for
# ## Status, then md_fields.set_value_line for ## PM Task, which replaces
# only the value line and keeps the blank line the bundle writer puts above
# it. Each write is committed through pgai_atomic_write.
# This replaces the old slug-grep idempotency check with an authoritative
# status field anchored to the bundle file itself.
_disc_update_bundle_pm_queued() {
    local bundle_file="$1"
    local pm_task_id="$2"
    # Update ## Status field
    _disc_set_status_field "$bundle_file" "running"
    # Update ## PM Task field
    pgai_atomic_write "$bundle_file" pp_run_ops pgai_agent_kanban.lib.md_fields \
        render-value "$bundle_file" "PM Task" "$pm_task_id"
}

# _disc_mark_cache_bundled <cache_file> <item_id>
# Add or update a "- [x] <item_id>" line in the cache. Idempotent.
# The rewrite is ops.ensure_marker (render_ensure_marker prints the new
# file): an existing line for <item_id> has its marker set to [x] by the
# shared queue-marker writer; otherwise the entry is added under ## Queue,
# and a missing cache file (and its directory) is created.
_disc_mark_cache_bundled() {
    local cache_file="$1"
    local item_id="$2"
    mkdir -p "$(dirname "$cache_file")"
    pgai_atomic_write "$cache_file" pp_run_ops pgai_agent_kanban.ops \
        render_ensure_marker "$cache_file" "$item_id" x
}

# _disc_order_bundle_items <files>
#
# Stably partitions a newline-separated list of absolute file paths into two
# groups -- items whose file carries a non-empty "## Known Issue Carry
# Forward" value, and everything else -- and prints the marked group first,
# then the unmarked group, preserving the original relative order WITHIN
# each group. This is a stable partition, not a sort: two marked items keep
# the order <files> gave them, and so do two unmarked items.
#
# A line may carry a trailing "\t<annotation>" (for example
# "\tRIDE_ALONG:<trigger-id>", written by _disc_bug_deferral_filter for a
# piggybacked ride-along) -- the marker check below reads only the path
# portion before the first tab, and the full original line (path plus
# annotation, unchanged) is what gets reordered and printed, so an
# annotation is never dropped or misread as part of the path.
#
# Called by _disc_write_bundle immediately before it renders the ##
# Bundled Items list, so both discovery_step_bugs and discovery_step_priority
# (the two callers of _disc_write_bundle) get identical ordering treatment
# for free -- there is exactly one place this decision is made. The marker
# is a bug-file-only convention today (stamped by
# team/scripts/lib/cm/release_known_issue.sh's cm_stamp_known_issue_marker
# on the causing bug at ship time); priority items are not currently
# stamped, so on the priority path this function is a no-op pass-through
# that preserves the numeric-priority order discovery_step_priority already
# computed. That is a property of what the marker means today, not a
# special case in this code -- a future writer that stamps a priority item
# would be honored identically, with no change here.
#
# This function never writes to any input file. Reading the marker is the
# pipeline's whole involvement with it; only a release ship stamps it.
#
# Marker recognition (what counts as "marked"):
#   A file is marked only when it contains a line matching, case-insensitively,
#   "## Known Issue Carry Forward" on its own, followed -- skipping blank
#   lines -- by a line with visible (non-whitespace) content before either
#   the next "## " heading or end of file. The actual value (the RC version
#   string a ship stamps there) is never inspected beyond "has content" --
#   ordering only cares whether a marker was stamped, not which version it
#   names.
#
# Malformed/unreadable marker handling (deliberate, tested):
#   - A marker header present with nothing but blank lines before the next
#     heading or EOF (an empty/absent value) is treated as UNMARKED. The
#     item is neither promoted to the front nor dropped from the list --
#     it stays exactly where <files> put it. A marker that cannot be read
#     as a real value must never silently jump the queue, and it must
#     never disappear either.
#   - A path that cannot be read at all (missing file, permission error)
#     is likewise treated as UNMARKED and kept in place, for the same
#     fail-safe reason: an ordering helper is not the place to fail loud
#     over a read error another stage already owns detecting.
#
# What this function does NOT do: it does not decide which items are
# eligible for bundling (that is _disc_find_unhandled_items and the caller's
# own status/cache checks, run before this function ever sees the list), it
# does not change the marker, and it does not change which items end up
# marked "running" afterwards. A marker on a file that never reaches <files>
# (because it was already bundled, or filtered by an earlier check) simply
# never appears in the output -- the marker cannot make an ineligible item
# eligible.
_disc_order_bundle_items() {
    local files="$1"
    pp_run_ops pgai_agent_kanban.discovery.items order "$files"
}

# _disc_write_bundle <bundle_path> <target_version> <workflow_type> <test_required> <summary> <files> <kind>
# Write a minimal but valid requirements bundle file referencing each input.
#
# Source Branch selection:
#   Queries the workflow plugin's wf_bundle_source_branch hook after loading the
#   plugin for the given workflow_type.  The release plugin returns rc/<version>;
#   other plugin types return main (or their own branch convention).
#   If the plugin cannot be loaded, the function logs a warning and returns
#   non-zero so the caller can surface the failure as BLOCKED.
_disc_write_bundle() {
    local bundle_path="$1"
    local target_version="$2"
    local workflow_type="$3"
    local test_required="$4"
    local summary="$5"
    local files="$6"
    local kind="$7"  # "BUG" or "PRIORITY"

    # Fail-closed guard: refuse to write a bundle when zero entries in
    # <files> resolve to an existing regular file. This is the last line of
    # defence in the render pipeline -- discovery_step_bugs and
    # discovery_step_priority both validate their own item-list shape
    # before ever calling here, but a future direct caller (or a test)
    # could hand this function an unfiltered list. Without this guard, the
    # per-item skip below would silently render a header-only bundle with
    # an empty ## Bundled Items section, which would queue PM on nothing.
    #
    # CONVERT (docs/blocking-site-census.md): the step declines to bundle
    # this tick and returns non-zero to its own in-process caller, exactly
    # like the workflow-plugin guard just above -- it never creates a HALT
    # flag and never alters task state.
    local _valid_entry_count=0
    local _shape_check_line _shape_check_path
    while IFS= read -r _shape_check_line; do
        [[ -z "$_shape_check_line" ]] && continue
        _shape_check_path="${_shape_check_line%%$'\t'*}"
        [[ -f "$_shape_check_path" ]] && _valid_entry_count=$((_valid_entry_count + 1))
    done <<< "$files"

    if [[ "$_valid_entry_count" -eq 0 ]]; then
        log "_disc_write_bundle: refusing to write ${bundle_path} -- no entry in the item list resolves to an existing regular file"
        pgai_emit_error_contract \
            "discovery.bundle-write-no-valid-items" \
            "_disc_write_bundle: refusing to write ${bundle_path} -- none of the entries in the item list resolve to an existing regular file." \
            "Inspect the caller's item list for this run (discovery_step_bugs or discovery_step_priority) and confirm it carries at least one real item path before retrying discovery." \
            ""
        # CENSUS-ANCHOR: discovery-state-write-bundle-no-valid-items-01
        return 1
    fi

    # Load the workflow plugin to query the source branch via wf_bundle_source_branch.
    # Fail-closed: if the plugin cannot be loaded, stop bundling and surface the
    # error so the operator can fix the plugin configuration.
    local _wf_load_exit=0
    set +e
    wf_load_plugin "$workflow_type"
    _wf_load_exit=$?
    set -e
    if [[ $_wf_load_exit -ne 0 ]]; then
        log "_disc_write_bundle: cannot load workflow plugin '${workflow_type}': ${WF_LOAD_ERROR}; bundle aborted"
        return 1
    fi

    # Compute the correct source branch via the plugin hook.
    local source_branch
    source_branch="$(wf_bundle_source_branch "$target_version")"

    if ! mkdir -p "$(dirname "$bundle_path")"; then
        log "_disc_write_bundle: cannot create $(dirname "$bundle_path"); bundle aborted, nothing written"
        return 1
    fi

    # Render into a scratch file beside the bundle and rename it into
    # place only when the whole render succeeded (pgai_atomic_write): a
    # full disk, a read-only mount or an unwritable requirements/ leaves no
    # half-written bundle behind, and the caller sees the non-zero return.
    if ! pgai_atomic_write "$bundle_path" _disc_render_bundle \
            "$target_version" "$workflow_type" "$test_required" "$summary" \
            "$files" "$kind" "$source_branch"; then
        log "_disc_write_bundle: writing ${bundle_path} failed; bundle aborted, nothing written"
        return 1
    fi
    return 0
}

# _disc_render_bundle <target_version> <workflow_type> <test_required> <summary> <files> <kind> <source_branch>
# Print the whole requirements bundle for _disc_write_bundle to stdout;
# writes no file itself. Arguments are _disc_write_bundle's own, plus the
# Source Branch it already resolved through the workflow plugin.
_disc_render_bundle() {
    local target_version="$1"
    local workflow_type="$2"
    local test_required="$3"
    local summary="$4"
    local files="$5"
    local kind="$6"
    local source_branch="$7"
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
        while IFS=$'\t' read -r f _annotation; do
            [[ -z "$f" ]] && continue
            # Consumer-side validation (defense in depth): <files> is a
            # caller-captured list that should carry only item paths -- a
            # line that is not an existing file cannot be a bundled item.
            # Skip it loudly on stderr, naming the rejected line, rather
            # than rendering it into the shipped requirements bundle.
            if [[ ! -f "$f" ]]; then
                log "_disc_write_bundle: WARNING skipping non-path line in bundled-item list: '${f}'"
                continue
            fi
            local fname
            fname="$(basename "$f")"
            local _suffix=""
            if [[ "$_annotation" == RIDE_ALONG:* ]]; then
                # Trailing text after the closing backtick-paren, byte-identical
                # to the wording team/roles/PM.md's ride-along-consolidation
                # section specifies -- PM's consolidation keys off this exact
                # string.
                _suffix=" — hygiene ride-along (piggyback on ${_annotation#RIDE_ALONG:})"
            fi
            echo "- ${fname} (\`${f}\`)${_suffix}"
        done <<< "$(_disc_order_bundle_items "$files")"
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
        echo "to understand the work to do. After this RC ships, scripts/cm/release.sh Step 16b"
        echo "will set this bundle's \`## Status\` to \`done\` and each bundled item's"
        echo "\`## Status\` to \`done\`."
    }
}
